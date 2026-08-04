from __future__ import annotations

import json
import os
import unittest

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
