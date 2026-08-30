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
        # CLI は自分の知っている口しか叩かない。404 が返るのは、動いている拡張が
        # その口を持たない古い版だということ。install.sh を流しても、動いている
        # 拡張ホストは古いままなので、閉じるまで入れ替わらない。
        if exc.code == 404:
            raise RuntimeError(
                "bridge の拡張が古いようです（このリクエストの口がありません）。"
                "install.sh を流し直したうえで、bridge の VS Code ウィンドウを"
                "一度閉じてください（動いている bridge は使い回されるので、"
                "閉じるまで新しい拡張が載りません）。") from exc
        raise RuntimeError(f"bridge error ({exc.code}): {message}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise RuntimeError(f"bridge への接続がタイムアウトしました（--timeout {timeout:g}）") from exc
        raise RuntimeError(
            f"bridge に接続できません: {exc.reason}"
            "（VS Code 側が落ちているかもしれません。--no-start を外すと自動で"
            "起こし直します）") from exc
    except TimeoutError as exc:
        # 応答待ちのタイムアウトは URLError に包まれず素で上がってくる。
        raise RuntimeError(
            f"応答が {timeout:g} 秒以内に返りませんでした（--timeout で伸ばせます）") from exc


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


class ToolNeedsChatContext(RuntimeError):
    """chat request の外からは呼べないツール（toolInvocationToken が要る）。"""


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
    try:
        with _urlopen(req, timeout) as response:
            return json.load(response)
    except RuntimeError as exc:
        if "toolInvocationToken" not in str(exc):
            raise
        raise ToolNeedsChatContext(
            f"{name} は chat request の中からしか呼べません（VS Code が"
            " toolInvocationToken を要求します）。このツールは一覧に並んでいても、"
            "チャットの外から呼ぶこの bridge では使えません。"
            "どのツールが同じ制約を持つかは、有効な入力で 1 つずつ呼んで確かめるしか"
            "ありません（空入力で試すのは危険です——VS Code は入力を検証せずツールへ渡します）。"
        ) from exc


def missing_required(tool: dict, tool_input: dict) -> list[str]:
    """スキーマの required のうち、渡していないものを返す。

    **VS Code は入力を検証せずツールへ渡す。** 欠けたまま送るとツール本体が
    `undefined` を掴んで動き、落ちる前に副作用を起こすものがある（実測で
    `copilot_createNewWorkspace` が空入力で実行され、拡張ホストごと落ちた）。
    ツールごとの知識は持たず、VS Code が配るスキーマだけを根拠に手前で止める。
    """
    required = (tool.get("inputSchema") or {}).get("required") or []
    return [key for key in required if key not in tool_input]


def describe_empty_result(result: dict) -> str:
    """テキストが 1 文字も返らなかったときに、何が起きたのかを言う。"""
    parts = result.get("content") or []
    if not parts:
        return "ツールは何も返しませんでした。"
    return (f"テキストの部品がありません（{len(parts)} 個は prompt-tsx などの非テキスト）。"
            "--json で中身を見られます。")


def print_tool_result(result: dict, as_json: bool) -> None:
    """ツールの結果を出す。空のまま黙って終わらない——失敗と見分けが付かないため。"""
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
        return
    text = result.get("text") or ""
    if text:
        print(text)
    else:
        print(describe_empty_result(result), file=sys.stderr)


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


# --agent が既定で持たせるツール。**明示した名前だけを通す allowlist** で、
# VS Code に新しいツールが増えても勝手には入らない——読み取り専用という約束を、
# 名前を数える側ではなく載せる側で守る。書き込み系が要るなら --agent-tools で明示する。
READ_ONLY_TOOLS = (
    "copilot_readFile",
    "copilot_listDirectory",
    "copilot_findFiles",
    "copilot_findTextInFiles",
    "copilot_searchCodebase",
    "copilot_searchWorkspaceSymbols",
    "copilot_readProjectStructure",
    "copilot_findTestFiles",
    "copilot_getChangedFiles",
    "copilot_getErrors",
)

# --agent-tools に書ける短縮名。長い名前を毎回並べるためだけに用途を諦めさせない。
# **どれも allowlist のまま**で、セットに載っている名前しか入りません。
# ここに置かないもの:
#   - copilot_createNewWorkspace … 空入力で実行され、ワークスペースが開いて拡張ホストごと
#     落ちた（実測）。用途が「今のリポジトリで作業する」と噛み合わないので名指しでだけ使う。
#   - runSubagent … toolInvocationToken を要求するのでこの bridge からは呼べない。
#   - MCP サーバの道具 … 環境ごとに違う。カテゴリで括れないので名指しで。
TOOL_SETS = {
    "read": READ_ONLY_TOOLS,
    "write": (
        "copilot_applyPatch",
        "copilot_replaceString",
        "copilot_createFile",
        "copilot_createDirectory",
    ),
    "run": (
        "run_in_terminal",
        "get_terminal_output",
        "runTests",
    ),
    "web": (
        "copilot_fetchWebPage",
    ),
}


def agent_tools(payload: dict, requested: str | None) -> list[str]:
    """使わせるツールを決める。

    `requested` はカンマ区切りで、セット名（TOOL_SETS）とツール名を混ぜて書けます。
    **名指しは失敗させ、セットは実在するものだけ通します**——名前で頼まれたものを黙って
    落とすと「頼んだ道具を使わないエージェント」になり、カテゴリで頼まれたものを
    落とさないと環境差だけで失敗するためです。
    """
    registered = {tool.get("name") for tool in payload.get("tools") or []}
    items = [item.strip() for item in (requested or "read").split(",") if item.strip()]
    names: list[str] = []
    missing: list[str] = []
    for item in items:
        if item in TOOL_SETS:
            names.extend(name for name in TOOL_SETS[item] if name in registered)
            continue
        (names if item in registered else missing).append(item)
    if missing:
        raise RuntimeError(
            f"VS Code に登録されていないツールです: {', '.join(missing)}"
            f"（--tools で一覧、セット名は {'/'.join(TOOL_SETS)}）")
    # 重複は落とし、書かれた順は保つ。同じ道具を 2 回モデルへ見せる意味はない。
    return list(dict.fromkeys(names))


def run_agent(endpoint: dict[str, object], prompt: str, tools: list[str], family: str | None,
              timeout: float, on_event) -> dict:
    """エージェントを 1 回走らせる。往復の途中経過は on_event へ流す。"""
    body: dict[str, object] = {"messages": [{"role": "user", "content": prompt}], "tools": tools}
    if family:
        body["family"] = family
    req = urllib.request.Request(
        _path_url(endpoint, "/v1/agent"),
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {endpoint['token']}", "Content-Type": "application/json"},
        method="POST",
    )
    with _urlopen(req, timeout) as response:
        return _consume_agent_stream(response, on_event)


def _consume_agent_stream(response, on_event) -> dict:
    chunks: list[str] = []
    done = None
    for line in response:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"bridge の応答を解釈できません: {exc}") from exc
        if "error" in event and "tool" not in event:
            raise RuntimeError(f"bridge error: {event['error']}")
        if "delta" in event:
            chunks.append(event["delta"])
        on_event(event)
        if event.get("done"):
            done = event
    if done is None:
        raise RuntimeError("エージェントの応答が途中で切れました")
    return {"text": done.get("text") or "".join(chunks),
            "rounds": done.get("rounds"), "model": done.get("model")}


def format_agent_event(event: dict) -> str | None:
    """途中経過の 1 行。何をしているか見えないまま黙るのが一番困るので出す。"""
    if "tool" in event:
        if event.get("ok"):
            return None  # 成功は呼び出し行だけで足りる
        if "error" in event:
            return f"  ! {event['tool']}: {event['error']}"
        argument = json.dumps(event.get("input") or {}, ensure_ascii=False)
        if len(argument) > 120:
            argument = argument[:117] + "…"
        return f"  → {event['tool']} {argument}"
    if event.get("done"):
        return f"  （{event.get('rounds')} 往復）"
    return None


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

    emit("vscode-copilot 対話モード。/help でコマンド一覧、Ctrl-D で終了。\n")
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
            emit(f"\nvscode-copilot: {exc}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="VS Code の Copilot Language Model へ問い合わせます")
    parser.add_argument("prompt", nargs="?", help="依頼。省略時は標準入力から読む")
    parser.add_argument("--family", help="モデル family の選択条件（例: gpt-4o）")
    parser.add_argument("--timeout", type=float, default=300, help="応答待ち秒数（既定: 300）")
    parser.add_argument("--json", action="store_true", help="モデル情報を含む JSON を出力")
    parser.add_argument("--tools", action="store_true",
                        help="VS Code に登録されているツールの一覧を出す（vscode.lm.tools）")
    parser.add_argument("--agent", metavar="TASK",
                        help="VS Code のツールを使わせながらタスクを解かせる"
                             "（- で標準入力から読む）")
    parser.add_argument("--agent-tools", metavar="NAMES",
                        help="--agent に持たせるツールをカンマ区切りで指定。"
                             "セット名（read/write/run/web）とツール名を混ぜて書ける"
                             "（既定は read）")
    parser.add_argument("--call", metavar="TOOL",
                        help="ツールを 1 つ呼ぶ。--input を省くと inputSchema を表示する")
    parser.add_argument("--input", metavar="JSON",
                        help="--call へ渡す JSON オブジェクト（- で標準入力から読む）")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="対話モード（端末から起動し prompt を省略したときの既定）")
    parser.add_argument("--start", action="store_true",
                        help="（互換のため受け付ける。繋がらなければ既定で自動起動します）")
    parser.add_argument("--no-start", dest="auto_start", action="store_false",
                        help="VS Code を自動で起こさない（落ちていれば失敗させる）")
    parser.add_argument("--start-only", action="store_true", help="起動だけ行い問い合わせない")
    parser.add_argument("--port", type=int, default=32190, help="Windows bridge port（既定: 32190）")
    parser.add_argument("--code-bin", default="code", help="Windows側のcode command（既定: code）")
    args = parser.parse_args()
    # 端末から引数なしで起動したら対話。パイプ入力は従来どおり片道実行のまま。
    one_off = args.tools or args.call or args.agent
    interactive = args.interactive or (args.prompt is None and not one_off and sys.stdin.isatty())
    if interactive and args.json:
        parser.error("--json は対話モードでは使えません")
    try:
        path = endpoint_path()
        endpoint = ensure_bridge(
            path, args.port, Path.cwd(), args.code_bin, args.timeout, args.auto_start,
            notify=lambda: print("VS Code を起動しています…", file=sys.stderr))
        if args.start_only:
            print(f"bridge ready: {endpoint['url']}")
            return 0
        if args.tools:
            payload = fetch_tools(endpoint, args.timeout)
            print(json.dumps(payload, ensure_ascii=False) if args.json else format_tools(payload))
            return 0
        if args.agent:
            prompt = sys.stdin.read() if args.agent == "-" else args.agent
            if not prompt.strip():
                raise RuntimeError("--agent の依頼文が空です")
            tools = agent_tools(fetch_tools(endpoint, args.timeout), args.agent_tools)
            if not tools:
                raise RuntimeError(
                    "使えるツールが 1 つもありません（--tools で一覧、--agent-tools で明示）")
            events: list[dict] = []

            def on_event(event: dict) -> None:
                events.append(event)
                if args.json:
                    return
                line = format_agent_event(event)
                if line:
                    print(line, file=sys.stderr)

            result = run_agent(endpoint, prompt, tools, args.family, args.timeout, on_event)
            if args.json:
                print(json.dumps({**result, "tools": tools, "events": events}, ensure_ascii=False))
            elif result["text"].strip():
                print(result["text"])
            else:
                print("エージェントはテキストを返しませんでした。", file=sys.stderr)
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
            tool = find_tool(fetch_tools(endpoint, args.timeout), args.call)
            if tool is None:
                raise RuntimeError(
                    f"VS Code に登録されていないツールです: {args.call}（--tools で一覧）")
            missing = missing_required(tool, tool_input)
            if missing:
                raise RuntimeError(
                    f"{args.call} の必須項目が足りません: {', '.join(missing)}"
                    f"（--call {args.call} でスキーマを見られます）。"
                    "VS Code は入力を検証せずツールへ渡すので、欠けたままでは送りません。")
            result = call_tool(endpoint, args.call, tool_input, args.timeout)
            print_tool_result(result, args.json)
            return 0
        session = Session(args.family)
        if interactive:
            return repl(endpoint, session, args.timeout)
        prompt = args.prompt if args.prompt is not None else sys.stdin.read()
        if not prompt.strip():
            parser.error("prompt が空です")
        result = session.ask(endpoint, prompt, args.timeout, None)
    except RuntimeError as exc:
        print(f"vscode-copilot: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False) if args.json else result["text"])
    return 0


def bridge_address(endpoint: dict[str, object]) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(str(endpoint["url"]))
    return parsed.hostname or "127.0.0.1", parsed.port or 80


def bridge_is_listening(endpoint: dict[str, object], timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection(bridge_address(endpoint), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_bridge(endpoint: dict[str, object], timeout: float, sleep=time.sleep) -> None:
    """VS Code が listen するまで待つ。

    モデルは呼ばない（起動待ちのために課金枠を焼かない）。TCP が繋がった時点で
    拡張は activate 済みなので、以降の失敗は本来の処理の中で見せれば足りる。
    """
    deadline = time.monotonic() + min(timeout, 30)
    while not bridge_is_listening(endpoint):
        if time.monotonic() >= deadline:
            return
        sleep(0.5)


def ensure_bridge(path: Path, port: int, cwd: Path, code_bin: str, timeout: float,
                  auto_start: bool = True, notify=None) -> dict[str, object]:
    """使える endpoint を返す。落ちていれば VS Code を起こして待つ。

    **既に listen しているなら起こさない。** 同じ user-data-dir で二重に起こすと、
    2 つ目の拡張ホストが同じ port を掴めずエラーになる。
    """
    endpoint = None
    try:
        endpoint = read_endpoint(path)
    except RuntimeError:
        if not auto_start:
            raise
    if endpoint and bridge_is_listening(endpoint):
        return endpoint
    if not auto_start:
        raise RuntimeError(
            "bridge が起動していません（--no-start を外すと自動で起こします）")
    if notify:
        notify()
    endpoint = start_bridge(path, port, cwd, code_bin)
    wait_for_bridge(endpoint, timeout)
    if not bridge_is_listening(endpoint):
        raise RuntimeError(
            "VS Code を起こしましたが bridge へ接続できません。拡張が入っているか"
            "（install.sh）と、port が空いているかを確認してください")
    return endpoint


if __name__ == "__main__":
    raise SystemExit(main())
