#!/usr/bin/env bash
# install.sh — opencode（+ 別 PC の ollama を推論エンジンにする構成）の独立インストーラ。
#
# agent-tools 一式（tools/agent-tools/install.sh）とは**別に単体で走る**。opencode は
# agent-project / agent-flow / agent-amigos / agent-audit のどれとも独立に入れ替わるし、
# 推論サーバ（別 PC の ollama）の住所も PC ごとに違うので、エンジンの配布物と同じ寿命に
# 縛らない。ここが入れるのは 3 つだけ:
#
#   1. opencode 本体（未導入なら公式スクリプト or npm）
#   2. ~/.config/opencode/opencode.json への ollama プロバイダ設定（既存設定は保ったままマージ）
#   3. agent-opencode（実測 usage と事前到達性チェックのアダプター）と agents/opencode.json
#
# 使い方:
#   bash tools/opencode/install.sh --ollama-host http://192.168.1.20:11434
#   bash tools/opencode/install.sh --ollama-host http://gpu-pc:11434 --models qwen3,qwen2.5-coder:14b
#   bash tools/opencode/install.sh --doctor          # 導入済み構成の点検だけ
#   bash tools/opencode/install.sh --print-config    # 生成する設定を表示するだけ（書かない）
#
# 前提: bash / curl / python3（標準ライブラリのみ）。

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()   { error "$*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

INSTALL_PREFIX="${HOME}/.local/bin"
OLLAMA_HOST_URL="${OLLAMA_HOST:-http://localhost:11434}"
PROVIDER_ID="ollama"
MODELS=""
DEFAULT_MODEL=""
SMALL_MODEL=""
CONTEXT_LIMIT="32768"
OUTPUT_LIMIT="8192"
CONFIG_PATH="${OPENCODE_CONFIG:-}"
AGENTS_DIR=""
METHOD="auto"
SKIP_AGENTS=0
PRINT_CONFIG=0
DOCTOR=0

usage() {
  cat <<'USAGE'
使い方: bash tools/opencode/install.sh [オプション]

  --ollama-host <url>     推論エンジン（ollama）の住所。別 PC なら http://<ホスト>:11434
                          （既定: $OLLAMA_HOST か http://localhost:11434）
  --models <a,b,...>      使うモデル。省略時は --ollama-host の /api/tags から自動取得
  --default-model <name>  既定モデル（省略時は --models の先頭）
  --small-model <name>    表題生成など軽い用途のモデル（既定: 既定モデルと同じ）
  --provider-id <id>      設定に書くプロバイダ ID（既定 ollama。model は <id>/<model> になる）
  --context <n>           モデルの入力上限の宣言（既定 32768）
  --max-output <n>        モデルの出力上限の宣言（既定 8192）
  --config <path>         opencode 設定ファイル（既定 $OPENCODE_CONFIG か
                          ~/.config/opencode/opencode.json）
  --prefix <dir>          agent-opencode の置き場（既定 ~/.local/bin）
  --agents-dir <dir>      CLI 定義の配布先（既定 ~/.agents/agents）
  --method <auto|script|npm|skip>
                          opencode 本体の入れ方（既定 auto: 未導入なら公式スクリプト→npm）
  --skip-agents           agents/opencode.json を配らない
  --print-config          生成する設定を表示するだけ（何も書かない）
  --doctor                導入済み構成の点検だけ（何も書かない）
  -h, --help              このヘルプ
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ollama-host) OLLAMA_HOST_URL="$2"; shift 2 ;;
    --models) MODELS="$2"; shift 2 ;;
    --default-model) DEFAULT_MODEL="$2"; shift 2 ;;
    --small-model) SMALL_MODEL="$2"; shift 2 ;;
    --provider-id) PROVIDER_ID="$2"; shift 2 ;;
    --context) CONTEXT_LIMIT="$2"; shift 2 ;;
    --max-output) OUTPUT_LIMIT="$2"; shift 2 ;;
    --config) CONFIG_PATH="$2"; shift 2 ;;
    --prefix) INSTALL_PREFIX="$2"; shift 2 ;;
    --agents-dir) AGENTS_DIR="$2"; shift 2 ;;
    --method) METHOD="$2"; shift 2 ;;
    --skip-agents) SKIP_AGENTS=1; shift ;;
    --print-config) PRINT_CONFIG=1; shift ;;
    --doctor) DOCTOR=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "不明な引数: $1" ;;
  esac
done

case "${METHOD}" in auto|script|npm|skip) : ;; *) die "不明な --method: ${METHOD}" ;; esac
[[ "${OLLAMA_HOST_URL}" == *"://"* ]] || OLLAMA_HOST_URL="http://${OLLAMA_HOST_URL}"
OLLAMA_HOST_URL="${OLLAMA_HOST_URL%/}"
BASE_URL="${OLLAMA_HOST_URL}/v1"

command -v python3 >/dev/null 2>&1 || die "python3 が必要です"
command -v curl >/dev/null 2>&1 || die "curl が必要です"

if [[ -z "${CONFIG_PATH}" ]]; then
  CONFIG_DIR="${OPENCODE_CONFIG_DIR:-${HOME}/.config/opencode}"
  CONFIG_PATH="${CONFIG_DIR}/opencode.json"
  # 既存が .jsonc ならそちらを更新する（2 つ置くと opencode がどちらを読むかで挙動が割れる）
  [[ -f "${CONFIG_PATH}" ]] || [[ ! -f "${CONFIG_DIR}/opencode.jsonc" ]] || \
    CONFIG_PATH="${CONFIG_DIR}/opencode.jsonc"
fi

# CLI 定義の配布先。agentcore._agents_home と同じ規則（agents サブディレクトリ単位で新旧判定）。
if [[ -z "${AGENTS_DIR}" ]]; then
  AGENTS_HOME="${AGENT_PROJECT_AGENTS_HOME:-}"
  if [[ -z "${AGENTS_HOME}" ]]; then
    if [[ ! -d "${HOME}/.agents/agents" && -d "${HOME}/.agent/agents" ]]; then
      AGENTS_HOME="${HOME}/.agent"
    else
      AGENTS_HOME="${HOME}/.agents"
    fi
  fi
  AGENTS_DIR="${AGENTS_HOME}/agents"
fi

# ---------------------------------------------------------------------------
# ollama への到達性とモデル一覧（別 PC が前提なので、届かないことを"静かに"通さない）
# ---------------------------------------------------------------------------
probe_ollama() {
  curl -fsS --max-time 5 "${OLLAMA_HOST_URL}/api/tags" 2>/dev/null
}

remote_models() {
  probe_ollama | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    raise SystemExit(0)
for m in data.get("models") or []:
    name = m.get("name") or m.get("model")
    if name:
        print(name)
' 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# doctor（点検のみ。何も書かない）
# ---------------------------------------------------------------------------
if [[ "${DOCTOR}" -eq 1 ]]; then
  echo ""
  echo "  opencode + ollama 構成の点検"
  echo ""
  rc=0
  if command -v opencode >/dev/null 2>&1; then
    ok "opencode: $(command -v opencode) ($(opencode --version 2>/dev/null | head -1))"
  else
    error "opencode が PATH にありません"; rc=1
  fi
  if command -v agent-opencode >/dev/null 2>&1; then
    ok "agent-opencode: $(command -v agent-opencode)"
  else
    error "agent-opencode が PATH にありません（agents/opencode.json はこれを呼びます）"; rc=1
  fi
  if [[ -f "${CONFIG_PATH}" ]]; then
    ok "設定: ${CONFIG_PATH}"
  else
    error "設定が見つかりません: ${CONFIG_PATH}"; rc=1
  fi
  if [[ -f "${AGENTS_DIR}/opencode.json" ]]; then
    ok "CLI 定義: ${AGENTS_DIR}/opencode.json"
  else
    warn "CLI 定義が配布先にありません: ${AGENTS_DIR}/opencode.json（リポジトリ直下から動かすなら不要）"
  fi
  if probe_ollama >/dev/null; then
    ok "ollama に到達しました: ${OLLAMA_HOST_URL}"
    remote_models | sed 's/^/        - /'
  else
    error "ollama に到達できません: ${OLLAMA_HOST_URL}
  推論する PC 側で OLLAMA_HOST=0.0.0.0 ollama serve が動いているか、11434 が開いているか"
    rc=1
  fi
  exit "${rc}"
fi

echo ""
echo "========================================"
echo "  opencode インストーラー（推論エンジン: ollama ${OLLAMA_HOST_URL}）"
echo "========================================"
echo ""

# ---------------------------------------------------------------------------
# 1. opencode 本体
# ---------------------------------------------------------------------------
install_opencode() {
  if command -v opencode >/dev/null 2>&1 && [[ "${METHOD}" == "auto" ]]; then
    ok "opencode は導入済みです: $(command -v opencode) ($(opencode --version 2>/dev/null | head -1))"
    return 0
  fi
  case "${METHOD}" in
    skip) info "opencode 本体のインストールは省きます（--method skip）"; return 0 ;;
    npm) install_via_npm ;;
    script) install_via_script ;;
    auto) install_via_script || install_via_npm || return 1 ;;
  esac
}

install_via_script() {
  info "公式インストールスクリプトで opencode を入れています..."
  if curl -fsSL --max-time 120 https://opencode.ai/install | bash; then
    ok "opencode をインストールしました（既定の置き場: ~/.opencode/bin）"
    export PATH="${HOME}/.opencode/bin:${PATH}"
    return 0
  fi
  warn "公式スクリプトでのインストールに失敗しました"
  return 1
}

install_via_npm() {
  command -v npm >/dev/null 2>&1 || { warn "npm が見つかりません"; return 1; }
  info "npm で opencode を入れています（opencode-ai）..."
  if npm install -g opencode-ai; then
    ok "opencode をインストールしました（npm -g）"
    return 0
  fi
  warn "npm でのインストールに失敗しました"
  return 1
}

if [[ "${PRINT_CONFIG}" -eq 0 ]]; then
  install_opencode || warn "opencode 本体を入れられませんでした。手動で入れてから再実行してください
  公式:  curl -fsSL https://opencode.ai/install | bash
  npm:   npm install -g opencode-ai"
fi

# ---------------------------------------------------------------------------
# 2. モデル一覧の決定（指定 > 自動取得）
# ---------------------------------------------------------------------------
if [[ -z "${MODELS}" ]]; then
  info "ollama からモデル一覧を取得しています: ${OLLAMA_HOST_URL}"
  FOUND="$(remote_models | paste -sd, -)"
  if [[ -n "${FOUND}" ]]; then
    MODELS="${FOUND}"
    ok "モデルを検出しました: ${MODELS}"
  else
    warn "ollama からモデル一覧を取得できませんでした（${OLLAMA_HOST_URL}）。
  推論する PC 側で OLLAMA_HOST=0.0.0.0 ollama serve が動いているか、11434 が開いているかを
  確認してください。いまは既定の qwen3 だけを設定に書きます（--models で明示もできます）。"
    MODELS="qwen3"
  fi
fi
[[ -n "${DEFAULT_MODEL}" ]] || DEFAULT_MODEL="${MODELS%%,*}"
[[ -n "${SMALL_MODEL}" ]] || SMALL_MODEL="${DEFAULT_MODEL}"

# ---------------------------------------------------------------------------
# 3. opencode 設定（既存を保ったままマージ）
# ---------------------------------------------------------------------------
# opencode の設定は**未知のキーを拒否する**（Unrecognized key で起動ごと落ちる）ので、
# ここが書くのは実機で通ることを確かめたキーだけにしてある:
#   provider.<id>.{npm,name,options.baseURL,models.<m>.{name,tool_call,limit}}
#   model / small_model / agent.plan.permission
CONFIG_JSON="$(CONFIG_PATH="${CONFIG_PATH}" PROVIDER_ID="${PROVIDER_ID}" BASE_URL="${BASE_URL}" \
  MODELS="${MODELS}" DEFAULT_MODEL="${DEFAULT_MODEL}" SMALL_MODEL="${SMALL_MODEL}" \
  CONTEXT_LIMIT="${CONTEXT_LIMIT}" OUTPUT_LIMIT="${OUTPUT_LIMIT}" HOST_URL="${OLLAMA_HOST_URL}" \
  python3 - <<'PY'
import json, os, re, sys

path = os.environ["CONFIG_PATH"]
provider_id = os.environ["PROVIDER_ID"]
models = [m.strip() for m in os.environ["MODELS"].split(",") if m.strip()]
default_model = os.environ["DEFAULT_MODEL"]
small_model = os.environ["SMALL_MODEL"]

comment_re = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)


def strip_jsonc(text):
    return re.sub(r",(\s*[}\]])", r"\1",
                  comment_re.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", text))


config = {}
if os.path.isfile(path):
    try:
        config = json.loads(strip_jsonc(open(path, encoding="utf-8").read())) or {}
    except ValueError as exc:
        print(f"既存の設定を JSON として読めません: {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(config, dict):
        print(f"既存の設定がオブジェクトではありません: {path}", file=sys.stderr)
        raise SystemExit(2)

config.setdefault("$schema", "https://opencode.ai/config.json")

provider = config.setdefault("provider", {})
if not isinstance(provider, dict):
    print("既存の provider がオブジェクトではありません", file=sys.stderr)
    raise SystemExit(2)
entry = provider.setdefault(provider_id, {})
entry["npm"] = "@ai-sdk/openai-compatible"
entry["name"] = f"Ollama ({os.environ['HOST_URL']})"
options = entry.setdefault("options", {})
options["baseURL"] = os.environ["BASE_URL"]
# models は**丸ごと置き換えない**。人が手で足したモデル宣言（limit の調整など）を消さない。
declared = entry.setdefault("models", {})
for m in models:
    slot = declared.setdefault(m, {})
    slot.setdefault("name", m)
    slot.setdefault("tool_call", True)
    slot.setdefault("limit", {"context": int(os.environ["CONTEXT_LIMIT"]),
                              "output": int(os.environ["OUTPUT_LIMIT"])})

# 既定モデルは「まだ決まっていないとき」だけ書く（人の選択を上書きしない）。
config.setdefault("model", f"{provider_id}/{default_model}")
config.setdefault("small_model", f"{provider_id}/{small_model}")

# 読み取り専用（agents/opencode.json の readonly_args = --agent plan）を実効化する。
# opencode 組み込みの plan は edit を拒むが bash は拒まないので、設定側で締める。
agent = config.setdefault("agent", {})
if isinstance(agent, dict):
    plan = agent.setdefault("plan", {})
    if isinstance(plan, dict):
        permission = plan.setdefault("permission", {})
        if isinstance(permission, dict):
            for key in ("edit", "bash", "webfetch"):
                permission.setdefault(key, "deny")

print(json.dumps(config, indent=2, ensure_ascii=False))
PY
)" || die "設定のマージに失敗しました（${CONFIG_PATH} を確認してください）"

if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
  echo "${CONFIG_JSON}"
  exit 0
fi

mkdir -p "$(dirname "${CONFIG_PATH}")"
if [[ -f "${CONFIG_PATH}" ]]; then
  cp "${CONFIG_PATH}" "${CONFIG_PATH}.bak"
  info "既存の設定を退避しました: ${CONFIG_PATH}.bak"
fi
printf '%s\n' "${CONFIG_JSON}" > "${CONFIG_PATH}"
ok "設定を書きました: ${CONFIG_PATH}（provider ${PROVIDER_ID} → ${BASE_URL}）"

# ---------------------------------------------------------------------------
# 4. agent-opencode（アダプター）
# ---------------------------------------------------------------------------
# 実装は agentcore.opencode_adapter にある（環境補完 agentcore.hostenv を 3 adapter で
# 共有するため、単体ファイルのコピー配布はやめた）。ここは agentcore を同梱した zipapp を
# 組む——このインストーラは agent-tools 一式とは独立に走るので、自己完結していないと
# 「opencode だけ入れた PC で agentcore が無い」が起きる。
# agent-tools 側の install.sh は同じ実体を agent-herd の別名として置く。どちらを走らせても
# 同じ実装が入り、後から走らせた方が上書きする。
AGENTCORE_PKG="${REPO_ROOT}/tools/agent-tools/agentcore/agentcore"
[[ -d "${AGENTCORE_PKG}" ]] || die "agentcore パッケージが見つかりません: ${AGENTCORE_PKG}"
mkdir -p "${INSTALL_PREFIX}"
OPENCODE_BUILD="$(mktemp -d "${TMPDIR:-/tmp}/agent-opencode-build.XXXXXX")"
( cd "${AGENTCORE_PKG}" && find . -name '*.py' -not -path './tests/*' -print0 \
    | while IFS= read -r -d '' f; do
        mkdir -p "${OPENCODE_BUILD}/agentcore/$(dirname "$f")"
        cp "$f" "${OPENCODE_BUILD}/agentcore/$f"
      done )
cat > "${OPENCODE_BUILD}/__main__.py" <<'PYMAIN'
from agentcore.opencode_adapter import main
raise SystemExit(main())
PYMAIN
python3 -m zipapp "${OPENCODE_BUILD}" -o "${INSTALL_PREFIX}/agent-opencode" \
  -p "/usr/bin/env python3"
chmod +x "${INSTALL_PREFIX}/agent-opencode"
rm -rf "${OPENCODE_BUILD}"
ok "インストールしました: ${INSTALL_PREFIX}/agent-opencode（実測 usage・事前到達性チェック）"

# ---------------------------------------------------------------------------
# 5. CLI 定義（agents/opencode.json）
# ---------------------------------------------------------------------------
# agent-tools 側の install.sh も同じ場所へ agents/*.json を配る（同梱定義の更新で上書きする
# 置き場）。同じ内容を配るので、どちらを先に走らせても結果は変わらない。
if [[ "${SKIP_AGENTS}" -eq 0 ]]; then
  DEF_SRC="${REPO_ROOT}/agents/opencode.json"
  if [[ -f "${DEF_SRC}" ]]; then
    mkdir -p "${AGENTS_DIR}"
    cp "${DEF_SRC}" "${AGENTS_DIR}/opencode.json"
    ok "CLI 定義を配置しました: ${AGENTS_DIR}/opencode.json"
  else
    warn "CLI 定義が見つかりません: ${DEF_SRC}（リポジトリの agents/ ごと取得しているか確認してください）"
  fi
fi

case ":${PATH}:" in
  *":${INSTALL_PREFIX}:"*) ok "${INSTALL_PREFIX} は PATH に含まれています。" ;;
  *) warn "${INSTALL_PREFIX} が PATH にありません。シェル設定に追加してください:
    export PATH=\"${INSTALL_PREFIX}:\$PATH\"" ;;
esac

# ---------------------------------------------------------------------------
# 6. 導通確認
# ---------------------------------------------------------------------------
if PATH="${INSTALL_PREFIX}:${PATH}" OPENCODE_CONFIG="${CONFIG_PATH}" \
   agent-opencode --check </dev/null; then
  ok "推論サーバへの到達を確認しました。"
else
  warn "推論サーバへ到達できませんでした。ollama 側を起動してから
    agent-opencode --check
  で確かめてください（設定は書き込み済みです）。"
fi

echo ""
echo "========================================"
ok "インストール完了！"
echo "========================================"
echo ""
echo "  単体で使う:"
echo "    opencode                                  # TUI"
echo "    echo '要件を要約して' | agent-opencode    # ヘッドレス（実測 usage 付き）"
echo "    agent-opencode --check                    # 推論サーバの到達性だけ確認"
echo ""
echo "  agent-tools シリーズから使う（設定に agent_cli: opencode）:"
echo "    agent-flow:    agent_cli: opencode / model: ${PROVIDER_ID}/${DEFAULT_MODEL}"
echo "    agent-project: defaults.agent_cli: opencode"
echo "    agent-audit:   agents: {extract: {agent_cli: opencode, model: ${PROVIDER_ID}/${DEFAULT_MODEL}}}"
echo "    agent-amigos:  agent_cli: opencode（roles.yaml の役割毎にも指定できます）"
echo ""
echo "  点検: bash tools/opencode/install.sh --doctor --ollama-host ${OLLAMA_HOST_URL}"
echo "  手引: tools/opencode/README.md"
echo ""
