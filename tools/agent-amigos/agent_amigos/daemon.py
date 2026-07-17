"""ノードデーモン — join したノードの常駐ループ（設計書 §6.1・§6.4）。

1 プロセスで次を回す:
- バス上の open なミッションを発見し、能力が合うロールへ応募（claim）
- 自ノードがオーナーのミッションでは、オーナー職務（roster の鏡写し・
  staffing_timeout 後の自己補充 self-staff）を行う
- roster 確定した自分の amigo のターンを順に実行する

オーナーノードも参加ノードも同じ `join` を動かす（役割は mission.json の
owner_node で決まる）。1 ノード運用は「オーナーが join し、self-staff で
全必須ロールを自分で claim する」という形で自然に成立する。
"""
from __future__ import annotations

import json
import os
import socket
import time

from .assign import (claim_role, matches_role, mirror_roster, staffing_expired,
                     unfilled_required)
from .bus import Bus
from .mission import derive_phase, load_mission, load_roles
from .runner import AmigoRunner
from .util import log, read_json, write_json_atomic


def default_node_id() -> str:
    """ノード ID: 環境変数 → ~/.agent/amigos/node.json（初回に採番）→ ホスト名。"""
    env = os.environ.get("AGENT_AMIGOS_NODE")
    if env:
        return env
    path = os.path.expanduser("~/.agent/amigos/node.json")
    data = read_json(path)
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    nid = f"{socket.gethostname()}-{os.urandom(2).hex()}".lower().replace(" ", "-")
    try:
        write_json_atomic(path, {"id": nid})
    except OSError:
        pass
    return nid


class NodeDaemon:
    def __init__(self, bus: Bus, node_id: str, agent_cli: "str | None" = None,
                 tags: "list[str] | None" = None, roles_filter: "list[str] | None" = None,
                 interval: float = 5.0):
        self.bus = bus
        self.node_id = node_id
        self.agent_cli = agent_cli
        self.tags = list(tags or [])
        self.roles_filter = list(roles_filter or [])
        self.interval = interval
        self._runners: "dict[tuple[str, str], AmigoRunner]" = {}

    def _runner(self, mission_id: str, role_id: str) -> AmigoRunner:
        key = (mission_id, role_id)
        if key not in self._runners:
            self._runners[key] = AmigoRunner(self.bus, mission_id, role_id,
                                             self.node_id, self.agent_cli)
        return self._runners[key]

    def cycle(self) -> dict:
        """1 巡: 全ミッションを見て応募・オーナー職務・自 amigo のターンを行う。
        返り値は観測サマリ {mission_id: phase}（テスト・status 表示用）。"""
        self.bus.sync_pull()
        seen = {}
        for mid in self.bus.list_missions():
            mp = self.bus.mission(mid)
            try:
                mission = load_mission(mp)
            except SystemExit:
                continue
            roles = load_roles(mp)
            phase = derive_phase(mission, roles, mp)
            seen[mid] = phase
            if phase in ("done", "cancelled", "failed"):
                continue
            i_am_owner = mission.get("owner_node") == self.node_id
            roster = read_json(mp.roster()) or {}

            # 応募: 未充足ロールのうち能力が合うものを claim（first-come）
            for role in roles.values():
                rid = role["id"]
                if rid in roster:
                    continue
                if self.roles_filter and rid not in self.roles_filter:
                    if not (i_am_owner and role.get("builtin") == "integrator"):
                        continue
                if role.get("builtin") == "integrator" and not i_am_owner:
                    continue    # integrator はオーナーノードの組み込み職務（§8.1）
                if not matches_role(role, self.tags, [self.agent_cli] if self.agent_cli else []):
                    continue
                if claim_role(self.bus, mp, rid, self.node_id, self.agent_cli):
                    log(self.node_id, f"{mid}: ロール {rid} を獲得しました")

            # オーナー職務: roster 鏡写し・自己補充
            if i_am_owner:
                roster = mirror_roster(self.bus, mp, roles, self.node_id)
                unfilled = unfilled_required(roles, roster)
                if unfilled and str(mission.get("staffing_policy")) == "self-staff" \
                        and staffing_expired(mission):
                    for rid in unfilled:
                        if claim_role(self.bus, mp, rid, self.node_id, self.agent_cli):
                            log(self.node_id, f"{mid}: 未充足ロール {rid} を自己補充します")
                    roster = mirror_roster(self.bus, mp, roles, self.node_id)

            # 自分の amigo のターン
            for rid, ent in sorted(roster.items()):
                if ent.get("node") != self.node_id:
                    continue
                result = self._runner(mid, rid).turn_once()
                if result in ("acted", "integrated"):
                    log(self.node_id, f"{mid}/{rid}: {result}")
        return seen

    def run(self, cycles: int = 0) -> None:
        """常駐ループ。cycles>0 ならその回数で終了（テスト・デバッグ用）。"""
        n = 0
        while True:
            try:
                self.cycle()
            except Exception as e:  # noqa: BLE001 — デーモンは 1 巡の失敗で死なない
                log(self.node_id, f"cycle 失敗: {e}")
            n += 1
            if cycles and n >= cycles:
                return
            time.sleep(self.interval)
