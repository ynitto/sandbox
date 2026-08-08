from __future__ import annotations
# session.py — 元 agent-loop.py の 996-1473 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# セッション管理
# ---------------------------------------------------------------------------

class SessionManager:
    """カレントディレクトリ上で、プロンプトごとの kiro-cli ペインを直接管理する。"""

    _layout_lock = threading.Lock()  # 全インスタンスで共有するレイアウトロック

    def __init__(
        self,
        target_path: str,
        instance_id: str,
        kiro_args_base: list[str],
        split_direction: str,
        startup_timeout: int,
        uses_concurrency_agent: bool = False,
    ):
        resolved = Path(target_path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"パスが存在しないかディレクトリではありません: {resolved}")

        self._target_path = str(resolved)
        self._target_name = resolved.name or "default"
        self._instance_id = _tmux_safe_id(instance_id, fallback="run")
        self._kiro_args_base = kiro_args_base[:]
        self._split_direction = "vertical" if str(split_direction).lower() == "vertical" else "horizontal"
        self._startup_timeout = startup_timeout
        self._uses_concurrency_agent = uses_concurrency_agent

        # prompt_id → pane_id (str)
        self._panes: dict[str, str] = {}
        self._prompt_names: dict[str, str] = {}
        self._tmux_names: dict[str, str] = {}
        self._prompt_cwds: dict[str, str | None] = {}
        self._owners: dict[str, str] = {}
        self._restart_locks: dict[str, threading.Lock] = {}
        # Phase 2A: ownership / generation / effective_model / launch fingerprint
        self._ownership: dict[str, str] = {}  # managed-persistent | managed-ephemeral
        self._generation: dict[str, int] = {}
        self._effective_model: dict[str, str | None] = {}
        self._launch_fingerprint: dict[str, str] = {}
        # グローバル指示: ペインごとに「最後に注入した instructions.revision」を覚え、
        # revision が変わったときだけ次の送信に前置する（長寿命チャットの文脈を汚さない）。
        self._instr_rev: dict[str, int] = {}
        self._state_extras: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._user_home = str(Path.home().resolve())
        self._environment_handoff: dict[str, Any] = {
            "prompt": False,
            "skill_home": None,
            "token_env_names": [],
        }

        self._tmux_bin: str | None = None
        self._layout_window_target: str | None = None
        self._layout_controller_pane: str | None = None
        self._active_session_name: str | None = None
        self._tmux_session_name = _tmux_session_name(resolved, self._instance_id)

    # ------------------------------------------------------------------
    # tmux ヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def _session_from_window_target(window_target: str) -> str:
        if ":" in window_target:
            return window_target.split(":", 1)[0]
        return window_target

    def _split_option(self) -> str:
        return "-v" if self._split_direction == "vertical" else "-h"

    def _layout_name(self) -> str:
        return "even-vertical" if self._split_direction == "vertical" else "even-horizontal"

    def _split_label(self) -> str:
        return "縦" if self._split_direction == "vertical" else "横"

    def _run_tmux(self, args: list[str], capture_output: bool = True) -> subprocess.CompletedProcess[str]:
        return _tmux_cmd(*args, capture=capture_output)

    def _has_session(self, session_name: str) -> bool:
        return _tmux_cmd("has-session", "-t", session_name).returncode == 0

    def _pane_exists(self, pane_target: str) -> bool:
        return _tmux_cmd(
            "display-message", "-p", "-t", pane_target, "#{pane_id}"
        ).returncode == 0

    def _window_target_from_pane(self, pane_target: str) -> str:
        return _tmux_cmd_or_raise(
            "display-message", "-p", "-t", pane_target, "#{session_name}:#{window_index}",
            error_label="tmux ウィンドウ取得",
        )

    def _get_first_window_target(self, session_name: str) -> str:
        raw = _tmux_cmd_or_raise(
            "list-windows", "-t", session_name, "-F", "#{session_name}:#{window_index}",
            error_label="tmux ウィンドウ一覧取得",
        )
        for line in raw.splitlines():
            if target := line.strip():
                return target
        raise RuntimeError("tmux ウィンドウ一覧取得に失敗しました: ウィンドウが見つかりません")

    def _get_first_pane_target(self, window_target: str) -> str:
        raw = _tmux_cmd_or_raise(
            "list-panes", "-t", window_target, "-F", "#{pane_id}",
            error_label="tmux ペイン一覧取得",
        )
        for line in raw.splitlines():
            if target := line.strip():
                return target
        raise RuntimeError("tmux ペイン一覧取得に失敗しました: ペインが見つかりません")

    def _ensure_layout(self) -> None:
        with self.__class__._layout_lock:
            window_target = self._layout_window_target
            controller_pane = self._layout_controller_pane
            if window_target is not None and controller_pane is not None and self._pane_exists(controller_pane):
                return

            pane_target = os.environ.get("TMUX_PANE")
            if pane_target:
                result = _tmux_cmd(
                    "display-message", "-p", "-t", pane_target, "#{session_name}:#{window_index}"
                )
                if result.returncode == 0:
                    window_target = (result.stdout or "").strip()
                    if window_target:
                        self._layout_window_target = window_target
                        self._layout_controller_pane = pane_target
                        self._active_session_name = self._session_from_window_target(window_target)
                        log.info("現在の tmux ウィンドウを %s分割に使用します: %s", self._split_label(), window_target)
                        return

            session_name = self._tmux_session_name
            if not self._has_session(session_name):
                result = _tmux_cmd("new-session", "-d", "-s", session_name)
                if result.returncode != 0:
                    err = (result.stderr or "").strip()
                    raise RuntimeError(f"tmux セッション作成に失敗しました: {err}")
                log.info("tmux セッション '%s' を作成しました。", session_name)

            window_target = self._get_first_window_target(session_name)
            controller_pane = self._get_first_pane_target(window_target)
            self._active_session_name = session_name
            log.info("分割表示するには別端末で `tmux attach -t %s` を実行してください。", session_name)

            self._layout_window_target = window_target
            self._layout_controller_pane = controller_pane

    def _create_worker_pane(self, cmd: str, cwd: str) -> str:
        """kiro-cli を実行する新しいペインを作成してペインターゲットを返す。"""
        self._ensure_layout()

        with self.__class__._layout_lock:
            window_target = self._layout_window_target
            controller_pane = self._layout_controller_pane
            if window_target is None:
                raise RuntimeError("tmux レイアウトが初期化されていません。")

            split_target = controller_pane or window_target
            pane_target = _tmux_cmd_or_raise(
                "split-window",
                self._split_option(),
                "-d", "-P", "-F", "#{pane_id}",
                "-t", split_target,
                "-c", cwd,
                cmd,
                error_label="tmux ペイン分割",
            )

            _tmux_cmd("set-option", "-p", "-t", pane_target, "remain-on-exit", "on", capture=False)
            _tmux_cmd("select-layout", "-t", window_target, self._layout_name(), capture=False)

            if controller_pane and self._pane_exists(controller_pane):
                _tmux_cmd("select-pane", "-t", controller_pane, capture=False)
                _tmux_cmd("refresh-client", "-S", capture=False)

            return pane_target

    # ------------------------------------------------------------------
    # セッション識別ヘルパー
    # ------------------------------------------------------------------

    def _prompt_token(self, prompt_id: str) -> str:
        return _tmux_safe_id(prompt_id, fallback="prompt")

    def _tmux_name_for_prompt(self, prompt_id: str) -> str:
        composed = f"{self._instance_id}-{self._prompt_token(prompt_id)}"
        return _tmux_session_name(Path(self._target_path), composed)

    def get_attach_session_name(self) -> str:
        """アタッチセッション名を返す。"""
        return self._active_session_name or self._tmux_session_name

    def get_target_name(self) -> str:
        return self._target_name

    def get_target_path(self) -> str:
        return self._target_path

    def get_user_home(self) -> str:
        return self._user_home

    def configure_environment_handoff(
        self,
        handoff: dict[str, Any],
        *,
        user_home: "Path | str | None" = None,
    ) -> None:
        self._environment_handoff = dict(handoff or {})
        if user_home is not None:
            self._user_home = str(Path(user_home).expanduser().resolve())

    def _build_launch_env(self) -> dict[str, str]:
        env: dict[str, str] = {
            "HOME": self._user_home,
            "AGENT_HOME": str(agent_home_dir().resolve()),
        }
        spec = _CLI_PROFILE.spec or {}
        agent_env = spec.get("env")
        if isinstance(agent_env, dict):
            for key, value in agent_env.items():
                name = str(key)
                if not name or name == "PATH":
                    continue
                env[name] = str(value)
        return env

    def _format_launch_command(self, argv: list[str], launch_env: dict[str, str]) -> str:
        env_prefix = "env " + " ".join(
            f"{shlex.quote(key)}={shlex.quote(value)}"
            for key, value in sorted(launch_env.items())
        )
        cmd = " ".join(shlex.quote(arg) for arg in argv)
        return f"{env_prefix} {cmd}"

    # ------------------------------------------------------------------
    # ペイン起動 / 停止
    # ------------------------------------------------------------------

    def _resolve_cwd(self, cwd: str | None) -> str:
        if cwd:
            candidate = Path(cwd).expanduser().resolve()
            if candidate.is_dir():
                return str(candidate)
            log.warning("エントリの cwd '%s' が存在しないため target_path を使用します。", cwd)
        return self._target_path

    def _start_pane(
        self,
        prompt_id: str,
        prompt_name: str,
        cwd: str | None = None,
        owner: str = "scheduled",
        launch_spec: dict[str, Any] | None = None,
    ) -> bool:
        """新しいエージェント CLI ペインを起動して管理下に登録する。"""
        if shutil.which("tmux") is None:
            raise RuntimeError("tmux が PATH に見つかりません。`sudo apt install tmux` を実行してください。")

        spec = dict(launch_spec) if launch_spec else None
        profile_name = _CLI_PROFILE.name
        effective_model: str | None = None
        ownership = "managed-persistent"

        if spec is not None:
            full_argv = list(spec.get("argv") or [])
            if not full_argv:
                raise RuntimeError("launch_spec.argv が空です")
            cli_bin = shutil.which(full_argv[0])
            if cli_bin is None:
                raise RuntimeError(
                    f"エージェント CLI '{full_argv[0]}' が PATH に見つかりません。")
            full_argv[0] = cli_bin
            session_cwd = self._resolve_cwd(str(spec.get("cwd") or cwd or "") or None)
            profile_name = str(spec.get("profile_name") or profile_name)
            effective_model = spec.get("effective_model")
            ownership = str(spec.get("ownership") or ownership)
            launch_env = self._build_launch_env()
            for k, v in dict(spec.get("env") or {}).items():
                if k and k != "PATH":
                    launch_env[str(k)] = str(v)
        else:
            # agent_cli 指定時は定義から組み立てた argv、未指定は従来の kiro-cli。
            if not _CLI_PROFILE.is_legacy and _CLI_PROFILE.argv:
                full_argv = list(_CLI_PROFILE.argv)
                cli_bin = shutil.which(full_argv[0])
                if cli_bin is None:
                    raise RuntimeError(
                        f"エージェント CLI '{full_argv[0]}'（agent_cli: {_CLI_PROFILE.name}）が"
                        " PATH に見つかりません。インストールしてください。")
                full_argv[0] = cli_bin
            else:
                kiro_bin = shutil.which("kiro-cli")
                if kiro_bin is None:
                    raise RuntimeError("kiro-cli が PATH に見つかりません。インストールしてください。")
                cmd_args = ["chat"] + self._kiro_args_base[:]
                if self._uses_concurrency_agent:
                    agent_file = Path.home() / ".kiro" / "agents" / f"{CONCURRENCY_AGENT_NAME}.json"
                    if agent_file.is_file():
                        cmd_args += ["--agent", CONCURRENCY_AGENT_NAME]
                full_argv = [kiro_bin, *cmd_args]
            session_cwd = self._resolve_cwd(cwd)
            launch_env = self._build_launch_env()

        # セッション開始コマンド（agent-session-commands）の process モード。ペインを作る
        # **前**に走らせる。前準備が終わっていない環境でエージェントを動かさないため、
        # on_error='fail' が失敗したらここで諦める（ペインを作らない）。
        if not run_session_commands(self._session_command_context(session_cwd), modes=("process",)):
            log.error("プロンプト '%s' はセッション開始コマンドの失敗により起動しません。", prompt_name)
            return False

        cmd = self._format_launch_command(full_argv, launch_env)

        try:
            pane_target = self._create_worker_pane(cmd, session_cwd)
        except RuntimeError as exc:
            log.error("プロンプト '%s' のペイン起動に失敗しました: %s", prompt_name, exc)
            return False

        attach_session_name = self.get_attach_session_name()
        fp = launch_fingerprint(profile_name, full_argv, session_cwd)

        with self._lock:
            self._panes[prompt_id] = pane_target
            self._prompt_names[prompt_id] = prompt_name
            self._tmux_names[prompt_id] = attach_session_name
            self._prompt_cwds[prompt_id] = cwd if cwd is not None else session_cwd
            self._owners[prompt_id] = owner
            self._ownership[prompt_id] = ownership
            self._generation[prompt_id] = int(self._generation.get(prompt_id, 0)) + 1
            self._effective_model[prompt_id] = effective_model
            self._launch_fingerprint[prompt_id] = fp
            if prompt_id not in self._restart_locks:
                self._restart_locks[prompt_id] = threading.Lock()

        log.info(
            "プロンプト '%s' 用ペインを起動しました (pane=%s, tmux=%s, ownership=%s, gen=%s)。",
            prompt_name, pane_target, attach_session_name, ownership,
            self._generation.get(prompt_id),
        )
        # chat モードは、CLI が入力を受け付ける状態になってから業務プロンプトより先に送る。
        self._send_session_chat_commands(pane_target, session_cwd)
        self.write_state()
        return True

    def _session_command_context(self, session_cwd: str) -> dict:
        """セッション開始コマンドの when 判定・プレースホルダ展開に渡す文脈。"""
        return {
            "engine": "agent-loop",
            "workload": "routine",
            "cwd": session_cwd,
            "workspace": self._target_path,
            "agent_cli": _CLI_PROFILE.name,
        }

    def _send_session_chat_commands(self, pane_target: str, session_cwd: str) -> None:
        """chat モードのセッション開始コマンドをペインへ送る（失敗しても起動は続ける）。"""
        try:
            deadline = time.time() + self._startup_timeout
            while time.time() < deadline:
                if _pane_has_prompt(_capture_pane(pane_target)):
                    break
                time.sleep(0.5)
            else:
                log.warning("ペイン %s の起動待ちがタイムアウトしたため chat コマンドは送りません。", pane_target)
                return
            run_session_commands(
                self._session_command_context(session_cwd),
                # 人が `/skill-name` と書いた chat コマンドを、その CLI のスキル起動記号へ
                # 差し替えてから送る（codex は `$skill-name`。既定 `/` の CLI は素通し）。
                send_chat=lambda text: _send_to_pane(pane_target, _CLI_PROFILE.rewrite_slash(text)),
                modes=("chat",),
            )
        except Exception:  # noqa: BLE001 — 開始コマンドの送信失敗でペイン起動を無効にしない
            log.warning("セッション開始コマンド（送信）に失敗しました。", exc_info=True)

    def _stop_pane(self, prompt_id: str) -> None:
        """ペインを終了する（_restart_locks は保持する）。"""
        with self._lock:
            pane_target = self._panes.pop(prompt_id, None)
            self._prompt_names.pop(prompt_id, None)
            self._tmux_names.pop(prompt_id, None)
            self._prompt_cwds.pop(prompt_id, None)
            self._owners.pop(prompt_id, None)
            self._ownership.pop(prompt_id, None)
            self._effective_model.pop(prompt_id, None)
            self._launch_fingerprint.pop(prompt_id, None)
            # generation は残して古い monitor callback が誤解放しないよう照合可能にする
            self._instr_rev.pop(prompt_id, None)

        if pane_target is not None and self._pane_exists(pane_target):
            log.info("kiro-cli ペインを終了します (pane=%s)。", pane_target)
            _tmux_cmd("send-keys", "-t", pane_target, "C-c", capture=False)
            time.sleep(0.2)
            try:
                window_target = self._window_target_from_pane(pane_target)
                _tmux_cmd("kill-pane", "-t", pane_target, capture=False)
                _tmux_cmd("select-layout", "-t", window_target, self._layout_name(), capture=False)
            except RuntimeError:
                _tmux_cmd("kill-pane", "-t", pane_target, capture=False)

    # ------------------------------------------------------------------
    # 公開インタフェース
    # ------------------------------------------------------------------

    def ensure_session(
        self,
        prompt_id: str,
        prompt_name: str,
        owner: str = "scheduled",
        cwd: str | None = None,
        launch_spec: dict[str, Any] | None = None,
    ) -> bool:
        """セッションが存在しない場合は起動する。成功時 True を返す。

        external pane は登録しない（呼び出し側で SessionManager を使わない）。
        """
        with self._lock:
            existing = self._panes.get(prompt_id)
            existing_cwd = self._prompt_cwds.get(prompt_id)
            existing_owner = self._owners.get(prompt_id, "scheduled")
            existing_model = self._effective_model.get(prompt_id)
        if existing is not None:
            if existing_owner != owner:
                return False
            requested_cwd = str((launch_spec or {}).get("cwd") or cwd or "")
            if requested_cwd and self._resolve_cwd(requested_cwd) != self._resolve_cwd(existing_cwd):
                return False
            if launch_spec is not None:
                want_model = launch_spec.get("effective_model")
                if existing_model != want_model:
                    return False  # model_mismatch — 呼び出し側で失敗扱いにする
            return True
        return self._start_pane(prompt_id, prompt_name, cwd, owner, launch_spec=launch_spec)

    def get_generation(self, prompt_id: str) -> int:
        with self._lock:
            return int(self._generation.get(prompt_id, 0))

    def get_effective_model(self, prompt_id: str) -> str | None:
        with self._lock:
            return self._effective_model.get(prompt_id)

    def get_ownership(self, prompt_id: str) -> str | None:
        with self._lock:
            return self._ownership.get(prompt_id)

    def get_pane_id(self, prompt_id: str) -> str | None:
        """prompt_id に対応するペイン ID を返す（なければ None）。"""
        with self._lock:
            return self._panes.get(prompt_id)

    def remove_session(
        self,
        prompt_id: str,
        *,
        owner: str | None = None,
        pane_id: str | None = None,
    ) -> bool:
        """所有者と pane が一致する session だけを管理対象から外して終了する。"""
        with self._lock:
            if owner is not None and self._owners.get(prompt_id) != owner:
                return False
            if pane_id is not None and self._panes.get(prompt_id) != pane_id:
                return False
            restart_lock = self._restart_locks.setdefault(prompt_id, threading.Lock())
        with restart_lock:
            with self._lock:
                if owner is not None and self._owners.get(prompt_id) != owner:
                    return False
                if pane_id is not None and self._panes.get(prompt_id) != pane_id:
                    return False
            self._stop_pane(prompt_id)
        with self._lock:
            if self._restart_locks.get(prompt_id) is restart_lock:
                self._restart_locks.pop(prompt_id, None)
        self.write_state()
        return True

    def send_prompt(self, prompt_id: str, prompt_text: str) -> bool:
        """tmux ペインにプロンプトを送信する（応答待ちはしない）。"""
        with self._lock:
            pane_target = self._panes.get(prompt_id)
            cwd = self._prompt_cwds.get(prompt_id, self._target_path) or self._target_path

        if pane_target is None or not self._pane_exists(pane_target):
            log.warning("kiro-cli ペインが存在しません (prompt_id=%s)。", prompt_id)
            return False

        prompt_text = self._maybe_prepend_instructions(prompt_id, prompt_text)

        short = prompt_text[:80] + ("..." if len(prompt_text) > 80 else "")
        log.info("プロンプトを送信します [%s] (pane=%s): %s", cwd, pane_target, short)
        print(f"[agent-loop] send [{cwd}] (pane={pane_target}) {short}", file=sys.stderr, flush=True)

        ok, err = _send_to_pane(pane_target, prompt_text)
        if not ok:
            log.warning("テキスト送信に失敗しました: %s", err)
            print(f"[agent-loop] done [{cwd}] failed", file=sys.stderr, flush=True)
            return False

        print(f"[agent-loop] done [{cwd}] sent", file=sys.stderr, flush=True)
        return True

    def _maybe_prepend_instructions(self, prompt_id: str, prompt_text: str) -> str:
        """グローバル指示（agent-instructions）を送信プロンプト先頭へ前置する。
        このペインで未注入 or revision が変わったときだけ前置し、覚えた revision を更新する。
        不在 / 破損 / 無効 / 既にマーカー混入済みはすべて no-op（フェイルセーフ）。"""
        global _INSTRUCTIONS_REV_APPLIED
        try:
            data = _load_instructions()
            block = render_instructions_block(data)
            if not block:
                return prompt_text
            rev = _instructions_revision(data)
            with self._lock:
                already = self._instr_rev.get(prompt_id)
            if already == rev:
                return prompt_text
            merged = prepend_instructions(prompt_text, block)
            if merged != prompt_text:
                with self._lock:
                    self._instr_rev[prompt_id] = rev
                _INSTRUCTIONS_REV_APPLIED = rev
                log.info("グローバル指示 rev:%s をペイン %s へ注入します。", rev, prompt_id)
            return merged
        except Exception:  # noqa: BLE001 — 指示注入の失敗で送信を止めない
            return prompt_text

    def is_pane_alive(self, prompt_id: str) -> bool:
        """ペインが存在するか確認する。"""
        with self._lock:
            pane_target = self._panes.get(prompt_id)
        return pane_target is not None and self._pane_exists(pane_target)

    def is_restarting(self, prompt_id: str) -> bool:
        with self._lock:
            lock = self._restart_locks.get(prompt_id)
        return lock is not None and lock.locked()

    def restart_pane(self, prompt_id: str) -> None:
        """ペインを再起動する。"""
        with self._lock:
            if prompt_id not in self._restart_locks:
                self._restart_locks[prompt_id] = threading.Lock()
            restart_lock = self._restart_locks[prompt_id]
            cwd = self._prompt_cwds.get(prompt_id)
            prompt_name = self._prompt_names.get(prompt_id, prompt_id)
            owner = self._owners.get(prompt_id, "scheduled")

        if not restart_lock.acquire(blocking=False):
            log.info("kiro-cli ペイン再起動は既に進行中です (prompt_id=%s)。", prompt_id)
            return

        log.info("kiro-cli ペインを再起動します (prompt_id=%s)。", prompt_id)
        try:
            with self._lock:
                if prompt_id not in self._panes:
                    return
            self._stop_pane(prompt_id)
            time.sleep(2)
            self._start_pane(prompt_id, prompt_name, cwd, owner)
        finally:
            restart_lock.release()

    def sync_entries(self, entries: list[dict[str, Any]]) -> None:
        """エントリ一覧に合わせてペインを起動/停止する。

        oneshot / external target は daemon 起動時に一括作成しない。
        """
        desired: dict[str, str] = {}
        desired_cwd: dict[str, str | None] = {}
        for entry in entries:
            prompt_id = str(entry.get("id", "")).strip()
            if not prompt_id:
                continue
            if entry.get("oneshot"):
                continue
            if entry.get("target"):
                continue  # external pane — SessionManager に登録しない
            prompt_name = str(entry.get("name", prompt_id)).strip() or prompt_id
            desired[prompt_id] = prompt_name
            desired_cwd[prompt_id] = str(entry.get("cwd", "")).strip() or None

        with self._lock:
            all_current_ids = set(self._panes)
            current_ids = {
                prompt_id for prompt_id in self._panes
                if self._owners.get(prompt_id, "scheduled") == "scheduled"
            }

        remove_ids = current_ids - set(desired.keys())
        add_ids = [pid for pid in desired.keys() if pid not in all_current_ids]
        keep_ids = current_ids & set(desired.keys())

        for prompt_id in remove_ids:
            with self._lock:
                prompt_name = self._prompt_names.pop(prompt_id, prompt_id)
                self._tmux_names.pop(prompt_id, None)
                self._prompt_cwds.pop(prompt_id, None)
            log.info("プロンプト '%s' のペインを停止します。", prompt_name)
            self._stop_pane(prompt_id)

        with self._lock:
            for prompt_id in keep_ids:
                self._prompt_names[prompt_id] = desired[prompt_id]

        for prompt_id in add_ids:
            self._start_pane(prompt_id, desired[prompt_id], desired_cwd.get(prompt_id))

        if remove_ids and not add_ids:
            self.write_state()

    def get_status(self) -> tuple[str, str, int, int]:
        with self._lock:
            pane_ids = list(self._panes.items())
        alive = sum(1 for _, pane_target in pane_ids if self._pane_exists(pane_target))
        return self._target_name, self._target_path, len(pane_ids), alive

    def list_prompt_statuses(self) -> list[tuple[str, str, bool, str, str]]:
        with self._lock:
            items = list(self._panes.items())
            names = dict(self._prompt_names)
            tmux_names = dict(self._tmux_names)

        statuses: list[tuple[str, str, bool, str, str]] = []
        for prompt_id, pane_target in items:
            prompt_name = names.get(prompt_id, prompt_id)
            tmux_name = tmux_names.get(prompt_id, "")
            statuses.append((prompt_name, prompt_id, self._pane_exists(pane_target), tmux_name, pane_target))

        statuses.sort(key=lambda item: item[0])
        return statuses

    def resolve_managed_pane(self, target: str) -> str | None:
        """管理下のペインの中から target に対応するペイン ID を返す。

        target には pane ID (%N)、tmux セッション名、またはプロンプト名を指定できる。
        管理外のターゲットは None を返す。
        """
        with self._lock:
            items = list(self._panes.items())
            names = dict(self._prompt_names)
            tmux_names = dict(self._tmux_names)

        for prompt_id, pane_target in items:
            if (
                target == pane_target
                or target == tmux_names.get(prompt_id, "")
                or target == names.get(prompt_id, "")
            ):
                return pane_target

        return None

    def is_managed_pane(self, pane_id: str) -> bool:
        """pane_id がこの SessionManager の管理下か。"""
        with self._lock:
            return pane_id in self._panes.values()

    @staticmethod
    def get_pane_pid(pane_id: str) -> int | None:
        """tmux からペインの shell PID を取得する。"""
        result = _tmux_cmd("display-message", "-p", "-t", pane_id, "#{pane_pid}")
        if result.returncode != 0:
            return None
        raw = (result.stdout or "").strip()
        try:
            pid = int(raw)
        except ValueError:
            return None
        return pid if pid > 0 else None

    @staticmethod
    def _enumerate_descendant_pids(root_pid: int) -> list[int]:
        """ps から root_pid と子孫 PID を深さ降順で列挙する（葉から kill する）。"""
        result = subprocess.run(
            ["ps", "-eo", "pid=", "ppid="],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return [root_pid]
        children: dict[int, list[int]] = {}
        for line in (result.stdout or "").splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                pid, ppid = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            children.setdefault(ppid, []).append(pid)

        depths: dict[int, int] = {root_pid: 0}
        queue = [root_pid]
        while queue:
            current = queue.pop(0)
            for child in children.get(current, []):
                if child in depths:
                    continue
                depths[child] = depths[current] + 1
                queue.append(child)

        ordered = sorted(depths.keys(), key=lambda p: depths[p], reverse=True)
        return ordered

    @staticmethod
    def _signal_pids(pids: list[int], sig: signal.Signals) -> None:
        for pid in pids:
            if pid <= 0:
                continue
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                log.warning("PID %d へ signal %s を送れません（権限不足）。", pid, sig.name)

    def kill_process_tree(self, pane_id: str, grace_seconds: float = 2.0) -> None:
        """managed pane の process tree を停止し tmux pane を削除する。

        PID 解決に失敗した場合は kill-pane のみ行い、広い process group へ signal を送らない。
        slot / monitor 解放は呼び出し元の責務。
        """
        if not self.is_managed_pane(pane_id):
            log.warning("kill_process_tree: 管理外ペイン %s をスキップします。", pane_id)
            return

        root_pid = self.get_pane_pid(pane_id)
        if root_pid is None:
            log.warning("kill_process_tree: PID 解決失敗 (pane=%s)。kill-pane のみ実行します。", pane_id)
        else:
            descendants = self._enumerate_descendant_pids(root_pid)
            self._signal_pids(descendants, signal.SIGTERM)
            if grace_seconds > 0:
                time.sleep(grace_seconds)
            survivors = [pid for pid in descendants if self._pid_is_alive(pid)]
            self._signal_pids(survivors, signal.SIGKILL)

        if self._pane_exists(pane_id):
            try:
                window_target = self._window_target_from_pane(pane_id)
                _tmux_cmd("kill-pane", "-t", pane_id, capture=False)
                _tmux_cmd("select-layout", "-t", window_target, self._layout_name(), capture=False)
            except RuntimeError:
                _tmux_cmd("kill-pane", "-t", pane_id, capture=False)

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def cleanup_managed_pane(self, prompt_id: str, grace_seconds: float = 2.0) -> bool:
        """prompt_id の managed pane を process tree ごと停止し管理から外す。"""
        with self._lock:
            pane_id = self._panes.get(prompt_id)
        if pane_id is None:
            return False
        self.kill_process_tree(pane_id, grace_seconds=grace_seconds)
        with self._lock:
            if self._panes.get(prompt_id) == pane_id:
                self._panes.pop(prompt_id, None)
                self._prompt_names.pop(prompt_id, None)
                self._tmux_names.pop(prompt_id, None)
                self._prompt_cwds.pop(prompt_id, None)
                self._owners.pop(prompt_id, None)
                self._ownership.pop(prompt_id, None)
                self._effective_model.pop(prompt_id, None)
                self._launch_fingerprint.pop(prompt_id, None)
                self._instr_rev.pop(prompt_id, None)
        self.write_state()
        return True

    @staticmethod
    def capture_visible_input_tail(pane_id: str, lines: int = 3) -> str:
        """入力残留判定用にペイン末尾の可視行を返す。"""
        content = _capture_pane(pane_id)
        tail_lines = [ln.rstrip() for ln in content.splitlines() if ln.strip()]
        if not tail_lines:
            return ""
        return "\n".join(tail_lines[-max(lines, 1):])

    def restart_if_dead(self) -> None:
        with self._lock:
            items = list(self._panes.items())
            names = dict(self._prompt_names)
            owners = dict(self._owners)

        for prompt_id, pane_target in items:
            if owners.get(prompt_id, "scheduled") != "scheduled":
                continue
            if self.is_restarting(prompt_id):
                continue
            if not self._pane_exists(pane_target):
                prompt_name = names.get(prompt_id, prompt_id)
                log.warning("プロンプト '%s' のペインが終了しました。再起動します。", prompt_name)
                try:
                    self.restart_pane(prompt_id)
                except RuntimeError as exc:
                    log.error("プロンプト '%s' のペイン再起動に失敗しました: %s", prompt_name, exc)

    def _state_file_path(self) -> Path:
        return _STATE_DIR / f"{os.getpid()}.json"

    def set_state_extras(self, extras: dict[str, Any]) -> None:
        """loop-state に載せる追加フィールド（run_state 等）。"""
        with self._lock:
            self._state_extras = dict(extras or {})

    def write_state(self) -> None:
        """現在のペイン状態をファイルに書き出す（ls/send サブコマンドが参照する）。"""
        with self._lock:
            items = list(self._panes.items())
            names = dict(self._prompt_names)
            extras = dict(getattr(self, "_state_extras", {}) or {})
        sessions_data = []
        for prompt_id, pane_target in items:
            sessions_data.append({
                "name": names.get(prompt_id, prompt_id),
                "id": prompt_id,
                "pane": pane_target,
                "alive": self._pane_exists(pane_target),
            })
        data = {
            "pid": os.getpid(),
            "cwd": self._target_path,
            "started_at": int(time.time()),
            "updated_at": time.time(),
            "sessions": sessions_data,
        }
        data.update(extras)
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            self._state_file_path().write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.warning("状態ファイルの書き出しに失敗しました: %s", exc)

    def remove_state(self) -> None:
        """状態ファイルを削除する。"""
        try:
            self._state_file_path().unlink(missing_ok=True)
        except OSError:
            pass

    def stop(self) -> None:
        with self._lock:
            prompt_ids = list(self._panes.keys())
            self._prompt_names.clear()
            self._tmux_names.clear()
            self._prompt_cwds.clear()
            self._owners.clear()

        for prompt_id in prompt_ids:
            self._stop_pane(prompt_id)
        self.remove_state()
