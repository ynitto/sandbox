#!/usr/bin/env python3
"""CLI オプションの効き先 — サブコマンドに効かない指定を黙って受理しないこと。

`--split-direction` / `--no-auto-attach` / `--controller-mode` / `--instance-id` は
tmux ペインの張り方とインスタンス識別で、**サブコマンド無しのデーモン起動**でしか読まれない。
以前はグローバル引数として黙って受理して捨てていたので、
`agent-loop --split-direction vertical methods list` が「効いたつもり」で通っていた。

    python3 -m unittest discover -s test
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402
from agent_loop.cli import _DAEMON_ONLY_OPTIONS, _reject_daemon_only_options  # noqa: E402


class DaemonOnlyOptionTests(unittest.TestCase):
    def _check(self, subcommand, **attrs):
        parser = al.argparse.ArgumentParser(prog="agent-loop")
        args = al.argparse.Namespace(subcommand=subcommand,
                                     **{a: None for a, _f in _DAEMON_ONLY_OPTIONS})
        for key, value in attrs.items():
            setattr(args, key, value)
        with redirect_stderr(io.StringIO()) as err:
            _reject_daemon_only_options(parser, args)
        return err.getvalue()

    def test_daemon_mode_accepts_them(self):
        # サブコマンド無し（＝本来の効き先）では素通り。None / "" のどちらでも。
        for subcommand in (None, ""):
            self.assertEqual(
                self._check(subcommand, split_direction="vertical", controller_mode=True), "")

    def test_subcommand_rejects_each_option(self):
        for attr, flag in _DAEMON_ONLY_OPTIONS:
            value = True if attr in ("no_auto_attach", "controller_mode") else "vertical"
            with self.assertRaises(SystemExit) as ctx:
                self._check("methods", **{attr: value})
            self.assertEqual(ctx.exception.code, 2, flag)   # usage エラー

    def test_unset_options_are_not_rejected(self):
        # 既定のまま（偽値）なら何も言わない——「指定された」と誤検知しない。
        self.assertEqual(self._check("send"), "")

    def test_message_names_the_flag_and_the_subcommand(self):
        parser = al.argparse.ArgumentParser(prog="agent-loop")
        args = al.argparse.Namespace(subcommand="methods", split_direction="vertical",
                                     no_auto_attach=False, controller_mode=False,
                                     instance_id=None)
        buf = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(buf):
            _reject_daemon_only_options(parser, args)
        message = buf.getvalue()
        self.assertIn("--split-direction", message)
        self.assertIn("methods", message)

    def test_log_level_stays_global(self):
        # 全サブコマンドで意味がある（parse 直後に logging へ効かせる）ので対象外。
        self.assertNotIn("log_level", [attr for attr, _flag in _DAEMON_ONLY_OPTIONS])


if __name__ == "__main__":
    unittest.main()
