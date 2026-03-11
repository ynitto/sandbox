---
name: teams-poster
description: PowerShell を使って Microsoft Teams のチャンネルやチャットにメッセージを投稿する。Webhook 不要でローカル認証セッション（MicrosoftTeams / Microsoft.Graph モジュール）を利用。「Teamsに投稿して」「Teams のチャンネルにメッセージを送って」「Teams に通知して」「Teams にメッセージを投稿したい」などのリクエストで発動する。Windows 環境の PowerShell から Graph API 経由でメッセージ送信を行う。
metadata:
  version: 1.0.0
  tier: experimental
  category: integration
  tags:
    - teams
    - microsoft
    - powershell
    - notification
---

# Teams Poster

PowerShell から Microsoft Teams チャンネルへメッセージを投稿する。Webhook は使わず、Microsoft Graph API をローカル認証セッション経由で呼び出す。

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

### Step 2: スクリプトを実行

[scripts/Send-TeamsMessage.ps1](scripts/Send-TeamsMessage.ps1) を使用する。

**基本的な使い方:**

```powershell
# チームとチャンネルを名前で指定して投稿
.\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "一般" -Message "デプロイが完了しました"

# チーム・チャンネル ID を直接指定（高速）
.\Send-TeamsMessage.ps1 -TeamId "<guid>" -ChannelId "<guid>" -Message "ビルド完了"

# HTML フォーマット（太字・リンク等）
.\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "通知" `
    -Message "<b>アラート</b>: エラーが発生しました" -ContentType "html"
```

### Step 3: 初回認証

スクリプト実行時にブラウザが開き、Microsoft アカウントの認証画面が表示される。認証は **デバイスコードフロー** または **インタラクティブ** に対応。認証後は `~/.graph-token-cache` にトークンがキャッシュされ、以降は再認証不要（有効期限内）。

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
| `Team not found` | チーム名のスペル・大文字小文字を確認、またはIDで指定 |
| `Connect-MgGraph not found` | `Install-Module Microsoft.Graph -Scope CurrentUser` を実行 |
| 認証ループ | `Disconnect-MgGraph` 後に再接続 |

## 詳細セットアップ

初回セットアップ手順・テナント設定・チャット投稿は [references/setup-guide.md](references/setup-guide.md) 参照。
