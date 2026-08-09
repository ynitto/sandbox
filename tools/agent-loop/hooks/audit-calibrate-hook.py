#!/usr/bin/env python3
"""Collect audit records and calibrate node-budget rates without an LLM prompt."""
from __future__ import annotations

import os
import subprocess
from typing import Any


def check(hook_config: dict[str, Any] | None = None) -> None:
    nested = (hook_config or {}).get("event_hook_config") or {}
    base = [str(nested.get("agent_audit") or "agent-audit")]
    if nested.get("audit_dir"):
        base += ["--audit-dir", os.path.expanduser(str(nested["audit_dir"]))]
    if nested.get("budget_dir"):
        base += ["--budget-dir", os.path.expanduser(str(nested["budget_dir"]))]
    for suffix in (["collect"], ["calibrate", "--write"]):
        result = subprocess.run(base + suffix, text=True, capture_output=True, timeout=300, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                               or f"agent-audit failed: {result.returncode}")
    return None
