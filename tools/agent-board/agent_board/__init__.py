"""agent-board — 委譲公示板（依頼の公示・入札・成果一本化の分散バックエンド）。

専用リポジトリ（またはローカル dir / hub）を『板』にして、エージェント処理の依頼を公示し、
登録ノードの入札（先勝ち claim）で引き受け先を決める。エンジン非依存の一段下の層で、
agent-flow / agent-amigos の分散処理の裏側として機能する（両エンジンのコードは import せず、
各エンジンの入力契約＝flow inbox / amigos-command をファイルとして書いて引き渡す）。

正典設計: docs/plans/2026-07-23-delegation-board-distributed-bidding-design.md
契約: schemas/board.schema.json ／ schemas/delegation.schema.json
"""
from __future__ import annotations

from .cli import main  # noqa: F401

__all__ = ["main"]
