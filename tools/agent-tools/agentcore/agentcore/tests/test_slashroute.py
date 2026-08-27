"""スラッシュ行のルータ（`agentcore.slashroute`）。

設計: docs/plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md §3.2。

見るのは 2 つ。①切り出しと適用の規約そのもの、②**3 か所に散っていた解釈がここへ
畳まれたこと**——`ollama_skills` の切り出し・`ollama_tui` のローカルコマンド表・
`harness.toolloop.run_prompt` の層別分岐が、同じ表と同じ関数を引いていることを
突き合わせる。畳んだ意味は「片方だけ直る」が起きなくなることなので、そこを縛る。
"""
from __future__ import annotations

import os
import tempfile
import types
import unittest
from pathlib import Path

from agentcore import ollama_skills, ollama_tui, slashroute
from agentcore.harness import toolloop


class ParseTests(unittest.TestCase):
    def test_name_and_args(self):
        self.assertEqual(slashroute.parse_line("/verify docs/spec.md"),
                         ("verify", "docs/spec.md"))
        self.assertEqual(slashroute.parse_line("/verify"), ("verify", ""))

    def test_not_a_command_line(self):
        for line in ("verify", "/usr/bin/env", "/Verify", "/", "/-bad", "本文 /verify"):
            self.assertIsNone(slashroute.parse_line(line), line)

    def test_casefold_folds_the_name_only(self):
        # 人が打つ面（TUI）は `/MODEL` も受ける。引数のモデル名は潰さない。
        self.assertEqual(slashroute.parse_line("/MODEL Gemma4:12B", casefold=True),
                         ("model", "Gemma4:12B"))

    def test_casefold_is_opt_in(self):
        # 本文の切り出しは厳密なまま（`/README.md` を呼び出しと誤認しない）。
        self.assertIsNone(slashroute.parse_line("/README.md"))


class SplitLeadingTests(unittest.TestCase):
    def test_leading_block_only(self):
        calls, body = slashroute.split_leading("/a\n/b 引数\n本文\n/c")
        self.assertEqual(calls, [("a", ""), ("b", "引数")])
        self.assertEqual(body, "本文\n/c")

    def test_blank_line_ends_the_block(self):
        calls, body = slashroute.split_leading("/a\n\n/b")
        self.assertEqual(calls, [("a", "")])
        self.assertEqual(body, "/b")

    def test_no_command_line_keeps_everything(self):
        calls, body = slashroute.split_leading("ただの本文")
        self.assertEqual(calls, [])
        self.assertEqual(body, "ただの本文")

    def test_all_command_lines_leave_empty_body(self):
        calls, body = slashroute.split_leading("/a\n/b")
        self.assertEqual(calls, [("a", ""), ("b", "")])
        self.assertEqual(body, "")

    def test_skills_module_delegates_here(self):
        # `ollama_skills.split_leading_slashes` は綴りとして残るだけで実装を持たない。
        for prompt in ("/a\n/b 引数\n本文", "本文だけ", "/a\n\n本文", ""):
            self.assertEqual(ollama_skills.split_leading_slashes(prompt),
                             slashroute.split_leading(prompt), prompt)


class TableTests(unittest.TestCase):
    def test_aliases_resolve_to_the_same_row(self):
        self.assertIs(slashroute.lookup("exit"), slashroute.lookup("quit"))

    def test_unknown_name_is_none(self):
        # 表に無い名前は None。呼び出し側がスキルとして解決する（種別 D）。
        self.assertIsNone(slashroute.lookup("wiki-use"))

    def test_takes_args_follows_the_hint(self):
        self.assertTrue(slashroute.lookup("model").takes_args)
        self.assertFalse(slashroute.lookup("status").takes_args)

    def test_spellings_include_aliases_but_help_does_not(self):
        self.assertIn("/exit", slashroute.spellings())
        self.assertNotIn("/exit", slashroute.render_help())
        self.assertIn("/quit", slashroute.render_help())

    def test_onoff_spellings(self):
        self.assertEqual(slashroute.onoff_spellings(), ("/tools", "/think"))


class TuiSharesTheTableTests(unittest.TestCase):
    """TUI の一覧・補完・判定が同じ表を引いていること（綴りを 2 度書かない）。"""

    def test_completion_candidates_come_from_the_table(self):
        self.assertEqual(ollama_tui._LOCAL_COMMANDS, slashroute.spellings())
        self.assertEqual(ollama_tui._ONOFF_COMMANDS, slashroute.onoff_spellings())

    def test_help_lists_every_built_in_command(self):
        text = ollama_tui._help_text()
        for cmd in slashroute.commands():
            self.assertIn(cmd.spell, text, cmd.name)


class NormalizeLineTests(unittest.TestCase):
    def test_both_spellings_normalize_the_same(self):
        # 設定ファイル（agent-loop の `slash:`）は `/` を剥がして持つ規約。
        self.assertEqual(slashroute.normalize_line("/report --lang ja"), "report --lang ja")
        self.assertEqual(slashroute.normalize_line(" report --lang ja "), "report --lang ja")


class ApplyToGoalTests(unittest.TestCase):
    def test_native_keeps_the_lines(self):
        goal, skills = slashroute.apply_to_goal(
            "本文", ["summarize-logs", "report --lang ja"], native=True)
        self.assertEqual(goal, "/summarize-logs\n/report --lang ja\n\n本文")
        self.assertEqual(skills, [])

    def test_native_prefix_is_declared(self):
        # codex のスキル起動記号は `$`。記号は定義が宣言する（`skill_command_prefix`）。
        goal, _ = slashroute.apply_to_goal("本文", ["compact"], native=True, prefix="$")
        self.assertEqual(goal, "$compact\n\n本文")

    def test_non_native_consumes_the_lines(self):
        goal, skills = slashroute.apply_to_goal(
            "本文", ["tech-harvester ニュースをまとめて"], native=False)
        self.assertEqual(skills, ["tech-harvester"])
        self.assertIn("`tech-harvester` スキルの手順に従って実行してください。", goal)
        self.assertIn("引数: ニュースをまとめて", goal)
        self.assertTrue(goal.endswith("本文"))

    def test_duplicate_names_are_declared_once(self):
        _goal, skills = slashroute.apply_to_goal("本文", ["a x", "a y"], native=False)
        self.assertEqual(skills, ["a"])

    def test_empty_and_blank_lines_leave_the_goal_alone(self):
        for lines in (None, [], ["", "  "]):
            self.assertEqual(slashroute.apply_to_goal("本文", lines, native=True),
                             ("本文", []))
            self.assertEqual(slashroute.apply_to_goal("本文", lines, native=False),
                             ("本文", []))

    def test_run_prompt_uses_this_function(self):
        # `run_prompt` に残るのは「ネイティブのスラッシュを持つか」の 1 判定だけ。
        self.assertIs(toolloop.slashroute, slashroute)


class _StubAgentcli:
    """`variants` の申告だけを持つ最小の定義ローダ。

    `by_cli` を渡すと定義ごとに違う申告を持てる——`variants` は**定義単位**の宣言なので、
    「aider には verify の申告が無い」を言い表せないと調停の順序を試せない。
    """

    def __init__(self, variants: dict, by_cli: "dict | None" = None) -> None:
        self.variants = variants
        self.by_cli = by_cli
        self.calls: "list[tuple]" = []

    def resolve_variant(self, name, purpose, project_dir=None):
        self.calls.append((name, purpose, project_dir))
        table = self.by_cli.get(name, {}) if self.by_cli is not None else self.variants
        return table.get(purpose)


class ResolvePurposeTests(unittest.TestCase):
    """用途 → 起動形の調停（設計 §3.3 / G2・G4）。"""

    def _cli(self, **variants):
        return _StubAgentcli({k: {"agent_cli": v[0], "default_model": v[1]}
                              for k, v in variants.items()})

    def _routed(self, **kwargs):
        """エージェント解決の 3 鍵だけを見る（起動形の他の鍵は Plan 側のテストで見る）。"""
        routed = slashroute.resolve(**kwargs)
        return {k: routed[k] for k in ("agent_cli", "model", "variant")}

    def test_declared_variant_switches_the_launch_form(self):
        mod = self._cli(judge=("ollama-json", "gemma4:e4b"))
        self.assertEqual(
            self._routed(command="judge", cli="ollama", agentcli=mod),
            {"agent_cli": "ollama-json", "model": "gemma4:e4b", "variant": True})

    def test_no_declaration_leaves_everything_alone(self):
        mod = self._cli(judge=("ollama-json", "gemma4:e4b"))
        self.assertEqual(
            self._routed(command="distill", cli="ollama", model="m", agentcli=mod),
            {"agent_cli": "ollama", "model": "m", "variant": False})

    def test_there_is_no_allow_list(self):
        """engine ごとの許可リストは無い——申告が唯一の許可リストである（G2）。

        以前は flow / project / audit が各々の集合を持ち、`ollama.json` が 15 キーを
        宣言しても flow は 9・project は 6・audit は 2 しか引かなかった。
        """
        mod = self._cli(**{"repo_map": ("x-json", "")})
        routed = slashroute.resolve(command="repo_map", cli="x", agentcli=mod)
        self.assertEqual(routed["agent_cli"], "x-json")
        self.assertEqual(mod.calls, [("x", "repo_map", None)])

    def test_explicit_model_is_not_overridden(self):
        mod = self._cli(verify=("ollama-verify", "gemma4:12b"))
        routed = self._routed(command="verify", cli="ollama", model="qwen3:8b",
                              explicit_model=True, agentcli=mod)
        self.assertEqual(routed, {"agent_cli": "ollama-verify", "model": "qwen3:8b",
                                  "variant": True})

    def test_by_purpose_decision_is_not_overridden(self):
        """用途別順位表の決定はその用途の実測。変種の既定で上書きしない（G4）。"""
        mod = self._cli(judge=("ollama-json", "gemma4:e4b"))
        routed = self._routed(command="judge", cli="ollama", model="gemma4:12b",
                              by_purpose=True, agentcli=mod)
        self.assertEqual(routed, {"agent_cli": "ollama-json", "model": "gemma4:12b",
                                  "variant": True})

    def test_flat_candidates_still_defer_to_the_variant_default(self):
        # 用途を知らない共通の順位表由来のモデルは、変種の用途専用チューニングに譲る。
        mod = self._cli(verify=("ollama-verify", "gemma4:12b"))
        routed = slashroute.resolve(command="verify", cli="ollama", model="gemma4:e4b",
                                    agentcli=mod)
        self.assertEqual(routed["model"], "gemma4:12b")

    def test_session_command_names_are_not_purposes(self):
        """名前空間は 1 つ。`/model` が用途としても解釈される状態を作らない。"""
        mod = self._cli(model=("x-json", "m"))
        routed = slashroute.resolve(command="model", cli="x", agentcli=mod)
        self.assertEqual(routed["agent_cli"], "x")
        self.assertEqual(mod.calls, [])

    def test_broken_declaration_does_not_kill_the_run(self):
        mod = types.SimpleNamespace(
            resolve_variant=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("壊れた定義")))
        self.assertEqual(
            self._routed(command="verify", cli="ollama", model="m", agentcli=mod),
            {"agent_cli": "ollama", "model": "m", "variant": False})

    def test_empty_command_is_a_no_op(self):
        mod = self._cli(verify=("ollama-verify", "gemma4:12b"))
        self.assertEqual(self._routed(command="", cli="ollama", agentcli=mod),
                         {"agent_cli": "ollama", "model": None, "variant": False})
        self.assertEqual(mod.calls, [])

    def test_bundled_definitions_declare_the_whole_former_allow_list(self):
        """同梱定義の申告が、消した 3 つの許可リストの和集合を覆っていること。

        削除が「宣言の効く範囲を狭めた」ではなく「engine の重複を消した」であることの
        確認。ここが割れたら、どの用途が振り替わらなくなったのかが分かる。
        """
        from agentcore import agentcli
        former = {
            # flow: JSON_CONTRACT_ROLES | LIST_CONTRACT_ROLES | {retrieve, verify}
            "planner", "evaluator", "split", "filter", "judge", "reduce", "extract",
            "retrieve", "verify",
            # project: JSON_CONTRACT_PURPOSES
            "plan", "review", "prioritize", "route", "adjudicate", "assess",
        }
        declared = set(agentcli.load_cli("ollama").get("variants") or {})
        self.assertEqual(former - declared, set())


class _CommandHome:
    """一時的な宣言ホームを `AGENT_COMMANDS_DIR` に差し込む。"""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._prev = os.environ.get("AGENT_COMMANDS_DIR")
        os.environ["AGENT_COMMANDS_DIR"] = str(self.root)
        slashroute.clear_cache()
        return self

    def add(self, name: str, body: str) -> Path:
        path = self.root / f"{name}.md"
        path.write_text(body, encoding="utf-8")
        slashroute.clear_cache()
        return path

    def __exit__(self, *_exc):
        if self._prev is None:
            os.environ.pop("AGENT_COMMANDS_DIR", None)
        else:
            os.environ["AGENT_COMMANDS_DIR"] = self._prev
        slashroute.clear_cache()
        self._tmp.cleanup()


class ShapeCommandTests(unittest.TestCase):
    """種別 B: 実行形。**ツールセットの選択がモデルの判断から 1 語へ移る**（設計 §3.4）。"""

    def test_ask_has_no_tools_and_find_reads(self):
        self.assertEqual((slashroute.lookup("ask").tools, slashroute.lookup("ask").toolset),
                         (False, slashroute.TOOLSET_NONE))
        self.assertEqual((slashroute.lookup("find").tools, slashroute.lookup("find").toolset),
                         (True, slashroute.TOOLSET_READ))

    def test_edit_and_sm_name_a_harness(self):
        self.assertEqual(slashroute.lookup("edit").harness, slashroute.HARNESS_TOOLLOOP)
        self.assertEqual(slashroute.lookup("sm").harness, slashroute.HARNESS_STATEMACHINE)

    def test_the_toolset_vocabulary_matches_the_engine(self):
        """語彙を写さない。`ollama_loop` が持つ綴りと同じであることを突き合わせる。"""
        from agentcore import ollama_loop
        self.assertEqual(set(ollama_loop.TOOLSETS),
                         {slashroute.TOOLSET_READ, slashroute.TOOLSET_BASH})
        self.assertEqual(slashroute.TOOLSET_BASH, ollama_loop.DEFAULT_TOOLSET)

    def test_help_columns_line_up_with_wide_hints(self):
        """`/help` は tmux の capture-pane からも読まれる。全角で列が崩れないこと。"""
        lines = slashroute.render_help(slashroute.KIND_SHAPE).splitlines()
        starts = {len(line) - len(line.split("  ")[-1]) for line in lines}
        self.assertEqual(len(lines), 4)
        widths = {slashroute._display_width(line[:line.rindex("  ") + 2]) for line in lines}
        self.assertEqual(len(widths), 1, "説明の開始桁が揃っている")
        self.assertTrue(starts)

    def test_session_help_is_unchanged(self):
        """種別 A の見え方（左列 17 桁）は足す前と 1 バイトも変わらない。"""
        self.assertIn("  /skills          読めるスキルの一覧",
                      slashroute.render_help(slashroute.KIND_SESSION))


class DeclarationTests(unittest.TestCase):
    """種別 C: 用途の宣言 1 枚（設計 §3.3）。"""

    def test_reads_the_flat_frontmatter_and_the_body(self):
        with _CommandHome() as home:
            home.add("verify", '---\ndescription: 判定する\nagent: ollama\n'
                               'model: gemma4:12b\ntools: []\noutput: json\n'
                               'argument-hint: "[基準ファイル]"\n---\nあなたは判定役です。\n')
            decl = slashroute.declaration("verify")
        self.assertEqual(decl.agent, "ollama")
        self.assertEqual(decl.model, "gemma4:12b")
        self.assertEqual(decl.tools, ())
        self.assertEqual(decl.output, "json")
        self.assertEqual(decl.argument_hint, "[基準ファイル]", "引用符は値に混ぜない")
        self.assertEqual(decl.system, "あなたは判定役です。\n")

    def test_a_toolset_is_one_name(self):
        with _CommandHome() as home:
            home.add("look", "---\ntools: [read]\n---\n本文\n")
            self.assertEqual(slashroute.declaration("look").tools, ("read",))
            home.add("bad", "---\ntools: [read, bash]\n---\n本文\n")
            with self.assertRaises(slashroute.DeclarationError):
                slashroute.declaration("bad")
            home.add("nope", "---\ntools: [magic]\n---\n本文\n")
            with self.assertRaises(slashroute.DeclarationError):
                slashroute.declaration("nope")

    def test_unknown_keys_are_an_explicit_error(self):
        with _CommandHome() as home:
            home.add("x", "---\nagent: aider\nwhoops: 1\n---\n本文\n")
            with self.assertRaises(slashroute.DeclarationError) as ctx:
                slashroute.declaration("x")
        self.assertIn("whoops", str(ctx.exception))

    def test_nested_yaml_is_refused(self):
        """平らな `key: value` だけ。読めるが意味が違う書き方を入り込ませない。"""
        with _CommandHome() as home:
            home.add("x", "---\nagent:\n  - aider\n---\n本文\n")
            with self.assertRaises(slashroute.DeclarationError):
                slashroute.declaration("x")

    def test_readme_is_not_a_declaration(self):
        """置き場に説明書を置ける（コマンド名は小文字が規約）。"""
        with _CommandHome() as home:
            home.add("README", "# 説明\n")
            home.add("mine", "---\nagent: aider\n---\n本文\n")
            names = [d.name for d in slashroute.declarations()]
        self.assertIn("mine", names)
        self.assertNotIn("readme", names)
        self.assertIsNone(slashroute.declaration("readme"))

    def test_a_broken_declaration_does_not_stop_the_listing(self):
        with _CommandHome() as home:
            home.add("ok", "---\nagent: aider\n---\n本文\n")
            home.add("broken", "---\nnot yaml at all\n---\n本文\n")
            names = [d.name for d in slashroute.declarations()]
        self.assertIn("ok", names)
        self.assertNotIn("broken", names)

    def test_the_declaration_names_the_agent(self):
        mod = _StubAgentcli({})
        with _CommandHome() as home:
            home.add("edit", "---\nagent: aider\n---\n編集役です。\n")
            routed = slashroute.resolve(command="edit", cli="ollama", agentcli=mod)
        self.assertEqual(routed["agent_cli"], "aider")
        self.assertTrue(routed["declared"])
        self.assertEqual(routed["harness"], slashroute.HARNESS_TOOLLOOP, "実行形は表から")
        self.assertEqual(routed["system"], "編集役です。\n")

    def test_the_declared_agent_still_resolves_its_variants(self):
        """`agent: ollama` ＋ 用途 verify が `ollama-verify` へ落ちる 2 段（§3.3）。"""
        mod = _StubAgentcli({"verify": {"agent_cli": "ollama-verify",
                                        "default_model": "gemma4:12b"}})
        with _CommandHome() as home:
            home.add("verify", "---\nagent: ollama\n---\n判定役です。\n")
            routed = slashroute.resolve(command="verify", cli="claude", agentcli=mod)
        self.assertEqual(routed["agent_cli"], "ollama-verify")
        self.assertEqual(mod.calls, [("ollama", "verify", None)], "宣言が名指しした側を引く")

    def test_the_declared_model_loses_to_the_measured_choice(self):
        """用途別順位表（実測）と人の明示は、宣言の既定より強い（G4 と同じ規則）。"""
        mod = _StubAgentcli({})
        with _CommandHome() as home:
            home.add("verify", "---\nagent: ollama\nmodel: gemma4:12b\n---\n本文\n")
            self.assertEqual(slashroute.resolve(command="verify", cli="x",
                                                agentcli=mod)["model"], "gemma4:12b")
            self.assertEqual(slashroute.resolve(command="verify", cli="x", model="qwen3:8b",
                                                by_purpose=True, agentcli=mod)["model"],
                             "qwen3:8b")
            self.assertEqual(slashroute.resolve(command="verify", cli="x", model="qwen3:8b",
                                                explicit_model=True, agentcli=mod)["model"],
                             "qwen3:8b")

    def test_a_declaration_beats_the_definitions_variant(self):
        """移行期は併読するが、宣言が `agent` を言えばそちらが起動形を決める。"""
        mod = _StubAgentcli({}, by_cli={
            "ollama": {"verify": {"agent_cli": "ollama-verify", "default_model": ""}},
            "aider": {}})
        with _CommandHome() as home:
            home.add("verify", "---\nagent: aider\n---\n本文\n")
            routed = slashroute.resolve(command="verify", cli="ollama", agentcli=mod)
        self.assertEqual(routed["agent_cli"], "aider", "aider に verify の申告は無い")
        self.assertEqual(mod.calls, [("aider", "verify", None)], "ollama 側は引かない")

    def test_bundled_edit_is_the_only_place_that_names_aider(self):
        """設計 §3.6: aider の名前が出るのは宣言 1 行だけ。"""
        bundled = Path(__file__).resolve().parents[5] / "commands"
        declared = {p.stem: p.read_text(encoding="utf-8") for p in bundled.glob("*.md")
                    if p.stem == p.stem.lower()}
        self.assertEqual(sorted(declared), ["edit"])
        self.assertIn("agent: aider", declared["edit"])


class ClassifyTests(unittest.TestCase):
    def test_each_kind(self):
        with _CommandHome() as home:
            home.add("verify", "---\nagent: ollama\n---\n本文\n")
            self.assertEqual(slashroute.classify("help"), slashroute.KIND_SESSION)
            self.assertEqual(slashroute.classify("find"), slashroute.KIND_SHAPE)
            self.assertEqual(slashroute.classify("verify"), slashroute.KIND_PURPOSE)
            self.assertEqual(
                slashroute.classify("wiki-use", skill_exists=lambda n: n == "wiki-use"),
                slashroute.KIND_SKILL)
            self.assertEqual(slashroute.classify("verfy"), "unknown")

    def test_the_unknown_message_names_the_way_out(self):
        message = slashroute.unknown_command_message("tmp")
        self.assertIn("知らないコマンドです: /tmp", message)
        self.assertIn("/find", message, "使えるコマンドを並べる")
        self.assertIn("先頭に空行", message, "本文として送る書き方を示す")


class PlanTests(unittest.TestCase):
    """起動前に読む 1 回（設計 §3.2）。LLM は 1 回も呼ばれない。"""

    def test_a_shape_command_is_consumed_and_decides_the_toolset(self):
        found = slashroute.plan("/find\nどこにあるか調べて")
        self.assertEqual(found.body, "どこにあるか調べて")
        self.assertEqual((found.tools, found.toolset), (True, "read"))

    def test_ask_turns_the_tools_off(self):
        self.assertEqual(slashroute.plan("/ask\n本文").tools, False)

    def test_a_skill_stays_a_skill(self):
        got = slashroute.plan("/wiki-use 引数\n本文", skill_exists=lambda n: n == "wiki-use")
        self.assertEqual(got.skills, (("wiki-use", "引数"),))
        self.assertEqual(got.body, "本文")

    def test_an_unknown_name_stops(self):
        with self.assertRaises(slashroute.UnknownCommand):
            slashroute.plan("/verfy\n本文")

    def test_a_leading_blank_line_is_the_way_out(self):
        self.assertEqual(slashroute.plan("\n/tmp を消して").body, "\n/tmp を消して")

    def test_lenient_mode_consumes_nothing(self):
        warnings = []
        got = slashroute.plan("/verfy\n本文", strict=False, warn=warnings.append)
        self.assertEqual(got.body, "/verfy\n本文")
        self.assertTrue(warnings)

    def test_a_declaration_and_a_shape_compose(self):
        # 同梱定義に申告の無い名前を使う（`plan()` は変種の解決までやるので、申告が
        # あると宣言の綴りではなく振り替え先が返る——それは `resolve()` 側で見る）。
        with _CommandHome() as home:
            home.add("check", "---\nagent: ollama\ntools: []\noutput: json\n---\n判定役。\n")
            got = slashroute.plan("/check\n本文")
        self.assertEqual(got.agent, "ollama")
        self.assertEqual((got.tools, got.toolset), (False, ""))
        self.assertEqual(got.output, "json")
        self.assertEqual(got.system, "判定役。\n")

    def test_session_commands_are_reported_not_applied(self):
        got = slashroute.plan("/help\n本文")
        self.assertEqual(got.session, (("help", ""),))

    def test_no_command_line_is_a_no_op(self):
        self.assertEqual(slashroute.plan("ただの本文").body, "ただの本文")


if __name__ == "__main__":
    unittest.main()
