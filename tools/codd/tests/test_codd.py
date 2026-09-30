"""codd の結合テスト。実装・設計書の 2 リポジトリを一時フォルダに作り、下請けスクリプトを通す。

LLM は呼ばない。アクションがやる判断（計画を書く・変える）は、テストが代わりにファイルを書いて進める。
graphify は PATH に置いたスタブで差し替え、呼ばれ方（自動更新の有無）を記録する。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
REPO = TOOL.parent.parent
sys.path.insert(0, str(TOOL))

import install  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}

# graphify のスタブ。呼ばれた引数を記録し、update なら $GRAPHIFY_OUT/graph.json を作る。
GRAPHIFY_STUB = """#!/bin/sh
echo "$PWD $@" >> "{log}"
case "$1" in
  update) mkdir -p "$GRAPHIFY_OUT" && echo '{{}}' > "$GRAPHIFY_OUT/graph.json" ;;
  query) echo "NODE $2 [src=docs/api.md loc=L3]" ;;
  affected) echo "Affected nodes for $2"; echo "- use() [calls] src/use.py:L4" ;;
esac
"""

PLAN_ALIGNED = """\
# 変更の計画

## やりたいこと

hello にログを足す。

## 参照先の前提

- hello は整数を返す（根拠: docs/api.md#hello）

## 参照先の制約

- hello は 1 を返す（根拠: docs/api.md:3）

## 参照先のその他

なし

## ずれ

なし

## 自分の変更案

- src/app.py — hello の中でログを出す

## 参照先の変更案

なし

## 影響範囲

なし
"""

PLAN_DRIFT = """\
# 変更の計画

## やりたいこと

hello が 2 を返すようにする。

## 参照先の前提

- hello は整数を返す（根拠: docs/api.md）

## 参照先の制約

- hello は 1 を返す（根拠: docs/api.md:3）

## 参照先のその他

- なし

## ずれ

- 制約: hello は 1 を返す（根拠: docs/api.md:3） — やりたいことは 2 を返す（src/app.py）

## 自分の変更案

- src/app.py — hello が 2 を返す

## 参照先の変更案

- docs/api.md — `hello` の戻り値を 2 と書き直す

## 影響範囲

- src/app.py — hello の戻り値
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout


def commit(repo: Path, files: dict[str, str], message: str) -> None:
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


class CoddTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.impl = self.tmp / "impl"
        self.design = self.tmp / "design"
        for repo in (self.impl, self.design):
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
        commit(self.impl, {"src/app.py": "def hello():\n    return 1\n"}, "init")
        commit(self.design, {"docs/api.md": "# API\n\n## hello\n\nhello は 1 を返す。\n"}, "init")
        install.install(self.impl, "impl", ["../design"])
        install.install(self.design, "design", ["../impl"])
        for repo in (self.impl, self.design):
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "add codd")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "graphify.log"

    def run_pa(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, **GIT_ENV, "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin"}
        return subprocess.run([sys.executable, ".statemachine/codd/codd.py", *args],
                              cwd=repo, capture_output=True, text=True, env=env)

    def use_graphify_stub(self) -> None:
        stub = self.bin / "graphify"
        stub.write_text(GRAPHIFY_STUB.format(log=self.log), encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.is_file() else []

    def write_plan(self, text: str) -> None:
        (self.impl / ".codd").mkdir(exist_ok=True)
        (self.impl / ".codd/plan.md").write_text(text, encoding="utf-8")

    def set_check(self, repo: Path, command: list[str]) -> None:
        path = repo / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["check"] = command
        path.write_text(json.dumps(cfg), encoding="utf-8")

    # ------------------------------------------------------------ 探す

    def test_explore_without_graphify_uses_grep(self) -> None:
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FOUND 1 files (graphify: not-installed)", r.stdout)
        report = (self.impl / ".codd/explore.md").read_text(encoding="utf-8")
        self.assertIn("docs/api.md:3:## hello", report)
        self.assertTrue((self.impl / ".codd/before.json").is_file())
        self.assertEqual(self.run_pa(self.impl, "explore").returncode, 2)  # 語が無い

    def test_graph_is_rebuilt_only_when_the_repo_changes(self) -> None:
        self.use_graphify_stub()
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertIn("graphify: updated", r.stdout)
        self.assertIn("- docs/api.md", (self.impl / ".codd/explore.md").read_text(encoding="utf-8"))
        first = self.calls()
        self.assertEqual(first[0], f"{self.design} update . --force")   # 参照先の中で作り、
        self.assertTrue((self.impl / ".codd/graph/ref-design/graph.json").is_file())  # 自分の側に置く
        self.assertFalse((self.design / "graphify-out").exists())
        self.assertIn("query hello --graph", first[1])

        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertIn("graphify: fresh", r.stdout)
        self.assertFalse(any(" update " in c for c in self.calls()[len(first):]))

        # 参照先が変わったら（コミットでも、作業中の変更でも）作り直す。
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\n変えた。\n", encoding="utf-8")
        self.assertIn("graphify: updated", self.run_pa(self.impl, "explore", "--term", "hello").stdout)
        commit(self.design, {"docs/new.md": "# new\n"}, "add")
        self.assertIn("graphify: updated", self.run_pa(self.impl, "explore", "--term", "hello").stdout)

    def test_impact_searches_own_repo_with_affected(self) -> None:
        self.use_graphify_stub()
        # まだコミットしていない呼び出し元も拾う（graphify の affected と git grep の両方）。
        (self.impl / "src/use.py").write_text("from app import hello\n\nhello()\n", encoding="utf-8")
        r = self.run_pa(self.impl, "impact", "--term", "hello")
        self.assertIn("FOUND 2 files (graphify: updated)", r.stdout)
        self.assertEqual(self.calls()[0], f"{self.impl} update . --force")
        self.assertIn("affected hello --graph", self.calls()[1])
        report = (self.impl / ".codd/impact.md").read_text(encoding="utf-8")
        self.assertIn("- src/use.py:3:hello()", report)
        self.assertIn("## 候補のファイル\n\n- src/use.py\n- src/app.py", report)

    def test_graphify_off(self) -> None:
        self.use_graphify_stub()
        path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["graphify"] = "off"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertIn("graphify: off", self.run_pa(self.impl, "explore", "--term", "hello").stdout)
        self.assertEqual(self.calls(), [])

    # ------------------------------------------------------------ 計画の検査

    def test_verify_plan_accepts_aligned_and_drift_plans(self) -> None:
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 1)  # まだ無い
        tpl = (self.impl / ".statemachine/codd/templates/plan.md").read_text(encoding="utf-8")
        self.write_plan(tpl)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("見出しの中身が空です", r.stderr)
        for plan in (PLAN_ALIGNED, PLAN_DRIFT):
            self.write_plan(plan)
            r = self.run_pa(self.impl, "verify-plan")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_verify_plan_rejects_inconsistent_plans(self) -> None:
        cases = {
            "実在する根拠のパスがありません": PLAN_ALIGNED.replace("docs/api.md#hello", "docs/nowhere.md"),
            "ずれがあるのに、参照先の変更案が「なし」": PLAN_DRIFT.replace(
                "- docs/api.md — `hello` の戻り値を 2 と書き直す", "なし"),
            "ずれが「なし」なのに、参照先の変更案があります": PLAN_ALIGNED.replace(
                "## 参照先の変更案\n\nなし", "## 参照先の変更案\n\n- docs/api.md — 書き直す"),
            "影響範囲が「なし」": PLAN_DRIFT.replace("- src/app.py — hello の戻り値", "なし"),
            "自分のリポジトリに実在するパスがありません": PLAN_DRIFT.replace(
                "- src/app.py — hello の戻り値", "- src/gone.py — 戻り値"),
            "`…` で囲んでください": PLAN_DRIFT.replace("`hello`", "hello"),
            "見出しの順番": PLAN_ALIGNED.replace("## 参照先の前提", "## tmp").replace(
                "## 参照先の制約", "## 参照先の前提").replace("## tmp", "## 参照先の制約"),
        }
        for expected, plan in cases.items():
            with self.subTest(expected=expected):
                self.write_plan(plan)
                r = self.run_pa(self.impl, "verify-plan")
                self.assertEqual(r.returncode, 1)
                self.assertIn(expected, r.stderr)

    # ------------------------------------------------------------ 変えたあとの検査

    def test_verify_apply_own_only(self) -> None:
        self.run_pa(self.impl, "explore", "--term", "hello")
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自分のリポジトリが変わっていません", r.stderr)

        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own=changed refs=none", r.stdout)

        # 変更案が「なし」なのに参照先を変えたら落とす。
        (self.design / "docs/api.md").write_text("勝手に変えた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("参照先の変更案に design は無いのに、design が変わっています", r.stderr)

    def test_verify_apply_both_and_runs_each_check(self) -> None:
        self.run_pa(self.impl, "explore", "--term", "hello")
        self.write_plan(PLAN_DRIFT)
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("design を変えるはずなのに、design が変わっていません", r.stderr)

        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n", encoding="utf-8")
        # 各リポジトリの検査は、それぞれの codd.json の check。
        self.set_check(self.impl, [sys.executable, "-c", "import sys; sys.exit('return 2' not in open('src/app.py').read())"])
        self.set_check(self.design, [sys.executable, "-c", "import sys; sys.exit('3 を返す' not in open('docs/api.md').read())"])
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("design（設計書）の検査が失敗しました", r.stderr)
        self.assertNotIn("実装の検査が失敗しました", r.stderr)

        self.set_check(self.design, [sys.executable, "-c", "import sys; sys.exit('2 を返す' not in open('docs/api.md').read())"])
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own=changed refs=design", r.stdout)

    # ------------------------------------------------------------ 影響範囲を測る

    def add_caller(self) -> None:
        commit(self.impl, {"src/use.py": "from app import hello\n\nprint(hello())\n",
                           "src/other.py": "def helloWorld():\n    return 0\n"}, "caller")

    def test_verify_plan_measures_impact_of_ref_change(self) -> None:
        self.add_caller()
        self.write_plan(PLAN_DRIFT)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("計画の影響範囲に無いファイル", r.stderr)
        self.assertIn("src/use.py", r.stderr)
        self.assertNotIn("src/other.py", r.stderr)  # 語単位で引くので helloWorld は拾わない
        report = (self.impl / ".codd/impact.md").read_text(encoding="utf-8")
        self.assertIn("- src/use.py", report)

        self.write_plan(PLAN_DRIFT.replace("- src/app.py — hello の戻り値",
                                           "- src/app.py — hello の戻り値\n- src/use.py — 変更不要: 表示するだけ"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("影響範囲を測った: 2 files", r.stdout)

    def test_verify_plan_does_not_measure_without_ref_change(self) -> None:
        self.add_caller()
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "OK plan")
        self.assertFalse((self.impl / ".codd/impact.md").exists())

    def test_verify_apply_remeasures_from_the_actual_ref_change(self) -> None:
        self.add_caller()
        commit(self.impl, {"src/bye.py": "def goodbye_world():\n    return 3\n"}, "bye")
        plan = PLAN_DRIFT.replace("- src/app.py — hello の戻り値",
                                  "- src/app.py — hello の戻り値\n- src/use.py — 表示を直す")
        self.run_pa(self.impl, "explore", "--term", "hello")
        self.write_plan(plan)
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        # 参照先は計画に無い見出しまで足した。実際の差分から測るので、その影響も拾う。
        (self.design / "docs/api.md").write_text(
            "# API\n\n## hello\n\nhello は 2 を返す。\n\n## goodbye_world\n\n3 を返す。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("直していないファイル", r.stderr)
        self.assertIn("src/use.py", r.stderr)   # 影響範囲に挙げたが直していない
        self.assertIn("src/bye.py", r.stderr)   # 計画に無かった変更の影響
        self.assertNotIn("src/app.py,", r.stderr)
        self.assertTrue((self.impl / ".codd/impact-after.md").is_file())

        (self.impl / "src/use.py").write_text("from app import hello\n\nprint('v', hello())\n", encoding="utf-8")
        self.write_plan(plan + "- src/bye.py — 変更不要: 名前だけ同じ別物\n")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("impact=3 files", r.stdout)

    def test_verify_apply_needs_explore_first(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)

    # ------------------------------------------------------------ 設定・設置・定義

    def test_missing_ref_is_reported(self) -> None:
        cfg = self.impl / ".statemachine/codd/codd.json"
        cfg.write_text(json.dumps({"side": "impl", "ref_path": "../nowhere"}), encoding="utf-8")
        r = self.run_pa(self.impl, "explore", "--term", "x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("refs を直してください", r.stderr)

    def test_install_is_idempotent_and_replaces_old_files(self) -> None:
        stale = self.impl / ".statemachine/codd/actions/old.md"
        stale.write_text("old", encoding="utf-8")
        install.install(self.impl, None, None)
        self.assertFalse(stale.exists())
        cfg = json.loads((self.impl / ".statemachine/codd/codd.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg, {"side": "impl", "refs": [{"path": "../design"}],
                               "skills": {"plan": [], "apply": []}, "graphify": "auto"})
        self.assertEqual((self.impl / ".gitignore").read_text(encoding="utf-8").splitlines().count(".codd/"), 1)
        self.assertEqual((self.impl / ".graphifyignore").read_text(encoding="utf-8").splitlines(),
                         [".statemachine/codd/"])
        fresh = self.tmp / "fresh"
        fresh.mkdir()
        git(fresh, "init", "-q")
        with self.assertRaises(SystemExit):
            install.install(fresh, "impl", [])

    # ------------------------------------------------------------ 参照先が複数

    def add_second_ref(self) -> Path:
        api = self.tmp / "api"
        api.mkdir()
        git(api, "init", "-q", "-b", "main")
        commit(api, {"docs/api.md": "# API\n\n## hello\n\nHTTP でも hello を返す。\n",
                     "spec/hello.md": "# hello\n"}, "init")
        install.install(api, "design", ["../impl"])
        self.set_check(api, [sys.executable, "-c", "print('api ok')"])
        install.install(self.impl, None, ["design=../design", "api=../api"])
        return api

    def test_explore_searches_every_ref(self) -> None:
        self.add_second_ref()
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FOUND 3 files (graphify: design=not-installed, api=not-installed)", r.stdout)
        report = (self.impl / ".codd/explore.md").read_text(encoding="utf-8")
        for line in ("- design:docs/api.md", "- api:docs/api.md", "- api:spec/hello.md"):
            self.assertIn(line, report)
        r = self.run_pa(self.impl, "explore", "--term", "hello", "--ref", "api")
        self.assertIn("FOUND 2 files (graphify: api=not-installed)", r.stdout)
        self.assertEqual(self.run_pa(self.impl, "explore", "--term", "x", "--ref", "nope").returncode, 2)

    def test_citations_name_the_ref_when_ambiguous(self) -> None:
        self.add_second_ref()
        # docs/api.md は両方の参照先にあるので、名前なしでは決まらない。
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("`名前:パス` で書いてください", r.stderr)
        self.write_plan(PLAN_ALIGNED.replace("docs/api.md#hello", "design:docs/api.md#hello")
                        .replace("docs/api.md:3", "api:docs/api.md:3"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        # spec/hello.md は api にしか無いので、名前なしでよい。
        self.write_plan(PLAN_ALIGNED.replace("docs/api.md#hello", "spec/hello.md")
                        .replace("docs/api.md:3", "design:docs/api.md:3"))
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 0)

    def test_verify_apply_checks_each_ref_against_the_plan(self) -> None:
        api = self.add_second_ref()
        plan = (PLAN_DRIFT.replace("（根拠: docs/api.md）", "（根拠: design:docs/api.md）")
                .replace("docs/api.md:3", "design:docs/api.md:3")
                .replace("- docs/api.md — `hello`", "- api:docs/api.md — `hello`"))
        self.write_plan(plan)
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 0)
        self.run_pa(self.impl, "explore", "--term", "hello")
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        # 計画は api を変える。design を変えてしまい、api を変えていない。
        (self.design / "docs/api.md").write_text("勝手に変えた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("api を変えるはずなのに、api が変わっていません", r.stderr)
        self.assertIn("参照先の変更案に design は無いのに、design が変わっています", r.stderr)

        git(self.design, "checkout", "--", ".")
        (api / "docs/api.md").write_text("# API\n\n## hello\n\nHTTP でも 2 を返す。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("refs=api", r.stdout)

    def test_ref_change_to_a_new_file_is_allowed(self) -> None:
        plan = PLAN_DRIFT.replace("- docs/api.md — `hello`", "- docs/hello.md — 新しく書く。`hello`")
        self.write_plan(plan)
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 0)

    def test_legacy_ref_path_config_still_works(self) -> None:
        cfg = self.impl / ".statemachine/codd/codd.json"
        cfg.write_text(json.dumps({"side": "impl", "ref_path": "../design"}), encoding="utf-8")
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertIn("FOUND 1 files", r.stdout)

    # ------------------------------------------------------------ 使うスキル

    def test_show_lists_skills_for_each_phase(self) -> None:
        cfg_path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["skills"] = {"plan": ["domain-modeler"], "apply": ["tdd", "plugin:refactor"]}
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        design_cfg = self.design / ".statemachine/codd/codd.json"
        dcfg = json.loads(design_cfg.read_text(encoding="utf-8"))
        dcfg["skills"] = {"apply": ["doc-writer"]}
        design_cfg.write_text(json.dumps(dcfg), encoding="utf-8")

        r = self.run_pa(self.impl, "show")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("- design: 設計書", r.stdout)
        self.assertIn("使うスキル（計画を練るとき）:\n  - 自分: `domain-modeler` スキル", r.stdout)
        self.assertIn("  - 自分: `tdd` スキル、`plugin:refactor` スキル", r.stdout)
        # 参照先を変えるときは、参照先に置いた codd.json の skills.apply。
        self.assertIn("  - design を変えるとき: `doc-writer` スキル", r.stdout)

        # codd.json の refs に skills を書けば、そちらが勝つ（参照先に codd が無いときにも使える）。
        cfg["refs"] = [{"name": "design", "path": "../design", "skills": ["spec-editor"]}]
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        r = self.run_pa(self.impl, "show", "--phase", "apply")
        self.assertIn("  - design を変えるとき: `spec-editor` スキル", r.stdout)
        self.assertNotIn("計画を練るとき", r.stdout)

    def test_bad_skills_config_is_reported(self) -> None:
        cfg_path = self.impl / ".statemachine/codd/codd.json"
        for skills in ({"plan": "tdd"}, {"review": []}, {"apply": ["bad name"]}):
            with self.subTest(skills=skills):
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                cfg["skills"] = skills
                cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
                r = self.run_pa(self.impl, "show")
                self.assertEqual(r.returncode, 2)
                self.assertIn("skills", r.stderr)

    def test_workflow_passes_engine_validation(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML がありません")
        runner = REPO / ".github/skills/statemachine-use/scripts/run_machine.py"
        r = subprocess.run([sys.executable, str(runner), str(TOOL / "machine/workflow.yaml"), "--dry-run"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
