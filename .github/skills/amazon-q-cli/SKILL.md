---
name: amazon-q-cli
description: Amazon Q Developer CLI（`q`コマンド）を使ってタスクを実行するスキル。自然文のタスクとスキル手順書をAmazon Q CLIに送信し、応答を受け取る。「Amazon Qに聞いて」「Amazon QでXXXして」「qコマンドでXXX」「Amazon Q CLIを使って」「Amazon Q CLIに任せて」などのリクエストで発動する。Amazon Q CLIが未インストールや非対応の場合も適切にフォールバックする。Windows / macOS / Linux で動作する。
metadata:
  version: 1.1.0
  tier: experimental
  category: integration
  tags:
    - amazon-q
    - cli
    - ai-agent
    - delegation
---

# Amazon Q CLI

Amazon Q Developer CLI（`q`コマンド）にタスクと手順書を送り、応答を受け取るスキル。Windows / macOS / Linux で動作する。

## 前提条件

- Amazon Q Developer CLI がインストール済み（`q` コマンドが使える）
- AWS Builder ID または IAM Identity Center でサインイン済み（`q login` 実行済み）
- Python 3.9 以上（スクリプト実行に使用）

インストール方法:

| OS | コマンド |
|----|---------|
| Windows | `winget install Amazon.AmazonQ` |
| macOS | `brew install amazon-q` |
| Linux | [公式ドキュメント](https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line-getting-started-installing.html) 参照 |

## 基本ワークフロー

### Step 1: CLIの可用性を確認する

```
q --version
```

- コマンドが見つからない → [フォールバック手順](#フォールバック) を参照
- バージョンが表示される → Step 2 へ

### Step 2: プロンプトを構築する

タスク説明とスキル手順書を1つのプロンプトに組み合わせる:

```
# タスク
<ユーザーから受け取った自然文のタスク説明>

# 手順・コンテキスト
<関連するスキルの手順書や背景情報>
```

手順書が長い場合はファイルに書き出して渡す（後述）。

### Step 3: Amazon Q CLIを呼び出す

[scripts/q_chat.py](scripts/q_chat.py) を使用する（Windows / macOS / Linux 共通）:

```
# 基本的な呼び出し
python .github/skills/amazon-q-cli/scripts/q_chat.py "プロンプトテキスト"

# プロンプトをファイルから渡す（長い場合）
python .github/skills/amazon-q-cli/scripts/q_chat.py --file prompt.txt

# エージェントを指定する（Amazon Q側でエージェントが設定済みの場合）
python .github/skills/amazon-q-cli/scripts/q_chat.py --agent my-agent "プロンプトテキスト"
```

スクリプトは以下を行う:
1. `q` コマンドの存在確認
2. `q chat --no-interactive --trust-all-tools` での呼び出し試行
3. `--agent` オプションがエラーを起こした場合は自動でリトライ
4. 応答の標準出力への出力

### Step 4: 応答を取り込む

スクリプトの標準出力を受け取り、タスクの文脈に沿って解釈・整形して返す。

---

## フォールバック

Amazon Q CLIが使えない場合の対処:

| 状況 | 対処 |
|------|------|
| `q` コマンドが見つからない | インストールを案内（上記インストール方法参照） |
| 未ログイン (auth error) | `q login` を実行してサインインを案内 |
| `--no-interactive` で停止する | インタラクティブモードで手動実行するよう案内 |
| CLIが応答しない・エラー | タスクを直接自分（Copilot/Claude）で処理する |

CLIが利用不可でも、タスク自体はこちらで引き続き対応する。

---

## 使用例

### 例1: AWS操作の委譲

```
python .github/skills/amazon-q-cli/scripts/q_chat.py "S3バケットの一覧をJSON形式で出力してください"
```

### 例2: コード生成の委譲

```
python .github/skills/amazon-q-cli/scripts/q_chat.py "Pythonで非同期HTTPクライアントを実装してください。aiohttpを使用し、リトライ機能を含めてください。"
```

### 例3: 手順書ありの委譲

プロンプトファイル `prompt.txt` を作成:
```
# タスク
DynamoDBテーブルの設計レビューをしてください。

# テーブル定義
PK: userId (String)
SK: timestamp (String)
GSI: statusIndex (status, createdAt)

# 手順・観点
- アクセスパターンとの整合性を確認
- ホットパーティション問題のリスクを評価
- GSI設計の妥当性を検証
```

実行:
```
python .github/skills/amazon-q-cli/scripts/q_chat.py --file prompt.txt
```

---

## Amazon Q CLIのAgent Skillsについて

Amazon Q CLIは `--agent <name>` オプションでエージェントを指定できるが、カスタムエージェント登録はAmazon Q Business等の別サービスが必要な場合がある。

- エージェントが設定されていない環境では `--agent` オプションがエラーになることがある
- `q_chat.py` はその場合に自動でエージェントなしにフォールバックする

## 注意事項

- `--no-interactive` モードは環境によって動作が不安定な場合がある（既知の問題）
- 長いプロンプト（数千文字超）はファイル経由で渡すほうが安定する
- 機密情報（APIキー、パスワード等）をプロンプトに含めない
