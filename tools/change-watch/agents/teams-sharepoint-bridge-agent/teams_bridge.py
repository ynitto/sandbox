"""同期先の依頼 JSON を検証し、Copilot アプリの受け箱に提出する。"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_REQUIRED = ("team_id", "channel_id", "message_id", "author", "posted_at", "text")
_STATE = "~/.local/state/change-watch/teams-sharepoint-bridge-agent"


def _invalid_constant(value):
    raise ValueError(f"JSON に使えない値です（{value}）")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_invalid_constant)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _ledger_path(value=None):
    return Path(value or os.environ.get("TEAMS_BRIDGE_LEDGER") or f"{_STATE}/submitted.json").expanduser()


def _read_ledger(path):
    try:
        ledger = _read_json(path)
    except FileNotFoundError:
        return {}
    if not isinstance(ledger, dict):
        raise ValueError("台帳の JSON はオブジェクトである必要があります")
    return ledger


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _resolve_target(request, path):
    config = _read_json(path)
    if not isinstance(config, dict) or not isinstance(config.get("targets"), dict):
        raise ValueError("宛先表の targets はオブジェクトである必要があります")
    name = request.get("target", config.get("default"))
    target = config["targets"].get(name) if isinstance(name, str) else None
    if not isinstance(target, dict):
        raise ValueError("指定された名前に対応する宛先がありません")
    for field in ("dir", "gitlab_project"):
        if not _nonempty(target.get(field)):
            raise ValueError(f"宛先の {field} は空でない文字列で指定してください")
    if target.get("mode") not in ("issue", "commit"):
        raise ValueError("宛先の mode は issue または commit にしてください")
    return target


def _validate(request, path, targets_path, resolved) -> list[str]:
    """様式違反を返す。宛先表がある場合は照合し、解決した宛先を resolved に入れる。"""
    if not isinstance(request, dict):
        return ["依頼の JSON はオブジェクトである必要があります"]

    errors = []
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        errors.append("request_id は英数字で始まる 1〜128 文字の英数字・ピリオド・下線・ハイフンにしてください")
    elif path.name != f"{request_id}.json":
        errors.append("request_id がファイル名と一致しません")

    for field in _REQUIRED:
        if not _nonempty(request.get(field)):
            errors.append(f"{field} は空でない文字列で指定してください")

    posted_at = request.get("posted_at")
    if _nonempty(posted_at):
        iso = posted_at[:-1] + "+00:00" if posted_at.endswith("Z") else posted_at
        try:
            datetime.fromisoformat(iso)
        except ValueError:
            errors.append("posted_at は ISO 8601 で指定してください")

    body = request.get("text")
    if isinstance(body, str) and len(body) > 20000:
        errors.append("text は 20000 文字までです")

    if "target" in request and not isinstance(request["target"], str):
        errors.append("target は文字列で指定してください")
    elif targets_path is not None:
        try:
            resolved.update(_resolve_target(request, targets_path))
        except (OSError, ValueError) as error:
            errors.append(f"宛先を解決できません（{error}）")
    return errors


def _load_request(args):
    path = Path(args.path).expanduser()
    target = {}
    try:
        request = _read_json(path)
    except OSError as error:
        return None, target, [f"依頼ファイルを読めません（{error}）"], "missing"
    except ValueError as error:
        return None, target, [f"依頼ファイルを JSON として読めません（{error}）"], "invalid"
    targets = Path(args.targets).expanduser() if args.targets else None
    errors = _validate(request, path, targets, target)
    return request, target, errors, "invalid" if errors else None


def _error(*errors):
    print(*errors, sep="\n", file=sys.stderr)
    return 2


def cmd_check(args):
    # 台帳が壊れていれば、依頼の状態にかかわらず振り分けを止める。
    try:
        ledger = _read_ledger(_ledger_path(args.ledger))
    except (OSError, ValueError) as error:
        return _error(f"台帳を読めません（{error}）")
    request, _, _, route = _load_request(args)
    if route is None:
        route = "done" if request["request_id"] in ledger else "submit"
    print(route)
    return 0


def _marker(request):
    return f'<!-- teams-bridge:request={request["request_id"]} teams-message={request["message_id"]} -->'


def _prompt(request, target):
    project = target["gitlab_project"]
    if target["mode"] == "issue":
        publish = f"結果を自分で GitLab のプロジェクト `{project}` に issue として上げてください"
        location = "issue の説明"
    else:
        publish = f"結果を GitLab のプロジェクト `{project}` にコミットして push してください"
        location = "コミットメッセージ"
    return (
        f'依頼者: {request["author"]}\n'
        f'投稿日時: {request["posted_at"]}\n'
        f'元の投稿の message_id: {request["message_id"]}\n\n'
        f'--- 依頼本文 ---\n{request["text"]}\n--- 依頼本文ここまで ---\n\n'
        f"{publish}。\n{location}に次の相関マーカーをそのまま必ず含めてください。\n"
        f"{_marker(request)}\n"
    )


def cmd_build_prompt(args):
    request, target, errors, _ = _load_request(args)
    if errors:
        return _error(*errors)
    print(_prompt(request, target), end="")
    return 0


@contextmanager
def _submission_lock(ledger_path):
    """台帳の読み取りから更新まで、同じ台帳を使う提出を直列にする。"""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.with_name(ledger_path.name + ".lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # ファイルを閉じるとロックが解放される（途中で停止したときも同じ）。
        yield


def _write_ledger(path, ledger):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            json.dump(ledger, tmp, ensure_ascii=False, indent=2)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _existing_job(target, request):
    # 台帳の更新前に停止しても、受け箱に残る依頼文の末尾のマーカーから照合できる。
    # Copilot が依頼を移す順に調べる。ジョブ名のラベルは切り詰められるため照合には使わない。
    box = Path(target["dir"]).resolve() / ".copilot-jobs"
    marker = "\n" + _marker(request)
    for state in ("pending", "running", "done"):
        for path in (box / state).glob("*.md"):
            try:
                if path.read_text(encoding="utf-8").rstrip("\n").endswith(marker):
                    return path.stem
            except (FileNotFoundError, UnicodeDecodeError):
                continue
    return None


def _emit(**data):
    print(json.dumps(data, ensure_ascii=True))
    return 0


def _submit(args, request, target, ledger_path):
    ledger = _read_ledger(ledger_path)
    request_id = request["request_id"]
    if request_id in ledger:
        return _emit(status="already", request_id=request_id)

    job = None if args.dry_run else _existing_job(target, request)
    if job is None:
        jobs_py = args.jobs_py or os.environ.get("TEAMS_BRIDGE_COPILOT_JOB")
        if jobs_py:
            jobs_py = Path(jobs_py).expanduser()
        else:
            jobs_py = Path(__file__).resolve().parent / "../../../copilot-app-jobs/copilot_app_job.py"
            jobs_py = jobs_py.resolve()
        if not jobs_py.is_file():
            return _error(f"copilot_app_job.py が見つかりません（{jobs_py}）。--jobs-py または TEAMS_BRIDGE_COPILOT_JOB で指定してください")

        prompt = _prompt(request, target)
        with tempfile.TemporaryDirectory(prefix="teams-bridge-") as tmp:
            prompt_path = Path(tmp) / "prompt.txt"
            command = [
                args.python, str(jobs_py), "run", "--dir", target["dir"], "--no-wait",
                "--name", f"teams-{request_id}", "--prompt-file", str(prompt_path),
            ]
            if args.dry_run:
                return _emit(status="dry-run", request_id=request_id, prompt=prompt, command=command)

            prompt_path.write_text(prompt, encoding="utf-8", newline="")
            result = subprocess.run(command, stdout=subprocess.PIPE)
            if result.returncode != 0:
                print(f"copilot-app-jobs への提出に失敗しました（終了コード {result.returncode}）", file=sys.stderr)
                return result.returncode if result.returncode > 0 else 1
            job = result.stdout.decode("utf-8").strip()

    ledger[request_id] = {
        "message_id": request["message_id"], "submitted_at": _now(), "dir": target["dir"], "job": job,
    }
    _write_ledger(ledger_path, ledger)
    return _emit(status="submitted", request_id=request_id, job=job)


def cmd_submit(args):
    request, target, errors, _ = _load_request(args)
    if errors:
        return _error(*errors)
    try:
        ledger_path = _ledger_path(args.ledger)
        if args.dry_run:
            return _submit(args, request, target, ledger_path)
        with _submission_lock(ledger_path):
            return _submit(args, request, target, ledger_path)
    except (OSError, ValueError) as error:
        return _error(f"依頼を提出できません（{error}）")


def cmd_record_invalid(args):
    request, _, errors, _ = _load_request(args)
    request_id = request.get("request_id") if isinstance(request, dict) else None
    entry = {"at": _now(), "path": args.path, "request_id": request_id if isinstance(request_id, str) else None,
             "errors": errors}
    try:
        path = Path(args.log).expanduser() if args.log else _ledger_path().with_name("invalid.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except (OSError, ValueError) as error:
        return _error(f"違反ログに追記できません（{error}）")
    return 0


_COMMANDS = {
    "check-file": (cmd_check, "依頼を調べ、次の処理を 1 語で出す"),
    "build-prompt": (cmd_build_prompt, "Copilot への依頼文を出す"),
    "submit": (cmd_submit, "依頼文を Copilot アプリの受け箱に提出する"),
    "record-invalid": (cmd_record_invalid, "依頼の様式違反をログに追記する"),
}


def _arguments(parser, name):
    parser.add_argument("--path", required=True, help="依頼の JSON ファイル")
    parser.add_argument("--targets", required=name in ("build-prompt", "submit"), help="宛先表の JSON ファイル")
    if name in ("check-file", "submit"):
        parser.add_argument("--ledger", help="処理済み台帳の JSON ファイル")
    if name == "submit":
        parser.add_argument("--jobs-py", help="copilot_app_job.py のパス")
        parser.add_argument("--python", default=sys.executable, help="copilot-app-jobs を動かす Python")
        parser.add_argument("--dry-run", action="store_true", help="提出せず依頼文とコマンドを出す")
    if name == "record-invalid":
        parser.add_argument("--log", help="様式違反を追記する JSON Lines ファイル")


def register(api):
    """change-watch の設定と状態を読まずに動くコマンドを足す。"""
    for name, (run, help_text) in _COMMANDS.items():
        parser = api.add_command(name, lambda _cfg, _store, args, run=run: run(args),
                                 help_text, needs_config=False)
        _arguments(parser, name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (_, help_text) in _COMMANDS.items():
        _arguments(sub.add_parser(name, help=help_text), name)
    args = parser.parse_args(argv)
    return _COMMANDS[args.command][0](args)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
