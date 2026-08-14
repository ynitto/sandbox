"""agentcore.nodebudget — node-budget 読取・推定・state / can_accept の単一実装。

## なぜここに 1 実装を置くか

同じ「台帳を読み、未報告行を rates で推定し、exceeded / soft を決める」判断が
5 箇所に複製されていた（C7 違反）:

- agent-amigos `nodebudget.py`
- agent-flow `agent.py`
- agent-project `prioritize.py`
- agent-loop `control.py`
- agent-dashboard `orchestration/main/budget.js`（管理面・本モジュールは Python 正本。
  JS 置換は別タスク。同一 fixture で突き合わせる）

正典契約: `schemas/node-budget.schema.json`。
置き場: `$AGENT_BUDGET_DIR`（既定 `~/.agents/budget/`）の `config.json` +
`ledger/<YYYYMMDD>.jsonl`。

本モジュールは **読取・推定・state / can_accept** だけを持つ。記帳（record）・
配分計算（allocation rebalance）・status 射影・eligible 接続は呼び出し側 / 後続タスク。
"""
from __future__ import annotations

import json
import os
import time
from typing import Iterator

AGENT_HOME = ".agents"
BUDGET_ENV = "AGENT_BUDGET_DIR"
BUDGET_SUBDIR = "budget"

# can_accept の固定語彙（summary / allocate が後続で参照する）。未知コードを足すな。
REASON_UNLIMITED = "unlimited"
REASON_OK = "ok"
REASON_SOFT = "soft"
REASON_TIME_EXCEEDED = "time_exceeded"
REASON_TOKEN_EXCEEDED = "token_exceeded"
REASON_EXCEEDED = "exceeded"
REASON_DEGRADE = "degrade"
REASON_UNAVAILABLE = "unavailable"

KNOWN_WORKLOADS = ("routine", "project", "flow", "amigos")


def budget_dir(override: "str | None" = None) -> str:
    """共有台帳ディレクトリ。`$AGENT_BUDGET_DIR` → `~/.agents/budget`。"""
    raw = override if override is not None else os.environ.get(BUDGET_ENV)
    if raw:
        return os.path.abspath(os.path.expanduser(str(raw)))
    return os.path.abspath(os.path.join(os.path.expanduser("~"), AGENT_HOME, BUDGET_SUBDIR))


def config_path(dir: "str | None" = None) -> str:
    return os.path.join(budget_dir(dir) if dir is not None else budget_dir(), "config.json")


def read_config(dir: "str | None" = None) -> dict:
    """config.json の生 dict。無い / 壊れているときは {}（呼び出し側が無制限扱い）。"""
    path = config_path(dir)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def rate(cfg: dict, cli: str = "", model: str = "") -> float:
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


def row_tokens(rec: dict, cfg: dict) -> float:
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
    return sec * rate(cfg, str(rec.get("agent_cli") or ""), str(rec.get("model") or ""))


def _period_prefix(period: str, now: "float | None" = None) -> str:
    gm = time.gmtime(now) if now is not None else time.gmtime()
    if period == "day":
        return time.strftime("%Y%m%d", gm)
    if period == "month":
        return time.strftime("%Y%m", gm)
    return ""


def ledger_paths(dir: "str | None" = None, period: str = "day",
                 now: "float | None" = None) -> list:
    """期間に入る ledger/*.jsonl の絶対パス（名前昇順）。"""
    base = budget_dir(dir) if dir is not None else budget_dir()
    led = os.path.join(base, "ledger")
    try:
        names = sorted(n for n in os.listdir(led) if n.endswith(".jsonl"))
    except OSError:
        return []
    prefix = _period_prefix(period, now)
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    return [os.path.join(led, n) for n in names]


def iter_ledger_records(dir: "str | None" = None, period: str = "day",
                        now: "float | None" = None) -> Iterator[dict]:
    """期間内の台帳行を yield。壊れた行は飛ばす。"""
    for path in ledger_paths(dir, period, now=now):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(rec, dict):
                        yield rec
        except OSError:
            continue


def totals(cfg: dict, period: str, workload: str, *,
           dir: "str | None" = None, now: "float | None" = None
           ) -> "tuple[float, float, float, float]":
    """期間内の (合計秒, 自 WL 秒, 合計トークン, 自 WL トークン)。

    観測行（event / quota_kind）はスキーマ上 seconds=0・トークン無しなので消費に入らない。
    エンジン既存実装と同じく明示スキップはしない（不正行でも secs/toks>0 なら数える）。
    """
    total_s = wl_s = tok = wl_tok = 0.0
    for rec in iter_ledger_records(dir, period, now=now):
        try:
            sec = float(rec.get("seconds") or 0.0)
        except (TypeError, ValueError):
            sec = 0.0
        toks = row_tokens(rec, cfg)
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


def _soft_ratio(alloc: dict) -> float:
    try:
        return float(alloc.get("soft_ratio") or 0.9)
    except (TypeError, ValueError):
        return 0.9


def _eff_wl_tokens(cfg: dict, workload: str) -> float:
    alloc = cfg.get("allocation") or {}
    wl_alloc = (alloc.get("workloads") or {}).get(workload) or {}
    computed = ((cfg.get("computed") or {}).get("workloads") or {}).get(workload) or {}
    return float(computed.get("tokens") or 0) or float(wl_alloc.get("max_tokens") or 0)


def _on_exhausted(cfg: dict, workload: str) -> str:
    alloc = cfg.get("allocation") or {}
    wl_alloc = (alloc.get("workloads") or {}).get(workload) or {}
    return str(wl_alloc.get("on_exhausted") or "pause")


def compute_state(workload: str, *, dir: "str | None" = None,
                  cfg: "dict | None" = None, now: "float | None" = None
                  ) -> dict:
    """正規 state（常に dict）。上限が全て 0 でも exceeded=False の無制限ビューを返す。

    呼び出し側が「チェック不要」とみなすかどうかは `has_limits` / `engine_view` を使う。
    """
    raw = cfg if cfg is not None else read_config(dir)
    period = str(raw.get("period") or "day")
    limit_min = float(raw.get("execution_minutes") or 0)
    wl_limit_min = float((raw.get("workloads") or {}).get(workload) or 0)
    token_limit = float(raw.get("tokens") or 0)
    eff_wl_tokens = _eff_wl_tokens(raw, workload)
    on_exhausted = _on_exhausted(raw, workload)
    soft_ratio = _soft_ratio(raw.get("allocation") or {})
    total_s, wl_s, tok, wl_tok = totals(raw, period, workload, dir=dir, now=now)
    limit_s = limit_min * 60.0
    wl_limit_s = wl_limit_min * 60.0
    time_exceeded = bool((limit_s and total_s >= limit_s)
                         or (wl_limit_s and wl_s >= wl_limit_s))
    token_exceeded = bool((token_limit and tok >= token_limit)
                          or (eff_wl_tokens and wl_tok >= eff_wl_tokens))
    exceeded = bool(time_exceeded or token_exceeded)
    soft_cap = eff_wl_tokens or token_limit
    soft_spent = wl_tok if eff_wl_tokens else tok
    soft = bool(soft_cap and soft_spent >= soft_ratio * soft_cap and not exceeded)
    has_limits = bool(limit_min > 0 or wl_limit_min > 0 or token_limit > 0 or eff_wl_tokens > 0)
    return {
        "workload": workload,
        "period": period,
        "limit_s": limit_s,
        "limit_min": limit_min,
        "spent_s": total_s,
        "spent_min": total_s / 60.0,
        "workload_limit_s": wl_limit_s,
        "workload_limit_min": wl_limit_min,
        "workload_spent_s": wl_s,
        "token_limit": token_limit,
        "spent_tokens": tok,
        "workload_spent_tokens": wl_tok,
        "eff_wl_tokens": eff_wl_tokens,
        "time_exceeded": time_exceeded,
        "token_exceeded": token_exceeded,
        "exceeded": exceeded,
        "soft": soft,
        "soft_ratio": soft_ratio,
        "on_exhausted": on_exhausted,
        "has_limits": has_limits,
        "config_present": bool(raw),
    }


def engine_view(st: dict) -> "dict | None":
    """flow / project / loop 互換: 上限なしなら None、あれば要約 dict。"""
    if not st.get("has_limits"):
        return None
    return {
        "exceeded": bool(st["exceeded"]),
        "soft": bool(st["soft"]),
        "on_exhausted": str(st["on_exhausted"]),
        "spent_min": float(st["spent_min"]),
        "limit_min": float(st["limit_min"]),
        "spent_tokens": float(st["spent_tokens"]),
        "token_limit": float(st["token_limit"]),
        "period": str(st["period"]),
    }


def amigos_view(st: dict) -> dict:
    """amigos nodebudget.state() 互換（無制限でも常に dict）。"""
    return {
        "limit_s": float(st["limit_s"]),
        "spent_s": float(st["spent_s"]),
        "period": str(st["period"]),
        "workload_limit_s": float(st["workload_limit_s"]),
        "workload_spent_s": float(st["workload_spent_s"]),
        "token_limit": float(st["token_limit"]),
        "spent_tokens": float(st["spent_tokens"]),
        "workload_spent_tokens": float(st["workload_spent_tokens"]),
        "exceeded": bool(st["exceeded"]),
        "soft": bool(st["soft"]),
        "on_exhausted": str(st["on_exhausted"]),
    }


def state(workload: str, *, dir: "str | None" = None,
          cfg: "dict | None" = None, now: "float | None" = None,
          view: str = "engine") -> "dict | None":
    """state の入口。view=engine（既定）| amigos | full。"""
    st = compute_state(workload, dir=dir, cfg=cfg, now=now)
    if view == "amigos":
        return amigos_view(st)
    if view == "full":
        return st
    return engine_view(st)


def can_accept(workload: str, *, dir: "str | None" = None,
               cfg: "dict | None" = None, now: "float | None" = None,
               st: "dict | None" = None) -> dict:
    """所有者ノードが「新規仕事を受けられるか」を決める。

    エンジン現行ゲートと同じ: 上限なし → 可。超過かつ on_exhausted != degrade → 不可。
    degrade 超過は縮退継続のため可（reason に degrade を付ける）。

    戻り値: ``{"can_accept": bool, "reason_codes": [str, ...], "state": full_state}``
    """
    full = st if st is not None else compute_state(workload, dir=dir, cfg=cfg, now=now)
    codes: list[str] = []
    if not full.get("config_present") and not full.get("has_limits"):
        # 設定ファイル無し = 宣言なし。無制限として受ける（未知とは区別: unavailable にしない）。
        return {"can_accept": True, "reason_codes": [REASON_UNLIMITED], "state": full}
    if not full.get("has_limits"):
        return {"can_accept": True, "reason_codes": [REASON_UNLIMITED], "state": full}
    if full.get("exceeded"):
        codes.append(REASON_EXCEEDED)
        if full.get("time_exceeded"):
            codes.append(REASON_TIME_EXCEEDED)
        if full.get("token_exceeded"):
            codes.append(REASON_TOKEN_EXCEEDED)
        if str(full.get("on_exhausted") or "pause") == "degrade":
            codes.append(REASON_DEGRADE)
            return {"can_accept": True, "reason_codes": codes, "state": full}
        return {"can_accept": False, "reason_codes": codes, "state": full}
    if full.get("soft"):
        return {"can_accept": True, "reason_codes": [REASON_SOFT], "state": full}
    return {"can_accept": True, "reason_codes": [REASON_OK], "state": full}


def spent_seconds(period: str, workload: "str | None" = None, *,
                  dir: "str | None" = None, now: "float | None" = None) -> float:
    """期間内の実行秒合計（workload 指定時はその WL のみ）。amigos.spent_seconds 互換。"""
    total = 0.0
    for rec in iter_ledger_records(dir, period, now=now):
        if workload and rec.get("workload") != workload:
            continue
        try:
            total += float(rec.get("seconds") or 0.0)
        except (TypeError, ValueError):
            continue
    return total
