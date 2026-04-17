<#
.SYNOPSIS
    現在の Obsidian ノートを Kiro タスクとして Windows Terminal で実行します。

.DESCRIPTION
    Obsidian プラグイン (obsidian-kiro-bridge) から呼び出されます。
    -Environment で Windows (PowerShell) または WSL (Bash) を選択できます。
    ノートの内容を一時ファイルに書き出し、kiro-cli へ渡します。

.PARAMETER FilePath
    ノート .md ファイルへの Windows 絶対パス

.PARAMETER Environment
    "windows" または "wsl"

.PARAMETER KiroCmd
    kiro コマンド (指定環境内でのパスまたはコマンド名)

.PARAMETER WslDistro
    WSL ディストリビューション名 (Environment=wsl の場合のみ使用)

.PARAMETER WorkDir
    kiro を起動するディレクトリ (Windows パス)
    WSL の場合は自動的に WSL パスに変換されます。

.PARAMETER ExtraFlags
    kiro-cli に追加で渡すフラグ (例: "--trust-all-tools")

.EXAMPLE
    .\KiroRun.ps1 -FilePath "C:\Vault\tasks\issue-123.md" -Environment wsl -KiroCmd kiro-cli -WslDistro Ubuntu -WorkDir "C:\projects\myapp"
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,

    [ValidateSet("windows", "wsl")]
    [string]$Environment = "wsl",

    [string]$KiroCmd = "kiro-cli",
    [string]$WslDistro = "Ubuntu",
    [string]$WorkDir = "",
    [string]$ExtraFlags = "--trust-all-tools"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------

function ConvertTo-WslPath([string]$WinPath) {
    $result = & wsl -d $WslDistro wslpath -u $WinPath 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "wslpath 変換に失敗しました: $WinPath`n$result"
    }
    return $result.Trim()
}

function Get-TmpDir {
    $dir = Join-Path $env:TEMP "kiro-bridge"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    return $dir
}

# ---------------------------------------------------------------------------
# ノートの存在確認
# ---------------------------------------------------------------------------

if (-not (Test-Path $FilePath)) {
    Write-Error "ファイルが見つかりません: $FilePath"
    exit 1
}

$tmpDir = Get-TmpDir
$noteFileName = [System.IO.Path]::GetFileNameWithoutExtension($FilePath)
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

# ---------------------------------------------------------------------------
# WSL 環境
# ---------------------------------------------------------------------------

if ($Environment -eq "wsl") {

    # Windows パスを WSL パスに変換
    $wslFilePath = ConvertTo-WslPath $FilePath
    $wslWorkDir  = if ($WorkDir) { ConvertTo-WslPath $WorkDir } else { "." }

    # WSL で実行するラッパースクリプトを生成
    # ノートの内容を stdin で kiro-cli に渡す
    $wslScriptPath = Join-Path $tmpDir "kiro-run-$timestamp.sh"
    $wslScript = @"
#!/usr/bin/env bash
set -euo pipefail

NOTE_FILE="$wslFilePath"
WORK_DIR="$wslWorkDir"
KIRO_CMD="$KiroCmd"
EXTRA_FLAGS="$ExtraFlags"

echo "=== Kiro Bridge ==="
echo "Note: \$NOTE_FILE"
echo "WorkDir: \$WORK_DIR"
echo ""

# PATH を補完 (kiro-cli がインストールされている可能性のある場所)
export PATH="\$HOME/.local/bin:\$HOME/.kiro/bin:/usr/local/bin:/usr/bin:\$PATH"

if [ ! -f "\$NOTE_FILE" ]; then
    echo "[ERROR] ノートファイルが見つかりません: \$NOTE_FILE"
    exit 1
fi

cd "\$WORK_DIR"

echo "--- ノート内容 ---"
cat "\$NOTE_FILE"
echo ""
echo "--- Kiro 起動 ---"

# ノート内容を stdin 経由で kiro-cli に渡す
\$KIRO_CMD \$EXTRA_FLAGS < "\$NOTE_FILE"
"@

    [System.IO.File]::WriteAllText($wslScriptPath, $wslScript, [System.Text.Encoding]::UTF8)

    # WSL パスに変換してパーミッション付与
    $wslScriptPathWsl = ConvertTo-WslPath $wslScriptPath
    & wsl -d $WslDistro chmod +x $wslScriptPathWsl | Out-Null

    # Windows Terminal で WSL タブを起動
    $wtArgs = "new-tab --title `"Kiro: $noteFileName`" wsl -d $WslDistro -- bash `"$wslScriptPathWsl`""
    Write-Host "Windows Terminal を起動中 (WSL: $WslDistro)..."
    Start-Process "wt" -ArgumentList $wtArgs
}

# ---------------------------------------------------------------------------
# Windows 環境 (PowerShell)
# ---------------------------------------------------------------------------

else {

    $psScriptPath = Join-Path $tmpDir "kiro-run-$timestamp.ps1"
    $escapedFilePath = $FilePath -replace "'", "''"
    $escapedWorkDir  = if ($WorkDir) { $WorkDir -replace "'", "''" } else { $PSScriptRoot }
    $psScript = @"
`$ErrorActionPreference = 'Continue'

Write-Host "=== Kiro Bridge ===" -ForegroundColor Cyan
Write-Host "Note: $escapedFilePath"
Write-Host "WorkDir: $escapedWorkDir"
Write-Host ""

if (-not (Test-Path '$escapedFilePath')) {
    Write-Error "ノートファイルが見つかりません: $escapedFilePath"
    exit 1
}

Set-Location '$escapedWorkDir'

Write-Host "--- ノート内容 ---" -ForegroundColor Yellow
Get-Content '$escapedFilePath'
Write-Host ""
Write-Host "--- Kiro 起動 ---" -ForegroundColor Yellow

# ノート内容を stdin 経由で kiro-cli に渡す
Get-Content '$escapedFilePath' | & $KiroCmd $ExtraFlags
"@

    [System.IO.File]::WriteAllText($psScriptPath, $psScript, [System.Text.Encoding]::UTF8)

    $wtArgs = "new-tab --title `"Kiro: $noteFileName`" powershell -NoExit -File `"$psScriptPath`""
    Write-Host "Windows Terminal を起動中 (Windows / PowerShell)..."
    Start-Process "wt" -ArgumentList $wtArgs
}

Write-Host "完了: $Environment 環境で Kiro を起動しました"
