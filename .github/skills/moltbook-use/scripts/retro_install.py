#!/usr/bin/env python3
"""Moltbook の振り返り投稿（retro）を、Copilot アプリ・kiro-cli に組み込む／外す／点検する。

設計: docs/designs/moltbook-retro-design.md §9

  python retro_install.py install   --client copilot|kiro [--url URL --token TOKEN] [--v3]
  python retro_install.py uninstall --client copilot|kiro
  python retro_install.py doctor    --client copilot|kiro

Copilot は Windows で、kiro は WSL（か Linux）で実行する。Windows からまとめて入れるときは
retro_install.ps1 を使う（WSL 側はその中から呼ぶ）。

すること（install）:
  1. moltbook-use を <エージェントの置き場所>/skills/moltbook-use に写す（フックはこの写しを指す）
  2. 接続先（connections.yaml の moltbook）を書く。URL とトークンは引数か環境変数
     MOLTBOOK_URL / MOLTBOOK_TOKEN、無ければ一度だけ尋ねる
  3. Copilot: フックをまとめたプラグインと、それを載せたマーケットプレイスを作り、copilot CLI があれば入れる
     kiro:    既定のエージェントに印付きのフックを追記する（組み込みの既定なら moltbook エージェントを作る）
  4. 点検（doctor）を実行する

置いたもの・書き換えたものは <状態の置き場所>/install.json に記録し、uninstall はそれだけを戻す。
エージェント設定は、印（コマンドに moltbook_hook.py を含む）の付いたフックだけを足し引きする。
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_SRC = Path(__file__).resolve().parent.parent
SKILL_NAME = "moltbook-use"
MARK = "moltbook_hook.py"
PLUGIN = "moltbook-retro"
AGENT_DIRS = {"copilot": ".copilot", "kiro": ".kiro"}
# Copilot: フックのきっかけ → moltbook_hook.py の入口
COPILOT_HOOKS = {"sessionStart": "start", "userPromptSubmitted": "prompt", "userPromptTransformed": "prompt",
                 "postToolUse": "tool", "postToolUseFailure": "tool --failed", "agentStop": "stop",
                 "sessionEnd": "end"}
KIRO_V2_HOOKS = {"agentSpawn": "start", "userPromptSubmit": "prompt", "postToolUse": "tool", "stop": "stop"}
KIRO_V3_HOOKS = {"SessionStart": "start", "UserPromptSubmit": "prompt", "PostToolUse": "tool", "Stop": "stop",
                 "SessionEnd": "end"}
BUILTIN_AGENTS = {"", "null", "none", "kiro_default", "q_cli_default", "default"}
# 組み込みの既定エージェントの中身を kiro-cli から取り出せないときに使う、既知の既定
FALLBACK_AGENT = {
    "name": "moltbook", "description": "既定のエージェントに Moltbook の振り返り投稿のフックを足したもの",
    "tools": ["*"], "allowedTools": [],
    "resources": ["file://AGENTS.md", "file://README.md", "file://.kiro/steering/**/*.md"],
    "useLegacyMcpJson": True,
}


def agent_home(client: str) -> Path:
    return Path(os.environ.get("MOLTBOOK_AGENT_HOME") or Path.home() / AGENT_DIRS[client])


def skill_dir(client: str) -> Path:
    return agent_home(client) / "skills" / SKILL_NAME


def state_dir(client: str) -> Path:
    """フックと moltbook.py が使うのと同じ置き場所（写したスキルの moltbook_config で決める）。"""
    if os.environ.get("MOLTBOOK_HOME"):
        return Path(os.path.expanduser(os.environ["MOLTBOOK_HOME"]))
    scripts = skill_dir(client) / "scripts"
    if not (scripts / "moltbook_config.py").is_file():
        scripts = SKILL_SRC / "scripts"
    code, out = _run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                      "from moltbook_config import get_moltbook_home; print(get_moltbook_home())", str(scripts)],
                     cwd=str(Path.home()))
    lines = out.strip().splitlines()
    return Path(lines[-1]) if code == 0 and lines else Path.home() / ".moltbook"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _record(client: str) -> dict:
    return _load(state_dir(client) / "install.json", {})


def _run(cmd: list, timeout: int = 60, **kw) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)
    out = p.stdout or b""
    text = out.decode("utf-16-le") if out[:2] == b"\xff\xfe" else out.decode("utf-8", "replace")
    return p.returncode, (text + (p.stderr or b"").decode("utf-8", "replace")).replace("\x00", "").strip()


def hook_command(client: str, entry: str, shell: str = "sh") -> str:
    """フックから呼ぶコマンド。写したスキルの moltbook_hook.py を、いま動いている Python で呼ぶ。"""
    py, hook = sys.executable, skill_dir(client if client == "copilot" else "kiro") / "scripts" / MARK
    kind = "copilot" if client == "copilot" else client
    args = f"{entry} --client {kind}"
    if shell == "powershell":
        return f'& "{py}" "{hook}" {args}'
    return f'"{py}" "{hook}" {args}'


# --- 1. スキルを写す -----------------------------------------------------------------

def copy_skill(client: str) -> Path:
    dest = skill_dir(client)
    if dest.resolve() == SKILL_SRC.resolve():
        return dest   # 写した先から実行している
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SKILL_SRC, dest, ignore=shutil.ignore_patterns("__pycache__", "tests", "probe"))
    return dest


# --- 2. 接続先 -------------------------------------------------------------------

def ensure_yaml() -> bool:
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        code, out = _run([sys.executable, "-m", "pip", "install", "--user", "pyyaml"], timeout=300)
        if code != 0:
            print(f"  ✗ PyYAML を入れられませんでした: {out[-200:]}")
            return False
        return True


def write_connection(client: str, url: str, token: str) -> bool:
    if not (url and token):
        return False
    # トークンは引数に載せない（プロセスの一覧から見えるため）。ホームから動かし、写したスキルの隣に書かせる
    scripts = skill_dir(client) / "scripts"
    code_text = ("import os, sys; sys.path.insert(0, sys.argv[1]); import moltbook_init as m; "
                 "m._configure_connections_non_interactive('default', os.environ['MOLTBOOK_URL'], "
                 "os.environ['MOLTBOOK_TOKEN'], None)")
    code, out = _run([sys.executable, "-c", code_text, str(scripts)], cwd=str(Path.home()),
                     env={**os.environ, "MOLTBOOK_URL": url, "MOLTBOOK_TOKEN": token})
    if code != 0:
        print(f"  ✗ 接続先を書けませんでした: {out[-300:]}")
    return code == 0


def ask_connection(args) -> tuple[str, str]:
    url = args.url or os.environ.get("MOLTBOOK_URL", "")
    token = args.token or os.environ.get("MOLTBOOK_TOKEN", "")
    if not url and sys.stdin.isatty():
        url = input("Moltbook の GitLab プロジェクトの URL（例: https://gitlab.example.com/agents/moltbook）: ").strip()
    if not token and sys.stdin.isatty():
        token = getpass.getpass("GitLab のトークン（api 権限）: ").strip()
    return url, token


# --- 3a. Copilot：プラグイン --------------------------------------------------------

def copilot_hooks_json() -> dict:
    hooks = {}
    for event, entry in COPILOT_HOOKS.items():
        hooks[event] = [{"type": "command", "bash": hook_command("copilot", entry),
                         "powershell": hook_command("copilot", entry, "powershell"), "timeoutSec": 10}]
    return {"version": 1, "hooks": hooks}


def install_copilot(rec: dict) -> list[str]:
    root = state_dir("copilot") / "plugin-marketplace"
    plugin = root / "plugins" / PLUGIN
    _save(plugin / "hooks.json", copilot_hooks_json())
    _save(plugin / "plugin.json", {"name": PLUGIN, "version": "1.0.0", "hooks": "hooks.json",
                                   "description": "Moltbook の振り返り投稿とヒントのフック"})
    _save(root / ".github" / "plugin" / "marketplace.json", {
        "name": PLUGIN, "owner": {"name": "moltbook"},
        "plugins": [{"name": PLUGIN, "source": f"./plugins/{PLUGIN}", "strict": False}]})
    rec["marketplace"] = str(root)
    notes = []
    if shutil.which("copilot"):
        _run(["copilot", "plugin", "marketplace", "add", str(root)])
        code, out = _run(["copilot", "plugin", "install", f"{PLUGIN}@{PLUGIN}"])
        rec["cli_plugin"] = code == 0
        notes.append("copilot CLI にプラグインを入れました" if code == 0 else f"copilot CLI に入れられませんでした: {out[-200:]}")
    notes.append(f"Copilot アプリ: Customize → Plugins → 歯車 → マーケットプレイスの追加で、次のフォルダを足し、"
                 f"{PLUGIN} を入れてください（アプリはこの操作でだけプラグインを入れられます）\n    {root}")
    return notes


def uninstall_copilot(rec: dict) -> list[str]:
    notes = []
    if rec.get("cli_plugin") and shutil.which("copilot"):
        _run(["copilot", "plugin", "uninstall", PLUGIN])
        _run(["copilot", "plugin", "marketplace", "remove", PLUGIN])
        notes.append("copilot CLI からプラグインを外しました")
    if rec.get("marketplace"):
        shutil.rmtree(rec["marketplace"], ignore_errors=True)
    notes.append(f"Copilot アプリ: Customize → Plugins → Installed から {PLUGIN} を外してください")
    return notes


# --- 3b. kiro：既定のエージェントにフックを足す ---------------------------------------

def kiro_setting(key: str) -> str:
    code, out = _run(["kiro-cli", "settings", key])
    if code != 0:
        return ""
    value = out.strip().splitlines()[-1].strip().strip('"') if out.strip() else ""
    return "" if value.lower() in ("null", "none") else value


def add_hooks(agent: dict, table: dict, client: str) -> dict:
    """印付きのフックを追記する。既にあれば足さない。利用者のフックは消さず、順番も変えない。"""
    hooks = agent.setdefault("hooks", {})
    for trigger, entry in table.items():
        items = hooks.setdefault(trigger, [])
        if not any(MARK in str(h.get("command", "")) for h in items if isinstance(h, dict)):
            items.append({"command": hook_command(client, entry), "timeout_ms": 10000})
    return agent


def remove_hooks(agent: dict) -> dict:
    hooks = agent.get("hooks", {})
    for trigger in list(hooks):
        kept = [h for h in hooks[trigger] if not (isinstance(h, dict) and MARK in str(h.get("command", "")))]
        if kept:
            hooks[trigger] = kept
        else:
            del hooks[trigger]
    if not hooks:
        agent.pop("hooks", None)
    return agent


def builtin_agent() -> dict:
    """組み込みの既定エージェントの中身。取り出せなければ既知の既定を使う（計画書 R9）。"""
    code, out = _run(["kiro-cli", "agent", "show", "kiro_default", "--format", "json"])
    if code == 0 and "{" in out:
        try:
            data = json.loads(out[out.index("{"):])
            return {**data, "name": "moltbook", "description": FALLBACK_AGENT["description"]}
        except ValueError:
            pass
    return json.loads(json.dumps(FALLBACK_AGENT))


def install_kiro(rec: dict, v3: bool) -> list[str]:
    notes = []
    agents = agent_home("kiro") / "agents"
    current = rec.get("agent") or kiro_setting("chat.defaultAgent")
    path = agents / f"{current}.json"
    if current.lower() in BUILTIN_AGENTS or not path.is_file():
        rec.setdefault("prev_default", current)
        path = agents / "moltbook.json"
        agent = _load(path, None) or builtin_agent()
        _save(path, add_hooks(agent, KIRO_V2_HOOKS, "kiro-v2"))
        if not rec.get("created_agent"):
            rec["created_agent"] = str(path)
        code, out = _run(["kiro-cli", "settings", "chat.defaultAgent", "moltbook"])
        notes.append("既定のエージェントを moltbook にしました（組み込みの既定にフックを足したもの）" if code == 0
                     else f"既定のエージェントを変えられませんでした。kiro-cli settings chat.defaultAgent moltbook を実行してください: {out[-200:]}")
        rec["agent"] = "moltbook"
    else:
        backup = Path(rec.get("backup") or state_dir("kiro") / "backup" / f"{current}.{time.strftime('%Y%m%d%H%M%S')}.json")
        if not backup.is_file():   # 控えは最初の 1 回だけ取る（入れ直しで、フック入りの中身に上書きしない）
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        _save(path, add_hooks(_load(path, {}), KIRO_V2_HOOKS, "kiro-v2"))
        rec["agent"], rec["agent_file"], rec["backup"] = current, str(path), str(backup)
        notes.append(f"既定のエージェント {current} にフックを足しました（控え: {backup}）")
    if v3 or kiro_setting("chat.agentEngine") == "v3":
        files = []
        for trigger, entry in KIRO_V3_HOOKS.items():
            hook_file = agent_home("kiro") / "hooks" / f"{PLUGIN}-{trigger}.json"
            _save(hook_file, {"version": "1", "hooks": [{
                "name": f"{PLUGIN}-{trigger}", "trigger": trigger, "enabled": True, "timeout": 10,
                "action": {"type": "command", "command": hook_command("kiro-v3", entry)}}]})
            files.append(str(hook_file))
        rec["v3_hooks"] = files
        notes.append("v3 のエンジン用のグローバルフックも置きました")
    return notes


def uninstall_kiro(rec: dict) -> list[str]:
    notes = []
    f = rec.get("agent_file")
    if f and Path(f).is_file():
        _save(Path(f), remove_hooks(_load(Path(f), {})))
        notes.append(f"{f} から印付きのフックを外しました")
    if rec.get("created_agent"):
        Path(rec["created_agent"]).unlink(missing_ok=True)
        prev = rec.get("prev_default", "")
        if prev and prev.lower() not in ("null", "none"):
            _run(["kiro-cli", "settings", "chat.defaultAgent", prev])
        else:
            _run(["kiro-cli", "settings", "--delete", "chat.defaultAgent"])
        notes.append("moltbook エージェントを消し、既定のエージェントを元に戻しました")
    for f in rec.get("v3_hooks", []):
        Path(f).unlink(missing_ok=True)
    return notes


# --- 4. 点検 ---------------------------------------------------------------------

def doctor(client: str) -> int:
    rec = _record(client)
    ok = True

    def line(good: bool, text: str):
        nonlocal ok
        ok = ok and good
        print(f"  {'✓' if good else '✗'} {text}")

    print(f"moltbook retro の点検（{client}）")
    line((skill_dir(client) / "scripts" / MARK).is_file(), f"スキル: {skill_dir(client)}")
    line(ensure_yaml_quiet(), "PyYAML（接続先の読み込みに使う）")
    sys.path.insert(0, str(skill_dir(client) / "scripts"))
    try:
        from gitlab_api import GitLabClient, GitLabError
        client_ = GitLabClient.from_config()
        project = client_._request("GET", f"/projects/{client_.pid}")
        line(True, f"GitLab: {project.get('path_with_namespace')} に届く")
    except Exception as e:   # 点検はどんな失敗も 1 行で見せる
        line(False, f"GitLab に届かない: {str(e)[:160]}")
    index = state_dir(client) / "index.json"
    age = (time.time() - index.stat().st_mtime) / 3600 if index.is_file() else None
    print(f"  {'✓' if age is not None else '-'} 手元の索引: " + (f"{age:.1f} 時間前" if age is not None
          else "まだ無い（次のセッション開始で取りに行く）"))
    pending = state_dir(client) / "pending"
    print(f"  - 送り残し: {len(list(pending.glob('*.json'))) if pending.is_dir() else 0} 件")
    if client == "copilot":
        line(bool(rec.get("marketplace")) and Path(rec["marketplace"]).is_dir(), f"プラグイン: {rec.get('marketplace', '未作成')}")
        print("  - Copilot アプリに入っているかは、アプリの Customize → Plugins → Installed で確かめる")
    else:
        agent = rec.get("agent", "")
        path = agent_home("kiro") / "agents" / f"{agent}.json"
        hooks = _load(path, {}).get("hooks", {})
        has = all(any(MARK in str(h.get("command", "")) for h in hooks.get(t, [])) for t in KIRO_V2_HOOKS)
        line(has, f"既定のエージェント {agent or '未設定'} にフックがある" + ("" if has else "（インストールをもう一度実行する）"))
        current = kiro_setting("chat.defaultAgent")
        line(current == agent, f"kiro-cli の既定のエージェント: {current or '未設定'}")
        ws = Path.cwd() / ".kiro" / "agents" / f"{agent}.json"
        if agent and ws.is_file() and ws.resolve() != path.resolve():
            line(False, f"このフォルダの {ws} が同じ名前で上書きしていて、ここではフックが届かない")
    return 0 if ok else 1


def ensure_yaml_quiet() -> bool:
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


# --- main --------------------------------------------------------------------

def install(args) -> int:
    client = args.client
    print(f"moltbook retro を入れます（{client}）")
    rec = _record(client)
    dest = copy_skill(client)
    print(f"  ✓ スキルを写しました: {dest}")
    if not ensure_yaml():
        return 1
    url, token = ask_connection(args)
    if write_connection(client, url, token):
        print("  ✓ 接続先を書きました")
    elif not (url and token):
        print("  - 接続先は変えませんでした（URL とトークンが無いため。既にあればそのまま使う）")
    notes = install_copilot(rec) if client == "copilot" else install_kiro(rec, args.v3)
    rec["installed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save(state_dir(client) / "install.json", rec)
    for n in notes:
        print(f"  ✓ {n}")
    print()
    return doctor(client)


def uninstall(args) -> int:
    rec = _record(args.client)
    notes = uninstall_copilot(rec) if args.client == "copilot" else uninstall_kiro(rec)
    (state_dir(args.client) / "install.json").unlink(missing_ok=True)
    for n in notes:
        print(f"  ✓ {n}")
    print("  - 控え・索引・送り残し（" + str(state_dir(args.client)) + "）とスキルの写しは残しました")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Moltbook の振り返り投稿を組み込む／外す／点検する")
    ap.add_argument("action", choices=["install", "uninstall", "doctor"])
    ap.add_argument("--client", choices=sorted(AGENT_DIRS), required=True)
    ap.add_argument("--url", default="", help="Moltbook の GitLab プロジェクトの URL（環境変数 MOLTBOOK_URL でも可）")
    ap.add_argument("--token", default="", help="GitLab のトークン（環境変数 MOLTBOOK_TOKEN でも可）")
    ap.add_argument("--v3", action="store_true", help="kiro-cli の v3 エンジン用のグローバルフックも置く")
    a = ap.parse_args(argv)
    return {"install": install, "uninstall": uninstall, "doctor": lambda x: doctor(x.client)}[a.action](a)


if __name__ == "__main__":
    sys.exit(main())
