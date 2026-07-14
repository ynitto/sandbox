from __future__ import annotations
# scheduler.py — 元 agent-loop.py の 1474-1977 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# 定期実行スケジューラ
# ---------------------------------------------------------------------------

class PeriodicScheduler:
    """定期プロンプトのスケジュール管理。"""

    def __init__(
        self,
        session_mgr: SessionManager,
        entries: list[dict[str, Any]],
        semaphore: GlobalSemaphore | None = None,
        slot_monitor: "SlotMonitor | None" = None,
    ):
        self._session_mgr = session_mgr
        self._semaphore = semaphore
        self._slot_monitor = slot_monitor
        self._entries: list[dict[str, Any]] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # event_hook / webhook モジュールのキャッシュ: {hook_path: (mtime, module)}
        # webhook は HTTP スレッドから、event_hook は scheduler スレッドから読むためロックで保護する。
        self._hook_cache: dict[str, tuple[float, Any]] = {}
        self._hook_cache_lock = threading.Lock()
        # 外部（webhook 等）から積まれた完成プロンプトの name 別キュー。
        # entries 全置換の影響を受けないよう scheduler 側で独立保有する。
        self._external_queues: dict[str, collections.deque[str]] = {}
        self._set_entries(entries, allow_immediate_once=True)

    def _release_slot(self, pane_id: str | None) -> None:
        if self._semaphore is not None and pane_id:
            self._semaphore.release(pane_id)

    def _update_entry(self, entry_id: str, **fields: Any) -> None:
        with self._lock:
            for e in self._entries:
                if e.get("id") == entry_id:
                    e.update(fields)
                    break

    def _set_entries(self, entries: list[dict[str, Any]], allow_immediate_once: bool = False) -> None:
        normalized: list[dict[str, Any]] = []
        now = time.time()

        for entry in entries:
            if not entry.get("enabled", True):
                continue

            prompt = str(entry.get("prompt", "")).strip()
            event_hook = str(entry.get("event_hook", "")).strip() or None
            event_hook_fallback = bool(entry.get("event_hook_fallback", False))
            webhook = self._normalize_webhook(entry.get("webhook"))
            # prompt は通常必須だが、event_hook がある場合は check() が
            # 送信テキストを返すため空でも許容する。
            if not prompt and not event_hook:
                continue

            name = str(entry.get("name", prompt[:40] or (event_hook or "")[:40]))

            # スケジュール: cron 式 または interval_minutes のどちらかが必要。
            # ただし webhook ブロックを持つエントリはスケジュール無し（push 駆動）を許容する。
            cron_str = str(entry.get("cron", "")).strip()
            cron_expr: CronExpression | None = None
            interval = 0
            scheduled = True

            if cron_str:
                try:
                    cron_expr = CronExpression(cron_str)
                except ValueError as exc:
                    log.warning("cron 式が不正なためスキップします: %s (%s)", cron_str, exc)
                    continue
            else:
                interval_minutes = entry.get("interval_minutes")
                try:
                    interval = int(interval_minutes)  # type: ignore[arg-type]
                except Exception:
                    interval = 0
                if interval < 1:
                    if webhook is not None:
                        scheduled = False   # webhook 専用: 自動発火せず受信時のみ送る
                    else:
                        continue

            prompt_id = str(entry.get("id") or uuid.uuid4())
            run_immediately = bool(
                entry.get("run_immediately_on_startup", entry.get("run_immediately", False))
            )

            if not scheduled:
                # sentinel: `now < inf` が常に真になりスケジュール発火パスに乗らない。
                next_run_at = math.inf
            elif allow_immediate_once and run_immediately:
                # 起動直後は kiro-cli セットアップ時間を見込んで 30 秒待ってから初回送信する。
                next_run_at = now + 30
            elif cron_expr is not None:
                next_run_at = cron_expr.next_run(_dt.datetime.now().astimezone()).timestamp()
            else:
                next_run_at = now + (interval * 60)

            fresh_context = bool(entry.get("fresh_context", False))
            fresh_context_interval_raw = entry.get("fresh_context_interval_minutes")
            try:
                fresh_context_interval = int(fresh_context_interval_raw) if fresh_context_interval_raw is not None else None
            except Exception:
                fresh_context_interval = None
            if fresh_context_interval is not None and fresh_context_interval < 1:
                fresh_context_interval = None

            entry_cwd = str(entry.get("cwd", "")).strip() or None

            normalized.append({
                "id": prompt_id,
                "name": name,
                "prompt": prompt,
                "cron": cron_str if cron_expr else None,
                "interval_minutes": interval,
                "enabled": True,
                "run_immediately_on_startup": run_immediately,
                "next_run_at": next_run_at,
                "fresh_context": fresh_context,
                "fresh_context_interval_minutes": fresh_context_interval,
                "next_clear_at": now if fresh_context else None,
                "cwd": entry_cwd,
                "exclude_from_concurrency": bool(entry.get("exclude_from_concurrency", False)),
                "event_hook": event_hook,
                "event_hook_fallback": event_hook_fallback,
                "webhook": webhook,
            })

        self._session_mgr.sync_entries(normalized)

        with self._lock:
            self._entries = normalized

    def set_entries(self, entries: list[dict[str, Any]]) -> None:
        """エントリを設定する（次回ループから適用）。"""
        self._set_entries(entries, allow_immediate_once=False)

    def _is_in_cooldown(self, entry: dict[str, Any], pane_id: str) -> bool:
        """クールダウン中かチェックし、中なら next_run_at を更新して True を返す。"""
        if self._semaphore is None:
            return False
        remaining = self._semaphore.cooldown_remaining(pane_id)
        if remaining > 0:
            name = str(entry.get("name", ""))
            log.info(
                "[%s] クールダウン中のため実行を延期します (残り %.0f 秒)。",
                name, remaining,
            )
            self._update_entry(str(entry.get("id", "")), next_run_at=time.time() + remaining + 1)
            return True
        return False

    def _next_run_at_for_entry(self, entry: dict[str, Any]) -> float:
        """エントリの次回実行時刻 (Unix timestamp) を計算する。"""
        cron_str = entry.get("cron")
        if cron_str:
            try:
                return CronExpression(cron_str).next_run(_dt.datetime.now()).timestamp()
            except Exception as exc:
                log.error("[%s] cron 次回時刻計算エラー: %s", entry.get("name", ""), exc)
                return time.time() + 60
        interval_minutes = max(int(entry.get("interval_minutes", 1)), 1)
        return time.time() + interval_minutes * 60

    def _acquire_slot(self, entry: dict[str, Any], pane_id: str) -> bool:
        """セマフォスロットを取得する。取得できない場合は今回の送信をスキップして False を返す。

        Returns True if execution should proceed, False if it should be skipped.
        """
        assert self._semaphore is not None
        name = str(entry.get("name", ""))

        elapsed = self._semaphore.slot_elapsed(pane_id)
        if elapsed is not None:
            if elapsed < self._semaphore.slot_timeout:
                log.info(
                    "[%s] 前回の実行が完了待ちです (経過 %.0f秒 / 猶予 %d秒)。"
                    "30秒後に再試行します。",
                    name, elapsed, self._semaphore.slot_timeout,
                )
                self._update_entry(str(entry.get("id", "")), next_run_at=time.time() + 30)
                return False
            else:
                log.warning(
                    "[%s] 猶予時間 (%d秒) を超過。スロットを強制解放します。",
                    name, self._semaphore.slot_timeout,
                )
                if self._slot_monitor is not None:
                    self._slot_monitor.untrack(pane_id)
                self._semaphore.release(pane_id)

        if self._is_in_cooldown(entry, pane_id):
            return False

        if not self._semaphore.acquire(pane_id):
            log.warning(
                "[%s] 同時実行数が上限 (%d) に達しています。今回の送信をスキップします。",
                name, self._semaphore.max_concurrent,
            )
            print(
                f"[agent-loop] [{name}] 同時実行数が上限に達しています。今回はスキップします。",
                file=sys.stderr, flush=True,
            )
            self._update_entry(str(entry.get("id", "")), next_run_at=self._next_run_at_for_entry(entry))
            return False

        return True

    # ---- inbound webhook 連携 --------------------------------------------

    @staticmethod
    def _normalize_webhook(raw: Any) -> dict[str, Any] | None:
        """エントリの webhook ブロックを正規化する。無ければ None。"""
        if not isinstance(raw, dict):
            return None
        return {
            "hook": str(raw.get("hook", "")).strip() or None,
            "secret": str(raw.get("secret", "")),
            "secret_header": str(raw.get("secret_header", "")).strip() or None,
        }

    def _find_entry_by_key_locked(self, key: str) -> dict[str, Any] | None:
        """`_lock` 保持前提で、webhook キー一致のエントリを返す。"""
        for e in self._entries:
            if _webhook_key(str(e.get("name", ""))) == key:
                return e
        return None

    def resolve_webhook_route(self, name: str) -> dict[str, Any] | None:
        """webhook パス名 → ルート情報。毎リクエスト最新エントリから解決する（リロード追従）。"""
        key = _webhook_key(name)
        with self._lock:
            entry = self._find_entry_by_key_locked(key)
            if entry is None or not entry.get("webhook"):
                return None
            wh = entry["webhook"]
            return {
                "name": str(entry.get("name", "")),
                "prompt_template": str(entry.get("prompt", "")),
                "hook": wh.get("hook"),
                "secret": wh.get("secret") or "",
                "secret_header": wh.get("secret_header"),
            }

    def enqueue_external(self, name: str, prompt_text: str) -> bool:
        """外部（webhook 等）から name 宛の完成プロンプトをキューに積む。

        scheduler スレッドが次サイクルで session 準備 + セマフォ込みで dispatch する。
        戻り値 False = 該当エントリ無し。
        """
        key = _webhook_key(name)
        with self._lock:
            entry = self._find_entry_by_key_locked(key)
            if entry is None:
                return False
            q = self._external_queues.setdefault(
                key, collections.deque(maxlen=_WEBHOOK_QUEUE_MAX))
            if len(q) >= _WEBHOOK_QUEUE_MAX:
                log.warning("[%s] webhook キューが上限 (%d) に達したため最古を破棄します。",
                            entry.get("name", key), _WEBHOOK_QUEUE_MAX)
            q.append(prompt_text)
            return True

    def _drain_external_one(self, entry: dict[str, Any]) -> bool:
        """entry の外部キューを 1 件だけ処理する。

        キューに要素があったサイクルでは True を返す（dispatch した／保留で積み直した
        いずれも）。空なら False。session 未準備・スロット上限時はプロンプトを積み直す。
        """
        key = _webhook_key(str(entry.get("name", "")))
        with self._lock:
            q = self._external_queues.get(key)
            if not q:
                return False
            prompt_text = q.popleft()

        name = str(entry.get("name", ""))
        prompt_id = str(entry.get("id", ""))
        exclude = bool(entry.get("exclude_from_concurrency", False))

        def requeue() -> None:
            with self._lock:
                self._external_queues.setdefault(
                    key, collections.deque(maxlen=_WEBHOOK_QUEUE_MAX)).appendleft(prompt_text)

        if not self._session_mgr.ensure_session(prompt_id, name):
            requeue()
            return True

        pane_id: str | None = None
        if self._semaphore is not None and not exclude:
            pane_id = self._session_mgr.get_pane_id(prompt_id)
            if pane_id:
                elapsed = self._semaphore.slot_elapsed(pane_id)
                if elapsed is not None:
                    if elapsed < self._semaphore.slot_timeout:
                        requeue()
                        return True
                    if self._slot_monitor is not None:
                        self._slot_monitor.untrack(pane_id)
                    self._semaphore.release(pane_id)
                if self._semaphore.cooldown_remaining(pane_id) > 0:
                    requeue()
                    return True
                if not self._semaphore.acquire(pane_id):
                    requeue()
                    return True

        dispatch_entry = dict(entry)
        dispatch_entry["prompt"] = prompt_text
        dispatch_entry["_should_clear"] = False
        self._dispatch_prompt(dispatch_entry, pane_id)
        return True

    def _load_hook_module(self, hook_path: Path) -> Any | None:
        """hook スクリプト（event_hook / webhook 共通）を importlib でロードする。

        mtime キャッシュ付き。event_hook は scheduler スレッド、webhook は HTTP
        スレッドから呼ばれるため、キャッシュ操作は `_hook_cache_lock` で保護する。
        """
        key = str(hook_path)
        try:
            mtime = hook_path.stat().st_mtime
        except OSError:
            log.warning("hook が見つかりません: %s", hook_path)
            return None

        with self._hook_cache_lock:
            cached = self._hook_cache.get(key)
            if cached and cached[0] == mtime:
                return cached[1]

            try:
                spec = importlib.util.spec_from_file_location("kiro_loop_hook", hook_path)
                if spec is None or spec.loader is None:
                    log.error("hook の spec 生成に失敗しました: %s", hook_path)
                    return None
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._hook_cache[key] = (mtime, module)
                return module
            except Exception as exc:
                log.error("hook のロードに失敗しました (%s): %s", hook_path, exc, exc_info=True)
                return None

    def _call_hook_check(self, entry: dict[str, Any]) -> str | None:
        """event_hook の check() を呼び出して送信プロンプトを得る。

        scheduler スレッド（単一）内で実行されるため env 変数の一時設定は安全。
        event_hook_fallback の値を環境変数経由でフックに渡し、フック側が
        「更新が無いときにフォールバック送信するか」を自己判断できるようにする。

        Returns:
            str  : kiro-cli に送信するプロンプトテキスト
            None : 今回のサイクルはスキップ
        """
        hook_path = Path(os.path.expanduser(entry["event_hook"])).resolve()
        name = str(entry.get("name", ""))
        module = self._load_hook_module(hook_path)
        if module is None:
            return None

        check_fn = getattr(module, "check", None)
        if not callable(check_fn):
            log.warning("[%s] event_hook に check() 関数が定義されていません: %s", name, hook_path)
            return None

        fallback_flag = "1" if entry.get("event_hook_fallback") else "0"
        env_overrides = {
            "AGENT_LOOP_EVENT_HOOK_FALLBACK": fallback_flag,
            "AGENT_LOOP_PROMPT_NAME": name,
        }
        previous = {k: os.environ.get(k) for k in env_overrides}
        os.environ.update(env_overrides)
        try:
            result = check_fn()
        except Exception as exc:
            log.error("[%s] check() でエラーが発生しました: %s", name, exc, exc_info=True)
            return None
        finally:
            for k, prev in previous.items():
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev

        if result is not None and not isinstance(result, str):
            log.warning("[%s] check() の戻り値が str でも None でもありません: %r", name, result)
            return None
        return result

    def _dispatch_prompt(self, entry: dict[str, Any], pane_id: str | None) -> None:
        """プロンプトを送信し、失敗時は再起動する。"""
        name = str(entry.get("name", ""))
        prompt_id = str(entry.get("id", ""))
        prompt = str(entry.get("prompt", ""))
        should_clear = bool(entry.get("_should_clear", False))
        fresh_context_interval = entry.get("fresh_context_interval_minutes")

        log.info("[%s] プロンプトを実行します。", name)
        try:
            if should_clear:
                log.info("[%s] fresh_context: コンテキストをクリアします。", name)
                if not self._session_mgr.send_prompt(prompt_id, "/clear"):
                    log.warning("[%s] /clear の送信に失敗しました。スキップします。", name)
                    self._release_slot(pane_id)
                    return
                time.sleep(2)
                if fresh_context_interval is not None:
                    new_next_clear_at = time.time() + (int(fresh_context_interval) * 60)
                    self._update_entry(str(entry.get("id", "")), next_clear_at=new_next_clear_at)

            ok = self._session_mgr.send_prompt(prompt_id, prompt)
            if ok:
                if self._slot_monitor is not None and pane_id:
                    self._slot_monitor.track(pane_id)
            else:
                self._release_slot(pane_id)
                if not self._stop_event.is_set():
                    log.warning("[%s] 送信失敗。ペイン再起動を試みます。", name)
                    try:
                        self._session_mgr.restart_pane(prompt_id)
                    except RuntimeError as exc:
                        log.error("[%s] 再起動失敗: %s", name, exc)
        except Exception as exc:
            self._release_slot(pane_id)
            log.error("[%s] 予期しないエラー: %s", name, exc, exc_info=True)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_loop,
            name="periodic-scheduler",
            daemon=True,
        )
        self._thread.start()
        log.info("定期スケジューラを開始しました。")

    def _run_loop(self) -> None:
        while not self._stop_event.wait(1):
            now = time.time()
            with self._lock:
                entries = [e.copy() for e in self._entries]

            for entry in entries:
                if not entry.get("enabled", True):
                    continue

                # webhook 等で積まれた外部キューを優先処理する（1 サイクル 1 件）。
                # 積まれていたサイクルはスケジュール発火より外部キューを優先する。
                if entry.get("webhook") and self._drain_external_one(entry):
                    continue

                if now < float(entry.get("next_run_at", now)):
                    continue

                name = str(entry.get("name", ""))
                prompt_id = str(entry.get("id", ""))
                exclude_from_concurrency = bool(entry.get("exclude_from_concurrency", False))

                fresh_context = bool(entry.get("fresh_context", False))
                fresh_context_interval = entry.get("fresh_context_interval_minutes")

                should_clear = False
                if fresh_context:
                    if fresh_context_interval is not None:
                        next_clear_at = float(entry.get("next_clear_at") or 0)
                        if now >= next_clear_at:
                            should_clear = True
                    else:
                        should_clear = True
                # Stash should_clear in entry copy for _acquire_slot / _dispatch_prompt
                entry["_should_clear"] = should_clear

                # event_hook がある場合は check() を呼んで送信プロンプトを決定する。
                # None が返ったら今回は送信せず次回スケジュールへ。
                if entry.get("event_hook"):
                    prompt_text = self._call_hook_check(entry)
                    if prompt_text is None:
                        self._update_entry(prompt_id, next_run_at=self._next_run_at_for_entry(entry))
                        continue
                    entry["prompt"] = prompt_text

                if not self._session_mgr.ensure_session(prompt_id, name):
                    log.warning("[%s] 対応セッションの準備に失敗したため今回の送信をスキップします。", name)
                else:
                    pane_id: str | None = None
                    if self._semaphore is not None and not exclude_from_concurrency:
                        pane_id = self._session_mgr.get_pane_id(prompt_id)
                        if pane_id and not self._acquire_slot(entry, pane_id):
                            continue

                    self._dispatch_prompt(entry, pane_id)

                self._update_entry(str(entry.get("id", "")), next_run_at=self._next_run_at_for_entry(entry))

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


