#!/usr/bin/env python3
"""dagu-issue-loop — api-watch が拾った GitLab の issue の変化を受け取り、エージェント CLI を 1 セッションずつ流す。

api-watch の受け渡し先（targets[].command）として、変化した issue 1 件ごとに呼ばれる。標準ライブラリだけで動く 1 ファイルのスクリプト。

    issue_loop.py handle       # 変化を 1 件受け取り、エージェント CLI を実行する
    issue_loop.py status       # 実行中のセッションを JSON で出す

issue の取得・変化の判定・実行待ちの管理・再試行・実行の前後の取り直し（流し終えた状態の記録と、自分の書き込みの吸収）は
api-watch が受け持つ（受け渡し先の command と refresh）。ここで持つのは、プロンプトの組み立て・1 セッションの排他・
エージェント CLI の起動と停止・実行中に自分以外が動いたかの確認だけ。設計は docs/designs/dagu-issue-loop-design.md。

終了コード:
    0  流して成功した
    1  エージェント CLI が失敗した / タイムアウトした / 止められた
    2  設定の誤り
    4  別のセッションが実行中だった（api-watch があとで再試行する）
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
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
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

HTTP_TIMEOUT_SEC = 20
MAX_BODY_BYTES = 10 * 1024 * 1024
DEFAULT_TOKEN_ENVS = ("GITLAB_TOKEN", "GL_TOKEN")

EXIT_OK = 0
EXIT_AGENT_FAILED = 1
EXIT_CONFIG = 2
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
    state_dir: Path = field(default_factory=lambda: Path.home() / ".agents" / "dagu-issue-loop")
    lock_file: Path | None = None
    agent: str = ""
    agent_cmd: str = ""
    agent_prompt_via: str = "stdin"
    agent_model: str = ""
    agent_cwd: str = ""
    agent_timeout: int = 3600
    prompt_template: str = ""
    self_username: str = ""

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
        self_username=_env("SELF_USERNAME"),
    )
    if cfg.agent_prompt_via not in ("stdin", "arg", "file"):
        raise ConfigError("ISSUE_LOOP_AGENT_PROMPT_VIA は stdin / arg / file のどれか")
    return cfg


# ---------------------------------------------------------------------------
# GitLab REST（必要な 2 本だけ: notes・自分。issue の取得・取り直しは api-watch）
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
# 1 セッションの排他
# ---------------------------------------------------------------------------

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
    # Dagu の中止・タイムアウトは POSIX では SIGTERM で来る（api-watch の work も同じシグナルを伝える）。子を止めて抜ける。
    # Windows の Dagu は Job Object ごと強制終了するのでここには来ない（api-watch の受け渡しの期限が切れたあと、同じ ID で流し直す）
    def handler(signum, _frame):
        _stop_requested.append(signum)
    for sig in stop_signals():
        with contextlib.suppress(OSError, ValueError):
            signal.signal(sig, handler)


# ---------------------------------------------------------------------------
# 実行中に自分以外が動いたか
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


# ---------------------------------------------------------------------------
# api-watch から受け取る（handle）
#
# api-watch が issue を取得して変化を検知し、受け渡し先の command として変化 1 件ごとにこれを呼ぶ。受け渡し先の refresh で、
# api-watch は実行の直前に issue を取り直し（絞り込みから外れた・処理し終えた状態と同じなら呼ばない）、実行のあとにも取り直して
# その状態を「処理し終えた状態」にする（エージェント自身の書き込みでもう一度起きない）。ここでは:
#   - 前回処理し終えた状態（refreshed.last_handled）から今までの差分でプロンプトを組み、エージェント CLI を 1 セッションで流す
#   - 実行中に自分以外の人が動いていたら、結果ファイルに {"handled": "before"} を書き、実行前の状態までを処理済みにさせる
#     （ほかの人の動きは次の取得で変化として出て、もう一度流れる）
# 再試行は api-watch に任せる（失敗は終了コード 1、別セッション実行中は 4）。
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


def issue_before(before: Any, current: dict[str, Any]) -> dict[str, Any]:
    """api-watch の監視する値（watch の項目だけ）を、今の issue に重ねて変化前の issue にする。
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


def write_result(handled: str) -> None:
    """api-watch に、どこまでを処理し終えた状態にするかを伝える（APIWATCH_RESULT_FILE）。"""
    path = os.environ.get("APIWATCH_RESULT_FILE")
    if path:
        Path(path).write_text(json.dumps({"handled": handled}), encoding="utf-8")


def cmd_handle(cfg: Config, gl: GitLab, event_file: str) -> int:
    event = load_event(event_file)
    issue = event["record"]
    key = str(issue["iid"])
    refreshed = event.get("refreshed") if isinstance(event.get("refreshed"), dict) else None
    if refreshed is None:
        log("api-watch の受け渡し先に refresh がありません。実行の直前の取り直しと、自分の書き込みの吸収が効きません"
            "（api-watch.yaml.example の refresh を書いてください）")
    snap = snapshot(issue)
    last = (refreshed or {}).get("last_handled")
    if last is not None:
        base = snapshot(issue_before(last, issue))           # 前回処理し終えたときからの累積
    elif "appeared" in (event.get("kinds") or []) or event.get("before") is None:
        base = None
    else:
        base = snapshot(issue_before(event.get("before"), issue))
    kind, lines = describe_changes(base, snap)
    attempt = _int(os.environ.get("APIWATCH_ATTEMPT") or "1", "APIWATCH_ATTEMPT")
    item = {"iid": issue["iid"], "version": str(issue.get("updated_at") or ""), "kind": kind,
            "changes": lines, "snapshot": snap, "issue": issue, "attempts": max(0, attempt - 1)}
    template = load_template(cfg)
    _install_signal_handlers()
    try:
        with single_session(cfg) as info_path:
            started = time.time()
            result = run_agent(cfg, render_prompt(template, cfg, item), item, info_path)
            if not result.ok:
                reason = ("中止された" if _stop_requested else
                          "タイムアウト" if result.timed_out else f"終了コード {result.returncode}")
                log(f"#{key} 失敗（{reason}）。再試行は api-watch に任せます ログ: {result.log_path}")
                return EXIT_AGENT_FAILED
            if not cfg.self_username:
                with contextlib.suppress(GitLabError):
                    cfg.self_username = gl.current_username()
                if not cfg.self_username:
                    log("自分のユーザー名が分かりません。実行中の他人の動きは拾えません"
                        "（ISSUE_LOOP_SELF_USERNAME で指定できます）")
            try:
                others = others_acted(gl, key, started, cfg.self_username)
            except GitLabError as exc:
                log(f"実行中のほかの人の動きを確かめられませんでした（実行前の状態までを処理済みにします）: {exc}")
                others = True
            if others:
                # 実行前の状態までを処理済みにする。実行中の他人の動きは、次の取得で変化として出てもう一度流れる
                write_result("before")
                log(f"#{key} 完了（実行中に別の動きがあったので、次の取得でもう一度流します） ログ: {result.log_path}")
            else:
                log(f"#{key} 完了 ログ: {result.log_path}")
            return EXIT_OK
    except SessionBusy as exc:
        log(f"{exc} — 後でやり直します")
        return EXIT_BUSY


def cmd_status(cfg: Config) -> int:
    view: dict[str, Any] = {"project": cfg.project, "state_dir": str(cfg.project_dir)}
    info = cfg.session_lock.with_suffix(".json")
    with contextlib.suppress(OSError, json.JSONDecodeError):
        view["session"] = json.loads(info.read_text(encoding="utf-8"))
    print(json.dumps(view, ensure_ascii=False, indent=2))
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
    sub = p.add_subparsers(dest="cmd", required=True)
    agent = argparse.ArgumentParser(add_help=False)
    agent.add_argument("--agent", help="agents/<name>.json の名前（例: claude, codex, kiro）")
    agent.add_argument("--agent-cmd", help="エージェント CLI のコマンド行（--agent より優先）")
    agent.add_argument("--prompt-via", choices=["stdin", "arg", "file"])
    agent.add_argument("--model")
    agent.add_argument("--cwd")
    agent.add_argument("--timeout")
    agent.add_argument("--prompt-template")
    handle = sub.add_parser("handle", parents=[common, agent],
                            help="api-watch が渡した変化を 1 件受け取ってエージェント CLI を実行する")
    handle.add_argument("--event-file", default=os.environ.get("APIWATCH_EVENT_FILE", ""),
                        help="api-watch のイベントの JSON（既定は環境変数 APIWATCH_EVENT_FILE）")
    sub.add_parser("status", parents=[common], help="実行中のセッション")
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
        cfg = build_config(args, need_gitlab=args.cmd == "handle")
        if args.cmd == "status":
            return cmd_status(cfg)
        gl = (gitlab_factory or (lambda c: GitLab(c.api_url, c.project, c.token)))(cfg)
        return cmd_handle(cfg, gl, args.event_file)
    except ConfigError as exc:
        log(f"設定の誤り: {exc}")
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
