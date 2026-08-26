"""制御応答（ollama-json 等）の一時障害リトライの回帰。

定義（agents/<name>.json の errors[]）が transient と分類した失敗は、hint が本文を
差し替えてメッセージから一時障害と読めなくなっても再試行される。実測の発端は
aider+gemma4:e4b: ollama が他リクエストで塞がると ollama-json が connect の
StallError（transient 分類）で落ち、hint 置換後の文言が _TL_TRANSIENT_RE に
掛からず一発で実行ごと失敗していた。
"""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agentcore.harness import toolloop as tl  # noqa: E402
from agentcore.tests.harnesspatch import patch_harness  # noqa: E402


class ControlRetryTests(unittest.TestCase):
    def test_spec_classified_transient_is_retried_even_if_hint_hides_it(self):
        calls = []

        def flaky(agent, prompt, **kw):
            calls.append(1)
            if len(calls) == 1:
                # hint 置換後の文言（「タイムアウト」「接続」等を含まない）
                raise tl.ToolLoopError(
                    "無進捗で打ち切りました（生成が進まなくなった状態）。",
                    transient=True)
            return '{"type":"final","output":"done"}'

        with tempfile.TemporaryDirectory() as tmp, \
                patch_harness("_tl_run_agent", side_effect=flaky):
            out = tl._tl_run_control({"cli": "x"}, "p", cwd=tmp, read_files=[],
                                     log_file=os.path.join(tmp, "x.jsonl"))
        self.assertEqual(len(calls), 2)
        self.assertIn("final", out)

    def test_non_transient_error_is_not_retried(self):
        calls = []

        def broken(agent, prompt, **kw):
            calls.append(1)
            raise tl.ToolLoopError("スキルが見つかりません: x")

        with tempfile.TemporaryDirectory() as tmp, \
                patch_harness("_tl_run_agent", side_effect=broken):
            with self.assertRaises(tl.ToolLoopError):
                tl._tl_run_control({"cli": "x"}, "p", cwd=tmp, read_files=[],
                                   log_file=os.path.join(tmp, "x.jsonl"))
        self.assertEqual(len(calls), 1)

    def test_run_agent_carries_the_spec_class_onto_the_error(self):
        fake_mod = types.SimpleNamespace(
            headless_cmd=lambda *a, **k: {"argv": ["x"], "timeout": 1},
            classify_error=lambda spec, blob, detailed=False, now=None: {
                "class": "transient", "hint": "リトライで解けることが多い",
                "quota_kind": None, "reset_at": None},
        )
        agent = {"cli": "x", "model": "", "spec": {}, "agentcli": fake_mod}
        failed = {"status": 1, "stdout": "",
                  "stderr": "応答が停止しました: connect のまま 120 秒無進捗", "error": ""}
        with tempfile.TemporaryDirectory() as tmp, \
                patch_harness("_tl_exec_argv", return_value=failed), \
                patch_harness("_tl_record_usage"):
            with self.assertRaises(tl.ToolLoopError) as ctx:
                tl._tl_run_agent(agent, "p", cwd=tmp, readonly=True, read_files=[],
                                 files=[], log_file=os.path.join(tmp, "x.jsonl"))
        self.assertTrue(ctx.exception.transient)
        self.assertIn("リトライ", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
