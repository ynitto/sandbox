"""agent-herd の入口契約——どの名前から入っても同じ道に落ちること、を縛る。

背骨は 2 つ:

1. **分岐は basename(argv[0]) の 1 回だけ。** `agent-aider …` と `agent-herd aider …` が
   同じ main へ同じ引数で届かなければ、「別名は本体そのもの」という前提が崩れる。
2. **サブコマンドは adapter の名前であって定義の名前ではない。** `ollama-json` のような
   定義名を打たれたときに黙って別解釈せず、`exec` を案内して止まること。
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import agentcli, herdcli  # noqa: E402

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))


class Argv0DispatchTests(unittest.TestCase):
    def test_each_distributed_name_resolves_to_its_adapter(self):
        for prog, expected in (("agent-aider", "aider"),
                               ("agent-ollama", "ollama"),
                               ("agent-opencode", "opencode")):
            sub, rest = herdcli.resolve(f"/usr/local/bin/{prog}", ["--model", "m"])
            self.assertEqual((sub, rest), (expected, ["--model", "m"]), prog)

    def test_the_entry_name_reads_its_first_argument_as_the_subcommand(self):
        self.assertEqual(herdcli.resolve("/x/agent-herd", ["ollama", "--tui", "q"]),
                         ("ollama", ["--tui", "q"]))

    def test_an_unknown_argv0_still_reads_the_first_argument(self):
        """開発木で `python -m agentcore.herdcli defs` のように叩いても同じ規則。"""
        self.assertEqual(herdcli.resolve("/x/python3", ["defs"]), ("defs", []))

    def test_both_spellings_reach_the_same_adapter_with_the_same_arguments(self):
        """別名と明示形が同じ呼び出しになる（互換シムではなく本体そのもの）。"""
        seen = []
        original = herdcli.ADAPTERS["ollama"]
        herdcli.ADAPTERS["ollama"] = lambda: (lambda argv: seen.append(list(argv)) or 0)
        try:
            herdcli.main(["--think", "off", "qwen3"], prog="/x/agent-ollama")
            herdcli.main(["ollama", "--think", "off", "qwen3"], prog="/x/agent-herd")
        finally:
            herdcli.ADAPTERS["ollama"] = original
        self.assertEqual(seen, [["--think", "off", "qwen3"]] * 2)

    def test_observation_aliases_are_not_a_second_implementation(self):
        """status / follow / replay は ollama adapter の同名フラグへそのまま渡すだけ。"""
        seen = []
        original = herdcli.ADAPTERS["ollama"]
        herdcli.ADAPTERS["ollama"] = lambda: (lambda argv: seen.append(list(argv)) or 0)
        try:
            herdcli.main(["status"], prog="/x/agent-herd")
            herdcli.main(["replay", "--arm", "model=q"], prog="/x/agent-herd")
        finally:
            herdcli.ADAPTERS["ollama"] = original
        self.assertEqual(seen, [["--status"], ["--replay", "--arm", "model=q"]])


class SubcommandNamespaceTests(unittest.TestCase):
    """サブコマンド名の空間は adapter だけ。定義名は exec から引く。"""

    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(_REPO)          # 同梱の agents/ を解決させる
        agentcli.clear_cache()

    def tearDown(self):
        os.chdir(self._cwd)
        agentcli.clear_cache()

    def test_a_definition_name_is_refused_with_a_pointer_to_exec(self):
        err = io.StringIO()
        saved, sys.stderr = sys.stderr, err
        try:
            rc = herdcli.main(["ollama-json"], prog="/x/agent-herd")
        finally:
            sys.stderr = saved
        self.assertEqual(rc, 2)
        self.assertIn("exec ollama-json", err.getvalue())
        self.assertIn("adapter ではありません", err.getvalue())

    def test_a_name_that_is_neither_lists_what_exists(self):
        err = io.StringIO()
        saved, sys.stderr = sys.stderr, err
        try:
            rc = herdcli.main(["nonsense"], prog="/x/agent-herd")
        finally:
            sys.stderr = saved
        self.assertEqual(rc, 2)
        self.assertIn("未知のサブコマンド", err.getvalue())
        self.assertIn("defs", err.getvalue())

    def test_adapter_names_are_not_definition_names(self):
        """この分離が守られているか（ollama-json が adapter に化けていないか）。"""
        self.assertEqual(set(herdcli.ADAPTERS), {"aider", "ollama", "opencode"})


class DefsTests(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(_REPO)
        agentcli.clear_cache()

    def tearDown(self):
        os.chdir(self._cwd)
        agentcli.clear_cache()

    def test_json_output_carries_the_effective_argv(self):
        out = io.StringIO()
        rc = herdcli.cmd_defs(["aider", "--json", "--model", "gemma4:e4b"], out=out)
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["name"], "aider")
        self.assertEqual(payload["argv_write"][0], "agent-aider")
        self.assertIn("ollama_chat/gemma4:e4b", payload["argv_write"])
        self.assertEqual(payload["headless_autonomy"], "single-shot")

    def test_the_effective_argv_matches_what_an_engine_would_build(self):
        """defs が第 2 実装になっていないこと（agentcli と同じ argv が出る）。"""
        out = io.StringIO()
        herdcli.cmd_defs(["ollama", "--json", "--model", "qwen3"], out=out)
        payload = json.loads(out.getvalue())
        spec = agentcli.load_cli("ollama", project_dir=_REPO)
        expected = agentcli.headless_cmd(spec, "qwen3", "<PROMPT>", readonly=False)["argv"]
        self.assertEqual(payload["argv_write"], expected)

    def test_a_purpose_resolves_through_the_variant(self):
        """variant は入口を増やさず定義を付け替えるだけ、が defs から見えること。"""
        out = io.StringIO()
        herdcli.cmd_defs(["ollama", "--json", "--purpose", "split"], out=out)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["resolved_via_variant"])
        self.assertEqual(payload["name"], "ollama-list")
        self.assertEqual(payload["requested"], "ollama")

    def test_listing_names_every_bundled_definition(self):
        out = io.StringIO()
        rc = herdcli.cmd_defs(["--json"], out=out)
        self.assertEqual(rc, 0)
        names = json.loads(out.getvalue())["definitions"]
        for expected in ("aider", "ollama", "ollama-json", "claude"):
            self.assertIn(expected, names)

    def test_an_unknown_definition_fails_instead_of_guessing(self):
        err = io.StringIO()
        rc = herdcli.cmd_defs(["no-such-cli"], out=io.StringIO(), err=err)
        self.assertEqual(rc, 1)


class ChatTests(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(_REPO)
        agentcli.clear_cache()

    def tearDown(self):
        os.chdir(self._cwd)
        agentcli.clear_cache()

    def test_the_default_target_is_the_builtin_ollama_tui(self):
        launched = []
        rc = herdcli.cmd_chat([], launcher=lambda argv: launched.append(argv) or 0)
        self.assertEqual(rc, 0)
        self.assertEqual(launched[0][:2], ["agent-ollama", "--tui"])

    def test_a_cloud_cli_with_an_interactive_block_launches(self):
        """chat は ollama 専用ではない——interactive を宣言した定義はどれでも起動できる。"""
        launched = []
        rc = herdcli.cmd_chat(["claude"], launcher=lambda argv: launched.append(argv) or 0)
        self.assertEqual(rc, 0)
        self.assertTrue(launched[0], "対話 argv が空です")

    def test_aider_has_no_interactive_block_yet(self):
        """aider の対話面はまだ無い。**足す前に agent-dashboard を直す必要がある**。

        agent-dashboard の定型業務は `spec.interactive` の**有無**を
        「対話ペインで駆動できる CLI か」の代理として読み、無い CLI（aider・素の ollama）を
        agent-loop の statemachine ハーネスへ回している
        （cowork.js の `if (!selected.spec.interactive)`）。

        つまり aider.json に interactive を足すと、chat が使えるようになる代わりに
        **定型業務の実行経路が黙ってハーネスから対話送信へ切り替わる**。実際 CI の
        `dashboard (npm test)` がこれを検出した（state-machine-window.test.js:
        「単発実行サブコマンドへ渡す（send ではない）」）。

        正しい弁別子は `headless_autonomy`（single-shot はハーネスが要る / tool-loop は
        自分で回せる）だが、それは dashboard 側の実行経路を変える独立した変更である。
        ここではその依存関係を固定しておく——このテストが落ちたら、aider.json に
        interactive を足した誰かが dashboard の弁別子も直したということなので、
        本テストを消して chat の起動テストへ置き換えてよい。
        """
        spec = agentcli.load_cli("aider", project_dir=_REPO)
        self.assertIsNone(spec.get("interactive"),
                          "aider.json に interactive を足すなら、先に agent-dashboard の "
                          "cowork.js が headless_autonomy で弁別するよう直すこと")
        err = io.StringIO()
        rc = herdcli.cmd_chat(["aider"], err=err, launcher=lambda argv: 0)
        self.assertEqual(rc, 1)

    def test_a_definition_without_an_interactive_block_fails_loudly(self):
        """黙ってヘッドレスへ倒さない（追加したければ定義に書く）。

        variant 定義（ollama-json 等）は役割専用のヘッドレス設定なので対話面を持たない。
        ここでヘッドレスへ倒すと、人は「対話に入ったつもり」で 1 往復だけの実行を眺める
        ことになる。
        """
        err = io.StringIO()
        rc = herdcli.cmd_chat(["ollama-json"], err=err, launcher=lambda argv: 0)
        self.assertEqual(rc, 1)
        self.assertIn("対話起動に対応していません", err.getvalue())

    def test_launching_our_own_name_stays_in_process(self):
        """interactive.command が配布名を指していても、開発木で PATH に無くて構わない。"""
        seen = []
        original = herdcli.ADAPTERS["ollama"]
        herdcli.ADAPTERS["ollama"] = lambda: (lambda argv: seen.append(list(argv)) or 7)
        try:
            rc = herdcli._launch(["agent-ollama", "--tui", "--think", "on", "qwen3"])
        finally:
            herdcli.ADAPTERS["ollama"] = original
        self.assertEqual(rc, 7)
        self.assertEqual(seen, [["--tui", "--think", "on", "qwen3"]])


class ExecTests(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(_REPO)
        agentcli.clear_cache()

    def tearDown(self):
        os.chdir(self._cwd)
        agentcli.clear_cache()

    def test_a_tty_is_not_read_as_a_prompt(self):
        """引数だけ打って Enter したときに、無言で入力待ちにならないこと。"""
        class _Tty(io.StringIO):
            def isatty(self):
                return True
        self.assertEqual(herdcli._read_prompt(_Tty("読んではいけない")), "")

    def test_exec_builds_the_argv_from_the_definition(self):
        built = []
        rc = herdcli.cmd_exec(["ollama-json", "--model", "gemma4:e4b"],
                              stdin=io.StringIO("本文"),
                              runner=lambda b: built.append(b) or 0)
        self.assertEqual(rc, 0)
        argv = built[0]["argv"]
        self.assertEqual(argv[0], "agent-ollama")
        self.assertIn("--format", argv)
        self.assertIn("json", argv)

    def test_exec_requires_a_definition_name(self):
        err = io.StringIO()
        self.assertEqual(herdcli.cmd_exec([], err=err, stdin=io.StringIO("")), 2)

    def test_exec_rejects_unknown_options_instead_of_forwarding_them(self):
        """adapter サブコマンドと違い、exec の引数面は閉じている（定義が argv を決める）。"""
        err = io.StringIO()
        rc = herdcli.cmd_exec(["ollama", "--tools"], err=err,
                              stdin=io.StringIO(""), runner=lambda b: 0)
        self.assertEqual(rc, 2)
        self.assertIn("--tools", err.getvalue())


class HarnessTests(unittest.TestCase):
    def test_harness_says_where_it_currently_lives(self):
        """P2 まではここに無い。黙って失敗せず、いま動く経路を指す。"""
        err = io.StringIO()
        rc = herdcli.cmd_harness(["statemachine"], err=err)
        self.assertEqual(rc, 2)
        self.assertIn("agent-loop statemachine", err.getvalue())


if __name__ == "__main__":
    unittest.main()
