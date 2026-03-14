# AI CLI ツール セットアップガイド（Windows / macOS / Linux）

各 AI CLI ツールのインストール・初期設定手順。

---

## Claude Code（`claude`）

### インストール

**全プラットフォーム共通（Node.js 18+ が必要）:**
```bash
npm install -g @anthropic-ai/claude-code
```

**Windows（PowerShell）:**
```powershell
# Node.js がない場合は winget でインストール
winget install OpenJS.NodeJS.LTS

# Claude Code をインストール
npm install -g @anthropic-ai/claude-code

# 確認
claude --version
```

**macOS（Homebrew）:**
```bash
brew install node
npm install -g @anthropic-ai/claude-code
```

### 認証設定

```bash
# ブラウザで認証（推奨）
claude auth login

# API キーで認証
export ANTHROPIC_API_KEY="sk-ant-..."   # bash/zsh
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # PowerShell
```

### Windows での PATH 設定

npm グローバルパスが通っていない場合:
```powershell
# npm のグローバルパスを確認
npm config get prefix

# PATH に追加（例: C:\Users\<User>\AppData\Roaming\npm）
$env:PATH += ";C:\Users\$env:USERNAME\AppData\Roaming\npm"

# 永続化（ユーザープロファイルに追加）
[Environment]::SetEnvironmentVariable(
  "PATH",
  $env:PATH + ";C:\Users\$env:USERNAME\AppData\Roaming\npm",
  "User"
)
```

---

## GitHub Copilot CLI（`gh copilot`）

### 前提条件

- GitHub CLI（`gh`）のインストールが必要
- GitHub Copilot サブスクリプションが必要

### インストール

**Windows（winget）:**
```powershell
winget install GitHub.cli
gh auth login
gh extension install github/gh-copilot
```

**Windows（Scoop）:**
```powershell
scoop install gh
gh auth login
gh extension install github/gh-copilot
```

**macOS（Homebrew）:**
```bash
brew install gh
gh auth login
gh extension install github/gh-copilot
```

**Linux（apt）:**
```bash
sudo apt install gh
gh auth login
gh extension install github/gh-copilot
```

### 確認

```bash
gh copilot --version
gh copilot suggest "hello world を表示する"
```

### Windows PowerShell エイリアス設定（任意）

```powershell
# PowerShell プロファイルに追加
Add-Content $PROFILE "`nfunction ghcs { gh copilot suggest @args }"
Add-Content $PROFILE "`nfunction ghce { gh copilot explain @args }"

# プロファイルをリロード
. $PROFILE
```

---

## OpenAI Codex CLI（`codex`）

### インストール

**全プラットフォーム共通（Node.js 22+ が必要）:**
```bash
npm install -g @openai/codex
```

**Windows（PowerShell）:**
```powershell
winget install OpenJS.NodeJS.LTS
npm install -g @openai/codex
codex --version
```

**macOS:**
```bash
brew install node
npm install -g @openai/codex
```

### API キー設定

```bash
# bash/zsh
export OPENAI_API_KEY="sk-..."
echo 'export OPENAI_API_KEY="sk-..."' >> ~/.zshrc

# PowerShell（セッション）
$env:OPENAI_API_KEY = "sk-..."

# PowerShell（永続化）
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "sk-...", "User")
```

### Windows Sandbox モードの設定

Codex はセキュリティのためサンドボックス内で実行される。Windows では Docker Desktop または WSL2 が必要:

```powershell
# WSL2 が有効か確認
wsl --status

# Docker Desktop のインストール（未インストールの場合）
winget install Docker.DockerDesktop
```

---

## Amazon Q Developer CLI（`q`）

### インストール

**Windows（インストーラー）:**
1. [Amazon Q Developer CLI リリースページ](https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line-installing.html) からインストーラーをダウンロード
2. `AmazonQ-Setup.exe` を実行

**Windows（winget）:**
```powershell
winget install Amazon.AmazonQ
```

**macOS（Homebrew）:**
```bash
brew install amazon-q
```

**Linux:**
```bash
curl -fsSL https://desktop-release.q.us-east-1.amazonaws.com/latest/linux/q.tar.gz | tar -xz
sudo mv q /usr/local/bin/
```

### 認証設定

**Builder ID（無料）:**
```bash
q login
# ブラウザが開いて AWS Builder ID でログイン
```

**IAM Identity Center（組織アカウント）:**
```bash
q login --sso-start-url https://your-org.awsapps.com/start
```

### 確認

```bash
q whoami
q chat "EC2 インスタンスの一覧を表示する AWS CLI コマンドは？"
```

### Windows での注意事項

- PowerShell 7（pwsh）を推奨。Windows PowerShell 5.x でも動作するが一部機能が制限される
- `q chat` の日本語入力は IME 経由で正常に動作する
- ターミナルとして Windows Terminal を使用すると表示が安定する

---

## Kiro（`kiro`）

> Kiro は AWS が開発中の AI ネイティブ IDE。2025年にプレビューリリース。CLI ツールとしても利用可能。

> **Windows での注意**: Kiro CLI は Windows ネイティブ未対応のため、**WSL2（Windows Subsystem for Linux）経由で実行する**。

### 前提条件（Windows）

WSL2 と Ubuntu が必要:

```powershell
# WSL2 を有効化してインストール（管理者 PowerShell で実行）
wsl --install

# 既存 WSL の場合はバージョン確認
wsl --status
# → WSL バージョン: 2 であること

# Ubuntu を起動
wsl
```

### インストール

**Windows（WSL2 内の Ubuntu で実行）:**
```bash
# WSL2 シェル内で実行
curl -fsSL https://kiro.dev/install.sh | bash

# または手動インストール
wget https://kiro.dev/releases/latest/kiro-linux-x64.tar.gz
tar -xzf kiro-linux-x64.tar.gz
sudo mv kiro /usr/local/bin/

# PATH に追加
echo 'export PATH="$PATH:/usr/local/bin"' >> ~/.bashrc
source ~/.bashrc
```

**macOS:**
```bash
# dmg からインストール後
# または Homebrew（利用可能になり次第）
brew install --cask kiro
```

**Linux:**
```bash
curl -fsSL https://kiro.dev/install.sh | bash
```

### CLI の有効化

**macOS / Linux:**
```bash
export PATH="$PATH:/Applications/Kiro.app/Contents/Resources/bin"
echo 'export PATH="$PATH:/Applications/Kiro.app/Contents/Resources/bin"' >> ~/.zshrc
```

**Windows（WSL2）:**
```bash
# WSL2 内で有効化（追加 PATH 設定は通常不要）
echo 'export PATH="$PATH:$HOME/.local/bin"' >> ~/.bashrc
source ~/.bashrc
```

### PowerShell から WSL 経由で呼び出す

```powershell
# WSL 経由で kiro を実行（PowerShell から直接呼び出せる）
wsl kiro --version

# エージェントタスクを実行
wsl kiro agent "src/ ディレクトリのすべての Python ファイルに型ヒントを追加して"

# Windows のパスを WSL パスに変換して渡す
$wslPath = (wsl wslpath ($PWD.Path -replace '\\', '/'))
wsl kiro agent --cwd $wslPath "JSDoc コメントを追加して"
```

PowerShell プロファイルにエイリアスを登録しておくと便利:

```powershell
# $PROFILE に追加
Add-Content $PROFILE "`nfunction kiro { wsl kiro @args }"

# プロファイルをリロード
. $PROFILE

# 以降は kiro コマンドとして使用可能
kiro --version
kiro agent "型ヒントを追加して"
```

### 認証設定

```bash
# WSL2 シェル内で認証
kiro auth login

# 確認
kiro --version
```

### Windows での注意事項

- ファイルパスは WSL 内のパス（`/mnt/c/...`）で指定する
- Windows 側のプロジェクトを操作する場合は `/mnt/c/Users/<User>/...` 形式を使う
- WSL2 と Windows 間のファイル I/O はパフォーマンスが低下する場合がある。プロジェクトは WSL ファイルシステム（`~/` 以下）に置くと高速
- Windows Terminal を使用すると WSL2 との統合が便利

---

## 環境変数の一括設定（`.env` テンプレート）

プロジェクトルートに `.env` ファイルを作成して管理する:

```bash
# .env（Git 管理外にすること）
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
# AWS 認証は q login コマンドで管理（キーをファイルに書かない）
```

**`.env` の読み込み:**
```bash
# bash/zsh
set -a && source .env && set +a
```

```powershell
# PowerShell
Get-Content .env | ForEach-Object {
    if ($_ -match "^([^#][^=]*)=(.*)$") {
        [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
    }
}
```

> **セキュリティ**: `.env` は必ず `.gitignore` に追加すること。API キーをリポジトリにコミットしない。

---

## バージョン確認スクリプト

インストール済みツールの状態を一括確認する。

**bash / zsh:**
```bash
#!/bin/bash
echo "=== AI CLI ツール インストール状況 ==="
for cmd in claude "gh copilot" codex q kiro; do
  if command -v ${cmd%% *} &>/dev/null; then
    echo "✅ $cmd: $(${cmd} --version 2>/dev/null || echo '(バージョン取得不可)')"
  else
    echo "❌ $cmd: 未インストール"
  fi
done
```

**PowerShell（Windows）:**
```powershell
# ネイティブツールの確認
$tools = @(
    @{ Name = "Claude Code";          Cmd = "claude";    Args = "--version";  UseWsl = $false },
    @{ Name = "GitHub Copilot CLI";   Cmd = "gh";        Args = "copilot --version"; UseWsl = $false },
    @{ Name = "OpenAI Codex CLI";     Cmd = "codex";     Args = "--version";  UseWsl = $false },
    @{ Name = "Amazon Q CLI";         Cmd = "q";         Args = "--version";  UseWsl = $false },
    @{ Name = "Kiro (WSL2)";          Cmd = "wsl";       Args = "kiro --version"; UseWsl = $true }
)

Write-Host "=== AI CLI ツール インストール状況 ===" -ForegroundColor Cyan
foreach ($tool in $tools) {
    if ($tool.UseWsl) {
        # WSL2 経由の確認
        $wslStatus = wsl --status 2>$null
        if ($LASTEXITCODE -eq 0) {
            $ver = wsl kiro --version 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "✅ $($tool.Name): $ver" -ForegroundColor Green
            } else {
                Write-Host "❌ $($tool.Name): WSL2 内に未インストール" -ForegroundColor Red
            }
        } else {
            Write-Host "❌ $($tool.Name): WSL2 が未インストール" -ForegroundColor Red
        }
    } else {
        $found = Get-Command $tool.Cmd -ErrorAction SilentlyContinue
        if ($found) {
            $ver = & $tool.Cmd $tool.Args 2>$null
            Write-Host "✅ $($tool.Name): $ver" -ForegroundColor Green
        } else {
            Write-Host "❌ $($tool.Name): 未インストール" -ForegroundColor Red
        }
    }
}
```
