#!/usr/bin/env bash
# install.sh — agent-board インストーラー
# 使い方: bash install.sh [--prefix <dir>]
#
# デフォルトのインストール先: ~/.local/bin/agent-board
# agent-board は標準ライブラリのみ（pip 依存なし）。git は分散モード（board: git+<url>）で必要。
# 実体は agent_board/ パッケージ。配布は agent-flow と同じく zipapp で単一実行ファイルにまとめる。

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
die()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }

INSTALL_PREFIX="${HOME}/.local/bin"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) INSTALL_PREFIX="$2"; shift 2 ;;
    --help|-h) echo "使い方: bash install.sh [--prefix <インストール先>]"; exit 0 ;;
    *) die "不明なオプション: $1" ;;
  esac
done

INSTALL_PATH="${INSTALL_PREFIX}/agent-board"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="${SCRIPT_DIR}/agent_board"

echo ""
echo "========================================"
echo "  agent-board インストーラー"
echo "========================================"
echo ""

info "python を確認しています..."
PYTHON_CMD=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PY_MAJOR="$("$cmd" -c 'import sys; print(sys.version_info.major)')"
    PY_MINOR="$("$cmd" -c 'import sys; print(sys.version_info.minor)')"
    if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 9 ]]; then
      PYTHON_CMD="$cmd"; ok "$("$cmd" --version 2>&1) が見つかりました"; break
    fi
  fi
done
[[ -n "$PYTHON_CMD" ]] || die "Python 3.9 以上が見つかりません。"

info "git を確認しています（分散モードで必須）..."
if command -v git &>/dev/null; then
  ok "git が見つかりました: $(git --version 2>&1 | head -1)"
else
  warn "git が見つかりません。ローカル board（board: <dir>）のみ利用可能です。"
fi

info "agent_board パッケージを zipapp にまとめてインストールしています..."
[[ -d "${PKG}" ]] || die "agent_board パッケージが見つかりません: ${PKG}"
mkdir -p "$INSTALL_PREFIX"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agent-board-build.XXXXXX")"
trap 'rm -rf "${BUILD_DIR}"' EXIT
mkdir -p "${BUILD_DIR}/agent_board"
( cd "${PKG}" && find . -name '*.py' -print0 | while IFS= read -r -d '' f; do
    mkdir -p "${BUILD_DIR}/agent_board/$(dirname "$f")"
    cp "$f" "${BUILD_DIR}/agent_board/$f"
  done )
cat > "${BUILD_DIR}/__main__.py" <<'EOF'
from agent_board import main

if __name__ == "__main__":
    raise SystemExit(main())
EOF

"$PYTHON_CMD" -m zipapp "${BUILD_DIR}" -o "${INSTALL_PATH}" -p "/usr/bin/env ${PYTHON_CMD}"
chmod +x "${INSTALL_PATH}"
ok "インストールしました: ${INSTALL_PATH}（zipapp）"

case ":${PATH}:" in
  *":${INSTALL_PREFIX}:"*) ok "${INSTALL_PREFIX} は PATH に含まれています。" ;;
  *) warn "${INSTALL_PREFIX} が PATH にありません。次を ~/.bashrc 等に追加してください:
  export PATH=\"${INSTALL_PREFIX}:\$PATH\"" ;;
esac
echo ""
ok "完了。使い方: agent-board --help"
