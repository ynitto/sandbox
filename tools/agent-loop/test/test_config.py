"""agent-loop 設定読み込みの回帰テスト。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class JsoncConfigTests(unittest.TestCase):
    def test_multiple_prompts_and_trailing_commas(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp, ".vscode", "settings.json")
            settings.parent.mkdir()
            settings.write_text('''{
              "agentExecutor.periodicPrompts": [
                {"agentId": "kiro", "prompt": "one", "intervalMinutes": 1},
                {"agentId": "kiro", "prompt": "two", "intervalMinutes": 2},
              ],
            }''', encoding="utf-8")
            self.assertEqual(
                [p["prompt"] for p in al.load_vscode_periodic_prompts(Path(tmp))],
                ["one", "two"],
            )


class PromptConfigTests(unittest.TestCase):
    def test_load_config_prefers_workspace_agents_over_global(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace_config = workspace / al.AGENT_HOME / "agent-loop.yaml"
            workspace_config.parent.mkdir(parents=True)
            workspace_config.write_text(
                "agent_cli: aider\nprompts:\n  - name: local\n    prompt: run\n",
                encoding="utf-8",
            )
            global_config = root / al.AGENT_HOME / "agent-loop.yaml"
            global_config.parent.mkdir(parents=True)
            global_config.write_text("agent_cli: copilot\n", encoding="utf-8")

            with mock.patch.object(al, "agent_home_dir", return_value=global_config.parent):
                config, path, exists = al.load_config(workspace)

            self.assertTrue(exists)
            self.assertEqual(path, workspace_config.resolve())
            self.assertEqual(config["agent_cli"], "aider")

    def test_load_config_prefers_workspace_root_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp, "workspace")
            workspace.mkdir()
            direct = workspace / "agent-loop.yaml"
            direct.write_text("agent_cli: kiro\n", encoding="utf-8")

            config, path, exists = al.load_config(workspace)

            self.assertTrue(exists)
            self.assertEqual(path, direct.resolve())
            self.assertEqual(config["agent_cli"], "kiro")

    def test_mapping_lookup_expands_prompt_and_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, al.AGENT_HOME, "agent-loop.json")
            config.parent.mkdir()
            config.write_text('''{
              "mapping": {
                "workspace": {"root": "/tmp/project"},
                "messages": {"review": "Review the changes"}
              },
              "prompts": [{
                "prompt": "{{lookup messages review}}",
                "cwd": "{{lookup workspace root}}"
              }]
            }''', encoding="utf-8")

            self.assertEqual(al.load_prompt_config(tmp), [{
                "prompt": "Review the changes",
                "cwd": "/tmp/project",
            }])

    def test_mapping_empty_section_is_allowed(self):
        # 中身を全てコメントアウトした空セクション（YAML では None）で落ちない
        config = {"mapping": {"workspace": None}, "prompts": []}
        self.assertEqual(al._resolve_config_mappings(config), config)

    def test_mapping_lookup_falls_back_to_global_config(self):
        # 共通設定（~/.agents）の mapping をプロジェクト側ファイルの lookup から参照できる
        with tempfile.TemporaryDirectory() as tmp:
            ghome = Path(tmp, "home", ".agents")
            ghome.mkdir(parents=True)
            (ghome / "agent-loop.yaml").write_text(
                "mapping:\n  cwd_map:\n    project: /path/to/proj\n",
                encoding="utf-8",
            )
            ws = Path(tmp, "ws")
            (ws / al.AGENT_HOME).mkdir(parents=True)
            (ws / al.AGENT_HOME / "agent-loop.yml").write_text(
                "prompts:\n"
                "  - name: n\n"
                "    prompt: p\n"
                "    cwd: '{{lookup cwd_map project}}'\n"
                "    interval_minutes: 5\n",
                encoding="utf-8",
            )
            with mock.patch.object(al, "agent_home_dir", return_value=ghome):
                config, _, _ = al.load_config(ws)
        self.assertEqual(config["prompts"][0]["cwd"], "/path/to/proj")

    def test_mapping_local_key_overrides_global(self):
        with tempfile.TemporaryDirectory() as tmp:
            ghome = Path(tmp, "home", ".agents")
            ghome.mkdir(parents=True)
            (ghome / "agent-loop.yaml").write_text(
                "mapping:\n  m:\n    a: global-a\n    b: global-b\n",
                encoding="utf-8",
            )
            ws = Path(tmp, "ws")
            ws.mkdir()
            (ws / "agent-loop.yaml").write_text(
                "mapping:\n  m:\n    a: local-a\n"
                "x: '{{lookup m a}}'\ny: '{{lookup m b}}'\n",
                encoding="utf-8",
            )
            with mock.patch.object(al, "agent_home_dir", return_value=ghome):
                config, _, _ = al.load_config(ws)
        self.assertEqual(config["x"], "local-a")   # ファイル側がキー単位で勝つ
        self.assertEqual(config["y"], "global-b")  # 無いキーは共通設定から補完

    def test_mapping_unknown_lookup_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp, "workspace")
            workspace.mkdir()
            (workspace / "agent-loop.yaml").write_text(
                'mapping:\n  m:\n    a: x\nprompts:\n'
                '  - name: n\n    prompt: "{{lookup m typo}}"\n',
                encoding="utf-8",
            )
            # デーモン起動側（cli.main）はこれを捕まえて traceback ではなく
            # 1 行のエラーで終了する
            with self.assertRaises(ValueError):
                al.load_config(workspace)

    def test_deferred_lookup_passes_through_config_load(self):
        # キーが実行時に決まる遅延 lookup（{変数}）は読み込み時に触らない
        config = {
            "mapping": {"cwd_map": {"sandbox": "/home/u/sandbox"}},
            "prompts": [{"prompt": "{{lookup cwd_map {project}}} で作業"}],
        }
        out = al._resolve_config_mappings(config)
        self.assertEqual(out["prompts"][0]["prompt"], "{{lookup cwd_map {project}}} で作業")

    def test_deferred_lookup_resolves_with_runtime_params(self):
        mappings = al._normalized_mappings({"cwd_map": {"sandbox": "/home/u/sandbox"}})
        text = al.resolve_deferred_lookups(
            "{{lookup cwd_map {project}}} で作業", mappings, {"project": "sandbox"})
        self.assertEqual(text, "/home/u/sandbox で作業")

    def test_deferred_lookup_rejects_missing_var_and_key(self):
        mappings = al._normalized_mappings({"cwd_map": {"sandbox": "/s"}})
        with self.assertRaises(ValueError):
            al.resolve_deferred_lookups("{{lookup cwd_map {project}}}", mappings, {})
        with self.assertRaises(ValueError):
            al.resolve_deferred_lookups(
                "{{lookup cwd_map {project}}}", mappings, {"project": "unknown"})

    def test_hook_vars_resolve_deferred_lookup_before_format(self):
        s = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        s._tool_config = {"mapping": {"cwd_map": {"sandbox": "/home/u/sandbox"}}}
        s._hook_quarantine = set()
        with mock.patch.object(al, "_global_config_mapping", return_value={}):
            out = s._normalize_hook_result(
                {"prompt": "MR {mr} を {{lookup cwd_map {project}}} でレビュー",
                 "vars": {"mr": 7, "project": "sandbox"}},
                "n",
            )
        self.assertEqual(out["prompt"], "MR 7 を /home/u/sandbox でレビュー")

    def test_user_home_does_not_read_dot_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp, ".agent", "agent-loop.json")
            old.parent.mkdir()
            old.write_text('{"prompts": [{"name": "old"}]}', encoding="utf-8")
            with mock.patch.object(al.Path, "home", return_value=Path(tmp)):
                self.assertEqual(al._load_prompt_file_data(tmp), {})
                self.assertEqual(al._prompt_file(tmp),
                                 Path(tmp, ".agents", "agent-loop.yml"))

    def test_save_updates_existing_yaml_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, al.AGENT_HOME, "agent-loop.yaml")
            config.parent.mkdir()
            config.write_text("kiro_options:\n  model: test\nprompts: []\n", encoding="utf-8")

            prompts = [{"name": "saved", "prompt": "hello", "interval_minutes": 5}]
            self.assertTrue(al.save_prompt_config(tmp, prompts))

            self.assertFalse(config.with_suffix(".yml").exists())
            self.assertEqual(al.load_prompt_config(tmp), prompts)
            self.assertEqual(al._load_prompt_file_data(tmp)["kiro_options"], {"model": "test"})


if __name__ == "__main__":
    unittest.main()
