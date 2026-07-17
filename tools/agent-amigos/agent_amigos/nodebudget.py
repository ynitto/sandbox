"""ノード予算 — 請負ノード側の実質実行時間の上限と共有台帳（設計書 §3.3）。

ミッション予算（依頼側がバスに宣言）とは独立に、**各ノードが自分の上限**を持てる。
台帳はツール横断の共有契約（正典: schemas/node-budget.schema.json）:

    $AGENT_BUDGET_DIR（既定 ~/.agent/budget/）
      config.json               … 上限設定（人 / agent-dashboard / CLI が書く。0 = 無制限）
      ledger/<YYYYMMDD>.jsonl   … 記帳（UTC 日付・追記専用・O_APPEND）

定常業務（routine）・agent-project（project）・agent-flow（flow）・amigos が同じ台帳に
記帳し、**合計**が上限を超えないよう各ツールが自律的に抑制する。agent-amigos は
自分のターンを workload=amigos で記帳し、超過時は amigo を paused にする
（ミッションは殺さない — 他ノードは進行継続）。

超過チェックは記帳の読み合計（プロセス間ロックなし）なので、上振れは
「進行中ターン × 同時実行数」に有界（ミッション予算 §3.2 と同じ性質）。
"""
from __future__ import annotations

import json
import os
import time

from .util import now_iso, read_json, read_jsonl, write_json_atomic

DEFAULT_DIR = "~/.agent/budget"
WORKLOAD = "amigos"


def budget_dir() -> str:
    return os.path.abspath(os.path.expanduser(
        os.environ.get("AGENT_BUDGET_DIR", DEFAULT_DIR)))


def config_path() -> str:
    return os.path.join(budget_dir(), "config.json")


def load_config() -> dict:
    cfg = read_json(config_path()) or {}
    return {"version": 1,
            "execution_minutes": float(cfg.get("execution_minutes") or 0),
            "period": str(cfg.get("period") or "day"),
            "workloads": {k: float(v or 0)
                          for k, v in dict(cfg.get("workloads") or {}).items()}}


def save_config(execution_minutes: "float | None" = None, period: "str | None" = None,
                workload_minutes: "dict | None" = None, updated_by: str = "cli") -> dict:
    cfg = load_config()
    if execution_minutes is not None:
        cfg["execution_minutes"] = float(execution_minutes)
    if period is not None:
        if period not in ("day", "month", "total"):
            raise SystemExit(f"[agent-amigos] period が不正です: {period!r}（day|month|total）")
        cfg["period"] = period
    if workload_minutes:
        cfg["workloads"].update({k: float(v) for k, v in workload_minutes.items()})
    cfg["updated_at"] = now_iso()
    cfg["updated_by"] = updated_by
    write_json_atomic(config_path(), cfg)
    return cfg


def _ledger_files(period: str) -> list:
    d = os.path.join(budget_dir(), "ledger")
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".jsonl"))
    except FileNotFoundError:
        return []
    if period == "day":
        names = [n for n in names if n[:8] == time.strftime("%Y%m%d", time.gmtime())]
    elif period == "month":
        names = [n for n in names if n[:6] == time.strftime("%Y%m", time.gmtime())]
    return [os.path.join(d, n) for n in names]


def spent_seconds(period: str, workload: "str | None" = None) -> float:
    total = 0.0
    for path in _ledger_files(period):
        for rec in read_jsonl(path):
            if workload and rec.get("workload") != workload:
                continue
            try:
                total += float(rec.get("seconds") or 0.0)
            except (TypeError, ValueError):
                continue
    return total


def state(workload: str = WORKLOAD) -> dict:
    """ノード予算の消費状況。exceeded は「合計上限 or 自ワークロード上限のどちらかに
    達した」（0 = 無制限はどちらにも数えない）。"""
    cfg = load_config()
    period = cfg["period"]
    limit_s = cfg["execution_minutes"] * 60.0
    spent = spent_seconds(period)
    wl_limit_s = float(cfg["workloads"].get(workload) or 0) * 60.0
    wl_spent = spent_seconds(period, workload) if wl_limit_s else 0.0
    exceeded = bool((limit_s and spent >= limit_s)
                    or (wl_limit_s and wl_spent >= wl_limit_s))
    return {"limit_s": limit_s, "spent_s": spent, "period": period,
            "workload_limit_s": wl_limit_s, "workload_spent_s": wl_spent,
            "exceeded": exceeded}


def record(seconds: float, workload: str = WORKLOAD, tool: str = "agent-amigos",
           ref: str = "", node: str = "") -> None:
    """台帳へ 1 記帳を追記する（O_APPEND — 複数プロセスの同時追記でも行は壊れない）。"""
    if seconds <= 0:
        return
    d = os.path.join(budget_dir(), "ledger")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, time.strftime("%Y%m%d", time.gmtime()) + ".jsonl")
    line = json.dumps({"ts": now_iso(), "workload": workload, "tool": tool,
                       "seconds": round(float(seconds), 3), "ref": ref, "node": node},
                      ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
