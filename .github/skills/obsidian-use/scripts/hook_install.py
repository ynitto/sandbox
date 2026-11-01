#!/usr/bin/env python3
"""Obsidian へメモを集めるフックを、Copilot・kiro-cli・Claude Code に組み込む／外す／点検する。

設計: docs/designs/obsidian-wiki-design.md §6

  python hook_install.py install   --client copilot|kiro|claude [--vault PATH] [--wiki-dir llm-wiki] [--v3]
  python hook_install.py uninstall --client copilot|kiro|claude
  python hook_install.py doctor    --client copilot|kiro|claude

すること（install）:
  1. obsidian-use を <エージェントの置き場所>/skills/obsidian-use に写す（フックはこの写しを指す）
  2. --vault（か環境変数 OBSIDIAN_VAULT）があれば、wiki の骨組みを作って設定を書く（無ければ一度だけ尋ねる）
  3. Copilot: フックをまとめたプラグインとマーケットプレイスを作り、copilot CLI があれば入れる
     kiro:    既定のエージェントに印付きのフックを追記する（組み込みの既定なら obsidian-memo エージェントを作る）
     claude:  ~/.claude/settings.json の hooks に印付きのフックを追記する
  4. 点検（doctor）を実行する

置いたもの・書き換えたものは <エージェントの置き場所>/.obsidian-use/install.json に記録し、uninstall はそれだけを戻す。
設定ファイルは、印（コマンドに obsidian_hook.py を含む）の付いたフックだけを足し引きする。moltbook-use の
フックと同じエージェントに並べて入れてよい（互いの印には触らない）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_SRC = Path(__file__).resolve().parent.parent
SKILL_NAME = "obsidian-use"
MARK = "obsidian_hook.py"
PLUGIN = "obsidian-memo"
AGENT_DIRS = {"copilot": ".copilot", "kiro": ".kiro", "claude": ".claude"}
COPILOT_HOOKS = {"sessionStart": "start", "userPromptSubmitted": "prompt", "postToolUse": "tool",
                 "postToolUseFailure": "tool --failed", "agentStop": "stop"}
KIRO_V2_HOOKS = {"agentSpawn": "start", "userPromptSubmit": "prompt", "postToolUse": "tool", "stop": "stop"}
KIRO_V3_HOOKS = {"SessionStart": "start", "UserPromptSubmit": "prompt", "PostToolUse": "tool", "Stop": "stop"}
CLAUDE_HOOKS = {"SessionStart": "start", "UserPromptSubmit": "prompt", "PostToolUse": "tool",
                "PostToolUseFailure": "tool --failed", "Stop": "stop"}
CLAUDE_TOOL_EVENTS = {"PostToolUse", "PostToolUseFailure"}
BUILTIN_AGENTS = {"", "null", "none", "kiro_default", "q_cli_default", "default"}
FALLBACK_AGENT = {
    "name": PLUGIN, "description": "既定のエージェントに Obsidian へメモを集めるフックを足したもの",
    "tools": ["*"], "allowedTools": [],
    "resources": ["file://AGENTS.md", "file://README.md", "file://.kiro/steering/**/*.md"],
    "useLegacyMcpJson": True,
}


def agent_home(client: str) -> Path:
    return Path(os.environ.get("OBSIDIAN_USE_AGENT_HOME") or Path.home() / AGENT_DIRS[client])


def skill_dir(client: str) -> Path:
    return agent_home(client) / "skills" / SKILL_NAME


def state_dir(client: str) -> Path:
    return agent_home(client) / ".obsidian-use"


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


def _run(cmd: list, timeout: int = 60, **kw) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)
    out = p.stdout or b""
    text = out.decode("utf-16-le") if out[:2] == b"\xff\xfe" else out.decode("utf-8", "replace")
    return p.returncode, (text + (p.stderr or b"").decode("utf-8", "replace")).replace("\x00", "").strip()


def hook_command(client: str, entry: str, kind: str, shell: str = "sh") -> str:
    py, hook = sys.executable, skill_dir(client) / "scripts" / MARK
    args = f"{entry} --client {kind}"
    return f'& "{py}" "{hook}" {args}' if shell == "powershell" else f'"{py}" "{hook}" {args}'


def _is_ours(h) -> bool:
    return isinstance(h, dict) and (MARK in str(h.get("command", "")) or MARK in str(h.get("bash", ""))
                                    or any(_is_ours(x) for x in h.get("hooks", []) if isinstance(h.get("hooks"), list)))


# --- 1. スキルを写す・2. ボルト -------------------------------------------------------

def copy_skill(client: str) -> Path:
    dest = skill_dir(client)
    if dest.resolve() == SKILL_SRC.resolve():
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SKILL_SRC, dest, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    return dest


def configure_vault(client: str, vault_path: str, wiki_dir: str) -> tuple[bool, str]:
    code, out = _run([sys.executable, str(skill_dir(client) / "scripts" / "vault.py"), "init", "--vault", vault_path,
                      "--wiki-dir", wiki_dir], env={**os.environ, "OBSIDIAN_USE_AGENT_HOME": str(agent_home(client))})
    return code == 0, out


def vault_config(client: str) -> dict:
    cfg = _load(agent_home(client) / "skill-registry.json", {}).get("skill_configs", {}).get(SKILL_NAME) or {}
    return cfg if cfg.get("vault") else {}


# --- 3a. Copilot -------------------------------------------------------------------

def copilot_hooks_json() -> dict:
    hooks = {}
    for event, entry in COPILOT_HOOKS.items():
        hooks[event] = [{"type": "command", "bash": hook_command("copilot", entry, "copilot"),
                         "powershell": hook_command("copilot", entry, "copilot", "powershell"), "timeoutSec": 10}]
    return {"version": 1, "hooks": hooks}


def install_copilot(rec: dict, _v3: bool) -> list[str]:
    root = state_dir("copilot") / "plugin-marketplace"
    plugin = root / "plugins" / PLUGIN
    _save(plugin / "hooks.json", copilot_hooks_json())
    _save(plugin / "plugin.json", {"name": PLUGIN, "version": "1.0.0", "hooks": "hooks.json",
                                   "description": "Obsidian の wiki にメモを集めるフック"})
    _save(root / ".github" / "plugin" / "marketplace.json", {
        "name": PLUGIN, "owner": {"name": "obsidian-use"},
        "plugins": [{"name": PLUGIN, "source": f"./plugins/{PLUGIN}", "strict": False}]})
    rec["marketplace"] = str(root)
    notes = []
    if shutil.which("copilot"):
        _run(["copilot", "plugin", "marketplace", "add", str(root)])
        code, out = _run(["copilot", "plugin", "install", f"{PLUGIN}@{PLUGIN}"])
        rec["cli_plugin"] = code == 0
        notes.append("copilot CLI にプラグインを入れました" if code == 0 else f"✗ copilot CLI に入れられませんでした: {out[-200:]}")
    notes.append(f"Copilot アプリ: Customize → Plugins → 歯車 → マーケットプレイスの追加で、次のフォルダを足し、"
                 f"{PLUGIN} を入れてください\n    {root}")
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


# --- 3b. kiro ----------------------------------------------------------------------

def kiro_setting(key: str) -> str:
    code, out = _run(["kiro-cli", "settings", key])
    if code != 0:
        return ""
    value = out.strip().splitlines()[-1].strip().strip('"') if out.strip() else ""
    return "" if value.lower() in ("null", "none") else value


def add_kiro_hooks(agent: dict, table: dict, kind: str) -> dict:
    hooks = agent.setdefault("hooks", {})
    for trigger, entry in table.items():
        items = hooks.setdefault(trigger, [])
        if not any(_is_ours(h) for h in items):
            items.append({"command": hook_command("kiro", entry, kind), "timeout_ms": 10000})
    return agent


def remove_marked(hooks: dict) -> dict:
    for trigger in list(hooks):
        kept = [h for h in hooks[trigger] if not _is_ours(h)]
        if kept:
            hooks[trigger] = kept
        else:
            del hooks[trigger]
    return hooks


def builtin_agent() -> dict:
    code, out = _run(["kiro-cli", "agent", "show", "kiro_default", "--format", "json"])
    if code == 0 and "{" in out:
        try:
            data = json.loads(out[out.index("{"):])
            return {**data, "name": PLUGIN, "description": FALLBACK_AGENT["description"]}
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
        path = agents / f"{PLUGIN}.json"
        _save(path, add_kiro_hooks(_load(path, None) or builtin_agent(), KIRO_V2_HOOKS, "kiro-v2"))
        rec.setdefault("created_agent", str(path))
        code, out = _run(["kiro-cli", "settings", "chat.defaultAgent", PLUGIN])
        notes.append(f"既定のエージェントを {PLUGIN} にしました（組み込みの既定にフックを足したもの）" if code == 0
                     else f"✗ 既定のエージェントを変えられませんでした。kiro-cli settings chat.defaultAgent {PLUGIN} を"
                          f"実行してください: {out[-200:]}")
        rec["agent"], rec["agent_file"] = PLUGIN, str(path)
    else:
        backup = Path(rec.get("backup") or state_dir("kiro") / "backup" / f"{current}.{time.strftime('%Y%m%d%H%M%S')}.json")
        if not backup.is_file():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        _save(path, add_kiro_hooks(_load(path, {}), KIRO_V2_HOOKS, "kiro-v2"))
        rec["agent"], rec["agent_file"], rec["backup"] = current, str(path), str(backup)
        notes.append(f"既定のエージェント {current} にフックを足しました（控え: {backup}）")
    if v3 or kiro_setting("chat.agentEngine") == "v3":
        files = []
        for trigger, entry in KIRO_V3_HOOKS.items():
            f = agent_home("kiro") / "hooks" / f"{PLUGIN}-{trigger}.json"
            _save(f, {"version": "1", "hooks": [{
                "name": f"{PLUGIN}-{trigger}", "trigger": trigger, "enabled": True, "timeout": 10,
                "action": {"type": "command", "command": hook_command("kiro", entry, "kiro-v3")}}]})
            files.append(str(f))
        rec["v3_hooks"] = files
        notes.append("v3 のエンジン用のグローバルフックも置きました")
    return notes


def uninstall_kiro(rec: dict) -> list[str]:
    notes = []
    f = Path(rec.get("agent_file", ""))
    if rec.get("agent_file") and f.is_file():
        agent = _load(f, {})
        agent["hooks"] = remove_marked(agent.get("hooks", {}))
        if not agent["hooks"]:
            agent.pop("hooks")
        if rec.get("created_agent") == str(f) and not agent.get("hooks"):
            f.unlink()
            prev = rec.get("prev_default", "")
            if prev and prev.lower() not in ("null", "none"):
                _run(["kiro-cli", "settings", "chat.defaultAgent", prev])
            else:
                _run(["kiro-cli", "settings", "--delete", "chat.defaultAgent"])
            notes.append(f"{PLUGIN} エージェントを消し、既定のエージェントを元に戻しました")
        else:   # ほかのフック（moltbook-use など）が足されていれば、エージェントは残す
            _save(f, agent)
            notes.append(f"{f} から印付きのフックを外しました")
    for v in rec.get("v3_hooks", []):
        Path(v).unlink(missing_ok=True)
    return notes


# --- 3c. Claude Code ----------------------------------------------------------------

def claude_settings() -> Path:
    return agent_home("claude") / "settings.json"


def add_claude_hooks(settings: dict) -> dict:
    hooks = settings.setdefault("hooks", {})
    for event, entry in CLAUDE_HOOKS.items():
        groups = hooks.setdefault(event, [])
        if any(_is_ours(g) for g in groups):
            continue
        group = {"hooks": [{"type": "command", "command": hook_command("claude", entry, "claude"), "timeout": 10}]}
        if event in CLAUDE_TOOL_EVENTS:
            group = {"matcher": "*", **group}
        groups.append(group)
    return settings


def remove_claude_hooks(settings: dict) -> dict:
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        groups = []
        for g in hooks[event]:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                g = {**g, "hooks": [h for h in g["hooks"] if not _is_ours(h)]}
                if not g["hooks"]:
                    continue
            elif _is_ours(g):
                continue
            groups.append(g)
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    return settings


def install_claude(rec: dict, _v3: bool) -> list[str]:
    path = claude_settings()
    if path.is_file() and not rec.get("backup"):
        backup = state_dir("claude") / "backup" / f"settings.{time.strftime('%Y%m%d%H%M%S')}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        rec["backup"] = str(backup)
    _save(path, add_claude_hooks(_load(path, {})))
    rec["settings"] = str(path)
    return [f"{path} にフックを足しました" + (f"（控え: {rec['backup']}）" if rec.get("backup") else "")]


def uninstall_claude(rec: dict) -> list[str]:
    path = Path(rec.get("settings") or claude_settings())
    if path.is_file():
        _save(path, remove_claude_hooks(_load(path, {})))
        return [f"{path} から印付きのフックを外しました"]
    return []


# --- 4. 点検 ---------------------------------------------------------------------

def doctor(client: str) -> int:
    rec = _load(state_dir(client) / "install.json", {})
    ok = True

    def line(good: bool, text: str):
        nonlocal ok
        ok = ok and good
        print(f"  {'✓' if good else '✗'} {text}")

    print(f"obsidian memo の点検（{client}）")
    line((skill_dir(client) / "scripts" / MARK).is_file(), f"スキル: {skill_dir(client)}")
    cfg = vault_config(client)
    vpath = Path(os.path.expanduser(cfg.get("vault", ""))) if cfg else None
    line(bool(vpath and vpath.is_dir()), f"ボルト: {vpath}" if cfg else "ボルト: 未設定（install --vault <フォルダ> で設定する）")
    if cfg:
        code, out = _run([sys.executable, str(skill_dir(client) / "scripts" / "vault.py"), "config"], timeout=20,
                         env={**os.environ, "OBSIDIAN_USE_AGENT_HOME": str(agent_home(client))})
        cli = next((ln.split(":", 1)[1].strip() for ln in out.splitlines() if ln.startswith("obsidian")), "")
        print(f"  {'✓' if cli and not cli.startswith('使えない') else '-'} Obsidian CLI: {cli or '不明'}"
              "（使えないときは検索が文字列一致になる）")
    if client == "copilot":
        line(bool(rec.get("marketplace")) and Path(rec["marketplace"]).is_dir(), f"プラグイン: {rec.get('marketplace', '未作成')}")
        print("  - Copilot アプリに入っているかは、アプリの Customize → Plugins → Installed で確かめる")
    elif client == "kiro":
        agent = rec.get("agent", "")
        hooks = _load(agent_home("kiro") / "agents" / f"{agent}.json", {}).get("hooks", {})
        has = all(any(_is_ours(h) for h in hooks.get(t, [])) for t in KIRO_V2_HOOKS)
        line(has, f"既定のエージェント {agent or '未設定'} にフックがある" + ("" if has else "（インストールをもう一度実行する）"))
        current = kiro_setting("chat.defaultAgent")
        line(current == agent, f"kiro-cli の既定のエージェント: {current or '未設定'}")
    else:
        hooks = _load(claude_settings(), {}).get("hooks", {})
        has = all(any(_is_ours(g) for g in hooks.get(e, [])) for e in CLAUDE_HOOKS)
        line(has, f"{claude_settings()} にフックがある" + ("" if has else "（インストールをもう一度実行する）"))
    return 0 if ok else 1


# --- main --------------------------------------------------------------------

def install(args) -> int:
    client = args.client
    print(f"obsidian memo を入れます（{client}）")
    rec = _load(state_dir(client) / "install.json", {})
    print(f"  ✓ スキルを写しました: {copy_skill(client)}")
    vault_path = args.vault or os.environ.get("OBSIDIAN_VAULT", "")
    if not vault_path and not vault_config(client) and sys.stdin.isatty():
        vault_path = input("Obsidian のボルトのフォルダ（例: ~/Documents/Obsidian/Main）: ").strip()
    if vault_path:
        good, out = configure_vault(client, vault_path, args.wiki_dir)
        print(f"  {'✓' if good else '✗'} {out.splitlines()[0] if out else 'ボルトを設定できませんでした'}")
        if not good:
            return 1
    notes = {"copilot": install_copilot, "kiro": install_kiro, "claude": install_claude}[client](rec, args.v3)
    rec["installed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save(state_dir(client) / "install.json", rec)
    for n in notes:
        print(f"  {n}" if n.startswith("✗ ") else f"  ✓ {n}")
    print()
    return doctor(client)


def uninstall(args) -> int:
    rec = _load(state_dir(args.client) / "install.json", {})
    notes = {"copilot": uninstall_copilot, "kiro": uninstall_kiro, "claude": uninstall_claude}[args.client](rec)
    (state_dir(args.client) / "install.json").unlink(missing_ok=True)
    for n in notes:
        print(f"  ✓ {n}")
    print(f"  - ボルトのメモ・控え（{state_dir(args.client)}）・スキルの写しは残しました")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Obsidian へメモを集めるフックを組み込む／外す／点検する")
    ap.add_argument("action", choices=["install", "uninstall", "doctor"])
    ap.add_argument("--client", choices=sorted(AGENT_DIRS), required=True)
    ap.add_argument("--vault", default="", help="Obsidian のボルトのフォルダ（環境変数 OBSIDIAN_VAULT でも可）")
    ap.add_argument("--wiki-dir", default="llm-wiki", help="ボルトの中の wiki のフォルダ名")
    ap.add_argument("--v3", action="store_true", help="kiro-cli の v3 エンジン用のグローバルフックも置く")
    a = ap.parse_args(argv)
    return {"install": install, "uninstall": uninstall, "doctor": lambda x: doctor(x.client)}[a.action](a)


if __name__ == "__main__":
    sys.exit(main())
