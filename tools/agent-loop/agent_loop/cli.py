from __future__ import annotations
# cli.py — 元 agent-loop.py の 3113-3441 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def _cmd_lifecycle(args: argparse.Namespace, cwd: Path) -> None:
    """pause / resume / cancel / drain / reload を running daemon へ file mailbox で渡す。"""
    pid = _find_running_daemon(cwd)
    cmd = str(args.subcommand)
    if cmd in ("pause", "resume"):
        set_local_pause(str(cwd.resolve()), cmd == "pause")
        print(f"[agent-loop] local pause を {'有効' if cmd == 'pause' else '解除'}にしました", file=sys.stderr)
        if pid is None:
            print("[agent-loop] 実行中の daemon はありません（loop-control のみ更新）", file=sys.stderr)
            sys.exit(0)
        write_loop_command(pid, cmd)
        sys.exit(0)

    if pid is None:
        print("[agent-loop] ERROR: 実行中の daemon が見つかりません", file=sys.stderr)
        sys.exit(1)

    if cmd == "cancel":
        target = str(getattr(args, "target", "") or "")
        if not target:
            print("[agent-loop] ERROR: cancel には target が必要です", file=sys.stderr)
            sys.exit(1)
        # 不明 target は daemon 側でも失敗し得る。CLI では mailbox 書き込み後、
        # 管理下に無い場合は事前チェックで非 0。
        states = _read_all_states()
        known = False
        for st in states:
            if int(st.get("pid", 0)) != pid:
                continue
            for s in st.get("sessions", []):
                if target in (s.get("pane"), s.get("id"), s.get("name")):
                    known = True
                    break
        if not known:
            print(f"[agent-loop] ERROR: 不明なターゲットです: {target}", file=sys.stderr)
            sys.exit(1)
        write_loop_command(pid, "cancel", {"target": target})
        print(f"[agent-loop] cancel を要求しました: {target}", file=sys.stderr)
        sys.exit(0)

    if cmd == "drain":
        write_loop_command(pid, "drain")
        print("[agent-loop] drain を要求しました", file=sys.stderr)
        sys.exit(0)

    if cmd == "reload":
        # YAML を読んで entries を payload に載せる（daemon が validate → 次 tick 交換）
        try:
            config, _, has_local = load_config(cwd)
            entries = list(config.get("prompts") or [])
            if not has_local:
                entries = load_vscode_periodic_prompts(cwd)
            ws = load_prompt_config(str(cwd))
            if ws:
                entries = ws
        except Exception as exc:
            print(f"[agent-loop] ERROR: 設定の読み込みに失敗しました: {exc}", file=sys.stderr)
            sys.exit(1)
        write_loop_command(pid, "reload", {
            "entries": entries,
            "external_panes": config.get("external_panes") or [],
            "environment_handoff": normalize_environment_handoff(config),
        })
        print("[agent-loop] reload を要求しました", file=sys.stderr)
        sys.exit(0)

    print(f"[agent-loop] ERROR: 未知のコマンド: {cmd}", file=sys.stderr)
    sys.exit(1)


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
        "--version",
        action="version",
        version=format_version_line(),
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
    send_parser.add_argument(
        "--wait",
        action="store_true",
        help="キュー受付後、対象ペインの busy→ready を待つ",
    )
    send_parser.add_argument(
        "--priority",
        choices=["high", "normal", "low"],
        default="normal",
        help="dispatch 優先度（既定: normal）",
    )
    send_parser.add_argument(
        "--response-timeout",
        type=float,
        default=None,
        metavar="SEC",
        help="--wait 時のタイムアウト秒（既定: 600）",
    )
    send_parser.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help="起動 model（既存 pane と不一致なら失敗。daemon 必須）",
    )
    send_parser.add_argument(
        "--sandbox",
        action="store_true",
        help="git worktree sandbox で実行（daemon 必須）",
    )
    send_parser.add_argument(
        "--force",
        action="store_true",
        help="visual ready / preflight のみ迂回（daemon 必須）",
    )
    send_parser.add_argument(
        "--ralph",
        action="store_true",
        help="Ralph 反復実行（--max-iterations 必須。daemon 必須）",
    )
    send_parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        metavar="N",
        help="Ralph の work iteration 数（1..100）",
    )

    sm_parser = subparsers.add_parser(
        "statemachine",
        help="ステートマシンを aider 等の headless CLI で完走させる",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="statemachine-use のワークフローを headless エージェント CLI で実行する"
                    "（限定ツールループつきハーネス）",
        epilog="""
使い方:
  agent-loop statemachine --workflow .statemachine/digest/workflow.yaml
  agent-loop statemachine --workflow .statemachine/digest/workflow.yaml \\
      --agent-cli aider --model gemma4:e4b --param topic=llm --input "今日の要約"
""",
    )
    sm_parser.add_argument(
        "--workflow", required=True, metavar="PATH",
        help="workflow.yaml のパス（作業ディレクトリからの相対または配下の絶対パス）",
    )
    sm_parser.add_argument(
        "--agent-cli", default="aider", metavar="NAME",
        help="agents/<name>.json の headless CLI 名（既定: aider）",
    )
    sm_parser.add_argument(
        "--model", default=None, metavar="MODEL",
        help="実行モデル（省略時は CLI 定義の default_model）",
    )
    sm_parser.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE",
        help="ワークフローの実行パラメータ。繰り返し指定可",
    )
    sm_parser.add_argument(
        "--input", default=None, metavar="TEXT",
        help="ワークフローの input パラメータ",
    )
    sm_parser.add_argument(
        "--dir", "-d", default=None, metavar="DIR",
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

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="設定・状態・slot・send-request を診断する",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="findings を JSON で出力する",
    )
    doctor_parser.add_argument(
        "--fix",
        action="store_true",
        help="安全な修復のみ実行（dir 作成、dead slot 削除、破損 request 隔離）",
    )

    subparsers.add_parser("pause", help="local pause（新規 dispatch / pane 起動を停止）")
    subparsers.add_parser("resume", help="local pause だけを解除する")
    cancel_parser = subparsers.add_parser("cancel", help="managed な entry/pane を停止・解放する")
    cancel_parser.add_argument("target", help="entry id / name / pane id")
    subparsers.add_parser("drain", help="新規受付を止め、実行中完了後に daemon を終了する")
    subparsers.add_parser("reload", help="設定の transactional reload を要求する")

    methods_parser = subparsers.add_parser("methods", help="手法パックの一覧・有効化・無効化・追加")
    methods_sub = methods_parser.add_subparsers(dest="methods_action", required=True)
    methods_list = methods_sub.add_parser("list", help="カタログと現在値を表示する")
    methods_list.add_argument("--json", action="store_true")
    methods_enable = methods_sub.add_parser("enable", help="カタログを複製して有効化する")
    methods_enable.add_argument("id")
    methods_disable = methods_sub.add_parser("disable", help="手法を無効化する")
    methods_disable.add_argument("id")
    methods_add = methods_sub.add_parser("add", help="独自手法を追加する")
    methods_add.add_argument("id")
    methods_add.add_argument("--role", required=True,
                             choices=["planner", "worker", "verify", "evaluator", "session"])
    methods_add.add_argument("--text", required=True)
    methods_add.add_argument("--description", default="")
    methods_add.add_argument("--when-json", default="{}")

    subparsers.add_parser("update", help="zipapp インストールを git remote から更新する")

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

    if args.subcommand == "statemachine":
        cmd_statemachine(args, cwd)
        return

    if args.subcommand == "msg":
        cmd_msg(args)
        return

    if args.subcommand == "agents":
        cmd_agents()
        return

    if args.subcommand == "doctor":
        cmd_doctor(args)
        return

    if args.subcommand in ("pause", "resume", "cancel", "drain", "reload"):
        _cmd_lifecycle(args, cwd)
        return

    if args.subcommand == "methods":
        try:
            if args.methods_action == "list":
                inventory = method_inventory()
                if args.json:
                    print(json.dumps(inventory, ensure_ascii=False, indent=2))
                else:
                    enabled = {str(m.get("id")) for m in inventory["methods"] if m.get("enabled") is True}
                    print(f"手法カタログ（revision={inventory['revision']}）")
                    for method in inventory["catalog"]:
                        state = "ON" if str(method.get("id")) in enabled else "OFF"
                        print(f"  [{state}] {method.get('id')}: {method.get('description', '')}")
                return
            if args.methods_action == "enable":
                data = method_enable(args.id)
            elif args.methods_action == "disable":
                data = method_disable(args.id)
            else:
                try:
                    when = json.loads(args.when_json)
                except ValueError as exc:
                    raise ValueError(f"--when-json が JSON ではありません: {exc}") from exc
                if not isinstance(when, dict):
                    raise ValueError("--when-json は JSON object で指定してください")
                data = method_add(args.id, args.role, args.text, when, args.description)
            print(f"tuning revision={data['revision']}")
        except ValueError as exc:
            print(f"[agent-loop] ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
        return

    if args.subcommand == "update":
        cmd_update(args)
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

    # エージェント CLI の差し替え（agent_cli / agent_cli_options）。未指定なら従来の
    # kiro-cli 組み込み経路（kiro_options）。未知・壊れた定義は fail fast — 黙って
    # kiro へ倒すと設定ミスに気づけない（agents/<name>.json 契約の明示エラー原則）。
    try:
        cli_profile = _init_cli_profile_from_config(config, project_dir=cwd, strict=True)
    except CliProfileError as exc:
        log.error("agent_cli の解決に失敗しました: %s", exc)
        sys.exit(1)
    if cli_profile is not None:
        log.info("エージェント CLI: %s (argv=%s)", cli_profile.name, cli_profile.argv)
        if kiro_opts:
            log.info("agent_cli 指定時は kiro_options を使いません（agent_cli_options を使ってください）。")

    startup_timeout = int(config.get("startup_timeout", 60))
    split_direction = args.split_direction or str(config.get("split_direction", "horizontal"))
    environment_handoff = normalize_environment_handoff(config)
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
    # ユーザーが独自 agent を設定した場合は注入しないが、セマフォ制御は適用する。
    # agent_cli 指定時も注入しない（stop hook は kiro-cli の agents 機構。他 CLI では
    # SlotMonitor のペイン監視だけで解放する）。
    uses_concurrency_agent = max_concurrent > 0 and not uses_user_agent and cli_profile is None

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

    # 起動時 stale slot / sandbox クリーンアップ（常時）
    cleanup_stale_slots_on_startup(
        slot_timeout_seconds,
        cooldown_seconds=cooldown_seconds,
    )
    try:
        cleanup_stale_sandboxes()
    except Exception as exc:
        log.warning("stale sandbox クリーンアップに失敗しました: %s", exc)

    health_cfg = config.get("health") if isinstance(config.get("health"), dict) else {}
    freeze_timeout = int(health_cfg.get("freeze_timeout_seconds", 0) or 0)

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
        uses_concurrency_agent=uses_concurrency_agent,
    )
    session_mgr.configure_environment_handoff(
        environment_handoff,
        user_home=Path.home().resolve(),
    )
    _session_mgr_ref = session_mgr

    register_daemon_update_lock()

    log.info("カレントディレクトリを起動対象に設定しました: %s", cwd)

    agent_name = str(config.get("agent_name", "")).strip()
    monitor_semaphore = semaphore or GlobalSemaphore(0, slot_timeout_seconds, 0)

    slot_monitor_box: list[SlotMonitor | None] = [None]

    def _on_freeze(pane_id: str) -> None:
        mon = slot_monitor_box[0]
        with session_mgr._lock:
            items = list(session_mgr._panes.items())
        for prompt_id, p in items:
            if p != pane_id:
                continue
            log.warning("freeze を検知したためペインを再起動します: %s", pane_id)
            if mon is not None:
                mon.fail(pane_id)
            try:
                session_mgr.restart_pane(prompt_id)
            except RuntimeError as exc:
                log.error("freeze 再起動失敗: %s", exc)
            return

    slot_monitor = SlotMonitor(
        monitor_semaphore,
        slot_timeout_seconds,
        freeze_timeout_seconds=freeze_timeout,
        on_freeze=_on_freeze,
    )
    slot_monitor_box[0] = slot_monitor
    _slot_monitor_ref = slot_monitor

    scheduler = PeriodicScheduler(
        session_mgr, entries, semaphore=semaphore, slot_monitor=slot_monitor,
        workspace=str(cwd.resolve()),
    )
    scheduler.configure_runtime(
        workspace=str(cwd.resolve()),
        health=health_cfg,
        environment_handoff=environment_handoff,
        external_panes=config.get("external_panes") or [],
    )
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
    inbox_poll_seconds = int(config.get("inbox_poll_seconds", 5))
    inbox_watcher: InboxWatcher | None = None
    if agent_name:
        inbox_watcher = InboxWatcher(
            agent_name=agent_name,
            session_mgr=session_mgr,
            scheduler=scheduler,
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
                secret_header=webhook_cfg.get("secret_header", "X-Gitlab-Token"),
                max_body_bytes=int(webhook_cfg.get("max_body_bytes", _WEBHOOK_DEFAULT_MAX_BODY)),
            )
            webhook_server.start()
            _webhook_server_ref = webhook_server
        else:
            log.warning("webhook.enabled ですが port が未指定/不正のため webhook を起動しません。")

    # スロット／inbox 完了監視スレッド起動
    slot_monitor.start()

    # セッション監視スレッド起動
    monitor_thread = threading.Thread(
        target=_monitor_loop,
        args=(session_mgr, stop_event, scheduler, health_cfg),
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
