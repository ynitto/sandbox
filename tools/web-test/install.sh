#!/usr/bin/env bash
# web-test のインストーラ（Linux / macOS / WSL）。
#
#   ./install.sh                 足りないものを入れて web-test コマンドを使えるようにする
#   ./install.sh --with-deps     Chromium が必要とする OS のライブラリも入れる（sudo を使う）
#   ./install.sh --skip-browser  ブラウザを入れない（社内ミラーから別に入れるときなど）
#   ./install.sh --check         入れたあと、同梱のサンプルでテストを 1 回動かして確かめる
#
# 入れるもの（すでにあれば使う）:
#   - Node.js 18 以上。無い・古いときは公式の LTS を ~/.local/share/web-test/node に入れる（sudo 不要）
#   - npm パッケージ（playwright・@playwright/test・@playwright/cli・yaml）… このフォルダの node_modules
#   - Playwright の Chromium
#   - web-test コマンド … ~/.local/bin/web-test（PREFIX で変えられる）
set -euo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${WEB_TEST_HOME:-$HOME/.local/share/web-test}"
BIN_DIR="${PREFIX:-$HOME/.local}/bin"
NODE_MAJOR="${WEB_TEST_NODE_MAJOR:-22}"
WITH_DEPS=0
SKIP_BROWSER=0
CHECK=0

for arg in "$@"; do
  case "$arg" in
    --with-deps) WITH_DEPS=1 ;;
    --skip-browser) SKIP_BROWSER=1 ;;
    --check) CHECK=1 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

download() { # URL 保存先
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then wget -q "$1" -O "$2"
  else die "curl か wget が要ります"; fi
}

install_node() {
  local os arch dist tmp file sum
  case "$(uname -s)" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
    *) die "この OS には Node.js を自動で入れられません。https://nodejs.org/ から 18 以上を入れてください" ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) die "この CPU（$(uname -m)）には Node.js を自動で入れられません" ;;
  esac
  dist="https://nodejs.org/dist/latest-v${NODE_MAJOR}.x"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  download "$dist/SHASUMS256.txt" "$tmp/SHASUMS256.txt"
  file="$(grep -o "node-v[0-9.]*-${os}-${arch}\.tar\.gz" "$tmp/SHASUMS256.txt" | head -n1)"
  [ -n "$file" ] || die "Node.js の配布物が見つかりません（$dist）"
  say "Node.js を入れます: $file → $DATA_DIR/node"
  download "$dist/$file" "$tmp/$file"
  sum="$(grep " $file\$" "$tmp/SHASUMS256.txt" | cut -d' ' -f1)"
  if command -v sha256sum >/dev/null 2>&1; then echo "$sum  $tmp/$file" | sha256sum -c - >/dev/null
  else echo "$sum  $tmp/$file" | shasum -a 256 -c - >/dev/null; fi || die "Node.js のチェックサムが合いません"
  rm -rf "$DATA_DIR/node"
  mkdir -p "$DATA_DIR/node"
  tar -xzf "$tmp/$file" -C "$DATA_DIR/node" --strip-components=1
}

# 1. Node.js
if node_ok node; then
  NODE="$(command -v node)"
elif node_ok "$DATA_DIR/node/bin/node"; then
  NODE="$DATA_DIR/node/bin/node"
else
  install_node
  NODE="$DATA_DIR/node/bin/node"
fi
NODE_DIR="$(dirname "$NODE")"
export PATH="$NODE_DIR:$PATH"
NPM="$NODE_DIR/npm"; [ -x "$NPM" ] || NPM="$(command -v npm || true)"
NPX="$NODE_DIR/npx"; [ -x "$NPX" ] || NPX="$(command -v npx || true)"
[ -n "$NPM" ] && [ -n "$NPX" ] || die "npm が見つかりません（Node.js と一緒に入れてください）"
say "Node.js $("$NODE" --version)（$NODE）"

# 2. npm パッケージ
say "npm パッケージを入れます（$TOOL_DIR/node_modules）"
(cd "$TOOL_DIR" && "$NPM" install --omit=dev --no-audit --no-fund)

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

# 4. web-test コマンド
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/web-test" <<EOF
#!/usr/bin/env bash
# install.sh が作成。web-test 本体は $TOOL_DIR
exec "$NODE" "$TOOL_DIR/bin/web-test.js" "\$@"
EOF
chmod +x "$BIN_DIR/web-test"
say "web-test コマンドを置きました: $BIN_DIR/web-test"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR が PATH にありません。~/.bashrc などに次を足してください: export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

# 5. エージェント CLI（テストケースを作るときに使う。入れ方は各製品の案内に従う）
for cli in kiro-cli copilot; do
  if command -v "$cli" >/dev/null 2>&1; then say "$cli: あり"; else warn "$cli が見つかりません（web-test generate で使うときに入れてください）"; fi
done

# 6. 確かめる
"$BIN_DIR/web-test" --help >/dev/null
if [ "$CHECK" = 1 ]; then
  say "同梱のサンプルでテストを動かします"
  port=38917
  "$NODE" "$TOOL_DIR/examples/sample-app/server.js" "$port" >/dev/null &
  server=$!
  trap 'kill $server 2>/dev/null || true' EXIT
  sleep 1
  "$BIN_DIR/web-test" run "$TOOL_DIR/examples/login.yaml" --base-url "http://localhost:$port" --out "$DATA_DIR/check-results"
fi
say "できました。使い方: web-test --help"
