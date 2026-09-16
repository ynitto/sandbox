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
from . import readers
from .scrub import scrub_text

MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 20000

# VS Code チャットの読解は collect と同じ実装（readers）を借りる。
text = readers.visible_text
vscode_objects = readers.vscode_objects
restore_vscode = readers.restore_vscode
local_path = readers.local_path
vscode_session = readers.vscode_session


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
                stat = Path(desc["path"]).stat()
                descriptors.append({**desc, "updatedAt": desc.get("updatedAt") or stat.st_mtime,
                                    "size": stat.st_size})
            except OSError as exc:
                errors.append({"provider": provider, "message": str(exc)})
            if len(descriptors) >= MAX_FILES:
                return {"descriptors": descriptors, "errors": errors[:20], "partial": True}
    except (OSError, sqlite3.Error) as exc:
        errors.append({"message": str(exc)})
    return {"descriptors": descriptors, "errors": errors[:20], "partial": False}


SIEVE_CHUNK = 1 << 20
# scrub_text が作り出す文字列。これらと重なりうる語は、生バイトのふるいで落とせない。
SCRUB_TOKENS = ("[redacted]", "~")


def ascii_lower(value):
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in value)


def touches(needle, token):
    """needle の一致が token と重なりうるか（前後のはみ出しも見る）。"""
    if token in needle or needle in token:
        return True
    return any(needle[:i] == token[-i:] or needle[-i:] == token[:i]
               for i in range(1, min(len(needle), len(token))))


def sieve_forms(needle):
    """本文を解析する前に生バイトで探す綴りの形。使えないときは None を返す。

    落とすのは「確実に一致しない」ときだけにしたいので、次のときは None にして
    従来どおり解析へ回す。 (1) 大小の畳み込みが ASCII の外に及ぶ語（bytes の小文字化で
    再現できない）。(2) scrub が作る `[REDACTED]` や `~` と重なりうる語（元のファイルには
    その綴りが無い）。
    """
    if not needle:
        return None
    low = needle.casefold()
    if low != ascii_lower(needle):
        return None
    if any(touches(low, token) for token in SCRUB_TOKENS):
        return None
    # 生のまま・JSON の逃がし（" \ 改行）・\uXXXX 形。どれで書かれていても拾う。
    forms = {low, json.dumps(low, ensure_ascii=False)[1:-1], json.dumps(low)[1:-1]}
    return [f.encode("utf-8").lower() for f in forms if f]


def sieve_file(path, forms):
    """ファイルを読むだけで判定する。真なら解析へ進む（偽は確実に一致しない）。"""
    overlap = max(len(f) for f in forms) - 1
    tail = b""
    try:
        with open(path, "rb") as stream:
            while True:
                chunk = stream.read(SIEVE_CHUNK)
                if not chunk:
                    return False
                blob = (tail + chunk).lower()
                if any(f in blob for f in forms):
                    return True
                tail = chunk[-overlap:] if overlap > 0 else b""
    except OSError:
        return True


def passes_sieve(desc, query, forms):
    """解析する価値があるか。落とすのは確実に不一致のときだけ。"""
    provider = desc["provider"]
    if query.get("agent") and query["agent"] != ("copilot" if provider == "vscode" else provider):
        return False
    if query.get("source") and query["source"] != ("vscode" if provider == "vscode" else "cli"):
        return False
    # 実際の更新日時は必ず mtime 以下なので、開始日より古いファイルは確実に範囲外。
    # 終了日は逆向き（mtime が新しくても中身は古くありうる）なので、ふるいに使わない。
    if query.get("since") and query.get("dateField") != "created":
        stamp = desc.get("updatedAt") or 0
        if stamp and stamp < query["since"]:
            return False
    if forms and provider != "kiro" and not sieve_file(desc["path"], forms):
        return False
    return True


def comparable_path(value):
    value = str(value or "").replace("\\", "/")
    if len(value) > 2 and value[1] == ":":
        value = "/mnt/" + value[0].lower() + value[2:]
    return value.casefold()


INDEX_BODY_LIMIT = 512 * 1024


def inspect(desc, query, needle):
    """1 件を解析して、条件に合えば一覧用の姿で返す。合わなければ None。"""
    obj = read_file(desc)
    if not obj["messages"]:
        return None
    if not query.get("archived") and obj["archived"]:
        return None
    stamp = obj.get("createdAt" if query.get("dateField") == "created" else "updatedAt", 0)
    if query.get("since") and stamp < query["since"]:
        return None
    if query.get("until") and stamp >= query["until"]:
        return None
    if query.get("repo") and comparable_path(query["repo"]) not in comparable_path(obj["repo"]):
        return None
    if query.get("model") and query["model"].casefold() not in obj["model"].casefold():
        return None
    match = next((m["text"] for m in obj["messages"] if needle in m["text"].casefold()), "")
    if needle and not match and needle not in obj["title"].casefold():
        return None
    match = match or obj["messages"][0]["text"]
    offset = max(0, match.casefold().find(needle) - 50) if needle else 0
    obj["snippet"] = match[offset:offset + 180]
    obj["count"] = len(obj.pop("messages"))
    return obj


def walk(options, found, progress=None):
    """候補を順に見て、一致を found へ渡す。ふるいで落ちたものは解析しない。"""
    errors, partial, scanned = [], False, 0
    query = options.get("query", {})
    needle = str(query.get("text", "")).casefold()
    forms = sieve_forms(query.get("text", ""))
    for i, desc in enumerate(options["descriptors"] if "descriptors" in options else discover(options)):
        if i >= MAX_FILES:
            partial = True
            break
        scanned += 1
        try:
            if not passes_sieve(desc, query, forms):
                continue
            obj = inspect(desc, query, needle)
            if obj is not None:
                found(obj)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            if len(errors) < 20:
                errors.append({"provider": desc["provider"], "message": str(exc), "file": Path(desc["path"]).name})
        if progress and scanned % 200 == 0:
            progress(scanned)
    return {"errors": errors, "partial": partial, "scanned": scanned}


def scan(options):
    sessions = []
    result = walk(options, sessions.append)
    return {"sessions": sessions, "errors": result["errors"], "partial": result["partial"]}


def stream(options, emit):
    """一致を見つけ次第送り出す。呼び出し側は最後まで待たずに表示できる。"""
    result = walk(options, lambda obj: emit({"hit": obj}),
                  lambda scanned: emit({"progress": {"scanned": scanned}}))
    return result


def index_records(options, emit):
    """索引へ入れる本文つきの記録を送り出す。条件での絞り込みはしない。"""
    errors, indexed = [], 0
    for desc in options["descriptors"]:
        try:
            obj = read_file(desc)
            body = "\n".join(m["text"] for m in obj["messages"])
            stat = Path(desc["path"]).stat()
            emit({"record": {"path": desc["path"], "provider": desc["provider"],
                             "descriptorId": desc.get("nativeId", ""), "nativeId": obj["nativeId"], "size": stat.st_size, "mtime": stat.st_mtime,
                             "repo": obj["repo"], "model": obj["model"], "title": obj["title"],
                             "createdAt": obj["createdAt"], "updatedAt": obj["updatedAt"],
                             "archived": obj["archived"], "count": len(obj["messages"]),
                             "partial": obj["partial"], "body": body[:INDEX_BODY_LIMIT],
                             "truncated": len(body) > INDEX_BODY_LIMIT}})
            indexed += 1
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            if len(errors) < 20:
                errors.append({"provider": desc["provider"], "message": str(exc), "file": Path(desc["path"]).name})
            emit({"skip": {"path": desc["path"], "nativeId": desc.get("nativeId", "")}})
    return {"errors": errors, "indexed": indexed}


def handle(options, emit):
    mode = options.get("mode")
    if mode == "read":
        return read_file(options["descriptor"])
    if mode == "inventory":
        return inventory(options)
    if mode == "stream":
        return stream(options, emit)
    if mode == "index":
        return index_records(options, emit)
    return scan(options)


def main():
    """1 行 1 要求の NDJSON。要求ごとに途中経過を流し、最後に ok 行で閉じる。

    呼び出し側は 1 回の検索につきこのプロセスを 1 つだけ起こし、終わったら標準入力を
    閉じて終了させる（検索していない間はプロセスを残さない）。
    """
    for line in sys.stdin:
        if not line.strip():
            continue
        request = {}
        try:
            request = json.loads(line)
            token = request.get("id", 0)
            def emit(payload, token=token):
                print(json.dumps({"id": token, **payload}, ensure_ascii=False), flush=True)
            print(json.dumps({"id": token, "ok": True, "data": handle(request, emit)}, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"id": request.get("id", 0), "ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
