#Requires -Version 5.1
<#
.SYNOPSIS
    Windows 起動時に複数の WSL ターミナルを自動起動するスクリプト。

.DESCRIPTION
    config.json に登録されたフォルダをカレントディレクトリとして
    WSL ターミナルを起動し、指定されたコマンドを実行します。

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

# -------------------------------------------------------
# 定数・初期設定
# -------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $ConfigPath) {
    $ConfigPath = Join-Path $ScriptDir "config.json"
}

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp][$Level] $Message"
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

$settings    = $config.settings
$terminals   = $config.terminals

$terminalApp         = if ($settings.terminalApp)         { $settings.terminalApp }         else { "wt" }
$delayMs             = if ($settings.delayBetweenLaunchesMs) { $settings.delayBetweenLaunchesMs } else { 500 }
$defaultDistro       = if ($settings.defaultDistro)       { $settings.defaultDistro }       else { "" }

# -------------------------------------------------------
# Windows Terminal (wt.exe) 存在チェック
# -------------------------------------------------------
$wtPath = Get-Command "wt.exe" -ErrorAction SilentlyContinue
$useWindowsTerminal = ($terminalApp -eq "wt") -and ($null -ne $wtPath)

if ($terminalApp -eq "wt" -and -not $wtPath) {
    Write-Log "Windows Terminal (wt.exe) が見つかりません。wsl.exe で起動します。" "WARN"
}

# -------------------------------------------------------
# 有効なターミナル一覧を取得
# -------------------------------------------------------
$enabledTerminals = $terminals | Where-Object { $_.enabled -eq $true }

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

    $args = @()
    $first = $true

    foreach ($term in $TerminalList) {
        $distro    = if ($term.distro) { $term.distro } else { $defaultDistro }
        $keepOpen  = if ($null -ne $term.keepOpen) { [bool]$term.keepOpen } else { $true }
        $bashCmd   = Build-BashCommand -WslPath $term.wslPath -Command $term.command -KeepOpen $keepOpen

        # Windows Terminal のスタートディレクトリ (UNC 形式)
        $distroName = if ($distro) { $distro } else { "Ubuntu" }
        $uncPath = "\\wsl`$\$distroName$($term.wslPath -replace '/', '\')"

        if ($first) {
            # 最初のタブ: wt の起動直後に開くタブ
            $args += "new-tab"
            $first = $false
        } else {
            # 2 枚目以降: セパレーター `;` で区切って追加タブ
            $args += ";"
            $args += "new-tab"
        }

        $args += "--title"
        $args += $term.name

        $args += "--startingDirectory"
        $args += $uncPath

        if ($distro) {
            $args += "wsl.exe"
            $args += "-d"
            $args += $distro
            $args += "--"
            $args += "bash"
            $args += "-c"
            $args += $bashCmd
        } else {
            $args += "wsl.exe"
            $args += "--"
            $args += "bash"
            $args += "-c"
            $args += $bashCmd
        }
    }

    Write-Log "Windows Terminal を起動します..."
    Start-Process "wt.exe" -ArgumentList $args
}

# -------------------------------------------------------
# wsl.exe を個別ウィンドウで起動 (Windows Terminal なし)
# -------------------------------------------------------
function Start-WithWsl {
    param($TerminalList)

    foreach ($term in $TerminalList) {
        $distro   = if ($term.distro) { $term.distro } else { $defaultDistro }
        $keepOpen = if ($null -ne $term.keepOpen) { [bool]$term.keepOpen } else { $true }
        $bashCmd  = Build-BashCommand -WslPath $term.wslPath -Command $term.command -KeepOpen $keepOpen

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

        if ($delayMs -gt 0) {
            Start-Sleep -Milliseconds $delayMs
        }
    }
}

# -------------------------------------------------------
# メイン処理
# -------------------------------------------------------
try {
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
