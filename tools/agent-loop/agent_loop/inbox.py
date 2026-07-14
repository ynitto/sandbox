from __future__ import annotations
# inbox.py — 元 agent-loop.py の 410-533 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# エージェント間メッセージ受信ウォッチャー
# ---------------------------------------------------------------------------

class InboxWatcher:
    """エージェント間メッセージ受信スレッド。

    メッセージファイル: ~/.kiro/agents/<agent_name>/inbox/<timestamp>_<uuid>.json
    処理済みアーカイブ: ~/.kiro/agents/<agent_name>/inbox/.processed/

    メッセージ JSON スキーマ:
      id          (str)   メッセージ固有 ID
      from        (str)   送信元エージェント名
      to          (str)   宛先エージェント名
      created_at  (float) 作成日時 (Unix timestamp)
      subject     (str)   件名（省略可）
      body        (str)   本文
      reply_to    (str)   返信先エージェント名（省略時は from と同じ）
      correlation_id (str) 会話追跡用 ID（省略可）
      cwd         (str)   送信元の作業ディレクトリ（省略可）
    """

    def __init__(
        self,
        agent_name: str,
        session_mgr: "SessionManager",
        semaphore: "GlobalSemaphore | None" = None,
        poll_interval: int = 5,
    ) -> None:
        self._agent_name = agent_name
        self._session_mgr = session_mgr
        self._semaphore = semaphore
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
        """受信ボックスの未処理メッセージを走査してディスパッチする。"""
        msg_files = sorted(self._inbox_dir.glob("*.json"))
        for msg_file in msg_files:
            try:
                data = json.loads(msg_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("[InboxWatcher] メッセージ読み込みエラー (%s): %s", msg_file.name, exc)
                continue

            dispatched = self._try_dispatch(data)
            if dispatched:
                dest = self._processed_dir / msg_file.name
                try:
                    msg_file.rename(dest)
                except OSError as exc:
                    log.warning("[InboxWatcher] アーカイブ移動エラー (%s): %s", msg_file.name, exc)
                log.info(
                    "[InboxWatcher] メッセージ処理完了: from=%s subject=%r",
                    data.get("from", "?"),
                    data.get("subject", ""),
                )
            else:
                log.debug("[InboxWatcher] メッセージ保留中 (busy/semaphore): %s", msg_file.name)

    def _try_dispatch(self, data: dict[str, Any]) -> bool:
        """セッションへメッセージをディスパッチする。成功時 True。"""
        prompt_id = f"inbox-{data.get('id', uuid.uuid4().hex[:8])}"
        name = f"inbox:{data.get('from', '?')}"

        if not self._session_mgr.ensure_session(prompt_id, name):
            log.warning("[InboxWatcher] セッション未準備のため保留")
            return False

        pane_id: str | None = self._session_mgr.get_pane_id(prompt_id)

        if self._semaphore is not None and pane_id:
            if not self._semaphore.acquire(pane_id):
                return False

        prompt_text = self._build_prompt(data)
        return self._session_mgr.send_prompt(prompt_id, prompt_text)

    def _build_prompt(self, data: dict[str, Any]) -> str:
        from_agent = data.get("from", "unknown")
        subject = data.get("subject", "")
        body = data.get("body", "")
        reply_to = data.get("reply_to") or from_agent
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


