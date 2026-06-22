#!/usr/bin/env bash
# install.sh — kiro-flow インストーラー
# 使い方: bash install.sh [--prefix <dir>]
#
# デフォルトのインストール先: ~/.local/bin/kiro-flow
# kiro-flow は標準ライブラリのみ（pip 依存なし）。git は分散モードで必要。

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

INSTALL_PATH="${INSTALL_PREFIX}/kiro-flow"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "========================================"
echo "  kiro-flow インストーラー"
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
# 3. git チェック（分散モードで必須・ローカルモードでは任意）
# ---------------------------------------------------------------------------
info "git を確認しています..."

if command -v git &>/dev/null; then
  ok "git が見つかりました: $(command -v git) ($(git --version 2>&1 | head -1))"
else
  warn "git が見つかりません。ローカルバスのみ利用可能です。
  複数 PC 分散（--git）を使うには git が必要:
  macOS:      brew install git
  WSL/Ubuntu: sudo apt install git"
fi

# ---------------------------------------------------------------------------
# 4. kiro-cli チェック（実行・計画に使用・無くても stub モードで動作）
# ---------------------------------------------------------------------------
info "kiro-cli を確認しています..."

if command -v kiro-cli &>/dev/null; then
  KIRO_VER="$(kiro-cli --version 2>&1 | head -1 || echo '(バージョン取得失敗)')"
  ok "kiro-cli が見つかりました: $(command -v kiro-cli) ($KIRO_VER)"
else
  warn "kiro-cli が見つかりません。stub モード（--planner stub --executor stub）でのみ動作します。
  実運用には kiro-cli が必要です。参考: https://kiro.dev/docs/installation"
fi

# ---------------------------------------------------------------------------
# 5. PyYAML チェック（任意。YAML 設定を使う場合のみ。JSON 設定なら不要）
# ---------------------------------------------------------------------------
info "PyYAML を確認しています（任意）..."

if "$PYTHON_CMD" -c "import yaml" &>/dev/null 2>&1; then
  ok "PyYAML はインストール済みです（kiro-flow.yaml が使えます）。"
else
  warn "PyYAML が見つかりません（YAML 設定ファイルを使う場合のみ必要）。"
  read -r -p "  PyYAML をインストールしますか？ [y/N] " yn
  case "${yn:-N}" in
    [Yy]*)
      if "$PYTHON_CMD" -m pip install --user pyyaml; then
        ok "PyYAML のインストールが完了しました。"
      else
        warn "PyYAML のインストールに失敗しました。JSON 設定ファイル（kiro-flow.json）を使えば不要です。"
      fi
      ;;
    *)
      warn "PyYAML をスキップしました。設定ファイルは JSON（kiro-flow.json）形式を使用してください。"
      ;;
  esac
fi

# ---------------------------------------------------------------------------
# 6. スクリプトのインストール
# ---------------------------------------------------------------------------
info "kiro-flow.py をインストールしています..."

SRC="${SCRIPT_DIR}/kiro-flow.py"
[[ -f "$SRC" ]] || die "kiro-flow.py が見つかりません: $SRC"

mkdir -p "$INSTALL_PREFIX"
cp "$SRC" "$INSTALL_PATH"
chmod +x "$INSTALL_PATH"

# shebang を環境の python コマンドに書き換える（BSD sed と GNU sed の差異を吸収）
if [[ "$OS" == "Darwin" ]]; then
  sed -i '' "1s|.*|#!/usr/bin/env ${PYTHON_CMD}|" "$INSTALL_PATH"
else
  sed -i "1s|.*|#!/usr/bin/env ${PYTHON_CMD}|" "$INSTALL_PATH"
fi

ok "インストールしました: $INSTALL_PATH"

# ---------------------------------------------------------------------------
# 6.5 executor プラグインのインストール
# ---------------------------------------------------------------------------
# kiro-loop の hooks と同じ流儀で、executor をプラグイン（executors/<name>.py）として
# 管理する。本体は単一ファイルで配布されるため、同梱プラグインを ~/.kiro/kiro-flow/
# executors/ に配置し、`--executor <name>` がインストール後も名前で解決できるようにする。
info "executor プラグインをインストールしています..."

EXEC_SRC_DIR="${SCRIPT_DIR}/executors"
EXEC_DEST_DIR="${HOME}/.kiro/kiro-flow/executors"

if [[ -d "$EXEC_SRC_DIR" ]]; then
  mkdir -p "$EXEC_DEST_DIR"
  installed=0
  for f in "$EXEC_SRC_DIR"/*.py; do
    [[ -e "$f" ]] || continue
    cp "$f" "$EXEC_DEST_DIR/"
    installed=$((installed + 1))
  done
  if [[ "$installed" -gt 0 ]]; then
    ok "executor プラグインを ${installed} 件配置しました: $EXEC_DEST_DIR"
    info "  例: kiro-flow run \"<要求>\" --executor gitlab   # opt-in の GitLab ワーカーバス"
  else
    warn "executor プラグインが見つかりませんでした（$EXEC_SRC_DIR）。"
  fi
else
  warn "executors/ ディレクトリが見つかりません（$EXEC_SRC_DIR）。プラグインはスキップします。"
fi

# ---------------------------------------------------------------------------
# 7. 動作確認
# ---------------------------------------------------------------------------
info "動作確認をしています..."

if "$INSTALL_PATH" --help >/dev/null 2>&1; then
  ok "kiro-flow --help が正常に動作しました。"
else
  warn "kiro-flow --help の実行に失敗しました。shebang / Python を確認してください。"
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
echo "  単発実行（既存 run-id なら自動で再開）:"
echo "    kiro-flow run \"要件整理; API設計; テスト\" --workers 3"
echo "    kiro-flow run --run-id <run-id>            # 中断した run を再開"
echo ""
echo "  デーモン（オンデマンド起動・推奨）:"
echo "    kiro-flow daemon --max-workers 4 &         # 常駐"
echo "    RID=\$(kiro-flow submit \"<要求>\")           # 要求を投入"
echo "    kiro-flow status --run-id \$RID --follow    # ライブ監視"
echo ""
echo "  分散（各 PC で同じ --git を指すだけ）:"
echo "    kiro-flow --git <repo-url> daemon --max-workers 4 &"
echo "    kiro-flow --git <repo-url> submit \"<要求>\""
echo ""
echo "  その他:"
echo "    kiro-flow status --run-id <run-id>         # 状態を 1 回表示"
echo "    kiro-flow gc --older-than 7 --status done  # 古い run を掃除"
echo ""
echo "  kiro-cli 無しで動作確認:"
echo "    kiro-flow run \"a; b; c\" --planner stub --executor stub"
echo ""
echo "  環境ごとの設定（bus/git/planner/max_workers 等）はファイル化できます:"
echo "    cp ${SCRIPT_DIR}/kiro-flow.yaml.example ~/.kiro/kiro-flow.yaml   # 自動検出される"
echo "    # CLI 引数 > 設定ファイル > 既定。--config で明示指定も可"
echo ""
