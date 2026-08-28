#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
mkdir -p "${BIN_DIR}"
install -m 0755 "${ROOT}/vscode-copilot-chat.py" "${BIN_DIR}/vscode-copilot-chat"
if command -v powershell.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1; then
  WIN_SOURCE="$(wslpath -w "${ROOT}/extension")"
  powershell.exe -NoProfile -NonInteractive -Command \
    '$dst=Join-Path $env:USERPROFILE ".vscode\extensions\local.vscode-copilot-bridge-0.1.0";' \
    'New-Item -ItemType Directory -Force $dst | Out-Null;' \
    "Copy-Item -Force '${WIN_SOURCE}\\package.json','${WIN_SOURCE}\\extension.js' \$dst"
  printf 'Installed Windows VS Code extension and WSL CLI: %s\n' "${BIN_DIR}/vscode-copilot-chat"
else
  EXT_DIR="${HOME}/.vscode/extensions/local.vscode-copilot-bridge-0.1.0"
  mkdir -p "${EXT_DIR}"
  cp "${ROOT}/extension/package.json" "${ROOT}/extension/extension.js" "${EXT_DIR}/"
  printf 'Installed extension: %s\nInstalled CLI: %s\n' "${EXT_DIR}" "${BIN_DIR}/vscode-copilot-chat"
fi
case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *) printf 'Note: %s is not on PATH. Add it to your shell profile.\n' "${BIN_DIR}" ;;
esac
printf 'Run: vscode-copilot-chat --start "hello"\n'
