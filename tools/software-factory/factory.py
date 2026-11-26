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
import tempfile
import time
from typing import NamedTuple
import uuid


HERE = Path(__file__).resolve().parent
PROGRESS = (
    "factory:implementing", "factory:pr-open", "factory:in-review",
    "factory:reviewed", "factory:failed",
)


class ConfigError(Exception):
    pass


class GhError(Exception):
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
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
            or any(part in (".", "..") for part in repo.split("/"))
            for repo in repos
        ):
            raise ConfigError("repositories は owner/repo 形式の空でない配列が必要です")
        names = [repo.split("/")[1].lower() for repo in repos]
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
        self.repositories = repos
        self.label = values["label"]
        self.work_root = Path(values["work_root"])
        self.goalie_py = Path(values["goalie_py"])
        self.journal = Path(values["journal"])
        self.job_timeout_sec = values["job_timeout_sec"]
        self.max_concurrent = values["max_concurrent"]
        self.review_enabled = values["review"]["enabled"]
        self.work_dirs = work_dirs
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
        default = self.work_root / repo.split("/")[1]
        return Path(self.work_dirs.get(repo, default)).resolve()


class Runner:
    def run(self, argv: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
        result = subprocess.run(
            argv, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace",
            env={**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"},
        )
        return result.returncode, result.stdout, result.stderr


class GhClient:
    def __init__(self, runner: Runner):
        self.runner = runner

    def _run(self, *args: str) -> str:
        try:
            code, stdout, _ = self.runner.run(["gh", *args], None)
        except OSError as error:
            raise GhError(f"gh {args[0]} {args[1]} を起動できません") from error
        if code:
            raise GhError(f"gh {args[0]} {args[1]} が終了コード {code} を返しました")
        return stdout

    def _json(self, *args: str):
        try:
            return json.loads(self._run(*args))
        except ValueError as error:
            raise GhError(f"gh {args[0]} {args[1]} の JSON を解析できません") from error

    def list_factory_issues(self, repo, label):
        return self._json("issue", "list", "--repo", repo, "--label", label, "--state",
                          "open", "--json", "number,title,body,labels,updatedAt", "--limit", "100")

    def set_progress_label(self, repo, number, progress):
        if progress is not None and progress not in PROGRESS:
            raise ValueError("不明な進捗ラベル")
        current = self._json("issue", "view", str(number), "--repo", repo, "--json", "labels")
        old = [name for name in label_names(current) if name in PROGRESS and name != progress]
        args = ["issue", "edit", str(number), "--repo", repo]
        if old:
            args.extend(("--remove-label", ",".join(sorted(old))))
        if progress and progress not in label_names(current):
            args.extend(("--add-label", progress))
        if len(args) > 5:
            self._run(*args)

    def _with_body(self, args, body):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "body.md"
            path.write_text(body, encoding="utf-8")
            return self._run(*args, "--body-file", str(path))

    def comment_issue(self, repo, number, body):
        self._with_body(["issue", "comment", str(number), "--repo", repo], body)

    def comment_pr(self, repo, number, body):
        self._with_body(["pr", "comment", str(number), "--repo", repo], body)

    def find_pr_by_head(self, repo, branch):
        prs = self._json("pr", "list", "--repo", repo, "--head", branch,
                         "--json", "number,url,headRefOid,state,isDraft")
        return prs[0] if prs else None

    def pr_head_sha(self, repo, number):
        return self._json("pr", "view", str(number), "--repo", repo,
                          "--json", "headRefOid")["headRefOid"]

    def pr_checks_state(self, repo, number):
        checks = self._json("pr", "view", str(number), "--repo", repo,
                            "--json", "statusCheckRollup")["statusCheckRollup"]
        pending = not checks
        for check in checks or []:
            if check.get("__typename") == "CheckRun" or "status" in check:
                if check.get("status") != "COMPLETED":
                    pending = True
                elif check.get("conclusion") in {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
                    return "failure"
            else:
                state = check.get("state")
                if state in {"FAILURE", "ERROR"}:
                    return "failure"
                if state != "SUCCESS":
                    pending = True
        return "pending" if pending else "success"

    def pr_diff(self, repo, number):
        return self._run("pr", "diff", str(number), "--repo", repo)

    def create_draft_pr(self, repo, head, title, body, label):
        url = self._with_body(["pr", "create", "--repo", repo, "--draft", "--head", head,
                               "--title", title, "--label", label], body)
        match = re.search(r"/pull/(\d+)/?\s*$", url)
        if not match:
            raise GhError("作成した PR の URL から番号を取得できません")
        return int(match[1])

    def default_branch(self, repo):
        return self._json("repo", "view", repo, "--json", "defaultBranchRef")["defaultBranchRef"]["name"]


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
            return JobOutcome("pending")

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
    return "major" if any(line.strip() == "FACTORY-VERDICT: major"
                          for line in body.splitlines()) else "clear"


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
                     event=event, exit_code=None, reason=None, pr=None)
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
            if entry["event"] in ("pr_created", "review_posted", "ci_failed", "ci_passed"):
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
    def __init__(self, config, gh, goalie, git, clock=time.time):
        self.config = config
        self.gh = gh
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
                    for issue in self.gh.list_factory_issues(repo, self.config.label):
                        if (repo, issue["number"]) not in recovered:
                            self._advance(repo, issue)
                code = 0
            except Exception as error:
                # 外部コマンドの出力、プロンプト、差分などはジャーナルに含めない。
                reason = str(error) if isinstance(error, (GhError, GitError, GoalieError)) else type(error).__name__
                self.journal.record("error", exit_code=1, reason=reason)
                raise
            finally:
                self.journal.record("cycle_finished", exit_code=code)

    def _run_event(self, event, run, **extra):
        fields = {key: run[key] for key in (
            "repository", "target_type", "target", "issue", "transition", "input_revision",
            "run_id", "job_name", "pr", "idempotency_key", "work_dir",
        )}
        if "job_id" in run:
            fields["job_id"] = run["job_id"]
        fields.update(extra)
        self.journal.record(event, **fields)

    def _label(self, repo, issue, progress, run=None):
        self.gh.set_progress_label(repo, issue, progress)
        if run is None:
            self.journal.record("label_changed", repository=repo, target_type="issue",
                                target=issue, label=progress)
        else:
            self._run_event("label_changed", run, label=progress)

    def _fail(self, repo, issue, message, run):
        self._label(repo, issue, "factory:failed", run)
        self.gh.comment_issue(repo, issue, message)

    def _recover(self, run):
        repo, issue = run["repository"], run["issue"]
        review = run["transition"] == "review"
        pr = run["pr"]
        if "job_id" not in run:
            reason = "interrupted_before_submit"
            self._fail(repo, issue, "ファクトリーの依頼投入が中断されました。受け箱を確認してください。", run)
            self._run_event("job_finished", run, exit_code=1, reason=reason)
            return True

        outcome = self.goalie.poll_job(Path(run["work_dir"]), run["job_id"],
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
                    head = self.gh.pr_head_sha(repo, pr)
                    if head != sha:
                        self._label(repo, issue, "factory:pr-open", run)
                        self._run_event("job_finished", run, exit_code=code, reason="stale_revision")
                        return True
                    checks = self.gh.pr_checks_state(repo, pr)
                    if self.gh.pr_head_sha(repo, pr) != sha:
                        self._label(repo, issue, "factory:pr-open", run)
                        self._run_event("job_finished", run, exit_code=code, reason="stale_revision")
                        return True
                    if checks == "pending":
                        return False
                    if checks == "failure":
                        self._ci_failed(repo, issue, pr, sha)
                        self._run_event("job_finished", run, exit_code=code, reason="ci_failed")
                        return True
                    self.gh.comment_pr(repo, pr, format_review_comment(run["run_id"], sha, outcome.body))
                    verdict = parse_verdict(outcome.body)
                    self._run_event("review_posted", run, verdict=verdict)
                self._label(repo, issue, "factory:failed" if verdict == "major" else "factory:reviewed", run)
            else:
                reason = self._apply_implementation(run)
        elif kind == "not_picked":
            self._label(repo, issue, "factory:pr-open" if review else None, run)
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
        if not self._milestone(run, "pr_created"):
            work_dir = Path(run["work_dir"])
            branch = issue_branch(issue)
            failure = None
            if not self.git.branch_exists(work_dir, branch):
                failure = f"ブランチ `{branch}` が見つかりません。"
            elif self.git.commits_ahead(work_dir, self.gh.default_branch(repo), branch) <= 0:
                failure = f"ブランチ `{branch}` に既定ブランチより先のコミットがありません。"
            elif self.git.tracked_dirty(work_dir):
                failure = "追跡対象のファイルに未コミットの変更があります。"
            if failure:
                self._fail(repo, issue, f"実装成果の検証に失敗しました。{failure}", run)
                return "invalid_implementation"
            existing = self.gh.find_pr_by_head(repo, branch)
            if existing:
                pr = existing["number"]
            else:
                self.git.push(work_dir, branch)
                pr = self.gh.create_draft_pr(
                    repo, branch, f"Factory: #{issue}",
                    f"Closes #{issue}\n\n<!-- factory:issue={issue} -->\n", self.config.label,
                )
            sha = self.gh.pr_head_sha(repo, pr)
            self._run_event("pr_created", run, input_revision=sha, pr=pr)
        self._label(repo, issue, "factory:pr-open", run)
        return "ok"

    def _ci_failed(self, repo, issue, pr, sha):
        key = [repo, pr, "ci", sha]
        if not self.journal.last_for_key(key, {"ci_failed"}):
            self.gh.comment_pr(repo, pr, f"コミット `{sha}` の CI チェックに失敗しました。")
            self.journal.record("ci_failed", repository=repo, target_type="pr", target=pr,
                                issue=issue, transition="ci", input_revision=sha, pr=pr,
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
            if can_submit:
                self._start(repo, issue, "implement", issue["updatedAt"], work_dir)
            return
        if progress == "factory:in-review":
            self._label(repo, number, "factory:pr-open")
            return

        pr = self.gh.find_pr_by_head(repo, issue_branch(number))
        if not pr:
            if progress == "factory:pr-open":
                self.journal.record("pr_waiting", repository=repo, target_type="issue",
                                    target=number, reason="pr_not_found")
            return
        pr_number = pr["number"]
        if progress in ("factory:reviewed", "factory:failed"):
            previous = self.journal.last_sha(repo, number)
            if pr["headRefOid"] != previous and not self._remaining_jobs(repo, number):
                self._label(repo, number, "factory:pr-open")
            return

        sha = self.gh.pr_head_sha(repo, pr_number)
        checks = self.gh.pr_checks_state(repo, pr_number)
        if checks == "pending" or self.gh.pr_head_sha(repo, pr_number) != sha:
            return
        if checks == "failure":
            self._ci_failed(repo, number, pr_number, sha)
        elif not self.config.review_enabled:
            self.journal.record("ci_passed", repository=repo, target_type="pr", target=pr_number,
                                issue=number, transition="ci", input_revision=sha, pr=pr_number)
            self._label(repo, number, "factory:reviewed")
        elif can_submit:
            self._start(repo, issue, "review", sha, work_dir, pr_number)

    def _start(self, repo, issue, transition, revision, work_dir, pr=None):
        number = issue["number"]
        review = transition == "review"
        target = pr if review else number
        key = [repo, target, transition, revision]
        success = self.journal.last_for_key(key, {"review_posted" if review else "pr_created"})
        if success:
            progress = "factory:pr-open"
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
            diff.write_text(self.gh.pr_diff(repo, pr), encoding="utf-8")
            if self.gh.pr_head_sha(repo, pr) != revision:
                diff.unlink()
                return
            variables.update(PR_NUMBER=str(pr), HEAD_SHA=revision,
                             DIFF_FILE=diff.relative_to(work_dir).as_posix())
            name = f"factory-review-{pr}"
            progress = "factory:in-review"
        else:
            variables.update(ISSUE_NUMBER=str(number), ISSUE_TITLE=issue["title"],
                             ISSUE_BODY=issue["body"], BRANCH=issue_branch(number))
            name = f"factory-impl-{number}"
            progress = "factory:implementing"
        template = (HERE / "tasks" / f"{transition}.md").read_text(encoding="utf-8")
        prompt = requests / f"{run_id}.md"
        prompt.write_text(re.sub(r"\{\{([A-Z_]+)\}\}", lambda match: variables[match[1]], template),
                          encoding="utf-8")
        run = dict(repository=repo, target_type="pr" if review else "issue", target=target,
                   issue=number, transition=transition, input_revision=revision, run_id=run_id,
                   job_name=name, pr=pr, idempotency_key=key, work_dir=str(work_dir))
        self._label(repo, number, progress, run)
        self._run_event("run_started", run)
        submitted_at = self.clock()
        try:
            job_id = self.goalie.submit(work_dir, prompt, name)
        except GoalieUsageError:
            self._run_event("hold_set", run, exit_code=2, reason="goalie_usage",
                            config_fingerprint=self.config.fingerprint)
            self._run_event("job_finished", run, exit_code=2, reason="goalie_usage")
            self.gh.comment_issue(repo, number, "goalie が終了コード 2 を返しました。設定が変わるまで新規依頼を保留します。")
        else:
            self._run_event("job_submitted", run, job_id=job_id, submitted_at=submitted_at)

    def status(self):
        self.journal.load()
        held = self.journal.held(self.config.fingerprint)
        print(f"保留: {'あり' if held else 'なし'}")
        for repo in self.config.repositories:
            print(repo)
            for issue in self.gh.list_factory_issues(repo, self.config.label):
                if self.config.label not in label_names(issue):
                    continue
                number = issue["number"]
                print(f"  #{number} {issue['title']} [{', '.join(sorted(label_names(issue)))}]")
                pr = self.gh.find_pr_by_head(repo, issue_branch(number))
                if pr:
                    pr_number = pr["number"]
                    checks = self.gh.pr_checks_state(repo, pr_number)
                    draft = "draft, " if pr["isDraft"] else ""
                    print(f"    PR #{pr_number} ({draft}{pr['state']}) {pr['headRefOid']} / CI {checks}")
                else:
                    print("    PR なし")
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
            runner = Runner()
            controller = Controller(config, GhClient(runner), GoalieClient(runner, config.goalie_py),
                                    GitClient(runner))
            if args.command == "status":
                controller.status()
            else:
                controller.poll()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    except (GhError, GitError, GoalieError) as error:
        print(error, file=sys.stderr)
        return 1
    except Exception as error:
        print(f"ファクトリーの実行に失敗しました ({type(error).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
