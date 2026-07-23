"""board — 委譲封筒の検証・ノード入札資格・エンジンへの引き渡し・成果の一本化。

板は『どのノードが引き受けるか』と『成果がどれか』だけを管理する。エンジン（agent-flow /
agent-amigos）のコードは import せず、各エンジンの入力契約をファイルとして書いて引き渡す
（結合はデータ契約のみ）:
  flow   → <flow_bus>/inbox/<id>.json（submit_request 形）
  amigos → <amigos_home>/.agents/agent-amigos/commands/<...>.json（amigos-command 形 post）
"""
from __future__ import annotations

import os
import random
import time

from . import repos as _repos
from .core import now_iso, write_json_atomic, safe_name

WORKLOADS = ("flow", "amigos")


# --- 委譲封筒（delegation.schema.json op=post）の検証と id 採番 -----------------

def mint_id() -> str:
    """冪等キー dg-<YYYYMMDDHHMMSS>-<hex4>（dashboard contract.js と同形）。"""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"dg-{ts}-{random.randbytes(2).hex()}"


_ID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def validate_post(env: dict) -> dict:
    """公示封筒を検証して正規化コピーを返す（不正は ValueError）。
    dashboard contract.js の validateEnvelope と同じ非対称 fail-fast を Python 側でも守る。"""
    if not isinstance(env, dict):
        raise ValueError("封筒は JSON オブジェクトが必要です")
    e = dict(env)
    if e.get("op") != "post":
        raise ValueError(f"op は post が必要です: {e.get('op')!r}")
    if e.get("version") != 1:
        raise ValueError("version は 1 が必要です")
    did = str(e.get("id") or "")
    if not (1 <= len(did) <= 64) or any(c not in _ID_OK for c in did):
        raise ValueError(f"id は [A-Za-z0-9_-]{{1,64}} が必要です: {did!r}")
    if e.get("workload") not in WORKLOADS:
        raise ValueError(f"workload は flow / amigos が必要です: {e.get('workload')!r}")
    if not str(e.get("goal") or "").strip():
        raise ValueError("goal（何を達成するか）が必要です")
    policy = dict(e.get("policy") or {})
    assignment = policy.get("assignment", "first-come")
    if assignment not in ("first-come", "owner-picks"):
        raise ValueError(f"policy.assignment が不正: {assignment!r}")
    # flow は owner-picks 非対応（D4）— 黙って first-come に落とさない
    if e["workload"] == "flow" and assignment == "owner-picks":
        raise ValueError("flow は owner-picks 非対応です（first-come のみ）")
    policy.setdefault("assignment", "first-come")
    e["policy"] = policy
    if e["workload"] == "amigos":
        roles = (((e.get("engine") or {}).get("amigos") or {}).get("roles"))
        if not isinstance(roles, list) or not roles:
            raise ValueError("workload=amigos は engine.amigos.roles が必要です")
    e.setdefault("requested_at", now_iso())
    return e


# --- ノード入札資格 ---------------------------------------------------------

def _post_requires(env: dict) -> dict:
    return dict(env.get("requires") or {})


def node_specs(node: dict) -> "list[dict]":
    return _repos.normalize_registry(node.get("repos") or {})


def node_eligible(node: dict, env: dict) -> "tuple[bool, str]":
    """ノードがこの公示に入札してよいか。(可否, 理由) を返す（理由は不可のとき）。
    条件（すべて AND）: workload 対応 / requires.tags 包含 / requires.agent_cli いずれか保有 /
    workspace.url と requires.repos を repos 宣言で担当（identity 照合）。"""
    workloads = node.get("workloads") or []
    if workloads and env.get("workload") not in workloads:
        return False, f"workload {env.get('workload')} 非対応"

    req = _post_requires(env)
    need_tags = set(str(t) for t in (req.get("tags") or []))
    if need_tags and not need_tags.issubset(set(node.get("tags") or [])):
        return False, f"tags 不足（要 {sorted(need_tags)}）"

    need_cli = [str(c) for c in (req.get("agent_cli") or [])]
    if need_cli and not (set(need_cli) & set(node.get("agent_cli") or [])):
        return False, f"agent_cli 不足（要 いずれか {need_cli}）"

    specs = node_specs(node)
    # workspace.url は暗黙のリポジトリ資格。書込先なので writable なエントリで担当が必要。
    ws = env.get("workspace") or {}
    if ws.get("url"):
        if not _repos.covers(specs, ws, writable=True):
            return False, f"workspace {ws.get('url')} を担当していない"
    # requires.repos は明示のリポジトリ資格（すべて担当していること）
    for ref in (req.get("repos") or []):
        if not _repos.covers_ref(specs, ref):
            return False, f"repo {ref} を担当していない"
    return True, ""


# --- エンジンへの引き渡し ---------------------------------------------------

def build_request(env: dict) -> str:
    """flow の request 本文。design があれば前置する（dashboard flow-adapter と同形）。"""
    goal = str(env.get("goal") or "").strip()
    design = str(env.get("design") or "").strip()
    if design:
        return f"## 設計\n\n{design}\n\n---\n\n{goal}"
    return goal


def handoff_flow(env: dict, flow_bus_dir: str, submitter: str) -> str:
    """<flow_bus>/inbox/<id>.json を書く（submit_request 契約）。書いたパスを返す。"""
    did = env["id"]
    flow_eng = (env.get("engine") or {}).get("flow") or {}
    rec = {
        "id": did,
        "request": build_request(env),
        "submitter": submitter,
        "workspace": env.get("workspace") or None,
        "references": list(env.get("references") or []),
        "submitted_at": env.get("requested_at") or now_iso(),
        # 板由来の来歴（agent-flow が meta へ引き回せる additive フィールド）
        "delegation": {"id": did, "board": True},
    }
    if flow_eng.get("inherit_from"):
        rec["inherit_from"] = str(flow_eng["inherit_from"])
    if flow_eng.get("executor"):
        rec["executor"] = str(flow_eng["executor"])
    if env.get("priority") and env["priority"] != "normal":
        rec["priority"] = env["priority"]
    inbox = os.path.join(os.path.abspath(flow_bus_dir), "inbox")
    path = os.path.join(inbox, f"{did}.json")
    write_json_atomic(path, rec)
    return path


def _synth_design(env: dict) -> str:
    """amigos の design doc を goal（＋参照）から最小合成する（dashboard synthDesign と同趣旨）。"""
    lines = [f"# {env.get('title') or env.get('goal') or env['id']}", "",
             "## ゴール", "", str(env.get("goal") or "").strip(), ""]
    refs = env.get("references") or []
    if refs:
        lines += ["## 参照リポジトリ", ""]
        for r in refs:
            if isinstance(r, dict) and r.get("url"):
                lines.append(f"- {r['url']}")
        lines.append("")
    return "\n".join(lines)


def amigos_command_record(env: dict) -> dict:
    """amigos-command.schema.json の post レコードへ変換（dashboard amigos-adapter と同形）。"""
    amigos = (env.get("engine") or {}).get("amigos") or {}
    rec = {
        "command": "post",
        "mission_id": env["id"],
        "title": env.get("title") or "",
        "goal": env.get("goal") or "",
        "design": (env.get("design") or "").strip() or _synth_design(env),
        "roles": amigos.get("roles") or [],
    }
    mission = dict(amigos.get("mission") or {})
    policy = env.get("policy") or {}
    if policy.get("assignment"):
        mission.setdefault("assignment_policy", policy["assignment"])
    if policy.get("staffing"):
        mission.setdefault("staffing_policy", policy["staffing"])
    if policy.get("staffing_timeout_sec") is not None:
        mission.setdefault("staffing_timeout", policy["staffing_timeout_sec"])
    if env.get("acceptance"):
        mission.setdefault("acceptance", env["acceptance"])
    if env.get("deadline"):
        mission.setdefault("deadline", env["deadline"])
    budget = env.get("budget") or {}
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


def handoff_amigos(env: dict, amigos_home: str, node_id: str) -> str:
    """amigos ホームの commands/ へ post コマンドを投函する。書いたパスを返す。
    落札ノードがオーナーとして公示する（board の落札 = ミッションオーナーの決定）。"""
    rec = amigos_command_record(env)
    cdir = os.path.join(os.path.abspath(amigos_home), ".agents", "agent-amigos", "commands")
    os.makedirs(cdir, exist_ok=True)
    fname = f"{int(time.time() * 1000)}-{random.randbytes(3).hex()}.json"
    path = os.path.join(cdir, fname)
    write_json_atomic(path, rec)
    return path


def handoff(env: dict, *, flow_bus_dir: str = "", amigos_home: str = "",
            node_id: str = "") -> str:
    """workload に応じてローカルエンジンへ引き渡す。書いたパスを返す。"""
    if env["workload"] == "flow":
        if not flow_bus_dir:
            raise ValueError("flow への引き渡しには flow_bus が必要です")
        return handoff_flow(env, flow_bus_dir, submitter=f"agent-board:{node_id}")
    if env["workload"] == "amigos":
        if not amigos_home:
            raise ValueError("amigos への引き渡しには amigos_home が必要です")
        return handoff_amigos(env, amigos_home, node_id)
    raise ValueError(f"未対応 workload: {env['workload']}")


# --- 成果の一本化 -----------------------------------------------------------

def resolve_first_valid(reports: "list[dict]") -> "dict | None":
    """verify PASS 報告の (completed_ts, who) 最小を勝者に選ぶ（決定的・投機の一本化）。
    verified が無い場合は status==done を資格とみなす。無ければ None。"""
    cands = [r for r in reports
             if r.get("status") == "done" and r.get("verified", True)]
    if not cands:
        return None
    cands.sort(key=lambda r: (float(r.get("completed_ts", 0.0)), str(r.get("who", ""))))
    return cands[0]
