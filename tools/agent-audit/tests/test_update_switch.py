from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from _shared import AuditTestCase, configfile
from agent_audit import doctor, update


def _sha_runner(sha="deadbeef" * 5):
    def run(cmd):
        return mock.Mock(returncode=0, stdout=f"{sha}\trefs/heads/main\n", stderr="")
    return run


class UpdateEnabledTests(AuditTestCase):
    """`update_enabled: false` は自己更新のキルスイッチとして効かなければならない。"""

    def _args(self, **over):
        return self.make_args(update_repo="ssh://git@example.invalid/tools.git", **over)

    def test_disabled_by_config_reports_disabled(self):
        info = update.check_update(self._args(update_enabled=False), runner=_sha_runner())
        self.assertFalse(info["enabled"])
        self.assertTrue(info["disabled_by_config"])

    def test_disabled_never_touches_the_remote(self):
        """切ってあるなら ls-remote すら打たない（ネットワークへ出ない）。"""
        runner = mock.Mock(side_effect=AssertionError("リモートへ出てはいけない"))
        info = update.check_update(self._args(update_enabled=False), runner=runner)
        self.assertFalse(info["enabled"])
        runner.assert_not_called()

    def test_enabled_by_default(self):
        info = update.check_update(self._args(), runner=_sha_runner())
        self.assertTrue(info["enabled"])
        self.assertFalse(info["disabled_by_config"])

    def test_missing_repo_is_distinguished_from_disabled(self):
        """未設定と「人が切った」を混ぜない——切ったつもりの人に理由が届かなくなる。"""
        info = update.check_update(self.make_args(update_repo=""), runner=_sha_runner())
        self.assertFalse(info["enabled"])
        self.assertFalse(info["disabled_by_config"])

    def test_cmd_update_exits_zero_and_says_why(self):
        args = self._args(update_enabled=False, check=False, now=True)
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = update.cmd_update(args)
        self.assertEqual(code, 0)
        self.assertIn("update_enabled", out.getvalue())

    def test_cmd_update_without_repo_is_still_a_usage_error(self):
        args = self.make_args(update_repo="", check=False, now=False)
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = update.cmd_update(args)
        self.assertEqual(code, 2)
        self.assertIn("update_repo", err.getvalue())


class InertKeyTests(AuditTestCase):
    """効かないキーを黙って飲み込まない。"""

    def test_update_check_interval_is_declared_inert(self):
        self.assertIn("update_check_interval", configfile.INERT_KEYS)
        self.assertNotIn("update_check_interval", configfile.CONFIG_DEFAULTS)

    def test_doctor_reports_it_when_present(self):
        out = io.StringIO()
        with redirect_stdout(out):
            doctor._print_inert_keys({"update_check_interval": 3600})
        self.assertIn("update_check_interval", out.getvalue())
        self.assertIn("効かない設定キー", out.getvalue())

    def test_doctor_is_silent_when_absent(self):
        out = io.StringIO()
        with redirect_stdout(out):
            doctor._print_inert_keys({"audit_dir": "/tmp/x"})
        self.assertEqual(out.getvalue(), "")

    def test_every_declared_default_has_a_reader(self):
        """CONFIG_DEFAULTS のキーは INERT_KEYS と重ならない（片方に属する）。"""
        overlap = set(configfile.CONFIG_DEFAULTS) & set(configfile.INERT_KEYS)
        self.assertEqual(overlap, set())


if __name__ == "__main__":
    unittest.main()
