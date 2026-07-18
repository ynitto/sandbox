#!/usr/bin/env python3
"""doctor の配線所見を変更前後で突き合わせるプローブ（r4 / t4）。

PYTHONPATH で before/after のツリーを切り替えて同じ入力を流し、findings の内容・件数・出力順を
JSON で吐く。リストは順序を保つため sort せず、辞書のキーだけ sort_keys で安定化する。

出力は 4 ブロック:
  wiring_real   -- doctor_wiring_findings（which は実物。結線済み環境の実測）
  wiring_unwired-- doctor_wiring_findings（which=None 注入で未結線を強制）
  deterministic -- cmd_doctor が組む決定的所見の合成（env + audit + flow_bus + wiring）
  cmd_doctor    -- cmd_doctor 全体の終了コード。例外を出さず走り切るかの確認
"""
import io
import json
import contextlib
import tempfile
import types
from pathlib import Path

import agent_project as km


def probe():
    out = {}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "repos.json").write_text(
            json.dumps({"a": {"url": "git@h:t/a.git"}}), encoding="utf-8")
        ns = types.SimpleNamespace(root=d, config=None)
        km.resolve_config(ns)
        cfg = km.build_config(ns)

        out["wiring_real"] = km.doctor_wiring_findings(cfg)
        out["wiring_unwired"] = km.doctor_wiring_findings(
            cfg, which=lambda n, path=None: None)
        out["deterministic"] = (km.doctor_env_findings(cfg) + km.doctor_audit_findings(cfg)
                                + km.doctor_flow_bus_coverage_findings(cfg)
                                + km.doctor_wiring_findings(cfg))
        # cmd_doctor 全体。エージェント診断と agent-flow 連携は決定性のため無効化する。
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = km.cmd_doctor(cfg, as_json=True,
                                   agent_run=lambda *a, **k: "[]",
                                   flow_finder=lambda c, f: [])
            out["cmd_doctor"] = {"exit": rc, "raised": None,
                                 "stdout": json.loads(buf.getvalue() or "null")}
        except Exception as e:                    # degrade できていなければここに出る
            out["cmd_doctor"] = {"exit": None, "raised": f"{type(e).__name__}: {e}"}
        # 実行ごとに変わる一時パスは比較の邪魔にしかならないので伏せる
        return json.loads(json.dumps(out, ensure_ascii=False)
                          .replace(str(Path(d).resolve()), "<ROOT>").replace(d, "<ROOT>"))


if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=False, indent=2, sort_keys=True))
