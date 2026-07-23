"""daemon — ノード登録とサーブループ（ポーリング入札 → 落札 → 引き渡し）。

各ノードで `agent-board serve` を回すと、板を巡回して入札資格のある公示に入札し、落札したら
ローカルエンジンへ引き渡す。真実は板のファイルにあり、プロセスはステートレス。中央（forge /
hub）が落ちても壊れず、回復後に同期が追いつくだけ。
"""
from __future__ import annotations

import sys
import time

from . import board as _board
from .core import Bus, now_iso


def log(node_id: str, msg: str) -> None:
    print(f"[agent-board:{node_id}] {msg}", file=sys.stderr)


def build_node_record(node_id: str, settings: dict) -> dict:
    rec = {
        "node": node_id,
        "workloads": settings.get("workloads") or [],
        "tags": settings.get("tags") or [],
        "agent_cli": settings.get("agent_cli") or [],
        "repos": settings.get("repos") or {},
        "heartbeat": now_iso(),
        "fresh_after_sec": max(120.0, float(settings.get("interval") or 15.0) * 2),
    }
    if settings.get("availability"):
        rec["availability"] = settings["availability"]
    if settings.get("max_concurrent"):
        rec["max_concurrent"] = int(settings["max_concurrent"])
    return rec


def register_node(bus: Bus, node_id: str, settings: dict) -> None:
    bus.ensure_root()
    bus.sync_pull()
    bus.write_node(build_node_record(node_id, settings))
    bus.sync_push(f"register node {node_id}")


def _active_won(bus: Bus, node_id: str) -> int:
    """自分が落札していて未終端の委譲数（max_concurrent の判定用）。"""
    n = 0
    for did in bus.list_delegations():
        if bus.has_result(did) or bus.is_cancelled(did):
            continue
        if bus.winner(did) == node_id:
            n += 1
    return n


def _dispatch_ok(bus: Bus, did: str, env: dict, node_id: str) -> bool:
    """自分が勝者のとき、いま引き渡してよいか。first-come は即可、owner-picks は award 待ち。"""
    assignment = (env.get("policy") or {}).get("assignment", "first-come")
    if assignment == "owner-picks":
        award = bus.read_award(did)
        return bool(award) and award.get("node") == node_id
    return True


def _already_dispatched(bus: Bus, did: str, node_id: str) -> bool:
    st = bus.read_status(did, node_id) or {}
    return bool(st.get("handoff"))


def _do_dispatch(bus: Bus, did: str, env: dict, node_id: str, settings: dict) -> bool:
    """落札済みの委譲をローカルエンジンへ引き渡す。成功で True。"""
    lease = float(settings.get("lease") or 900.0)
    try:
        path = _board.handoff(env, flow_bus_dir=settings.get("flow_bus") or "",
                              amigos_home=settings.get("amigos_home") or "", node_id=node_id)
    except ValueError as e:
        log(node_id, f"引き渡し失敗 {did}: {e}")
        bus.write_status(did, node_id, {"state": "failed", "error": str(e)})
        bus.sync_push(f"handoff-failed {did}")
        return False
    bus.write_status(did, node_id, {"state": "dispatched", "native_id": did,
                                    "lease_until": time.time() + lease, "handoff": path})
    bus.sync_push(f"won+dispatch {did} by {node_id}")
    log(node_id, f"落札→引き渡し {did}（{env.get('workload')}）: {path}")
    return True


def serve_cycle(bus: Bus, node_id: str, settings: dict) -> "list[str]":
    """1 巡回。落札→引き渡しした委譲 id の一覧を返す（テスト・単発実行用）。"""
    bus.sync_pull()
    node = build_node_record(node_id, settings)
    bus.write_node(node)
    handed = []
    max_conc = int(settings.get("max_concurrent") or 0)
    lease = float(settings.get("lease") or 900.0)

    for did in bus.list_delegations():
        if bus.has_result(did) or bus.is_cancelled(did):
            continue
        env = bus.read_post(did)
        if not isinstance(env, dict) or env.get("op") != "post":
            continue

        win = bus.winner(did)

        # 既に自分が落札済み
        if win == node_id:
            bus.extend_bid(did, node_id, lease)
            if not _already_dispatched(bus, did, node_id) and _dispatch_ok(bus, did, env, node_id):
                if _do_dispatch(bus, did, env, node_id, settings):
                    handed.append(did)
            else:
                st = bus.read_status(did, node_id) or {}
                bus.write_status(did, node_id, {
                    "state": st.get("state", "dispatched" if st.get("handoff") else "applied"),
                    "native_id": did, "lease_until": time.time() + lease,
                    **({"handoff": st["handoff"]} if st.get("handoff") else {"applied": True})})
            continue

        # 既に他者が勝者（lease 内）→ 触らない（先勝ち）
        if win is not None:
            continue

        # 未落札: 入札資格・同時上限を見て入札
        ok, _why = _board.node_eligible(node, env)
        if not ok:
            continue
        if max_conc and _active_won(bus, node_id) >= max_conc:
            continue
        won = bus.try_bid(did, node_id, lease,
                          {"workload": env.get("workload"),
                           "agent_cli": (settings.get("agent_cli") or [None])[0]})
        if not won:
            continue
        # 入札成立 → 落札可能なら即引き渡し（first-come）。owner-picks は応募のまま
        if _dispatch_ok(bus, did, env, node_id):
            if _do_dispatch(bus, did, env, node_id, settings):
                handed.append(did)
        else:
            bus.write_status(did, node_id, {"state": "applied", "applied": True})
            bus.sync_push(f"applied {did} by {node_id}")
    return handed


def serve(bus: Bus, node_id: str, settings: dict, cycles: "int | None" = None) -> None:
    register_node(bus, node_id, settings)
    interval = float(settings.get("interval") or 15.0)
    log(node_id, f"serve 開始（board={bus.root}・interval={interval}s）")
    i = 0
    while cycles is None or i < cycles:
        try:
            serve_cycle(bus, node_id, settings)
        except Exception as e:  # pragma: no cover — ループは 1 巡の失敗で止めない
            log(node_id, f"巡回エラー: {e}")
        i += 1
        if cycles is None or i < cycles:
            time.sleep(interval)
