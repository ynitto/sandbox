#Requires -Version 5.1
<#
.SYNOPSIS
    WSL ターミナルランチャーのセットアップウィザード。

.DESCRIPTION
    必要に応じて UAC で管理者昇格し、前提条件チェック・設定ファイル編集・
    スタートアップ登録・動作テストを対話形式でサポートします。

.EXAMPLE
    .\Setup.ps1
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$LauncherPath = Join-Path $ScriptDir "Start-WslTerminals.ps1"
$ConfigPath   = Join-Path $ScriptDir "config.json"
$TaskName     = "WslTerminalLauncher"

# -------------------------------------------------------
# ユーティリティ
# -------------------------------------------------------
function Write-Header {
    param([string]$Title)
    Write-Host ""
    Write-Host ("=" * 55) -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host ("=" * 55) -ForegroundColor Cyan
}

function Write-Step {
    param([string]$Message)
    Write-Host "[*] $Message" -ForegroundColor Yellow
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[!]  $Message" -ForegroundColor DarkYellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "[NG] $Message" -ForegroundColor Red
}

function Prompt-YesNo {
    param([string]$Question, [bool]$Default = $true)
    $hint = if ($Default) { "[Y/n]" } else { "[y/N]" }
    while ($true) {
        $input = (Read-Host "$Question $hint").Trim().ToLower()
        if ($input -eq "" )    { return $Default }
        if ($input -eq "y")    { return $true }
        if ($input -eq "n")    { return $false }
        Write-Host "  y か n を入力してください。" -ForegroundColor DarkGray
    }
}

function Prompt-Input {
    param([string]$Prompt, [string]$Default = "")
    if ($Default) {
        $input = (Read-Host "$Prompt [$Default]").Trim()
        return if ($input -eq "") { $Default } else { $input }
    } else {
        while ($true) {
            $input = (Read-Host $Prompt).Trim()
            if ($input -ne "") { return $input }
            Write-Host "  値を入力してください。" -ForegroundColor DarkGray
        }
    }
}

# -------------------------------------------------------
# 管理者権限チェック / 自己昇格
# -------------------------------------------------------
function Test-Administrator {
    $id        = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Request-Elevation {
    Write-Warn "タスクスケジューラへの登録には管理者権限が必要です。"
    Write-Warn "UAC ダイアログが表示されます。『はい』をクリックしてください。"
    Write-Host ""

    $psArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.ScriptName)`""
    try {
        Start-Process powershell.exe -ArgumentList $psArgs -Verb RunAs -Wait
    } catch {
        Write-Err "昇格がキャンセルされました。管理者権限なしで続行します。"
        return $false
    }
    return $true
}

# -------------------------------------------------------
# STEP 1: 前提条件チェック
# -------------------------------------------------------
function Invoke-PrerequisiteCheck {
    Write-Header "STEP 1: 前提条件チェック"

    $ok = $true

    # --- ランチャースクリプト ---
    Write-Step "Start-WslTerminals.ps1 の確認..."
    if (Test-Path $LauncherPath) {
        Write-Ok "Start-WslTerminals.ps1 が見つかりました。"
    } else {
        Write-Err "Start-WslTerminals.ps1 が見つかりません: $LauncherPath"
        $ok = $false
    }

    # --- WSL ---
    Write-Step "WSL の確認..."
    $wslExe = Get-Command "wsl.exe" -ErrorAction SilentlyContinue
    if ($wslExe) {
        Write-Ok "wsl.exe が見つかりました: $($wslExe.Source)"
    } else {
        Write-Err "wsl.exe が見つかりません。WSL をインストールしてください。"
        $ok = $false
    }

    # --- WSL ディストロ一覧 ---
    Write-Step "WSL ディストロの確認..."
    try {
        $distros = wsl.exe --list --quiet 2>$null | Where-Object { $_ -match '\S' }
        if ($distros) {
            Write-Ok "利用可能なディストロ:"
            $distros | ForEach-Object { Write-Host "       - $_" -ForegroundColor Gray }
        } else {
            Write-Warn "WSL ディストロが見つかりません。`wsl --install` で導入してください。"
        }
    } catch {
        Write-Warn "ディストロ一覧の取得に失敗しました: $_"
    }

    # --- Windows Terminal ---
    Write-Step "Windows Terminal (wt.exe) の確認..."
    $wtExe = Get-Command "wt.exe" -ErrorAction SilentlyContinue
    if ($wtExe) {
        Write-Ok "wt.exe が見つかりました。複数タブで起動します。"
    } else {
        Write-Warn "wt.exe が見つかりません。wsl.exe の個別ウィンドウで起動します。"
    }

    Write-Host ""
    return $ok
}

# -------------------------------------------------------
# STEP 2: config.json の設定
# -------------------------------------------------------
function Invoke-ConfigSetup {
    Write-Header "STEP 2: ターミナル設定 (config.json)"

    # 設定ファイル読み込み (なければ雛形を作成)
    if (Test-Path $ConfigPath) {
        $cfg = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Ok "既存の config.json を読み込みました。"
    } else {
        Write-Warn "config.json が見つかりません。新規作成します。"
        $cfg = [PSCustomObject]@{
            settings  = [PSCustomObject]@{
                terminalApp            = "wt"
                delayBetweenLaunchesMs = 500
                defaultDistro          = "Ubuntu"
            }
            terminals = @()
        }
    }

    # --- 現在の設定表示 ---
    Write-Host ""
    Write-Host "  現在のターミナル設定:" -ForegroundColor Cyan
    if ($cfg.terminals.Count -eq 0) {
        Write-Host "    (未登録)" -ForegroundColor DarkGray
    } else {
        for ($i = 0; $i -lt $cfg.terminals.Count; $i++) {
            $t      = $cfg.terminals[$i]
            $status = if ($t.enabled) { "有効" } else { "無効" }
            Write-Host ("    [{0}] {1} ({2})  {3} -> {4}" -f ($i + 1), $t.name, $status, $t.wslPath, $t.command)
        }
    }
    Write-Host ""

    # --- 操作メニュー ---
    $changed = $false
    while ($true) {
        Write-Host "  操作を選択してください:" -ForegroundColor Cyan
        Write-Host "    a) ターミナルを追加"
        Write-Host "    t) ターミナルを切替 (有効/無効)"
        Write-Host "    d) ターミナルを削除"
        Write-Host "    s) 設定を保存して次へ進む"
        Write-Host ""

        $choice = (Read-Host "  選択 [a/t/d/s]").Trim().ToLower()

        switch ($choice) {
            "a" {
                Write-Host ""
                $name    = Prompt-Input "    表示名"
                $path    = Prompt-Input "    WSL パス (例: /home/user/myproject)"
                $cmd     = Prompt-Input "    実行コマンド (例: npm run dev)"
                $distro  = Prompt-Input "    ディストロ名 (例: Ubuntu)" -Default ($cfg.settings.defaultDistro)
                $keep    = Prompt-YesNo "    コマンド終了後もシェルを維持しますか?" -Default $true

                $entry = [PSCustomObject]@{
                    name     = $name
                    wslPath  = $path
                    command  = $cmd
                    distro   = $distro
                    keepOpen = $keep
                    enabled  = $true
                }
                $cfg.terminals += $entry
                Write-Ok "'$name' を追加しました。"
                $changed = $true
            }
            "t" {
                if ($cfg.terminals.Count -eq 0) { Write-Warn "ターミナルが登録されていません。"; break }
                $num = [int](Prompt-Input "    切り替える番号 (1-$($cfg.terminals.Count))")
                if ($num -ge 1 -and $num -le $cfg.terminals.Count) {
                    $t = $cfg.terminals[$num - 1]
                    $t.enabled = -not $t.enabled
                    $state = if ($t.enabled) { "有効" } else { "無効" }
                    Write-Ok "'$($t.name)' を $state にしました。"
                    $changed = $true
                } else {
                    Write-Warn "無効な番号です。"
                }
            }
            "d" {
                if ($cfg.terminals.Count -eq 0) { Write-Warn "ターミナルが登録されていません。"; break }
                $num = [int](Prompt-Input "    削除する番号 (1-$($cfg.terminals.Count))")
                if ($num -ge 1 -and $num -le $cfg.terminals.Count) {
                    $name = $cfg.terminals[$num - 1].name
                    if (Prompt-YesNo "    '$name' を削除しますか?" -Default $false) {
                        $newList = @()
                        for ($i = 0; $i -lt $cfg.terminals.Count; $i++) {
                            if ($i -ne ($num - 1)) { $newList += $cfg.terminals[$i] }
                        }
                        $cfg.terminals = $newList
                        Write-Ok "'$name' を削除しました。"
                        $changed = $true
                    }
                } else {
                    Write-Warn "無効な番号です。"
                }
            }
            "s" {
                if ($changed) {
                    $cfg | ConvertTo-Json -Depth 10 | Set-Content $ConfigPath -Encoding UTF8
                    Write-Ok "config.json を保存しました。"
                }
                Write-Host ""
                return
            }
            default {
                Write-Host "  a / t / d / s のいずれかを入力してください。" -ForegroundColor DarkGray
            }
        }

        # 変更後にリストを再表示
        if ($changed -and $choice -in @("a","t","d")) {
            Write-Host ""
            Write-Host "  現在のターミナル設定:" -ForegroundColor Cyan
            if ($cfg.terminals.Count -eq 0) {
                Write-Host "    (未登録)" -ForegroundColor DarkGray
            } else {
                for ($i = 0; $i -lt $cfg.terminals.Count; $i++) {
                    $t      = $cfg.terminals[$i]
                    $status = if ($t.enabled) { "有効" } else { "無効" }
                    Write-Host ("    [{0}] {1} ({2})  {3} -> {4}" -f ($i + 1), $t.name, $status, $t.wslPath, $t.command)
                }
            }
            Write-Host ""
        }
    }
}

# -------------------------------------------------------
# STEP 3: スタートアップ登録
# -------------------------------------------------------
function Invoke-StartupRegistration {
    Write-Header "STEP 3: スタートアップ登録"

    $isAdmin = Test-Administrator

    # --- 登録方式の選択 ---
    Write-Host "  登録方式を選択してください:" -ForegroundColor Cyan
    Write-Host "    1) タスクスケジューラ (推奨・管理者権限が必要)"
    Write-Host "       ログオン遅延設定が可能で確実に動作します。"
    Write-Host "    2) スタートアップフォルダ (管理者権限不要)"
    Write-Host "       ショートカットを %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup に配置します。"
    Write-Host ""

    $method = ""
    while ($method -notin @("1", "2")) {
        $method = (Read-Host "  選択 [1/2]").Trim()
    }

    switch ($method) {
        "1" { Register-ViaTaskScheduler -IsAdmin $isAdmin }
        "2" { Register-ViaStartupFolder }
    }
}

function Register-ViaTaskScheduler {
    param([bool]$IsAdmin)

    if (-not $IsAdmin) {
        Write-Warn "管理者権限がありません。UAC で昇格します..."
        Write-Host ""
        # 昇格した新プロセスで Register-Startup.ps1 を直接呼ぶ
        $regScript = Join-Path $ScriptDir "Register-Startup.ps1"
        $delay     = [int](Prompt-Input "  ログオン後の起動遅延秒数" -Default "10")
        $psArgs    = "-NoProfile -ExecutionPolicy Bypass -File `"$regScript`" -Action register -DelaySeconds $delay"
        try {
            Start-Process powershell.exe -ArgumentList $psArgs -Verb RunAs -Wait
            Write-Ok "タスクスケジューラへの登録が完了しました。"
        } catch {
            Write-Err "昇格がキャンセルされました。登録を中止します。"
        }
        return
    }

    # 管理者として実行中の場合はここで直接登録
    $regScript  = Join-Path $ScriptDir "Register-Startup.ps1"
    $delay      = [int](Prompt-Input "  ログオン後の起動遅延秒数" -Default "10")

    Write-Step "タスクスケジューラに登録中..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $regScript -Action register -DelaySeconds $delay

    Write-Host ""
    Write-Ok "登録完了。次回 Windows ログイン時から自動起動します。"
}

function Register-ViaStartupFolder {
    $startupDir  = [System.Environment]::GetFolderPath("Startup")
    $shortcut    = Join-Path $startupDir "WslTerminalLauncher.lnk"
    $psExe       = (Get-Command powershell.exe).Source
    $psArgs      = "-NonInteractive -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LauncherPath`""

    Write-Step "スタートアップフォルダにショートカットを作成中..."
    Write-Host "  保存先: $shortcut" -ForegroundColor DarkGray

    # WScript.Shell でショートカット作成
    $wsh  = New-Object -ComObject WScript.Shell
    $lnk  = $wsh.CreateShortcut($shortcut)
    $lnk.TargetPath       = $psExe
    $lnk.Arguments        = $psArgs
    $lnk.WorkingDirectory = $ScriptDir
    $lnk.Description      = "WSL Terminal Launcher"
    $lnk.WindowStyle      = 7  # 最小化ウィンドウで起動
    $lnk.Save()

    Write-Ok "ショートカットを作成しました: $shortcut"
    Write-Ok "次回 Windows ログイン時から自動起動します。"
    Write-Host ""
    Write-Warn "削除する場合は以下のフォルダからショートカットを削除してください:"
    Write-Host "  $startupDir" -ForegroundColor Gray
}

# -------------------------------------------------------
# STEP 4: 動作テスト
# -------------------------------------------------------
function Invoke-TestRun {
    Write-Header "STEP 4: 動作テスト"

    if (-not (Prompt-YesNo "  今すぐターミナルを起動してテストしますか?" -Default $true)) {
        Write-Host "  テストをスキップしました。" -ForegroundColor DarkGray
        Write-Host ""
        return
    }

    Write-Step "Start-WslTerminals.ps1 を実行します..."
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $LauncherPath -ConfigPath $ConfigPath
        Write-Ok "起動要求を送信しました。ターミナルウィンドウが開くことを確認してください。"
    } catch {
        Write-Err "テスト実行に失敗しました: $_"
    }
    Write-Host ""
}

# -------------------------------------------------------
# メイン
# -------------------------------------------------------
Write-Header "WSL Terminal Launcher セットアップ"
Write-Host ""
Write-Host "  このウィザードは以下をサポートします:" -ForegroundColor Gray
Write-Host "    1. 前提条件チェック (WSL / Windows Terminal)" -ForegroundColor Gray
Write-Host "    2. 起動するターミナルの設定 (config.json)" -ForegroundColor Gray
Write-Host "    3. Windows スタートアップへの登録" -ForegroundColor Gray
Write-Host "    4. 動作テスト" -ForegroundColor Gray
Write-Host ""

$prereqOk = Invoke-PrerequisiteCheck

if (-not $prereqOk) {
    Write-Err "前提条件を満たしていません。上記の問題を解決してから再実行してください。"
    Write-Host ""
    Read-Host "Enterキーで終了"
    exit 1
}

Invoke-ConfigSetup

if (Prompt-YesNo "スタートアップに登録しますか?" -Default $true) {
    Invoke-StartupRegistration
}

Invoke-TestRun

Write-Header "セットアップ完了"
Write-Host ""
Write-Ok "セットアップが完了しました。"
Write-Host ""
Write-Host "  その他の操作:" -ForegroundColor Gray
Write-Host "    登録確認  : .\Register-Startup.ps1 -Action status" -ForegroundColor Gray
Write-Host "    登録解除  : .\Register-Startup.ps1 -Action unregister" -ForegroundColor Gray
Write-Host "    手動起動  : .\Start-WslTerminals.ps1" -ForegroundColor Gray
Write-Host ""
Read-Host "Enterキーで終了"
