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
        response_timeout: int,
        echo_output: bool = False,
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
        self._response_timeout = response_timeout
        self._echo_output = echo_output
        self._uses_concurrency_agent = uses_concurrency_agent

        # prompt_id → pane_id (str)
        self._panes: dict[str, str] = {}
        self._prompt_names: dict[str, str] = {}
        self._tmux_names: dict[str, str] = {}
        self._prompt_cwds: dict[str, str | None] = {}
        self._restart_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

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

    def _start_pane(self, prompt_id: str, prompt_name: str, cwd: str | None = None) -> bool:
        """新しい kiro-cli ペインを起動して管理下に登録する。"""
        if shutil.which("tmux") is None:
            raise RuntimeError("tmux が PATH に見つかりません。`sudo apt install tmux` を実行してください。")
        kiro_bin = shutil.which("kiro-cli")
        if kiro_bin is None:
            raise RuntimeError("kiro-cli が PATH に見つかりません。インストールしてください。")

        session_cwd = self._resolve_cwd(cwd)

        cmd_args = ["chat"] + self._kiro_args_base[:]
        if self._uses_concurrency_agent:
            agent_file = Path.home() / ".kiro" / "agents" / f"{CONCURRENCY_AGENT_NAME}.json"
            if agent_file.is_file():
                cmd_args += ["--agent", CONCURRENCY_AGENT_NAME]
        cmd = " ".join(shlex.quote(arg) for arg in [kiro_bin, *cmd_args])

        try:
            pane_target = self._create_worker_pane(cmd, session_cwd)
        except RuntimeError as exc:
            log.error("プロンプト '%s' のペイン起動に失敗しました: %s", prompt_name, exc)
            return False

        attach_session_name = self.get_attach_session_name()

        with self._lock:
            self._panes[prompt_id] = pane_target
            self._prompt_names[prompt_id] = prompt_name
            self._tmux_names[prompt_id] = attach_session_name
            self._prompt_cwds[prompt_id] = cwd
            if prompt_id not in self._restart_locks:
                self._restart_locks[prompt_id] = threading.Lock()

        log.info(
            "プロンプト '%s' 用ペインを起動しました (pane=%s, tmux=%s, args=%s)。",
            prompt_name, pane_target, attach_session_name, self._kiro_args_base,
        )
        self.write_state()
        return True

    def _stop_pane(self, prompt_id: str) -> None:
        """ペインを終了する（_restart_locks は保持する）。"""
        with self._lock:
            pane_target = self._panes.pop(prompt_id, None)

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

    def ensure_session(self, prompt_id: str, prompt_name: str) -> bool:
        """セッションが存在しない場合は起動する。成功時 True を返す。"""
        with self._lock:
            existing = self._panes.get(prompt_id)
            cwd = self._prompt_cwds.get(prompt_id)
        if existing is not None:
            return True
        return self._start_pane(prompt_id, prompt_name, cwd)

    def get_pane_id(self, prompt_id: str) -> str | None:
        """prompt_id に対応するペイン ID を返す（なければ None）。"""
        with self._lock:
            return self._panes.get(prompt_id)

    def send_prompt(self, prompt_id: str, prompt_text: str) -> bool:
        """tmux ペインにプロンプトを送信する（応答待ちはしない）。"""
        with self._lock:
            pane_target = self._panes.get(prompt_id)
            cwd = self._prompt_cwds.get(prompt_id, self._target_path) or self._target_path

        if pane_target is None or not self._pane_exists(pane_target):
            log.warning("kiro-cli ペインが存在しません (prompt_id=%s)。", prompt_id)
            return False

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

        if not restart_lock.acquire(blocking=False):
            log.info("kiro-cli ペイン再起動は既に進行中です (prompt_id=%s)。", prompt_id)
            return

        log.info("kiro-cli ペインを再起動します (prompt_id=%s)。", prompt_id)
        try:
            self._stop_pane(prompt_id)
            time.sleep(2)
            self._start_pane(prompt_id, prompt_name, cwd)
        finally:
            restart_lock.release()

    def sync_entries(self, entries: list[dict[str, Any]]) -> None:
        """エントリ一覧に合わせてペインを起動/停止する。"""
        desired: dict[str, str] = {}
        desired_cwd: dict[str, str | None] = {}
        for entry in entries:
            prompt_id = str(entry.get("id", "")).strip()
            if not prompt_id:
                continue
            prompt_name = str(entry.get("name", prompt_id)).strip() or prompt_id
            desired[prompt_id] = prompt_name
            desired_cwd[prompt_id] = str(entry.get("cwd", "")).strip() or None

        with self._lock:
            current_ids = set(self._panes.keys())

        remove_ids = current_ids - set(desired.keys())
        add_ids = [pid for pid in desired.keys() if pid not in current_ids]
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

    def restart_if_dead(self) -> None:
        with self._lock:
            items = list(self._panes.items())
            names = dict(self._prompt_names)

        for prompt_id, pane_target in items:
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

    def write_state(self) -> None:
        """現在のペイン状態をファイルに書き出す（ls/send サブコマンドが参照する）。"""
        with self._lock:
            items = list(self._panes.items())
            names = dict(self._prompt_names)
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

        for prompt_id in prompt_ids:
            self._stop_pane(prompt_id)
        self.remove_state()


