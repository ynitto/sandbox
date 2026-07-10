#!/usr/bin/env bash
# install.sh — codd-gate インストーラー
# 使い方: bash install.sh [--prefix <dir>]
#
# デフォルトのインストール先: ~/.local/bin/codd-gate
# codd-gate は標準ライブラリのみ（pip 依存なし）。git が PATH にあること。

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
SRC="${SCRIPT_DIR}/codd-gate.py"
[[ -f "${SRC}" ]] || die "codd-gate.py が見つかりません: ${SRC}"

command -v python3 >/dev/null 2>&1 || die "python3 が必要です"
command -v git >/dev/null 2>&1 || warn "git が見つかりません（差分ゲートには git が必要です）"

mkdir -p "${INSTALL_PREFIX}"
DEST="${INSTALL_PREFIX}/codd-gate"
cp "${SRC}" "${DEST}"
chmod +x "${DEST}"
ok "インストールしました: ${DEST}"

if command -v kiro-project >/dev/null 2>&1; then
  ok "kiro-project を検出（regression_cmd / acceptance / tasks で連携できます）"
else
  info "kiro-project が無くても単体の一貫性ゲートとして使えます"
fi

case ":${PATH}:" in
  *":${INSTALL_PREFIX}:"*) ;;
  *) warn "${INSTALL_PREFIX} が PATH にありません" ;;
esac
