"""agentcore.agentcli の単体テスト（探索順・正規化・argv 組み立て・モード別フラグ）。

実行: python3 -m unittest discover -s tools/agent-tools/agentcore/agentcore/tests
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import agentcli  # noqa: E402

BUNDLED = Path(__file__).resolve().parents[5] / "agents"


class _Isolated(unittest.TestCase):
    """実ホーム（~/.agents・~/.kiro）と開発者の cwd/agents が漏れないようにする。"""

    def setUp(self):
        agentcli.clear_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._env = {k: os.environ.get(k) for k in
                     ("KIRO_AGENTS_DIR", "AGENT_PROJECT_AGENTS_HOME", "HOME")}
        os.environ["AGENT_PROJECT_AGENTS_HOME"] = str(self.tmp / "no-agents-home")
        os.environ["HOME"] = str(self.tmp / "no-home")
        os.environ.pop("KIRO_AGENTS_DIR", None)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        agentcli.clear_cache()
        self._tmp.cleanup()

    def write_def(self, dirname: str, name: str, spec) -> Path:
        d = self.tmp / dirname
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{name}.json"
        p.write_text(spec if isinstance(spec, str) else json.dumps(spec), encoding="utf-8")
        return p


class TestSlashNative(_Isolated):
    """本文先頭のコマンド行を CLI へ残して渡すか、ランチャが消費するか（設計 2026-08-27 §3.2）。"""

    def _spec(self, body):
        path = self.write_def("agents", "x", {"command": ["x"], **body})
        os.environ["KIRO_AGENTS_DIR"] = str(path.parent)
        return agentcli.load_cli("x")

    def test_declaration_wins(self):
        self.assertFalse(self._spec({"headless_autonomy": "tool-loop",
                                     "slash_native": False})["slash_native"])
        agentcli.clear_cache()
        self.assertTrue(self._spec({"headless_autonomy": "single-shot",
                                    "slash_native": True})["slash_native"])

    def test_undeclared_falls_back_to_the_layer(self):
        """宣言していない定義（利用者が置いたものを含む）は今日と同じに振る舞う。

        以前この判定は `headless_autonomy == "tool-loop"` という代理で書かれていた。
        """
        self.assertTrue(self._spec({"headless_autonomy": "tool-loop"})["slash_native"])
        agentcli.clear_cache()
        self.assertFalse(self._spec({"headless_autonomy": "single-shot"})["slash_native"])
        agentcli.clear_cache()
        self.assertFalse(self._spec({})["slash_native"])   # 未宣言 = 安全側の single-shot

    def test_a_non_boolean_declaration_is_an_explicit_error(self):
        with self.assertRaises(agentcli.AgentCliError) as ctx:
            self._spec({"slash_native": "yes"})
        self.assertIn("slash_native", str(ctx.exception))

    def test_profiles_do_not_inherit_it(self):
        """起動の形ごとに決まる性質なので、profile は自分の層から導く。

        `ollama` は base が tool-loop（自分で先頭スラッシュを解釈する）だが、
        `ollama-json` などの single-shot profile ではハーネスが解決する側になる。
        """
        spec = self._spec({
            "headless_autonomy": "tool-loop", "slash_native": True,
            "profiles": {"one": {"command": ["x"], "headless_autonomy": "single-shot"}}})
        self.assertTrue(spec["slash_native"])
        self.assertFalse(agentcli.load_cli("x-one")["slash_native"])

    def test_bundled_definitions_declare_it(self):
        """同梱定義は代理に頼らず自分で言う（ファイルが振る舞いを説明する）。"""
        for name in ("claude", "codex", "kiro", "copilot", "cursor", "ollama"):
            self.assertIs(json.loads((BUNDLED / f"{name}.json").read_text(
                encoding="utf-8")).get("slash_native"), True, name)
        for name in ("aider", "vscode-copilot"):
            self.assertIs(json.loads((BUNDLED / f"{name}.json").read_text(
                encoding="utf-8")).get("slash_native"), False, name)


class TestLoad(_Isolated):
    def test_common_home_does_not_fall_back_to_dot_agent(self):
        os.environ.pop("AGENT_PROJECT_AGENTS_HOME")
        home = self.tmp / "home"
        (home / ".agent" / "agents").mkdir(parents=True)
        os.environ["HOME"] = str(home)
        self.assertEqual(agentcli._agents_home(), home / ".agents")

    def test_bundled_builtins_resolve(self):
        """組み込み 4 CLI が同梱定義から解決できる（コード側の分岐は無くなった）。"""
        for name in ("kiro", "claude", "copilot", "codex", "cursor", "ollama"):
            self.assertEqual(agentcli.load_cli(name)["name"], name)

    def test_project_dir_overrides_bundled(self):
        """上位（プロジェクトの agents/）に置いた定義が同梱定義に勝つ（first-wins）。"""
        self.write_def("proj/agents", "claude", {"command": ["my-claude"]})
        spec = agentcli.load_cli("claude", project_dir=str(self.tmp / "proj"), use_cache=False)
        self.assertEqual(spec["command"], ["my-claude"])

    def test_env_dir_wins_over_project(self):
        self.write_def("envd", "claude", {"command": ["env-claude"]})
        self.write_def("proj/agents", "claude", {"command": ["proj-claude"]})
        os.environ["KIRO_AGENTS_DIR"] = str(self.tmp / "envd")
        spec = agentcli.load_cli("claude", project_dir=str(self.tmp / "proj"), use_cache=False)
        self.assertEqual(spec["command"], ["env-claude"])

    def test_unknown_name_is_explicit_error(self):
        """未知の agent_cli は黙って別 CLI へ倒さず明示エラー。"""
        with self.assertRaises(agentcli.AgentCliError) as cm:
            agentcli.load_cli("no-such-cli-xyz", use_cache=False)
        self.assertIn("no-such-cli-xyz", str(cm.exception))

    def test_broken_definitions_raise(self):
        cases = [
            ({"command": "notalist"}, "command"),
            ({"command": []}, "command"),
            ({"command": ["x"], "output": "file"}, "output_file"),
            ({"command": ["x"], "output": "stderr"}, "output"),
            ({"command": ["x"], "prompt_via": "file"}, "prompt_via"),
            ({"command": ["x"], "env": "TOKEN=1"}, "env"),
            ({"command": ["x"], "env": []}, "env"),
            ({"command": ["x"], "errors": [{"match": "([", "class": "env"}]}, "正規表現"),
            ({"command": ["x"], "errors": [{"match": "x", "class": "quota",
                                                 "quota_kind": "unknown"}]}, "quota_kind"),
            ({"command": ["x"], "errors": ["env"]}, "errors"),
            ({"command": ["x"], "errors": {}}, "errors"),
            ({"command": ["x"], "readonly": "sometimes"}, "readonly"),
            ({"command": ["x"], "interactive": {"prompt_inject": "telepathy",
                                                "command": ["x"]}}, "prompt_inject"),
        ]
        for i, (spec, needle) in enumerate(cases):
            p = self.write_def("proj/agents", f"broken{i}", spec)
            with self.assertRaises(agentcli.AgentCliError, msg=str(spec)) as cm:
                agentcli.load_cli(f"broken{i}", project_dir=str(self.tmp / "proj"),
                                  use_cache=False)
            self.assertIn(needle, str(cm.exception), msg=str(p))

    def test_invalid_json_raises_rather_than_falling_through(self):
        self.write_def("proj/agents", "brokenjson", "{ not json")
        with self.assertRaises(agentcli.AgentCliError):
            agentcli.load_cli("brokenjson", project_dir=str(self.tmp / "proj"), use_cache=False)

    def test_usage_contract_reads_only_complete_stderr_marker(self):
        self.assertEqual(
            agentcli.parse_usage("log\n@agent-usage tokens_in=12 tokens_out=34\n"),
            (12, 34))
        self.assertEqual(agentcli.parse_usage("@agent-usage tokens_in=-1 tokens_out=2"),
                         (None, None))
        self.assertEqual(agentcli.parse_usage("@agent-usage tokens_in=1"), (None, None))


class TestHeadless(_Isolated):
    def spec(self, **over):
        base = {"command": ["cli", "run"], "prompt_via": "stdin", "model_flag": "--model"}
        base.update(over)
        self.write_def("proj/agents", "t", base)
        return agentcli.load_cli("t", project_dir=str(self.tmp / "proj"), use_cache=False)

    def test_model_placeholder_dropped_when_unset(self):
        s = self.spec(command=["cli", "run", "{model}"])
        self.assertEqual(agentcli.headless_cmd(s, "", "P")["argv"], ["cli", "run"])
        self.assertEqual(agentcli.headless_cmd(s, "m1", "P")["argv"], ["cli", "run", "m1"])

    def test_model_flag_only_when_no_placeholder(self):
        self.assertEqual(agentcli.headless_cmd(self.spec(), "m1", "P")["argv"],
                         ["cli", "run", "--model", "m1"])
        s = self.spec(command=["cli", "{model}"])
        self.assertNotIn("--model", agentcli.headless_cmd(s, "m1", "P")["argv"])

    def test_default_model_used_when_unset(self):
        s = self.spec(command=["cli", "{model}"], default_model="d")
        self.assertEqual(agentcli.headless_cmd(s, "", "P")["argv"], ["cli", "d"])

    def test_prompt_via_stdin_and_argv(self):
        s = self.spec()
        self.assertEqual(agentcli.headless_cmd(s, "", "P")["stdin"], "P")
        s = self.spec(prompt_via="argv")
        r = agentcli.headless_cmd(s, "", "P")
        self.assertIsNone(r["stdin"])
        self.assertEqual(r["argv"][-1], "P")
        s = self.spec(prompt_via="argv", prompt_flag="-p")
        self.assertEqual(agentcli.headless_cmd(s, "", "P")["argv"][-2:], ["-p", "P"])

    def test_output_file_placeholder(self):
        s = self.spec(command=["cli", "-o", "{output_file}"], output="file")
        r = agentcli.headless_cmd(s, "", "P")
        self.assertTrue(r["output_file"])
        self.assertIn(r["output_file"], r["argv"])
        os.unlink(r["output_file"])

    def test_mode_args(self):
        s = self.spec(write_args=["--w"], readonly_args=["--r"], no_session_args=["--n"])
        self.assertEqual(agentcli.headless_cmd(s, "", "P")["argv"], ["cli", "run", "--w"])
        self.assertEqual(agentcli.headless_cmd(s, "", "P", readonly=True)["argv"],
                         ["cli", "run", "--r"])
        self.assertEqual(agentcli.headless_cmd(s, "", "P", readonly=True, no_session=True)["argv"],
                         ["cli", "run", "--r", "--n"])

    def test_no_session_arg_not_duplicated(self):
        s = self.spec(readonly_args=["--x"], no_session_args=["--x"])
        self.assertEqual(agentcli.headless_cmd(s, "", "P", readonly=True, no_session=True)["argv"],
                         ["cli", "run", "--x"])

    def test_command_suffix_stays_last(self):
        s = self.spec(command_suffix=["-"], write_args=["--w"])
        self.assertEqual(agentcli.headless_cmd(s, "m", "P")["argv"],
                         ["cli", "run", "--w", "--model", "m", "-"])

    def test_spill_replaces_permission_args_and_appends_instruction(self):
        s = self.spec(prompt_via="argv", readonly_args=["--r"],
                      spill={"args": ["--read-only-fs"], "instruction": "read {file}"})
        r = agentcli.headless_cmd(s, "", "SHORT", readonly=True, spill_path="/tmp/x.md")
        # 権限フラグは置き換え、指示は呼び出し側のものへ **付け足す**（役割を消さない）
        self.assertEqual(r["argv"], ["cli", "run", "--read-only-fs", "SHORT read /tmp/x.md"])

    def test_spill_ignored_without_instruction(self):
        s = self.spec(prompt_via="argv", spill={"args": ["--x"]})
        r = agentcli.headless_cmd(s, "", "LONG", spill_path="/tmp/x.md")
        self.assertEqual(r["argv"][-1], "LONG")


class TestInteractive(_Isolated):
    def spec(self, **over):
        base = {"command": ["cli", "run"], "model_flag": "--model"}
        base.update(over)
        self.write_def("proj/agents", "t", base)
        return agentcli.load_cli("t", project_dir=str(self.tmp / "proj"), use_cache=False)

    def test_missing_section_is_error(self):
        with self.assertRaises(agentcli.AgentCliError):
            agentcli.interactive_cmd(self.spec(), "m")

    def test_model_expansion(self):
        s = self.spec(interactive={"command": ["cli", "chat"]})
        self.assertEqual(agentcli.interactive_cmd(s, "m"), ["cli", "chat", "--model", "m"])
        s = self.spec(interactive={"command": ["cli", "chat", "{model}"]})
        self.assertEqual(agentcli.interactive_cmd(s, "m"), ["cli", "chat", "m"])
        with self.assertRaises(agentcli.AgentCliError):
            agentcli.interactive_cmd(s, "")

    def test_readonly_inherits_toplevel_when_absent(self):
        s = self.spec(readonly_args=["--r"], interactive={"command": ["cli", "chat"]})
        self.assertEqual(agentcli.interactive_cmd(s, "", readonly=True), ["cli", "chat", "--r"])
        s = self.spec(readonly_args=["--r"],
                      interactive={"command": ["cli", "chat"], "readonly_args": ["--ir"]})
        self.assertEqual(agentcli.interactive_cmd(s, "", readonly=True), ["cli", "chat", "--ir"])

    def test_write_args_not_inherited_from_toplevel(self):
        """トップレベルの write_args はヘッドレス専用の危険フラグを含むので継承しない。"""
        s = self.spec(write_args=["--dangerous"], interactive={"command": ["cli", "chat"]})
        self.assertEqual(agentcli.interactive_cmd(s, ""), ["cli", "chat"])
        s = self.spec(write_args=["--dangerous"],
                      interactive={"command": ["cli", "chat"], "write_args": ["--trust"]})
        self.assertEqual(agentcli.interactive_cmd(s, ""), ["cli", "chat", "--trust"])

    def test_accessors(self):
        s = self.spec(interactive={"command": ["c"], "ready_pattern": "PAT",
                                   "failure_pattern": "FAIL", "ready_timeout_sec": 12,
                                   "ready_tail_lines": 8,
                                   "prompt_inject": "file"})
        self.assertEqual(agentcli.ready_pattern(s, "D"), "PAT")
        self.assertEqual(s["interactive"]["failure_pattern"], "FAIL")
        self.assertEqual(agentcli.ready_timeout_sec(s), 12)
        self.assertEqual(s["interactive"]["ready_tail_lines"], 8)
        self.assertEqual(agentcli.prompt_inject(s), "file")
        s = self.spec(interactive={"command": ["c"]})
        self.assertEqual(agentcli.ready_pattern(s, "D"), "D")
        self.assertEqual(agentcli.ready_timeout_sec(s), 60)
        self.assertEqual(agentcli.prompt_inject(s), "send-keys")


class TestReadonlyWarningAndErrors(_Isolated):
    def test_relative_cost_is_normalized_and_validated(self):
        self.write_def("proj/agents", "free", {"command": ["c"], "relative_cost": 0})
        spec = agentcli.load_cli("free", project_dir=str(self.tmp / "proj"), use_cache=False)
        self.assertEqual(spec["relative_cost"], 0.0)
        with self.assertRaisesRegex(agentcli.AgentCliError, "relative_cost"):
            agentcli.normalize("bad", {"command": ["c"], "relative_cost": -1}, "bad.json")

    def test_costlier_fallback_skips_equal_cost_and_takes_one_step(self):
        self.write_def("proj/agents", "free", {"command": ["c"], "relative_cost": 0})
        self.write_def("proj/agents", "peer", {"command": ["c"], "relative_cost": 0})
        self.write_def("proj/agents", "paid", {"command": ["c"], "relative_cost": 2})
        got = agentcli.costlier_fallback("free", [
            {"agent_cli": "peer"}, {"agent_cli": "paid", "model": "large"}],
            str(self.tmp / "proj"))
        self.assertEqual((got["agent_cli"], got["model"], got["to_relative_cost"]),
                         ("paid", "large", 2.0))

    def test_best_effort_warns_enforced_does_not(self):
        self.write_def("proj/agents", "be", {"command": ["c"], "readonly": "best-effort"})
        self.write_def("proj/agents", "en", {"command": ["c"], "readonly": "enforced"})
        proj = str(self.tmp / "proj")
        be = agentcli.load_cli("be", project_dir=proj, use_cache=False)
        en = agentcli.load_cli("en", project_dir=proj, use_cache=False)
        self.assertTrue(agentcli.readonly_warning(be, True))
        self.assertEqual(agentcli.readonly_warning(be, False), "")
        self.assertEqual(agentcli.readonly_warning(en, True), "")
        self.assertTrue(agentcli.headless_cmd(be, "", "P", readonly=True)["readonly_warning"])

    def test_classify_error(self):
        self.write_def("proj/agents", "e", {"command": ["c"], "errors": [
            {"match": "rate limit", "class": "quota", "hint": "待て"},
            {"match": "not logged in", "class": "auth", "hint": "ログイン"}]})
        s = agentcli.load_cli("e", project_dir=str(self.tmp / "proj"), use_cache=False)
        self.assertEqual(agentcli.classify_error(s, "Error: RATE LIMIT reached"),
                         ("quota", "待て"))
        self.assertEqual(agentcli.classify_error(s, "you are not logged in")[0], "auth")
        self.assertIsNone(agentcli.classify_error(s, "something else"))

    def test_quota_detail_and_reset_time(self):
        cases = [
            ("kiro", "Monthly request limit reached", "exhausted", None),
            ("claude", "Rate limit: retry after 120 seconds", "rate_limit",
             "1970-01-01T00:02:00Z"),
            ("codex", "Too many requests; reset at 2026-08-09T03:00:00+09:00",
             "rate_limit", "2026-08-08T18:00:00Z"),
            ("cursor", "Usage limit reached", "exhausted", None),
        ]
        for name, message, kind, reset_at in cases:
            result = agentcli.classify_error(
                agentcli.load_cli(name, use_cache=False), message, detailed=True, now=0)
            self.assertEqual(result["class"], "quota", name)
            self.assertEqual(result["quota_kind"], kind, name)
            self.assertEqual(result["reset_at"], reset_at, name)


class TestBundledGolden(_Isolated):
    """同梱定義から出る argv を固定する（移行前のハードコード argv と一致することの担保）。

    ここが JS ローダとのゴールデン比較の基準にもなる（tools/agent-dashboard/test/agent-cli-golden）。
    """

    GOLDEN = {
        "kiro": {
            "write": ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools",
                      "--model", "M", "P"],
            "readonly": ["kiro-cli", "chat", "--no-interactive", "--trust-tools=fs_read",
                         "--model", "M", "P"],
            "interactive": ["kiro-cli", "chat", "--trust-all-tools", "--model", "M"],
        },
        "claude": {
            "write": ["claude", "-p", "--output-format", "text",
                      "--dangerously-skip-permissions", "--model", "M"],
            "readonly": ["claude", "-p", "--output-format", "text",
                         "--permission-mode", "plan", "--model", "M"],
            "interactive": ["claude", "--model", "M"],
        },
        "copilot": {
            "write": ["copilot", "-s", "--allow-all-tools", "--no-color",
                      "--allow-all-paths", "--model", "M", "-p", "P"],
            "readonly": ["copilot", "-s", "--allow-all-tools", "--no-color",
                         "--available-tools=view,grep,glob", "--disable-builtin-mcps",
                         "--no-custom-instructions", "--model", "M", "-p", "P"],
            "interactive": ["copilot", "--model", "M"],
        },
        "cursor": {
            "write": ["cursor-agent", "-p", "--output-format", "text", "--force",
                      "--model", "M"],
            "readonly": ["cursor-agent", "-p", "--output-format", "text", "--force",
                         "--mode", "ask", "--model", "M"],
            "interactive": ["cursor-agent", "--model", "M"],
        },
        "ollama": {
            # --tools は write のときだけ生える（ループとツールを書き込みモードに閉じる）。
            # readonly は素の text→text なので `readonly: enforced` の宣言が嘘にならない。
            # 予算は write だけ 12 ラウンドへ絞ってある（read セットの ollama-read は 30
            # のまま）。実測の空回り run に「もう少し回れば畳めた」形跡が無く、30 まで
            # 回せること自体がターンの食いつぶしだった。読取は 1 ラウンドが安いので別。
            # think は write（道具ループ）と format 併用で off。実測（2026-08-10・ログ
            # 236 本）で write の on は 1 ラウンドが思考だけで 7700 トークン・12 分
            # （p90 942 秒 > agent_timeout 600 秒）、--format 併用は本文が空になる
            # （文法が thinking から掛かる。39/39 件）。
            # readonly だけ on（2026-08-31 に反転）: readonly は材料がプロンプト内で完結する
            # 面で、思考が唯一の計算になる。e4b の実測（MP1・道具ゼロ）は think off 1/5 →
            # on 5/5・中央値 46 秒。2026-08-10 の「readonly on は中央値 1000 秒」は当時の
            # モデル（qwen 系）の数字で、herd の既定（gemma4）では再現しない。
            "write": ["agent-herd", "ollama", "M", "--think", "off", "--tools", "bash",
                      "--max-rounds", "12", "--command-timeout", "900"],
            "readonly": ["agent-herd", "ollama", "M", "--think", "on"],
            "interactive": ["agent-herd", "ollama", "--tui", "--think", "on", "M"],
        },
        "ollama-json": {
            # JSON 契約の役割用。--format json は文法レベルの強制で、道具は持たせない
            # （JSON しか出せない状態でツールループの規約は成立しない）。
            "write": ["agent-herd", "ollama", "--think", "off", "--format", "json", "M"],
            "readonly": ["agent-herd", "ollama", "--think", "off", "--format", "json", "M"],
        },
        "ollama-read": {
            # 探索が要る readonly 役割用。write 経路に read セットを載せ、権限はゲートが絞る。
            "write": ["agent-herd", "ollama", "--think", "off", "M", "--tools", "read",
                      "--max-rounds", "30", "--command-timeout", "900"],
            "readonly": ["agent-herd", "ollama", "--think", "off", "M"],
        },
    }

    def test_golden(self):
        for name, want in self.GOLDEN.items():
            s = agentcli.load_cli(name)
            self.assertEqual(agentcli.headless_cmd(s, "M", "P")["argv"], want["write"], name)
            self.assertEqual(agentcli.headless_cmd(s, "M", "P", readonly=True)["argv"],
                             want["readonly"], name)
            if "interactive" in want:
                self.assertEqual(agentcli.interactive_cmd(s, "M"), want["interactive"], name)

    def test_codex_golden_with_output_file(self):
        """codex だけは {output_file} が実行毎に変わるので、そこを伏せて比較する。"""
        s = agentcli.load_cli("codex")
        r = agentcli.headless_cmd(s, "M", "P")
        argv = [("<out>" if t == r["output_file"] else t) for t in r["argv"]]
        self.assertEqual(argv, ["codex", "exec", "--skip-git-repo-check", "--color", "never",
                                "--output-last-message", "<out>",
                                "--dangerously-bypass-approvals-and-sandbox", "--model", "M", "-"])
        self.assertEqual(r["stdin"], "P")
        os.unlink(r["output_file"])

    def test_all_bundled_definitions_are_loadable(self):
        """agents/*.json が全部読める（壊れた定義を同梱しない）。"""
        for p in sorted(BUNDLED.glob("*.json")):
            agentcli.load_cli(p.stem, use_cache=False)


class TestSpillPrompt(_Isolated):
    """argv 長制限の退避（P1-2）。3 ツール（agent-project / agent-flow / agent-amigos）が
    同じ 12 行を別々に持っていたものの集約先。"""

    def spec(self, **over):
        base = {"command": ["cli", "run"], "prompt_via": "stdin", "model_flag": "--model"}
        base.update(over)
        self.write_def("proj/agents", "t", base)
        return agentcli.load_cli("t", project_dir=str(self.tmp / "proj"), use_cache=False)

    def test_short_prompt_is_untouched(self):
        path, text = agentcli.spill_prompt("short", 100, prompt_via="argv",
                                           prefix="t-", instruction="read {file}")
        self.assertIsNone(path)
        self.assertEqual(text, "short")

    def test_stdin_is_never_spilled(self):
        # stdin 渡しは ARG_MAX に当たらない（退避すると本文を無駄に往復させるだけ）。
        path, text = agentcli.spill_prompt("x" * 500, 10, prompt_via="stdin",
                                           prefix="t-", instruction="read {file}")
        self.assertIsNone(path)
        self.assertEqual(len(text), 500)

    def test_large_prompt_is_written_and_referenced(self):
        big = "x" * 500
        path, text = agentcli.spill_prompt(big, 10, prompt_via="argv",
                                           prefix="t-", instruction="read {file} first")
        self.addCleanup(os.remove, path)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), big)
        self.assertEqual(text, f"read {path} first")
        self.assertNotIn(big, text)

    def test_limit_falls_back_to_the_builtin_default(self):
        path, _text = agentcli.spill_prompt("x" * 500, 0, prompt_via="argv",
                                            prefix="t-", instruction="read {file}")
        self.assertIsNone(path, "0 以下は既定（100000）へ戻す")

    def test_instruction_frame_is_shared(self):
        """退避の指示文は「何の全文か」だけが呼び出し側の裁量で、枠は共通（P2-5）。

        「必ずファイルの内容を読み込ませる」という**効き目に関わる部分**を 3 者が別々に
        持つと、言い回しの改善が 1 か所にしか入らない（入っていない方は誰も気付かない）。"""
        text = agentcli.spill_instruction("このタスクの全文")
        self.assertIn("{file}", text)
        self.assertIn("このタスクの全文", text)
        self.assertIn("必ずファイルの内容を読み込み", text)
        self.assertTrue(text.endswith(": {file}"))
        # 読んだあと何をするかも呼び出し側が決められる（役割ごとに違う）
        self.assertIn("その内容を対象にしてください",
                      agentcli.spill_instruction("入力の全文", then="その内容を対象にしてください"))

    def test_instruction_frame_is_used_by_spill_prompt(self):
        path, text = agentcli.spill_prompt(
            "x" * 500, 10, prompt_via="argv", prefix="t-",
            instruction=agentcli.spill_instruction("このターンの全文"))
        self.addCleanup(os.remove, path)
        self.assertIn(path, text)
        self.assertNotIn("{file}", text)

    def test_measured_in_bytes_not_characters(self):
        # 日本語は 1 文字 3 バイト。文字数で測ると ARG_MAX の手前で見逃す。
        path, _text = agentcli.spill_prompt("あ" * 20, 30, prompt_via="argv",
                                            prefix="t-", instruction="read {file}")
        self.assertIsNotNone(path)
        os.remove(path)

    def test_permission_flags_are_untouched(self):
        """退避しても権限フラグを落とさない——`headless_cmd(spill_path=…)` との違い。

        定義側の spill は権限フラグを spill.args（kiro では `--trust-tools=fs_read`）へ
        置き換える読み取り専用向けの機構で、実行して確かめる呼び出しに掛けると
        1 つもコマンドを実行できなくなる。"""
        spec = self.spec(prompt_via="argv", write_args=["--trust-all-tools"],
                         spill={"args": ["--trust-tools=fs_read"], "instruction": "read {file}"})
        path, text = agentcli.spill_prompt("x" * 500, 10, prompt_via="argv",
                                           prefix="t-", instruction="read {file}")
        self.addCleanup(os.remove, path)
        argv = agentcli.headless_cmd(spec, "", text)["argv"]      # spill_path は渡さない
        self.assertIn("--trust-all-tools", argv)
        self.assertNotIn("--trust-tools=fs_read", argv)


class TestIdleDetectionFields(_Isolated):
    """待機判定フィールド（busy_pattern / idle_quiet_sec / clear_command）の正規化。

    判定方法は CLI ごとに違う（入力欄を出したまま処理する TUI では ready の消失が
    起きない）ため、定義側の宣言をローダが素通しで届けることを固定する。
    """

    def spec(self, **inter):
        base = {"command": ["cli", "run"],
                "interactive": {"command": ["cli", "chat"], **inter}}
        self.write_def("proj/agents", "idle", base)
        return agentcli.load_cli("idle", project_dir=str(self.tmp / "proj"), use_cache=False)

    def test_defaults(self):
        s = self.spec()
        self.assertEqual(agentcli.busy_pattern(s), "")
        self.assertEqual(agentcli.idle_quiet_sec(s), 0)
        self.assertEqual(agentcli.clear_command(s), "/clear")

    def test_declared_values_pass_through(self):
        s = self.spec(busy_pattern="esc to interrupt", idle_quiet_sec=4,
                      clear_command="/new")
        self.assertEqual(agentcli.busy_pattern(s), "esc to interrupt")
        self.assertEqual(agentcli.idle_quiet_sec(s), 4.0)
        self.assertEqual(agentcli.clear_command(s), "/new")

    def test_empty_clear_command_declares_no_clear(self):
        """空文字は「クリア手段なし」の宣言。既定 /clear と区別して保持する。"""
        s = self.spec(clear_command="")
        self.assertEqual(agentcli.clear_command(s), "")

    def test_clear_command_without_interactive_defaults(self):
        base = {"command": ["cli", "run"]}
        self.write_def("proj/agents", "noint", base)
        s = agentcli.load_cli("noint", project_dir=str(self.tmp / "proj"), use_cache=False)
        self.assertEqual(agentcli.clear_command(s), "/clear")


if __name__ == "__main__":
    unittest.main()
