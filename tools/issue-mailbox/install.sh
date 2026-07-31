#!/usr/bin/env bash
# issue-mailbox インストールスクリプト
# ~/.local/bin/issue-mailbox にシンボリックリンクを作成します

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$HOME/.local/bin/issue-mailbox"

# ── 依存チェック ──────────────────────────────────────────────────────────

echo "[install] 依存ライブラリをチェックしています..."

if ! command -v tmux &>/dev/null; then
    echo "[install] ERROR: tmux が見つかりません"
    echo "  sudo apt install tmux"
    exit 1
fi

if ! python3 -c "import requests" &>/dev/null 2>&1; then
    echo "[install] requests をインストールします..."
    pip install requests
fi

if ! python3 -c "import yaml" &>/dev/null 2>&1; then
    echo "[install] PyYAML をインストールします..."
    pip install pyyaml
fi

# ── インストール先の準備 ──────────────────────────────────────────────────

mkdir -p "$HOME/.local/bin"

# ── シンボリックリンクの作成 ──────────────────────────────────────────────

if [ -L "$TARGET" ]; then
    echo "[install] 既存のシンボリックリンクを更新します: $TARGET"
    rm "$TARGET"
fi

ln -s "$SCRIPT_DIR/issue-mailbox.py" "$TARGET"
chmod +x "$SCRIPT_DIR/issue-mailbox.py"

echo "[install] インストール完了: $TARGET"

# ── PATH チェック ──────────────────────────────────────────────────────────

if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
    echo ""
    echo "[install] WARN: ~/.local/bin が PATH に含まれていません。"
    echo "  以下を ~/.bashrc または ~/.zshrc に追加してください:"
    echo '    export PATH="$HOME/.local/bin:$PATH"'
fi

# ── 設定ファイルのセットアップ案内 ────────────────────────────────────────

if [ ! -f "$HOME/issue-mailbox.yaml" ]; then
    echo ""
    echo "次のステップ: 設定ファイルを作成してください"
    echo "  cp $SCRIPT_DIR/issue-mailbox.yaml.example ~/issue-mailbox.yaml"
    echo "  # gitlab_url / project_id / private_token を編集してください"
fi

echo ""
echo "使い方:"
echo "  # ポーリング起動（別 tmux ペインで）"
echo "  issue-mailbox"
echo ""
echo "  # 通知ビューアを起動（さらに別の tmux ペインで）"
echo "  issue-mailbox view"
echo ""
echo "  # 状態確認"
echo "  issue-mailbox status"
