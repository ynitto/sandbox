"""agent-opencode（opencode アダプター）の単体テスト。

見ているのは 3 つ:

1. **本文と usage の分離** — stdout は最終応答だけ・実測トークンは stderr の
   `@agent-usage` 1 行（schemas/agent-cli.schema.json の契約）。ここが崩れると成果物に
   イベントログが混じるか、台帳が推定へ落ちる。
2. **プロバイダの解決** — `--model provider/model` から到達性チェックの相手を決める。
   外すと「別 PC の ollama が落ちている」を実行前に捕まえられない。
3. **設定の読み取り** — opencode は .jsonc も許すので、コメント付き設定から baseURL を
   取れること（取れなければチェックを省くだけで、失敗にはしない）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "agent-opencode.py"

_spec = importlib.util.spec_from_file_location("agent_opencode", _SRC)
adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(adapter)


class _Isolated(unittest.TestCase):
    """実ホームと実 cwd を見に行かせない（設定の探索は cwd を上へ辿るため）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-opencode-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self._cwd = os.getcwd()
        self.addCleanup(os.chdir, self._cwd)
        os.chdir(self.tmp)
        for key in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "AGENT_OPENCODE_PROVIDER",
                    "AGENT_OPENCODE_SKIP_PREFLIGHT", "OPENCODE_BIN"):
            self._unset(key)

    def _unset(self, key: str) -> None:
        old = os.environ.pop(key, None)
        if old is not None:
            self.addCleanup(os.environ.__setitem__, key, old)

    def _setenv(self, key: str, value: str) -> None:
        old = os.environ.get(key)
        os.environ[key] = value
        self.addCleanup(lambda: os.environ.__setitem__(key, old) if old is not None
                        else os.environ.pop(key, None))

    def write_config(self, name: str, text: str) -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        self._setenv("OPENCODE_CONFIG", str(path))
        return path


class TestJsoncAndConfig(_Isolated):
    def test_comments_and_trailing_commas_are_stripped(self):
        text = """{
          // 行コメント。URL の // は消さない
          "model": "ollama/qwen3", /* ブロック */
          "note": "http://example.com/v1",
        }"""
        data = json.loads(adapter._strip_jsonc(text))
        self.assertEqual(data["model"], "ollama/qwen3")
        self.assertEqual(data["note"], "http://example.com/v1")

    def test_base_url_is_read_from_jsonc(self):
        self.write_config("opencode.jsonc", """{
          // 別 PC の ollama
          "provider": {"ollama": {"options": {"baseURL": "http://gpu-pc:11434/v1/"}}},
          "model": "ollama/qwen3"
        }""")
        providers, default_model = adapter.load_providers()
        self.assertEqual(default_model, "ollama/qwen3")
        self.assertEqual(adapter.base_url_of(providers, "ollama"), "http://gpu-pc:11434/v1")

    def test_env_template_in_base_url_is_expanded(self):
        self._setenv("MY_OLLAMA", "http://192.168.1.20:11434/v1")
        self.write_config("opencode.json", json.dumps(
            {"provider": {"ollama": {"options": {"baseURL": "{env:MY_OLLAMA}"}}}}))
        providers, _ = adapter.load_providers()
        self.assertEqual(adapter.base_url_of(providers, "ollama"), "http://192.168.1.20:11434/v1")

    def test_broken_config_does_not_raise(self):
        # 壊れた設定は opencode 自身が「Configuration is invalid」で落ちる。ここは
        # 別の言い方の失敗を作らず、到達性チェックを諦めるだけ。
        self.write_config("opencode.json", "{ this is not json")
        providers, default_model = adapter.load_providers()
        self.assertEqual((providers, default_model), ({}, ""))


class TestProviderResolution(_Isolated):
    def test_model_flag_decides_the_provider(self):
        self.assertEqual(adapter.provider_of(["--model", "vllm/qwen3"], ""), "vllm")
        self.assertEqual(adapter.provider_of(["-m", "vllm/qwen3"], ""), "vllm")
        self.assertEqual(adapter.provider_of(["--model=vllm/qwen3"], ""), "vllm")

    def test_falls_back_to_config_default_then_env(self):
        self.assertEqual(adapter.provider_of([], "lmstudio/qwen3"), "lmstudio")
        self.assertEqual(adapter.provider_of([], ""), "ollama")
        self._setenv("AGENT_OPENCODE_PROVIDER", "vllm")
        self.assertEqual(adapter.provider_of([], "qwen3"), "vllm")

    def test_preflight_is_skipped_without_base_url(self):
        # クラウドのプロバイダ（baseURL を持たない）まで到達性チェックで止めない。
        self.write_config("opencode.json", json.dumps(
            {"provider": {"anthropic": {}}, "model": "anthropic/claude"}))
        ok, message = adapter.preflight([])
        self.assertTrue(ok)
        self.assertIn("到達性チェックを省きます", message)

    def test_unreachable_server_fails_with_env_wording(self):
        self.write_config("opencode.json", json.dumps(
            {"provider": {"ollama": {"options": {"baseURL": "http://127.0.0.1:1/v1"}}},
             "model": "ollama/qwen3"}))
        ok, message = adapter.preflight([])
        self.assertFalse(ok)
        # agents/opencode.json の errors[0]（class=env）が拾う言い回しであること。
        self.assertIn("推論サーバに接続できません", message)


class TestRunAdapter(_Isolated):
    """偽の opencode（JSON イベントを吐くスクリプト）を相手に、本文と usage を確かめる。"""

    def fake_opencode(self, events: "list[dict]", rc: int = 0) -> str:
        path = self.tmp / "fake-opencode"
        payload = json.dumps(events)
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"events = json.loads({payload!r})\n"
            "sys.stdin.read()\n"
            "for e in events:\n"
            "    print(json.dumps(e))\n"
            f"raise SystemExit({rc})\n",
            encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    def run_adapter(self, events, rc=0, args=()):
        binary = self.fake_opencode(events, rc)
        env = dict(os.environ, OPENCODE_BIN=binary, AGENT_OPENCODE_SKIP_PREFLIGHT="1")
        proc = subprocess.run(
            [sys.executable, str(_SRC), *args], input="プロンプト", text=True,
            capture_output=True, env=env, cwd=str(self.tmp))
        return proc

    def test_text_to_stdout_and_usage_to_stderr(self):
        proc = self.run_adapter([
            {"type": "step_start", "part": {"id": "p0"}},
            {"type": "text", "part": {"id": "p1", "text": "本文です"}},
            {"type": "step_finish", "part": {"id": "p2", "tokens": {"input": 100, "output": 20}}},
        ])
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "本文です\n")
        self.assertIn("@agent-usage tokens_in=100 tokens_out=20", proc.stderr)

    def test_tokens_are_summed_across_steps(self):
        proc = self.run_adapter([
            {"type": "text", "part": {"id": "p1", "text": "一段目"}},
            {"type": "step_finish", "part": {"tokens": {"input": 10, "output": 3}}},
            {"type": "text", "part": {"id": "p2", "text": "二段目"}},
            {"type": "step_finish", "part": {"tokens": {"input": 40, "output": 7}}},
        ])
        self.assertEqual(proc.stdout, "一段目\n\n二段目\n")
        self.assertIn("@agent-usage tokens_in=50 tokens_out=10", proc.stderr)

    def test_same_part_id_keeps_the_last_text(self):
        # 逐次更新で同じ part が何度も来ても、本文が重複しない。
        proc = self.run_adapter([
            {"type": "text", "part": {"id": "p1", "text": "途中"}},
            {"type": "text", "part": {"id": "p1", "text": "途中まで書けた全文"}},
        ])
        self.assertEqual(proc.stdout, "途中まで書けた全文\n")

    def test_non_json_lines_go_to_stderr(self):
        binary = self.tmp / "noisy-opencode"
        binary.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.stdin.read()\n"
            "print('進捗らしき行')\n"
            "print('{\"type\": \"text\", \"part\": {\"id\": \"p1\", \"text\": \"本文\"}}')\n",
            encoding="utf-8")
        binary.chmod(0o755)
        env = dict(os.environ, OPENCODE_BIN=str(binary), AGENT_OPENCODE_SKIP_PREFLIGHT="1")
        proc = subprocess.run([sys.executable, str(_SRC)], input="p", text=True,
                              capture_output=True, env=env, cwd=str(self.tmp))
        self.assertEqual(proc.stdout, "本文\n")
        self.assertIn("進捗らしき行", proc.stderr)

    def test_exit_code_is_propagated(self):
        proc = self.run_adapter([{"type": "text", "part": {"id": "p1", "text": "x"}}], rc=3)
        self.assertEqual(proc.returncode, 3)

    def test_missing_binary_is_env_shaped_failure(self):
        env = dict(os.environ, OPENCODE_BIN=str(self.tmp / "does-not-exist"),
                   AGENT_OPENCODE_SKIP_PREFLIGHT="1")
        proc = subprocess.run([sys.executable, str(_SRC)], input="p", text=True,
                              capture_output=True, env=env, cwd=str(self.tmp))
        self.assertEqual(proc.returncode, 127)
        self.assertIn("opencode が見つかりません", proc.stderr)


class TestLoadProfileEnv(unittest.TestCase):
    """複製した環境補完（正典: agentcore/ollama_adapter.py）が同じ振る舞いをすること。"""

    def test_env_completion_matches_canonical_implementation(self):
        with mock.patch.dict(adapter.os.environ):
            for name in ("OLLAMA_HOST", "OLLAMA_API_BASE", "NO_PROXY", "no_proxy"):
                adapter.os.environ.pop(name, None)
            adapter.os.environ["OLLAMA_HOST"] = "http://10.0.0.5:11434"
            adapter.load_profile_env("/nonexistent")
            self.assertEqual(adapter.os.environ["OLLAMA_API_BASE"],
                             "http://10.0.0.5:11434",
                             "設定の {env:OLLAMA_API_BASE} テンプレートが空にならない")
            for var in ("NO_PROXY", "no_proxy"):
                self.assertEqual(adapter.os.environ[var], "10.0.0.5",
                                 "ollama のホストはプロキシ対象から外れる")


class TestBundledDefinition(unittest.TestCase):
    """定義（agents/opencode.json）とアダプターの取り決めがずれていないこと。"""

    def setUp(self):
        root = _HERE.parents[2]
        self.definition = json.loads((root / "agents" / "opencode.json").read_text(encoding="utf-8"))

    def test_definition_calls_the_adapter_over_stdin(self):
        self.assertEqual(self.definition["command"], ["agent-herd", "opencode"])
        self.assertEqual(self.definition["prompt_via"], "stdin")
        self.assertEqual(self.definition["output"], "stdout")

    def test_readonly_is_declared_best_effort(self):
        # --agent plan は edit を拒むが bash は拒まない。enforced と名乗らない。
        self.assertEqual(self.definition["readonly_args"], ["--agent", "plan"])
        self.assertEqual(self.definition["readonly"], "best-effort")

    def test_unreachable_message_is_classified_env(self):
        import re
        _, message = "", "推論サーバに接続できません: http://x/v1 ([Errno 111] Connection refused)"
        hits = [e["class"] for e in self.definition["errors"]
                if re.search(e["match"], message, re.I)]
        self.assertIn("env", hits)


if __name__ == "__main__":
    unittest.main()
