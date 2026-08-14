#!/usr/bin/env python3
"""statemachine サブコマンド（headless CLI 向けステートマシンハーネス）の単体テスト。

対象: agent_loop/statemachine.py — パス検証、限定ツール契約、JSON 応答の抽出、
スタブ aider による複数状態の完走（未実行の成功申告を安全な書込へ補正して終端へ遷移）。
dashboard の旧 in-process 実行器（stateMachineRunner.js）のテストを移植したもの。
"""
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

    def test_shell_script_runs_via_fixed_interpreter(self):
        # shebang に従うと「どのシェルが走るか」をスクリプト側が決められてしまう。
        script = os.path.join(self.repo, "tool.sh")
        pathlib.Path(script).write_text("#!/usr/bin/env zsh\necho ok\n", encoding="utf-8")
        request = al._sm_validate_tool_request(
            {"type": "run", "command": script, "args": []}, self.repo, [])
        self.assertIn(os.path.basename(request["command"]), ("bash", "sh"))
        self.assertEqual(request["args"][0], os.path.realpath(script))

    @unittest.skipUnless(__import__("shutil").which("node"), "node が無い環境")
    def test_js_script_runs_via_node(self):
        script = os.path.join(self.repo, "tool.js")
        pathlib.Path(script).write_text("console.log('ok')\n", encoding="utf-8")
        request = al._sm_validate_tool_request(
            {"type": "run", "command": script, "args": []}, self.repo, [])
        self.assertEqual(os.path.basename(request["command"]), "node")
        self.assertEqual(request["args"][0], os.path.realpath(script))

    def test_missing_interpreter_is_rejected(self):
        script = os.path.join(self.repo, "tool.js")
        pathlib.Path(script).write_text("console.log('ok')\n", encoding="utf-8")
        with mock.patch.object(al, "_tl_executable_on_path",
                               side_effect=lambda name: "" if name == "node" else "/bin/" + name):
            with self.assertRaisesRegex(al.StateMachineHarnessError, "インタプリタ"):
                al._sm_validate_tool_request(
                    {"type": "run", "command": script, "args": []}, self.repo, [])


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

    def test_planner_prompt_has_no_copyable_path_placeholder(self):
        prompt = al._sm_planner_prompt(
            action="input.json を読む", cwd="/repo", skills=[], reads=[], history=[], retry="")
        self.assertNotIn("relative/path", prompt)
        self.assertIn(
            '{"type":"run","command":"executable","args":["arg"],"timeout_sec":60}',
            prompt)

    def test_planner_prompt_only_claims_run_after_success(self):
        initial = al._sm_planner_prompt(
            action="tool.py を実行", cwd="/repo", skills=[], reads=[], history=[], retry="")
        completed = al._sm_planner_prompt(
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

                result = al.run_statemachine(
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
            "json_variant": "fake-ollama-json",
        }), encoding="utf-8")
        (agents / "fake-ollama-json.json").write_text(json.dumps({
            **common,
            "command": [sys.executable, str(control)],
        }), encoding="utf-8")

        stdout = io.StringIO()
        args = al.argparse.Namespace(
            workflow=".statemachine/one-step/workflow.yaml",
            agent_cli="fake-aider", model="gemma4:e4b", param=[], input=None,
            dir=self.repo,
        )
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", io.StringIO()), \
                self.assertRaises(SystemExit) as exit_info:
            al.cmd_statemachine(args, pathlib.Path(self.repo))

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

        result = al.run_statemachine(
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

        result = al.run_statemachine(
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
            al.StateMachineHarnessError("editor failed"),
        ]

        with mock.patch.object(al, "_tl_run_agent", side_effect=responses):
            with self.assertRaisesRegex(al.StateMachineHarnessError, "editor failed"):
                al._sm_execute_action(
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

        with mock.patch.object(al, "_SM_MAX_TOOL_ROUNDS", 1), \
                mock.patch.object(al, "_tl_run_agent", side_effect=responses):
            with self.assertRaisesRegex(al.StateMachineHarnessError, "Output Contract"):
                al._sm_execute_action(
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

        with mock.patch.object(al, "_tl_run_agent", side_effect=fake_agent):
            with self.assertRaisesRegex(al.StateMachineHarnessError, "Output Contract"):
                al._sm_execute_action(
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

        with mock.patch.object(al, "_tl_run_agent", side_effect=fake_agent):
            result = al._sm_execute_action(
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

        result = al._sm_write_success_output(
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

        result = al._sm_write_success_output(
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

        with mock.patch.object(al, "_tl_run_agent", side_effect=fake_agent):
            output = al._sm_execute_action(
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

        with mock.patch.object(al, "_tl_run_agent", side_effect=fake_agent), \
                mock.patch.object(al, "_sm_exec_argv") as execute:
            output = al._sm_execute_action(
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

        with mock.patch.object(al, "_tl_run_agent", side_effect=lambda *_a, **_k: next(responses)), \
                mock.patch.object(al, "_sm_exec_argv", return_value={
                    "status": 0, "error": "", "stdout": "done\n", "stderr": "",
                }) as execute:
            output = al._sm_execute_action(
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

        with mock.patch.object(al, "_tl_run_agent", side_effect=lambda *_a, **_k: next(responses)), \
                mock.patch.object(al, "_sm_exec_argv", return_value={
                    "status": 0, "error": "", "stdout": "done\n", "stderr": "",
                }) as execute:
            output = al._sm_execute_action(
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
        return al._sm_execute_action(
            workflow_path=self.workflow, state_id="make", state=self.state, context={},
            cwd=self.repo, agent={}, log_file=os.path.join(self.repo, "run.jsonl"),
            touched=set())

    def test_rejected_tool_request_is_not_accepted_as_output(self):
        # 契約文の形をした行を添えた不正なツール要求。拒否 = やっていない。
        with mock.patch.object(al, "_SM_MAX_TOOL_ROUNDS", 1), \
                mock.patch.object(al, "_tl_run_agent",
                                  return_value='OK\n{"type":"spawn","output":"OK"}'):
            with self.assertRaisesRegex(al.StateMachineHarnessError, "Output Contract"):
                self._execute()

    def test_plain_text_contract_is_still_accepted(self):
        # ツール要求ですらない素の本文は、従来どおり契約文として拾う。
        with mock.patch.object(al, "_tl_run_agent", return_value="OK"):
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
        with mock.patch.object(al, "_SM_MAX_TOOL_ROUNDS", 2), \
                mock.patch.object(al, "_tl_run_agent",
                                  side_effect=lambda *_a, **_k: next(responses)), \
                mock.patch.object(al, "_sm_exec_argv", return_value={
                    "status": 0, "error": "", "stdout": "done\n", "stderr": "",
                }) as execute:
            try:
                output = al._sm_execute_action(
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

        with mock.patch.object(al, "_tl_run_agent", side_effect=fake_agent), \
                mock.patch.object(al, "_sm_exec_argv", return_value={
                    "status": 0, "error": "", "stdout": "", "stderr": "",
                }):
            output = al._sm_execute_action(
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
        with mock.patch.object(al, "_SM_MAX_TOOL_ROUNDS", 1), \
                mock.patch.object(al, "_tl_run_agent", return_value=response):
            return al.run_statemachine(workflow_path=self.workflow, cwd=self.repo,
                                       parameters={}, agent={})

    def test_failed_action_does_not_advance(self):
        with self.assertRaisesRegex(al.StateMachineHarnessError, "Output Contract"):
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
        with mock.patch.object(al, "_tl_run_agent", side_effect=side_effect) as agent:
            try:
                return al._tl_run_control({}, "prompt", cwd=self.repo, read_files=[],
                                          log_file=self.log_file), agent
            except al.ToolLoopError as exc:
                return exc, agent

    def test_transient_failure_is_retried(self):
        result, agent = self._run([al.ToolLoopError("fake-cli がタイムアウトしました"), "OK"])

        self.assertEqual(result, "OK")
        self.assertEqual(agent.call_count, 2)

    def test_permanent_failure_is_not_retried(self):
        result, agent = self._run(al.ToolLoopError("許可されていないツール要求です: spawn"))

        self.assertIsInstance(result, al.ToolLoopError)
        self.assertEqual(agent.call_count, 1, "恒久的な失敗をクレジット分だけ繰り返さない")

    def test_retry_count_is_bounded(self):
        result, agent = self._run(al.ToolLoopError("rate limit"))

        self.assertIsInstance(result, al.ToolLoopError)
        self.assertEqual(agent.call_count, al._TL_CONTROL_RETRIES + 1)


class RuntimeContextTest(unittest.TestCase):
    def test_today_and_now_are_provided(self):
        context = al._sm_initial_context({}, {})

        self.assertRegex(context["today"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(context["now"].startswith(context["today"]), context["now"])

    def test_parameters_still_win(self):
        context = al._sm_initial_context({}, {"today": "2000-01-01"})

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
        skill = al._sm_resolve_skill("statemachine-use", self.repo)
        self.assertIsNotNone(skill, "statemachine-use スキルの実体が必要")
        self.scripts = {"next": os.path.join(skill["root"], "scripts", "next_state.py")}
        self.log_file = os.path.join(self.repo, "run.jsonl")

    def _next(self, output, outputs, agent_response=None):
        calls = []

        def fake_agent(*_args, **_kwargs):
            calls.append(_args)
            return agent_response

        with mock.patch.object(al, "_tl_run_agent", side_effect=fake_agent):
            state = al._sm_next_state(
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

        with self.assertRaisesRegex(al.StateMachineHarnessError, "条件リストを解析できません"):
            self._next("何でもよい", {})

    def test_deprecated_flags_are_gone(self):
        _state, argv, _calls = self._next("???", {"kind": "???"}, agent_response='{"1": false}')

        for call in argv:
            self.assertNotIn("--last-output", call)
            self.assertNotIn("--output", call)
            self.assertNotIn("--list-conditions", call)


class ParamParsingTest(unittest.TestCase):
    def test_param_pairs_and_input(self):
        params = al._sm_parse_params(["topic=llm", "context.depth=2"], "本文")
        self.assertEqual(params, {"topic": "llm", "context.depth": "2", "input": "本文"})

    def test_param_requires_key_value(self):
        with self.assertRaisesRegex(al.StateMachineHarnessError, "KEY=VALUE"):
            al._sm_parse_params(["broken"], None)


class SkillPythonTest(unittest.TestCase):
    """スキルのスクリプト（3.10+）を動かせるインタプリタを選ぶ。"""

    def setUp(self):
        os.environ.pop("PYTHON", None)
        al._SM_SKILL_PYTHON = ""
        self.addCleanup(setattr, al, "_SM_SKILL_PYTHON", "")

    def test_uses_own_interpreter_when_new_enough(self):
        self.assertEqual(al._sm_python_command(), sys.executable)

    def test_falls_back_when_own_interpreter_is_too_old(self):
        # macOS の /usr/bin/python3（3.9）で zipapp が動いている状況を模す。
        real = al.sys
        al.sys = types.SimpleNamespace(
            version_info=(3, 9, 6), executable="/usr/bin/python3", version="3.9.6 (fake)")
        self.addCleanup(setattr, al, "sys", real)
        self.assertTrue(al._sm_python_ok(al._sm_python_command()))


class UsageLedgerTest(unittest.TestCase):
    """headless で呼んだ CLI の実測トークン（`@agent-usage`）をノード予算の台帳へ記帳する。

    ここが抜けると、aider / ollama のように実測を出す CLI を回しても台帳は空のままで、
    モデルの格付け（C9）を推定でしか語れなくなる。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-usage-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._budget = tempfile.TemporaryDirectory(prefix="agent-loop-budget-")
        self.addCleanup(self._budget.cleanup)
        os.environ["AGENT_BUDGET_DIR"] = self._budget.name
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)
        self.log_file = os.path.join(self.repo, "run.jsonl")

    def _agent(self, stderr_text):
        fake = pathlib.Path(self.repo, "fake-cli.py")
        fake.write_text("import sys\nprint('OK')\nsys.stderr.write({!r})\n".format(stderr_text),
                        encoding="utf-8")
        spec = agentcli.normalize("fake-usage", {
            "name": "fake-usage",
            "command": [sys.executable, str(fake)],
            "prompt_via": "argv",
            "prompt_flag": "--message",
            "timeout": 10,
        }, pathlib.Path(self.repo, "fake-usage.json"))
        return {"cli": "fake-usage", "spec": spec, "model": "m", "agentcli": agentcli}

    def _run(self, stderr_text):
        return al._tl_run_agent(self._agent(stderr_text), "やって", cwd=self.repo,
                                readonly=False, read_files=[], files=[], log_file=self.log_file)

    def _ledger_rows(self):
        led = pathlib.Path(self._budget.name, "ledger")
        return [json.loads(line) for path in sorted(led.glob("*.jsonl"))
                for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
                ] if led.is_dir() else []

    def test_measured_usage_is_recorded_with_cli_attribution(self):
        self.assertEqual(self._run("@agent-usage tokens_in=12 tokens_out=34\n"), "OK")
        rows = self._ledger_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0]["tokens_in"], rows[0]["tokens_out"]), (12.0, 34.0))
        self.assertEqual((rows[0]["agent_cli"], rows[0]["model"]), ("fake-usage", "m"))
        self.assertEqual(rows[0]["seconds"], 0,
                         "実行時間はスロット側で記帳済み——ここで足すと二重計上になる")
        events = [json.loads(line) for line
                  in pathlib.Path(self.log_file).read_text(encoding="utf-8").splitlines()]
        self.assertIn({"cli": "fake-usage", "tokensIn": 12, "tokensOut": 34},
                      [{k: e[k] for k in ("cli", "tokensIn", "tokensOut")}
                       for e in events if e.get("event") == "usage"])

    def test_silent_cli_is_not_filled_with_an_estimate(self):
        self.assertEqual(self._run("aider warning\n"), "OK")
        self.assertEqual(self._ledger_rows(), [])

    def test_quota_failure_is_recorded_for_collection(self):
        fake = pathlib.Path(self.repo, "fake-quota.py")
        fake.write_text(
            "import sys\nsys.stderr.write('too many requests; retry after 5 minutes\\n')\n"
            "raise SystemExit(1)\n", encoding="utf-8")
        spec = agentcli.normalize("fake-quota", {
            "name": "fake-quota",
            "command": [sys.executable, str(fake)],
            "prompt_via": "argv",
            "prompt_flag": "--message",
            "timeout": 10,
            "errors": [{
                "match": "too many requests", "class": "quota",
                "quota_kind": "rate_limit", "hint": "一時制限です",
            }],
        }, pathlib.Path(self.repo, "fake-quota.json"))
        agent = {"cli": "fake-quota", "spec": spec, "model": "m", "agentcli": agentcli}

        with self.assertRaisesRegex(al.ToolLoopError, "一時制限です"):
            al._tl_run_agent(agent, "やって", cwd=self.repo, readonly=False,
                             read_files=[], files=[], log_file=self.log_file)

        rows = self._ledger_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["event"], "quota")
        self.assertEqual(rows[0]["quota_kind"], "rate_limit")
        self.assertEqual(rows[0]["agent_cli"], "fake-quota")
        self.assertGreater(__import__("datetime").datetime.fromisoformat(
            rows[0]["reset_at"].replace("Z", "+00:00")).timestamp(), __import__("time").time())


if __name__ == "__main__":
    unittest.main()
