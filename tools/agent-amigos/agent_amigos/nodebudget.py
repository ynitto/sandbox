"""ノード予算 — 請負ノード側の実質実行時間の上限と共有台帳（設計書 §6.2）。

ミッション予算（依頼側がバスに宣言）とは独立に、**各ノードが自分の上限**を持てる。
台帳はツール横断の共有契約（正典: schemas/node-budget.schema.json）:

    $AGENT_BUDGET_DIR（既定 ~/.agents/budget/）
      config.json               … 上限設定（人 / agent-dashboard / CLI が書く。0 = 無制限）
      ledger/<YYYYMMDD>.jsonl   … 記帳（UTC 日付・追記専用・O_APPEND）

定常業務（routine）・agent-project（project）・agent-flow（flow）・amigos が同じ台帳に
記帳し、**合計**が上限を超えないよう各ツールが自律的に抑制する。agent-amigos は
自分のターンを workload=amigos で記帳し、超過時は amigo を paused にする
（ミッションは殺さない — 他ノードは進行継続）。

読取・推定・state は agentcore.nodebudget に集約（C7）。本モジュールは設定更新・記帳と
薄い委譲を残す。
"""
from __future__ import annotations

import json
import os
import time

from agentcore import nodebudget as _core

from .configfile import agent_home_subdir
from .util import now_iso, read_json, write_json_atomic

WORKLOAD = "amigos"


def budget_dir() -> str:
    """共有台帳の場所。共通ホームはサブディレクトリ単位で新旧を判定する
    （agent-project / agent-flow / agent-loop / agent-dashboard と同じ解決）。
    旧 ~/.agent/budget 決め打ちだと、.agents へ移行済みの端末で agent-amigos だけ
    別の台帳へ記帳し、ツール横断の合計という契約の前提が崩れる。"""
    return os.path.abspath(agent_home_subdir("AGENT_BUDGET_DIR", "budget"))


def config_path() -> str:
    return os.path.join(budget_dir(), "config.json")


def _raw_config() -> dict:
    """config.json の生データ（v2 キー tokens / allocation / computed / rates を含む）。"""
    return read_json(config_path()) or {}


def load_config() -> dict:
    """v1 正規化ビュー（後方互換）。v2 の生データは _raw_config() を使う。"""
    cfg = _raw_config()
    return {"version": cfg.get("version") or 1,
            "execution_minutes": float(cfg.get("execution_minutes") or 0),
            "period": str(cfg.get("period") or "day"),
            "tokens": float(cfg.get("tokens") or 0),
            "workloads": {k: float(v or 0)
                          for k, v in dict(cfg.get("workloads") or {}).items()}}


def save_config(execution_minutes: "float | None" = None, period: "str | None" = None,
                workload_minutes: "dict | None" = None, tokens: "float | None" = None,
                updated_by: str = "cli") -> dict:
    """config.json を部分更新する。dashboard が書いた v2 キー（allocation / computed /
    rates 等）は保持したまま v1 の上限だけを書き換える（未知キーを消さない）。"""
    cfg = _raw_config()
    if execution_minutes is not None:
        cfg["execution_minutes"] = float(execution_minutes)
    if tokens is not None:
        cfg["tokens"] = float(tokens)
    if period is not None:
        if period not in ("day", "month", "total"):
            raise SystemExit(f"[agent-amigos] period が不正です: {period!r}（day|month|total）")
        cfg["period"] = period
    if workload_minutes:
        wl = dict(cfg.get("workloads") or {})
        wl.update({k: float(v) for k, v in workload_minutes.items()})
        cfg["workloads"] = wl
    if not cfg.get("version"):
        cfg["version"] = 1
    cfg["updated_at"] = now_iso()
    cfg["updated_by"] = updated_by
    write_json_atomic(config_path(), cfg)
    return load_config()


def _rate(cfg: dict, cli: str, model: str) -> float:
    return _core.rate(cfg, cli, model)


def _row_tokens(rec: dict, cfg: dict) -> float:
    return _core.row_tokens(rec, cfg)


def _ledger_files(period: str) -> list:
    return _core.ledger_paths(budget_dir(), period)


def spent_seconds(period: str, workload: "str | None" = None) -> float:
    return _core.spent_seconds(period, workload, dir=budget_dir())


def _totals(cfg: dict, period: str, workload: str) -> "tuple[float, float, float, float]":
    return _core.totals(cfg, period, workload, dir=budget_dir())


def state(workload: str = WORKLOAD) -> dict:
    """ノード予算 v2 の消費状況。exceeded は時間上限・トークン上限（合計 or 自ワークロードの
    実効上限）のいずれか到達。soft は縮退開始（soft_ratio 到達・未超過）。on_exhausted は
    超過時の方針（既定 pause）。0 = 無制限はどの上限にも数えない。"""
    return _core.state(workload, dir=budget_dir(), cfg=_raw_config(), view="amigos")  # type: ignore[return-value]


def record(seconds: float, workload: str = WORKLOAD, tool: str = "agent-amigos",
           ref: str = "", node: str = "", agent_cli: str = "", model: str = "",
           tokens_in=None, tokens_out=None, usd=None) -> None:
    """台帳へ 1 記帳を追記する（O_APPEND — 複数プロセスの同時追記でも行は壊れない）。
    tokens_* は実測できたときだけ渡す（推定値は書かない）。agent_cli / model は帰属。"""
    if seconds <= 0 and not tokens_in and not tokens_out:
        return
    d = os.path.join(budget_dir(), "ledger")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, time.strftime("%Y%m%d", time.gmtime()) + ".jsonl")
    rec = {"ts": now_iso(), "workload": workload, "tool": tool,
           "seconds": round(float(seconds), 3), "ref": ref, "node": node}
    if agent_cli:
        rec["agent_cli"] = str(agent_cli)
    if model:
        rec["model"] = str(model)
    if tokens_in is not None:
        rec["tokens_in"] = float(tokens_in)
    if tokens_out is not None:
        rec["tokens_out"] = float(tokens_out)
    if usd is not None:
        rec["usd"] = float(usd)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
