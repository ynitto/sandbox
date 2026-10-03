#!/usr/bin/env bash
# webui-test のインストーラ（Linux / macOS / WSL）。
#
#   ./install.sh                 webui-test をグローバルに入れてコマンドで使えるようにする
#   ./install.sh --with-deps     Chromium が必要とする OS のライブラリも入れる（sudo を使う）
#   ./install.sh --skip-browser  ブラウザを入れない（社内ミラーから別に入れるときなど）
#   ./install.sh --check         入れたあと、同梱のサンプルでテストを 1 回動かして確かめる
#
# 前提: Node.js 18 以上（無い・古いときはエラーで止まる。Node.js は入れない）
# 入れるもの:
#   - webui-test をグローバルに npm install -g（依存の playwright・@playwright/test・@playwright/cli・yaml もこのフォルダに入る）
#   - Playwright の Chromium
set -euo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${WEBUI_TEST_HOME:-$HOME/.local/share/webui-test}"
WITH_DEPS=0
SKIP_BROWSER=0
CHECK=0

for arg in "$@"; do
  case "$arg" in
    --with-deps) WITH_DEPS=1 ;;
    --skip-browser) SKIP_BROWSER=1 ;;
    --check) CHECK=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "知らない指定: $arg（--help で使い方）" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m注意:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31mエラー:\033[0m %s\n' "$*" >&2; exit 1; }

node_ok() {
  command -v "$1" >/dev/null 2>&1 || return 1
  local major
  major="$("$1" -p 'process.versions.node.split(".")[0]' 2>/dev/null)" || return 1
  [ "${major:-0}" -ge 18 ]
}

# 1. Node.js（確認だけ）
command -v node >/dev/null 2>&1 || die "Node.js が見つかりません。https://nodejs.org/ から 18 以上を入れてください"
node_ok node || die "Node.js が古いです（$(node --version)）。18 以上にしてください"
command -v npm >/dev/null 2>&1 || die "npm が見つかりません（Node.js と一緒に入れてください）"
NODE="$(command -v node)"
NPM="$(command -v npm)"
NPX="$(command -v npx || true)"
[ -n "$NPX" ] || die "npx が見つかりません（Node.js と一緒に入れてください）"
say "Node.js $(node --version)（$NODE）"

# 2. 依存を入れてから、グローバルに入れる（フォルダの -g は依存を入れず、このフォルダへのリンクを global bin に置くだけ）
say "npm パッケージを入れます（$TOOL_DIR/node_modules）"
(cd "$TOOL_DIR" && "$NPM" install --omit=dev --no-audit --no-fund)
say "webui-test をグローバルに入れます（npm install -g）"
"$NPM" install -g "$TOOL_DIR" --omit=dev --no-audit --no-fund ||
  die "npm install -g に失敗しました。権限の不足なら npm の prefix をユーザーのフォルダにしてください（npm config set prefix ~/.npm-global）"
BIN_DIR="$("$NPM" prefix -g)/bin"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR が PATH にありません。~/.bashrc などに次を足してください: export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

# 3. ブラウザ
if [ "$SKIP_BROWSER" = 1 ]; then
  warn "ブラウザは入れません（--skip-browser）。使う前に: cd $TOOL_DIR && npx playwright install chromium"
else
  deps=()
  if [ "$(uname -s)" = Linux ]; then
    if [ "$WITH_DEPS" = 1 ] || [ "$(id -u)" = 0 ] || sudo -n true 2>/dev/null; then deps=(--with-deps); fi
  fi
  say "Playwright の Chromium を入れます ${deps[*]:-}"
  (cd "$TOOL_DIR" && "$NPX" playwright install "${deps[@]}" chromium)
  if [ "$(uname -s)" = Linux ] && [ ${#deps[@]} -eq 0 ]; then
    warn "OS のライブラリが足りずにブラウザが起動しないときは: ./install.sh --with-deps"
  fi
fi

# 4. エージェント CLI（テストケースを作るときに使う。入れ方は各製品の案内に従う）
for cli in kiro-cli copilot; do
  if command -v "$cli" >/dev/null 2>&1; then say "$cli: あり"; else warn "$cli が見つかりません（webui-test generate で使うときに入れてください）"; fi
done

# 5. 確かめる
"$BIN_DIR/webui-test" --help >/dev/null
if [ "$CHECK" = 1 ]; then
  say "同梱のサンプルでテストを動かします"
  port=38917
  "$NODE" "$TOOL_DIR/examples/sample-app/server.js" "$port" >/dev/null &
  server=$!
  trap 'kill $server 2>/dev/null || true' EXIT
  sleep 1
  "$BIN_DIR/webui-test" run "$TOOL_DIR/examples/login.yaml" --base-url "http://localhost:$port" --out "$DATA_DIR/check-results"
fi
say "できました。使い方: webui-test --help"
