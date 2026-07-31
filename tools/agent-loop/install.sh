#!/usr/bin/env bash
# install.sh — agent-loop インストーラー
# 使い方: bash install.sh [--prefix <dir>]
#
# デフォルトのインストール先: ~/.local/bin/agent-loop

set -euo pipefail

# ---------------------------------------------------------------------------
# カラー出力
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

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

INSTALL_PATH="${INSTALL_PREFIX}/agent-loop"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "========================================"
echo "  agent-loop インストーラー"
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
# 2. tmux チェック
# ---------------------------------------------------------------------------
info "tmux を確認しています..."

if command -v tmux &>/dev/null; then
  TMUX_VER="$(tmux -V 2>&1 | head -1)"
  ok "tmux が見つかりました: $(command -v tmux) ($TMUX_VER)"
else
  die "tmux が見つかりません。インストールしてください。
  macOS:      brew install tmux
  Ubuntu/WSL: sudo apt install tmux"
fi

# ---------------------------------------------------------------------------
# 3. python チェック
# ---------------------------------------------------------------------------
info "python を確認しています..."

PYTHON_CMD=""
for cmd in python3 python; do  # python3 を優先（python は環境により未存在・別バージョンのため）
  if command -v "$cmd" &>/dev/null; then
    PY_VER="$("$cmd" --version 2>&1)"
    # バージョン番号を抽出して 3.9 以上か確認
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
# 4. pip チェック
# ---------------------------------------------------------------------------
info "pip を確認しています..."

PIP_CMD=""
for cmd in pip pip3; do
  if "$PYTHON_CMD" -m pip --version &>/dev/null 2>&1; then
    PIP_CMD="$PYTHON_CMD -m pip"
    ok "pip が使用可能です。"
    break
  elif command -v "$cmd" &>/dev/null; then
    PIP_CMD="$cmd"
    ok "pip が見つかりました: $(command -v "$cmd")"
    break
  fi
done

if [[ -z "$PIP_CMD" ]]; then
  die "pip が見つかりません。インストールしてください。
  macOS:      brew install python3  # pip3 が同梱されます
  WSL/Ubuntu: sudo apt install python3-pip"
fi

# ---------------------------------------------------------------------------
# 5. kiro-cli チェック
# ---------------------------------------------------------------------------
info "kiro-cli を確認しています..."

if ! command -v kiro-cli &>/dev/null; then
  die "kiro-cli が見つかりません。先に手動でインストールしてください。
  参考: https://kiro.dev/docs/installation"
fi

KIRO_VER="$(kiro-cli --version 2>&1 | head -1 || echo '(バージョン取得失敗)')"
ok "kiro-cli が見つかりました: $(command -v kiro-cli) ($KIRO_VER)"

# ---------------------------------------------------------------------------
# 6. Python 依存ライブラリのインストール（PyYAML は任意）
# ---------------------------------------------------------------------------
info "Python 依存ライブラリを確認・インストールしています..."

if "$PYTHON_CMD" -c "import yaml" &>/dev/null 2>&1; then
  ok "pyyaml はインストール済みです。"
else
  warn "pyyaml が見つかりません（YAML 設定ファイルを使う場合は必要）。"
  read -r -p "  pyyaml をインストールしますか？ [Y/n] " yn
  case "${yn:-Y}" in
    [Yy]*)
      info "pyyaml をインストールしています..."
      if $PIP_CMD install --user pyyaml; then
        ok "pyyaml のインストールが完了しました。"
      else
        warn "pyyaml のインストールに失敗しました。JSON 設定ファイルを使う場合は不要です。"
      fi
      ;;
    *)
      warn "pyyaml をスキップしました。JSON 形式の設定ファイルを使用してください。"
      ;;
  esac
fi

# ---------------------------------------------------------------------------
# 7. スクリプトのインストール（zipapp: agent_loop パッケージ）
# ---------------------------------------------------------------------------
info "agent_loop パッケージを zipapp にまとめてインストールしています..."

PKG="${SCRIPT_DIR}/agent_loop"
[[ -d "${PKG}" ]] || die "agent_loop パッケージが見つかりません: ${PKG}"

mkdir -p "$INSTALL_PREFIX"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agent-loop-build.XXXXXX")"
# NOTE: trap は後続でもう一度設定しない（上書き防止）。既存 trap が無い前提で設定。
trap 'rm -rf "${BUILD_DIR}"' EXIT
mkdir -p "${BUILD_DIR}/agent_loop"
( cd "${PKG}" && find . -name '*.py' -print0 | while IFS= read -r -d '' f; do
    mkdir -p "${BUILD_DIR}/agent_loop/$(dirname "$f")"
    cp "$f" "${BUILD_DIR}/agent_loop/$f"
  done )
cat > "${BUILD_DIR}/__main__.py" <<'EOF'
from agent_loop import main

if __name__ == "__main__":
    raise SystemExit(main())
EOF

"$PYTHON_CMD" -m zipapp "${BUILD_DIR}" -o "${INSTALL_PATH}" -p "/usr/bin/env ${PYTHON_CMD}"
chmod +x "${INSTALL_PATH}"
ok "インストールしました: ${INSTALL_PATH}（zipapp）"

# 付属の agent-send も同じ prefix へ（単一ファイル）
SEND_SRC="${SCRIPT_DIR}/agent-send.py"
if [[ -f "$SEND_SRC" ]]; then
  SEND_DEST="${INSTALL_PREFIX}/agent-send"
  cp "$SEND_SRC" "$SEND_DEST"
  chmod +x "$SEND_DEST"
  if [[ "$OS" == "Darwin" ]]; then
    sed -i '' "1s|.*|#!/usr/bin/env ${PYTHON_CMD}|" "$SEND_DEST"
  else
    sed -i "1s|.*|#!/usr/bin/env ${PYTHON_CMD}|" "$SEND_DEST"
  fi
  ok "agent-send もインストールしました: $SEND_DEST"
fi

# ---------------------------------------------------------------------------
# 8. 同時実行数制御用ファイルのインストール
# ---------------------------------------------------------------------------
info "同時実行数制御用ファイルをインストールしています..."

# kiro-cli が探索するエージェントホームへ配置（共有 ~/.kiro/agents）
KIRO_AGENTS_DIR="${KIRO_AGENTS_DIR:-$HOME/.kiro/agents}"
mkdir -p "$KIRO_AGENTS_DIR"

CONCURRENCY_AGENT_FILE="$KIRO_AGENTS_DIR/agent-loop-concurrency.json"
cat > "$CONCURRENCY_AGENT_FILE" << 'EOF'
{
  "name": "agent-loop-concurrency",
  "description": "agent-loop 並列実行制御用エージェント（自動生成 — 手動で編集しないでください）",
  "hooks": {
    "stop": [
      {
        "type": "command",
        "command": "agent-loop slot-release"
      }
    ]
  },
  "resources": [
    "skill://~/.kiro/skills/**/SKILL.md",
    "skill://.kiro/skills/**/SKILL.md",
    "skill://~/.agent/skills/**/SKILL.md",
    "skill://.agent/skills/**/SKILL.md"
  ],
  "tools": ["*"]
}
EOF
ok "エージェント設定を作成しました: $CONCURRENCY_AGENT_FILE"

# ---------------------------------------------------------------------------
# 9. PATH チェック
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
echo "  使い方:"
echo "    cd ~/projects/my-app"
echo "    agent-loop                                      # デーモンモードで起動"
echo "    agent-loop ls                                   # kiro 関連セッションを一覧表示"
echo "    agent-loop send 'コードをレビューして'           # プロンプトを送信"
echo "    agent-loop send task.md                         # ファイル内容を読んで実行"
echo "    agent-loop send 'MR コメント返答'               # 定期プロンプト名で送信"
echo "    agent-loop send -s SESSION 'プロンプト'         # 指定セッションに送信"
echo ""
echo "  デーモン起動後のコマンド例:"
echo "    > status                                        # 状態表示"
echo "    > prompt-list                                   # 定期プロンプト一覧"
echo "    > help                                          # コマンド一覧"
echo ""
echo "  tmux セッション名の確認:"
echo "    agent-loop ls                                    # kiro 関連セッション一覧"
echo "    tmux list-sessions                              # 全セッション一覧"
echo ""
