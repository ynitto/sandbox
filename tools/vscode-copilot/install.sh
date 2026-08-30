#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
mkdir -p "${BIN_DIR}"
install -m 0755 "${ROOT}/vscode-copilot.py" "${BIN_DIR}/vscode-copilot"
# 旧名を残すと、PATH に居座った古い版をそのまま実行してしまう。改名前の版は
# 拡張との噛み合わせも古いので、消して名前を 1 つにする。
if [ -e "${BIN_DIR}/vscode-copilot-chat" ]; then
  rm -f "${BIN_DIR}/vscode-copilot-chat"
  printf 'Removed the old CLI: %s\n' "${BIN_DIR}/vscode-copilot-chat"
fi
if command -v powershell.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1; then
  WIN_SOURCE="$(wslpath -w "${ROOT}/extension")"
  powershell.exe -NoProfile -NonInteractive -Command \
    '$dst=Join-Path $env:USERPROFILE ".vscode\extensions\local.vscode-copilot-bridge-0.1.0";' \
    'New-Item -ItemType Directory -Force $dst | Out-Null;' \
    "Copy-Item -Force '${WIN_SOURCE}\\package.json','${WIN_SOURCE}\\extension.js' \$dst"
  printf 'Installed Windows VS Code extension and WSL CLI: %s\n' "${BIN_DIR}/vscode-copilot"
else
  EXT_DIR="${HOME}/.vscode/extensions/local.vscode-copilot-bridge-0.1.0"
  mkdir -p "${EXT_DIR}"
  cp "${ROOT}/extension/package.json" "${ROOT}/extension/extension.js" "${EXT_DIR}/"
  printf 'Installed extension: %s\nInstalled CLI: %s\n' "${EXT_DIR}" "${BIN_DIR}/vscode-copilot"
fi
case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *) printf 'Note: %s is not on PATH. Add it to your shell profile.\n' "${BIN_DIR}" ;;
esac
# 既に bridge が動いていると、CLI はそれを使い回す（二重起動を避けるため）。入れ替えた
# 拡張を読ませるには、その VS Code ウィンドウを一度閉じる必要がある。
printf 'Note: bridge が起動中なら、その VS Code ウィンドウを閉じてください（新しい拡張は次の起動から）。\n'
printf 'Run: vscode-copilot "hello"\n'
