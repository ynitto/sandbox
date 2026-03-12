#Requires -Modules Microsoft.Graph.Teams, Microsoft.Graph.Authentication
<#
.SYNOPSIS
    Microsoft Teams チャンネルの投稿を読み取る。

.DESCRIPTION
    Microsoft Graph API を使い、Teams チャンネルの投稿一覧を取得・表示する。
    ChannelMessage.Read.All スコープのみ使用。ChannelMessage.Send は不要。
    タイトルでのフィルタリング、本文プレビューの表示に対応。

.PARAMETER TeamName
    対象チームの表示名（TeamId と排他）。

.PARAMETER TeamId
    対象チームの GUID（TeamName と排他）。

.PARAMETER ChannelName
    対象チャンネルの表示名（ChannelId と排他）。

.PARAMETER ChannelId
    対象チャンネルの GUID（ChannelName と排他）。

.PARAMETER Top
    取得するメッセージの最大件数。既定値は 20。

.PARAMETER FilterSubject
    タイトル（件名）で絞り込む。部分一致。省略すると全メッセージを表示。

.PARAMETER ShowBody
    本文プレビュー（先頭 100 文字）を表示する。省略すると Id・タイトル・日時・送信者のみ表示。

.EXAMPLE
    .\Get-TeamsMessages.ps1 -TeamName "開発チーム" -ChannelName "通知"

.EXAMPLE
    .\Get-TeamsMessages.ps1 -TeamName "開発チーム" -ChannelName "通知" -Top 50

.EXAMPLE
    .\Get-TeamsMessages.ps1 -TeamName "開発チーム" -ChannelName "通知" -FilterSubject "リリース"

.EXAMPLE
    .\Get-TeamsMessages.ps1 -TeamId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
        -ChannelId "19:xxxx@thread.tacv2" -Top 10 -ShowBody
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

    [ValidateRange(1, 200)]
    [int]$Top = 20,

    [string]$FilterSubject,

    [switch]$ShowBody
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --- 曖昧検索 + 確認ヘルパー ---
function Select-FromMatches {
    param(
        [string]$Query,
        [object[]]$Candidates,
        [string]$Label
    )

    $lower = $Query.ToLower()

    $scored = $Candidates | ForEach-Object {
        $dn    = $_.DisplayName
        $score = if ($dn.ToLower() -eq $lower)           { 3 }
                 elseif ($dn.ToLower().StartsWith($lower)) { 2 }
                 elseif ($dn.ToLower().Contains($lower))   { 1 }
                 else                                      { 0 }
        [PSCustomObject]@{ Item = $_; Score = $score }
    } | Where-Object { $_.Score -gt 0 } | Sort-Object -Property Score -Descending

    if ($scored.Count -eq 0) {
        throw "${Label} '$Query' に一致する候補が見つかりません。スペルを確認するか -${Label}Id で ID を直接指定してください。"
    }

    $hits = @($scored | Select-Object -ExpandProperty Item)

    if ($hits.Count -eq 1) {
        if ($scored[0].Score -eq 3) {
            Write-Host "${Label}: 「$($hits[0].DisplayName)」" -ForegroundColor DarkGray
            return $hits[0]
        }
        Write-Host "${Label}候補: 「$($hits[0].DisplayName)」" -ForegroundColor Cyan
        $ans = ''
        while ($ans -notin @('y', 'n')) {
            $ans = (Read-Host "この${Label}のメッセージを読み取りますか？ [y/n]").Trim().ToLower()
        }
        if ($ans -ne 'y') {
            throw "キャンセルされました。-${Label}Name を修正するか -${Label}Id で指定してください。"
        }
        return $hits[0]
    }

    Write-Host "'$Query' に一致する${Label}が複数見つかりました:" -ForegroundColor Cyan
    for ($i = 0; $i -lt $hits.Count; $i++) {
        Write-Host ("  [{0}] {1}" -f ($i + 1), $hits[$i].DisplayName) -ForegroundColor White
    }
    $choice = $null
    while ($null -eq $choice) {
        $raw = (Read-Host "番号を選択してください [1-$($hits.Count)]").Trim()
        if ($raw -match '^\d+$') {
            $n = [int]$raw
            if ($n -ge 1 -and $n -le $hits.Count) { $choice = $n }
        }
        if ($null -eq $choice) {
            Write-Host "  1 から $($hits.Count) の数字を入力してください。" -ForegroundColor Yellow
        }
    }
    return $hits[$choice - 1]
}

# --- 認証 ---
# 読み取り専用: ChannelMessage.Read.All のみ要求。ChannelMessage.Send は不要。
$requiredScopes = @('ChannelMessage.Read.All', 'Team.ReadBasic.All', 'Channel.ReadBasic.All')

$context = Get-MgContext -ErrorAction SilentlyContinue
if (-not $context) {
    Write-Host "Microsoft Graph に接続します..." -ForegroundColor Cyan
    Connect-MgGraph -Scopes $requiredScopes -NoWelcome
}

# --- チーム / チャンネル ID 解決 ---
if ($PSCmdlet.ParameterSetName -eq 'ByName') {
    Write-Host "チーム '$TeamName' を検索中..." -ForegroundColor DarkGray
    $allTeams = Get-MgJoinedTeam -All
    $team = Select-FromMatches -Query $TeamName -Candidates $allTeams -Label 'チーム'
    $TeamId = $team.Id

    Write-Host "チャンネル '$ChannelName' を検索中..." -ForegroundColor DarkGray
    $allChannels = Get-MgTeamChannel -TeamId $TeamId -All
    $channel = Select-FromMatches -Query $ChannelName -Candidates $allChannels -Label 'チャンネル'
    $ChannelId = $channel.Id
    $channelDisplayName = $channel.DisplayName
} else {
    $channelDisplayName = $ChannelId
}

# --- メッセージ取得 ---
Write-Host "メッセージを取得中（最大 $Top 件）..." -ForegroundColor DarkGray
$messages = @(Get-MgTeamChannelMessage -TeamId $TeamId -ChannelId $ChannelId -Top $Top)

if ($messages.Count -eq 0) {
    Write-Host "チャンネルにメッセージが見つかりません。" -ForegroundColor Yellow
    return
}

# --- フィルタリング ---
if ($FilterSubject) {
    $lower = $FilterSubject.ToLower()
    $messages = @($messages | Where-Object {
        $_.Subject -and $_.Subject.ToLower().Contains($lower)
    })

    if ($messages.Count -eq 0) {
        Write-Host "タイトル '$FilterSubject' に一致するメッセージが見つかりません。" -ForegroundColor Yellow
        return
    }
}

# --- 表示 ---
Write-Host "`nチャンネル: $channelDisplayName  ($($messages.Count) 件)" -ForegroundColor Cyan
Write-Host ("-" * 60) -ForegroundColor DarkGray

foreach ($msg in $messages) {
    $ts      = $msg.CreatedDateTime.ToLocalTime().ToString('yyyy-MM-dd HH:mm')
    $sender  = if ($msg.From.User.DisplayName) { $msg.From.User.DisplayName } else { '（不明）' }
    $subject = if ($msg.Subject) { "[$($msg.Subject)] " } else { '' }

    Write-Host "$ts  $sender" -ForegroundColor White -NoNewline
    Write-Host "  Id: $($msg.Id)" -ForegroundColor DarkGray
    if ($subject) {
        Write-Host "  件名: $($msg.Subject)" -ForegroundColor Yellow
    }
    if ($ShowBody) {
        # HTML タグを除去してプレビュー表示
        $plainBody = $msg.Body.Content -replace '<[^>]+>', '' -replace '&nbsp;', ' ' `
                     -replace '&lt;', '<' -replace '&gt;', '>' -replace '&amp;', '&'
        $plainBody = $plainBody.Trim()
        $preview   = if ($plainBody.Length -gt 100) { $plainBody.Substring(0, 100) + '…' } else { $plainBody }
        if ($preview) {
            Write-Host "  本文: $preview" -ForegroundColor Gray
        }
    }
    Write-Host ("-" * 60) -ForegroundColor DarkGray
}

Write-Host "合計 $($messages.Count) 件を表示しました。" -ForegroundColor Green
