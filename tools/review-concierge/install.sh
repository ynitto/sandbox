#!/usr/bin/env bash
# install.sh — review-concierge インストーラー
# 使い方: bash install.sh [--prefix <dir>]
#   デフォルトのインストール先: ~/.local/bin/review-concierge

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()   { error "$*"; exit 1; }

INSTALL_PREFIX="${HOME}/.local/bin"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) INSTALL_PREFIX="$2"; shift 2 ;;
    --help|-h)
      echo "使い方: bash install.sh [--prefix <インストール先ディレクトリ>]"
      echo "  デフォルト: ~/.local/bin"
      exit 0 ;;
    *) die "不明な引数: $1" ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${SCRIPT_DIR}/concierge.py"
[[ -f "$SRC" ]] || die "concierge.py が見つかりません: $SRC"

command -v python3 >/dev/null 2>&1 || die "python3 が必要です"
info "依存チェック (PyYAML 推奨, 無ければ JSON 設定で代替可)"
python3 -c "import yaml" 2>/dev/null && ok "PyYAML 検出" || warn "PyYAML 未検出 — review-concierge.json を使うか pip install pyyaml"

info "オフライン自己テストを実行"
python3 "$SRC" selftest >/dev/null && ok "selftest PASS" || die "selftest 失敗"

mkdir -p "$INSTALL_PREFIX"
TARGET="${INSTALL_PREFIX}/review-concierge"
cat > "$TARGET" <<EOF
#!/usr/bin/env bash
exec python3 "${SRC}" "\$@"
EOF
chmod +x "$TARGET"
ok "インストール完了: $TARGET"

case ":${PATH}:" in
  *":${INSTALL_PREFIX}:"*) : ;;
  *) warn "PATH に ${INSTALL_PREFIX} が含まれていません。シェル設定に追加してください。" ;;
esac

cat <<'EOS'

次の手順:
  1. cp review-concierge.yaml.example ~/review-concierge.yaml && 値を編集
  2. 単発:   review-concierge scan      --config ~/review-concierge.yaml
  3. 常駐:   review-concierge watch     --config ~/review-concierge.yaml
  4. 反映:   review-concierge writeback --config ~/review-concierge.yaml
EOS
