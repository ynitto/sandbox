#!/usr/bin/env python3
"""CLI 定義が `timeout` を宣言していないときの共通 fallback（600 秒）。

180 秒だった頃は、ローカル推論の正常な実行を切っていた——gemma4:e4b は 1 周 50〜90 秒、
判定役の gemma4:12b はさらに遅く、ollama が他リクエストで塞がっていれば queue 待ちが
そこへ積み上がる。fallback は toolloop の 1 か所だけに置き、周ごと・変種ごとに別の既定を
作らない（どの上限で切られたのかを読む側が追えなくなる）。
"""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agentcore.harness import toolloop as tl  # noqa: E402
from agentcore.tests.harnesspatch import patch_harness  # noqa: E402

_AGENTS_DIR = Path(__file__).resolve().parents[5] / "agents"


def _agent(timeout=None):
    """headless_cmd が `timeout` をそのまま返す最小のエージェント。"""
    mod = types.SimpleNamespace(
        headless_cmd=lambda spec, model, prompt, **kw: {
            "argv": ["fake-cli"], "env": {}, "stdin": prompt,
            "output_file": None, "timeout": timeout},
        resolve_variant=lambda name, purpose, cwd=None: None,
        classify_error=lambda spec, blob, detailed=False, now=None: None,
    )
    return {"cli": "fake", "spec": {"headless_autonomy": "single-shot"},
            "model": None, "agentcli": mod}


class FallbackValueTests(unittest.TestCase):
    def test_common_fallback_is_600_seconds(self):
        self.assertEqual(tl._TL_DEFAULT_AGENT_TIMEOUT_SEC, 600)

    def test_no_second_fallback_constant_remains(self):
        """判定（judge）だけ別の既定を持たない。持たせると本体は通るのに判定だけ切れる。"""
        self.assertFalse(hasattr(tl, "_JUDGE_TIMEOUT_SEC"))


class RunAgentTimeoutTests(unittest.TestCase):
    def _timeout_used(self, agent, **kw):
        seen = {}

        def exec_argv(command, args, *, cwd, timeout_sec, **rest):
            seen["timeout"] = timeout_sec
            return {"status": 0, "stdout": "ok", "stderr": "", "error": ""}

        with tempfile.TemporaryDirectory() as tmp, \
                patch_harness("_tl_exec_argv", side_effect=exec_argv), \
                patch_harness("_tl_record_usage"):
            tl._tl_run_agent(agent, "p", cwd=tmp, readonly=True, read_files=[],
                             files=[], log_file=os.path.join(tmp, "x.jsonl"), **kw)
        return seen["timeout"]

    def test_undeclared_timeout_falls_back_to_600(self):
        self.assertEqual(self._timeout_used(_agent(timeout=None)), 600)

    def test_zero_is_treated_as_undeclared(self):
        self.assertEqual(self._timeout_used(_agent(timeout=0)), 600)

    def test_declared_timeout_wins(self):
        """個別に伸ばしたい・縮めたい CLI は定義の `timeout` で宣言する（fallback より優先）。"""
        self.assertEqual(self._timeout_used(_agent(timeout=45)), 45)


class JudgeTimeoutTests(unittest.TestCase):
    """判定層も同じ fallback に従う（判定役は本体より遅い変種のことが多い）。"""

    def _timeout_used(self, timeout):
        seen = {}

        def exec_argv(command, args, *, cwd, timeout_sec, **rest):
            seen["timeout"] = timeout_sec
            return {"status": 0, "error": "", "stderr": "",
                    "stdout": json.dumps({"results": [{"ok": True, "reason": ""}]})}

        with tempfile.TemporaryDirectory() as tmp, \
                patch_harness("_tl_exec_argv", side_effect=exec_argv):
            tl.judge_acceptance(["文章が日本語である"], cwd=tmp, agent=_agent(timeout),
                                log_file=os.path.join(tmp, "x.jsonl"),
                                output="done", files=[])
        return seen["timeout"]

    def test_undeclared_timeout_falls_back_to_600(self):
        self.assertEqual(self._timeout_used(None), 600)

    def test_declared_timeout_wins(self):
        self.assertEqual(self._timeout_used(30), 30)


class DefinitionContractTests(unittest.TestCase):
    """配布する CLI 定義そのものの回帰。

    fallback を 1 か所に保つため、既定で足りる CLI は `timeout` を宣言しない。宣言を
    戻すと fallback の変更がその CLI だけ効かなくなる（気づけないまま古い上限が残る）。
    """

    def _spec(self, name):
        return json.loads((_AGENTS_DIR / f"{name}.json").read_text(encoding="utf-8"))

    def test_ollama_variants_declare_no_fixed_timeout(self):
        names = sorted(p.stem for p in _AGENTS_DIR.glob("ollama*.json"))
        self.assertTrue(names, "ollama 変種の定義が見つからない")
        for name in names:
            with self.subTest(cli=name):
                self.assertIsNone(self._spec(name).get("timeout"))

    def test_aider_declares_no_fixed_timeout(self):
        self.assertIsNone(self._spec("aider").get("timeout"))


if __name__ == "__main__":
    unittest.main()
