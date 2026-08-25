#!/usr/bin/env bash
# install.sh — 単一常駐コントローラ一式のインストーラ（実装計画 W3-1）。
#
# 使い方:
#   bash tools/agent-tools/install.sh                                # 3 本すべて（既定）
#   bash tools/agent-tools/install.sh --only agent-project           # 1 本だけ
#   bash tools/agent-tools/install.sh --prefix /usr/local/bin
#   bash tools/agent-tools/install.sh --service --host-config <path> # 常駐化（systemd）も構成
#
# **配布は 1 パッケージ・CLI エントリは 4 本**（設計 R9・R10）。agent-project /
# agent-flow / agent-amigos / agent-audit は同じ agentcore（transport・protocol・vocab・
# heartbeat・agentcli）を共有し、契約バージョンも揃っていなければならない——別々に
# 入れると片方だけ古いノードができて板の入札や状態の読み書きが噛み合わなくなる。
# だから入口を 1 本にする。
#
# 各エンジンの install.sh は本スクリプトへ委譲する薄いシムとして残してある
# （既存の手順書・setup.sh・自己更新の呼び出しパスを壊さないため）。
#
# agent-loop はエンジン 4 本には含めないが、**同じ agentcore を同梱する zipapp**なので
# 一緒に入れ直す（委譲は §3。ここから漏れていた間、agent-loop だけが古い agentcore と
# 古い実装のまま残り、新しい定義契約を解釈できずに fail fast していた）。
#
# 前提: 標準ライブラリのみ（pip 依存なし）。python3.11+ が要る（CI が回すのと同じ下限）。
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

ALL_ENGINES=(agent-project agent-flow agent-amigos agent-audit)

INSTALL_PREFIX="${HOME}/.local/bin"
ENGINES=("${ALL_ENGINES[@]}")
WITH_SERVICE=0
HOST_CONFIG=""
WITH_RICH=0

usage() {
  cat <<'USAGE'
使い方: bash tools/agent-tools/install.sh [オプション]

  --prefix <dir>          インストール先（既定 ~/.local/bin）
  --only <a>[,<b>]        入れる対象を絞る（agent-project / agent-flow / agent-amigos /
                          agent-audit / agent-herd）。`--only agent-herd` は
                          エンジンを入れず実行系の入口だけを置く（推論だけ担当する PC 向け）
  --service               systemd --user unit（agent-project.service）を生成・有効化する
                          （WSL/Linux のみ。設計 §7 の常駐化 2 案のうち systemd 案。
                          Windows タスクスケジューラ案は docs/guides/ 参照 — 二重構成しない）
  --host-config <path>    --service と併用: unit の ExecStart へ渡す host.yaml
  --with-rich             agent-ollama の zipapp へ rich を同梱する（TUI の色付けが有効に
                          なる。**pip とネットワークが要る**——このインストーラは既定では
                          標準ライブラリだけで完結するので、要るときだけ明示する。
                          取得に失敗しても中断せず、素の ANSI 表示のまま続ける）
  -h, --help              このヘルプ
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) INSTALL_PREFIX="$2"; shift 2 ;;
    --only)
      IFS=', ' read -r -a _ONLY <<< "$2"
      ENGINES=()
      for e in "${_ONLY[@]}"; do
        case " ${ALL_ENGINES[*]} " in
          *" ${e} "*) ENGINES+=("${e}") ; continue ;;
        esac
        # agent-herd はエンジンではなく実行系の入口。エンジンを 1 本も入れずに
        # 実行系だけ置きたいノード（推論だけ担当する PC）のために単独指定を許す。
        [[ "${e}" == "agent-herd" ]] \
          || die "不明な対象: ${e}（指定できるのは ${ALL_ENGINES[*]} agent-herd）"
      done
      shift 2 ;;
    --service) WITH_SERVICE=1; shift ;;
    --host-config) HOST_CONFIG="$2"; shift 2 ;;
    --with-rich) WITH_RICH=1; shift ;;
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
    if "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
      PYTHON_CMD="$cmd"
      ok "$PY_VER が見つかりました: $(command -v "$cmd")"
      break
    fi
    warn "$PY_VER は 3.11 未満のため除外します。"
  fi
done
# 下限は CI が実際に回している版に揃えてある（宣言だけ低くしても誰も確かめていない状態になる）。
[[ -n "$PYTHON_CMD" ]] || die "Python 3.11 以上が見つかりません。手動でインストールしてください。
  macOS:      brew install python@3.11
  WSL/Ubuntu: sudo apt install python3.11
              （Ubuntu 22.04 の既定は 3.10 なので、必要なら
                sudo add-apt-repository ppa:deadsnakes/ppa を足す。24.04 以降は既定で 3.12）
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
for cli in kiro-cli claude copilot codex cursor-agent opencode; do
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
  それ以外は agents/<name>.json 定義で追加できます（契約: schemas/agent-cli.schema.json）。
  ローカル推論（別 PC の ollama）で回すなら opencode を独立インストーラで入れられます:
    bash tools/opencode/install.sh --ollama-host http://<推論する PC>:11434"
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
    agent-audit)   echo agent_audit ;;
    *) die "パッケージ名が未定義のエンジン: $1" ;;
  esac
}

for engine in "${ENGINES[@]+"${ENGINES[@]}"}"; do
  build_engine "${engine}" "$(pkg_of "${engine}")"
done

# ---------------------------------------------------------------------------
# agent-herd — LAN の ollama を動かす実行系の入口（busybox 型 1 zipapp）
# ---------------------------------------------------------------------------
# 3 adapter（aider / ollama / opencode）は同じ agentcore を使い、同じ環境補完
# （agentcore.hostenv）を必要とする。以前は agent-ollama だけが zipapp で、agent-aider と
# agent-opencode は**単体ファイルのコピー**だったため agentcore を import できず、環境補完
# のコードを複製で持っていた（「直すときは 3 箇所を揃えること」）。
#
# ここでは 1 つの zipapp を作り、従来の 3 名をそれへの**ハードリンク**として置く。
# basename(argv[0]) でサブコマンドへ振り分けるので、`agent-aider …` の打ち方も出力も
# 従来どおりのまま、実装・版・配布は 1 つになる（「シムだけ古い」が構造的に起きない）。
# 設計: docs/plans/2026-08-25-agent-herd-unified-entry-design.md §3 / §6。
HERD_BUILD="$(mktemp -d "${TMPDIR:-/tmp}/agent-herd-build.XXXXXX")"
copy_py_tree "${AGENTCORE_PKG}" "${HERD_BUILD}/agentcore" -not -path './tests/*'
cat > "${HERD_BUILD}/__main__.py" <<'EOF'
from agentcore.herdcli import main
raise SystemExit(main())
EOF
RICH_NOTE=""
if [[ "${WITH_RICH}" -eq 1 ]]; then
  # 任意依存。zipimport で動く純 Python なので zipapp へそのまま同梱できる。
  # **失敗しても中断しない**——rich が無ければ TUI は素の ANSI 表示へ落ちるだけで、
  # 実行系としては何も欠けない（色が付かないことと動かないことを同じ重さで扱わない）。
  if "$PYTHON_CMD" -m pip install --quiet --target "${HERD_BUILD}" rich >/dev/null 2>&1; then
    RICH_NOTE="・rich 同梱"
  else
    warn "rich の取得に失敗しました。素の ANSI 表示のまま続けます（--with-rich は任意）"
  fi
fi
"$PYTHON_CMD" -m zipapp "${HERD_BUILD}" -o "${INSTALL_PREFIX}/agent-herd" \
  -p "/usr/bin/env ${PYTHON_CMD}"
chmod +x "${INSTALL_PREFIX}/agent-herd"
rm -rf "${HERD_BUILD}"
ok "インストールしました: ${INSTALL_PREFIX}/agent-herd（実行系の入口${RICH_NOTE}）"

# 従来の 3 名は同じ実体を指すハードリンク。**互換シムではなく本体そのもの**なので、
# 片方だけ古いという状態が作れない。ハードリンクが張れない FS（一部の Windows 共有・
# 別デバイス跨ぎ）ではコピーへ落とす——その場合だけは入れ直しで両方が更新される必要が
# あるが、このインストーラは常に 4 つ全部を書き直すので実害は無い。
for alias in agent-aider agent-ollama agent-opencode; do
  rm -f "${INSTALL_PREFIX}/${alias}"
  if ln "${INSTALL_PREFIX}/agent-herd" "${INSTALL_PREFIX}/${alias}" 2>/dev/null; then
    ok "インストールしました: ${INSTALL_PREFIX}/${alias}（agent-herd への別名）"
  else
    cp "${INSTALL_PREFIX}/agent-herd" "${INSTALL_PREFIX}/${alias}"
    chmod +x "${INSTALL_PREFIX}/${alias}"
    warn "${alias} はハードリンクにできなかったのでコピーしました（実体は同じ版）"
  fi
done

# 入口が起動することまで確かめる。ここで落ちるのは shebang / python の取り違えなので、
# 「入った」と言い切る前に踏んでおく。別名の 1 つも叩いて argv[0] 分岐を通す。
if "${INSTALL_PREFIX}/agent-herd" --help >/dev/null 2>&1 \
   && "${INSTALL_PREFIX}/agent-ollama" --help >/dev/null 2>&1; then
  ok "agent-herd --help と agent-ollama --help が正常に動作しました。"
else
  warn "agent-herd の起動確認に失敗しました。shebang / Python を確認してください。"
fi

# ---------------------------------------------------------------------------
# 3. エンジン固有の付帯物
# ---------------------------------------------------------------------------
installed() {
  case " ${ENGINES[*]} " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

# エージェント CLI 定義（agents/<name>.json）をユーザー共通の置き場へ配る。
# zipapp はリポジトリの agents/ を持ち出せない（同梱定義の解決は「リポジトリから直接動かす
# 開発環境」でしか効かない）ので、配布インストールではここで配らないと組み込み CLI すら
# 「未知の agent_cli」になる。探索順（agentcore.agentcli.plugin_dirs）の 3 番目に置くので、
# $KIRO_AGENTS_DIR とプロジェクトの agents/ に置いた定義が引き続き優先される。
AGENTS_SRC_DIR="$(cd "${TOOLS_DIR}/.." && pwd)/agents"
if [[ -d "${AGENTS_SRC_DIR}" ]]; then
  AGENTS_HOME="${AGENT_PROJECT_AGENTS_HOME:-${HOME}/.agents}"
  AGENTS_DEST_DIR="${AGENTS_HOME}/agents"
  mkdir -p "${AGENTS_DEST_DIR}"
  n=0
  for f in "${AGENTS_SRC_DIR}"/*.json; do
    [[ -e "$f" ]] || continue
    cp "$f" "${AGENTS_DEST_DIR}/"; n=$((n + 1))
  done
  if [[ "$n" -gt 0 ]]; then
    ok "エージェント CLI 定義を ${n} 件配置しました: ${AGENTS_DEST_DIR}"
    info "  この置き場は同梱定義の更新で上書きします。独自定義はプロジェクトの agents/ か \$KIRO_AGENTS_DIR へ置いてください。"
  fi
else
  warn "エージェント CLI 定義（agents/）が見つかりません: ${AGENTS_SRC_DIR}
  自己更新の sparse-checkout なら update_subdir に agents が含まれているか確認してください。"
fi

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

# agent-loop も agentcore を同梱する zipapp なので、ここから一緒に入れ直す。付帯物
# （tmux 前提・hooks・concurrency 定義）が多く手順も独立しているため、ビルドは複製せず
# 本人の install.sh へ委譲する。**失敗しても中断しない**——tmux が無い PC では
# agent-loop 側が die するが、それでエンジン 4 本のインストールまで巻き添えにしない。
# stdin を切るのは、向こうが pyyaml の導入を対話で聞く箇所を持つため（既定 Y で進む）。
AGENT_LOOP_INSTALLER="${TOOLS_DIR}/agent-loop/install.sh"
if [[ -f "${AGENT_LOOP_INSTALLER}" ]]; then
  if bash "${AGENT_LOOP_INSTALLER}" --prefix "${INSTALL_PREFIX}" </dev/null >/dev/null 2>&1; then
    ok "agent-loop も同梱インストールしました（agentcore を揃えるため常に入れ直します）"
  else
    warn "agent-loop のインストールに失敗しました（tmux の有無を確認してください）:
    bash ${AGENT_LOOP_INSTALLER}"
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
if installed agent-audit; then
  echo "  agent-audit（実行証跡・セッションログの収集と知見蒸留。単発実行）:"
  echo "    agent-audit collect                     # 源泉の増分収集（決定的）"
  echo "    agent-audit usage --period month        # トークン・コスト集計"
  echo "    agent-audit doctor                      # 源泉の到達性の点検"
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
