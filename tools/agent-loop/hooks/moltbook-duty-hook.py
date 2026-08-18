#!/usr/bin/env python3
"""Run the deterministic Moltbook outbox sweep without an LLM prompt.

計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.3（K2）

LLM を要らない分だけをここで回す: outbox の publish バックログを privacy gate に
通して sweep する（「新しい判断」はしない——既に置かれた候補を gate に通すだけ）。
timeline の確認・根拠つきの新規 reply・good の判断は「Moltbook 当番」の定期プロンプト
側が担う（LLM が要る分）。Moltbook は各ノードの AI だけが操作する前提で、人の承認・
差し戻しの経路は持たない。

hook_config:
  skill_home: ~/.claude/skills                 # moltbook-use のスクリプトを探す親
  moltbook_batch: /path/to/moltbook_batch.py    # 直接指定（skill_home より優先）
  label_conn: default
  python: python3
  timeout: 300
"""
from __future__ import annotations

import os
import subprocess
from typing import Any

DEFAULT_TIMEOUT = 300


def _path(value: str) -> str:
    return os.path.abspath(os.path.expanduser(str(value)))


def _batch_script(cfg: dict) -> "str | None":
    """スクリプト置き場を解決する。見つからなければ未導入として黙って抜ける。"""
    explicit = cfg.get("moltbook_batch")
    if explicit:
        return _path(explicit)
    skill_home = cfg.get("skill_home")
    if not skill_home:
        return None
    cand = os.path.join(_path(skill_home), "moltbook-use", "scripts", "moltbook_batch.py")
    return cand if os.path.isfile(cand) else None


def _label(command: "list[str]") -> str:
    return " ".join(command[-2:]) if len(command) >= 2 else command[0]


def check(hook_config: "dict[str, Any] | None" = None) -> None:
    """純バッチ——常に None を返し、LLM へは 1 文字も送らない。

    moltbook-use が未導入のノードでは何もしない（正常）。失敗は理由付きで
    例外にする——黙って「巡回が回っている」ことにしない。
    """
    cfg = (hook_config or {}).get("hook_config") or {}
    script = _batch_script(cfg)
    if not script:
        return None
    python = str(cfg.get("python") or "python3")
    label = str(cfg.get("label_conn") or "default")
    try:
        timeout = float(cfg.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    command = [python, script, "--label-conn", label, "--direction", "publish"]
    try:
        result = subprocess.run(command, text=True, capture_output=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"moltbook duty failed: {_label(command)}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise RuntimeError(f"moltbook duty failed: {_label(command)}: exit {result.returncode}"
                           f"{' — ' + detail[-1] if detail else ''}")
    return None
