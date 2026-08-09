from __future__ import annotations
# tuning.py — agent-tuning 契約の読取と決定的適用。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。

AGENT_TUNING_MARKER = "<!-- agent-tuning"
_TUNING_DEFAULT_MAX = 4000
_TUNING_HARD_MAX = 16000
_TUNING_REV_APPLIED: "int | None" = None


def _tuning_dir() -> Path:
    return agent_home_subdir("AGENT_TUNING_DIR", "tuning").absolute()


def _load_tuning() -> "dict | None":
    try:
        data = json.loads((_tuning_dir() / "tuning.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("enabled") is not False else None


def _tuning_revision(data: "dict | None") -> int:
    try:
        return int((data or {}).get("revision") or 0)
    except (TypeError, ValueError):
        return 0


def _tuning_context(agent_cli: str) -> dict[str, str]:
    return {"engine": "agent-loop", "workload": "routine", "agent_cli": agent_cli}


def _tuning_profile(data: dict, name: str) -> dict:
    """プロファイルを引く。`external-facing` は**契約に関わらず注入なし**へ丸める。

    スキーマは `profiles.external-facing.injections` を空に縛るが、tuning.json は人も書く
    ファイルで、読むときにスキーマ検証は走らない。「外向き成果物に文体圧縮を漏らさない」を
    ドキュメントの約束だけに預けると、1 行書き足しただけで破れる。読み手側で潰す。"""
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    profile = profiles.get(name)
    if name == "external-facing":
        env = profile.get("env") if isinstance(profile, dict) and isinstance(profile.get("env"), list) else []
        return {"injections": [], "env": env}
    if isinstance(profile, dict):
        return profile
    default = profiles.get("default")
    return default if isinstance(default, dict) else {"injections": [], "env": []}


def _tuning_items(data: dict, kind: str, profile_name: str, agent_cli: str) -> list[dict]:
    profile = _tuning_profile(data, profile_name)
    selected = profile.get(kind) if isinstance(profile.get(kind), list) else []
    by_id = {
        str(item.get("id")): item for item in (data.get(kind) or [])
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    ctx = _tuning_context(agent_cli)
    return [by_id[item_id] for item_id in selected
            if item_id in by_id and session_command_matches(by_id[item_id].get("when"), ctx)]


def _tuning_source_text(item: dict) -> str:
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    if source.get("type") == "inline":
        text = str(source.get("text") or "")
    elif source.get("type") == "file":
        try:
            text = Path(os.path.expanduser(str(source.get("path") or ""))).read_text(encoding="utf-8")
        except OSError:
            return ""
    else:
        return ""
    try:
        cap = int(source.get("max_chars") or _TUNING_DEFAULT_MAX)
    except (TypeError, ValueError):
        cap = _TUNING_DEFAULT_MAX
    cap = max(1, min(cap, _TUNING_HARD_MAX))
    return text.strip()[:cap]


def render_tuning_blocks(data: "dict | None", profile_name: str, agent_cli: str,
                         *, include_session_start: bool) -> str:
    if not isinstance(data, dict):
        return ""
    rev = _tuning_revision(data)
    blocks = []
    for item in _tuning_items(data, "injections", profile_name, agent_cli):
        trigger = str(item.get("trigger") or "")
        if trigger == "session_start" and not include_session_start:
            continue
        if trigger not in ("session_start", "every_prompt"):
            continue
        text = _tuning_source_text(item)
        if text:
            blocks.append(f"{AGENT_TUNING_MARKER} rev:{rev} id:{item['id']} -->\n{text}")
    return "\n\n".join(blocks)


def tuning_launch_env(data: "dict | None", profile_name: str, agent_cli: str,
                      base_path: str) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    paths: list[str] = []
    for item in _tuning_items(data, "env", profile_name, agent_cli):
        for raw in (item.get("path_prepend") or []):
            path = os.path.expanduser(str(raw).strip())
            if path and path not in paths:
                paths.append(path)
        for key, value in (item.get("vars") or {}).items():
            name = str(key).strip()
            if name and name not in ("HOME", "AGENT_HOME", "PATH"):
                out[name] = str(value)
    if paths:
        out["PATH"] = os.pathsep.join([*paths, base_path])
    return out
