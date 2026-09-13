"""Host-side session receipt. Runs on the CLI host, including WSL."""
import json
import fcntl
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


def write_json(file, data):
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=file.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
        os.replace(name, file)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def locked(file):
    with open(str(file) + ".lock", "a") as stream:
        os.chmod(stream.name, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def record(file, token, cli, chained, payload):
    try:
        event = json.loads(payload)
        sid = event.get("thread-id") if cli == "codex" else event.get("session_id")
        if cli == "codex" and event.get("type") != "agent-turn-complete":
            return
        with locked(file):
            saved = json.loads(Path(file).read_text())
            if saved.get("token") == token and isinstance(sid, str) and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,200}", sid):
                write_json(file, {"token": token, "id": sid})
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    # A user's existing notification still receives the original payload.
    if chained:
        try:
            subprocess.run([*chained, payload], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass


def prepare(opts):
    cli, argv = opts["cli"], list(opts["argv"])
    env = dict(opts.get("env") or {})
    runtime = Path(opts["runtime"])
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    file = runtime / "session.json"
    token = opts["token"]
    hook = [sys.executable, str(Path(__file__).resolve()), "record", str(file), token, cli,
            json.dumps(opts.get("chained") or [])]
    if cli == "codex":
        argv.extend(["--config", "notify=" + json.dumps(hook)])
    elif cli == "kiro":
        if "--v3" in argv or "--agent-engine=v3" in argv:
            raise ValueError("Kiro v3 のセッション記録には未対応です")
        source = Path(env.get("KIRO_HOME") or os.environ.get("KIRO_HOME") or Path.home() / ".kiro")
        private = runtime / "kiro"
        private.mkdir(exist_ok=True, mode=0o700)
        # Keep settings, credentials, skills and the session store at their original paths.
        # Only the generated agent definition belongs to this app conversation.
        if source.is_dir():
            for item in source.iterdir():
                target = private / item.name
                if item.name != "agents" and not target.exists() and not target.is_symlink():
                    target.symlink_to(item.resolve(), target_is_directory=item.is_dir())
        agents = private / "agents"
        agents.mkdir(exist_ok=True, mode=0o700)
        if (source / "agents").is_dir():
            for item in (source / "agents").iterdir():
                target = agents / item.name
                if item.name != "agent-app.json" and not target.exists() and not target.is_symlink():
                    target.symlink_to(item.resolve())
        selected, position = "default", None
        for settings in (source / "settings/cli.json", Path(opts["cwd"]) / ".kiro/settings/cli.json"):
            if settings.is_file():
                selected = json.loads(settings.read_text()).get("chat.defaultAgent") or selected
        for index, arg in enumerate(argv):
            if arg == "--agent" and index + 1 < len(argv):
                selected, position = argv[index + 1], index + 1
            elif arg.startswith("--agent="):
                selected, position = arg.split("=", 1)[1], index
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", selected):
            raise ValueError("Kiro のエージェント名を読み取れません")
        cwd = Path(opts["cwd"])
        candidates = [cwd / ".kiro" / "agents" / (selected + ".json"), source / "agents" / (selected + ".json")]
        agent_file = next((item for item in candidates if item.is_file()), None)
        if (position is not None or selected != "default") and agent_file is None:
            raise ValueError("Kiro のカスタムエージェントが見つかりません: " + selected)
        agent = json.loads(agent_file.read_text()) if agent_file else {"includeMcpJson": True}
        hooks = agent.setdefault("hooks", {})
        for event in ("agentSpawn", "stop"):
            hooks.setdefault(event, []).append({"command": shlex.join(hook)})
        resources = agent.setdefault("resources", [])
        for uri in ("file://" + str(source / "steering/**/*.md"),
                    "skill://" + str(source / "skills/**/SKILL.md"),
                    "file://" + str(cwd / ".kiro/steering/**/*.md"),
                    "skill://" + str(cwd / ".kiro/skills/**/SKILL.md"),
                    "file://" + str(cwd / "AGENTS.md")):
            if uri not in resources:
                resources.append(uri)
        agent["name"] = "agent-app"
        write_json(agents / "agent-app.json", agent)
        if position is None:
            argv.extend(["--agent", "agent-app"])
        elif argv[position].startswith("--agent="):
            argv[position] = "--agent=agent-app"
        else:
            argv[position] = "agent-app"
        env["KIRO_HOME"] = str(private)
    with locked(file):
        write_json(file, {"token": token, "id": ""})
    return {"argv": argv, "env": env, "file": str(file)}


if __name__ == "__main__":
    if sys.argv[1] == "prepare":
        print(json.dumps(prepare(json.loads(sys.argv[2]))))
    elif sys.argv[1] == "record":
        record(*sys.argv[2:5], json.loads(sys.argv[5]), sys.argv[6] if len(sys.argv) > 6 else sys.stdin.read())
