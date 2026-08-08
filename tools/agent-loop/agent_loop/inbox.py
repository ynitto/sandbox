from __future__ import annotations
# inbox.py — エージェント間メッセージ受信ウォッチャー。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# エージェント間メッセージ受信ウォッチャー
# ---------------------------------------------------------------------------

class InboxWatcher:
    """エージェント間メッセージ受信スレッド。

    メッセージファイル: ~/.kiro/agents/<agent_name>/inbox/<timestamp>_<uuid>.json
    処理済みアーカイブ: ~/.kiro/agents/<agent_name>/inbox/.processed/

    配送は PeriodicScheduler の dispatch gate 経由。tmux 送信成功後に .processed/ へ移動する。
    """

    def __init__(
        self,
        agent_name: str,
        session_mgr: "SessionManager",
        semaphore: "GlobalSemaphore | None" = None,
        slot_monitor: "SlotMonitor | None" = None,
        poll_interval: int = 5,
        scheduler: "PeriodicScheduler | None" = None,
    ) -> None:
        self._agent_name = agent_name
        self._session_mgr = session_mgr
        self._semaphore = semaphore
        self._slot_monitor = slot_monitor
        self._scheduler = scheduler
        self._poll_interval = poll_interval
        self._inbox_dir = _AGENTS_DIR / agent_name / "inbox"
        self._processed_dir = self._inbox_dir / ".processed"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._inbox_dir.mkdir(parents=True, exist_ok=True)
        self._processed_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._run_loop,
            name="inbox-watcher",
            daemon=True,
        )
        self._thread.start()
        log.info("[InboxWatcher] 起動しました (agent=%s): %s", self._agent_name, self._inbox_dir)

    def stop(self) -> None:
        self._stop_event.set()

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self._poll_interval):
            try:
                self._check_inbox()
            except Exception as exc:
                log.error("[InboxWatcher] ポーリングエラー: %s", exc, exc_info=True)

    def _check_inbox(self) -> None:
        """受信ボックスの未処理メッセージを走査して enqueue する。"""
        msg_files = sorted(self._inbox_dir.glob("*.json"))
        for msg_file in msg_files:
            if self._scheduler is not None and self._scheduler.has_pending_ack_path(str(msg_file)):
                continue
            try:
                data = json.loads(msg_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("[InboxWatcher] メッセージ読み込みエラー (%s): %s", msg_file.name, exc)
                continue

            if self._enqueue_message(data, msg_file):
                log.info(
                    "[InboxWatcher] メッセージをキューへ投入: from=%s subject=%r",
                    data.get("from", "?"),
                    data.get("subject", ""),
                )
            else:
                log.debug("[InboxWatcher] メッセージ保留中 (drain/busy): %s", msg_file.name)

    def _enqueue_message(self, data: dict[str, Any], msg_file: Path) -> bool:
        """Scheduler gate へ投入する。受付できたら True。"""
        prompt_text = self._build_prompt(data)
        prompt_id = f"inbox-{data.get('id', uuid.uuid4().hex[:8])}"
        name = f"inbox:{data.get('from', '?')}"
        req = make_dispatch_request(
            source="inbox",
            entry_id=prompt_id,
            prompt=prompt_text,
            cwd=str(data["cwd"]) if data.get("cwd") else None,
            priority="normal",
            ack={
                "kind": "inbox_file",
                "path": str(msg_file),
                "processed_dir": str(self._processed_dir),
            },
            meta={
                "prompt_id": prompt_id,
                "session_name": name,
                "inbox_cleanup": True,
                "from": data.get("from"),
            },
        )
        if self._scheduler is not None:
            return self._scheduler.enqueue_request(req)
        # scheduler 未設定時の後方互換（テスト用）
        return self._try_dispatch(data)

    def _try_dispatch(self, data: dict[str, Any]) -> bool:
        """レガシー直接送信（scheduler なし / 回帰テスト用）。"""
        prompt_id = f"inbox-{data.get('id', uuid.uuid4().hex[:8])}"
        name = f"inbox:{data.get('from', '?')}"

        if not self._session_mgr.ensure_session(prompt_id, name, owner="inbox"):
            log.warning("[InboxWatcher] セッション未準備のため保留")
            return False

        pane_id: str | None = self._session_mgr.get_pane_id(prompt_id)

        if self._semaphore is not None and pane_id:
            if not self._semaphore.acquire(pane_id):
                return False

        prompt_text = self._build_prompt(data)
        ok = self._session_mgr.send_prompt(prompt_id, prompt_text)
        if ok:
            if self._slot_monitor is not None and pane_id:
                self._slot_monitor.track(
                    pane_id,
                    on_complete=lambda: self._session_mgr.remove_session(
                        prompt_id, owner="inbox", pane_id=pane_id
                    ),
                )
        else:
            if self._semaphore is not None and pane_id:
                self._semaphore.release(pane_id)
        return ok

    def _build_prompt(self, data: dict[str, Any]) -> str:
        from_agent = data.get("from", "unknown")
        subject = data.get("subject", "")
        body = data.get("body", "")
        msg_id = data.get("id", "")

        parts: list[str] = [f"[エージェント {from_agent} からのメッセージ]"]
        if subject:
            parts.append(f"件名: {subject}")
        parts.append("")
        parts.append(body)
        parts.append("")
        parts.append("---")
        reply_cmd = f'agent-loop msg --to {from_agent}'
        if msg_id:
            reply_cmd += f' --reply-to "{msg_id}"'
        reply_cmd += ' "返答内容"'
        parts.append(f"返信する場合: {reply_cmd}")
        return "\n".join(parts)
