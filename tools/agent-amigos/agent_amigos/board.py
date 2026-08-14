"""board — 委譲公示板（agent-board）への参加（請負・入札）。

agent-board は「リポジトリ＋契約」だけで処理を持たない（schemas/board.schema.json）。入札・
引き渡しの処理はこの請負側デーモンが担う: 板を巡回し、workload=amigos の公示に repos/tags 照合で
入札（名前空間付き claim ＋ (ts, who) タイブレーク＝flow / amigos の claim と同じ仕様・別実装）、
勝てば**オーナーとしてミッションを公示**する。結合はデータ契約のみ — agent-board のコードは
import せず、板のレイアウトを読み書きするだけ。設計:
docs/plans/2026-07-23-delegation-board-distributed-bidding-design.md
"""
from __future__ import annotations

import hashlib
import os
import time

from agentcore import (board as _boardrules, protocol, repolocal as _repolocal, transport,
                       vocab)

from .commands import _do_post
from .mission import active_roles, derive_phase, load_mission, load_roles
from .ownerops import _latest_reject_feedback
from .util import log, now_iso, read_json, write_json_atomic


def _safe(s: str) -> str:
    """ミラーのクローン先ディレクトリ名。**板のレイアウト（`status/<who>.json` 等）には
    使わない**——あちらは `agentcore.protocol.safe_name` が正典で、入札を書く `renew_lease`
    と同じ規則で綴る必要がある（P2-5）。実装は同一だが、規則の出どころを 1 つにしておく。"""
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in str(s)) or "x"


class BoardMirror:
    """板リポジトリのローカルミラー。git+<url> はノード専用クローン、他はローカル dir。
    転送の実体は `agentcore.transport.GitTransport`（agent-project / agent-flow と同じ
    唯一の転送実装 ——stale lock 掃除・中断 rebase の abort・破損時の自己回復・
    pull --rebase・force push 禁止。自パスのみは所有権分割で自然に成立）。"""

    def __init__(self, spec: str, node_id: str, workdir: "str | None" = None,
                 branch: str = "main"):
        spec = str(spec or "").strip()
        self.branch = branch or "main"
        if spec.startswith("git+"):
            self.git = True
            self.remote = spec[4:]
            base = workdir or os.path.join(
                os.path.expanduser("~/.agents/amigos-board"),
                hashlib.sha1(self.remote.encode()).hexdigest()[:8])
            self.dir = os.path.join(os.path.abspath(base), _safe(node_id))
            self._transport = transport.GitTransport(
                self.dir, self.remote, branch=self.branch,
                managed_flag="agent-amigos.board",
                commit_user_name="agent-amigos", commit_user_email="agent-amigos@local")
            self._ensure_clone()
        else:
            self.git = False
            self.dir = os.path.abspath(spec)
            self._transport = None
            os.makedirs(os.path.join(self.dir, "delegations"), exist_ok=True)

    def _ensure_clone(self) -> None:
        self._transport.ensure_clone()
        os.makedirs(os.path.join(self.dir, "delegations"), exist_ok=True)

    def sync_pull(self) -> None:
        if self.git:
            self._transport.sync_pull()

    def sync_push(self, msg: str) -> None:
        if not self.git:
            return
        self._transport.sync_push(msg or "board update")


def _winner(bids_dir: str) -> "str | None":
    """lease 内の bid から (ts, who) 最小の勝者を決定的に選ぶ（flow のタスク claim・amigos
    のロール claim と同じアルゴリズム — 共通実装は agentcore.protocol）。"""
    return protocol.winner(bids_dir)


def _try_bid(mirror: BoardMirror, bids_dir: str, did: str, who: str, lease: float) -> bool:
    """入札して勝者になれたら True（先勝ち・(ts, who) 決定的タイブレーク）。"""
    return protocol.try_claim(
        bids_dir, who, lease,
        on_write=lambda: mirror.sync_push(f"bid {did} by {who}"),
        on_sync=mirror.sync_pull,
        on_withdraw=lambda: mirror.sync_push(f"bid withdraw {who}"),
        extra={"workload": "amigos"})


def _board_declared_repos(node_repos) -> "set[str]":
    """ノードの担当リポジトリ宣言 → 板入札の照合に使う識別子集合。実装は `agentcore.board`。

    通常のロール応募（assign.py の requires.repos 照合。読み取り専用ロールが readonly 宣言と
    正当にマッチしうる）とは選別基準が違うので、共有の `_declared_repos` ではなくこちらを使う。"""
    return _boardrules.declared_repo_ids(node_repos)


def board_eligible(post: dict, node_repos, node_tags, node_agent_cli=None, *,
                   node_workloads=None, max_concurrent=None, inflight=0,
                   budget=None, heartbeat=None, updated_iso=None,
                   fresh_after_sec=None, node=None, enforce_default=False,
                   at=None) -> bool:
    """公示に入札してよいか（成果物リポジトリ・タグ・CLI・契約バージョン・引き受ける
    エンジン・枠・利用枠での選別）。

    判定規則は `agentcore.board.eligible` に一本化した——agent-flow が「同じ仕様・別実装」で
    持っていたもので、片方だけ育つと**同じ公示が経路によって拾えたり拾えなかったりする**
    （`agentcore.repolocal` が解決したのと同型の問題）。"""
    return _boardrules.eligible(post, repos=node_repos, tags=node_tags,
                                agent_cli=node_agent_cli or [],
                                workloads=node_workloads or [],
                                max_concurrent=max_concurrent, inflight=inflight,
                                budget=budget, heartbeat=heartbeat,
                                updated_iso=updated_iso,
                                fresh_after_sec=fresh_after_sec, node=node,
                                enforce_default=enforce_default, at=at)


def _node_board_declaration(daemon) -> "tuple[list, list, int | None]":
    """入札選別に使う `(agent_cli, workloads, max_concurrent)` を解決する（P2-3）。

    `daemon.agent_cli` は**スカラ 1 件**（`--agent-cli` / 設定）で、板の語彙は「使える CLI の
    一覧」。粒度が違うので畳んで渡す（`assign.py` のロール応募と同じ流儀）。
    **以前はこれを渡しておらず**、`eligible` の CLI 判定は fail-close なので
    `requires.agent_cli` を持つ公示に amigos ノードは永久に入札しなかった。

    `workloads` / `max_concurrent` は**このノードの宣言**なので正典は host.yaml
    （agent-flow の `_node_declaration` と同じ判断）。読めなければ制限しない。
    """
    cli = [str(daemon.agent_cli)] if getattr(daemon, "agent_cli", None) else []
    host = _repolocal.load_host_declaration(getattr(daemon, "node_declaration", None) or None)
    budget = host.get("budget")
    raw_max = budget.get("max_concurrent") if isinstance(budget, dict) else None
    max_concurrent = (int(raw_max) if isinstance(raw_max, (int, float))
                      and not isinstance(raw_max, bool) and int(raw_max) >= 0 else None)
    return cli, _boardrules.declared_workloads(host), max_concurrent


def _synth_design(post: dict) -> str:
    return (f"# {post.get('title') or post.get('goal') or post.get('id')}\n\n"
            f"## ゴール\n\n{str(post.get('goal') or '').strip()}\n")


def _post_to_command(post: dict) -> dict:
    """委譲封筒（delegation post）→ amigos-command の post レコード。"""
    amigos = (post.get("engine") or {}).get("amigos") or {}
    rec = {
        "command": "post", "mission_id": post["id"],
        "title": post.get("title") or "", "goal": post.get("goal") or "",
        "design": (post.get("design") or "").strip() or _synth_design(post),
        "roles": amigos.get("roles") or [],
    }
    mission = dict(amigos.get("mission") or {})
    policy = post.get("policy") or {}
    if policy.get("assignment"):
        mission.setdefault("assignment_policy", policy["assignment"])
    if policy.get("staffing"):
        mission.setdefault("staffing_policy", policy["staffing"])
    if policy.get("staffing_timeout_sec") is not None:
        mission.setdefault("staffing_timeout", policy["staffing_timeout_sec"])
    if post.get("acceptance"):
        mission.setdefault("acceptance", post["acceptance"])
    if post.get("deadline"):
        mission.setdefault("deadline", post["deadline"])
    budget = post.get("budget") or {}
    if budget:
        mb = dict(mission.get("budget") or {})
        if budget.get("execution_minutes") is not None:
            mb.setdefault("execution_minutes", budget["execution_minutes"])
        if budget.get("per_unit_turns") is not None:
            mb.setdefault("per_role_turns", budget["per_unit_turns"])
        if mb:
            mission["budget"] = mb
    if mission:
        rec["mission"] = mission
    return rec


def _delegation_result_extras(mp: "MissionPaths", phase: str) -> dict:
    """完了ミッションから板 result.json への追加ペイロードを組み立てる（実装計画 W1-9:
    板 result.json に result_notes / discoveries / reject_guidance を追加してから submit を
    消す——順序固定。設計 §4.4）。amigos には result_notes/discoveries に対応する概念が無い
    （ロール協働の成果はミッション自体・納品棚が正）——reject_guidance だけ、直近の差し戻し
    フィードバック（reject_mission が書く rejections/<round>.json の feedback）から載せる。

    **done には載せない。** `rejections/` は round ごとの履歴なので、round 0 で差し戻し →
    round 1 で accept というミッションでも最新 feedback が残る。契約上 reject_guidance は
    「却下時のやり直し指示」（board.schema.json）なので、受入済みの成果に付けると
    消費側（依頼元の自動回収）が「やり直しが要る」と読み違える。"""
    if phase == "done":
        return {}
    guidance = _latest_reject_feedback(mp)
    return {"reject_guidance": guidance} if guidance else {}


def report_board_results(daemon, mirror: "BoardMirror") -> "list[str]":
    """自分がオーナーとして公示済みの委譲のうち、ミッションが終端に達したものを board の
    result.json へ書き戻す（依頼側 agent-project 等の自動回収先。board は「リポジトリ＋契約」
    だけで処理を持たないため、この報告は請負側＝このデーモンの責務）。speculation 無し（既定・
    単一落札）を前提に、落札ノード自身（＝ミッションオーナー）が確定点を書く。冪等
    （result.json が既にあれば触らない）。書き戻した委譲 id の一覧を返す。"""
    deleg_root = os.path.join(mirror.dir, "delegations")
    reported = []
    if not os.path.isdir(deleg_root):
        return reported
    for did in sorted(os.listdir(deleg_root)):
        ddir = os.path.join(deleg_root, did)
        if not os.path.isdir(ddir) or os.path.exists(os.path.join(ddir, "result.json")):
            continue
        status_path = os.path.join(ddir, "status", f"{protocol.safe_name(daemon.node_id)}.json")
        st = read_json(status_path)
        if not st or st.get("state") != "dispatched":
            continue    # 自分が落札した委譲ではない（または既に終端まで報告済み）
        mp = daemon.bus.mission(did)
        if not mp.exists():
            continue
        try:
            mission = load_mission(mp)
        except SystemExit:
            continue
        roles = active_roles(load_roles(mp), mp)
        phase = derive_phase(mission, roles, mp)
        if not vocab.is_terminal(phase):
            continue    # まだ working/integrating/reviewing 等
        payload = {"winner": daemon.node_id, "native_id": did, "status": phase,
                  "resolved_by": daemon.node_id, "resolved_at": now_iso()}
        payload.update(_delegation_result_extras(mp, phase))
        write_json_atomic(os.path.join(ddir, "result.json"), payload)
        write_json_atomic(status_path, {**st, "state": phase, "heartbeat": now_iso()})
        reported.append(did)
        log(daemon.node_id, f"board 成果報告 {did}: {phase}")
    if reported:
        mirror.sync_push(f"report {len(reported)} results")
    return reported


def _write_or_renew_bid(bids_dir: str, who: str, lease: float, workload: str) -> bool:
    """bids/<who>.json を書く／更新する。既存が無ければ新規（ts はいま）、あれば残 lease が
    半分未満のときだけ lease_until を延長する（(ts, who) タイブレークの根拠 ts は温存し、
    毎 poll 書き換えて先勝ちの意味を壊さない・push 頻度も抑える）。書いたら True。
    共通実装は agentcore.protocol.renew_lease（agent-project の同種心拍延長と同じ規律）。"""
    return protocol.renew_lease(bids_dir, who, lease, extra={"workload": workload})


def _renew_dispatched_leases(daemon, mirror: "BoardMirror", lease: float) -> None:
    """自分がオーナーとして公示済み・まだ終端していない委譲の bid lease を延長する（設計 §5.2 の
    「status/<who>.json のハートビートで延長」）。延長しないと長時間ミッションが board_lease
    （既定 900 秒）を超えたときに他ノードから見て勝者が失効し、再入札→二重実行が起こりうる
    （§8「落札ノードのクラッシュ」検知はこの心拍の停止で成立する——延長を止める＝クラッシュ扱い）。"""
    deleg_root = os.path.join(mirror.dir, "delegations")
    if not os.path.isdir(deleg_root):
        return
    renewed = False
    for did in sorted(os.listdir(deleg_root)):
        ddir = os.path.join(deleg_root, did)
        if not os.path.isdir(ddir) or os.path.exists(os.path.join(ddir, "result.json")) or \
           os.path.exists(os.path.join(ddir, "cancelled.json")):
            continue
        status_path = os.path.join(ddir, "status", f"{protocol.safe_name(daemon.node_id)}.json")
        st = read_json(status_path)
        state = st.get("state") if st else None
        # 板の上の値を**読む**ので `is_terminal_read`（旧綴り `canceled` も終端）。
        if not st or state == "away" or vocab.is_terminal_read(state):
            continue    # 自分が落札した委譲ではない（または既に終端/away）
        if _write_or_renew_bid(os.path.join(ddir, "bids"), daemon.node_id, lease, "amigos"):
            write_json_atomic(status_path, {**st, "heartbeat": now_iso(),
                                            "lease_until": time.time() + lease})
            renewed = True
    if renewed:
        mirror.sync_push(f"lease renew by {daemon.node_id}")


def poll_board(daemon) -> "list[str]":
    """板を 1 巡: まず自分がオーナー公示済みの委譲の完了を board へ報告し、実行中のものは bid
    lease を延長し、次に workload=amigos の公示に入札する。policy.assignment が既定の first-come
    なら claim 勝者＝即落札（オーナーとしてミッション公示）。owner-picks なら bid（応募）を書く
    だけに留め、依頼者が award.json で自分を指名したときだけ落札として公示する（設計 §5.2）。
    公示した委譲 id の一覧を返す（報告は別途 report_board_results の返り値）。board 未設定
    なら no-op。"""
    spec = getattr(daemon, "board", None)
    if not spec:
        return []
    mirror = BoardMirror(spec, daemon.node_id, getattr(daemon, "board_workdir", None))
    mirror.sync_pull()
    report_board_results(daemon, mirror)
    node_repos = getattr(daemon, "repos", None) or {}
    node_tags = getattr(daemon, "tags", None) or []
    node_cli, node_workloads, max_concurrent = _node_board_declaration(daemon)
    own_rec = read_json(os.path.join(mirror.dir, "nodes",
                                     f"{protocol.safe_name(daemon.node_id)}.json"))
    own_budget = _boardrules.budget_kwargs_from_node(
        own_rec if isinstance(own_rec, dict) else None)
    lease = float(getattr(daemon, "board_lease", None) or 900.0)
    _renew_dispatched_leases(daemon, mirror, lease)
    home = daemon.commands_home or daemon.home or os.getcwd()
    deleg_root = os.path.join(mirror.dir, "delegations")
    handed = []
    if not os.path.isdir(deleg_root):
        return handed
    # 板上で自分がいま預かっている件数（枠の自己抑制・P2-3）。**ループの外で 1 度だけ**
    # 数え、この 1 巡で落札するたびに +1 する（委譲ごとに走査すると O(n²) になる）。
    inflight = _boardrules.node_inflight(mirror.dir, daemon.node_id) if max_concurrent else 0
    for did in sorted(os.listdir(deleg_root)):
        ddir = os.path.join(deleg_root, did)
        if not os.path.isdir(ddir):
            continue
        if os.path.exists(os.path.join(ddir, "result.json")) or \
           os.path.exists(os.path.join(ddir, "cancelled.json")):
            continue
        post = read_json(os.path.join(ddir, "post.json"))
        if not isinstance(post, dict) or post.get("workload") != "amigos" or post.get("op") != "post":
            continue
        # 既にこのミッションを公示済み（自分の bus にある）ならスキップ
        if daemon.bus.mission(did).exists():
            continue
        if not board_eligible(post, node_repos, node_tags, node_cli,
                              node_workloads=node_workloads,
                              max_concurrent=max_concurrent, inflight=inflight,
                              **own_budget):
            continue
        bids_dir = os.path.join(ddir, "bids")
        assignment = str((post.get("policy") or {}).get("assignment") or "first-come")
        if assignment == "owner-picks":
            # 先勝ちタイブレークでは決めない。bid ＝応募として書くだけで、依頼者が
            # award.json を書いた者だけが落札する（設計 §5.2）。
            award = read_json(os.path.join(ddir, "award.json"))
            awarded_node = award.get("node") if isinstance(award, dict) else None
            if awarded_node is None:
                if _write_or_renew_bid(bids_dir, daemon.node_id, lease, "amigos"):
                    mirror.sync_push(f"apply {did} by {daemon.node_id}")
                continue
            if awarded_node != daemon.node_id:
                continue    # 他ノードが落札
            # 自分が award された → 落札として下のミッション公示へ進む
        else:
            w = _winner(bids_dir)
            if w is not None and w != daemon.node_id:
                continue
            if not _try_bid(mirror, bids_dir, did, daemon.node_id, lease):
                continue
        # 落札 → オーナーとしてミッションを公示（board の落札 = ミッションオーナーの決定）
        try:
            _do_post(daemon.bus, daemon.node_id, home, _post_to_command(post))
        except (ValueError, RuntimeError, OSError, SystemExit, KeyError) as e:
            write_json_atomic(os.path.join(ddir, "status", f"{protocol.safe_name(daemon.node_id)}.json"), {
                "who": daemon.node_id, "state": "failed", "error": str(e),
                "heartbeat": now_iso()})
            mirror.sync_push(f"handoff-failed {did}")
            continue
        write_json_atomic(os.path.join(ddir, "status", f"{protocol.safe_name(daemon.node_id)}.json"), {
            "who": daemon.node_id, "state": "dispatched", "native_id": did,
            "heartbeat": now_iso(), "lease_until": time.time() + lease})
        mirror.sync_push(f"won+dispatch {did} by {daemon.node_id}")
        handed.append(did)
        inflight += 1
        log(daemon.node_id, f"board 落札→ミッション公示 {did}: {str(post.get('goal',''))[:50]}")
    return handed
