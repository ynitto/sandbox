#!/usr/bin/env bash
# install.sh — kiro-loop インストーラー
# 使い方: bash install.sh [--prefix <dir>]
#
# デフォルトのインストール先: ~/.local/bin/kiro-loop

set -euo pipefail

# ---------------------------------------------------------------------------
# カラー出力
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

# ---------------------------------------------------------------------------
# 引数解析
# ---------------------------------------------------------------------------
INSTALL_PREFIX="${HOME}/.local/bin"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      INSTALL_PREFIX="$2"
      shift 2
      ;;
    --help|-h)
      echo "使い方: bash install.sh [--prefix <インストール先ディレクトリ>]"
      echo "  デフォルト: ~/.local/bin"
      exit 0
      ;;
    *)
      die "不明なオプション: $1"
      ;;
  esac
done

INSTALL_PATH="${INSTALL_PREFIX}/kiro-loop"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "========================================"
echo "  kiro-loop インストーラー"
echo "========================================"
echo ""

# ---------------------------------------------------------------------------
# 1. 実行環境チェック（macOS / Linux / WSL）
# ---------------------------------------------------------------------------
info "実行環境を確認しています..."

OS="$(uname -s)"
case "$OS" in
  Darwin)
    ok "macOS 環境を検出しました。"
    ;;
  Linux)
    if grep -qi microsoft /proc/version 2>/dev/null; then
      ok "WSL 環境を検出しました。"
    else
      ok "Linux 環境を検出しました。"
    fi
    ;;
  *)
    die "サポートされていない OS です（検出: $OS）。macOS / Linux / WSL が必要です。"
    ;;
esac

# ---------------------------------------------------------------------------
# 2. tmux チェック
# ---------------------------------------------------------------------------
info "tmux を確認しています..."

if command -v tmux &>/dev/null; then
  TMUX_VER="$(tmux -V 2>&1 | head -1)"
  ok "tmux が見つかりました: $(command -v tmux) ($TMUX_VER)"
else
  die "tmux が見つかりません。インストールしてください。
  macOS:      brew install tmux
  Ubuntu/WSL: sudo apt install tmux"
fi

# ---------------------------------------------------------------------------
# 3. python チェック
# ---------------------------------------------------------------------------
info "python を確認しています..."

PYTHON_CMD=""
for cmd in python python3; do
  if command -v "$cmd" &>/dev/null; then
    PY_VER="$("$cmd" --version 2>&1)"
    # バージョン番号を抽出して 3.9 以上か確認
    PY_MAJOR="$("$cmd" -c 'import sys; print(sys.version_info.major)')"
    PY_MINOR="$("$cmd" -c 'import sys; print(sys.version_info.minor)')"
    if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 9 ]]; then
      PYTHON_CMD="$cmd"
      ok "$PY_VER が見つかりました: $(command -v "$cmd")"
      break
    else
      warn "$PY_VER は 3.9 未満のため除外します。"
    fi
  fi
done

if [[ -z "$PYTHON_CMD" ]]; then
  die "Python 3.9 以上が見つかりません。手動でインストールしてください。
  macOS:      brew install python3
  WSL/Ubuntu: sudo apt install python3
  参考: https://www.python.org/downloads/"
fi

# ---------------------------------------------------------------------------
# 4. pip チェック
# ---------------------------------------------------------------------------
info "pip を確認しています..."

PIP_CMD=""
for cmd in pip pip3; do
  if "$PYTHON_CMD" -m pip --version &>/dev/null 2>&1; then
    PIP_CMD="$PYTHON_CMD -m pip"
    ok "pip が使用可能です。"
    break
  elif command -v "$cmd" &>/dev/null; then
    PIP_CMD="$cmd"
    ok "pip が見つかりました: $(command -v "$cmd")"
    break
  fi
done

if [[ -z "$PIP_CMD" ]]; then
  die "pip が見つかりません。インストールしてください。
  macOS:      brew install python3  # pip3 が同梱されます
  WSL/Ubuntu: sudo apt install python3-pip"
fi

# ---------------------------------------------------------------------------
# 5. kiro-cli チェック
# ---------------------------------------------------------------------------
info "kiro-cli を確認しています..."

if ! command -v kiro-cli &>/dev/null; then
  die "kiro-cli が見つかりません。先に手動でインストールしてください。
  参考: https://kiro.dev/docs/installation"
fi

KIRO_VER="$(kiro-cli --version 2>&1 | head -1 || echo '(バージョン取得失敗)')"
ok "kiro-cli が見つかりました: $(command -v kiro-cli) ($KIRO_VER)"

# ---------------------------------------------------------------------------
# 6. Python 依存ライブラリのインストール（PyYAML は任意）
# ---------------------------------------------------------------------------
info "Python 依存ライブラリを確認・インストールしています..."

if "$PYTHON_CMD" -c "import yaml" &>/dev/null 2>&1; then
  ok "pyyaml はインストール済みです。"
else
  warn "pyyaml が見つかりません（YAML 設定ファイルを使う場合は必要）。"
  read -r -p "  pyyaml をインストールしますか？ [Y/n] " yn
  case "${yn:-Y}" in
    [Yy]*)
      info "pyyaml をインストールしています..."
      if $PIP_CMD install --user pyyaml; then
        ok "pyyaml のインストールが完了しました。"
      else
        warn "pyyaml のインストールに失敗しました。JSON 設定ファイルを使う場合は不要です。"
      fi
      ;;
    *)
      warn "pyyaml をスキップしました。JSON 形式の設定ファイルを使用してください。"
      ;;
  esac
fi

# ---------------------------------------------------------------------------
# 7. スクリプトのインストール
# ---------------------------------------------------------------------------
info "kiro-loop.py をインストールしています..."

SRC="${SCRIPT_DIR}/kiro-loop.py"
if [[ ! -f "$SRC" ]]; then
  die "kiro-loop.py が見つかりません: $SRC"
fi

mkdir -p "$INSTALL_PREFIX"

cp "$SRC" "$INSTALL_PATH"
chmod +x "$INSTALL_PATH"

# shebang を環境の python コマンドに書き換える（BSD sed と GNU sed の差異を吸収）
if [[ "$OS" == "Darwin" ]]; then
  sed -i '' "1s|.*|#!/usr/bin/env ${PYTHON_CMD}|" "$INSTALL_PATH"
else
  sed -i "1s|.*|#!/usr/bin/env ${PYTHON_CMD}|" "$INSTALL_PATH"
fi

ok "インストールしました: $INSTALL_PATH"

# ---------------------------------------------------------------------------
# 8. PATH チェック
# ---------------------------------------------------------------------------
info "PATH を確認しています..."

if echo "$PATH" | tr ':' '\n' | grep -qF "$INSTALL_PREFIX"; then
  ok "$INSTALL_PREFIX は PATH に含まれています。"
else
  warn "$INSTALL_PREFIX が PATH に含まれていません。"
  echo ""
  echo "  以下を ~/.bashrc または ~/.zshrc に追加してください:"
  echo ""
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo ""
fi

# ---------------------------------------------------------------------------
# 完了
# ---------------------------------------------------------------------------
echo ""
echo "========================================"
ok "インストール完了！"
echo "========================================"
echo ""
echo "  使い方:"
echo "    cd ~/projects/my-app"
echo "    kiro-loop                                      # デーモンモードで起動"
echo "    kiro-loop ls                                   # kiro 関連セッションを一覧表示"
echo "    kiro-loop send 'コードをレビューして'           # プロンプトを送信"
echo "    kiro-loop send task.md                         # ファイル内容を読んで実行"
echo "    kiro-loop send 'MR コメント返答'               # 定期プロンプト名で送信"
echo "    kiro-loop send -s SESSION 'プロンプト'         # 指定セッションに送信"
echo ""
echo "  デーモン起動後のコマンド例:"
echo "    > status                                        # 状態表示"
echo "    > prompt-list                                   # 定期プロンプト一覧"
echo "    > help                                          # コマンド一覧"
echo ""
echo "  tmux セッション名の確認:"
echo "    kiro-loop ls                                    # kiro 関連セッション一覧"
echo "    tmux list-sessions                              # 全セッション一覧"
echo ""
