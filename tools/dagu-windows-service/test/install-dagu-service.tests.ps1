# install-dagu-service.ps1 のテスト。Pester を使わず、Windows PowerShell 5.1 と PowerShell 7 の両方で動く。
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\dagu-windows-service\test\install-dagu-service.tests.ps1
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path (Split-Path -Parent $here) 'install-dagu-service.ps1'
. $scriptPath

$Script:Failures = 0
$Script:Count = 0
function Check {
    param([string]$Name, [scriptblock]$Body)
    $Script:Count++
    try { & $Body; Write-Host "ok   $Name" }
    catch { $Script:Failures++; Write-Host "FAIL $Name`n     $($_.Exception.Message)" -ForegroundColor Red }
}
function Same {
    param($Actual, $Expected)
    $a = (@($Actual) | ForEach-Object { [string]$_ }) -join '|'
    $e = (@($Expected) | ForEach-Object { [string]$_ }) -join '|'
    if ($a -cne $e) { throw "expected [$e] but got [$a]" }
}
function New-Cred {
    param([string]$User, [string]$Pass)
    New-Object System.Management.Automation.PSCredential($User, (ConvertTo-SecureString $Pass -AsPlainText -Force))
}

Check 'ConvertTo-ProxyUrl はスキームを補い末尾の / を落とす' {
    Same (ConvertTo-ProxyUrl 'proxy:8080') 'http://proxy:8080'
    Same (ConvertTo-ProxyUrl ' http://proxy:8080/ ') 'http://proxy:8080'
    Same (ConvertTo-ProxyUrl '') ''
}

Check 'プロキシ サーバーの単一指定は http と https の両方に使う' {
    $p = ConvertFrom-ProxyServerString 'proxy.example.co.jp:8080'
    Same $p.Http 'http://proxy.example.co.jp:8080'
    Same $p.Https 'http://proxy.example.co.jp:8080'
}

Check 'スキームごとの指定を分け、socks は外す' {
    $p = ConvertFrom-ProxyServerString 'http=a:3128;https=b:3129;socks=c:1080'
    Same $p.Http 'http://a:3128'
    Same $p.Https 'http://b:3129'
    Same $p.Ignored 'socks=c:1080'
}

Check '除外リストを NO_PROXY の書式に寄せる' {
    $r = ConvertTo-NoProxyList @('*.corp.example;10.*;192.168.1.*;172.16.*.*;<local>;intra*;host:8080;http://wiki.example/', '.already,10.0.0.0/8')
    Same $r.Entries @('.corp.example', '10.0.0.0/8', '192.168.1.0/24', '172.16.0.0/16', 'host:8080', 'wiki.example', '.already', '10.0.0.0/8')
    Same $r.Ignored @('<local>', 'intra*')
}

Check '途中に * がある IP は書き換えない' {
    $r = ConvertTo-NoProxyList @('10.*.1.2')
    Same $r.Entries @()
    Same $r.Ignored '10.*.1.2'
}

Check 'Join-NoProxy は大文字小文字を無視して重複を落とす' {
    Same (Join-NoProxy @('localhost', 'LOCALHOST', '.a', '', '.a')) 'localhost,.a'
}

Check '資格情報は URL エンコードして埋め、表示では伏せる' {
    $u = Add-ProxyCredential 'http://proxy:8080' (New-Cred 'CORP\taro' 'p@ss:w%rd/1')
    Same $u 'http://CORP%5Ctaro:p%40ss%3Aw%25rd%2F1@proxy:8080'
    Same (Hide-ProxyPassword $u) 'http://CORP%5Ctaro:****@proxy:8080'
    Same (Hide-ProxyPassword 'http://proxy:8080') 'http://proxy:8080'
}

Check 'Split-ProxyUrl は埋めた資格情報を取り出す' {
    $s = Split-ProxyUrl 'http://CORP%5Ctaro:p%40ss%3Aw%25rd%2F1@proxy:8080'
    Same $s.Url 'http://proxy:8080'
    Same $s.Credential.UserName 'CORP\taro'
    Same $s.Credential.GetNetworkCredential().Password 'p@ss:w%rd/1'
    $n = Split-ProxyUrl 'http://proxy:8080'
    Same $n.Url 'http://proxy:8080'
    if ($null -ne $n.Credential) { throw 'credential should be null' }
}

function New-WinHttpBytes {
    param([int]$Flags, [string]$Server, [string]$Bypass)
    $ms = New-Object IO.MemoryStream
    $w = New-Object IO.BinaryWriter($ms)
    $w.Write([uint32]0x28); $w.Write([uint32]5); $w.Write([uint32]$Flags)
    $sb = [Text.Encoding]::ASCII.GetBytes($Server); $w.Write([uint32]$sb.Length); $w.Write($sb)
    $bb = [Text.Encoding]::ASCII.GetBytes($Bypass); $w.Write([uint32]$bb.Length); $w.Write($bb)
    $w.Flush()
    return , $ms.ToArray()
}

Check 'WinHTTP の保存形式を読む' {
    $r = ConvertFrom-WinHttpSettings (New-WinHttpBytes 3 'proxy:8080' '<local>;*.corp')
    Same $r.Server 'proxy:8080'
    Same $r.Bypass '<local>;*.corp'
    if ($null -ne (ConvertFrom-WinHttpSettings (New-WinHttpBytes 1 '' ''))) { throw 'direct should be null' }
    if ($null -ne (ConvertFrom-WinHttpSettings ([byte[]](1, 2, 3)))) { throw 'short should be null' }
}

Check '-Proxy の明示指定: 除外と自分の待ち受けを NO_PROXY に足す' {
    $s = Resolve-ProxySettings -Mode 'proxy:8080' -Bypass @('*.corp.example', '10.*') -HostAddress '192.168.0.5' -Detect { throw 'must not detect' }
    Same $s.Http 'http://proxy:8080'
    Same $s.Https 'http://proxy:8080'
    Same $s.NoProxy 'localhost,127.0.0.1,::1,192.168.0.5,.corp.example,10.0.0.0/8'
}

Check '-Proxy none ではプロキシも NO_PROXY も書かない' {
    $s = Resolve-ProxySettings -Mode 'none' -Bypass @('.x') -Detect { throw 'must not detect' }
    Same $s.Http ''
    Same $s.NoProxy ''
}

Check 'auto: 見つけた設定と除外を使い、<local> は警告する' {
    $fake = { [pscustomobject]@{ Source = 'fake'; Http = 'http://a:1'; Https = 'http://b:2'; Bypass = @('<local>;*.corp'); Warnings = @('w1') } }
    $s = Resolve-ProxySettings -Mode 'auto' -HostAddress '0.0.0.0' -Detect $fake
    Same $s.Source 'fake'
    Same $s.Http 'http://a:1'
    Same $s.Https 'http://b:2'
    Same $s.NoProxy 'localhost,127.0.0.1,::1,.corp'
    if (@($s.Warnings).Count -ne 2) { throw "warnings: $($s.Warnings -join ' / ')" }
}

Check 'auto: 見つからなければ直結' {
    $fake = { [pscustomobject]@{ Source = ''; Http = ''; Https = ''; Bypass = @(); Warnings = @() } }
    $s = Resolve-ProxySettings -Mode 'auto' -Detect $fake
    Same $s.Http ''
    Same $s.NoProxy ''
}

Check '資格情報は http と https の両方に埋める' {
    $s = Resolve-ProxySettings -Mode 'http://proxy:8080' -Credential (New-Cred 'u' 'p') -Detect { }
    Same $s.Http 'http://u:p@proxy:8080'
    Same $s.Https 'http://u:p@proxy:8080'
}

Check 'サービスの環境: プロキシと、ステップへ渡す DAGU_ENV_PASSTHROUGH' {
    $s = Resolve-ProxySettings -Mode 'proxy:8080' -Detect { }
    $e = Get-ServiceEnvironment -DaguHome 'C:\ProgramData\Dagu' -HostAddress '127.0.0.1' -Port 8080 -ProxySettings $s
    Same @($e.Keys) @('DAGU_HOME', 'DAGU_HOST', 'DAGU_PORT', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'DAGU_ENV_PASSTHROUGH')
    Same $e['DAGU_ENV_PASSTHROUGH'] 'HTTP_PROXY,HTTPS_PROXY,NO_PROXY'
    $none = Get-ServiceEnvironment -DaguHome 'C:\d' -HostAddress '127.0.0.1' -Port 9000 -ProxySettings (Resolve-ProxySettings -Mode none)
    Same @($none.Keys) @('DAGU_HOME', 'DAGU_HOST', 'DAGU_PORT')
    Same $none['DAGU_PORT'] '9000'
    $extra = Get-ServiceEnvironment -DaguHome 'C:\d' -HostAddress '127.0.0.1' -Port 8080 -ProxySettings $s -ExtraPassthrough @('GITLAB_TOKEN,no_proxy', 'PYTHONUTF8')
    Same $extra['DAGU_ENV_PASSTHROUGH'] 'HTTP_PROXY,HTTPS_PROXY,NO_PROXY,GITLAB_TOKEN,PYTHONUTF8'
    $only = Get-ServiceEnvironment -DaguHome 'C:\d' -HostAddress '127.0.0.1' -Port 8080 -ProxySettings (Resolve-ProxySettings -Mode none) -ExtraPassthrough 'PYTHONUTF8'
    Same $only['DAGU_ENV_PASSTHROUGH'] 'PYTHONUTF8'
}

Check 'サービス定義は XML として読め、値はエスケープされる' {
    $envs = [ordered]@{ DAGU_HOME = 'C:\Program Data\D&D'; HTTP_PROXY = 'http://u:p%26x@proxy:8080' }
    $text = New-ServiceXml -ServiceName 'Dagu' -DaguExe 'C:\Program Files\Dagu\dagu.exe' -DaguHome 'C:\Program Data\D&D' -Environment $envs
    $x = [xml]$text
    Same $x.service.id 'Dagu'
    Same $x.service.executable 'C:\Program Files\Dagu\dagu.exe'
    Same $x.service.arguments 'start-all'
    $node = $x.service.env | Where-Object { $_.name -eq 'DAGU_HOME' }
    Same $node.value 'C:\Program Data\D&D'
    $node = $x.service.env | Where-Object { $_.name -eq 'HTTP_PROXY' }
    Same $node.value 'http://u:p%26x@proxy:8080'
}

Check '-DryRun は何も変えずにサービス定義を出し、パスワードを伏せる' {
    $exe = (Get-Process -Id $PID).Path
    # 5.1 は Stop のまま子プロセスの標準エラーを 2>&1 で拾うと例外にする。終了コードと出力で判定する。
    $ErrorActionPreference = 'Continue'
    $cmd = "& '$scriptPath' -DryRun -Proxy 'http://u:secret@proxy:8080' -ProxyBypass '.corp,10.*' -InstallDir 'C:\nowhere\Dagu' -DaguHome 'C:\nowhere\Home' 6>&1"
    $out = (& $exe -NoProfile -ExecutionPolicy Bypass -Command $cmd 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE`n$out" }
    if ($out -match 'secret') { throw "password leaked:`n$out" }
    if ($out -notmatch [regex]::Escape('<env name="HTTPS_PROXY" value="http://u:****@proxy:8080"/>')) { throw "no proxy env:`n$out" }
    if ($out -notmatch [regex]::Escape('<env name="NO_PROXY" value="localhost,127.0.0.1,::1,.corp,10.0.0.0/8"/>')) { throw "no NO_PROXY:`n$out" }
    if (Test-Path 'C:\nowhere') { throw 'dry run created files' }
}

Write-Host ""
Write-Host "$($Script:Count - $Script:Failures) / $($Script:Count) passed"
if ($Script:Failures -gt 0) { exit 1 }
exit 0
