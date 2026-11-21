"""Dagu の実行履歴から新しい失敗を集め、GitLab ハブ issue に報告する。"""

import argparse
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ALL = "__all__"
FAILURE_STATUSES = {"failed", "error", "partially_succeeded", "partial_success"}
DEFAULT_STATE = "~/.local/state/dagu-failure-digest/state.json"


class HistoryError(Exception):
    """履歴を取得または解釈できなかった。"""


@dataclass(frozen=True)
class Run:
    dag: str
    run_id: str
    status: str
    started: datetime | None
    finished: datetime | None
    timestamp: datetime
    failed_steps: list[str]

    @property
    def is_failure(self):
        """実行の失敗と、成功扱いの実行に含まれるステップ失敗を判定する。"""
        return self.status in FAILURE_STATUSES or (
            self.status == "succeeded" and bool(self.failed_steps)
        )


def valid_text(value):
    return isinstance(value, str) and bool(value.strip()) and "\0" not in value


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("設定は JSON オブジェクトで指定してください")
    unknown = config.keys() - {
        "history_command", "history_limit", "dags", "state_file", "dry_run",
        "timeout_seconds", "lookback_hours",
    }
    if unknown:
        raise ValueError(f"未定義の設定項目があります（{', '.join(sorted(unknown))}）")
    command = config.get("history_command")
    if not isinstance(command, list) or not command or not all(map(valid_text, command)):
        raise ValueError("history_command は空でない文字列の配列を指定してください")
    history_limit = config.setdefault("history_limit", 100)
    if type(history_limit) is not int or history_limit <= 0:
        raise ValueError("history_limit は正の整数を指定してください")
    per_dag = any("{dag}" in arg for arg in command)
    dags = config.get("dags", [])
    if (not isinstance(dags, list) or not all(map(valid_text, dags))
            or len(set(dags)) != len(dags) or ALL in dags or (per_dag and not dags)):
        raise ValueError("dags は重複や __all__ を含まない DAG 名の配列を指定してください"
                         "（{dag} を使う場合は必須）")
    config["commands"] = {
        dag: [arg.replace("{dag}", dag) if per_dag else arg for arg in command]
        for dag in (dags if per_dag else [ALL])
    }
    state_file = config.get("state_file", DEFAULT_STATE)
    if not valid_text(state_file):
        raise ValueError("state_file は空でないパスを指定してください")
    config["state_file"] = (path.parent / Path(state_file).expanduser()).resolve()
    if type(config.setdefault("dry_run", True)) is not bool:
        raise ValueError("dry_run は true または false を指定してください")
    timeout = config.setdefault("timeout_seconds", 60)
    if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
        raise ValueError("timeout_seconds は 0 より大きく 3600 以下を指定してください")
    hours = config.setdefault("lookback_hours", 26)
    if type(hours) not in (int, float) or not 0 < hours < float("inf"):
        raise ValueError("lookback_hours は正の有限な数値を指定してください")
    try:
        config["since"] = datetime.now(timezone.utc) - timedelta(hours=hours)
    except OverflowError:
        raise ValueError("lookback_hours が日時の範囲を超えています") from None
    return config


def parse_time(value):
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00"
                                    if value.endswith("Z") else value)
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def first_text(row, *keys):
    return next((row[key] for key in keys if valid_text(row.get(key))), "")


def first_time(row, *keys):
    for key in keys:
        value = first_text(row, key)
        if value:
            try:
                return parse_time(value)
            except (ValueError, OverflowError):
                continue
    return None


def first_list(row, *keys):
    return next((row[key] for key in keys if isinstance(row.get(key), list)), None)


def normalize(row, dag):
    if not isinstance(row, dict):
        return None
    run_id = first_text(row, "dagRunId", "runId", "id")
    started = first_time(row, "startedAt", "started_at", "createdAt")
    finished = first_time(row, "finishedAt", "finished_at")
    timestamp = finished or first_time(row, "updatedAt") or started
    if not run_id or timestamp is None:
        return None
    steps = first_list(row, "nodes", "steps") or []
    failed_steps = [
        first_text(step, "name") or "ステップ不明" for step in steps
        if isinstance(step, dict) and first_text(step, "status").casefold() in FAILURE_STATUSES
    ]
    return Run(first_text(row, "name", "dag", "dagName") or dag or "DAG 名不明",
               run_id, first_text(row, "status").casefold(), started, finished, timestamp,
               failed_steps)


def read_history(command, source, cwd, timeout):
    # コマンド名は PATH で探し、相対パスは設定ディレクトリから解決する。
    argv = [os.path.expanduser(arg) if arg.startswith("~/") else arg for arg in command]
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, stdin=subprocess.DEVNULL,
                                timeout=timeout, check=False)
    except FileNotFoundError:
        raise HistoryError("履歴取得コマンドが見つかりません") from None
    except subprocess.TimeoutExpired:
        raise HistoryError("履歴取得コマンドがタイムアウトしました") from None
    except OSError:
        raise HistoryError("履歴取得コマンドを実行できません") from None
    if result.returncode:
        raise HistoryError(f"履歴取得コマンド失敗（終了コード {result.returncode}）")
    try:
        rows = json.loads(result.stdout)
    except (ValueError, RecursionError):
        raise HistoryError("履歴の JSON を読み取れません") from None
    if isinstance(rows, dict):
        rows = first_list(rows, "runs", "history", "dagRuns", "items")
    if not isinstance(rows, list):
        raise HistoryError("履歴の JSON に実行の配列がありません")
    dag = None if source == ALL else source
    runs = [run for row in rows if (run := normalize(row, dag)) is not None]
    return runs, len(rows)


def load_state(path):
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(state, dict) or type(state.get("version")) is not int
                or state["version"] != 1 or not isinstance(state.get("sources"), dict)):
            raise ValueError("状態ファイルの形式が不正です")
        for entry in state["sources"].values():
            if (not isinstance(entry, dict) or not valid_text(entry.get("watermark"))
                    or not isinstance(entry.get("reported_ids"), list)
                    or not all(map(valid_text, entry["reported_ids"]))):
                raise ValueError("source の状態が不正です")
            parse_time(entry["watermark"])
        return state
    except FileNotFoundError:
        pass
    except (ValueError, OverflowError, RecursionError):
        print("状態ファイルが壊れているため、初回として扱います", file=sys.stderr)
    return {"version": 1, "sources": {}}


def save_state(path, state):
    # 同じディレクトリで置き換え、処理が中断されても書きかけの状態を残さない。
    file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temporary = Path(file.name)
    try:
        with file:
            json.dump(state, file, ensure_ascii=True, indent=2)
            file.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def pending_runs(runs, entry, since):
    watermark = parse_time(entry["watermark"]) if entry else since
    previous_ids = entry["reported_ids"] if entry else []
    seen = set(previous_ids)
    pending = []
    for run in sorted(runs, key=lambda run: run.timestamp, reverse=True):
        if (run.is_failure and run.run_id not in seen
                and (run.finished is None or run.timestamp >= watermark)):
            pending.append(run)
            seen.add(run.run_id)
    return pending, previous_ids


def markdown(text):
    # 夜間点検と同様に一行に収め、Markdown や GitLab のメンションを無効にする。
    line = " ".join(text.splitlines()).strip()
    if len(line) > 400:
        line = line[:397] + "..."
    return re.sub(r"[\\`*_\[\]<>#&@~!()|\ud800-\udfff]",
                  lambda match: f"&#{ord(match[0])};", line)


def render(runs, errors, limited_sources, history_limit, day):
    lines = [f"<!-- dagu-failure-digest {day} -->", f"## Dagu 実行失敗サマリ（{day}）", ""]
    for run in sorted(runs, key=lambda run: run.timestamp, reverse=True):
        started = run.started.isoformat() if run.started else "開始時刻不明"
        steps = markdown("、".join(run.failed_steps)) or "ステップ不明"
        lines.append(f"- {markdown(run.dag)} — {started} — 失敗ステップ: {steps}"
                     f" — 実行 ID: {markdown(run.run_id)}")
    if errors:
        if runs:
            lines.append("")
        lines.extend(["### ❓ 未確認（履歴を取得できませんでした）", ""])
        for source, reason in errors.items():
            label = "全体" if source == ALL else markdown(source)
            lines.append(f"- {label} — {markdown(reason)}")
    if not runs and not errors:
        lines.append("✅ Dagu の実行失敗はありません")
    if limited_sources:
        lines.extend(["", "### ⚠️ 注意（履歴の上限）", ""])
        for source in limited_sources:
            label = "全体" if source == ALL else markdown(source)
            lines.append(f"- {label} — 履歴が上限 {history_limit} 件に達したため、それより古い失敗を"
                         "取りこぼした可能性があります。history_command の --limit と history_limit を"
                         "大きくしてください。")
    return "\n".join(lines) + "\n"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def hub_notes_url(issue_url):
    parts = urlsplit(issue_url)
    match = re.fullmatch(r"/([^/]+(?:/[^/]+)+)/-/issues/([0-9]+)/?", parts.path)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.query or parts.fragment or match is None
            or any(char.isspace() or ord(char) < 32 for char in issue_url)):
        raise ValueError("GITLAB_HUB_ISSUE_URL に HTTPS の GitLab issue URL を指定してください")
    parts.port  # 不正なポート指定は ValueError になる。
    project = quote(unquote(match[1]), safe="")
    path = f"/api/v4/projects/{project}/issues/{match[2]}/notes"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def post(report, timeout):
    notes_url = hub_notes_url(os.environ.get("GITLAB_HUB_ISSUE_URL", ""))
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        raise ValueError("GITLAB_TOKEN を設定してください")
    headers = {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}
    payload = json.dumps({"body": report}, ensure_ascii=False).encode("utf-8")
    opener = build_opener(NoRedirect())
    request = Request(notes_url, data=payload, headers=headers, method="POST")
    with opener.open(request, timeout=timeout) as response:
        if response.status != 201:
            raise ValueError(f"GitLab の投稿結果を確認できません（HTTP {response.status}）")


def run(config, cwd, day, dry_run):
    path = config["state_file"]
    timeout = config["timeout_seconds"]
    lock_context = nullcontext()
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_context = path.with_name(path.name + ".lock").open("a")
    with lock_context as lock:
        if lock is not None:
            # 履歴の取得から保存まで直列化し、待機中に古くなった履歴で状態を上書きしない。
            fcntl.flock(lock, fcntl.LOCK_EX)
        state = load_state(path)
        sources = state["sources"]
        failures = []
        errors = {}
        limited_sources = []
        history_limit = config["history_limit"]
        for source, command in config["commands"].items():
            try:
                runs, count = read_history(command, source, cwd, timeout)
            except HistoryError as error:
                errors[source] = str(error)
                continue
            limited = count >= history_limit
            if limited:
                limited_sources.append(source)
            pending, previous_ids = pending_runs(runs, sources.get(source), config["since"])
            failures.extend(pending)
            if runs:
                watermark = min if limited else max
                sources[source] = {
                    "watermark": watermark(run.timestamp for run in runs).isoformat(),
                    "reported_ids": ([run.run_id for run in pending] + previous_ids)[:1000],
                }

        report = render(failures, errors, limited_sources, history_limit, day)
        sys.stdout.write(report)
        posted = False
        if dry_run:
            print("dry-run のため投稿しません", file=sys.stderr)
        elif failures or errors or limited_sources:
            post(report, timeout)
            posted = True
            print("GitLab ハブ issue に投稿しました", file=sys.stderr)
        else:
            print("投稿対象がないため投稿しません", file=sys.stderr)

        all_failed = len(errors) == len(config["commands"])
        if not dry_run and not all_failed:
            try:
                save_state(path, state)
            except OSError as error:
                message = "状態保存に失敗しました。"
                if posted:
                    message += "次回に同じ内容が重複して投稿される可能性があります。"
                print(f"dagu-failure-digest: {message} {error}", file=sys.stderr)
                return 2
        return 3 if all_failed else 0


def parse_date(value):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise argparse.ArgumentTypeError("日付は YYYY-MM-DD で指定してください")
    return date.fromisoformat(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="JSON 設定ファイル")
    parser.add_argument("--dry-run", action="store_true", help="投稿と状態保存を行わない")
    parser.add_argument("--date", type=parse_date, help="タイトルの日付（YYYY-MM-DD）")
    args = parser.parse_args(argv)
    try:
        path = args.config.resolve()
        config = load_config(path)
        override = os.environ.get("DAGU_FAILURE_DIGEST_DRY_RUN")
        dry_run = args.dry_run or (override == "1" if override in ("0", "1") else config["dry_run"])
        return run(config, path.parent, args.date or date.today(), dry_run)
    except (OSError, ValueError, RuntimeError, OverflowError) as error:
        print(f"dagu-failure-digest: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
