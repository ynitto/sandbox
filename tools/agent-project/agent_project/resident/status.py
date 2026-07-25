"""resident.status — ノード契約: `nodes/<pc>.json`（板・能力宣言）と
`.agents/engine/status.json`（常駐体 → dashboard、心拍・健康・子状態）の書き出し
（設計 §4.2・§5、実装計画 W1-6）。

`NodeCapability` は `schemas/board.schema.json` の `$defs.node`（板上の nodes/<node-id>.json）
と 1:1 対応する。`EngineStatus` は設計 §5 の `.agents/engine/status.json` 契約
（新規・スキーマファイルは未作成 — dashboard 連携が実装される P2 まではこの dataclass が
契約の正）。どちらも書くだけ・読む側（板の入札判定・dashboard）はこのモジュールの外。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

# agentcore への import 経路（agent_project/__init__.py と同じ流儀）。このファイルは
# agent_project/resident/ 配下＝`__init__.py` より 1 階層深いので、tools/ まで 4 段上がる
# （tools/agent-project/agent_project/resident/status.py → tools/agentcore）。親パッケージ
# 経由で import すれば `agent_project/__init__.py` が先に同じパスを入れるが、resident は
# 単体 import できる通常パッケージとして書く方針（§4.7）なので自前でも解決する。
_tools_dir = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_agentcore_dir = os.path.join(_tools_dir, "agentcore")
if _agentcore_dir not in sys.path:
    sys.path.insert(0, _agentcore_dir)

from agentcore.protocol import write_json_atomic  # noqa: E402

# 常駐一本化のノード契約バージョン（設計 §9 C13）。互換性の無い変更をする時だけ上げる。
# 公示側が要求するバージョンと不一致のノードは「入札しない」（誤動作でなく不参加に倒す）。
CONTRACT_VERSION = 1


def contract_compatible(required_by_post: "int | None", *,
                        declared: "int | None" = CONTRACT_VERSION) -> bool:
    """公示（post.json）の `requires.contract_version` と、ノードの宣言
    （`nodes/<pc>.json` の `contract_version`）が噛み合うか（設計 §9 C13
    「公示の要求と不一致のノードは入札しない」）。

    - 要求の無い公示（None）は不問 — True。契約バージョンを載せる前から板にある公示を
      この項目の追加だけで一斉に入札不能にしない。
    - 要求のある公示に対して未宣言のノード（declared=None）は非互換 — False
      （fail-close。更新漏れの古いノードを誤動作でなく不参加に倒す）。"""
    if required_by_post is None:
        return True
    return declared == required_by_post


@dataclass
class NodeCapability:
    """板上の `nodes/<node-id>.json`（`schemas/board.schema.json` `$defs.node`）。"""
    node: str
    workloads: "list[str]" = field(default_factory=list)
    tags: "list[str]" = field(default_factory=list)
    agent_cli: "list[str]" = field(default_factory=list)
    repos: "Any" = None
    availability: "str | None" = None
    max_concurrent: int = 0
    heartbeat: "str | None" = None
    fresh_after_sec: "float | None" = None
    contract_version: int = CONTRACT_VERSION

    def to_dict(self) -> dict:
        d = {"node": self.node, "workloads": list(self.workloads), "tags": list(self.tags),
            "agent_cli": list(self.agent_cli), "max_concurrent": self.max_concurrent,
            "contract_version": self.contract_version}
        if self.repos is not None:
            d["repos"] = self.repos
        if self.availability is not None:
            d["availability"] = self.availability
        if self.heartbeat is not None:
            d["heartbeat"] = self.heartbeat
        if self.fresh_after_sec is not None:
            d["fresh_after_sec"] = self.fresh_after_sec
        return d

    def write(self, board_root: str) -> str:
        """`<board_root>/nodes/<node>.json` へ原子的に書く。書いたパスを返す。"""
        path = os.path.join(board_root, "nodes", f"{self.node}.json")
        write_json_atomic(path, self.to_dict())
        return path


@dataclass
class ChildStatus:
    """子プロセス 1 件分の観測（`resident.supervisor.Supervisor.status()` の 1 エントリを
    そのまま載せられる形）。

    `root` は host.yaml のプロジェクト宣言そのまま（`run --watch --root` に渡す値）。
    dashboard のプロジェクト発見はこのフィールドが唯一の入口——ここが空だと、dashboard は
    「常駐体は動いているのにプロジェクトが 1 件も無い」画面になる（設計 §5・実装計画 W2-4）。"""
    name: str
    alive: bool
    quarantined: bool = False
    deaths: int = 0
    root: "str | None" = None


@dataclass
class SyncHealth:
    """調整系リポジトリ 1 本の同期健康（設計 §5「同期健康（ahead/behind/エラー）」）。"""
    name: str
    ahead: int = 0
    behind: int = 0
    last_error: "str | None" = None


@dataclass
class EngineStatus:
    """`.agents/engine/status.json`（常駐体 → dashboard。設計 §5 新規契約）。
    書き手は常駐体のみ——dashboard のプロジェクト発見もこのファイルから行う想定。"""
    node: str
    heartbeat: "str | None" = None
    tick_counts: "dict[str, int]" = field(default_factory=dict)
    sync_health: "list[SyncHealth]" = field(default_factory=list)
    recent_errors: "list[str]" = field(default_factory=list)
    children: "list[ChildStatus]" = field(default_factory=list)
    running_runs: "list[str]" = field(default_factory=list)
    # ノード契約のバージョン。dashboard はこれを見て「更新漏れの古いノード」を表示する
    # （設計 §6・実装計画 W2-5）。載せないと、古い常駐体が新しい dashboard に対して
    # 静かに一部の情報を欠いたまま「正常」に見える。
    contract_version: int = CONTRACT_VERSION
    # 直近エラーのリングバッファ上限（設計 §5「直近エラーのリングバッファ」）。
    max_recent_errors: int = 50

    def record_error(self, message: str) -> None:
        # 上限 0 以下は「保持しない」。`del lst[:-0]` は `del lst[:0]` と同義で何も消さず、
        # 常駐体が長時間走ると際限なく伸びるため、スライスに載せる前に分岐する。
        if self.max_recent_errors <= 0:
            self.recent_errors.clear()
            return
        self.recent_errors.append(message)
        if len(self.recent_errors) > self.max_recent_errors:
            del self.recent_errors[:-self.max_recent_errors]

    def to_dict(self) -> dict:
        return {
            "node": self.node,
            "contract_version": self.contract_version,
            "heartbeat": self.heartbeat,
            "tick_counts": dict(self.tick_counts),
            "sync_health": [
                {"name": s.name, "ahead": s.ahead, "behind": s.behind, "last_error": s.last_error}
                for s in self.sync_health],
            "recent_errors": list(self.recent_errors),
            "children": [
                {"name": c.name, "alive": c.alive, "quarantined": c.quarantined,
                 "deaths": c.deaths, "root": c.root}
                for c in self.children],
            "running_runs": list(self.running_runs),
        }

    def write(self, state_home: str) -> str:
        """`<state_home>/.agents/engine/status.json` へ原子的に書く。書いたパスを返す。"""
        path = os.path.join(state_home, ".agents", "engine", "status.json")
        write_json_atomic(path, self.to_dict())
        return path
