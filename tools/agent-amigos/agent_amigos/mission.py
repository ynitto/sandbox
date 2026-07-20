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
    "done_when": "all-required-done",   # all-required-done | reviewer-approved | consensus
    "quiescence_turns": 3,
    "review_rounds": 2,
    "question_timeout": 2,              # 未回答質問を owner へ昇格するまでの自ターン数（§7.3）
    "consensus_ratio": 0.6,            # done_when=consensus: 席グループの最頻回答の占有率しきい値
    "consensus_min": 2,                # done_when=consensus: 合意判定に要る最小回答席数
}
DONE_WHEN_MODES = ("all-required-done", "reviewer-approved", "consensus")
BUDGET_DEFAULTS = {
    "execution_minutes": 0,             # 0 = 無制限
    "per_role_turns": 30,
    "soft_ratio": 0.9,
    "on_exhausted": "wrap-up",          # wrap-up | fail
}

# seats>1（並列同一シート、G1）のロールに付ける集約モード（G2。integrator が決定的に集約）。
#   majority       — 各席の回答（answer ファイル）の最頻値を選ぶ（多数決）。決定的タイブレーク
#   consensus      — 全席一致なら採用、割れたら flag（agreed:false）付きで最頻値を採る
#   weighted-vote  — 席ごとの重み（SCORE ファイル、既定 1.0）を回答ごとに合計して最大を採る
#   approval-count — 各席を候補とみなし、スコア（SCORE ファイル、既定 0）最大の候補を選ぶ
#   gather         — 全席の回答を席見出し付きで 1 ファイルに集める（選抜せず統合）
AGGREGATE_MODES = ("majority", "consensus", "weighted-vote", "approval-count", "gather")
DEFAULT_ANSWER_FILE = "ANSWER.md"       # 集約が読む各席の正準回答ファイル（席の artifacts 内）
DEFAULT_SCORE_FILE = "SCORE"            # weighted-vote / approval-count が読む席の数値信号ファイル


def _expand_seats(base_roles: list) -> list:
    """seats>1 のロールを N 個の具体席ロール（`<id>#0..#N-1`）へ展開する（G1）。

    展開後は各席が通常の 1 席ロールなので、claim / roster / runner / 収束 / 統合 /
    納品の既存機構をそのまま再利用できる（コアに手を入れない）。collaborates_with が
    席化グループの基底 id を指す場合は、その席 id 群へ書き換える（実在ロールへの参照に保つ）。
    """
    group_ids: "dict[str, list]" = {}
    for r in base_roles:
        n = int(r.get("seats", 1))
        group_ids[r["id"]] = ([r["id"]] if n <= 1
                              else [f"{r['id']}#{k}" for k in range(n)])

    def _remap(collabs: list) -> list:
        out = []
        for c in collabs:
            out.extend(group_ids.get(c, [c]))
        return out

    expanded = []
    for r in base_roles:
        n = int(r.get("seats", 1))
        if n <= 1:
            role = dict(r)
            role["collaborates_with"] = _remap(role.get("collaborates_with") or [])
            role["seat_group"] = r["id"]
            role["seat_index"] = 0
            role["seat_count"] = 1
            expanded.append(role)
            continue
        for k in range(n):
            s = dict(r)
            s["id"] = f"{r['id']}#{k}"
            s["seats"] = 1
            s["title"] = f"{r.get('title') or r['id']}（席 {k + 1}/{n}）"
            s["seat_group"] = r["id"]
            s["seat_index"] = k
            s["seat_count"] = n
            s["aggregate"] = r.get("aggregate")
            s["aggregate_answer"] = r.get("aggregate_answer")
            s["aggregate_score"] = r.get("aggregate_score")
            s["collaborates_with"] = _remap(r.get("collaborates_with") or [])
            expanded.append(s)
    return expanded


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
    if mission["assignment_policy"] not in ("first-come", "owner-picks"):
        raise SystemExit(f"[agent-amigos] assignment_policy={mission['assignment_policy']!r} が"
                         "不正です（first-come | owner-picks）")
    if mission["acceptance"] not in ("manual", "agent"):
        raise SystemExit(f"[agent-amigos] acceptance={mission['acceptance']!r} は未対応です"
                         "（manual | agent。codd-gate は将来拡張 — 設計書 §8.2）")
    mission["convergence"] = {**CONVERGENCE_DEFAULTS, **dict(m.get("convergence") or {})}
    if mission["convergence"]["done_when"] not in DONE_WHEN_MODES:
        raise SystemExit(f"[agent-amigos] convergence.done_when が不正です: "
                         f"{mission['convergence']['done_when']!r}"
                         f"（{' | '.join(DONE_WHEN_MODES)}）")
    mission["budget"] = {**BUDGET_DEFAULTS, **dict(m.get("budget") or {})}
    mission["workspace"] = dict(m.get("workspace") or {})

    base_roles = []
    seen = set()
    has_integrator = False
    for r in roles_in:
        rid = str(r.get("id") or "").strip()
        if not rid or "/" in rid or "#" in rid or rid in ("all", "owner"):
            raise SystemExit(f"[agent-amigos] ロール id が不正です: {rid!r}"
                             "（all / owner は予約語、/ と # は不可）")
        if rid in seen:
            raise SystemExit(f"[agent-amigos] ロール id が重複しています: {rid!r}")
        seen.add(rid)
        seats = int(r.get("seats", 1))
        if seats < 1:
            raise SystemExit(f"[agent-amigos] seats は 1 以上が必要です（ロール {rid}）")
        aggregate = r.get("aggregate")
        if aggregate is not None:
            aggregate = str(aggregate)
            if aggregate not in AGGREGATE_MODES:
                raise SystemExit(f"[agent-amigos] aggregate={aggregate!r} が不正です"
                                 f"（{' | '.join(AGGREGATE_MODES)}）")
            if seats < 2:
                raise SystemExit(f"[agent-amigos] aggregate は seats>=2 のロールにのみ指定できます"
                                 f"（ロール {rid}）")
        rounds = int(r.get("rounds", 0))     # 同期討論ラウンド数（G3）。0 = 無効
        if rounds < 0:
            raise SystemExit(f"[agent-amigos] rounds は 0 以上が必要です（ロール {rid}）")
        if rounds >= 1 and seats < 2:
            raise SystemExit(f"[agent-amigos] rounds（同期討論）は seats>=2 のロールにのみ"
                             f"指定できます（ロール {rid}）")
        role = {"id": rid,
                "title": str(r.get("title") or rid),
                "mission": str(r.get("mission") or ""),
                "deliverables": [str(d) for d in (r.get("deliverables") or [])],
                "required": bool(r.get("required", True)),
                "seats": seats,
                "rounds": rounds,
                "aggregate": aggregate,
                "aggregate_answer": (str(r["aggregate_answer"])
                                     if r.get("aggregate_answer") else None),
                "aggregate_score": (str(r["aggregate_score"])
                                    if r.get("aggregate_score") else None),
                "agent_cli": r.get("agent_cli"),
                "model": r.get("model"),
                "requires": dict(r.get("requires") or {}),
                "collaborates_with": [str(c) for c in (r.get("collaborates_with") or [])],
                "approver": bool(r.get("approver", False)),
                "builtin": str(r.get("builtin") or "")}
        if role["builtin"] == "integrator" and seats != 1:
            raise SystemExit("[agent-amigos] integrator に seats>1 は指定できません")
        if role["builtin"] == "integrator":
            has_integrator = True
        base_roles.append(role)
    for role in base_roles:
        for c in role["collaborates_with"]:
            if c not in seen:
                raise SystemExit(f"[agent-amigos] ロール {role['id']} の collaborates_with に"
                                 f" 未定義ロール {c!r} があります")
    roles = _expand_seats(base_roles)
    if not has_integrator:
        # integrator 省略時はオーナーノードが self-staff する組み込みロールを自動追加（§8.1）
        roles.append({"id": "integrator", "title": "統合", "mission":
                      "全ロールの成果物を検証・統合し deliverable/ を組み立てる。",
                      "deliverables": [], "required": True, "seats": 1, "rounds": 0,
                      "aggregate": None,
                      "aggregate_answer": None, "aggregate_score": None, "agent_cli": None,
                      "model": None, "requires": {}, "collaborates_with": [],
                      "approver": False, "builtin": "integrator", "seat_group": "integrator",
                      "seat_index": 0, "seat_count": 1})
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
    bus.prepare_mission(mid)
    mp = bus.mission(mid)
    if mp.exists():
        raise SystemExit(f"[agent-amigos] ミッション {mid} は既に存在します")
    bus.sync_pull()
    mission_doc = {**mission, "id": mid, "owner_node": owner_node, "posted_at": now_iso()}
    for role in roles:
        write_json_atomic(mp.role_json(role["id"]), role)
    shutil.copyfile(design_doc_path, mp.design_doc())
    write_json_atomic(mp.mission_json(), mission_doc)   # mission.json は最後（公示の宣言）
    bus.register_mission(mid, mission_doc)              # GitBus: main の公示インデックス
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


# --- 席グループの集約・合意（G1/G2） ----------------------------------------

def seat_answer_file(role: dict) -> str:
    """席の正準回答ファイル名: aggregate_answer > 単一 deliverable > 既定 ANSWER.md。"""
    dels = role.get("deliverables") or []
    return role.get("aggregate_answer") or (dels[0] if len(dels) == 1 else DEFAULT_ANSWER_FILE)


def seat_groups_with_aggregate(roles: "dict[str, dict]") -> "dict[str, list]":
    """aggregate 指定のある席グループ {基底 id: [席ロール…]} を返す。"""
    groups: "dict[str, list]" = {}
    for r in roles.values():
        g = r.get("seat_group")
        if g and int(r.get("seat_count") or 1) > 1 and r.get("aggregate"):
            groups.setdefault(g, []).append(r)
    return groups


def read_seat_answer(mp: MissionPaths, seat_id: str, answer_file: str) -> "str | None":
    path = os.path.join(mp.artifacts_dir(seat_id), answer_file)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def group_consensus(mp: MissionPaths, seat_roles: list, ratio: float, min_n: int) -> bool:
    """席グループが合意に達したか: 回答済み席数 >= min_n かつ 最頻回答の占有率 >= ratio。"""
    from collections import Counter
    af = seat_answer_file(seat_roles[0])
    answers = [a for a in (read_seat_answer(mp, r["id"], af) for r in seat_roles) if a]
    if len(answers) < max(min_n, 1):
        return False
    top = Counter(answers).most_common(1)[0][1]
    return top / len(answers) >= ratio


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
    done_when = conv.get("done_when")
    if done_when == "consensus":
        # 席グループが合意に達し、席以外の必須ワーカー（承認者を除く）が完了したら収束。
        # 全席の完了は待たない（早期停止）。席グループが無ければ all-required-done に退避。
        groups = seat_groups_with_aggregate(roles)
        seat_ids = {r["id"] for grp in groups.values() for r in grp}
        plain = [r for r in workers if r["id"] not in seat_ids and not r.get("approver")]
        base_done = staffed and all(r["id"] in done_roles for r in plain)
        if groups:
            ratio = float(conv.get("consensus_ratio") or 0.0)
            min_n = int(conv.get("consensus_min") or 0)
            cons_ok = all(group_consensus(mp, sr, ratio, min_n)
                          for sr in groups.values())
            all_done = base_done and cons_ok
        else:
            all_done = staffed and all(r["id"] in done_roles for r in workers)
    else:
        all_done = staffed and all(r["id"] in done_roles for r in workers)
        if done_when == "reviewer-approved":
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
