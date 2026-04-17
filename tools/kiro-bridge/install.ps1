<#
.SYNOPSIS
    kiro-bridge ツールを C:\tools\kiro-bridge\ へインストールします。

.DESCRIPTION
    KiroRun.ps1 を規定のディレクトリにコピーします。
    Obsidian プラグインの設定で scriptPath を変更している場合は
    -DestDir を指定してください。

.PARAMETER DestDir
    インストール先ディレクトリ (省略時: C:\tools\kiro-bridge)

.EXAMPLE
    .\install.ps1
    .\install.ps1 -DestDir "D:\tools\kiro-bridge"
#>

param(
    [string]$DestDir = "C:\tools\kiro-bridge"
)

$srcDir = $PSScriptRoot
$files = @("KiroRun.ps1")

if (-not (Test-Path $DestDir)) {
    New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
    Write-Host "ディレクトリを作成しました: $DestDir"
}

foreach ($f in $files) {
    $src = Join-Path $srcDir $f
    $dst = Join-Path $DestDir $f
    Copy-Item -Path $src -Destination $dst -Force
    Write-Host "コピー完了: $dst"
}

Write-Host ""
Write-Host "インストール完了。Obsidian プラグイン設定の 'KiroRun.ps1 のパス' を以下に設定してください:"
Write-Host "  $DestDir\KiroRun.ps1" -ForegroundColor Cyan
