from __future__ import annotations
# semaphore.py — 元 agent-loop.py の 145-409 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# 分散セマフォ（複数 agent-loop 間の kiro-cli 同時実行数制御）
# ---------------------------------------------------------------------------

CONCURRENCY_AGENT_NAME = "agent-loop-concurrency"
_SLOTS_DIR = agent_home_subdir("", "slots")
_SLOTS_MUTEX = _SLOTS_DIR / ".lock"
_DEFAULT_SLOT_TIMEOUT = 7200  # 猶予時間のデフォルト値（秒）
_STATE_DIR = agent_home_subdir("", "loop-state")  # デーモン状態ファイルディレクトリ


class GlobalSemaphore:
    """ファイルベースの分散セマフォ。複数 agent-loop プロセス間でエージェント CLI の同時実行数を制御する。

    スロットファイル:     ~/.agents/slots/pane_{N}.json
    クールダウンファイル: ~/.agents/slots/cooldown_{N}.json
    ミューテックス:       ~/.agents/slots/.lock (fcntl.flock)
    """

    def __init__(self, max_concurrent: int, slot_timeout_seconds: int = _DEFAULT_SLOT_TIMEOUT, cooldown_seconds: int = 0) -> None:
        self.max_concurrent = max_concurrent
        self._slot_timeout = slot_timeout_seconds
        self.cooldown_seconds = cooldown_seconds
        _SLOTS_DIR.mkdir(parents=True, exist_ok=True)

    def acquire(self, pane_id: str, pid: int | None = None) -> bool:
        """スロットを取得する。取得できた場合 True、上限に達した場合 False を返す。

        pid を指定した場合はそのプロセス ID をスロットファイルに記録する。
        省略時は呼び出し元プロセスの PID を使用する。
        """
        if self.max_concurrent <= 0:
            return True

        slot_file = self._slot_path(pane_id)
        try:
            with open(_SLOTS_MUTEX, "w") as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    slot_file.unlink(missing_ok=True)
                    active = self._count_active_slots()
                    if active < self.max_concurrent:
                        slot_file.write_text(
                            json.dumps({
                                "pane_id": pane_id,
                                "pid": pid if pid is not None else os.getpid(),
                                "acquired_at": time.time(),
                            }),
                            encoding="utf-8",
                        )
                        return True
                    return False
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except OSError as exc:
            log.warning("セマフォ取得中にエラーが発生しました: %s", exc)
            return True  # エラー時は実行を許可（安全側に倒す）

    def release(self, pane_id: str) -> None:
        """スロットを解放する（冪等）。クールダウンが設定されている場合は記録する。
        解放時、スロットの保持時間（送信 → 完了検知）をノード予算の台帳へ記帳する
        （エージェント CLI の実行時間の近似。node-budget 契約）。"""
        slot_file = self._slot_path(pane_id)
        try:
            data = json.loads(slot_file.read_text(encoding="utf-8"))
            elapsed = time.time() - float(data.get("acquired_at", 0))
            if 0 < elapsed <= self._slot_timeout:   # タイムアウト強制解放は実行時間として数えない
                _node_budget_record(elapsed, ref=pane_id)
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            pass
        try:
            slot_file.unlink(missing_ok=True)
        except OSError:
            pass
        if self.cooldown_seconds > 0:
            try:
                self._cooldown_path(pane_id).write_text(
                    json.dumps({"pane_id": pane_id, "released_at": time.time()}),
                    encoding="utf-8",
                )
            except OSError:
                pass

    @property
    def slot_timeout(self) -> int:
        return self._slot_timeout

    def slot_elapsed(self, pane_id: str) -> float | None:
        """スロットファイルが存在する場合、取得からの経過秒を返す。存在しない場合は None。
        ファイルが読めない場合はタイムアウト超過扱いの値を返す。
        """
        slot_file = self._slot_path(pane_id)
        if not slot_file.exists():
            return None
        try:
            data = json.loads(slot_file.read_text(encoding="utf-8"))
            return time.time() - float(data.get("acquired_at", 0))
        except (json.JSONDecodeError, OSError, ValueError):
            return float(self._slot_timeout + 1)

    def cooldown_remaining(self, pane_id: str) -> float:
        """クールダウンの残り秒数を返す。クールダウン中でなければ 0 以下の値を返す。
        期限切れのクールダウンファイルは削除する。
        """
        if self.cooldown_seconds <= 0:
            return 0.0
        cooldown_file = self._cooldown_path(pane_id)
        if not cooldown_file.exists():
            return 0.0
        try:
            data = json.loads(cooldown_file.read_text(encoding="utf-8"))
            released_at = float(data.get("released_at", 0))
            remaining = self.cooldown_seconds - (time.time() - released_at)
            if remaining <= 0:
                cooldown_file.unlink(missing_ok=True)
            return remaining
        except (json.JSONDecodeError, OSError, ValueError):
            return 0.0

    @staticmethod
    def is_busy(pane_id: str, slot_timeout: int = _DEFAULT_SLOT_TIMEOUT) -> bool:
        """スロットファイルを参照してペインが処理中かを判断する。"""
        slot_file = _SLOTS_DIR / f"pane_{pane_id.lstrip('%')}.json"
        if not slot_file.exists():
            return False
        try:
            data = json.loads(slot_file.read_text(encoding="utf-8"))
            acquired_at = float(data.get("acquired_at", 0))
            if time.time() - acquired_at > slot_timeout:
                return False
            pid = int(data.get("pid", 0))
            if pid > 0:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return False
            return True
        except (json.JSONDecodeError, OSError, ValueError):
            return False

    def _slot_path(self, pane_id: str) -> Path:
        return _SLOTS_DIR / f"pane_{pane_id.lstrip('%')}.json"

    def _cooldown_path(self, pane_id: str) -> Path:
        return _SLOTS_DIR / f"cooldown_{pane_id.lstrip('%')}.json"

    def _count_active_slots(self) -> int:
        now = time.time()
        count = 0
        for slot_file in _SLOTS_DIR.glob("pane_*.json"):
            try:
                data = json.loads(slot_file.read_text(encoding="utf-8"))
                pid = int(data.get("pid", 0))
                acquired_at = float(data.get("acquired_at", 0))
                timed_out = now - acquired_at > self._slot_timeout

                if pid > 0:
                    try:
                        os.kill(pid, 0)
                        if timed_out:
                            log.warning(
                                "生存 PID のスロットがタイムアウト超過ですが保持します: %s (pid=%d)",
                                slot_file.name, pid,
                            )
                        count += 1
                    except ProcessLookupError:
                        slot_file.unlink(missing_ok=True)
                    except PermissionError:
                        count += 1  # 他ユーザーのプロセスは生きているとみなす
                elif timed_out:
                    slot_file.unlink(missing_ok=True)
                else:
                    count += 1
            except (json.JSONDecodeError, OSError, ValueError):
                try:
                    slot_file.unlink(missing_ok=True)
                except OSError:
                    pass
        return count


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def cleanup_stale_slots_on_startup(
    slot_timeout: int = _DEFAULT_SLOT_TIMEOUT,
    daemon_started_at: float | None = None,
    *,
    cooldown_seconds: int = 0,
    slots_dir: Path | None = None,
) -> dict[str, int]:
    """daemon 起動時に stale slot / cooldown を走査して整理する。

    JSON 破損または PID 死亡 → 削除。PID 生存 → timeout 超過でも保持（warning）。
    cooldown は期限切れのみ削除。
    """
    root = Path(slots_dir) if slots_dir is not None else _SLOTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    mutex = root / ".lock"
    stats = {"slots_deleted": 0, "cooldowns_deleted": 0, "slots_kept_alive": 0}

    try:
        with open(mutex, "w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX)
                now = time.time()
                for slot_file in list(root.glob("pane_*.json")):
                    try:
                        data = json.loads(slot_file.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                        slot_file.unlink(missing_ok=True)
                        stats["slots_deleted"] += 1
                        continue

                    pid = int(data.get("pid", 0))
                    acquired_at = float(data.get("acquired_at", 0))
                    timed_out = now - acquired_at > slot_timeout

                    if pid > 0 and _pid_alive(pid):
                        if timed_out:
                            log.warning(
                                "起動時クリーンアップ: 生存 PID のスロットを保持します: %s (pid=%d)",
                                slot_file.name, pid,
                            )
                        stats["slots_kept_alive"] += 1
                        continue

                    if pid > 0 or timed_out:
                        slot_file.unlink(missing_ok=True)
                        stats["slots_deleted"] += 1

                for cooldown_file in list(root.glob("cooldown_*.json")):
                    try:
                        data = json.loads(cooldown_file.read_text(encoding="utf-8"))
                        released_at = float(data.get("released_at", 0))
                        if cooldown_seconds <= 0:
                            continue
                        if now - released_at > cooldown_seconds:
                            cooldown_file.unlink(missing_ok=True)
                            stats["cooldowns_deleted"] += 1
                    except (json.JSONDecodeError, OSError, ValueError):
                        cooldown_file.unlink(missing_ok=True)
                        stats["cooldowns_deleted"] += 1
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except OSError as exc:
        log.warning("stale slot クリーンアップ中にエラーが発生しました: %s", exc)

    if daemon_started_at is not None:
        marker = root / ".daemon_started_at"
        try:
            marker.write_text(json.dumps({"started_at": daemon_started_at}), encoding="utf-8")
        except OSError:
            pass

    return stats


class SlotMonitor:
    """ペイン出力から処理完了を検知し、スロット解放と完了処理を行う。

    状態遷移:
      waiting_start → (プロンプト消失) → processing → (プロンプト再出現 or タイムアウト) → 解放
    """

    _POLL_INTERVAL = 2.0
    _START_WAIT_TIMEOUT = 60.0  # kiro-cli が処理を始めるまでの最大待機秒数（固定）

    def __init__(
        self,
        semaphore: GlobalSemaphore,
        slot_timeout_seconds: int = _DEFAULT_SLOT_TIMEOUT,
        freeze_timeout_seconds: int = 0,
        on_freeze: Any = None,
    ) -> None:
        self._semaphore = semaphore
        self._slot_timeout = slot_timeout_seconds
        self._freeze_timeout = max(int(freeze_timeout_seconds), 0)
        self._on_freeze = on_freeze
        self._lock = threading.Lock()
        # pane_id → state / acquired_at / optional callbacks / freeze hash
        self._pending: dict[str, dict[str, Any]] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def track(
        self,
        pane_id: str,
        on_complete: Any = None,
        on_freeze: Any = None,
    ) -> None:
        """ペインの処理完了監視を開始する。"""
        with self._lock:
            self._pending[pane_id] = {
                "state": "waiting_start",
                "acquired_at": time.time(),
                "on_complete": on_complete,
                "on_freeze": on_freeze,
                "content_hash": None,
                "hash_unchanged_since": None,
            }

    def untrack(self, pane_id: str) -> None:
        """監視を手動で終了する（agent hook 発火時など）。"""
        with self._lock:
            self._pending.pop(pane_id, None)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_loop,
            name="slot-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self._POLL_INTERVAL):
            with self._lock:
                pane_ids = list(self._pending.keys())

            for pane_id in pane_ids:
                self._check_pane(pane_id)

    def _check_pane(self, pane_id: str) -> None:
        with self._lock:
            entry = self._pending.get(pane_id)
            if entry is None:
                return
            state = entry["state"]
            acquired_at = entry["acquired_at"]

        # ペインが存在しない場合は即座に解放
        result = subprocess.run(
            [shutil.which("tmux") or "tmux", "display-message", "-p", "-t", pane_id, "#{pane_id}"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            self._release(pane_id, notify_complete=True)
            return

        # 待機/処理中の判定は CLI ごとに方法が違うため CliProfile が行う（ready/busy
        # パターン、パターンで判定できない CLI は静穏判定）。legacy は従来と同一。
        content = _capture_pane(pane_id)
        is_idle = _CLI_PROFILE.is_idle(pane_id, content)
        now = time.time()

        if state == "waiting_start":
            if not is_idle:
                with self._lock:
                    if pane_id in self._pending:
                        self._pending[pane_id]["state"] = "processing"
            elif now - acquired_at > self._START_WAIT_TIMEOUT:
                # エージェント CLI が処理を開始しないままタイムアウト
                log.warning("SlotMonitor: ペイン %s が処理を開始しないためスロットを解放します。", pane_id)
                self._release(pane_id, notify_complete=True)

        elif state == "processing":
            if is_idle:
                log.info("SlotMonitor: ペイン %s の処理完了を検知。スロットを解放します。", pane_id)
                self._release(pane_id, notify_complete=True)
            else:
                if self._freeze_timeout > 0:
                    self._check_freeze(pane_id, content, entry, now)
                if now - acquired_at > self._slot_timeout:
                    log.warning("SlotMonitor: ペイン %s がタイムアウト。スロットを強制解放します。", pane_id)
                    self._release(pane_id, keep_for_completion=True)

    def _check_freeze(
        self,
        pane_id: str,
        content: str,
        entry: dict[str, Any],
        now: float,
    ) -> None:
        """busy 中の画面 hash が freeze_timeout 変化しない場合に on_freeze を呼ぶ。"""
        content_hash = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        with self._lock:
            current = self._pending.get(pane_id)
            if current is not entry or current.get("state") != "processing":
                return
            prev_hash = current.get("content_hash")
            if prev_hash != content_hash:
                current["content_hash"] = content_hash
                current["hash_unchanged_since"] = now
                return
            unchanged_since = float(current.get("hash_unchanged_since") or now)
            if now - unchanged_since < self._freeze_timeout:
                return
            callback = current.get("on_freeze") or self._on_freeze
            current["hash_unchanged_since"] = now  # 連続発火を防ぐ
        if callable(callback):
            log.warning("SlotMonitor: ペイン %s が freeze 状態です。回復処理を呼び出します。", pane_id)
            try:
                callback(pane_id)
            except Exception:
                log.warning("SlotMonitor: freeze 回復処理に失敗しました (pane=%s)。", pane_id, exc_info=True)

    def _release(
        self,
        pane_id: str,
        *,
        notify_complete: bool = False,
        keep_for_completion: bool = False,
    ) -> None:
        with self._lock:
            entry = self._pending.get(pane_id)
            release_slot = entry is None or not entry.get("slot_released", False)
            if entry is not None and release_slot:
                entry["slot_released"] = True
            if entry and keep_for_completion and callable(entry.get("on_complete")):
                entry["acquired_at"] = float("inf")
            elif not notify_complete:
                self._pending.pop(pane_id, None)
        _CLI_PROFILE.forget_pane(pane_id)
        if release_slot:
            self._semaphore.release(pane_id)
        on_complete = entry.get("on_complete") if entry and notify_complete else None
        if callable(on_complete):
            try:
                on_complete()
            except Exception:
                log.warning("SlotMonitor: 完了処理に失敗しました (pane=%s)。", pane_id, exc_info=True)
                return
        if notify_complete:
            with self._lock:
                if self._pending.get(pane_id) is entry:
                    self._pending.pop(pane_id, None)
