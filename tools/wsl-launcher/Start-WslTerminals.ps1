#Requires -Version 5.1
<#
.SYNOPSIS
    Windows 起動時に複数の WSL ターミナルを自動起動するスクリプト。

.DESCRIPTION
    config.json に登録されたフォルダをカレントディレクトリとして
    WSL ターミナルを起動し、指定されたコマンドを実行します。
    Windows Terminal の settings.json からプロファイル情報を取得し、
    適切なディストロで起動します。

.PARAMETER ConfigPath
    設定ファイルのパス。省略時はスクリプトと同じフォルダの config.json を使用します。

.EXAMPLE
    .\Start-WslTerminals.ps1
    .\Start-WslTerminals.ps1 -ConfigPath "C:\MyConfig\config.json"
#>
param(
    [string]$ConfigPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# コンソールエンコーディングを UTF-8 に統一 (Windows PowerShell 5.1 / Shift-JIS 環境対策)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
$OutputEncoding           = [System.Text.Encoding]::UTF8

# -------------------------------------------------------
# 定数・初期設定
# -------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogPath   = Join-Path $ScriptDir "launcher.log"

if (-not $ConfigPath) {
    $ConfigPath = Join-Path $ScriptDir "config.json"
}

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp][$Level] $Message"
    Write-Host $line
    try { Add-Content -Path $LogPath -Value $line -Encoding UTF8 } catch {}
}

# -------------------------------------------------------
# Windows Terminal settings.json の読み込み
# -------------------------------------------------------
function Get-WtSettings {
    $searchPaths = @(
        (Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"),
        (Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\Windows Terminal\settings.json")
    )
    foreach ($p in $searchPaths) {
        if (Test-Path $p) {
            try {
                # JSONC のコメントを除去してパース
                $raw = Get-Content $p -Raw -Encoding UTF8
                $raw = $raw -replace '(?m)//[^\r\n]*', ''
                $raw = $raw -replace '(?s)/\*.*?\*/', ''
                return $raw | ConvertFrom-Json
            } catch {
                Write-Log "Windows Terminal settings.json の読み込みに失敗しました: $p" "WARN"
            }
        }
    }
    return $null
}

# WT プロファイル名からディストロ名を解決する
function Resolve-DistroFromProfile {
    param([string]$ProfileName, $WtSettings)

    if (-not $WtSettings -or -not $ProfileName) { return $ProfileName }

    $profiles = @($WtSettings.profiles.list)
    $prof     = $profiles | Where-Object { $_.name -eq $ProfileName } | Select-Object -First 1

    if (-not $prof) { return $ProfileName }

    # 自動生成 WSL プロファイル: source が "Windows.Terminal.Wsl" → name がディストロ名
    if ($prof.source -eq "Windows.Terminal.Wsl") {
        return $prof.name
    }

    # カスタムプロファイル: commandline から -d フラグを解析
    if ($prof.commandline -match '(?:^|\s)wsl(?:\.exe)?\s+.*?-d\s+(\S+)') {
        return $Matches[1]
    }

    return $ProfileName
}

# WT のデフォルト WSL プロファイル名を取得する
function Get-DefaultWslProfileName {
    param($WtSettings)

    if (-not $WtSettings) { return "" }

    $defaultGuid = $WtSettings.defaultProfile
    $profiles    = @($WtSettings.profiles.list)

    # デフォルトプロファイルが WSL ならそれを使用
    if ($defaultGuid) {
        $def = $profiles | Where-Object { $_.guid -eq $defaultGuid } | Select-Object -First 1
        if ($def -and (-not $def.hidden) -and
            ($def.source -eq "Windows.Terminal.Wsl" -or $def.commandline -like "*wsl*")) {
            return $def.name
        }
    }

    # 最初の非表示でない WSL プロファイルにフォールバック
    $firstWsl = $profiles |
        Where-Object { (-not $_.hidden) -and
                       ($_.source -eq "Windows.Terminal.Wsl" -or $_.commandline -like "*wsl*") } |
        Select-Object -First 1

    return if ($firstWsl) { $firstWsl.name } else { "" }
}

# -------------------------------------------------------
# WSL 起動完了を待機する
# wsl.exe -e echo ok が成功するまでポーリングし、
# タイムアウトした場合は $false を返す
# -------------------------------------------------------
function Wait-WslReady {
    param(
        [string]$Distro          = "",
        [int]$TimeoutSeconds     = 60,
        [int]$RetryIntervalMs    = 3000
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    Write-Log "WSL の起動を待機中$(if ($Distro) { " (ディストロ: $Distro)" })..."

    while ((Get-Date) -lt $deadline) {
        try {
            $wslArgs = @()
            if ($Distro) { $wslArgs += @("-d", $Distro) }
            $wslArgs += @("-e", "echo", "ok")

            $result = & wsl.exe @wslArgs 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Log "WSL の起動を確認しました$(if ($Distro) { " (ディストロ: $Distro)" })。"
                return $true
            }
        } catch {
            # 起動中の例外は無視してリトライ
        }
        Start-Sleep -Milliseconds $RetryIntervalMs
    }

    Write-Log "WSL の起動待機がタイムアウトしました (${TimeoutSeconds}秒)。" "WARN"
    return $false
}

# -------------------------------------------------------
# 設定ファイル読み込み
# -------------------------------------------------------
if (-not (Test-Path $ConfigPath)) {
    Write-Log "設定ファイルが見つかりません: $ConfigPath" "ERROR"
    exit 1
}

try {
    $config = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    Write-Log "設定ファイルの読み込みに失敗しました: $_" "ERROR"
    exit 1
}

$settings  = $config.settings
$terminals = @($config.terminals)  # PS 5.1 では JSON 配列が1要素のとき単一オブジェクトになるため強制配列化

# -------------------------------------------------------
# Windows Terminal settings.json を読み込み、デフォルトプロファイルを取得
# -------------------------------------------------------
$wtSettings     = Get-WtSettings
$defaultProfile = Get-DefaultWslProfileName -WtSettings $wtSettings

if ($wtSettings) {
    Write-Log "Windows Terminal settings.json を読み込みました。デフォルト WSL プロファイル: $(if ($defaultProfile) { $defaultProfile } else { '(なし)' })"
} else {
    Write-Log "Windows Terminal settings.json が見つかりません。" "WARN"
}

$wslWaitTimeout = if ($settings.wslWaitTimeoutSeconds) { [int]$settings.wslWaitTimeoutSeconds } else { 60 }
$wslWaitEnabled = ($wslWaitTimeout -gt 0)

# -------------------------------------------------------
# Windows Terminal (wt.exe) 存在チェック
# -------------------------------------------------------
$wtPath             = Get-Command "wt.exe" -ErrorAction SilentlyContinue
$useWindowsTerminal = ($null -ne $wtPath)

if (-not $wtPath) {
    Write-Log "Windows Terminal (wt.exe) が見つかりません。wsl.exe で起動します。" "WARN"
}

# -------------------------------------------------------
# 有効なターミナル一覧を取得
# enabled が明示的に false 以外はすべて有効とみなす
# -------------------------------------------------------
$enabledTerminals = @($terminals | Where-Object { $_.enabled -ne $false })

if ($enabledTerminals.Count -eq 0) {
    Write-Log "有効なターミナルが設定されていません。config.json を確認してください。" "WARN"
    exit 0
}

Write-Log "起動するターミナル数: $($enabledTerminals.Count)"

# -------------------------------------------------------
# WSL シェルコマンドの生成
# keepOpen=true の場合、コマンド終了後もシェルを維持する
# -------------------------------------------------------
function Build-BashCommand {
    param(
        [string]$WslPath,
        [string]$Command,
        [bool]$KeepOpen
    )

    # シングルクォートをエスケープ
    $safePath    = $WslPath -replace "'", "'\\''"
    $safeCommand = $Command -replace "'", "'\\''"

    if ($KeepOpen) {
        # コマンド実行後も bash を維持
        return "cd '$safePath' && ($safeCommand); exec bash"
    } else {
        return "cd '$safePath' && $safeCommand"
    }
}

# -------------------------------------------------------
# Windows Terminal を使って全タブをまとめて起動
# -------------------------------------------------------
function Start-WithWindowsTerminal {
    param($TerminalList)

    # 注意: $args は PowerShell 自動変数のため使用禁止。$wtArgs を使用する。
    $wtArgs = @()
    $first = $true

    foreach ($term in $TerminalList) {
        # profile → distro 解決 (後方互換性のため distro フィールドも参照)
        $profileName = if ($term.profile)      { $term.profile }
                       elseif ($term.distro)   { $term.distro }
                       else                    { $defaultProfile }
        $distro      = Resolve-DistroFromProfile -ProfileName $profileName -WtSettings $wtSettings
        $keepOpen    = if ($null -ne $term.keepOpen) { [bool]$term.keepOpen } else { $true }
        $bashCmd     = Build-BashCommand -WslPath $term.wslPath -Command $term.command -KeepOpen $keepOpen

        if ($first) {
            # 最初のタブ: wt の起動直後に開くタブ
            $wtArgs += "new-tab"
            $first = $false
        } else {
            # 2 枚目以降: セパレーター `;` で区切って追加タブ
            $wtArgs += ";"
            $wtArgs += "new-tab"
        }

        $wtArgs += "--title"
        $wtArgs += $term.name

        # --profile で WT プロファイルの外観設定 (フォント・配色等) を継承
        if ($profileName) {
            $wtArgs += "--profile"
            $wtArgs += $profileName
        }

        # --startingDirectory に UNC パス (\\wsl$\...) は環境依存で失敗するため廃止。
        # WT オプションと wsl コマンドを -- で明示的に区切り、
        # wsl.exe --cd で WSL 内パスを直接指定する。
        $wtArgs += "--"
        $wtArgs += "wsl.exe"
        if ($distro) {
            $wtArgs += "-d"
            $wtArgs += $distro
        }
        $wtArgs += "--cd"
        $wtArgs += $term.wslPath
        $wtArgs += "--"
        $wtArgs += "bash"
        $wtArgs += "-c"
        $wtArgs += $bashCmd
    }

    Write-Log "Windows Terminal を起動します..."
    Start-Process "wt.exe" -ArgumentList $wtArgs
}

# -------------------------------------------------------
# wsl.exe を個別ウィンドウで起動 (Windows Terminal なし)
# -------------------------------------------------------
function Start-WithWsl {
    param($TerminalList)

    foreach ($term in $TerminalList) {
        # profile → distro 解決 (後方互換性のため distro フィールドも参照)
        $profileName = if ($term.profile)      { $term.profile }
                       elseif ($term.distro)   { $term.distro }
                       else                    { $defaultProfile }
        $distro      = Resolve-DistroFromProfile -ProfileName $profileName -WtSettings $wtSettings
        $keepOpen    = if ($null -ne $term.keepOpen) { [bool]$term.keepOpen } else { $true }
        $bashCmd     = Build-BashCommand -WslPath $term.wslPath -Command $term.command -KeepOpen $keepOpen

        Write-Log "起動: $($term.name) ($($term.wslPath))"

        $wslArgs = @()
        if ($distro) {
            $wslArgs += "-d"
            $wslArgs += $distro
        }
        $wslArgs += "--"
        $wslArgs += "bash"
        $wslArgs += "-c"
        $wslArgs += $bashCmd

        # 新しいコンソールウィンドウで wsl を起動
        Start-Process "wsl.exe" -ArgumentList $wslArgs

        Start-Sleep -Milliseconds 500
    }
}

# -------------------------------------------------------
# メイン処理
# -------------------------------------------------------
try {
    # WSL が起動完了するまで待機（使用するディストロごとに1回）
    if ($wslWaitEnabled) {
        $distrosToWait = @($enabledTerminals | ForEach-Object {
            $profileName = if ($_.profile)    { $_.profile }
                           elseif ($_.distro) { $_.distro }
                           else               { $defaultProfile }
            Resolve-DistroFromProfile -ProfileName $profileName -WtSettings $wtSettings
        } | Sort-Object -Unique)

        foreach ($distro in $distrosToWait) {
            $ready = Wait-WslReady -Distro $distro -TimeoutSeconds $wslWaitTimeout
            if (-not $ready) {
                Write-Log "WSL (ディストロ: $distro) の起動確認に失敗しました。起動を試みますが不安定な場合があります。" "WARN"
            }
        }
    }

    if ($useWindowsTerminal) {
        Start-WithWindowsTerminal -TerminalList $enabledTerminals
    } else {
        Start-WithWsl -TerminalList $enabledTerminals
    }
    Write-Log "すべてのターミナルの起動要求が完了しました。"
} catch {
    Write-Log "ターミナル起動中にエラーが発生しました: $_" "ERROR"
    exit 1
}
