from __future__ import annotations
# doctor.py — 診断コマンド（YAML / tmux / state / slot / send-request）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。

_REQUIRED_DIRS = (
    "send-requests",
    "loop-state",
    "slots",
    "loop-commands",
    "loop-control",
    "loop-adaptive",
)

_TURN_HOOK_ASSETS = {
    "kiro": ("kiro/agent-loop.json",),
    "claude": ("claude/.claude-plugin/plugin.json", "claude/hooks/hooks.json"),
    "copilot": ("copilot/plugin.json", "copilot/hooks.json"),
}


def _finding(
    fid: str,
    severity: str,
    evidence: str,
    *,
    fixable: bool = False,
) -> dict[str, Any]:
    return {
        "id": fid,
        "severity": severity,
        "evidence": evidence,
        "fixable": fixable,
    }


def _check_config_load(cwd: Path, agent_home: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    # load_config と同じ候補を見る: workspace 直下 → workspace/.agents → agent_home。
    # 以前は workspace/.agents を見ておらず、デーモンが実際に読むファイルの
    # 設定エラーを doctor が報告できなかった。
    search_paths: list[Path] = []
    local = find_default_config(cwd)
    if local is not None:
        search_paths.append(local)
    if cwd.resolve() != Path.home().resolve():
        ws_agents = find_default_config(cwd / AGENT_HOME)
        if ws_agents is not None:
            search_paths.append(ws_agents)
    search_paths.extend(agent_home / name for name in DEFAULT_CONFIG_NAMES)

    found_any = False
    for path in search_paths:
        if not path.is_file():
            continue
        found_any = True
        try:
            _load_config_file(path)
        except Exception as exc:  # noqa: BLE001
            findings.append(_finding(
                "config.load",
                "error",
                f"{path}: {exc}",
            ))
        else:
            findings.append(_finding(
                "config.load",
                "info",
                f"設定ファイルを読み込めました: {path}",
            ))
            config = _load_config_file(path)
            agent_cli = str(config.get("agent_cli") or "").strip()
            if agent_cli:
                agent_file = _AGENTS_DIR / f"{agent_cli}.json"
                if not agent_file.is_file():
                    findings.append(_finding(
                        "agent_cli.definition",
                        "error",
                        f"agent_cli '{agent_cli}' の定義が見つかりません: {agent_file}",
                    ))
                else:
                    findings.append(_finding(
                        "agent_cli.definition",
                        "info",
                        f"agent_cli 定義: {agent_file}",
                    ))
                try:
                    _resolve_cli_profile(config, project_dir=cwd)
                except CliProfileError as exc:
                    findings.append(_finding(
                        "agent_cli.resolve",
                        "error",
                        str(exc),
                    ))

    if not found_any:
        findings.append(_finding(
            "config.missing",
            "warning",
            f"設定ファイルが見つかりません（探索: {', '.join(str(p) for p in search_paths)}）",
        ))

    findings.append(_finding(
        "config.search_paths",
        "info",
        ", ".join(str(p) for p in search_paths),
    ))
    return findings


def _check_commands(config: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if shutil.which("tmux") is None:
        findings.append(_finding("tmux.missing", "error", "tmux が PATH にありません"))
    else:
        findings.append(_finding("tmux.present", "info", "tmux は利用可能です"))

    agent_cli = str(config.get("agent_cli") or "").strip()
    if agent_cli:
        try:
            profile = _resolve_cli_profile(config)
            binary = profile.argv[0] if profile.argv else agent_cli
        except CliProfileError:
            binary = agent_cli
        if shutil.which(binary) is None and not Path(binary).expanduser().is_file():
            findings.append(_finding(
                "command.agent_cli",
                "error",
                f"エージェント CLI '{binary}' が PATH にありません",
            ))
        else:
            findings.append(_finding(
                "command.agent_cli",
                "info",
                f"エージェント CLI: {binary}",
            ))
    else:
        if shutil.which("kiro-cli") is None:
            findings.append(_finding(
                "command.kiro_cli",
                "error",
                "kiro-cli が PATH にありません",
            ))
        else:
            findings.append(_finding(
                "command.kiro_cli",
                "info",
                "kiro-cli は利用可能です",
            ))
    return findings


def _check_directories(agent_home: Path, *, fix: bool) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for name in _REQUIRED_DIRS:
        path = agent_home / name
        if not path.exists():
            findings.append(_finding(
                f"dir.missing.{name}",
                "warning",
                f"ディレクトリがありません: {path}",
                fixable=True,
            ))
            if fix:
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    findings.append(_finding(
                        f"dir.created.{name}",
                        "info",
                        f"ディレクトリを作成しました: {path}",
                    ))
                except OSError as exc:
                    findings.append(_finding(
                        f"dir.create_failed.{name}",
                        "error",
                        f"{path}: {exc}",
                    ))
        elif not os.access(path, os.W_OK):
            findings.append(_finding(
                f"dir.not_writable.{name}",
                "error",
                f"書き込み不可: {path}",
            ))
        else:
            findings.append(_finding(
                f"dir.ok.{name}",
                "info",
                f"ディレクトリ OK: {path}",
            ))
    return findings


def _check_turn_hook_assets(adapter: str, assets_dir: Path) -> list[dict[str, Any]]:
    required = _TURN_HOOK_ASSETS.get(str(adapter), ())
    if not required:
        return [_finding(
            "turn_hook.assets_not_required", "info",
            f"{adapter}: external asset は不要です",
        )]
    missing = [name for name in required if not (Path(assets_dir) / name).is_file()]
    if missing:
        return [_finding(
            "turn_hook.assets_missing", "warning",
            f"{adapter}: {', '.join(missing)}（画面監視へfallbackします）",
        )]
    return [_finding(
        "turn_hook.assets_ok", "info", f"{adapter}: {Path(assets_dir)}",
    )]


def _check_turn_hook_runtime(runtime_dir: Path) -> list[dict[str, Any]]:
    if not runtime_dir.is_dir():
        return []
    unsafe = []
    for path in [*runtime_dir.iterdir(), *runtime_dir.glob("*/active/*.json"),
                 *runtime_dir.glob("*/events/*.json")]:
        try:
            if path.stat().st_mode & 0o077:
                unsafe.append(str(path))
        except OSError:
            pass
    if unsafe:
        return [_finding(
            "turn_hook.runtime_permissions", "warning",
            f"group/other権限があります: {', '.join(unsafe[:5])}",
        )]
    return [_finding("turn_hook.runtime_ok", "info", f"runtime権限 OK: {runtime_dir}")]


def _check_daemon_state(state_dir: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not state_dir.is_dir():
        findings.append(_finding("daemon_state.missing_dir", "warning", f"loop-state がありません: {state_dir}"))
        return findings

    states = []
    for path in sorted(state_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            alive = _pid_alive(pid) if pid > 0 else False
            states.append((path.name, pid, alive, data))
        except (OSError, json.JSONDecodeError, ValueError):
            findings.append(_finding(
                "daemon_state.corrupt",
                "warning",
                f"破損した状態ファイル: {path}",
            ))

    if not states:
        findings.append(_finding("daemon_state.empty", "info", "稼働中のデーモン状態ファイルはありません"))
        return findings

    for name, pid, alive, data in states:
        sev = "info" if alive else "warning"
        findings.append(_finding(
            "daemon_state.process",
            sev,
            f"{name}: pid={pid} alive={alive} cwd={data.get('cwd', '')}",
        ))
        for session in data.get("sessions") or []:
            pane = session.get("pane", "")
            session_alive = bool(session.get("alive"))
            findings.append(_finding(
                "daemon_state.pane",
                "info" if session_alive else "warning",
                f"  session={session.get('name', '')} pane={pane} alive={session_alive}",
            ))
    return findings


def _check_slots(
    slots_dir: Path,
    slot_timeout: int,
    *,
    fix: bool,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not slots_dir.is_dir():
        findings.append(_finding("slots.missing_dir", "warning", f"slots がありません: {slots_dir}", fixable=True))
        if fix:
            slots_dir.mkdir(parents=True, exist_ok=True)
        return findings

    now = time.time()
    for slot_file in sorted(slots_dir.glob("pane_*.json")):
        try:
            data = json.loads(slot_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            findings.append(_finding(
                "slots.corrupt",
                "warning",
                f"破損スロット: {slot_file.name}",
                fixable=True,
            ))
            if fix:
                slot_file.unlink(missing_ok=True)
            continue

        pid = int(data.get("pid", 0))
        acquired_at = float(data.get("acquired_at", 0))
        elapsed = now - acquired_at
        alive = _pid_alive(pid) if pid > 0 else False
        timed_out = elapsed > slot_timeout

        if pid > 0 and not alive:
            findings.append(_finding(
                "slots.dead_pid",
                "warning",
                f"{slot_file.name}: pid={pid} は死亡",
                fixable=True,
            ))
            if fix:
                slot_file.unlink(missing_ok=True)
        elif timed_out and alive:
            findings.append(_finding(
                "slots.alive_past_timeout",
                "warning",
                f"{slot_file.name}: pid={pid} 生存・timeout超過 ({elapsed:.0f}s)",
            ))
        elif timed_out:
            findings.append(_finding(
                "slots.expired",
                "warning",
                f"{slot_file.name}: 期限切れ ({elapsed:.0f}s)",
                fixable=True,
            ))
            if fix:
                slot_file.unlink(missing_ok=True)
        else:
            findings.append(_finding(
                "slots.active",
                "info",
                f"{slot_file.name}: pid={pid} active ({elapsed:.0f}s)",
            ))
    return findings


def _check_send_requests(send_dir: Path, *, fix: bool) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not send_dir.is_dir():
        findings.append(_finding(
            "send_requests.missing_dir",
            "warning",
            f"send-requests がありません: {send_dir}",
            fixable=True,
        ))
        if fix:
            send_dir.mkdir(parents=True, exist_ok=True)
        return findings

    pending = 0
    for path in sorted(send_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            findings.append(_finding(
                "send_requests.corrupt",
                "warning",
                f"破損 send-request: {path.name}",
                fixable=True,
            ))
            if fix:
                _isolate_invalid(path)
            continue
        if not isinstance(data, dict) or "prompt" not in data:
            findings.append(_finding(
                "send_requests.invalid",
                "warning",
                f"不正 send-request: {path.name}",
                fixable=True,
            ))
            if fix:
                _isolate_invalid(path)
            continue
        pending += 1

    findings.append(_finding(
        "send_requests.pending",
        "info",
        f"未処理 send-request: {pending} 件",
    ))
    return findings


def run_doctor_checks(
    *,
    cwd: Path | None = None,
    agent_home: Path | None = None,
    slots_dir: Path | None = None,
    send_requests_dir: Path | None = None,
    state_dir: Path | None = None,
    slot_timeout: int = _DEFAULT_SLOT_TIMEOUT,
    fix: bool = False,
) -> list[dict[str, Any]]:
    """診断を実行して findings リストを返す（テスト・cmd_doctor 共用）。"""
    work_cwd = Path(cwd or Path.cwd()).resolve()
    home = Path(agent_home) if agent_home is not None else agent_home_dir()
    slots = Path(slots_dir) if slots_dir is not None else _SLOTS_DIR
    send_root = Path(send_requests_dir) if send_requests_dir is not None else _SEND_REQUESTS_DIR
    loop_state = Path(state_dir) if state_dir is not None else _STATE_DIR

    config: dict[str, Any] = {}
    config_path = find_default_config(home)
    if config_path and config_path.is_file():
        try:
            config = _load_config_file(config_path)
        except Exception:  # noqa: BLE001
            pass

    findings: list[dict[str, Any]] = []
    findings.extend(_check_directories(home, fix=fix))
    findings.extend(_check_config_load(work_cwd, home))
    findings.extend(_check_commands(config))
    adapter = "kiro"
    agent_cli = str(config.get("agent_cli") or "").strip()
    if agent_cli:
        try:
            adapter = str(_resolve_cli_profile(config, project_dir=work_cwd).turn_completion or "")
        except CliProfileError:
            adapter = ""
    if adapter:
        findings.extend(_check_turn_hook_assets(adapter, _turn_hook_assets_dir()))
    findings.extend(_check_turn_hook_runtime(home / "loop-hooks"))
    findings.extend(_check_daemon_state(loop_state))
    findings.extend(_check_slots(slots, slot_timeout, fix=fix))
    findings.extend(_check_send_requests(send_root, fix=fix))
    return findings


def _print_doctor_summary(findings: list[dict[str, Any]]) -> None:
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    infos = sum(1 for f in findings if f["severity"] == "info")
    print(f"agent-loop doctor: {errors} error(s), {warnings} warning(s), {infos} info")
    for item in findings:
        if item["severity"] == "info":
            continue
        tag = item["severity"].upper()
        fixable = " [fixable]" if item.get("fixable") else ""
        print(f"  [{tag}]{fixable} {item['id']}: {item['evidence']}")


def cmd_doctor(args: argparse.Namespace) -> None:
    cwd = Path.cwd()
    # 設定が壊れていても doctor 自身は落とさない——壊れていることの診断は
    # _check_config_load が finding（config.load）として報告する。
    try:
        config, _, _ = load_config(cwd)
    except Exception as exc:  # noqa: BLE001
        log.warning("設定ファイルを読み込めないため既定値で診断します: %s", exc)
        config = {}
    slot_timeout = int(config.get("slot_timeout_seconds", _DEFAULT_SLOT_TIMEOUT))

    if getattr(args, "fix", False):
        cleanup_stale_slots_on_startup(
            slot_timeout=slot_timeout,
            cooldown_seconds=int(config.get("cooldown_seconds", 0)),
        )

    findings = run_doctor_checks(
        cwd=cwd,
        slot_timeout=slot_timeout,
        fix=getattr(args, "fix", False),
    )

    if getattr(args, "json", False):
        print(json.dumps({"findings": findings}, ensure_ascii=False, indent=2))
    else:
        _print_doctor_summary(findings)

    if any(f["severity"] == "error" for f in findings):
        sys.exit(1)
