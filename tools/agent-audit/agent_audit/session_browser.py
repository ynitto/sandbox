"""Read-only session browser. Shared by desktop clients; never truncates transfer text.

CLI JSONL/SQLite parsing stays in readers. VS Code's append-only object mutations
are replayed before extracting the visible conversation (not tool instructions).
Run as `python -m agent_audit.session_browser`; request/response are JSON on stdio.
"""
from __future__ import annotations
import glob
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import sys
from urllib.parse import unquote, urlparse
from . import readers
from .scrub import scrub_text

MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 20000


def text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (text(v) for v in value)))
    if isinstance(value, dict):
        # Only visible text/markdown, never arbitrary tool payloads.
        if value.get("kind") not in (None, "markdownContent", "markdownVuln"):
            return ""
        return text(value.get("text", value.get("value", value.get("content", ""))))
    return ""


def json_objects(file):
    if file.stat().st_size > MAX_BYTES:
        raise ValueError("会話が大きすぎます。エクスポートした会話を取り込んでください")
    raw = file.read_text(encoding="utf-8-sig")
    try:
        obj = json.loads(raw)
        return [obj], False
    except ValueError:
        result = []
        broken = False
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                result.append(json.loads(line))
            except ValueError:
                broken = True
        if not result:
            raise ValueError("会話の保存形式を読み取れません")
        return result, broken


def vscode_objects(file, status):
    """Replay append-only logs without retaining superseded versions in memory."""
    with file.open(encoding="utf-8-sig") as stream:
        first = next((line for line in stream if line.strip()), "")
        try:
            obj = json.loads(first)
        except ValueError:
            # Pretty-printed JSON exports are a single document.
            stream.seek(0)
            try:
                yield json.load(stream)
            except ValueError as exc:
                raise ValueError("会話の保存形式を読み取れません") from exc
            return
        yield obj
        for line in stream:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except ValueError:
                status["partial"] = True


def restore_vscode(objects):
    state = None
    for entry in objects:
        if not isinstance(entry, dict):
            raise ValueError("未対応の会話更新形式です")
        if state is None and "requests" in entry:
            state = entry
            continue
        kind = entry.get("kind")
        if kind == 0 and state is None:
            state = entry["v"]
            continue
        keys = entry.get("k")
        if state is None or kind not in (1, 2, 3) or not isinstance(keys, list):
            raise ValueError("未対応の会話更新形式です")
        if not keys:
            if kind == 1:
                state = entry["v"]
                continue
            target = state
        else:
            target = state
            for key in keys[:-1]:
                target = target[key]
            key = keys[-1]
            if kind == 1:
                target[key] = entry["v"]
                continue
            if kind == 3:
                if isinstance(target, dict):
                    target.pop(key, None)
                else:
                    raise ValueError("未対応の会話更新形式です")
                continue
            target = target[key]
        if kind == 2 and isinstance(target, list):
            if "i" in entry:
                i = entry["i"]
                if not isinstance(i, int) or i < 0 or i > len(target):
                    raise ValueError("不正な会話更新位置です")
                del target[i:]
            target.extend(entry.get("v", []))
        else:
            raise ValueError("未対応の会話更新形式です")
    if not isinstance(state, dict) or not isinstance(state.get("requests"), list):
        raise ValueError("会話の本文がありません")
    return state


def local_path(value):
    if isinstance(value, dict):
        value = value.get("path", "") if value.get("scheme") == "file" else ""
    if not isinstance(value, str):
        return ""
    if value.startswith("file://"):
        parsed = urlparse(value)
        path = unquote(parsed.path)
        if len(path) > 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return ("//" + parsed.netloc if parsed.netloc else "") + path
    return value


def vscode_session(file, objects):
    obj = restore_vscode(objects)
    messages = []
    dates = []
    model = ""
    for i, req in enumerate(obj["requests"]):
        if not isinstance(req, dict) or req.get("hiddenFromTranscript") or req.get("isHidden"):
            continue
        stamp = readers._epoch_sec(req.get("timestamp"))
        if stamp:
            dates.append(stamp)
        rid = str(req.get("requestId") or i)
        if not req.get("requestHiddenFromTranscript"):
            body = text(req.get("message"))
            if body:
                messages.append({"id": rid + ":user", "role": "user", "text": body})
        body = text(req.get("response"))
        if body:
            status = req.get("modelState", {})
            status = status.get("value") if isinstance(status, dict) else status
            messages.append({"id": rid + ":assistant", "role": "assistant", "text": body,
                             "complete": not req.get("isCanceled") and status in (None, 1)})
        model = req.get("modelId") or model
    model = model or (obj.get("inputState", {}).get("selectedModel") or {}).get("identifier", "")
    cwd = local_path(obj.get("workingDirectory", ""))
    workspace = file.parent.parent / "workspace.json"
    if not cwd and workspace.is_file():
        info = json.loads(workspace.read_text(encoding="utf-8"))
        cwd = local_path(info.get("folder", info.get("workspace", "")))
    return {"nativeId": str(obj.get("sessionId") or file.stem), "title": obj.get("customTitle", ""),
            "repo": cwd, "model": model, "messages": messages,
            "createdAt": readers._epoch_sec(obj.get("creationDate")), "updatedAt": max(dates or [0])}


def copilot_session(file, objects):
    result = {"nativeId": file.parent.name if file.name == "events.jsonl" else file.stem,
              "repo": "", "model": "", "createdAt": 0, "updatedAt": 0, "messages": []}
    pending = []
    for i, event in enumerate(objects):
        if not isinstance(event, dict) or event.get("agentId") or event.get("ephemeral"):
            continue
        data = event.get("data") or {}
        kind = event.get("type")
        stamp = readers._epoch_sec(event.get("timestamp"))
        result["updatedAt"] = max(result["updatedAt"], stamp)
        if kind == "session.start":
            result.update(nativeId=data.get("sessionId", result["nativeId"]),
                          repo=(data.get("context") or {}).get("cwd", ""),
                          model=data.get("selectedModel", ""), createdAt=stamp)
        elif kind == "session.model_change":
            result["model"] = data.get("newModel", data.get("model", result["model"]))
        elif kind in ("user.message", "assistant.message"):
            body = text(data.get("content"))
            if body:
                message = {"id": str(event.get("id") or i), "role": kind.split(".")[0], "text": body,
                           "complete": kind == "user.message"}
                result["messages"].append(message)
                if kind == "assistant.message":
                    pending.append(message)
        elif kind in ("assistant.turn_end", "session.task_complete", "session.shutdown"):
            for message in pending:
                message["complete"] = True
            pending = []
    return result


def cli_messages(objects, provider):
    events = [o for o in objects if isinstance(o, dict) and not o.get("isSidechain")]
    if provider == "claude":
        linked = {o["uuid"]: o for o in events if isinstance(o.get("uuid"), str)}
        leaves = [o for o in events if o.get("uuid") and readers._message_of(o)]
        if leaves and any("parentUuid" in o for o in leaves):
            chain, seen = [], set()
            current = leaves[-1]
            while current and current.get("uuid") not in seen:
                seen.add(current.get("uuid"))
                chain.append(current)
                current = linked.get(current.get("parentUuid"))
            events = list(reversed(chain))
    result = []
    for i, event in enumerate(events):
        got = readers._message_of(event)
        if not got:
            continue
        role, body = got
        payload = event.get("payload") or event.get("message") or {}
        result.append({"id": str(event.get("uuid") or event.get("id") or i), "role": role.lower(),
                       "text": body, "complete": payload.get("channel") not in ("analysis", "commentary")})
    return result


def read_file(descriptor):
    file = Path(descriptor["path"])
    before = file.stat()
    provider = descriptor["provider"]
    broken = False
    if provider == "kiro":
        sessions = readers._read_kiro_sqlite(str(file), want_messages=True, native_id=descriptor.get("nativeId"))
        row = next((s for s in sessions if s["native_id"] == descriptor.get("nativeId")), None)
        if row is None:
            raise ValueError("会話が見つからないか、保存形式が未対応です")
        obj = from_reader(row)
    elif provider == "vscode":
        status = {"partial": False}
        obj = vscode_session(file, vscode_objects(file, status))
        broken = status["partial"]
    else:
        objects, broken = json_objects(file)
        if provider == "copilot":
            obj = copilot_session(file, objects)
        else:
            # The shared reader handles Claude/Codex text blocks without display cleaning caps.
            row = readers._parse_jsonl_session(str(file), want_messages=True)
            if row is None:
                raise ValueError("会話の保存形式を読み取れません")
            obj = from_reader(row)
            obj["messages"] = cli_messages(objects, provider)
            for event in objects:
                if not isinstance(event, dict):
                    continue
                payload = event.get("payload") or {}
                if event.get("type") == "session_meta":
                    obj["nativeId"] = payload.get("id", obj["nativeId"])
                    obj["repo"] = payload.get("cwd", obj["repo"])
                if event.get("type") == "turn_context":
                    obj["model"] = payload.get("model", obj["model"])
    after = file.stat()
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise ValueError("会話が更新されました。もう一度選択してください")
    obj.update(provider=provider, source="vscode" if provider == "vscode" else "cli",
               agent="copilot" if provider == "vscode" else provider,
               descriptor=descriptor, partial=broken,
               revision=hashlib.sha256((str(after.st_mtime_ns) + ":" + str(after.st_size) + ":" + json.dumps(obj["messages"], ensure_ascii=False)).encode()).hexdigest())
    for message in obj["messages"]:
        message["text"] = scrub_text(message["text"])
        if "[VS Code: value truncated for persistence" in message["text"]:
            obj["partial"] = True
    obj["title"] = scrub_text(str(obj.get("title") or next((m["text"].split("\n")[0] for m in obj["messages"] if m["role"] == "user"), file.stem)))[:120]
    obj["updatedAt"] = obj.get("updatedAt") or after.st_mtime
    obj["createdAt"] = obj.get("createdAt") or after.st_mtime
    obj["archived"] = "archived_sessions" in file.parts
    return obj


def from_reader(row):
    completion = row.get("message_completion") or [True] * len(row["messages"])
    return {"nativeId": row["native_id"], "repo": row["cwd"], "model": row["model"],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "messages": [{"id": str(i), "role": role.lower(), "text": body, "complete": completion[i]}
                         for i, (role, body) in enumerate(row["messages"])]}


def discover(options):
    home = Path(options.get("home") or Path.home())
    homes = [home] + [Path(p) for p in options.get("extraHomes", [])]
    roots = []
    for base in homes:
        roots.extend([(base / ".codex/sessions", "codex"), (base / ".codex/archived_sessions", "codex"),
                      (base / ".claude/projects", "claude"), (base / ".copilot/session-state", "copilot"),
                      (base / ".kiro/store.db", "kiro"),
                      (base / ".local/share/kiro-cli/data.sqlite3", "kiro"),
                      (base / "Library/Application Support/kiro-cli/data.sqlite3", "kiro")])
    if os.environ.get("XDG_DATA_HOME"):
        roots.append((Path(os.environ["XDG_DATA_HOME"]) / "kiro-cli/data.sqlite3", "kiro"))
    for variable, provider, suffix in [("CODEX_HOME", "codex", "sessions"), ("CLAUDE_CONFIG_DIR", "claude", "projects")]:
        if os.environ.get(variable):
            roots.append((Path(os.environ[variable]) / suffix, provider))
    code_roots = [home / "Library/Application Support/Code", home / "Library/Application Support/Code - Insiders",
                  home / ".config/Code", home / ".config/Code - Insiders",
                  home / ".vscode-server/data", home / ".vscode-server-insiders/data"]
    for appdata in options.get("appData", []):
        code_roots.extend([Path(appdata) / "Code", Path(appdata) / "Code - Insiders"])
    code_roots.extend(Path(p) for p in options.get("codeRoots", []))
    for base in code_roots:
        for pattern in ["User/workspaceStorage/*/chatSessions", "User/globalStorage/emptyWindowChatSessions",
                        "User/profiles/*/workspaceStorage/*/chatSessions", "User/profiles/*/globalStorage/emptyWindowChatSessions"]:
            roots.extend((Path(p), "vscode") for p in glob.glob(str(base / pattern)))
    for added in options.get("codeRoots", []):
        root = Path(added)
        if root.name in ("chatSessions", "emptyWindowChatSessions"):
            roots.append((root, "vscode"))
        for directory, provider in [(".codex", "codex"), (".claude", "claude"), (".copilot", "copilot")]:
            if directory in root.parts:
                roots.append((root, provider))
    roots.extend((Path(p), "vscode") for p in options.get("imports", []))
    seen = set()
    for root, provider in roots:
        paths = [root] if root.is_file() else root.rglob("*.json*") if root.is_dir() else []
        for file in paths:
            if file.suffix not in (".json", ".jsonl", ".db", ".sqlite3") or (provider == "copilot" and file.name != "events.jsonl" and file.parent != root):
                continue
            if provider == "kiro":
                for row in kiro_inventory(file):
                    yield {"path": str(file), "provider": provider, **row}
                continue
            identity = str(file.resolve())
            if identity not in seen:
                seen.add(identity)
                yield {"path": str(file), "provider": provider}


def kiro_inventory(file):
    """Read indexed IDs/timestamps, without loading current conversation JSON."""
    conn = sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "conversations_v2" in tables:
            for sid, stamp in conn.execute("SELECT conversation_id, updated_at FROM conversations_v2 ORDER BY updated_at DESC"):
                yield {"nativeId": sid, "updatedAt": readers._epoch_sec(stamp)}
            return
    finally:
        conn.close()
    for row in readers._read_kiro_sqlite(str(file), want_messages=False):
        yield {"nativeId": row["native_id"], "updatedAt": row["updated_at"]}


def inventory(options):
    descriptors, errors = [], []
    query = options.get("query", {})
    try:
        for desc in discover(options):
            provider = desc["provider"]
            if query.get("agent") and query["agent"] != ("copilot" if provider == "vscode" else provider):
                continue
            if query.get("source") and query["source"] != ("vscode" if provider == "vscode" else "cli"):
                continue
            try:
                descriptors.append({**desc, "updatedAt": desc.get("updatedAt") or Path(desc["path"]).stat().st_mtime})
            except OSError as exc:
                errors.append({"provider": provider, "message": str(exc)})
            if len(descriptors) >= MAX_FILES:
                return {"descriptors": descriptors, "errors": errors[:20], "partial": True}
    except (OSError, sqlite3.Error) as exc:
        errors.append({"message": str(exc)})
    return {"descriptors": descriptors, "errors": errors[:20], "partial": False}


def comparable_path(value):
    value = str(value or "").replace("\\", "/")
    if len(value) > 2 and value[1] == ":":
        value = "/mnt/" + value[0].lower() + value[2:]
    return value.casefold()


def scan(options):
    sessions, errors = [], []
    query = options.get("query", {})
    needle = str(query.get("text", "")).casefold()
    partial = False
    for i, desc in enumerate(options["descriptors"] if "descriptors" in options else discover(options)):
        if i >= MAX_FILES:
            partial = True
            break
        if query.get("agent") and query["agent"] != ("copilot" if desc["provider"] == "vscode" else desc["provider"]):
            continue
        source = "vscode" if desc["provider"] == "vscode" else "cli"
        if query.get("source") and query["source"] != source:
            continue
        try:
            obj = read_file(desc)
            if not obj["messages"]:
                continue
            if not query.get("archived") and obj["archived"]:
                continue
            stamp = obj.get("createdAt" if query.get("dateField") == "created" else "updatedAt", 0)
            if query.get("since") and stamp < query["since"]:
                continue
            if query.get("until") and stamp >= query["until"]:
                continue
            if query.get("repo") and comparable_path(query["repo"]) not in comparable_path(obj["repo"]):
                continue
            if query.get("model") and query["model"].casefold() not in obj["model"].casefold():
                continue
            match = next((m["text"] for m in obj["messages"] if needle in m["text"].casefold()), "")
            if needle and not match and needle not in obj["title"].casefold():
                continue
            match = match or obj["messages"][0]["text"]
            offset = max(0, match.casefold().find(needle) - 50) if needle else 0
            obj["snippet"] = match[offset:offset + 180]
            obj["count"] = len(obj.pop("messages"))
            sessions.append(obj)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            if len(errors) < 20:
                errors.append({"provider": desc["provider"], "message": str(exc), "file": Path(desc["path"]).name})
    return {"sessions": sessions, "errors": errors, "partial": partial}


def main():
    try:
        options = json.load(sys.stdin)
        result = (read_file(options["descriptor"]) if options.get("mode") == "read" else
                  inventory(options) if options.get("mode") == "inventory" else scan(options))
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
