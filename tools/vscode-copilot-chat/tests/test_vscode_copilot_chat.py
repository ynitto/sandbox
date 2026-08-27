import importlib.util
import json
import threading
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


def test_request_uses_bearer_and_returns_response():
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received["authorization"] = self.headers["Authorization"]
            received["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload = json.dumps({"text": "回答", "model": {"id": "test"}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    result = client.request({"url": f"http://127.0.0.1:{server.server_port}/v1/chat", "token": "secret"},
                            "質問", "gpt-test", 2)
    thread.join()
    server.server_close()
    assert result["text"] == "回答"
    assert received == {"authorization": "Bearer secret",
                        "body": {"prompt": "質問", "family": "gpt-test"}}
