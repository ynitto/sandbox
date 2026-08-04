"""gc — 種別別保持日数での掃除と、collect へ相乗りする定期実行（設計 §3.3）。

insights と state.json は gc 対象外——洞察は蒸留の成果そのもので、消すと同じ
クラスタを再蒸留してトークンを二重に払う。処理済みカーソル（state.json の seen）は
records より長生きし、gc 後の再収集 → LLM 再投入を防ぐ。
"""
from __future__ import annotations

import datetime as _dt
import glob
import os
import time

from .configfile import resolve_audit_dir
from .store import Store
from .util import log

DEFAULT_KEEP_DAYS = {"records": 90, "transcripts": 30, "observations": 90, "reports": 30}


def _keep_days(args) -> dict:
    cfg = getattr(args, "gc_keep_days", None)
    out = dict(DEFAULT_KEEP_DAYS)
    if isinstance(cfg, dict):
        for key in out:
            v = cfg.get(key)
            if isinstance(v, (int, float)) and v >= 0:
                out[key] = int(v)
    elif isinstance(cfg, (int, float)):
        out = {k: int(cfg) for k in out}     # 単一値なら全種別へ
    return out


def _sweep_dated_jsonl(dir_path: str, keep_days: int, *, dry_run: bool) -> int:
    """<YYYYMMDD>.jsonl をファイル名の日付で消す（0 = 消さない）。"""
    if keep_days <= 0 or not os.path.isdir(dir_path):
        return 0
    floor = (_dt.datetime.now(_dt.timezone.utc)
             - _dt.timedelta(days=keep_days)).strftime("%Y%m%d")
    removed = 0
    for path in sorted(glob.glob(os.path.join(glob.escape(dir_path), "*.jsonl"))):
        day = os.path.basename(path)[:-6]
        if day and day < floor:
            removed += 1
            if not dry_run:
                try:
                    os.unlink(path)
                except OSError:
                    removed -= 1
    return removed


def _sweep_by_mtime(dir_path: str, keep_days: int, *, dry_run: bool) -> int:
    if keep_days <= 0 or not os.path.isdir(dir_path):
        return 0
    floor = time.time() - keep_days * 86400.0
    removed = 0
    for dirpath, _dirnames, filenames in os.walk(dir_path):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                if os.path.getmtime(path) < floor:
                    removed += 1
                    if not dry_run:
                        os.unlink(path)
            except OSError:
                continue
    return removed


def run_gc(args, store: Store, *, dry_run: bool = False) -> dict:
    keep = _keep_days(args)
    result = {
        "records": _sweep_dated_jsonl(store.records_dir, keep["records"], dry_run=dry_run),
        "observations": _sweep_dated_jsonl(store.observations_dir, keep["observations"],
                                           dry_run=dry_run),
        "transcripts": _sweep_by_mtime(store.transcripts_dir, keep["transcripts"],
                                       dry_run=dry_run),
        "reports": _sweep_by_mtime(store.reports_dir, keep["reports"], dry_run=dry_run),
    }
    if not dry_run:
        store.note_run("gc")
        store.save_state()
    return result


def auto_gc(args, store: Store) -> None:
    """collect の末尾から呼ばれる定期クリーンアップ。前回 gc から gc_interval_hours
    以上経過しているときだけ 1 回走る（daemon を持たない本ツールで「定期」を成立させる
    唯一の場所。C7）。"""
    interval_h = float(getattr(args, "gc_interval_hours", 24.0) or 0.0)
    if interval_h <= 0:
        return
    if time.time() - store.last_run("gc") < interval_h * 3600.0:
        return
    result = run_gc(args, store)
    total = sum(result.values())
    if total:
        log("gc", f"定期クリーンアップ: " +
            ", ".join(f"{k}={v}" for k, v in result.items() if v))


def cmd_gc(args) -> int:
    store = Store(resolve_audit_dir(args))
    dry = bool(getattr(args, "dry_run", False))
    result = run_gc(args, store, dry_run=dry)
    label = "削除対象" if dry else "削除"
    print(f"[agent-audit] gc {label}: " +
          ", ".join(f"{k}={v}" for k, v in result.items()) +
          "（insights と state.json は対象外）")
    return 0
