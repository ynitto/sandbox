#!/usr/bin/env python3
"""Windows 側から WSL の kiro-cli の会話データを、一時コピーして読めるかを確かめる。

kiro-cli で会話している最中に、Windows の Python で実行する。件数と時間だけを出す。

  python probe_wsl_copy.py                 # 既定のディストロ・ユーザーを自動で探す
  python probe_wsl_copy.py --distro Ubuntu --user alice
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _wsl(*args: str) -> str:
    out = subprocess.run(["wsl.exe", *args], capture_output=True)
    for enc in ("utf-16-le", "utf-8"):   # wsl.exe -l は UTF-16 で返す
        try:
            return out.stdout.decode(enc).replace("\x00", "").strip()
        except UnicodeDecodeError:
            continue
    return ""


def _count(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    names = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    return {n: con.execute(f"select count(*) from '{n}'").fetchone()[0] for n in names}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--distro")
    ap.add_argument("--user")
    a = ap.parse_args()
    distro = a.distro or _wsl("-l", "-q").splitlines()[0].strip()
    running = _wsl("-l", "--running", "-q")
    print(f"distro  : {distro}（起動中: {'はい' if distro in running else 'いいえ'}）")
    user = a.user or _wsl("-d", distro, "--", "whoami")
    base = Path(rf"\\wsl.localhost\{distro}\home\{user}")
    src = base / ".local" / "share" / "kiro-cli" / "data.sqlite3"
    print(f"source  : {src}（存在: {src.exists()}）")
    if not src.exists():
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.perf_counter()
        copied = []
        for suffix in ("", "-wal", "-shm"):
            s = Path(str(src) + suffix)
            if s.exists():
                shutil.copy2(s, Path(tmp) / ("data.sqlite3" + suffix))
                copied.append(suffix or "db")
        ms = (time.perf_counter() - t0) * 1000
        print(f"copy    : {', '.join(copied)}（{ms:.0f} ms）")
        print(f"db+wal  : {_count(Path(tmp) / 'data.sqlite3')}")
        only = Path(tmp) / "only"
        only.mkdir()
        shutil.copy2(src, only / "data.sqlite3")
        print(f"db だけ : {_count(only / 'data.sqlite3')}  ← db+wal より少なければ、-wal も写す必要がある")
    logs = sorted(base.joinpath(".kiro").rglob("conversation_log.jsonl"), key=lambda p: p.stat().st_mtime)[-1:] \
        if base.joinpath(".kiro").exists() else []
    for p in logs:
        print(f"log     : {p}（{sum(1 for _ in open(p, encoding='utf-8', errors='replace'))} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
