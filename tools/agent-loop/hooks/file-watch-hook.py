#!/usr/bin/env python3
"""Directory snapshot hook for agent-loop event_hook (mtime/size diff)."""
from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path
from typing import Any

_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from _hook_util import hook_state_dir, load_json, save_json_atomic

STATE_FILE_NAME = "file-snapshot.json"
DEFAULT_MAX_FILES = 10000
DEFAULT_WATCH_DIRS = ["."]
DEFAULT_PATTERNS = ["**/*"]
DEFAULT_IGNORE = ["**/__pycache__/**", "**/.venv/**", "**/.git/**"]

_runtime: dict[str, Any] = {
    "entry_id": None,
    "state_path": None,
    "pending_snapshot": None,
    "state": None,
}


def _state_path(entry_id: str) -> Path:
    return hook_state_dir(entry_id) / STATE_FILE_NAME


def _hook_cfg(hook_config: dict[str, Any] | None) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if isinstance(hook_config, dict):
        nested = hook_config.get("event_hook_config")
        if isinstance(nested, dict):
            cfg.update(nested)
    return cfg


def _resolve_cwd(hook_config: dict[str, Any] | None) -> Path:
    raw = (hook_config or {}).get("cwd")
    if raw:
        return Path(os.path.expanduser(str(raw))).resolve()
    return Path.cwd()


def _normalize_rel(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return rel


def _matches(rel_posix: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_posix, pat) for pat in patterns)


def _scan_snapshot(
    cwd: Path,
    cfg: dict[str, Any],
) -> dict[str, dict[str, int]] | None:
    watch_dirs = cfg.get("watch_dirs") or DEFAULT_WATCH_DIRS
    patterns = cfg.get("patterns") or DEFAULT_PATTERNS
    ignore_patterns = list(cfg.get("ignore_patterns") or DEFAULT_IGNORE)
    if "**/.git/**" not in ignore_patterns:
        ignore_patterns.append("**/.git/**")
    try:
        max_files = int(cfg.get("max_files", DEFAULT_MAX_FILES))
    except (TypeError, ValueError):
        max_files = DEFAULT_MAX_FILES

    snapshot: dict[str, dict[str, int]] = {}
    count = 0
    for watch in watch_dirs:
        raw_base = cwd / str(watch)
        if raw_base.is_symlink():
            continue
        base = raw_base.resolve()
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            current = Path(dirpath)
            if current.is_symlink():
                dirnames[:] = []
                continue
            for name in filenames:
                full = current / name
                if full.is_symlink():
                    continue
                try:
                    rel = _normalize_rel(full, cwd)
                except ValueError:
                    continue
                if not _matches(rel, patterns):
                    continue
                if _matches(rel, ignore_patterns):
                    continue
                try:
                    st = full.stat()
                except OSError:
                    return None
                snapshot[rel] = {"mtime_ns": int(st.st_mtime_ns), "size": int(st.st_size)}
                count += 1
                if count > max_files:
                    return None
    return snapshot


def _diff(
    old: dict[str, dict[str, int]],
    new: dict[str, dict[str, int]],
) -> tuple[list[str], list[str], list[str]]:
    old_keys = set(old)
    new_keys = set(new)
    added = sorted(new_keys - old_keys)
    deleted = sorted(old_keys - new_keys)
    changed = sorted(
        k for k in old_keys & new_keys
        if old[k] != new[k]
    )
    return added, changed, deleted


def _build_prompt(added: list[str], changed: list[str], deleted: list[str]) -> str:
    lines = ["File changes detected:"]
    if added:
        lines.append("Added:")
        lines.extend(f"  - {p}" for p in added)
    if changed:
        lines.append("Changed:")
        lines.extend(f"  - {p}" for p in changed)
    if deleted:
        lines.append("Deleted:")
        lines.extend(f"  - {p}" for p in deleted)
    return "\n".join(lines)


def check(hook_config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    global _runtime
    entry_id = str((hook_config or {}).get("entry_id") or "default")
    path = _state_path(entry_id)
    _runtime = {
        "entry_id": entry_id,
        "state_path": path,
        "pending_snapshot": None,
        "state": None,
    }

    cfg = _hook_cfg(hook_config)
    cwd = _resolve_cwd(hook_config)
    if not cwd.is_dir():
        return None

    new_snapshot = _scan_snapshot(cwd, cfg)
    if new_snapshot is None:
        return None

    raw_state = load_json(path, {})
    baseline_done = bool(raw_state.get("baseline_done"))
    old_snapshot = raw_state.get("snapshot") if isinstance(raw_state.get("snapshot"), dict) else {}

    if not baseline_done:
        save_json_atomic(
            path,
            {"version": 1, "baseline_done": True, "snapshot": new_snapshot},
        )
        return None

    added, changed, deleted = _diff(old_snapshot, new_snapshot)
    if not added and not changed and not deleted:
        return None

    vars_map = {
        "added": added,
        "changed": changed,
        "deleted": deleted,
        "change_count": len(added) + len(changed) + len(deleted),
    }
    _runtime["pending_snapshot"] = new_snapshot
    _runtime["state"] = raw_state
    return {"prompt": _build_prompt(added, changed, deleted), "vars": vars_map}


def ack() -> None:
    pending = _runtime.get("pending_snapshot")
    path: Path | None = _runtime.get("state_path")
    if pending is None or path is None:
        return
    save_json_atomic(
        path,
        {"version": 1, "baseline_done": True, "snapshot": pending},
    )
    _runtime["pending_snapshot"] = None


if __name__ == "__main__":
    print(check({"entry_id": "manual", "cwd": "."}))
