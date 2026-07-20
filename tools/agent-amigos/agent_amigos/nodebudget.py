"""ノード予算 — 請負ノード側の実質実行時間の上限と共有台帳（設計書 §3.3）。

ミッション予算（依頼側がバスに宣言）とは独立に、**各ノードが自分の上限**を持てる。
台帳はツール横断の共有契約（正典: schemas/node-budget.schema.json）:

    $AGENT_BUDGET_DIR（既定 ~/.agents/budget/）
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

from .configfile import agent_home_subdir
from .util import now_iso, read_json, read_jsonl, write_json_atomic

WORKLOAD = "amigos"


def budget_dir() -> str:
    """共有台帳の場所。共通ホームはサブディレクトリ単位で新旧を判定する
    （agent-project / agent-flow / kiro-loop / agent-dashboard と同じ解決）。
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
    """トークン未報告行の推定レート（tokens/秒）。解決順 cli:model → cli → default。"""
    rates = cfg.get("rates") or {}
    per = rates.get("per_cli") or {}
    for key in (f"{cli}:{model}" if model else None, cli or None):
        if key and per.get(key):
            try:
                return float(per[key])
            except (TypeError, ValueError):
                pass
    try:
        return float(rates.get("default_tokens_per_second") or 0)
    except (TypeError, ValueError):
        return 0.0


def _row_tokens(rec: dict, cfg: dict) -> float:
    """1 記帳のトークン消費。実測（tokens_in+tokens_out）があればその値、無ければ秒 × レート。"""
    ti, to = rec.get("tokens_in"), rec.get("tokens_out")
    if ti is not None or to is not None:
        try:
            return float(ti or 0) + float(to or 0)
        except (TypeError, ValueError):
            return 0.0
    try:
        sec = float(rec.get("seconds") or 0)
    except (TypeError, ValueError):
        return 0.0
    if sec <= 0:
        return 0.0
    return sec * _rate(cfg, str(rec.get("agent_cli") or ""), str(rec.get("model") or ""))


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


def _totals(cfg: dict, period: str, workload: str) -> "tuple[float, float, float, float]":
    """期間内の (合計秒, 自WL秒, 合計トークン, 自WLトークン)。トークンは実測 or 秒×レート。"""
    total_s = wl_s = tok = wl_tok = 0.0
    for path in _ledger_files(period):
        for rec in read_jsonl(path):
            try:
                sec = float(rec.get("seconds") or 0.0)
            except (TypeError, ValueError):
                sec = 0.0
            toks = _row_tokens(rec, cfg)
            is_wl = rec.get("workload") == workload
            if sec > 0:
                total_s += sec
                if is_wl:
                    wl_s += sec
            if toks > 0:
                tok += toks
                if is_wl:
                    wl_tok += toks
    return total_s, wl_s, tok, wl_tok


def state(workload: str = WORKLOAD) -> dict:
    """ノード予算 v2 の消費状況。exceeded は時間上限・トークン上限（合計 or 自ワークロードの
    実効上限）のいずれか到達。soft は縮退開始（soft_ratio 到達・未超過）。on_exhausted は
    超過時の方針（既定 pause）。0 = 無制限はどの上限にも数えない。"""
    cfg = _raw_config()
    period = str(cfg.get("period") or "day")
    limit_s = float(cfg.get("execution_minutes") or 0) * 60.0
    wl_limit_s = float((cfg.get("workloads") or {}).get(workload) or 0) * 60.0
    token_limit = float(cfg.get("tokens") or 0)
    alloc = cfg.get("allocation") or {}
    wl_alloc = (alloc.get("workloads") or {}).get(workload) or {}
    computed = ((cfg.get("computed") or {}).get("workloads") or {}).get(workload) or {}
    eff_wl_tokens = float(computed.get("tokens") or 0) or float(wl_alloc.get("max_tokens") or 0)
    on_exhausted = str(wl_alloc.get("on_exhausted") or "pause")
    try:
        soft_ratio = float(alloc.get("soft_ratio") or 0.9)
    except (TypeError, ValueError):
        soft_ratio = 0.9
    total_s, wl_s, tok, wl_tok = _totals(cfg, period, workload)
    time_exceeded = bool((limit_s and total_s >= limit_s) or (wl_limit_s and wl_s >= wl_limit_s))
    token_exceeded = bool((token_limit and tok >= token_limit)
                          or (eff_wl_tokens and wl_tok >= eff_wl_tokens))
    exceeded = bool(time_exceeded or token_exceeded)
    soft_cap = eff_wl_tokens or token_limit
    soft_spent = wl_tok if eff_wl_tokens else tok
    soft = bool(soft_cap and soft_spent >= soft_ratio * soft_cap and not exceeded)
    return {"limit_s": limit_s, "spent_s": total_s, "period": period,
            "workload_limit_s": wl_limit_s, "workload_spent_s": wl_s,
            "token_limit": token_limit, "spent_tokens": tok, "workload_spent_tokens": wl_tok,
            "exceeded": exceeded, "soft": soft, "on_exhausted": on_exhausted}


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
