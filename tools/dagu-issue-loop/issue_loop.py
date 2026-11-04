#!/usr/bin/env python3
"""dagu-issue-loop — GitLab の issue をポーリングして変化を拾い、エージェント CLI を 1 セッションずつ流す。

Dagu の 2 本の DAG から呼ばれる想定の、標準ライブラリだけで動く 1 ファイルのスクリプト。

    issue_loop.py poll     # issue を取得し、前回からの変化を待ち行列へ積む（重複はまとめる）
    issue_loop.py run      # 待ち行列から 1 件取り出してエージェント CLI を実行する（同時に 1 セッションだけ）
    issue_loop.py status   # 待ち行列・実行中・打ち切りの一覧を JSON で出す
    issue_loop.py forget IID   # 打ち切り（dead）や既読から外し、次の poll で拾い直させる
    issue_loop.py handle       # api-watch が検知した変化を 1 件受け取ってエージェント CLI を実行する
                               # （poll と待ち行列の代わりに api-watch と Dagu のキューを使う構成）

設計は docs/designs/dagu-issue-loop-design.md。agent-loop の hooks/gitlab-issue-hook.py
（既読 + ack の 2 相）と agent_loop/semaphore.py（pid 付きスロットファイル）を下敷きにしている。

終了コード:
    0  正常（積んだ・実行して成功した・待ち行列が空・別セッションが実行中で見送った）
    1  エージェント CLI が失敗した / タイムアウトした
    2  設定の誤り
    3  GitLab に届かない・認証に失敗した
    4  別のセッションが実行中だった（handle だけ。Dagu に後で再試行させる）
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

STATE_VERSION = 1
HTTP_TIMEOUT_SEC = 20
MAX_BODY_BYTES = 10 * 1024 * 1024
DEFAULT_TOKEN_ENVS = ("GITLAB_TOKEN", "GL_TOKEN")

EXIT_OK = 0
EXIT_AGENT_FAILED = 1
EXIT_CONFIG = 2
EXIT_GITLAB = 3
EXIT_BUSY = 4

# 変化として扱う項目。updated_at だけが動いて下の項目が同じなら「変化なし」として既読を進めるだけにする
# （MR からの言及やシステム側の更新でエージェントを起こさないため）。
SNAPSHOT_FIELDS = ("title", "state", "labels", "assignees", "milestone", "due_date",
                   "user_notes_count", "description_sha")

DEFAULT_PROMPT = """\
GitLab の issue に変化がありました。内容を確認し、必要な対応をしてください。

- プロジェクト: {project}
- issue: #{iid} {title}
- URL: {web_url}
- 変化: {change_kind}
{changes}

issue の内容（JSON）:
{issue_json}
"""


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[issue-loop {ts}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    pass


@dataclass
class Config:
    api_url: str
    project: str
    token: str
    issue_state: str = "opened"
    issue_labels: str = ""
    issue_assignee: str = ""
    issue_scope: str = "all"
    max_pages: int = 10
    baseline: bool = True
    state_dir: Path = field(default_factory=lambda: Path.home() / ".agents" / "dagu-issue-loop")
    lock_file: Path | None = None
    agent: str = ""
    agent_cmd: str = ""
    agent_prompt_via: str = "stdin"
    agent_model: str = ""
    agent_cwd: str = ""
    agent_timeout: int = 3600
    prompt_template: str = ""
    max_attempts: int = 3
    retry_backoff: int = 300
    self_username: str = ""
    drain: int = 1
    poll_jitter: int = 0

    @property
    def project_dir(self) -> Path:
        h = hashlib.sha256(f"{self.api_url}|{self.project}".encode()).hexdigest()[:12]
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", self.project)[-48:]
        return self.state_dir / f"{safe}-{h}"

    @property
    def session_lock(self) -> Path:
        # 既定はプロジェクトをまたいで 1 つ。別プロジェクトの DAG を並べても同時に動くのは 1 セッションだけ。
        return self.lock_file or (self.state_dir / "session.lock")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"ISSUE_LOOP_{name}", default)


def _int(value: str, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} は整数で指定してください: {value!r}")


def build_config(args: argparse.Namespace, *, need_gitlab: bool = True) -> Config:
    api_url = args.gitlab_api_url or os.environ.get("GITLAB_API_URL", "")
    if not api_url:
        base = args.gitlab_url or os.environ.get("GITLAB_URL", "")
        if base:
            api_url = base.rstrip("/") + "/api/v4"
    project = args.project or os.environ.get("GITLAB_PROJECT", "")
    token = ""
    for name in ([args.token_env] if args.token_env else []) + list(DEFAULT_TOKEN_ENVS):
        if name and os.environ.get(name):
            token = os.environ[name]
            break
    checks = [("GITLAB_URL か GITLAB_API_URL", api_url), ("GITLAB_PROJECT", project)]
    if need_gitlab:
        checks.append(("GITLAB_TOKEN", token))
    missing = [n for n, v in checks if not v]
    if missing:
        raise ConfigError("足りない設定: " + ", ".join(missing))
    state_dir = Path(args.state_dir or _env("STATE_DIR") or Path.home() / ".agents" / "dagu-issue-loop")
    lock = args.lock_file or _env("LOCK_FILE")
    cfg = Config(
        api_url=api_url.rstrip("/"),
        project=project,
        token=token,
        issue_state=args.issue_state or _env("ISSUE_STATE", "opened"),
        issue_labels=args.labels if args.labels is not None else _env("ISSUE_LABELS"),
        issue_assignee=args.assignee if args.assignee is not None else _env("ISSUE_ASSIGNEE"),
        issue_scope=_env("ISSUE_SCOPE", "all"),
        max_pages=_int(_env("MAX_PAGES", "10"), "ISSUE_LOOP_MAX_PAGES"),
        baseline=not (getattr(args, "no_baseline", False) or _env("NO_BASELINE") in ("1", "true")),
        state_dir=state_dir.expanduser(),
        lock_file=Path(lock).expanduser() if lock else None,
        agent=getattr(args, "agent", None) or _env("AGENT"),
        agent_cmd=getattr(args, "agent_cmd", None) or _env("AGENT_CMD"),
        agent_prompt_via=getattr(args, "prompt_via", None) or _env("AGENT_PROMPT_VIA", "stdin"),
        agent_model=getattr(args, "model", None) or _env("AGENT_MODEL"),
        agent_cwd=getattr(args, "cwd", None) or _env("AGENT_CWD"),
        agent_timeout=_int(getattr(args, "timeout", None) or _env("AGENT_TIMEOUT", "3600"),
                           "ISSUE_LOOP_AGENT_TIMEOUT"),
        prompt_template=getattr(args, "prompt_template", None) or _env("PROMPT_TEMPLATE"),
        max_attempts=_int(_env("MAX_ATTEMPTS", "3"), "ISSUE_LOOP_MAX_ATTEMPTS"),
        retry_backoff=_int(_env("RETRY_BACKOFF", "300"), "ISSUE_LOOP_RETRY_BACKOFF"),
        self_username=_env("SELF_USERNAME"),
        drain=_int(getattr(args, "drain", None) or _env("DRAIN", "1"), "ISSUE_LOOP_DRAIN"),
        poll_jitter=_int(getattr(args, "jitter", None) or _env("POLL_JITTER", "0"), "ISSUE_LOOP_POLL_JITTER"),
    )
    if cfg.agent_prompt_via not in ("stdin", "arg", "file"):
        raise ConfigError("ISSUE_LOOP_AGENT_PROMPT_VIA は stdin / arg / file のどれか")
    return cfg


# ---------------------------------------------------------------------------
# GitLab REST（必要な 4 本だけ）
# ---------------------------------------------------------------------------

class GitLabError(Exception):
    def __init__(self, message: str, *, status: int | None = None, kind: str = "error"):
        super().__init__(message)
        self.status = status
        self.kind = kind  # auth | transient | error


class GitLab:
    def __init__(self, api_url: str, project: str, token: str):
        self.api_url = api_url.rstrip("/")
        self.project = project
        self.token = token
        self._pid = urllib.parse.quote(project, safe="")

    def _get(self, path: str, params: dict[str, str] | None = None) -> tuple[Any, dict[str, str]]:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        req = urllib.request.Request(
            f"{self.api_url}{path}{query}",
            headers={"PRIVATE-TOKEN": self.token, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
                body = resp.read(MAX_BODY_BYTES + 1)
                headers = {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise GitLabError("GitLab の認証に失敗しました", status=exc.code, kind="auth") from exc
            kind = "transient" if exc.code == 429 or exc.code >= 500 else "error"
            raise GitLabError(f"GitLab が {exc.code} を返しました: {path}", status=exc.code, kind=kind) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitLabError(f"GitLab に届きません: {exc}", kind="transient") from exc
        if len(body) > MAX_BODY_BYTES:
            raise GitLabError("GitLab の応答が大きすぎます")
        try:
            return json.loads(body.decode("utf-8")), headers
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitLabError("GitLab の応答が JSON ではありません") from exc

    def list_issues(self, *, state: str, labels: str, assignee: str, scope: str,
                    max_pages: int) -> tuple[list[dict[str, Any]], bool]:
        """(issues, complete)。complete=False は max_pages で打ち切ったことを表す。"""
        params = {"scope": scope, "per_page": "100", "order_by": "updated_at", "sort": "asc"}
        if state and state != "all":
            params["state"] = state
        if labels:
            params["labels"] = labels
        if assignee:
            params["assignee_username"] = assignee
        out: list[dict[str, Any]] = []
        page = "1"
        for _ in range(max(1, max_pages)):
            data, headers = self._get(f"/projects/{self._pid}/issues", {**params, "page": page})
            if not isinstance(data, list):
                raise GitLabError("issues の応答が配列ではありません")
            out.extend(i for i in data if isinstance(i, dict))
            page = headers.get("x-next-page", "")
            if not page:
                return out, True
        return out, False

    def get_issue(self, iid: int | str) -> dict[str, Any]:
        data, _ = self._get(f"/projects/{self._pid}/issues/{iid}")
        if not isinstance(data, dict):
            raise GitLabError("issue の応答が不正です")
        return data

    def list_notes(self, iid: int | str) -> list[dict[str, Any]]:
        data, _ = self._get(f"/projects/{self._pid}/issues/{iid}/notes",
                            {"order_by": "created_at", "sort": "desc", "per_page": "50"})
        return [n for n in data if isinstance(n, dict)] if isinstance(data, list) else []

    def current_username(self) -> str:
        data, _ = self._get("/user")
        return str(data.get("username") or "") if isinstance(data, dict) else ""


# ---------------------------------------------------------------------------
# OS ごとの違い（ファイルロック・プロセスの生存確認・子孫ごとの停止・シグナル）
# ---------------------------------------------------------------------------

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import ctypes
    import msvcrt
else:
    import fcntl


class LockBusy(Exception):
    pass


def lock_file(f, *, blocking: bool) -> None:
    """開いたファイルに排他ロックを掛ける。blocking=False で取れなければ LockBusy。

    POSIX は flock、Windows は msvcrt.locking（先頭 1 バイト）。どちらもプロセスが死ねば OS が外す。
    """
    if not IS_WINDOWS:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise LockBusy()
        return
    f.seek(0)
    while True:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if not blocking:
                raise LockBusy()
            time.sleep(0.05)


def unlock_file(f) -> None:
    if not IS_WINDOWS:
        fcntl.flock(f, fcntl.LOCK_UN)
        return
    f.seek(0)
    with contextlib.suppress(OSError):
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        # os.kill(pid, 0) は Windows では生存確認にならず、相手を終了させてしまう。API で問い合わせる
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.GetLastError() == 5                # ERROR_ACCESS_DENIED = 生きているが見えない
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259                         # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def spawn_group_kwargs() -> dict[str, Any]:
    """子孫ごと止められるよう、エージェント CLI を別のプロセスグループで起動する引数。"""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def terminate_tree(proc: subprocess.Popen, grace: float = 15.0) -> None:
    """エージェント CLI を子孫ごと止める。まず穏便に頼み、grace 秒で強制終了する。"""
    if IS_WINDOWS:
        with contextlib.suppress(OSError, ValueError):
            proc.send_signal(signal.CTRL_BREAK_EVENT)   # 同じコンソールのグループにだけ届く
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        # 本体が先に終わっても子孫は残りうるので、ツリーごと落とす（終わっていれば何もしない）
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)


def stop_signals() -> list[int]:
    """止めてほしいときに届くシグナル。Windows の Ctrl+Break は SIGBREAK で受ける。"""
    names = ("SIGTERM", "SIGINT", "SIGHUP", "SIGBREAK")
    return [getattr(signal, n) for n in names if hasattr(signal, n)]


def split_command(cmd: str) -> list[str]:
    """コマンド行を argv に分ける。Windows ではバックスラッシュのパスを壊さない。"""
    if not IS_WINDOWS:
        return shlex.split(cmd)
    out = []
    for tok in shlex.split(cmd, posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def resolve_executable(argv: list[str]) -> list[str]:
    """先頭を PATH から引いて絶対パスにする（Windows で claude.cmd のような .cmd を起動できるように）。"""
    if argv and not os.path.dirname(argv[0]):
        found = shutil.which(argv[0])
        if found:
            return [found, *argv[1:]]
    return argv


# ---------------------------------------------------------------------------
# 状態ファイル（state.json）と排他
# ---------------------------------------------------------------------------

def empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "baseline_done": False, "seen": {}, "queue": {},
            "inflight": None, "dead": {}, "last_poll_at": 0.0, "handle_attempts": {}}


def normalize_state(raw: Any) -> dict[str, Any]:
    st = empty_state()
    if not isinstance(raw, dict):
        return st
    st["baseline_done"] = bool(raw.get("baseline_done"))
    for k in ("seen", "queue", "dead", "handle_attempts"):
        if isinstance(raw.get(k), dict):
            st[k] = raw[k]
    if isinstance(raw.get("inflight"), dict):
        st["inflight"] = raw["inflight"]
    try:
        st["last_poll_at"] = float(raw.get("last_poll_at") or 0.0)
    except (TypeError, ValueError):
        pass
    return st


@contextlib.contextmanager
def locked_state(cfg: Config) -> Iterator[dict[str, Any]]:
    """state.json を排他して読み、ブロックを抜けたら原子的に書き戻す（poll と run が同時に触っても壊れない）。"""
    d = cfg.project_dir
    d.mkdir(parents=True, exist_ok=True)
    path = d / "state.json"
    with open(d / "state.lock", "a+") as lf:
        lock_file(lf, blocking=True)
        try:
            try:
                st = normalize_state(json.loads(path.read_text(encoding="utf-8")))
            except FileNotFoundError:
                st = empty_state()
            except (OSError, json.JSONDecodeError):
                broken = path.with_suffix(f".broken-{int(time.time())}")
                with contextlib.suppress(OSError):
                    path.replace(broken)
                log(f"state.json が読めないため退避して作り直します: {broken}")
                st = empty_state()
            yield st
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        finally:
            unlock_file(lf)


class SessionBusy(Exception):
    pass


@contextlib.contextmanager
def single_session(cfg: Config) -> Iterator[Path]:
    """エージェント CLI のセッションを同時に 1 つだけにする。

    1. session.lock を待たずに排他ロックする（取れなければ別の run が実行中）。
       ロックはプロセスが死ねば OS が外すので、取りっぱなしにはならない。
    2. それでも、run が強制終了されてエージェント CLI だけが生き残ると、ロックは外れたのに
       セッションは続いている。そこで実行中の子の pid を session.json に書き、取得時に生きていれば
       ロックを返して見送る（agent-loop のスロットファイルと同じ考え方）。
    """
    lock_path = cfg.session_lock
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    info_path = lock_path.with_suffix(".json")
    lf = open(lock_path, "a+")
    try:
        try:
            lock_file(lf, blocking=False)
        except LockBusy:
            raise SessionBusy(f"別のセッションが実行中です（{lock_path}）")
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            info = {}
        child = int(info.get("child_pid") or 0)
        if child and child != os.getpid() and pid_alive(child):
            unlock_file(lf)
            raise SessionBusy(
                f"前の実行が残したエージェント CLI（pid {child}）がまだ動いています。終わるのを待つか止めてください")
        info_path.write_text(json.dumps({"runner_pid": os.getpid(), "child_pid": 0,
                                         "project": cfg.project, "started_at": time.time()}),
                             encoding="utf-8")
        try:
            yield info_path
        finally:
            with contextlib.suppress(OSError):
                info_path.unlink()
            unlock_file(lf)
    finally:
        lf.close()


# ---------------------------------------------------------------------------
# 変化の判定
# ---------------------------------------------------------------------------

def snapshot(issue: dict[str, Any]) -> dict[str, Any]:
    desc = str(issue.get("description") or "")
    milestone = issue.get("milestone")
    return {
        "title": str(issue.get("title") or ""),
        "state": str(issue.get("state") or ""),
        "labels": sorted(str(x) for x in (issue.get("labels") or [])),
        "assignees": sorted(str(a.get("username")) for a in (issue.get("assignees") or [])
                            if isinstance(a, dict)),
        "milestone": str(milestone.get("title")) if isinstance(milestone, dict) else "",
        "due_date": str(issue.get("due_date") or ""),
        "user_notes_count": int(issue.get("user_notes_count") or 0),
        "description_sha": hashlib.sha256(desc.encode()).hexdigest()[:16],
    }


def describe_changes(prev: dict[str, Any] | None, cur: dict[str, Any]) -> tuple[str, list[str]]:
    """(change_kind, 箇条書きの行)。prev が無ければ new。"""
    if not prev:
        return "new", ["- 新しい issue"]
    lines: list[str] = []
    if prev.get("state") != cur["state"]:
        lines.append(f"- 状態: {prev.get('state')} → {cur['state']}")
    if prev.get("title") != cur["title"]:
        lines.append(f"- タイトル: {prev.get('title')} → {cur['title']}")
    added = sorted(set(cur["labels"]) - set(prev.get("labels") or []))
    removed = sorted(set(prev.get("labels") or []) - set(cur["labels"]))
    if added:
        lines.append("- ラベル追加: " + ", ".join(added))
    if removed:
        lines.append("- ラベル削除: " + ", ".join(removed))
    if prev.get("assignees") != cur["assignees"]:
        lines.append("- 担当: " + (", ".join(cur["assignees"]) or "（なし）"))
    if prev.get("milestone") != cur["milestone"]:
        lines.append(f"- マイルストーン: {cur['milestone'] or '（なし）'}")
    if prev.get("due_date") != cur["due_date"]:
        lines.append(f"- 期日: {cur['due_date'] or '（なし）'}")
    diff_notes = cur["user_notes_count"] - int(prev.get("user_notes_count") or 0)
    if diff_notes > 0:
        lines.append(f"- コメント +{diff_notes}")
    if prev.get("description_sha") != cur["description_sha"]:
        lines.append("- 本文が更新された")
    return "updated", lines


def issue_key(issue: dict[str, Any]) -> str | None:
    iid = issue.get("iid")
    return str(iid) if iid is not None else None


def poll_into_state(st: dict[str, Any], issues: list[dict[str, Any]], *, complete: bool,
                    baseline: bool, now: float) -> dict[str, int]:
    """取得した issue を state に反映する。戻り値は件数の内訳。

    重複制御:
      - 既読（seen）と同じ版なら何もしない。版が違っても SNAPSHOT_FIELDS が同じなら既読を進めるだけ
      - 待ち行列は issue ごとに 1 件。同じ issue の変化が重なったら最新の版へまとめる（試行回数は引き継ぐ）
      - 実行中（inflight）の issue と同じ版は積まない
      - 打ち切り（dead）の issue は、打ち切ったときより新しい変化が来たら蘇らせる
    """
    counts = {"fetched": len(issues), "queued": 0, "coalesced": 0, "unchanged": 0,
              "baseline": 0, "dropped": 0}
    seen: dict[str, Any] = st["seen"]
    queue: dict[str, Any] = st["queue"]
    dead: dict[str, Any] = st["dead"]
    inflight = st.get("inflight") or {}

    if not st["baseline_done"]:
        st["baseline_done"] = True
        if baseline:
            # 初回は今ある issue をすべて既読にして、エージェントを一斉に起こさない
            for issue in issues:
                key = issue_key(issue)
                if key:
                    seen[key] = {"version": str(issue.get("updated_at") or ""), "snapshot": snapshot(issue)}
            counts["baseline"] = len(seen)
            st["last_poll_at"] = now
            return counts

    present: set[str] = set()
    for issue in issues:
        key = issue_key(issue)
        if key is None:
            continue
        present.add(key)
        version = str(issue.get("updated_at") or "")
        snap = snapshot(issue)
        prev = seen.get(key)
        if prev and prev.get("version") == version:
            counts["unchanged"] += 1
            continue
        if inflight.get("key") == key and inflight.get("version") == version:
            counts["unchanged"] += 1
            continue
        queued = queue.get(key)
        if queued and queued.get("version") == version:
            counts["unchanged"] += 1
            continue
        base_snap = (queued or {}).get("prev_snapshot", prev.get("snapshot") if prev else None)
        if prev and not queued and prev.get("snapshot") == snap:
            seen[key] = {"version": version, "snapshot": snap}   # 中身が同じ更新は既読を進めるだけ
            counts["unchanged"] += 1
            continue
        if key in dead:
            if dead[key].get("version") == version:
                counts["unchanged"] += 1
                continue
            dead.pop(key, None)
        kind, lines = describe_changes(base_snap, snap)
        queue[key] = {
            "iid": issue.get("iid"),
            "version": version,
            "kind": kind,
            "changes": lines,
            "prev_snapshot": base_snap,
            "snapshot": snap,
            "issue": issue,
            "enqueued_at": (queued or {}).get("enqueued_at", now),
            "updated_at_queue": now,
            "attempts": int((queued or {}).get("attempts") or 0),
            "next_attempt_at": float((queued or {}).get("next_attempt_at") or 0.0),
        }
        counts["coalesced" if queued else "queued"] += 1

    if complete:
        # フィルタから外れた issue（閉じた・ラベルが外れた）は待ち行列から下ろす。実行中のものは触らない
        for key in [k for k in queue if k not in present and k != inflight.get("key")]:
            queue.pop(key, None)
            counts["dropped"] += 1
    st["last_poll_at"] = now
    return counts


def pick_next(st: dict[str, Any], now: float) -> dict[str, Any] | None:
    ready = [q for q in st["queue"].values() if float(q.get("next_attempt_at") or 0.0) <= now]
    if not ready:
        return None
    ready.sort(key=lambda q: (float(q.get("enqueued_at") or 0.0), int(q.get("iid") or 0)))
    return ready[0]


# ---------------------------------------------------------------------------
# プロンプトとエージェント CLI
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{(project|iid|title|web_url|change_kind|changes|issue_json|labels|attempt)\}")


def render_prompt(template: str, cfg: Config, item: dict[str, Any]) -> str:
    issue = item.get("issue") or {}
    values = {
        "project": cfg.project,
        "iid": str(item.get("iid")),
        "title": str(issue.get("title") or ""),
        "web_url": str(issue.get("web_url") or ""),
        "change_kind": "新規" if item.get("kind") == "new" else "更新",
        "changes": "\n".join(item.get("changes") or []),
        "issue_json": json.dumps(issue, ensure_ascii=False, indent=2),
        "labels": ", ".join(issue.get("labels") or []),
        "attempt": str(int(item.get("attempts") or 0) + 1),
    }
    # 既知の名前だけ置き換える。テンプレート中の JSON などの波括弧はそのまま残る
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def load_template(cfg: Config) -> str:
    if not cfg.prompt_template:
        return DEFAULT_PROMPT
    path = Path(cfg.prompt_template).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"プロンプトのテンプレートが読めません: {path} ({exc})")


def _profile_dirs() -> list[Path]:
    dirs = []
    if os.environ.get("ISSUE_LOOP_AGENT_PROFILES"):
        dirs.append(Path(os.environ["ISSUE_LOOP_AGENT_PROFILES"]).expanduser())
    dirs.append(Path.home() / ".agents" / "agents")
    dirs.append(Path(__file__).resolve().parents[2] / "agents")   # リポジトリから直接動かすとき
    return dirs


def resolve_agent_argv(cfg: Config, prompt_file: Path, output_file: Path) -> tuple[list[str], str]:
    """(argv, prompt_via)。ISSUE_LOOP_AGENT_CMD があればそれを、無ければ agents/<name>.json を使う。"""
    if cfg.agent_cmd:
        argv = split_command(cfg.agent_cmd)
        via = cfg.agent_prompt_via
        prompt_flag = None
    elif cfg.agent:
        prof = None
        for d in _profile_dirs():
            p = d / f"{cfg.agent}.json"
            if p.is_file():
                try:
                    prof = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ConfigError(f"エージェント定義が読めません: {p} ({exc})")
                break
        if prof is None:
            raise ConfigError(f"エージェント定義 {cfg.agent}.json が見つかりません")
        head = prof.get("headless") if isinstance(prof.get("headless"), dict) else prof
        argv = [str(a) for a in (head.get("command") or [])]
        if not argv:
            raise ConfigError(f"{cfg.agent}.json に command がありません")
        via = "arg" if head.get("prompt_via") == "argv" else "stdin"
        prompt_flag = head.get("prompt_flag")
        model_flag = head.get("model_flag")
        if cfg.agent_model and model_flag and not any("{model}" in a for a in argv):
            argv += [str(model_flag), cfg.agent_model]
    else:
        raise ConfigError("ISSUE_LOOP_AGENT（例: claude）か ISSUE_LOOP_AGENT_CMD を指定してください")

    subst = {"{model}": cfg.agent_model, "{output_file}": str(output_file),
             "{prompt_file}": str(prompt_file)}
    out: list[str] = []
    for a in argv:
        for k, v in subst.items():
            a = a.replace(k, v)
        out.append(a)
    if any("{model}" in a for a in argv) and not cfg.agent_model:
        raise ConfigError("このエージェント定義はモデル名が要ります（ISSUE_LOOP_AGENT_MODEL）")
    if via == "arg":
        prompt = prompt_file.read_text(encoding="utf-8")
        out += ([str(prompt_flag)] if prompt_flag else []) + [prompt]
    elif via == "file" and not any(str(prompt_file) in a for a in out):
        out.append(str(prompt_file))
    return resolve_executable(out), via


@dataclass
class RunResult:
    ok: bool
    returncode: int | None
    timed_out: bool
    log_path: Path


_stop_requested: list[int] = []


def run_agent(cfg: Config, prompt: str, item: dict[str, Any], info_path: Path) -> RunResult:
    run_dir = cfg.project_dir / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = run_dir / f"{stamp}-issue{item.get('iid')}.log"
    prompt_file = run_dir / f"{stamp}-issue{item.get('iid')}.prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    output_file = run_dir / f"{stamp}-issue{item.get('iid')}.out.txt"
    argv, via = resolve_agent_argv(cfg, prompt_file, output_file)

    env = dict(os.environ)
    env.update({"ISSUE_LOOP_IID": str(item.get("iid")), "ISSUE_LOOP_PROJECT": cfg.project,
                "ISSUE_LOOP_PROMPT_FILE": str(prompt_file)})
    cwd = os.path.expanduser(cfg.agent_cwd) if cfg.agent_cwd else None
    log(f"#{item.get('iid')} のセッションを始めます: {shlex.join(argv[:6])}{' …' if len(argv) > 6 else ''}")
    with open(log_path, "wb") as lf:
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, env=env,
                stdin=subprocess.PIPE if via == "stdin" else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                **spawn_group_kwargs(),
            )
        except OSError as exc:
            raise ConfigError(f"エージェント CLI を起動できません: {argv[0]} ({exc})")
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info.update({"child_pid": proc.pid, "iid": item.get("iid"), "log": str(log_path)})
        info_path.write_text(json.dumps(info), encoding="utf-8")

        # 出力は別スレッドで読んでログと標準出力へ流す（パイプの select は Windows で使えないため）。
        # 文字コードは決め打ちせず bytes のまま写す
        def pump() -> None:
            assert proc.stdout is not None
            for chunk in iter(lambda: proc.stdout.read1(65536), b""):
                lf.write(chunk)
                lf.flush()
                with contextlib.suppress(OSError, ValueError):
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.flush()
        reader = threading.Thread(target=pump, daemon=True)
        reader.start()

        if via == "stdin" and proc.stdin:
            # 子が stdin を読まないままパイプが埋まると write が戻らず、下の打ち切り判定に
            # 進めなくなる。別スレッドで流し、タイムアウトと中止は常に効くようにする
            def feed() -> None:
                with contextlib.suppress(BrokenPipeError, OSError, ValueError):
                    proc.stdin.write(prompt.encode("utf-8"))
                with contextlib.suppress(BrokenPipeError, OSError, ValueError):
                    proc.stdin.close()
            threading.Thread(target=feed, daemon=True).start()

        deadline = time.monotonic() + cfg.agent_timeout if cfg.agent_timeout > 0 else None
        timed_out = False
        while proc.poll() is None:
            if _stop_requested or (deadline is not None and time.monotonic() > deadline):
                timed_out = not _stop_requested
                terminate_tree(proc)
                break
            time.sleep(0.2)
        rc = proc.wait()
        # 出力を握った子孫が残っていても待ち続けない
        reader.join(timeout=5)
    if output_file.exists():
        with open(log_path, "ab") as lf:
            lf.write(b"\n--- output_file ---\n" + output_file.read_bytes())
    ok = rc == 0 and not timed_out and not _stop_requested
    if timed_out:
        log(f"#{item.get('iid')} は {cfg.agent_timeout} 秒で打ち切りました")
    return RunResult(ok=ok, returncode=rc, timed_out=timed_out, log_path=log_path)


def _install_signal_handlers() -> None:
    # Dagu の中止・タイムアウトは POSIX では SIGTERM で来る。子を止めて、失敗として記録してから抜ける。
    # Windows の Dagu は Job Object ごと強制終了するのでここには来ない（次の run が inflight から数え直す）
    def handler(signum, _frame):
        _stop_requested.append(signum)
    for sig in stop_signals():
        with contextlib.suppress(OSError, ValueError):
            signal.signal(sig, handler)


# ---------------------------------------------------------------------------
# 実行後の既読の付け方（自分の書き込みで自分を起こさない）
# ---------------------------------------------------------------------------

def _parse_ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def others_acted(gl: GitLab, iid: Any, since: float, self_username: str) -> bool:
    """実行中に自分以外が issue にコメント・操作したか（システムノートを含む）。"""
    if not self_username:
        # 自分が分からないと自分の書き込みと区別できない。「他人が動いた」と見なすと、成功のたびに
        # 同じ issue を流し直し続けてしまうので、動いていない側に倒す（実行直後の版を既読にする）
        return False
    for note in gl.list_notes(iid):
        if _parse_ts(str(note.get("created_at") or "")) < since:
            break
        author = (note.get("author") or {}).get("username")
        if author and author != self_username:
            return True
    return False


def settle_success(st: dict[str, Any], item: dict[str, Any], *, current: dict[str, Any] | None,
                   others: bool) -> str:
    """成功した実行を既読にする。戻り値は待ち行列の扱い（dropped / kept）。

    エージェントが issue にコメントやラベル変更をすると updated_at が進み、そのままでは次の poll で
    自分の書き込みに反応してしまう。そこで実行直後の issue を取り直して既読にする。実行中に自分以外の
    動きがあったときだけ待ち行列に残し、もう一度流す。
    """
    key = str(item.get("iid"))
    queued = st["queue"].get(key)
    if current is not None:
        st["seen"][key] = {"version": str(current.get("updated_at") or ""), "snapshot": snapshot(current)}
    else:
        st["seen"][key] = {"version": item["version"], "snapshot": item["snapshot"]}
    newer = bool(queued) and queued.get("version") != item["version"]
    if newer and (others or current is None):
        queued["prev_snapshot"] = item["snapshot"]
        queued["attempts"] = 0
        queued["next_attempt_at"] = 0.0
        if current is not None:
            queued.update({"version": str(current.get("updated_at") or ""), "snapshot": snapshot(current),
                           "issue": current})
        return "kept"
    if not newer and others and current is not None:
        # 実行中に人がコメントしたが、poll がまだ拾っていない場合
        st["queue"][key] = {**item, "prev_snapshot": item["snapshot"], "snapshot": snapshot(current),
                            "version": str(current.get("updated_at") or ""), "issue": current,
                            "kind": "updated", "changes": ["- 前回の実行中に別の人の動きがあった"],
                            "attempts": 0, "next_attempt_at": 0.0}
        return "kept"
    st["queue"].pop(key, None)
    return "dropped"


def settle_failure(st: dict[str, Any], item: dict[str, Any], cfg: Config, now: float, reason: str) -> str:
    key = str(item.get("iid"))
    queued = st["queue"].get(key)
    if queued is None:
        return "gone"
    attempts = int(queued.get("attempts") or 0) + 1
    if attempts >= cfg.max_attempts:
        st["queue"].pop(key, None)
        st["dead"][key] = {"iid": item.get("iid"), "version": queued.get("version"),
                           "reason": reason, "attempts": attempts, "at": now}
        # 打ち切った版は既読にする。次に issue が動いたらもう一度拾う
        st["seen"][key] = {"version": queued.get("version"), "snapshot": queued.get("snapshot")}
        return "dead"
    queued["attempts"] = attempts
    queued["next_attempt_at"] = now + cfg.retry_backoff * (2 ** (attempts - 1))
    queued["last_error"] = reason
    return "retry"


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------

def cmd_poll(cfg: Config, gl: GitLab, *, sleep: Callable[[float], None] = time.sleep) -> int:
    if cfg.poll_jitter > 0:
        # 定刻（毎時 0 分）にそろって GitLab を叩かないよう、0〜poll_jitter 秒ずらす
        delay = random.uniform(0, cfg.poll_jitter)
        log(f"GitLab への問い合わせを {delay:.0f} 秒ずらします")
        sleep(delay)
    try:
        issues, complete = gl.list_issues(state=cfg.issue_state, labels=cfg.issue_labels,
                                          assignee=cfg.issue_assignee, scope=cfg.issue_scope,
                                          max_pages=cfg.max_pages)
    except GitLabError as exc:
        log(str(exc))
        return EXIT_GITLAB
    if not complete:
        log(f"issue が {cfg.max_pages * 100} 件を超えたため途中までで判定します（ISSUE_LOOP_MAX_PAGES）")
    with locked_state(cfg) as st:
        counts = poll_into_state(st, issues, complete=complete, baseline=cfg.baseline, now=time.time())
        waiting = len(st["queue"])
    if counts["baseline"]:
        log(f"初回のため、今ある {counts['baseline']} 件を既読にしました（次の変化から拾います）")
    else:
        log("取得 {fetched} 件 / 新しく積んだ {queued} / まとめた {coalesced} / 変化なし {unchanged} / "
            "下ろした {dropped}".format(**counts) + f" / 待ち {waiting}")
    return EXIT_OK


def _recover_stale_inflight(cfg: Config) -> None:
    """前の run が強制終了して inflight が残っていたら失敗として数え直す（single_session の内側で呼ぶ）。"""
    with locked_state(cfg) as st:
        stale = st.get("inflight")
        if stale:
            log(f"前回の実行（#{stale.get('key')}）が終わりを記録せずに止まっていたため、失敗として数えます")
            item = st["queue"].get(str(stale.get("key")))
            if item:
                settle_failure(st, item, cfg, time.time(), "前回の実行が途中で止まった")
            st["inflight"] = None


def cmd_run(cfg: Config, gl: GitLab) -> int:
    template = load_template(cfg)
    _install_signal_handlers()
    try:
        with single_session(cfg) as info_path:
            _recover_stale_inflight(cfg)
            # ここまで GitLab には触らない。毎分起動しても、待ち行列が空なら問い合わせは 0 回
            rc = EXIT_OK
            for _ in range(max(1, cfg.drain)):
                if _stop_requested:
                    break
                now = time.time()
                with locked_state(cfg) as st:
                    item = pick_next(st, now)
                    if item is None:
                        log("待ち行列は空です" if not st["queue"] else "待ち行列は再試行の待ち時間中です")
                        break
                    item = json.loads(json.dumps(item))   # 書き戻しの影響を受けないよう複製
                    st["inflight"] = {"key": str(item["iid"]), "version": item["version"],
                                      "started_at": now, "pid": os.getpid()}
                prompt = render_prompt(template, cfg, item)
                try:
                    result = run_agent(cfg, prompt, item, info_path)
                except ConfigError:
                    # 起動できない設定の誤りは issue のせいではない。試行回数を使わずに inflight だけ外す
                    with locked_state(cfg) as st:
                        st["inflight"] = None
                    raise
                current = None
                others = True
                if result.ok:
                    if not cfg.self_username:
                        with contextlib.suppress(GitLabError):
                            cfg.self_username = gl.current_username()
                        if not cfg.self_username:
                            log("自分のユーザー名が分かりません。実行中の他人の動きは拾えません"
                                "（ISSUE_LOOP_SELF_USERNAME で指定できます）")
                    try:
                        current = gl.get_issue(item["iid"])
                        others = others_acted(gl, item["iid"], now, cfg.self_username)
                    except GitLabError as exc:
                        log(f"実行後の issue を取り直せませんでした（次の poll で拾い直します）: {exc}")
                with locked_state(cfg) as st:
                    st["inflight"] = None
                    if result.ok:
                        how = settle_success(st, item, current=current, others=others)
                        log(f"#{item['iid']} 完了（{'もう一度流します' if how == 'kept' else '既読にしました'}） "
                            f"ログ: {result.log_path}")
                    else:
                        reason = ("中止された" if _stop_requested else
                                  "タイムアウト" if result.timed_out else f"終了コード {result.returncode}")
                        how = settle_failure(st, item, cfg, time.time(), reason)
                        log(f"#{item['iid']} 失敗（{reason}）→ "
                            f"{ {'retry': '後で再試行', 'dead': '打ち切り', 'gone': '待ち行列から外れていた'}[how] } "
                            f"ログ: {result.log_path}")
                        rc = EXIT_AGENT_FAILED
                        break
            return rc
    except SessionBusy as exc:
        log(f"{exc} — 今回は見送ります")
        return EXIT_OK

# ---------------------------------------------------------------------------
# api-watch から受け取る（handle）
#
# poll と待ち行列の代わりに api-watch が issue を取得して変化を検知し、Dagu のキュー（同時実行 1）へ
# 1 件ずつ渡す構成で使う。ここで持つのは「最後に流した版」（seen）だけで、次を守る:
#   - エージェント自身の書き込みで起きた変化は流さない（実行後に取り直した版を既読にする）
#   - キューで待つ間に同じ issue の変化が重なったら、先に動いた実行が最新の issue でまとめて流し、
#     後ろのものは既読と比べて飛ばす
#   - 実行中に自分以外の人が動いたときは既読を実行前の版にとどめ、次の変化の受け取りでもう一度流す
# 再試行は Dagu の retry_policy に任せる（失敗は終了コード 1、取り直しの失敗は 3、別セッション実行中は 4）。
# ---------------------------------------------------------------------------

def load_event(path: str) -> dict[str, Any]:
    if not path:
        raise ConfigError("イベントのファイルがありません（--event-file か APIWATCH_EVENT_FILE）")
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"イベントのファイルが読めません: {path} ({exc})")
    record = event.get("record") if isinstance(event, dict) else None
    if not isinstance(record, dict) or record.get("iid") is None:
        raise ConfigError("イベントに issue のレコードがありません（api-watch のルールの include_record を "
                          "true のままにし、id を iid にしてください）")
    return event


def already_handled(st: dict[str, Any], key: str, version: str) -> bool:
    """この版（updated_at）までの変化を、もう流し終えているか。"""
    prev = st["seen"].get(key)
    if not prev or not version or not prev.get("version"):
        return False
    return _parse_ts(version) <= _parse_ts(str(prev["version"]))


def issue_before(before: Any, current: dict[str, Any]) -> dict[str, Any]:
    """api-watch のイベントの before（監視する項目だけ）を、今の issue に重ねて変化前の issue にする。
    監視していない項目は今と同じとみなす（変化の箇条書きに余計な行を出さない）。"""
    out = dict(current)
    if not isinstance(before, dict):
        return out
    for name in ("title", "state", "labels", "due_date", "user_notes_count", "description"):
        if name in before:
            out[name] = before[name]
    if "assignees[*].username" in before:
        out["assignees"] = [{"username": u} for u in (before["assignees[*].username"] or [])]
    elif "assignees" in before:
        out["assignees"] = before["assignees"]
    if "milestone.title" in before:
        out["milestone"] = {"title": before["milestone.title"]} if before["milestone.title"] else None
    elif "milestone" in before:
        out["milestone"] = before["milestone"]
    return out


def matches_filter(cfg: Config, issue: dict[str, Any]) -> bool:
    """ISSUE_LOOP_ISSUE_STATE / LABELS / ASSIGNEE の絞り込みに今も当てはまるか（キューで待つ間に外れたら流さない）。"""
    if cfg.issue_state and cfg.issue_state != "all" and issue.get("state") != cfg.issue_state:
        return False
    want = {x.strip() for x in cfg.issue_labels.split(",") if x.strip()}
    if want and not want <= set(issue.get("labels") or []):
        return False
    if cfg.issue_assignee:
        names = {a.get("username") for a in (issue.get("assignees") or []) if isinstance(a, dict)}
        if cfg.issue_assignee not in names:
            return False
    return True


def cmd_handle(cfg: Config, gl: GitLab, event_file: str, delivery_id: str) -> int:
    event = load_event(event_file)
    record = event["record"]
    key = str(record["iid"])
    template = load_template(cfg)
    with locked_state(cfg) as st:
        if already_handled(st, key, str(record.get("updated_at") or "")):
            log(f"#{key} のこの変化は流し終えています（エージェント自身の書き込みか、前の実行でまとめて扱った）")
            return EXIT_OK
    _install_signal_handlers()
    try:
        with single_session(cfg) as info_path:
            # キューで待つ間に issue がさらに動いているかもしれないので、最新を取り直して流す
            try:
                current = gl.get_issue(key)
            except GitLabError as exc:
                log(str(exc))
                return EXIT_GITLAB
            version = str(current.get("updated_at") or "")
            if not matches_filter(cfg, current):
                log(f"#{key} は対象から外れました（閉じた・ラベルが外れたなど）。流しません")
                return EXIT_OK
            with locked_state(cfg) as st:
                if already_handled(st, key, version):
                    log(f"#{key} の今の版は流し終えています")
                    return EXIT_OK
                prev = st["seen"].get(key)
                attempts = int(st["handle_attempts"].get(delivery_id) or 0) if delivery_id else 0
            snap = snapshot(current)
            if prev and isinstance(prev.get("snapshot"), dict):
                base = prev["snapshot"]                    # 前回流したときからの累積
            elif "appeared" in (event.get("kinds") or []) or event.get("before") is None:
                base = None
            else:
                base = snapshot(issue_before(event.get("before"), current))
            kind, lines = describe_changes(base, snap)
            item = {"iid": current.get("iid", record["iid"]), "version": version, "kind": kind,
                    "changes": lines, "snapshot": snap, "issue": current, "attempts": attempts}
            started = time.time()
            result = run_agent(cfg, render_prompt(template, cfg, item), item, info_path)
            if not result.ok:
                reason = ("中止された" if _stop_requested else
                          "タイムアウト" if result.timed_out else f"終了コード {result.returncode}")
                with locked_state(cfg) as st:
                    if delivery_id:
                        st["handle_attempts"][delivery_id] = attempts + 1
                        for old in list(st["handle_attempts"])[:-200]:
                            st["handle_attempts"].pop(old, None)
                log(f"#{key} 失敗（{reason}）。再試行は Dagu に任せます ログ: {result.log_path}")
                return EXIT_AGENT_FAILED
            after = None
            others = True
            if not cfg.self_username:
                with contextlib.suppress(GitLabError):
                    cfg.self_username = gl.current_username()
                if not cfg.self_username:
                    log("自分のユーザー名が分かりません。実行中の他人の動きは拾えません"
                        "（ISSUE_LOOP_SELF_USERNAME で指定できます）")
            try:
                after = gl.get_issue(key)
                others = others_acted(gl, key, started, cfg.self_username)
            except GitLabError as exc:
                log(f"実行後の issue を取り直せませんでした: {exc}")
            with locked_state(cfg) as st:
                if delivery_id:
                    st["handle_attempts"].pop(delivery_id, None)
                if after is not None and not others:
                    st["seen"][key] = {"version": str(after.get("updated_at") or ""), "snapshot": snapshot(after)}
                    log(f"#{key} 完了（既読にしました） ログ: {result.log_path}")
                else:
                    # 実行前の版までを既読にする。実行中の他人の動きは、次に受け取ったときにもう一度流す
                    st["seen"][key] = {"version": version, "snapshot": snap}
                    log(f"#{key} 完了（実行中に別の動きがあったので、次の受け取りでもう一度流します） "
                        f"ログ: {result.log_path}")
            return EXIT_OK
    except SessionBusy as exc:
        log(f"{exc} — 後でやり直します")
        return EXIT_BUSY


def cmd_status(cfg: Config) -> int:
    with locked_state(cfg) as st:
        view = {
            "project": cfg.project,
            "state_dir": str(cfg.project_dir),
            "baseline_done": st["baseline_done"],
            "last_poll_at": st["last_poll_at"],
            "inflight": st["inflight"],
            "queue": [{k: q.get(k) for k in ("iid", "version", "kind", "changes", "attempts",
                                              "next_attempt_at", "last_error")}
                      for q in st["queue"].values()],
            "dead": list(st["dead"].values()),
            "seen": len(st["seen"]),
        }
    info = cfg.session_lock.with_suffix(".json")
    with contextlib.suppress(OSError, json.JSONDecodeError):
        view["session"] = json.loads(info.read_text(encoding="utf-8"))
    print(json.dumps(view, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_forget(cfg: Config, iid: str) -> int:
    with locked_state(cfg) as st:
        hit = [name for name in ("dead", "seen", "queue") if st[name].pop(str(iid), None) is not None]
    log(f"#{iid} を {', '.join(hit) or '（どこにもありませんでした）'} から外しました")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--gitlab-url")
    common.add_argument("--gitlab-api-url")
    common.add_argument("--project", help="GitLab のプロジェクト（group/name か数値 ID）")
    common.add_argument("--token-env", help="トークンを読む環境変数名（既定 GITLAB_TOKEN / GL_TOKEN）")
    common.add_argument("--state-dir")
    common.add_argument("--lock-file", help="セッションの排他に使うファイル（既定は全プロジェクト共通の 1 つ）")
    common.add_argument("--issue-state")
    common.add_argument("--labels", default=None)
    common.add_argument("--assignee", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    poll = sub.add_parser("poll", parents=[common])
    poll.add_argument("--no-baseline", action="store_true", help="初回も今ある issue を全部積む")
    poll.add_argument("--jitter", help="問い合わせ前に 0〜この秒数だけランダムに待つ（既定 0。DAG は 900）")
    agent = argparse.ArgumentParser(add_help=False)
    agent.add_argument("--agent", help="agents/<name>.json の名前（例: claude, codex, kiro）")
    agent.add_argument("--agent-cmd", help="エージェント CLI のコマンド行（--agent より優先）")
    agent.add_argument("--prompt-via", choices=["stdin", "arg", "file"])
    agent.add_argument("--model")
    agent.add_argument("--cwd")
    agent.add_argument("--timeout")
    agent.add_argument("--prompt-template")
    run = sub.add_parser("run", parents=[common, agent])
    run.add_argument("--drain", help="1 回の run で続けて流す最大件数（既定 1）")
    handle = sub.add_parser("handle", parents=[common, agent],
                            help="api-watch が渡した変化を 1 件受け取ってエージェント CLI を実行する")
    handle.add_argument("--event-file", default=os.environ.get("APIWATCH_EVENT_FILE", ""),
                        help="api-watch のイベントの JSON（既定は環境変数 APIWATCH_EVENT_FILE）")
    handle.add_argument("--delivery-id", default=os.environ.get("APIWATCH_DELIVERY_ID", ""),
                        help="受け渡しの ID（再試行の回数を数える。既定は環境変数 APIWATCH_DELIVERY_ID）")
    sub.add_parser("status", parents=[common])
    forget = sub.add_parser("forget", parents=[common])
    forget.add_argument("iid")
    return p


def _utf8_stdio() -> None:
    """標準出力・標準エラーを UTF-8 に揃える。Windows はコンソールのコードページ（cp932 / cp1252）、
    POSIX でも UTF-8 でないロケールだと、日本語のログや status の出力で落ちたり化けたりする。
    Dagu が受け取るのはパイプなので UTF-8 にする（書けない文字は置き換えて止まらない）。"""
    for stream in (sys.stdout, sys.stderr):
        if str(getattr(stream, "encoding", "") or "").lower().replace("-", "") == "utf8":
            continue
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None, *, gitlab_factory: Callable[[Config], GitLab] | None = None) -> int:
    _utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        cfg = build_config(args, need_gitlab=args.cmd in ("poll", "run", "handle"))
        if args.cmd == "status":
            return cmd_status(cfg)
        if args.cmd == "forget":
            return cmd_forget(cfg, args.iid)
        gl = (gitlab_factory or (lambda c: GitLab(c.api_url, c.project, c.token)))(cfg)
        if args.cmd == "poll":
            return cmd_poll(cfg, gl)
        if args.cmd == "handle":
            return cmd_handle(cfg, gl, args.event_file, args.delivery_id)
        return cmd_run(cfg, gl)
    except ConfigError as exc:
        log(f"設定の誤り: {exc}")
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
