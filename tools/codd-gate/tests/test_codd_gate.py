# -*- coding: utf-8 -*-
"""codd-gate の単体テスト（標準ライブラリ unittest・LLM/ネットワーク不要）。

一時 git リポジトリを組み立てて、接続マップ（Trace）・差分分類（Impact）・ゲート（Verify）・
修復タスク生成（tasks）・状態アサーション（check）を決定的に検証する。

    python -m unittest discover -s tools/codd-gate/tests
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

# テストの git コミットを環境の署名設定から切り離す（kiro-autonomous のテストと同じ流儀）
os.environ["GIT_CONFIG_COUNT"] = "1"
os.environ["GIT_CONFIG_KEY_0"] = "commit.gpgsign"
os.environ["GIT_CONFIG_VALUE_0"] = "false"

_MOD = Path(__file__).resolve().parent.parent / "codd-gate.py"
_spec = importlib.util.spec_from_file_location("codd_gate", _MOD)
cg = importlib.util.module_from_spec(_spec)
sys.modules["codd_gate"] = cg
_spec.loader.exec_module(cg)


def _git(d: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True)
    assert p.returncode == 0, f"git {args} 失敗: {p.stderr}"
    return p.stdout


def init_repo(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@example.com")
    _git(d, "config", "user.name", "t")


def write(d: Path, rel: str, text: str) -> None:
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def commit(d: Path, msg: str, epoch: int = 1700000000) -> str:
    """決定的なコミット（鮮度テスト用にタイムスタンプを固定できる）。"""
    _git(d, "add", "-A")
    env = {**os.environ,
           "GIT_AUTHOR_DATE": f"{epoch} +0000", "GIT_COMMITTER_DATE": f"{epoch} +0000"}
    p = subprocess.run(["git", "-C", str(d), "commit", "-q", "-m", msg, "--allow-empty"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return _git(d, "rev-parse", "HEAD").strip()


def run_cli(argv: "list[str]") -> "tuple[int, str]":
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = cg.main(argv)
    except SystemExit as e:                     # _die（使い方/環境エラー）は SystemExit(2)
        rc = e.code
    return rc, buf.getvalue()


class CharterParseTests(unittest.TestCase):
    CHARTER = """# Charter: demo

## goal
リアーキテクチャの指針（機能要件ではない）。

## repos
- app = git@example.com:team/app.git
  - owns: src/**
  - desc: アプリ本体
  - base: main
  - target: develop
  - docs: docs/**, README.md
  - tests: tests/**
- lib = https://example.com/team/lib.git   # 行内コメント
  - desc: 共有ライブラリ
  - base: develop
- shop-api = git@example.com:team/shop.git
  - path: apps/api
  - base: main
  - desc: モノレポの API 側
"""

    def test_parse(self):
        specs = cg.parse_charter_repos(self.CHARTER)
        self.assertEqual([s["name"] for s in specs], ["app", "lib", "shop-api"])
        app = specs[0]
        self.assertEqual(app["base"], "main")
        self.assertEqual(app["target"], "develop")
        self.assertEqual(app["docs"], ["docs/**", "README.md"])
        self.assertEqual(app["tests"], ["tests/**"])
        self.assertEqual(specs[1]["url"], "https://example.com/team/lib.git")
        self.assertEqual(specs[1]["target"], "develop")   # target 省略 = base
        self.assertEqual(specs[2]["path"], "apps/api")    # (url, path, base) で一意

    def test_no_repos_section(self):
        self.assertEqual(cg.parse_charter_repos("# Charter: x\n## goal\ny\n"), [])


class ClassifyTests(unittest.TestCase):
    def test_defaults(self):
        r = cg.Repo(name="a")
        self.assertEqual(r.classify("README.md"), "doc")
        self.assertEqual(r.classify("docs/api.yaml"), "doc")
        self.assertEqual(r.classify("tests/test_x.py"), "test")
        self.assertEqual(r.classify("pkg/util_test.go"), "test")
        self.assertEqual(r.classify("src/app.test.ts"), "test")
        self.assertEqual(r.classify("src/util.py"), "code")
        self.assertEqual(r.classify(".gitignore"), "other")
        self.assertEqual(r.classify("tests/README.md"), "doc")   # doc 拡張子は置き場所に依らず doc

    def test_custom_globs(self):
        r = cg.Repo(name="a", docs=["spec/**"], tests=["qa/**"], code=["src/**"])
        self.assertEqual(r.classify("spec/design.yaml"), "doc")
        self.assertEqual(r.classify("qa/check.py"), "test")
        self.assertEqual(r.classify("src/x.py"), "code")
        self.assertEqual(r.classify("scripts/x.py"), "other")    # code globs 明示時は範囲外 = other


class MapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name) / "repo"
        init_repo(self.d)
        write(self.d, "src/util.py", "def helper():\n    return 1\n")
        write(self.d, "src/extra.py", "# coherence: doc=docs/extra.md\nX = 1\n")
        write(self.d, "docs/extra.md", "extra の説明。\n")
        write(self.d, "README.md",
              "# demo\n\n`src/util.py` が本体。壊れた参照: `src/gone.py`。\n"
              "リンク: [設計](docs/extra.md)\n\n```\nコードフェンス内の `src/fence.py` は無視\n```\n")
        write(self.d, "tests/test_util.py", "from src.util import helper\n\ndef test_h():\n    assert helper() == 1\n")
        commit(self.d, "init")
        self.repos = [cg.Repo(name="app", dir=self.d)]

    def tearDown(self):
        self.tmp.cleanup()

    def test_map(self):
        m = cg.build_map(self.repos)
        kinds = {n: v["kind"] for n, v in m["nodes"].items()}
        self.assertEqual(kinds["app:src/util.py"], "code")
        self.assertEqual(kinds["app:README.md"], "doc")
        self.assertEqual(kinds["app:tests/test_util.py"], "test")
        edges = {(e["src"], e["dst"], e["kind"]) for e in m["edges"]}
        self.assertIn(("app:README.md", "app:src/util.py", "documents"), edges)
        self.assertIn(("app:README.md", "app:docs/extra.md", "documents"), edges)   # md リンク
        self.assertIn(("app:docs/extra.md", "app:src/extra.py", "documents"), edges)  # 注釈（code 側宣言）
        self.assertIn(("app:tests/test_util.py", "app:src/util.py", "tests"), edges)  # import + 命名規約
        # コードフェンス内は拾わない
        self.assertNotIn("src/fence.py", [b["token"] for b in m["broken_refs"]])
        self.assertEqual([b["token"] for b in m["broken_refs"]], ["src/gone.py"])
        self.assertIn("app:src/extra.py", m["orphans"]["untested"])
        self.assertNotIn("app:src/util.py", m["orphans"]["undocumented"])

    def test_cross_repo_refs(self):
        lib = Path(self.tmp.name) / "lib"
        init_repo(lib)
        write(lib, "core/engine.py", "E = 1\n")
        commit(lib, "init")
        write(self.d, "docs/arch.md", "エンジンは `lib:core/engine.py` と `core/engine.py`。\n")
        commit(self.d, "arch")
        repos = [cg.Repo(name="app", dir=self.d), cg.Repo(name="lib", dir=lib)]
        m = cg.build_map(repos)
        edges = {(e["src"], e["dst"]) for e in m["edges"] if e["kind"] == "documents"}
        self.assertIn(("app:docs/arch.md", "lib:core/engine.py"), edges)  # 明示 prefix も素のパスも解決
        self.assertEqual([b for b in m["broken_refs"] if b["node"] == "app:docs/arch.md"], [])

    def test_unscanned_repo_listed(self):
        repos = self.repos + [cg.Repo(name="ghost", url="git@x:g.git")]
        m = cg.build_map(repos)
        self.assertEqual(m["unscanned"], ["ghost"])


class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name) / "repo"
        init_repo(self.d)
        write(self.d, "src/util.py", "def helper():\n    return 1\n")
        write(self.d, "docs/util.md", "`src/util.py` の説明。helper は 1 を返す。\n")
        write(self.d, "tests/test_util.py", "from src.util import helper\n")
        self.base = commit(self.d, "init", epoch=1700000000)
        self.args = ["--repo-dir", "app=" + str(self.d)]

    def tearDown(self):
        self.tmp.cleanup()

    def _impact(self):
        repos = [cg.Repo(name="app", dir=self.d)]
        m = cg.build_map(repos)
        return cg.classify_impact(m, repos, repos[0], self.base)

    def test_amber_doc_stale(self):
        write(self.d, "src/util.py", "def helper():\n    return 2\n")
        imp = self._impact()
        self.assertEqual([a["type"] for a in imp["amber"]], ["doc-stale"])
        self.assertEqual(imp["amber"][0]["counterpart"], "app:docs/util.md")
        rc, out = run_cli(["verify", *self.args, "--base", self.base])
        self.assertEqual(rc, 1)
        self.assertIn("AMBER", out)

    def test_green_coherent_change(self):
        write(self.d, "src/util.py", "def helper():\n    return 2\n")
        write(self.d, "docs/util.md", "`src/util.py` の説明。helper は 2 を返す。\n")
        imp = self._impact()
        self.assertEqual(imp["amber"], [])
        self.assertTrue(any(g["node"] == "app:src/util.py" for g in imp["green"]))
        rc, _ = run_cli(["verify", *self.args, "--base", self.base])
        self.assertEqual(rc, 0)

    def test_gray_unmapped_new_code(self):
        write(self.d, "src/new_thing.py", "Y = 1\n")
        imp = self._impact()
        self.assertEqual([g["type"] for g in imp["gray"]], ["unmapped"])
        rc, _ = run_cli(["verify", *self.args, "--base", self.base])
        self.assertEqual(rc, 0)                                  # gray は既定で NG にしない
        rc, _ = run_cli(["verify", *self.args, "--base", self.base, "--strict"])
        self.assertEqual(rc, 1)

    def test_amber_broken_ref_in_changed_doc(self):
        write(self.d, "docs/util.md", "`src/util.py` と `src/nope.py` の説明。\n")
        imp = self._impact()
        self.assertEqual([a["type"] for a in imp["amber"]], ["broken-ref"])

    def test_amber_dangling_ref_on_delete(self):
        (self.d / "src/util.py").unlink()
        imp = self._impact()
        types = {a["type"] for a in imp["amber"]}
        self.assertIn("dangling-ref", types)                     # docs/util.md が浮いた参照を持つ

    def test_cross_repo_followup(self):
        lib = Path(self.tmp.name) / "lib"
        init_repo(lib)
        write(lib, "docs/engine.md", "`app:src/util.py` に依存する。\n")
        commit(lib, "init")
        repos = [cg.Repo(name="app", dir=self.d), cg.Repo(name="lib", dir=lib)]
        write(self.d, "src/util.py", "def helper():\n    return 3\n")
        write(self.d, "docs/util.md", "`src/util.py` の説明。helper は 3 を返す。\n")
        m = cg.build_map(repos)
        imp = cg.classify_impact(m, repos, repos[0], self.base)
        self.assertEqual(imp["amber"], [])
        self.assertEqual([f["counterpart"] for f in imp["followup"]], ["lib:docs/engine.md"])
        args = ["--repo-dir", f"app={self.d}", "--repo-dir", f"lib={lib}", "--repo", "app"]
        rc, _ = run_cli(["verify", *args, "--base", self.base])
        self.assertEqual(rc, 0)
        rc, _ = run_cli(["verify", *args, "--base", self.base, "--strict-cross"])
        self.assertEqual(rc, 1)

    def test_base_from_env(self):
        write(self.d, "src/util.py", "def helper():\n    return 9\n")
        old = os.environ.get("KIRO_BASE_REV")
        os.environ["KIRO_BASE_REV"] = self.base
        try:
            rc, _ = run_cli(["verify", *self.args])
            self.assertEqual(rc, 1)
        finally:
            if old is None:
                os.environ.pop("KIRO_BASE_REV", None)
            else:
                os.environ["KIRO_BASE_REV"] = old

    def test_missing_base_dies(self):
        os.environ.pop("KIRO_BASE_REV", None)
        rc, _ = run_cli(["verify", *self.args])
        self.assertEqual(rc, 2)


class TasksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name) / "repo"
        init_repo(self.d)
        write(self.d, "src/util.py", "A = 1\n")
        write(self.d, "docs/util.md", "`src/util.py` の説明。\n")
        self.base = commit(self.d, "init")

    def tearDown(self):
        self.tmp.cleanup()

    def test_tasks_from_drift(self):
        write(self.d, "src/util.py", "A = 2\n")
        rc, out = run_cli(["tasks", "--repo-dir", f"app={self.d}", "--base", self.base])
        self.assertEqual(rc, 0)
        specs = json.loads(out)
        self.assertEqual(len(specs), 1)
        s = specs[0]
        self.assertIn("docs/util.md", s["title"])
        self.assertIn("check --repo-dir app=. --doc docs/util.md --code src/util.py --fresh", s["verify"])
        self.assertEqual(s["paths"], "docs/util.md")
        self.assertEqual(s["expect"], "changes")

    def test_cross_repo_task_uses_accept_and_workspace(self):
        lib = Path(self.tmp.name) / "lib"
        init_repo(lib)
        write(lib, "docs/dep.md", "`app:src/util.py` に依存。\n")
        commit(lib, "init")
        write(self.d, "src/util.py", "A = 3\n")
        write(self.d, "docs/util.md", "`src/util.py` の説明。A=3。\n")
        rc, out = run_cli(["tasks", "--repo-dir", f"app={self.d}", "--repo-dir", f"lib={lib}",
                           "--repo", "app", "--base", self.base])
        specs = json.loads(out)
        self.assertEqual(len(specs), 1)
        self.assertIn("accept", specs[0])
        self.assertNotIn("verify", specs[0])                     # 別 repo は accept（合成/人）へ
        self.assertEqual(specs[0]["workspace"], "lib")

    def test_task_ids_fit_enqueue_slug_rules(self):
        """id は kiro-autonomous の _slug_id（[A-Za-z0-9_-]・48 字）をそのまま通る＝intake の冪等キー。"""
        long = "src/very/long/nested/path/some_component_with_long_name.py"
        tid = cg._task_id("cohort", f"app:docs/x.md", f"app:{long}")   # cohort が最長の kind
        self.assertLessEqual(len(tid), 48)
        self.assertRegex(tid, r"^[A-Za-z0-9_-]+$")
        self.assertEqual(tid, cg._task_id("cohort", "app:docs/x.md", f"app:{long}"))   # 決定的
        other = cg._task_id("cohort", "app:docs/x.md", f"app:{long.replace('some', 'anot')}")
        self.assertNotEqual(tid, other)                  # 切り詰めても別発見は別 id（ハッシュ）

    def test_debt_cohort_groups_homogeneous_debt(self):
        for i in range(3):
            write(self.d, f"src/mod{i}.py", f"V{i} = 1\n")
        commit(self.d, "orphans")
        rc, out = run_cli(["tasks", "--repo-dir", f"app={self.d}", "--debt", "--cohort"])
        self.assertEqual(rc, 0)
        specs = json.loads(out)
        cohorts = [s for s in specs if "cohort_items" in s]
        self.assertEqual(len(cohorts), 2)                # 未文書化 / 未テスト × repo app
        doc = next(s for s in cohorts if "--need doc" in s["verify"])
        self.assertIn("{item}", doc["title"])            # pilot-then-batch の差し込みプレースホルダ
        self.assertIn("{item}", doc["verify"])
        self.assertIn("src/mod0.py", doc["cohort_items"])
        self.assertGreaterEqual(len(doc["cohort_items"]), 3)

    def test_debt_tasks_and_inbox(self):
        write(self.d, "docs/util.md", "`src/util.py` と `src/gone.py` の説明。\n")
        write(self.d, "src/orphan.py", "B = 1\n")
        commit(self.d, "debt")
        inbox = Path(self.tmp.name) / "inbox"
        rc, out = run_cli(["tasks", "--repo-dir", f"app={self.d}", "--debt", "--inbox", str(inbox)])
        self.assertEqual(rc, 0)
        files = sorted(inbox.glob("*.json"))
        self.assertTrue(files)
        specs = [json.loads(f.read_text(encoding="utf-8")) for f in files]
        titles = " / ".join(s["title"] for s in specs)
        self.assertIn("壊れた参照", titles)
        self.assertIn("文書化", titles)
        self.assertIn("テストを追加", titles)


class DebtVerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name) / "repo"
        init_repo(self.d)
        write(self.d, "src/a.py", "A = 1\n")
        write(self.d, "README.md", "`src/a.py` と `src/gone.py`。\n")
        commit(self.d, "init")

    def tearDown(self):
        self.tmp.cleanup()

    def test_thresholds(self):
        args = ["verify", "--repo-dir", f"app={self.d}", "--debt"]
        rc, _ = run_cli(args)                                    # しきい値なし = 棚卸しのみ
        self.assertEqual(rc, 0)
        rc, out = run_cli([*args, "--max-broken", "0"])
        self.assertEqual(rc, 1)
        self.assertIn("壊れた参照", out)
        rc, _ = run_cli([*args, "--max-broken", "1"])
        self.assertEqual(rc, 0)
        rc, _ = run_cli([*args, "--max-untested", "0"])
        self.assertEqual(rc, 1)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name) / "repo"
        init_repo(self.d)
        write(self.d, "src/util.py", "A = 1\n")
        write(self.d, "docs/util.md", "`src/util.py` の説明。\n")
        write(self.d, "tests/test_util.py", "from src.util import A\n")
        commit(self.d, "doc+code", epoch=1700000000)
        self.args = ["--repo-dir", f"app={self.d}"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_refs(self):
        rc, _ = run_cli(["check", *self.args, "--refs", "docs/util.md"])
        self.assertEqual(rc, 0)
        write(self.d, "docs/util.md", "`src/nope.py`\n")
        rc, out = run_cli(["check", *self.args, "--refs", "docs/util.md"])
        self.assertEqual(rc, 1)
        self.assertIn("src/nope.py", out)

    def test_covered(self):
        rc, _ = run_cli(["check", *self.args, "--covered", "src/util.py", "--need", "doc,test"])
        self.assertEqual(rc, 0)
        write(self.d, "src/alone.py", "B = 1\n")
        rc, _ = run_cli(["check", *self.args, "--covered", "src/alone.py", "--need", "doc"])
        self.assertEqual(rc, 1)

    def test_fresh(self):
        rc, _ = run_cli(["check", *self.args, "--doc", "docs/util.md", "--code", "src/util.py", "--fresh"])
        self.assertEqual(rc, 0)                                  # 同時コミット = doc は code より古くない
        write(self.d, "src/util.py", "A = 2\n")
        commit(self.d, "code のみ更新", epoch=1700009999)
        rc, out = run_cli(["check", *self.args, "--doc", "docs/util.md", "--code", "src/util.py", "--fresh"])
        self.assertEqual(rc, 1)
        self.assertIn("古い", out)
        write(self.d, "docs/util.md", "`src/util.py` の説明。A=2。\n")   # 未コミット変更 = 今
        rc, _ = run_cli(["check", *self.args, "--doc", "docs/util.md", "--code", "src/util.py", "--fresh"])
        self.assertEqual(rc, 0)

    def test_edge_required(self):
        write(self.d, "docs/other.md", "接続なし。\n")
        rc, out = run_cli(["check", *self.args, "--doc", "docs/other.md", "--code", "src/util.py"])
        self.assertEqual(rc, 1)
        self.assertIn("documents 接続が無い", out)


class ScanCliTests(unittest.TestCase):
    def test_scan_writes_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "repo"
            init_repo(d)
            write(d, "src/x.py", "X = 1\n")
            write(d, "README.md", "`src/x.py`\n")
            commit(d, "init")
            out = Path(tmp) / "map.json"
            rc, text = run_cli(["scan", "--repo-dir", f"app={d}", "--map", str(out)])
            self.assertEqual(rc, 0)
            self.assertIn("ノード", text)
            m = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("app:src/x.py", m["nodes"])
            self.assertEqual(m["repos"]["app"]["branch"], "main")   # (パス+ブランチ) を記録

    def test_native_config_registry(self):
        """レジストリの正は codd-gate ネイティブの設定ファイル repos:（charter 書式に依存しない）。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "app"
            init_repo(d)
            write(d, "src/x.py", "X = 1\n")
            write(d, "manual/x.md", "`src/x.py`\n")
            commit(d, "init")
            conf = Path(tmp) / "codd-gate.json"
            conf.write_text(json.dumps({
                "repos": {"app": {"url": "git@x:app.git", "base": "main",
                                  "dir": str(d), "docs": ["manual/**"]}},
            }), encoding="utf-8")
            out = Path(tmp) / "map.json"
            rc, _ = run_cli(["scan", "--config", str(conf), "--map", str(out)])
            self.assertEqual(rc, 0)
            m = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(m["nodes"]["app:manual/x.md"]["kind"], "doc")   # docs: グロブが効く
            self.assertEqual(m["repos"]["app"]["url"], "git@x:app.git")
            edges = {(e["src"], e["dst"]) for e in m["edges"]}
            self.assertIn(("app:manual/x.md", "app:src/x.py"), edges)
            # CLI の --repo-dir は設定より勝つ（同名 repo の checkout 差し替え）
            rc, _ = run_cli(["scan", "--config", str(conf), "--map", str(out),
                             "--repo-dir", f"app={d}"])
            self.assertEqual(rc, 0)

    def test_charter_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "app"
            init_repo(d)
            write(d, "src/x.py", "X = 1\n")
            write(d, "manual/x.md", "`src/x.py`\n")
            commit(d, "init")
            charter = Path(tmp) / "charter.md"
            charter.write_text(
                "# Charter: demo\n\n## repos\n- app = git@x:app.git\n"
                "  - desc: 本体\n  - base: main\n  - owns: src/**\n"
                "  - docs: manual/**\n", encoding="utf-8")
            out = Path(tmp) / "map.json"
            rc, _ = run_cli(["scan", "--charter", str(charter),
                             "--repo-dir", f"app={d}", "--map", str(out)])
            self.assertEqual(rc, 0)
            m = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(m["nodes"]["app:manual/x.md"]["kind"], "doc")   # プラグインキー docs: が効く
            edges = {(e["src"], e["dst"]) for e in m["edges"]}
            self.assertIn(("app:manual/x.md", "app:src/x.py"), edges)


if __name__ == "__main__":
    unittest.main()
