from __future__ import annotations
# cliprofile.py — エージェント CLI 差し替え（agents/<name>.json 契約）の実行プロファイル。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
#
# 設計: docs/designs/agent-loop-design.md「機能 5: エージェント CLI の差し替え」。
# 定義の解決・argv 組み立ては agentcore.agentcli（Python 側の唯一のローダ）へ委譲し、
# ここが持つのは agent-loop 固有の「待機状態の監視・判定」だけ。判定方法は CLI ごとに
# 違う（入力欄を出したまま処理する TUI では ready の消失が起きない）ため、定義の
# interactive.{ready_pattern, busy_pattern, idle_quiet_sec} を解釈して 1 つの判定器にする。

# ---------------------------------------------------------------------------
# agentcore.agentcli の解決（zipapp は同梱、リポジトリ実行は相対探索）
# ---------------------------------------------------------------------------


def _import_agentcli():
    """agentcore.agentcli を import する。見つからなければ None（legacy 経路のみ動作）。"""
    try:
        from agentcore import agentcli as _mod  # type: ignore
        return _mod
    except ImportError:
        pass
    # リポジトリから直接動かす開発環境: tools/agent-tools/agentcore を sys.path へ足す。
    try:
        here = Path(__file__).resolve()
    except NameError:
        return None
    for parent in here.parents:
        cand = parent / "agent-tools" / "agentcore"
        if (cand / "agentcore" / "agentcli.py").is_file():
            sys.path.insert(0, str(cand))
            try:
                from agentcore import agentcli as _mod  # type: ignore
                return _mod
            except ImportError:
                return None
    return None


# ---------------------------------------------------------------------------
# ERE（grep -E 方言）→ Python re 変換
# ---------------------------------------------------------------------------

# 定義の ready_pattern / busy_pattern は「grep -qiE にそのまま渡せる ERE」が契約
# （schemas/agent-cli.schema.json）。Python re は POSIX 文字クラスを解さないため、
# よく使うクラスだけ等価表現へ写像する。変換しきれない・壊れているパターンは
# WARNING を出して None（呼び出し側が組み込み既定へフォールバック）。
_POSIX_CLASS_MAP = {
    "[:space:]": r"\s",
    "[:blank:]": r" \t",
    "[:digit:]": "0-9",
    "[:alpha:]": "a-zA-Z",
    "[:alnum:]": "a-zA-Z0-9",
    "[:upper:]": "A-Z",
    "[:lower:]": "a-z",
}


def _compile_ere(pattern: str, *, label: str = "") -> "re.Pattern | None":
    """ERE 文字列を Python 正規表現へ変換してコンパイルする（大文字小文字無視・行単位）。"""
    text = str(pattern or "")
    if not text:
        return None
    for posix, py in _POSIX_CLASS_MAP.items():
        text = text.replace(posix, py)
    try:
        return re.compile(text, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        log.warning("CLI 定義の %s が正規表現として解釈できません（組み込み既定を使います）: %s", label or "pattern", exc)
        return None


# ---------------------------------------------------------------------------
# CliProfile — 待機判定と対話起動 argv
# ---------------------------------------------------------------------------


class CliProfile:
    """agent_cli 1 つぶんの実行プロファイル。

    判定の優先順位（agents/README.md「待機判定」）:
      busy_pattern マッチ → 処理中 ＞ ready_pattern マッチ → 待機 ＞
      idle_quiet_sec 静穏 → 待機 ＞ それ以外 → 処理中。
    legacy プロファイル（agent_cli 未指定）は従来の _PROMPT_RE 判定と同一。
    """

    _READY_TAIL_LINES = 3  # 末尾何行（空行除く）へ ready_pattern を当てるか（従来と同じ）

    def __init__(self, name: str = "kiro", spec: "dict | None" = None,
                 argv: "list[str] | None" = None, *, clock=None):
        self.name = str(name)
        self.spec = spec
        self.argv = list(argv) if argv else None
        self._clock = clock or time.monotonic
        inter = (spec or {}).get("interactive") or {}
        self._ready_re = _compile_ere(inter.get("ready_pattern", ""),
                                      label=f"{name}.ready_pattern") or _PROMPT_RE
        self._busy_re = _compile_ere(inter.get("busy_pattern", ""),
                                     label=f"{name}.busy_pattern")
        try:
            self.idle_quiet_sec = float(inter.get("idle_quiet_sec") or 0)
        except (TypeError, ValueError):
            self.idle_quiet_sec = 0.0
        # clear/save/exit: 未定義または空文字 = その操作をサポートしない（推測しない）
        if inter and "clear_command" in inter:
            raw_clear = inter.get("clear_command")
            self.clear_command = "" if raw_clear is None else str(raw_clear)
        elif inter:
            self.clear_command = "/clear"  # interactive あり・未指定は従来既定
        else:
            self.clear_command = "/clear"  # legacy
        raw_save = (inter or {}).get("save_command", "")
        self.save_command = "" if raw_save is None else str(raw_save)
        raw_exit = (inter or {}).get("exit_command", "")
        self.exit_command = "" if raw_exit is None else str(raw_exit)
        self.skill_command_prefix = str((spec or {}).get("skill_command_prefix") or "/")
        self.failure_pattern = str(inter.get("failure_pattern") or "") or None
        # 静穏判定用: pane_id → (直近内容のハッシュ, 最終変化時刻)
        self._quiet_state: dict[str, tuple[str, float]] = {}

    @property
    def is_legacy(self) -> bool:
        return self.spec is None

    # -- 判定 ---------------------------------------------------------------

    def _tail(self, content: str) -> str:
        lines = [line for line in str(content or "").splitlines() if line.strip()]
        return "\n".join(lines[-self._READY_TAIL_LINES:])

    def classify(self, content: str) -> str:
        """ペイン可視画面から 'busy' / 'idle' / 'unknown' を判定する（状態レス）。"""
        text = str(content or "")
        if not text.strip():
            return "unknown"
        if self._busy_re is not None and self._busy_re.search(text):
            return "busy"
        if self._ready_re.search(self._tail(text)):
            return "idle"
        return "unknown"

    def is_ready(self, content: str) -> bool:
        """入力受付（送信してよい状態）か。busy_pattern がマッチしていれば ready でも拒む。"""
        return self.classify(content) == "idle"

    def is_idle(self, pane_id: str, content: str) -> bool:
        """待機状態か（SlotMonitor 用・状態つき）。

        パターンで判定できないとき（unknown）、idle_quiet_sec > 0 なら
        「画面が idle_quiet_sec 秒間変化しない」ことを待機とみなす。
        """
        verdict = self.classify(content)
        now = self._clock()
        digest = hashlib.sha1(str(content or "").encode("utf-8", "replace")).hexdigest()
        prev = self._quiet_state.get(pane_id)
        if prev is None or prev[0] != digest:
            self._quiet_state[pane_id] = (digest, now)
        if verdict != "unknown":
            return verdict == "idle"
        if self.idle_quiet_sec > 0:
            _, last_change = self._quiet_state[pane_id]
            return (now - last_change) >= self.idle_quiet_sec
        return False

    def forget_pane(self, pane_id: str) -> None:
        self._quiet_state.pop(pane_id, None)

    # -- 送信テキストの作法 --------------------------------------------------

    def rewrite_slash(self, line: str) -> str:
        """行頭 `/` のスラッシュコマンドをこの CLI の起動記号へ差し替える（既定 `/` は素通し）。"""
        if self.skill_command_prefix == "/":
            return line
        global _AGENTCLI_MOD
        mod = _AGENTCLI_MOD
        if mod is None:
            mod = _import_agentcli()
            _AGENTCLI_MOD = mod
        if mod is not None:
            return mod.rewrite_skill_commands(line, self.skill_command_prefix)
        # ローダ不在でここに来るのは定義なし（legacy）だけのはずだが、安全側の素通し。
        return line


_AGENTCLI_MOD = None  # 遅延 import の結果キャッシュ（None = 未試行 or 失敗）


class CliProfileError(RuntimeError):
    """agent_cli 設定の解決失敗。デーモン起動では fail fast、補助コマンドでは WARNING。"""


def _resolve_cli_profile(config: "dict[str, Any]", project_dir: "str | Path | None" = None) -> "CliProfile | None":
    """設定の agent_cli からプロファイルを作る。未指定なら None（従来の kiro 組み込み経路）。

    未知・壊れた定義は CliProfileError（黙って kiro へ倒さない —
    docs/designs/agent-cli-plugin-design.md の明示エラー原則）。
    """
    global _AGENTCLI_MOD
    name = str(config.get("agent_cli", "") or "").strip()
    if not name:
        return None
    if _AGENTCLI_MOD is None:
        _AGENTCLI_MOD = _import_agentcli()
    if _AGENTCLI_MOD is None:
        raise CliProfileError(
            "agent_cli が指定されましたが agentcore（定義ローダ）を解決できません。"
            "install.sh の再実行を検討してください。")
    opts = config.get("agent_cli_options")
    if not isinstance(opts, dict):
        opts = {}
    try:
        spec = _AGENTCLI_MOD.load_cli(name, project_dir=project_dir)
        argv = _AGENTCLI_MOD.interactive_cmd(
            spec,
            str(opts.get("model") or "") or None,
            readonly=bool(opts.get("readonly", False)),
        )
    except _AGENTCLI_MOD.AgentCliError as exc:
        raise CliProfileError(str(exc)) from exc
    argv += [str(a) for a in (opts.get("extra_args") or [])]
    return CliProfile(name=name, spec=spec, argv=argv)


# 現在有効なプロファイル。既定は legacy（従来の kiro-cli 組み込み判定と同一挙動）。
_CLI_PROFILE = CliProfile()


def _set_cli_profile(profile: "CliProfile | None") -> None:
    global _CLI_PROFILE
    _CLI_PROFILE = profile if profile is not None else CliProfile()


def _init_cli_profile_from_config(config: "dict[str, Any]", project_dir=None, *, strict: bool) -> "CliProfile | None":
    """設定からプロファイルを解決してグローバルへ据える。

    strict=True（デーモン起動）は失敗を CliProfileError のまま上げる。
    strict=False（send 等の補助コマンド）は WARNING を出して legacy 判定で続行する
    （cowork の定常業務フォールバックと同じ「黙らないフォールバック」）。
    """
    try:
        profile = _resolve_cli_profile(config, project_dir=project_dir)
    except CliProfileError as exc:
        if strict:
            raise
        log.warning("agent_cli の解決に失敗したため従来の待機判定で続行します: %s", exc)
        return None
    _set_cli_profile(profile)
    return profile
