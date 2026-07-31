from __future__ import annotations
# interactive.py — 元 agent-loop.py の 2184-2577 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# インタラクティブコマンドループ
# ---------------------------------------------------------------------------

_HELP_TEXT = """\
コマンド一覧:
  status                          実行状態を表示
  ls                              管理下のセッション一覧を表示
  send <target> <text>            管理下のセッションにテキストを送信
                                  target: pane ID (%12)、tmux セッション名、またはプロンプト名
                                  例: send %12 status確認してください
                                  例: send my-prompt コードをレビューしてください
  prompt-add <interval> <prompt>  定期プロンプトを追加 (interval は分単位の整数)
  prompt-add <name> <interval> <prompt>
                                  名前付きで定期プロンプトを追加
  prompt-list                     定期プロンプト設定を表示
  prompt-remove <index>           指定インデックスの定期プロンプトを削除
  help                            このヘルプを表示
  quit / exit                     終了"""


def command_loop(
    session_mgr: SessionManager,
    scheduler: PeriodicScheduler,
    stop_event: threading.Event,
    config_path: Path,
) -> None:
    """stdin からコマンドを読んで定期プロンプト設定を管理する（メインスレッドで実行）。"""
    target_name = session_mgr.get_target_name()
    target_path = session_mgr.get_target_path()

    print("定期プロンプトが実行中です。'help' でコマンド一覧を表示します。", flush=True)
    print(f"設定ファイル: {config_path}", flush=True)

    while not stop_event.is_set():
        try:
            try:
                line = input("> ")
            except EOFError:
                # stdin が閉じられた（パイプ終端など）
                break

            line = line.strip()
            if not line:
                continue

            parts = line.split(maxsplit=2)
            cmd = parts[0].lower()

            if cmd in ("help", "h", "?"):
                print(_HELP_TEXT, flush=True)

            elif cmd == "status":
                target_label, target_dir, total_count, alive_count = session_mgr.get_status()
                print(f"target: {target_label}", flush=True)
                print(f"path: {target_dir}", flush=True)
                print(f"sessions: {alive_count}/{total_count} alive", flush=True)

                prompt_statuses = session_mgr.list_prompt_statuses()
                if not prompt_statuses:
                    print("  (プロンプトセッションは未作成)", flush=True)
                else:
                    for prompt_name, prompt_id, is_alive, tmux_name, pane_target in prompt_statuses:
                        state = "alive" if is_alive else "dead"
                        print(
                            f"  - [{state}] {prompt_name} (id={prompt_id}, tmux={tmux_name}, pane={pane_target})",
                            flush=True,
                        )

            elif cmd == "ls":
                prompt_statuses = session_mgr.list_prompt_statuses()
                if not prompt_statuses:
                    print("  (管理下のセッションはありません)", flush=True)
                else:
                    col_name = max(len(p[0]) for p in prompt_statuses)
                    col_name = max(col_name, 12)
                    col_tmux = max(len(p[3]) for p in prompt_statuses)
                    col_tmux = max(col_tmux, 10)
                    header = f"  {'プロンプト名':<{col_name}}  {'pane':>6}  {'状態':<6}  tmux セッション"
                    print(header, flush=True)
                    print(f"  {'-' * (col_name + col_tmux + 26)}", flush=True)
                    for prompt_name, _, is_alive, tmux_name, pane_target in prompt_statuses:
                        state = "alive" if is_alive else "dead"
                        pane_str = pane_target or "-"
                        print(
                            f"  {prompt_name:<{col_name}}  {pane_str:>6}  {state:<6}  {tmux_name}",
                            flush=True,
                        )

            elif cmd == "send":
                args = line.split(maxsplit=2)
                if len(args) < 3:
                    print("使い方: send <target> <text>", flush=True)
                    print("  target: pane ID (%N)、tmux セッション名、またはプロンプト名", flush=True)
                    continue

                target = args[1].strip()
                send_text = args[2].strip()
                # クォート除去
                if (
                    len(send_text) >= 2
                    and send_text[0] == send_text[-1]
                    and send_text[0] in ('"', "'")
                ):
                    send_text = send_text[1:-1].strip()

                if not target:
                    print("target が空です。", flush=True)
                    continue

                if not send_text:
                    print("text が空です。", flush=True)
                    continue

                pane_id = session_mgr.resolve_managed_pane(target)
                if pane_id is None:
                    print(f"管理下のセッションが見つかりません: '{target}'", flush=True)
                    print("  'ls' で管理下のセッション一覧を確認してください。", flush=True)
                    continue

                ok, err = _send_to_pane(pane_id, send_text)
                if ok:
                    print(f"送信しました: pane={pane_id}", flush=True)
                else:
                    print(f"送信に失敗しました: {err}", flush=True)

            elif cmd == "prompt-add":
                args = line.split(maxsplit=3)
                if len(args) < 3:
                    print(
                        "使い方: prompt-add <interval_minutes> <prompt>\n"
                        "        prompt-add <name> <interval_minutes> <prompt>",
                        flush=True,
                    )
                else:
                    name_override: str | None = None
                    interval_text = ""
                    prompt_parts: list[str] = []

                    # 形式A: prompt-add <interval> <prompt>
                    # 形式B: prompt-add <name> <interval> <prompt>
                    try:
                        int(args[1])
                        interval_text = args[1]
                        prompt_parts = args[2:]
                    except ValueError:
                        if len(args) < 4:
                            print(
                                "使い方: prompt-add <interval_minutes> <prompt>\n"
                                "        prompt-add <name> <interval_minutes> <prompt>",
                                flush=True,
                            )
                            continue
                        name_override = args[1]
                        interval_text = args[2]
                        prompt_parts = args[3:]

                    try:
                        interval = int(interval_text)
                        if interval < 1:
                            raise ValueError()
                    except ValueError:
                        print("interval_minutes は 1 以上の整数を指定してください。", flush=True)
                        continue

                    prompt_text = " ".join(prompt_parts).strip()
                    if not prompt_text:
                        print("prompt が空です。", flush=True)
                        continue

                    # 先頭と末尾が同じ引用符なら外す
                    if (
                        len(prompt_text) >= 2
                        and prompt_text[0] == prompt_text[-1]
                        and prompt_text[0] in ('"', "'")
                    ):
                        prompt_text = prompt_text[1:-1].strip()

                    if not prompt_text:
                        print("prompt が空です。", flush=True)
                        continue

                    ws_prompts = load_prompt_config(target_path)                    
                    ws_prompts.append({
                        "id": str(uuid.uuid4()),
                        "name": name_override or prompt_text[:40],
                        "prompt": prompt_text,
                        "interval_minutes": interval,
                        "enabled": True,
                    })

                    if save_prompt_config(target_path, ws_prompts):
                        scheduler.set_entries(ws_prompts)
                        print("定期プロンプトを追加しました。", flush=True)

            elif cmd == "prompt-list":
                args = line.split(maxsplit=1)
                if len(args) >= 2:
                    print("使い方: prompt-list", flush=True)
                    continue

                ws_prompts = load_prompt_config(target_path)
                print(f"[{target_name}] {target_path}", flush=True)
                if not ws_prompts:
                    print("  (定期プロンプトは未設定)", flush=True)
                    continue

                for idx, p in enumerate(ws_prompts, start=1):
                    enabled = p.get("enabled", True)
                    cron = str(p.get("cron", "")).strip()
                    run_immediately = bool(
                        p.get("run_immediately_on_startup", p.get("run_immediately", False))
                    )
                    prompt_text = str(p.get("prompt", "")).replace("\n", " ")
                    short = prompt_text[:80] + ("..." if len(prompt_text) > 80 else "")
                    flag = "on" if enabled else "off"
                    immediate_note = " (起動時即実行)" if run_immediately else ""
                    if cron:
                        schedule_note = f'cron "{cron}"'
                    else:
                        interval = p.get("interval_minutes", "?")
                        schedule_note = f"{interval}分"
                    print(f"  {idx:>2}. [{flag}] {schedule_note}{immediate_note}: {short}", flush=True)

            elif cmd == "prompt-remove":
                args = line.split(maxsplit=1)
                if len(args) < 2:
                    print("使い方: prompt-remove <index>", flush=True)
                else:
                    index_text = args[1]
                    ws_prompts = load_prompt_config(target_path)
                    if not ws_prompts:
                        print("削除対象がありません。", flush=True)
                        continue
                    try:
                        index = int(index_text)
                    except ValueError:
                        print("index は整数を指定してください。", flush=True)
                        continue

                    if index < 1 or index > len(ws_prompts):
                        print(f"インデックスは 1 から {len(ws_prompts)} の範囲で指定してください。", flush=True)
                        continue

                    removed = ws_prompts.pop(index - 1)
                    if save_prompt_config(target_path, ws_prompts):
                        scheduler.set_entries(ws_prompts)
                        short = str(removed.get("prompt", ""))[:60]
                        print(f"削除しました: {short}", flush=True)

            elif cmd in ("quit", "exit", "q"):
                print("終了します。", flush=True)
                stop_event.set()
                break

            else:
                print(f"不明なコマンド: '{cmd}'。'help' でコマンド一覧を表示します。", flush=True)

        except KeyboardInterrupt:
            break

    log.info("コマンドループを終了しました。")


# ---------------------------------------------------------------------------
# セッション監視ループ（別スレッド）
# ---------------------------------------------------------------------------

def _monitor_loop(session_mgr: SessionManager, stop_event: threading.Event) -> None:
    """死んだセッションを定期的に検出して再起動する。"""
    while not stop_event.wait(10):
        session_mgr.restart_if_dead()
        session_mgr.write_state()


# ---------------------------------------------------------------------------
# シグナルハンドラ / グローバル cleanup
# ---------------------------------------------------------------------------

_session_mgr_ref: SessionManager | None = None
_scheduler_ref: PeriodicScheduler | None = None
_slot_monitor_ref: SlotMonitor | None = None
_stop_event_ref: threading.Event | None = None
_webhook_server_ref: "WebhookServer | None" = None
_inbox_watcher_ref: InboxWatcher | None = None


def _cleanup() -> None:
    if _webhook_server_ref is not None:
        _webhook_server_ref.stop()
    if _inbox_watcher_ref is not None:
        _inbox_watcher_ref.stop()
    if _scheduler_ref is not None:
        _scheduler_ref.stop()
    if _slot_monitor_ref is not None:
        _slot_monitor_ref.stop()
    if _session_mgr_ref is not None:
        _session_mgr_ref.stop()


def _signal_handler(sig: int, frame: Any) -> None:
    sig_name = signal.Signals(sig).name
    log.info("シグナル %s を受信しました。終了します。", sig_name)
    if _stop_event_ref is not None:
        _stop_event_ref.set()
    _cleanup()
    sys.exit(0)


# ---------------------------------------------------------------------------
# tmux 自動アタッチ
# ---------------------------------------------------------------------------

def _auto_attach_tmux_if_needed(args: argparse.Namespace) -> None:
    """tmux 外で起動された場合、tmux セッション内へ自己再実行して表示を有効化する。"""
    if args.controller_mode or args.no_auto_attach:
        return
    if os.environ.get("TMUX"):
        return

    tmux_bin = shutil.which("tmux")
    if tmux_bin is None:
        return

    target_path = Path.cwd()
    instance_id = args.instance_id or uuid.uuid4().hex[:8]
    session_name = _tmux_session_name(target_path, instance_id)
    script_path = Path(__file__).resolve()
    command_parts = [
        shlex.quote(sys.executable),
        shlex.quote(str(script_path)),
    ]

    command_parts.extend(["--instance-id", shlex.quote(instance_id)])

    if args.log_level:
        command_parts.extend(["--log-level", shlex.quote(args.log_level)])

    if args.split_direction:
        command_parts.extend(["--split-direction", shlex.quote(args.split_direction)])

    command_parts.append("--controller-mode")
    controller_cmd = " ".join(command_parts)

    has_session = (
        subprocess.run(
            [tmux_bin, "has-session", "-t", session_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
    )

    if not has_session:
        log.info("tmux 外で起動されたため `%s` を新規作成してアタッチします。", session_name)
        os.execvp(
            tmux_bin,
            [
                tmux_bin,
                "new-session",
                "-s",
                session_name,
                "-c",
                str(target_path),
                controller_cmd,
            ],
        )

    create_window = subprocess.run(
        [
            tmux_bin,
            "new-window",
            "-t",
            session_name,
            "-n",
            "agent-loop",
            "-c",
            str(target_path),
            controller_cmd,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if create_window.returncode != 0:
        log.warning("既存セッションへの controller ウィンドウ追加に失敗しました。")

    log.info("tmux 外で起動されたため `%s` へ自動アタッチします。", session_name)
    os.execvp(
        tmux_bin,
        [tmux_bin, "attach-session", "-t", session_name],
    )


