# 検証(4b): run_intake の実 e2e 冪等（intake_cmd をファイル cat で確実に流す）。
import json, tempfile, types, sys
from pathlib import Path
import agent_project as km

out = {}
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "repos.json").write_text(json.dumps({"a": {"url": "git@h:t/a.git"}}), encoding="utf-8")
    payload = [{"title": "drift A", "id": 123}, {"title": "B", "id": "  x  "},
               {"title": "D", "id": 0}, {"title": "C", "id": ""}]
    pf = root / "payload.json"
    pf.write_text(json.dumps(payload), encoding="utf-8")
    ns = types.SimpleNamespace(root=str(root), config=None)
    km.resolve_config(ns)
    cfg = km.build_config(ns)
    km.ensure_dirs(cfg)
    cfg.intake_cmd = f"cat {pf}"
    cfg.intake_interval = 0
    try:
        first = km.run_intake(cfg)
        out["run1_count"] = len(first)
        out["run1_ids"] = sorted(t.id for t in first)
        km._INTAKE_LAST.clear()
        second = km.run_intake(cfg)
        out["run2_count"] = len(second)
        out["run2_ids"] = sorted(t.id for t in second)
        out["IDEMPOTENT"] = (len(second) == 0)
        out["backlog_files"] = sorted(p.name for p in cfg.backlog.glob("*.md"))
    except Exception as e:
        import traceback; traceback.print_exc()
        out["run_intake_EXCEPTION"] = repr(e)
print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
