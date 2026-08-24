from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from _shared import AuditTestCase, configfile


class ConfigTests(AuditTestCase):
    def test_resolution_order_args_over_config(self):
        """書き先は 引数 > 設定 > 既定（環境変数は見ない・不変条件 5）。"""
        args = self.make_args()
        self.assertEqual(configfile.resolve_audit_dir(args), self.audit_dir)
        args2 = self.make_args(audit_dir=None)
        self.assertTrue(configfile.resolve_audit_dir(args2).endswith("audit"))

    def test_no_env_dependency_for_audit_dir(self):
        """AGENT_AUDIT_DIR のような env を置いても書き先は変わらない。"""
        os.environ["AGENT_AUDIT_DIR"] = "/tmp/should-not-be-used"
        try:
            args = self.make_args()
            self.assertEqual(configfile.resolve_audit_dir(args), self.audit_dir)
        finally:
            del os.environ["AGENT_AUDIT_DIR"]

    def test_find_config_cwd_then_home(self):
        cfg = os.path.join(self.tmp, "agent-audit.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"agent_cli": "kiro"}, f)
        found = configfile.find_config(None, cwd=self.tmp)
        self.assertEqual(found, cfg)

    def test_global_config_uses_dot_agents_only(self):
        home = os.path.join(self.tmp, "home")
        old = os.path.join(home, ".agent")
        new = os.path.join(home, ".agents")
        os.makedirs(old)
        with open(os.path.join(old, "agent-audit.json"), "w", encoding="utf-8") as f:
            json.dump({"agent_cli": "old"}, f)
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertIsNone(configfile.find_config(None, cwd=home))
            os.makedirs(new)
            expected = os.path.join(new, "agent-audit.json")
            with open(expected, "w", encoding="utf-8") as f:
                json.dump({"agent_cli": "new"}, f)
            self.assertEqual(configfile.find_config(None, cwd=home), expected)

    def test_with_transcripts_config_key_fills_unset_flag(self):
        """--with-transcripts を渡さない定期実行でも、設定で副作用保存を有効化できる。"""
        import argparse
        cfg = os.path.join(self.tmp, "agent-audit.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"with_transcripts": True}, f)
        args = argparse.Namespace(config=cfg, with_transcripts=None)
        configfile.resolve_config(args)
        self.assertTrue(args.with_transcripts)

    def test_explicit_config_missing_is_hard_error(self):
        with self.assertRaises(SystemExit):
            configfile.find_config(os.path.join(self.tmp, "nope.yaml"))

    def test_agent_for_purpose_overrides(self):
        args = self.make_args(agent_cli="claude", model=None,
                              agents={"extract": {"agent_cli": "ollama", "model": "qwen3"},
                                      "distill": {"model": "opus"},
                                      "broken": "not-a-dict"})
        self.assertEqual(configfile.agent_for(args, "extract"), ("ollama", "qwen3"))
        self.assertEqual(configfile.agent_for(args, "distill"), ("claude", "opus"))
        self.assertEqual(configfile.agent_for(args, "review"), ("claude", None))


if __name__ == "__main__":
    unittest.main()
