#!/usr/bin/env bash
# install.sh — task-loop インストーラー
# 使い方: bash install.sh [--prefix <dir>]
#
# デフォルトのインストール先: ~/.local/bin/task-loop
# task-loop は標準ライブラリのみ（pip 依存なし）。
# act の委譲先として kiro-flow を PATH に置いておくと連携できる（無くても --dry-run で動く）。

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()   { error "$*"; exit 1; }

INSTALL_PREFIX="${HOME}/.local/bin"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) INSTALL_PREFIX="$2"; shift 2 ;;
    -h|--help) echo "使い方: bash install.sh [--prefix <dir>]"; exit 0 ;;
    *) die "不明な引数: $1" ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${SCRIPT_DIR}/task-loop.py"
[[ -f "${SRC}" ]] || die "task-loop.py が見つかりません: ${SRC}"

command -v python3 >/dev/null 2>&1 || die "python3 が必要です"

mkdir -p "${INSTALL_PREFIX}"
DEST="${INSTALL_PREFIX}/task-loop"
cp "${SRC}" "${DEST}"
chmod +x "${DEST}"
ok "インストールしました: ${DEST}"

if command -v kiro-flow >/dev/null 2>&1; then
  ok "kiro-flow を検出（act の委譲先として連携できます）"
else
  warn "kiro-flow が PATH にありません。--dry-run なら不要、実行委譲には tools/kiro-flow/install.sh を実行してください"
fi

case ":${PATH}:" in
  *":${INSTALL_PREFIX}:"*) : ;;
  *) warn "${INSTALL_PREFIX} が PATH にありません。シェル設定に追加してください" ;;
esac
