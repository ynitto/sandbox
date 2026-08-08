from __future__ import annotations
# sendcmd.py — 元 agent-loop.py の 2578-3112 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# send/ls サブコマンド: tmux ヘルパー
# ---------------------------------------------------------------------------

def _session_name_exists(session: str) -> bool:
    return _tmux_cmd("has-session", "-t", session).returncode == 0


def _capture_pane(target: str) -> str:
    """セッション名またはペイン ID でペイン内容を取得する。"""
    r = _tmux_cmd("capture-pane", "-p", "-t", target)
    return r.stdout if r.returncode == 0 else ""



def _pane_has_prompt(content: str) -> bool:
    """入力受付（送信してよい状態）か。判定方法は CLI ごとに違うため CliProfile へ委譲する。

    agent_cli 未指定（legacy プロファイル）では従来どおり、空行を除いた末尾 3 行への
    _PROMPT_RE 一致。busy_pattern を宣言する CLI では、入力欄が見えていても処理中
    （例: claude の '(esc to interrupt)'）なら False になる。
    """
    return _CLI_PROFILE.is_ready(content)


def _get_session_pane_cwd(session: str) -> str:
    r = _tmux_cmd("display-message", "-p", "-t", session, "#{pane_current_path}")
    return r.stdout.strip() if r.returncode == 0 else ""


def _find_kiro_pane_in_session(session: str) -> str | None:
    """セッション内の kiro-cli ペインを探してペイン ID を返す。

    pane_current_command で python/python3（コントローラー）を除外し、
    残りの中から kiro プロンプトが表示されているペインを優先して返す。
    """
    r = _tmux_cmd(
        "list-panes", "-t", session, "-F",
        "#{pane_id}\t#{pane_current_command}\t#{pane_dead}",
    )
    if r.returncode != 0:
        return None

    non_controller: list[str] = []
    all_alive: list[str] = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pane_id, command, dead = parts[0], parts[1], parts[2]
        if dead == "1":
            continue
        all_alive.append(pane_id)
        if not command.startswith("python"):
            non_controller.append(pane_id)

    # コントローラー以外でプロンプトが出ているペインを優先
    for pane_id in non_controller:
        if _pane_has_prompt(_capture_pane(pane_id)):
            return pane_id

    if non_controller:
        return non_controller[0]

    # フォールバックなし — コントローラーを kiro ペインと誤認しないため None を返す
    return None


def _resolve_target_pane(target: str) -> str | None:
    """セッション名またはペイン ID から kiro-cli ペイン ID を解決する。

    target が '%' で始まる場合はそのまま使用し、セッション名の場合は
    _find_kiro_pane_in_session() で kiro ペインを探す。
    """
    if target.startswith("%"):
        r = _tmux_cmd("display-message", "-p", "-t", target, "#{pane_id}")
        return target if r.returncode == 0 else None
    return _find_kiro_pane_in_session(target)


def _wait_for_session_prompt(session: str, timeout: int, label: str) -> bool:
    """セッション名でプロンプト待機（ensure_cli_session の起動待ち用）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _session_name_exists(session):
            print(f"[agent-loop] ERROR: セッション '{session}' が消えました", file=sys.stderr)
            return False
        if _pane_has_prompt(_capture_pane(session)):
            return True
        time.sleep(0.5)
    print(f"[agent-loop] WARN: {label} がタイムアウトしました ({timeout}秒)", file=sys.stderr)
    return False


def _set_session_last_active(session: str) -> None:
    _tmux_cmd("set-environment", "-t", session, _ENV_LAST_ACTIVE, str(int(time.time())))


def ensure_cli_session(session: str, work_dir: Path | None, cli_argv: "list[str]") -> bool:
    """エージェント CLI が起動中の tmux セッションを確保する。"""
    kiro_cmd = shlex.join(cli_argv)
    cwd_str = str(work_dir) if work_dir else None

    if not _session_name_exists(session):
        effective_cwd = cwd_str or str(Path.home())
        print(f"[agent-loop] tmux セッション '{session}' を作成します (cwd={effective_cwd})", file=sys.stderr)
        r = _tmux_cmd("new-session", "-d", "-s", session, "-c", effective_cwd, kiro_cmd)
        if r.returncode != 0:
            print(f"[agent-loop] ERROR: セッション作成に失敗しました: {r.stderr.strip()}", file=sys.stderr)
            return False
        print("[agent-loop] kiro-cli 起動待ち...", file=sys.stderr)
        ok = _wait_for_session_prompt(session, _SEND_STARTUP_TIMEOUT, "起動")
        if ok:
            print("[agent-loop] kiro-cli 起動完了", file=sys.stderr)
            _set_session_last_active(session)
        return ok

    pane_cwd = _get_session_pane_cwd(session)
    kiro_alive = _pane_has_prompt(_capture_pane(session))

    if kiro_alive and (cwd_str is None or pane_cwd == cwd_str):
        print(f"[agent-loop] 既存セッション '{session}' を再利用します (cwd={pane_cwd})", file=sys.stderr)
        return True

    reason = f"cwd 変更 ({pane_cwd} → {cwd_str})" if kiro_alive else "kiro-cli が終了していました"
    print(f"[agent-loop] kiro-cli を再起動します ({reason})", file=sys.stderr)

    effective_cwd = cwd_str or pane_cwd or str(Path.home())
    r = _tmux_cmd("respawn-pane", "-k", "-t", session, "-c", effective_cwd, kiro_cmd)
    if r.returncode != 0:
        print(f"[agent-loop] ERROR: respawn-pane に失敗しました: {r.stderr.strip()}", file=sys.stderr)
        return False
    print("[agent-loop] kiro-cli 起動待ち...", file=sys.stderr)
    ok = _wait_for_session_prompt(session, _SEND_STARTUP_TIMEOUT, "起動")
    if ok:
        print("[agent-loop] kiro-cli 起動完了", file=sys.stderr)
        _set_session_last_active(session)
    return ok


def send_prompt_to_session(session: str, text: str) -> bool:
    """テキストを tmux セッションの kiro-cli ペインに送信する（応答待ちはしない）。

    セッション名が渡された場合は _resolve_target_pane() で kiro-cli ペインを
    特定してから送信する（コントローラーペインへの誤送信を防ぐ）。
    """
    pane_id = _resolve_target_pane(session)
    if pane_id is None:
        print(f"[agent-loop] ERROR: kiro-cli ペインが見つかりません (target={session})", file=sys.stderr)
        return False

    single_line = " ".join(text.splitlines()).strip()
    short = single_line[:80] + ("..." if len(single_line) > 80 else "")
    print(f"[agent-loop] 送信: {short} (pane={pane_id})", file=sys.stderr)

    r = _tmux_cmd("send-keys", "-t", pane_id, "--", single_line, "Enter")
    if r.returncode != 0:
        print(f"[agent-loop] ERROR: send-keys に失敗しました: {r.stderr.strip()}", file=sys.stderr)
        return False

    return True


def _resolve_prompt_text(prompt_arg: str, cwd: Path) -> str:
    """プロンプト引数を解決して送信テキストを返す。

    解決順序:
    1. ファイルとして存在する → kiro-cli にファイル内容の実行を指示
    2. .agent/agent-loop.yml の定期プロンプト名と一致する → そのプロンプトテキスト
    3. そのまま自然文として使用
    """
    candidate = Path(prompt_arg).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    if candidate.is_file():
        content = candidate.read_text(encoding="utf-8").strip()
        return f"以下のファイルの内容を読んで実行してください:\n\n{content}"

    ws_prompts = load_prompt_config(str(cwd))
    for p in ws_prompts:
        if p.get("name") == prompt_arg:
            return str(p.get("prompt", "")).strip()

    return prompt_arg


# ---------------------------------------------------------------------------
# ls / send サブコマンド
# ---------------------------------------------------------------------------

def cmd_ls() -> None:
    """agent-loop send -s PANE_ID で指定するペインIDをプロンプト名付きで表示する。"""
    states = _read_all_states()
    if states:
        all_sessions = [s for st in states for s in st.get("sessions", [])]
        col_name = max((len(s.get("name", "")) for s in all_sessions), default=10)
        col_name = max(col_name, 12)
        print(f"{'プロンプト名':<{col_name}}  {'pane':>6}  状態")
        print("-" * (col_name + 16))
        for state in states:
            for s in state.get("sessions", []):
                name = str(s.get("name", ""))
                pane = str(s.get("pane", "")) or "-"
                alive = "alive" if s.get("alive") else "dead"
                print(f"{name:<{col_name}}  {pane:>6}  {alive}")
        return

    # デーモンが動いていない場合: tmuxから全ペインを直接取得
    tmux_bin = shutil.which("tmux")
    if tmux_bin is None:
        print("[agent-loop] ERROR: tmux が見つかりません。", file=sys.stderr)
        return

    result = subprocess.run(
        [tmux_bin, "list-sessions", "-F", "#{session_name}"],
        check=False,
        text=True,
        capture_output=True,
    )

    if result.returncode != 0 or not result.stdout.strip():
        print("実行中の kiro セッションはありません。")
        return

    kiro_sessions = [s.strip() for s in result.stdout.splitlines() if s.strip().startswith("kiro")]

    if not kiro_sessions:
        print("実行中の kiro セッションはありません。")
        return

    # セッション内の全非コントローラーペインを列挙
    rows: list[tuple[str, str]] = []
    for session in kiro_sessions:
        r = _tmux_cmd(
            "list-panes", "-t", session, "-F",
            "#{pane_id}\t#{pane_current_command}\t#{pane_dead}",
        )
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            pane_id, command, dead = parts[0], parts[1], parts[2]
            if dead == "1" or command.startswith("python"):
                continue
            rows.append((pane_id, session))

    if not rows:
        print("実行中の kiro ペインはありません。")
        return

    col_sess = max(len(r[1]) for r in rows)
    col_sess = max(col_sess, 20)
    print(f"{'pane':>6}  {'セッション'}  ")
    print("-" * (col_sess + 10))
    for pane_id, session in rows:
        print(f"{pane_id:>6}  {session}")
    print()
    print("送信: agent-loop send -s PANE_ID テキスト")
    print("例:   agent-loop send -s %12 確認してください")


# ---------------------------------------------------------------------------
# デーモン状態ファイルのユーティリティ
# ---------------------------------------------------------------------------

def _read_all_states() -> list[dict[str, Any]]:
    """生きている agent-loop デーモンの状態ファイルを全て読んで返す。"""
    if not _STATE_DIR.exists():
        return []
    results = []
    for f in sorted(_STATE_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    results.append(data)
                except ProcessLookupError:
                    f.unlink(missing_ok=True)
                except PermissionError:
                    results.append(data)
        except Exception:
            pass
    return results


def _pane_is_busy(pane_id: str) -> bool:
    """スロットファイルを参照してペインが処理中かを判断する。

    スロットファイルが存在しない場合（max_concurrent=0 など）は False を返す。
    """
    return GlobalSemaphore.is_busy(pane_id)


def _find_managing_daemon(pane_id: str) -> dict[str, Any] | None:
    """指定ペインを管理しているデーモンの状態データを返す。"""
    for state in _read_all_states():
        for session in state.get("sessions", []):
            if session.get("pane") == pane_id:
                return state
    return None


def _try_acquire_slot_for_send(pane_id: str) -> bool:
    """cmd_send 用のスロットファイルを書き込む（max_concurrent > 0 のデーモン管理下のみ）。

    スロットファイルに管理デーモンの PID を設定することで、デーモンの SlotMonitor が
    kiro-cli のプロンプト復帰を検知した際に適切に解放できるようにする。
    max_concurrent=0 のデーモンはスロットを使わないため書き込まない（放置ファイル防止）。
    同時実行数上限に達している場合は False を返す。
    """
    daemon_state = _find_managing_daemon(pane_id)
    if daemon_state is None:
        return True

    cwd = daemon_state.get("cwd", "")
    if not cwd:
        return True

    daemon_pid = int(daemon_state.get("pid", 0))
    if daemon_pid <= 0:
        return True

    try:
        config, _, _ = load_config(Path(cwd))
        max_concurrent = int(config.get("max_concurrent", 0))
        if max_concurrent <= 0:
            return True
        slot_timeout = int(config.get("slot_timeout_seconds", _DEFAULT_SLOT_TIMEOUT))
        cooldown = int(config.get("cooldown_seconds", 0))
    except Exception:
        return True

    semaphore = GlobalSemaphore(max_concurrent, slot_timeout, cooldown)
    if semaphore.acquire(pane_id, pid=daemon_pid):
        log.debug("cmd_send: スロットを取得しました (pane=%s, daemon_pid=%d)", pane_id, daemon_pid)
        return True
    else:
        log.warning("cmd_send: 同時実行数が上限 (%d) に達しています (pane=%s)", max_concurrent, pane_id)
        return False


def cmd_slot_release() -> None:
    """$TMUX_PANE に対応するセマフォスロットを解放する（kiro-cli agent hook から呼び出される）。"""
    pane_env = os.environ.get("TMUX_PANE", "")
    if not pane_env:
        sys.exit(0)
    cooldown_seconds = 0
    try:
        config, _, _ = load_config(Path.cwd())
        cooldown_seconds = int(config.get("cooldown_seconds", 0))
    except Exception:
        pass
    GlobalSemaphore(0, cooldown_seconds=cooldown_seconds).release(pane_env)
    sys.exit(0)


def _resolve_send_entry_id(prompt_arg: str, prompt_text: str, target: str, cwd: Path) -> tuple[str, str]:
    """(entry_id, resolved_prompt)。スケジュール名一致ならその entry id。"""
    ws_prompts = load_prompt_config(str(cwd))
    for p in ws_prompts:
        if p.get("name") == prompt_arg:
            eid = str(p.get("id") or p.get("name") or target)
            return eid, str(p.get("prompt", "")).strip() or prompt_text
    # 共通 config の prompts も見る
    try:
        cfg, _, _ = load_config(cwd)
        for p in cfg.get("prompts") or []:
            if isinstance(p, dict) and p.get("name") == prompt_arg:
                eid = str(p.get("id") or p.get("name") or target)
                return eid, str(p.get("prompt", "")).strip() or prompt_text
    except Exception:
        pass
    return target, prompt_text


def _wait_for_send_completion(
    pane_id: str,
    *,
    response_timeout: float,
    failure_pattern: str | None,
) -> int:
    """busy→ready を待つ。0=ready, 1=death/failure, 2=timeout。"""
    deadline = time.time() + max(response_timeout, 1.0)
    saw_busy = False
    fail_re = None
    if failure_pattern:
        try:
            fail_re = re.compile(failure_pattern, re.IGNORECASE | re.MULTILINE)
        except re.error:
            fail_re = None

    while time.time() < deadline:
        # pane 生存
        r = _tmux_cmd("display-message", "-p", "-t", pane_id, "#{pane_id}")
        if r.returncode != 0:
            print(f"[agent-loop] ERROR: ペイン {pane_id} が終了しました", file=sys.stderr)
            return 1
        content = _capture_pane(pane_id)
        if fail_re is not None and fail_re.search(content):
            print("[agent-loop] ERROR: failure_pattern に一致しました", file=sys.stderr)
            return 1
        busy_slot = _pane_is_busy(pane_id)
        ready = _pane_has_prompt(content)
        if busy_slot or not ready:
            saw_busy = True
        elif saw_busy and ready:
            return 0
        time.sleep(0.5)

    print(f"[agent-loop] ERROR: response_timeout ({int(response_timeout)}秒) を超過しました", file=sys.stderr)
    return 2


def cmd_send(args: argparse.Namespace, cwd: Path) -> None:
    """プロンプトを永続 send-request キューへ投入する（daemon が配送）。"""
    try:
        send_config, _, _ = load_config(cwd)
    except Exception:
        send_config = {}
    # 待機判定用にプロファイルを初期化（--wait 時）
    _init_cli_profile_from_config(send_config, project_dir=cwd, strict=False)

    prompt_arg = " ".join(args.prompt).strip()
    if not prompt_arg:
        print("[agent-loop] ERROR: プロンプトが空です。", file=sys.stderr)
        sys.exit(1)

    target = getattr(args, "session", None)
    if not target:
        states = _read_all_states()
        alive_sessions = [
            s for st in states for s in st.get("sessions", [])
            if s.get("alive") and s.get("pane")
        ]
        if len(alive_sessions) == 1:
            target = alive_sessions[0]["pane"]
            print(
                f"[agent-loop] 送信先ペインを自動解決: {target} ({alive_sessions[0].get('name')})",
                file=sys.stderr,
            )
        elif len(alive_sessions) > 1:
            print("[agent-loop] 複数のペインが動作中です。-s PANE_ID で送信先を指定してください:", file=sys.stderr)
            for s in alive_sessions:
                print(f"  {s['pane']}  ({s.get('name', '')})", file=sys.stderr)
            print("例: agent-loop send -s %12 テキスト", file=sys.stderr)
            sys.exit(1)

    if not target:
        target = _DEFAULT_SEND_SESSION

    work_dir: Path | None = None
    raw_dir = getattr(args, "dir", None)
    if raw_dir:
        work_dir = Path(raw_dir).expanduser().resolve()
        if not work_dir.is_dir():
            print(f"[agent-loop] ERROR: ディレクトリが存在しません: {work_dir}", file=sys.stderr)
            sys.exit(1)

    prompt_text = _resolve_prompt_text(prompt_arg, cwd)
    entry_id, prompt_text = _resolve_send_entry_id(prompt_arg, prompt_text, target, cwd)
    priority = str(getattr(args, "priority", None) or "normal")
    if priority not in ("high", "normal", "low"):
        print(f"[agent-loop] ERROR: --priority は high|normal|low です: {priority}", file=sys.stderr)
        sys.exit(1)

    # CLI 側 debounce（同一 entry+prompt が直近 3 秒）
    dedupe = _dedupe_key(entry_id, prompt_text)
    if not hasattr(cmd_send, "_debouncer"):
        cmd_send._debouncer = RequestDebouncer()  # type: ignore[attr-defined]
    if cmd_send._debouncer.is_duplicate(dedupe):  # type: ignore[attr-defined]
        print("[agent-loop] 同一内容の送信要求を debounce しました（受付済み扱い）", file=sys.stderr)
        sys.exit(0)

    req = write_send_request(
        entry_id=entry_id,
        prompt=prompt_text,
        cwd=str(work_dir) if work_dir else None,
        priority=priority,
        meta={"target": target, "workspace": str(cwd.resolve())},
    )
    print(
        f"[agent-loop] 送信要求を受け付けました (request_id={req['id']} entry_id={entry_id} priority={priority})",
        file=sys.stderr,
    )

    if not getattr(args, "wait", False):
        sys.exit(0)

    # --wait: 対象ペインの busy→ready
    pane_id = _resolve_target_pane(target) if not str(target).startswith("%") else str(target)
    if pane_id is None and str(target).startswith("%"):
        pane_id = str(target)
    if pane_id is None:
        # セッション名のまま待つ
        pane_id = str(target)

    response_timeout = float(
        getattr(args, "response_timeout", None)
        or send_config.get("response_timeout", 600)
        or 600
    )
    failure_pattern = None
    profile = globals().get("_CLI_PROFILE")
    if profile is not None:
        failure_pattern = getattr(profile, "failure_pattern", None)

    # キュー受付直後はまだ ready のままなので、busy 遷移を待つ
    code = _wait_for_send_completion(
        pane_id,
        response_timeout=response_timeout,
        failure_pattern=failure_pattern if isinstance(failure_pattern, str) else None,
    )
    sys.exit(code)


def cmd_msg(args: argparse.Namespace) -> None:
    """msg サブコマンド: エージェントの受信ボックスにメッセージを投函する。"""
    to_agent = args.to
    from_agent = args.from_agent or "unknown"
    subject = args.subject or ""
    reply_to_id = args.reply_to or ""
    correlation_id = args.correlation_id or uuid.uuid4().hex

    # ボディの解決（引数 or ファイル）
    body_arg = " ".join(args.body) if args.body else ""
    body = body_arg.strip()
    if not body:
        print("[agent-loop msg] ERROR: メッセージ本文を指定してください。", file=sys.stderr)
        sys.exit(1)

    # ファイルパスとして解釈を試みる
    maybe_file = Path(body_arg.strip())
    if maybe_file.is_file():
        body = maybe_file.read_text(encoding="utf-8").strip()

    inbox_dir = _AGENTS_DIR / to_agent / "inbox"
    try:
        inbox_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[agent-loop msg] ERROR: 受信ボックスの作成に失敗しました: {exc}", file=sys.stderr)
        sys.exit(1)

    msg_id = uuid.uuid4().hex
    ts = int(time.time())
    msg_file = inbox_dir / f"{ts}_{msg_id}.json"

    payload: dict[str, Any] = {
        "id": msg_id,
        "from": from_agent,
        "to": to_agent,
        "created_at": float(ts),
        "subject": subject,
        "body": body,
        "reply_to": reply_to_id or None,
        "correlation_id": correlation_id,
        "cwd": str(Path.cwd()),
    }

    try:
        msg_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[agent-loop msg] ERROR: メッセージファイルの書き込みに失敗しました: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"[agent-loop msg] メッセージを投函しました → {msg_file}", file=sys.stderr)
    print(f"  to:      {to_agent}", file=sys.stderr)
    print(f"  from:    {from_agent}", file=sys.stderr)
    if subject:
        print(f"  subject: {subject}", file=sys.stderr)
    print(f"  id:      {msg_id}", file=sys.stderr)


def cmd_agents() -> None:
    """agents サブコマンド: 登録済みエージェントの一覧を表示する。"""
    if not _AGENTS_DIR.exists():
        print("(登録済みエージェントはありません)")
        return

    agents = [d for d in _AGENTS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not agents:
        print("(登録済みエージェントはありません)")
        return

    for agent_dir in sorted(agents):
        inbox = agent_dir / "inbox"
        pending = len(list(inbox.glob("*.json"))) if inbox.exists() else 0
        processed_dir = inbox / ".processed"
        processed = len(list(processed_dir.glob("*.json"))) if processed_dir.exists() else 0
        print(f"  {agent_dir.name}  (inbox: {pending} pending, {processed} processed)")


