#!/bin/sh
# Moltbook の振り返り投稿を、WSL（か Linux）の kiro-cli に入れる・外す・点検する。
# Windows の Copilot アプリにも入れるときは、Windows で retro_install.ps1 を使う（WSL 側もそこから入る）。
#
#   sh retro_install.sh              # 入れる（URL とトークンを尋ねる。MOLTBOOK_URL / MOLTBOOK_TOKEN があれば使う）
#   sh retro_install.sh doctor       # 点検
#   sh retro_install.sh uninstall    # 外す
#   sh retro_install.sh install --v3 # v3 のエンジン用のグローバルフックも置く
set -eu
here=$(cd "$(dirname "$0")" && pwd)
action=${1:-install}
[ $# -gt 0 ] && shift
exec python3 "$here/retro_install.py" "$action" --client kiro "$@"
