#!/usr/bin/env python3
"""CLI client for the authenticated VS Code Language Model API bridge."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# 対話モードの入力プロンプト。agents/vscode-copilot.json の interactive.ready_pattern が
# この文字列を待機判定に使うので、変えるときは定義も一緒に直す（golden test が守る）。
PROMPT = "copilot> "
HELP = """\
/help            このヘルプ
/clear           会話履歴を捨てて新しい会話を始める
/model [family]  モデル family を表示・変更（例: /model gpt-4o、引数なしで解除）
/exit            終了（Ctrl-D でも同じ）"""


def endpoint_path() -> Path:
    return Path(os.environ.get("VSCODE_COPILOT_BRIDGE_FILE", "~/.vscode-copilot-bridge.json")).expanduser()


def powershell_executable() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not executable:
        raise RuntimeError("powershell.exe が見つかりません（この起動方法は WSL 用です）")
    return executable


def windows_path(path: Path) -> str:
    try:
        return subprocess.run(["wslpath", "-w", str(path)], check=True, text=True,
                              capture_output=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Windows pathへ変換できません: {path}") from exc


def is_wsl() -> bool:
    """WSL から Windows 側の VS Code を起こす経路が使えるか。

    起動先の判定に platform 名は使わない——WSL は Linux を名乗るし、要るのは
    「Windows 側へ渡す道具が揃っているか」だけ。
    """
    return bool(shutil.which("powershell.exe") and shutil.which("wslpath"))


# macOS は `code` を PATH へ入れる操作（Shell Command: Install 'code' command in PATH）が
# 任意なので、入れていない人のために既定の場所も見る。
# 候補は安定版だけにする。install.sh が拡張を置くのは `~/.vscode/extensions` なので、
# Insiders を起こしても拡張が載っておらず「接続できません」で終わる——見つからないと
# 言われるほうが原因が分かる。Insiders を使う人は --code-bin と手動配置で。
MAC_CODE_PATHS = (
    "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
    "~/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
)


def resolve_code_bin(code_bin: str) -> str:
    found = shutil.which(code_bin)
    if found:
        return found
    if code_bin == "code":
        for candidate in MAC_CODE_PATHS:
            path = Path(candidate).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
    raise RuntimeError(
        f"VS Code の起動コマンドが見つかりません: {code_bin}"
        "（PATH へ通すか --code-bin で場所を指定してください）")


def user_data_dir() -> Path:
    return Path.home() / ".vscode-copilot-bridge" / "user-data"


def launch_local_vscode(port: int, token: str, cwd: Path, code_bin: str) -> None:
    """同じ OS 上の VS Code を起こす（macOS / Linux）。"""
    if not 1 <= port <= 65535:
        raise RuntimeError("port は1から65535で指定してください")
    executable = resolve_code_bin(code_bin)
    data_dir = user_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    # Windows 経路と同じ理由で専用 --user-data-dir を使う。既に起動している VS Code へ
    # 接続してしまうと、この env が拡張へ届かない。
    env = {**os.environ,
           "VSCODE_COPILOT_BRIDGE_PORT": str(port),
           "VSCODE_COPILOT_BRIDGE_TOKEN": token}
    subprocess.Popen([executable, "--user-data-dir", str(data_dir), "--new-window", str(cwd)],
                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch_vscode(port: int, token: str, cwd: Path, code_bin: str) -> None:
    if is_wsl():
        launch_windows_vscode(port, token, cwd, code_bin)
    else:
        launch_local_vscode(port, token, cwd, code_bin)


def launch_windows_vscode(port: int, token: str, cwd: Path, code_bin: str) -> None:
    """WSL から Windows 側の VS Code を起こす。"""
    if not 1 <= port <= 65535:
        raise RuntimeError("port は1から65535で指定してください")
    win_cwd = windows_path(cwd)
    # PowerShell の単一引用符文字列は '' でエスケープする。f-string の中で quote を
    # 入れ子にすると Python 3.12 未満で構文エラーになるので、先に組み立てておく。
    quoted_bin = code_bin.replace("'", "''")
    quoted_cwd = win_cwd.replace("'", "''")
    # --user-data-dirにより既存VS Codeプロセスへのenv引き渡し消失を避ける。
    script = (
        f"$env:VSCODE_COPILOT_BRIDGE_PORT='{port}';"
        f"$env:VSCODE_COPILOT_BRIDGE_TOKEN='{token}';"
        "$data=Join-Path $env:LOCALAPPDATA 'vscode-copilot-bridge';"
        f"& '{quoted_bin}' --user-data-dir $data --new-window "
        f"'{quoted_cwd}'"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    subprocess.Popen([powershell_executable(), "-NoProfile", "-NonInteractive",
                      "-EncodedCommand", encoded], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def start_bridge(path: Path, port: int, cwd: Path, code_bin: str) -> dict[str, object]:
    token = secrets.token_hex(32)
    endpoint = {"version": 1, "url": f"http://127.0.0.1:{port}/v1/chat", "token": token}
    launch_vscode(port, token, cwd, code_bin)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(endpoint) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return endpoint


def read_endpoint(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not data.get("url") or not data.get("token"):
            raise ValueError("unsupported or incomplete endpoint file")
        return data
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"bridge endpoint を読めません: {path}: {exc}") from exc


def _consume_stream(response, on_delta) -> dict:
    """NDJSON の {"delta"} / {"done","model"} / {"error"} を読み、全文とモデル情報を返す。"""
    chunks: list[str] = []
    model = None
    finished = False
    for line in response:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"bridge の応答を解釈できません: {exc}") from exc
        if "error" in event:
            raise RuntimeError(f"bridge error: {event['error']}")
        if "delta" in event:
            chunks.append(event["delta"])
            on_delta(event["delta"])
        if event.get("done"):
            finished = True
            model = event.get("model")
    # done も error も来ずに切れたのは途中終了。成功として履歴へ残さない。
    if not finished:
        raise RuntimeError("応答が途中で切れました（VS Code 側の bridge を確認してください）")
    return {"text": "".join(chunks), "model": model}


def _urlopen(req: urllib.request.Request, timeout: float):
    """bridge へのリクエストを投げ、失敗を利用者向けの RuntimeError へ翻訳する。"""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc).get("error", exc.reason)
        except Exception:
            message = exc.reason
        raise RuntimeError(f"bridge error ({exc.code}): {message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"bridge に接続できません: {exc.reason}") from exc


def request(endpoint: dict[str, object], messages: list[dict[str, str]], family: str | None,
            timeout: float, on_delta=None) -> dict:
    """会話全体を投げて応答を得る。on_delta を渡すと NDJSON ストリームで受け取る。"""
    body: dict[str, object] = {"messages": messages}
    if family:
        body["family"] = family
    if on_delta is not None:
        body["stream"] = True
    req = urllib.request.Request(
        str(endpoint["url"]),
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {endpoint['token']}", "Content-Type": "application/json"},
        method="POST",
    )
    with _urlopen(req, timeout) as response:
        return _consume_stream(response, on_delta) if on_delta is not None else json.load(response)


def _path_url(endpoint: dict[str, object], path: str) -> str:
    parsed = urllib.parse.urlparse(str(endpoint["url"]))
    return urllib.parse.urlunparse(parsed._replace(path=path))


def tools_url(endpoint: dict[str, object]) -> str:
    return _path_url(endpoint, "/v1/tools")


def fetch_tools(endpoint: dict[str, object], timeout: float) -> dict:
    """VS Code に今そのとき登録されているツールを取る（vscode.lm.tools）。

    中身は VS Code のバージョン・設定・入れている MCP サーバで変わるので、
    どのツールを使えるかは推測せずここで実測する。
    """
    req = urllib.request.Request(
        tools_url(endpoint),
        headers={"Authorization": f"Bearer {endpoint['token']}"},
        method="GET",
    )
    with _urlopen(req, timeout) as response:
        return json.load(response)


def call_tool(endpoint: dict[str, object], name: str, tool_input: dict,
              timeout: float) -> dict:
    """ツールを 1 つ呼ぶ（vscode.lm.invokeTool）。

    入力の検証は VS Code 側のスキーマが行う。ここはツールごとの知識を持たない——
    持つと環境差で必ず古くなる。
    """
    req = urllib.request.Request(
        _path_url(endpoint, "/v1/tool"),
        data=json.dumps({"name": name, "input": tool_input}).encode(),
        headers={"Authorization": f"Bearer {endpoint['token']}", "Content-Type": "application/json"},
        method="POST",
    )
    with _urlopen(req, timeout) as response:
        return json.load(response)


def find_tool(payload: dict, name: str) -> dict | None:
    for tool in payload.get("tools") or []:
        if tool.get("name") == name:
            return tool
    return None


def format_tool_schema(tool: dict) -> str:
    lines = [str(tool.get("name"))]
    description = (tool.get("description") or "").strip()
    if description:
        lines += ["", description]
    lines += ["", "inputSchema:",
              json.dumps(tool.get("inputSchema"), ensure_ascii=False, indent=2)]
    return "\n".join(lines)


def parse_tool_input(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"--input は JSON オブジェクトで渡してください: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("--input は JSON オブジェクトで渡してください")
    return value


def format_tools(payload: dict) -> str:
    tools = payload.get("tools") or []
    if not tools:
        return "VS Code に登録されているツールはありません。"
    lines = [f"{len(tools)} 個のツールが VS Code に登録されています。"]
    for tool in sorted(tools, key=lambda t: t.get("name", "")):
        tags = f"  [{' '.join(tool['tags'])}]" if tool.get("tags") else ""
        lines.append(f"  {tool.get('name')}{tags}")
        summary = (tool.get("description") or "").strip().splitlines()
        if summary:
            lines.append(f"      {summary[0]}")
    return "\n".join(lines)


def is_command(line: str) -> bool:
    """行頭 `/` + スラッシュを含まない 1 トークンだけをコマンドとみなす。

    `/home/user/... を説明して` のようなパス始まりの依頼をコマンドと誤認しないため
    （エージェント CLI 契約の skill_command_prefix と同じ見分け方）。
    """
    head = line.strip().split(maxsplit=1)[0]
    return head.startswith("/") and "/" not in head[1:]


class Session:
    """1 つの対話。履歴は拡張ではなく手元に持つ（bridge を再起動しても会話が続く）。"""

    def __init__(self, family: str | None = None):
        self.messages: list[dict[str, str]] = []
        self.family = family

    def command(self, line: str) -> tuple[str, str]:
        """スラッシュコマンドを処理して (action, 表示テキスト) を返す。action は continue|exit。"""
        parts = line.strip().split(maxsplit=1)
        name = parts[0]
        argument = parts[1].strip() if len(parts) > 1 else ""
        if name in ("/exit", "/quit"):
            return "exit", ""
        if name == "/help":
            return "continue", HELP
        if name == "/clear":
            self.messages = []
            return "continue", "会話履歴を消しました。"
        if name == "/model":
            self.family = argument or None
            return "continue", f"model family: {self.family or '(既定)'}"
        return "continue", f"未知のコマンドです: {name}（/help でコマンド一覧）"

    def ask(self, endpoint: dict[str, object], text: str, timeout: float, on_delta) -> dict:
        """1 手番。失敗したターンは履歴に残さない（次の質問が壊れた文脈を引きずらない）。"""
        self.messages.append({"role": "user", "content": text})
        try:
            result = request(endpoint, self.messages, self.family, timeout, on_delta)
            # 空の応答を履歴へ入れると、次の手番が「本文が空の assistant」を送って
            # 400 で弾かれ続ける。失敗として扱い、会話を汚さずに終わらせる。
            if not result["text"].strip():
                raise RuntimeError("モデルが空の応答を返しました")
        except BaseException:
            self.messages.pop()
            raise
        self.messages.append({"role": "assistant", "content": result["text"]})
        return result


def repl(endpoint: dict[str, object], session: Session, timeout: float, stream=sys.stdout) -> int:
    try:
        import readline  # noqa: F401  行編集と履歴のために import するだけ
    except ImportError:
        pass

    def emit(text: str) -> None:
        stream.write(text)
        stream.flush()

    emit("vscode-copilot-chat 対話モード。/help でコマンド一覧、Ctrl-D で終了。\n")
    while True:
        try:
            line = input(PROMPT)
        except EOFError:
            emit("\n")
            return 0
        except KeyboardInterrupt:
            emit("\n")
            continue
        if not line.strip():
            continue
        if is_command(line):
            action, message = session.command(line)
            if action == "exit":
                return 0
            if message:
                emit(f"{message}\n")
            continue
        try:
            session.ask(endpoint, line, timeout, emit)
            emit("\n")
        except KeyboardInterrupt:
            # 接続が切れると拡張側が CancellationToken を落とすので、モデルも止まる。
            emit("\n中断しました。\n")
        except RuntimeError as exc:
            emit(f"\nvscode-copilot-chat: {exc}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="VS Code の Copilot Language Model へ問い合わせます")
    parser.add_argument("prompt", nargs="?", help="依頼。省略時は標準入力から読む")
    parser.add_argument("--family", help="モデル family の選択条件（例: gpt-4o）")
    parser.add_argument("--timeout", type=float, default=300, help="応答待ち秒数（既定: 300）")
    parser.add_argument("--json", action="store_true", help="モデル情報を含む JSON を出力")
    parser.add_argument("--tools", action="store_true",
                        help="VS Code に登録されているツールの一覧を出す（vscode.lm.tools）")
    parser.add_argument("--call", metavar="TOOL",
                        help="ツールを 1 つ呼ぶ。--input を省くと inputSchema を表示する")
    parser.add_argument("--input", metavar="JSON",
                        help="--call へ渡す JSON オブジェクト（- で標準入力から読む）")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="対話モード（端末から起動し prompt を省略したときの既定）")
    parser.add_argument("--start", action="store_true",
                        help="PowerShellから専用Windows VS Codeを現在のディレクトリで起動")
    parser.add_argument("--start-only", action="store_true", help="起動だけ行い問い合わせない")
    parser.add_argument("--port", type=int, default=32190, help="Windows bridge port（既定: 32190）")
    parser.add_argument("--code-bin", default="code", help="Windows側のcode command（既定: code）")
    args = parser.parse_args()
    # 端末から引数なしで起動したら対話。パイプ入力は従来どおり片道実行のまま。
    one_off = args.tools or args.call
    interactive = args.interactive or (args.prompt is None and not one_off and sys.stdin.isatty())
    if interactive and args.json:
        parser.error("--json は対話モードでは使えません")
    try:
        path = endpoint_path()
        endpoint = start_bridge(path, args.port, Path.cwd(), args.code_bin) if args.start else read_endpoint(path)
        if args.start_only:
            print(f"bridge starting: {endpoint['url']}")
            return 0
        if one_off and args.start:
            wait_for_bridge(endpoint, args.timeout)
        if args.tools:
            payload = fetch_tools(endpoint, args.timeout)
            print(json.dumps(payload, ensure_ascii=False) if args.json else format_tools(payload))
            return 0
        if args.call:
            if args.input is None:
                # 何を渡せばよいかは VS Code が持つスキーマが正。まずそれを見せる。
                tool = find_tool(fetch_tools(endpoint, args.timeout), args.call)
                if tool is None:
                    raise RuntimeError(
                        f"VS Code に登録されていないツールです: {args.call}（--tools で一覧）")
                print(json.dumps(tool, ensure_ascii=False) if args.json else format_tool_schema(tool))
                return 0
            tool_input = parse_tool_input(sys.stdin.read() if args.input == "-" else args.input)
            result = call_tool(endpoint, args.call, tool_input, args.timeout)
            print(json.dumps(result, ensure_ascii=False) if args.json else result["text"])
            return 0
        session = Session(args.family)
        if interactive:
            if args.start:
                wait_for_bridge(endpoint, args.timeout)
            return repl(endpoint, session, args.timeout)
        prompt = args.prompt if args.prompt is not None else sys.stdin.read()
        if not prompt.strip():
            parser.error("prompt が空です")
        # VS Codeの初回起動を待つ。接続拒否だけを短く再試行し、モデル/APIエラーは即時返す。
        deadline = time.monotonic() + min(args.timeout, 30) if args.start else 0
        while True:
            try:
                result = session.ask(endpoint, prompt, args.timeout, None)
                break
            except RuntimeError as exc:
                if not args.start or "接続できません" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
    except RuntimeError as exc:
        print(f"vscode-copilot-chat: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False) if args.json else result["text"])
    return 0


def wait_for_bridge(endpoint: dict[str, object], timeout: float, sleep=time.sleep) -> None:
    """--start 直後の対話起動で、VS Code が listen するまで待つ。

    モデルは呼ばない（起動待ちのために課金枠を焼かない）。TCP が繋がった時点で
    拡張は activate 済みなので、以降の失敗は対話の中で見せれば足りる。
    """
    parsed = urllib.parse.urlparse(str(endpoint["url"]))
    address = (parsed.hostname or "127.0.0.1", parsed.port or 80)
    deadline = time.monotonic() + min(timeout, 30)
    while True:
        try:
            with socket.create_connection(address, timeout=2):
                return
        except OSError:
            if time.monotonic() >= deadline:
                return
            sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
