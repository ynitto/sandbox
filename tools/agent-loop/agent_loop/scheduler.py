from __future__ import annotations
# scheduler.py — PeriodicScheduler を唯一の dispatch gate とする。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# 定期実行スケジューラ
# ---------------------------------------------------------------------------

# `slash` の名前規約（スキル・スラッシュコマンドの命名に合わせる）。
# 設計: docs/designs/agent-loop-slash-property-design.md
_SLASH_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# slash 行を送ってから次を送るまでの待ち。対話 CLI 側がコマンドを処理する間を空ける
# （fresh_context の `/clear` 後に 2 秒待つのと同じ趣旨。こちらは軽いので 1 秒）。
_SLASH_SEND_INTERVAL_SEC = 1.0
_HOOK_TIMEOUT_SEC = 30.0
_PREFLIGHT_TIMEOUT_SEC = 15.0
_INPUT_RECOVERY_WAIT_SEC = 0.4


def _normalize_slash(value: Any, ctx: str = "") -> list[str]:
    """`slash` を送信行（`/<name> [args]`）のリストへ正規化する。

    `string | string[]` を受ける。**規約外の要素はその要素だけ捨てる**——タイポ 1 個で
    エントリごと無効化すると、定期駆動が黙って止まる（止まったことにも気づきにくい）。
    """
    if value is None:
        return []
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)):
        log.warning("[%s] slash は文字列か文字列配列です（無視します）: %r", ctx, value)
        return []

    lines: list[str] = []
    for item in items:
        if not isinstance(item, str):
            log.warning("[%s] slash の要素が文字列ではありません（無視します）: %r", ctx, item)
            continue
        text = item.strip()
        if text.startswith("/"):
            log.warning("[%s] slash の値に先頭の '/' は不要です（剥がして送ります）: %r", ctx, item)
            text = text[1:].lstrip()
        if not text:
            continue
        name, _, args = text.partition(" ")
        if not _SLASH_NAME_RE.match(name):
            log.warning("[%s] slash の名前が規約外のため無視します: %r", ctx, item)
            continue
        args = args.strip()
        lines.append(f"/{name} {args}" if args else f"/{name}")
    return lines


def validate_entries(
    entries: list[dict[str, Any]],
    *,
    allow_immediate_once: bool = False,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """エントリを副作用なしで normalize / validate する。

    不正な個別エントリはスキップする（現行どおり）。`entries` 自体が list でなければ
    ValueError。
    """
    if not isinstance(entries, list):
        raise ValueError(f"entries は list である必要があります: {type(entries)!r}")

    normalized: list[dict[str, Any]] = []
    ts = float(now if now is not None else time.time())

    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"entry は dict である必要があります: {type(entry)!r}")
        if not entry.get("enabled", True):
            continue

        prompt = str(entry.get("prompt", "")).strip()
        event_hook = str(entry.get("event_hook", "")).strip() or None
        event_hook_fallback = bool(entry.get("event_hook_fallback", False))
        webhook = PeriodicScheduler._normalize_webhook(entry.get("webhook"))
        slash = _normalize_slash(entry.get("slash"), str(entry.get("name", "")))
        if not prompt and not event_hook and not slash:
            continue

        name = str(entry.get("name", prompt[:40] or (event_hook or "")[:40]
                              or (slash[0] if slash else "")))

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
                    scheduled = False
                else:
                    continue

        prompt_id = str(entry.get("id") or uuid.uuid4())
        run_immediately = bool(
            entry.get("run_immediately_on_startup", entry.get("run_immediately", False))
        )

        if not scheduled:
            next_run_at = math.inf
        elif allow_immediate_once and run_immediately:
            next_run_at = ts + 30
        elif cron_expr is not None:
            next_run_at = cron_expr.next_run(_dt.datetime.now().astimezone()).timestamp()
        else:
            next_run_at = ts + (interval * 60)

        fresh_context = bool(entry.get("fresh_context", False))
        fresh_context_interval_raw = entry.get("fresh_context_interval_minutes")
        try:
            fresh_context_interval = int(fresh_context_interval_raw) if fresh_context_interval_raw is not None else None
        except Exception:
            fresh_context_interval = None
        if fresh_context_interval is not None and fresh_context_interval < 1:
            fresh_context_interval = None

        entry_cwd = str(entry.get("cwd", "")).strip() or None
        adaptive_raw = entry.get("adaptive")
        adaptive: dict[str, Any] | None = None
        if isinstance(adaptive_raw, dict) and adaptive_raw.get("enabled"):
            adaptive = dict(adaptive_raw)
            adaptive["enabled"] = True

        preflight = str(entry.get("preflight", "")).strip() or None

        normalized.append({
            "id": prompt_id,
            "name": name,
            "prompt": prompt,
            "slash": slash,
            "cron": cron_str if cron_expr else None,
            "interval_minutes": interval,
            "enabled": True,
            "run_immediately_on_startup": run_immediately,
            "next_run_at": next_run_at,
            "fresh_context": fresh_context,
            "fresh_context_interval_minutes": fresh_context_interval,
            "next_clear_at": ts if fresh_context else None,
            "cwd": entry_cwd,
            "exclude_from_concurrency": bool(entry.get("exclude_from_concurrency", False)),
            "event_hook": event_hook,
            "event_hook_fallback": event_hook_fallback,
            "webhook": webhook,
            "adaptive": adaptive,
            "preflight": preflight,
            "scheduled": scheduled,
        })

    return normalized


class PeriodicScheduler:
    """定期プロンプトのスケジュール管理。唯一の dispatch gate。"""

    def __init__(
        self,
        session_mgr: SessionManager,
        entries: list[dict[str, Any]],
        semaphore: GlobalSemaphore | None = None,
        slot_monitor: "SlotMonitor | None" = None,
        workspace: str | None = None,
    ):
        self._session_mgr = session_mgr
        self._semaphore = semaphore
        self._slot_monitor = slot_monitor
        self._workspace = workspace or getattr(session_mgr, "_target_path", "") or ""
        self._entries: list[dict[str, Any]] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._node_budget_warned_at = 0.0
        self._hook_cache: dict[str, tuple[float, Any]] = {}
        self._hook_cache_lock = threading.Lock()
        self._external_queues: dict[str, collections.deque[str]] = {}
        # dispatch gate
        self._pending: list[dict[str, Any]] = []
        self._debouncer = RequestDebouncer()
        self._draining = False
        self._run_state = "run"
        self._reload_entries: list[dict[str, Any]] | None = None
        self._hook_quarantine: set[str] = set()
        self._active_count = 0
        self._active_ids: set[str] = set()
        self._inflight_ack_paths: set[str] = set()
        self._health: dict[str, Any] = {}
        self._input_recovery = False
        self._mem_ok_streak = 0
        self._mem_paused = False
        self._preflight_cache: dict[str, tuple[float, Any]] = {}
        self._set_entries(entries, allow_immediate_once=True)

    # ---- 設定 / 状態 -------------------------------------------------------

    def configure_runtime(
        self,
        *,
        workspace: str | None = None,
        health: dict[str, Any] | None = None,
    ) -> None:
        if workspace is not None:
            self._workspace = workspace
        if health is not None and isinstance(health, dict):
            self._health = dict(health)
            self._input_recovery = bool(health.get("input_recovery", False))

    def _release_slot(self, pane_id: str | None) -> None:
        if self._semaphore is not None and pane_id:
            self._semaphore.release(pane_id)

    def _update_entry(self, entry_id: str, **fields: Any) -> None:
        with self._lock:
            for e in self._entries:
                if e.get("id") == entry_id:
                    e.update(fields)
                    break

    def _find_entry(self, entry_id: str) -> dict[str, Any] | None:
        with self._lock:
            for e in self._entries:
                if e.get("id") == entry_id or e.get("name") == entry_id:
                    return e.copy()
        return None

    def _set_entries(self, entries: list[dict[str, Any]], allow_immediate_once: bool = False) -> None:
        normalized = validate_entries(entries, allow_immediate_once=allow_immediate_once)
        self._session_mgr.sync_entries(normalized)
        with self._lock:
            self._entries = normalized

    def set_entries(self, entries: list[dict[str, Any]]) -> bool:
        """エントリを transactional に設定する。失敗時は現行を維持して False。"""
        try:
            normalized = validate_entries(entries, allow_immediate_once=False)
        except Exception as exc:
            log.error("設定の検証に失敗したため現行設定を維持します: %s", exc)
            return False
        return self._apply_normalized_entries(normalized)

    def request_reload(self, entries: list[dict[str, Any]]) -> bool:
        """次 tick で一括交換する reload を要求する。検証失敗時は False。"""
        try:
            normalized = validate_entries(entries, allow_immediate_once=False)
        except Exception as exc:
            log.error("reload 検証に失敗したため現行設定を維持します: %s", exc)
            return False
        with self._lock:
            self._reload_entries = normalized
        log.info("event=config_reload_requested entries=%d", len(normalized))
        return True

    def _apply_normalized_entries(self, normalized: list[dict[str, Any]]) -> bool:
        with self._lock:
            old_by_id = {str(e.get("id")): e for e in self._entries}
            for e in normalized:
                old = old_by_id.get(str(e.get("id")))
                if old is None:
                    continue
                e["next_run_at"] = old.get("next_run_at", e["next_run_at"])
                if "next_clear_at" in old:
                    e["next_clear_at"] = old.get("next_clear_at")
            keep_ids = {str(e.get("id")) for e in normalized}
            self._pending = [
                r for r in self._pending
                if str(r.get("entry_id")) in keep_ids or r.get("source") in ("send", "inbox", "webhook")
            ]
            # 削除された entry の webhook キューを捨てる
            keep_keys = {_webhook_key(str(e.get("name", ""))) for e in normalized}
            self._external_queues = {
                k: v for k, v in self._external_queues.items() if k in keep_keys
            }
            self._entries = normalized
            # ID 変更は delete+add。quarantine は reload で解除。
            self._hook_quarantine.clear()
        self._session_mgr.sync_entries(normalized)
        _log_dispatch("config_reloaded", None, entries=len(normalized))
        return True

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._pending)

    def active_count(self) -> int:
        with self._lock:
            return self._active_count

    def run_state(self) -> str:
        return self._run_state

    # ---- pending queue -----------------------------------------------------

    def enqueue_request(self, req: dict[str, Any]) -> bool:
        """外部（inbox 等）から request を gate へ入れる。受付なら True。"""
        with self._lock:
            if self._draining:
                return False
        return self._accept_request(req)

    def has_pending_ack_path(self, path: str) -> bool:
        """inbox ファイルが既に pending / 処理中か。"""
        with self._lock:
            if path in self._inflight_ack_paths:
                return True
            for r in self._pending:
                ack = r.get("ack") or {}
                if ack.get("path") == path:
                    return True
        return False

    def _accept_request(self, req: dict[str, Any]) -> bool:
        """debounce / schedule coalesce のうえ pending へ入れる。"""
        if self._draining:
            return False
        dedupe = str(req.get("dedupe_key") or _dedupe_key(str(req.get("entry_id", "")), str(req.get("prompt", ""))))
        req["dedupe_key"] = dedupe
        if self._debouncer.is_duplicate(dedupe):
            _log_dispatch("request_debounced", req)
            return True  # 成功扱いで破棄

        source = str(req.get("source", ""))
        entry_id = str(req.get("entry_id", ""))
        with self._lock:
            if source == "schedule":
                for existing in self._pending:
                    if existing.get("source") == "schedule" and str(existing.get("entry_id")) == entry_id:
                        _log_dispatch("schedule_coalesced", req)
                        return True
            pri = _PRIORITY_ORDER.get(str(req.get("priority", "normal")), 1)
            if pri == 0:  # high → 先頭（同優先内 FIFO のため、先頭の high 群の末尾へ）
                idx = 0
                while idx < len(self._pending):
                    if _PRIORITY_ORDER.get(str(self._pending[idx].get("priority", "normal")), 1) == 0:
                        idx += 1
                    else:
                        break
                self._pending.insert(idx, req)
            else:
                self._pending.append(req)
        _log_dispatch("request_accepted", req)
        return True

    def _requeue_front(self, req: dict[str, Any]) -> None:
        """busy / slot 満杯時に保留へ戻す（先頭寄り・同一 priority 内）。"""
        pri = _PRIORITY_ORDER.get(str(req.get("priority", "normal")), 1)
        with self._lock:
            if pri == 0:
                self._pending.insert(0, req)
            else:
                # 先頭の high 群の直後へ（優先を落とさない）
                idx = 0
                while idx < len(self._pending):
                    if _PRIORITY_ORDER.get(str(self._pending[idx].get("priority", "normal")), 1) == 0:
                        idx += 1
                    else:
                        break
                self._pending.insert(idx, req)
        _log_dispatch("dispatch_deferred", req)

    # ---- webhook -----------------------------------------------------------

    @staticmethod
    def _normalize_webhook(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        return {
            "hook": str(raw.get("hook", "")).strip() or None,
            "secret": str(raw.get("secret", "")),
            "secret_header": str(raw.get("secret_header", "")).strip() or None,
        }

    def _find_entry_by_key_locked(self, key: str) -> dict[str, Any] | None:
        for e in self._entries:
            if _webhook_key(str(e.get("name", ""))) == key:
                return e
        return None

    def resolve_webhook_route(self, name: str) -> dict[str, Any] | None:
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
        """外部（webhook 等）から name 宛の完成プロンプトをキューに積む。"""
        key = _webhook_key(name)
        with self._lock:
            if self._draining:
                return False
            entry = self._find_entry_by_key_locked(key)
            if entry is None:
                return False
            q = self._external_queues.setdefault(
                key, collections.deque(maxlen=_WEBHOOK_QUEUE_MAX))
            if len(q) >= _WEBHOOK_QUEUE_MAX:
                log.warning("[%s] 外部イベントキューが上限 (%d) に達したため最古を破棄します。",
                            entry.get("name", key), _WEBHOOK_QUEUE_MAX)
            q.append(prompt_text)
            return True

    def _drain_external_to_pending(self) -> None:
        """webhook deque → pending（受付時に deque から外す。busy 時は pending に留める）。"""
        with self._lock:
            items: list[tuple[dict[str, Any], str]] = []
            for entry in self._entries:
                key = _webhook_key(str(entry.get("name", "")))
                q = self._external_queues.get(key)
                if not q:
                    continue
                # 1 tick あたり entry ごと 1 件（従来どおり）
                prompt_text = q.popleft()
                items.append((entry.copy(), prompt_text))
        for entry, prompt_text in items:
            req = make_dispatch_request(
                source="webhook",
                entry_id=str(entry.get("id", "")),
                prompt=prompt_text,
                cwd=entry.get("cwd"),
                priority="normal",
                meta={"entry_name": entry.get("name"), "_should_clear": False},
            )
            if not self._accept_request(req):
                # draining 等で受けられない場合は deque へ戻す
                key = _webhook_key(str(entry.get("name", "")))
                with self._lock:
                    self._external_queues.setdefault(
                        key, collections.deque(maxlen=_WEBHOOK_QUEUE_MAX)
                    ).appendleft(prompt_text)

    def _drain_external_one(self, entry: dict[str, Any]) -> bool:
        """互換: entry の外部キューを 1 件処理する（テスト・旧経路）。

        空なら False。session 未準備・スロット上限時はプロンプトを積み直す。
        """
        key = _webhook_key(str(entry.get("name", "")))
        with self._lock:
            q = self._external_queues.get(key)
            if not q:
                return False
            prompt_text = q.popleft()

        req = make_dispatch_request(
            source="webhook",
            entry_id=str(entry.get("id", "")),
            prompt=prompt_text,
            cwd=entry.get("cwd"),
            meta={"entry_name": entry.get("name"), "_should_clear": False},
        )
        result = self._try_dispatch_request(req, entry_hint=entry)
        if result == "defer":
            with self._lock:
                self._external_queues.setdefault(
                    key, collections.deque(maxlen=_WEBHOOK_QUEUE_MAX)
                ).appendleft(prompt_text)
        return True

    # ---- hooks / preflight -------------------------------------------------

    def _load_hook_module(self, hook_path: Path) -> Any | None:
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

    def _call_hook_check(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        """event_hook の check() を呼び、正規化済み dict（prompt/cwd/vars）または None を返す。"""
        hook_path = Path(os.path.expanduser(entry["event_hook"])).resolve()
        name = str(entry.get("name", ""))
        hook_key = str(hook_path)
        if hook_key in self._hook_quarantine:
            log.warning("[%s] event_hook は timeout 隔離中のためスキップします: %s", name, hook_path)
            return None

        module = self._load_hook_module(hook_path)
        if module is None:
            return None

        check_fn = getattr(module, "check", None)
        if not callable(check_fn):
            log.warning("[%s] event_hook に check() 関数が定義されていません: %s", name, hook_path)
            return None

        hook_config = {
            "name": name,
            "entry_id": entry.get("id"),
            "event_hook_fallback": bool(entry.get("event_hook_fallback")),
            "cwd": entry.get("cwd"),
        }
        fallback_flag = "1" if entry.get("event_hook_fallback") else "0"
        env_overrides = {
            "AGENT_LOOP_EVENT_HOOK_FALLBACK": fallback_flag,
            "AGENT_LOOP_PROMPT_NAME": name,
        }
        previous = {k: os.environ.get(k) for k in env_overrides}
        os.environ.update(env_overrides)

        result_box: dict[str, Any] = {"done": False, "value": None, "error": None}

        def _invoke() -> None:
            try:
                # 1 引数 → 0 引数の順で試す（arity 不明時）
                try:
                    nparams = len(inspect.signature(check_fn).parameters)
                except (TypeError, ValueError):
                    nparams = -1
                if nparams == 0:
                    result_box["value"] = check_fn()
                elif nparams == 1:
                    result_box["value"] = check_fn(hook_config)
                else:
                    try:
                        result_box["value"] = check_fn(hook_config)
                    except TypeError:
                        result_box["value"] = check_fn()
            except Exception as exc:
                result_box["error"] = exc
            finally:
                result_box["done"] = True

        try:
            t = threading.Thread(target=_invoke, name=f"hook-check-{name}", daemon=True)
            t.start()
            t.join(timeout=_HOOK_TIMEOUT_SEC)
            if not result_box["done"]:
                self._hook_quarantine.add(hook_key)
                _log_dispatch("hook_timeout", None, entry_id=entry.get("id"), hook=hook_key)
                log.error("[%s] check() が %.0f 秒でタイムアウト。隔離します: %s",
                          name, _HOOK_TIMEOUT_SEC, hook_path)

                def _clear_quarantine() -> None:
                    t.join()
                    self._hook_quarantine.discard(hook_key)
                    log.info("[%s] event_hook の隔離を解除しました: %s", name, hook_path)

                threading.Thread(target=_clear_quarantine, daemon=True).start()
                return None
        finally:
            for k, prev in previous.items():
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev

        if result_box["error"] is not None:
            log.error("[%s] check() でエラーが発生しました: %s", name, result_box["error"], exc_info=True)
            return None

        return self._normalize_hook_result(result_box["value"], name)

    def _normalize_hook_result(self, result: Any, name: str) -> dict[str, Any] | None:
        if result is None:
            return None
        if isinstance(result, str):
            text = result.strip()
            return {"prompt": text} if text else None
        if isinstance(result, dict):
            prompt = result.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                log.warning("[%s] check() dict に有効な prompt がありません: %r", name, result)
                return None
            out: dict[str, Any] = {"prompt": prompt}
            cwd = result.get("cwd")
            if cwd is not None:
                cwd_path = Path(os.path.expanduser(str(cwd)))
                if not cwd_path.is_dir():
                    log.warning("[%s] check() の cwd が存在しないため却下します: %s", name, cwd)
                    return None
                out["cwd"] = str(cwd_path)
            vars_map = result.get("vars")
            if isinstance(vars_map, dict):
                try:
                    out["prompt"] = prompt.format_map(_SafeDict(vars_map))
                except Exception as exc:
                    log.warning("[%s] check() vars の format に失敗しました: %s", name, exc)
                    return None
            return out
        log.warning("[%s] check() の戻り値が不正です: %r", name, result)
        return None

    def _call_hook_ack(self, entry: dict[str, Any]) -> None:
        hook_path = Path(os.path.expanduser(entry["event_hook"])).resolve()
        module = self._load_hook_module(hook_path)
        ack_fn = getattr(module, "ack", None) if module is not None else None
        if not callable(ack_fn):
            return
        try:
            ack_fn()
        except Exception as exc:
            log.error("[%s] ack() でエラーが発生しました: %s",
                      entry.get("name", ""), exc, exc_info=True)

    def _run_preflight(self, req: dict[str, Any], entry: dict[str, Any]) -> bool:
        """preflight.should_dispatch → False でブロック。例外/timeout は fail-open。"""
        path_raw = entry.get("preflight") or req.get("meta", {}).get("preflight")
        if not path_raw:
            return True
        path = Path(os.path.expanduser(str(path_raw))).resolve()
        if not path.is_file():
            log.warning("preflight が見つかりません（fail-open）: %s", path)
            return True

        try:
            mtime = path.stat().st_mtime
            cached = self._preflight_cache.get(str(path))
            if cached and cached[0] == mtime:
                module = cached[1]
            else:
                spec = importlib.util.spec_from_file_location("kiro_loop_preflight", path)
                if spec is None or spec.loader is None:
                    log.warning("preflight のロードに失敗（fail-open）: %s", path)
                    return True
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._preflight_cache[str(path)] = (mtime, module)
        except Exception as exc:
            log.warning("preflight ロード例外（fail-open）: %s", exc)
            return True

        fn = getattr(module, "should_dispatch", None)
        if not callable(fn):
            log.warning("preflight に should_dispatch がありません（fail-open）: %s", path)
            return True

        box: dict[str, Any] = {"done": False, "value": True, "error": None}

        def _invoke() -> None:
            try:
                box["value"] = bool(fn(req, {"path": str(path), "entry_id": entry.get("id")}))
            except Exception as exc:
                box["error"] = exc
            finally:
                box["done"] = True

        t = threading.Thread(target=_invoke, name="preflight", daemon=True)
        t.start()
        t.join(timeout=_PREFLIGHT_TIMEOUT_SEC)
        if not box["done"]:
            log.warning("preflight がタイムアウト（fail-open）: %s", path)
            return True
        if box["error"] is not None:
            log.warning("preflight 例外（fail-open）: %s", box["error"])
            return True
        return bool(box["value"])

    # ---- slot / schedule helpers -------------------------------------------

    def _is_in_cooldown(self, entry: dict[str, Any], pane_id: str) -> bool:
        if self._semaphore is None:
            return False
        remaining = self._semaphore.cooldown_remaining(pane_id)
        if remaining > 0:
            log.info(
                "[%s] クールダウン中のため実行を延期します (残り %.0f 秒)。",
                entry.get("name", ""), remaining,
            )
            return True
        return False

    def _next_run_at_for_entry(self, entry: dict[str, Any], *, outcome: str = "idle") -> float:
        cron_str = entry.get("cron")
        if cron_str:
            try:
                return CronExpression(cron_str).next_run(_dt.datetime.now()).timestamp()
            except Exception as exc:
                log.error("[%s] cron 次回時刻計算エラー: %s", entry.get("name", ""), exc)
                return time.time() + 60

        adaptive = entry.get("adaptive")
        if isinstance(adaptive, dict) and adaptive.get("enabled") and not cron_str:
            state = load_adaptive_state(str(entry.get("id", "")))
            interval, new_state = next_adaptive_interval(adaptive, state, outcome=outcome)
            save_adaptive_state(str(entry.get("id", "")), new_state)
            return time.time() + interval

        interval_minutes = max(int(entry.get("interval_minutes", 1)), 1)
        return time.time() + interval_minutes * 60

    def _try_acquire_slot(self, entry: dict[str, Any], pane_id: str) -> str:
        """'ok' | 'defer'。busy/上限時は defer（next_run を進めない）。"""
        assert self._semaphore is not None
        name = str(entry.get("name", ""))

        elapsed = self._semaphore.slot_elapsed(pane_id)
        if elapsed is not None:
            if elapsed < self._semaphore.slot_timeout:
                log.info(
                    "[%s] 前回の実行が完了待ちです (経過 %.0f秒 / 猶予 %d秒)。保留します。",
                    name, elapsed, self._semaphore.slot_timeout,
                )
                return "defer"
            log.warning(
                "[%s] 猶予時間 (%d秒) を超過。スロットを強制解放します。",
                name, self._semaphore.slot_timeout,
            )
            if self._slot_monitor is not None:
                self._slot_monitor.untrack(pane_id)
            self._semaphore.release(pane_id)

        if self._is_in_cooldown(entry, pane_id):
            return "defer"

        if not self._semaphore.acquire(pane_id):
            log.warning(
                "[%s] 同時実行数が上限 (%d) に達しています。保留します。",
                name, self._semaphore.max_concurrent,
            )
            return "defer"
        return "ok"

    # 旧テスト互換エイリアス
    def _acquire_slot(self, entry: dict[str, Any], pane_id: str) -> bool:
        return self._try_acquire_slot(entry, pane_id) == "ok"

    # ---- dispatch ----------------------------------------------------------

    def _dispatch_prompt(self, entry: dict[str, Any], pane_id: str | None,
                         *, req: dict[str, Any] | None = None) -> bool:
        name = str(entry.get("name", ""))
        prompt_id = str(entry.get("id", ""))
        prompt = str(entry.get("prompt", ""))
        should_clear = bool(entry.get("_should_clear", False))
        fresh_context_interval = entry.get("fresh_context_interval_minutes")

        log.info("[%s] プロンプトを実行します。", name)
        _log_dispatch("dispatch_started", req)
        try:
            if should_clear:
                clear_cmd = _CLI_PROFILE.clear_command
                if not clear_cmd:
                    log.warning("[%s] fresh_context: agent_cli '%s' はクリアコマンドを持たないためスキップします。",
                                name, _CLI_PROFILE.name)
                else:
                    log.info("[%s] fresh_context: コンテキストをクリアします。", name)
                    if not self._session_mgr.send_prompt(prompt_id, clear_cmd):
                        log.warning("[%s] %s の送信に失敗しました。スキップします。", name, clear_cmd)
                        self._release_slot(pane_id)
                        return False
                    time.sleep(2)
                if fresh_context_interval is not None:
                    new_next_clear_at = time.time() + (int(fresh_context_interval) * 60)
                    self._update_entry(str(entry.get("id", "")), next_clear_at=new_next_clear_at)

            for raw_line in (entry.get("slash") or []):
                line = _CLI_PROFILE.rewrite_slash(raw_line)
                log.info("[%s] slash を送信します: %s", name, line)
                if not self._session_mgr.send_prompt(prompt_id, line):
                    log.warning("[%s] slash（%s）の送信に失敗しました。スキップします。", name, line)
                    self._release_slot(pane_id)
                    return False
                time.sleep(_SLASH_SEND_INTERVAL_SEC)

            ok = self._session_mgr.send_prompt(prompt_id, prompt) if prompt else True
            if ok and prompt and self._input_recovery and pane_id:
                ok = self._maybe_input_recovery(pane_id, prompt_id, prompt, req)
            if ok:
                if self._slot_monitor is not None and pane_id:
                    on_complete = None
                    meta = (req or {}).get("meta") or {}
                    if meta.get("inbox_cleanup"):
                        pid = str(meta.get("prompt_id") or prompt_id)
                        on_complete = lambda: self._session_mgr.remove_session(
                            pid, owner="inbox", pane_id=pane_id
                        )
                    self._slot_monitor.track(pane_id, on_complete=on_complete)
                _log_dispatch("dispatch_sent", req)
                return True
            else:
                self._release_slot(pane_id)
                if not self._stop_event.is_set():
                    log.warning("[%s] 送信失敗。ペイン再起動を試みます。", name)
                    try:
                        self._session_mgr.restart_pane(prompt_id)
                    except RuntimeError as exc:
                        log.error("[%s] 再起動失敗: %s", name, exc)
                _log_dispatch("dispatch_failed", req)
                return False
        except Exception as exc:
            self._release_slot(pane_id)
            log.error("[%s] 予期しないエラー: %s", name, exc, exc_info=True)
            _log_dispatch("dispatch_failed", req)
            return False

    def _maybe_input_recovery(
        self,
        pane_id: str,
        prompt_id: str,
        prompt: str,
        req: dict[str, Any] | None,
    ) -> bool:
        """送信後も ready・busy 未観測・末尾一致・未再送なら C-u + 1 回再送。"""
        meta = (req or {}).setdefault("meta", {}) if req is not None else {}
        if meta.get("_input_recovered"):
            return True
        time.sleep(_INPUT_RECOVERY_WAIT_SEC)
        content = _capture_pane(pane_id)
        if not _CLI_PROFILE.is_ready(content):
            return True  # busy 遷移 or 不明 → 再送しない
        # busy を一度でも見たら再送しない（ready のままなのでここに来る時点では未観測）
        tail = SessionManager.capture_visible_input_tail(pane_id)
        single = " ".join(prompt.splitlines()).strip()
        if single not in tail and prompt.strip() not in tail:
            return True  # 判定不能
        log.info("入力残留を検知したため C-u 後に 1 回再送します (pane=%s)", pane_id)
        _tmux_cmd("send-keys", "-t", pane_id, "C-u")
        time.sleep(0.1)
        meta["_input_recovered"] = True
        return self._session_mgr.send_prompt(prompt_id, prompt)

    def _try_dispatch_request(
        self,
        req: dict[str, Any],
        *,
        entry_hint: dict[str, Any] | None = None,
    ) -> str:
        """'done' | 'defer' | 'discard'。"""
        entry = entry_hint or self._find_entry(str(req.get("entry_id", "")))
        source = str(req.get("source", ""))

        if entry is None and source == "send":
            return self._try_dispatch_adhoc(req)

        if entry is None and source == "inbox":
            meta = req.get("meta") or {}
            entry = {
                "id": str(meta.get("prompt_id") or req.get("entry_id")),
                "name": str(meta.get("session_name") or req.get("entry_id")),
                "prompt": str(req.get("prompt", "")),
                "exclude_from_concurrency": False,
                "slash": [],
                "fresh_context": False,
            }
        elif entry is None:
            log.warning("dispatch: エントリが見つかりません entry_id=%s", req.get("entry_id"))
            self._finalize_ack(req, success=False)
            return "discard"

        if not self._run_preflight(req, entry):
            _log_dispatch("dispatch_blocked_preflight", req)
            # inbox 等は再投入ループを避けるため消費する（hook ack は呼ばない）
            self._finalize_ack(req, success=True)
            return "discard"

        name = str(entry.get("name", ""))
        prompt_id = str(entry.get("id", ""))
        owner = "inbox" if source == "inbox" else "scheduled"
        if source == "inbox":
            prompt_id = str((req.get("meta") or {}).get("prompt_id") or f"inbox-{req.get('id', '')[:8]}")
            name = str((req.get("meta") or {}).get("session_name") or name)

        cwd = req.get("cwd") or entry.get("cwd")
        dispatch_entry = dict(entry)
        dispatch_entry["id"] = prompt_id
        dispatch_entry["name"] = name
        dispatch_entry["prompt"] = str(req.get("prompt", ""))
        if cwd:
            dispatch_entry["cwd"] = cwd
        dispatch_entry["_should_clear"] = bool(
            (req.get("meta") or {}).get("_should_clear", False)
        )

        if not self._session_mgr.ensure_session(prompt_id, name, owner=owner):
            log.warning("[%s] 対応セッションの準備に失敗したため保留します。", name)
            return "defer"

        # cwd 上書き（request 指定時）
        if cwd:
            with self._session_mgr._lock:
                self._session_mgr._prompt_cwds[prompt_id] = str(cwd)

        pane_id: str | None = None
        exclude = bool(entry.get("exclude_from_concurrency", False))
        if self._semaphore is not None and not exclude:
            pane_id = self._session_mgr.get_pane_id(prompt_id)
            if pane_id:
                acq = self._try_acquire_slot(entry, pane_id)
                if acq == "defer":
                    return "defer"

        ack_path = str((req.get("ack") or {}).get("path") or "")
        with self._lock:
            self._active_count += 1
            self._active_ids.add(str(req.get("id")))
            if ack_path:
                self._inflight_ack_paths.add(ack_path)
        try:
            ok = self._dispatch_prompt(dispatch_entry, pane_id, req=req)
        finally:
            with self._lock:
                self._active_count = max(0, self._active_count - 1)
                self._active_ids.discard(str(req.get("id")))
                if ack_path and not ok:
                    # defer 時は pending へ戻るので path 監視は pending 側へ
                    self._inflight_ack_paths.discard(ack_path)

        if ok:
            if source == "hook" and entry.get("event_hook"):
                self._call_hook_ack(entry)
            self._finalize_ack(req, success=True)
            with self._lock:
                if ack_path:
                    self._inflight_ack_paths.discard(ack_path)
            _log_dispatch("dispatch_completed", req)
            if entry.get("adaptive") and not entry.get("cron"):
                self._update_entry(
                    str(entry.get("id")),
                    next_run_at=self._next_run_at_for_entry(entry, outcome="activity"),
                )
            return "done"

        # 送信失敗は webhook と同様 requeue（defer）して消失を避ける
        return "defer"

    def _try_dispatch_adhoc(self, req: dict[str, Any]) -> str:
        """スケジュール外 send（pane/session 指定）。"""
        target = str((req.get("meta") or {}).get("target") or req.get("entry_id") or "")
        if not target:
            return "discard"
        prompt = str(req.get("prompt", ""))
        # 管理下ペインなら entry id 解決
        pane_id = _resolve_target_pane(target) if not target.startswith("%") else target
        if target.startswith("%"):
            pane_id = target
        if pane_id is None:
            pane_id = target if target.startswith("%") else _resolve_target_pane(target)
        if pane_id is None:
            log.warning("adhoc send: ペインを解決できません target=%s", target)
            return "defer"

        if self._semaphore is not None:
            elapsed = self._semaphore.slot_elapsed(pane_id)
            if elapsed is not None and elapsed < self._semaphore.slot_timeout:
                return "defer"
            if self._semaphore.cooldown_remaining(pane_id) > 0:
                return "defer"
            if not self._semaphore.acquire(pane_id):
                return "defer"

        ok = send_prompt_to_session(pane_id, prompt)
        if ok:
            if self._slot_monitor is not None:
                self._slot_monitor.track(pane_id)
            self._finalize_ack(req, success=True)
            _log_dispatch("dispatch_completed", req)
            return "done"
        self._release_slot(pane_id)
        return "defer"

    def _finalize_ack(self, req: dict[str, Any], *, success: bool) -> None:
        ack = req.get("ack")
        if not isinstance(ack, dict):
            return
        kind = ack.get("kind")
        if kind == "inbox_file" and success:
            path = Path(str(ack.get("path", "")))
            processed = Path(str(ack.get("processed_dir", "")))
            if path.is_file() and processed:
                try:
                    processed.mkdir(parents=True, exist_ok=True)
                    dest = processed / path.name
                    path.rename(dest)
                except OSError as exc:
                    log.warning("inbox アーカイブ移動エラー (%s): %s", path, exc)

    # ---- lifecycle / commands ----------------------------------------------

    def _lifecycle_gate(self, now: float) -> str:
        """'stop' | 'drain_exit' | 'pause' | 'run'。優先順位どおり。"""
        lifecycle = _control_lifecycle()
        nb = _node_budget_state()
        local_paused = load_local_pause(self._workspace) if self._workspace else False
        if self._mem_paused:
            local_paused = True

        if lifecycle == "stop":
            self._run_state = "stopped"
            return "stop"

        if self._draining:
            self._run_state = "draining"
            with self._lock:
                active = self._active_count
            if active == 0:
                return "drain_exit"
            return "pause"  # 新規受付なし・処理中待ち

        if lifecycle == "pause":
            self._run_state = "paused"
            if now - self._node_budget_warned_at > 600:
                self._node_budget_warned_at = now
                log.warning("[agent-control] 管理面により lifecycle=pause 指定です。"
                            "定期送信を停止中 — dashboard の全体設定から "
                            "run に戻してください。")
            return "pause"

        if nb and nb["exceeded"]:
            if nb.get("on_exhausted") == "stop":
                self._run_state = "stopped"
                unit = "トークン" if nb.get("token_limit") else "実行時間"
                log.warning(
                    "[node-budget] このノードの%s予算を超過しました"
                    "（%.1f分 / %.0ftok・period=%s・on_exhausted=stop）。"
                    "agent-loop を停止します — 再開は dashboard の定常業務から"
                    "再起動してください。",
                    unit, nb["spent_min"], nb["spent_tokens"], nb["period"],
                )
                _write_stopped_reason("node-budget")
                return "stop"
            self._run_state = "paused"
            if now - self._node_budget_warned_at > 600:
                self._node_budget_warned_at = now
                log.warning(
                    "[node-budget] このノードの予算を超過しています"
                    "（%.1f分/%.0f分・%.0ftok/%.0ftok・period=%s）。定期送信を停止中 — "
                    "上限を上げる（dashboard の全体設定 / agent-amigos budget node）か"
                    "期間の更新を待ってください。",
                    nb["spent_min"], nb["limit_min"], nb["spent_tokens"],
                    nb["token_limit"], nb["period"],
                )
            return "pause"

        if local_paused:
            self._run_state = "paused"
            return "pause"

        self._run_state = "run"
        return "run"

    def _handle_loop_commands(self) -> None:
        cmds = drain_loop_commands(os.getpid())
        for cmd in cmds:
            name = str(cmd.get("command", ""))
            payload = cmd.get("payload") or {}
            if name == "pause":
                set_local_pause(self._workspace, True)
                _log_dispatch("local_pause_changed", None, paused=True)
            elif name == "resume":
                set_local_pause(self._workspace, False)
                self._mem_paused = False
                self._mem_ok_streak = 0
                _log_dispatch("local_pause_changed", None, paused=False)
            elif name == "drain":
                self._enter_drain()
            elif name == "reload":
                # payload に entries があればそれ、なければ呼び出し側が request_reload 済み
                entries = payload.get("entries")
                if isinstance(entries, list):
                    self.request_reload(entries)
            elif name == "cancel":
                self._cancel_target(str(payload.get("target", "")))

    def _enter_drain(self) -> None:
        self._draining = True
        self._run_state = "draining"
        with self._lock:
            discarded = len(self._pending)
            self._pending.clear()
            # 未開始 send-requests を破棄
            for req in load_send_requests():
                remove_send_request_file(req)
                discarded += 1
            self._external_queues.clear()
        log.info("event=drain_started discarded=%d", discarded)

    def _cancel_target(self, target: str) -> bool:
        if not target:
            return False
        # entry id / name / pane
        entry = self._find_entry(target)
        prompt_id = str(entry["id"]) if entry else None
        pane_id: str | None = None
        if entry:
            pane_id = self._session_mgr.get_pane_id(prompt_id)  # type: ignore[arg-type]
        if target.startswith("%"):
            pane_id = target
            # pane から prompt_id を逆引き
            with self._session_mgr._lock:
                for pid, p in self._session_mgr._panes.items():
                    if p == target:
                        prompt_id = pid
                        break
        if prompt_id is None and pane_id is None:
            log.warning("cancel: 不明なターゲットです: %s", target)
            return False
        if prompt_id:
            if self._slot_monitor is not None and pane_id:
                self._slot_monitor.untrack(pane_id)
            if self._semaphore is not None and pane_id:
                self._semaphore.release(pane_id)
            self._session_mgr.cleanup_managed_pane(prompt_id)
            log.info("event=cancel_done target=%s prompt_id=%s", target, prompt_id)
            return True
        return False

    def _drain_send_requests(self) -> None:
        for req in load_send_requests():
            if self._draining:
                remove_send_request_file(req)
                continue
            # 受付時にディスクから削除（at-most-once）
            if self._accept_request(req):
                remove_send_request_file(req)
            else:
                break

    def _fire_due_schedules(self, now: float) -> None:
        with self._lock:
            entries = [e.copy() for e in self._entries]
        for entry in entries:
            if not entry.get("enabled", True):
                continue
            if not entry.get("scheduled", True):
                continue
            if now < float(entry.get("next_run_at", now)):
                continue

            prompt_id = str(entry.get("id", ""))
            name = str(entry.get("name", ""))

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

            prompt_text = str(entry.get("prompt", ""))
            cwd = entry.get("cwd")
            source = "schedule"

            if entry.get("event_hook"):
                hook_result = self._call_hook_check(entry)
                if hook_result is None:
                    # idle / スキップ — adaptive は idle
                    outcome = "idle"
                    self._update_entry(prompt_id, next_run_at=self._next_run_at_for_entry(entry, outcome=outcome))
                    continue
                prompt_text = str(hook_result["prompt"])
                if hook_result.get("cwd"):
                    cwd = hook_result["cwd"]
                source = "hook"

            req = make_dispatch_request(
                source=source,
                entry_id=prompt_id,
                prompt=prompt_text,
                cwd=cwd,
                priority="normal",
                meta={
                    "entry_name": name,
                    "_should_clear": should_clear,
                },
            )
            self._accept_request(req)
            # スケジュール時刻は受付時に進める（保留中でも二重発火しない）。coalesce で 1 件維持。
            outcome = "activity" if source == "hook" else "idle"
            # 実際の activity は送信成功時に再調整。ここでは次回枠を確保。
            self._update_entry(prompt_id, next_run_at=self._next_run_at_for_entry(entry, outcome="idle"))

    def _process_pending(self) -> None:
        if self._draining or self._run_state == "paused":
            return
        while True:
            with self._lock:
                if not self._pending:
                    return
                req = self._pending.pop(0)

            result = self._try_dispatch_request(req)
            if result == "defer":
                self._requeue_front(req)
                return  # 次 tick で再試行

    def _write_loop_state_extras(self) -> None:
        extras = {
            "run_state": self._run_state,
            "queue_depth": self.queue_depth(),
            "active_count": self.active_count(),
            "health": {
                "mem_paused": self._mem_paused,
                "input_recovery": self._input_recovery,
                "freeze_timeout_seconds": int(self._health.get("freeze_timeout_seconds", 0) or 0),
            },
        }
        writer = getattr(self._session_mgr, "set_state_extras", None)
        if callable(writer):
            writer(extras)

    # ---- main loop ---------------------------------------------------------

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
            lifecycle = _control_lifecycle()
            nb = _node_budget_state()
            _write_status(lifecycle=lifecycle, budget=nb)

            # transactional reload（次 tick）
            with self._lock:
                pending_reload = self._reload_entries
                self._reload_entries = None
            if pending_reload is not None:
                self._apply_normalized_entries(pending_reload)

            self._handle_loop_commands()
            gate = self._lifecycle_gate(now)

            if gate == "stop":
                log.warning("[agent-control] 管理面により lifecycle=stop 指定です。"
                            "agent-loop を停止します。")
                _write_stopped_reason("agent-control:stop")
                self._request_shutdown()
                return

            if gate == "drain_exit":
                log.info("drain 完了: 実行中リクエストが無くなったため停止します。")
                self._request_shutdown()
                return

            self._write_loop_state_extras()

            if gate == "pause":
                continue

            # run
            self._drain_send_requests()
            self._drain_external_to_pending()
            self._fire_due_schedules(now)
            self._process_pending()
            self._write_loop_state_extras()

    def _request_shutdown(self) -> None:
        self._stop_event.set()
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except (OSError, ValueError, AttributeError):
            pass

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ---- health helpers（monitor から呼ぶ） --------------------------------

    def check_memory_pressure(self) -> None:
        """空きメモリ監視。閾値未満で local pause、連続 2 回 OK で自動 resume。"""
        min_free = int(self._health.get("min_free_memory_mb", 0) or 0)
        if min_free <= 0:
            return
        free_mb = _get_free_memory_mb()
        if free_mb is None:
            return
        if free_mb < min_free:
            self._mem_ok_streak = 0
            if not self._mem_paused:
                self._mem_paused = True
                log.warning(
                    "空きメモリ不足のため local pause します (free=%sMB < %sMB)",
                    free_mb, min_free,
                )
                _log_dispatch("local_pause_changed", None, paused=True, reason="memory")
        else:
            if self._mem_paused:
                self._mem_ok_streak += 1
                if self._mem_ok_streak >= 2:
                    self._mem_paused = False
                    self._mem_ok_streak = 0
                    log.info("空きメモリが回復したため memory pause を解除します (free=%sMB)", free_mb)
                    _log_dispatch("local_pause_changed", None, paused=False, reason="memory")
            else:
                self._mem_ok_streak = 0

    def check_pane_rss(self) -> None:
        max_rss = int(self._health.get("max_pane_rss_mb", 0) or 0)
        if max_rss <= 0:
            return
        with self._session_mgr._lock:
            items = list(self._session_mgr._panes.items())
        for prompt_id, pane_id in items:
            pid = self._session_mgr.get_pane_pid(pane_id) if hasattr(self._session_mgr, "get_pane_pid") else None
            if pid is None:
                # best-effort: tmux pane pid
                r = _tmux_cmd("display-message", "-p", "-t", pane_id, "#{pane_pid}")
                if r.returncode != 0 or not r.stdout.strip():
                    continue
                try:
                    pid = int(r.stdout.strip())
                except ValueError:
                    continue
            rss = _get_process_rss_mb(pid)
            if rss is None:
                continue
            if rss <= max_rss:
                continue
            content = _capture_pane(pane_id)
            if not _CLI_PROFILE.is_ready(content):
                log.warning(
                    "ペイン RSS 超過ですが busy のため再起動を延期します (pane=%s rss=%sMB)",
                    pane_id, rss,
                )
                continue
            log.warning(
                "ペイン RSS 超過のため ready pane を再起動します (pane=%s rss=%sMB > %sMB)",
                pane_id, rss, max_rss,
            )
            try:
                self._session_mgr.restart_pane(prompt_id)
            except RuntimeError as exc:
                log.error("RSS 再起動失敗: %s", exc)


def _get_free_memory_mb() -> float | None:
    """OS の空きメモリ (MB)。取得不能なら None。"""
    try:
        if sys.platform == "linux":
            text = Path("/proc/meminfo").read_text(encoding="utf-8")
            info = {}
            for line in text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip()] = v.strip()
            # MemAvailable 優先
            raw = info.get("MemAvailable") or info.get("MemFree")
            if not raw:
                return None
            kb = float(raw.split()[0])
            return kb / 1024.0
        if sys.platform == "darwin":
            r = subprocess.run(["vm_stat"], capture_output=True, text=True, check=False)
            if r.returncode != 0:
                return None
            page_size = 4096
            free_pages = 0
            for line in r.stdout.splitlines():
                if "page size of" in line:
                    parts = line.split()
                    for p in parts:
                        if p.isdigit():
                            page_size = int(p)
                if line.startswith("Pages free:") or line.startswith("Pages speculative:"):
                    num = line.split(":")[-1].strip().rstrip(".")
                    free_pages += int(num)
            return free_pages * page_size / (1024 * 1024)
    except Exception:
        return None
    return None


def _get_process_rss_mb(pid: int) -> float | None:
    try:
        r = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True, text=True, check=False,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return float(r.stdout.strip()) / 1024.0
    except Exception:
        return None
