---
name: ai-cli-caller
description: Claude・GitHub Copilot・Codex・Amazon Q・Kiro などの AI CLI ツールを呼び出すスキル。「Claude に聞いて」「Copilot に質問して」「Codex で生成して」「Amazon Q に確認して」「Kiro を使って」「別の AI に聞いて」「他の AI ツールに投げて」「AI CLI を実行して」などのリクエストで発動する。Windows（PowerShell）・macOS・Linux に対応。GitHub Copilot および Claude Code の両エージェントで動作する。
metadata:
  version: 1.0.0
  tier: experimental
  category: integration
  tags:
    - ai-cli
    - claude
    - copilot
    - codex
    - amazon-q
    - kiro
    - windows
    - powershell
    - cross-platform
---

# AI CLI Caller

Claude・GitHub Copilot CLI・OpenAI Codex CLI・Amazon Q Developer CLI・Kiro などの AI CLI ツールを、エージェント内から呼び出す。

## 対応ツール一覧

| ツール | コマンド | 提供元 | Windows対応 |
|--------|---------|--------|------------|
| **Claude Code** | `claude` | Anthropic | ✅ |
| **GitHub Copilot CLI** | `gh copilot` | GitHub / Microsoft | ✅ |
| **OpenAI Codex CLI** | `codex` | OpenAI | ✅ |
| **Amazon Q Developer CLI** | `q` | AWS | ✅ |
| **Kiro** | `kiro` | AWS | ✅ WSL2経由 (プレビュー) |

インストール手順は [references/windows-setup.md](references/windows-setup.md) を参照。

---

## ワークフロー

### Step 1: ツールの選択

ユーザーが特定のツールを指定していない場合、タスクに最適なツールを提案する:

| タスク | 推奨ツール | 理由 |
|--------|-----------|------|
| コードレビュー・説明 | `claude` または `gh copilot` | 長いコンテキスト、詳細な説明が得意 |
| シェルコマンド提案 | `gh copilot suggest` | コマンドライン特化、安全性確認あり |
| コード生成・補完 | `codex` | OpenAI モデルによる生成 |
| AWS リソース・クラウド | `q` | AWS 固有の知識が豊富 |
| エージェント的タスク実行 | `kiro` | ファイル操作・複数ステップ実行 |

### Step 2: ツールの存在確認

呼び出す前にツールがインストールされているか確認する。

**bash / zsh（macOS・Linux）:**
```bash
# Claude Code
command -v claude && claude --version

# GitHub Copilot CLI
gh copilot --version

# Codex CLI
command -v codex && codex --version

# Amazon Q CLI
q --version

# Kiro
kiro --version
```

**PowerShell（Windows）:**
```powershell
# Claude Code
Get-Command claude -ErrorAction SilentlyContinue
claude --version

# GitHub Copilot CLI
gh copilot --version

# Codex CLI
Get-Command codex -ErrorAction SilentlyContinue

# Amazon Q CLI
q --version

# Kiro（WSL2 経由）
wsl kiro --version
```

ツールが見つからない場合は [references/windows-setup.md](references/windows-setup.md) のインストール手順を案内する。

### Step 3: ツールを呼び出す

---

## ツール別呼び出し方法

### Claude Code（`claude`）

非インタラクティブモード（`-p` / `--print`）でエージェントから呼び出す。

```bash
# 質問する
claude -p "このコードの問題点を教えて: $(cat src/main.py)"

# ファイルを渡してレビュー
claude -p "レビューして" --file src/main.py

# 特定モデルを指定
claude -p "設計を提案して" --model claude-opus-4-5

# 出力をファイルに保存
claude -p "テストを書いて" --file src/app.py > tests/test_app.py
```

**PowerShell（Windows）:**
```powershell
# ファイルの内容を渡す
$code = Get-Content src\main.py -Raw
claude -p "このコードをレビューして:`n$code"

# パイプで渡す
Get-Content src\main.py | claude -p "レビューして"
```

**主要オプション:**

| オプション | 説明 |
|-----------|------|
| `-p <prompt>` | 非インタラクティブで質問（必須） |
| `--file <path>` | ファイルをコンテキストに追加 |
| `--model <id>` | モデル指定（例: `claude-opus-4-6`） |
| `--no-markdown` | Markdown なしでテキスト出力 |
| `--output-format json` | JSON 形式で出力 |

---

### GitHub Copilot CLI（`gh copilot`）

```bash
# シェルコマンドを提案
gh copilot suggest "Dockerコンテナの一覧を表示して停止中のものを削除する"

# コマンドを説明
gh copilot explain "git rebase -i HEAD~3"

# Git 操作を提案
gh copilot suggest -t git "直近3コミットをまとめる"

# gh コマンドを提案
gh copilot suggest -t gh "PR の一覧を表示してドラフトだけ絞り込む"
```

**PowerShell（Windows）:**
```powershell
# シェルコマンドを提案（PowerShell 向け）
gh copilot suggest "ファイルの拡張子ごとに件数を集計する" --shell powershell

# コマンドの説明
gh copilot explain "Get-ChildItem -Recurse | Where-Object { `$_.Length -gt 1MB }"
```

**`suggest` のターゲット種別（`-t`）:**

| 値 | 対象 |
|----|------|
| `shell` | bash / zsh / PowerShell（デフォルト） |
| `git` | Git コマンド |
| `gh` | GitHub CLI コマンド |

---

### OpenAI Codex CLI（`codex`）

```bash
# コード生成
codex "Python で CSV を読み込んで集計するスクリプトを書いて"

# 既存コードの改善
codex "このコードをリファクタリングして" --file src/utils.py

# テスト生成
codex "このモジュールのユニットテストを書いて" --file src/service.py

# 特定モデルを指定
codex -m gpt-4o "型安全な TypeScript のクラスを設計して"

# 承認なしで自動実行（注意: ファイルが変更される）
codex --approval-mode full-auto "依存パッケージを最新バージョンに更新して"
```

**PowerShell（Windows）:**
```powershell
# ファイルを渡してコード生成
codex "このスクリプトを最適化して" --file src\process.py

# 環境変数で API キーを設定
$env:OPENAI_API_KEY = "sk-..."
codex "REST API クライアントを TypeScript で書いて"
```

**主要オプション:**

| オプション | 説明 |
|-----------|------|
| `-m <model>` | モデル指定（デフォルト: `o4-mini`） |
| `--file <path>` | ファイルをコンテキストに追加 |
| `--approval-mode` | `suggest`（提案のみ）/ `auto-edit`（編集自動）/ `full-auto`（全自動） |
| `--quiet` | 確認なしで実行 |

---

### Amazon Q Developer CLI（`q`）

```bash
# 質問する（インラインチャット）
q chat "S3 バケットの公開アクセスをブロックする AWS CLI コマンドを教えて"

# コマンドを提案
q translate "EC2 インスタンス一覧を Name タグでソートして表示"

# コードをスキャン（セキュリティ）
q scan --language python src/

# チャットを終了せずにコンテキストを保持
q chat --context src/lambda_function.py "この Lambda 関数の問題点は？"
```

**PowerShell（Windows）:**
```powershell
# AWS CLI コマンドを PowerShell 向けに提案
q translate "S3 バケット一覧を表示してサイズ順に並べる" --shell powershell

# プロジェクトコンテキストを渡す
q chat --context .\src\handler.py "このコードのバグを見つけて"
```

**主要サブコマンド:**

| サブコマンド | 説明 |
|------------|------|
| `chat` | インタラクティブチャット（`-p` で非インタラクティブ） |
| `translate` | 自然言語 → シェルコマンド変換 |
| `scan` | コードの脆弱性スキャン |
| `whoami` | 認証状態の確認 |

---

### Kiro（`kiro`）

> **注意**: Kiro は AWS が開発中の AI ネイティブ IDE。CLI 機能はプレビュー段階のため、インターフェースが変更される可能性がある。
> **Windows**: Kiro CLI はネイティブ未対応のため WSL2 経由で実行する。

**macOS / Linux:**
```bash
# エージェントタスクを実行
kiro agent "src/ ディレクトリのすべての Python ファイルに型ヒントを追加して"

# 仕様（スペック）を生成
kiro spec "ユーザー認証機能の設計書を作って"

# コードを自動生成
kiro generate --spec docs/auth-spec.md

# フック設定を確認
kiro hooks list
```

**Windows（WSL2 経由）:**
```powershell
# wsl コマンド経由で直接実行
wsl kiro agent "src/ ディレクトリのすべての Python ファイルに型ヒントを追加して"
wsl kiro spec "ユーザー認証機能の設計書を作って"

# PowerShell プロファイルにエイリアスを登録すると kiro コマンドとして使える
# 登録方法: Add-Content $PROFILE "`nfunction kiro { wsl kiro @args }"
kiro agent "JSDoc コメントを追加して"
```

---

## 複数ツールを組み合わせる

異なる AI ツールの出力を組み合わせることで、より高品質な結果を得られる。

```bash
# Step 1: Copilot でコマンド候補を出す
gh copilot suggest "データベースのバックアップを取る"

# Step 2: Claude で詳細な説明を得る
claude -p "このコマンドのリスクと注意点を教えて: pg_dump -U admin mydb"

# Step 3: Amazon Q で AWS 向けに最適化
q chat "RDS スナップショットに変換するには？"
```

**パイプで連携（bash）:**
```bash
# Copilot の提案を Claude でレビュー
gh copilot suggest "本番DBの重複データを削除する" 2>&1 | \
  claude -p "このコマンドの安全性を確認して、問題があれば代替案を示して"
```

**PowerShell での連携:**
```powershell
# Copilot の提案を取得して Claude でレビュー
$suggestion = gh copilot suggest "古いログファイルを削除する"
claude -p "このコマンドを本番環境で実行する前に確認すべき点は？:`n$suggestion"
```

---

## エラー対処

| エラー | 原因 | 対処 |
|--------|------|------|
| `command not found: claude` | 未インストール | [セットアップガイド](references/windows-setup.md) 参照 |
| `command not found: codex` | 未インストール | `npm install -g @openai/codex` |
| `gh copilot: unknown command` | 拡張未インストール | `gh extension install github/gh-copilot` |
| `q: No credentials` | AWS 認証未設定 | `q login` を実行 |
| `OPENAI_API_KEY not set` | 環境変数未設定 | `.env` または環境変数に設定 |
| PowerShell 実行ポリシーエラー | ポリシー制限 | `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |

---

## セキュリティ上の注意

- `--approval-mode full-auto` / `--quiet` はファイルを自動変更するため、Git で変更管理された環境でのみ使用する
- API キーは環境変数または `.env` ファイルで管理し、コードにハードコードしない
- 機密情報（パスワード・トークン等）を含むコードをプロンプトに貼り付けない
- `q scan` の結果は参考情報として扱い、最終的なセキュリティ判断は人間が行う
