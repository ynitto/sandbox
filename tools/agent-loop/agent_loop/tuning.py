from __future__ import annotations
# tuning.py — agent-tuning 契約の読取と決定的適用。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。

AGENT_TUNING_MARKER = "<!-- agent-tuning"
_TUNING_DEFAULT_MAX = 4000
_TUNING_HARD_MAX = 16000
_TUNING_REV_APPLIED: "int | None" = None
_METHODS_APPLIED: list[str] = []
_METHOD_APPLICATIONS: dict[str, dict] = {}
_WARNED_IGNORED_TRIALS: set = set()

from agentcore import methods as _methodlib  # noqa: E402


def _tuning_dir() -> Path:
    return agent_home_subdir("AGENT_TUNING_DIR", "tuning").absolute()


def _methods_catalog_dir() -> Path:
    return agent_home_subdir("AGENT_METHODS_DIR", "methods").absolute()


def _read_tuning_raw() -> dict:
    """tuning.json をそのまま読む（無ければ空）。**編集系はこちらを使う。**

    `_load_tuning` は `enabled: false` のとき None を返す「適用の視点」で、
    これを編集の土台にすると全体を停止していた端末で injections / profiles / trials が
    丸ごと消え、revision も 0 から振り直しになる（単調増加の契約も壊れる）。
    """
    try:
        data = json.loads((_tuning_dir() / "tuning.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_tuning() -> "dict | None":
    data = _read_tuning_raw()
    return data if data and data.get("enabled") is not False else None


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


def render_method_blocks(data: "dict | None", agent_cli: str, *, assignment_key: str = "") -> dict:
    global _METHODS_APPLIED
    context = {
        "engine": "agent-loop", "workload": "routine", "purpose": "session", "role": "session",
        "agent_cli": agent_cli, "model": "",
        "tier": _methodlib.current_tier(_control_dir(), "routine"),
        "relative_cost": _methodlib.relative_cost(agent_cli),
    }
    app = _methodlib.select(data, context, assignment_key)
    _METHODS_APPLIED = list(app.get("methods") or [])
    _METHOD_APPLICATIONS[str(assignment_key or "")] = app
    for ignored in app.get("ignored_trials") or []:
        # 条件が重なる trial は id 順で 1 つだけ走る。捨てた分を黙っていると、
        # 宣言した trial が一度も走らないことに書いた人が気づけない。
        if ignored not in _WARNED_IGNORED_TRIALS:
            _WARNED_IGNORED_TRIALS.add(ignored)
            log.warning("trial '%s' は同時実行の対象外です"
                        "（条件が重なる trial は id 順で 1 つだけ走ります）。", ignored)
    return app


_PANE_PROMPTS: dict[str, str] = {}


def bind_method_application(pane_id: str, prompt_id: str) -> None:
    """ペイン ID ↔ 定期プロンプト ID を結ぶ。

    記帳は解放時（ペイン ID しか手元に無い）に走るので、そこで**仕事の名前**へ戻せる
    ようにここで覚える。ペイン ID は使い捨てなので、それを仕事種別として台帳へ書くと
    集計が 1 行ずつのバケットに割れる。"""
    app = _METHOD_APPLICATIONS.get(str(prompt_id or ""))
    if app:
        _METHOD_APPLICATIONS[str(pane_id or "")] = app
    if pane_id and prompt_id:
        _PANE_PROMPTS[str(pane_id)] = str(prompt_id)


def method_evidence(key: str) -> dict:
    app = _METHOD_APPLICATIONS.get(str(key or "")) or {}
    out = {"methods": list(app.get("methods") or [])}
    if isinstance(app.get("trial"), dict):
        out["trial"] = dict(app["trial"])
    return out


def routine_purpose(pane_id: str) -> str:
    """このペインが回している定期プロンプト名（分からなければ workload 名）。"""
    return _PANE_PROMPTS.get(str(pane_id or "")) or "routine"


def _write_tuning(data: dict, updated_by: str, *, base_revision: int) -> dict:
    """tuning.json を書き換える。**書く直前に revision を照合する**（compare-and-swap）。

    この契約の書き手は人・dashboard・`agent-audit tune --apply`・`agent-loop methods` の
    4 つで、全員が読んで直して全体を置き換える。照合しないと後勝ちで前の変更が消え、
    revision は両者とも +1 するので消えたことにも気づけない。
    """
    path = _tuning_dir() / "tuning.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _tuning_revision(_read_tuning_raw())
    if current != base_revision:
        raise ValueError(
            f"tuning.json が他の書き手に更新されています（revision {base_revision} → {current}）。"
            "読み直してからやり直してください")
    data["version"] = 1
    data["revision"] = max(0, base_revision) + 1
    data["updated_at"] = _utc_iso()
    data["updated_by"] = updated_by
    data.setdefault("profiles", {"default": {"injections": [], "env": []},
                                 "external-facing": {"injections": [], "env": []}})
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return data


def method_catalog() -> list[dict]:
    out = []
    try:
        paths = sorted(_methods_catalog_dir().glob("*.json"))
    except OSError:
        paths = []
    for path in paths:
        try:
            method = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(method, dict) and method.get("id"):
            out.append(method)
    return out


def method_inventory() -> dict:
    data = _read_tuning_raw()
    active = {str(m.get("id")): m for m in data.get("methods") or []
              if isinstance(m, dict) and m.get("id")}
    return {"revision": _tuning_revision(data), "catalog": method_catalog(),
            "methods": [active[k] for k in sorted(active)]}


def method_enable(method_id: str) -> dict:
    mid = str(method_id or "").strip()
    catalog = next((m for m in method_catalog() if str(m.get("id")) == mid), None)
    if not catalog:
        raise ValueError(f"カタログに無い手法です: {mid}")
    snap = dict(catalog)
    digest = hashlib.sha256(json.dumps(catalog, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()[:12]
    snap.update({"enabled": True, "source": f"methods/{mid}@{digest}"})
    data = _read_tuning_raw()
    base = _tuning_revision(data)
    methods = [m for m in data.get("methods") or []
               if isinstance(m, dict) and str(m.get("id")) != mid]
    data["methods"] = [*methods, snap]
    return _write_tuning(data, "agent-loop methods enable", base_revision=base)


def method_disable(method_id: str) -> dict:
    mid = str(method_id or "").strip()
    data = _read_tuning_raw()
    base = _tuning_revision(data)
    found = False
    methods = []
    for method in data.get("methods") or []:
        if isinstance(method, dict) and str(method.get("id")) == mid:
            method = {**method, "enabled": False}
            found = True
        methods.append(method)
    if not found:
        raise ValueError(f"有効化履歴の無い手法です: {mid}")
    data["methods"] = methods
    return _write_tuning(data, "agent-loop methods disable", base_revision=base)


def method_add(method_id: str, role: str, text: str, when: "dict | None" = None,
               description: str = "") -> dict:
    mid = str(method_id or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", mid):
        raise ValueError("id は英小文字・数字・ハイフンで指定してください")
    if role not in ("planner", "worker", "verify", "evaluator", "session"):
        raise ValueError(f"未対応の role です: {role}")
    body = str(text or "").strip()
    if not body:
        raise ValueError("text は空にできません")
    method = {"id": mid, "description": description or body[:80], "enabled": True,
              "fragments": [{"role": role, "text": body}], "when": dict(when or {}),
              "origin": "user", "source": f"custom/{mid}"}
    data = _read_tuning_raw()
    base = _tuning_revision(data)
    data["methods"] = [m for m in data.get("methods") or []
                       if isinstance(m, dict) and str(m.get("id")) != mid] + [method]
    return _write_tuning(data, "agent-loop methods add", base_revision=base)


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
