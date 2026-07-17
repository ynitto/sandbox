"""ミッション — 公示（post）・正規化・状態導出・収束条件と予算会計。

状態は専用フィールドを持たず**ファイルの存在から導出**する（設計書 §3.1・§4.3 の継承）。
予算は wall-clock でなく**実質実行時間**（events の cli_seconds 総和）で、
どのノードが計算しても同じ値になる（設計書 §3.2）。
"""
from __future__ import annotations

import os
import random
import shutil
import time

from .bus import Bus, MissionPaths, read_all_json
from .util import now_iso, read_json, read_jsonl, write_json_atomic

# mission.json / roles/<id>.json は正規化 JSON をバスに置く（読み手に PyYAML を要求しない）。
# YAML はオーナーの入力形式（post 時に変換）。

DEFAULTS = {
    "assignment_policy": "first-come",
    "staffing_policy": "self-staff",
    "staffing_timeout": 600,
    "acceptance": "manual",
}
CONVERGENCE_DEFAULTS = {
    "done_when": "all-required-done",   # all-required-done | reviewer-approved
    "quiescence_turns": 3,
    "review_rounds": 2,
    "question_timeout": 2,              # 未回答質問を owner へ昇格するまでの自ターン数（§7.3）
}
BUDGET_DEFAULTS = {
    "execution_minutes": 0,             # 0 = 無制限
    "per_role_turns": 30,
    "soft_ratio": 0.9,
    "on_exhausted": "wrap-up",          # wrap-up | fail
}


def _load_spec_file(path: str) -> dict:
    if path.lower().endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except ImportError:
            raise SystemExit("[agent-amigos] YAML の役割ミッション表には PyYAML が必要です"
                             "（pip install pyyaml、または JSON で渡してください）")
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    data = read_json(path)
    if data is None:
        raise SystemExit(f"[agent-amigos] 役割ミッション表を読めません: {path}")
    return data


def normalize_mission(spec: dict) -> "tuple[dict, list]":
    """roles.yaml の内容を検証し (mission 設定, ロール定義列) に正規化する。"""
    m = dict(spec.get("mission") or {})
    roles_in = spec.get("roles")
    if not isinstance(roles_in, list) or not roles_in:
        raise SystemExit("[agent-amigos] roles が空です（役割ミッション表には 1 つ以上のロールが必要）")
    mission = {**DEFAULTS,
               "title": str(m.get("title") or "untitled"),
               "goal": str(m.get("goal") or ""),
               "deadline": m.get("deadline")}
    for k in ("assignment_policy", "staffing_policy", "acceptance"):
        if m.get(k) is not None:
            mission[k] = str(m[k])
    if m.get("staffing_timeout") is not None:
        mission["staffing_timeout"] = float(m["staffing_timeout"])
    if mission["assignment_policy"] != "first-come":
        raise SystemExit(f"[agent-amigos] assignment_policy={mission['assignment_policy']!r} は"
                         " P0 未対応です（first-come のみ。owner-picks は P2）")
    if mission["acceptance"] != "manual":
        raise SystemExit(f"[agent-amigos] acceptance={mission['acceptance']!r} は P0 未対応です"
                         "（manual のみ。agent / codd-gate は P2）")
    mission["convergence"] = {**CONVERGENCE_DEFAULTS, **dict(m.get("convergence") or {})}
    if mission["convergence"]["done_when"] not in ("all-required-done", "reviewer-approved"):
        raise SystemExit(f"[agent-amigos] convergence.done_when が不正です: "
                         f"{mission['convergence']['done_when']!r}")
    mission["budget"] = {**BUDGET_DEFAULTS, **dict(m.get("budget") or {})}
    mission["workspace"] = dict(m.get("workspace") or {})

    roles = []
    seen = set()
    has_integrator = False
    for r in roles_in:
        rid = str(r.get("id") or "").strip()
        if not rid or "/" in rid or rid in ("all", "owner"):
            raise SystemExit(f"[agent-amigos] ロール id が不正です: {rid!r}"
                             "（all / owner は予約語）")
        if rid in seen:
            raise SystemExit(f"[agent-amigos] ロール id が重複しています: {rid!r}")
        seen.add(rid)
        role = {"id": rid,
                "title": str(r.get("title") or rid),
                "mission": str(r.get("mission") or ""),
                "deliverables": [str(d) for d in (r.get("deliverables") or [])],
                "required": bool(r.get("required", True)),
                "seats": int(r.get("seats", 1)),
                "agent_cli": r.get("agent_cli"),
                "model": r.get("model"),
                "requires": dict(r.get("requires") or {}),
                "collaborates_with": [str(c) for c in (r.get("collaborates_with") or [])],
                "approver": bool(r.get("approver", False)),
                "builtin": str(r.get("builtin") or "")}
        if role["seats"] != 1:
            raise SystemExit(f"[agent-amigos] seats>1 は P0 未対応です（ロール {rid}）")
        if role["builtin"] == "integrator":
            has_integrator = True
        roles.append(role)
    for role in roles:
        for c in role["collaborates_with"]:
            if c not in seen:
                raise SystemExit(f"[agent-amigos] ロール {role['id']} の collaborates_with に"
                                 f" 未定義ロール {c!r} があります")
    if not has_integrator:
        # integrator 省略時はオーナーノードが self-staff する組み込みロールを自動追加（§8.1）
        roles.append({"id": "integrator", "title": "統合", "mission":
                      "全ロールの成果物を検証・統合し deliverable/ を組み立てる。",
                      "deliverables": [], "required": True, "seats": 1, "agent_cli": None,
                      "model": None, "requires": {}, "collaborates_with": [],
                      "approver": False, "builtin": "integrator"})
    return mission, roles


def new_mission_id() -> str:
    return f"am-{time.strftime('%Y%m%d%H%M%S')}-{random.randint(0, 0xffff):04x}"


def post_mission(bus: Bus, design_doc_path: str, roles_path: str, owner_node: str,
                 mission_id: "str | None" = None) -> str:
    """公示: mission.json / design-doc.md / roles/<id>.json を書き、募集を開始する。"""
    spec = _load_spec_file(roles_path)
    mission, roles = normalize_mission(spec)
    if not os.path.isfile(design_doc_path):
        raise SystemExit(f"[agent-amigos] design doc が見つかりません: {design_doc_path}")
    mid = mission_id or new_mission_id()
    mp = bus.mission(mid)
    if mp.exists():
        raise SystemExit(f"[agent-amigos] ミッション {mid} は既に存在します")
    bus.sync_pull()
    mission_doc = {**mission, "id": mid, "owner_node": owner_node, "posted_at": now_iso()}
    for role in roles:
        write_json_atomic(mp.role_json(role["id"]), role)
    shutil.copyfile(design_doc_path, mp.design_doc())
    write_json_atomic(mp.mission_json(), mission_doc)   # mission.json は最後（公示の宣言）
    bus.sync_push(f"post {mid}")
    return mid


def load_mission(mp: MissionPaths) -> dict:
    doc = read_json(mp.mission_json())
    if not isinstance(doc, dict):
        raise SystemExit(f"[agent-amigos] ミッションが見つかりません: {mp.mission_id}")
    return doc


def load_roles(mp: MissionPaths) -> "dict[str, dict]":
    return read_all_json(mp.roles_dir())


# --- 予算会計（決定的） ------------------------------------------------------

def budget_spent_seconds(mp: MissionPaths) -> float:
    """消費 = バス上の全 events の cli_seconds 総和（設計書 §3.2）。"""
    total = 0.0
    try:
        names = sorted(os.listdir(mp.events_dir()))
    except FileNotFoundError:
        return 0.0
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        for rec in read_jsonl(os.path.join(mp.events_dir(), name)):
            try:
                total += float(rec.get("cli_seconds") or 0.0)
            except (TypeError, ValueError):
                continue
    return total


def budget_state(mission: dict, mp: MissionPaths) -> dict:
    """予算の消費状況: {limit_s, spent_s, soft, hard}（limit_s=0 は無制限）。"""
    budget = mission.get("budget") or {}
    limit_s = float(budget.get("execution_minutes") or 0) * 60.0
    spent = budget_spent_seconds(mp)
    soft = bool(limit_s and spent >= limit_s * float(budget.get("soft_ratio") or 0.9))
    hard = bool(limit_s and spent >= limit_s)
    return {"limit_s": limit_s, "spent_s": spent, "soft": soft, "hard": hard}


# --- ラウンド（差し戻し）と完了宣言 ------------------------------------------

def current_round(mp: MissionPaths) -> int:
    """ラウンド = 差し戻し（rejections/）の件数。declare_done はラウンド付きで記録され、
    差し戻し後は旧ラウンドの done が自動的に無効になる（ファイル導出・書き換えなし）。"""
    try:
        return len([n for n in os.listdir(mp.rejections_dir()) if n.endswith(".json")])
    except FileNotFoundError:
        return 0


def load_statuses(mp: MissionPaths) -> "dict[str, dict]":
    return read_all_json(mp.status_dir())


def role_status(mp: MissionPaths, statuses: "dict[str, dict]", role_id: str) -> "dict | None":
    """ロールの現担当 amigo の status（roster 確定者のもの）。"""
    roster = read_json(mp.roster()) or {}
    ent = roster.get(role_id)
    if not ent:
        return None
    return statuses.get(f"{ent['node']}--{role_id}")


# --- 収束判定（設計書 §3.2） -------------------------------------------------

def convergence_state(mission: dict, roles: "dict[str, dict]", mp: MissionPaths) -> dict:
    """収束状況を導出する。returns:
    {staffed, converged, reason, partial, round, budget, done_roles, unanswered}
    reason: done | quiescence | budget | None
    """
    from .messages import unanswered_questions   # 循環回避の遅延 import
    rnd = current_round(mp)
    statuses = load_statuses(mp)
    roster = read_json(mp.roster()) or {}
    required = [r for r in roles.values() if r.get("required")]
    staffed = all(r["id"] in roster for r in required)
    budget = budget_state(mission, mp)
    conv = mission.get("convergence") or {}

    done_roles = []
    approved_roles = []
    for r in roles.values():
        st = role_status(mp, statuses, r["id"])
        if st and st.get("done_round") == rnd:
            done_roles.append(r["id"])
        if st and st.get("approved_round") == rnd:
            approved_roles.append(r["id"])
    workers = [r for r in required if r.get("builtin") != "integrator"]
    all_done = staffed and all(r["id"] in done_roles for r in workers)
    if conv.get("done_when") == "reviewer-approved":
        approvers = [r for r in roles.values() if r.get("approver")]
        all_done = all_done and all(r["id"] in approved_roles for r in approvers)

    unanswered = len(unanswered_questions(mp, roles))
    q_turns = int(conv.get("quiescence_turns") or 0)
    quiescent = False
    if staffed and q_turns > 0 and workers:
        idles = []
        for r in workers:
            st = role_status(mp, statuses, r["id"])
            idles.append(int((st or {}).get("idle_turns") or 0))
        quiescent = min(idles) >= q_turns and unanswered == 0

    converged, reason, partial = False, None, False
    if all_done:
        converged, reason = True, "done"
    elif budget["hard"] and (mission.get("budget") or {}).get("on_exhausted") == "wrap-up":
        converged, reason, partial = True, "budget", True
    elif quiescent:
        converged, reason, partial = True, "quiescence", True
    return {"staffed": staffed, "converged": converged, "reason": reason, "partial": partial,
            "round": rnd, "budget": budget, "done_roles": sorted(done_roles),
            "unanswered": unanswered}


def derive_phase(mission: dict, roles: "dict[str, dict]", mp: MissionPaths) -> str:
    """ミッションの状態をファイルの存在から導出する（設計書 §3.1）。"""
    if os.path.isfile(mp.cancelled()):
        return "cancelled"
    final = read_json(mp.final())
    if final and final.get("accepted"):
        return "done"
    cs = convergence_state(mission, roles, mp)
    if cs["budget"]["hard"] and (mission.get("budget") or {}).get("on_exhausted") == "fail":
        return "failed"
    if not cs["staffed"]:
        return "open"
    if not cs["converged"]:
        return "working"
    manifest = read_json(mp.manifest())
    if not manifest or int(manifest.get("round", -1)) != cs["round"]:
        return "integrating"
    return "reviewing"
