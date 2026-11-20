"""GitLab Issue の変化を記録し、設定したチャネルに届ける。"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime
import fcntl
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import string
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_CONFIG = "~/.config/change-watch/gitlab-notify-agent/gitlab-notify.json"
DEFAULT_OUTBOX = "~/.local/state/change-watch/gitlab-notify-agent/outbox.db"
WATCH = dict(new_issues=True, state_changes=True, title_changes=False,
             label_changes=False, assignee_changes=True)
EVENTS = {"new", "state", "title", "labels", "assignee", "closed"}
FIELDS = {"line", "message", "repo", "event", "iid", "title", "url", "state",
          "labels", "assignee", "old", "new"}
MAX_AGE = {"tmux": 1440, "prompt": 60, "log": 1440, "hub": 1440}
CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")


def one_line(value):
    return CONTROLS.sub(" ", str(value))


def redact(value):
    token = os.environ.get("GITLAB_TOKEN", "").strip()
    return str(value).replace(token, "***") if token else str(value)


def report(value):
    print(one_line(redact(value)), file=sys.stderr)


def print_json(value):
    def clean(item):
        if isinstance(item, str):
            return redact(item)
        if isinstance(item, list):
            return [clean(child) for child in item]
        if isinstance(item, dict):
            return {redact(key): clean(child) for key, child in item.items()}
        return item

    print(json.dumps(clean(value), ensure_ascii=False))


def obj(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} はオブジェクトで指定してください")
    return value


def required_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} に文字列を指定してください")
    return value


def boolean(value, name):
    if type(value) is not bool:
        raise ValueError(f"{name} は true または false で指定してください")
    return value


def validate_template(value):
    if not isinstance(value, str):
        raise ValueError("template は文字列で指定してください")
    for _, field, spec, conversion in string.Formatter().parse(value):
        if field is not None and (field not in FIELDS or spec or conversion):
            raise ValueError(f"template の置き換え名が不正です: {field}")


def hub_issue_url(channel):
    return channel.get("issue_url", os.environ.get("GITLAB_HUB_ISSUE_URL", "").strip())


def validate_channel(channel):
    kind = required_text(channel.get("type"), "type")
    if kind not in MAX_AGE:
        raise ValueError("type は tmux / prompt / log / hub のいずれかです")
    channel["enabled"] = boolean(channel.get("enabled", True), "enabled")
    age = channel.get("max_age_minutes", MAX_AGE[kind])
    if type(age) not in (int, float) or age <= 0 or age > sys.float_info.max / 60 or not math.isfinite(age):
        raise ValueError("max_age_minutes は有限の正の数で指定してください")
    channel["max_age_minutes"] = age
    for name in ("repos", "events"):
        if name in channel:
            values = channel[name]
            if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
                raise ValueError(f"{name} は文字列の配列で指定してください")
            if name == "events" and set(values) - EVENTS:
                raise ValueError("events に未知のイベントがあります")
    required = {"tmux": "target", "prompt": "target", "log": "path", "hub": "issue_url"}[kind]
    if required in channel or channel["enabled"]:
        value = hub_issue_url(channel) if kind == "hub" else channel.get(required)
        required_text(value, required)
    if kind == "prompt":
        validate_template(channel.get("template", "{line}"))
        for event, template in obj(channel.get("templates", {}), "templates").items():
            if event not in EVENTS:
                raise ValueError("templates に未知のイベントがあります")
            validate_template(template)


def load_config():
    path = Path(os.environ.get("GITLAB_NOTIFY_CONFIG", DEFAULT_CONFIG)).expanduser()
    config = obj(json.loads(path.read_text(encoding="utf-8")), "設定")
    outbox = Path(required_text(config.get("outbox", DEFAULT_OUTBOX), "outbox")).expanduser()
    repos = obj(config.get("repos", {}), "repos")
    for name, repo in repos.items():
        obj(repo, f"repos.{name}")
        boolean(repo.get("removed_as_closed", True), f"repos.{name}.removed_as_closed")
        watch = obj(repo.get("watch", {}), f"repos.{name}.watch")
        for flag, value in watch.items():
            if flag not in WATCH:
                raise ValueError(f"repos.{name}.watch に未知の項目があります: {flag}")
            boolean(value, f"repos.{name}.watch.{flag}")
    channels = {}
    entries = config.get("channels")
    if not isinstance(entries, list):
        raise ValueError("channels は配列で指定してください")
    for entry in entries:
        channel = obj(entry, "チャネル")
        name = required_text(channel.get("id"), "チャネルの id")
        if name in channels:
            raise ValueError(f"チャネル {name} の id が重複しています")
        try:
            validate_channel(channel)
        except ValueError as exc:
            raise ValueError(f"チャネル {name}: {exc}") from None
        channels[name] = channel
    return outbox, repos, channels


def snapshot(record):
    assigned = record.get("assignee")
    assignee = {} if assigned is None else obj(assigned, "assignee")
    labels = record.get("labels", [])
    if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
        raise ValueError("labels は文字列の配列で指定してください")
    fields = dict(title=record["title"], state=record["state"],
                  assignee=assignee.get("username") or "")
    if any(not isinstance(value, str) for value in fields.values()):
        raise ValueError("title・state・assignee.username は文字列で指定してください")
    return dict(fields, labels=sorted(labels))


def messages(repo, settings, change):
    kind = change["kind"]
    record = obj(change["record"], "record")
    iid = record["iid"]
    if type(iid) is not int or iid <= 0:
        raise ValueError("Issue IID は正の整数です")
    current = snapshot(record)
    title = current["title"]
    watch = {**WATCH, **settings.get("watch", {})}

    def message(event, text, old="", new=""):
        fields = dict(current, iid=iid, url=record.get("web_url") or "", repo=repo,
                      event=event, old=old, new=new, labels=", ".join(current["labels"]))
        return event, f"[{repo}] {text}", fields

    if kind == "appeared":
        if watch["new_issues"]:
            labels = f" [{', '.join(current['labels'])}]" if current["labels"] else ""
            assignee = f" @{current['assignee']}" if current["assignee"] else ""
            yield message("new", f"[NEW] #{iid} {title}{labels}{assignee}")
    elif kind == "removed":
        if watch["state_changes"] and settings.get("removed_as_closed", True):
            yield message("closed", f"[CLOSED] #{iid} {title}")
    elif kind == "changed":
        before = snapshot(obj(change["before"], "before"))
        for event, flag in (("state", "state_changes"), ("title", "title_changes"),
                            ("labels", "label_changes"), ("assignee", "assignee_changes")):
            old, new = before[event], current[event]
            if not watch[flag] or old == new:
                continue
            if event == "title":
                detail = "title changed"
            elif event == "labels":
                added = sorted(set(new) - set(old))
                removed = sorted(set(old) - set(new))
                parts = []
                if added:
                    parts.append("+" + ",".join(added))
                if removed:
                    parts.append("-" + ",".join(removed))
                detail = "labels:" + " ".join(parts)
                old, new = ", ".join(old), ", ".join(new)
            elif event == "assignee":
                detail = f"assignee:{old or '(未割り当て)'}→{new or '(未割り当て)'}"
            else:
                detail = f"state:{old}→{new}"
            yield message(event, f"[UPD] #{iid} {detail}  {title}", old, new)
    else:
        raise ValueError("未知の kind です")


def line(row):
    return f"{row['ts']}  {one_line(row['message'])}"


def prompt_text(channel, row):
    template = channel.get("templates", {}).get(row["event"], channel.get("template", "{line}"))
    fields = dict(json.loads(row["fields"]), line=line(row), message=row["message"])
    return one_line(template.format_map(fields))


def _tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10)


def send_tmux(channel, row):
    prompt = channel["type"] == "prompt"
    target = channel["target"]
    text = prompt_text(channel, row) if prompt else line(row)
    # tmux は -- の後でも、引数末尾の ; をコマンドの区切りとして扱う。
    if text.endswith(";"):
        text = text[:-1] + r"\;"
    args = ["send-keys", "-t", target, "-l", "--", text]
    if prompt:
        args.extend([";", "send-keys", "-t", target, "Enter"])
    result = _tmux(*args)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"tmux の終了コード {result.returncode}")


def send_log(channel, row):
    path = Path(channel["path"]).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(line(row) + "\n")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def hub_body(row):
    message = row["message"].replace("\r\n", "\n").replace("\r", "\n")
    runs = re.findall(r"`+", message)
    fence = "`" * max(3, 1 + max(map(len, runs), default=0))
    url = json.loads(row["fields"])["url"]
    if url:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or re.search(r'[\s<>`]', url):
            raise ValueError("変化 JSON の web_url が不正です")
    body = f"【通知】\n\n{fence}\n{message}\n{fence}\n{url}"
    return re.sub(r"(?m)^([ \t]*)/", r"\1\\/", body)


def send_hub(channel, row):
    base = os.environ.get("GITLAB_URL", "").strip().rstrip("/")
    token = required_text(os.environ.get("GITLAB_TOKEN", "").strip(), "GITLAB_TOKEN")
    parts = urlsplit(base)
    if parts.scheme not in {"http", "https"} or not parts.hostname or (
        parts.username is not None or parts.query or parts.fragment
    ):
        raise ValueError("GITLAB_URL に GitLab のベース URL を設定してください")
    url = hub_issue_url(channel)
    api_prefix = base + "/api/v4/projects/"
    ref = None
    if url.startswith(api_prefix):
        ref = re.fullmatch(r"([^/]+)/issues/([1-9][0-9]*)/?", url[len(api_prefix):])
    if not ref and url.startswith(base + "/"):
        path = urlsplit(url[len(base):]).path.strip("/")
        ref = re.fullmatch(r"(.+?)/-/issues/([1-9][0-9]*)", path)
    if not ref:
        raise ValueError("issue_url に同じ GitLab のハブ Issue の URL を設定してください")
    project = quote(unquote(ref[1]), safe="")
    data = json.dumps({"body": hub_body(row)}).encode("utf-8")
    request = Request(f"{api_prefix}{project}/issues/{ref[2]}/notes", data=data,
                      headers={"PRIVATE-TOKEN": token, "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=30):
            pass
    except HTTPError as exc:
        exc.close()
        raise ValueError(f"GitLab の POST が HTTP {exc.code} で失敗しました") from None
    except (OSError, http.client.HTTPException):
        raise ValueError("GitLab の POST の応答を受け取れませんでした") from None


SEND = {"tmux": send_tmux, "prompt": send_tmux, "log": send_log, "hub": send_hub}


def open_outbox(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=60)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS outbox (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                repo TEXT NOT NULL, rkey TEXT NOT NULL, kind TEXT NOT NULL,
                event TEXT NOT NULL, channel TEXT NOT NULL, ts TEXT NOT NULL,
                message TEXT NOT NULL, fields TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                last_attempt_at REAL, next_attempt_at REAL NOT NULL,
                created_at REAL NOT NULL, sent_at REAL, dead_at REAL,
                max_age_minutes REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS outbox_queue ON outbox(channel, status, seq);
        """)
    except sqlite3.Error:
        db.close()
        raise
    return db


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def enqueue(db, repo, change, rendered, channels):
    key, kind = change["key"], change["kind"]
    if not isinstance(key, str) or not key:
        raise ValueError("key は空でない文字列です")
    basis = (fingerprint(change["before"]) if kind == "changed"
             else change["record"].get("updated_at"))
    now = time.time()
    ts = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
    selected = set()
    with db:
        for event, message, fields in rendered:
            for name, channel in channels.items():
                if (not channel["enabled"] or repo not in channel.get("repos", [repo])
                        or event not in channel.get("events", [event])):
                    continue
                selected.add(name)
                row_id = fingerprint([repo, key, kind, basis, message, name])[:32]
                db.execute("""
                    INSERT INTO outbox (id, repo, rkey, kind, event, channel, ts, message,
                                        fields, next_attempt_at, created_at, max_age_minutes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING
                """, (row_id, repo, key, kind, event, name, ts, message, canonical(fields),
                      now, now, channel["max_age_minutes"]))
    return selected


def expire(db, channels):
    with db:
        db.execute("BEGIN IMMEDIATE")
        now = time.time()
        pending = db.execute("""
            SELECT id, channel, rkey, created_at, max_age_minutes FROM outbox WHERE status = 'pending'
        """).fetchall()
        for row in pending:
            channel = channels.get(row["channel"])
            age = channel["max_age_minutes"] if channel else row["max_age_minutes"]
            if now - row["created_at"] >= age * 60:
                db.execute("UPDATE outbox SET status = 'dead', dead_at = ? WHERE id = ?",
                           (now, row["id"]))
                report(f"{row['channel']} key={row['rkey']}: 配信期限切れ (dead)")
        db.execute("DELETE FROM outbox WHERE status = 'sent' AND sent_at <= ?", (now - 7 * 86400,))
        db.execute("DELETE FROM outbox WHERE status = 'dead' AND dead_at <= ?", (now - 30 * 86400,))


@contextmanager
def delivery_lock(db):
    path = Path(db.execute("PRAGMA database_list").fetchone()["file"]).resolve()
    with path.with_name(path.name + ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True


def deliver(db, name, channel):
    if not channel["enabled"]:
        return False
    with delivery_lock(db) as acquired:
        if not acquired:
            return False
        return drain_channel(db, name, channel)


def drain_channel(db, name, channel):
    while True:
        with closing(db.execute("""
            SELECT * FROM outbox WHERE channel = ? AND status = 'pending' ORDER BY seq LIMIT 1
        """, (name,))) as cursor:
            row = cursor.fetchone()
        now = time.time()
        if row is None or row["next_attempt_at"] > now:
            return False
        if now - row["created_at"] >= channel["max_age_minutes"] * 60:
            with db:
                db.execute("UPDATE outbox SET status = 'dead', dead_at = ? WHERE id = ?", (now, row["id"]))
            report(f"{name} key={row['rkey']}: 配信期限切れ (dead)")
            continue
        try:
            SEND[channel["type"]](channel, row)
        except Exception as exc:
            reason = one_line(redact(exc))
            delay = min(3600, 60 * 2 ** min(row["attempts"], 6))
            now = time.time()
            with db:
                db.execute("""
                    UPDATE outbox SET attempts = attempts + 1, last_error = ?,
                        last_attempt_at = ?, next_attempt_at = ? WHERE id = ?
                """, (reason, now, now + delay, row["id"]))
            report(f"{name} key={row['rkey']}: {reason}")
            return True
        with db:
            db.execute("""
                UPDATE outbox SET status = 'sent', sent_at = ?, attempts = attempts + 1 WHERE id = ?
            """, (time.time(), row["id"]))


def notify(args, db, repos, channels):
    if args.repo not in repos:
        raise ValueError(f"repos に {args.repo} がありません")
    change = obj(json.loads(sys.stdin.read() if args.change == "-" else args.change), "変化 JSON")
    rendered = list(messages(args.repo, repos[args.repo], change))
    selected = enqueue(db, args.repo, change, rendered, channels)
    print_json({"repo": args.repo, "key": change["key"], "messages": len(rendered),
                "changes": change.get("changes", [])})
    for name, channel in channels.items():
        if name in selected:
            try:
                deliver(db, name, channel)
            except Exception as exc:
                report(f"{name} key={change['key']}: {exc}")
    return 0


def retry(args, db, repos, channels):
    # 配信中の行を別プロセスが期限切れにしないよう、整理も同じロックを使う。
    with delivery_lock(db) as acquired:
        if not acquired:
            return 0
        expire(db, channels)
    failures = [deliver(db, name, channel) for name, channel in channels.items()]
    return int(any(failures))


def status(args, db, repos, channels):
    rows = db.execute("""
        WITH outstanding AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY repo, channel ORDER BY last_attempt_at DESC, seq DESC
            ) AS latest
            FROM outbox WHERE status IN ('pending', 'dead')
        )
        SELECT repo, channel, SUM(status = 'pending') AS pending, SUM(status = 'dead') AS dead,
               MIN(CASE WHEN status = 'pending' THEN ts END) AS oldest_pending,
               MAX(CASE WHEN latest = 1 THEN last_error END) AS last_error
        FROM outstanding GROUP BY repo, channel ORDER BY repo, channel
    """)
    data = [dict(row) for row in rows]
    if args.json:
        print_json(data)
    else:
        for row in data:
            print(redact("{repo} / {channel}  pending={pending} dead={dead}  "
                         "oldest_pending={oldest_pending}  last_error={last_error}".format(**row)))
        if not data:
            print("未送信・期限切れの通知はありません")
    return 0


def run(args):
    try:
        path, repos, channels = load_config()
        with closing(open_outbox(path)) as db:
            return args.handler(args, db, repos, channels)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, sqlite3.Error) as exc:
        report(exc)
        return 1


def notify_arguments(parser):
    parser.add_argument("--repo", required=True)
    parser.add_argument("change", help="change-watch get --one の JSON（標準入力は -）")
    parser.set_defaults(handler=notify)


def retry_arguments(parser):
    parser.set_defaults(handler=retry)


def status_arguments(parser):
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(handler=status)


COMMANDS = (
    ("notify", notify_arguments, "変化を台帳に記録して配信する"),
    ("retry", retry_arguments, "台帳の未送信通知を再送する"),
    ("status", status_arguments, "台帳の未送信・期限切れ件数を表示する"),
)


def register(api):
    for name, arguments, help_text in COMMANDS:
        # change-watch の status は取得元と consumer の状況を表示する。
        plugin_name = "notify-status" if name == "status" else name
        arguments(api.add_command(plugin_name, lambda _cfg, _store, args: run(args), help_text, needs_config=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    for name, arguments, help_text in COMMANDS:
        arguments(commands.add_parser(name, help=help_text))
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
