#!/usr/bin/env bash
# install.sh — agent-amigos インストーラー
# 使い方: bash install.sh [--prefix <dir>]
#
# デフォルトのインストール先: ~/.local/bin/agent-amigos
# agent-amigos は標準ライブラリのみ（pip 依存なし）。
#   - git は分散モード（--bus git+<url>）で必要（ローカル / hub モードでは不要）。
#   - PyYAML は YAML の役割ミッション表を使う場合のみ（JSON なら不要）。
#   - agent CLI（kiro/claude/copilot/codex/cursor…）は実運用に必要。無くても
#     --agent-cli stub でプロトコルを動かせる。
#
# 実体は agent_amigos/ パッケージ（LLM が編集できる大きさの断片へ分割済み）。
# 配布は agent-project / agent-flow と同じく zipapp で「パッケージ + ルート __main__.py」を
# 1 実行ファイルへまとめる。

set -euo pipefail

# ---------------------------------------------------------------------------
# カラー出力
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()   { error "$*"; exit 1; }

# ---------------------------------------------------------------------------
# 引数解析
# ---------------------------------------------------------------------------
INSTALL_PREFIX="${HOME}/.local/bin"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      INSTALL_PREFIX="$2"
      shift 2
      ;;
    --help|-h)
      echo "使い方: bash install.sh [--prefix <インストール先ディレクトリ>]"
      echo "  デフォルト: ~/.local/bin"
      exit 0
      ;;
    *)
      die "不明なオプション: $1"
      ;;
  esac
done

INSTALL_PATH="${INSTALL_PREFIX}/agent-amigos"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="${SCRIPT_DIR}/agent_amigos"

echo ""
echo "========================================"
echo "  agent-amigos インストーラー"
echo "========================================"
echo ""

# ---------------------------------------------------------------------------
# 1. 実行環境チェック（macOS / Linux / WSL）
# ---------------------------------------------------------------------------
info "実行環境を確認しています..."

OS="$(uname -s)"
case "$OS" in
  Darwin)
    ok "macOS 環境を検出しました。"
    ;;
  Linux)
    if grep -qi microsoft /proc/version 2>/dev/null; then
      ok "WSL 環境を検出しました。"
    else
      ok "Linux 環境を検出しました。"
    fi
    ;;
  *)
    die "サポートされていない OS です（検出: $OS）。macOS / Linux / WSL が必要です。"
    ;;
esac

# ---------------------------------------------------------------------------
# 2. python チェック（3.9 以上・標準ライブラリのみ使用）
# ---------------------------------------------------------------------------
info "python を確認しています..."

PYTHON_CMD=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PY_VER="$("$cmd" --version 2>&1)"
    PY_MAJOR="$("$cmd" -c 'import sys; print(sys.version_info.major)')"
    PY_MINOR="$("$cmd" -c 'import sys; print(sys.version_info.minor)')"
    if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 9 ]]; then
      PYTHON_CMD="$cmd"
      ok "$PY_VER が見つかりました: $(command -v "$cmd")"
      break
    else
      warn "$PY_VER は 3.9 未満のため除外します。"
    fi
  fi
done

if [[ -z "$PYTHON_CMD" ]]; then
  die "Python 3.9 以上が見つかりません。手動でインストールしてください。
  macOS:      brew install python3
  WSL/Ubuntu: sudo apt install python3
  参考: https://www.python.org/downloads/"
fi

# ---------------------------------------------------------------------------
# 3. git チェック（分散モード git+<url> で必須・ローカル/hub モードでは任意）
# ---------------------------------------------------------------------------
info "git を確認しています..."

if command -v git &>/dev/null; then
  ok "git が見つかりました: $(command -v git) ($(git --version 2>&1 | head -1))"
else
  warn "git が見つかりません。ローカルバス（--bus <dir>）と hub（--bus hub+<url>）は利用可能です。
  複数 PC 分散を専用 git バスリポジトリで行う（--bus git+<url>）には git が必要:
  macOS:      brew install git
  WSL/Ubuntu: sudo apt install git"
fi

# ---------------------------------------------------------------------------
# 4. エージェント CLI チェック（amigo の実行に使用・無くても stub で動作）
#    agents/<name>.json プラグイン契約（schemas/agent-cli.schema.json）で
#    kiro / claude / copilot / codex / cursor 等を切り替えられる。
# ---------------------------------------------------------------------------
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
  warn "エージェント CLI が見つかりません。stub モード（--agent-cli stub）でのみ動作します。
  実運用には agent_cli に対応した CLI が必要です（kiro / claude / copilot / codex）。
  それ以外は agents/<name>.json 定義で追加できます（契約: schemas/agent-cli.schema.json）。"
fi

# ---------------------------------------------------------------------------
# 5. PyYAML チェック（任意。YAML の役割ミッション表を使う場合のみ。JSON なら不要）
# ---------------------------------------------------------------------------
info "PyYAML を確認しています（任意）..."

if "$PYTHON_CMD" -c "import yaml" &>/dev/null 2>&1; then
  ok "PyYAML はインストール済みです（roles.yaml が使えます）。"
else
  warn "PyYAML が見つかりません（YAML の役割ミッション表を使う場合のみ必要）。
  pip install --user pyyaml で入れられます。JSON（roles.json）なら不要です。"
fi

# ---------------------------------------------------------------------------
# 6. zipapp でインストール（単一ファイル配布を維持・実体はパッケージ）
# ---------------------------------------------------------------------------
info "agent_amigos パッケージを zipapp にまとめてインストールしています..."

[[ -d "${PKG}" ]] || die "agent_amigos パッケージが見つかりません: ${PKG}"

mkdir -p "$INSTALL_PREFIX"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agent-amigos-build.XXXXXX")"
trap 'rm -rf "${BUILD_DIR}"' EXIT
mkdir -p "${BUILD_DIR}/agent_amigos"
# __pycache__ を除いてパッケージをコピー（zipapp に .pyc を含めない）。
( cd "${PKG}" && find . -name '*.py' -print0 | while IFS= read -r -d '' f; do
    mkdir -p "${BUILD_DIR}/agent_amigos/$(dirname "$f")"
    cp "$f" "${BUILD_DIR}/agent_amigos/$f"
  done )
cat > "${BUILD_DIR}/__main__.py" <<'EOF'
from agent_amigos import main

if __name__ == "__main__":
    raise SystemExit(main())
EOF

"$PYTHON_CMD" -m zipapp "${BUILD_DIR}" -o "${INSTALL_PATH}" -p "/usr/bin/env ${PYTHON_CMD}"
chmod +x "${INSTALL_PATH}"
ok "インストールしました: ${INSTALL_PATH}（zipapp）"

# ---------------------------------------------------------------------------
# 7. 動作確認
# ---------------------------------------------------------------------------
info "動作確認をしています..."

if "$INSTALL_PATH" --help >/dev/null 2>&1; then
  ok "agent-amigos --help が正常に動作しました。"
else
  warn "agent-amigos --help の実行に失敗しました。shebang / Python を確認してください。"
fi

# ---------------------------------------------------------------------------
# 8. PATH チェック
# ---------------------------------------------------------------------------
info "PATH を確認しています..."

if echo "$PATH" | tr ':' '\n' | grep -qF "$INSTALL_PREFIX"; then
  ok "$INSTALL_PREFIX は PATH に含まれています。"
else
  warn "$INSTALL_PREFIX が PATH に含まれていません。"
  echo ""
  echo "  以下を ~/.bashrc または ~/.zshrc に追加してください:"
  echo ""
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo ""
fi

# ---------------------------------------------------------------------------
# 完了
# ---------------------------------------------------------------------------
echo ""
echo "========================================"
ok "インストール完了！"
echo "========================================"
echo ""
echo "  1 ノードで試す（stub・LLM 不要）:"
echo "    agent-amigos init-bus --bus /tmp/amigos-bus"
echo "    agent-amigos post --bus /tmp/amigos-bus \\"
echo "      --design design-doc.md --roles ${SCRIPT_DIR}/roles.yaml.example \\"
echo "      --serve --agent-cli stub"
echo "    agent-amigos status  --bus /tmp/amigos-bus"
echo "    agent-amigos collect <mission-id> --bus /tmp/amigos-bus --out ./deliverable"
echo "    agent-amigos accept  <mission-id> --bus /tmp/amigos-bus"
echo ""
echo "  参加ノード（別 PC も可）:"
echo "    agent-amigos join --bus <bus> --tags python --agent-cli codex"
echo ""
echo "  複数 PC 分散（専用 git バスリポジトリ）:"
echo "    agent-amigos post --bus git+ssh://git@host/team/amigos-bus.git \\"
echo "      --design design-doc.md --roles roles.yaml --serve --agent-cli claude"
echo ""
echo "  git が使えない環境（オンプレ hub 中継）:"
echo "    AGENT_AMIGOS_HUB_TOKEN=secret agent-amigos hub --data /srv/amigos --port 8765"
echo "    AGENT_AMIGOS_HUB_TOKEN=secret agent-amigos join --bus hub+http://hub.local:8765"
echo ""
echo "  ノード予算（このマシンの実行時間上限。0 = 無制限）:"
echo "    agent-amigos budget node --limit-minutes 240 --period day"
echo ""
echo "  役割ミッション表の雛形: ${SCRIPT_DIR}/roles.yaml.example"
echo "  正典スキーマ: schemas/mission.schema.json ／ 設計: docs/designs/agent-amigos-design.md"
echo ""
echo "  開発時の編集は tools/agent-amigos/agent_amigos/*.py（断片）。配布は zipapp 単一ファイル。"
echo ""
