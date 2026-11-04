#!/usr/bin/env python3
"""api-watch の入口。`python api_watch.py --help` を参照。

Python 3.10 以降。YAML の設定を使うなら PyYAML が要る（JSON なら標準ライブラリだけで動く）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apiwatch.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
