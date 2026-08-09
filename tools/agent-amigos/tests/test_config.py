"""agent-amigos の単体テスト — config（`test_agent_amigos.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・`AmigosTestCase`）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-amigos/tests
"""
import os as _os, sys as _sys
from unittest import mock
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class ConfigFileTests(unittest.TestCase):
    """`.agents/agent-amigos.yaml` 設定（agent-project と同じ CLI > config > 既定の流儀）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amigos-cfg-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write(self, name, data, dirname=".agents"):
        path = os.path.join(self.tmp, dirname, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_defaults_without_config(self):
        from agent_amigos.configfile import load_settings, resolve_bus_spec
        s = load_settings(cwd=self.tmp)
        self.assertIsNone(s["_config_path"])
        self.assertEqual(s["_home"], os.path.abspath(self.tmp))
        # bus 既定 "." はホーム自身に解決される
        self.assertEqual(resolve_bus_spec(s, None), os.path.abspath(self.tmp))

    def test_json_config(self):
        from agent_amigos.configfile import commands_dir, load_settings
        self._write("agent-amigos.json",
                    {"node_id": "n1", "bus": "shared-bus", "manual_claim": True})
        s = load_settings(cwd=self.tmp)
        self.assertEqual(s["node_id"], "n1")
        self.assertTrue(s["manual_claim"])
        from agent_amigos.configfile import resolve_bus_spec
        self.assertEqual(resolve_bus_spec(s, None),
                         os.path.join(os.path.abspath(self.tmp), "shared-bus"))
        # CLI --bus は設定より優先
        self.assertEqual(resolve_bus_spec(s, "git+ssh://x/y.git"), "git+ssh://x/y.git")
        self.assertEqual(commands_dir(self.tmp),
                         os.path.join(self.tmp, ".agents", "agent-amigos", "commands"))

    def test_yaml_config(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML なし")
        from agent_amigos.configfile import load_settings
        self._write("agent-amigos.yaml",
                    "node_id: yaml-node\ntags: [python, web]\n")
        s = load_settings(cwd=self.tmp)
        self.assertEqual(s["node_id"], "yaml-node")
        self.assertEqual(s["tags"], ["python", "web"])

    def test_home_for_explicit_config_is_parent_dir(self):
        from agent_amigos.configfile import load_settings
        path = os.path.join(self.tmp, "custom.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("node_id: explicit\n")
        s = load_settings(explicit=path, cwd=os.path.join(self.tmp, "other"))
        self.assertEqual(s["_config_path"], path)
        self.assertEqual(s["_home"], os.path.abspath(self.tmp))
        self.assertEqual(s["node_id"], "explicit")

    def test_root_level_config_preferred_over_dot_agents(self):
        from agent_amigos.configfile import load_settings
        root = os.path.join(self.tmp, "agent-amigos.json")
        with open(root, "w", encoding="utf-8") as f:
            json.dump({"node_id": "root"}, f)
        self._write("agent-amigos.json", {"node_id": "nested"})
        s = load_settings(cwd=self.tmp)
        self.assertEqual(s["_config_path"], root)
        self.assertEqual(s["_home"], os.path.abspath(self.tmp))
        self.assertEqual(s["node_id"], "root")

    def test_project_local_dot_agent_remains_readable(self):
        from agent_amigos.configfile import load_settings
        path = self._write("agent-amigos.json", {"node_id": "legacy"}, dirname=".agent")
        s = load_settings(cwd=self.tmp)
        self.assertEqual(s["_config_path"], path)
        self.assertEqual(s["node_id"], "legacy")

    def test_global_config_home_is_cwd(self):
        from agent_amigos.configfile import load_settings
        fake_home = tempfile.mkdtemp(prefix="amigos-home-")
        self.addCleanup(shutil.rmtree, fake_home, ignore_errors=True)
        gdir = os.path.join(fake_home, ".agents")
        os.makedirs(gdir)
        gpath = os.path.join(gdir, "agent-amigos.json")
        with open(gpath, "w", encoding="utf-8") as f:
            json.dump({"node_id": "global", "bus": "from-global"}, f)
        old = os.environ.get("HOME")
        os.environ["HOME"] = fake_home
        try:
            s = load_settings(cwd=self.tmp)
            self.assertEqual(s["_config_path"], gpath)
            self.assertEqual(s["_home"], os.path.abspath(self.tmp))
            self.assertEqual(s["node_id"], "global")
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old

    def test_old_global_home_is_ignored(self):
        from agent_amigos.configfile import load_settings
        fake_home = tempfile.mkdtemp(prefix="amigos-old-home-")
        self.addCleanup(shutil.rmtree, fake_home, ignore_errors=True)
        gdir = os.path.join(fake_home, ".agent")
        os.makedirs(gdir)
        with open(os.path.join(gdir, "agent-amigos.json"), "w", encoding="utf-8") as f:
            json.dump({"node_id": "old"}, f)
        with mock.patch.dict(os.environ, {"HOME": fake_home}):
            self.assertIsNone(load_settings(cwd=fake_home)["_config_path"])

    def test_bare_invocation_shows_guidance_instead_of_starting_a_daemon(self):
        # 以前は裸起動が serve（常駐）に化けていた。常駐は agent-project serve の 1 本に
        # 集約したので（実装計画 W1-9）、黙って常駐すると二重に回って claim を奪い合う。
        with self.assertRaises(SystemExit) as cm:
            cli.resolve_argv([])
        self.assertEqual(cm.exception.code, 2)
        # サブコマンドは素通し（補完しない）
        self.assertEqual(cli.resolve_argv(["status"]), ["status"])
        self.assertEqual(cli.resolve_argv(["-h"]), ["-h"])
        self.assertEqual(cli.resolve_argv(["--cycles", "1"]), ["--cycles", "1"])


class NodeIdHomeMigrationTests(unittest.TestCase):
    """node.json は `~/.agents` だけを使う。"""

    def setUp(self):
        from agent_amigos import daemon as _daemon
        self.daemon = _daemon
        self.home = tempfile.mkdtemp(prefix="am-home-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self._real_expanduser = os.path.expanduser
        os.path.expanduser = lambda p: (
            p.replace("~", self.home, 1) if isinstance(p, str) and p.startswith("~") else p)
        self.addCleanup(setattr, os.path, "expanduser", self._real_expanduser)
        os.environ.pop("AGENT_AMIGOS_NODE", None)

    def _write_node(self, home_dir, node_id):
        d = os.path.join(self.home, home_dir, "amigos")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "node.json"), "w", encoding="utf-8") as f:
            json.dump({"id": node_id}, f)

    def test_env_var_wins(self):
        os.environ["AGENT_AMIGOS_NODE"] = "from-env"
        self.addCleanup(os.environ.pop, "AGENT_AMIGOS_NODE", None)
        self.assertEqual(self.daemon.default_node_id(), "from-env")

    def test_legacy_home_id_is_ignored(self):
        self._write_node(".agent", "legacy-node")
        self.assertNotEqual(self.daemon.default_node_id(), "legacy-node")

    def test_new_home_is_read(self):
        self._write_node(".agent", "legacy-node")
        self._write_node(".agents", "new-node")
        self.assertEqual(self.daemon.default_node_id(), "new-node")

    def test_fresh_id_is_minted_into_new_home(self):
        nid = self.daemon.default_node_id()
        self.assertTrue(nid)
        self.assertTrue(os.path.exists(
            os.path.join(self.home, ".agents", "amigos", "node.json")))
        self.assertEqual(self.daemon.default_node_id(), nid, "採番後は同じ ID を返す")

    def test_fresh_id_is_bare_hostname_without_random_suffix(self):
        # 実装計画 W1-10: 新規採番は PC 名そのもの（板上の身元は PC 単位で 1 つ）。
        # 以前の乱数接尾辞付き採番と違い、同じホストなら毎回同じ ID になる。
        import socket

        from agentcore.nodeid import normalize_node_id
        # 綴りは agentcore の共通正規化。エンジンごとに持つと同じ PC が flow で `Mac`・
        # amigos で `mac` になり、板に 2 ノードとして現れる（W1-10 レビュー指摘）。
        self.assertEqual(self.daemon.default_node_id(),
                         normalize_node_id(socket.gethostname()))


class SettingsResolutionTests(unittest.TestCase):
    """設定の解決は `_resolve_ctx` 1 本（CLI > 設定 > 既定）。

    以前は bus と node_id しかここで解決せず、agent_cli / tags / roles / manual_claim /
    board は `participate` だけが設定を読んでいた。`join` / `drive` / `run` は CLI 引数
    しか見ず、設定した agent_cli が効かないまま stub へ落ちる沈黙した失敗になっていた。
    サブコマンドが増えても同じ穴が空かないよう、解決経路そのものを固定する。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amigos-resolve-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        os.makedirs(os.path.join(self.tmp, ".agents"), exist_ok=True)
        self.config = os.path.join(self.tmp, ".agents", "agent-amigos.json")
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"node_id": "cfg-node", "agent_cli": "claude",
                       "tags": ["python"], "roles": ["impl"], "interval": 7,
                       "resume_hours": 3, "manual_claim": True,
                       "board": "/tmp/board", "bus": "bus"}, f)

    def _ctx(self, argv, interval_default=5.0, config=True):
        argv = argv + (["--config", self.config] if config else [])
        args = cli.build_parser().parse_args(argv)
        return cli._resolve_ctx(args, interval_default=interval_default)

    def test_join_reads_every_knob_from_config(self):
        ctx = self._ctx(["join"])
        self.assertEqual(ctx.node_id, "cfg-node")
        self.assertEqual(ctx.agent_cli, "claude")
        self.assertEqual(ctx.tags, ["python"])
        self.assertEqual(ctx.roles, ["impl"])
        self.assertEqual(ctx.interval, 7.0)
        self.assertEqual(ctx.resume_hours, 3.0)
        self.assertTrue(ctx.manual_claim)
        self.assertEqual(ctx.board, "/tmp/board")

    def test_run_and_drive_read_agent_cli_from_config(self):
        for argv in (["run", "--mission", "m", "--role", "r"], ["drive"]):
            with self.subTest(cmd=argv[0]):
                self.assertEqual(self._ctx(argv).agent_cli, "claude")

    def test_cli_args_win_over_config(self):
        ctx = self._ctx(["join", "--agent-cli", "codex", "--tags", "go,rust",
                         "--roles", "reviewer", "--interval", "1",
                         "--no-manual-claim", "--node-id", "arg-node"])
        self.assertEqual((ctx.agent_cli, ctx.node_id), ("codex", "arg-node"))
        self.assertEqual((ctx.tags, ctx.roles), (["go", "rust"], ["reviewer"]))
        self.assertEqual(ctx.interval, 1.0)
        self.assertFalse(ctx.manual_claim)

    def test_config_interval_wins_over_command_default(self):
        # 設定 > 既定。設定に interval があれば drive の既定 0.5 には落ちない
        self.assertEqual(self._ctx(["drive"], interval_default=0.5).interval, 7.0)

    def test_argv_limit_is_configurable(self):
        # argv_limit は agent-project / agent-flow と同名の設定キー（ノード側で上書き可能）。
        # ctx のフィールドではなく agentcli モジュールの上限として確定する
        # （run_agent の呼び出し側が全員 argv_limit を引き回さずに済む・S9 の踏襲）。
        from agent_amigos import agentcli
        self.addCleanup(agentcli.configure_argv_limit, 0)
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"node_id": "cfg-node", "argv_limit": 42}, f)
        self._ctx(["join"])
        self.assertEqual(agentcli._agent_argv_limit(), 42)

    def test_argv_limit_defaults_to_builtin_when_unset(self):
        from agent_amigos import agentcli
        self.addCleanup(agentcli.configure_argv_limit, 0)
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"node_id": "cfg-node"}, f)
        self._ctx(["join"])
        self.assertEqual(agentcli._agent_argv_limit(), agentcli.DEFAULT_ARGV_LIMIT)

    def test_post_roles_file_is_not_a_role_filter(self):
        """`post --roles` は役割ミッション表のファイルパス。応募ロールの絞り込み
        （`join --roles`）と dest を共有すると、公示のたびに roles.yaml という名前の
        ロールだけに応募する絞り込みが生えてしまう。"""
        ctx = self._ctx(["post", "--design", "d.md", "--roles", "roles.yaml"])
        self.assertEqual(ctx.roles, ["impl"])     # 設定の絞り込みがそのまま残る
