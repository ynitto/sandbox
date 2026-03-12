<#
.SYNOPSIS
    プロキシ設定を対話的に入力し、PowerShell モジュールをインストールする。

.DESCRIPTION
    社内プロキシ環境で Install-Module が失敗する場合に使用する。
    プロキシ URL と認証情報を対話入力し、セッション内の既定プロキシとして設定したうえで
    Microsoft.Graph モジュールをインストールする。
    設定はカレントセッション内のみ有効（OS のプロキシ設定は変更しない）。

.EXAMPLE
    .\Setup-Proxy.ps1
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Write-Host '=== Teams Poster: プロキシ設定 & モジュールインストール ===' -ForegroundColor Cyan
Write-Host ''

# --- プロキシ URL 入力 ---
do {
    $proxyUrl = (Read-Host 'プロキシ URL を入力してください (例: http://proxy.example.com:8080)').Trim()
    if (-not $proxyUrl) {
        Write-Host '  プロキシ URL が空です。再入力してください。' -ForegroundColor Yellow
    }
} while (-not $proxyUrl)

# --- 認証要否 ---
$useAuth = ''
while ($useAuth -notin @('y', 'n')) {
    $useAuth = (Read-Host 'プロキシに認証が必要ですか？ [y/n]').Trim().ToLower()
}

# --- プロキシオブジェクト構築 ---
try {
    $proxy = New-Object System.Net.WebProxy($proxyUrl, $true)
} catch {
    Write-Error "プロキシ URL の形式が不正です: $proxyUrl"
    exit 1
}

if ($useAuth -eq 'y') {
    $proxyUser = ''
    while (-not $proxyUser) {
        $proxyUser = (Read-Host 'プロキシ ユーザー名').Trim()
        if (-not $proxyUser) {
            Write-Host '  ユーザー名が空です。再入力してください。' -ForegroundColor Yellow
        }
    }
    $proxyPass = Read-Host 'プロキシ パスワード' -AsSecureString
    $credential = New-Object System.Management.Automation.PSCredential($proxyUser, $proxyPass)
    $proxy.Credentials = $credential.GetNetworkCredential()
    Write-Host "  認証情報を設定しました (ユーザー: $proxyUser)" -ForegroundColor DarkGray
} else {
    # 認証不要の場合は Windows 統合認証（現在のログインユーザー）を使用
    $proxy.UseDefaultCredentials = $true
    Write-Host '  統合 Windows 認証を使用します。' -ForegroundColor DarkGray
}

# --- セッションへ適用 ---
[System.Net.WebRequest]::DefaultWebProxy = $proxy
# TLS 1.2 を明示的に有効化（古い環境での PowerShell Gallery 接続に必要）
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

Write-Host ''
Write-Host "プロキシ設定完了: $proxyUrl" -ForegroundColor Green
Write-Host ''

# --- 疎通確認 ---
Write-Host '疎通確認中 (PowerShell Gallery)...' -ForegroundColor Cyan
try {
    $null = Invoke-WebRequest -Uri 'https://www.powershellgallery.com' -UseBasicParsing -TimeoutSec 15
    Write-Host '  PowerShell Gallery へ到達できました。' -ForegroundColor Green
} catch {
    Write-Warning "PowerShell Gallery への接続に失敗しました: $($_.Exception.Message)"
    Write-Warning 'プロキシ URL・認証情報を確認し、再実行してください。'
    exit 1
}
Write-Host ''

# --- モジュールインストール ---
Write-Host 'モジュールをインストール中...' -ForegroundColor Cyan

$modules = @(
    'Microsoft.Graph.Authentication',
    'Microsoft.Graph.Teams'
)

foreach ($mod in $modules) {
    if (Get-Module -Name $mod -ListAvailable -ErrorAction SilentlyContinue) {
        Write-Host "  $mod : 既にインストール済み（スキップ）" -ForegroundColor DarkGray
    } else {
        Write-Host "  $mod : インストール中..." -ForegroundColor Cyan
        Install-Module $mod -Scope CurrentUser -Force -AllowClobber
        Write-Host "  $mod : インストール完了" -ForegroundColor Green
    }
}

Write-Host ''
Write-Host 'セットアップが完了しました。続けて Send-TeamsMessage.ps1 を実行できます。' -ForegroundColor Green
Write-Host ''
Write-Host '注意: このプロキシ設定は現在の PowerShell セッション内のみ有効です。' -ForegroundColor Yellow
Write-Host '      Send-TeamsMessage.ps1 は別セッションで開くため、必要に応じて' -ForegroundColor Yellow
Write-Host '      $env:HTTPS_PROXY を設定するか、同一セッション内で実行してください。' -ForegroundColor Yellow
