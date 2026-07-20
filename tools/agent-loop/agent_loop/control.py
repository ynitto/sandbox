from __future__ import annotations
# control.py — agent-control 契約の status ハートビート（読み取りと書き出しのみ）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
#
# 正典: schemas/agent-control.schema.json。実体は $AGENT_CONTROL_DIR
# （既定 ~/.agents/control/）の control.json（管理面が原子書換）と
# status/<tool>-<pid>.json（各エンジンが適用状況を書き、管理面が読む）。
#
# あわせてノード予算（node-budget 契約: schemas/node-budget.schema.json）も読む。
# 定常業務（agent-loop）・agent-project・agent-flow・agent-amigos が同じ台帳
# （$AGENT_BUDGET_DIR、既定 ~/.agents/budget/）に記帳し、合計が上限（0 = 無制限）を
# 超えたら新規のプロンプト送信を控える。agent-loop は subprocess で LLM を呼ばない
# （tmux のエージェント CLI に送信する）ため、実行秒はセマフォスロットの保持時間
# （送信 → 完了検知）で近似して記帳する。セマフォ未設定（max_concurrent <= 0）のときは
# 計測点が無く記帳されない（kiro-loop と同じ既知の制約）。
#
# 由来: tools/kiro-loop/kiro-loop.py の同名実装をクローン（agent-loop は後継クローン）。

_NODE_BUDGET_WORKLOAD = "routine"
_NODE_BUDGET_TOOL = "agent-loop"
_CONTROL_CACHE = {"mtime": None, "data": {}}


def _utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _control_dir() -> str:
    return str(agent_home_subdir("AGENT_CONTROL_DIR", "control").absolute())


def _load_control() -> dict:
    """control.json を mtime キャッシュ付きで読む。無ければ {}。"""
    path = os.path.join(_control_dir(), "control.json")
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        _CONTROL_CACHE["mtime"], _CONTROL_CACHE["data"] = None, {}
        return {}
    if _CONTROL_CACHE["mtime"] != mtime:
        try:
            with open(path, encoding="utf-8") as f:
                _CONTROL_CACHE["data"] = json.load(f) or {}
        except (OSError, ValueError):
            _CONTROL_CACHE["data"] = {}
        _CONTROL_CACHE["mtime"] = mtime
    return _CONTROL_CACHE["data"]


def _control_workload() -> dict:
    return dict((_load_control().get("workloads") or {}).get(_NODE_BUDGET_WORKLOAD) or {})


def _control_lifecycle() -> str:
    """このワークロードの望ましい lifecycle（run|pause|stop）。既定 run。"""
    return str(_control_workload().get("lifecycle") or "run")


# --- ノード予算（node-budget 契約 v2: トークン一次・時間は v1 互換で AND） ---------------

def _node_budget_dir() -> str:
    return str(agent_home_subdir("AGENT_BUDGET_DIR", "budget").absolute())


def _node_budget_rate(cfg: dict, cli: str, model: str) -> float:
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
    return sec * _node_budget_rate(cfg, str(rec.get("agent_cli") or ""), str(rec.get("model") or ""))


def _node_budget_state() -> "dict | None":
    """ノード予算の消費状況。設定が無い / 上限が全て 0 なら None（= 無制限・チェック不要）。
    exceeded はトークン上限か時間上限のいずれか到達。soft は縮退開始（soft_ratio 到達・未超過）。
    on_exhausted は超過時の方針（既定 pause）。"""
    base = _node_budget_dir()
    try:
        with open(os.path.join(base, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    limit_min = float(cfg.get("execution_minutes") or 0)
    wl_limit_min = float((cfg.get("workloads") or {}).get(_NODE_BUDGET_WORKLOAD) or 0)
    token_limit = float(cfg.get("tokens") or 0)
    alloc = cfg.get("allocation") or {}
    wl_alloc = (alloc.get("workloads") or {}).get(_NODE_BUDGET_WORKLOAD) or {}
    computed = ((cfg.get("computed") or {}).get("workloads") or {}).get(_NODE_BUDGET_WORKLOAD) or {}
    eff_wl_tokens = float(computed.get("tokens") or 0) or float(wl_alloc.get("max_tokens") or 0)
    on_exhausted = str(wl_alloc.get("on_exhausted") or "pause")
    try:
        soft_ratio = float(alloc.get("soft_ratio") or 0.9)
    except (TypeError, ValueError):
        soft_ratio = 0.9
    if limit_min <= 0 and wl_limit_min <= 0 and token_limit <= 0 and eff_wl_tokens <= 0:
        return None
    period = str(cfg.get("period") or "day")
    prefix = (time.strftime("%Y%m%d", time.gmtime()) if period == "day"
              else time.strftime("%Y%m", time.gmtime()) if period == "month" else "")
    total = wl_total = tok_total = wl_tok = 0.0
    led = os.path.join(base, "ledger")
    try:
        names = sorted(n for n in os.listdir(led)
                       if n.endswith(".jsonl") and n.startswith(prefix))
    except OSError:
        names = []
    for name in names:
        try:
            with open(os.path.join(led, name), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        sec = float(rec.get("seconds") or 0)
                    except (ValueError, TypeError):
                        continue
                    toks = _row_tokens(rec, cfg)
                    is_wl = rec.get("workload") == _NODE_BUDGET_WORKLOAD
                    if sec > 0:
                        total += sec
                        if is_wl:
                            wl_total += sec
                    if toks > 0:
                        tok_total += toks
                        if is_wl:
                            wl_tok += toks
        except OSError:
            continue
    time_exceeded = ((limit_min > 0 and total >= limit_min * 60)
                     or (wl_limit_min > 0 and wl_total >= wl_limit_min * 60))
    token_exceeded = ((token_limit > 0 and tok_total >= token_limit)
                      or (eff_wl_tokens > 0 and wl_tok >= eff_wl_tokens))
    exceeded = bool(time_exceeded or token_exceeded)
    soft_cap = eff_wl_tokens or token_limit
    soft_spent = wl_tok if eff_wl_tokens else tok_total
    soft = bool(soft_cap > 0 and soft_spent >= soft_ratio * soft_cap and not exceeded)
    return {"exceeded": exceeded, "soft": soft, "on_exhausted": on_exhausted,
            "spent_min": total / 60, "limit_min": limit_min,
            "spent_tokens": tok_total, "token_limit": token_limit, "period": period}


def _node_budget_record(seconds: float, ref: str = "", agent_cli: str = "routine",
                        model: str = "", tokens_in=None, tokens_out=None, usd=None) -> None:
    """台帳へ 1 記帳を追記する（O_APPEND — 複数プロセスの同時追記でも行は壊れない）。
    agent-loop は subprocess で LLM を呼ばないためトークンは実測できず、agent_cli 帰属のみ付す。"""
    if seconds <= 0 and not tokens_in and not tokens_out:
        return
    d = os.path.join(_node_budget_dir(), "ledger")
    try:
        os.makedirs(d, exist_ok=True)
        rec = {"ts": _utc_iso(), "workload": _NODE_BUDGET_WORKLOAD,
               "tool": _NODE_BUDGET_TOOL, "seconds": round(float(seconds), 3), "ref": ref}
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
        fd = os.open(os.path.join(d, time.strftime("%Y%m%d", time.gmtime()) + ".jsonl"),
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass    # 記帳失敗で実行を止めない（台帳は best-effort、上限は次の送信前チェックで効く）


def _write_stopped_reason(reason: str) -> None:
    """予算枯渇 / 管理面停止で graceful 終了する際、停止理由を state ディレクトリへ残す。"""
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        (_STATE_DIR / f"stopped-{os.getpid()}.json").write_text(
            json.dumps({"pid": os.getpid(), "stopped_reason": reason, "ts": _utc_iso()},
                       ensure_ascii=False),
            encoding="utf-8")
    except OSError:
        pass


def _write_status(lifecycle: str = "run", budget: "dict | None" = None,
                  fresh_after_sec: int = 120) -> None:
    """status/<tool>-<pid>.json へ適用状況ハートビートを原子書換する（best-effort）。
    書けなくても定常業務は止めない。"""
    ctl = _load_control()
    d = os.path.join(_control_dir(), "status")
    try:
        os.makedirs(d, exist_ok=True)
        rec = {"tool": _NODE_BUDGET_TOOL, "workload": _NODE_BUDGET_WORKLOAD,
               "pid": os.getpid(), "lifecycle": lifecycle,
               "effective": {"agent_cli": _NODE_BUDGET_TOOL, "model": None},
               "fresh_after_sec": fresh_after_sec, "ts": _utc_iso()}
        if ctl.get("revision") is not None:
            rec["revision_applied"] = ctl.get("revision")
        # グローバル指示: 直近で paste 注入したブロックの revision（未注入は省略）。
        if _INSTRUCTIONS_REV_APPLIED is not None:
            rec["instructions_revision_applied"] = _INSTRUCTIONS_REV_APPLIED
        # セッション開始コマンド: 直近のペイン起動で適用した revision（未適用は省略）。
        if _SESSION_COMMANDS_REV_APPLIED is not None:
            rec["session_commands_revision_applied"] = _SESSION_COMMANDS_REV_APPLIED
        if budget is not None:
            rec["budget"] = {"exceeded": bool(budget.get("exceeded")),
                             "soft": bool(budget.get("soft"))}
        target = os.path.join(d, f"{_NODE_BUDGET_TOOL}-{os.getpid()}.json")
        tmp = target + f".tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, target)
    except OSError:
        pass
