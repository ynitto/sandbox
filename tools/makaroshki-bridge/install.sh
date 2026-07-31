#!/usr/bin/env bash
# install.sh — makaroshki-bridge インストーラー
# 使い方: bash install.sh [--prefix <dir>]
#
# デフォルトのインストール先: ~/.local/bin/makaroshki-bridge

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()   { error "$*"; exit 1; }

INSTALL_PREFIX="${HOME}/.local/bin"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) INSTALL_PREFIX="$2"; shift 2 ;;
    --help|-h)
      echo "使い方: bash install.sh [--prefix <インストール先ディレクトリ>]"
      echo "  デフォルト: ~/.local/bin"
      exit 0 ;;
    *) die "不明なオプション: $1" ;;
  esac
done

INSTALL_PATH="${INSTALL_PREFIX}/makaroshki-bridge"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "========================================"
echo "  makaroshki-bridge インストーラー"
echo "========================================"
echo ""

# 1. OS チェック
info "実行環境を確認しています..."
OS="$(uname -s)"
case "$OS" in
  Darwin) ok "macOS 環境を検出しました。" ;;
  Linux)
    if grep -qi microsoft /proc/version 2>/dev/null; then ok "WSL 環境を検出しました。"; else ok "Linux 環境を検出しました。"; fi ;;
  *) die "サポートされていない OS です（検出: $OS）。macOS / Linux / WSL が必要です。" ;;
esac

# 2. git チェック（必須）
info "git を確認しています..."
command -v git &>/dev/null || die "git が見つかりません。先にインストールしてください。"
ok "git が見つかりました: $(command -v git) ($(git --version 2>&1 | head -1))"

# 3. python チェック（3.9+）
info "python を確認しています..."
PYTHON_CMD=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PY_MAJOR="$("$cmd" -c 'import sys; print(sys.version_info.major)')"
    PY_MINOR="$("$cmd" -c 'import sys; print(sys.version_info.minor)')"
    if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 9 ]]; then
      PYTHON_CMD="$cmd"; ok "$("$cmd" --version 2>&1) が見つかりました: $(command -v "$cmd")"; break
    fi
  fi
done
[[ -n "$PYTHON_CMD" ]] || die "Python 3.9 以上が見つかりません。"

# 4. PyYAML（任意）
info "PyYAML を確認しています（YAML 設定を使う場合に必要）..."
if "$PYTHON_CMD" -c "import yaml" &>/dev/null 2>&1; then
  ok "pyyaml はインストール済みです。"
else
  warn "pyyaml が見つかりません。YAML 設定を使うなら 'pip install pyyaml'、または JSON 設定を使ってください。"
fi

# 5. tmux（任意・runner: tmux を使う場合のみ）
if command -v tmux &>/dev/null; then
  ok "tmux が見つかりました（runner: tmux を使う場合に利用）。"
else
  warn "tmux が見つかりません。runner: command なら不要です（runner: tmux を使う場合のみ必要）。"
fi

# 6. スクリプトのインストール
info "makaroshki-bridge.py をインストールしています..."
SRC="${SCRIPT_DIR}/makaroshki-bridge.py"
[[ -f "$SRC" ]] || die "makaroshki-bridge.py が見つかりません: $SRC"
mkdir -p "$INSTALL_PREFIX"
cp "$SRC" "$INSTALL_PATH"
chmod +x "$INSTALL_PATH"
if [[ "$OS" == "Darwin" ]]; then
  sed -i '' "1s|.*|#!/usr/bin/env ${PYTHON_CMD}|" "$INSTALL_PATH"
else
  sed -i "1s|.*|#!/usr/bin/env ${PYTHON_CMD}|" "$INSTALL_PATH"
fi
ok "インストールしました: $INSTALL_PATH"

# 7. PATH チェック
if echo "$PATH" | tr ':' '\n' | grep -qF "$INSTALL_PREFIX"; then
  ok "$INSTALL_PREFIX は PATH に含まれています。"
else
  warn "$INSTALL_PREFIX が PATH に含まれていません。"
  echo "  ~/.bashrc または ~/.zshrc に追加してください:"
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo ""
echo "========================================"
ok "インストール完了！"
echo "========================================"
echo ""
echo "  次のステップ:"
echo "    cp ${SCRIPT_DIR}/config.yaml.example ~/makaroshki-bridge.yaml"
echo "    \$EDITOR ~/makaroshki-bridge.yaml      # hub.remote と agent を設定"
echo "    makaroshki-bridge --config ~/makaroshki-bridge.yaml chats   # 接続確認"
echo "    makaroshki-bridge --config ~/makaroshki-bridge.yaml run     # 常駐起動"
echo ""
