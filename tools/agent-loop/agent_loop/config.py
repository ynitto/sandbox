from __future__ import annotations
# config.py — 元 agent-loop.py の 677-940 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# 設定ロード
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_NAMES = ["agent-loop.yaml", "agent-loop.yml", "agent-loop.json"]
_LOOKUP_RE = re.compile(r"\{\{\s*lookup\s+([^\s{}]+)\s+([^\s{}]+)\s*\}\}")


def _resolve_config_mappings(
    config: dict[str, Any],
    fallback: "dict[str, Any] | None" = None,
) -> dict[str, Any]:
    """設定内の `{{lookup <ラベル> <キー>}}` を mapping で解決する。

    fallback は共通設定（~/.agents）側の mapping。ファイル自身の mapping が
    ラベル・キー単位で勝つ。解決はどちらにも無いときだけ設定エラー。
    """
    if not isinstance(config, dict):
        return config
    raw = config.get("mapping")
    if raw is None and not fallback:
        return config
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("mapping は dict です")

    mappings: dict[str, dict[str, Any]] = {}
    for label, values in (fallback or {}).items():
        if not isinstance(values, dict):
            continue  # 共通設定側の形の問題は共通設定自身の読み込みで報告される
        mappings[str(label)] = {str(key): value for key, value in values.items()}
    for label, values in raw.items():
        if values is None:
            values = {}  # 中身を全てコメントアウトした空セクションを許す
        if not isinstance(values, dict):
            raise ValueError(f"mapping {label!r} は dict です")
        merged = mappings.setdefault(str(label), {})
        merged.update({str(key): value for key, value in values.items()})

    def resolve(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if not isinstance(value, str):
            return value

        def replace(match: re.Match[str]) -> str:
            label, key = match.groups()
            if label not in mappings or key not in mappings[label]:
                raise ValueError(f"mapping lookup が見つかりません: {label} {key}")
            return str(mappings[label][key])

        return _LOOKUP_RE.sub(replace, value)

    return {
        key: value if key == "mapping" else resolve(value)
        for key, value in config.items()
    }


def find_default_config(cwd: Path) -> Path | None:
    """カレントディレクトリのみを探す（グローバル設定は使わない）。"""
    for name in DEFAULT_CONFIG_NAMES:
        candidate = cwd / name
        if candidate.is_file():
            return candidate
    return None


def load_config(cwd: Path) -> tuple[dict[str, Any], Path, bool]:
    """設定ファイルを読み込み (config, resolved_path, exists) を返す。
    workspace 直下、workspace/.agents、~/.agents の順に探す。
    ファイルが存在しない場合は空の config とデフォルトパスを返す（終了しない）。
    """
    workspace = Path(cwd).expanduser().resolve()
    agent_home = agent_home_dir()
    config_path = find_default_config(workspace)
    if config_path is None and workspace != Path.home().resolve():
        config_path = find_default_config(workspace / AGENT_HOME)
    if config_path is None:
        config_path = find_default_config(agent_home)
    if config_path is None:
        default_path = agent_home / "agent-loop.yaml"
        log.info(
            "~/.kiro の設定ファイルが見つかりません。必要に応じて %s に保存されます。",
            default_path,
        )
        return {}, default_path, False

    log.info("設定ファイルを読み込みます: %s", config_path)
    return _load_config_file(config_path), config_path, True


# ---------------------------------------------------------------------------
# tmux セッション名の生成
# ---------------------------------------------------------------------------

_TMUX_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def _tmux_safe_id(s: str, maxlen: int = 12, fallback: str = "id") -> str:
    return _TMUX_SAFE_RE.sub("", s)[:maxlen] or fallback


def _sanitize_session_label(name: str) -> str:
    """tmux セッション名に使用できる文字列に変換する。"""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", name).strip("-_")
    return (cleaned or "target")[:24]


def _webhook_key(name: str) -> str:
    """webhook のパス名とエントリ名を突き合わせるための URL-safe キー。"""
    return _WEBHOOK_NAME_RE.sub("-", name).strip("-_").lower()


def _tmux_session_name(base_path: Path, instance_id: str) -> str:
    """実行インスタンスごとに独立した tmux セッション名を生成する。"""
    resolved = str(base_path.resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:8]
    label = _sanitize_session_label(base_path.name)
    short_id = _tmux_safe_id(instance_id, fallback="run")
    return f"agent-loop-{label}-{digest}-{short_id}"


# ---------------------------------------------------------------------------
# JSONC (JSON with Comments) サポート
# ---------------------------------------------------------------------------

def _strip_jsonc_comments(text: str) -> str:
    """JSONC のコメント（// および /* */）を除去する。"""
    out: list[str] = []
    i = 0
    in_string = False
    escape = False

    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue

        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2 if i + 1 < len(text) else 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _load_jsonc_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        # VS Code settings.json は JSONC のため、コメントと trailing comma を許容する。
        stripped = _strip_jsonc_comments(text)
        stripped = re.sub(r",(?=\s*[}\]])", "", stripped)
        data = json.loads(stripped)
        return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# VS Code 設定からの定期プロンプト読み込み
# ---------------------------------------------------------------------------

def load_vscode_periodic_prompts(base_path: Path) -> list[dict[str, Any]]:
    """.vscode/settings.json の agentExecutor.periodicPrompts を読み込み、agent-loop 形式へ変換する。"""
    settings_path = base_path / ".vscode" / "settings.json"
    if not settings_path.is_file():
        return []

    try:
        data = _load_jsonc_file(settings_path)
    except Exception as exc:
        log.warning("%s の読み込みに失敗しました: %s", settings_path, exc)
        return []

    raw_entries = data.get("agentExecutor.periodicPrompts")
    if not isinstance(raw_entries, list):
        return []

    prompts: list[dict[str, Any]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("enabled", True) is False:
            continue

        agent_id = str(entry.get("agentId", "")).strip().lower()
        if agent_id not in ("kiro", "kiro-cli"):
            continue

        prompt = str(entry.get("prompt", "")).strip()
        if not prompt:
            continue

        try:
            interval = int(entry.get("intervalMinutes", 0))
        except Exception:
            continue
        if interval < 1:
            continue

        prompts.append(
            {
                "name": prompt[:40],
                "prompt": prompt,
                "interval_minutes": interval,
                "enabled": True,
            }
        )

    if prompts:
        log.info("VS Code 設定から periodicPrompts を %d 件読み込みました。", len(prompts))

    return prompts


# ---------------------------------------------------------------------------
# ワークスペース固有のプロンプト設定（.agents/agent-loop.yml）
# ---------------------------------------------------------------------------

def _prompt_file(base_path: str) -> Path:
    """起動ディレクトリ単位の定期プロンプト設定ファイルパスを返す。"""
    base = Path(base_path).expanduser()
    dirs = (AGENT_HOME,) if base.resolve() == Path.home().resolve() else (
        AGENT_HOME, AGENT_HOME_LEGACY)
    for dirname in dirs:
        for name in ("agent-loop.yaml", "agent-loop.yml"):
            candidate = base / dirname / name
            if candidate.is_file():
                return candidate
    return base / AGENT_HOME / "agent-loop.yml"


def _load_prompt_file_data(base_path: str) -> dict[str, Any]:
    """起動ディレクトリ配下 .agents/（旧 .agent/）から設定ファイルを探して読む。"""
    base = Path(base_path).expanduser()
    dirs = (AGENT_HOME,) if base.resolve() == Path.home().resolve() else (
        AGENT_HOME, AGENT_HOME_LEGACY)
    path: Path | None = None
    for dirname in dirs:
        for name in DEFAULT_CONFIG_NAMES:
            candidate = base / dirname / name
            if candidate.is_file():
                path = candidate
                break
        if path is not None:
            break

    if path is None:
        return {}

    if path.suffix.lower() in (".yaml", ".yml") and yaml is None:
        log.warning("PyYAML がないため %s を読めません。pip install pyyaml", path)
        return {}

    try:
        data = _load_config_file(path)
        if isinstance(data, dict):
            return data
        log.warning("%s の形式が不正なため空設定として扱います。", path)
    except Exception as exc:
        log.error("%s の読み込みに失敗しました: %s", path, exc)

    return {}


def load_prompt_config(base_path: str) -> list[dict[str, Any]]:
    """起動ディレクトリ配下 .agents/（旧 .agent/）から prompts を読む。"""
    data = _load_prompt_file_data(base_path)
    prompts = data.get("prompts", [])
    if isinstance(prompts, list):
        return [p for p in prompts if isinstance(p, dict)]
    if data:
        log.warning("%s/.agents/ の prompts が配列ではありません。", base_path)

    return []


# ---------------------------------------------------------------------------
# environment handoff（Phase 2C）
# ---------------------------------------------------------------------------

_TOKEN_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def normalize_environment_handoff(config: dict[str, Any]) -> dict[str, Any]:
    """environment_handoff 設定を正規化する。"""
    raw = config.get("environment_handoff")
    if raw is None:
        return {"prompt": False, "skill_home": None, "token_env_names": []}
    if not isinstance(raw, dict):
        return {"prompt": False, "skill_home": None, "token_env_names": []}

    prompt = bool(raw.get("prompt", False))
    skill_home = raw.get("skill_home")
    if skill_home is not None:
        skill_home = str(skill_home).strip() or None

    token_names: list[str] = []
    raw_tokens = raw.get("token_env_names")
    if isinstance(raw_tokens, list):
        for item in raw_tokens:
            name = str(item or "").strip()
            if not name:
                continue
            if _TOKEN_ENV_NAME_RE.fullmatch(name):
                token_names.append(name)
    return {
        "prompt": prompt,
        "skill_home": skill_home,
        "token_env_names": token_names,
    }


def detect_runtime_os() -> str:
    try:
        system = sys.platform
    except Exception:  # noqa: BLE001
        return "unknown"
    if system == "darwin":
        return "darwin"
    if system.startswith("linux"):
        return "linux"
    return "unknown"


def detect_runtime_shell() -> str:
    if sys.platform == "win32":
        return "powershell"
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/version", encoding="utf-8", errors="replace") as fh:
                if "microsoft" in fh.read().lower():
                    return "wsl"
        except OSError:
            pass
        return "posix"
    if sys.platform == "darwin":
        return "posix"
    return "posix"


def build_env_prompt_block(
    handoff: dict[str, Any],
    *,
    agent_cli: str,
    agent_home: Path,
) -> str:
    """[ENV]...[/ENV] ブロックを組み立てる（値は token 名の SET/UNSET のみ）。"""
    skill_home = handoff.get("skill_home")
    skill_value = "UNSET"
    if skill_home:
        resolved = Path(str(skill_home)).expanduser()
        if resolved.is_dir():
            skill_value = str(resolved.resolve())

    lines = [
        "[ENV]",
        f"os={detect_runtime_os()}",
        f"shell={detect_runtime_shell()}",
        f"agent_cli={agent_cli}",
        f"agent_home={agent_home.resolve()}",
        f"skill_home={skill_value}",
    ]
    for name in handoff.get("token_env_names") or []:
        token_name = str(name)
        if not _TOKEN_ENV_NAME_RE.fullmatch(token_name):
            continue
        state = "SET" if os.environ.get(token_name) else "UNSET"
        lines.append(f"token.{token_name}={state}")
    lines.append("[/ENV]")
    return "\n".join(lines)


def save_prompt_config(base_path: str, prompts: list[dict[str, Any]]) -> bool:
    """起動ディレクトリ配下の既存 YAML、または既定 .yml に prompts を保存する。"""
    path = _prompt_file(base_path)

    if yaml is None:
        log.error("PyYAML が必要です。`pip install pyyaml` を実行してください。")
        return False

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # prompts 以外の設定（kiro_options など）を保持する。
        data = _load_prompt_file_data(base_path)
        data["prompts"] = prompts
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
        log.info("定期プロンプト設定を保存しました: %s", path)
        return True
    except Exception as exc:
        log.error("定期プロンプト設定の保存に失敗しました: %s", exc)
        return False
