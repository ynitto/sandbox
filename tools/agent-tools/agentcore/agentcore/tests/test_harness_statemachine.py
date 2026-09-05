#!/usr/bin/env python3
"""statemachine サブコマンド（headless CLI 向けステートマシンハーネス）の単体テスト。

対象: agent_loop/statemachine.py — パス検証、限定ツール契約、JSON 応答の抽出、
スタブ aider による複数状態の完走（未実行の成功申告を安全な書込へ補正して終端へ遷移）。
dashboard の旧 in-process 実行器（stateMachineRunner.js）のテストを移植したもの。
"""
import argparse
import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from agentcore import agentcli  # noqa: E402
from agentcore.harness import statemachine as sm  # noqa: E402
from agentcore.harness import toolloop as tl  # noqa: E402
from agentcore.tests.harnesspatch import patch_harness  # noqa: E402


class ProjectPathTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_rejects_parent_escape(self):
        with self.assertRaisesRegex(sm.StateMachineHarnessError, "作業フォルダ外"):
            sm._sm_project_path(self.repo, "../outside")

    def test_rejects_symlink_escape(self):
        outside = tempfile.mkdtemp(prefix="agent-loop-sm-outside-")
        self.addCleanup(lambda: os.rmdir(outside))
        os.symlink(outside, os.path.join(self.repo, "escape"))
        with self.assertRaisesRegex(sm.StateMachineHarnessError, "作業フォルダ外"):
            sm._sm_project_path(self.repo, "escape/secret.txt")

    def test_accepts_nested_new_path(self):
        target = sm._sm_project_path(self.repo, "a/b/new.txt")
        self.assertTrue(target.startswith(self.repo))


class ToolRequestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-req-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_rejects_shell(self):
        with self.assertRaisesRegex(sm.StateMachineHarnessError, "シェル"):
            sm._sm_validate_tool_request(
                {"type": "run", "command": "bash", "args": []}, self.repo, [])

    def test_python_script_runs_via_python(self):
        script = os.path.join(self.repo, "tool.py")
        pathlib.Path(script).write_text('print("ok")\n', encoding="utf-8")
        request = sm._sm_validate_tool_request(
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
        sm._sm_validate_tool_request(
            {"type": "run", "command": os.path.basename(sys.executable), "args": [rel]},
            self.repo, [{"name": "s", "root": skill_dir}])

    def test_unknown_type_rejected(self):
        with self.assertRaisesRegex(sm.StateMachineHarnessError, "許可されていない"):
            sm._sm_validate_tool_request({"type": "spawn"}, self.repo, [])

    def test_shell_script_runs_via_fixed_interpreter(self):
        # shebang に従うと「どのシェルが走るか」をスクリプト側が決められてしまう。
        script = os.path.join(self.repo, "tool.sh")
        pathlib.Path(script).write_text("#!/usr/bin/env zsh\necho ok\n", encoding="utf-8")
        request = sm._sm_validate_tool_request(
            {"type": "run", "command": script, "args": []}, self.repo, [])
        self.assertIn(os.path.basename(request["command"]), ("bash", "sh"))
        self.assertEqual(request["args"][0], os.path.realpath(script))

    @unittest.skipUnless(__import__("shutil").which("node"), "node が無い環境")
    def test_js_script_runs_via_node(self):
        script = os.path.join(self.repo, "tool.js")
        pathlib.Path(script).write_text("console.log('ok')\n", encoding="utf-8")
        request = sm._sm_validate_tool_request(
            {"type": "run", "command": script, "args": []}, self.repo, [])
        self.assertEqual(os.path.basename(request["command"]), "node")
        self.assertEqual(request["args"][0], os.path.realpath(script))

    def test_missing_interpreter_is_rejected(self):
        script = os.path.join(self.repo, "tool.js")
        pathlib.Path(script).write_text("console.log('ok')\n", encoding="utf-8")
        with patch_harness("_tl_executable_on_path",
                           side_effect=lambda name: "" if name == "node" else "/bin/" + name):
            with self.assertRaisesRegex(sm.StateMachineHarnessError, "インタプリタ"):
                sm._sm_validate_tool_request(
                    {"type": "run", "command": script, "args": []}, self.repo, [])


class ParseAndStatusTest(unittest.TestCase):
    def test_terminal_status(self):
        self.assertTrue(sm._sm_terminal_status("complete", "OK")["ok"])
        self.assertFalse(sm._sm_terminal_status("failed", "FETCH_FAILED")["ok"])
        self.assertFalse(sm._sm_terminal_status("complete", "FETCH_FAILED")["ok"])

    def test_parse_tool_request_in_markdown(self):
        self.assertEqual(
            sm._sm_parse_tool_request('warning\n```json\n{"type":"final","output":"OK"}\n```\n'),
            {"type": "final", "output": "OK"})

    def test_parse_tool_request_picks_last_object(self):
        self.assertEqual(
            sm._sm_parse_tool_request(
                'example {"type":"read_files","paths":["x"]}\n'
                'answer {"type":"final","output":"OK"}'),
            {"type": "final", "output": "OK"},
            "説明中の契約例ではなく最後の完全な応答を選ぶ")

    def test_validated_output_recovers_contract_tail(self):
        self.assertEqual(
            sm._sm_validated_output("aider warning\nOK\n\npath: out.txt", "startswith:OK"),
            "OK\npath: out.txt")

    def test_planner_prompt_has_no_copyable_path_placeholder(self):
        prompt = sm._sm_planner_prompt(
            action="input.json を読む", cwd="/repo", skills=[], reads=[], history=[], retry="")
        self.assertNotIn("relative/path", prompt)
        self.assertIn(
            '{"type":"run","command":"executable","args":["arg"],"timeout_sec":60}',
            prompt)

    def test_planner_prompt_only_claims_run_after_success(self):
        initial = sm._sm_planner_prompt(
            action="tool.py を実行", cwd="/repo", skills=[], reads=[], history=[], retry="")
        completed = sm._sm_planner_prompt(
            action="tool.py を実行", cwd="/repo", skills=[], reads=[],
            history=['TOOL_RESULT {"type":"run","status":0}'], retry="")
        self.assertNotIn("already completed", initial)
        self.assertIn("already completed", completed)
        self.assertIn("request write_files", initial)


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
        result = sm.run_statemachine(
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

    def test_read_request_handles_existing_and_mixed_missing_paths(self):
        input_file = os.path.realpath(os.path.join(self.repo, "input.txt"))
        cases = {
            "existing": ["input.txt"],
            "mixed-missing": ["relative/path", "input.txt"],
        }
        for name, paths in cases.items():
            with self.subTest(name=name):
                fake = pathlib.Path(self.repo, f"fake-{name}-read.py")
                fake.write_text("\n".join([
                    "import json, sys",
                    f"target = {input_file!r}",
                    "if target in sys.argv:",
                    "    print(json.dumps({'type': 'final', 'output': 'OK'}))",
                    "else:",
                    f"    print(json.dumps({{'type': 'read_files', 'paths': {paths!r}}}))",
                    "",
                ]), encoding="utf-8")
                spec = agentcli.normalize(name, {
                    "name": name,
                    "command": [sys.executable, str(fake)],
                    "prompt_via": "argv",
                    "prompt_flag": "--message",
                    "read_flag": "--read",
                    "readonly": "enforced",
                    "timeout": 10,
                }, pathlib.Path(self.repo, f"fake-{name}-read.json"))

                result = sm.run_statemachine(
                    workflow_path=os.path.join(
                        self.repo, ".statemachine", "one-step", "workflow.yaml"),
                    cwd=self.repo,
                    parameters={},
                    agent={"cli": name, "spec": spec, "model": "fake",
                           "agentcli": agentcli},
                )

                self.assertTrue(result["ok"], result.get("error"))

    def test_cli_handles_gemma_placeholder_read_and_completes(self):
        pathlib.Path(self.repo, "input.txt").write_text(
            "source evidence\n", encoding="utf-8")
        pathlib.Path(self.repo, "out.txt").write_text("stale\n", encoding="utf-8")
        control = pathlib.Path(self.repo, "fake-ollama-json.py")
        control.write_text("\n".join([
            "import json, sys",
            "argv = sys.argv",
            "prompt = argv[argv.index('--message') + 1]",
            "if 'source evidence' in prompt:",
            "    print(json.dumps({'type': 'final', 'output': 'OK\\npath: out.txt'}))",
            "else:",
            "    print(json.dumps({'type': 'read_files', "
            "'paths': ['relative/path', 'input.txt']}))",
            "",
        ]), encoding="utf-8")
        editor = pathlib.Path(self.repo, "fake-aider.py")
        editor.write_text("\n".join([
            "import pathlib, sys",
            "argv = sys.argv",
            "target = pathlib.Path(argv[argv.index('--file') + 1])",
            "if not target.read_text(encoding='utf-8'):",
            "    target.write_text('done\\n', encoding='utf-8')",
            "print('Applied edit to out.txt')",
            "",
        ]), encoding="utf-8")
        agents = pathlib.Path(self.repo, "agents")
        agents.mkdir()
        common = {
            "prompt_via": "argv", "prompt_flag": "--message",
            "read_flag": "--read", "model_flag": "--model", "timeout": 10,
        }
        (agents / "fake-aider.json").write_text(json.dumps({
            **common,
            "command": [sys.executable, str(editor)],
            "file_flag": "--file",
            "variants": {"planner": "fake-ollama-json"},
        }), encoding="utf-8")
        (agents / "fake-ollama-json.json").write_text(json.dumps({
            **common,
            "command": [sys.executable, str(control)],
        }), encoding="utf-8")

        stdout = io.StringIO()
        args = argparse.Namespace(
            workflow=".statemachine/one-step/workflow.yaml",
            agent_cli="fake-aider", model="gemma4:e4b", param=[], input=None,
            dir=self.repo,
        )
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", io.StringIO()), \
                self.assertRaises(SystemExit) as exit_info:
            sm.cmd_statemachine(args, pathlib.Path(self.repo))

        line = [line for line in stdout.getvalue().splitlines()
                if line.startswith("RESULT ")][-1]
        result = json.loads(line[len("RESULT "):])
        self.assertEqual(exit_info.exception.code, 0, result)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["finalState"], "complete")
        self.assertEqual(pathlib.Path(self.repo, "out.txt").read_text(encoding="utf-8"),
                         "done\n")
        events = [json.loads(line) for line in pathlib.Path(
            result["logFile"]).read_text(encoding="utf-8").splitlines()]
        argv = [event.get("argv", []) for event in events if event.get("event") == "finish"]
        self.assertTrue(any(str(control) in call for call in argv), "制御はollama-json変種を通る")
        self.assertTrue(any(str(editor) in call for call in argv), "書き込みはaiderを通る")
        self.assertTrue(any(event.get("event") == "write_completed"
                            and event.get("contractSource") == "machine"
                            for event in events), "Aiderの契約文に依存せず完了する")

    def test_silent_editor_completes_via_machine_contract(self):
        # 「黙って直す」編集 CLI は実在する。空 stdout を失敗にすると成果物があるのに落ちる。
        fake = pathlib.Path(self.repo, "fake-silent.py")
        fake.write_text("\n".join([
            "import sys",
            "argv = sys.argv",
            "if '--dry-run' in argv:",
            "    print('{\"type\":\"final\",\"output\":\"OK\\\\npath: out.txt\"}')",
            "else:",
            "    with open(argv[argv.index('--file') + 1], 'w') as f:",
            "        f.write('done\\n')",
            "",
        ]), encoding="utf-8")
        spec = agentcli.normalize("silent", {
            "name": "silent",
            "command": [sys.executable, str(fake)],
            "prompt_via": "argv",
            "prompt_flag": "--message",
            "file_flag": "--file",
            "read_flag": "--read",
            "readonly_args": ["--dry-run"],
            "readonly": "enforced",
            "timeout": 10,
        }, pathlib.Path(self.repo, "silent.json"))

        result = sm.run_statemachine(
            workflow_path=os.path.join(
                self.repo, ".statemachine", "one-step", "workflow.yaml"),
            cwd=self.repo, parameters={},
            agent={"cli": "silent", "spec": spec, "model": "fake", "agentcli": agentcli})

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["finalState"], "complete")
        self.assertEqual(pathlib.Path(self.repo, "out.txt").read_text(encoding="utf-8"),
                         "done\n")

    def test_write_request_replaces_preexisting_output(self):
        output = pathlib.Path(self.repo, "out.txt")
        output.write_text("stale\n", encoding="utf-8")
        fake = pathlib.Path(self.repo, "fake-replace.py")
        fake.write_text("\n".join([
            "import pathlib, sys",
            "argv = sys.argv",
            "def val(flag):",
            "    return argv[argv.index(flag) + 1] if flag in argv else ''",
            "if '--dry-run' in argv:",
            "    print('{\"type\":\"final\",\"output\":\"OK\\\\npath: out.txt\"}')",
            "else:",
            "    target = pathlib.Path(val('--file'))",
            "    if not target.read_text(encoding='utf-8'):",
            "        target.write_text('fresh\\n', encoding='utf-8')",
            "    print('OK\\npath: out.txt')",
            "",
        ]), encoding="utf-8")
        spec = agentcli.normalize("replace", {
            "name": "replace",
            "command": [sys.executable, str(fake)],
            "prompt_via": "argv",
            "prompt_flag": "--message",
            "file_flag": "--file",
            "read_flag": "--read",
            "readonly_args": ["--dry-run"],
            "readonly": "enforced",
            "timeout": 10,
        }, pathlib.Path(self.repo, "fake-replace.json"))

        result = sm.run_statemachine(
            workflow_path=os.path.join(
                self.repo, ".statemachine", "one-step", "workflow.yaml"),
            cwd=self.repo,
            parameters={},
            agent={"cli": "replace", "spec": spec, "model": "fake",
                   "agentcli": agentcli},
        )

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(output.read_text(encoding="utf-8"), "fresh\n")

    def test_write_failure_restores_preexisting_output(self):
        output = pathlib.Path(self.repo, "out.txt")
        output.write_text("stale\n", encoding="utf-8")
        workflow = os.path.join(
            self.repo, ".statemachine", "one-step", "workflow.yaml")
        state = {
            "action_file": "actions/make.md",
            "output_validator": "startswith:OK",
        }
        responses = [
            '{"type":"final","output":"OK\\npath: out.txt"}',
            sm.StateMachineHarnessError("editor failed"),
        ]

        with patch_harness("_tl_run_agent", side_effect=responses):
            with self.assertRaisesRegex(sm.StateMachineHarnessError, "editor failed"):
                sm._sm_execute_action(
                    workflow_path=workflow, state_id="make", state=state, context={},
                    cwd=self.repo, agent={}, log_file=os.path.join(self.repo, "run.jsonl"),
                    touched=set())

        self.assertEqual(output.read_text(encoding="utf-8"), "stale\n")
        self.assertEqual(list(pathlib.Path(self.repo).glob("*.agent-loop-*.bak")), [])

    def test_contract_without_file_change_is_rejected(self):
        output = pathlib.Path(self.repo, "out.txt")
        output.write_text("stale\n", encoding="utf-8")
        responses = [
            '{"type":"final","output":"OK\\npath: out.txt"}',
            "OK\npath: out.txt",
        ]

        with patch_harness("_SM_MAX_TOOL_ROUNDS", 1), \
                patch_harness("_tl_run_agent", side_effect=responses):
            with self.assertRaisesRegex(sm.StateMachineHarnessError, "Output Contract"):
                sm._sm_execute_action(
                    workflow_path=os.path.join(
                        self.repo, ".statemachine", "one-step", "workflow.yaml"),
                    state_id="make",
                    state={"action_file": "actions/make.md",
                           "output_validator": "startswith:OK"},
                    context={}, cwd=self.repo, agent={},
                    log_file=os.path.join(self.repo, "run.jsonl"), touched=set())

        self.assertEqual(output.read_text(encoding="utf-8"), "stale\n")
        self.assertEqual(list(pathlib.Path(self.repo).glob("*.agent-loop-*.bak")), [])

    def test_invalid_write_output_restores_preexisting_output(self):
        output = pathlib.Path(self.repo, "out.txt")
        output.write_text("stale\n", encoding="utf-8")
        responses = [
            '{"type":"final","output":"OK\\npath: out.txt"}',
            "BROKEN",
        ]

        def fake_agent(*_args, readonly=False, files=None, **_kwargs):
            response = responses.pop(0)
            if not readonly:
                pathlib.Path(files[0]).write_text("fresh\n", encoding="utf-8")
            return response

        with patch_harness("_tl_run_agent", side_effect=fake_agent):
            with self.assertRaisesRegex(sm.StateMachineHarnessError, "Output Contract"):
                sm._sm_execute_action(
                    workflow_path=os.path.join(
                        self.repo, ".statemachine", "one-step", "workflow.yaml"),
                    state_id="make",
                    state={"action_file": "actions/make.md",
                           "output_validator": "startswith:BUG,FEATURE"},
                    context={}, cwd=self.repo, agent={},
                    log_file=os.path.join(self.repo, "run.jsonl"), touched=set())

        self.assertEqual(output.read_text(encoding="utf-8"), "stale\n")
        self.assertEqual(list(pathlib.Path(self.repo).glob("*.agent-loop-*.bak")), [])

    def test_changed_file_gets_machine_contract_when_editor_omits_it(self):
        output = pathlib.Path(self.repo, "out.txt")
        output.write_text("stale\n", encoding="utf-8")
        prompts = []
        responses = [
            '{"type":"final","output":"OK\\npath: out.txt"}',
            "Applied edit to out.txt",
        ]

        def fake_agent(_agent, prompt, *, readonly=False, files=None, **_kwargs):
            prompts.append(prompt)
            response = responses.pop(0)
            if not readonly:
                pathlib.Path(files[0]).write_text("fresh\n", encoding="utf-8")
            return response

        with patch_harness("_tl_run_agent", side_effect=fake_agent):
            result = sm._sm_execute_action(
                workflow_path=os.path.join(
                    self.repo, ".statemachine", "one-step", "workflow.yaml"),
                state_id="make",
                state={"action_file": "actions/make.md", "output_validator": "startswith:OK"},
                context={}, cwd=self.repo, agent={},
                log_file=os.path.join(self.repo, "run.jsonl"), touched=set())

        self.assertEqual(result, "OK\npath: out.txt")
        self.assertEqual(output.read_text(encoding="utf-8"), "fresh\n")
        self.assertEqual(len(prompts), 2, "書込後にLLMへ契約生成を再依頼しない")
        events = [json.loads(line) for line in pathlib.Path(
            self.repo, "run.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(event.get("event") == "write_completed"
                            and event.get("contractSource") == "machine"
                            for event in events))
        self.assertEqual(list(pathlib.Path(self.repo).glob("*.agent-loop-*.bak")), [])

    def test_machine_contract_refuses_ambiguous_success_routes(self):
        workflow = pathlib.Path(self.repo, ".statemachine", "ambiguous.yaml")
        workflow.write_text("\n".join([
            "states:",
            "  make:",
            "    output_key: result",
            "  bug:",
            "    terminal: true",
            "  feature:",
            "    terminal: true",
            "transitions:",
            "  - from: make",
            "    to: bug",
            '    condition_rule: "startswith:result:BUG"',
            "  - from: make",
            "    to: feature",
            '    condition_rule: "startswith:result:FEATURE"',
            "",
        ]), encoding="utf-8")

        result = sm._sm_write_success_output(
            workflow_path=str(workflow), state_id="make",
            state={"output_key": "result"}, validator="startswith:BUG,FEATURE",
            files={os.path.join(self.repo, "out.txt")}, cwd=self.repo)

        self.assertEqual(result, "")

    def test_machine_contract_selects_digest_success_not_failure_route(self):
        workflow = pathlib.Path(self.repo, ".statemachine", "digest.yaml")
        workflow.write_text("\n".join([
            "states:",
            "  write_digest: {}",
            "  verify_digest: {}",
            "  failed:",
            "    terminal: true",
            "transitions:",
            "  - from: write_digest",
            "    to: verify_digest",
            '    condition_rule: "startswith:last_output:DIGEST_OK"',
            "  - from: write_digest",
            "    to: failed",
            '    condition_rule: "startswith:last_output:DIGEST_FAILED"',
            "",
        ]), encoding="utf-8")

        result = sm._sm_write_success_output(
            workflow_path=str(workflow), state_id="write_digest", state={},
            validator="startswith:DIGEST_OK,DIGEST_FAILED",
            files={os.path.join(self.repo, "deliveries", "tech-digest.md")}, cwd=self.repo)

        self.assertEqual(result, "DIGEST_OK\npath: deliveries/tech-digest.md")

    def test_read_request_returns_small_file_content_to_control_agent(self):
        input_file = pathlib.Path(self.repo, "input.txt")
        input_file.write_text("source evidence\n", encoding="utf-8")

        def fake_agent(_agent, prompt, **_kwargs):
            if "source evidence" in prompt:
                return '{"type":"final","output":"OK"}'
            return '{"type":"read_files","paths":["input.txt"]}'

        with patch_harness("_tl_run_agent", side_effect=fake_agent):
            output = sm._sm_execute_action(
                workflow_path=os.path.join(
                    self.repo, ".statemachine", "one-step", "workflow.yaml"),
                state_id="make",
                state={"action_file": "actions/make.md", "output_validator": "startswith:OK"},
                context={}, cwd=self.repo, agent={},
                log_file=os.path.join(self.repo, "run.jsonl"), touched=set())

        self.assertEqual(output, "OK")

    def test_unmentioned_skill_script_is_not_executed(self):
        skill = pathlib.Path(self.repo, ".github", "skills", "demo")
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text("# demo\n", encoding="utf-8")
        script = skill / "scripts" / "mutate.py"
        script.write_text("raise SystemExit('must not run')\n", encoding="utf-8")
        action = pathlib.Path(
            self.repo, ".statemachine", "one-step", "actions", "make.md")
        action.write_text(
            "`demo` スキルと `input.txt` を使い out.txt を生成する。\n",
            encoding="utf-8")
        readonly_responses = iter([
            json.dumps({"type": "run", "command": str(script), "args": []}),
            '{"type":"final","output":"OK\\npath: out.txt"}',
        ])

        def fake_agent(*_args, readonly=False, files=None, **_kwargs):
            if readonly:
                return next(readonly_responses)
            pathlib.Path(files[0]).write_text("fresh\n", encoding="utf-8")
            return "OK\npath: out.txt"

        with patch_harness("_tl_run_agent", side_effect=fake_agent), \
                patch_harness("_sm_exec_argv") as execute:
            output = sm._sm_execute_action(
                workflow_path=os.path.join(
                    self.repo, ".statemachine", "one-step", "workflow.yaml"),
                state_id="make",
                state={"action_file": "actions/make.md", "output_validator": "startswith:OK"},
                context={}, cwd=self.repo, agent={},
                log_file=os.path.join(self.repo, "run.jsonl"), touched=set())

        self.assertRegex(output, r"^OK")
        execute.assert_not_called()

    def test_successful_run_is_not_executed_twice(self):
        skill = pathlib.Path(self.repo, ".github", "skills", "demo")
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text("# demo\n", encoding="utf-8")
        script = skill / "scripts" / "once.py"
        script.write_text("print('done')\n", encoding="utf-8")
        action = pathlib.Path(
            self.repo, ".statemachine", "one-step", "actions", "make.md")
        action.write_text(
            "`demo` スキルの `once.py` を実行して OK を返す。\n", encoding="utf-8")
        run = json.dumps({"type": "run", "command": str(script), "args": []})
        responses = iter([run, run, '{"type":"final","output":"OK"}'])

        with patch_harness("_tl_run_agent", side_effect=lambda *_a, **_k: next(responses)), \
                patch_harness("_sm_exec_argv", return_value={
                    "status": 0, "error": "", "stdout": "done\n", "stderr": "",
                }) as execute:
            output = sm._sm_execute_action(
                workflow_path=os.path.join(
                    self.repo, ".statemachine", "one-step", "workflow.yaml"),
                state_id="make",
                state={"action_file": "actions/make.md", "output_validator": "startswith:OK"},
                context={}, cwd=self.repo, agent={},
                log_file=os.path.join(self.repo, "run.jsonl"), touched=set())

        self.assertEqual(output, "OK")
        self.assertEqual(execute.call_count, 1)

    def test_different_successful_runs_are_both_executed(self):
        skill = pathlib.Path(self.repo, ".github", "skills", "demo")
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text("# demo\n", encoding="utf-8")
        scripts = [skill / "scripts" / name for name in ("one.py", "two.py")]
        for script in scripts:
            script.write_text("print('done')\n", encoding="utf-8")
        action = pathlib.Path(
            self.repo, ".statemachine", "one-step", "actions", "make.md")
        action.write_text(
            "`demo` スキルの `one.py` と `two.py` を実行して OK を返す。\n",
            encoding="utf-8")
        responses = iter([
            json.dumps({"type": "run", "command": str(script), "args": []})
            for script in scripts
        ] + ['{"type":"final","output":"OK"}'])

        with patch_harness("_tl_run_agent", side_effect=lambda *_a, **_k: next(responses)), \
                patch_harness("_sm_exec_argv", return_value={
                    "status": 0, "error": "", "stdout": "done\n", "stderr": "",
                }) as execute:
            output = sm._sm_execute_action(
                workflow_path=os.path.join(
                    self.repo, ".statemachine", "one-step", "workflow.yaml"),
                state_id="make",
                state={"action_file": "actions/make.md", "output_validator": "startswith:OK"},
                context={}, cwd=self.repo, agent={},
                log_file=os.path.join(self.repo, "run.jsonl"), touched=set())

        self.assertEqual(output, "OK")
        self.assertEqual(execute.call_count, 2)


class ActionContractTest(unittest.TestCase):
    """アクションの成功判定。拒否されたツール要求を成功と読み替えない。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-action-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        machine = pathlib.Path(self.repo, ".statemachine", "one-step")
        (machine / "actions").mkdir(parents=True)
        (machine / "workflow.yaml").write_text("states: {make: {}}\n", encoding="utf-8")
        (machine / "actions" / "make.md").write_text("OK を返す。\n", encoding="utf-8")
        self.workflow = str(machine / "workflow.yaml")
        self.state = {"action_file": "actions/make.md", "output_validator": "startswith:OK"}

    def _execute(self):
        return sm._sm_execute_action(
            workflow_path=self.workflow, state_id="make", state=self.state, context={},
            cwd=self.repo, agent={}, log_file=os.path.join(self.repo, "run.jsonl"),
            touched=set())

    def test_rejected_tool_request_is_not_accepted_as_output(self):
        # 契約文の形をした行を添えた不正なツール要求。拒否 = やっていない。
        with patch_harness("_SM_MAX_TOOL_ROUNDS", 1), \
                patch_harness("_tl_run_agent",
                              return_value='OK\n{"type":"spawn","output":"OK"}'):
            with self.assertRaisesRegex(sm.StateMachineHarnessError, "Output Contract"):
                self._execute()

    def test_plain_text_contract_is_still_accepted(self):
        # ツール要求ですらない素の本文は、従来どおり契約文として拾う。
        with patch_harness("_tl_run_agent", return_value="OK"):
            self.assertEqual(self._execute(), "OK")


class SkillScriptPolicyTest(unittest.TestCase):
    """スキルへ移譲したときに実行してよいスクリプトの範囲。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-skill-policy-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.skill = pathlib.Path(self.repo, ".github", "skills", "demo")
        (self.skill / "scripts").mkdir(parents=True)
        for name in ("entry.py", "internal.py"):
            (self.skill / "scripts" / name).write_text("print('done')\n", encoding="utf-8")
        (self.skill / "SKILL.md").write_text(
            "# demo\n\n入口は `python scripts/entry.py` だけ。\n", encoding="utf-8")
        machine = pathlib.Path(self.repo, ".statemachine", "one-step")
        (machine / "actions").mkdir(parents=True)
        (machine / "workflow.yaml").write_text("states: {make: {}}\n", encoding="utf-8")
        # アクションはスキル名だけ明記し、スクリプト名は書かない。
        (machine / "actions" / "make.md").write_text(
            "`demo` スキルへ移譲して OK を返す。\n", encoding="utf-8")
        self.workflow = str(machine / "workflow.yaml")

    def _execute(self, script_name):
        script = str(self.skill / "scripts" / script_name)
        responses = iter([json.dumps({"type": "run", "command": script, "args": []}),
                          '{"type":"final","output":"OK"}'])
        with patch_harness("_SM_MAX_TOOL_ROUNDS", 2), \
                patch_harness("_tl_run_agent",
                              side_effect=lambda *_a, **_k: next(responses)), \
                patch_harness("_sm_exec_argv", return_value={
                    "status": 0, "error": "", "stdout": "done\n", "stderr": "",
                }) as execute:
            try:
                output = sm._sm_execute_action(
                    workflow_path=self.workflow, state_id="make",
                    state={"action_file": "actions/make.md",
                           "output_validator": "startswith:OK"},
                    context={}, cwd=self.repo, agent={},
                    log_file=os.path.join(self.repo, "run.jsonl"), touched=set())
            except StopIteration:
                output = ""
        return output, execute

    def test_script_declared_in_skill_md_is_allowed(self):
        output, execute = self._execute("entry.py")

        self.assertEqual(output, "OK")
        self.assertEqual(execute.call_count, 1, "SKILL.md が入口として載せたスクリプトは実行する")

    def test_script_absent_from_skill_md_is_rejected(self):
        output, execute = self._execute("internal.py")

        self.assertEqual(output, "OK")
        execute.assert_not_called()

    def test_empty_stdout_run_is_reported_as_success(self):
        # 何も印字しないコマンドは珍しくない。stdout の有無で成否を推測させると、
        # モデルは「失敗した」と読んで同じコマンドを回し続ける。
        script = str(self.skill / "scripts" / "entry.py")
        prompts = []
        responses = iter([json.dumps({"type": "run", "command": script, "args": []}),
                          '{"type":"final","output":"OK"}'])

        def fake_agent(_agent, prompt, **_kwargs):
            prompts.append(prompt)
            return next(responses)

        with patch_harness("_tl_run_agent", side_effect=fake_agent), \
                patch_harness("_sm_exec_argv", return_value={
                    "status": 0, "error": "", "stdout": "", "stderr": "",
                }):
            output = sm._sm_execute_action(
                workflow_path=self.workflow, state_id="make",
                state={"action_file": "actions/make.md", "output_validator": "startswith:OK"},
                context={}, cwd=self.repo, agent={},
                log_file=os.path.join(self.repo, "run.jsonl"), touched=set())

        self.assertEqual(output, "OK")
        self.assertIn('"ok": true', prompts[1])


class AutoAdvanceExecutionTest(unittest.TestCase):
    """auto_advance が省くのは条件評価だけ。アクション実行と成功確認は残る。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-advance-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        machine = pathlib.Path(self.repo, ".statemachine", "advance")
        (machine / "actions").mkdir(parents=True)
        (machine / "workflow.yaml").write_text("\n".join([
            "initial_state: make",
            "config:",
            "  max_steps: 3",
            "states:",
            "  make:",
            "    action_file: actions/make.md",
            '    output_validator: "startswith:OK"',
            "  complete:",
            "    terminal: true",
            "transitions:",
            "  - from: make",           # 無条件 = auto_advance
            "    to: complete",
            "",
        ]), encoding="utf-8")
        (machine / "actions" / "make.md").write_text("OK を返す。\n", encoding="utf-8")
        self.workflow = str(machine / "workflow.yaml")

    def _run(self, response):
        with patch_harness("_SM_MAX_TOOL_ROUNDS", 1), \
                patch_harness("_tl_run_agent", return_value=response):
            return sm.run_statemachine(workflow_path=self.workflow, cwd=self.repo,
                                       parameters={}, agent={})

    def test_failed_action_does_not_advance(self):
        with self.assertRaisesRegex(sm.StateMachineHarnessError, "Output Contract"):
            self._run("BROKEN")

    def test_successful_action_advances_without_condition_eval(self):
        result = self._run('{"type":"final","output":"OK"}')

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["finalState"], "complete")


class ControlRetryTest(unittest.TestCase):
    """制御応答の再試行は一時障害だけ、回数も限る。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-retry-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.log_file = os.path.join(self.repo, "run.jsonl")

    def _run(self, side_effect):
        with patch_harness("_tl_run_agent", side_effect=side_effect) as agent:
            try:
                return tl._tl_run_control({}, "prompt", cwd=self.repo, read_files=[],
                                          log_file=self.log_file), agent
            except tl.ToolLoopError as exc:
                return exc, agent

    def test_transient_failure_is_retried(self):
        result, agent = self._run([tl.ToolLoopError("fake-cli がタイムアウトしました"), "OK"])

        self.assertEqual(result, "OK")
        self.assertEqual(agent.call_count, 2)

    def test_permanent_failure_is_not_retried(self):
        result, agent = self._run(tl.ToolLoopError("許可されていないツール要求です: spawn"))

        self.assertIsInstance(result, tl.ToolLoopError)
        self.assertEqual(agent.call_count, 1, "恒久的な失敗をクレジット分だけ繰り返さない")

    def test_retry_count_is_bounded(self):
        result, agent = self._run(tl.ToolLoopError("rate limit"))

        self.assertIsInstance(result, tl.ToolLoopError)
        self.assertEqual(agent.call_count, tl._TL_CONTROL_RETRIES + 1)


class RuntimeContextTest(unittest.TestCase):
    def test_today_and_now_are_provided(self):
        context = sm._sm_initial_context({}, {})

        self.assertRegex(context["today"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(context["now"].startswith(context["today"]), context["now"])

    def test_parameters_still_win(self):
        context = sm._sm_initial_context({}, {"today": "2000-01-01"})

        self.assertEqual(context["today"], "2000-01-01")


class NextStateContractTest(unittest.TestCase):
    """next_state.py の呼び出しが statemachine-use の現行契約に沿うこと。

    廃止済みの --last-output / --output は使わず、状態値は --context JSON で渡す。
    condition_rule で決まる遷移は --auto-eval だけで確定させ、LLM は呼ばない。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-next-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.workflow = os.path.join(self.repo, "workflow.yaml")
        pathlib.Path(self.workflow).write_text("\n".join([
            "initial_state: classify",
            "states:",
            "  classify:",
            "    output_key: kind",
            "  bug:",
            "    terminal: true",
            "  ask:",
            "    terminal: true",
            "transitions:",
            "  - from: classify",
            "    to: bug",
            '    condition_rule: "startswith:kind:BUG"',
            "    priority: 1",
            "  - from: classify",
            "    to: ask",
            '    condition: "{{last_output}} が判断できない場合"',
            "    priority: 2",
            "",
        ]), encoding="utf-8")
        skill = sm._sm_resolve_skill("statemachine-use", self.repo)
        self.assertIsNotNone(skill, "statemachine-use スキルの実体が必要")
        self.scripts = {"next": os.path.join(skill["root"], "scripts", "next_state.py")}
        self.log_file = os.path.join(self.repo, "run.jsonl")

    def _next(self, output, outputs, agent_response=None):
        calls = []

        def fake_agent(*_args, **_kwargs):
            calls.append(_args)
            return agent_response

        with patch_harness("_tl_run_agent", side_effect=fake_agent):
            state = sm._sm_next_state(
                scripts=self.scripts, workflow_path=self.workflow, state_id="classify",
                output=output, outputs=outputs, agent={}, cwd=self.repo,
                log_file=self.log_file)
        events = [json.loads(line) for line
                  in pathlib.Path(self.log_file).read_text(encoding="utf-8").splitlines()]
        return state, [e["argv"] for e in events
                       if e.get("event") == "finish" and self.scripts["next"] in e["argv"]], calls

    def test_condition_rule_route_skips_llm(self):
        state, argv, calls = self._next("BUG in login", {"kind": "BUG in login"})

        self.assertEqual(state, "bug")
        self.assertEqual(calls, [], "condition_rule だけで決まる遷移でLLMを呼ばない")
        self.assertEqual(len(argv), 1, "--auto-eval だけで確定し --eval を呼ばない")
        self.assertIn("--auto-eval", argv[0])
        context = json.loads(argv[0][argv[0].index("--context") + 1])
        self.assertEqual(context, {"last_output": "BUG in login", "kind": "BUG in login"})

    def test_llm_condition_is_finalized_with_evals(self):
        state, argv, calls = self._next("???", {"kind": "???"}, agent_response='{"1": true}')

        self.assertEqual(state, "ask")
        self.assertEqual(len(calls), 1, "LLM評価が要る条件だけ問い合わせる")
        self.assertEqual(len(argv), 2)
        self.assertIn("--evals", argv[1])
        self.assertEqual(json.loads(argv[1][argv[1].index("--evals") + 1]), {"1": True})
        self.assertEqual(json.loads(argv[1][argv[1].index("--context") + 1]),
                         {"last_output": "???", "kind": "???"})

    def _fake_next_state(self, payload: dict):
        """auto_advance を返す next_state.py（fork 側の応答形）を差し込む。"""
        fake = pathlib.Path(self.repo, "fake-next-state.py")
        fake.write_text("print(%r)\n" % json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        self.scripts = {"next": str(fake)}

    def test_auto_advance_response_needs_no_conditions(self):
        # 条件が無いステート: conditions を返さず next_state だけ返る応答を受け付ける。
        self._fake_next_state({"state": "classify", "auto_advance": True, "next_state": "bug"})

        state, _argv, calls = self._next("何でもよい", {})

        self.assertEqual(state, "bug")
        self.assertEqual(calls, [], "auto_advance でLLMを呼ばない")

    def test_real_script_emits_auto_advance_for_unconditional_transition(self):
        # 偽スクリプトではなく実物の next_state.py と噛み合うことを確かめる。
        self.workflow = os.path.join(self.repo, "chain.yaml")
        pathlib.Path(self.workflow).write_text("\n".join([
            "initial_state: classify",
            "states:",
            "  classify: {}",
            "  parse:",
            "    terminal: true",
            "transitions:",
            "  - from: classify",
            "    to: parse",
            "",
        ]), encoding="utf-8")

        state, argv, calls = self._next("なんでもよい", {})

        self.assertEqual(state, "parse")
        self.assertEqual(calls, [], "無条件トランジションでLLMを呼ばない")
        self.assertEqual(len(argv), 1, "--auto-eval だけで確定し --eval を呼ばない")

    def test_response_without_conditions_or_next_state_is_rejected(self):
        self._fake_next_state({"state": "classify"})

        with self.assertRaisesRegex(sm.StateMachineHarnessError, "条件リストを解析できません"):
            self._next("何でもよい", {})

    def test_deprecated_flags_are_gone(self):
        _state, argv, _calls = self._next("???", {"kind": "???"}, agent_response='{"1": false}')

        for call in argv:
            self.assertNotIn("--last-output", call)
            self.assertNotIn("--output", call)
            self.assertNotIn("--list-conditions", call)


class NextStateContractGateTest(unittest.TestCase):
    """配布された statemachine-use がハーネスの契約と噛み合うかを起動時に確かめる。

    ハーネスは `--auto-eval` を値の無いフラグとして渡す。解決された実体が古い配布
    （旧 `--list-conditions`）や `--auto-eval` が値を取る変種だと、実行の途中で
    argparse の生エラーだけが上がり、どの実体が使われたのかも分からない
    （実測: 定常業務の実行が `argument --auto-eval: expected one argument` を残して落ちた）。
    """

    def test_current_contract_passes(self):
        self.assertEqual(sm._sm_next_state_contract_error(
            "usage: next_state.py [-h] [--state STATE] [--auto-eval] [--context JSON]"), "")

    def test_old_distribution_is_named(self):
        problem = sm._sm_next_state_contract_error(
            "usage: next_state.py [-h] [--state STATE] [--list-conditions]")
        self.assertIn("古い配布", problem)

    def test_value_taking_variant_is_named(self):
        problem = sm._sm_next_state_contract_error(
            "usage: next_state.py [-h] [--auto-eval AUTO_EVAL] [--context JSON]")
        self.assertIn("値を取る変種", problem)

    def test_usage_wrapped_across_lines_is_normalized(self):
        self.assertEqual(sm._sm_next_state_contract_error(
            "usage: next_state.py [-h] [--state STATE]\n"
            "                     [--auto-eval] [--context JSON]"), "")

    def _repo_with_skill(self, next_state_body):
        tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-gate-")
        self.addCleanup(tmp.cleanup)
        repo = os.path.realpath(tmp.name)
        skill = pathlib.Path(repo, ".github", "skills", "statemachine-use")
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text("# statemachine-use\n", encoding="utf-8")
        (skill / "scripts" / "next_state.py").write_text(next_state_body, encoding="utf-8")
        (skill / "scripts" / "run_machine.py").write_text("print('ok')\n", encoding="utf-8")
        pathlib.Path(repo, "workflow.yaml").write_text("\n".join([
            "initial_state: work",
            "states:",
            "  work:",
            "    terminal: true",
            "",
        ]), encoding="utf-8")
        return repo

    # `--auto-eval` が値を取る変種（利用者の環境で実際に起きた形）。
    _VARIANT = (
        "import argparse\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('workflow', nargs='?')\n"
        "p.add_argument('--state')\n"
        "p.add_argument('--context')\n"
        "p.add_argument('--auto-eval')\n"
        "p.parse_args()\n"
    )

    def test_variant_stops_before_any_agent_call(self):
        repo = self._repo_with_skill(self._VARIANT)
        calls = []

        with patch_harness("_tl_run_agent", side_effect=lambda *a, **k: calls.append(a)):
            with self.assertRaises(sm.StateMachineHarnessError) as ctx:
                sm.run_statemachine(workflow_path="workflow.yaml", cwd=repo, agent={})

        message = str(ctx.exception)
        self.assertIn("値を取る変種", message)
        # どの実体が使われたのかを必ず名指しする（探索先が複数あり、人が直せるのはここだけ）。
        self.assertIn(os.path.join(".github", "skills", "statemachine-use"), message)
        self.assertIn("install.py", message)
        self.assertEqual(calls, [], "契約が噛み合わないときは LLM を 1 回も呼ばない")

    def test_real_distribution_passes_the_gate(self):
        """実物のスキルで誤検知しないこと（ゲートが正常な実行を止めない）。"""
        tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-gate-ok-")
        self.addCleanup(tmp.cleanup)
        repo = os.path.realpath(tmp.name)
        skill = sm._sm_resolve_skill("statemachine-use", repo)
        self.assertIsNotNone(skill, "statemachine-use スキルの実体が必要")
        sm._sm_require_next_state_contract(
            os.path.join(skill["root"], "scripts", "next_state.py"),
            cwd=repo, log_file=os.path.join(repo, "run.jsonl"))


class CheckGateTest(unittest.TestCase):
    """決定的検査（check）— 遷移の材料を自己申告からハーネスの実測へ移す。

    実測（tools/agent-tools/eval/results/archive/2026-08-13-t1-decomposition-report.md）:
    検知を伴わない分解は受入を下げ（0/3）、決定的な検知 + 再投入で 3/3 になった。
    ここで固定するのは、その検知が**モデルの申告では満たせない**ことと、
    上限到達が失敗一般ではなく段の昇格シグナルとして出てくることの 2 点。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-check-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.counter = os.path.join(self.repo, "attempts")
        self.machine = pathlib.Path(self.repo, ".statemachine", "gated")
        (self.machine / "actions").mkdir(parents=True)
        (self.machine / "actions" / "work.md").write_text("作業する。\n", encoding="utf-8")

    def _workflow(self, *, passes_on: int, retries: int, extra: str = "",
                  gated: bool = True) -> str:
        """`passes_on` 回目の action で初めて通る検査を持つワークフローを書く。"""
        script = pathlib.Path(self.repo, "check.py")
        script.write_text(
            "import pathlib, sys\n"
            f"p = pathlib.Path({self.counter!r})\n"
            "n = int(p.read_text()) if p.exists() else 0\n"
            f"print('attempts=%d' % n)\n"
            f"sys.exit(0 if n >= {passes_on} else 1)\n", encoding="utf-8")
        check = json.dumps([sys.executable, str(script)])
        lines = [
            "name: gated",
            "initial_state: work",
            "config:",
            "  max_steps: 3",
            "states:",
            "  work:",
            "    action_file: actions/work.md",
            '    output_validator: "startswith:OK"',
        ]
        if gated:
            lines += [f"    check: {check}", f"    check_retries: {retries}"]
            lines += [f"    {line}" for line in extra.splitlines() if line.strip()]
        lines += [
            "  done:",
            "    terminal: true",
            "transitions:",
            "  - from: work",
            "    to: done",
            '    condition_rule: "%s"' % ("equals:check_ok:true" if gated
                                          else "startswith:last_output:OK"),
            "",
        ]
        (self.machine / "workflow.yaml").write_text("\n".join(lines), encoding="utf-8")
        return str(self.machine / "workflow.yaml")

    def _run(self, workflow: str, response: str = '{"type":"final","output":"OK"}'):
        calls: list = []

        def fake_agent(*args, **_kwargs):
            calls.append(args)
            pathlib.Path(self.counter).write_text(str(len(calls)), encoding="utf-8")
            return response

        with patch_harness("_SM_MAX_TOOL_ROUNDS", 1), \
                patch_harness("_tl_run_agent", side_effect=fake_agent):
            try:
                return sm.run_statemachine(workflow_path=workflow, cwd=self.repo,
                                           parameters={}, agent={}), calls
            except sm.StateMachineHarnessError as exc:
                return exc, calls

    def test_passing_check_advances_and_costs_no_extra_call(self):
        result, calls = self._run(self._workflow(passes_on=1, retries=2))

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["finalState"], "done")
        self.assertEqual(len(calls), 1, "通る課題にゲートは課金しない")

    def test_declared_check_output_reaches_the_transition_context(self):
        workflow = self._workflow(passes_on=1, retries=0)
        result, _calls = self._run(workflow)

        events = [json.loads(line) for line
                  in pathlib.Path(result["logFile"]).read_text(encoding="utf-8").splitlines()]
        checks = [e for e in events if e.get("event") == "check"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["check_ok"], "true")
        self.assertEqual(checks[0]["check_status"], "0")
        # 遷移は測った値で決まっている（--context に検査結果が載る）。
        contexts = [json.loads(e["argv"][e["argv"].index("--context") + 1]) for e in events
                    if e.get("event") == "finish" and "--context" in (e.get("argv") or [])]
        self.assertTrue(any(c.get("check_ok") == "true" for c in contexts), contexts)

    def test_failing_check_resubmits_the_same_state(self):
        result, calls = self._run(self._workflow(passes_on=2, retries=2))

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(len(calls), 2, "落ちた 1 回目のあと同じステートをやり直す")

    def test_retry_carries_the_measured_diagnostic(self):
        _result, calls = self._run(self._workflow(passes_on=2, retries=2))

        retry_prompt = "\n".join(str(a) for a in calls[1])
        self.assertIn("check failed", retry_prompt)
        self.assertIn("attempts=1", retry_prompt, "測った出力を課題文へ戻す")
        self.assertIn("Do not modify the check itself", retry_prompt)

    def test_feedback_can_be_reduced_to_truth_only(self):
        _result, calls = self._run(
            self._workflow(passes_on=2, retries=2, extra="check_feedback: false"))

        retry_prompt = "\n".join(str(a) for a in calls[1])
        self.assertIn("check failed", retry_prompt)
        self.assertNotIn("attempts=1", retry_prompt, "真偽だけで動かす選択肢を残す")

    def test_silent_write_completes_when_check_is_the_material(self):
        # 検査だけを材料に遷移するステートでは、契約文を返さない編集 CLI（黙って直す）の
        # 完了済み書込を受理して check へ進む。書式契約で落とすと P1 の検査まで到達しない
        # （実機再測 2026-08-15 で T1 実装ステートがここで落ちた）。
        workflow = self._workflow(passes_on=1, retries=0)
        out = os.path.join(self.repo, "out.txt")

        def control(*_args, **_kwargs):
            pathlib.Path(self.counter).write_text("1", encoding="utf-8")
            return json.dumps({"type": "write_files", "paths": ["out.txt"]})

        def editor(*_args, **_kwargs):
            pathlib.Path(out).write_text("done\n", encoding="utf-8")
            return ""

        with patch_harness("_SM_MAX_TOOL_ROUNDS", 1), \
                patch_harness("_sm_run_control", side_effect=control), \
                patch_harness("_tl_run_agent", side_effect=editor):
            result = sm.run_statemachine(workflow_path=workflow, cwd=self.repo,
                                         parameters={}, agent={})

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["finalState"], "done")
        self.assertEqual(pathlib.Path(out).read_text(encoding="utf-8"), "done\n")
        events = [json.loads(line) for line
                  in pathlib.Path(result["logFile"]).read_text(encoding="utf-8").splitlines()]
        writes = [e for e in events if e.get("event") == "write_completed"]
        self.assertEqual([e.get("contractSource") for e in writes], ["machine"])
        self.assertEqual([e.get("check_ok") for e in events if e.get("event") == "check"],
                         ["true"], "受理した書込はそのまま check で測られる")

    def test_check_retry_reinvokes_editor_directly(self):
        # 検査の再投入は制御周（次の一手を訊く）を挟まず、前の試行が書いたファイルへの
        # 編集から入る。小型モデルの制御周は再投入で調査ループに落ちて周を使い切る
        # （実機再測 2026-08-15 の失敗機序＝ハーネス模擬 T1gate との差分）。
        out = os.path.join(self.repo, "out.txt")
        script = pathlib.Path(self.repo, "check.py")
        script.write_text(
            "import pathlib, sys\n"
            f"p = pathlib.Path({out!r})\n"
            "ok = p.exists() and p.read_text().startswith('fixed')\n"
            "sys.exit(0 if ok else 1)\n", encoding="utf-8")
        check = json.dumps([sys.executable, str(script)])
        (self.machine / "workflow.yaml").write_text("\n".join([
            "name: gated",
            "initial_state: work",
            "config:",
            "  max_steps: 3",
            "states:",
            "  work:",
            "    action_file: actions/work.md",
            '    output_validator: "startswith:OK"',
            f"    check: {check}",
            "    check_retries: 1",
            "  done:",
            "    terminal: true",
            "transitions:",
            "  - from: work",
            "    to: done",
            '    condition_rule: "equals:check_ok:true"',
            "",
        ]), encoding="utf-8")
        controls: list = []
        edits: list = []

        def control(*args, **_kwargs):
            controls.append(args)
            return json.dumps({"type": "write_files", "paths": ["out.txt"]})

        def editor(*args, **_kwargs):
            edits.append(args)
            pathlib.Path(out).write_text("fixed\n" if len(edits) > 1 else "draft\n",
                                         encoding="utf-8")
            return ""

        with patch_harness("_SM_MAX_TOOL_ROUNDS", 2), \
                patch_harness("_sm_run_control", side_effect=control), \
                patch_harness("_tl_run_agent", side_effect=editor):
            result = sm.run_statemachine(
                workflow_path=str(self.machine / "workflow.yaml"),
                cwd=self.repo, parameters={}, agent={})

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["finalState"], "done")
        self.assertEqual(len(controls), 1, "再投入は制御周を挟まず編集へ直行する")
        self.assertEqual(len(edits), 2)
        self.assertEqual(pathlib.Path(out).read_text(encoding="utf-8"), "fixed\n")

    def test_declared_write_seeds_editor_without_control_round(self):
        # 定型の事前分解はファイル割付まで決めてある。`write:` を宣言したステートは
        # 制御周を挟まず編集 CLI へ直行する——割付は制御席のモデルに訊く仕事ではない
        # （訊くと小型モデルは pytest / pip install の調査ループで周を使い切る）。
        out = os.path.join(self.repo, "out.txt")
        script = pathlib.Path(self.repo, "check.py")
        script.write_text(
            "import pathlib, sys\n"
            f"sys.exit(0 if pathlib.Path({out!r}).exists() else 1)\n", encoding="utf-8")
        check = json.dumps([sys.executable, str(script)])
        (self.machine / "workflow.yaml").write_text("\n".join([
            "name: gated",
            "initial_state: work",
            "config:",
            "  max_steps: 3",
            "states:",
            "  work:",
            "    action_file: actions/work.md",
            "    write: out.txt",
            '    output_validator: "startswith:OK"',
            f"    check: {check}",
            "    check_retries: 1",
            "  done:",
            "    terminal: true",
            "transitions:",
            "  - from: work",
            "    to: done",
            '    condition_rule: "equals:check_ok:true"',
            "",
        ]), encoding="utf-8")
        controls: list = []

        def control(*args, **_kwargs):
            controls.append(args)
            return json.dumps({"type": "final", "output": "OK"})

        def editor(*_args, **_kwargs):
            pathlib.Path(out).write_text("done\n", encoding="utf-8")
            return ""

        with patch_harness("_SM_MAX_TOOL_ROUNDS", 2), \
                patch_harness("_sm_run_control", side_effect=control), \
                patch_harness("_tl_run_agent", side_effect=editor):
            result = sm.run_statemachine(
                workflow_path=str(self.machine / "workflow.yaml"),
                cwd=self.repo, parameters={}, agent={})

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["finalState"], "done")
        self.assertEqual(len(controls), 0, "割付宣言があれば制御周は要らない")

    def test_retry_action_failure_escalates(self):
        # 再投入がアクションを完走できない（契約不成立）のは「この段では直せない」——
        # run 全体のエラーでなく上限到達として escalate で返す。
        workflow = self._workflow(passes_on=99, retries=1)
        responses = iter(['{"type":"final","output":"OK"}'])

        def fake_agent(*args, **_kwargs):
            pathlib.Path(self.counter).write_text("1", encoding="utf-8")
            return next(responses, "garbage")

        with patch_harness("_SM_MAX_TOOL_ROUNDS", 1), \
                patch_harness("_tl_run_agent", side_effect=fake_agent):
            result = sm.run_statemachine(workflow_path=workflow, cwd=self.repo,
                                         parameters={}, agent={})

        self.assertFalse(result["ok"])
        self.assertTrue(result.get("escalate"), "アクション不成立の再投入も昇格シグナルで返る")

    def test_self_reported_success_cannot_satisfy_a_failing_check(self):
        # P1 の核心。「OK」と書いても、測った事実が伴わなければ先へ進まない。
        result, _calls = self._run(self._workflow(passes_on=99, retries=0),
                                   response='{"type":"final","output":"OK\\nPASS 完了しました"}')

        self.assertFalse(result["ok"])
        self.assertEqual(result["finalState"], "work")

    def test_exhausted_retries_escalate_instead_of_failing_generically(self):
        result, calls = self._run(self._workflow(passes_on=99, retries=1))

        self.assertFalse(result["ok"])
        self.assertTrue(result["escalate"], "上限到達は段を上げるシグナル")
        self.assertEqual(len(calls), 2, "上限は宣言どおり（1 回やり直して打ち切る）")
        self.assertEqual(result["check"]["state"], "work")
        self.assertEqual(result["check"]["check_status"], "1")
        self.assertEqual(result["check"]["attempts"], 2)

    def test_escalation_is_recorded_in_the_log(self):
        result, _calls = self._run(self._workflow(passes_on=99, retries=0))

        events = [json.loads(line) for line
                  in pathlib.Path(result["logFile"]).read_text(encoding="utf-8").splitlines()]
        exhausted = [e for e in events if e.get("event") == "check_exhausted"]
        self.assertEqual(len(exhausted), 1)
        self.assertTrue(exhausted[0]["escalate"])

    def test_error_mode_fails_without_escalating(self):
        result, _calls = self._run(
            self._workflow(passes_on=99, retries=0, extra="check_on_exhausted: error"))

        self.assertIsInstance(result, sm.StateMachineHarnessError)
        self.assertIn("検査", str(result))

    def test_continue_mode_hands_the_failure_to_the_transitions(self):
        result, _calls = self._run(
            self._workflow(passes_on=99, retries=0, extra="check_on_exhausted: continue"))

        # 成功経路（equals:check_ok:true）は成立せず、他に経路が無いので遷移不一致で止まる。
        self.assertIsInstance(result, sm.StateMachineHarnessError)
        self.assertIn("一致する遷移がありません", str(result))

    def test_ungated_state_is_untouched(self):
        result, calls = self._run(self._workflow(passes_on=0, retries=0, gated=False))

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(len(calls), 1)
        events = [json.loads(line) for line
                  in pathlib.Path(result["logFile"]).read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e for e in events if e.get("event") == "check"], [],
                         "宣言が無ければ検査は走らない（従来動作のまま）")

    def test_broken_declaration_fails_before_any_agent_call(self):
        (self.machine / "workflow.yaml").write_text("\n".join([
            "name: gated",
            "initial_state: work",
            "states:",
            "  work:",
            "    action_file: actions/work.md",
            '    check: "pytest | tee log"',
            "  done:",
            "    terminal: true",
            "transitions:",
            "  - from: work",
            "    to: done",
            '    condition_rule: "equals:check_ok:true"',
            "",
        ]), encoding="utf-8")

        result, calls = self._run(str(self.machine / "workflow.yaml"))

        self.assertIsInstance(result, sm.StateMachineHarnessError)
        self.assertEqual(calls, [], "壊れた宣言は投入前に落ちる（クレジットを焼かない）")


class CheckContextContractTest(unittest.TestCase):
    """検査結果 → コンテキストの書式。正典は statemachine-use の engine.check_context。"""

    def test_success(self):
        self.assertEqual(sm._sm_check_context(0, "all good\n", "", ""),
                         {"check_status": "0", "check_ok": "true",
                          "check_output": "all good"})

    def test_failure_keeps_first_diagnostic_line(self):
        self.assertEqual(sm._sm_check_context(1, "", "E assert 1 == 2\nmore", ""),
                         {"check_status": "1", "check_ok": "false",
                          "check_output": "E assert 1 == 2"})

    def test_unrunnable_check_is_not_a_pass(self):
        context = sm._sm_check_context(None, "", "", "コマンドがありません")

        self.assertEqual(context["check_status"], "error")
        self.assertEqual(context["check_ok"], "false")

    def test_matches_the_skill_implementation(self):
        # 2 実装が同じ答えを返すことを固定する（キー名と書式の契約は schema.md）。
        skill = sm._sm_resolve_skill("statemachine-use", os.getcwd())
        self.assertIsNotNone(skill, "statemachine-use スキルの実体が必要")
        sys.path.insert(0, skill["root"])
        self.addCleanup(lambda: sys.path.remove(skill["root"]))
        from scripts.engine import check_context  # noqa: E402

        for args in [(0, "ok", "", ""), (1, "", "boom", ""), (None, "", "", "no such file")]:
            self.assertEqual(sm._sm_check_context(*args), check_context(*args), args)


class EscalationExitCodeTest(unittest.TestCase):
    """呼び出し側が RESULT を読まずに終了コードだけで昇格へ振り分けられること。"""

    def _exit_code(self, result: dict) -> int:
        args = argparse.Namespace(workflow="w.yaml", dir=None, param=[], input=None,
                                  agent_cli="aider", model="")
        with patch_harness("_sm_resolve_agent", return_value={"cli": "x", "model": ""}), \
                patch_harness("run_statemachine", return_value=result), \
                mock.patch("sys.stdout", new=io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                sm.cmd_statemachine(args, pathlib.Path(os.getcwd()))
        return caught.exception.code

    def test_success_is_zero(self):
        self.assertEqual(self._exit_code({"ok": True}), 0)

    def test_plain_failure_is_one(self):
        self.assertEqual(self._exit_code({"ok": False}), 1)

    def test_escalation_has_its_own_code(self):
        self.assertEqual(self._exit_code({"ok": False, "escalate": True}), 3)

    def test_startup_failure_is_reported_to_the_result_recorder(self):
        args = argparse.Namespace(workflow="w.yaml", dir=None, param=[], input=None,
                                  agent_cli="broken", model="")
        recorder = mock.Mock()
        with patch_harness("_sm_entry_plan", return_value=(None, pathlib.Path(os.getcwd()))), \
                patch_harness("_sm_resolve_agent", side_effect=sm.StateMachineHarnessError("AIを起動できません")), \
                mock.patch("sys.stdout", new=io.StringIO()), mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                sm.cmd_statemachine(args, pathlib.Path(os.getcwd()), result_recorder=recorder)

        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(recorder.call_args.args[2], {"ok": False, "error": "AIを起動できません"})


class ParamParsingTest(unittest.TestCase):
    def test_param_pairs_and_input(self):
        params = sm._sm_parse_params(["topic=llm", "context.depth=2"], "本文")
        self.assertEqual(params, {"topic": "llm", "context.depth": "2", "input": "本文"})

    def test_param_requires_key_value(self):
        with self.assertRaisesRegex(sm.StateMachineHarnessError, "KEY=VALUE"):
            sm._sm_parse_params(["broken"], None)


class SkillPythonTest(unittest.TestCase):
    """スキルのスクリプト（3.10+）を動かせるインタプリタを選ぶ。"""

    def setUp(self):
        os.environ.pop("PYTHON", None)
        # 選んだインタプリタは本文が覚える（`_TL_SKILL_PYTHON`）。前のテストの記憶を
        # 持ち越すと、何を試したのか分からなくなるので毎回まっさらにする。
        memo = patch_harness("_TL_SKILL_PYTHON", "")
        memo.__enter__()
        self.addCleanup(memo.__exit__, None, None, None)

    def test_uses_own_interpreter_when_new_enough(self):
        self.assertEqual(sm._sm_python_command(), sys.executable)

    def test_falls_back_when_own_interpreter_is_too_old(self):
        # macOS の /usr/bin/python3（3.9）で zipapp が動いている状況を模す。
        fake = types.SimpleNamespace(
            version_info=(3, 9, 6), executable="/usr/bin/python3", version="3.9.6 (fake)")
        with patch_harness("sys", fake):
            self.assertTrue(sm._sm_python_ok(sm._sm_python_command()))



class OneDeliverablePerStateTests(unittest.TestCase):
    """1 ステート 1 成果物。宣言でこの規約を崩せるままにしない。

    小さいモデルは成果物 2 つを同時に渡されると片方を丸ごと落とす（実測 2026-08-29:
    一括 0/3・1 成果物ずつ 3/3）。定型業務（T7 / T8）が通っていたのは 1 ステート
    1 成果物に割れていたからで、そこが規約であることを機械で縛る。
    """

    def test_two_writes_in_one_state_are_refused(self):
        crowded = sm._sm_crowded_write_states({"states": {
            "impl": {"write": ["src/x.py", "tests/test_x.py"]},
            "one": {"write": "src/y.py"},
            "none": {},
        }})
        self.assertEqual(crowded, [("impl", ["src/x.py", "tests/test_x.py"])])

    def test_one_write_per_state_passes(self):
        self.assertEqual(sm._sm_crowded_write_states({"states": {
            "a": {"write": "src/x.py"}, "b": {"write": ["tests/test_x.py"]}}}), [])


if __name__ == "__main__":
    unittest.main()
