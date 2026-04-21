---
name: teams-use
description: Python + Microsoft Graph API を使って Microsoft Teams のチャンネルにメッセージを投稿したり、投稿済みのメッセージを読み取ったりする。Webhook 不要で MSAL デバイスコードフロー認証を利用。「Teamsに投稿して」「Teams のチャンネルにメッセージを送って」「Teams に通知して」「Teams にメッセージを投稿したい」「Teamsにタイトルを付けて投稿して」「@channel メンションして投稿して」「@team メンションして通知して」「Teams のスレッドに返信して」「Teams のメッセージに返信して」「Teams のチャンネルを読んで」「Teams の投稿を取得して」「Teams のメッセージを確認して」「Teams の最新投稿を見せて」「Teamsの投稿一覧を表示して」などのリクエストで発動する。Windows / macOS / Linux 環境の Python から Graph API 経由でメッセージ送受信を行う。
metadata:
  version: 1.0.0
  tier: experimental
  category: integration
  tags:
    - teams
    - microsoft
    - python
    - graph-api
    - notification
    - messaging
---

# teams-use

Python から Microsoft Graph API 経由で Teams チャンネルへのメッセージ投稿と、チャンネル内の投稿読み取りを行う。Webhook は使わず、MSAL デバイスコードフロー認証（初回のみブラウザ、以降はトークンキャッシュ）を利用する。タイトル（件名）の付与・`@channel` / `@team` メンション・スレッド返信・メッセージ一覧取得に対応。

セットアップ手順: [`references/setup-guide.md`](references/setup-guide.md)

---

## 前提条件

```bash
pip install msal requests
```

Azure AD にアプリ登録が必要。詳細は [references/setup-guide.md](references/setup-guide.md) を参照。

---

## 権限スコープ一覧

| 操作 | 必要スコープ | 備考 |
|------|------------|------|
| チャンネル投稿 | `ChannelMessage.Send` | 基本スコープ（投稿系） |
| チーム名前解決 | `Team.ReadBasic.All` | 基本スコープ（共通） |
| チャンネル名前解決 | `Channel.ReadBasic.All` | 基本スコープ（共通） |
| @channel メンション | `Channel.ReadBasic.All`（既存） | 追加不要 |
| @team メンション | `Team.ReadBasic.All`（既存） | 追加不要 |
| スレッド返信 | `ChannelMessage.Send`（既存） | 追加不要 |
| タイトルで返信先検索 | `ChannelMessage.Read.All`（追加） | `--reply-to-subject` 指定時のみ追加要求 |
| **メッセージ読み取り** | **`ChannelMessage.Read.All`** | **読み取り操作専用スコープ（投稿スコープ不要）** |

> **スコープ最小化の原則**: 読み取り操作（`get_teams_messages.py`）は `ChannelMessage.Read.All` のみを要求し `ChannelMessage.Send` は要求しない。投稿スクリプト（`send_teams_message.py`）は通常操作では `ChannelMessage.Read.All` を要求せず、`--reply-to-subject` 指定時のみ追加要求する。

---

## 基本ワークフロー

### 投稿する場合（send_teams_message.py）

#### Step 1: 送信先の特定

ユーザーに以下を確認する（不明な場合のみ質問する）:

| 項目 | 取得方法 |
|------|---------|
| チーム名 / ID | Teams クライアント → チーム右クリック → チームへのリンク取得 |
| チャンネル名 / ID | 同上、またはスクリプトで列挙 |
| メッセージ本文 | ユーザーの入力 |
| タイトル（任意） | ユーザーの入力 |
| メンション（任意） | `@channel` / `@team` の指定 |
| 返信先メッセージ ID（任意） | 投稿済みメッセージの WebUrl 末尾数値（`--reply-to-message-id`）|
| 返信先タイトル（任意） | タイトル文字列で検索（`--reply-to-subject`、`ChannelMessage.Read.All` を追加取得） |

#### Step 2: スクリプトを実行

[scripts/send_teams_message.py](scripts/send_teams_message.py) を使用する。

**基本的な使い方:**

```bash
# チームとチャンネルを名前で指定して投稿
python scripts/send_teams_message.py \
    --team-name "開発チーム" --channel-name "一般" \
    --message "デプロイが完了しました"

# タイトルを付けて投稿
python scripts/send_teams_message.py \
    --team-name "開発チーム" --channel-name "通知" \
    --subject "リリース完了" --message "v1.2.0 をリリースしました"

# @channel メンション付きで投稿（追加スコープ不要）
python scripts/send_teams_message.py \
    --team-name "開発チーム" --channel-name "通知" \
    --message "デプロイが完了しました" --mention-channel

# @team メンション付きで投稿（追加スコープ不要）
python scripts/send_teams_message.py \
    --team-name "開発チーム" --channel-name "告知" \
    --message "重要なお知らせです" --mention-team

# タイトル + @channel / @team メンションを同時に使用
python scripts/send_teams_message.py \
    --team-name "開発チーム" --channel-name "告知" \
    --subject "緊急メンテナンス" --message "本日 22:00 よりメンテナンスを実施します。" \
    --mention-channel --mention-team

# チーム・チャンネル ID を直接指定（高速）
python scripts/send_teams_message.py \
    --team-id "<guid>" --channel-id "<id>" \
    --message "ビルド完了"

# HTML フォーマット（太字・リンク等）
python scripts/send_teams_message.py \
    --team-name "開発チーム" --channel-name "通知" \
    --message "<b>アラート</b>: エラーが発生しました" --content-type html

# スレッド返信（追加スコープ不要）
python scripts/send_teams_message.py \
    --team-name "開発チーム" --channel-name "通知" \
    --reply-to-message-id "1234567890123" --message "対応完了しました。"

# タイトルで返信先を検索して返信（ChannelMessage.Read.All を追加要求）
python scripts/send_teams_message.py \
    --team-name "開発チーム" --channel-name "通知" \
    --reply-to-subject "リリース完了" --message "動作確認しました。問題ありません。"
```

### 読み取る場合（get_teams_messages.py）

チャンネルの投稿一覧を取得・表示する。**必要スコープ: `ChannelMessage.Read.All`**（投稿スコープ不要）

[scripts/get_teams_messages.py](scripts/get_teams_messages.py) を使用する。

```bash
# 直近 20 件を取得（デフォルト）
python scripts/get_teams_messages.py \
    --team-name "開発チーム" --channel-name "通知"

# 件数を指定して取得
python scripts/get_teams_messages.py \
    --team-name "開発チーム" --channel-name "通知" --top 50

# タイトルでフィルタリング（タイトル付き投稿のみ表示）
python scripts/get_teams_messages.py \
    --team-name "開発チーム" --channel-name "通知" --filter-subject "リリース"

# チーム・チャンネル ID を直接指定（高速）
python scripts/get_teams_messages.py \
    --team-id "<guid>" --channel-id "<id>" --top 10

# 本文プレビュー付きで表示
python scripts/get_teams_messages.py \
    --team-name "開発チーム" --channel-name "一般" --show-body

# JSON 形式で出力
python scripts/get_teams_messages.py \
    --team-name "開発チーム" --channel-name "通知" --json
```

### Step 3: 初回認証

スクリプト実行時にデバイスコードが表示される:

```
To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code XXXXXXXX to authenticate.
```

ブラウザでコードを入力して認証する。トークンは `~/.teams_graph_cache.json` にキャッシュされ、以降は再認証不要（有効期限内）。

---

## メンション機能について

### スコープを追加せずにメンションできる理由

`@channel` / `@team` メンションは、投稿先のチャンネル ID・チーム ID と表示名を使って構築できる。
これらは既存スコープ（`Channel.ReadBasic.All` / `Team.ReadBasic.All`）で取得済みのため、**追加スコープが不要**。

| メンション種別 | 必要スコープ | 本スクリプトで利用可能 |
|--------------|------------|----------------------|
| `@channel` | `Channel.ReadBasic.All`（既存） | ✅ `--mention-channel` |
| `@team` | `Team.ReadBasic.All`（既存） | ✅ `--mention-team` |
| `@ユーザー` | `User.ReadBasic.All`（追加必要） | ❌ 対象外 |

### スレッド返信も追加スコープ不要

`--reply-to-message-id` を指定すると返信 API（`/messages/{id}/replies`）を使用する。この API は `ChannelMessage.Send`（既存）のみで動作する。

返信先のメッセージ ID は `get_teams_messages.py` で確認できる:

```bash
python scripts/get_teams_messages.py \
    --team-name "開発チーム" --channel-name "通知" --top 10
# → 出力された Id を --reply-to-message-id に渡す
```

`--reply-to-subject` を使うとタイトルで曖昧検索して返信先を特定できる。このオプション指定時だけ `ChannelMessage.Read.All` を追加要求する。

| 返信方法 | オプション | 必要スコープ |
|---------|-----------|------------|
| ID を直接指定 | `--reply-to-message-id "1234..."` | 追加なし |
| タイトルで検索 | `--reply-to-subject "リリース完了"` | `ChannelMessage.Read.All`（追加）|

> **注意**: `--mention-channel` または `--mention-team` を指定した場合、メッセージ本文は HTML として送信される（メンションタグ `<at>` が HTML 形式のため）。`--content-type text` を同時に指定した場合、本文はエスケープされて HTML に変換される。

---

## チーム・チャンネル名の曖昧検索

チーム名・チャンネル名は完全一致でなくても動作する。スコアリングルール:

| 条件 | スコア | 動作 |
|------|-------|------|
| 完全一致（大小無視） | 3 | 確認なしで即選択 |
| 前方一致 | 2 | 候補が 1 件なら確認プロンプト |
| 部分一致 | 1 | 候補が 1 件なら確認プロンプト |
| 複数候補 | — | 番号選択プロンプト |

---

## エラー対処

| エラー | 対処 |
|--------|------|
| `Insufficient privileges` | Azure AD 管理者に必要スコープの権限付与を依頼 |
| `チームに一致する候補が見つかりません` | 別のキーワードで再試行するか `--team-id` で GUID を直接指定 |
| `チャンネルに一致する候補が見つかりません` | 別のキーワードで再試行するか `--channel-id` で ID を直接指定 |
| `msal not found` | `pip install msal requests` を実行 |
| 認証ループ | `~/.teams_graph_cache.json` を削除して再認証 |
| `ClientId not found` | `~/.teams_graph_client_id` を削除して Client ID を再入力 |

---

## スクリプト構成

```
scripts/
├── auth.py                  ← MSAL 認証共通ヘルパー（直接実行しない）
├── send_teams_message.py    ← メッセージ投稿・スレッド返信・メンション
└── get_teams_messages.py    ← メッセージ一覧取得・フィルタリング
references/
└── setup-guide.md           ← Azure AD 設定・初回セットアップ手順
```
