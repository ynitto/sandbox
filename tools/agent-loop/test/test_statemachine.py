#!/usr/bin/env python3
"""statemachine サブコマンド（headless CLI 向けステートマシンハーネス）の単体テスト。

対象: agent_loop/statemachine.py — パス検証、限定ツール契約、JSON 応答の抽出、
スタブ aider による複数状態の完走（未実行の成功申告を安全な書込へ補正して終端へ遷移）。
dashboard の旧 in-process 実行器（stateMachineRunner.js）のテストを移植したもの。
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import agent_loop as al  # noqa: E402

sys.path.insert(0, str(HERE.parent.parent / "agent-tools" / "agentcore"))
from agentcore import agentcli  # noqa: E402


class ProjectPathTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_rejects_parent_escape(self):
        with self.assertRaisesRegex(al.StateMachineHarnessError, "作業フォルダ外"):
            al._sm_project_path(self.repo, "../outside")

    def test_rejects_symlink_escape(self):
        outside = tempfile.mkdtemp(prefix="agent-loop-sm-outside-")
        self.addCleanup(lambda: os.rmdir(outside))
        os.symlink(outside, os.path.join(self.repo, "escape"))
        with self.assertRaisesRegex(al.StateMachineHarnessError, "作業フォルダ外"):
            al._sm_project_path(self.repo, "escape/secret.txt")

    def test_accepts_nested_new_path(self):
        target = al._sm_project_path(self.repo, "a/b/new.txt")
        self.assertTrue(target.startswith(self.repo))


class ToolRequestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-req-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_rejects_shell(self):
        with self.assertRaisesRegex(al.StateMachineHarnessError, "シェル"):
            al._sm_validate_tool_request(
                {"type": "run", "command": "bash", "args": []}, self.repo, [])

    def test_python_script_runs_via_python(self):
        script = os.path.join(self.repo, "tool.py")
        pathlib.Path(script).write_text('print("ok")\n', encoding="utf-8")
        request = al._sm_validate_tool_request(
            {"type": "run", "command": script, "args": ["x"]}, self.repo, [])
        self.assertRegex(os.path.basename(request["command"]), r"^python")
        self.assertEqual(request["args"][0], os.path.realpath(script),
                         "非実行のPythonスクリプトはPython経由にする")

    def test_skill_relative_arg_is_allowed(self):
        skill_dir = tempfile.mkdtemp(prefix="agent-loop-sm-skill-")
        self.addCleanup(lambda: __import__("shutil").rmtree(skill_dir, ignore_errors=True))
        script = os.path.join(skill_dir, "tool.py")
        pathlib.Path(script).write_text('print("ok")\n', encoding="utf-8")
        rel = os.path.relpath(script, self.repo)
        al._sm_validate_tool_request(
            {"type": "run", "command": os.path.basename(sys.executable), "args": [rel]},
            self.repo, [{"name": "s", "root": skill_dir}])

    def test_unknown_type_rejected(self):
        with self.assertRaisesRegex(al.StateMachineHarnessError, "許可されていない"):
            al._sm_validate_tool_request({"type": "spawn"}, self.repo, [])


class ParseAndStatusTest(unittest.TestCase):
    def test_terminal_status(self):
        self.assertTrue(al._sm_terminal_status("complete", "OK")["ok"])
        self.assertFalse(al._sm_terminal_status("failed", "FETCH_FAILED")["ok"])
        self.assertFalse(al._sm_terminal_status("complete", "FETCH_FAILED")["ok"])

    def test_parse_tool_request_in_markdown(self):
        self.assertEqual(
            al._sm_parse_tool_request('warning\n```json\n{"type":"final","output":"OK"}\n```\n'),
            {"type": "final", "output": "OK"})

    def test_parse_tool_request_picks_last_object(self):
        self.assertEqual(
            al._sm_parse_tool_request(
                'example {"type":"read_files","paths":["x"]}\n'
                'answer {"type":"final","output":"OK"}'),
            {"type": "final", "output": "OK"},
            "説明中の契約例ではなく最後の完全な応答を選ぶ")

    def test_validated_output_recovers_contract_tail(self):
        self.assertEqual(
            al._sm_validated_output("aider warning\nOK\n\npath: out.txt", "startswith:OK"),
            "OK\npath: out.txt")


class RunStatemachineTest(unittest.TestCase):
    """スタブ aider で「final の成功申告 → 書込補正 → 検証 → 終端遷移」を完走させる。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-run-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        machine = pathlib.Path(self.repo, ".statemachine", "one-step")
        (machine / "actions").mkdir(parents=True)
        (machine / "workflow.yaml").write_text("\n".join([
            "name: one step",
            "initial_state: make",
            "config:",
            "  max_steps: 3",
            "states:",
            "  make:",
            "    action_file: actions/make.md",
            "    output_key: made",
            '    output_validator: "startswith:OK"',
            "  complete:",
            "    terminal: true",
            "transitions:",
            "  - from: make",
            "    to: complete",
            '    condition_rule: "startswith:last_output:OK"',
            "",
        ]), encoding="utf-8")
        (machine / "actions" / "make.md").write_text(
            "`input.txt` を読み、out.txt を生成し、第1行に OK と返す。\n", encoding="utf-8")
        pathlib.Path(self.repo, "input.txt").write_text("source\n", encoding="utf-8")
        fake = pathlib.Path(self.repo, "fake-aider.py")
        fake.write_text("\n".join([
            "import sys",
            "argv = sys.argv",
            "def val(flag):",
            "    return argv[argv.index(flag) + 1] if flag in argv else ''",
            "if '--dry-run' not in argv:",
            "    with open(val('--file'), 'w') as f:",
            "        f.write('done\\n')",
            "    print('aider warning\\nOK\\n\\npath: out.txt')",
            "else:",
            '    print(\'{"type":"final","output":"OK\\\\npath: out.txt"}\')',
            "",
        ]), encoding="utf-8")
        self.spec = agentcli.normalize("fake-aider", {
            "name": "fake-aider",
            "command": [sys.executable, str(fake)],
            "prompt_via": "argv",
            "prompt_flag": "--message",
            "file_flag": "--file",
            "read_flag": "--read",
            "readonly_args": ["--dry-run"],
            "readonly": "enforced",
            "timeout": 10,
        }, pathlib.Path(self.repo, "fake-aider.json"))

    def test_completes_to_terminal_state(self):
        result = al.run_statemachine(
            workflow_path=os.path.join(self.repo, ".statemachine", "one-step", "workflow.yaml"),
            cwd=self.repo,
            parameters={},
            agent={"cli": "fake-aider", "spec": self.spec, "model": "fake",
                   "agentcli": agentcli},
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["finalState"], "complete")
        self.assertRegex(result["stdout"], r"^OK")
        self.assertEqual(pathlib.Path(self.repo, "out.txt").read_text(encoding="utf-8"),
                         "done\n")
        self.assertTrue(os.path.exists(result["logFile"]), "argv・cwd・結果を残すログがある")
        events = [json.loads(line)
                  for line in pathlib.Path(result["logFile"]).read_text(encoding="utf-8")
                  .strip().splitlines()]
        input_file = os.path.realpath(os.path.join(self.repo, "input.txt"))
        self.assertTrue(
            any(isinstance(e.get("argv"), list)
                and "--read" in e["argv"] and input_file in e["argv"] for e in events),
            "アクションが参照する既存ファイルを Aider に割り当てる")


class ParamParsingTest(unittest.TestCase):
    def test_param_pairs_and_input(self):
        params = al._sm_parse_params(["topic=llm", "context.depth=2"], "本文")
        self.assertEqual(params, {"topic": "llm", "context.depth": "2", "input": "本文"})

    def test_param_requires_key_value(self):
        with self.assertRaisesRegex(al.StateMachineHarnessError, "KEY=VALUE"):
            al._sm_parse_params(["broken"], None)


if __name__ == "__main__":
    unittest.main()
