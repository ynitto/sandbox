#!/bin/sh
# Obsidian へメモを集めるフックを、kiro-cli（WSL・Linux）か Claude Code に入れる・外す・点検する。
# Windows の Copilot アプリにも入れるときは、Windows で hook_install.ps1 を使う（WSL 側もそこから入る）。
#
#   sh hook_install.sh --vault ~/Documents/Obsidian/Main          # kiro-cli に入れる
#   sh hook_install.sh install --client claude --vault <フォルダ>  # Claude Code に入れる
#   sh hook_install.sh doctor                                       # 点検
#   sh hook_install.sh uninstall                                    # 外す
set -eu
here=$(cd "$(dirname "$0")" && pwd)
action=install
case "${1:-}" in install|uninstall|doctor) action=$1; shift ;; esac
case " $* " in *" --client "*) exec python3 "$here/hook_install.py" "$action" "$@" ;; esac
exec python3 "$here/hook_install.py" "$action" --client kiro "$@"
