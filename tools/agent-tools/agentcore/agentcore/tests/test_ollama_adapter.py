from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from agentcore import agentcli, ollama_adapter


class TestOllamaAdapter(unittest.TestCase):
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
