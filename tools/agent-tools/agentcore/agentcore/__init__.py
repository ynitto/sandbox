"""agentcore — agent-flow / agent-amigos / agent-project の共通内部ライブラリ。

git 転送（`transport`）と claim/lease/完了語彙/心拍（`protocol` / `vocab` / `heartbeat`）を
1 実装に集約する（設計: docs/plans/2026-07-24-single-resident-controller-design.md §4.1）。
node-budget の読取・推定・state/can_accept は `nodebudget`（設計: 2026-07-29 分散クレジット計画 Phase 1）。

独立プロダクトではない: 配布しない・CLI を持たない・利用者向け文書に登場しない（R10）。
3 ツールから `import agentcore` / `from agentcore import transport` として使う通常パッケージ。
"""
from __future__ import annotations

__version__ = "0.1.0"
