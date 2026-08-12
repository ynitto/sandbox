#!/usr/bin/env python3
"""Run cost-free, black-box scenario tests for the agent-tools engines."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TEST_DIRS = {
    "agent-project": ROOT / "tools/agent-project/tests",
    "agent-flow": ROOT / "tools/agent-flow/tests",
    "agent-amigos": ROOT / "tools/agent-amigos/tests",
    "agent-loop": ROOT / "tools/agent-loop/test",
    "agent-audit": ROOT / "tools/agent-audit/tests",
    "agent-ollama": ROOT / "tools/agent-tools/agentcore/agentcore/tests",
    "agent-aider": ROOT / "tools/agent-tools/agentcore/agentcore/tests",
}


def load_scenarios() -> list[dict]:
    scenarios = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]
    ids = [item.get("id") for item in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario ids must be unique")
    for item in scenarios:
        missing = {"id", "engine", "test", "covers"} - item.keys()
        if missing or item.get("engine") not in TEST_DIRS or not item.get("covers"):
            raise ValueError(f"invalid scenario: {item!r} (missing={sorted(missing)})")
    return scenarios


def run_mock(scenario: dict, timeout: float) -> dict:
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="agent-e2e-bin-") as bindir:
            # agent-loop validates tmux at import time. Its selected scenarios mock the
            # session boundary, so provide a no-op executable instead of requiring tmux.
            tmux = Path(bindir) / "tmux"
            tmux.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            tmux.chmod(0o755)
            pythonpath = str(ROOT / "tools/agent-tools/agentcore")
            if os.environ.get("PYTHONPATH"):
                pythonpath += os.pathsep + os.environ["PYTHONPATH"]
            env = {**os.environ, "AGENT_FLOW_STUB_SLEEP_MAX": "0",
                   "AGENT_LOOP_STUB_DELAY": "0", "PYTHONPATH": pythonpath,
                   "PATH": bindir + os.pathsep + os.environ.get("PATH", "")}
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "-v", scenario["test"]],
                cwd=TEST_DIRS[scenario["engine"]], capture_output=True, text=True,
                timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        return {"id": scenario["id"], "engine": scenario["engine"], "status": "timeout",
                "seconds": round(time.monotonic() - started, 3), "exit_code": None,
                "covers": scenario["covers"], "stdout": exc.stdout or "",
                "stderr": exc.stderr or ""}
    return {"id": scenario["id"], "engine": scenario["engine"],
            "status": "passed" if proc.returncode == 0 else "failed",
            "seconds": round(time.monotonic() - started, 3), "exit_code": proc.returncode,
            "covers": scenario["covers"], "stdout": proc.stdout, "stderr": proc.stderr}


def run_cloud(agent_cli: str, request: str, timeout: float) -> dict:
    """Opt-in smoke test. This is the only path which may incur cloud charges."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="agent-e2e-cloud-") as bus:
        command = [sys.executable, str(ROOT / "tools/agent-flow/agent-flow.py"),
                   "--bus", bus, "run", request, "--workers", "1", "--planner", "stub",
                   "--executor", "agent", "--agent-cli", agent_cli, "--poll", "0.2"]
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    return {"id": "flow-cloud-smoke", "engine": "agent-flow",
            "status": "passed" if proc.returncode == 0 else "failed",
            "seconds": round(time.monotonic() - started, 3), "exit_code": proc.returncode,
            "covers": ["real-agent-cli", "stdin", "agent-output"],
            "command": command, "stdout": proc.stdout, "stderr": proc.stderr}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="agent-tools scenario E2E runner (mock by default)")
    parser.add_argument("--engine", choices=[*TEST_DIRS, "all"], default="all")
    parser.add_argument("--scenario", action="append", default=[], help="scenario id (repeatable)")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true", help="emit one JSON report")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--agent-cli", metavar="NAME",
                        help="OPT-IN: replace mocks with one billable agent-flow cloud smoke test")
    parser.add_argument("--request", default="次の文字列をそのまま返してください: agent-e2e-ok")
    args = parser.parse_args(argv)
    scenarios = load_scenarios()
    if args.list:
        for item in scenarios:
            print(f"{item['id']:<24} {item['engine']:<14} {', '.join(item['covers'])}")
        return 0
    if args.agent_cli:
        results = [run_cloud(args.agent_cli, args.request, args.timeout)]
    else:
        selected = [s for s in scenarios if (args.engine == "all" or s["engine"] == args.engine)
                    and (not args.scenario or s["id"] in args.scenario)]
        unknown = set(args.scenario) - {s["id"] for s in scenarios}
        if unknown:
            parser.error("unknown scenario: " + ", ".join(sorted(unknown)))
        results = [run_mock(s, args.timeout) for s in selected]
    report = {"mode": "cloud" if args.agent_cli else "mock", "ok": bool(results) and all(
              r["status"] == "passed" for r in results), "results": results}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(f"[{result['status'].upper():6}] {result['id']} ({result['seconds']:.3f}s)")
            if result["status"] != "passed":
                print((result["stdout"] + result["stderr"])[-4000:], file=sys.stderr)
        print(f"{sum(r['status'] == 'passed' for r in results)}/{len(results)} scenarios passed")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
