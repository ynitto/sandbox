"""agent-project の単体テスト — 安定プレフィックス化（案 H・stable_prefix・オプトイン）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


def _write_map(cfg, name, body, sha="abc123"):
    cdir = km.context_dir(cfg)
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / f"{name}.md").write_text(
        f"<!-- head: {sha} -->\n# リポジトリ理解: {name}\n\n{body}\n", encoding="utf-8")


def _seed_project(d: Path, cfg) -> None:
    """charter・rules.md・repo_map を 1 セット用意する（3 ブロックとも非空にする）。"""
    cfg.charter.write_text(
        "# Charter: demo\n## goal\nGOAL-TEXT を達成する\n"
        "## repos\n- app = https://example.com/app.git\n", encoding="utf-8")
    km.rules_path(cfg).write_text("- RULES-TEXT を必ず守る\n", encoding="utf-8")
    _write_map(cfg, "app", "REPOMAP-TEXT が本体")


class ProjectContextBlockTests(unittest.TestCase):
    """project_context_block(): build_request が今日埋め込む 3 ブロックと同じ見出し・
    同じ内容を、切り出して返すだけ（内容は変えない）。"""

    def test_empty_when_nothing_declared(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            t = km.Task(id="T1", title="x", verify="true")
            self.assertEqual(km.project_context_block(cfg, t), "")

    def test_renders_same_headers_and_content_as_build_request(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            _seed_project(d, cfg)
            t = km.Task(id="T1", title="x", verify="true")
            block = km.project_context_block(cfg, t)
            self.assertIn("プロジェクト定義（charter", block)
            self.assertIn("GOAL-TEXT", block)
            self.assertIn("プロジェクトルール（rules.md", block)
            self.assertIn("RULES-TEXT", block)
            self.assertIn("リポジトリ理解（構造", block)
            self.assertIn("REPOMAP-TEXT", block)

    def test_workspace_scoping_matches_repo_map_context(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            _seed_project(d, cfg)
            _write_map(cfg, "docs", "DOCSMAP-TEXT")
            t = km.Task(id="T1", title="x", verify="true")
            t.extra.append(("workspace", "docs"))
            block = km.project_context_block(cfg, t)
            self.assertIn("DOCSMAP-TEXT", block)
            self.assertNotIn("REPOMAP-TEXT", block)


class BuildRequestStablePrefixTests(unittest.TestCase):
    """build_request(): stable_prefix の on/off で「どこに情報があるか」だけが変わり、
    情報そのものは失われない。"""

    def test_default_off_is_byte_identical_to_before(self):
        """既定 off（stable_prefix 未指定）では、3 ブロックとも従来どおり本文に埋め込まれる
        ——このテストが落ちたら「オプトイン既定 off で挙動不変」という不変条件が壊れている。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)  # stable_prefix 未指定 → Config 既定 False
            _seed_project(d, cfg)
            t = km.Task(id="T1", title="x", verify="true")
            req = km.build_request(t, cfg)
            self.assertIn("GOAL-TEXT", req)
            self.assertIn("RULES-TEXT", req)
            self.assertIn("REPOMAP-TEXT", req)

    def test_stable_prefix_on_omits_the_three_blocks_from_request(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, stable_prefix=True)
            _seed_project(d, cfg)
            t = km.Task(id="T1", title="x", verify="true")
            req = km.build_request(t, cfg)
            self.assertNotIn("GOAL-TEXT", req)
            self.assertNotIn("RULES-TEXT", req)
            self.assertNotIn("REPOMAP-TEXT", req)
            # タスク固有の内容（タイトル・完了条件）は変わらず残る
            self.assertIn("T1", req)

    def test_stable_prefix_on_moves_not_loses_information(self):
        """外した分は project_context_block() で丸ごと得られる（情報の欠落が無いことの保証）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, stable_prefix=True)
            _seed_project(d, cfg)
            t = km.Task(id="T1", title="x", verify="true")
            req = km.build_request(t, cfg)
            block = km.project_context_block(cfg, t)
            for needle in ("GOAL-TEXT", "RULES-TEXT", "REPOMAP-TEXT"):
                self.assertNotIn(needle, req)
                self.assertIn(needle, block)

    def test_other_blocks_unaffected_by_stable_prefix(self):
        """decision_context 等は stable_prefix に関わらず従来どおり本文に残る
        （案 H の対象は charter/rules/repo_map の 3 ブロックだけ）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, stable_prefix=True)
            t = km.Task(id="T1", title="x", verify="true")
            cfg.decisions.mkdir(parents=True, exist_ok=True)
            km.decision_path(cfg, t.id).write_text(
                "## DR-0001  2026-07-01  actor: human\n"
                "- context : DECISION-TEXT を踏まえて直す\n", encoding="utf-8")
            req = km.build_request(t, cfg)
            self.assertIn("DECISION-TEXT", req)

    def test_force_inline_context_ignores_stable_prefix(self):
        """board 委譲経路（別マシン実行）向けの逃げ道: stable_prefix が有効でも
        force_inline_context=True なら常に本文へ埋め込む（--context-file を渡せないため）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, stable_prefix=True)
            _seed_project(d, cfg)
            t = km.Task(id="T1", title="x", verify="true")
            req = km.build_request(t, cfg, force_inline_context=True)
            self.assertIn("GOAL-TEXT", req)
            self.assertIn("RULES-TEXT", req)
            self.assertIn("REPOMAP-TEXT", req)


class ContextFileWiringTests(unittest.TestCase):
    """flow.py: --context-file の argv 配線（agent-flow への受け渡し）。"""

    def test_off_adds_no_context_file_arg(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            _seed_project(d, cfg)
            t = km.Task(id="T1", title="x", verify="true")
            cmd = km.build_agent_flow_cmd(t, cfg)
            self.assertNotIn("--context-file", cmd)

    def test_on_adds_context_file_arg_with_the_moved_content(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, stable_prefix=True)
            _seed_project(d, cfg)
            t = km.Task(id="T1", title="x", verify="true")
            cmd = km.build_agent_flow_cmd(t, cfg)
            self.assertIn("--context-file", cmd)
            path = cmd[cmd.index("--context-file") + 1]
            self.assertTrue(_os.path.isfile(path))
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("GOAL-TEXT", content)
            self.assertIn("RULES-TEXT", content)
            self.assertIn("REPOMAP-TEXT", content)

    def test_on_with_nothing_to_say_adds_no_arg(self):
        """charter/rules/repo_map が何も無ければ空ブロック → ファイルも argv も作らない。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, stable_prefix=True)
            t = km.Task(id="T1", title="x", verify="true")
            cmd = km.build_agent_flow_cmd(t, cfg)
            self.assertNotIn("--context-file", cmd)

    def test_path_is_deterministic_per_task_and_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, stable_prefix=True)
            _seed_project(d, cfg)
            t = km.Task(id="T1", title="x", verify="true")
            cmd1 = km.build_agent_flow_cmd(t, cfg)
            cmd2 = km.build_agent_flow_cmd(t, cfg)
            path1 = cmd1[cmd1.index("--context-file") + 1]
            path2 = cmd2[cmd2.index("--context-file") + 1]
            self.assertEqual(path1, path2)                # 同じタスクは同じパス（上書き）


if __name__ == "__main__":
    unittest.main()
