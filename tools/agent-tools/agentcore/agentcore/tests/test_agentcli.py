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


class TestLoad(_Isolated):
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
            ({"command": ["x"], "errors": [{"match": "([", "class": "env"}]}, "正規表現"),
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

    def test_spill_replaces_permission_args_and_passes_instruction(self):
        s = self.spec(prompt_via="argv", readonly_args=["--r"],
                      spill={"args": ["--read-only-fs"], "instruction": "read {file}"})
        r = agentcli.headless_cmd(s, "", "LONG", readonly=True, spill_path="/tmp/x.md")
        self.assertEqual(r["argv"], ["cli", "run", "--read-only-fs", "read /tmp/x.md"])
        self.assertNotIn("LONG", r["argv"])

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
                                   "ready_timeout_sec": 12, "prompt_inject": "file"})
        self.assertEqual(agentcli.ready_pattern(s, "D"), "PAT")
        self.assertEqual(agentcli.ready_timeout_sec(s), 12)
        self.assertEqual(agentcli.prompt_inject(s), "file")
        s = self.spec(interactive={"command": ["c"]})
        self.assertEqual(agentcli.ready_pattern(s, "D"), "D")
        self.assertEqual(agentcli.ready_timeout_sec(s), 60)
        self.assertEqual(agentcli.prompt_inject(s), "send-keys")


class TestReadonlyWarningAndErrors(_Isolated):
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


class TestBundledGolden(_Isolated):
    """同梱定義から出る argv を固定する（移行前のハードコード argv と一致することの担保）。

    ここが JS ローダとのゴールデン比較の基準にもなる（tools/agent-dashboard/test/agent-cli-golden）。
    """

    GOLDEN = {
        "kiro": {
            "write": ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools",
                      "--model", "M", "P"],
            "readonly": ["kiro-cli", "chat", "--no-interactive", "--trust-tools=",
                         "--model", "M", "P"],
            "interactive": ["kiro-cli", "chat", "--trust-all-tools", "--model", "M"],
        },
        "claude": {
            "write": ["claude", "-p", "--output-format", "text",
                      "--dangerously-skip-permissions", "--model", "M"],
            "readonly": ["claude", "-p", "--output-format", "text",
                         "--permission-mode", "plan", "--tools", "", "--model", "M"],
            "interactive": ["claude", "--model", "M"],
        },
        "copilot": {
            "write": ["copilot", "-s", "--allow-all-tools", "--no-color",
                      "--allow-all-paths", "--model", "M", "-p", "P"],
            "readonly": ["copilot", "-s", "--allow-all-tools", "--no-color",
                         "--available-tools=", "--disable-builtin-mcps",
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
            "write": ["ollama", "run", "M"],
            "readonly": ["ollama", "run", "M"],
            "interactive": ["ollama", "run", "M"],
        },
    }

    def test_golden(self):
        for name, want in self.GOLDEN.items():
            s = agentcli.load_cli(name)
            self.assertEqual(agentcli.headless_cmd(s, "M", "P")["argv"], want["write"], name)
            self.assertEqual(agentcli.headless_cmd(s, "M", "P", readonly=True)["argv"],
                             want["readonly"], name)
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


if __name__ == "__main__":
    unittest.main()
