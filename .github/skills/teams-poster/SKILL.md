---
name: teams-poster
description: PowerShell を使って Microsoft Teams のチャンネルやチャットにメッセージを投稿する。Webhook 不要でローカル認証セッション（MicrosoftTeams / Microsoft.Graph モジュール）を利用。「Teamsに投稿して」「Teams のチャンネルにメッセージを送って」「Teams に通知して」「Teams にメッセージを投稿したい」「Teamsにタイトルを付けて投稿して」「@channel メンションして投稿して」「@team メンションして通知して」「Teams のスレッドに返信して」「Teams のメッセージに返信して」などのリクエストで発動する。Windows 環境の PowerShell から Graph API 経由でメッセージ送信を行う。
metadata:
  version: 1.1.0
  tier: experimental
  category: integration
  tags:
    - teams
    - microsoft
    - powershell
    - notification
---

# Teams Poster

PowerShell から Microsoft Teams チャンネルへメッセージを投稿する。Webhook は使わず、Microsoft Graph API をローカル認証セッション経由で呼び出す。タイトル（件名）の付与と `@channel` / `@team` メンションに対応。

## 前提条件

以下のいずれかの PowerShell モジュールが必要:

- **Microsoft.Graph**（推奨）: `Install-Module Microsoft.Graph -Scope CurrentUser`
- **MicrosoftTeams**: `Install-Module MicrosoftTeams -Scope CurrentUser`

必要な Graph API スコープ: `ChannelMessage.Send`（チャンネル投稿）または `Chat.ReadWrite`（チャット投稿）

## 基本ワークフロー

### Step 1: 送信先の特定

ユーザーに以下を確認する（不明な場合のみ質問する）:

| 項目 | 取得方法 |
|------|---------|
| チーム名 / ID | Teams クライアント → チーム右クリック → チームへのリンク取得 |
| チャンネル名 / ID | 同上、またはスクリプトで列挙 |
| メッセージ本文 | ユーザーの入力 |
| タイトル（任意） | ユーザーの入力 |
| メンション（任意） | `@channel` / `@team` の指定 |
| 返信先メッセージ ID（任意） | 投稿済みメッセージの WebUrl 末尾の数値（`-ReplyToMessageId`）|
| 返信先タイトル（任意） | タイトル文字列で検索（`-ReplyToSubject`、`ChannelMessage.Read.All` を追加取得） |

### Step 2: スクリプトを実行

[scripts/Send-TeamsMessage.ps1](scripts/Send-TeamsMessage.ps1) を使用する。

**基本的な使い方:**

```powershell
# チームとチャンネルを名前で指定して投稿
.\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "一般" -Message "デプロイが完了しました"

# タイトルを付けて投稿
.\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "通知" `
    -Subject "リリース完了" -Message "v1.2.0 をリリースしました"

# @channel メンション付きで投稿（追加スコープ不要）
.\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "通知" `
    -Message "デプロイが完了しました" -MentionChannel

# @team メンション付きで投稿（追加スコープ不要）
.\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "告知" `
    -Message "重要なお知らせです" -MentionTeam

# タイトル + @channel / @team メンションを同時に使用
.\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "告知" `
    -Subject "緊急メンテナンス" -Message "本日 22:00 よりメンテナンスを実施します。" `
    -MentionChannel -MentionTeam

# チーム・チャンネル ID を直接指定（高速）
.\Send-TeamsMessage.ps1 -TeamId "<guid>" -ChannelId "<guid>" -Message "ビルド完了"

# HTML フォーマット（太字・リンク等）
.\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "通知" `
    -Message "<b>アラート</b>: エラーが発生しました" -ContentType "html"

# スレッド返信（追加スコープ不要）
.\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "通知" `
    -ReplyToMessageId "1234567890123" -Message "対応完了しました。"

# タイトルで返信先を検索して返信（ChannelMessage.Read.All を追加要求）
.\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "通知" `
    -ReplyToSubject "リリース完了" -Message "動作確認しました。問題ありません。"
```

### Step 3: 初回認証

スクリプト実行時にブラウザが開き、Microsoft アカウントの認証画面が表示される。認証は **デバイスコードフロー** または **インタラクティブ** に対応。認証後は `~/.graph-token-cache` にトークンがキャッシュされ、以降は再認証不要（有効期限内）。

## メンション機能について

### スコープを追加せずにメンションできる理由

`@channel` / `@team` メンションは、投稿先のチャンネル ID・チーム ID と表示名を使って構築できる。
これらは既存スコープ（`Channel.ReadBasic.All` / `Team.ReadBasic.All`）で取得済みのため、**追加スコープが不要**。

| メンション種別 | 必要スコープ | 本スクリプトで利用可能 |
|--------------|------------|----------------------|
| `@channel` | `Channel.ReadBasic.All`（既存） | ✅ `-MentionChannel` |
| `@team` | `Team.ReadBasic.All`（既存） | ✅ `-MentionTeam` |
| `@ユーザー` | `User.ReadBasic.All`（追加必要） | ❌ 対象外 |

### スレッド返信も追加スコープ不要

`-ReplyToMessageId` を指定すると `New-MgTeamChannelMessageReply` を使用する。この API は `ChannelMessage.Send`（既存）のみで動作する。

返信先のメッセージ ID は投稿済みメッセージの WebUrl 末尾の数値か、以下で取得できる:

```powershell
# チャンネルの最新メッセージ一覧（ChannelMessage.Read.All が別途必要なため参考）
# → 実運用では投稿時に返却された WebUrl から ID を控えておくのが簡単
Get-MgTeamChannelMessage -TeamId "<team-id>" -ChannelId "<channel-id>" -Top 10 |
    Select-Object Id, @{N='Preview'; E={$_.Body.Content.Substring(0, [Math]::Min(50, $_.Body.Content.Length))}}
```

> **注意**: `Get-MgTeamChannelMessage` は `ChannelMessage.Read.All` が必要なため、メッセージ ID の取得には別途スコープが必要。既知の ID（投稿時に WebUrl から控えた値）を使う場合は追加スコープ不要。

`-ReplyToSubject` を使うとタイトルで曖昧検索して返信先を特定できる。このパラメータ指定時だけ `ChannelMessage.Read.All` を追加要求し、それ以外の通常操作には影響しない。

| 返信方法 | パラメータ | 必要スコープ |
|---------|-----------|------------|
| ID を直接指定 | `-ReplyToMessageId "1234..."` | 追加なし |
| タイトルで検索 | `-ReplyToSubject "リリース完了"` | `ChannelMessage.Read.All`（追加）|

> **注意**: `-MentionChannel` または `-MentionTeam` を指定した場合、メッセージ本文は HTML として送信される（メンションタグ `<at>` が HTML 形式のため）。`-ContentType text` を同時に指定した場合、本文はエスケープされて HTML に変換される。

## ID の調べ方

名前がわかればスクリプトが自動解決するが、ID を事前に調べる場合:

```powershell
Connect-MgGraph -Scopes "Team.ReadBasic.All","Channel.ReadBasic.All"

# 参加中のチーム一覧
Get-MgJoinedTeam | Select-Object DisplayName, Id

# チャンネル一覧（TeamId を置換）
Get-MgTeamChannel -TeamId "<team-id>" | Select-Object DisplayName, Id
```

## エラー対処

| エラー | 対処 |
|--------|------|
| `Insufficient privileges` | Azure AD 管理者に `ChannelMessage.Send` 権限付与を依頼 |
| `チームに一致する候補が見つかりません` | 別のキーワードで再試行するか `-TeamId` で GUID を直接指定 |
| `チャンネルに一致する候補が見つかりません` | 別のキーワードで再試行するか `-ChannelId` で ID を直接指定 |
| `Connect-MgGraph not found` | `Install-Module Microsoft.Graph -Scope CurrentUser` を実行 |
| 認証ループ | `Disconnect-MgGraph` 後に再接続 |

## 詳細セットアップ

初回セットアップ手順・テナント設定・チャット投稿は [references/setup-guide.md](references/setup-guide.md) 参照。
