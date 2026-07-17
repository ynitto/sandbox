"""agent-amigos — 役割駆動マルチエージェント協働ツール。

設計書: docs/designs/agent-amigos-design.md（P0 実装）。
オーナーノードが design doc ＋ 役割ミッション表でミッションを公示し、
ノードがロールを claim して amigo として参加、型付きメッセージで相互協働して
1 つの deliverable をオーナーへ納品する。
"""
from .cli import main  # noqa: F401

__version__ = "0.1.0"
