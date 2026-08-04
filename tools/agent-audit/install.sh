#!/usr/bin/env bash
# install.sh — agent-audit インストーラ（シム）。
#
# 実体は tools/agent-tools/install.sh（agentcore を共有する 4 本を 1 つの installer で
# 束ねる。別々に入れると片方だけ古い状態ができるため入口を 1 本にしている）。
# このシムは既存の手順書・自己更新の呼び出しパスを壊さないために残す。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/../agent-tools/install.sh" --only agent-audit "$@"
