#Requires -Version 5.1
<#
.SYNOPSIS
    WSL ターミナルランチャーを Windows スタートアップに登録・解除するスクリプト。

.DESCRIPTION
    タスクスケジューラを使用して、Windows ログイン時に
    Start-WslTerminals.ps1 を自動実行するタスクを登録します。

.PARAMETER Action
    "register"   : スタートアップに登録 (デフォルト)
    "unregister" : スタートアップから削除
    "status"     : 登録状況を表示

.PARAMETER TaskName
    タスクスケジューラに登録するタスク名。
    デフォルト: "WslTerminalLauncher"

.PARAMETER DelaySeconds
    ログイン後、起動を遅延させる秒数。
    ネットワークやWSLの初期化を待つために使用します。
    デフォルト: 10

.EXAMPLE
    # 管理者権限で実行
    .\Register-Startup.ps1 -Action register
    .\Register-Startup.ps1 -Action unregister
    .\Register-Startup.ps1 -Action status
    .\Register-Startup.ps1 -Action register -DelaySeconds 15
#>
param(
    [ValidateSet("register", "unregister", "status")]
    [string]$Action = "register",

    [string]$TaskName = "WslTerminalLauncher",

    [int]$DelaySeconds = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# コンソールエンコーディングを UTF-8 に統一 (Windows PowerShell 5.1 / Shift-JIS 環境対策)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
$OutputEncoding           = [System.Text.Encoding]::UTF8

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$LauncherPath = Join-Path $ScriptDir "Start-WslTerminals.ps1"
$ConfigPath   = Join-Path $ScriptDir "config.json"

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp][$Level] $Message"
}

# -------------------------------------------------------
# 管理者権限チェック (register/unregister は要管理者)
# -------------------------------------------------------
function Assert-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal   = New-Object Security.Principal.WindowsPrincipal($currentUser)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Log "このスクリプトは管理者権限で実行してください。" "ERROR"
        Write-Log "PowerShell を右クリック -> 『管理者として実行』で再実行してください。" "ERROR"
        exit 1
    }
}

# -------------------------------------------------------
# スタートアップ登録
# -------------------------------------------------------
function Register-StartupTask {
    Assert-Administrator

    if (-not (Test-Path $LauncherPath)) {
        Write-Log "ランチャースクリプトが見つかりません: $LauncherPath" "ERROR"
        exit 1
    }

    # 既存タスクがあれば削除して再登録
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Log "既存のタスク '$TaskName' を削除して再登録します..."
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    # PowerShell 実行コマンド (-WindowStyle Hidden でコンソールウィンドウを非表示)
    $psArgs = "-NonInteractive -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LauncherPath`" -ConfigPath `"$ConfigPath`""

    # タスク定義
    $action  = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument $psArgs `
        -WorkingDirectory $ScriptDir

    # ログオン時トリガー + 遅延
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $trigger.Delay = "PT${DelaySeconds}S"

    # 実行ユーザー = 現在のユーザー
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal   = New-ScheduledTaskPrincipal `
        -UserId $currentUser `
        -LogonType Interactive `
        -RunLevel Limited

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "WSL ターミナルランチャー: Windows ログイン時に複数の WSL ターミナルを起動します。" `
        | Out-Null

    Write-Log "タスク '$TaskName' を登録しました。"
    Write-Log "次回ログイン時 ($DelaySeconds 秒後) に自動起動します。"
    Write-Log "手動でテスト実行するには: Start-ScheduledTask -TaskName '$TaskName'"
}

# -------------------------------------------------------
# スタートアップ解除
# -------------------------------------------------------
function Unregister-StartupTask {
    Assert-Administrator

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Log "タスク '$TaskName' は登録されていません。" "WARN"
        return
    }

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Log "タスク '$TaskName' を削除しました。"
}

# -------------------------------------------------------
# 登録状況の確認
# -------------------------------------------------------
function Show-Status {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Log "タスク '$TaskName' は登録されていません。"
        return
    }

    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host "=== タスク情報 ===" -ForegroundColor Cyan
    Write-Host "  タスク名     : $($task.TaskName)"
    Write-Host "  状態         : $($task.State)"
    Write-Host "  最終実行時刻 : $($info.LastRunTime)"
    Write-Host "  最終結果     : $($info.LastTaskResult)"
    Write-Host "  次回実行時刻 : $($info.NextRunTime)"
    Write-Host ""

    # 設定ファイルの中身を表示
    if (Test-Path $ConfigPath) {
        Write-Host "=== 登録済みターミナル ===" -ForegroundColor Cyan
        $cfg = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($term in $cfg.terminals) {
            $status = if ($term.enabled) { "[有効]" } else { "[無効]" }
            Write-Host "  $status $($term.name) -> $($term.wslPath) : $($term.command)"
        }
        Write-Host ""
    }
}

# -------------------------------------------------------
# メイン処理
# -------------------------------------------------------
switch ($Action) {
    "register"   { Register-StartupTask }
    "unregister" { Unregister-StartupTask }
    "status"     { Show-Status }
}
