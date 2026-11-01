<#
.SYNOPSIS
  Dagu (https://github.com/dagucloud/dagu) を Windows サービスとして登録する。プロキシ環境向け。

.DESCRIPTION
  dagu.exe を取ってきて WinSW でサービスにし、`dagu start-all`（Web UI + スケジューラ）を常駐させる。
  プロキシは次の 2 か所に効かせる。

    1. このスクリプト自身のダウンロード（Dagu 本体・checksums.txt・WinSW）
    2. サービスとして動く Dagu と、Dagu が流すステップ
       （サービスは LocalSystem で動くため、利用者のプロキシ設定も環境変数も見えない。
        HTTP_PROXY / HTTPS_PROXY / NO_PROXY をサービスの環境に書き込み、
        DAGU_ENV_PASSTHROUGH でステップへも渡す）

  -Proxy を省くと、環境変数 → インターネット オプション → WinHTTP（netsh winhttp）の順に探す。

  管理者の PowerShell で実行する。もう一度実行すると、上書きで更新（版上げ・設定変更）になる。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install-dagu-service.ps1

.EXAMPLE
  .\install-dagu-service.ps1 -Proxy http://proxy.example.co.jp:8080 -ProxyBypass .example.co.jp,10.0.0.0/8

.EXAMPLE
  .\install-dagu-service.ps1 -Proxy http://proxy.example.co.jp:8080 -ProxyCredential (Get-Credential)

.EXAMPLE
  .\install-dagu-service.ps1 -DaguArchive .\dagu_2.0.0_windows_amd64.tar.gz -WinSWPath .\WinSW-x64.exe

.EXAMPLE
  .\install-dagu-service.ps1 -Proxy http://proxy:8080 -DryRun

.EXAMPLE
  .\install-dagu-service.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    # 入れる版（例 v2.0.0）。latest なら最新。
    [string]$Version = 'latest',
    [string]$InstallDir = "$env:ProgramFiles\Dagu",
    # Dagu の設定・DAG・履歴・ログの置き場所（DAGU_HOME）。
    [string]$DaguHome = "$env:ProgramData\Dagu",
    [string]$ServiceName = 'Dagu',
    [string]$HostAddress = '127.0.0.1',
    [int]$Port = 8080,
    # auto（探す）/ none（使わない）/ http://host:port
    [string]$Proxy = 'auto',
    # プロキシを通さない宛先（NO_PROXY の書式。*.example.com や 10.* も受け付けて書き換える）。
    [string[]]$ProxyBypass = @(),
    # 認証付きプロキシのユーザーとパスワード。
    [pscredential]$ProxyCredential,
    # ダウンロードだけ、ログオン中の Windows 資格情報（NTLM/Kerberos）でプロキシ認証する。
    [switch]$ProxyUseDefaultCredentials,
    # プロキシのほかにステップへ渡す環境変数の名前（config.yaml の env_passthrough の代わり）。
    [string[]]$EnvPassthrough = @(),
    # 手元の Dagu の配布物（.tar.gz / .zip）。インターネットに出られないときに使う。
    [string]$DaguArchive = '',
    # 手元の WinSW の exe。
    [string]$WinSWPath = '',
    [string]$WinSWVersion = 'v2.12.0',
    [switch]$Uninstall,
    # 何も変えずに、決まった設定とサービス定義を表示する。
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Script:DaguRepo = 'https://github.com/dagucloud/dagu'
$Script:WinSWRepo = 'https://github.com/winsw/winsw'
$Script:ProxyEnvNames = @('HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY')
$Script:AlwaysNoProxy = @('localhost', '127.0.0.1', '::1')

# ---------------------------------------------------------------------------
# プロキシの読み取りと書き換え（副作用なし。テストはここを直接呼ぶ）
# ---------------------------------------------------------------------------

function ConvertTo-ProxyUrl {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    $v = $Value.Trim()
    if ($v -notmatch '^[A-Za-z][A-Za-z0-9+.-]*://') { $v = "http://$v" }
    return $v.TrimEnd('/')
}

# インターネット オプション / WinHTTP の「プロキシ サーバー」の書式を読む。
#   proxy:8080                     → http も https も同じ
#   http=a:8080;https=b:8080;...   → スキームごと（socks は Go が socks4 を扱えないので外す）
function ConvertFrom-ProxyServerString {
    param([string]$ProxyServer)
    $result = [ordered]@{ Http = ''; Https = ''; Ignored = @() }
    if ([string]::IsNullOrWhiteSpace($ProxyServer)) { return [pscustomobject]$result }
    if ($ProxyServer -notmatch '=') {
        $url = ConvertTo-ProxyUrl (@($ProxyServer -split '[;\s]+' | Where-Object { $_ })[0])
        $result.Http = $url
        $result.Https = $url
        return [pscustomobject]$result
    }
    foreach ($part in ($ProxyServer -split '[;\s]+')) {
        if (-not $part) { continue }
        $kv = $part -split '=', 2
        if ($kv.Count -ne 2 -or -not $kv[1]) { continue }
        switch ($kv[0].Trim().ToLowerInvariant()) {
            'http' { $result.Http = ConvertTo-ProxyUrl $kv[1] }
            'https' { $result.Https = ConvertTo-ProxyUrl $kv[1] }
            default { $result.Ignored += $part }
        }
    }
    return [pscustomobject]$result
}

# 除外リスト（インターネット オプションの ; 区切り、WinHTTP の ; / 空白区切り、NO_PROXY の , 区切り）を
# Go の NO_PROXY の書式へ寄せる。書き換えられないものは Ignored に返す。
function ConvertTo-NoProxyList {
    param([string[]]$Entries)
    $out = New-Object System.Collections.Generic.List[string]
    $ignored = New-Object System.Collections.Generic.List[string]
    foreach ($raw in @($Entries)) {
        if ($null -eq $raw) { continue }
        foreach ($item in ($raw -split '[;,\s]+')) {
            $e = $item.Trim()
            if (-not $e) { continue }
            if ($e -match '^<.*>$') { $ignored.Add($e); continue }   # <local> / <-loopback>
            $e = $e -replace '^[A-Za-z][A-Za-z0-9+.-]*://', ''
            $e = $e.TrimEnd('/')
            if ($e -eq '*') { $out.Add('*'); continue }
            if ($e -match '^\*\.([^*]+)$') { $out.Add(".$($Matches[1])"); continue }
            if ($e -match '^\d{1,3}(\.(\d{1,3}|\*)){1,3}$' -and $e.Contains('*')) {
                # 10.* / 192.168.* / 192.168.1.* → CIDR。* は末尾に続けて置かれたものだけ書き換える。
                $octets = @($e -split '\.')
                $fixed = @()
                foreach ($o in $octets) { if ($o -eq '*') { break }; $fixed += $o }
                $rest = @($octets | Select-Object -Skip $fixed.Count)
                if ($fixed.Count -ge 1 -and $fixed.Count -le 3 -and -not ($rest | Where-Object { $_ -ne '*' })) {
                    $bits = $fixed.Count * 8
                    while ($fixed.Count -lt 4) { $fixed += '0' }
                    $out.Add(("{0}/{1}" -f ($fixed -join '.'), $bits))
                    continue
                }
                $ignored.Add($e); continue
            }
            if ($e.Contains('*')) { $ignored.Add($e); continue }
            $out.Add($e)
        }
    }
    return [pscustomobject]@{ Entries = @($out); Ignored = @($ignored) }
}

function Join-NoProxy {
    param([string[]]$Entries)
    $seen = @{}
    $list = New-Object System.Collections.Generic.List[string]
    foreach ($e in @($Entries)) {
        if (-not $e) { continue }
        $k = $e.ToLowerInvariant()
        if ($seen.ContainsKey($k)) { continue }
        $seen[$k] = $true
        $list.Add($e)
    }
    return ($list -join ',')
}

# HKLM\...\Internet Settings\Connections の WinHttpSettings（netsh winhttp set proxy の保存先）を読む。
#   0: 構造体の版  4: 更新回数  8: フラグ（2 = プロキシあり）
#   12: プロキシ文字列の長さ → 文字列 → 除外リストの長さ → 文字列（どちらも ANSI）
function ConvertFrom-WinHttpSettings {
    param([byte[]]$Bytes)
    if (-not $Bytes -or $Bytes.Length -lt 16) { return $null }
    $flags = [BitConverter]::ToUInt32($Bytes, 8)
    if (($flags -band 2) -eq 0) { return $null }
    $pos = 12
    $len = [BitConverter]::ToUInt32($Bytes, $pos); $pos += 4
    if ($len -eq 0 -or $pos + $len -gt $Bytes.Length) { return $null }
    $server = [Text.Encoding]::ASCII.GetString($Bytes, $pos, $len); $pos += $len
    $bypass = ''
    if ($pos + 4 -le $Bytes.Length) {
        $blen = [BitConverter]::ToUInt32($Bytes, $pos); $pos += 4
        if ($blen -gt 0 -and $pos + $blen -le $Bytes.Length) {
            $bypass = [Text.Encoding]::ASCII.GetString($Bytes, $pos, $blen)
        }
    }
    return [pscustomobject]@{ Server = $server.Trim([char]0); Bypass = $bypass.Trim([char]0) }
}

# パスワードを含む URL を画面に出すときに伏せる。
function Hide-ProxyPassword {
    param([string]$Url)
    if (-not $Url) { return $Url }
    return ($Url -replace '^([^:/]+://[^:/@]+:)[^@]*@', '$1****@')
}

function Add-ProxyCredential {
    param([string]$Url, [pscredential]$Credential)
    if (-not $Url -or -not $Credential) { return $Url }
    if ($Url -notmatch '^(?<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?<rest>.*)$') { return $Url }
    $rest = $Matches['rest']
    $scheme = $Matches['scheme']
    if ($rest -match '^[^/]*@') { $rest = $rest -replace '^[^/]*@', '' }   # 書かれていた認証は差し替える
    $user = [Uri]::EscapeDataString($Credential.UserName)
    $pass = [Uri]::EscapeDataString($Credential.GetNetworkCredential().Password)
    return "$scheme${user}:${pass}@$rest"
}

# 認証付きの URL を、Invoke-WebRequest に渡す URL と資格情報に分ける。
function Split-ProxyUrl {
    param([string]$Url)
    $result = [ordered]@{ Url = $Url; Credential = $null }
    if ($Url -match '^(?<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?<user>[^:/@]*)(:(?<pass>[^@/]*))?@(?<rest>.*)$') {
        $result.Url = $Matches['scheme'] + $Matches['rest']
        $user = [Uri]::UnescapeDataString($Matches['user'])
        $passText = if ($Matches.ContainsKey('pass')) { [Uri]::UnescapeDataString($Matches['pass']) } else { '' }
        $secure = New-Object System.Security.SecureString
        foreach ($c in $passText.ToCharArray()) { $secure.AppendChar($c) }
        $result.Credential = New-Object System.Management.Automation.PSCredential($user, $secure)
    }
    return [pscustomobject]$result
}

# システムからプロキシを探す。見つけた順に 1 つだけ返す。
function Find-SystemProxy {
    $envHttps = [Environment]::GetEnvironmentVariable('HTTPS_PROXY')
    $envHttp = [Environment]::GetEnvironmentVariable('HTTP_PROXY')
    if ($envHttps -or $envHttp) {
        return [pscustomobject]@{
            Source = '環境変数（HTTP_PROXY / HTTPS_PROXY）'
            Http = ConvertTo-ProxyUrl $envHttp
            Https = ConvertTo-ProxyUrl $envHttps
            Bypass = @([Environment]::GetEnvironmentVariable('NO_PROXY'))
            Warnings = @()
        }
    }

    $warnings = @()
    $ie = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
    if ($ie) {
        $enabled = ($ie.PSObject.Properties['ProxyEnable'] -and $ie.ProxyEnable -eq 1)
        $server = if ($ie.PSObject.Properties['ProxyServer']) { $ie.ProxyServer } else { '' }
        if ($enabled -and $server) {
            $p = ConvertFrom-ProxyServerString $server
            $bypass = if ($ie.PSObject.Properties['ProxyOverride']) { $ie.ProxyOverride } else { '' }
            return [pscustomobject]@{
                Source = 'インターネット オプション（このユーザー）'
                Http = $p.Http; Https = $p.Https; Bypass = @($bypass)
                Warnings = @($p.Ignored | ForEach-Object { "プロキシの指定 $_ は使えないので外した" })
            }
        }
        if ($ie.PSObject.Properties['AutoConfigURL'] -and $ie.AutoConfigURL) {
            $warnings += "自動構成スクリプト（PAC: $($ie.AutoConfigURL)）はサービスでは使えない。-Proxy でプロキシを指定すること"
        }
    }

    $conn = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\Connections' -Name WinHttpSettings -ErrorAction SilentlyContinue
    if ($conn) {
        $w = ConvertFrom-WinHttpSettings ([byte[]]$conn.WinHttpSettings)
        if ($w -and $w.Server) {
            $p = ConvertFrom-ProxyServerString $w.Server
            return [pscustomobject]@{
                Source = 'WinHTTP（netsh winhttp）'
                Http = $p.Http; Https = $p.Https; Bypass = @($w.Bypass)
                Warnings = $warnings + @($p.Ignored | ForEach-Object { "プロキシの指定 $_ は使えないので外した" })
            }
        }
    }
    return [pscustomobject]@{ Source = ''; Http = ''; Https = ''; Bypass = @(); Warnings = $warnings }
}

# サービスに書くプロキシ設定を決める。
function Resolve-ProxySettings {
    param(
        [string]$Mode = 'auto',
        [string[]]$Bypass = @(),
        [pscredential]$Credential,
        [string]$HostAddress = '127.0.0.1',
        [scriptblock]$Detect = { Find-SystemProxy }
    )
    $warnings = @()
    $http = ''; $https = ''; $source = ''; $found = @()
    switch -Regex ($Mode) {
        '^(?i)none$' { $source = '使わない（-Proxy none）' }
        '^(?i)auto$' {
            $d = & $Detect
            $warnings += @($d.Warnings)
            if ($d.Http -or $d.Https) {
                $http = $d.Http; $https = $d.Https; $source = $d.Source; $found = @($d.Bypass)
            }
            else { $source = '見つからない（直結）' }
        }
        default {
            $http = ConvertTo-ProxyUrl $Mode
            $https = $http
            $source = '-Proxy で指定'
        }
    }
    if ($Credential) {
        $http = Add-ProxyCredential $http $Credential
        $https = Add-ProxyCredential $https $Credential
    }

    $noProxy = ''
    if ($http -or $https) {
        $conv = ConvertTo-NoProxyList (@($found) + @($Bypass))
        foreach ($i in $conv.Ignored) {
            if ($i -match '^<(?i)local>$') {
                $warnings += '除外の <local>（ドットのないホスト名）は NO_PROXY で表せないので外した。必要なら -ProxyBypass に名前を足す'
            }
            elseif ($i -notmatch '^<') {
                $warnings += "除外の $i は NO_PROXY で表せないので外した"
            }
        }
        $own = @()
        if ($HostAddress -and $HostAddress -notin @('0.0.0.0', '::', '[::]')) { $own = @($HostAddress) }
        $noProxy = Join-NoProxy (@($Script:AlwaysNoProxy) + $own + @($conv.Entries))
    }
    return [pscustomobject]@{
        Source = $source; Http = $http; Https = $https; NoProxy = $noProxy; Warnings = @($warnings | Where-Object { $_ })
    }
}

# ---------------------------------------------------------------------------
# サービス定義（WinSW の XML）
# ---------------------------------------------------------------------------

function Get-ServiceEnvironment {
    param([string]$DaguHome, [string]$HostAddress, [int]$Port, $ProxySettings, [string[]]$ExtraPassthrough = @())
    $envs = [ordered]@{
        DAGU_HOME = $DaguHome
        DAGU_HOST = $HostAddress
        DAGU_PORT = [string]$Port
    }
    $pass = @()
    if ($ProxySettings -and ($ProxySettings.Http -or $ProxySettings.Https)) {
        if ($ProxySettings.Http) { $envs['HTTP_PROXY'] = $ProxySettings.Http; $pass += 'HTTP_PROXY' }
        if ($ProxySettings.Https) { $envs['HTTPS_PROXY'] = $ProxySettings.Https; $pass += 'HTTPS_PROXY' }
        $envs['NO_PROXY'] = $ProxySettings.NoProxy
        $pass += 'NO_PROXY'
    }
    foreach ($item in @($ExtraPassthrough)) {
        if ($item) { $pass += @($item -split '[,\s]+' | Where-Object { $_ }) }
    }
    # Dagu はステップに既定で環境変数を絞って渡す。プロキシはステップ（git / curl / pip など）にも要る。
    # この指定は config.yaml の env_passthrough を置き換えるので、他に渡す名前は -EnvPassthrough で足す。
    $joined = Join-NoProxy $pass
    if ($joined) { $envs['DAGU_ENV_PASSTHROUGH'] = $joined }
    return $envs
}

function New-ServiceXml {
    param(
        [string]$ServiceName,
        [string]$DaguExe,
        [string]$DaguHome,
        [System.Collections.IDictionary]$Environment
    )
    $esc = { param($s) [System.Security.SecurityElement]::Escape([string]$s) }
    $logDir = Join-Path $DaguHome 'logs\service'
    $lines = @(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<service>'
        "  <id>$(& $esc $ServiceName)</id>"
        "  <name>$(& $esc $ServiceName)</name>"
        '  <description>Dagu workflow engine (dagu start-all)</description>'
        "  <executable>$(& $esc $DaguExe)</executable>"
        '  <arguments>start-all</arguments>'
        "  <workingdirectory>$(& $esc $DaguHome)</workingdirectory>"
        '  <startmode>Automatic</startmode>'
        '  <delayedAutoStart>true</delayedAutoStart>'
        '  <onfailure action="restart" delay="10 sec"/>'
        '  <onfailure action="restart" delay="30 sec"/>'
        '  <resetfailure>1 hour</resetfailure>'
        '  <stoptimeout>30 sec</stoptimeout>'
        "  <logpath>$(& $esc $logDir)</logpath>"
        '  <log mode="roll-by-size">'
        '    <sizeThreshold>10240</sizeThreshold>'
        '    <keepFiles>8</keepFiles>'
        '  </log>'
    )
    foreach ($k in $Environment.Keys) {
        $lines += "  <env name=`"$(& $esc $k)`" value=`"$(& $esc $Environment[$k])`"/>"
    }
    $lines += '</service>'
    return ($lines -join "`r`n") + "`r`n"
}

# ---------------------------------------------------------------------------
# ダウンロードと導入（副作用あり）
# ---------------------------------------------------------------------------

function Write-Step { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Note { param([string]$Message) Write-Host "    $Message" }
function Write-Caution { param([string]$Message) Write-Warning $Message }

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-WebParams {
    param($ProxySettings, [string]$Mode, [switch]$UseDefaultCredentials)
    $p = @{ UseBasicParsing = $true }
    $url = if ($ProxySettings.Https) { $ProxySettings.Https } else { $ProxySettings.Http }
    if ($url) {
        $split = Split-ProxyUrl $url
        $p['Proxy'] = $split.Url
        if ($split.Credential) { $p['ProxyCredential'] = $split.Credential }
        elseif ($UseDefaultCredentials) { $p['ProxyUseDefaultCredentials'] = $true }
    }
    elseif ($Mode -match '^(?i)none$' -and $PSVersionTable.PSVersion.Major -ge 7) {
        $p['NoProxy'] = $true
    }
    return $p
}

function Initialize-Network {
    param([string]$Mode, [switch]$UseDefaultCredentials)
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    }
    catch { }
    if ($PSVersionTable.PSVersion.Major -lt 7) {
        if ($Mode -match '^(?i)none$') {
            [Net.WebRequest]::DefaultWebProxy = New-Object Net.WebProxy
        }
        elseif ($UseDefaultCredentials -and [Net.WebRequest]::DefaultWebProxy) {
            [Net.WebRequest]::DefaultWebProxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials
        }
    }
}

function Invoke-Download {
    param([string]$Url, [string]$OutFile, [hashtable]$WebParams)
    Write-Note "取得: $Url"
    try {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile @WebParams
    }
    catch {
        $msg = $_.Exception.Message
        throw "取得に失敗した: $Url`n  $msg`n  プロキシの指定（-Proxy / -ProxyCredential / -ProxyUseDefaultCredentials）を確かめるか、-DaguArchive / -WinSWPath で手元のファイルを渡すこと。"
    }
}

# 最新版の名前を、releases/latest の転送先から読む。
# 社内プロキシの出口は多くの PC で共有されるため、回数制限のある GitHub API は避ける。
function Get-LatestDaguVersion {
    param([hashtable]$WebParams)
    $r = Invoke-WebRequest -Uri "$Script:DaguRepo/releases/latest" @WebParams
    $final = $null
    $base = $r.BaseResponse
    if ($base.PSObject.Properties['ResponseUri'] -and $base.ResponseUri) { $final = $base.ResponseUri.AbsoluteUri }
    elseif ($base.PSObject.Properties['RequestMessage'] -and $base.RequestMessage) { $final = $base.RequestMessage.RequestUri.AbsoluteUri }
    if ($final -and $final -match '/releases/tag/(?<tag>[^/?#]+)') { return $Matches['tag'] }
    throw "最新版を判別できなかった（$final）。-Version v2.0.0 のように版を指定すること。"
}

function Get-WindowsArch {
    if (-not [Environment]::Is64BitOperatingSystem) { return '386' }
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { return 'arm64' }
    return 'amd64'
}

function Test-ArchiveChecksum {
    param([string]$Archive, [string]$ChecksumFile, [string]$AssetName)
    $line = Get-Content -LiteralPath $ChecksumFile | Where-Object { $_ -match ('\s\*?' + [regex]::Escape($AssetName) + '$') } | Select-Object -First 1
    if (-not $line) { throw "checksums.txt に $AssetName が見つからない。" }
    $expected = ($line -split '\s+')[0].ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($expected -ne $actual) { throw "$AssetName のハッシュが合わない（壊れたか、途中で書き換えられた）。" }
}

function Expand-DaguExe {
    param([string]$Archive, [string]$WorkDir)
    $dest = Join-Path $WorkDir 'extract'
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    if ($Archive -match '\.zip$') {
        Expand-Archive -LiteralPath $Archive -DestinationPath $dest -Force
    }
    else {
        & tar.exe -xzf $Archive -C $dest
        if ($LASTEXITCODE -ne 0) { throw "展開に失敗した: $Archive" }
    }
    $exe = Get-ChildItem -LiteralPath $dest -Recurse -Filter 'dagu.exe' | Select-Object -First 1
    if (-not $exe) { throw "配布物に dagu.exe が入っていない: $Archive" }
    return $exe.FullName
}

function Protect-File {
    # パスワード入りの定義を、SYSTEM と Administrators だけが読めるようにする。
    param([string]$Path)
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $id = New-Object System.Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($id, 'FullControl', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Invoke-WinSW {
    param([string]$Wrapper, [string]$Command, [switch]$IgnoreError)
    # Windows PowerShell 5.1 は、Stop のまま標準エラーを 2>&1 で拾うと例外にしてしまう。
    $ErrorActionPreference = 'Continue'
    $out = & $Wrapper $Command 2>&1
    if ($LASTEXITCODE -ne 0 -and -not $IgnoreError) {
        throw "サービスの $Command に失敗した（$LASTEXITCODE）: $($out -join ' ')"
    }
}

function Wait-DaguHealthy {
    param([string]$Url, [int]$Seconds = 60)
    for ($i = 0; $i -lt $Seconds; $i++) {
        try {
            # 自分自身への確認はプロキシを通さない。
            $req = [Net.HttpWebRequest]::Create("$Url/api/v1/health")
            $req.Proxy = New-Object Net.WebProxy
            $req.Timeout = 3000
            $res = $req.GetResponse()
            $res.Close()
            return $true
        }
        catch { Start-Sleep -Seconds 1 }
    }
    return $false
}

function Uninstall-DaguService {
    param([string]$ServiceName, [string]$InstallDir, [string]$DaguHome)
    $wrapper = Join-Path $InstallDir 'dagu-service.exe'
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) {
        Write-Note "サービス $ServiceName は登録されていない。"
        return
    }
    Write-Step "サービス $ServiceName を止めて登録を外す"
    if (Test-Path -LiteralPath $wrapper) {
        Invoke-WinSW $wrapper 'stop' -IgnoreError
        Invoke-WinSW $wrapper 'uninstall'
    }
    else {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        & sc.exe delete $ServiceName | Out-Null
    }
    Write-Note "外した。$InstallDir（本体）と $DaguHome（DAG・履歴）は残してある。要らなければ手で消すこと。"
}

function Install-DaguService {
    $resolveArgs = @{ Mode = $Proxy; Bypass = $ProxyBypass; HostAddress = $HostAddress }
    if ($ProxyCredential) { $resolveArgs['Credential'] = $ProxyCredential }
    $ps = Resolve-ProxySettings @resolveArgs
    $daguExe = Join-Path $InstallDir 'dagu.exe'
    $wrapper = Join-Path $InstallDir 'dagu-service.exe'
    $xmlPath = Join-Path $InstallDir 'dagu-service.xml'
    $envs = Get-ServiceEnvironment -DaguHome $DaguHome -HostAddress $HostAddress -Port $Port -ProxySettings $ps -ExtraPassthrough $EnvPassthrough
    $xml = New-ServiceXml -ServiceName $ServiceName -DaguExe $daguExe -DaguHome $DaguHome -Environment $envs

    Write-Step 'プロキシ'
    Write-Note "見つけた場所: $($ps.Source)"
    if ($ps.Http -or $ps.Https) {
        Write-Note "HTTP_PROXY  = $(Hide-ProxyPassword $ps.Http)"
        Write-Note "HTTPS_PROXY = $(Hide-ProxyPassword $ps.Https)"
        Write-Note "NO_PROXY    = $($ps.NoProxy)"
    }
    foreach ($w in $ps.Warnings) { Write-Caution $w }
    if ($ProxyUseDefaultCredentials -and -not $ProxyCredential) {
        Write-Caution 'Windows 資格情報によるプロキシ認証は、このスクリプトのダウンロードにだけ効く。サービスの Dagu には効かないので、要るなら -ProxyCredential を渡すこと'
    }

    if ($DryRun) {
        Write-Step "サービス定義（$xmlPath に書く内容）"
        $shown = $xml
        foreach ($u in @($ps.Http, $ps.Https) | Where-Object { $_ } | Select-Object -Unique) {
            $shown = $shown.Replace([System.Security.SecurityElement]::Escape($u), [System.Security.SecurityElement]::Escape((Hide-ProxyPassword $u)))
        }
        Write-Host $shown
        Write-Note '-DryRun なので何も変えていない。'
        return
    }

    if (-not (Test-IsAdmin)) { throw '管理者の PowerShell で実行すること（サービスの登録に要る）。' }
    Initialize-Network -Mode $Proxy -UseDefaultCredentials:$ProxyUseDefaultCredentials
    $web = Get-WebParams -ProxySettings $ps -Mode $Proxy -UseDefaultCredentials:$ProxyUseDefaultCredentials

    $work = Join-Path ([IO.Path]::GetTempPath()) ('dagu-service-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    try {
        Write-Step 'Dagu 本体'
        if ($DaguArchive) {
            $archive = (Resolve-Path -LiteralPath $DaguArchive).Path
            $sums = Join-Path (Split-Path -Parent $archive) 'checksums.txt'
            if (Test-Path -LiteralPath $sums) { Test-ArchiveChecksum $archive $sums (Split-Path -Leaf $archive) }
            else { Write-Caution "checksums.txt が $archive の隣にないので、ハッシュを確かめずに使う" }
        }
        else {
            $ver = $Version
            if ($ver -eq 'latest') { $ver = Get-LatestDaguVersion -WebParams $web }
            if ($ver -notmatch '^v') { $ver = "v$ver" }
            $asset = "dagu_$($ver.TrimStart('v'))_windows_$(Get-WindowsArch).tar.gz"
            $archive = Join-Path $work $asset
            $sums = Join-Path $work 'checksums.txt'
            Invoke-Download "$Script:DaguRepo/releases/download/$ver/$asset" $archive $web
            Invoke-Download "$Script:DaguRepo/releases/download/$ver/checksums.txt" $sums $web
            Test-ArchiveChecksum $archive $sums $asset
            Write-Note "版: $ver"
        }
        $newExe = Expand-DaguExe $archive $work

        Write-Step 'サービスの包み（WinSW）'
        $newWrapper = Join-Path $work 'dagu-service.exe'
        if ($WinSWPath) {
            Copy-Item -LiteralPath (Resolve-Path -LiteralPath $WinSWPath).Path -Destination $newWrapper
        }
        elseif (Test-Path -LiteralPath $wrapper) {
            Copy-Item -LiteralPath $wrapper -Destination $newWrapper
            Write-Note "既にある $wrapper を使う"
        }
        else {
            $winswAsset = if ((Get-WindowsArch) -eq '386') { 'WinSW-x86.exe' } else { 'WinSW-x64.exe' }
            Invoke-Download "$Script:WinSWRepo/releases/download/$WinSWVersion/$winswAsset" $newWrapper $web
        }

        $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Step "登録済みのサービス $ServiceName を止めて置き換える"
            if (Test-Path -LiteralPath $wrapper) {
                Invoke-WinSW $wrapper 'stop' -IgnoreError
                Invoke-WinSW $wrapper 'uninstall' -IgnoreError
            }
            else {
                Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
                & sc.exe delete $ServiceName | Out-Null
            }
            Start-Sleep -Seconds 2
        }

        Write-Step "配置: $InstallDir"
        New-Item -ItemType Directory -Force -Path $InstallDir, $DaguHome, (Join-Path $DaguHome 'logs\service') | Out-Null
        Copy-Item -LiteralPath $newExe -Destination $daguExe -Force
        Copy-Item -LiteralPath $newWrapper -Destination $wrapper -Force
        if (Test-Path -LiteralPath $xmlPath) {
            Copy-Item -LiteralPath $xmlPath -Destination "$xmlPath.$((Get-Date).ToString('yyyyMMddHHmmss')).bak" -Force
            Get-ChildItem -LiteralPath $InstallDir -Filter 'dagu-service.xml.*.bak' | ForEach-Object { Protect-File $_.FullName }
        }
        [IO.File]::WriteAllText($xmlPath, $xml, (New-Object Text.UTF8Encoding($true)))
        Protect-File $xmlPath

        Write-Step "サービス $ServiceName を登録して起動"
        Invoke-WinSW $wrapper 'install'
        Invoke-WinSW $wrapper 'start'

        $probeHost = if ($HostAddress -in @('0.0.0.0', '::', '[::]')) { '127.0.0.1' } else { $HostAddress }
        $url = "http://${probeHost}:$Port"
        if (Wait-DaguHealthy $url) {
            Write-Step "動いている: $url"
        }
        else {
            Write-Caution "60 秒待っても $url が応答しない。ログを見ること: $(Join-Path $DaguHome 'logs\service')"
        }
        Write-Note "DAG の置き場所: $(Join-Path $DaguHome 'dags')"
        Write-Note "手で dagu を叩くときは DAGU_HOME=$DaguHome を合わせる（例: `$env:DAGU_HOME='$DaguHome'; & '$daguExe' status <DAG>）"
    }
    finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ドットで読み込まれたとき（テスト）は関数の定義だけで終える。
if ($MyInvocation.InvocationName -eq '.') { return }

if ($Uninstall) {
    if (-not $DryRun -and -not (Test-IsAdmin)) { throw '管理者の PowerShell で実行すること（サービスの削除に要る）。' }
    if ($DryRun) { Write-Note "-DryRun: サービス $ServiceName の登録を外す（ファイルは消さない）。"; return }
    Uninstall-DaguService -ServiceName $ServiceName -InstallDir $InstallDir -DaguHome $DaguHome
    return
}
Install-DaguService
