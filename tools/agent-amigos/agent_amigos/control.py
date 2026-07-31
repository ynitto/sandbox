"""agent-control — 管理面→エンジンの宣言的オーケストレーション契約（agent-amigos 側の読取）。

正典: schemas/agent-control.schema.json。$AGENT_CONTROL_DIR（既定 ~/.agents/control/）の
control.json に管理面（agent-dashboard / CLI / 人）が「望ましい状態」を書き、amigos ランナーが
ターン先頭で mtime を見て pull で適用する（push 型 IPC なし）。amigos のワークロードでは:

- ロール別のエージェント / モデル上書き（`workloads.amigos.agents.<role_id>`）
- lifecycle（run|pause|stop）— pause/stop はこのノードの amigo を働かせない
- soft/縮退中の degraded 指定

を解釈する。優先順位 control > 設定（roles[].agent_cli/model）> 既定。適用状況は
status/<tool>-<pid>.json へハートビート書換する。結合はデータ契約のみ（コード共有なし）。
"""
from __future__ import annotations

import json
import os

from .configfile import agent_home_subdir
from .util import now_iso

WORKLOAD = "amigos"
_CACHE = {"mtime": None, "data": {}}


def control_dir() -> str:
    return agent_home_subdir("AGENT_CONTROL_DIR", "control")


def load_control() -> dict:
    """control.json を mtime キャッシュ付きで読む。無ければ {}。"""
    path = os.path.join(control_dir(), "control.json")
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        _CACHE["mtime"], _CACHE["data"] = None, {}
        return {}
    if _CACHE["mtime"] != mtime:
        try:
            with open(path, encoding="utf-8") as f:
                _CACHE["data"] = json.load(f) or {}
        except (OSError, ValueError):
            _CACHE["data"] = {}
        _CACHE["mtime"] = mtime
    return _CACHE["data"]


def _workload() -> dict:
    return dict((load_control().get("workloads") or {}).get(WORKLOAD) or {})


def lifecycle() -> str:
    """このワークロードの望ましい lifecycle（run|pause|stop）。既定 run。"""
    return str(_workload().get("lifecycle") or "run")


def override(role_id: str = "") -> "tuple[str | None, str | None]":
    """(agent_cli, model) の上書き。解決 workloads.amigos.agents[role] > workloads.amigos >
    defaults。無ければ (None, None)。"""
    ctl = load_control()
    wl = _workload()
    agents = wl.get("agents") or {}
    layers = ([agents.get(role_id) or {}] if role_id else []) + [wl, ctl.get("defaults") or {}]
    cli = model = None
    for layer in layers:
        if cli is None and layer.get("agent_cli"):
            cli = str(layer.get("agent_cli"))
        if model is None and layer.get("model"):
            model = str(layer.get("model"))
    return cli, model


def degraded() -> "tuple[str | None, str | None]":
    d = _workload().get("degraded") or {}
    return (str(d["agent_cli"]) if d.get("agent_cli") else None,
            str(d["model"]) if d.get("model") else None)


def write_status(effective_cli: str = "", effective_model: str = "", life: str = "run",
                 budget: "dict | None" = None, fresh_after_sec: int = 120) -> None:
    """status/<tool>-<pid>.json へ適用状況ハートビートを原子書換する（best-effort）。"""
    ctl = load_control()
    d = os.path.join(control_dir(), "status")
    try:
        os.makedirs(d, exist_ok=True)
        rec = {"tool": "agent-amigos", "workload": WORKLOAD, "pid": os.getpid(),
               "lifecycle": life,
               "effective": {"agent_cli": effective_cli or None, "model": effective_model or None},
               "fresh_after_sec": fresh_after_sec, "ts": now_iso()}
        if ctl.get("revision") is not None:
            rec["revision_applied"] = ctl.get("revision")
        if budget is not None:
            rec["budget"] = {"exceeded": bool(budget.get("exceeded")),
                             "soft": bool(budget.get("soft"))}
        target = os.path.join(d, f"agent-amigos-{os.getpid()}.json")
        tmp = target + f".tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, target)
    except OSError:
        pass
