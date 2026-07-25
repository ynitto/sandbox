#!/usr/bin/env bash
# install.sh — agent-project インストーラー
# 使い方: bash install.sh [--prefix <dir>]
#
# デフォルトのインストール先: ~/.local/bin/agent-project
# agent-project は標準ライブラリのみ（pip 依存なし）。
# act の委譲先として agent-flow を PATH に置いておくと連携できる（無くても --dry-run で動く）。

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()   { error "$*"; exit 1; }

INSTALL_PREFIX="${HOME}/.local/bin"
WITH_SERVICE=0
HOST_CONFIG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) INSTALL_PREFIX="$2"; shift 2 ;;
    --service) WITH_SERVICE=1; shift ;;
    --host-config) HOST_CONFIG="$2"; shift 2 ;;
    -h|--help) echo "使い方: bash install.sh [--prefix <dir>] [--service [--host-config <path>]]
  --service       systemd --user unit（agent-project.service）を生成・有効化する
                  （WSL/Linux のみ。設計 §7 の常駐化 2 案のうち systemd 案。
                  Windows タスクスケジューラ案は docs/guides/ 参照 — 二重構成しない）"
      exit 0 ;;
    *) die "不明な引数: $1" ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="${SCRIPT_DIR}/agent_project"
[[ -d "${PKG}" ]] || die "agent_project パッケージが見つかりません: ${PKG}"

command -v python3 >/dev/null 2>&1 || die "python3 が必要です"

mkdir -p "${INSTALL_PREFIX}"
DEST="${INSTALL_PREFIX}/agent-project"

# 単一ファイル配布は維持しつつ、実体はパッケージ（LLM が編集できる大きさの断片へ分割済み）。
# zipapp で「agent_project/ パッケージ + ルート __main__.py」を1実行ファイルへまとめる。
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agent-project-build.XXXXXX")"
trap 'rm -rf "${BUILD_DIR}"' EXIT
mkdir -p "${BUILD_DIR}/agent_project"
# __pycache__ を除いてパッケージをコピー（zipapp に .pyc を含めない）。
( cd "${PKG}" && find . -name '*.py' -print0 | while IFS= read -r -d '' f; do
    mkdir -p "${BUILD_DIR}/agent_project/$(dirname "$f")"
    cp "$f" "${BUILD_DIR}/agent_project/$f"
  done )
# agentcore（transport/protocol/vocab/heartbeat の共通ライブラリ。独立配布しない内部モジュール
# ——設計 R10）を同じ zipapp へ同梱する。tools/ の兄弟ディレクトリなので配置は SCRIPT_DIR/../agentcore。
AGENTCORE_PKG="${SCRIPT_DIR}/../agentcore/agentcore"
[[ -d "${AGENTCORE_PKG}" ]] || die "agentcore パッケージが見つかりません: ${AGENTCORE_PKG}"
( cd "${AGENTCORE_PKG}" && find . -name '*.py' -not -path './tests/*' -print0 | while IFS= read -r -d '' f; do
    mkdir -p "${BUILD_DIR}/agentcore/$(dirname "$f")"
    cp "$f" "${BUILD_DIR}/agentcore/$f"
  done )
# codd-gate 自動検出・regression/intake 結線が読む sibling module（agent_project/ の外、
# このスクリプトと同じ階層にある codd_gate_*.py）もzipapp ルートへ同梱する。無ければ
# doctor.py/model.py/configfile.py の遅延 import が失敗し no-op 縮退するだけ（安全）だが、
# 同梱すれば配布バイナリでも検出・regression・intake が動く。
for f in "${SCRIPT_DIR}"/codd_gate_*.py; do
  [[ -f "$f" ]] && cp "$f" "${BUILD_DIR}/$(basename "$f")"
done
cat > "${BUILD_DIR}/__main__.py" <<'EOF'
from agent_project import main

if __name__ == "__main__":
    raise SystemExit(main())
EOF

python3 -m zipapp "${BUILD_DIR}" -o "${DEST}" -p "/usr/bin/env python3"
chmod +x "${DEST}"
ok "インストールしました: ${DEST}（zipapp）"

# 同リポジトリの独立ツール codd-gate（doc/code/test 一貫性ゲート）も隣にあれば同じ prefix へ入れる。
# 有効化は設定だけ（intake_cmd / regression_cmd / charter acceptance。本体は無改造・任意連携）。
# sparse-checkout（自動アップデート）等で隣に無ければ何もしない（codd-gate は独立に更新する）。
CODD_INSTALLER="${SCRIPT_DIR}/../codd-gate/install.sh"
if [[ -f "${CODD_INSTALLER}" ]]; then
  if bash "${CODD_INSTALLER}" --prefix "${INSTALL_PREFIX}" >/dev/null; then
    ok "codd-gate も同梱インストールしました（有効化は設定で: intake_cmd / regression_cmd / acceptance）"
  else
    warn "codd-gate のインストールに失敗しました（agent-project 本体には影響ありません）"
  fi
fi

if command -v agent-flow >/dev/null 2>&1; then
  ok "agent-flow を検出（act の委譲先として連携できます）"
else
  warn "agent-flow が PATH にありません。--dry-run なら不要、実行委譲には tools/agent-flow/install.sh を実行してください"
fi

case ":${PATH}:" in
  *":${INSTALL_PREFIX}:"*) : ;;
  *) warn "${INSTALL_PREFIX} が PATH にありません。シェル設定に追加してください" ;;
esac

# 常駐化（設計 §4.2・§7）: PC 起動/ログオン時に常駐体が上がり、死んだら上げ直される
# ようにする 2 案のうち systemd user unit（WSL/Linux）を --service で選択式に構成する。
# Windows タスクスケジューラ案（wsl.exe -d <distro> -- agent-project serve をログオン時に
# 再起動ループ付きで常駐させる）は WSL の外側（Windows 側）の操作なのでこのスクリプトからは
# 実行できない——手順は docs/guides/single-resident-setup.md を参照。二重構成しないのは人の
# 責任（doctor が検査できるのは systemd 側だけで、Windows 側は WSL の外から見えないため
# 二重構成は検出できない）。タスクスケジューラ案を選んだら host.yaml に
# `residency: windows-task` を宣言する——doctor の誤警告（常駐化が未構成）を止めるため。
if [[ "${WITH_SERVICE}" -eq 1 ]]; then
  if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
    warn "systemd が見つかりません。--service は WSL/Linux 専用です（スキップ）"
  else
    UNIT_DIR="${HOME}/.config/systemd/user"
    UNIT_PATH="${UNIT_DIR}/agent-project.service"
    mkdir -p "${UNIT_DIR}"
    EXEC_START="${DEST} serve"
    [[ -n "${HOST_CONFIG}" ]] && EXEC_START="${EXEC_START} --host-config ${HOST_CONFIG}"
    cat > "${UNIT_PATH}" <<EOF
[Unit]
Description=agent-project resident (単一常駐コントローラ)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${EXEC_START}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
    ok "systemd unit を書き出しました: ${UNIT_PATH}"
    systemctl --user daemon-reload
    systemctl --user enable --now agent-project.service \
      && ok "agent-project.service を有効化・起動しました" \
      || warn "systemctl --user enable --now に失敗しました（unit は書き出し済み。手動で確認してください）"
    if command -v loginctl >/dev/null 2>&1; then
      loginctl enable-linger "$(whoami)" 2>/dev/null \
        && ok "loginctl enable-linger を設定しました（ログアウト後もサービスを維持）" \
        || warn "loginctl enable-linger に失敗しました（ログアウトで停止する可能性があります）"
    fi
  fi
fi
