<#
.SYNOPSIS
    不要なファイル/状態を作成するテストを特定する二分探索スクリプト（Windows PowerShell版）

.DESCRIPTION
    テストを1つずつ実行し、指定されたファイルやディレクトリが作成された時点で
    汚染者となるテストを特定して停止します。

.PARAMETER PollutionCheck
    確認するファイルまたはディレクトリのパス

.PARAMETER TestPattern
    テストファイルを検索するglobパターン

.EXAMPLE
    .\find-polluter.ps1 -PollutionCheck '.git' -TestPattern 'src\**\*.test.ts'

.EXAMPLE
    .\find-polluter.ps1 '.git' 'src\**\*.test.ts'
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$PollutionCheck,

    [Parameter(Mandatory = $true, Position = 1)]
    [string]$TestPattern
)

$ErrorActionPreference = "Stop"

Write-Host "検索中: $PollutionCheck を作成するテスト" -ForegroundColor Cyan
Write-Host "テストパターン: $TestPattern" -ForegroundColor Cyan
Write-Host ""

# テストファイルのリストを取得
$TestFiles = Get-ChildItem -Path . -Filter "*.test.ts" -Recurse |
    Where-Object { $_.FullName -like "*$($TestPattern.Replace('**\', '').Replace('**/', ''))*" -or
                   $_.FullName -match ($TestPattern -replace '\*\*[/\\]', '.*' -replace '\*', '[^/\\]*') } |
    Sort-Object FullName

if ($TestFiles.Count -eq 0) {
    # パターンで見つからない場合、直接globで試す
    $TestFiles = Get-ChildItem -Path . -Recurse -Include "*.test.ts", "*.test.js", "*.spec.ts", "*.spec.js" |
        Sort-Object FullName
}

$Total = $TestFiles.Count

if ($Total -eq 0) {
    Write-Host "テストファイルが見つかりませんでした。パターンを確認してください: $TestPattern" -ForegroundColor Red
    exit 1
}

Write-Host "テストファイル数: $Total"
Write-Host ""

$Count = 0
foreach ($TestFile in $TestFiles) {
    $Count++
    $RelativePath = $TestFile.FullName.Replace((Get-Location).Path + [IO.Path]::DirectorySeparatorChar, "")

    # 汚染が既に存在する場合スキップ
    if (Test-Path $PollutionCheck) {
        Write-Host "警告: テスト $Count/$Total の前に汚染が既に存在" -ForegroundColor Yellow
        Write-Host "   スキップ: $RelativePath"
        continue
    }

    Write-Host "[$Count/$Total] テスト中: $RelativePath"

    # テストを実行（エラーを抑制）
    try {
        $null = & npm test $RelativePath 2>&1
    }
    catch {
        # テスト失敗は無視して続行
    }

    # 汚染が出現したか確認
    if (Test-Path $PollutionCheck) {
        Write-Host ""
        Write-Host "汚染者を発見!" -ForegroundColor Red -BackgroundColor Black
        Write-Host "   テスト: $RelativePath" -ForegroundColor Red
        Write-Host "   作成物: $PollutionCheck" -ForegroundColor Red
        Write-Host ""
        Write-Host "汚染の詳細:" -ForegroundColor Yellow
        Get-Item $PollutionCheck | Format-List Name, FullName, LastWriteTime, Length
        Write-Host ""
        Write-Host "調査方法:" -ForegroundColor Cyan
        Write-Host "  npm test $RelativePath    # このテストのみ実行"
        Write-Host "  Get-Content $RelativePath # テストコードを確認"
        exit 1
    }
}

Write-Host ""
Write-Host "汚染者は見つかりませんでした ― 全テストクリーン!" -ForegroundColor Green
exit 0
