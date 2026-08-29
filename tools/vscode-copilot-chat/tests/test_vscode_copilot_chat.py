import importlib.util
import io
import json
import threading
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "vscode-copilot-chat.py"
SPEC = importlib.util.spec_from_file_location("client", SCRIPT)
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)


def test_read_endpoint_validates_protocol(tmp_path):
    endpoint = tmp_path / "endpoint.json"
    endpoint.write_text('{"version":2,"url":"x","token":"y"}')
    try:
        client.read_endpoint(endpoint)
        assert False, "invalid version must fail"
    except RuntimeError as exc:
        assert "endpoint" in str(exc)


def test_start_bridge_knows_port_and_launches_current_windows_directory(tmp_path):
    endpoint_file = tmp_path / "endpoint.json"
    with mock.patch.object(client, "is_wsl", return_value=True), \
         mock.patch.object(client, "windows_path", return_value="C:\\work\\repo"), \
         mock.patch.object(client, "powershell_executable", return_value="powershell.exe"), \
         mock.patch.object(client.subprocess, "Popen") as popen:
        endpoint = client.start_bridge(endpoint_file, 32191, Path("/work/repo"), "code")
    assert endpoint["url"] == "http://127.0.0.1:32191/v1/chat"
    assert json.loads(endpoint_file.read_text())["token"] == endpoint["token"]
    encoded = popen.call_args.args[0][-1]
    import base64
    command = base64.b64decode(encoded).decode("utf-16le")
    assert "VSCODE_COPILOT_BRIDGE_PORT='32191'" in command
    assert "code' --user-data-dir" in command
    assert "C:\\work\\repo" in command


# --- 会話（案1: multi-turn） ---------------------------------------------------


def _serve_once(payload: bytes, content_type: str, captured: dict):
    """1 リクエストだけ受ける HTTP サーバを起こし、(port, join) を返す。"""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            captured["authorization"] = self.headers["Authorization"]
            captured["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    return server, thread


def test_request_sends_full_history_and_returns_response():
    captured = {}
    payload = json.dumps({"text": "回答", "model": {"id": "test"}}).encode()
    server, thread = _serve_once(payload, "application/json", captured)
    messages = [{"role": "user", "content": "質問"},
                {"role": "assistant", "content": "前の回答"},
                {"role": "user", "content": "続き"}]
    result = client.request({"url": f"http://127.0.0.1:{server.server_port}/v1/chat", "token": "secret"},
                            messages, "gpt-test", 2)
    thread.join()
    server.server_close()
    assert result["text"] == "回答"
    assert captured["authorization"] == "Bearer secret"
    # 履歴は丸ごと送る。拡張側に状態を持たせない設計の要。
    assert captured["body"] == {"messages": messages, "family": "gpt-test"}
    assert "stream" not in captured["body"]


def test_request_streams_ndjson_and_reports_deltas():
    captured = {}
    payload = (b'{"delta":"\xe3\x81\x93"}\n'
               b'{"delta":"\xe3\x82\x93"}\n'
               b'{"done":true,"model":{"id":"test"}}\n')
    server, thread = _serve_once(payload, "application/x-ndjson", captured)
    seen = []
    result = client.request({"url": f"http://127.0.0.1:{server.server_port}/v1/chat", "token": "s"},
                            [{"role": "user", "content": "q"}], None, 2, seen.append)
    thread.join()
    server.server_close()
    assert captured["body"]["stream"] is True
    assert seen == ["こ", "ん"]
    assert result == {"text": "こん", "model": {"id": "test"}}


def test_stream_error_event_becomes_a_runtime_error():
    captured = {}
    payload = b'{"delta":"partial"}\n{"error":"model exploded"}\n'
    server, thread = _serve_once(payload, "application/x-ndjson", captured)
    try:
        client.request({"url": f"http://127.0.0.1:{server.server_port}/v1/chat", "token": "s"},
                       [{"role": "user", "content": "q"}], None, 2, lambda _: None)
        assert False, "stream error must raise"
    except RuntimeError as exc:
        assert "model exploded" in str(exc)
    finally:
        thread.join()
        server.server_close()


class _FakeAsk:
    """request の代役。呼ばれた messages を記録し、決めた応答か例外を返す。"""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, endpoint, messages, family, timeout, on_delta=None):
        self.calls.append({"messages": [dict(m) for m in messages], "family": family})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if on_delta:
            on_delta(reply)
        return {"text": reply, "model": {"id": "test"}}


def test_session_accumulates_both_roles():
    session = client.Session()
    ask = _FakeAsk("A1", "A2")
    with mock.patch.object(client, "request", ask):
        session.ask({}, "Q1", 1, None)
        session.ask({}, "Q2", 1, None)
    assert ask.calls[1]["messages"] == [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "Q2"},
    ]
    assert len(session.messages) == 4


def test_failed_turn_is_not_kept_in_history():
    session = client.Session()
    ask = _FakeAsk(RuntimeError("bridge に接続できません"), "A1")
    with mock.patch.object(client, "request", ask):
        try:
            session.ask({}, "Q1", 1, None)
            assert False, "failure must propagate"
        except RuntimeError:
            pass
        session.ask({}, "Q2", 1, None)
    # 壊れたターンを引きずらない: 2 回目は Q2 だけを送る。
    assert ask.calls[1]["messages"] == [{"role": "user", "content": "Q2"}]


def test_paths_are_not_mistaken_for_commands():
    assert client.is_command("/clear")
    assert client.is_command("/model gpt-4o")
    assert client.is_command("/nope")
    # パス始まりの依頼はモデルへ送る。
    assert not client.is_command("/home/user/sandbox を説明して")
    assert not client.is_command("/etc/hosts")
    assert not client.is_command("要約して")


def test_truncated_stream_is_an_error():
    captured = {}
    payload = b'{"delta":"partial"}\n'  # done も error も来ないまま切れる
    server, thread = _serve_once(payload, "application/x-ndjson", captured)
    try:
        client.request({"url": f"http://127.0.0.1:{server.server_port}/v1/chat", "token": "s"},
                       [{"role": "user", "content": "q"}], None, 2, lambda _: None)
        assert False, "truncated stream must raise"
    except RuntimeError as exc:
        assert "途中で切れました" in str(exc)
    finally:
        thread.join()
        server.server_close()


def test_repl_sends_path_like_input_to_the_model():
    code, _, _, ask = _run_repl(["/home/user/sandbox を説明して", EOFError()], ["A1"])
    assert code == 0
    assert ask.calls[0]["messages"][-1]["content"] == "/home/user/sandbox を説明して"


def test_empty_answer_does_not_poison_the_history():
    session = client.Session()
    ask = _FakeAsk("", "A1")
    with mock.patch.object(client, "request", ask):
        try:
            session.ask({}, "Q1", 1, None)
            assert False, "empty answer must fail the turn"
        except RuntimeError as exc:
            assert "空の応答" in str(exc)
        session.ask({}, "Q2", 1, None)
    assert ask.calls[1]["messages"] == [{"role": "user", "content": "Q2"}]


def test_slash_command_accepts_tab_separated_argument():
    session = client.Session()
    session.command("/model\tgpt-4o")
    assert session.family == "gpt-4o"


def test_slash_commands_clear_switch_model_and_exit():
    session = client.Session()
    session.messages = [{"role": "user", "content": "old"}]
    assert session.command("/clear")[0] == "continue"
    assert session.messages == []
    assert session.command("/model gpt-4o")[0] == "continue"
    assert session.family == "gpt-4o"
    assert session.command("/model")[0] == "continue"
    assert session.family is None
    assert session.command("/exit")[0] == "exit"
    assert session.command("/quit")[0] == "exit"
    action, message = session.command("/nope")
    assert action == "continue" and "未知のコマンド" in message


# --- 対話ループ（案1: REPL） ---------------------------------------------------


def _run_repl(lines, replies):
    session = client.Session()
    ask = _FakeAsk(*replies)
    out = io.StringIO()
    with mock.patch.object(client, "request", ask), \
         mock.patch.object(client, "input", create=True, side_effect=lines):
        code = client.repl({}, session, 1, out)
    return code, out.getvalue(), session, ask


def test_repl_streams_answers_and_keeps_context():
    code, output, session, ask = _run_repl(["Q1", "Q2", EOFError()], ["A1", "A2"])
    assert code == 0
    assert "A1" in output and "A2" in output
    assert [m["content"] for m in session.messages] == ["Q1", "A1", "Q2", "A2"]


def test_repl_clear_starts_a_new_conversation():
    code, _, session, ask = _run_repl(["Q1", "/clear", "Q2", EOFError()], ["A1", "A2"])
    assert code == 0
    assert ask.calls[1]["messages"] == [{"role": "user", "content": "Q2"}]


def test_repl_exit_command_stops_the_loop():
    code, _, _, ask = _run_repl(["/exit", "never asked"], ["A1"])
    assert code == 0
    assert ask.calls == []


def test_repl_skips_blank_lines():
    code, _, _, ask = _run_repl(["", "   ", EOFError()], [])
    assert code == 0 and ask.calls == []


def test_repl_survives_bridge_errors_and_interrupts():
    code, output, session, _ = _run_repl(
        ["Q1", "Q2", EOFError()],
        [RuntimeError("bridge に接続できません"), KeyboardInterrupt()])
    assert code == 0
    assert "bridge に接続できません" in output
    assert "中断しました" in output
    # どちらの失敗も履歴を汚さない。
    assert session.messages == []


def test_repl_ctrl_c_at_the_prompt_does_not_exit():
    code, _, _, ask = _run_repl([KeyboardInterrupt(), "Q1", EOFError()], ["A1"])
    assert code == 0
    assert [c["messages"][-1]["content"] for c in ask.calls] == ["Q1"]


# --- 起動モードの選択 ----------------------------------------------------------


def _main_with(argv, isatty):
    stdin = mock.Mock()
    stdin.isatty.return_value = isatty
    stdin.read.return_value = "piped prompt"
    with mock.patch.object(client.sys, "argv", ["vscode-copilot-chat", *argv]), \
         mock.patch.object(client.sys, "stdin", stdin), \
         mock.patch.object(client, "read_endpoint", return_value={"url": "u", "token": "t"}), \
         mock.patch.object(client, "repl", return_value=0) as repl, \
         mock.patch.object(client, "request", _FakeAsk("A1")) as request:
        code = client.main()
    return code, repl, request


def test_tty_without_prompt_enters_the_repl():
    code, repl, _ = _main_with([], isatty=True)
    assert code == 0 and repl.called


def test_piped_stdin_stays_one_shot():
    code, repl, _ = _main_with([], isatty=False)
    assert code == 0 and not repl.called


def test_prompt_argument_stays_one_shot_even_on_a_tty():
    code, repl, _ = _main_with(["質問"], isatty=True)
    assert code == 0 and not repl.called


def test_interactive_flag_forces_the_repl_even_when_piped():
    code, repl, _ = _main_with(["--interactive"], isatty=False)
    assert code == 0 and repl.called


# --- 起動待ち ------------------------------------------------------------------


def test_wait_for_bridge_polls_tcp_without_calling_the_model():
    calls = []

    def create_connection(address, timeout=None):
        calls.append(address)
        if len(calls) < 3:
            raise OSError("refused")
        return mock.MagicMock(__enter__=lambda s: s, __exit__=lambda *a: False)

    with mock.patch.object(client.socket, "create_connection", create_connection), \
         mock.patch.object(client, "request", side_effect=AssertionError("model must not be called")):
        client.wait_for_bridge({"url": "http://127.0.0.1:32190/v1/chat"}, 30, sleep=lambda _: None)
    assert calls == [("127.0.0.1", 32190)] * 3


# --- ツール一覧（案3 の下見） ---------------------------------------------------


def test_tools_url_is_derived_from_the_chat_endpoint():
    assert client.tools_url({"url": "http://127.0.0.1:32190/v1/chat"}) == \
        "http://127.0.0.1:32190/v1/tools"


def test_fetch_tools_uses_get_with_bearer():
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured["path"] = self.path
            captured["authorization"] = self.headers["Authorization"]
            payload = json.dumps({"tools": [{"name": "run_in_terminal", "description": "run",
                                             "tags": [], "inputSchema": {}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    result = client.fetch_tools({"url": f"http://127.0.0.1:{server.server_port}/v1/chat",
                                 "token": "secret"}, 2)
    thread.join()
    server.server_close()
    assert captured == {"path": "/v1/tools", "authorization": "Bearer secret"}
    assert result["tools"][0]["name"] == "run_in_terminal"


def test_format_tools_lists_names_tags_and_first_description_line():
    text = client.format_tools({"tools": [
        {"name": "copilot_readFile", "description": "Read a file.\n（2 行目は出さない）",
         "tags": ["vscode_codesearch"]},
        {"name": "run_in_terminal", "description": "Run a command.", "tags": []},
    ]})
    assert "2 個のツール" in text
    assert "copilot_readFile  [vscode_codesearch]" in text
    assert "Read a file." in text
    assert "2 行目は出さない" not in text
    # 名前順に並ぶ。
    assert text.index("copilot_readFile") < text.index("run_in_terminal")


def test_format_tools_says_so_when_empty():
    assert "ツールはありません" in client.format_tools({"tools": []})


def test_tools_flag_does_not_enter_the_repl_on_a_tty():
    stdin = mock.Mock()
    stdin.isatty.return_value = True
    with mock.patch.object(client.sys, "argv", ["vscode-copilot-chat", "--tools"]), \
         mock.patch.object(client.sys, "stdin", stdin), \
         mock.patch.object(client, "read_endpoint", return_value={"url": "u", "token": "t"}), \
         mock.patch.object(client, "repl", return_value=0) as repl, \
         mock.patch.object(client, "fetch_tools", return_value={"tools": []}) as fetch:
        code = client.main()
    assert code == 0 and fetch.called and not repl.called


# --- ツールを呼ぶ（案3: ループを VS Code に投げる） -----------------------------


def test_call_tool_posts_name_and_input():
    captured = {}
    payload = json.dumps({"content": [{"type": "text", "value": "done"}], "text": "done"}).encode()
    server, thread = _serve_once(payload, "application/json", captured)
    result = client.call_tool({"url": f"http://127.0.0.1:{server.server_port}/v1/chat",
                               "token": "secret"}, "runSubagent", {"prompt": "やって"}, 2)
    thread.join()
    server.server_close()
    assert captured["authorization"] == "Bearer secret"
    assert captured["body"] == {"name": "runSubagent", "input": {"prompt": "やって"}}
    assert result["text"] == "done"


def test_find_tool_returns_none_when_absent():
    payload = {"tools": [{"name": "run_in_terminal"}]}
    assert client.find_tool(payload, "run_in_terminal")["name"] == "run_in_terminal"
    assert client.find_tool(payload, "runSubagent") is None
    assert client.find_tool({}, "anything") is None


def test_format_tool_schema_shows_the_schema_verbatim():
    text = client.format_tool_schema({
        "name": "runSubagent",
        "description": "Run a subagent.",
        "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}}},
    })
    assert text.startswith("runSubagent")
    assert "Run a subagent." in text
    assert '"prompt"' in text and "inputSchema:" in text


def test_parse_tool_input_rejects_non_objects():
    assert client.parse_tool_input('{"a": 1}') == {"a": 1}
    for bad in ("[1,2]", '"text"', "{", "null"):
        try:
            client.parse_tool_input(bad)
            assert False, f"must reject {bad}"
        except RuntimeError as exc:
            assert "JSON オブジェクト" in str(exc)


def _main_call(argv, *, tools=None, call_result=None):
    stdin = mock.Mock()
    stdin.isatty.return_value = True
    stdin.read.return_value = '{"prompt": "stdin から"}'
    with mock.patch.object(client.sys, "argv", ["vscode-copilot-chat", *argv]), \
         mock.patch.object(client.sys, "stdin", stdin), \
         mock.patch.object(client, "read_endpoint", return_value={"url": "u", "token": "t"}), \
         mock.patch.object(client, "repl", return_value=0) as repl, \
         mock.patch.object(client, "fetch_tools", return_value=tools or {"tools": []}) as fetch, \
         mock.patch.object(client, "call_tool", return_value=call_result or {"text": "ok"}) as call:
        code = client.main()
    return code, repl, fetch, call


def test_call_without_input_shows_the_schema_and_does_not_invoke():
    tools = {"tools": [{"name": "runSubagent", "description": "d", "inputSchema": {"type": "object"}}]}
    code, repl, fetch, call = _main_call(["--call", "runSubagent"], tools=tools)
    assert code == 0 and fetch.called and not call.called and not repl.called


SUBAGENT_ONE_REQUIRED = {"tools": [
    {"name": "runSubagent", "inputSchema": {"required": ["prompt"]}}]}


def test_call_with_input_invokes_the_tool():
    code, repl, fetch, call = _main_call(["--call", "runSubagent", "--input", '{"prompt": "x"}'],
                                         tools=SUBAGENT_ONE_REQUIRED)
    assert code == 0 and call.called and not repl.called
    assert call.call_args.args[1:3] == ("runSubagent", {"prompt": "x"})


def test_call_reads_input_from_stdin_with_a_dash():
    code, _, _, call = _main_call(["--call", "runSubagent", "--input", "-"],
                                  tools=SUBAGENT_ONE_REQUIRED)
    assert code == 0
    assert call.call_args.args[2] == {"prompt": "stdin から"}


def test_call_refuses_to_send_an_input_missing_required_fields():
    """VS Code は検証せずツールへ渡すので、欠けた入力は手前で止める。"""
    tools = {"tools": [{"name": "copilot_readFile",
                        "inputSchema": {"required": ["filePath", "startLine", "endLine"]}}]}
    code, _, _, call = _main_call(
        ["--call", "copilot_readFile", "--input", '{"filePath": "/tmp/x"}'], tools=tools)
    assert code == 1
    assert not call.called


def test_call_on_an_unregistered_tool_with_input_does_not_send():
    code, _, _, call = _main_call(["--call", "nope", "--input", "{}"], tools={"tools": []})
    assert code == 1 and not call.called


def test_missing_required_lists_only_what_is_absent():
    tool = {"inputSchema": {"required": ["a", "b", "c"]}}
    assert client.missing_required(tool, {"a": 1, "c": 3}) == ["b"]
    assert client.missing_required(tool, {"a": 1, "b": 2, "c": 3}) == []
    # required が無い・スキーマが無いツールは素通し（こちらで足さない）。
    assert client.missing_required({"inputSchema": {}}, {}) == []
    assert client.missing_required({}, {}) == []


def test_connection_refused_points_at_start():
    error = client.urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    with mock.patch.object(client.urllib.request, "urlopen", side_effect=error):
        try:
            client._urlopen(mock.Mock(), 5)
            assert False, "must raise"
        except RuntimeError as exc:
            assert "--start" in str(exc)


def test_call_on_an_unregistered_tool_fails_with_a_hint():
    code, _, _, call = _main_call(["--call", "nope"])
    assert code == 1 and not call.called


# --- 起動先の切り替え（WSL / macOS・Linux） -------------------------------------


def test_is_wsl_needs_both_windows_tools():
    def which(name, table):
        return table.get(name)

    for table, expected in [
        ({"powershell.exe": "/p", "wslpath": "/w"}, True),
        ({"powershell.exe": "/p"}, False),   # wslpath が無い
        ({"wslpath": "/w"}, False),          # PowerShell が無い（素の Linux）
        ({}, False),                         # macOS
    ]:
        with mock.patch.object(client.shutil, "which", lambda n, t=table: which(n, t)):
            assert client.is_wsl() is expected


def test_start_bridge_launches_local_vscode_when_not_on_wsl(tmp_path):
    endpoint_file = tmp_path / "endpoint.json"
    with mock.patch.object(client, "is_wsl", return_value=False), \
         mock.patch.object(client, "resolve_code_bin", return_value="/usr/local/bin/code"), \
         mock.patch.object(client, "user_data_dir", return_value=tmp_path / "user-data"), \
         mock.patch.object(client.subprocess, "Popen") as popen:
        endpoint = client.start_bridge(endpoint_file, 32195, Path("/Users/me/repo"), "code")
    assert endpoint["url"] == "http://127.0.0.1:32195/v1/chat"
    argv, kwargs = popen.call_args.args[0], popen.call_args.kwargs
    assert argv == ["/usr/local/bin/code", "--user-data-dir", str(tmp_path / "user-data"),
                    "--new-window", "/Users/me/repo"]
    # port/token は env で渡す。専用 user-data-dir なので既存ウィンドウに吸われない。
    assert kwargs["env"]["VSCODE_COPILOT_BRIDGE_PORT"] == "32195"
    assert kwargs["env"]["VSCODE_COPILOT_BRIDGE_TOKEN"] == endpoint["token"]
    assert (tmp_path / "user-data").is_dir()


def test_local_launch_rejects_a_bad_port(tmp_path):
    with mock.patch.object(client, "resolve_code_bin", return_value="/bin/code"):
        try:
            client.launch_local_vscode(0, "t", tmp_path, "code")
            assert False, "port 0 must be rejected"
        except RuntimeError as exc:
            assert "port" in str(exc)


def test_resolve_code_bin_prefers_path():
    with mock.patch.object(client.shutil, "which", return_value="/opt/bin/code"):
        assert client.resolve_code_bin("code") == "/opt/bin/code"


def test_resolve_code_bin_falls_back_to_the_mac_app_bundle(tmp_path):
    bundle = tmp_path / "Visual Studio Code.app/Contents/Resources/app/bin/code"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("#!/bin/sh\n")
    bundle.chmod(0o755)
    with mock.patch.object(client.shutil, "which", return_value=None), \
         mock.patch.object(client, "MAC_CODE_PATHS", (str(bundle),)):
        assert client.resolve_code_bin("code") == str(bundle)


def test_resolve_code_bin_explains_how_to_fix_it():
    with mock.patch.object(client.shutil, "which", return_value=None), \
         mock.patch.object(client, "MAC_CODE_PATHS", ()):
        try:
            client.resolve_code_bin("code")
            assert False, "missing code must fail"
        except RuntimeError as exc:
            assert "--code-bin" in str(exc)


def test_explicit_code_bin_is_not_second_guessed(tmp_path):
    """--code-bin を打った人には、当てずっぽうの候補ではなくその指定の失敗を返す。"""
    with mock.patch.object(client.shutil, "which", return_value=None), \
         mock.patch.object(client, "MAC_CODE_PATHS", ("/should/not/be/used",)):
        try:
            client.resolve_code_bin("/my/code")
            assert False, "must fail"
        except RuntimeError as exc:
            assert "/my/code" in str(exc)


# --- エージェントへ丸投げする（案3 の入口） -------------------------------------


REAL_SUBAGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string"},
        "description": {"type": "string"},
        "agentName": {"type": "string"},
        "model": {"type": "string"},
    },
    "required": ["prompt", "description"],
}
REAL_SUBAGENT_TOOL = {"name": "runSubagent", "description": "Run a subagent.",
                      "inputSchema": REAL_SUBAGENT_SCHEMA}


def test_agent_input_fills_both_required_fields():
    payload = client.agent_input("テストを直して", None, None)
    assert payload == {"prompt": "テストを直して", "description": "テストを直して"}
    client.check_agent_input(REAL_SUBAGENT_TOOL, payload)


def test_description_is_derived_without_calling_the_model():
    long = "一行目です。\n二行目です。" + "あ" * 100
    payload = client.agent_input(long, None, None)
    assert len(payload["description"]) == 40
    assert "\n" not in payload["description"]
    # 空白だけの依頼でも空の description は作らない（required を満たせなくなる）。
    assert client.agent_input("   ", None, None)["description"] == "task"


def test_explicit_description_and_agent_name_win():
    payload = client.agent_input("やって", "fix the tests", "Explore")
    assert payload == {"prompt": "やって", "description": "fix the tests", "agentName": "Explore"}
    client.check_agent_input(REAL_SUBAGENT_TOOL, payload)


def test_agent_name_is_omitted_when_not_given():
    assert "agentName" not in client.agent_input("やって", None, None)


def test_check_agent_input_catches_a_new_required_field():
    """スキーマが増えたら黙って壊れた入力を送らず、--call へ案内する。"""
    tool = {"inputSchema": {**REAL_SUBAGENT_SCHEMA, "required": ["prompt", "description", "cwd"]}}
    try:
        client.check_agent_input(tool, client.agent_input("やって", None, None))
        assert False, "a new required field must be reported"
    except RuntimeError as exc:
        assert "cwd" in str(exc) and "--call" in str(exc)


def test_check_agent_input_catches_a_renamed_field():
    tool = {"inputSchema": {"properties": {"prompt": {}, "summary": {}},
                            "required": ["prompt"]}}
    try:
        client.check_agent_input(tool, client.agent_input("やって", None, None))
        assert False, "a renamed field must be reported"
    except RuntimeError as exc:
        assert "description" in str(exc) and "--call" in str(exc)


def test_check_agent_input_accepts_a_schema_without_properties():
    client.check_agent_input({"inputSchema": {}}, client.agent_input("やって", None, None))


def _main_agent(argv, *, tools, stdin_text=""):
    stdin = mock.Mock()
    stdin.isatty.return_value = True
    stdin.read.return_value = stdin_text
    with mock.patch.object(client.sys, "argv", ["vscode-copilot-chat", *argv]), \
         mock.patch.object(client.sys, "stdin", stdin), \
         mock.patch.object(client, "read_endpoint", return_value={"url": "u", "token": "t"}), \
         mock.patch.object(client, "repl", return_value=0) as repl, \
         mock.patch.object(client, "fetch_tools", return_value=tools), \
         mock.patch.object(client, "call_tool", return_value={"text": "done"}) as call:
        code = client.main()
    return code, repl, call


def test_agent_invokes_run_subagent():
    code, repl, call = _main_agent(["--agent", "テストを直して"],
                                   tools={"tools": [REAL_SUBAGENT_TOOL]})
    assert code == 0 and not repl.called
    assert call.call_args.args[1] == "runSubagent"
    assert call.call_args.args[2] == {"prompt": "テストを直して", "description": "テストを直して"}


def test_agent_reads_the_task_from_stdin_with_a_dash():
    code, _, call = _main_agent(["--agent", "-"], tools={"tools": [REAL_SUBAGENT_TOOL]},
                                stdin_text="長い依頼文")
    assert code == 0
    assert call.call_args.args[2]["prompt"] == "長い依頼文"


def test_agent_passes_the_agent_name_through():
    code, _, call = _main_agent(["--agent", "調べて", "--agent-name", "Explore"],
                                tools={"tools": [REAL_SUBAGENT_TOOL]})
    assert code == 0
    assert call.call_args.args[2]["agentName"] == "Explore"


def test_agent_says_so_when_the_tool_is_absent():
    code, _, call = _main_agent(["--agent", "やって"], tools={"tools": []})
    assert code == 1 and not call.called


def test_agent_refuses_an_empty_task():
    code, _, call = _main_agent(["--agent", "   "], tools={"tools": [REAL_SUBAGENT_TOOL]})
    assert code == 1 and not call.called


def test_agent_does_not_invoke_when_the_schema_moved():
    moved = {"name": "runSubagent",
             "inputSchema": {"properties": {"prompt": {}}, "required": ["prompt", "cwd"]}}
    code, _, call = _main_agent(["--agent", "やって"], tools={"tools": [moved]})
    assert code == 1 and not call.called


# --- タイムアウトの伝え方 -------------------------------------------------------


def test_read_timeout_points_at_the_timeout_flag():
    with mock.patch.object(client.urllib.request, "urlopen", side_effect=TimeoutError()):
        try:
            client._urlopen(mock.Mock(), 12)
            assert False, "timeout must raise"
        except RuntimeError as exc:
            assert "12 秒" in str(exc) and "--timeout" in str(exc)


def test_connect_timeout_is_reported_as_a_timeout_too():
    error = client.urllib.error.URLError(TimeoutError())
    with mock.patch.object(client.urllib.request, "urlopen", side_effect=error):
        try:
            client._urlopen(mock.Mock(), 5)
            assert False, "timeout must raise"
        except RuntimeError as exc:
            assert "タイムアウト" in str(exc)


# --- chat の外から呼べないツール ------------------------------------------------


def _serve_error(status: int, message: str, captured: dict):
    payload = json.dumps({"error": message}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            captured["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    return server, thread


def test_gated_tool_gets_an_explanation_not_a_raw_500():
    captured = {}
    server, thread = _serve_error(500, "toolInvocationToken is required for this tool", captured)
    try:
        client.call_tool({"url": f"http://127.0.0.1:{server.server_port}/v1/chat", "token": "s"},
                         "runSubagent", {"prompt": "p", "description": "d"}, 2)
        assert False, "must raise"
    except RuntimeError as exc:
        assert "runSubagent" in str(exc)
        assert "chat request の中からしか呼べません" in str(exc)
    finally:
        thread.join()
        server.server_close()


def test_other_tool_errors_are_passed_through_unchanged():
    captured = {}
    server, thread = _serve_error(500, "something else broke", captured)
    try:
        client.call_tool({"url": f"http://127.0.0.1:{server.server_port}/v1/chat", "token": "s"},
                         "copilot_readFile", {}, 2)
        assert False, "must raise"
    except RuntimeError as exc:
        assert "something else broke" in str(exc)
        assert "chat request" not in str(exc)
    finally:
        thread.join()
        server.server_close()


# --- テキストを返さないツール ---------------------------------------------------


def test_empty_text_is_explained_instead_of_printing_nothing(capsys):
    """成功したのに標準出力が空だと、失敗と見分けが付かない。"""
    client.print_tool_result({"content": [{"type": "other"}, {"type": "other"}], "text": ""}, False)
    out, err = capsys.readouterr()
    assert out == ""
    assert "2 個" in err and "--json" in err


def test_no_content_at_all_says_so(capsys):
    client.print_tool_result({"content": [], "text": ""}, False)
    out, err = capsys.readouterr()
    assert out == "" and "何も返しませんでした" in err


def test_text_is_printed_plainly_with_no_note(capsys):
    client.print_tool_result({"content": [{"type": "text", "value": "本文"}], "text": "本文"}, False)
    out, err = capsys.readouterr()
    assert out == "本文\n" and err == ""


def test_json_output_carries_the_non_text_parts(capsys):
    result = {"content": [{"type": "other", "value": {"node": "prompt-tsx"}}], "text": ""}
    client.print_tool_result(result, True)
    out, err = capsys.readouterr()
    assert json.loads(out) == result
    assert err == ""
