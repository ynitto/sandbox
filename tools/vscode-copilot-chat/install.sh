#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DIR="${HOME}/.vscode/extensions/local.vscode-copilot-bridge-0.1.0"
BIN_DIR="${HOME}/.local/bin"
mkdir -p "${EXT_DIR}" "${BIN_DIR}"
cp "${ROOT}/extension/package.json" "${ROOT}/extension/extension.js" "${EXT_DIR}/"
install -m 0755 "${ROOT}/vscode-copilot-chat.py" "${BIN_DIR}/vscode-copilot-chat"
printf 'Installed extension: %s\nInstalled CLI: %s\nRestart VS Code, then run: vscode-copilot-chat "hello"\n' \
  "${EXT_DIR}" "${BIN_DIR}/vscode-copilot-chat"
