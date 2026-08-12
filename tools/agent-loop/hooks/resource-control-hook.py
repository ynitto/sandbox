#!/usr/bin/env python3
"""Run the shared headless resource controller without dispatching an LLM prompt."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


def check(hook_config: dict[str, Any] | None = None) -> None:
    nested = (hook_config or {}).get("hook_config") or {}
    audit_command = [str(nested.get("agent_audit") or "agent-audit")]
    if nested.get("audit_dir"):
        audit_command += ["--audit-dir", os.path.expanduser(str(nested["audit_dir"]))]
    if nested.get("budget_dir"):
        audit_command += ["--budget-dir", os.path.expanduser(str(nested["budget_dir"]))]
    audit_command += ["collect", "--source", "cli-quota"]
    try:
        # quota取得失敗は既存の予算再配分まで止めない。判断側は古い観測をstaleとして扱う。
        subprocess.run(audit_command, text=True, capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass
    default_script = Path(__file__).resolve().parents[2] / "agent-dashboard" / "scripts" / "resource-control.js"
    script = Path(os.path.expanduser(str(nested.get("script") or default_script))).resolve()
    command = [str(nested.get("node") or "node"), str(script)]
    if nested.get("control_dir"):
        command += ["--control-dir", os.path.expanduser(str(nested["control_dir"]))]
    if nested.get("budget_dir"):
        command += ["--budget-dir", os.path.expanduser(str(nested["budget_dir"]))]
    result = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"resource control failed: {result.returncode}")
    return None
