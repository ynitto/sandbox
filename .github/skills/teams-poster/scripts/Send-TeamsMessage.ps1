#Requires -Modules Microsoft.Graph.Teams, Microsoft.Graph.Authentication
<#
.SYNOPSIS
    Microsoft Teams チャンネルにメッセージを投稿する。

.DESCRIPTION
    Microsoft Graph API を使い、Webhook なしで Teams チャンネルへメッセージを送信する。
    初回はブラウザ認証が必要。以降はトークンキャッシュを使用する。
    タイトル（件名）の付与、@channel / @team メンション、スレッド返信に対応。
    いずれの機能も追加スコープ不要（ChannelMessage.Send / Team.ReadBasic.All / Channel.ReadBasic.All のみ使用）。

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

.PARAMETER ReplyToMessageId
    返信先メッセージの ID。指定するとスレッド返信になる（追加スコープ不要）。
    メッセージ ID は投稿済みメッセージの WebUrl 末尾数値。-ReplyToSubject と排他。
    返信時は -Subject は無視される（Teams の仕様）。

.PARAMETER ReplyToSubject
    返信先メッセージをタイトル（件名）で検索して ID を特定する。
    ChannelMessage.Read.All スコープが必要（このパラメータ指定時のみ追加要求）。
    -ReplyToMessageId と排他。曖昧検索＋確認プロンプトで返信先を選択できる。
    直近 50 件のメッセージを対象に検索する。

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

.EXAMPLE
    # スレッド返信（追加スコープ不要）
    .\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "通知" `
        -ReplyToMessageId "1234567890123" -Message "対応完了しました。"

.EXAMPLE
    # タイトルでメッセージを検索して返信（ChannelMessage.Read.All を追加要求）
    .\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "通知" `
        -ReplyToSubject "リリース完了" -Message "動作確認しました。問題ありません。"
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

    [switch]$MentionTeam,

    [string]$ReplyToMessageId,

    [string]$ReplyToSubject
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($ReplyToMessageId -and $ReplyToSubject) {
    throw '-ReplyToMessageId と -ReplyToSubject は同時に指定できません。どちらか一方を使用してください。'
}

# --- 曖昧検索 + 確認ヘルパー ---
# 追加スコープ不要。Team.ReadBasic.All / Channel.ReadBasic.All のみ使用。
function Select-FromMatches {
    param(
        [string]$Query,
        [object[]]$Candidates,
        [string]$Label  # 表示用ラベル（例: "チーム" / "チャンネル"）
    )

    $lower = $Query.ToLower()

    # スコアリング: 完全一致(大小無視)=3 / 前方一致=2 / 部分一致=1 / 不一致=0
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
            # 完全一致（大小無視）→ 確認不要
            Write-Host "${Label}: 「$($hits[0].DisplayName)」" -ForegroundColor DarkGray
            return $hits[0]
        }
        # 部分一致の1件 → 確認
        Write-Host "${Label}候補: 「$($hits[0].DisplayName)」" -ForegroundColor Cyan
        $ans = ''
        while ($ans -notin @('y', 'n')) {
            $ans = (Read-Host "この${Label}に投稿しますか？ [y/n]").Trim().ToLower()
        }
        if ($ans -ne 'y') {
            throw "キャンセルされました。-${Label}Name を修正するか -${Label}Id で指定してください。"
        }
        return $hits[0]
    }

    # 複数候補 → 番号選択
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
# -ReplyToSubject 指定時のみ ChannelMessage.Read.All を追加要求する
$requiredScopes = @('ChannelMessage.Send', 'Team.ReadBasic.All', 'Channel.ReadBasic.All')
if ($ReplyToSubject) {
    $requiredScopes += 'ChannelMessage.Read.All'
}

$context = Get-MgContext -ErrorAction SilentlyContinue
if (-not $context) {
    Write-Host "Microsoft Graph に接続します..." -ForegroundColor Cyan
    Connect-MgGraph -Scopes $requiredScopes -NoWelcome
}

# --- チーム / チャンネル ID 解決 + DisplayName 取得 ---
if ($PSCmdlet.ParameterSetName -eq 'ByName') {
    Write-Host "チーム '$TeamName' を検索中..." -ForegroundColor DarkGray
    $allTeams = Get-MgJoinedTeam -All
    $team = Select-FromMatches -Query $TeamName -Candidates $allTeams -Label 'チーム'
    $TeamId = $team.Id
    $teamDisplayName = $team.DisplayName

    Write-Host "チャンネル '$ChannelName' を検索中..." -ForegroundColor DarkGray
    $allChannels = Get-MgTeamChannel -TeamId $TeamId -All
    $channel = Select-FromMatches -Query $ChannelName -Candidates $allChannels -Label 'チャンネル'
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

# --- タイトル検索によるメッセージ ID 解決 ---
# ChannelMessage.Read.All が必要（-ReplyToSubject 指定時のみ追加取得）
if ($ReplyToSubject) {
    Write-Host "タイトル '$ReplyToSubject' でメッセージを検索中（直近 50 件）..." -ForegroundColor DarkGray
    $messages = @(Get-MgTeamChannelMessage -TeamId $TeamId -ChannelId $ChannelId -Top 50 |
        Where-Object { $_.Subject })  # Subject なし（通常投稿）は除外

    if ($messages.Count -eq 0) {
        throw "チャンネル内にタイトル付きメッセージが見つかりません。直近 50 件に Subject のある投稿がありません。"
    }

    $lower = $ReplyToSubject.ToLower()
    $scored = $messages | ForEach-Object {
        $s     = $_.Subject
        $score = if ($s.ToLower() -eq $lower)           { 3 }
                 elseif ($s.ToLower().StartsWith($lower)) { 2 }
                 elseif ($s.ToLower().Contains($lower))   { 1 }
                 else                                      { 0 }
        [PSCustomObject]@{ Item = $_; Score = $score }
    } | Where-Object { $_.Score -gt 0 } | Sort-Object Score -Descending

    if ($scored.Count -eq 0) {
        throw "タイトル '$ReplyToSubject' に一致するメッセージが見つかりません。別のキーワードで再試行してください。"
    }

    $hits = @($scored | Select-Object -ExpandProperty Item)

    if ($hits.Count -eq 1 -and $scored[0].Score -eq 3) {
        # 完全一致 → 確認不要
        $ts = $hits[0].CreatedDateTime.ToLocalTime().ToString('yyyy-MM-dd HH:mm')
        Write-Host "メッセージ: 「$($hits[0].Subject)」 ($ts)" -ForegroundColor DarkGray
        $ReplyToMessageId = $hits[0].Id
    } elseif ($hits.Count -eq 1) {
        # 部分一致 1件 → 確認
        $ts = $hits[0].CreatedDateTime.ToLocalTime().ToString('yyyy-MM-dd HH:mm')
        Write-Host "メッセージ候補: 「$($hits[0].Subject)」 ($ts)" -ForegroundColor Cyan
        $ans = ''
        while ($ans -notin @('y', 'n')) {
            $ans = (Read-Host 'このメッセージに返信しますか？ [y/n]').Trim().ToLower()
        }
        if ($ans -ne 'y') {
            throw "キャンセルされました。-ReplyToSubject のキーワードを変更するか -ReplyToMessageId で直接指定してください。"
        }
        $ReplyToMessageId = $hits[0].Id
    } else {
        # 複数候補 → 番号選択
        Write-Host "'$ReplyToSubject' に一致するメッセージが複数見つかりました:" -ForegroundColor Cyan
        for ($i = 0; $i -lt $hits.Count; $i++) {
            $ts = $hits[$i].CreatedDateTime.ToLocalTime().ToString('yyyy-MM-dd HH:mm')
            Write-Host ("  [{0}] 「{1}」 ({2})" -f ($i + 1), $hits[$i].Subject, $ts) -ForegroundColor White
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
        $ReplyToMessageId = $hits[$choice - 1].Id
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

# Subject は新規投稿のみ有効（返信スレッドには設定不可）
if ($Subject -and -not $ReplyToMessageId) {
    $body['subject'] = $Subject
}

if ($mentions.Count -gt 0) {
    $body['mentions'] = $mentions
}

# --- メッセージ投稿 / 返信 ---
# 返信も ChannelMessage.Send のみで動作し、追加スコープは不要。
if ($ReplyToMessageId) {
    Write-Verbose "返信を投稿中 (Team: $TeamId, Channel: $ChannelId, ReplyTo: $ReplyToMessageId)..."
    $result = New-MgTeamChannelMessageReply `
        -TeamId $TeamId -ChannelId $ChannelId -ChatMessageId $ReplyToMessageId `
        -BodyParameter $body
} else {
    Write-Verbose "メッセージを投稿中 (Team: $TeamId, Channel: $ChannelId)..."
    $result = New-MgTeamChannelMessage -TeamId $TeamId -ChannelId $ChannelId -BodyParameter $body
}

Write-Host "投稿完了: $($result.WebUrl)" -ForegroundColor Green
