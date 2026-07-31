#!/usr/bin/env bash
# install.sh — agent-flow の入口（実体は tools/agent-tools/install.sh。実装計画 W3-1）。
#
# 配布は 1 パッケージ・CLI エントリは 3 本（設計 R9・R10）。3 エンジンは同じ agentcore を
# 共有し契約バージョンも揃っている必要があるため、ビルドと環境チェックは tools/agent-tools/install.sh に
# 一本化した。このファイルはそこへ委譲する薄いシムとして残してある——既存の手順書・setup.sh・
# 自己更新（update.py が update_subdir の中で install.sh を叩く）の呼び出しパスを壊さないため。
#
# 3 本まとめて入れたいときは tools/agent-tools/install.sh を直接叩く（このシムは agent-flow だけ入れる）。
# 引数はすべてそのまま渡す（--prefix / --service / --host-config など）。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIFIED="${SCRIPT_DIR}/../agent-tools/install.sh"
if [[ ! -f "${UNIFIED}" ]]; then
  echo "[ERROR] 統合インストーラが見つかりません: ${UNIFIED}" >&2
  echo "        リポジトリを部分取得している場合は tools/agent-tools/ も取得してください" >&2
  exit 1
fi
exec bash "${UNIFIED}" --only agent-flow "$@"
