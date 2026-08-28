from __future__ import annotations
# turnhooks.py — managed interactive CLI のターン完了 mailbox。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。

_TURN_HOOKS_DIR = agent_home_subdir("", "loop-hooks")
_TURN_HOOK_ID_RE = re.compile(r"[A-Za-z0-9_.:%-]{1,128}\Z")


def _turn_hook_assets_dir() -> Path:
    installed = Path(sys.argv[0]).expanduser().resolve().parent / "agent-hooks"
    source = Path(__file__).resolve().parents[1] / "agent-hooks"
    return installed if installed.is_dir() else source


def _turn_hook_executable() -> str:
    arg0 = Path(sys.argv[0]).expanduser().resolve()
    if arg0.name.startswith("agent-loop"):
        return str(arg0)
    return str(Path(__file__).resolve().parents[1] / "agent-loop.py")


def _private_tree(path: Path) -> None:
    for child in [path, *path.rglob("*")]:
        try:
            child.chmod(0o700 if child.is_dir() else 0o600)
        except OSError:
            pass


def _copy_private_path(source: Path, target: Path) -> None:
    if source.is_symlink() or source.name.lower() in {
        "auth.json", "credential.json", "credentials.json", "token.json", "tokens.json",
    }:
        return
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        for child in source.iterdir():
            _copy_private_path(child, target / child.name)
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source, target, follow_symlinks=False)


def _kiro_agent_name(argv: list[str]) -> tuple[str, int | None]:
    for index, arg in enumerate(argv):
        if arg == "--agent" and index + 1 < len(argv):
            return argv[index + 1], index + 1
        if arg.startswith("--agent="):
            return arg.split("=", 1)[1], index
    return "", None


def _prepare_kiro_launch(argv: list[str], env: dict[str, str], *, assets: Path,
                         runtime_root: Path, user_home: Path, cwd: Path):
    if "--v3" in argv:
        raise ValueError("Kiro CLI v3 turn hooks are not supported")
    private_home = runtime_root / f"kiro-{uuid.uuid4().hex}"
    private_home.mkdir(parents=True, mode=0o700)
    try:
        source_home = user_home / ".kiro"
        for name in ("agents", "prompts", "skills", "steering"):
            source = source_home / name
            if source.is_dir():
                _copy_private_path(source, private_home / name)
        for name in ("cli.json", "mcp.json"):
            source = source_home / "settings" / name
            if source.is_file() and not source.is_symlink():
                _copy_private_path(source, private_home / "settings" / name)

        template_path = assets / "kiro" / "agent-loop.json"
        template = json.loads(template_path.read_text(encoding="utf-8"))
        selected, selected_index = _kiro_agent_name(argv)
        if selected:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", selected):
                raise ValueError("Kiro custom agent name is not safely copyable")
            candidates = [
                cwd / ".kiro" / "agents" / f"{selected}.json",
                source_home / "agents" / f"{selected}.json",
            ]
            source_agent = next((path for path in candidates if path.is_file()), None)
            if source_agent is None or source_agent.is_symlink():
                raise ValueError(f"Kiro custom agent is not a copyable JSON file: {selected}")
            agent = json.loads(source_agent.read_text(encoding="utf-8"))
        else:
            agent = dict(template)
        if not isinstance(agent, dict):
            raise ValueError("Kiro custom agent must be a JSON object")

        hooks = agent.setdefault("hooks", {})
        resources = agent.setdefault("resources", [])
        if not isinstance(hooks, dict) or not isinstance(resources, list):
            raise ValueError("Kiro custom agent hooks/resources have invalid types")
        stop_hooks = hooks.setdefault("stop", [])
        template_hook = template["hooks"]["stop"][0]
        if not isinstance(stop_hooks, list):
            raise ValueError("Kiro custom agent hooks.stop must be an array")
        if not any(
            isinstance(item, dict) and item.get("command") == template_hook["command"]
            for item in stop_hooks
        ):
            stop_hooks.append(template_hook)

        inherited = [
            f"file://{private_home}/steering/**/*.md",
            f"skill://{private_home}/skills/**/SKILL.md",
            f"file://{cwd}/.kiro/steering/**/*.md",
            f"skill://{cwd}/.kiro/skills/**/SKILL.md",
            f"file://{cwd}/AGENTS.md",
        ]
        for resource in inherited:
            if resource not in resources:
                resources.append(resource)
        generated_name = f"agent-loop-{uuid.uuid4().hex[:12]}"
        agent["name"] = generated_name
        agent["includeMcpJson"] = True
        _write_private_json(private_home / "agents" / f"{generated_name}.json", agent)
        _private_tree(private_home)

        if selected_index is None:
            argv.extend(["--agent", generated_name])
        elif argv[selected_index].startswith("--agent="):
            argv[selected_index] = f"--agent={generated_name}"
        else:
            argv[selected_index] = generated_name
        env["KIRO_HOME"] = str(private_home)
        return argv, env, private_home
    except Exception:
        shutil.rmtree(private_home, ignore_errors=True)
        raise


def _codex_profile(argv: list[str], base: dict[str, Any]) -> str:
    for index, arg in enumerate(argv):
        if arg in {"--profile", "-p"} and index + 1 < len(argv):
            return str(argv[index + 1])
        if arg.startswith("--profile="):
            return arg.split("=", 1)[1]
    return str(base.get("profile") or "")


def _prepare_codex_launch(argv: list[str], env: dict[str, str], *,
                          executable: str, user_home: Path):
    for index, arg in enumerate(argv):
        value = argv[index + 1] if arg in {"--config", "-c"} and index + 1 < len(argv) else arg
        if value.startswith("notify=") or value.startswith("--config=notify="):
            raise ValueError("Codex notify is already overridden on the command line")
    try:
        import tomllib
    except ImportError as exc:
        raise ValueError("Codex notify requires Python 3.11+") from exc

    codex_home = Path(env.get("CODEX_HOME") or os.environ.get("CODEX_HOME")
                      or (user_home / ".codex"))
    config_path = codex_home / "config.toml"
    base: dict[str, Any] = {}
    if config_path.is_file():
        with config_path.open("rb") as stream:
            base = tomllib.load(stream)
    notify = base.get("notify")
    profile = _codex_profile(argv, base)
    if profile:
        profile_path = codex_home / f"{profile}.config.toml"
        if profile_path.is_file():
            with profile_path.open("rb") as stream:
                profile_config = tomllib.load(stream)
            if "notify" in profile_config:
                notify = profile_config["notify"]
        profiles = base.get("profiles") or {}
        if not isinstance(profiles, dict):
            raise ValueError("Codex profiles must be a table")
        inline_profile = profiles.get(profile)
        if isinstance(inline_profile, dict) and "notify" in inline_profile:
            notify = inline_profile["notify"]
    if notify is not None:
        if not isinstance(notify, list) or not all(isinstance(item, str) for item in notify):
            raise ValueError("Codex notify must be an array of strings")
        env["AGENT_LOOP_CODEX_NOTIFY"] = json.dumps(notify, ensure_ascii=False)

    command = [
        executable, "hook-event", "--adapter", "codex", "--status", "complete",
        "--native-event", "agent-turn-complete",
    ]
    argv.extend(["--config", f"notify={json.dumps(command, ensure_ascii=False)}"])
    return argv, env, None


def prepare_turn_hook_launch(*, adapter: str, argv: list[str],
                             env: dict[str, str], instance_id: str,
                             hook_token: str, executable: str | None = None,
                             assets_dir: Path | None = None,
                             runtime_root: Path | None = None,
                             user_home: Path | None = None,
                             cwd: Path | None = None):
    """managed interactive CLIへsession-local hookだけを加算する。"""
    adapter = str(adapter)
    # ollama は**自前の TUI** なので hook 資産（プラグイン・設定ファイル）が要らない。
    # 前面が我々のものなら、ターンの終わりに自分で `hook-event` を呼べる（設計 §7.3 A）。
    if adapter not in {"kiro", "claude", "codex", "copilot", "ollama"}:
        raise ValueError(f"unsupported turn-completion adapter: {adapter}")
    out_argv = list(argv)
    out_env = dict(env)
    out_env.update({
        "AGENT_LOOP_INSTANCE_ID": _turn_hook_id(instance_id),
        "AGENT_LOOP_HOOK_TOKEN": str(hook_token),
        "AGENT_LOOP_AGENT_CLI": adapter,
        "AGENT_LOOP_EXECUTABLE": str(executable or _turn_hook_executable()),
    })
    assets = Path(assets_dir) if assets_dir is not None else _turn_hook_assets_dir()
    root = Path(runtime_root) if runtime_root is not None else (
        _TURN_HOOKS_DIR / _turn_hook_id(instance_id) / "runtime"
    )

    if adapter == "kiro":
        return _prepare_kiro_launch(
            out_argv, out_env,
            assets=assets,
            runtime_root=root,
            user_home=Path(user_home) if user_home is not None else Path.home(),
            cwd=Path(cwd) if cwd is not None else Path.cwd(),
        )

    if adapter == "codex":
        return _prepare_codex_launch(
            out_argv, out_env,
            executable=out_env["AGENT_LOOP_EXECUTABLE"],
            user_home=Path(user_home) if user_home is not None else Path.home(),
        )

    if adapter in {"claude", "copilot"}:
        plugin = assets / adapter
        if not plugin.is_dir():
            raise FileNotFoundError(plugin)
        if not any(
            arg == "--plugin-dir" and i + 1 < len(out_argv)
            and Path(out_argv[i + 1]).resolve() == plugin.resolve()
            for i, arg in enumerate(out_argv)
        ):
            out_argv.extend(["--plugin-dir", str(plugin.resolve())])
        return out_argv, out_env, None

    return out_argv, out_env, None


def _turn_hook_id(value: Any) -> str:
    value = str(value)
    if not _TURN_HOOK_ID_RE.fullmatch(value):
        raise ValueError("invalid turn hook id")
    return value


def _turn_hook_paths(instance_id: str, pane_id: str, dispatch_id: str = ""):
    root = _TURN_HOOKS_DIR / _turn_hook_id(instance_id)
    active = root / "active" / f"{_turn_hook_id(pane_id)}.json"
    event = root / "events" / f"{_turn_hook_id(dispatch_id)}.json" if dispatch_id else None
    return active, event


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in (path.parent.parent, path.parent):
        try:
            directory.chmod(0o700)
        except OSError:
            pass
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        path.chmod(0o600)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _create_private_json(path: Path, data: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in (path.parent.parent, path.parent):
        try:
            directory.chmod(0o700)
        except OSError:
            pass
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmp, path)
            path.chmod(0o600)
            return True
        except FileExistsError:
            return False
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def write_turn_hook_active(*, instance_id: str, pane_id: str,
                           dispatch_id: str, generation: int,
                           agent_cli: str, hook_token: str) -> Path:
    active, _ = _turn_hook_paths(instance_id, pane_id)
    record = {
        "version": 1,
        "instance_id": _turn_hook_id(instance_id),
        "pane_id": _turn_hook_id(pane_id),
        "dispatch_id": _turn_hook_id(dispatch_id),
        "generation": int(generation),
        "agent_cli": str(agent_cli),
        "hook_token": str(hook_token),
        "failure_pending": False,
        "started_at": time.time(),
    }
    _write_private_json(active, record)
    return active


def record_turn_hook_event(*, adapter: str, status: str,
                           native_event: str = "") -> bool:
    """CLI hook を検証してイベントを記録する。不正・管理外なら何もしない。"""
    try:
        instance_id = os.environ["AGENT_LOOP_INSTANCE_ID"]
        pane_id = os.environ["TMUX_PANE"]
        active_path, _ = _turn_hook_paths(instance_id, pane_id)
        active = json.loads(active_path.read_text(encoding="utf-8"))
        token = os.environ["AGENT_LOOP_HOOK_TOKEN"]
        agent_cli = os.environ["AGENT_LOOP_AGENT_CLI"]
        if (
            status not in {"complete", "failure", "failure-hint"}
            or active.get("instance_id") != instance_id
            or active.get("pane_id") != pane_id
            or active.get("agent_cli") != agent_cli
            or adapter != agent_cli
            or not hmac.compare_digest(str(active.get("hook_token", "")), token)
        ):
            return False
        if status == "failure-hint":
            active["failure_pending"] = True
            _write_private_json(active_path, active)
            return True
        if active.get("failure_pending"):
            status = "failure"
        _, event_path = _turn_hook_paths(
            instance_id, pane_id, str(active["dispatch_id"]),
        )
        assert event_path is not None
        return _create_private_json(event_path, {
            "version": 1,
            "instance_id": instance_id,
            "pane_id": pane_id,
            "dispatch_id": active["dispatch_id"],
            "generation": active["generation"],
            "status": status,
            "adapter": adapter,
            "native_event": str(native_event),
            "occurred_at": time.time(),
        })
    except (KeyError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def handle_codex_notify(payload: str) -> bool:
    try:
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("type") != "agent-turn-complete":
            return False
    except (TypeError, json.JSONDecodeError):
        return False
    accepted = record_turn_hook_event(
        adapter="codex", status="complete", native_event="agent-turn-complete",
    )
    if not accepted:
        return False
    try:
        command = json.loads(os.environ.get("AGENT_LOOP_CODEX_NOTIFY", "null"))
        if isinstance(command, list) and command and all(isinstance(item, str) for item in command):
            subprocess.run(
                [*command, payload],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
    except (OSError, TypeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        pass
    return True


def handle_copilot_error(payload: str) -> bool:
    try:
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("recoverable") is not False:
            return False
    except (TypeError, json.JSONDecodeError):
        return False
    return record_turn_hook_event(
        adapter="copilot", status="failure-hint", native_event="errorOccurred",
    )


def claim_turn_hook_event(instance_id: str, pane_id: str,
                          dispatch_id: str, generation: int):
    """現在の dispatch と一致するイベントを一度だけ取り出す。"""
    try:
        active_path, event_path = _turn_hook_paths(instance_id, pane_id, dispatch_id)
        assert event_path is not None
        active = json.loads(active_path.read_text(encoding="utf-8"))
        event = json.loads(event_path.read_text(encoding="utf-8"))
        expected = (instance_id, pane_id, dispatch_id, int(generation))
        if tuple(active.get(k) for k in ("instance_id", "pane_id", "dispatch_id", "generation")) != expected:
            return None
        if tuple(event.get(k) for k in ("instance_id", "pane_id", "dispatch_id", "generation")) != expected:
            return None
        event_path.unlink()
        return event
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def turn_hook_failure_pending(instance_id: str, pane_id: str,
                              dispatch_id: str, generation: int) -> bool:
    try:
        active_path, _ = _turn_hook_paths(instance_id, pane_id)
        active = json.loads(active_path.read_text(encoding="utf-8"))
        expected = (instance_id, pane_id, dispatch_id, int(generation))
        actual = tuple(
            active.get(key) for key in ("instance_id", "pane_id", "dispatch_id", "generation")
        )
        return actual == expected and active.get("failure_pending") is True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def clear_turn_hook(instance_id: str, pane_id: str, dispatch_id: str) -> None:
    try:
        active_path, event_path = _turn_hook_paths(instance_id, pane_id, dispatch_id)
        active_path.unlink(missing_ok=True)
        if event_path is not None:
            event_path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def clear_turn_hook_pane(instance_id: str, pane_id: str) -> None:
    try:
        active_path, _ = _turn_hook_paths(instance_id, pane_id)
        active = json.loads(active_path.read_text(encoding="utf-8"))
        dispatch_id = str(active.get("dispatch_id") or "")
        if dispatch_id:
            clear_turn_hook(instance_id, pane_id, dispatch_id)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
