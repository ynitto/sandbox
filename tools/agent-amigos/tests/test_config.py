"""agent-amigos の単体テスト — config（`test_agent_amigos.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・`AmigosTestCase`）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-amigos/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class ConfigFileTests(unittest.TestCase):
    """`.agent/agent-amigos.yaml` 設定（agent-project と同じ CLI > config > 既定の流儀）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amigos-cfg-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write(self, name, data):
        path = os.path.join(self.tmp, ".agent", name)
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
                         os.path.join(self.tmp, ".agent", "agent-amigos", "commands"))

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

    def test_root_level_config_preferred_over_dot_agent(self):
        from agent_amigos.configfile import load_settings
        root = os.path.join(self.tmp, "agent-amigos.json")
        with open(root, "w", encoding="utf-8") as f:
            json.dump({"node_id": "root"}, f)
        self._write("agent-amigos.json", {"node_id": "nested"})
        s = load_settings(cwd=self.tmp)
        self.assertEqual(s["_config_path"], root)
        self.assertEqual(s["_home"], os.path.abspath(self.tmp))
        self.assertEqual(s["node_id"], "root")

    def test_global_config_home_is_cwd(self):
        from agent_amigos.configfile import load_settings
        fake_home = tempfile.mkdtemp(prefix="amigos-home-")
        self.addCleanup(shutil.rmtree, fake_home, ignore_errors=True)
        gdir = os.path.join(fake_home, ".agent")
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
    """node.json は共通ホーム移行中も既存のノード ID を失わない。

    ID は claim / assign / メッセージ宛先に使われるため、振り直しは同一性の断絶になる。
    他の状態と違い「新旧の両方を読む」ことで、どちらに置かれていても拾えるようにしてある。
    """

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

    def test_legacy_home_id_is_preserved(self):
        self._write_node(".agent", "legacy-node")
        self.assertEqual(self.daemon.default_node_id(), "legacy-node")

    def test_legacy_id_survives_when_new_home_dir_exists_but_file_does_not(self):
        """移行の途中（新ホームのディレクトリだけ先にできた端末）でも ID を振り直さない。"""
        self._write_node(".agent", "legacy-node")
        os.makedirs(os.path.join(self.home, ".agents", "amigos"), exist_ok=True)
        self.assertEqual(self.daemon.default_node_id(), "legacy-node")

    def test_new_home_takes_precedence(self):
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
