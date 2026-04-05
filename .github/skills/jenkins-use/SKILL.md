---
name: jenkins-use
description: Jenkins REST API でビルド操作・状態監視・ログ取得を行う。「Jenkins でビルドして」「ビルド状態を確認して」「コンソールログを取得して」「Jenkins ジョブを実行して」「CI の状態を確認して」「Jenkins に接続して」などで発動する。Python スクリプト経由でリトライ・タイムアウト付きで安定実行。
metadata:
  version: 1.0.0
  tier: experimental
  category: integration
  tags:
    - jenkins
    - ci-cd
    - rest-api
    - python
    - build
    - monitoring
---

# Jenkins Use

Python スクリプトから Jenkins REST API を呼び出してビルド操作と状態監視を行う。

## 前提条件

- Python 3.8+（標準ライブラリのみ、追加インストール不要）
- Jenkins URL、ユーザー名、API トークン（Jenkins 管理画面 → ユーザー → API Token で生成）

## 接続情報の設定

接続情報の解決順序（上位優先）:

1. `--url` / `--user` / `--token` オプション
2. 環境変数 `JENKINS_URL` / `JENKINS_USER` / `JENKINS_TOKEN`
3. ワークスペース設定ファイル `.jenkins.json`（カレントディレクトリ）

**推奨: `configure` コマンドで設定ファイルに保存する**

```bash
python scripts/jenkins_client.py configure
# → Jenkins URL: https://jenkins.example.com
# → Username: your-username
# → API Token: ****
# → Saved to .jenkins.json  (パーミッション 600)
```

`.jenkins.json` はトークンを含むため `.gitignore` に追加すること。

## 基本ワークフロー

### Step 1: 接続確認

```bash
python scripts/jenkins_client.py info
```

Jenkins バージョンと接続状態を確認する。

### Step 2: 操作を実行

目的に応じて以下のコマンドを使い分ける。

---

## コマンドリファレンス

### ジョブ一覧の取得

```bash
python scripts/jenkins_client.py list-jobs
```

### ビルドのトリガー

```bash
# パラメータなしでビルド
python scripts/jenkins_client.py build --job my-pipeline

# パラメータ付きでビルド
python scripts/jenkins_client.py build --job my-pipeline \
  --params BRANCH=main ENV=production
```

戻り値: キューに入ったビルド番号（利用可能になるまで待機）

### ビルド一覧の取得

```bash
# 直近10件
python scripts/jenkins_client.py list-builds --job my-pipeline

# 件数を指定
python scripts/jenkins_client.py list-builds --job my-pipeline --limit 20
```

### ビルド状態の確認

```bash
# 最新ビルドの状態
python scripts/jenkins_client.py status --job my-pipeline

# ビルド番号を指定
python scripts/jenkins_client.py status --job my-pipeline --build 42
```

出力例:
```
Build #42
  Status : SUCCESS
  Started: 2025-01-15 10:30:00
  Duration: 3m 42s
  URL    : https://jenkins.example.com/job/my-pipeline/42/
```

### ビルド完了まで待機

```bash
# デフォルト: 30分タイムアウト、10秒ポーリング
python scripts/jenkins_client.py wait --job my-pipeline --build 42

# タイムアウトとポーリング間隔を指定
python scripts/jenkins_client.py wait --job my-pipeline --build 42 \
  --timeout 3600 --interval 30
```

完了後にビルド結果（SUCCESS / FAILURE / ABORTED）を表示して終了する。

### コンソールログの取得

```bash
# 最新ビルドのログ全体
python scripts/jenkins_client.py log --job my-pipeline

# ビルド番号を指定
python scripts/jenkins_client.py log --job my-pipeline --build 42

# 末尾 N 行のみ表示
python scripts/jenkins_client.py log --job my-pipeline --tail 100

# 実行中ビルドをストリーミング（完了まで追従）
python scripts/jenkins_client.py log --job my-pipeline --follow
```

---

## リトライとタイムアウトの仕様

`jenkins_client.py` は以下の挙動をデフォルトで持つ:

| 設定 | デフォルト | 変更方法 |
|------|-----------|---------|
| HTTP タイムアウト | 30 秒 | `--http-timeout` |
| リトライ回数 | 3 回 | `--retries` |
| リトライ間隔（指数バックオフ） | 2s → 4s → 8s | 固定 |
| ビルド待機タイムアウト | 1800 秒（30分） | `--timeout` |
| ポーリング間隔 | 10 秒 | `--interval` |

リトライ対象: HTTP 5xx・接続エラー・タイムアウト。4xx（認証失敗など）はリトライしない。

---

## エラー対処

| エラー | 原因 | 対処 |
|--------|------|------|
| `401 Unauthorized` | 認証情報が不正 | API トークンを再生成して設定 |
| `403 Forbidden` | 権限不足 | Jenkins ユーザーに必要な権限を付与 |
| `404 Not Found` | ジョブ名が不正 | `list-jobs` で正確な名前を確認 |
| `Connection refused` | Jenkins 未起動・URL が不正 | URL と Jenkins の起動状態を確認 |
| タイムアウト | ビルドが長時間実行中 | `--timeout` を延長するか手動で確認 |
| `CSRF crumb error` | CSRF 保護が有効 | スクリプトが自動で crumb を取得する |

---

## 使用例: ビルドして結果を確認

```bash
# 1. ビルドをトリガー
python scripts/jenkins_client.py build --job deploy-prod \
  --params VERSION=1.2.0

# 2. 完了まで待機（最大60分）
python scripts/jenkins_client.py wait --job deploy-prod \
  --timeout 3600

# 3. 失敗した場合はコンソールログを確認
python scripts/jenkins_client.py log --job deploy-prod --tail 200
```

詳細な API リファレンスは [references/api-reference.md](references/api-reference.md) 参照。
