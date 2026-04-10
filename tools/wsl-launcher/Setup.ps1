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

# コンソールエンコーディングを UTF-8 に統一 (Windows PowerShell 5.1 / Shift-JIS 環境対策)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
$OutputEncoding           = [System.Text.Encoding]::UTF8

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
# STEP 0: Windows Terminal インストール確認・インストール
# -------------------------------------------------------
function Invoke-WtInstall {
    Write-Header "STEP 0: Windows Terminal の確認"

    $wtExe = Get-Command "wt.exe" -ErrorAction SilentlyContinue
    if ($wtExe) {
        Write-Ok "Windows Terminal は既にインストールされています: $($wtExe.Source)"
        Write-Host ""
        return
    }

    Write-Warn "Windows Terminal (wt.exe) が見つかりません。"
    if (-not (Prompt-YesNo "  winget でインストールしますか?" -Default $true)) {
        Write-Host "  スキップしました。" -ForegroundColor DarkGray
        Write-Host ""
        return
    }

    $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Err "winget.exe が見つかりません。"
        Write-Warn "Microsoft Store から 'アプリ インストーラー' をインストールしてください。"
        Write-Host ""
        return
    }

    Write-Step "Windows Terminal をインストール中..."
    try {
        & winget.exe install --id Microsoft.WindowsTerminal --accept-package-agreements --accept-source-agreements
        Write-Ok "Windows Terminal をインストールしました。"
        Write-Warn "インストール後は PowerShell を再起動してから Setup.ps1 を再実行してください。"
    } catch {
        Write-Err "インストールに失敗しました: $_"
    }
    Write-Host ""
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
        # wsl.exe --list の出力は UTF-16 LE のため、ヌル文字を除去して ASCII 文字列に変換する
        $distros = @(wsl.exe --list --quiet 2>$null) |
                   ForEach-Object { ($_ -replace '\x00', '').Trim() } |
                   Where-Object   { $_ -match '\S' }

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
# Windows Terminal settings.json ユーティリティ
# -------------------------------------------------------
function Get-WtSettingsJson {
    $candidatePaths = @(
        "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json",
        "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json",
        "$env:LOCALAPPDATA\Microsoft\Windows Terminal\settings.json"
    )
    $settingsPath = $candidatePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $settingsPath) { return $null }
    try {
        $raw = Get-Content $settingsPath -Raw -Encoding UTF8
        $raw = $raw -replace '(?m)//[^\r\n]*', ''
        $raw = $raw -replace '(?s)/\*.*?\*/', ''
        return $raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

# WT プロファイル名からディストロ名を解決する
function Resolve-WslDistroFromProfile {
    param([string]$ProfileName, $WtSettings)
    if (-not $WtSettings -or -not $ProfileName) { return $ProfileName }
    $profiles = @($WtSettings.profiles.list)
    $prof     = $profiles | Where-Object { $_.name -eq $ProfileName } | Select-Object -First 1
    if (-not $prof) { return $ProfileName }
    if ($prof.source -eq "Windows.Terminal.Wsl") { return $prof.name }
    if ($prof.commandline -match '(?:^|\s)wsl(?:\.exe)?\s+.*?-d\s+(\S+)') { return $Matches[1] }
    return $ProfileName
}

# WT のデフォルト WSL プロファイル名を取得する
function Get-DefaultWslProfile {
    param($WtSettings)
    if (-not $WtSettings) { return "" }
    $defaultGuid = $WtSettings.defaultProfile
    $profiles    = @($WtSettings.profiles.list)
    if ($defaultGuid) {
        $def = $profiles | Where-Object { $_.guid -eq $defaultGuid } | Select-Object -First 1
        if ($def -and (-not $def.hidden) -and
            ($def.source -eq "Windows.Terminal.Wsl" -or $def.commandline -like "*wsl*")) {
            return $def.name
        }
    }
    $firstWsl = $profiles |
        Where-Object { (-not $_.hidden) -and
                       ($_.source -eq "Windows.Terminal.Wsl" -or $_.commandline -like "*wsl*") } |
        Select-Object -First 1
    return if ($firstWsl) { $firstWsl.name } else { "" }
}

# WT の WSL プロファイル名一覧を取得する
function Get-WslProfileNames {
    param($WtSettings)
    if (-not $WtSettings) { return @() }
    return @($WtSettings.profiles.list |
        Where-Object { (-not $_.hidden) -and
                       ($_.source -eq "Windows.Terminal.Wsl" -or $_.commandline -like "*wsl*") } |
        ForEach-Object { $_.name })
}

# -------------------------------------------------------
# STEP 2: config.json の設定
# -------------------------------------------------------
function Invoke-ConfigSetup {
    Write-Header "STEP 2: ターミナル設定 (config.json)"

    # 設定ファイル読み込み (なければ雛形を作成)
    if (Test-Path $ConfigPath) {
        $cfg = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        # PS 5.1 では JSON 配列が1要素のとき単一オブジェクトになるため強制配列化
        $cfg.terminals = @($cfg.terminals)
        Write-Ok "既存の config.json を読み込みました。"
    } else {
        Write-Warn "config.json が見つかりません。新規作成します。"
        $cfg = [PSCustomObject]@{
            settings  = [PSCustomObject]@{
                wslWaitTimeoutSeconds = 60
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
                $name        = Prompt-Input "    表示名"
                $path        = Prompt-Input "    WSL パス (例: /home/user/myproject)"
                $cmd         = Prompt-Input "    実行コマンド (例: npm run dev)"
                $wtProfiles  = Get-WslProfileNames -WtSettings (Get-WtSettingsJson)
                $defProfile  = if ($wtProfiles.Count -gt 0) { $wtProfiles[0] } else { "Ubuntu" }
                if ($wtProfiles.Count -gt 0) {
                    Write-Host "    利用可能な WT WSL プロファイル: $($wtProfiles -join ', ')" -ForegroundColor DarkGray
                }
                $profile = Prompt-Input "    Windows Terminal プロファイル名" -Default $defProfile
                $keep    = Prompt-YesNo "    コマンド終了後もシェルを維持しますか?" -Default $true

                $entry = [PSCustomObject]@{
                    name     = $name
                    profile  = $profile
                    wslPath  = $path
                    command  = $cmd
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
# WSL 自動起動設定 (オプション)
# PC ログイン時に WSL を WT より先に起動してウォームアップする
# -------------------------------------------------------

# wsl-autostart をレジストリの Run キーに登録するヘルパー
function Register-WslAutostartRegistry {
    param([string]$VbsPath)

    $regValueName = "WSLAutostart"
    $regData      = "wscript `"$VbsPath`""
    $hklmKey      = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    $hkcuKey      = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"

    try {
        Set-ItemProperty -Path $hklmKey -Name $regValueName -Value $regData -ErrorAction Stop
        Write-Ok "HKLM Run に登録しました (全ユーザー対象)。"
    } catch {
        Write-Warn "HKLM への書き込みに失敗 (管理者権限が必要)。HKCU にフォールバックします..."
        try {
            Set-ItemProperty -Path $hkcuKey -Name $regValueName -Value $regData -ErrorAction Stop
            Write-Ok "HKCU Run に登録しました (現在のユーザーのみ)。"
        } catch {
            Write-Err "レジストリ登録に失敗しました: $_"
            return
        }
    }

    $installDir = Split-Path -Parent $VbsPath
    Write-Host ""
    Write-Host "  インストール先: $installDir" -ForegroundColor Gray
    Write-Host "  起動コマンド  : $regData" -ForegroundColor Gray
    Write-Host "  サービス追加  : $installDir\commands.txt を編集してください" -ForegroundColor Gray
}

# OSS: troytse/wsl-autostart をダウンロード・セットアップ
function Setup-WslAutostartOss {
    $defaultDir  = "C:\wsl-autostart"
    $installDir  = Prompt-Input "  インストール先" -Default $defaultDir
    $zipUrl      = "https://github.com/troytse/wsl-autostart/archive/refs/heads/master.zip"
    $tempZip     = Join-Path $env:TEMP "wsl-autostart.zip"
    $tempExtract = Join-Path $env:TEMP "wsl-autostart-extract"

    # 既存インストールをスキップできる
    $vbsPath = Join-Path $installDir "start.vbs"
    if ((Test-Path $installDir) -and (Test-Path $vbsPath)) {
        if (-not (Prompt-YesNo "  $installDir に既にインストールされています。再インストールしますか?" -Default $false)) {
            Register-WslAutostartRegistry -VbsPath $vbsPath
            return
        }
    }

    # ダウンロード
    Write-Step "ダウンロード中: $zipUrl"
    try {
        Invoke-WebRequest -Uri $zipUrl -OutFile $tempZip -UseBasicParsing
        Write-Ok "ダウンロード完了"
    } catch {
        Write-Err "ダウンロードに失敗しました: $_"
        Write-Warn "手動ダウンロード先: https://github.com/troytse/wsl-autostart"
        return
    }

    # 展開・コピー
    Write-Step "展開中..."
    if (Test-Path $tempExtract) { Remove-Item $tempExtract -Recurse -Force }
    try { Expand-Archive -Path $tempZip -DestinationPath $tempExtract } catch {
        Write-Err "展開に失敗しました: $_"; return
    }
    $srcDir = Join-Path $tempExtract "wsl-autostart-master"
    if (-not (Test-Path $srcDir)) {
        Write-Err "展開先に wsl-autostart-master が見つかりません"; return
    }
    if (Test-Path $installDir) { Remove-Item $installDir -Recurse -Force }
    Copy-Item $srcDir $installDir -Recurse
    Write-Ok "インストール先: $installDir"

    # commands.txt 生成
    # ここに Linux サービスを記述すると WSL 起動時に自動実行される
    # 例: /etc/init.d/ssh
    $commandsPath = Join-Path $installDir "commands.txt"
    if (-not (Test-Path $commandsPath)) {
        Set-Content $commandsPath "" -Encoding UTF8
    }
    Write-Ok "commands.txt: $commandsPath"
    Write-Host "  (起動したい Linux サービスがあれば 1 行 1 コマンドで追記してください)" -ForegroundColor DarkGray

    # start.vbs の存在確認
    $vbsPath = Join-Path $installDir "start.vbs"
    if (-not (Test-Path $vbsPath)) {
        Write-Warn "start.vbs が見つかりません。リポジトリの構成を確認してください。"
        return
    }

    Register-WslAutostartRegistry -VbsPath $vbsPath
}

# シンプルな Task Scheduler ウォームアップ (追加ダウンロードなし)
function Register-WslWarmupTask {
    $taskName = "WslWarmup"

    # Windows Terminal settings.json からデフォルト WSL ディストロを取得
    $distro = ""
    $wtCfg  = Get-WtSettingsJson
    if ($wtCfg) {
        $defProfile = Get-DefaultWslProfile -WtSettings $wtCfg
        if ($defProfile) {
            $distro = Resolve-WslDistroFromProfile -ProfileName $defProfile -WtSettings $wtCfg
        }
    }

    # 既存タスクを削除して再登録
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "  既存タスクを削除して再登録します。" -ForegroundColor DarkGray
    }

    $wslCmdArgs = if ($distro) { "-d `"$distro`" --exec echo warmup" } else { "--exec echo warmup" }
    $action     = New-ScheduledTaskAction -Execute "wsl.exe" -Argument $wslCmdArgs
    $trigger    = New-ScheduledTaskTrigger -AtLogOn
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal  = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
    $settings   = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
        -MultipleInstances IgnoreNew

    try {
        Register-ScheduledTask `
            -TaskName    $taskName `
            -Action      $action `
            -Trigger     $trigger `
            -Principal   $principal `
            -Settings    $settings `
            -Description "WSL ウォームアップ: ログオン直後に WSL を先行起動して WT の遅延を防ぎます。" `
            | Out-Null
        Write-Ok "タスク '$taskName' を登録しました。"
        if ($distro) { Write-Host "  対象ディストロ: $distro" -ForegroundColor Gray }
        Write-Ok "次回ログイン時から WSL がバックグラウンドで先行起動します。"
    } catch {
        Write-Err "タスク登録に失敗しました: $_"
        Write-Warn "管理者権限で実行してください。"
    }
    Write-Host ""
}

function Invoke-WslAutostartSetup {
    Write-Header "WSL 自動起動設定 (オプション)"

    Write-Host "  WT がタブを開く前に WSL を起動しておくことで" -ForegroundColor Gray
    Write-Host "  初回ログイン時の遅延・エラーを根本から解消できます。" -ForegroundColor Gray
    Write-Host ""

    if (-not (Prompt-YesNo "  WSL 自動起動を設定しますか?" -Default $true)) {
        Write-Host "  スキップしました。" -ForegroundColor DarkGray
        Write-Host ""
        return
    }

    Write-Host ""
    Write-Host "  方式を選択してください:" -ForegroundColor Cyan
    Write-Host "    1) wsl-autostart (troytse/wsl-autostart) [OSS]"
    Write-Host "       GitHub からダウンロード。Linux サービスの起動にも対応。"
    Write-Host "       HKLM (or HKCU) Run キーに登録します。"
    Write-Host "    2) タスクスケジューラ ウォームアップ (追加ダウンロードなし)"
    Write-Host "       ログオン時に wsl.exe を先行実行するだけのシンプルな方式。"
    Write-Host ""

    $method = ""
    while ($method -notin @("1", "2")) {
        $method = (Read-Host "  選択 [1/2]").Trim()
    }

    switch ($method) {
        "1" { Setup-WslAutostartOss }
        "2" { Register-WslWarmupTask }
    }
}

# -------------------------------------------------------
# STEP 3: スタートアップ登録
# -------------------------------------------------------

# Windows Terminal settings.json に startupActions / startOnUserLogin を設定する
function Register-ViaWtSettings {
    # settings.json の候補パス (安定版 / Preview 版 / 非ストア版)
    $candidatePaths = @(
        "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json",
        "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json",
        "$env:LOCALAPPDATA\Microsoft\Windows Terminal\settings.json"
    )
    $settingsPath = $candidatePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $settingsPath) {
        Write-Warn "settings.json が見つかりません。"
        Write-Warn "Windows Terminal を一度起動してから再実行してください。"
        return
    }
    Write-Host "  設定ファイル: $settingsPath" -ForegroundColor DarkGray

    # settings.json を読み込み (JSONC コメントを除去してパース)
    $raw = Get-Content $settingsPath -Raw -Encoding UTF8
    $raw = $raw -replace '(?m)//[^\r\n]*', ''
    $raw = $raw -replace '(?s)/\*.*?\*/', ''
    $settings         = $raw | ConvertFrom-Json
    $defaultWtProfile = Get-DefaultWslProfile -WtSettings $settings

    # config.json から有効ターミナル設定を読み込む
    $cfg          = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $cfgTerminals = @($cfg.terminals) | Where-Object { $_.enabled -ne $false }

    if (@($cfgTerminals).Count -eq 0) {
        Write-Warn "有効なターミナルが設定されていません。config.json を確認してください。"
        return
    }

    # startupActions 文字列を構築
    # 形式: new-tab --title "name" --profile "Profile" [--tabColor "#RRGGBB"] -- wsl.exe -d Distro --cd "/path" -- bash -c 'cmd; exec bash'
    $parts   = @()
    $isFirst = $true
    foreach ($term in $cfgTerminals) {
        # profile → distro 解決 (後方互換性のため distro フィールドも参照)
        $profName = if ($term.profile)    { $term.profile }
                    elseif ($term.distro) { $term.distro }
                    else                  { $defaultWtProfile }
        $distro   = Resolve-WslDistroFromProfile -ProfileName $profName -WtSettings $settings
        $keepOpen = if ($null -ne $term.keepOpen) { [bool]$term.keepOpen } else { $true }
        $safeCmd  = $term.command -replace "'", "'\\''"
        $innerCmd = if ($keepOpen) { "bash -c '$safeCmd; exec bash'" } else { "bash -c '$safeCmd'" }

        $tabPart = "new-tab --title `"$($term.name)`""
        if ($profName) {
            $tabPart += " --profile `"$profName`""
        }
        if ($isFirst) {
            $tabPart += " --tabColor `"#0078D4`""
            $isFirst  = $false
        }
        if ($distro) {
            $tabPart += " -- wsl.exe -d `"$distro`" --cd `"$($term.wslPath)`" -- $innerCmd"
        } else {
            $tabPart += " -- wsl.exe --cd `"$($term.wslPath)`" -- $innerCmd"
        }
        $parts += $tabPart
    }
    $startupActions = $parts -join " ; "

    # settings.json をバックアップ (タイムスタンプ付き)
    $backupPath = $settingsPath -replace '\.json$', (".json.bak." + (Get-Date -Format "yyyyMMddHHmmss"))
    Copy-Item $settingsPath $backupPath
    Write-Ok "バックアップ: $backupPath"

    # 設定を更新
    Add-Member -InputObject $settings -NotePropertyName "startOnUserLogin" -NotePropertyValue $true           -Force
    Add-Member -InputObject $settings -NotePropertyName "startupActions"   -NotePropertyValue $startupActions -Force

    # 書き込み
    $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8
    Write-Ok "settings.json を更新しました。"
    Write-Host ""
    Write-Host "  startOnUserLogin : true" -ForegroundColor Gray
    Write-Host "  startupActions   :" -ForegroundColor Gray
    $parts | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    Write-Host ""
    Write-Warn "設定を元に戻す場合はバックアップから復元してください:"
    Write-Host "  $backupPath" -ForegroundColor Gray
}

function Invoke-StartupRegistration {
    Write-Header "STEP 3: スタートアップ登録"

    $isAdmin = Test-Administrator

    # --- 登録方式の選択 ---
    Write-Host "  登録方式を選択してください:" -ForegroundColor Cyan
    Write-Host "    1) Windows Terminal 自動起動設定 (推奨・管理者権限不要)"
    Write-Host "       settings.json に startOnUserLogin と startupActions を設定します。"
    Write-Host "       Windows Terminal が起動時にタブを直接開くため最も確実です。"
    Write-Host "    2) タスクスケジューラ (管理者権限が必要)"
    Write-Host "       ログオン遅延設定が可能。Windows Terminal がなくても動作します。"
    Write-Host "    3) スタートアップフォルダ (管理者権限不要)"
    Write-Host "       ショートカットを Startup フォルダに配置します。"
    Write-Host ""

    $method = ""
    while ($method -notin @("1", "2", "3")) {
        $method = (Read-Host "  選択 [1/2/3]").Trim()
    }

    switch ($method) {
        "1" { Register-ViaWtSettings }
        "2" { Register-ViaTaskScheduler -IsAdmin $isAdmin }
        "3" { Register-ViaStartupFolder }
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
    $startupDir = [System.Environment]::GetFolderPath("Startup")
    $shortcut   = Join-Path $startupDir "WslTerminalLauncher.lnk"
    $vbsLauncher = Join-Path $ScriptDir "Start-WslTerminals.vbs"

    Write-Step "スタートアップフォルダにショートカットを作成中..."
    Write-Host "  保存先: $shortcut" -ForegroundColor DarkGray

    # VBScript ランチャーが存在する場合はそれを使う (コンソールウィンドウが出ない)
    # 存在しない場合は powershell.exe + -WindowStyle Hidden にフォールバック
    $wsh = New-Object -ComObject WScript.Shell
    $lnk = $wsh.CreateShortcut($shortcut)

    if (Test-Path $vbsLauncher) {
        $lnk.TargetPath       = "wscript.exe"
        $lnk.Arguments        = "`"$vbsLauncher`""
        $lnk.WorkingDirectory = $ScriptDir
        $lnk.Description      = "WSL Terminal Launcher"
        $lnk.WindowStyle      = 1
        Write-Host "  起動方式: VBScript (コンソールなし)" -ForegroundColor DarkGray
    } else {
        $psExe  = (Get-Command powershell.exe).Source
        $psArgs = "-NonInteractive -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LauncherPath`""
        $lnk.TargetPath       = $psExe
        $lnk.Arguments        = $psArgs
        $lnk.WorkingDirectory = $ScriptDir
        $lnk.Description      = "WSL Terminal Launcher"
        $lnk.WindowStyle      = 7  # 最小化
        Write-Host "  起動方式: PowerShell (WindowStyle Hidden)" -ForegroundColor DarkGray
    }

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
Write-Host "    0. Windows Terminal の確認・インストール (winget)" -ForegroundColor Gray
Write-Host "    1. 前提条件チェック (WSL / Windows Terminal)" -ForegroundColor Gray
Write-Host "    2. 起動するターミナルの設定 (config.json)" -ForegroundColor Gray
Write-Host "    3. WSL 自動起動設定 (wsl-autostart / タスクスケジューラ)" -ForegroundColor Gray
Write-Host "    4. スタートアップ登録 (settings.json / タスクスケジューラ / Startup フォルダ)" -ForegroundColor Gray
Write-Host "    5. 動作テスト" -ForegroundColor Gray
Write-Host ""

Invoke-WtInstall

$prereqOk = Invoke-PrerequisiteCheck

if (-not $prereqOk) {
    Write-Err "前提条件を満たしていません。上記の問題を解決してから再実行してください。"
    Write-Host ""
    Read-Host "Enterキーで終了"
    exit 1
}

Invoke-ConfigSetup

Invoke-WslAutostartSetup

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
Write-Host "    手動起動  : Start-WslTerminals.vbs をダブルクリック (コンソールなし)" -ForegroundColor Gray
Write-Host "              ※ .ps1 を直接実行するとコンソールが表示されます" -ForegroundColor DarkGray
Write-Host ""
Read-Host "Enterキーで終了"
