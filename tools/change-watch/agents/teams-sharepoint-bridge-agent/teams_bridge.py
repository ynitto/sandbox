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


_TAG = re.compile(r"^\s*(?:#([A-Za-z0-9_.-]{1,64})|【([^】\s]{1,64})】)")


def _check_target(target):
    if not isinstance(target, dict):
        raise ValueError("指定された名前に対応する宛先がありません")
    for field in ("dir", "gitlab_project"):
        if not _nonempty(target.get(field)):
            raise ValueError(f"宛先の {field} は空でない文字列で指定してください")
    if not Path(target["dir"]).is_absolute():
        raise ValueError("宛先の dir は絶対パスで指定してください")
    if target.get("mode") not in ("issue", "commit"):
        raise ValueError("宛先の mode は issue または commit にしてください")
    return target


def _keyword_hits(text, words):
    low = text.lower()
    return [w for w in words if isinstance(w, str) and w.strip() and w.lower() in low]


def _for_teams(target):
    """Teams の宛先か。gitlab_project も mode も無いものは、Teams 以外から振るために登録した振り先として外す。
    どちらかがあれば Teams の宛先として扱い、書き誤りはそのまま様式違反にする（黙って外さない）。"""
    return not isinstance(target, dict) or "gitlab_project" in target or "mode" in target


def _hub_of(config, path):
    """ハブのフォルダ。宛先表がハブの受け箱（<ハブ>/.copilot-jobs/targets.json）にあれば、その場所から決める。"""
    hub = config.get("hub")
    if hub is not None:
        if not _nonempty(hub) or not Path(hub).is_absolute():
            raise ValueError("宛先表の hub は絶対パスで指定してください")
        return hub
    folder = Path(path).expanduser().resolve().parent
    return str(folder.parent) if folder.name == ".copilot-jobs" else None


def _resolve_target(request, path):
    """宛先を決める。決まる順: 投稿の 1 行目の #名前・【名前】 → フローの target → チャンネル → キーワード
    → （ハブがあれば）ハブのセッションに選ばせる → 既定。返すのは {name, reason, target} か {auto, choices, default}。

    宛先表は goalie の振り先の表と同じファイル。gitlab_project・mode の無い宛先（Teams 以外から振るために
    登録したもの）は、Teams の振り分けには使わない。"""
    config = _read_json(path)
    if not isinstance(config, dict) or not isinstance(config.get("targets"), dict):
        raise ValueError("宛先表の targets はオブジェクトである必要があります")
    targets = {n: t for n, t in config["targets"].items() if _for_teams(t)}
    hub = _hub_of(config, path)
    text = request.get("text") if isinstance(request.get("text"), str) else ""
    first = next((line for line in text.splitlines() if line.strip()), "")

    def one(name, reason):
        return {"name": name, "reason": reason, "target": _check_target(targets.get(name)), "hub": hub}

    tag = _TAG.match(first)
    if tag and (tag.group(1) or tag.group(2)) in targets:
        name = tag.group(1) or tag.group(2)
        return one(name, f"投稿で指定（{tag.group(0).strip()}）")
    if "target" in request:
        return one(request["target"], "フローで指定")
    pool = list(targets)
    channel = request.get("channel_id")
    by_channel = [n for n in pool if isinstance(targets[n], dict) and channel in (targets[n].get("channels") or [])]
    if len(by_channel) == 1:
        return one(by_channel[0], "投稿したチャンネル")
    if by_channel:
        pool = by_channel
    hits = {n: _keyword_hits(text, targets[n].get("keywords") or []) for n in pool if isinstance(targets[n], dict)}
    best = max((len(h) for h in hits.values()), default=0)
    winners = [n for n, h in hits.items() if len(h) == best]
    if best > 0 and len(winners) == 1:
        return one(winners[0], "キーワード（" + "・".join(hits[winners[0]]) + "）")
    if best > 0:
        pool = winners
    default = config.get("default")
    if hub and len(pool) > 1:
        choices = {n: _check_target(targets[n]) for n in pool}
        return {"auto": True, "choices": choices, "default": default if default in choices else pool[0], "hub": hub,
                "reason": "ハブのセッションが選ぶ"}
    if len(pool) == 1 and pool != list(targets):
        return one(pool[0], "候補が 1 つに絞れた")
    return one(default, "既定の宛先")


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

    # マーカー（HTML コメント）の形を壊さないため、message_id を検証する。
    message_id = request.get("message_id")
    if _nonempty(message_id) and (
        len(message_id) > 256 or any(char.isspace() for char in message_id)
        or any(part in message_id for part in ("<", ">", "--"))
    ):
        errors.append("message_id は 256 文字以内で、空白・<・>・-- を含めないでください")

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
        except (OSError, ValueError, TypeError) as error:
            errors.append(f"宛先を解決できません（{error}）")
    return errors


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _request_path(args):
    """依頼ファイルの場所。--path か、--inbox（受信フォルダ）と --name（フォルダからの相対の名前）で受け取る。

    Dagu の例は --inbox と --name で渡す。Windows の道（C:\\...\\tm-1.json）を ${…} で引数に展開させると、
    途中の \\t などが制御文字に化けることがあるため、\\ を含まない相対の名前だけを Dagu に渡す。"""
    if args.path:
        return Path(args.path).expanduser()
    name = str(args.name or "")
    parts = name.replace("\\", "/").split("/")
    if not args.inbox or not name or name.startswith("/") or ".." in parts or ":" in name:
        raise ValueError("--path か、--inbox と --name（受信フォルダからの相対の名前）を指定してください")
    return Path(args.inbox).expanduser() / Path(*parts)


def _load_request(args):
    target = {}
    try:
        path = _request_path(args)
    except ValueError as error:
        return None, target, [str(error)], "missing"
    try:
        request = _read_json(path)
    except OSError as error:
        hint = ""
        if _CONTROL.search(str(path)):
            hint = "。パスに制御文字が入っています。Dagu が \\t などを展開した可能性があります。--inbox と --name で渡してください"
        return None, target, [f"依頼ファイルを読めません（{str(path)!r}: {error}）{hint}"], "missing"
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
    request, _, errors, route = _load_request(args)
    if route is None:
        route = "done" if request["request_id"] in ledger else "submit"
    elif route == "missing":
        print(*errors, sep="\n", file=sys.stderr)   # Dagu の記録に、どの道を読めなかったかを残す
    print(route)
    return 0


def _marker(request):
    return f'<!-- teams-bridge:request={request["request_id"]} teams-message={request["message_id"]} -->'


def _escape_marker(value):
    # 利用者の文が相関マーカーの形を作れないようにする。
    return value.replace("teams-bridge:", "teams-bridge：")


def _publish(request, target, name, reason):
    project = target["gitlab_project"]
    if target["mode"] == "issue":
        publish = f"結果を自分で GitLab のプロジェクト `{project}` に issue として上げてください"
        location = "issue の説明"
    else:
        publish = f"結果を GitLab のプロジェクト `{project}` にコミットして push してください"
        location = "コミットメッセージ"
    return (
        f"{publish}。\n{location}の最後に、次の 2 行をそのまま置いてください。\n"
        f"宛先: {name}（{reason}）\n"
        f"{_marker(request)}\n"
    )


def _body(request):
    request_id = request["request_id"]
    return (
        f'依頼者: {_escape_marker(request["author"])}\n'
        f'投稿日時: {request["posted_at"]}\n'
        f'元の投稿の message_id: {request["message_id"]}\n\n'
        f'--- 依頼本文（request_id: {request_id}） ---\n'
        f'{_escape_marker(request["text"])}\n'
        f'--- 依頼本文ここまで（request_id: {request_id}） ---\n'
    )


def _prompt(request, resolved):
    """宛先が決まっていれば依頼文の全体。ハブに選ばせるときは本文だけ（宛先ごとの続きは候補に持たせる）。"""
    if resolved.get("auto"):
        return _body(request)
    return _body(request) + "\n" + _publish(request, resolved["target"], resolved["name"], resolved["reason"])


def _choices(request, resolved):
    return {"default": resolved["default"], "choices": {
        name: {"dir": target["dir"], "description": str(target.get("description") or ""),
               "append": _publish(request, target, name, "ハブのセッションが選んだ")}
        for name, target in resolved["choices"].items()}}


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


def _box_dir(resolved):
    """依頼を置く受け箱のフォルダ。ハブがあればハブ、無ければ宛先のフォルダ。"""
    return resolved.get("hub") or resolved["target"]["dir"]


def _existing_job(resolved, request):
    # 台帳の更新前に停止しても、受け箱に残る依頼文のマーカーから照合できる。
    # Copilot が依頼を移す順に調べる。ジョブ名のラベルは切り詰められるため照合には使わない。
    box = Path(_box_dir(resolved)).resolve() / ".copilot-jobs"
    marker = _marker(request)
    for state in ("pending", "running", "dispatched", "done"):
        for path in (box / state).glob("*.md"):
            try:
                if marker in path.read_text(encoding="utf-8", errors="replace"):
                    return path.stem
            except FileNotFoundError:
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
        jobs_py = args.jobs_py or os.environ.get("TEAMS_BRIDGE_GOALIE_JOB")
        if jobs_py:
            jobs_py = Path(jobs_py).expanduser()
        else:
            jobs_py = Path(__file__).resolve().parent / "../../../goalie/goalie.py"
            jobs_py = jobs_py.resolve()
        if not jobs_py.is_file():
            return _error(f"goalie.py が見つかりません（{jobs_py}）。--jobs-py または TEAMS_BRIDGE_GOALIE_JOB で指定してください")

        prompt = _prompt(request, target)
        with tempfile.TemporaryDirectory(prefix="teams-bridge-") as tmp:
            prompt_path = Path(tmp) / "prompt.txt"
            choices_path = Path(tmp) / "choices.json"
            command = [
                args.python, str(jobs_py), "job", "run", "--dir", _box_dir(target), "--no-wait",
                "--name", f"teams-{request_id}", "--prompt-file", str(prompt_path),
            ]
            if target.get("auto"):
                command += ["--to", "auto", "--choices-file", str(choices_path)]
            elif target.get("hub"):
                command += ["--to", target["target"]["dir"]]
            route = {"target": target.get("name"), "reason": target.get("reason")}
            if target.get("auto"):
                route = {"target": "auto", "reason": target["reason"], "choices": list(target["choices"])}
            if args.dry_run:
                return _emit(status="dry-run", request_id=request_id, prompt=prompt, command=command, route=route)

            prompt_path.write_text(prompt, encoding="utf-8", newline="")
            if target.get("auto"):
                choices_path.write_text(json.dumps(_choices(request, target), ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(command, stdout=subprocess.PIPE)
            if result.returncode != 0:
                print(f"goalie への提出に失敗しました（終了コード {result.returncode}）", file=sys.stderr)
                return result.returncode if result.returncode > 0 else 1
            job = result.stdout.decode("utf-8").strip()

    ledger[request_id] = {
        "message_id": request["message_id"], "submitted_at": _now(), "dir": _box_dir(target), "job": job,
        "target": "auto" if target.get("auto") else target["name"], "reason": target["reason"],
    }
    _write_ledger(ledger_path, ledger)
    return _emit(status="submitted", request_id=request_id, job=job,
                 target="auto" if target.get("auto") else target["name"], reason=target["reason"])


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
    except BlockingIOError:
        return _error("別の提出が進行中です。次回のサイクルで再試行されます")
    except (OSError, ValueError) as error:
        return _error(f"依頼を提出できません（{error}）")


def cmd_record_invalid(args):
    request, _, errors, _ = _load_request(args)
    request_id = request.get("request_id") if isinstance(request, dict) else None
    entry = {"at": _now(), "path": args.path or f"{args.inbox}/{args.name}", "request_id": request_id if isinstance(request_id, str) else None,
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
    parser.add_argument("--path", help="依頼の JSON ファイル（--inbox と --name の代わり）")
    parser.add_argument("--inbox", help="受信フォルダ（change-watch.yaml の folder.path と同じ）")
    parser.add_argument("--name", help="受信フォルダからの相対の名前（get の出力の record.rel）")
    parser.add_argument("--targets", required=name in ("check-file", "build-prompt", "submit"),
                        help="宛先表の JSON ファイル")
    if name in ("check-file", "submit"):
        parser.add_argument("--ledger", help="処理済み台帳の JSON ファイル")
    if name == "submit":
        parser.add_argument("--jobs-py", help="goalie.py のパス")
        parser.add_argument("--python", default=sys.executable, help="goalie を動かす Python")
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
