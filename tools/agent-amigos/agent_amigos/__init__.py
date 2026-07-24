"""agent-amigos — 役割駆動マルチエージェント協働ツール。

設計書: docs/designs/agent-amigos-design.md（P0 実装）。
オーナーノードが design doc ＋ 役割ミッション表でミッションを公示し、
ノードがロールを claim して amigo として参加、型付きメッセージで相互協働して
1 つの deliverable をオーナーへ納品する。
"""
import os as _os
import sys as _sys

# agentcore（transport / protocol / vocab / heartbeat の共通ライブラリ）への import 経路。
# 開発木・リポジトリ内直接実行では tools/agentcore が兄弟ディレクトリにある
# （tools/agent-amigos/agent_amigos/__init__.py から見て ../../agentcore）。zipapp 配布では
# install.sh が agentcore/ を同じアーカイブへ同梱するため、zip 自身が sys.path に載って
# いれば下記の追加パスは（存在しなくても無害に）素通りし、素の `import agentcore` が
# アーカイブ内の agentcore/ を解決する（事前検証 V2）。
_agentcore_dir = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "agentcore")
if _agentcore_dir not in _sys.path:
    _sys.path.insert(0, _agentcore_dir)
del _os, _sys, _agentcore_dir

from .cli import main  # noqa: F401,E402

__version__ = "0.1.0"
