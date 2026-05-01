---
name: redmine-use
description: Redmine REST API でチケットの一覧取得・読み取り・更新・コメント投稿を行う。「Redmine のチケットを確認して」「チケットを更新して」「コメントを投稿して」「Redmine を設定して」「接続情報を設定して」「Redmine の設定をして」などで発動する。Python スクリプト経由。
metadata:
  version: 1.0.0
  tier: experimental
  category: integration
  tags:
    - redmine
    - issue-tracker
    - rest-api
    - python
    - ticket
---

# Redmine Use

Python スクリプトから Redmine REST API を呼び出してチケット操作を行う。

## 「設定して」リクエスト時の対応

ユーザーから「Redmine を設定して」「接続情報を設定して」などのリクエストを受けたら以下を実行する:

1. **必要情報の確認**: URL・ラベルが指定されているか確認する。ない場合はユーザーに確認するか対話プロンプトを使う。
2. **configure の実行**: 必要情報が提示されている場合はオプション付きで、なければ対話形式で実行する。

```bash
# URL と API キーが分かっている場合
python {skill_home}/redmine-use/scripts/redmine_client.py --label default configure \
  --url https://redmine.example.com --api-key YOUR_API_KEY

# URL やキーが不明な場合は対話プロンプト
python {skill_home}/redmine-use/scripts/redmine_client.py --label default configure
```

3. **接続確認**: configure 実行後に `info` で接続を確認する。

```bash
python {skill_home}/redmine-use/scripts/redmine_client.py --label default info
```

## 前提条件

- Python 3.8+（標準ライブラリのみ。YAML 設定ファイルを使う場合は `pip install pyyaml` が必要）
- Redmine URL と API キー（Redmine → 個人設定 → API アクセスキーで確認）

## 接続情報の設定

「Redmine を設定して」「接続情報を設定して」などのリクエストを受けたら、`configure` コマンドを実行して `connections.yaml` に保存する。

### configure コマンドで設定（推奨）

```bash
# 対話形式（プロンプトで入力）
python {skill_home}/redmine-use/scripts/redmine_client.py configure

# オプションで直接指定
python {skill_home}/redmine-use/scripts/redmine_client.py configure \
  --url https://redmine.example.com --api-key YOUR_API_KEY

# ラベルを指定して複数環境を管理
python {skill_home}/redmine-use/scripts/redmine_client.py --label staging configure \
  --url https://staging.redmine.example.com --api-key YOUR_STAGING_KEY
```

設定は `{agent_dir}/connections.yaml`（例: `.github/connections.yaml`）に保存される。
APIキーを直接記述する場合は `.gitignore` に追加すること。

### connections.yaml の直接編集

```yaml
# .github/connections.yaml
redmine:
  - label: default
    url: https://redmine.example.com
    api_key: ${REDMINE_API_KEY}   # 環境変数を参照

  - label: staging
    url: https://staging.redmine.example.com
    api_key: your_staging_api_key
```

テンプレートは `{agent_dir}/connections.yaml.example`（例: `.github/connections.yaml.example`）を参照。

接続情報の解決順序（上位優先）:

1. `--url` / `--api-key` CLI オプション
2. **`connections.yaml`**（ワークスペース > グローバル）`--label` で接続先を切り替え可能
3. 環境変数 `REDMINE_URL` / `REDMINE_API_KEY`
4. ワークスペース設定ファイル `.redmine.json`（後方互換）

### 環境変数で設定

```bash
export REDMINE_URL=https://redmine.example.com
export REDMINE_API_KEY=your_api_key_here
```

## 基本ワークフロー

### Step 1: 接続確認

```bash
python {skill_home}/redmine-use/scripts/redmine_client.py info
```

Redmine バージョンと接続状態を確認する。

### Step 2: 操作を実行

目的に応じて以下のコマンドを使い分ける。

---

## コマンドリファレンス

### チケット一覧の取得

```bash
# プロジェクト内の全オープンチケット
python {skill_home}/redmine-use/scripts/redmine_client.py list --project my-project

# ステータス指定（open / closed / * / ステータスID）
python {skill_home}/redmine-use/scripts/redmine_client.py list --project my-project --status closed

# 担当者で絞り込み（me または ユーザーID）
python {skill_home}/redmine-use/scripts/redmine_client.py list --project my-project --assigned-to me

# トラッカー指定
python {skill_home}/redmine-use/scripts/redmine_client.py list --project my-project --tracker 1

# 優先度指定
python {skill_home}/redmine-use/scripts/redmine_client.py list --project my-project --priority 2

# 取得件数とオフセット
python {skill_home}/redmine-use/scripts/redmine_client.py list --project my-project --limit 50 --offset 0

# ソート（created_on:desc, updated_on:asc など）
python {skill_home}/redmine-use/scripts/redmine_client.py list --project my-project --sort updated_on:desc

# 作成日時・更新日時の範囲フィルタ（Redmine 演算子形式）
python {skill_home}/redmine-use/scripts/redmine_client.py list --project my-project \
  --created-on ">=2025-01-01"

# キーワードで件名検索
python {skill_home}/redmine-use/scripts/redmine_client.py list --project my-project --subject "ログイン"

# 追加フィルタ（任意の Redmine フィルタパラメータを KEY=VALUE 形式で指定）
python {skill_home}/redmine-use/scripts/redmine_client.py list --project my-project \
  --filter author_id=5 due_date=><2025-01-01|2025-03-31
```

出力例:
```
#1234  [新規] [高]  ログイン画面でエラーが発生する  (担当: 山田太郎, 更新: 2025-01-15)
#1235  [進行中] [通常]  パスワードリセット機能の実装  (担当: 鈴木花子, 更新: 2025-01-14)
```

### チケットの読み取り

```bash
python {skill_home}/redmine-use/scripts/redmine_client.py show --id 1234
```

出力例:
```
=== チケット #1234 ===
件名     : ログイン画面でエラーが発生する
プロジェクト: my-project
トラッカー : バグ
ステータス : 新規
優先度   : 高
担当者   : 山田太郎
作成日   : 2025-01-10 09:00
更新日   : 2025-01-15 14:30
説明     :
  ログイン画面で...

ジャーナル (3件):
  [2025-01-11 10:00] 鈴木花子: ステータスを「進行中」に変更
  [2025-01-12 15:00] 田中次郎: 調査結果を追加
    > 原因が判明しました。...
```

### チケットの更新

```bash
# ステータス変更
python {skill_home}/redmine-use/scripts/redmine_client.py update --id 1234 --status-id 2

# 担当者変更
python {skill_home}/redmine-use/scripts/redmine_client.py update --id 1234 --assigned-to-id 5

# 優先度変更
python {skill_home}/redmine-use/scripts/redmine_client.py update --id 1234 --priority-id 3

# 件名と説明の変更
python {skill_home}/redmine-use/scripts/redmine_client.py update --id 1234 \
  --subject "新しい件名" --description "更新した説明"

# 進捗率変更
python {skill_home}/redmine-use/scripts/redmine_client.py update --id 1234 --done-ratio 50

# 期日設定
python {skill_home}/redmine-use/scripts/redmine_client.py update --id 1234 --due-date 2025-03-31

# 複数フィールドをまとめて更新
python {skill_home}/redmine-use/scripts/redmine_client.py update --id 1234 \
  --status-id 2 --assigned-to-id 5 --done-ratio 30
```

### コメントの投稿

```bash
# テキストでコメント
python {skill_home}/redmine-use/scripts/redmine_client.py comment --id 1234 \
  --text "調査しました。原因は〇〇です。"

# ファイルからコメント本文を読み込む
python {skill_home}/redmine-use/scripts/redmine_client.py comment --id 1234 \
  --file comment.txt
```

---

## フィルタ条件の詳細

`list` コマンドの `--filter` オプションで Redmine がサポートするすべてのフィルタを指定できる。

| パラメータ | 説明 | 値の例 |
|-----------|------|-------|
| `status_id` | ステータス | `open`, `closed`, `*`, ID |
| `assigned_to_id` | 担当者 | `me`, ユーザーID |
| `tracker_id` | トラッカー | ID |
| `priority_id` | 優先度 | ID |
| `author_id` | 作成者 | `me`, ユーザーID |
| `subject` | 件名（部分一致） | 文字列 |
| `created_on` | 作成日時 | `>=2025-01-01`, `><2025-01-01|2025-03-31` |
| `updated_on` | 更新日時 | `>=2025-01-01` |
| `due_date` | 期日 | `<=2025-03-31` |
| `sort` | ソート順 | `created_on:desc` |

---

## エラー対処

| エラー | 原因 | 対処 |
|--------|------|------|
| `401 Unauthorized` | APIキーが不正 | Redmine の個人設定でAPIキーを確認 |
| `403 Forbidden` | 権限不足 | プロジェクトへのアクセス権を確認 |
| `404 Not Found` | チケット・プロジェクトが存在しない | IDを確認 |
| `422 Unprocessable Entity` | 入力値が不正 | ステータスIDなどの値を確認 |
| `Connection refused` | URLが不正・サーバー停止 | URLと Redmine の起動状態を確認 |

---

## 使用例: チケットを確認して更新する

```bash
# 1. 担当中のチケットを確認
python {skill_home}/redmine-use/scripts/redmine_client.py list \
  --project my-project --assigned-to me --status open

# 2. チケットの詳細を確認
python {skill_home}/redmine-use/scripts/redmine_client.py show --id 1234

# 3. ステータスを「完了」に変更してコメントを投稿
python {skill_home}/redmine-use/scripts/redmine_client.py update --id 1234 \
  --status-id 5 --done-ratio 100

python {skill_home}/redmine-use/scripts/redmine_client.py comment --id 1234 \
  --text "実装完了しました。レビューをお願いします。"
```

詳細な API リファレンスは [references/api-reference.md](references/api-reference.md) 参照。
