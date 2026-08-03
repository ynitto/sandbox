#!/usr/bin/env bash
# install.sh — gitlab-repo-sync を単体コマンドとして入れるインストーラ。
#
# 使い方:
#   bash tools/gitlab-repo-sync/install.sh
#   bash tools/gitlab-repo-sync/install.sh --prefix /usr/local/bin
#   bash tools/gitlab-repo-sync/install.sh --no-config          # 設定の雛形を置かない
#   bash tools/gitlab-repo-sync/install.sh --config-dest ~/etc/sync.yaml
#
# agent-tools と同じく zipapp 単一ファイルとして配る（構成は tools/agent-tools/install.sh
# に倣った）。ただし **agent-* とは何も共有しない独立コマンド**で、agentcore にも
# 依存しない。片方だけ入れ替えても、もう片方には影響しない。
#
# 前提:
#   - git          … 必須（同期そのものが git の呼び出しでできている）
#   - python 3.9+  … 必須（標準ライブラリのみ）
#   - PyYAML       … YAML 設定を使う場合のみ。JSON 設定なら不要

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()   { error "$*"; exit 1; }

COMMAND_NAME="gitlab-repo-sync"
PACKAGE_NAME="gitlab_repo_sync"
INSTALL_PREFIX="${HOME}/.local/bin"
CONFIG_DEST="${HOME}/gitlab-repo-sync.yaml"
WITH_CONFIG=1

usage() {
  cat <<'USAGE'
使い方: bash tools/gitlab-repo-sync/install.sh [オプション]

  --prefix <dir>        インストール先（既定 ~/.local/bin）
  --config-dest <path>  設定の雛形の置き場（既定 ~/gitlab-repo-sync.yaml）
  --no-config           設定の雛形を置かない
  -h, --help            このヘルプ

インストール後の実行タイミングは外部に任せます（cron / systemd timer /
Windows タスクスケジューラ）。最後に例を表示します。
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) INSTALL_PREFIX="$2"; shift 2 ;;
    --config-dest) CONFIG_DEST="$2"; shift 2 ;;
    --no-config) WITH_CONFIG=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "不明な引数: $1" ;;
  esac
done

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="${SRC_DIR}/${PACKAGE_NAME}"

echo ""
echo "========================================"
echo "  ${COMMAND_NAME} インストーラー"
echo "========================================"
echo ""

# ---------------------------------------------------------------------------
# 1. 環境チェック
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
[[ -n "$PYTHON_CMD" ]] || die "Python 3.9 以上が見つかりません。
  WSL/Ubuntu: sudo apt install python3
  macOS:      brew install python3"

# git はこのツールの実体。無ければ入れても一度も動かないので、警告ではなく停止する。
info "git を確認しています..."
command -v git &>/dev/null \
  || die "git が見つかりません。同期は git の呼び出しでできているので必須です。
  WSL/Ubuntu: sudo apt install git
  macOS:      brew install git"
ok "git が見つかりました: $(command -v git) ($(git --version 2>&1 | head -1))"

info "PyYAML を確認しています（任意）..."
if "$PYTHON_CMD" -c "import yaml" &>/dev/null; then
  ok "PyYAML はインストール済みです（*.yaml 設定が使えます）。"
else
  warn "PyYAML が見つかりません。YAML 設定を使うなら入れてください:
    pip install --user pyyaml
  JSON 設定（gitlab-repo-sync.json）なら無くても動きます。"
fi

# ---------------------------------------------------------------------------
# 2. zipapp ビルド
# ---------------------------------------------------------------------------
[[ -d "${PKG_DIR}" ]] || die "パッケージが見つかりません: ${PKG_DIR}"
mkdir -p "${INSTALL_PREFIX}"
DEST="${INSTALL_PREFIX}/${COMMAND_NAME}"

info "${COMMAND_NAME} を zipapp にまとめています..."
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/${COMMAND_NAME}-build.XXXXXX")"
trap 'rm -rf "${BUILD_DIR}"' EXIT

# __pycache__ / .pyc は配布物に持ち込まない（ビルド環境の痕跡を混ぜない）。
mkdir -p "${BUILD_DIR}/${PACKAGE_NAME}"
( cd "${PKG_DIR}" && find . -name '*.py' -print0 | while IFS= read -r -d '' f; do
    mkdir -p "${BUILD_DIR}/${PACKAGE_NAME}/$(dirname "$f")"
    cp "$f" "${BUILD_DIR}/${PACKAGE_NAME}/$f"
  done )

cat > "${BUILD_DIR}/__main__.py" <<EOF
from ${PACKAGE_NAME} import main

if __name__ == "__main__":
    raise SystemExit(main())
EOF

"$PYTHON_CMD" -m zipapp "${BUILD_DIR}" -o "${DEST}" -p "/usr/bin/env ${PYTHON_CMD}"
chmod +x "${DEST}"
ok "インストールしました: ${DEST}（zipapp）"

# 入った直後に一度起動しておく。ここで落ちるのは shebang / python の取り違えなので、
# 人が最初の cron 実行で初めて気づくより先に踏んでおく。
if "${DEST}" --version >/dev/null 2>&1; then
  ok "$("${DEST}" --version) が正常に起動しました。"
else
  warn "${COMMAND_NAME} --version の実行に失敗しました。shebang / Python を確認してください。"
fi

# ---------------------------------------------------------------------------
# 3. 設定の雛形
# ---------------------------------------------------------------------------
EXAMPLE="${SRC_DIR}/config.yaml.example"
if [[ "${WITH_CONFIG}" -eq 1 && -f "${EXAMPLE}" ]]; then
  if [[ -e "${CONFIG_DEST}" ]]; then
    info "設定は既にあります（上書きしません）: ${CONFIG_DEST}"
  else
    mkdir -p "$(dirname "${CONFIG_DEST}")"
    cp "${EXAMPLE}" "${CONFIG_DEST}"
    # PAT を書き込む先なので、他ユーザーから読めないようにしておく。
    chmod 600 "${CONFIG_DEST}"
    ok "設定の雛形を置きました: ${CONFIG_DEST}（パーミッション 600）"
  fi
fi

case ":${PATH}:" in
  *":${INSTALL_PREFIX}:"*) ok "${INSTALL_PREFIX} は PATH に含まれています。" ;;
  *) warn "${INSTALL_PREFIX} が PATH にありません。シェル設定に追加してください:
    export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# ---------------------------------------------------------------------------
# 4. 次の一手
# ---------------------------------------------------------------------------
echo ""
echo "========================================"
ok "インストール完了！"
echo "========================================"
echo ""
echo "  1) 設定を編集する（同期したいリポジトリの組・ルール・PAT）"
echo "     \$EDITOR ${CONFIG_DEST}"
echo ""
echo "  2) 何が起きるか確認する（リモートを変えません）"
echo "     ${COMMAND_NAME} list"
echo "     ${COMMAND_NAME} sync --dry-run"
echo ""
echo "  3) 実行タイミングを外部から与える。このコマンド自身はスケジュールを持ちません。"
echo ""
echo "     cron（毎日 2:30）:"
echo "       30 2 * * * ${DEST} sync --quiet"
echo ""
echo "     systemd --user timer:"
echo "       ExecStart=${DEST} sync --quiet"
echo ""
echo "     Windows タスクスケジューラから WSL 越しに:"
echo "       wsl.exe -d <distro> -- ${DEST} sync --quiet"
echo ""
echo "  状態の確認: ${COMMAND_NAME} status"
echo ""
