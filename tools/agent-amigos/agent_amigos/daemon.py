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

import os
import signal
import socket
import time

from .assign import (apply_role, claim_role, confirm_assignment, matches_role,
                     mirror_roster, staffing_expired, unfilled_required, winner)
from .bus import Bus
from .mission import derive_phase, load_mission, load_roles
from .runner import AmigoRunner
from .util import log, now_iso, read_json, write_json_atomic


def _node_id_paths() -> "list[str]":
    """node.json の探索候補（新ホーム優先）。**読むときは両方を見る。**

    共通ホームは `.agent` → `.agents` へ移行中で、他の状態は agent_home_subdir が
    サブディレクトリ単位で新旧を判定している。しかし node.json だけは同じ扱いにできない:
    採番済みのノード ID を保持しており、参照先が変わって既存ファイルを見失うと ID が
    振り直される。ノード ID は claim / assign / メッセージ宛先に使われるので、
    振り直しは同一性の断絶になる。そこで「読みは新旧の両方を順に見る」ことで、
    どちらに置かれていても既存 ID を必ず拾う。新規採番だけ新ホームへ書く。
    """
    home = os.path.expanduser("~")
    return [os.path.join(home, ".agents", "amigos", "node.json"),
            os.path.join(home, ".agent", "amigos", "node.json")]


def default_node_id() -> str:
    """ノード ID: 環境変数 → node.json（新旧ホームの順に探索。無ければ採番）→ ホスト名。"""
    env = os.environ.get("AGENT_AMIGOS_NODE")
    if env:
        return env
    paths = _node_id_paths()
    for path in paths:
        data = read_json(path)
        if isinstance(data, dict) and data.get("id"):
            return str(data["id"])
    nid = f"{socket.gethostname()}-{os.urandom(2).hex()}".lower().replace(" ", "-")
    try:
        write_json_atomic(paths[0], {"id": nid})   # 新規採番は新ホームへ
    except OSError:
        pass
    return nid


class NodeDaemon:
    def __init__(self, bus: Bus, node_id: str, agent_cli: "str | None" = None,
                 tags: "list[str] | None" = None, roles_filter: "list[str] | None" = None,
                 interval: float = 5.0, resume_hours: float = 12.0,
                 manual_claim: bool = False, commands_home: "str | None" = None,
                 home: "str | None" = None):
        self.bus = bus
        self.node_id = node_id
        self.agent_cli = agent_cli
        self.tags = list(tags or [])
        self.roles_filter = list(roles_filter or [])
        self.interval = interval
        self.resume_hours = resume_hours
        # manual_claim: 自動応募しない（commands/ 経由の手動引き受けのみ。
        # 引き受け済みロールのターン実行・オーナー職務は従来どおり動く）
        self.manual_claim = manual_claim
        # commands_home: 指示のファイル取り込み（<home>/.agent/agent-amigos/commands/）を
        # 有効にするホームディレクトリ。None = 取り込まない
        self.commands_home = commands_home
        # home: 納品棚（<home>/deliveries/）の置き場。既定は commands_home と同じ
        # ディレクトリ（＝ノードのホーム）。None なら accept しても搬出しない
        self.home = home if home is not None else commands_home
        self._runners: "dict[tuple[str, str], AmigoRunner]" = {}
        self._active = False
        self._stopping = False

    def _runner(self, mission_id: str, role_id: str) -> AmigoRunner:
        key = (mission_id, role_id)
        if key not in self._runners:
            self._runners[key] = AmigoRunner(self.bus, mission_id, role_id,
                                             self.node_id, self.agent_cli)
        return self._runners[key]

    def cycle(self) -> dict:
        """1 巡: 指示の取り込み → 全ミッションを見て応募・オーナー職務・自 amigo のターン。
        返り値は観測サマリ {mission_id: phase}（テスト・status 表示用）。"""
        self.bus.sync_pull()
        if self.commands_home:
            from .commands import ingest_commands
            if ingest_commands(self.bus, self.node_id, self.commands_home, self.agent_cli):
                self._active = True
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
            policy = str(mission.get("assignment_policy") or "first-come")
            roster = read_json(mp.roster()) or {}

            # 応募: 未充足ロールのうち能力が合うものへ。
            # first-come は claim（勝者＝確定）、owner-picks は応募のみ（確定はオーナー）。
            # manual_claim では自動応募しない（commands/ の手動引き受けのみ）。
            for role in [] if self.manual_claim else list(roles.values()):
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
                if policy == "owner-picks":
                    apply_role(self.bus, mp, rid, self.node_id, self.agent_cli)
                    continue
                if winner(mp, rid) == self.node_id:
                    continue    # claim 済み（roster への鏡写しはオーナー待ち）
                if claim_role(self.bus, mp, rid, self.node_id, self.agent_cli):
                    log(self.node_id, f"{mid}: ロール {rid} を獲得しました")

            # オーナー職務: roster 維持・自己補充・受入の自動判定
            if i_am_owner:
                roster = mirror_roster(self.bus, mp, roles, self.node_id, policy=policy)
                unfilled = unfilled_required(roles, roster)
                if unfilled and str(mission.get("staffing_policy")) == "self-staff" \
                        and staffing_expired(mission):
                    for rid in unfilled:
                        if policy == "owner-picks":
                            apply_role(self.bus, mp, rid, self.node_id, self.agent_cli)
                            confirm_assignment(self.bus, mp, rid, self.node_id)
                            log(self.node_id, f"{mid}: 未充足ロール {rid} を自己補充します")
                        elif claim_role(self.bus, mp, rid, self.node_id, self.agent_cli):
                            log(self.node_id, f"{mid}: 未充足ロール {rid} を自己補充します")
                    roster = mirror_roster(self.bus, mp, roles, self.node_id, policy=policy)
                if str(mission.get("acceptance")) == "agent" and phase == "reviewing":
                    from .ownerops import acceptance_turn
                    result = acceptance_turn(self.bus, mp, mission, self.node_id,
                                             self.agent_cli, home=self.home)
                    if result in ("accepted", "rejected"):
                        self._active = True

            # 自分の amigo のターン
            for rid, ent in sorted(roster.items()):
                if ent.get("node") != self.node_id:
                    continue
                result = self._runner(mid, rid).turn_once()
                if result in ("acted", "integrated"):
                    self._active = True
                    log(self.node_id, f"{mid}/{rid}: {result}")
        return seen

    # --- graceful offboard（away プロトコル、設計書 §6.6） ------------------
    def offboard(self, resume_hours: "float | None" = None) -> None:
        """計画停止: 自分の全 amigo を `state: away`（resume_at 付き）にして
        最後の push をする。引き継ぎメモは毎ターン更新済みなので、ここでは
        状態遷移だけを宣言する。ロールは resume_at + grace まで保持される。"""
        hours = resume_hours if resume_hours is not None else self.resume_hours
        resume_at = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                  time.gmtime(time.time() + hours * 3600))
        for (mid, rid), runner in sorted(self._runners.items()):
            st = runner._load_status()
            st["state"] = "away"
            st["resume_at"] = resume_at
            st["heartbeat"] = now_iso()
            write_json_atomic(runner.mp.status(runner.who), st)
            log(self.node_id, f"{mid}/{rid}: away（resume_at={resume_at}）")
        self.bus.sync_push(f"offboard {self.node_id}")

    def _install_signal_handlers(self) -> None:
        def _on_signal(_signum, _frame):
            self._stopping = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _on_signal)
            except (ValueError, OSError):
                pass    # メインスレッド以外（テスト等）では設定できない

    def run(self, cycles: int = 0) -> None:
        """常駐ループ。cycles>0 ならその回数で終了（テスト・デバッグ用）。
        SIGTERM / SIGINT で graceful offboard（away 宣言）してから終了する。
        無風時はインターバルを伸ばす（adaptive interval の簡略採用、上限 8 倍）。"""
        self._install_signal_handlers()
        n = 0
        sleep = self.interval
        while not self._stopping:
            self._active = False
            try:
                self.cycle()
            except Exception as e:  # noqa: BLE001 — デーモンは 1 巡の失敗で死なない
                log(self.node_id, f"cycle 失敗: {e}")
            n += 1
            if cycles and n >= cycles:
                return
            sleep = self.interval if self._active else min(sleep * 2, self.interval * 8)
            deadline = time.time() + sleep
            while time.time() < deadline and not self._stopping:
                time.sleep(min(0.2, self.interval or 0.2))
        self.offboard()
