# 検証(1): フック解決が本当に呼ばれ、対象モジュールが配線されるかを実行トレースで確認する。
import json, sys, tempfile, types, importlib
from pathlib import Path
import agent_project as km

calls = []
_real = km._hook_import
def traced(name, required):
    mod = _real(name, required)
    calls.append({"import_attempt": name, "required": list(required), "ok": mod is not None})
    return mod
km._hook_import = traced

out = {}
with tempfile.TemporaryDirectory() as d:
    (Path(d) / "repos.json").write_text(json.dumps({"a": {"url": "git@h:t/a.git"}}), encoding="utf-8")
    ns = types.SimpleNamespace(root=d, config=None)
    km.resolve_config(ns)
    cfg = km.build_config(ns)
    out["cfg_hooks"] = cfg.hooks
    out["cfg_regression_cmd"] = cfg.regression_cmd
    out["cfg_intake_cmd"] = cfg.intake_cmd

    det = km._hook_provider("wiring.detect", cfg)
    ren = km._hook_provider("wiring.findings", cfg)
    out["resolved_detect"] = getattr(det, "__name__", None)
    out["resolved_findings"] = getattr(ren, "__name__", None)
    out["detect_is_real_module"] = det is not None and hasattr(det, "detect_wiring")
    out["findings_is_real_module"] = ren is not None and hasattr(ren, "doctor_findings")
    out["import_trace"] = calls

    findings = km.doctor_wiring_findings(cfg, which=lambda n, path=None: None)
    out["findings_count"] = len(findings)
    out["findings"] = findings
print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
