"""agent-herd の入口契約——どの名前から入っても同じ道に落ちること、を縛る。

背骨は 2 つ:

1. **分岐は basename(argv[0]) の 1 回だけ。** `agent-aider …` と `agent-herd aider …` が
   同じ main へ同じ引数で届かなければ、「別名は本体そのもの」という前提が崩れる。
2. **サブコマンドは adapter の名前であって定義の名前ではない。** `ollama-json` のような
   定義名を打たれたときに黙って別解釈せず、`exec` を案内して止まること。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import agentcli, herdcli  # noqa: E402

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))


class Argv0DispatchTests(unittest.TestCase):
    def test_each_distributed_name_resolves_to_its_adapter(self):
        for prog, expected in (("agent-aider", "aider"),
                               ("agent-ollama", "ollama")):
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

    def test_a_flag_first_invocation_is_not_a_subcommand(self):
        """先頭がフラグなら自分自身への指定（設計 2026-08-27 §3.1）。"""
        self.assertTrue(herdcli._is_toplevel_invocation(None))
        self.assertTrue(herdcli._is_toplevel_invocation("-p"))
        self.assertTrue(herdcli._is_toplevel_invocation("--model"))
        self.assertFalse(herdcli._is_toplevel_invocation("ollama"))

    def test_the_alias_argument_face_stays_a_pass_through(self):
        """別名（argv0）はフラグより先に拾う——あちらの引数面は adapter のものである。

        これが崩れると `agent-ollama --model m` を herdcli が解釈しはじめ、adapter だけが
        知っているフラグが「受け取りません」で落ちる。
        """
        seen = []
        original = herdcli.ADAPTERS["ollama"]
        herdcli.ADAPTERS["ollama"] = lambda: (lambda argv: seen.append(list(argv)) or 0)
        try:
            herdcli.main(["--tui", "--think", "off"], prog="/x/agent-ollama")
        finally:
            herdcli.ADAPTERS["ollama"] = original
        self.assertEqual(seen, [["--tui", "--think", "off"]])

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
        self.assertEqual(set(herdcli.ADAPTERS), {"aider", "ollama"})


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
        self.assertEqual(payload["argv_write"][:2], ["agent-herd", "aider"])
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
        """variant は入口も agent_cli も増やさず、profile を付け替えるだけ。"""
        out = io.StringIO()
        herdcli.cmd_defs(["ollama", "--json", "--purpose", "split"], out=out)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["resolved_via_variant"])
        self.assertEqual(payload["name"], "ollama", "用途で agent_cli を増やさない")
        self.assertEqual(payload["profile"], "list")
        self.assertEqual(payload["requested"], "ollama")
        self.assertIn("--format", payload["argv_write"])
        self.assertIn("array", payload["argv_write"])

    def test_listing_names_every_bundled_definition(self):
        out = io.StringIO()
        rc = herdcli.cmd_defs(["--json"], out=out)
        self.assertEqual(rc, 0)
        names = json.loads(out.getvalue())["definitions"]
        for expected in ("aider", "ollama", "claude"):
            self.assertIn(expected, names)
        # 用途別の起動形は profile なので、一覧は**実エージェント数**になる。
        # ここが増えると、運用者にはクラウド CLI と並ぶ別エージェントに見える。
        self.assertNotIn("ollama-json", names)
        self.assertNotIn("ollama-list", names)

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
        self.assertEqual(launched[0][:3], ["agent-herd", "ollama", "--tui"])

    def test_a_cloud_cli_with_an_interactive_block_launches(self):
        """chat は ollama 専用ではない——interactive を宣言した定義はどれでも起動できる。"""
        launched = []
        rc = herdcli.cmd_chat(["claude"], launcher=lambda argv: launched.append(argv) or 0)
        self.assertEqual(rc, 0)
        self.assertTrue(launched[0], "対話 argv が空です")

    def test_aider_chat_opens_the_common_tui_with_the_policy(self):
        """対話で試したことがヘッドレスで再現しないのを防ぐ: policy は同じ経路で付く。

        段 12 で対話面は aider 素の TUI から**共通 TUI の aider バックエンド**になった
        （設計 2026-08-27 §7.1）。1 入力 = aider 1 回のヘッドレス実行なので、ヘッドレスと
        同じ押し切り（--yes-always）と表示制御（--no-stream / --no-pretty）を持つ。
        `--message` だけは adapter がターンごとに付けるので、起動 argv には無い。

        この対話面を開通させるには先に agent-dashboard の弁別子を直す必要があった——
        あちらは `spec.interactive` の有無を「対話ペインで駆動できるか」の代理として読んで
        いたので、interactive を足すと aider の定型業務が黙ってハーネスから対話送信へ
        切り替わっていた。いまは `headlessAutonomy` で弁別する
        （`cowork.needsHeadlessHarness`。固定は dashboard の state-machine-window.test.js）。
        """
        launched = []
        rc = herdcli.cmd_chat(["aider", "--model", "gemma4:e4b"],
                              launcher=lambda argv: launched.append(argv) or 0)
        self.assertEqual(rc, 0)
        argv = launched[0]
        self.assertIn("--tui", argv)
        self.assertIn("--agent-policy", argv)
        self.assertIn("gemma4-e4b-reliability-v1", argv)
        self.assertIn("ollama_chat/gemma4:e4b", argv)
        self.assertNotIn("--message", argv)

    def test_aider_stays_single_shot_so_the_harness_still_owns_its_routines(self):
        """対話面が付いても `headless_autonomy` は single-shot のまま。

        この 2 つは別の宣言である。混ぜると定型業務の実行経路が変わる。
        """
        spec = agentcli.load_cli("aider", project_dir=_REPO)
        self.assertTrue(spec.get("interactive"), "chat aider のための interactive が無い")
        self.assertEqual(spec.get("headless_autonomy"), "single-shot")

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
        self.assertEqual(argv[:2], ["agent-herd", "ollama"])
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
    """引数の綴りは `agent-loop statemachine` / `agent-loop run` と揃える。

    同じハーネスの 2 つの入口なので、片方だけ違う名前を人に覚えさせない。
    """

    def test_statemachine_takes_the_same_flags_as_agent_loop(self):
        seen = []
        rc = herdcli.cmd_harness(
            ["statemachine", "--workflow", "wf.yaml", "--agent-cli", "aider",
             "--model", "gemma4:e4b", "--param", "topic=llm", "--input", "本文"],
            runner=lambda kind, args, cwd: seen.append((kind, args)) or 0)
        self.assertEqual(rc, 0)
        kind, args = seen[0]
        self.assertEqual(kind, "statemachine")
        self.assertEqual(args.workflow, "wf.yaml")
        self.assertEqual(args.agent_cli, "aider")
        self.assertEqual(args.model, "gemma4:e4b")
        self.assertEqual(args.param, ["topic=llm"])
        self.assertEqual(args.input, "本文")

    def test_run_takes_the_same_flags_as_agent_loop(self):
        seen = []
        rc = herdcli.cmd_harness(
            ["run", "タスク本文", "--acceptance", "X がある", "--judge"],
            runner=lambda kind, args, cwd: seen.append((kind, args)) or 0)
        self.assertEqual(rc, 0)
        kind, args = seen[0]
        self.assertEqual(kind, "run")
        self.assertEqual(args.prompt, ["タスク本文"])
        self.assertEqual(args.acceptance, ["X がある"])
        self.assertTrue(args.judge)

    def test_a_missing_required_flag_fails_instead_of_running(self):
        """--workflow 無しで走り出さない（argparse の 2 を入口の 2 へ揃える）。"""
        err = io.StringIO()
        saved, sys.stderr = sys.stderr, err
        try:
            rc = herdcli.cmd_harness(["statemachine"], runner=lambda *a: 0)
        finally:
            sys.stderr = saved
        self.assertEqual(rc, 2)

    def test_an_unknown_kind_lists_what_exists(self):
        err = io.StringIO()
        rc = herdcli.cmd_harness(["nonsense"], err=err)
        self.assertEqual(rc, 2)
        self.assertIn("statemachine", err.getvalue())

    def test_the_dir_flag_decides_the_working_directory(self):
        seen = []
        with tempfile.TemporaryDirectory() as d:
            herdcli.cmd_harness(["run", "X", "--dir", d],
                                runner=lambda kind, args, cwd: seen.append(cwd) or 0)
            self.assertEqual(seen[0], pathlib.Path(d).resolve())

    def test_it_calls_the_ported_harness_not_agent_loop(self):
        """実体は agentcore.harness（移植先）。agent-loop のデーモンを介さない。"""
        from agentcore.harness import statemachine as ported
        called = []
        original = ported.cmd_statemachine
        ported.cmd_statemachine = lambda args, cwd: called.append((args, cwd))
        try:
            args = argparse.Namespace(workflow="wf.yaml", agent_cli="aider", model=None,
                                      param=[], input=None, dir=None)
            rc = herdcli._run_harness("statemachine", args, pathlib.Path("."))
        finally:
            ported.cmd_statemachine = original
        self.assertEqual(rc, 0)
        self.assertEqual(len(called), 1)

    def test_sys_exit_from_the_harness_becomes_the_exit_code(self):
        """移植元は終了を sys.exit で表す。入口はそれを終了コードへ戻す。"""
        from agentcore.harness import toolloop as ported
        original = ported.cmd_run

        def _boom(args, cwd):
            raise SystemExit(3)

        ported.cmd_run = _boom
        try:
            rc = herdcli._run_harness("run", argparse.Namespace(), pathlib.Path("."))
        finally:
            ported.cmd_run = original
        self.assertEqual(rc, 3)


class TopLevelFlagsTests(unittest.TestCase):
    """`agent-herd [フラグ]` がクラウド CLI と同型であること（設計 2026-08-27 §3.1）。

    **新しい実行経路は足していない。** ここが見るのは、フラグが既に `chat` / `exec` が
    持っている当て先（`interactive_cmd` / `headless_cmd`）へ翻訳されることだけである。
    """

    def _headless(self, argv, stdin=None):
        built = {}
        rc = herdcli.cmd_toplevel(argv, runner=lambda b: built.update(b) or 0, stdin=stdin)
        return rc, built

    def _interactive(self, argv):
        seen = {}
        rc = herdcli.cmd_toplevel(argv, launcher=lambda a: seen.update(argv=a) or 0)
        return rc, seen.get("argv")

    def test_no_arguments_opens_the_interactive_face(self):
        rc, argv = self._interactive([])
        self.assertEqual(rc, 0)
        self.assertIn("--tui", argv)

    def test_dash_p_runs_once_with_the_body_on_stdin_of_the_child(self):
        rc, built = self._headless(["-p", "こんにちは"])
        self.assertEqual(rc, 0)
        self.assertEqual(built["stdin"], "こんにちは")
        self.assertNotIn("--tui", built["argv"])

    def test_dash_p_without_a_value_reads_stdin(self):
        _rc, built = self._headless(["-p"], stdin=io.StringIO("パイプ本文"))
        self.assertEqual(built["stdin"], "パイプ本文")

    def test_dash_p_without_a_value_does_not_eat_the_next_flag(self):
        _rc, built = self._headless(["-p", "--model", "qwen3:8b"],
                                    stdin=io.StringIO("本文"))
        self.assertIn("qwen3:8b", built["argv"])
        self.assertEqual(built["stdin"], "本文")

    def test_agent_takes_a_definition_name_including_a_profile_spelling(self):
        """`--agent` が取るのは**定義名**。「adapter 名」という概念を外から消す。"""
        _rc, built = self._headless(["--agent", "ollama-json", "-p", "x"])
        self.assertIn("--format", built["argv"])
        self.assertIn("json", built["argv"])

    def test_model_and_readonly_reach_the_argv(self):
        _rc, built = self._headless(["--model", "gemma4:12b", "--readonly", "-p", "x"])
        self.assertIn("gemma4:12b", built["argv"])
        _rc, plain = self._headless(["--model", "gemma4:12b", "-p", "x"])
        self.assertNotEqual(built["argv"], plain["argv"], "readonly で argv が変わる")

    def test_purpose_routes_through_the_router(self):
        """用途の 1 語で起動形が決まる（宣言 → variants の調停は slashroute が持つ）。"""
        _rc, built = self._headless(["--purpose", "verify", "-p", "x"])
        self.assertIn("--format", built["argv"], "verify は JSON 契約の起動形へ落ちる")

    def test_the_model_stays_a_flag_in_the_interactive_face_too(self):
        _rc, argv = self._interactive(["--model", "qwen3:8b"])
        self.assertIn("qwen3:8b", argv)

    def test_dir_changes_the_working_directory(self):
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        with tempfile.TemporaryDirectory() as tmp:
            self._headless(["--dir", tmp, "-p", "x"])
            self.assertEqual(os.path.realpath(os.getcwd()), os.path.realpath(tmp))

    def test_a_missing_dir_is_an_explicit_error(self):
        err = io.StringIO()
        rc = herdcli.cmd_toplevel(["--dir", "/no/such/place", "-p", "x"], err=err)
        self.assertEqual(rc, 2)
        self.assertIn("ディレクトリが存在しません", err.getvalue())

    def test_session_continuation_without_material_or_declaration_stops(self):
        """継続できない定義で黙って新規セッションを走らせない（§4・未決 1 の決着）。

        実体は 2 つ——ネイティブの `continue_args` / `resume_args` か、自前 CLI の材料の
        再構築か。どちらも無いなら明示エラーで止める。詳しい振る舞いは
        `test_session_continue.py`。
        """
        err = io.StringIO()
        rc = herdcli.cmd_toplevel(["--resume"], err=err)
        self.assertEqual(rc, 2)
        self.assertIn("セッション ID", err.getvalue())

    def test_an_unknown_flag_names_what_is_accepted(self):
        err = io.StringIO()
        rc = herdcli.cmd_toplevel(["--wat"], err=err)
        self.assertEqual(rc, 2)
        self.assertIn("--agent", err.getvalue())

    def test_a_positional_argument_is_refused(self):
        """本文は -p か stdin。位置引数を本文と読むと `agent-herd ollama` と紛れる。"""
        err = io.StringIO()
        rc = herdcli.cmd_toplevel(["おはよう"], err=err)
        self.assertEqual(rc, 2)
        self.assertIn("位置引数", err.getvalue())

    def test_every_old_subcommand_is_still_reachable(self):
        """従来の綴りは別名として温存する（仕様書 §3 を壊さない）。"""
        for sub in ("aider", "ollama", "defs", "exec", "chat", "harness",
                    "status", "follow", "replay"):
            self.assertFalse(herdcli._is_toplevel_invocation(sub), sub)
            self.assertIn(sub, herdcli.HELP, sub)


if __name__ == "__main__":
    unittest.main()
