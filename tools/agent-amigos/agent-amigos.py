#!/usr/bin/env python3
"""agent-amigos エントリポイント（実体は agent_amigos パッケージ）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# agentcore（transport/protocol/vocab/heartbeat の共通ライブラリ）は tools/ の兄弟ディレクトリ。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agentcore"))

from agent_amigos.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
