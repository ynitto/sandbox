from __future__ import annotations
# cli.py — 元 agent-loop.py の 3113-3441 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="kiro-cli を定期プロンプトで自動操作するスクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使い方:
  agent-loop                              # デーモンモードで起動
  agent-loop ls                           # kiro 関連セッションを一覧表示
  agent-loop send "プロンプト"             # セッションにプロンプトを送信
  agent-loop send task.md                 # ファイル内容を読んで実行
  agent-loop send "MR コメント返答"        # agent-loop.yaml の定期プロンプト名で送信
  agent-loop send -s SESSION "プロンプト"  # 指定セッションに送信
""",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル (デフォルト: INFO)",
    )
    parser.add_argument(
        "--split-direction",
        choices=["horizontal", "vertical"],
        help="tmux 分割方向 (horizontal: 左右 / vertical: 上下)",
    )
    parser.add_argument(
        "--no-auto-attach",
        action="store_true",
        help="tmux 外で起動時に自動アタッチしない",
    )
    parser.add_argument(
        "--controller-mode",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--instance-id",
        help=argparse.SUPPRESS,
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    subparsers.add_parser("ls", help="kiro 関連の tmux セッションを一覧表示する")

    subparsers.add_parser(
        "slot-release",
        help=argparse.SUPPRESS,  # agent hook 専用コマンドのためヘルプ非表示
    )

    send_parser = subparsers.add_parser(
        "send",
        help="tmux セッションの kiro-cli にプロンプトを送信する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="プロンプトを kiro-cli tmux セッションに送信する",
        epilog=f"""
プロンプトの種類:
  自然文:                agent-loop send "コードをレビューしてください"
  マークダウンファイル:   agent-loop send task.md
  スケジュール済み名:     agent-loop send "MR コメント返答"

セッションを指定しない場合は '{_DEFAULT_SEND_SESSION}' セッションを使用します。
""",
    )
    send_parser.add_argument(
        "prompt",
        nargs="+",
        metavar="PROMPT",
        help="送信するプロンプト（自然文、ファイルパス、またはスケジュール名）",
    )
    send_parser.add_argument(
        "--session", "-s",
        default=None,
        metavar="NAME",
        help=f"対象 tmux セッション名（省略時: '{_DEFAULT_SEND_SESSION}'）",
    )
    send_parser.add_argument(
        "--dir", "-d",
        default=None,
        metavar="DIR",
        help="作業ディレクトリ（省略時: カレントディレクトリ）",
    )

    msg_parser = subparsers.add_parser(
        "msg",
        help="エージェントの受信ボックスにメッセージを投函する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="エージェント間メッセージを非同期に送信する（受信側の InboxWatcher が処理）",
        epilog="""
使い方:
  agent-loop msg --to worker1 "実装してください: feature X"
  agent-loop msg --to worker1 --from orchestrator --subject "タスク依頼" task.md
  agent-loop msg --to orchestrator --reply-to <msg_id> "完了しました"
""",
    )
    msg_parser.add_argument("--to", required=True, metavar="AGENT", help="宛先エージェント名")
    msg_parser.add_argument("--from", dest="from_agent", default=None, metavar="AGENT", help="送信元エージェント名")
    msg_parser.add_argument("--subject", "-S", default=None, metavar="TEXT", help="件名")
    msg_parser.add_argument("--reply-to", default=None, metavar="MSG_ID", help="返信元メッセージ ID")
    msg_parser.add_argument("--correlation-id", default=None, metavar="ID", help="会話追跡 ID")
    msg_parser.add_argument(
        "body",
        nargs="*",
        metavar="BODY",
        help="メッセージ本文またはファイルパス",
    )

    subparsers.add_parser(
        "agents",
        help="登録済みエージェントの一覧を表示する",
    )

    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)

    cwd = Path.cwd()

    if args.subcommand == "ls":
        cmd_ls()
        return

    if args.subcommand == "slot-release":
        cmd_slot_release()
        return

    if args.subcommand == "send":
        cmd_send(args, cwd)
        return

    if args.subcommand == "msg":
        cmd_msg(args)
        return

    if args.subcommand == "agents":
        cmd_agents()
        return

    running_pid = _find_running_daemon(cwd)
    if running_pid is not None:
        log.info("既に実行中のプロセスがあります (pid=%d)。起動をスキップします。", running_pid)
        sys.exit(0)

    # tmux 外で起動された場合、自己を tmux 内で再実行
    _auto_attach_tmux_if_needed(args)

    # 再度チェック（tmux 内での再起動後）
    running_pid = _find_running_daemon(cwd)
    if running_pid is not None:
        log.info("既に実行中のプロセスがあります (pid=%d)。起動をスキップします。", running_pid)
        sys.exit(0)

    log_file = configure_file_logging()
    log.info("ファイルログを開始しました: %s", log_file)

    config, config_path, has_local_config = load_config(cwd)

    ws_config = _load_prompt_file_data(str(cwd))

    # kiro-cli 起動オプションの解決
    kiro_opts = config.get("kiro_options", {})
    if not isinstance(kiro_opts, dict):
        kiro_opts = {}

    if not has_local_config:
        ws_kiro_opts = ws_config.get("kiro_options", {})
        if isinstance(ws_kiro_opts, dict) and ws_kiro_opts:
            kiro_opts = ws_kiro_opts
            log.info(".agent/agent-loop.yml の kiro_options を使用します。")

    kiro_args: list[str] = []
    if kiro_opts.get("trust_all_tools", True):
        kiro_args.append("--trust-all-tools")
    if kiro_opts.get("resume", False):
        kiro_args.append("--resume")
    if kiro_opts.get("agent"):
        kiro_args.extend(["--agent", str(kiro_opts["agent"])])
    if kiro_opts.get("model"):
        kiro_args.extend(["--model", str(kiro_opts["model"])])
    for extra in kiro_opts.get("extra_args", []):
        kiro_args.append(str(extra))

    startup_timeout = int(config.get("startup_timeout", 60))
    response_timeout = int(config.get("response_timeout", 300))
    echo_output = bool(config.get("echo_output", False))
    split_direction = args.split_direction or str(config.get("split_direction", "horizontal"))
    if split_direction not in ("horizontal", "vertical"):
        log.warning("split_direction の値が不正なため horizontal を使用します: %s", split_direction)
        split_direction = "horizontal"

    entries: list[dict[str, Any]] = config.get("prompts", [])
    if not has_local_config:
        entries = load_vscode_periodic_prompts(cwd)

    if not entries:
        log.info("prompts が定義されていません。定期プロンプト未設定で起動します。")

    # 同時実行数制御の設定
    max_concurrent = int(config.get("max_concurrent", 0))
    slot_timeout_seconds = int(config.get("slot_timeout_seconds", 7200))
    cooldown_seconds = int(config.get("cooldown_seconds", 0))
    uses_user_agent = bool(kiro_opts.get("agent"))
    # uses_concurrency_agent: agent-loop-concurrency agent を kiro-cli に注入するか
    # ユーザーが独自 agent を設定した場合は注入しないが、セマフォ制御は適用する
    uses_concurrency_agent = max_concurrent > 0 and not uses_user_agent

    semaphore: GlobalSemaphore | None = GlobalSemaphore(max_concurrent, slot_timeout_seconds, cooldown_seconds) if max_concurrent > 0 else None
    if max_concurrent > 0:
        if uses_user_agent:
            log.info(
                "同時実行数制御を有効にします (ペイン監視のみ): max_concurrent=%d, slot_timeout=%ds, cooldown=%ds",
                max_concurrent, slot_timeout_seconds, cooldown_seconds,
            )
        else:
            log.info(
                "同時実行数制御を有効にします: max_concurrent=%d, slot_timeout=%ds, cooldown=%ds",
                max_concurrent, slot_timeout_seconds, cooldown_seconds,
            )

    # グローバル参照（cleanup / シグナルハンドラ用）
    global _session_mgr_ref, _scheduler_ref, _slot_monitor_ref, _stop_event_ref
    global _webhook_server_ref, _inbox_watcher_ref

    stop_event = threading.Event()
    _stop_event_ref = stop_event

    instance_id = args.instance_id or uuid.uuid4().hex[:8]

    session_mgr = SessionManager(
        target_path=str(cwd),
        instance_id=instance_id,
        kiro_args_base=kiro_args,
        split_direction=split_direction,
        startup_timeout=startup_timeout,
        response_timeout=response_timeout,
        echo_output=echo_output,
        uses_concurrency_agent=uses_concurrency_agent,
    )
    _session_mgr_ref = session_mgr

    log.info("カレントディレクトリを起動対象に設定しました: %s", cwd)

    slot_monitor: SlotMonitor | None = SlotMonitor(semaphore, slot_timeout_seconds) if semaphore is not None else None
    _slot_monitor_ref = slot_monitor

    scheduler = PeriodicScheduler(session_mgr, entries, semaphore=semaphore, slot_monitor=slot_monitor)
    _scheduler_ref = scheduler

    # カレントディレクトリ配下の .agent/agent-loop.yml から定期プロンプトを読み込み
    ws_prompts = load_prompt_config(str(cwd))
    if ws_prompts:
        scheduler.set_entries(ws_prompts)

    # シグナルハンドラ登録
    # SIGHUP: ターミナルを閉じたとき / SIGTERM: kill / SIGINT: Ctrl+C
    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _signal_handler)

    atexit.register(_cleanup)

    # スケジューラ開始
    scheduler.start()

    # InboxWatcher: agent_name が設定されている場合に受信ボックスを監視する
    agent_name = str(config.get("agent_name", "")).strip()
    inbox_poll_seconds = int(config.get("inbox_poll_seconds", 5))
    inbox_watcher: InboxWatcher | None = None
    if agent_name:
        inbox_watcher = InboxWatcher(
            agent_name=agent_name,
            session_mgr=session_mgr,
            semaphore=semaphore,
            poll_interval=inbox_poll_seconds,
        )
        inbox_watcher.start()
        _inbox_watcher_ref = inbox_watcher
        log.info("InboxWatcher を起動しました: agent_name=%s", agent_name)

    # WebhookServer: webhook.enabled かつ port 指定時に inbound webhook を受ける
    webhook_cfg = config.get("webhook")
    if isinstance(webhook_cfg, dict) and webhook_cfg.get("enabled"):
        try:
            webhook_port = int(webhook_cfg.get("port"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            webhook_port = 0
        if webhook_port > 0:
            webhook_server = WebhookServer(
                scheduler=scheduler,
                host=str(webhook_cfg.get("host", _WEBHOOK_DEFAULT_HOST)),
                port=webhook_port,
                path_prefix=str(webhook_cfg.get("path_prefix", _WEBHOOK_DEFAULT_PATH_PREFIX)),
                secret=str(webhook_cfg.get("secret", "")),
                secret_header=webhook_cfg.get("secret_header"),
                max_body_bytes=int(webhook_cfg.get("max_body_bytes", _WEBHOOK_DEFAULT_MAX_BODY)),
            )
            webhook_server.start()
            _webhook_server_ref = webhook_server
        else:
            log.warning("webhook.enabled ですが port が未指定/不正のため webhook を起動しません。")

    # スロット監視スレッド起動（同時実行数制御が有効な場合のみ）
    if slot_monitor is not None:
        slot_monitor.start()

    # セッション監視スレッド起動
    monitor_thread = threading.Thread(
        target=_monitor_loop,
        args=(session_mgr, stop_event),
        name="session-monitor",
        daemon=True,
    )
    monitor_thread.start()

    log.info("実行中です。ターミナルを閉じるか 'quit' コマンドで終了します。")

    # コマンドループはメインスレッドで実行
    command_loop(session_mgr, scheduler, stop_event, config_path)

    # コマンドループ終了後のクリーンアップ
    stop_event.set()
    _cleanup()
    sys.exit(0)


if __name__ == "__main__":
    main()
