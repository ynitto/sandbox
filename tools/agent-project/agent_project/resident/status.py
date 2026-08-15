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
# （tools/agent-project/agent_project/resident/status.py → tools/agent-tools/agentcore）。
# 親パッケージ経由で import すれば `agent_project/__init__.py` が先に同じパスを入れるが、
# resident は単体 import できる通常パッケージとして書く方針（§4.7）なので自前でも解決する。
_tools_dir = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_agentcore_dir = os.path.join(_tools_dir, "agent-tools", "agentcore")
if _agentcore_dir not in sys.path:
    sys.path.insert(0, _agentcore_dir)

from agentcore.protocol import safe_name, write_json_atomic  # noqa: E402

# ノード契約バージョンと互換判定の**正典は `agentcore.board`**（P2-1）。ここは名前を
# 通すだけで、値も判定も持たない——以前はこのファイルに同じ定数と同じ関数本体（docstring
# ごと）があり、板の判定（`agentcore.board.eligible`）とは別の実装だった。片方だけ上げると
# 「版 2 と宣言しつつ版 1 で判定する」が作れてしまい、入札選別は fail-close なので
# 誤動作ではなく**無言の不参加**として出る（設計 §9 C13）。
from agentcore.board import CONTRACT_VERSION, contract_compatible  # noqa: E402,F401


@dataclass
class NodeCapability:
    """板上の `nodes/<node-id>.json`（`schemas/board.schema.json` `$defs.node`）。"""
    node: str
    workloads: "list[str]" = field(default_factory=list)
    tags: "list[str]" = field(default_factory=list)
    agent_cli: "list[str]" = field(default_factory=list)
    repos: "Any" = None
    availability: "str | None" = None
    # 同時に落札・実行してよい委譲数の上限。**0 = 無制限**（スキーマの語彙）で、
    # 「未宣言」は呼び出し側が既定を当ててから渡す（P2-3）。
    max_concurrent: int = 0
    heartbeat: "str | None" = None
    fresh_after_sec: "float | None" = None
    contract_version: int = CONTRACT_VERSION
    # status/<node>.json と同形の node-budget-summary（Phase1 ミラー・任意）。
    budget: "dict | None" = None

    def to_dict(self) -> dict:
        d = {"node": self.node, "tags": list(self.tags),
             "agent_cli": list(self.agent_cli), "max_concurrent": self.max_concurrent,
             "contract_version": self.contract_version}
        # `workloads` は**空なら出さない**（P2-3）。スキーマの語彙では「空 = 全部」で、
        # キーが無いことと同義。宣言していないものを空配列として配ると、読み手には
        # 「宣言したうえで空」と区別が付かない。tags / agent_cli は「要求との突き合わせ」で
        # 空が意味を持つ（fail-close の材料）ので、そちらは常に出す。
        if self.workloads:
            d["workloads"] = list(self.workloads)
        if self.repos is not None:
            d["repos"] = self.repos
        if self.availability is not None:
            d["availability"] = self.availability
        if self.heartbeat is not None:
            d["heartbeat"] = self.heartbeat
        if self.fresh_after_sec is not None:
            d["fresh_after_sec"] = self.fresh_after_sec
        if self.budget is not None:
            d["budget"] = self.budget
        return d

    def write(self, board_root: str) -> str:
        """`<board_root>/nodes/<node>.json` へ原子的に書く。書いたパスを返す。

        ファイル名は `agentcore.protocol.safe_name` を通す（P2-5）——読む側の
        `BoardRepo.node_path` が同じ規則で綴るので、ここだけ素通しにすると
        「書いたのに読めない」名義が理屈の上で作れる（`node` は正規化済みなので
        現経路では同値だが、同値であることを規則ではなく偶然に依存させない）。"""
        path = os.path.join(board_root, "nodes", f"{safe_name(self.node)}.json")
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
    # 計画停止中（稼働時間帯の外）。隔離（quarantined）と区別する——前者は「時間が来れば
    # 自動で戻る」、後者は「人が原因を直すまで戻らない」で、利用者に見せる文言が変わる。
    paused: bool = False


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
    # 板（agent-board）への参加状況（R2a）。dashboard が「この端末は板に参加しているか・
    # 手動入札できるか」を判断する**唯一の根拠**——dashboard が host.yaml と agent-flow の
    # 設定を自前で読み解いて判定すると、宣言の解釈が 2 実装になる（S1 で畳んだはずの
    # 二重宣言が別の場所に戻る）。板未設定なら {"configured": false}。
    board: "dict | None" = None
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
            "board": dict(self.board) if isinstance(self.board, dict) else None,
            "heartbeat": self.heartbeat,
            "tick_counts": dict(self.tick_counts),
            "sync_health": [
                {"name": s.name, "ahead": s.ahead, "behind": s.behind, "last_error": s.last_error}
                for s in self.sync_health],
            "recent_errors": list(self.recent_errors),
            "children": [
                {"name": c.name, "alive": c.alive, "quarantined": c.quarantined,
                 "deaths": c.deaths, "root": c.root, "paused": c.paused}
                for c in self.children],
            "running_runs": list(self.running_runs),
        }

    def write(self, state_home: str) -> str:
        """`<state_home>/.agents/engine/status.json` へ原子的に書く。書いたパスを返す。"""
        path = os.path.join(state_home, ".agents", "engine", "status.json")
        write_json_atomic(path, self.to_dict())
        return path
