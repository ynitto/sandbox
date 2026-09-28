#!/bin/sh
# install-laya.sh — macOS / Linux 用の入口。Python 3.10 以上を探して install_laya.py へ渡す。
#   sh tools/laya/install-laya.sh [--port 8000] [--dry-run] …
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for py in "${PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3 python; do
  [ -n "$py" ] || continue
  command -v "$py" >/dev/null 2>&1 || continue
  if "$py" -c 'import sys, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    exec "$py" "$HERE/install_laya.py" "$@"
  fi
done
echo "Python 3.10 以上が見つかりません（venv も要ります。Debian / Ubuntu なら python3-venv）。" >&2
exit 1
