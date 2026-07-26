"""アサインプロトコル — claim → 決定的勝者 → roster 確定 → 自己補充（設計書 §5.1）。

各ノードは自分名義ファイル `assignments/<role>/<node>.json` を書くだけ（add/add
コンフリクトなし）。勝者は lease 内の全 claim のうち (ts, node) 昇順の先頭 seats 件に
決定的に定まり、ローカルでも git でも全ノードが同じ勝者を導く。claim/lease の実体は
agentcore.protocol（flow のタスク claim・板の入札と共通の (ts, who) タイブレーク・
lease 延長アルゴリズム — 設計 §4.1・R1）。claim レコードは `who` だけでなく歴史的な
`node` フィールド（roster/CLI/他モジュールが直接読む）も持たせて後方互換を保つ。
"""
from __future__ import annotations

import time

import os

from agentcore import protocol

from .bus import Bus, MissionPaths
from .util import iso_to_epoch, now_iso, read_json, write_json_atomic

DEFAULT_LEASE = 600.0


def default_lease() -> float:
    """claim の lease 秒。環境変数 AGENT_AMIGOS_LEASE で上書き可能
    （テスト・短周期運用向け。lease は liveness の信号、§5.3）。"""
    try:
        return float(os.environ.get("AGENT_AMIGOS_LEASE", DEFAULT_LEASE))
    except ValueError:
        return DEFAULT_LEASE


def claim_role(bus: Bus, mp: MissionPaths, role_id: str, node_id: str,
               agent_cli: "str | None" = None, lease: "float | None" = None) -> bool:
    """ロールに応募し、勝者になったかを返す（agentcore.protocol.try_claim の 3 手順:
    書く → push → pull → 決定的タイブレークで勝者確認。負ければ自分の claim を取り下げる）。
    claim の pull は force（間隔律速なし）: 勝者確認の鮮度はプロトコルの正しさに効く。"""
    eff_lease = lease if lease is not None else default_lease()
    claim_dir = mp.assignments_dir(role_id)
    bus.sync_pull(force=True)
    if winner(mp, role_id) not in (None, node_id):
        return False
    return protocol.try_claim(
        claim_dir, node_id, eff_lease,
        on_write=lambda: bus.sync_push(f"claim {role_id} by {node_id}"),
        on_sync=lambda: bus.sync_pull(force=True),
        on_withdraw=lambda: bus.sync_push(f"claim withdraw {role_id} by {node_id}"),
        extra={"node": node_id, "agent_cli": agent_cli})


def apply_role(bus: Bus, mp: MissionPaths, role_id: str, node_id: str,
               agent_cli: "str | None" = None, lease: "float | None" = None) -> None:
    """owner-picks 用の応募: 自分名義の claim を書くだけで勝者判定はしない
    （確定はオーナーの roster 書き込み。設計書 §5.1）。既に応募済みなら lease を延長する
    （renew_lease に委ね、再応募時の agent_cli 変更は無視 — 既存応募を尊重する）。"""
    existing = read_json(mp.assignment(role_id, node_id))
    if isinstance(existing, dict) and existing.get("node") == node_id:
        renew_lease(mp, role_id, node_id, lease)
        return
    eff_lease = lease if lease is not None else default_lease()
    protocol.write_claim(mp.assignments_dir(role_id), node_id, eff_lease,
                         extra={"node": node_id, "agent_cli": agent_cli})
    bus.sync_push(f"apply {role_id} by {node_id}")


def live_claims(mp: MissionPaths, role_id: str) -> list:
    """lease 内の claim（(ts, node) 昇順）。期限切れは孤児として無視する。"""
    claims = []
    for node, data in protocol.list_claims(mp.assignments_dir(role_id)).items():
        try:
            if float(data.get("lease_until") or 0) < time.time():
                continue
            claims.append((float(data.get("ts") or 0), str(data.get("node") or node), data))
        except (TypeError, ValueError):
            continue
    return sorted(claims)


def winner(mp: MissionPaths, role_id: str) -> "str | None":
    """決定的タイブレーク: lease 内 claim の (ts, node) 最小 1 件（seats=1、P0）。
    実体は agentcore.protocol.winner。"""
    return protocol.winner(mp.assignments_dir(role_id))


def renew_lease(mp: MissionPaths, role_id: str, node_id: str,
                lease: "float | None" = None) -> None:
    """ハートビート: 自分の claim の lease を延長する（残りが半分以上あるうちは書かない —
    git バスでの無駄なコミットを作らない。実体は agentcore.protocol.renew_lease）。
    既存レコードの agent_cli を読み直して引き継ぐ（protocol.renew_lease は extra 分しか
    温存しないため、呼び出し側でフィールドを保つ必要がある）。

    **claim が消えていたら何もしない**（`create_if_missing=False`）。剪定・取り下げ・
    オーナーの再編で自分の claim が消えたあとに心拍が書き戻すと、誰も動いていない
    ロールを占有し続ける zombie 勝者になる。"""
    eff = lease if lease is not None else default_lease()
    claim_dir = mp.assignments_dir(role_id)
    existing = read_json(mp.assignment(role_id, node_id))
    if not isinstance(existing, dict):
        return
    extra = {"node": node_id}
    if "agent_cli" in existing:
        extra["agent_cli"] = existing["agent_cli"]
    protocol.renew_lease(claim_dir, node_id, eff, extra=extra, create_if_missing=False)


def _norm_repo_url(u: str) -> str:
    u = str(u or "").strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u.lower()


def _declared_repos(node_repos) -> "set[str]":
    """ノードの repos レジストリ（repos.schema.json 形）から担当リポジトリの名前と
    正規化 URL の集合を返す。requires.repos の突き合わせに使う。"""
    have: "set[str]" = set()
    if isinstance(node_repos, dict):
        for name, e in node_repos.items():
            if str(name).startswith("_") or not isinstance(e, dict):
                continue
            have.add(str(name))
            if e.get("url"):
                have.add(_norm_repo_url(e["url"]))
    elif isinstance(node_repos, list):
        for e in node_repos:
            if isinstance(e, dict):
                if e.get("name"):
                    have.add(str(e["name"]))
                if e.get("url"):
                    have.add(_norm_repo_url(e["url"]))
    return have


def matches_role(role: dict, node_tags: "list[str]", node_clis: "list[str]",
                 node_repos=None) -> bool:
    """ロール要件とノード能力のマッチング（設計書 §5.1）。
    requires.repos はノードが担当するリポジトリ（agent-amigos.yaml の repos:）で選別する
    — 成果物リポジトリに応じて入札するノードを絞る機構（board.schema.json の node と同語彙）。"""
    req = role.get("requires") or {}
    need_tags = set(str(t) for t in (req.get("tags") or []))
    if need_tags and not need_tags.issubset(set(node_tags)):
        return False
    need_cli = req.get("cli")
    if need_cli and node_clis and str(need_cli) not in node_clis:
        return False
    need_repos = req.get("repos") or []
    if need_repos:
        have = _declared_repos(node_repos)
        for r in need_repos:
            if str(r) not in have and _norm_repo_url(r) not in have:
                return False
    return True


DEFAULT_AWAY_GRACE = 7200.0


def away_grace() -> float:
    """away の resume_at からの猶予秒（設計書 §5.3。既定 2 時間）。"""
    try:
        return float(os.environ.get("AGENT_AMIGOS_AWAY_GRACE", DEFAULT_AWAY_GRACE))
    except ValueError:
        return DEFAULT_AWAY_GRACE


def is_away_within_grace(mp: MissionPaths, role_id: str, node_id: str) -> bool:
    """担当が計画停止（away）中で、まだ待つべきか（設計書 §5.3:
    計画停止ではロールを奪わない。resume_at + grace までは本人の復帰を待つ）。"""
    st = read_json(mp.status(f"{node_id}--{role_id}")) or {}
    if st.get("state") != "away":
        return False
    resume = iso_to_epoch(st.get("resume_at"))
    return time.time() < resume + away_grace()


def mirror_roster(bus: Bus, mp: MissionPaths, roles: "dict[str, dict]",
                  owner_node: str, policy: str = "first-come") -> dict:
    """roster の維持（オーナーのみ書く。設計書 §5.1）。

    - first-come: claim 勝者＝確定。導出結果を roster.json に鏡写しする（表示・監査用）。
    - owner-picks: claim は「応募」。自動確定はせず、オーナーの明示アサイン
      （confirm_assignment）だけが roster を埋める。ここでは離脱の掃除のみ行う。

    away 保持（§5.3）: 担当の lease が切れていても `state: away` かつ
    resume_at + grace 内なら roster から外さない（再募集しない）。
    grace 超過またはクラッシュ（away 宣言なし）は通常の再募集に戻る。"""
    roster = read_json(mp.roster()) or {}
    changed = False
    for role_id in roles:
        cur = (roster.get(role_id) or {}).get("node")
        if policy == "first-come":
            w = winner(mp, role_id)
            if w and cur != w:
                if cur and is_away_within_grace(mp, role_id, cur):
                    continue     # away 中の担当を横取り claim から守る
                claim = read_json(mp.assignment(role_id, w)) or {}
                roster[role_id] = {"node": w, "agent_cli": claim.get("agent_cli"),
                                   "confirmed_at": now_iso()}
                changed = True
                continue
        # 担当消滅の掃除（両ポリシー共通）: lease 失効かつ away でない = クラッシュ → 再募集
        if cur:
            claim = read_json(mp.assignment(role_id, cur)) or {}
            lease_alive = float(claim.get("lease_until") or 0) >= time.time()
            if not lease_alive and not is_away_within_grace(mp, role_id, cur):
                del roster[role_id]
                changed = True
    if changed:
        write_json_atomic(mp.roster(), roster)
        bus.sync_push("roster")
    return roster


def confirm_assignment(bus: Bus, mp: MissionPaths, role_id: str, node_id: str) -> dict:
    """owner-picks: オーナーが応募者を確定する（roster への明示書き込み。設計書 §5.1）。
    応募（claim）が実在することを検証する。"""
    claim = read_json(mp.assignment(role_id, node_id))
    if not isinstance(claim, dict) or claim.get("node") != node_id:
        raise SystemExit(f"[agent-amigos] {node_id} はロール {role_id} に応募していません")
    roster = read_json(mp.roster()) or {}
    roster[role_id] = {"node": node_id, "agent_cli": claim.get("agent_cli"),
                       "confirmed_at": now_iso()}
    write_json_atomic(mp.roster(), roster)
    bus.sync_push(f"assign {role_id} -> {node_id}")
    return roster


def applicants(mp: MissionPaths, role_id: str) -> list:
    """ロールへの有効な応募者一覧（(ts, node) 昇順 = 応募順）。owner-picks の判断材料。"""
    return [{"node": node, "agent_cli": data.get("agent_cli"),
             "claimed_at": data.get("claimed_at")}
            for _ts, node, data in live_claims(mp, role_id)]


def unfilled_required(roles: "dict[str, dict]", roster: dict) -> list:
    return sorted(r["id"] for r in roles.values()
                  if r.get("required") and r["id"] not in roster)


def staffing_expired(mission: dict) -> bool:
    """公示から staffing_timeout 経過したか（自己補充の発動条件、設計書 §5.2）。"""
    posted = mission.get("posted_at") or ""
    try:
        import calendar
        t = calendar.timegm(time.strptime(posted, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return True
    return time.time() - t >= float(mission.get("staffing_timeout") or 0)
