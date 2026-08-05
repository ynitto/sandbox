from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from agentcore import agentcli, ollama_adapter


class _Isolated(unittest.TestCase):
    """実ホーム（~/.agents の host.yaml）と開発者の cwd が宣言として漏れないようにする。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._env = {k: os.environ.get(k) for k in
                     ("AGENT_PROJECT_AGENTS_HOME", "HOME", "OLLAMA_HOST", "OLLAMA_TIMEOUT")}
        self.home = self.tmp / "agents-home"
        self.home.mkdir(parents=True, exist_ok=True)
        os.environ["AGENT_PROJECT_AGENTS_HOME"] = str(self.home)
        os.environ["HOME"] = str(self.tmp / "no-home")
        os.environ.pop("OLLAMA_HOST", None)
        os.environ.pop("OLLAMA_TIMEOUT", None)
        self._cwd = os.getcwd()
        os.chdir(self.tmp)          # cwd の host.yaml も探索順に入る（repolocal の規約）
        self.addCleanup(self._restore)

    def _restore(self):
        os.chdir(self._cwd)
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def declare(self, text: str) -> None:
        try:
            import yaml  # noqa: F401 — repolocal は PyYAML が無ければ宣言を読まない
        except ImportError:      # pragma: no cover — PyYAML 無しの環境
            self.skipTest("PyYAML が無いため host.yaml 由来の宣言は読めない")
        (self.home / "agent-project.host.yaml").write_text(text, encoding="utf-8")


class TestOllamaAdapter(_Isolated):
    def test_generate_requests_non_streaming_and_returns_usage(self):
        response = io.BytesIO(json.dumps({
            "response": "ok", "prompt_eval_count": 12, "eval_count": 34,
        }).encode())
        response.__enter__ = lambda self: self
        response.__exit__ = lambda *args: None
        with mock.patch.object(ollama_adapter.urllib.request, "urlopen", return_value=response) as call:
            result = ollama_adapter.generate("qwen3", "hello")
        sent = json.loads(call.call_args.args[0].data)
        self.assertEqual(sent, {"model": "qwen3", "prompt": "hello", "stream": False})
        self.assertEqual(call.call_args.args[0].full_url,
                         "http://127.0.0.1:11434/api/generate")
        self.assertEqual((result["prompt_eval_count"], result["eval_count"]), (12, 34))

    def test_main_separates_model_text_and_usage(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ollama_adapter, "generate", return_value={
                "response": "answer", "prompt_eval_count": 12, "eval_count": 34}), \
                mock.patch.object(ollama_adapter.sys, "stdin", io.StringIO("hello")), \
                redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(ollama_adapter.main(["qwen3"]), 0)
        self.assertEqual(out.getvalue(), "answer")
        self.assertEqual(agentcli.parse_usage(err.getvalue()), (12, 34))


class TestHostResolution(_Isolated):
    """接続先の解決順 --host > $OLLAMA_HOST > host.yaml の ollama.host > 既定。"""

    def test_default_is_localhost(self):
        self.assertEqual(ollama_adapter.resolve_host(), ollama_adapter.DEFAULT_HOST)

    def test_node_declaration_is_used(self):
        self.declare("ollama:\n  host: gpu-box.local:11434\n")
        # scheme 省略も受ける（人が host.yaml へ書く値の揺れを吸収する）
        self.assertEqual(ollama_adapter.resolve_host(), "http://gpu-box.local:11434")

    def test_env_beats_declaration(self):
        self.declare("ollama:\n  host: http://gpu-box.local:11434\n")
        os.environ["OLLAMA_HOST"] = "http://127.0.0.1:9999"
        self.assertEqual(ollama_adapter.resolve_host(), "http://127.0.0.1:9999")

    def test_explicit_flag_beats_env(self):
        os.environ["OLLAMA_HOST"] = "http://127.0.0.1:9999"
        self.assertEqual(ollama_adapter.resolve_host("http://other:11434/"),
                         "http://other:11434")

    def test_broken_declaration_falls_back_to_default(self):
        self.declare("ollama: [not, an, object]\n")
        self.assertEqual(ollama_adapter.resolve_host(), ollama_adapter.DEFAULT_HOST)

    def test_timeout_from_declaration_and_env(self):
        self.declare("ollama:\n  host: http://gpu-box:11434\n  timeout_sec: 900\n")
        self.assertEqual(ollama_adapter.resolve_timeout_sec(), 900.0)
        os.environ["OLLAMA_TIMEOUT"] = "120"
        self.assertEqual(ollama_adapter.resolve_timeout_sec(), 120.0)
        os.environ["OLLAMA_TIMEOUT"] = "0"      # 不正値は既定へ倒す（黙って 0 秒にしない）
        self.assertEqual(ollama_adapter.resolve_timeout_sec(), 900.0)

    def test_generate_targets_declared_host(self):
        self.declare("ollama:\n  host: http://gpu-box:11434\n")
        response = io.BytesIO(json.dumps({"response": "ok"}).encode())
        response.__enter__ = lambda self: self
        response.__exit__ = lambda *args: None
        with mock.patch.object(ollama_adapter.urllib.request, "urlopen",
                               return_value=response) as call:
            ollama_adapter.generate("qwen3", "hello")
        self.assertEqual(call.call_args.args[0].full_url, "http://gpu-box:11434/api/generate")

    def test_connection_error_names_the_host(self):
        self.declare("ollama:\n  host: http://gpu-box:11434\n")
        err = ollama_adapter.urllib.error.URLError("Connection refused")
        with mock.patch.object(ollama_adapter.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as ctx:
                ollama_adapter.generate("qwen3", "hello")
        # 既定の localhost だと思っている人と別 PC を指すノードとで、次にやることが違う
        self.assertIn("http://gpu-box:11434", str(ctx.exception))

    def test_cli_host_flag(self):
        self.assertEqual(ollama_adapter.parse_args(["qwen3"]), ("qwen3", ""))
        self.assertEqual(ollama_adapter.parse_args(["qwen3", "--host", "http://a:1"]),
                         ("qwen3", "http://a:1"))
        self.assertEqual(ollama_adapter.parse_args(["--host=http://a:1", "qwen3"]),
                         ("qwen3", "http://a:1"))
        for bad in ([], ["a", "b"], ["qwen3", "--host"], ["qwen3", "--nope"]):
            with self.assertRaises(ValueError):
                ollama_adapter.parse_args(bad)

    def test_main_rejects_bad_arguments_with_usage(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(ollama_adapter.main([]), 2)
        self.assertIn("usage: agent-ollama <model> [--host <url>]", err.getvalue())


if __name__ == "__main__":
    unittest.main()
