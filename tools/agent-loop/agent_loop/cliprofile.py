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

    mode は「この CLI をどう起こすか」。interactive 節を持つ定義は 'interactive'
    （tmux ペインに常駐させて send-keys で送る従来経路）、持たない定義は 'headless'
    （実行のたびに subprocess を起こす）。headless では待機判定も clear/save/exit も
    使わない——判定する相手のペインが無い。

    autonomy は「headless で 1 回起動したとき自分でツールを回して完遂できるか」
    （定義の headless_autonomy）。'tool-loop' は 1 回呼べば終わる。'single-shot' は
    呼び出し側がツールループを供給しないと着手しない（aider・素の ollama）。
    """

    _READY_TAIL_LINES = 3  # 未指定時の互換値

    def __init__(self, name: str = "kiro", spec: "dict | None" = None,
                 argv: "list[str] | None" = None, *, clock=None):
        self.name = str(name)
        self.spec = spec
        self.argv = list(argv) if argv else None
        self._clock = clock or time.monotonic
        # legacy（定義なし）は従来の kiro 対話経路。定義ありは interactive 節の有無で決まる。
        self.mode = "interactive" if (spec is None or spec.get("interactive")) else "headless"
        self.autonomy = str((spec or {}).get("headless_autonomy") or "tool-loop")
        # 解決済みモデル（headless 実行の argv 組み立てと launch fingerprint で使う）
        self.model: "str | None" = None
        inter = (spec or {}).get("interactive") or {}
        try:
            self.ready_tail_lines = max(
                1, int(inter.get("ready_tail_lines") or self._READY_TAIL_LINES)
            )
        except (TypeError, ValueError):
            self.ready_tail_lines = self._READY_TAIL_LINES
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
        self.turn_completion = (
            "kiro" if spec is None else str(inter.get("turn_completion") or "")
        )
        # 静穏判定用: pane_id → (直近内容のハッシュ, 最終変化時刻)
        self._quiet_state: dict[str, tuple[str, float]] = {}

    @property
    def is_legacy(self) -> bool:
        return self.spec is None

    @property
    def is_headless(self) -> bool:
        """対話ペインを持たない（実行のたびに subprocess を起こす）プロファイルか。"""
        return self.mode == "headless"

    @property
    def needs_tool_loop(self) -> bool:
        """呼び出し側がツールループを供給しないと着手しない CLI か。"""
        return self.autonomy == "single-shot"

    # -- 判定 ---------------------------------------------------------------

    def _tail(self, content: str) -> str:
        lines = [line for line in str(content or "").splitlines() if line.strip()]
        return "\n".join(lines[-self.ready_tail_lines:])

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

    @property
    def freeze_timeout(self) -> float:
        """このペインを「止まった」とみなすまでの無進捗秒数（設計 2026-08-27 §7.4-3）。

        ヘッドレスは**同じ意味の上限を既定で持っている**——定義の `timeout`、無ければ
        共通 fallback（`toolloop._TL_DEFAULT_AGENT_TIMEOUT_SEC` = 600 秒）。ペインだけが
        既定 off で、`slot_timeout_seconds`（既定 7200）まで居座っていた。**同じ性質の
        上限なので同じ源から採る**——ここで別の数字を置くと、どちらの上限で切られたのかを
        読む側が追えなくなる。

        壁時計ではなく無進捗である点も同じ。画面の hash が変わっていれば進んでいる。
        """
        declared = (self.spec or {}).get("timeout")
        try:
            value = float(declared or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
        return float(_harness_toolloop._TL_DEFAULT_AGENT_TIMEOUT_SEC)

    def classify_failure(self, content: str, *, now=None) -> "dict | None":
        """ペインの画面を定義の `errors[]` で分類する（設計 2026-08-27 §7.4-1）。

        **ヘッドレスと同じ 1 実装**（`agentcli.classify_error`）を引く。以前この経路には
        分類が無く、効くのは `interactive.failure_pattern`（しかも `send --wait` だけ）
        だった——定義が `errors[]` に quota や auth を宣言していても、ペインで起きた分は
        誰も読まなかった。legacy（定義なし）は `errors[]` を持たないので None。
        """
        if self.spec is None:
            return None
        global _AGENTCLI_MOD
        mod = _AGENTCLI_MOD
        if mod is None:
            mod = _import_agentcli()
            _AGENTCLI_MOD = mod
        if mod is None:
            return None
        try:
            return mod.classify_error(self.spec, content, detailed=True,
                                      now=time.time() if now is None else now)
        except Exception:
            return None      # 分類できないことを理由に実行を止めない

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
    except _AGENTCLI_MOD.AgentCliError as exc:
        raise CliProfileError(str(exc)) from exc
    # interactive 節を持たない定義（aider 等）は対話 argv を組めない。かつては
    # ここで fail fast していたが、対話セッションは**会話を人に見せるための可視化**で
    # あって実行の必須要件ではない——headless プロファイルとして通し、実行のたびに
    # subprocess を起こす経路（scheduler の headless 枝）へ回す。
    model = str(opts.get("model") or "") or None
    if not spec.get("interactive"):
        profile = CliProfile(name=name, spec=spec, argv=None)
        profile.model = model
        return profile
    try:
        argv = _AGENTCLI_MOD.interactive_cmd(
            spec, model, readonly=bool(opts.get("readonly", False)),
        )
    except _AGENTCLI_MOD.AgentCliError as exc:
        raise CliProfileError(str(exc)) from exc
    argv += [str(a) for a in (opts.get("extra_args") or [])]
    profile = CliProfile(name=name, spec=spec, argv=argv)
    profile.model = model
    return profile


def resolve_entry_profile(config: "dict[str, Any]", entry: "dict[str, Any] | None",
                          project_dir=None) -> "tuple[CliProfile | None, str]":
    """entry 1 件ぶんのプロファイルと実行経路を解決する。

    CLI / モデルの解決順（設計書「entry ごとのエージェントとモデル」）:
      1. control.json の workloads.routine（degraded 差し替えを含む）——config へ
         `_apply_control_agent` 済みの値として渡ってくる
      2. entry の agent_cli / model
      3. entry 共通設定（agent-loop.yaml トップレベル）
      4. 既定

    返す経路は 'interactive' か 'per-run'。entry が `session: keep`（既定）でも、
    対話ペインを持たない CLI では 'per-run' へ倒す——対話できない CLI に
    「セッションを保つ」と書いてあっても保てない。倒したことは呼び出し側が警告する。
    """
    e = entry or {}
    merged = dict(config or {})
    # control.json が明示していれば config の agent_cli が既に上書きされている。
    # 管理面の宣言を entry で上書きしない（予算枯渇時の degrade を素通しさせない）。
    if not _control_declared_agent() and e.get("agent_cli"):
        merged["agent_cli"] = str(e["agent_cli"])
        merged.pop("agent_cli_options", None)   # CLI が変われば共通のモデルは引き継がない
    if not _control_declared_model() and e.get("model"):
        key = "agent_cli_options" if merged.get("agent_cli") else "kiro_options"
        merged[key] = {**dict(merged.get(key) or {}), "model": str(e["model"])}
    profile = _resolve_cli_profile(merged, project_dir=project_dir)
    if profile is not None and profile.is_headless:
        return profile, "per-run"
    # ステートマシンは**実行形**であって経路ではない。対話ペインを持つ CLI では、
    # コマンド面の 1 行（一族は `/sm`、クラウド CLI はスキル発動文）をペインへ送る
    # ——受けた側がハーネスなりスキルなりで回す。state ごとにヘッドレス起動すると、
    # CLI の起動・文脈の再構築・システムプロンプトの再送が state の数だけ掛かる
    # （クラウド CLI ではトークン消費が跳ねる）。ペインを持たない CLI は上の
    # `is_headless` 分岐で既に per-run へ落ちている。
    if str(e.get("session") or "keep") == "per-run":
        return profile, "per-run"
    return profile, "interactive"


def check_headless_entries(config: "dict[str, Any]", entries: "list[dict[str, Any]]",
                           project_dir=None) -> "list[str]":
    """headless 経路になる entry を起動時に点検し、致命的な問題の一覧を返す。

    致命（返り値に載る＝呼び出し側が起動を止める）:
      - headless で扱えない機能を使っている（ralph 多段 / external target）

    警告（ログのみ・起動は続ける）:
      - ツールループ非内蔵の CLI（層3）なのに受入条件が無い。この CLI は自分で
        ツールを回さないので、受入条件が無いと done を機械検証できない。移行のため
        起動は止めず、実行結果は「検証なし」として記録する。
      - 対話ペインを持たない CLI に `session: keep` が書いてある（保てないので per-run）。
    """
    fatal: list[str] = []
    for entry in entries or []:
        name = str(entry.get("name") or entry.get("id") or "")
        try:
            profile, route = resolve_entry_profile(config, entry, project_dir=project_dir)
        except CliProfileError as exc:
            fatal.append(f"定期プロンプト「{name}」のエージェントを解決できません: {exc}")
            continue
        if entry.get("statemachine"):
            # 宣言したワークフローが実在するかは、最初の LLM 実行より前に知りたい
            # （無ければ dispatch のたびに 1 回ぶん起こして落ちる）。**経路によらず見る**
            # ——ペインへ `/sm` を送る形でも、当て先が無ければ同じところで落ちる。
            base = Path(entry.get("cwd") or project_dir or Path.cwd()).expanduser()
            workflow = base / str(entry.get("statemachine"))
            if not workflow.is_file():
                fatal.append(f"定期プロンプト「{name}」: ステートマシン定義が見つかりません: "
                             f"{workflow}")
            continue
        if route != "per-run":
            # 対話ペイン経路。受入条件の機械層はターン境界で回る（設計 2026-08-27 §7.3 B）
            # が、判定層（judge）はエージェントの**報告本文**を要るのでまだ走らない。
            # 黙って無視すると「判定させたつもり」が残るので、起動時に言う。
            if entry.get("acceptance_judge") and (entry.get("acceptance") or []):
                log.warning(
                    "定期プロンプト「%s」: 対話ペイン経路では受入条件の判定層（acceptance_judge）は"
                    "まだ走りません。機械層（ファイル指紋の照合）だけで検証し、`verifiedBy` には"
                    "judge が出ません。", name)
            continue
        if profile is None:
            continue
        if str(entry.get("mode") or "normal") == "ralph":
            fatal.append(f"定期プロンプト「{name}」: mode=ralph は headless 実行に対応していません"
                         f"（agent_cli: {profile.name}）。")
        # slash はスキルとして headless 実行へ渡る（toolloop.run_prompt）。層3 では
        # dispatch 時に解決失敗で落ちるので、設定ミスは起動時に fail fast で知らせる。
        if profile.needs_tool_loop:
            for line in entry.get("slash") or []:
                skill_name = str(line).split()[0] if str(line).strip() else ""
                if skill_name and _harness_toolloop._tl_resolve_skill(
                        skill_name, str(project_dir or Path.cwd())) is None:
                    dirs = ", ".join(_harness_toolloop._tl_skill_search_dirs(
                        str(project_dir or Path.cwd())))
                    fatal.append(
                        f"定期プロンプト「{name}」: スキルが見つかりません: {skill_name}"
                        f"（探索先: {dirs}。配布は `python install.py --agent aider"
                        " --all-skills` 等で行います）。")
        if entry.get("target"):
            fatal.append(f"定期プロンプト「{name}」: external target は headless 実行に"
                         f"対応していません（agent_cli: {profile.name}）。")
        if profile.is_headless and str(entry.get("session") or "keep") == "keep":
            log.warning("定期プロンプト「%s」: %s は対話ペインを持たないため、"
                        "session: keep を per-run として扱います。", name, profile.name)
        if profile.needs_tool_loop and not (entry.get("acceptance") or []):
            log.warning(
                "定期プロンプト「%s」: %s は自分でツールを回さないため、受入条件（acceptance）が"
                "無いと done を検証できません。実行はしますが結果は「検証なし」として記録します"
                "（agent-dashboard の補完で受入条件を作れます）。", name, profile.name)
    return fatal


def _control_declared_agent() -> bool:
    cli, _ = _control_override()
    return bool(cli)


def _control_declared_model() -> bool:
    _, model = _control_override()
    return bool(model)


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
