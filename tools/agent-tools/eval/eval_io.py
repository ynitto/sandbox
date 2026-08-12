"""評価結果を、比較可能な run ディレクトリへ保存するための共通処理。"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path


def safe_name(value: str) -> str:
    """モデル名等をディレクトリ名に使える短い識別子へする。"""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.") or "unknown"


def new_run_dir(root: Path, model: str, label: str = "") -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{safe_name(label)}" if label else ""
    base = root / f"{stamp}-{safe_name(model)}{suffix}"
    path = base
    sequence = 2
    while path.exists():
        path = Path(f"{base}-{sequence}")
        sequence += 1
    path.mkdir(parents=True)
    return path


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
