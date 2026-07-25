#!/usr/bin/env bash
# install.sh — 単一常駐コントローラ一式のインストーラ（実装計画 W3-1）。
#
# 使い方:
#   bash tools/agent-tools/install.sh                                # 3 本すべて（既定）
#   bash tools/agent-tools/install.sh --only agent-project           # 1 本だけ
#   bash tools/agent-tools/install.sh --prefix /usr/local/bin
#   bash tools/agent-tools/install.sh --service --host-config <path> # 常駐化（systemd）も構成
#
# **配布は 1 パッケージ・CLI エントリは 3 本**（設計 R9・R10）。agent-project /
# agent-flow / agent-amigos は同じ agentcore（transport・protocol・vocab・heartbeat）を
# 共有し、契約バージョンも揃っていなければならない——別々に入れると片方だけ古い
# ノードができて板の入札や状態の読み書きが噛み合わなくなる。だから入口を 1 本にする。
#
# 各エンジンの install.sh は本スクリプトへ委譲する薄いシムとして残してある
# （既存の手順書・setup.sh・自己更新の呼び出しパスを壊さないため）。
#
# 前提: 標準ライブラリのみ（pip 依存なし）。python3.9+ が要る。
#   - git       … 複数 PC 分散（状態共有・git バス）で必要。単機なら任意。
#   - PyYAML    … YAML 設定を使う場合のみ。JSON 設定なら不要。
#   - agent CLI … 実運用に必要（kiro / claude / copilot / codex / cursor）。
#                 無くても stub でプロトコルは動く。

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()   { error "$*"; exit 1; }

ALL_ENGINES=(agent-project agent-flow agent-amigos)

INSTALL_PREFIX="${HOME}/.local/bin"
ENGINES=("${ALL_ENGINES[@]}")
WITH_SERVICE=0
HOST_CONFIG=""

usage() {
  cat <<'USAGE'
使い方: bash tools/agent-tools/install.sh [オプション]

  --prefix <dir>          インストール先（既定 ~/.local/bin）
  --only <a>[,<b>]        入れるエンジンを絞る（agent-project / agent-flow / agent-amigos）
  --service               systemd --user unit（agent-project.service）を生成・有効化する
                          （WSL/Linux のみ。設計 §7 の常駐化 2 案のうち systemd 案。
                          Windows タスクスケジューラ案は docs/guides/ 参照 — 二重構成しない）
  --host-config <path>    --service と併用: unit の ExecStart へ渡す host.yaml
  -h, --help              このヘルプ
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) INSTALL_PREFIX="$2"; shift 2 ;;
    --only)
      IFS=', ' read -r -a ENGINES <<< "$2"
      for e in "${ENGINES[@]}"; do
        case " ${ALL_ENGINES[*]} " in
          *" ${e} "*) : ;;
          *) die "不明なエンジン: ${e}（指定できるのは ${ALL_ENGINES[*]}）" ;;
        esac
      done
      shift 2 ;;
    --service) WITH_SERVICE=1; shift ;;
    --host-config) HOST_CONFIG="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "不明な引数: $1" ;;
  esac
done

# このスクリプトが居る tools/agent-tools/（3 エンジンで共有するものの置き場）と、
# その親 tools/（各エンジンのディレクトリが並ぶ）。
SHARED_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$(cd "${SHARED_DIR}/.." && pwd)"
AGENTCORE_PKG="${SHARED_DIR}/agentcore/agentcore"

echo ""
echo "========================================"
echo "  agent tools インストーラー（${ENGINES[*]}）"
echo "========================================"
echo ""

# ---------------------------------------------------------------------------
# 1. 環境チェック（**1 回だけ**。エンジンごとに 3 回繰り返すと、同じ警告を 3 度読ませて
#    「どのエンジンの話か」を人が突き合わせる手間だけが増える）
# ---------------------------------------------------------------------------
info "実行環境を確認しています..."
OS="$(uname -s)"
case "$OS" in
  Darwin) ok "macOS 環境を検出しました。" ;;
  Linux)
    if grep -qi microsoft /proc/version 2>/dev/null; then
      ok "WSL 環境を検出しました。"
    else
      ok "Linux 環境を検出しました。"
    fi ;;
  *) die "サポートされていない OS です（検出: $OS）。macOS / Linux / WSL が必要です。" ;;
esac

info "python を確認しています..."
PYTHON_CMD=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PY_VER="$("$cmd" --version 2>&1)"
    if "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
      PYTHON_CMD="$cmd"
      ok "$PY_VER が見つかりました: $(command -v "$cmd")"
      break
    fi
    warn "$PY_VER は 3.9 未満のため除外します。"
  fi
done
[[ -n "$PYTHON_CMD" ]] || die "Python 3.9 以上が見つかりません。手動でインストールしてください。
  macOS:      brew install python3
  WSL/Ubuntu: sudo apt install python3
  参考: https://www.python.org/downloads/"

info "git を確認しています..."
if command -v git &>/dev/null; then
  ok "git が見つかりました: $(command -v git) ($(git --version 2>&1 | head -1))"
else
  warn "git が見つかりません。単機（ローカルの状態・ローカルバス）なら動きます。
  複数 PC で状態を共有する／git バスを使うには git が必要:
  macOS:      brew install git
  WSL/Ubuntu: sudo apt install git"
fi

info "エージェント CLI を確認しています..."
FOUND_CLI=""
for cli in kiro-cli claude copilot codex cursor-agent; do
  if command -v "$cli" &>/dev/null; then
    FOUND_CLI="$cli"
    ok "エージェント CLI を検出: $cli ($(command -v "$cli"))"
    break
  fi
done
if [[ -z "$FOUND_CLI" ]]; then
  warn "エージェント CLI が見つかりません。stub モードでのみ動作します
  （agent-flow: --planner stub --executor stub / agent-amigos: --agent-cli stub）。
  実運用には kiro / claude / copilot / codex のいずれかが必要です。
  それ以外は agents/<name>.json 定義で追加できます（契約: schemas/agent-cli.schema.json）。"
fi

info "PyYAML を確認しています（任意）..."
if "$PYTHON_CMD" -c "import yaml" &>/dev/null; then
  ok "PyYAML はインストール済みです（*.yaml 設定が使えます）。"
else
  warn "PyYAML が見つかりません（YAML 設定を使う場合のみ必要）。
  pip install --user pyyaml で入れられます。JSON 設定なら不要です。"
fi

mkdir -p "${INSTALL_PREFIX}"

# ---------------------------------------------------------------------------
# 2. zipapp ビルド（3 エンジン共通の手順を 1 か所に）
# ---------------------------------------------------------------------------
# 単一ファイル配布は維持しつつ、実体はパッケージ（LLM が編集できる大きさの断片へ分割済み）。
# agentcore は**各 zipapp へ同梱する**（独立配布しない内部モジュール — 設計 R10。
# 置き場は tools/agent-tools/agentcore＝3 エンジンで共有するものの置き場）。
# 3 本が別実行ファイルである以上、それぞれが自己完結していないと片方だけ動く状態になる。
build_engine() {
  local engine="$1" pkg_name="$2"
  local src_dir="${TOOLS_DIR}/${engine}"
  local pkg_dir="${src_dir}/${pkg_name}"
  local dest="${INSTALL_PREFIX}/${engine}"

  [[ -d "${pkg_dir}" ]] || die "${pkg_name} パッケージが見つかりません: ${pkg_dir}"
  [[ -d "${AGENTCORE_PKG}" ]] || die "agentcore パッケージが見つかりません: ${AGENTCORE_PKG}
  （自己更新の sparse-checkout なら update_subdir に tools/agent-tools が含まれているか確認）"

  info "${engine} を zipapp にまとめています..."
  local build_dir
  build_dir="$(mktemp -d "${TMPDIR:-/tmp}/${engine}-build.XXXXXX")"

  copy_py_tree "${pkg_dir}" "${build_dir}/${pkg_name}"
  copy_py_tree "${AGENTCORE_PKG}" "${build_dir}/agentcore" -not -path './tests/*'

  # codd-gate 自動検出・regression/intake 結線が読む sibling module（パッケージの外・
  # install.sh と同階層の codd_gate_*.py）は zipapp ルートへ同梱する。無ければ遅延 import が
  # 失敗して no-op 縮退するだけ（安全）だが、同梱すれば配布バイナリでも検出・結線が動く。
  local f
  for f in "${src_dir}"/codd_gate_*.py; do
    [[ -f "$f" ]] && cp "$f" "${build_dir}/$(basename "$f")"
  done

  cat > "${build_dir}/__main__.py" <<EOF
from ${pkg_name} import main

if __name__ == "__main__":
    raise SystemExit(main())
EOF

  "$PYTHON_CMD" -m zipapp "${build_dir}" -o "${dest}" -p "/usr/bin/env ${PYTHON_CMD}"
  chmod +x "${dest}"
  rm -rf "${build_dir}"
  ok "インストールしました: ${dest}（zipapp）"

  # 起動できることまで確かめる。ここで落ちるのは shebang / python の取り違えなので、
  # 「入った」と言い切る前に踏んでおく（人が最初のコマンドで初めて気づくのを避ける）。
  if "${dest}" --help >/dev/null 2>&1; then
    ok "${engine} --help が正常に動作しました。"
  else
    warn "${engine} --help の実行に失敗しました。shebang / Python を確認してください。"
  fi
}

# __pycache__ / .pyc を zipapp に含めない（配布物にビルド環境の痕跡を持ち込まない）。
copy_py_tree() {
  local src="$1" dst="$2"; shift 2
  mkdir -p "${dst}"
  ( cd "${src}" && find . -name '*.py' "$@" -print0 | while IFS= read -r -d '' f; do
      mkdir -p "${dst}/$(dirname "$f")"
      cp "$f" "${dst}/$f"
    done )
}

# エンジン名 → パッケージ名。連想配列（declare -A）は bash 4+ 専用で、macOS 標準の
# /bin/bash は 3.2 なので使わない。
pkg_of() {
  case "$1" in
    agent-project) echo agent_project ;;
    agent-flow)    echo agent_flow ;;
    agent-amigos)  echo agent_amigos ;;
    *) die "パッケージ名が未定義のエンジン: $1" ;;
  esac
}

for engine in "${ENGINES[@]}"; do
  build_engine "${engine}" "$(pkg_of "${engine}")"
done

# ---------------------------------------------------------------------------
# 3. エンジン固有の付帯物
# ---------------------------------------------------------------------------
installed() {
  case " ${ENGINES[*]} " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

# agent-flow の executor プラグイン: 本体は zipapp 単一ファイルなので、同梱プラグインは
# 「本体と同じフォルダ」（<prefix>/executors/）に置く。agent-flow の検索順 #1
# 「スクリプト同階層の executors/」がインストール後も名前で解決できるようにするため。
if installed agent-flow; then
  EXEC_SRC_DIR="${TOOLS_DIR}/agent-flow/executors"
  if [[ -d "$EXEC_SRC_DIR" ]]; then
    mkdir -p "${INSTALL_PREFIX}/executors"
    n=0
    for f in "$EXEC_SRC_DIR"/*.py; do
      [[ -e "$f" ]] || continue
      cp "$f" "${INSTALL_PREFIX}/executors/"; n=$((n + 1))
    done
    [[ "$n" -gt 0 ]] && ok "executor プラグインを ${n} 件配置しました: ${INSTALL_PREFIX}/executors"
  fi
fi

# 同リポジトリの独立ツール codd-gate（doc/code/test 一貫性ゲート）も隣にあれば同じ prefix へ。
# 有効化は設定だけ（intake_cmd / regression_cmd / charter acceptance。本体は無改造・任意連携）。
# sparse-checkout（自己更新）等で隣に無ければ何もしない（codd-gate は独立に更新する）。
if installed agent-project; then
  CODD_INSTALLER="${TOOLS_DIR}/codd-gate/install.sh"
  if [[ -f "${CODD_INSTALLER}" ]]; then
    if bash "${CODD_INSTALLER}" --prefix "${INSTALL_PREFIX}" >/dev/null; then
      ok "codd-gate も同梱インストールしました（有効化は設定で: intake_cmd / regression_cmd / acceptance）"
    else
      warn "codd-gate のインストールに失敗しました（本体には影響ありません）"
    fi
  fi
fi

case ":${PATH}:" in
  *":${INSTALL_PREFIX}:"*) ok "${INSTALL_PREFIX} は PATH に含まれています。" ;;
  *) warn "${INSTALL_PREFIX} が PATH にありません。シェル設定に追加してください:
    export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# ---------------------------------------------------------------------------
# 4. 常駐化（設計 §4.2・§7）
# ---------------------------------------------------------------------------
# PC 起動/ログオン時に常駐体が上がり、死んだら上げ直されるようにする 2 案のうち
# systemd user unit（WSL/Linux）を --service で選択式に構成する。Windows タスク
# スケジューラ案（wsl.exe -d <distro> -- agent-project serve をログオン時に再起動ループ付きで
# 常駐させる）は WSL の外側の操作なのでここからは実行できない——手順は
# docs/guides/single-resident-setup.md を参照。二重構成しないのは人の責任（doctor が
# 検査できるのは systemd 側だけで、Windows 側は WSL の外から見えないため検出できない）。
# タスクスケジューラ案を選んだら host.yaml に `residency: windows-task` を宣言する
# ——doctor の誤警告（常駐化が未構成）を止めるため。
if [[ "${WITH_SERVICE}" -eq 1 ]]; then
  if ! installed agent-project; then
    warn "--service は agent-project の常駐化です（--only に agent-project が無いのでスキップ）"
  elif ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
    warn "systemd が見つかりません。--service は WSL/Linux 専用です（スキップ）"
  else
    UNIT_DIR="${HOME}/.config/systemd/user"
    UNIT_PATH="${UNIT_DIR}/agent-project.service"
    mkdir -p "${UNIT_DIR}"
    EXEC_START="${INSTALL_PREFIX}/agent-project serve"
    [[ -n "${HOST_CONFIG}" ]] && EXEC_START="${EXEC_START} --host-config ${HOST_CONFIG}"
    cat > "${UNIT_PATH}" <<EOF
[Unit]
Description=agent-project resident (単一常駐コントローラ)
After=network-online.target
Wants=network-online.target

[Service]
# Type=notify: 常駐体が全 tick を上げてから READY=1 を送る（Scheduler.start）。
# 起動完了を systemd が正しく捉えられるので、起動途中の異常を「上がった」と誤認しない。
Type=notify
# WatchdogSec: 常駐体は WatchdogSec/2 の間隔で WATCHDOG=1 を打つ（Scheduler.watchdog_interval が
# WATCHDOG_USEC から導出する）。内蔵 self-watchdog の自己 abort が主経路で、これはその外側の
# 二重化——スケジューラごと固まって abort すら打てない場合に systemd が殺して上げ直す。
WatchdogSec=90
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

echo ""
echo "========================================"
ok "インストール完了！（${ENGINES[*]}）"
echo "========================================"
echo ""
echo "  導入手順の正典: docs/guides/single-resident-setup.md"
echo ""
if installed agent-project; then
  echo "  常駐（PC に 1 本。監督・参加 tick・gc を担う）:"
  echo "    agent-project serve"
  echo "    agent-project status            # 心拍・子の生死・休止/切り離し"
  echo "    agent-project doctor            # 構成の検査"
  echo ""
fi
if installed agent-flow; then
  echo "  agent-flow（要求の分解と分散実行）:"
  echo "    agent-flow run \"要件整理; API設計; テスト\" --workers 3"
  echo "    agent-flow status --run-id <run-id> --follow"
  echo ""
fi
if installed agent-amigos; then
  echo "  agent-amigos（役割駆動の協働。常駐はしない — 単発実行）:"
  echo "    agent-amigos participate                # 参加だけ 1 巡"
  echo "    agent-amigos drive --mission-id <id>    # 終端まで回して戻る"
  echo ""
fi
echo "  設定の雛形:"
for engine in "${ENGINES[@]}"; do
  for f in "${TOOLS_DIR}/${engine}"/*.example; do
    [[ -f "$f" ]] && echo "    ${f}"
  done
done
echo ""
echo "  開発時の編集は tools/<engine>/<package>/*.py（断片）。配布は zipapp 単一ファイル。"
echo ""
