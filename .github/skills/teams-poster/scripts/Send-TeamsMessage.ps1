#Requires -Modules Microsoft.Graph.Teams, Microsoft.Graph.Authentication
<#
.SYNOPSIS
    Microsoft Teams チャンネルにメッセージを投稿する。

.DESCRIPTION
    Microsoft Graph API を使い、Webhook なしで Teams チャンネルへメッセージを送信する。
    初回はブラウザ認証が必要。以降はトークンキャッシュを使用する。
    タイトル（件名）の付与、@channel / @team メンションに対応。
    メンション機能は既存スコープ（Channel.ReadBasic.All / Team.ReadBasic.All）のみで動作し、
    追加スコープは不要。

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

.PARAMETER Subject
    メッセージのタイトル（件名）。省略可。

.PARAMETER ContentType
    メッセージ形式。"text"（既定）または "html"。
    -MentionChannel / -MentionTeam 指定時は自動的に "html" になる。

.PARAMETER MentionChannel
    投稿先チャンネルを @メンションする（追加スコープ不要）。

.PARAMETER MentionTeam
    投稿先チームを @メンションする（追加スコープ不要）。

.EXAMPLE
    .\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "一般" -Message "デプロイ完了"

.EXAMPLE
    .\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "通知" `
        -Subject "リリース完了" -Message "v1.2.0 をリリースしました" -MentionChannel

.EXAMPLE
    .\Send-TeamsMessage.ps1 -TeamId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
        -ChannelId "19:xxxx@thread.tacv2" -Message "<b>アラート</b>" -ContentType "html" -MentionTeam

.EXAMPLE
    .\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "告知" `
        -Subject "緊急メンテナンス" -Message "本日 22:00 よりメンテナンスを実施します。" `
        -MentionChannel -MentionTeam
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

    [string]$Subject,

    [ValidateSet('text', 'html')]
    [string]$ContentType = 'text',

    [switch]$MentionChannel,

    [switch]$MentionTeam
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

# --- チーム / チャンネル ID 解決 + DisplayName 取得 ---
if ($PSCmdlet.ParameterSetName -eq 'ByName') {
    Write-Verbose "チーム '$TeamName' を検索中..."
    $team = Get-MgJoinedTeam -All | Where-Object { $_.DisplayName -eq $TeamName } | Select-Object -First 1
    if (-not $team) {
        throw "チーム '$TeamName' が見つかりません。チーム名を確認するか -TeamId で指定してください。"
    }
    $TeamId = $team.Id
    $teamDisplayName = $team.DisplayName

    Write-Verbose "チャンネル '$ChannelName' を検索中..."
    $channel = Get-MgTeamChannel -TeamId $TeamId -All |
        Where-Object { $_.DisplayName -eq $ChannelName } | Select-Object -First 1
    if (-not $channel) {
        throw "チャンネル '$ChannelName' が見つかりません。チャンネル名を確認するか -ChannelId で指定してください。"
    }
    $ChannelId = $channel.Id
    $channelDisplayName = $channel.DisplayName
    $channelMembershipType = if ($channel.MembershipType) { $channel.MembershipType } else { 'standard' }
} else {
    # ById の場合、メンション用に DisplayName を取得（追加スコープ不要）
    if ($MentionTeam) {
        Write-Verbose "チーム情報を取得中 (ID: $TeamId)..."
        $team = Get-MgTeam -TeamId $TeamId
        $teamDisplayName = $team.DisplayName
    }
    if ($MentionChannel) {
        Write-Verbose "チャンネル情報を取得中 (ID: $ChannelId)..."
        $channel = Get-MgTeamChannel -TeamId $TeamId -ChannelId $ChannelId
        $channelDisplayName = $channel.DisplayName
        $channelMembershipType = if ($channel.MembershipType) { $channel.MembershipType } else { 'standard' }
    }
}

# --- メンション構築 ---
# @channel / @team メンションは Channel.ReadBasic.All / Team.ReadBasic.All のみで利用可能。
# ユーザーメンションは User.ReadBasic.All が別途必要なため対象外。
$mentions = @()
$mentionId = 0
$mentionPrefix = ''

if ($MentionTeam) {
    $mentions += @{
        id          = $mentionId
        mentionText = $teamDisplayName
        mentioned   = @{
            team = @{
                id          = $TeamId
                displayName = $teamDisplayName
            }
        }
    }
    $mentionPrefix += "<at id=`"$mentionId`">$([System.Web.HttpUtility]::HtmlEncode($teamDisplayName))</at> "
    $mentionId++
}

if ($MentionChannel) {
    $mentions += @{
        id          = $mentionId
        mentionText = $channelDisplayName
        mentioned   = @{
            channel = @{
                id             = $ChannelId
                displayName    = $channelDisplayName
                membershipType = $channelMembershipType
            }
        }
    }
    $mentionPrefix += "<at id=`"$mentionId`">$([System.Web.HttpUtility]::HtmlEncode($channelDisplayName))</at> "
    $mentionId++
}

# --- メッセージ本文の組み立て ---
if ($mentions.Count -gt 0) {
    # メンションがある場合は HTML 必須
    $effectiveContentType = 'html'
    if ($ContentType -eq 'text') {
        # プレーンテキストを HTML エスケープしてからメンションを先頭に付加
        $escapedMessage = [System.Web.HttpUtility]::HtmlEncode($Message)
        $content = "$mentionPrefix$escapedMessage"
    } else {
        $content = "$mentionPrefix$Message"
    }
} else {
    $effectiveContentType = $ContentType
    $content = $Message
}

# --- 投稿ペイロード ---
$body = @{
    body = @{
        contentType = $effectiveContentType
        content     = $content
    }
}

if ($Subject) {
    $body['subject'] = $Subject
}

if ($mentions.Count -gt 0) {
    $body['mentions'] = $mentions
}

# --- メッセージ投稿 ---
Write-Verbose "メッセージを投稿中 (Team: $TeamId, Channel: $ChannelId)..."
$result = New-MgTeamChannelMessage -TeamId $TeamId -ChannelId $ChannelId -BodyParameter $body

Write-Host "投稿完了: $($result.WebUrl)" -ForegroundColor Green
