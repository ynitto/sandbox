---
name: agent-cli-proxy
description: Claude・GitHub Copilot・Codex・Kiro などの AI CLI ツールを呼び出すスキル。「Claude に聞いて」「Copilot に質問して」「Codex で生成して」「Kiro を使って」「別の AI に聞いて」「他の AI ツールに投げて」「AI CLI を実行して」などのリクエストで発動する。Windows（PowerShell）・macOS・Linux に対応。GitHub Copilot および Claude Code の両エージェントで動作する。
metadata:
  version: 1.0.0
  tier: experimental
  category: integration
  tags:
    - ai-cli
    - claude
    - copilot
    - codex
    - kiro
    - windows
    - powershell
    - cross-platform
---

# AI CLI Caller

Claude・GitHub Copilot CLI・OpenAI Codex CLI・Kiro などの AI CLI ツールを、エージェント内から呼び出す。

## 対応ツール一覧

| ツール | コマンド | 提供元 | Windows対応 |
|--------|---------|--------|------------|
| **Claude Code** | `claude` | Anthropic | ✅ |
| **GitHub Copilot CLI** | `gh copilot` | GitHub / Microsoft | ✅ |
| **OpenAI Codex CLI** | `codex` | OpenAI | ✅ |
| **Kiro** | `kiro-cli` | AWS | ✅ WSL2経由 (プレビュー) |

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
| エージェント的タスク実行 | `kiro-cli` | ファイル操作・複数ステップ実行 |

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

# Kiro
kiro-cli --version
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

# Kiro（WSL2 経由）
wsl kiro-cli --version
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

### Kiro（`kiro-cli`）

> **注意**: Kiro は AWS が開発中の AI ネイティブ IDE。CLI 機能はプレビュー段階のため、インターフェースが変更される可能性がある。
> **Windows**: Kiro CLI はネイティブ未対応のため WSL2 経由で実行する。

**macOS / Linux:**
```bash
# チャット（インタラクティブ）
kiro-cli chat

# タスクを指定して起動
kiro-cli chat "src/ ディレクトリのすべての Python ファイルに型ヒントを追加して"

# 非インタラクティブ（スクリプト・エージェントから呼び出す場合）
kiro-cli chat --no-interactive "タスクの説明"

# ツール承認を自動化（ファイル操作なども自動実行）
kiro-cli chat --no-interactive --trust-all-tools "JSDoc コメントを追加して"

# エージェント設定の管理（タスク実行ではなく設定操作）
kiro-cli agent list
kiro-cli agent create my-agent
kiro-cli agent set-default my-agent
```

**Windows（WSL2 経由）:**
```powershell
# wsl コマンド経由で直接実行
wsl kiro-cli chat --no-interactive "src/ ディレクトリのすべての Python ファイルに型ヒントを追加して"

# PowerShell プロファイルにエイリアスを登録すると kiro-cli コマンドとして使える
# 登録方法: Add-Content $PROFILE "`nfunction kiro-cli { wsl kiro-cli @args }"
kiro-cli chat --no-interactive "JSDoc コメントを追加して"
```

**出力の取得（スクリプトから利用する場合）:**
```bash
# 変数に取得
result=$(kiro-cli chat --no-interactive "設計書を作って" 2>&1)
echo "$result"

# ファイルに保存
kiro-cli chat --no-interactive --trust-all-tools \
  "src/ の Python ファイルに型ヒントを追加して" > kiro-output.txt

# 別ツールにパイプ
kiro-cli chat --no-interactive "設計書を作って" | \
  claude -p "この設計書の問題点を指摘して"
```

---

## 複数ツールを組み合わせる

異なる AI ツールの出力を組み合わせることで、より高品質な結果を得られる。

```bash
# Step 1: Copilot でコマンド候補を出す
gh copilot suggest "データベースのバックアップを取る"

# Step 2: Claude で詳細な説明を得る
claude -p "このコマンドのリスクと注意点を教えて: pg_dump -U admin mydb"
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
| `kiro-cli: unknown command "agent"` | agent はタスク実行ではなく設定管理用 | `kiro-cli chat --no-interactive "..."` を使う |
| `kiro-cli: unknown command "auth"` | auth サブコマンドは存在しない | `kiro-cli login` を使う |
| `OPENAI_API_KEY not set` | 環境変数未設定 | `.env` または環境変数に設定 |
| PowerShell 実行ポリシーエラー | ポリシー制限 | `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |

---

## セキュリティ上の注意

- `--approval-mode full-auto` / `--quiet` はファイルを自動変更するため、Git で変更管理された環境でのみ使用する
- API キーは環境変数または `.env` ファイルで管理し、コードにハードコードしない
- 機密情報（パスワード・トークン等）を含むコードをプロンプトに貼り付けない
