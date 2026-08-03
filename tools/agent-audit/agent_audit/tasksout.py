"""tasks — 洞察 → 改善タスク（schemas/task.schema.json 形。設計 §7）。

agent-project の汎用 intake（intake_cmd / enqueue --json）が読める JSON を stdout へ
出すだけで、state repo へは書かない（C7）。洞察 id を task id に含め冪等にする
（intake_cmd の重複投入は id で弾かれる）。review が refuted の洞察は出さない。
"""
from __future__ import annotations

import json

from .configfile import resolve_audit_dir
from .scrub import scrub_obj
from .store import Store
from .util import log


def insight_tasks(store: Store) -> "tuple[list[dict], list[str]]":
    """(タスク一覧, 元になった洞察 id 一覧) を返す。"""
    tasks = []
    insight_ids = []
    for ins in sorted(store.iter_insights(), key=lambda i: i.get("id") or ""):
        if ins.get("exported"):
            continue
        if not str(ins.get("suggested_action") or "").strip():
            continue
        review = ins.get("review") or {}
        if review.get("verdict") == "refuted":
            continue
        iid = str(ins["id"]).replace("ins-", "", 1)
        tasks.append({
            "id": f"audit-{iid}"[:48],
            "title": str(ins.get("statement") or "")[:80],
            "why": f"agent-audit の洞察（観測 {ins.get('occurrences')} 件・"
                   f"confidence={ins.get('confidence')}）",
            "desc": str(ins.get("suggested_action") or ""),
            "note": "根拠: " + ", ".join(ins.get("observation_ids") or [])
                    + f"（洞察 {ins['id']}）",
            "source": "agent-audit",
        })
        insight_ids.append(str(ins["id"]))
    return tasks, insight_ids


def cmd_tasks(args) -> int:
    store = Store(resolve_audit_dir(args))
    tasks, insight_ids = insight_tasks(store)
    print(json.dumps(scrub_obj(tasks), ensure_ascii=False, indent=1))
    if getattr(args, "mark_exported", False):
        wanted = set(insight_ids)
        n = 0
        for ins in list(store.iter_insights()):
            if str(ins.get("id")) in wanted:
                ins["exported"] = True
                store.write_insight(ins)
                n += 1
        log("tasks", f"{n} 件の洞察を exported=true にしました")
    return 0
