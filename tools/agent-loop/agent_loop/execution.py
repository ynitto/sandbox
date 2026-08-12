from __future__ import annotations
# execution.py — Phase 2A 実行 metadata / launch spec / Ralph prompt。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。

_EXEC_KINDS = frozenset({"normal", "ralph"})
_SESSION_POLICIES = frozenset({"persistent", "oneshot", "sandbox", "external"})
_TARGET_KINDS = frozenset({"managed", "external"})
_OWNERSHIPS = frozenset({"managed-persistent", "managed-ephemeral"})

_RALPH_CONTINUE = (
    "前回の結果を検証し、未完了の作業を続けてください。"
    "完了している場合は成果を再確認してください。"
)
_RALPH_FINAL = (
    "最終反復です。未完了事項を可能な範囲で完了し、"
    "成果・検証結果・残課題を要約してください。"
)


def default_execution(request_id: str) -> dict[str, Any]:
    """Phase 1 互換の normal/persistent/managed metadata。"""
    rid = str(request_id or "")
    return {
        "kind": "normal",
        "root_id": rid,
        "step": 1,
        "max_steps": 1,
        "session_policy": "persistent",
        "session_key": "",
        "target_kind": "managed",
        "target": None,
        "model": None,
        "sandbox_id": None,
        "force_ready": False,
        "skip_preflight": False,
    }


def normalize_execution(meta: dict[str, Any] | None, *, request_id: str = "") -> dict[str, Any]:
    """meta.execution を正規化する。欠落は互換既定、不明値は ValueError。"""
    raw_meta = meta if isinstance(meta, dict) else {}
    raw = raw_meta.get("execution")
    if raw is None:
        return default_execution(request_id)
    if not isinstance(raw, dict):
        raise ValueError(f"execution は dict である必要があります: {type(raw)!r}")

    out = default_execution(request_id)
    if "kind" in raw:
        kind = str(raw["kind"])
        if kind not in _EXEC_KINDS:
            raise ValueError(f"不明な execution.kind: {kind!r}")
        out["kind"] = kind
    if "session_policy" in raw:
        policy = str(raw["session_policy"])
        if policy not in _SESSION_POLICIES:
            raise ValueError(f"不明な execution.session_policy: {policy!r}")
        out["session_policy"] = policy
    if "target_kind" in raw:
        tk = str(raw["target_kind"])
        if tk not in _TARGET_KINDS:
            raise ValueError(f"不明な execution.target_kind: {tk!r}")
        out["target_kind"] = tk
    if "root_id" in raw and raw["root_id"] is not None:
        out["root_id"] = str(raw["root_id"])
    elif request_id:
        out["root_id"] = str(request_id)
    if "step" in raw and raw["step"] is not None:
        out["step"] = int(raw["step"])
    if "max_steps" in raw and raw["max_steps"] is not None:
        out["max_steps"] = int(raw["max_steps"])
    if "session_key" in raw and raw["session_key"] is not None:
        out["session_key"] = str(raw["session_key"])
    if "target" in raw:
        out["target"] = None if raw["target"] is None else str(raw["target"])
    if "model" in raw:
        out["model"] = None if raw["model"] is None else str(raw["model"])
    if "sandbox_id" in raw:
        out["sandbox_id"] = None if raw["sandbox_id"] is None else str(raw["sandbox_id"])
    if "force_ready" in raw:
        out["force_ready"] = bool(raw["force_ready"])
    if "skip_preflight" in raw:
        out["skip_preflight"] = bool(raw["skip_preflight"])

    if out["step"] < 1:
        raise ValueError(f"execution.step は 1 以上です: {out['step']}")
    if out["max_steps"] < 1:
        raise ValueError(f"execution.max_steps は 1 以上です: {out['max_steps']}")
    if out["kind"] == "ralph" and out["max_steps"] > 100:
        raise ValueError(f"execution.max_steps は 100 以下です: {out['max_steps']}")
    return out


def build_ralph_prompt(original: str, step: int, max_steps: int) -> str:
    """Ralph 送信用 prompt（§6.1 テンプレート）。"""
    step = int(step)
    max_steps = int(max_steps)
    header = f"[agent-loop ralph iteration {step}/{max_steps}]"
    original = str(original or "")
    if max_steps <= 1 or step >= max_steps:
        if step == 1 and max_steps == 1:
            return f"{header}\n{original}\n\n{_RALPH_FINAL}"
        return f"{header}\n{_RALPH_FINAL}"
    if step == 1:
        return f"{header}\n{original}"
    return f"{header}\n{_RALPH_CONTINUE}"


def make_launch_spec(
    *,
    argv: list[str],
    env: dict[str, str] | None = None,
    cwd: str,
    profile_name: str,
    effective_model: str | None = None,
    ownership: str = "managed-persistent",
    turn_completion: str = "",
) -> dict[str, Any]:
    """pane 起動用 launch_spec（起動時に copy する dict）。"""
    if ownership not in _OWNERSHIPS:
        raise ValueError(f"不明な ownership: {ownership!r}")
    return {
        "argv": list(argv),
        "env": dict(env or {}),
        "cwd": str(cwd),
        "profile_name": str(profile_name),
        "effective_model": None if effective_model is None else str(effective_model),
        "ownership": ownership,
        "turn_completion": str(turn_completion or ""),
    }


def launch_fingerprint(profile_name: str, argv: list[str], cwd: str) -> str:
    """profile名 + argv + cwd の SHA-256（env 値は含めない）。"""
    payload = json.dumps(
        {"profile": str(profile_name), "argv": list(argv), "cwd": str(cwd)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_external_panes(raw: Any) -> list[dict[str, Any]]:
    """external_panes を副作用なしで正規化する。不正は ValueError。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"external_panes は list である必要があります: {type(raw)!r}")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"external_panes[{i}] は dict である必要があります")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError(f"external_panes[{i}].name は必須です")
        if name in seen:
            raise ValueError(f"external_panes の name が重複しています: {name}")
        seen.add(name)
        tmux_target = str(item.get("tmux_target", "")).strip()
        if not tmux_target:
            raise ValueError(f"external_panes[{i}].tmux_target は必須です ({name})")
        agent_cli = str(item.get("agent_cli", "")).strip() or None
        out.append({
            "name": name,
            "tmux_target": tmux_target,
            "agent_cli": agent_cli,
        })
    return out


def resolve_external_tmux_pane(tmux_target: str) -> str | None:
    """tmux target を毎回 display-message で pane_id へ解決する。"""
    result = _tmux_cmd(
        "display-message", "-p", "-t", str(tmux_target), "#{pane_id}",
    )
    if result.returncode != 0:
        return None
    pane = (result.stdout or "").strip()
    return pane or None
