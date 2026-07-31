---
name: jenkins-use
description: Jenkins REST API でビルド操作・状態監視・ログ取得を行う。「Jenkins でビルドして」「ビルド状態を確認して」「Jenkins を設定して」「接続情報を設定して」「Jenkins の設定をして」などで発動する。Python スクリプト経由でリトライ・タイムアウト付きで安定実行。
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

## 「設定して」リクエスト時の対応

ユーザーから「Jenkins を設定して」「接続情報を設定して」などのリクエストを受けたら以下を実行する:

1. **必要情報の確認**: URL・ユーザー名・トークン・ラベルが指定されているか確認する。ない場合はユーザーに確認するか対話プロンプトを使う。
2. **configure の実行**: 必要情報が提示されている場合はオプション付きで、なければ対話形式で実行する。

```bash
# URL ・ユーザー名・トークンが分かっている場合
python {skill_home}/jenkins-use/scripts/jenkins_client.py --label default configure \
  --url https://jenkins.example.com --user your-username --token YOUR_API_TOKEN

# 不明な場合は対話プロンプト
python {skill_home}/jenkins-use/scripts/jenkins_client.py --label default configure
```

3. **接続確認**: configure 実行後に `info` で接続を確認する。

```bash
python {skill_home}/jenkins-use/scripts/jenkins_client.py --label default info
```

- Python 3.8+（標準ライブラリのみ、追加インストール不要）
- Jenkins URL、ユーザー名、API トークン（Jenkins 管理画面 → ユーザー → API Token で生成）

## 接続情報の設定

「Jenkins を設定して」「接続情報を設定して」などのリクエストを受けたら、`configure` コマンドを実行して `connections.yaml` に保存する。

### configure コマンドで設定（推奨）

```bash
# 対話形式（プロンプトで入力）
python {skill_home}/jenkins-use/scripts/jenkins_client.py configure

# オプションで直接指定
python {skill_home}/jenkins-use/scripts/jenkins_client.py configure \
  --url https://jenkins.example.com --user your-username --token YOUR_API_TOKEN

# ラベルを指定して複数環境を管理
python {skill_home}/jenkins-use/scripts/jenkins_client.py --label staging configure \
  --url https://staging.jenkins.example.com --user your-username --token YOUR_TOKEN
```

設定は `{agent_dir}/connections.yaml`（例: `.github/connections.yaml`）に保存される。
APIトークンを直接記述する場合は `.gitignore` に追加すること。

### connections.yaml の直接編集

```yaml
# .github/connections.yaml
jenkins:
  - label: default
    url: https://jenkins.example.com
    user: ${JENKINS_USER}    # 環境変数を参照
    token: ${JENKINS_TOKEN}

  - label: staging
    url: https://staging.jenkins.example.com
    user: your-username
    token: your_staging_token
```

テンプレートは `{agent_dir}/connections.yaml.example`（例: `.github/connections.yaml.example`）を参照。

接続情報の解決順序（上位優先）:

1. `--url` / `--user` / `--token` CLI オプション
2. **`connections.yaml`**（ワークスペース > グローバル）`--label` で接続先を切り替え可能
3. 環境変数 `JENKINS_URL` / `JENKINS_USER` / `JENKINS_TOKEN`
4. ワークスペース設定ファイル `.jenkins.json`（後方互換）

### 環境変数で設定

```bash
export JENKINS_URL=https://jenkins.example.com
export JENKINS_USER=your-username
export JENKINS_TOKEN=your_api_token
```

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
