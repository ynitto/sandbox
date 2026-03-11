#Requires -Modules Microsoft.Graph.Teams, Microsoft.Graph.Authentication
<#
.SYNOPSIS
    Microsoft Teams チャンネルにメッセージを投稿する。

.DESCRIPTION
    Microsoft Graph API を使い、Webhook なしで Teams チャンネルへメッセージを送信する。
    初回はブラウザ認証が必要。以降はトークンキャッシュを使用する。

.PARAMETER TeamName
    投稿先チームの表示名（TeamId と排他）。

.PARAMETER TeamId
    投稿先チームの GUID（TeamName と排他）。

.PARAMETER ChannelName
    投稿先チャンネルの表示名（ChannelId と排他）。

.PARAMETER ChannelId
    投稿先チャンネルの GUID（ChannelName と排他）。

.PARAMETER Message
    投稿するメッセージ本文。

.PARAMETER ContentType
    メッセージ形式。"text"（既定）または "html"。

.EXAMPLE
    .\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "一般" -Message "デプロイ完了"

.EXAMPLE
    .\Send-TeamsMessage.ps1 -TeamId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
        -ChannelId "19:xxxx@thread.tacv2" -Message "<b>アラート</b>" -ContentType "html"
#>
[CmdletBinding()]
param(
    [Parameter(ParameterSetName = 'ByName', Mandatory)]
    [string]$TeamName,

    [Parameter(ParameterSetName = 'ById', Mandatory)]
    [string]$TeamId,

    [Parameter(ParameterSetName = 'ByName', Mandatory)]
    [string]$ChannelName,

    [Parameter(ParameterSetName = 'ById', Mandatory)]
    [string]$ChannelId,

    [Parameter(Mandatory)]
    [string]$Message,

    [ValidateSet('text', 'html')]
    [string]$ContentType = 'text'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --- 認証 ---
$requiredScopes = @('ChannelMessage.Send', 'Team.ReadBasic.All', 'Channel.ReadBasic.All')

$context = Get-MgContext -ErrorAction SilentlyContinue
if (-not $context) {
    Write-Host "Microsoft Graph に接続します..." -ForegroundColor Cyan
    Connect-MgGraph -Scopes $requiredScopes -NoWelcome
}

# --- チーム ID 解決 ---
if ($PSCmdlet.ParameterSetName -eq 'ByName') {
    Write-Verbose "チーム '$TeamName' を検索中..."
    $team = Get-MgJoinedTeam -All | Where-Object { $_.DisplayName -eq $TeamName } | Select-Object -First 1
    if (-not $team) {
        throw "チーム '$TeamName' が見つかりません。チーム名を確認するか -TeamId で指定してください。"
    }
    $TeamId = $team.Id

    Write-Verbose "チャンネル '$ChannelName' を検索中..."
    $channel = Get-MgTeamChannel -TeamId $TeamId -All |
        Where-Object { $_.DisplayName -eq $ChannelName } | Select-Object -First 1
    if (-not $channel) {
        throw "チャンネル '$ChannelName' が見つかりません。チャンネル名を確認するか -ChannelId で指定してください。"
    }
    $ChannelId = $channel.Id
}

# --- メッセージ投稿 ---
$body = @{
    body = @{
        contentType = $ContentType
        content     = $Message
    }
}

Write-Verbose "メッセージを投稿中 (Team: $TeamId, Channel: $ChannelId)..."
$result = New-MgTeamChannelMessage -TeamId $TeamId -ChannelId $ChannelId -BodyParameter $body

Write-Host "投稿完了: $($result.WebUrl)" -ForegroundColor Green
