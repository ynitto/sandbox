#!/usr/bin/env python3
"""agent-loop — 薄いエントリポイント。

実体は隣接する agent_loop/ パッケージ（LLM が編集できる大きさの断片へ分割済み）。
このファイルは後方互換のための起動口で、リポジトリ内から `python3 agent-loop.py ...`
で直接実行できるようにするだけ。配布は install.sh が zipapp を生成する。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_loop import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
