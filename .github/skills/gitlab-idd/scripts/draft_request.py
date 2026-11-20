"""依頼メモから分割案を作り、ハブイシューへ下書きとして返す。"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile

import gl

DRAFT_MARK = "【下書き】分割案（承認前にイシューは作成されません）"
REFERENCE = Path(__file__).resolve().parents[1] / "references" / "request-draft.md"
AGENT_ENV_NAMES = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP",
    "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "USER", "LOGNAME",
)
SHELL_NAMES = {"sh", "bash", "dash", "ash", "zsh", "ksh", "fish", "cmd", "powershell", "pwsh"}
CHILD_HEADING = re.compile(r"^## 子イシュー案[ \t]*$", re.MULTILINE)
TITLE_HEADING = re.compile(r"^###[ \t]+(\S[^\n]*)$", re.MULTILINE)
ACCEPTANCE_HEADING = re.compile(r"^#### 受け入れ条件[ \t]*$", re.MULTILINE)
QUICK_ACTION = re.compile(r"^(\s*)/", re.MULTILINE)


def agent_timeout():
    try:
        seconds = int(os.environ.get("IDD_DRAFT_TIMEOUT_SEC", "300"))
    except ValueError:
        seconds = 300
    return max(1, min(seconds, 900))


def stop_agent(process):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, check=True)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        process.kill()


def generate(memo):
    try:
        argv = json.loads(os.environ.get("IDD_DRAFT_AGENT", ""))
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            return ""
        if not argv[0] or "{prompt}" in argv[0] or "{prompt}" not in argv[1:]:
            return ""
        executable = Path(argv[0]).name.lower().removesuffix(".exe")
        if executable in SHELL_NAMES or executable.endswith((".bat", ".cmd")):
            return ""
        if any("{prompt}" in arg and arg != "{prompt}" for arg in argv[1:]):
            return ""
        reference = REFERENCE.read_text(encoding="utf-8")
        template = reference.split("```text\n", 1)[1].split("\n```", 1)[0]
        prompt = template.replace("{memo}", memo)
        command = [prompt if arg == "{prompt}" else arg for arg in argv]
        env = {name: os.environ[name] for name in AGENT_ENV_NAMES if name in os.environ}
        group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                 else {"start_new_session": True})
        with tempfile.TemporaryDirectory(prefix="idd-draft-") as directory:
            with subprocess.Popen(command, cwd=directory, env=env, shell=False,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, encoding="utf-8", **group) as process:
                try:
                    stdout, _ = process.communicate(timeout=agent_timeout())
                except subprocess.TimeoutExpired:
                    stop_agent(process)
                    return ""
                return stdout.strip() if process.returncode == 0 else ""
    except (OSError, ValueError, IndexError):
        return ""


def draft_titles(body):
    body = body.replace("\r", "")
    section = CHILD_HEADING.search(body)
    if section is None:
        return []
    # A heading in another section cannot supply a title or acceptance criteria.
    proposals = re.split(r"^#{1,2}[ \t]+", body[section.end():], maxsplit=1, flags=re.MULTILINE)[0]
    headings = list(TITLE_HEADING.finditer(proposals))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(proposals)
        if not ACCEPTANCE_HEADING.search(proposals, heading.end(), end):
            return []
    return [heading[1].strip() for heading in headings]


def fallback_body(memo):
    # Use a fence longer than any run of backticks in the memo.
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", memo)), default=0))
    separator = "" if memo.endswith("\n") else "\n"
    return (
        "（自動生成できませんでした。メモを転記しています）\n\n"
        "## 子イシュー案\n\n"
        "### TODO: タイトルを記入\n\n"
        "#### 概要\n\n"
        "- TODO: 依頼メモをもとに分割し、各案の概要を記入\n\n"
        "#### 受け入れ条件\n\n"
        "- [ ] TODO: 確認可能な条件を記入\n\n"
        f"## 依頼メモ\n\n{fence}text\n{memo}{separator}{fence}"
    )


def build(args):
    with args.memo.open(encoding="utf-8", newline="") as source:
        memo = source.read()
    body = generate(memo)
    titles = draft_titles(body)
    method = "cli" if titles else "fallback"
    if not titles:
        body = fallback_body(memo)
        titles = draft_titles(body)
    with args.out.open("w", encoding="utf-8", newline="\n") as output:
        output.write(f"{DRAFT_MARK}\n\n{body}\n")
    summary = {"count": len(titles), "titles": titles, "method": method}
    args.json_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def post(args):
    body = args.draft.read_text(encoding="utf-8")
    if body.splitlines()[:1] != [DRAFT_MARK]:
        raise ValueError("下書きの先頭に所定の印が必要です")
    if os.environ.get("REQUEST_DRAFT_DRY_RUN") != "0":
        gl.out({"dry_run": True, "hub_issue": args.hub_issue})
        return

    # GitLab interprets slash-prefixed lines in notes as quick actions.
    body = QUICK_ACTION.sub(r"\1&#47;", body)
    host, project = gl.get_project_info(args.label)
    token = gl.get_token(args.label)
    path = f"/projects/{gl.encode_project(project)}/issues/{args.hub_issue}/notes"
    gl.out(gl.api(host, token, "POST", path, data={"body": body}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_args = commands.add_parser("build", help="メモから分割案の下書きを作る")
    build_args.add_argument("--memo", type=Path, required=True)
    build_args.add_argument("--out", type=Path, required=True)
    build_args.add_argument("--json-out", type=Path, required=True)
    build_args.set_defaults(run=build)
    post_args = commands.add_parser("post", help="ハブイシューに下書きをコメントする")
    post_args.add_argument("--draft", type=Path, required=True)
    post_args.add_argument("--hub-issue", type=int, required=True, help="ハブイシューの IID")
    post_args.add_argument("--label-conn", dest="label", default="default", help="GitLab 接続ラベル")
    post_args.set_defaults(run=post)
    args = parser.parse_args(argv)
    if args.command == "post" and args.hub_issue < 1:
        parser.error("--hub-issue は正の整数を指定してください")
    try:
        args.run(args)
    except (OSError, ValueError) as error:
        parser.exit(1, f"ERROR: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
