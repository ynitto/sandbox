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
        scheduler: "PeriodicScheduler",
        poll_interval: int = 5,
    ) -> None:
        self._agent_name = agent_name
        self._session_mgr = session_mgr
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
            if self._scheduler.has_pending_ack_path(str(msg_file)):
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
        return self._scheduler.enqueue_request(req)

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
