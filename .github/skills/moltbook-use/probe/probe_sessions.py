#!/usr/bin/env python3
"""会話の保存形式を調べる。中身（発言・コード）は出さず、項目名・件数・型だけを出す。

  python probe_sessions.py --client copilot    # Windows で実行
  python3 probe_sessions.py --client kiro      # WSL で実行
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sqlite3
import sys
from pathlib import Path


def _keys(obj, depth=0):
    if isinstance(obj, dict) and depth < 3:
        return {k: _keys(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_keys(obj[0], depth + 1)] if obj else []
    return type(obj).__name__


def jsonl_shape(path: Path, limit: int = 5000) -> dict:
    types, shapes = collections.Counter(), {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                types["<unparsed>"] += 1
                continue
            if isinstance(ev, dict):
                t = ev.get("type") or ev.get("kind") or "<no type>"
            else:
                t = "<non-object>"
            types[str(t)] += 1
            shapes.setdefault(str(t), _keys(ev))
    return {"file": str(path), "bytes": path.stat().st_size, "types": dict(types), "shapes": shapes}


def sqlite_shape(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {"file": str(path), "tables": {}}
    for (name,) in con.execute("select name from sqlite_master where type='table'"):
        cols = [r[1] for r in con.execute(f"pragma table_info('{name}')")]
        count = con.execute(f"select count(*) from '{name}'").fetchone()[0]
        sample = {}
        for c in cols:   # JSON を入れていそうな列だけ、項目名を見る
            row = con.execute(f"select \"{c}\" from '{name}' where typeof(\"{c}\")='text' limit 1").fetchone()
            if row and str(row[0]).lstrip().startswith("{"):
                try:
                    sample[c] = _keys(json.loads(row[0]))
                except ValueError:
                    pass
        out["tables"][name] = {"columns": cols, "rows": count, "json_shapes": sample}
    return out


def copilot() -> list:
    home = Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot"))
    res = []
    files = sorted((home / "session-state").glob("*/events.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    res += [jsonl_shape(p) for p in files[:3]]
    if (home / "session-store.db").exists():
        res.append(sqlite_shape(home / "session-store.db"))
    if not res:
        res.append({"note": f"見つかりません: {home}"})
    return res


def kiro() -> list:
    res = []
    for p in (Path.home() / ".local/share/kiro-cli/data.sqlite3", Path.home() / ".kiro/store.db"):
        if p.exists():
            res.append(sqlite_shape(p))
    logs = []
    for base in (Path.home() / ".kiro", Path.home() / ".local/share/kiro-cli"):
        if base.exists():
            logs += list(base.rglob("conversation_log.jsonl")) + list(base.rglob("*.jsonl"))[:20]
    logs = sorted(set(logs), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    res += [jsonl_shape(p) for p in logs]
    return res or [{"note": "Kiro の会話の保存先が見つかりません"}]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", choices=["copilot", "kiro"], required=True)
    a = ap.parse_args()
    print(json.dumps(copilot() if a.client == "copilot" else kiro(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
