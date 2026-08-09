from __future__ import annotations
# methods.py — agent-tuning.methods の追補注入（条件評価は agentcore の共通実装）。
from agentcore import methods as _methodlib  # noqa: E402

_METHOD_ASSIGNMENT_KEY = ""
_METHOD_RUN_ID = ""
_METHOD_NODE_ID = ""
_LAST_METHOD_APPLICATION: dict[str, dict] = {}


def _set_method_context(run_id: str, node_id: str = "") -> None:
    global _METHOD_ASSIGNMENT_KEY, _METHOD_RUN_ID, _METHOD_NODE_ID
    _METHOD_RUN_ID, _METHOD_NODE_ID = str(run_id or ""), str(node_id or "")
    _METHOD_ASSIGNMENT_KEY = _METHOD_RUN_ID
    _LAST_METHOD_APPLICATION.clear()


def _method_tuning_file() -> str:
    return os.path.join(agent_home_subdir("AGENT_TUNING_DIR", "tuning"), "tuning.json")


def _method_context(purpose: str, agent_cli: str, model: "str | None") -> dict:
    return {
        "engine": "agent-flow", "workload": "flow", "purpose": str(purpose or ""),
        "role": _methodlib.role_for(purpose), "agent_cli": agent_cli, "model": str(model or ""),
        "tier": _methodlib.current_tier(_control_dir(), "flow"),
        "relative_cost": _methodlib.relative_cost(agent_cli, os.getcwd()),
    }


_WARNED_IGNORED_TRIALS: set = set()


def _apply_methods(prompt: str, purpose: str, agent_cli: str, model: "str | None") -> str:
    app = _methodlib.select(_methodlib.load(_method_tuning_file()),
                            _method_context(purpose, agent_cli, model), _METHOD_ASSIGNMENT_KEY)
    _LAST_METHOD_APPLICATION[str(purpose or "")] = app
    for ignored in app.get("ignored_trials") or []:
        # 同時に走らせられるのは 1 trial だけ。捨てた分を黙っていると、宣言した trial が
        # 一度も走らないことに書いた人が気づけない（プロセスにつき 1 回だけ言う）。
        if ignored not in _WARNED_IGNORED_TRIALS:
            _WARNED_IGNORED_TRIALS.add(ignored)
            log("methods", f"trial '{ignored}' は同時実行の対象外です"
                           "（条件が重なる trial は id 順で 1 つだけ走ります）")
    return f"{prompt}\n\n{app['text']}" if app.get("text") else prompt


def _last_methods(purpose: str) -> dict:
    return dict(_LAST_METHOD_APPLICATION.get(str(purpose or "")) or
                {"methods": [], "trial": None})


def _method_ledger_fields(purpose: str) -> dict:
    """台帳へ添える手法の証拠。**空の値は書かない**——全消費行に `methods: []` と
    `run_id: ""` が並ぶと、追記専用の台帳が中身の無い列で太る。"""
    app = _last_methods(purpose)
    out = {}
    if _METHOD_RUN_ID:
        out["run_id"] = _METHOD_RUN_ID
    if _METHOD_NODE_ID:
        out["flow_node"] = _METHOD_NODE_ID
    if app.get("methods"):
        out["methods"] = list(app["methods"])
    if isinstance(app.get("trial"), dict):
        out["trial"] = app["trial"]
    return out
