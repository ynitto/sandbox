"""アサインプロトコル — claim → 決定的勝者 → roster 確定 → 自己補充（設計書 §6）。

agent-flow の claim プロトコルの流用: 各ノードは自分名義ファイル
`assignments/<role>/<node>.json` を書くだけ（add/add コンフリクトなし）。
勝者は lease 内の全 claim のうち (ts, node) 昇順の先頭 seats 件に決定的に定まり、
ローカルでも git でも全ノードが同じ勝者を導く。
"""
from __future__ import annotations

import time

import os

from .bus import Bus, MissionPaths, read_all_json
from .util import now_iso, read_json, unique_ts, write_json_atomic

DEFAULT_LEASE = 600.0


def default_lease() -> float:
    """claim の lease 秒。環境変数 AGENT_AMIGOS_LEASE で上書き可能
    （テスト・短周期運用向け。lease は liveness の信号、§6.5）。"""
    try:
        return float(os.environ.get("AGENT_AMIGOS_LEASE", DEFAULT_LEASE))
    except ValueError:
        return DEFAULT_LEASE


def claim_role(bus: Bus, mp: MissionPaths, role_id: str, node_id: str,
               agent_cli: "str | None" = None, lease: "float | None" = None) -> bool:
    """ロールに応募し、勝者になったかを返す（agent-flow §5.1 と同じ 3 手順:
    書く → push → pull → 決定的タイブレークで勝者確認）。
    claim の pull は force（間隔律速なし）: 勝者確認の鮮度はプロトコルの正しさに効く。"""
    bus.sync_pull(force=True)
    if winner(mp, role_id) not in (None, node_id):
        return False
    write_json_atomic(mp.assignment(role_id, node_id),
                      {"node": node_id, "ts": unique_ts(), "agent_cli": agent_cli,
                       "lease_until": time.time() + (lease if lease is not None
                                                     else default_lease()),
                       "claimed_at": now_iso()})
    bus.sync_push(f"claim {role_id} by {node_id}")
    bus.sync_pull(force=True)
    return winner(mp, role_id) == node_id


def live_claims(mp: MissionPaths, role_id: str) -> list:
    """lease 内の claim（(ts, node) 昇順）。期限切れは孤児として無視する。"""
    claims = []
    for node, data in read_all_json(mp.assignments_dir(role_id)).items():
        try:
            if float(data.get("lease_until") or 0) < time.time():
                continue
            claims.append((float(data.get("ts") or 0), str(data.get("node") or node), data))
        except (TypeError, ValueError):
            continue
    return sorted(claims)


def winner(mp: MissionPaths, role_id: str) -> "str | None":
    """決定的タイブレーク: lease 内 claim の (ts, node) 最小 1 件（seats=1、P0）。"""
    claims = live_claims(mp, role_id)
    return claims[0][1] if claims else None


def renew_lease(mp: MissionPaths, role_id: str, node_id: str,
                lease: "float | None" = None) -> None:
    """ハートビート: 自分の claim の lease を延長する（自分名義ファイルの上書きのみ）。
    残りが半分以上あるうちは書かない — git バスでの無駄なコミットを作らない
    （state_git の「アイドル中の追加コミットはゼロ」の流儀）。"""
    eff = lease if lease is not None else default_lease()
    path = mp.assignment(role_id, node_id)
    data = read_json(path)
    if isinstance(data, dict) and data.get("node") == node_id:
        if float(data.get("lease_until") or 0) - time.time() > eff / 2:
            return
        data["lease_until"] = time.time() + eff
        write_json_atomic(path, data)


def matches_role(role: dict, node_tags: "list[str]", node_clis: "list[str]") -> bool:
    """ロール要件とノード能力のマッチング（設計書 §6.1）。"""
    req = role.get("requires") or {}
    need_tags = set(str(t) for t in (req.get("tags") or []))
    if need_tags and not need_tags.issubset(set(node_tags)):
        return False
    need_cli = req.get("cli")
    if need_cli and node_clis and str(need_cli) not in node_clis:
        return False
    return True


DEFAULT_AWAY_GRACE = 7200.0


def away_grace() -> float:
    """away の resume_at からの猶予秒（設計書 §6.6。既定 2 時間）。"""
    try:
        return float(os.environ.get("AGENT_AMIGOS_AWAY_GRACE", DEFAULT_AWAY_GRACE))
    except ValueError:
        return DEFAULT_AWAY_GRACE


def _iso_to_epoch(iso: str) -> float:
    import calendar
    try:
        return calendar.timegm(time.strptime(str(iso or ""), "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0.0


def is_away_within_grace(mp: MissionPaths, role_id: str, node_id: str) -> bool:
    """担当が計画停止（away）中で、まだ待つべきか（設計書 §6.6:
    計画停止ではロールを奪わない。resume_at + grace までは本人の復帰を待つ）。"""
    st = read_json(mp.status(f"{node_id}--{role_id}")) or {}
    if st.get("state") != "away":
        return False
    resume = _iso_to_epoch(st.get("resume_at"))
    return time.time() < resume + away_grace()


def mirror_roster(bus: Bus, mp: MissionPaths, roles: "dict[str, dict]",
                  owner_node: str) -> dict:
    """first-come: claim 勝者＝確定。オーナーが導出結果を roster.json に鏡写しする
    （表示・監査用。設計書 §6.3）。roster はオーナーのみ書く。

    away 保持（§6.6）: 担当の lease が切れていても `state: away` かつ
    resume_at + grace 内なら roster から外さない（再募集しない）。
    grace 超過またはクラッシュ（away 宣言なし）は通常の再募集に戻る。"""
    roster = read_json(mp.roster()) or {}
    changed = False
    for role_id in roles:
        w = winner(mp, role_id)
        cur = (roster.get(role_id) or {}).get("node")
        if w and cur != w:
            if cur and is_away_within_grace(mp, role_id, cur):
                continue     # away 中の担当を横取り claim から守る
            claim = read_json(mp.assignment(role_id, w)) or {}
            roster[role_id] = {"node": w, "agent_cli": claim.get("agent_cli"),
                               "confirmed_at": now_iso()}
            changed = True
        elif cur and not w:
            if is_away_within_grace(mp, role_id, cur):
                continue     # 計画停止 → ロール保持のまま復帰を待つ
            # 担当消滅（lease 失効・away 宣言なし = クラッシュ）→ 再募集へ
            del roster[role_id]
            changed = True
    if changed:
        write_json_atomic(mp.roster(), roster)
        bus.sync_push("roster")
    return roster


def unfilled_required(roles: "dict[str, dict]", roster: dict) -> list:
    return sorted(r["id"] for r in roles.values()
                  if r.get("required") and r["id"] not in roster)


def staffing_expired(mission: dict) -> bool:
    """公示から staffing_timeout 経過したか（自己補充の発動条件、設計書 §6.4）。"""
    posted = mission.get("posted_at") or ""
    try:
        import calendar
        t = calendar.timegm(time.strptime(posted, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return True
    return time.time() - t >= float(mission.get("staffing_timeout") or 0)
