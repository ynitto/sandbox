"""LLM 呼び出し — agents/<name>.json 契約 + node-budget ゲート + 記帳（設計 §6.1）。

呼び出しの型は 3 エンジンと同一（agentcore.agentcli の load_cli / headless_cmd /
spill_prompt / parse_usage）。agent-audit 自身の消費も workload=audit として台帳へ
記帳し、実行前に予算を読む——超過中は LLM 段を実行しない（C1・C7）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import time

from .configfile import agent_for, agent_home_dir, resolve_budget_dir
from .util import iter_jsonl, now_iso, read_json, strip_ansi

WORKLOAD = "audit"


class LlmBlocked(RuntimeError):
    """予算・実行制御による停止（LLM を 1 回も呼ばずに終了する側の理由）。"""


class LlmError(RuntimeError):
    pass


# -- agent-control（宣言的な一時停止・CLI/モデル上書き。既定位置のみ・env は見ない） --

def _control(args) -> dict:
    path = os.path.join(agent_home_dir(), "control", "control.json")
    return read_json(path) or {}


def control_overrides(args) -> "tuple[str | None, str | None]":
    ctl = _control(args)
    lifecycle = str(ctl.get("lifecycle") or "run")
    workloads = ctl.get("workloads") or {}
    wctl = workloads.get(WORKLOAD) if isinstance(workloads, dict) else None
    if isinstance(wctl, dict) and wctl.get("lifecycle"):
        lifecycle = str(wctl["lifecycle"])
    if lifecycle in ("pause", "stop"):
        raise LlmBlocked(f"[agent-error:control] agent-control が {lifecycle} を指示しています"
                         "（dashboard で実行を許可してください）")
    cli = ctl.get("agent_cli") if isinstance(ctl.get("agent_cli"), str) else None
    model = ctl.get("model") if isinstance(ctl.get("model"), str) else None
    if isinstance(wctl, dict):
        cli = wctl.get("agent_cli") or cli
        model = wctl.get("model") or model
    return (cli or None), (model or None)


# -- node-budget（読み取り = 抑制判定・書き込み = 自分の消費の記帳のみ） --------

def _period_days(period: str) -> "list[str]":
    now = _dt.datetime.now(_dt.timezone.utc)
    if period == "day":
        return [now.strftime("%Y%m%d")]
    if period == "month":
        return [now.replace(day=1).strftime("%Y%m") + f"{d:02d}" for d in range(1, now.day + 1)]
    return []          # total: 全ファイル


def budget_exceeded(args) -> "str | None":
    """超過していれば理由文字列。ロックなしの読み合計（node-budget 契約と同じ規律）。"""
    bdir = resolve_budget_dir(args)
    cfg = read_json(os.path.join(bdir, "config.json")) or {}
    limit_min = float(cfg.get("execution_minutes") or 0.0)
    limit_tokens = float(cfg.get("tokens") or 0.0)
    if limit_min <= 0 and limit_tokens <= 0:
        return None
    period = str(cfg.get("period") or "day")
    days = _period_days(period)
    ledger_dir = os.path.join(bdir, "ledger")
    try:
        names = sorted(n for n in os.listdir(ledger_dir) if n.endswith(".jsonl"))
    except OSError:
        return None
    if days:
        names = [n for n in names if n[:-6] in days]
    seconds = tokens = 0.0
    rates = cfg.get("rates") or {}
    default_rate = float(rates.get("default_tokens_per_second") or 0.0)
    per_cli = rates.get("per_cli") or {}
    for name in names:
        for row in iter_jsonl(os.path.join(ledger_dir, name)):
            try:
                sec = float(row.get("seconds") or 0.0)
            except (TypeError, ValueError):
                sec = 0.0
            seconds += sec
            tin, tout = row.get("tokens_in"), row.get("tokens_out")
            if tin is not None or tout is not None:
                tokens += float(tin or 0) + float(tout or 0)
            else:
                cli = str(row.get("agent_cli") or "")
                model = str(row.get("model") or "")
                rate = 0.0
                for key in (f"{cli}:{model}" if model else "", cli):
                    if key and isinstance(per_cli.get(key), (int, float)):
                        rate = float(per_cli[key])
                        break
                tokens += sec * (rate or default_rate)
    if limit_min > 0 and seconds / 60.0 >= limit_min:
        return f"[agent-error:quota] [node-budget] 実行時間の上限に達しています" \
               f"（{seconds / 60.0:.0f}/{limit_min:.0f} 分・period={period}）"
    if limit_tokens > 0 and tokens >= limit_tokens:
        return f"[agent-error:quota] [node-budget] トークン上限に達しています" \
               f"（{tokens:.0f}/{limit_tokens:.0f}・period={period}）"
    return None


def record_ledger(args, seconds: float, *, ref: str, agent_cli: str, model: str,
                  tokens_in=None, tokens_out=None) -> None:
    """台帳へ 1 行追記（O_APPEND・実測できた値だけ書く）。"""
    bdir = resolve_budget_dir(args)
    day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    path = os.path.join(bdir, "ledger", f"{day}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = {"ts": now_iso(), "workload": WORKLOAD, "tool": "agent-audit",
           "usage_kind": "llm",
           "seconds": round(seconds, 3), "ref": ref, "agent_cli": agent_cli}
    if model:
        row["model"] = model
    if tokens_in is not None:
        row["tokens_in"] = tokens_in
    if tokens_out is not None:
        row["tokens_out"] = tokens_out
    line = json.dumps(row, ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


# -- 実行 ---------------------------------------------------------------------

def run_llm(args, purpose: str, prompt: str) -> str:
    """purpose（extract / distill / review）の CLI・モデルで 1 回実行して本文を返す。
    優先順位: agent-control > agents[purpose] > グローバル設定（設計 §6.1）。"""
    from agentcore import agentcli as _agentcli
    reason = budget_exceeded(args)
    if reason:
        raise LlmBlocked(reason)
    ctl_cli, ctl_model = control_overrides(args)
    cli, model = agent_for(args, purpose)
    cli = ctl_cli or cli
    model = ctl_model or model
    plug = _agentcli.load_cli(cli)
    spill, prompt = _agentcli.spill_prompt(
        prompt, int(getattr(args, "argv_limit", 100000) or 100000),
        prompt_via=plug.get("prompt_via", "stdin"),
        prefix="agent-audit-prompt-",
        instruction=_agentcli.spill_instruction("この処理の入力の全文（正規化レコードの要約）"))
    built = _agentcli.headless_cmd(plug, model, prompt, spill_path=spill)
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", **(built.get("env") or {})}
    timeout = built.get("timeout") or float(getattr(args, "agent_timeout", 300) or 300)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(built["argv"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              input=built.get("stdin"), timeout=timeout, env=env)
    except FileNotFoundError:
        raise LlmError(f"[agent-error:env] エージェント CLI が見つかりません: {cli}")
    except subprocess.TimeoutExpired:
        raise LlmError(f"[agent-error:transient] タイムアウト（{timeout:.0f}s）: {cli}")
    finally:
        if spill:
            try:
                os.unlink(spill)
            except OSError:
                pass
        elapsed = time.monotonic() - t0
    tokens_in, tokens_out = _agentcli.parse_usage(proc.stderr or "")
    record_ledger(args, elapsed, ref=purpose, agent_cli=cli, model=model or "",
                  tokens_in=tokens_in, tokens_out=tokens_out)
    if proc.returncode != 0:
        blob = (proc.stderr or "") + (proc.stdout or "")
        cls = _agentcli.classify_error(plug, blob)
        tag = f"[agent-error:{cls[0]}] " if cls else ""
        raise LlmError(f"{tag}{cli} が失敗しました（rc={proc.returncode}）: "
                       f"{blob.strip()[-300:]}")
    text = strip_ansi(proc.stdout or "").strip()
    out_file = built.get("output_file")
    if out_file:
        try:
            with open(out_file, encoding="utf-8") as f:
                text = f.read().strip() or text
        except OSError:
            pass
        finally:
            try:
                os.unlink(out_file)
            except OSError:
                pass
    if not text and built.get("empty_output_is_error", True):
        raise LlmError(f"[agent-error:env] {cli} の応答が空でした（認証切れの可能性）")
    return text


def parse_json_reply(text: str):
    """応答から JSON を取り出す（コードフェンス・前後の散文に耐える）。失敗は None。"""
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    for candidate in (s,):
        try:
            return json.loads(candidate)
        except ValueError:
            pass
    start = min((i for i in (s.find("{"), s.find("[")) if i >= 0), default=-1)
    if start >= 0:
        closers = [i + 1 for i, ch in enumerate(s) if ch in "}]" and i >= start]
        for end in reversed(closers[-50:]):     # 末尾側の閉じ括弧だけを試す（有界）
            try:
                return json.loads(s[start:end])
            except ValueError:
                continue
    return None
