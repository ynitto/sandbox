# 検証(3): configfile 配線 -> doctor findings -> model debt -> regression gate の
# エンドツーエンドを main / 現ブランチの双方で同一手順で測る。
import json, tempfile, types
from pathlib import Path
import agent_project as km

out = {}
with tempfile.TemporaryDirectory() as d:
    (Path(d) / "repos.json").write_text(json.dumps({"a": {"url": "git@h:t/a.git"}}), encoding="utf-8")
    ns = types.SimpleNamespace(root=d, config=None)
    km.resolve_config(ns)
    cfg = km.build_config(ns)
    # (A) configfile 配線: 自動配線が cfg を補うか
    out["A_regression_cmd"] = cfg.regression_cmd
    out["A_intake_cmd"] = cfg.intake_cmd
    # (B) doctor findings: 名前は版で違うので両方試す
    fn = getattr(km, "doctor_wiring_findings", None) or getattr(km, "doctor_codd_gate_findings", None)
    out["B_findings_fn"] = fn.__name__
    f = fn(cfg, which=lambda n, path=None: None)
    out["B_findings"] = sorted([x.get("title", "") for x in f])
    out["B_count"] = len(f)
    # (C) model debt: intake パーサの正規化
    text = json.dumps({"items": [
        {"title": "drift A", "id": 123},
        {"title": " B ", "id": "  x  "},
        {"title": "C", "id": ""},
        {"title": "D", "id": 0},
        {"title": "E", "id": "   "},
    ]})
    parse = getattr(km, "_parse_intake_records", None)
    if parse is not None:
        specs, errs = parse(text)
        out["C_specs"] = specs
        out["C_errors"] = errs
    else:
        out["C_specs"] = "n/a"
    # (D) regression gate: cfg.regression_cmd が gate に届くか
    out["D_gate_cmd_present"] = bool(cfg.regression_cmd)
print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
