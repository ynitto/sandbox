# 検証(4): intake の id 冪等が維持されているか。
# 変更前の正 = sibling codd_gate_debt.parse_debt_output（未変更で残っている生き証人）。
import json, tempfile, types, sys
from pathlib import Path
import agent_project as km

CASES = [
    {"title": "drift A", "id": 123},
    {"title": " B ", "id": "  x  "},
    {"title": "C", "id": ""},
    {"title": "D", "id": 0},
    {"title": "E", "id": "   "},
    {"title": "F", "id": 1.5},
    {"title": "G"},
    {"title": "H", "id": None},
    {"title": "", "id": "z"},
    "not-an-object",
    {"title": "I", "id": " k ", "extra": {"n": 1}},
]
text = json.dumps(CASES)

out = {}
specs, errors = km._parse_intake_records(text)
out["parser_specs"] = specs
out["parser_errors"] = errors

# 変更前実装との突き合わせ
sys.path.insert(0, str(Path(km.__file__).resolve().parent.parent))
try:
    import codd_gate_debt
    res = codd_gate_debt.parse_debt_output(text)
    ref_specs = [i.to_spec() for i in res.items]
    out["ref_specs"] = ref_specs
    out["ref_errors"] = list(res.errors)
    out["MATCH_specs"] = (ref_specs == specs)
    out["MATCH_errors"] = (list(res.errors) == errors)
except Exception as e:
    out["ref_error"] = repr(e)

# run_intake の冪等: 同じ入力を2回流す
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "repos.json").write_text(json.dumps({"a": {"url": "git@h:t/a.git"}}), encoding="utf-8")
    ns = types.SimpleNamespace(root=str(root), config=None)
    km.resolve_config(ns)
    cfg = km.build_config(ns)
    km.ensure_dirs(cfg)
    payload = json.dumps([{"title": "drift A", "id": 123}, {"title": "B", "id": "  x  "},
                          {"title": "D", "id": 0}, {"title": "C", "id": ""}])
    cfg.intake_cmd = f"{sys.executable} -c \"import sys;sys.stdout.write({payload!r})\""
    cfg.intake_interval = 0
    try:
        first = km.run_intake(cfg)
        out["run1_count"] = len(first)
        out["run1_ids"] = sorted(t.id for t in first)
        second = km.run_intake(cfg)
        out["run2_count"] = len(second)
        out["run2_ids"] = sorted(t.id for t in second)
        out["IDEMPOTENT"] = (len(second) == 0)
    except Exception as e:
        out["run_intake_EXCEPTION"] = repr(e)
print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
