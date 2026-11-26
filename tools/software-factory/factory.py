from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import NamedTuple
import urllib.error
import urllib.parse
import urllib.request
import uuid


HERE = Path(__file__).resolve().parent
PROGRESS = (
    "factory:implementing", "factory:mr-open", "factory:in-review",
    "factory:reviewed", "factory:failed",
)


class ConfigError(Exception):
    pass


class GitLabError(Exception):
    pass


class GitError(Exception):
    pass


class GoalieError(Exception):
    pass


class GoalieUsageError(GoalieError):
    pass


class Config:
    def __init__(self, values: dict, fingerprint: str | None = None):
        if not isinstance(values, dict):
            raise ConfigError("設定は JSON オブジェクトで指定してください")
        repos = values.get("repositories")
        if not isinstance(repos, list) or not repos or any(
            not isinstance(repo, str)
            or "/" not in repo
            or any(segment in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", segment)
                   for segment in repo.split("/"))
            for repo in repos
        ):
            raise ConfigError("repositories は group/project 形式の空でない配列が必要です")
        names = [repo.rsplit("/", 1)[-1].lower() for repo in repos]
        if len(names) != len(set(names)):
            raise ConfigError("repositories のリポジトリ名が重複しています")
        for key in ("label", "work_root", "goalie_py", "journal"):
            if not isinstance(values.get(key), str) or not values[key].strip():
                raise ConfigError(f"{key} は空でない文字列が必要です")
        if values["label"].lower() in PROGRESS:
            raise ConfigError("label には進捗ラベルと異なる名前を指定してください")
        for key in ("poll_interval_sec", "job_timeout_sec"):
            number = values.get(key)
            if type(number) not in (int, float) or number <= 0 or (
                isinstance(number, float) and not math.isfinite(number)
            ):
                raise ConfigError(f"{key} は正の数が必要です")
        if type(values.get("max_concurrent")) is not int or values["max_concurrent"] < 1:
            raise ConfigError("max_concurrent は 1 以上の整数が必要です")
        for key in ("review", "fix"):
            section = values.get(key)
            if not isinstance(section, dict) or type(section.get("enabled")) is not bool:
                raise ConfigError(f"{key}.enabled は真偽値が必要です")
        work_dirs = values.get("work_dirs", {})
        if not isinstance(work_dirs, dict) or any(
            key not in repos or not isinstance(path, str) or not path.strip()
            for key, path in work_dirs.items()
        ):
            raise ConfigError("work_dirs は対象リポジトリから作業フォルダへの対応が必要です")
        gitlab_url = values.get("gitlab_url", os.environ.get("GITLAB_URL"))
        if not isinstance(gitlab_url, str) or not gitlab_url.strip():
            raise ConfigError(
                "gitlab_url が未設定です。factory.json の gitlab_url か環境変数 GITLAB_URL を設定してください"
            )
        gitlab_url = gitlab_url.strip()
        try:
            parsed_url = urllib.parse.urlparse(gitlab_url)
            valid_url = parsed_url.scheme in {"http", "https"} and bool(parsed_url.hostname)
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ConfigError("gitlab_url にはホストを含む HTTP または HTTPS の URL を指定してください")
        self.gitlab_url = gitlab_url
        self.repositories = repos
        self.label = values["label"]
        self.work_root = Path(values["work_root"])
        self.goalie_py = Path(values["goalie_py"])
        self.journal = Path(values["journal"])
        self.job_timeout_sec = values["job_timeout_sec"]
        self.max_concurrent = values["max_concurrent"]
        self.review_enabled = values["review"]["enabled"]
        self.work_dirs = work_dirs
        resolved_dirs = [os.path.normcase(self.work_dir(repo)) for repo in repos]
        if len(resolved_dirs) != len(set(resolved_dirs)):
            raise ConfigError("リポジトリの作業フォルダが重複しています")
        self.fingerprint = fingerprint or hashlib.sha256(
            json.dumps(values, sort_keys=True).encode()
        ).hexdigest()

    @classmethod
    def load(cls, path: Path) -> Config:
        try:
            raw = path.read_bytes()
            values = json.loads(raw)
        except (OSError, UnicodeError, ValueError) as error:
            raise ConfigError(f"設定を読み込めません: {path}") from error
        return cls(values, hashlib.sha256(raw).hexdigest())

    def work_dir(self, repo: str) -> Path:
        default = self.work_root / repo.rsplit("/", 1)[-1]
        return Path(self.work_dirs.get(repo, default)).resolve()


class Runner:
    def run(self, argv: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
        env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"}
        # トークンはコントローラー自身の API 呼び出し専用で、Copilot アプリ側には渡さない。
        env.pop("GITLAB_TOKEN", None)
        env.pop("GL_TOKEN", None)
        result = subprocess.run(
            argv, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace",
            env=env,
        )
        return result.returncode, result.stdout, result.stderr


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    def __init__(self, gitlab_url, token):
        self.base_url = gitlab_url.rstrip("/") + "/api/v4"
        self.token = token
        self.opener = urllib.request.build_opener(_NoRedirect())

    def request(self, method, path, params=None, json_body=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"PRIVATE-TOKEN": self.token, "Accept": "application/json"}
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            with self.opener.open(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            error.close()
            raise GitLabError(f"GitLab API が HTTP {error.code} を返しました") from None
        except (OSError, ValueError):
            raise GitLabError("GitLab API への接続または応答の解析に失敗しました") from None


class GitLabClient:
    def __init__(self, transport):
        self.transport = transport

    def _project_path(self, repo):
        return "/projects/" + urllib.parse.quote(repo, safe="")

    def _get_all(self, path, params=None):
        per_page = 100
        items = []
        for page in range(1, 51):
            batch = self.transport.request(
                "GET", path, params={**(params or {}), "per_page": per_page, "page": page},
            )
            items.extend(batch)
            if len(batch) < per_page:
                break
        return items

    def list_factory_issues(self, repo, label):
        issues = self._get_all(self._project_path(repo) + "/issues",
                               params={"labels": label, "state": "opened"})
        return [
            {"number": issue["iid"], "title": issue["title"], "body": issue["description"] or "",
             "labels": [{"name": name} for name in issue["labels"]], "updatedAt": issue["updated_at"]}
            for issue in issues
        ]

    def set_progress_label(self, repo, number, progress):
        if progress is not None and progress not in PROGRESS:
            raise ValueError("不明な進捗ラベル")
        path = f"{self._project_path(repo)}/issues/{number}"
        current = self.transport.request("GET", path)["labels"]
        labels = [label for label in current if label not in PROGRESS or label == progress]
        if progress is not None and progress not in labels:
            labels.append(progress)
        if labels != current:
            self.transport.request("PUT", path, json_body={"labels": ",".join(labels)})

    def comment_issue(self, repo, number, body):
        self.transport.request("POST", f"{self._project_path(repo)}/issues/{number}/notes",
                               json_body={"body": body})

    def comment_mr(self, repo, number, body):
        self.transport.request("POST", f"{self._project_path(repo)}/merge_requests/{number}/notes",
                               json_body={"body": body})

    def mr_comment_exists(self, repo, number, marker):
        notes = self._get_all(f"{self._project_path(repo)}/merge_requests/{number}/notes")
        return any(marker in (note.get("body") or "") for note in notes)

    def find_mr_by_head(self, repo, branch):
        mrs = self._get_all(self._project_path(repo) + "/merge_requests",
                            params={"source_branch": branch, "state": "opened"})
        mr = next((mr for mr in mrs if mr["state"] == "opened"), None)
        if mr is None:
            return None
        draft = mr.get("draft")
        if draft is None:
            draft = mr.get("work_in_progress")
        return {"number": mr["iid"], "url": mr["web_url"], "headRefOid": mr["sha"],
                "state": "OPEN", "isDraft": bool(draft)}

    def mr_head_sha(self, repo, number):
        return self.transport.request("GET", f"{self._project_path(repo)}/merge_requests/{number}")["sha"]

    def mr_checks_state(self, repo, number):
        mr = self.transport.request("GET", f"{self._project_path(repo)}/merge_requests/{number}")
        status = (mr.get("head_pipeline") or {}).get("status")
        # GitLab のマージ条件では skipped パイプラインはマージを妨げない。
        if status in {"success", "skipped"}:
            return "success"
        if status in {"failed", "canceled"}:
            return "failure"
        return "pending"

    def mr_diff(self, repo, number):
        mr = self.transport.request("GET", f"{self._project_path(repo)}/merge_requests/{number}/changes")
        if mr.get("overflow") or any(change.get("too_large") for change in mr["changes"]):
            raise GitLabError("差分が大きすぎて取得できません")
        return "".join(
            f'diff --git a/{change["old_path"]} b/{change["new_path"]}\n'
            + (change["diff"].rstrip("\n") + "\n" if change["diff"] else "")
            for change in mr["changes"]
        )

    def create_draft_mr(self, repo, head, title, body, label):
        if not title.startswith("Draft:"):
            title = f"Draft: {title}"
        mr = self.transport.request("POST", self._project_path(repo) + "/merge_requests",
                                    json_body={"source_branch": head, "target_branch": self.default_branch(repo),
                                               "title": title, "description": body, "labels": label})
        return mr["iid"]

    def default_branch(self, repo):
        return self.transport.request("GET", self._project_path(repo))["default_branch"]


class JobOutcome(NamedTuple):
    kind: str
    body: str = ""


class GoalieClient:
    def __init__(self, runner: Runner, goalie_py: Path):
        self.runner = runner
        self.goalie_py = goalie_py

    def submit(self, work_dir, prompt_file, name):
        try:
            code, stdout, _ = self.runner.run(
                [sys.executable, str(self.goalie_py), "job", "run", "--dir", str(work_dir),
                 "--prompt-file", str(prompt_file), "--name", name, "--no-wait"], None,
            )
        except OSError as error:
            raise GoalieError("goalie を起動できません") from error
        if code == 2:
            raise GoalieUsageError("goalie が終了コード 2 を返しました")
        if code:
            raise GoalieError(f"goalie が終了コード {code} を返しました")
        job_id = stdout.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
            raise GoalieError("goalie の出力から job_id を取得できません")
        return job_id

    def job_present(self, work_dir, job_id):
        box = work_dir / ".copilot-jobs"
        return any((box / state / f"{job_id}.md").is_file()
                   for state in ("pending", "running", "dispatched"))

    def find_job_by_name(self, work_dir, job_name):
        box = work_dir / ".copilot-jobs"
        for state in ("pending", "running", "dispatched", "results", "done"):
            for path in (box / state).glob("*"):
                if path.stem.endswith(f"-{job_name}") and path.is_file():
                    try:
                        return path.stem, path.stat().st_mtime
                    except FileNotFoundError:
                        continue
        return None

    def poll_job(self, work_dir, job_id, submitted_at, timeout_sec, now):
        box = work_dir / ".copilot-jobs"
        filename = f"{job_id}.md"
        result = box / "results" / filename
        pending = box / "pending" / filename
        withdrawn = box / "tmp" / f"{job_id}.withdrawn.md"
        timed_out = now - submitted_at >= timeout_sec

        def read_result():
            try:
                lines = result.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                return None
            header = lines[0].strip() if lines else ""
            if header in ("status: ok", "status: failed"):
                return JobOutcome(header.split()[1], "\n".join(lines[2:]))
            return JobOutcome("missing" if timed_out else "pending")

        outcome = read_result()
        if outcome is not None:
            return outcome
        if pending.is_file():
            if not timed_out:
                return JobOutcome("pending")
            withdrawn.parent.mkdir(parents=True, exist_ok=True)
            try:
                pending.replace(withdrawn)
            except FileNotFoundError:
                # アプリが先に依頼を取得した場合は残りの状態を照合する。
                pass
            else:
                return JobOutcome("not_picked")
        if any((box / state / filename).is_file() for state in ("running", "dispatched")):
            return read_result() or JobOutcome("not_finished" if timed_out else "pending")
        return read_result() or JobOutcome("not_picked" if withdrawn.is_file() else "missing")


class GitClient:
    def __init__(self, runner: Runner):
        self.runner = runner

    def _run(self, work_dir, *args, allowed=(0,)):
        try:
            code, stdout, _ = self.runner.run(["git", *args], work_dir)
        except OSError as error:
            raise GitError(f"git {args[0]} を起動できません") from error
        if code not in allowed:
            raise GitError(f"git {args[0]} が終了コード {code} を返しました")
        return code, stdout

    def fetch(self, work_dir):
        self._run(work_dir, "fetch", "origin")

    def branch_exists(self, work_dir, branch):
        return self._run(work_dir, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}",
                         allowed=(0, 1))[0] == 0

    def commits_ahead(self, work_dir, base, branch):
        return int(self._run(work_dir, "rev-list", "--count", f"{base}..{branch}")[1])

    def tracked_dirty(self, work_dir):
        return bool(self._run(work_dir, "status", "--porcelain", "--untracked-files=no")[1].strip())

    def push(self, work_dir, branch):
        self._run(work_dir, "push", "-u", "origin", branch)


def iso_time(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat()


def label_names(issue):
    return {label["name"] for label in issue["labels"]}


def issue_progress(issue):
    names = label_names(issue)
    return next((label for label in PROGRESS if label in names), None)


def issue_branch(number):
    return f"factory/issue-{number}"


def parse_verdict(body):
    for line in body.splitlines():
        if line.strip() == "FACTORY-VERDICT: major" or line.lstrip().startswith("- [重大]"):
            return "major"
    return "clear"


def format_review_comment(run_id, sha, body):
    verdict = "重大な指摘あり" if parse_verdict(body) == "major" else "重大な指摘なし"
    report = "\n".join(line for line in body.splitlines()
                       if not line.lstrip().startswith("FACTORY-VERDICT:")).strip()
    return (f"<!-- factory:review run={run_id} sha={sha} -->\n"
            "## ファクトリーレビュー結果\n\n"
            f"対象リビジョン: `{sha}`\n判定: {verdict}\n\n{report}\n")


class Journal:
    def __init__(self, path, clock):
        self.path = path
        self.clock = clock
        self.events = []

    def load(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            raw = b""
        self.events = [json.loads(line) for line in raw.splitlines() if line.strip()]

    def record(self, event, **fields):
        entry = dict(at=iso_time(self.clock()), repository=None, target_type=None, target=None,
                     transition="none", input_revision=None, run_id=None, job_name=None,
                     event=event, exit_code=None, reason=None, mr=None)
        entry.update(fields)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.events.append(entry)
        return entry

    def unresolved(self):
        runs = {}
        for event in self.events:
            run_id = event["run_id"]
            if event["event"] == "run_started":
                runs[run_id] = event.copy()
            elif event["event"] == "job_submitted" and run_id in runs:
                runs[run_id].update(job_id=event["job_id"], submitted_at=event["submitted_at"])
            elif event["event"] == "job_finished":
                runs.pop(run_id, None)
        return list(runs.values())

    def last_for_key(self, key, events=None):
        return next((entry for entry in reversed(self.events)
                     if entry.get("idempotency_key") == key
                     and (events is None or entry["event"] in events)), None)

    def last_sha(self, repo, issue):
        for entry in reversed(self.events):
            if entry["repository"] != repo or entry.get("issue", entry["target"]) != issue:
                continue
            if entry["event"] in ("mr_created", "review_posted", "ci_failed", "ci_passed"):
                return entry["input_revision"]
            if (entry["event"] == "job_finished" and entry["transition"] == "review"
                    and entry["reason"] not in ("stale_revision", "not_picked", "goalie_usage")):
                return entry["input_revision"]
        return None

    def held(self, fingerprint):
        latest = next((entry for entry in reversed(self.events)
                       if entry["event"] in ("hold_set", "hold_cleared")), None)
        return (latest is not None and latest["event"] == "hold_set"
                and (fingerprint is None or latest["config_fingerprint"] == fingerprint))


@contextmanager
def poll_lock(path, clock):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            stat = path.stat()
        except FileNotFoundError:
            stat = None
        if stat is not None and clock() - stat.st_mtime <= 3600:
            yield False
            return
        try:
            if stat is not None and path.stat() == stat:
                path.unlink()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileNotFoundError:
            with poll_lock(path, clock) as acquired:
                yield acquired
            return
        except FileExistsError:
            yield False
            return
    token = json.dumps({"pid": os.getpid(), "at": iso_time(clock()), "token": uuid.uuid4().hex})
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(token)
        yield True
    finally:
        try:
            if path.read_text(encoding="utf-8") == token:
                path.unlink()
        except FileNotFoundError:
            pass


class Controller:
    def __init__(self, config, gitlab, goalie, git, clock=time.time):
        self.config = config
        self.gitlab = gitlab
        self.goalie = goalie
        self.git = git
        self.clock = clock
        self.journal = Journal(config.journal, clock)

    def poll(self):
        self.config.work_root.mkdir(parents=True, exist_ok=True)
        with poll_lock(self.config.work_root / "factory.lock", self.clock) as acquired:
            if not acquired:
                print("他の poll が実行中です")
                return
            self.journal.path.parent.mkdir(parents=True, exist_ok=True)
            self.journal.load()
            self.journal.record("cycle_started")
            code = 1
            try:
                if self.journal.held(None) and not self.journal.held(self.config.fingerprint):
                    self.journal.record("hold_cleared", reason="config_changed")
                recovered = set()
                for run in self.journal.unresolved():
                    if self._recover(run):
                        recovered.add((run["repository"], run["issue"]))
                for repo in self.config.repositories:
                    for issue in self.gitlab.list_factory_issues(repo, self.config.label):
                        if (repo, issue["number"]) not in recovered:
                            self._advance(repo, issue)
                code = 0
            except Exception as error:
                # 外部コマンドの出力、プロンプト、差分などはジャーナルに含めない。
                reason = str(error) if isinstance(error, (GitLabError, GitError, GoalieError)) else type(error).__name__
                self.journal.record("error", exit_code=1, reason=reason)
                raise
            finally:
                self.journal.record("cycle_finished", exit_code=code)

    def _run_event(self, event, run, **extra):
        fields = {key: run[key] for key in (
            "repository", "target_type", "target", "issue", "transition", "input_revision",
            "run_id", "job_name", "mr", "idempotency_key", "work_dir",
        )}
        if "job_id" in run:
            fields["job_id"] = run["job_id"]
        fields.update(extra)
        self.journal.record(event, **fields)

    def _label(self, repo, issue, progress, run=None):
        self.gitlab.set_progress_label(repo, issue, progress)
        if run is None:
            self.journal.record("label_changed", repository=repo, target_type="issue",
                                target=issue, label=progress)
        else:
            self._run_event("label_changed", run, label=progress)

    def _fail(self, repo, issue, message, run):
        self._label(repo, issue, "factory:failed", run)
        self.gitlab.comment_issue(repo, issue, message)

    def _recover(self, run):
        repo, issue = run["repository"], run["issue"]
        review = run["transition"] == "review"
        mr = run["mr"]
        work_dir = Path(run["work_dir"])
        if "job_id" not in run:
            found = self.goalie.find_job_by_name(work_dir, run["job_name"])
            if found is None:
                reason = "interrupted_before_submit"
                self._fail(repo, issue, "ファクトリーの依頼投入が中断されました。受け箱を確認してください。", run)
                self._run_event("job_finished", run, exit_code=1, reason=reason)
                return True
            job_id, submitted_at = found
            run.update(job_id=job_id, submitted_at=submitted_at)
            self._run_event("job_submitted", run, submitted_at=submitted_at)

        outcome = self.goalie.poll_job(work_dir, run["job_id"],
                                       run["submitted_at"], self.config.job_timeout_sec, self.clock())
        kind = outcome.kind
        if kind == "pending":
            return False
        code = {"ok": 0, "failed": 1, "not_picked": 3, "not_finished": 4, "missing": None}[kind]
        reason = kind
        if kind == "ok":
            if review:
                saved = self._milestone(run, "review_posted")
                if saved:
                    verdict = saved["verdict"]
                else:
                    sha = run["input_revision"]
                    head = self.gitlab.mr_head_sha(repo, mr)
                    if head != sha:
                        self._label(repo, issue, "factory:mr-open", run)
                        self._run_event("job_finished", run, exit_code=code, reason="stale_revision")
                        return True
                    checks = self.gitlab.mr_checks_state(repo, mr)
                    if self.gitlab.mr_head_sha(repo, mr) != sha:
                        self._label(repo, issue, "factory:mr-open", run)
                        self._run_event("job_finished", run, exit_code=code, reason="stale_revision")
                        return True
                    if checks == "pending":
                        return False
                    if checks == "failure":
                        self._ci_failed(repo, issue, mr, sha)
                        self._run_event("job_finished", run, exit_code=code, reason="ci_failed")
                        return True
                    comment = format_review_comment(run["run_id"], sha, outcome.body)
                    marker = comment.split("\n", 1)[0]
                    if not self.gitlab.mr_comment_exists(repo, mr, marker):
                        self.gitlab.comment_mr(repo, mr, comment)
                    verdict = parse_verdict(outcome.body)
                    self._run_event("review_posted", run, verdict=verdict)
                self._label(repo, issue, "factory:failed" if verdict == "major" else "factory:reviewed", run)
            else:
                reason = self._apply_implementation(run)
        elif kind == "not_picked":
            self._label(repo, issue, "factory:mr-open" if review else None, run)
        elif kind == "failed":
            summary = outcome.body.strip()[:1000]
            message = "ファクトリーのレビューに失敗しました。" if review else "ファクトリーの実装に失敗しました。"
            self._fail(repo, issue, f"{message}\n\n{summary}".rstrip(), run)
        else:
            self._fail(repo, issue,
                       "依頼が残っている可能性があるため再投入しません。受け箱を確認してください。", run)
        self._run_event("job_finished", run, exit_code=code, reason=reason)
        return True

    def _milestone(self, run, event):
        return next((entry for entry in reversed(self.journal.events)
                     if entry["run_id"] == run["run_id"] and entry["event"] == event), None)

    def _apply_implementation(self, run):
        repo, issue = run["repository"], run["issue"]
        if not self._milestone(run, "mr_created"):
            work_dir = Path(run["work_dir"])
            self.git.fetch(work_dir)
            branch = issue_branch(issue)
            failure = None
            if not self.git.branch_exists(work_dir, branch):
                failure = f"ブランチ `{branch}` が見つかりません。"
            elif self.git.commits_ahead(work_dir, f"origin/{self.gitlab.default_branch(repo)}", branch) <= 0:
                failure = f"ブランチ `{branch}` に既定ブランチより先のコミットがありません。"
            elif self.git.tracked_dirty(work_dir):
                failure = "追跡対象のファイルに未コミットの変更があります。"
            if failure:
                self._fail(repo, issue, f"実装成果の検証に失敗しました。{failure}", run)
                return "invalid_implementation"
            existing = self.gitlab.find_mr_by_head(repo, branch)
            if existing:
                mr = existing["number"]
            else:
                self.git.push(work_dir, branch)
                mr = self.gitlab.create_draft_mr(
                    repo, branch, f"Factory: #{issue}",
                    f"Closes #{issue}\n\n<!-- factory:issue={issue} -->\n", self.config.label,
                )
            sha = self.gitlab.mr_head_sha(repo, mr)
            self._run_event("mr_created", run, input_revision=sha, mr=mr)
        self._label(repo, issue, "factory:mr-open", run)
        return "ok"

    def _ci_failed(self, repo, issue, mr, sha):
        key = [repo, mr, "ci", sha]
        if not self.journal.last_for_key(key, {"ci_failed"}):
            self.gitlab.comment_mr(repo, mr, f"コミット `{sha}` の CI チェックに失敗しました。")
            self.journal.record("ci_failed", repository=repo, target_type="mr", target=mr,
                                issue=issue, transition="ci", input_revision=sha, mr=mr,
                                idempotency_key=key)
        self._label(repo, issue, "factory:failed")

    def _remaining_jobs(self, repo=None, issue=None, work_dir=None):
        for entry in self.journal.events:
            if entry["event"] != "job_finished" or entry["reason"] not in ("not_finished", "missing"):
                continue
            if repo is not None and (entry["repository"], entry["issue"]) != (repo, issue):
                continue
            directory = Path(entry["work_dir"])
            if work_dir is not None and directory != work_dir:
                continue
            if self.goalie.job_present(directory, entry["job_id"]):
                return True
        return False

    def _advance(self, repo, issue):
        number = issue["number"]
        if self.config.label not in label_names(issue):
            return
        unresolved = self.journal.unresolved()
        if any((run["repository"], run["issue"]) == (repo, number) for run in unresolved):
            return
        progress = issue_progress(issue)
        work_dir = self.config.work_dir(repo)
        can_submit = (
            not self.journal.held(self.config.fingerprint)
            and len(unresolved) < self.config.max_concurrent
            and all(Path(run["work_dir"]) != work_dir for run in unresolved)
            and not self._remaining_jobs(work_dir=work_dir)
        )
        if progress in (None, "factory:implementing"):
            mr = self.gitlab.find_mr_by_head(repo, issue_branch(number))
            if mr:
                if not any(entry["event"] == "mr_created" and entry["repository"] == repo
                           and entry.get("issue", entry["target"]) == number
                           for entry in self.journal.events):
                    self.journal.record("mr_created", repository=repo, target_type="issue",
                                        target=number, issue=number, transition="implement",
                                        input_revision=mr["headRefOid"], mr=mr["number"])
                self._label(repo, number, "factory:mr-open")
            elif can_submit:
                self._start(repo, issue, "implement", issue["updatedAt"], work_dir)
            return
        if progress == "factory:in-review":
            self._label(repo, number, "factory:mr-open")
            return

        mr = self.gitlab.find_mr_by_head(repo, issue_branch(number))
        if not mr:
            if progress == "factory:mr-open":
                self.journal.record("mr_waiting", repository=repo, target_type="issue",
                                    target=number, reason="mr_not_found")
            return
        mr_number = mr["number"]
        if progress in ("factory:reviewed", "factory:failed"):
            previous = self.journal.last_sha(repo, number)
            sha = mr["headRefOid"]
            if sha != previous and not self._remaining_jobs(repo, number):
                self._label(repo, number, "factory:mr-open")
            elif sha == previous and progress == "factory:failed":
                latest = next((entry for entry in reversed(self.journal.events)
                               if entry["repository"] == repo and entry.get("issue") == number
                               and entry["input_revision"] == sha
                               and (entry["event"] in ("ci_failed", "review_posted")
                                    or (entry["event"] == "label_changed"
                                        and entry["transition"] == "review"
                                        and entry["label"] == "factory:failed"))), None)
                if (latest and latest["event"] == "ci_failed"
                        and self.gitlab.mr_checks_state(repo, mr_number) == "success"
                        and self.gitlab.mr_head_sha(repo, mr_number) == sha):
                    self.journal.record("ci_passed", repository=repo, target_type="mr",
                                        target=mr_number, issue=number, transition="ci",
                                        input_revision=sha, mr=mr_number)
                    self._label(repo, number, "factory:mr-open")
            return

        sha = self.gitlab.mr_head_sha(repo, mr_number)
        checks = self.gitlab.mr_checks_state(repo, mr_number)
        if checks == "pending" or self.gitlab.mr_head_sha(repo, mr_number) != sha:
            return
        if checks == "failure":
            self._ci_failed(repo, number, mr_number, sha)
        elif not self.config.review_enabled:
            self.journal.record("ci_passed", repository=repo, target_type="mr", target=mr_number,
                                issue=number, transition="ci", input_revision=sha, mr=mr_number)
            self._label(repo, number, "factory:reviewed")
        elif can_submit:
            self._start(repo, issue, "review", sha, work_dir, mr_number)

    def _start(self, repo, issue, transition, revision, work_dir, mr=None):
        number = issue["number"]
        review = transition == "review"
        target = mr if review else number
        key = [repo, target, transition, revision]
        success = self.journal.last_for_key(key, {"review_posted" if review else "mr_created"})
        if success:
            progress = "factory:mr-open"
            if review:
                progress = "factory:failed" if success["verdict"] == "major" else "factory:reviewed"
            self._label(repo, number, progress)
            return
        run_id = uuid.uuid4().hex[:12]
        requests = work_dir / ".factory" / "requests"
        requests.mkdir(parents=True, exist_ok=True)
        variables = {"REPO": repo}
        if review:
            diff = requests / f"{run_id}-diff.patch"
            diff.write_text(self.gitlab.mr_diff(repo, mr), encoding="utf-8")
            if self.gitlab.mr_head_sha(repo, mr) != revision:
                diff.unlink()
                return
            variables.update(MR_NUMBER=str(mr), HEAD_SHA=revision,
                             DIFF_FILE=diff.relative_to(work_dir).as_posix())
            name = f"factory-review-{mr}"
            progress = "factory:in-review"
        else:
            variables.update(ISSUE_NUMBER=str(number), ISSUE_TITLE=issue.get("title") or "",
                             ISSUE_BODY=issue.get("body") or "", BRANCH=issue_branch(number))
            name = f"factory-impl-{number}"
            progress = "factory:implementing"
        template = (HERE / "tasks" / f"{transition}.md").read_text(encoding="utf-8")
        prompt = requests / f"{run_id}.md"
        prompt.write_text(re.sub(r"\{\{([A-Z_]+)\}\}", lambda match: variables[match[1]], template),
                          encoding="utf-8")
        run = dict(repository=repo, target_type="mr" if review else "issue", target=target,
                   issue=number, transition=transition, input_revision=revision, run_id=run_id,
                   job_name=name, mr=mr, idempotency_key=key, work_dir=str(work_dir))
        self._label(repo, number, progress, run)
        self._run_event("run_started", run)
        submitted_at = self.clock()
        try:
            job_id = self.goalie.submit(work_dir, prompt, name)
        except GoalieUsageError:
            self._run_event("hold_set", run, exit_code=2, reason="goalie_usage",
                            config_fingerprint=self.config.fingerprint)
            self._run_event("job_finished", run, exit_code=2, reason="goalie_usage")
            self.gitlab.comment_issue(repo, number, "goalie が終了コード 2 を返しました。設定が変わるまで新規依頼を保留します。")
        else:
            self._run_event("job_submitted", run, job_id=job_id, submitted_at=submitted_at)

    def status(self):
        self.journal.load()
        held = self.journal.held(self.config.fingerprint)
        print(f"保留: {'あり' if held else 'なし'}")
        for repo in self.config.repositories:
            print(repo)
            for issue in self.gitlab.list_factory_issues(repo, self.config.label):
                if self.config.label not in label_names(issue):
                    continue
                number = issue["number"]
                print(f"  #{number} {issue['title']} [{', '.join(sorted(label_names(issue)))}]")
                mr = self.gitlab.find_mr_by_head(repo, issue_branch(number))
                if mr:
                    mr_number = mr["number"]
                    checks = self.gitlab.mr_checks_state(repo, mr_number)
                    draft = "draft, " if mr["isDraft"] else ""
                    print(f"    MR !{mr_number} ({draft}{mr['state']}) {mr['headRefOid']} / CI {checks}")
                else:
                    print("    MR なし")
        unresolved = self.journal.unresolved()
        print(f"未解決の実行: {len(unresolved)}")
        for run in unresolved:
            print(f"  {run['repository']} #{run['issue']} {run['transition']} "
                  f"run={run['run_id']} job={run.get('job_id', '未記録')}")
        print("直近のジャーナル:")
        for entry in self.journal.events[-10:]:
            details = " ".join(str(value) for value in (
                entry["repository"], entry["target"], entry["run_id"], entry["reason"],
            ) if value is not None)
            print(f"  {entry['at']} {entry['event']} {details}".rstrip())


def main(argv=None):
    parser = argparse.ArgumentParser(description="ソフトウェアファクトリーのコントローラー")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="設定の雛形を書く")
    init.add_argument("--force", action="store_true")
    poll = commands.add_parser("poll", aliases=["run-once"], help="1 サイクル実行する")
    status = commands.add_parser("status", help="現在の状態を表示する")
    for command in (init, poll, status):
        command.add_argument("--config", type=Path, default=HERE / "factory.json")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            template = (HERE / "factory.example.json").read_bytes()
            try:
                with args.config.open("wb" if args.force else "xb") as stream:
                    stream.write(template)
            except FileExistsError as error:
                raise ConfigError(f"設定ファイルが既に存在します: {args.config}") from error
        else:
            config = Config.load(args.config)
            token = os.environ.get("GITLAB_TOKEN") or os.environ.get("GL_TOKEN")
            if not token:
                raise ConfigError("環境変数 GITLAB_TOKEN または GL_TOKEN を設定してください")
            runner = Runner()
            gitlab = GitLabClient(UrllibTransport(config.gitlab_url, token))
            controller = Controller(config, gitlab, GoalieClient(runner, config.goalie_py),
                                    GitClient(runner))
            if args.command == "status":
                controller.status()
            else:
                controller.poll()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    except (GitLabError, GitError, GoalieError) as error:
        print(error, file=sys.stderr)
        return 1
    except Exception as error:
        print(f"ファクトリーの実行に失敗しました ({type(error).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
