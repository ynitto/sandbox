"""agent-tuning.methods の条件評価と trial 割付（flow / loop 共通・決定的）。"""
from __future__ import annotations

import hashlib
import json
import os
import re

from . import agentcli

MARKER = "<!-- agent-methods"


def load(path: str) -> "dict | None":
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("enabled") is not False else None


def current_tier(control_dir: str, workload: str) -> str:
    """このワークロードがいま走っている段。読むのは **agent-control だけ**。

    段を決めるのは dashboard（agent-profiles）で、その結果は control.json の
    `workloads.<名前>.tier` へ投函される。以前はここが profiles.json の `state` を
    直接読んでいたが、agent-profiles は「エンジンから読まれない」ことを不変条件に
    している契約なので、読み口を 1 つ（agent-control）へ寄せた——段の名前が
    dashboard の宣言と手法パックの `when.tiers` で別物になる余地も同時に消える。
    """
    try:
        with open(os.path.join(control_dir, "control.json"), encoding="utf-8") as f:
            control = json.load(f)
    except (OSError, ValueError):
        return ""
    workloads = control.get("workloads") if isinstance(control, dict) else None
    rec = workloads.get(workload) if isinstance(workloads, dict) else None
    return str(rec.get("tier") or "") if isinstance(rec, dict) else ""


def relative_cost(agent_cli: str, project_dir: "str | None" = None) -> "float | None":
    try:
        return float(agentcli.load_cli(agent_cli, project_dir=project_dir)["relative_cost"])
    except Exception:  # noqa: BLE001 — optional tuning must not stop execution
        return None


def role_for(purpose: str) -> str:
    return {"planner": "planner", "evaluator": "evaluator", "verify": "verify",
            "session": "session"}.get(str(purpose or ""), "worker")


def matches(when, context: dict) -> bool:
    if not isinstance(when, dict):
        return True
    for field, key in (("engines", "engine"), ("workloads", "workload"),
                       ("agent_cli", "agent_cli"), ("models", "model"),
                       ("roles", "role"), ("purposes", "purpose"), ("tiers", "tier")):
        values = when.get(field)
        if isinstance(values, list) and values and str(context.get(key) or "") not in {
                str(v) for v in values}:
            return False
    cost = context.get("relative_cost")
    for field, op in (("min_relative_cost", "min"), ("max_relative_cost", "max")):
        if field not in when:
            continue
        try:
            limit = float(when[field])
            value = float(cost)
        except (TypeError, ValueError):
            return False
        if (op == "min" and value < limit) or (op == "max" and value > limit):
            return False
    return True


# 自動注入（`enabled: true` で全対象へ効く）に参加できる選ばれ方。
# `per-task`（工程ごとに人・planner が選ぶ）と `engine`（エンジンが run パラメータから
# 決定的に選ぶ）は、**enabled が true でも自動注入しない**。選ばれ方が別系統だからで、
# ここを通すと「--split-policy file の run に behavior の指示も入る」ような、
# 選択と矛盾する二重注入が起きる。UI が出し分けているだけでは守れない
# （`agent-loop methods enable <id>`・手書きの tuning.json・run 複製が同じ穴を開ける）ので、
# 自動注入の唯一のチョークポイントであるここで強制する。
AUTO_SELECTION = "auto"


def auto_selectable(method) -> bool:
    """この定義が自動注入（enabled ベース）の対象か。無指定は auto（既存定義の互換）。"""
    if not isinstance(method, dict):
        return False
    return str(method.get("selection") or AUTO_SELECTION) == AUTO_SELECTION


def _variant_index(key: str, trial_id: str) -> int:
    # agent-project の同一タスク再試行は ...-rN なので、同じ仕事では厳密に交互になる。
    match = re.search(r"-r(\d+)(?:-|$)", str(key or ""))
    if match:
        return int(match.group(1)) % 2
    digest = hashlib.sha256(f"{trial_id}:{key}".encode()).digest()
    return digest[0] % 2


def select(data: "dict | None", context: dict, assignment_key: str = "") -> dict:
    """この呼び出しで注入する手法テキストと、実際に適用した証拠を返す。

    返す `trial` は「**この variant を本当に適用した**」ときだけ入る。宣言だけで
    trial を名乗ると、何も注入していない実行が variant の証拠として集計され、
    比較が「効かなかった」ではなく「測っていない」を測ることになる。
    """
    empty = {"text": "", "methods": [], "trial": None, "ignored_trials": []}
    if not isinstance(data, dict):
        return empty
    by_id = {str(m.get("id")): m for m in data.get("methods") or []
             if isinstance(m, dict) and m.get("id") and auto_selectable(m)}
    chosen = {mid for mid, method in by_id.items() if method.get("enabled") is True}
    trials = sorted((t for t in data.get("trials") or []
                     if isinstance(t, dict) and t.get("id") and t.get("enabled") is not False
                     and matches(t.get("when"), context)), key=lambda t: str(t["id"]))
    # 同時に複数の trial を走らせると効果の帰属が割れるので 1 つに絞る。**捨てた分は返す**
    # ——黙って落とすと、宣言した trial が一度も走らないことを書いた人が検知できない。
    ignored = [str(t["id"]) for t in trials[1:]]
    trial_id = variant_id = ""
    declared: "list[str]" = []
    if trials:
        trial = trials[0]
        variants = trial.get("variants") or []
        if len(variants) == 2 and all(isinstance(v, dict) for v in variants):
            variant = variants[_variant_index(assignment_key, str(trial["id"]))]
            declared = [str(mid) for mid in variant.get("methods") or []]
            chosen.update(declared)
            trial_id, variant_id = str(trial["id"]), str(variant.get("id") or "")

    role = str(context.get("role") or "")
    blocks, active = [], []
    for mid in sorted(chosen):
        method = by_id.get(mid)
        if not method or not matches(method.get("when"), context):
            continue
        texts = [str(f.get("text") or "").strip() for f in method.get("fragments") or []
                 if isinstance(f, dict) and f.get("role") == role and str(f.get("text") or "").strip()]
        if texts:
            active.append(mid)
            blocks.extend(texts)

    trial_rec = None
    if trial_id:
        # variant が手法を宣言しているのに 1 つも効かなかった＝この実行は variant を
        # 代表しない（`methods[]` へ複製されていない id、`when` 不一致、role 不一致）。
        # 対照群（宣言 0 件）は宣言どおり「何も足さない」ので、適用として数えてよい。
        if not declared or any(mid in active for mid in declared):
            trial_rec = {"id": trial_id, "variant": variant_id}
    if not blocks:
        return {"text": "", "methods": [], "trial": trial_rec, "ignored_trials": ignored}
    trial_text = (f" trial:{trial_rec['id']}/{trial_rec['variant']}" if trial_rec else "")
    header = f"{MARKER} ids:{','.join(active)}{trial_text} -->"
    return {"text": header + "\n" + "\n".join(f"- {line}" for line in blocks),
            "methods": active, "trial": trial_rec, "ignored_trials": ignored}
