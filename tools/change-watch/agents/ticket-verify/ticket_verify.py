"""チケット対応のスモークテストを実行し、完了判定に使う証拠を残す。"""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ENV_NAMES = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP",
    "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "USER", "LOGNAME",
)
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|CREDENTIAL|AUTH", re.I)
MAX_LINES = 40
MAX_CHARS = 4000


def argv_list(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(arg, str) and "\x00" not in arg for arg in value)
            and bool(value[0]))


def redactor():
    values = {value for name, value in os.environ.items() if value and SECRET_NAME.search(name)}
    if not values:
        return lambda text: text
    pattern = re.compile("|".join(re.escape(value) for value in sorted(values, key=len, reverse=True)))
    return lambda text: pattern.sub("***", text)


def timeout_seconds():
    try:
        value = int(os.environ.get("TICKET_VERIFY_TIMEOUT_SEC", "120"))
    except ValueError:
        value = 120
    return max(1, min(600, value))


def code_block(text):
    fence = "`" * max(3, 1 + max(map(len, re.findall(r"`+", text)), default=0))
    return f"{fence}\n{text}\n{fence}"


def markdown(evidence, command, truncated):
    output = evidence["output"]
    suffix = "\n\n（出力は末尾の 40 行・4000 字まで）" if truncated else ""
    return (
        "### 検証証拠（スモークテスト）\n\n"
        f"コマンド\n\n{code_block(command)}\n\n"
        f"終了コード {evidence['exit_code']}\n\n"
        f"出力\n\n{code_block(output)}{suffix}\n"
    )


def run(args):
    symlink_output = args.out.is_symlink() or args.md_out.is_symlink()
    try:
        args.out.unlink(missing_ok=True)
    finally:
        args.md_out.unlink(missing_ok=True)
    if symlink_output:
        raise ValueError("証拠の出力先にシンボリックリンクは指定できません")
    paths = (args.command_file, args.out, args.md_out)
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("コマンド・証拠 JSON・証拠 Markdown には別々のパスを指定してください")
    command = json.loads(args.command_file.read_text(encoding="utf-8"))
    if not argv_list(command):
        raise ValueError("検証コマンドには空でない JSON の argv 配列を指定してください")

    mask = redactor()
    evidence = {
        "version": 1,
        "command": [mask(arg) for arg in command],
        "exit_code": None,
        "timed_out": False,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "verified": False,
        "output": "",
    }
    config_error = False
    raw_allow = os.environ.get("TICKET_VERIFY_ALLOW", "")
    try:
        allowed = json.loads(raw_allow) if raw_allow else []
    except ValueError:
        allowed = None

    if not isinstance(allowed, list) or not allowed or not all(argv_list(prefix) for prefix in allowed):
        output = "TICKET_VERIFY_ALLOW には空でない JSON の argv 接頭辞リストを指定してください"
        config_error = True
    elif not any(command[:len(prefix)] == prefix for prefix in allowed):
        output = "TICKET_VERIFY_ALLOW に許可された検証コマンドがありません"
    else:
        env = {name: os.environ[name] for name in ENV_NAMES if name in os.environ}
        timeout = timeout_seconds()
        with tempfile.TemporaryFile() as log:
            message = ""
            try:
                result = subprocess.run(command, cwd=args.cwd, env=env, shell=False,
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=timeout)
                evidence["exit_code"] = result.returncode
                evidence["verified"] = result.returncode == 0
            except subprocess.TimeoutExpired:
                evidence["timed_out"] = True
                message = f"制限時間（{timeout} 秒）を超えました"
            except OSError as exc:
                message = f"検証コマンドを起動できませんでした：{exc}"
            log.seek(0)
            output = log.read().decode("utf-8", errors="replace")
            if message:
                output = f"{output}\n{message}" if output else message

    lines = mask(output).splitlines()
    tail = "\n".join(lines[-MAX_LINES:])
    truncated = len(lines) > MAX_LINES or len(tail) > MAX_CHARS
    evidence["output"] = tail[-MAX_CHARS:]
    if evidence["verified"]:
        shown_command = mask(shlex.join(evidence["command"]))
        args.md_out.write_text(markdown(evidence, shown_command, truncated),
                               encoding="utf-8")
    args.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if evidence["verified"] else (2 if config_error else 1)


def is_fresh(evidence, max_age):
    try:
        hours = float(max_age)
        ran_at = datetime.fromisoformat(evidence["ran_at"])
        age_hours = (datetime.now(timezone.utc) - ran_at).total_seconds() / 3600
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(hours) and hours > 0 and 0 <= age_hours <= hours


def gate(path):
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        result = {"complete": False, "reason": "証拠 JSON がありません"}
    except (OSError, ValueError):
        result = {"complete": False, "reason": "証拠 JSON を読み取れません"}
    else:
        complete = (isinstance(evidence, dict)
                    and type(evidence.get("version")) is int and evidence["version"] == 1
                    and evidence.get("verified") is True
                    and type(evidence.get("exit_code")) is int and evidence["exit_code"] == 0
                    and evidence.get("timed_out") is False)
        reason = "検証済み" if complete else "検証成功の証拠がありません"
        max_age = os.environ.get("TICKET_VERIFY_MAX_EVIDENCE_AGE_HOURS")
        if complete and max_age is not None and not is_fresh(evidence, max_age):
            complete = False
            reason = "証拠の鮮度を確認できません"
        result = {"complete": complete, "reason": reason}
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    runner = commands.add_parser("run")
    for option in ("--command-file", "--cwd", "--out", "--md-out"):
        runner.add_argument(option, type=Path, required=True)
    commands.add_parser("gate").add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args) if args.action == "run" else gate(args.evidence)
    except (OSError, ValueError, RuntimeError) as exc:
        print(redactor()(str(exc)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
