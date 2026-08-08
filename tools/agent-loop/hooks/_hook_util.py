"""Shared utilities for agent-loop event hooks (not imported by scheduler)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

_HOOKS_DIR = Path(__file__).resolve().parent
_SIBLING_CACHE: dict[str, Any] = {}


def entry_hash(entry_id: str) -> str:
    return hashlib.sha256(str(entry_id).encode()).hexdigest()[:16]


def hook_state_dir(entry_id: str) -> Path:
    return Path.home() / ".agents" / "hooks" / entry_hash(entry_id)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_sibling_module(name: str) -> Any:
    """Load a co-located hook helper module once per process."""
    cached = _SIBLING_CACHE.get(name)
    if cached is not None:
        return cached
    path = _HOOKS_DIR / name
    mod_name = f"agent_loop_hook_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load hook helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    _SIBLING_CACHE[name] = module
    return module
