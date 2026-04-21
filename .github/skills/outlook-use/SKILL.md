---
name: outlook-use
description: Python + Microsoft Graph API を使って Outlook のメール・カレンダーを操作する。「メールを送って」「受信トレイを確認して」「メールを検索して」「予定を確認して」「カレンダーに予定を追加して」「予定を削除して」「Outlookを確認して」「未読メールを見せて」「〇〇に返信して」「会議を作成して」などのリクエストで発動する。Windows / macOS / Linux 環境の Python から Graph API を呼び出す。認証は MSAL デバイスコードフロー（初回のみブラウザ）。
metadata:
  version: 1.0.0
  tier: experimental
  category: integration
  tags:
    - outlook
    - email
    - calendar
    - microsoft
    - graph-api
    - python
---

# outlook-use

Python から Microsoft Graph API 経由で Outlook のメールとカレンダーを操作する。
認証は MSAL デバイスコードフロー（初回のみブラウザ認証、以降はトークンキャッシュを利用）。

セットアップ手順: [`references/setup-guide.md`](references/setup-guide.md)

---

## 前提条件

```bash
pip install msal requests
```

Azure AD にアプリ登録が必要。詳細は [references/setup-guide.md](references/setup-guide.md) を参照。

---

## 権限スコープ一覧

| 操作 | 必要スコープ |
|------|------------|
| メール読み取り | `Mail.Read` |
| メール送信 | `Mail.Send` |
| カレンダー読み取り | `Calendars.Read` |
| カレンダー作成・更新・削除 | `Calendars.ReadWrite` |
| オフライン（トークン更新） | `offline_access` |

> **スコープ最小化の原則**: 各スクリプトは必要最小限のスコープのみ要求する。読み取りスクリプトは書き込みスコープを要求しない。

---

## 操作一覧

| 操作 | スクリプト | 主なスコープ |
|------|-----------|------------|
| メール一覧・検索 | `get_mail.py` | `Mail.Read` |
| メール送信 | `send_mail.py` | `Mail.Send` |
| カレンダー予定管理 | `calendar_events.py` | `Calendars.ReadWrite` |

---

## メール読み取り（get_mail.py）

受信トレイやフォルダのメールを一覧・検索する。

```bash
# 受信トレイの直近 20 件を表示
python scripts/get_mail.py

# 未読メールのみ表示
python scripts/get_mail.py --unread-only

# 件数を指定して取得
python scripts/get_mail.py --top 50

# 送信済みフォルダを確認
python scripts/get_mail.py --folder sentitems

# キーワードで検索
python scripts/get_mail.py --search "会議"

# 本文も含めて表示
python scripts/get_mail.py --show-body

# JSON 形式で出力
python scripts/get_mail.py --json

# メール ID を指定して本文を表示
python scripts/get_mail.py --message-id <message-id>
```

### 対応フォルダ名

| フォルダ | 名前 |
|---------|------|
| 受信トレイ | `inbox` |
| 送信済み | `sentitems` |
| 下書き | `drafts` |
| 削除済み | `deleteditems` |
| 迷惑メール | `junkemail` |

---

## メール送信（send_mail.py）

新規メールを送信する。

```bash
# 基本的な送信
python scripts/send_mail.py \
    --to "example@example.com" \
    --subject "件名" \
    --body "本文"

# 複数宛先（カンマ区切り）
python scripts/send_mail.py \
    --to "a@example.com,b@example.com" \
    --subject "件名" \
    --body "本文"

# CC / BCC を指定
python scripts/send_mail.py \
    --to "a@example.com" \
    --cc "b@example.com" \
    --bcc "c@example.com" \
    --subject "件名" \
    --body "本文"

# HTML 形式で送信
python scripts/send_mail.py \
    --to "a@example.com" \
    --subject "件名" \
    --body "<b>太字</b>テキスト" \
    --html

# 送信済みフォルダに保存しない
python scripts/send_mail.py \
    --to "a@example.com" \
    --subject "件名" \
    --body "本文" \
    --no-save
```

---

## カレンダー管理（calendar_events.py）

予定の一覧取得・作成・削除を行う。

### 予定一覧

```bash
# 今後の予定を 20 件表示
python scripts/calendar_events.py list

# 件数を指定
python scripts/calendar_events.py list --top 50

# 日付範囲でフィルタ
python scripts/calendar_events.py list --start 2025-01-01 --end 2025-01-31

# JSON 形式で出力
python scripts/calendar_events.py list --json
```

### 予定作成

```bash
# 基本的な予定作成（タイムゾーンは Asia/Tokyo がデフォルト）
python scripts/calendar_events.py create \
    --subject "チームミーティング" \
    --start "2025-01-15T10:00:00" \
    --end "2025-01-15T11:00:00"

# 場所・参加者・本文を指定
python scripts/calendar_events.py create \
    --subject "プロジェクトレビュー" \
    --start "2025-01-15T14:00:00" \
    --end "2025-01-15T15:00:00" \
    --location "会議室A" \
    --body "月次レビューです" \
    --attendees "a@example.com,b@example.com"

# 終日予定
python scripts/calendar_events.py create \
    --subject "全社休日" \
    --start "2025-01-15" \
    --end "2025-01-15" \
    --all-day

# タイムゾーンを指定
python scripts/calendar_events.py create \
    --subject "海外MTG" \
    --start "2025-01-15T09:00:00" \
    --end "2025-01-15T10:00:00" \
    --timezone "UTC"
```

### 予定削除

```bash
# 予定 ID を指定して削除（ID は list コマンドで確認）
python scripts/calendar_events.py delete --event-id <event-id>
```

---

## 基本ワークフロー

### Step 1: 初回セットアップ

1. Azure AD にアプリを登録し Client ID を取得する（[setup-guide.md](references/setup-guide.md) 参照）
2. `pip install msal requests` でパッケージをインストール
3. スクリプト初回実行時に Client ID の入力を求められる（`~/.outlook_graph_client_id` に保存）

### Step 2: 初回認証

スクリプト実行時にデバイスコードが表示される:

```
To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code XXXXXXXX to authenticate.
```

ブラウザでコードを入力して認証する。トークンは `~/.outlook_graph_cache.json` にキャッシュされ、以降は再認証不要（有効期限内）。

### Step 3: 以降の実行

キャッシュが有効な間はサイレントで認証され、コマンドがすぐに実行される。

---

## エラー対処

| エラー | 対処 |
|--------|------|
| `Insufficient privileges` | Azure AD 管理者に必要スコープの権限付与を依頼 |
| `AADSTS70011` | スコープが正しく設定されているか確認 |
| `msal not found` | `pip install msal requests` を実行 |
| 認証ループ | `~/.outlook_graph_cache.json` を削除して再認証 |
| `ClientId not found` | `~/.outlook_graph_client_id` を削除して Client ID を再入力 |

---

## スクリプト構成

```
scripts/
├── auth.py              ← MSAL 認証共通ヘルパー（直接実行しない）
├── get_mail.py          ← メール読み取り
├── send_mail.py         ← メール送信
└── calendar_events.py   ← カレンダー管理（list / create / delete）
references/
└── setup-guide.md       ← Azure AD 設定・初回セットアップ手順
```
