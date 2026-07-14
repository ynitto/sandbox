from __future__ import annotations
# config.py — 元 agent-loop.py の 677-940 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# 設定ロード
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_NAMES = ["agent-loop.yaml", "agent-loop.yml", "agent-loop.json"]


def find_default_config(cwd: Path) -> Path | None:
    """カレントディレクトリのみを探す（グローバル設定は使わない）。"""
    for name in DEFAULT_CONFIG_NAMES:
        candidate = cwd / name
        if candidate.is_file():
            return candidate
    return None


def load_config(cwd: Path) -> tuple[dict[str, Any], Path, bool]:
    """設定ファイルを読み込み (config, resolved_path, exists) を返す。
    ~/.agent/ 配下の DEFAULT_CONFIG_NAMES を探す。
    ファイルが存在しない場合は空の config とデフォルトパスを返す（終了しない）。
    """
    agent_home = Path.home() / ".agent"
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
        stripped = re.sub(r"(\s*[}\]]),", r"\1", stripped)
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
# ワークスペース固有のプロンプト設定（.agent/agent-loop.yml）
# ---------------------------------------------------------------------------

def _prompt_file(base_path: str) -> Path:
    """起動ディレクトリ単位の定期プロンプト設定ファイルパスを返す。"""
    return Path(base_path) / ".agent" / "agent-loop.yml"


def _load_prompt_file_data(base_path: str) -> dict[str, Any]:
    """起動ディレクトリ配下 .agent/ から設定ファイル（DEFAULT_CONFIG_NAMES）を探して読む。"""
    agent_dir = Path(base_path) / ".agent"
    path: Path | None = None
    for name in DEFAULT_CONFIG_NAMES:
        candidate = agent_dir / name
        if candidate.is_file():
            path = candidate
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
    """起動ディレクトリ配下 .agent/ から prompts を読む。"""
    data = _load_prompt_file_data(base_path)
    prompts = data.get("prompts", [])
    if isinstance(prompts, list):
        return [p for p in prompts if isinstance(p, dict)]
    if data:
        log.warning("%s/.agent/ の prompts が配列ではありません。", base_path)

    return []


def save_prompt_config(base_path: str, prompts: list[dict[str, Any]]) -> bool:
    """起動ディレクトリ配下 .agent/agent-loop.yml に prompts を保存する。"""
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


