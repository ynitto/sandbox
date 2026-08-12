#!/usr/bin/env python3
"""評価対象の呼び出し面と、現在の測定有無を一覧・検査する。LLM は呼ばない。"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MANIFEST = HERE / "coverage.json"


def canonical_flow_roles() -> set[str]:
    import sys
    sys.path.insert(0, str(REPO / "tools/agent-tools/agentcore"))
    from agentcore.nodecontract import VALID_KINDS  # noqa: PLC0415
    return set(VALID_KINDS) | {"planner", "evaluator"}


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def canonical_project_purposes() -> set[str]:
    """agent-project のチョークポイントへ渡す literal purpose を実装から拾う。"""
    found = set()
    root = REPO / "tools/agent-project/agent_project"
    for path in root.glob("*.py"):
        found.update(re.findall(r'purpose\s*=\s*["\']([^"\']+)',
                                path.read_text(encoding="utf-8")))
    return found


def audit(data: dict) -> list[str]:
    errors = []
    surfaces = data.get("surfaces") or {}
    flow = set((surfaces.get("agent-flow") or {}).keys())
    expected = canonical_flow_roles()
    if flow != expected:
        errors.append(f"agent-flow inventory drift: missing={sorted(expected-flow)} extra={sorted(flow-expected)}")
    project = set((surfaces.get("agent-project") or {}).keys())
    expected_project = canonical_project_purposes()
    if project != expected_project:
        errors.append("agent-project inventory drift: "
                      f"missing={sorted(expected_project-project)} extra={sorted(project-expected_project)}")
    allowed = {"direct", "indirect", "missing", "deterministic"}
    for surface, purposes in surfaces.items():
        if not isinstance(purposes, dict) or not purposes:
            errors.append(f"{surface}: purpose が空")
            continue
        for purpose, status in purposes.items():
            if status not in allowed:
                errors.append(f"{surface}/{purpose}: 未知の status={status}")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, help="同じレポートを JSON で保存")
    args = ap.parse_args()
    data = load_manifest()
    errors = audit(data)
    totals: dict[str, int] = {}
    for surface, purposes in data["surfaces"].items():
        print(f"\n[{surface}]")
        for purpose, status in purposes.items():
            totals[status] = totals.get(status, 0) + 1
            print(f"  {purpose:<28} {status}")
    print("\n合計: " + " / ".join(f"{k}={v}" for k, v in sorted(totals.items())))
    report = {**data, "summary": totals, "audit_errors": errors}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for error in errors:
        print(f"ERROR: {error}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
