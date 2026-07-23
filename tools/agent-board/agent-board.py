#!/usr/bin/env python3
"""agent-board — 薄いエントリポイント。

実体は隣接する agent_board/ パッケージ。このファイルはリポジトリ内から
`python3 agent-board.py ...` で直接実行するための起動口。標準ライブラリのみ
（git は分散モードで必要）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_board import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
