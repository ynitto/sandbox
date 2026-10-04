"""codd-agent の結合テスト。実装・設計書の 2 リポジトリを一時フォルダに作り、下請けスクリプトを通す。

LLM は呼ばない。アクションがやる判断（計画を書く・変える）は、テストが代わりにファイルを書いて進める。
graphify は PATH に置いたスタブで差し替え、呼ばれ方（自動更新の有無）を記録する。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
REPO = TOOL.parent.parent
sys.path.insert(0, str(TOOL))

import init  # noqa: E402
import install  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}

# graphify のスタブ。引数を記録し、更新・検索の作業ファイルを出力先に作る。
GRAPHIFY_STUB = """#!/bin/sh
echo "$PWD $@" >> "{log}"
out_dir="${{GRAPHIFY_OUT:-graphify-out}}"
mkdir -p "$out_dir"
echo "$1" > "$out_dir/manifest.json"
case "$1" in
  update) echo '{{}}' > "$out_dir/graph.json" ;;
  query) echo "NODE $2 [src=docs/api.md loc=L3]" ;;
  affected) echo "Affected nodes for $2"; echo "- use() [calls] src/use.py:L4" ;;
esac
"""

PLAN = ".plans/2026-10-01-0000-test.md"

PLAN_ALIGNED = """\
# 変更の計画

## やりたいこと

hello にログを足す。

## 守る決まり

なし

## 使ったスキルと道具

なし

## 参照先の前提

- hello は整数を返す（根拠: docs/api.md#hello）

## 参照先の制約

- hello は 1 を返す（根拠: docs/api.md:3）

## 参照先のその他

なし

## ずれ

なし

## 自分の変更案

- src/app.py — `hello` の中でログを出す

## 参照先の変更案

なし

## 影響範囲

なし

## テストの変更案

- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）

## 今回やらないこと

なし
"""

PLAN_DRIFT = """\
# 変更の計画

## やりたいこと

hello が 2 を返すようにする。

## 守る決まり

- docs/api.md — 文書の書式: 今の見出しの並び（hello の節）と書き方を保つ

## 使ったスキルと道具

なし

## 参照先の前提

- hello は整数を返す（根拠: docs/api.md）

## 参照先の制約

- hello は 1 を返す（根拠: docs/api.md:3）

## 参照先のその他

- なし

## ずれ

- 制約: hello は 1 を返す（根拠: docs/api.md:3） — やりたいことは 2 を返す（src/app.py）

## 自分の変更案

- src/app.py — `hello` が 2 を返す

## 参照先の変更案

- docs/api.md — `hello` の戻り値を 2 と書き直す

## 影響範囲

- src/app.py — hello の戻り値

## テストの変更案

- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）

## 今回やらないこと

- `hello` の引数を足す — 別の回に回す
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
        init.init_repo(self.impl, "impl", ["../design"])
        init.init_repo(self.design, "design", ["../impl"])
        for repo in (self.impl, self.design):
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "add codd")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "graphify.log"

    def run_pa(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, **GIT_ENV, "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
               "HOME": str(self.tmp / "home")}   # 利用者のホームのスキルを拾わない
        return subprocess.run([sys.executable, ".statemachine/codd/codd.py", *args],
                              cwd=repo, capture_output=True, text=True, env=env)

    def use_graphify_stub(self) -> None:
        stub = self.bin / "graphify"
        stub.write_text(GRAPHIFY_STUB.format(log=self.log), encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.is_file() else []

    def write_plan(self, text: str, read: bool = True) -> None:
        (self.impl / ".plans").mkdir(parents=True, exist_ok=True)
        (self.impl / PLAN).write_text(text, encoding="utf-8")
        if read:
            self.read_up(self.impl)

    def read_up(self, repo: Path, *terms: str) -> None:
        """計画の前にアクションがすること: 参照先を探し、守る決まりを読み込む。"""
        args = [a for t in (terms or ("hello",)) for a in ("--term", t)]
        r = self.run_pa(repo, "explore", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.run_pa(repo, "rule", "--all")

    def assert_plan_ok(self, repo: Path | None = None) -> None:
        r = self.run_pa(repo or self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)

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
        self.assertEqual(self.run_pa(self.impl, "explore").returncode, 2)  # 語が無い

    def test_explore_keeps_three_lines_per_file_without_git_max_count(self) -> None:
        commit(self.design, {"docs/many.md": "".join(f"hello {i}\n" for i in range(6))}, "many")
        self.assertEqual(self.run_pa(self.impl, "explore", "--term", "hello").returncode, 0)
        report = (self.impl / ".codd/explore.md").read_text(encoding="utf-8")
        self.assertEqual(report.count("docs/many.md:"), 3)   # 1 ファイル 3 行までは保つ

    def test_a_failing_git_grep_is_not_taken_as_no_match(self) -> None:
        # 古い git が知らないオプションを渡したときのように、git grep が 1 以外で落ちたら「該当なし」にしない。
        shim = self.bin / "git"
        shim.write_text('#!/bin/sh\nfor a in "$@"; do [ "$a" = grep ] && { echo "error: unknown option" >&2; exit 129; }; done\n'
                        f'exec {shutil.which("git")} "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("git grep が失敗しました（終了コード 129）", r.stderr)
        self.assertNotIn("該当なし", (self.impl / ".codd/explore.md").read_text(encoding="utf-8")
                         if (self.impl / ".codd/explore.md").is_file() else "")

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
        manifest = self.impl / ".codd/graph/ref-design/manifest.json"
        self.assertEqual(manifest.read_text(encoding="utf-8").strip(), "query")

        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertIn("graphify: fresh", r.stdout)
        self.assertFalse(any(" update " in c for c in self.calls()[len(first):]))
        self.assertFalse((self.design / "graphify-out").exists())
        self.assertEqual(manifest.read_text(encoding="utf-8").strip(), "query")

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
        self.assertEqual((self.impl / ".codd/graph/own/manifest.json").read_text(encoding="utf-8").strip(),
                         "affected")
        self.assertFalse((self.impl / "graphify-out").exists())
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
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
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
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
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
        self.assertIn("「未判断」として書き足しました", r.stderr)
        self.assertIn("src/use.py", r.stderr)
        self.assertNotIn("src/other.py", r.stderr)  # 語単位で引くので helloWorld は拾わない
        report = (self.impl / ".codd/impact.md").read_text(encoding="utf-8")
        self.assertIn("- src/use.py", report)
        # 測ったファイルは計画に「未判断」として書き足される。判断しないままでは通らない。
        self.assertIn("- src/use.py — 未判断（変わる名前が出てくる）", (self.impl / PLAN).read_text(encoding="utf-8"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」の項目が 1 件残っています", r.stderr)

        self.write_plan(PLAN_DRIFT.replace("- src/app.py — hello の戻り値",
                                           "- src/app.py — hello の戻り値\n- src/use.py — 変更不要: 表示するだけ"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("影響範囲を測った: 2 files", r.stdout)

    def test_verify_plan_measures_impact_of_own_change_too(self) -> None:
        # 参照先を変えない計画でも、自分の変更で動く名前の呼び出し元を漏れなく扱わせる。
        self.add_caller()
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        self.assertIn("src/use.py", r.stderr)
        # 「なし」だった見出しは、書き足した項目に置き換わる。エージェントは印を判断に書き換えるだけでよい。
        plan = (self.impl / PLAN).read_text(encoding="utf-8")
        self.assertIn("## 影響範囲\n\n- src/use.py — 未判断（変わる名前が出てくる）\n\n## ", plan)
        (self.impl / PLAN).write_text(plan.replace("未判断（変わる名前が出てくる）", "変更不要: 戻り値は同じ"),
                                      encoding="utf-8")
        self.assert_plan_ok()
        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/use.py — 変更不要: 戻り値は同じ"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("影響範囲を測った: 2 files", r.stdout)
        self.assertIn("参照先で触れている: 1 files", r.stdout)

    def test_verify_plan_finds_ref_files_the_own_change_touches(self) -> None:
        # 自分の変更で動く名前に触れている参照先のファイルを読まずに計画したら落とす（逆向きの漏れ）。
        commit(self.design, {"docs/guide.md": "# 使い方\n\n`hello` を呼ぶ。\n"}, "guide")
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        self.assertIn("docs/guide.md", r.stderr)
        self.assertNotIn("docs/api.md", r.stderr)  # 根拠に挙げたものは扱った
        # 一致した行を添えて書き足す（その前後だけを読めば判断できる）
        self.assertIn("- 未判断: docs/guide.md:3（自分の変更で変わる名前が出てくる） 「hello を呼ぶ。」",
                      (self.impl / PLAN).read_text(encoding="utf-8"))
        self.assertIn("docs/guide.md", (self.impl / ".codd/ref-impact.md").read_text(encoding="utf-8"))
        self.write_plan(PLAN_ALIGNED.replace("## 参照先のその他\n\nなし",
                                             "## 参照先のその他\n\n- 関係なし: 呼び方の例だけ（根拠: docs/guide.md）"))
        self.assert_plan_ok()

    def test_verify_apply_remeasures_from_the_actual_ref_change(self) -> None:
        self.add_caller()
        commit(self.impl, {"src/bye.py": "def goodbye_world():\n    return 3\n"}, "bye")
        plan = PLAN_DRIFT.replace("- src/app.py — hello の戻り値",
                                  "- src/app.py — hello の戻り値\n- src/use.py — 表示を直す")
        self.write_plan(plan)
        self.assert_plan_ok()
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
        # 「変更不要」とするのは計画を直すこと。変えた分を残して、計画の検査を通し直してから確かめる。
        self.write_plan(plan.replace("- src/use.py — 表示を直す", "- src/use.py — 表示を直す\n- src/bye.py — 変更不要: 名前だけ同じ別物"))
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("利用者が確かめたものではありません", r.stderr)
        self.assertIn("keep-changes", self.run_pa(self.impl, "advise").stdout)
        self.assertEqual(self.run_pa(self.impl, "keep-changes").returncode, 0)
        self.assert_plan_ok()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("impact=3 files", r.stdout)

    def test_apply_needs_the_plan_that_passed_and_was_not_rejected(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.assertEqual(self.run_pa(self.impl, "decide", "NG", "--note", "やり直して").returncode, 0)
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")   # 止まったところから飛ばして来ても、退けた計画では通さない
        self.assertEqual(r.returncode, 1)
        self.assertIn("利用者が確かめたものではありません", r.stderr)
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 0)
        self.assertNotIn("利用者が確かめたものではありません", self.run_pa(self.impl, "verify-apply").stderr)

    # ------------------------------------------------------------ パスのつながり

    def test_verify_plan_follows_path_links_both_ways(self) -> None:
        # 参照先の文書が自分の変更案のファイルをパスで指しているなら、名前が一致しなくても計画で扱わせる。
        commit(self.design, {"docs/map.md": "# 対応表\n\n実装は [app](../impl/src/app.py) と `src/app.py`。\n"
                                            "```\nsrc/app.py はコードブロックの中なので数えない\n```\n"}, "map")
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        self.assertIn("docs/map.md", r.stderr)
        self.assertIn("- 未判断: docs/map.md（自分の変更案のファイルとパスでつながっている）",
                      (self.impl / PLAN).read_text(encoding="utf-8"))
        self.assertIn("docs/map.md", (self.impl / ".codd/trace.md").read_text(encoding="utf-8"))
        self.write_plan(PLAN_ALIGNED.replace("## 参照先のその他\n\nなし",
                                             "## 参照先のその他\n\n- 関係なし: 置き場所の一覧だけ（根拠: docs/map.md）"))
        self.assert_plan_ok()

        # 自分のファイルに書いた注記で、参照先の変更案のファイルとつながる（逆向き）。
        commit(self.impl, {"src/client.py": "# coherence: doc=docs/api.md\ndef call():\n    return 0\n"}, "client")
        plan = PLAN_DRIFT.replace("## 参照先のその他\n\n- なし",
                                  "## 参照先のその他\n\n- 関係なし: 置き場所の一覧だけ（根拠: docs/map.md）")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("- src/client.py — 未判断（参照先の変更案のファイルとパスでつながっている）",
                      (self.impl / PLAN).read_text(encoding="utf-8"))
        self.assertIn("src/client.py", r.stderr)
        plan = plan.replace("- src/app.py — hello の戻り値", "- src/app.py — hello の戻り値\n- src/client.py — 変更不要: 呼ぶだけ")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_verify_apply_catches_broken_and_dangling_paths(self) -> None:
        commit(self.impl, {"src/old.py": "def old():\n    return 0\n"}, "old")
        commit(self.design, {"docs/map.md": "# 対応表\n\n- `src/old.py` は古い入口\n"}, "map")
        plan = (PLAN_DRIFT
                .replace("- src/app.py — `hello` が 2 を返す", "- src/app.py — `hello` が 2 を返す\n- src/old.py — `old` を消す")
                .replace("## 参照先のその他\n\n- なし", "## 参照先のその他\n\n- 古い入口の一覧（根拠: docs/map.md）"))
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.impl / "src/old.py").unlink()
        (self.design / "docs/api.md").write_text(
            "# API\n\n## hello\n\nhello は 2 を返す。詳しくは [手順](steps.md)。\n\n"
            "```\n例: `path/to/nothing.md`\n```\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("書き足したパスが、どのリポジトリにもありません", r.stderr)
        self.assertIn("docs/api.md:5 → steps.md", r.stderr)
        self.assertNotIn("nothing.md", r.stderr)          # コードブロックの中は数えない
        self.assertIn("消したファイルを、まだ指しているところがあります", r.stderr)
        self.assertIn("docs/map.md:3 → src/old.py", r.stderr)
        self.assertTrue((self.impl / ".codd/trace-after.md").is_file())

    def test_report_lists_changes_without_path_links(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        r = self.run_pa(self.impl, "report")
        self.assertIn("## 参照先とパスでつながっていない変更", r.stdout)
        self.assertIn("- src/app.py\n", r.stdout)
        (self.impl / "src/app.py").write_text("# coherence: doc=docs/api.md\ndef hello():\n    return 1\n",
                                              encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        r = self.run_pa(self.impl, "report")
        self.assertNotIn("## 参照先とパスでつながっていない変更", r.stdout)

    # ------------------------------------------------------------ 止まったとき

    def test_advise_proposes_next_steps(self) -> None:
        self.add_caller()
        self.write_plan(PLAN_ALIGNED)
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 1)
        for _ in range(2):   # 未判断だけなら、まず訊かずに練り直す（2 回まで）
            self.assertTrue(self.run_pa(self.impl, "advise").stdout.startswith("AUTO PLAN\n"))
        r = self.run_pa(self.impl, "advise")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# 計画の検査で止まりました", r.stdout)
        self.assertIn("src/use.py", r.stdout)
        self.assertIn("直すか「変更不要」か関係が無いかを決めてもらいます", r.stdout)
        self.assertIn("1. 計画を練り直す（勧め） → `PLAN`", r.stdout)
        self.assertNotIn("`APPLY`", r.stdout)   # 計画が通っていないので、変える段へは進めない

        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/use.py — 変更不要: 戻り値は同じ"))
        self.assert_plan_ok()
        self.assertFalse((self.impl / ".codd/problems.json").exists())   # 通ったら理由は消える
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / "src/extra.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
        r = self.run_pa(self.impl, "advise")
        self.assertIn("# 変えたあとの検査で止まりました", r.stdout)
        self.assertIn("1. 計画はそのままで、変え直す（勧め） → `APPLY`", r.stdout)
        self.assertIn("変えた分は残して、計画を直す → `PLAN`。先に `python3 .statemachine/codd/codd.py keep-changes` を実行",
                      r.stdout)
        self.assertIn("変えた分を戻して、計画から練り直す → `PLAN`。先に `python3 .statemachine/codd/codd.py rollback` を実行",
                      r.stdout)
        self.assertIn("`STOP`", r.stdout)

        # 設定の誤りで止まっても、理由と次の手は示す。
        cfg = self.impl / ".statemachine/codd/codd.json"
        good = cfg.read_text(encoding="utf-8")
        cfg.write_text(json.dumps({"side": "impl", "refs": [{"path": "../nowhere"}]}), encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 2)
        r = self.run_pa(self.impl, "advise")
        self.assertIn("設定か環境の誤りです", r.stdout)
        self.assertIn("refs を直してください", r.stdout)
        cfg.write_text(good, encoding="utf-8")

    def test_failures_needing_no_human_retry_without_asking(self) -> None:
        # テストが落ちただけなら、利用者に訊かずに変え直す（同じ段で 2 回まで）。人の判断が要るものが混じれば訊く。
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.set_check(self.impl, [sys.executable, "-c", "import sys; sys.exit('テストが落ちた')"])
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        for n in (1, 2):
            self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
            r = self.run_pa(self.impl, "advise")
            self.assertTrue(r.stdout.startswith("AUTO APPLY\n"), r.stdout)
            self.assertIn(f"{n}/2 回目", (self.impl / ".codd/decisions.json").read_text(encoding="utf-8"))
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
        r = self.run_pa(self.impl, "advise")
        self.assertNotIn("AUTO", r.stdout)                  # 上限を超えたら訊く
        self.assertIn("1. 計画はそのままで、変え直す（勧め） → `APPLY`", r.stdout)
        # 通れば数え直す。計画に無い変更が混じれば最初から訊く。
        self.set_check(self.impl, [sys.executable, "-c", "pass"])
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0, self.run_pa(self.impl, "verify-apply").stderr)
        self.set_check(self.impl, [sys.executable, "-c", "import sys; sys.exit('テストが落ちた')"])
        (self.impl / "src/extra.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
        self.assertNotIn("AUTO", self.run_pa(self.impl, "advise").stdout)

    def test_keep_changes_lets_the_plan_grow_without_losing_the_work(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / "src/log.py").write_text("def log(m):\n    print(m)\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)   # log.py は計画に無い
        self.assertEqual(self.run_pa(self.impl, "keep-changes").returncode, 0)
        self.write_plan(PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                             "- src/app.py — `hello` の中でログを出す\n- src/log.py — `log` を足す"))
        self.assert_plan_ok()
        r = self.run_pa(self.impl, "verify-apply")   # 前の印から数えるので、残した変更がそのまま効く
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own=changed", r.stdout)

    def test_rollback_restores_the_state_before_the_change(self) -> None:
        commit(self.impl, {"src/gone.py": "g = 1\n"}, "gone")
        (self.impl / "src/wip.py").write_text("wip = 1\n", encoding="utf-8")   # 計画より前から作業中
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.impl / "src/wip.py").write_text("wip = 2\n", encoding="utf-8")
        (self.impl / "src/new.py").write_text("n = 1\n", encoding="utf-8")
        (self.impl / "src/gone.py").unlink()
        (self.design / "docs/api.md").write_text("変えた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "rollback")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("変える前に戻しました: 5 files", r.stdout)
        self.assertEqual((self.impl / "src/app.py").read_text(encoding="utf-8"), "def hello():\n    return 1\n")
        self.assertEqual((self.impl / "src/wip.py").read_text(encoding="utf-8"), "wip = 1\n")   # 作業中の中身へ
        self.assertFalse((self.impl / "src/new.py").exists())
        self.assertEqual((self.impl / "src/gone.py").read_text(encoding="utf-8"), "g = 1\n")
        self.assertIn("hello は 1 を返す", (self.design / "docs/api.md").read_text(encoding="utf-8"))
        # 途中でコミットされたら、戻さずに知らせる。
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        git(self.impl, "commit", "-q", "-am", "mid")
        r = self.run_pa(self.impl, "rollback")
        self.assertEqual(r.returncode, 1)
        self.assertIn("途中でコミットされた", r.stderr)

    # ------------------------------------------------------------ リポジトリのスキル・カスタムエージェント

    def test_external_home_skills_are_read_without_agent_cli(self) -> None:
        for folder, name in ((".kiro/skills", "graphify"), (".copilot/skills", "caveman")):
            path = self.tmp / "home" / folder / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {name}\ndescription: external skill\n---\nExternal content\n", encoding="utf-8")
            r = self.run_pa(self.impl, "skill", name)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("External content", r.stdout)
            log = json.loads((self.impl / ".codd/skills-read.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(log[name]["path"]), path)

    def test_repo_skills_are_used_without_config(self) -> None:
        commit(self.impl, {".agents/skills/tdd-lite/SKILL.md":
                           "---\nname: tdd-lite\ndescription: テストを先に書く\n---\n\n# tdd-lite\n"}, "skill")
        r = self.run_pa(self.impl, "show")
        self.assertIn("リポジトリのスキル", r.stdout)
        self.assertIn("`tdd-lite` — テストを先に書く（.agents/skills/tdd-lite/SKILL.md）", r.stdout)
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()   # 使わないリポジトリのスキルを、1 つずつ断らせない
        # 使うと書いたスキルは、codd.py skill で読み込んでいなければ落とす（エージェントの自動選択に頼らない）。
        plan = PLAN_ALIGNED.replace("## 使ったスキルと道具\n\nなし",
                                    "## 使ったスキルと道具\n\n- `tdd-lite` — 変えるときにテストを先に書く")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("スキルを読み込んでいません", r.stderr)
        r = self.run_pa(self.impl, "skill", "tdd-lite")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# tdd-lite", r.stdout)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / ".codd/apply.md").write_text("- `tdd-lite` — テストを先に書いた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")   # 計画のときに読んだだけでは、変えるときに読んだことにならない
        self.assertEqual(r.returncode, 1)
        self.assertIn(".codd/apply.md に挙げたスキルを読み込んでいません", r.stderr)
        self.run_pa(self.impl, "skill", "tdd-lite")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.run_pa(self.impl, "skill", "nothing").returncode, 1)
        # 置き場所は skill_dirs で変えられ、[] で使わない。
        path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["skill_dirs"] = []
        path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertNotIn("リポジトリのスキル", self.run_pa(self.impl, "show").stdout)

    def test_draft_places_template_and_keeps_existing_plan(self) -> None:
        # 計画は一度に全文を書かせず、ひな形を置いて見出しごとに書かせる（応答の長さの上限で止まらないように）。
        # 名前は日時と英語の短い名前で一意にする。
        r = self.run_pa(self.impl, "draft")
        self.assertEqual(r.returncode, 2)
        self.assertIn("英語の短い名前", r.stderr)
        self.assertEqual(self.run_pa(self.impl, "draft", "--name", "ログを足す").returncode, 2)
        r = self.run_pa(self.impl, "draft", "--name", "add-hello-log")
        self.assertEqual(r.returncode, 0, r.stderr)
        plans = list((self.impl / ".plans").glob("*.md"))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertRegex(plan.name, r"^\d{4}-\d{2}-\d{2}-\d{4}-add-hello-log\.md$")
        template = (self.impl / ".statemachine/codd/templates/plan.md").read_text(encoding="utf-8")
        self.assertEqual(plan.read_text(encoding="utf-8"), template)
        # 書きかけのままでは計画の検査を通らない（コメントだけの見出しは空として落ちる）。
        self.read_up(self.impl)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("見出しの中身が空です", r.stdout + r.stderr)
        plan.write_text(PLAN_ALIGNED, encoding="utf-8")
        self.assertIn("計画はもうあります", self.run_pa(self.impl, "draft", "--name", "other").stdout)
        self.assertEqual(plan.read_text(encoding="utf-8"), PLAN_ALIGNED)
        self.run_pa(self.impl, "draft", "--new", "--name", "add-hello-log")
        plans = list((self.impl / ".plans").glob("*.md"))
        self.assertEqual(len(plans), 1)    # 進めていた計画は捨てて置き直す（同じ時刻なら -2 が付く）
        self.assertEqual(plans[0].read_text(encoding="utf-8"), template)

    def test_summary_is_short_and_points_to_the_plan(self) -> None:
        self.assertEqual(self.run_pa(self.impl, "summary").returncode, 1)
        many = "\n".join(f"- src/m{i}.py — {'長い説明' * 40}" for i in range(20))
        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし", f"## 影響範囲\n\n{many}"), read=False)
        r = self.run_pa(self.impl, "summary")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn(f"全文: {PLAN}", out)
        self.assertIn("## やりたいこと\n\nhello にログを足す。", out)
        self.assertIn("## 自分の変更案", out)
        self.assertIn(f"- ほか 8 件（{PLAN}）", out)
        self.assertNotIn("src/m12.py", out)
        self.assertTrue(all(len(ln) <= 120 for ln in out.splitlines()))
        # 根拠の見出しは件数だけ（全文は貼らない）。
        self.assertIn("根拠: 参照先の前提", out)
        self.assertNotIn("変更不要", out)
        self.assertNotIn("## 参照先の前提", out)

    @unittest.skipIf(os.name == "nt", "偽の uv を sh で作る")
    def test_install_puts_middleware_on_the_terminal_and_init_places_codd(self) -> None:
        # install.py は外部のミドルウェア（graphify など）を端末に入れる。codd はリポジトリに init.py で置く。
        bin_dir = self.tmp / "tools-bin"
        bin_dir.mkdir()
        uv_log = self.tmp / "uv.log"
        uv = bin_dir / "uv"
        uv.write_text(f'#!/bin/sh\necho "$@" >> {uv_log}\n'
                      f'printf \'#!/bin/sh\\necho "graphify 1.0"\\n\' > {bin_dir}/graphify\n'
                      f'chmod +x {bin_dir}/graphify\n', encoding="utf-8")
        uv.chmod(0o755)
        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(self.tmp / "home")}
        run = lambda *a: subprocess.run([sys.executable, str(TOOL / "install.py"), *a], capture_output=True,
                                        text=True, env=env)
        r = run("--no-skills")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(uv_log.read_text(encoding="utf-8").split(), ["tool", "install", "graphifyy"])
        self.assertIn("✓ graphify: graphify 1.0", r.stdout)
        self.assertIn("✓ git:", r.stdout)
        self.assertNotIn("webui-test", r.stdout)
        r = run("--no-skills")   # 入っていれば入れ直さない
        self.assertEqual(uv_log.read_text(encoding="utf-8").split(), ["tool", "install", "graphifyy"])
        run("--upgrade", "--no-skills")
        self.assertEqual(uv_log.read_text(encoding="utf-8").split()[-3:], ["tool", "upgrade", "graphifyy"])
        # 端末に入れても、リポジトリには何も置かない。
        app = self.tmp / "app"
        app.mkdir()
        git(app, "init", "-q", "-b", "main")
        self.assertFalse((app / ".statemachine").exists())
        # 以前の使い方（install.py にリポジトリを渡す）は init.py に渡す。
        rc = install.main([str(app), "--side", "impl", "--ref", "../design", "--no-agents", "--no-discover-rules"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads((app / ".statemachine/codd/codd.json").read_text(encoding="utf-8"))["side"], "impl")

    def test_middleware_installs_external_skills_for_both_ides_without_agent_cli(self) -> None:
        with mock.patch.object(install, "setup_caveman", return_value=True) as caveman, \
             mock.patch.object(install, "version", return_value="graphify 1.0"), \
             mock.patch.object(install.shutil, "which", side_effect=lambda name: f"/tools/{name}" if name in ("graphify", "git") else None), \
             mock.patch.object(install.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(install.main([]), 0)
        self.assertEqual(caveman.call_args_list,
                         [mock.call("kiro", force=False), mock.call("copilot", force=False)])
        self.assertEqual(run.call_args_list,
                         [mock.call(["/tools/graphify", "install", "--platform", agent], timeout=install.TIMEOUT)
                          for agent in ("kiro", "copilot")])

    def test_middleware_agent_option_and_upgrade_do_not_delegate_to_repo_init(self) -> None:
        with mock.patch.object(install, "install_middleware", return_value=0) as middleware:
            self.assertEqual(install.main(["--agent", "kiro", "--upgrade"]), 0)
            middleware.assert_called_once_with(True, agents=["kiro"], skills=True)
        with mock.patch.object(install, "install_middleware", return_value=0) as middleware:
            self.assertEqual(install.main(["--no-skills"]), 0)
            middleware.assert_called_once_with(False, agents=install.AGENT_KINDS, skills=False)

    def test_middleware_reports_failed_skill_registration_and_still_installs_other_skills(self) -> None:
        with mock.patch.object(install, "setup_caveman", return_value=True) as caveman, \
             mock.patch.object(install, "version", return_value="graphify 1.0"), \
             mock.patch.object(install, "run_first", return_value="uv"), \
             mock.patch.object(install.shutil, "which", return_value="/tools/graphify"), \
             mock.patch.object(install.subprocess, "run", side_effect=[subprocess.TimeoutExpired("graphify", 600),
                                                                      mock.Mock(returncode=0)]) as run:
            self.assertEqual(install.install_middleware(upgrade=True, agents=("kiro", "copilot")), 1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(caveman.call_args_list,
                         [mock.call("kiro", force=True), mock.call("copilot", force=True)])

    def test_middleware_runs_from_standalone_distribution_without_root_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tool = root / "distribution with spaces/tools/codd-agent"
            tool.mkdir(parents=True)
            script = tool / "install.py"
            shutil.copy2(TOOL / "install.py", script)
            self.assertFalse((tool.parent.parent / "install.py").exists())
            archive_data = io.BytesIO()
            with zipfile.ZipFile(archive_data, "w") as archive:
                archive.writestr("caveman-main/skills/caveman/SKILL.md", "official skill")
                archive.writestr("caveman-main/skills/caveman/references/detail.md", "official reference")
                archive.writestr("caveman-main/LICENSE", "official license")
            # Run from an unrelated working directory with no installer or agent CLIs.
            runner = root / "run.py"
            runner.write_text(textwrap.dedent(f"""\
                import io, runpy
                from unittest import mock
                with mock.patch.dict("os.environ", {{"USERPROFILE": {str(root / 'home')!r}}}), \
                     mock.patch("shutil.which", side_effect=lambda name: "graphify" if name == "graphify" else None), \
                     mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout="graphify 1.0", stderr="")), \
                     mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO({archive_data.getvalue()!r})):
                    runpy.run_path({str(script)!r}, run_name="__main__")
                """), encoding="utf-8")
            run = subprocess.run([sys.executable, str(runner)], cwd=root, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            for agent in ("kiro", "copilot"):
                dest = root / f"home/.{agent}/skills/caveman"
                self.assertEqual((dest / "SKILL.md").read_text(), "official skill")
                self.assertEqual((dest / "references/detail.md").read_text(), "official reference")
                self.assertEqual((dest / "LICENSE").read_text(), "official license")

    def test_middleware_caveman_preserves_existing_skill_on_failed_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"USERPROFILE": home}):
            dest = Path(home) / ".kiro/skills/caveman"
            dest.mkdir(parents=True)
            (dest / "SKILL.md").write_text("user skill")
            with mock.patch.object(install.urllib_request, "urlopen", side_effect=OSError("offline")) as fetch:
                self.assertTrue(install.setup_caveman("kiro"))
                fetch.assert_not_called()
                self.assertFalse(install.setup_caveman("kiro", force=True))
            self.assertEqual((dest / "SKILL.md").read_text(), "user skill")

    def test_middleware_rejects_invalid_caveman_archive_before_writing(self) -> None:
        cases = [None, {"skills/caveman/README.md": "missing skill"},
                 {"skills/caveman/SKILL.md": "skill", "skills/caveman/../../escape": "bad path"}]
        for files in cases:
            with self.subTest(files=files), tempfile.TemporaryDirectory() as home:
                data = io.BytesIO(b"not a zip")
                if files is not None:
                    data = io.BytesIO()
                    with zipfile.ZipFile(data, "w") as archive:
                        for path, body in files.items():
                            archive.writestr(f"caveman-main/{path}", body)
                dest = Path(home) / ".copilot/skills/caveman"
                dest.mkdir(parents=True)
                (dest / "SKILL.md").write_text("user skill")
                with mock.patch.dict(os.environ, {"USERPROFILE": home}), \
                     mock.patch.object(install.urllib_request, "urlopen", return_value=io.BytesIO(data.getvalue())):
                    self.assertFalse(install.setup_caveman("copilot", force=True))
                self.assertEqual((dest / "SKILL.md").read_text(), "user skill")
                self.assertFalse((Path(home) / ".copilot/escape").exists())

    def test_install_writes_custom_agents(self) -> None:
        kiro = json.loads((self.impl / ".kiro/agents/codd.json").read_text(encoding="utf-8"))
        self.assertEqual(kiro["name"], "codd")
        self.assertIn("skill://~/.kiro/skills/caveman/SKILL.md", kiro["resources"])
        self.assertFalse((self.impl / ".statemachine/codd/skills").exists())
        self.assertFalse((self.impl / ".kiro/skills").exists())
        self.assertFalse((self.impl / ".github/skills").exists())
        self.assertIn("必ず codd のステートマシン", kiro["prompt"])
        self.assertEqual(kiro["hooks"]["agentSpawn"][0]["command"], "python3 .statemachine/codd/codd.py show")
        copilot = (self.impl / ".github/agents/codd.agent.md").read_text(encoding="utf-8")
        self.assertTrue(copilot.startswith("---\nname: codd\ndescription: "))
        self.assertIn("必ず codd のステートマシン", copilot)
        # 応答の長さに上限があるエージェントでも止まらないよう、全文を貼らず分けて書くことを指示する。
        self.assertIn("the response hit the length limit", copilot)
        self.assertIn("codd.py summary", copilot)
        # エージェントのファイルはマシンの一部なので、影響範囲や変えたファイルに数えない。
        self.run_pa(self.impl, "impact", "--term", "statemachine")
        self.assertNotIn("codd.agent.md", (self.impl / ".codd/impact.md").read_text(encoding="utf-8"))
        other = self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        init.init_repo(other, "impl", ["../design"], discover=False, agents=())
        self.assertFalse((other / ".kiro").exists())
        self.assertFalse((other / ".github").exists())

    def test_init_uses_only_explicit_check(self) -> None:
        cfg = self.impl / ".statemachine/codd/codd.json"
        self.assertNotIn("check", json.loads(cfg.read_text(encoding="utf-8")))
        webui = ("serve: { command: npm start, url: http://localhost:3000 }\ncheck:\n  cases: [tests/e2e]\n"
                 "envs: { local: {} }\n")
        # 既にある codd.json には、あとから置いた webui-test の設定を勝手に書き足さない。
        (self.impl / "webui-test.config.yaml").write_text(webui, encoding="utf-8")
        init.init_repo(self.impl, None, None, discover=False)
        self.assertNotIn("check", json.loads(cfg.read_text(encoding="utf-8")))
        # 初めて置くときも、ほかの道具の設定からコマンドを推測しない。
        app = self.tmp / "app"
        app.mkdir()
        git(app, "init", "-q", "-b", "main")
        (app / "webui-test.config.yaml").write_text(webui, encoding="utf-8")
        init.init_repo(app, "impl", ["../design"], discover=False)
        self.assertNotIn("check", json.loads((app / ".statemachine/codd/codd.json").read_text(encoding="utf-8")))
        init.init_repo(self.impl, None, None, discover=False, check="webui-test check")
        r = self.run_pa(self.impl, "show")
        self.assertIn("変えたあとに実行するもの", r.stdout)
        self.assertIn("自分の検査: webui-test check", r.stdout)
        # 手で書いた検査は上書きしない。--check で書き換え、"" で消す。
        init.init_repo(self.impl, None, None, discover=False, check="python3 -m pytest -q")
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8"))["check"], ["python3", "-m", "pytest", "-q"])
        init.init_repo(self.impl, None, None, discover=False)
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8"))["check"], ["python3", "-m", "pytest", "-q"])
        init.init_repo(self.impl, None, None, discover=False, check="")
        self.assertNotIn("check", json.loads(cfg.read_text(encoding="utf-8")))
        # 単体テストは webui-test ではなく codd の test に書く。--test で書き、"" で消す。
        init.init_repo(self.impl, None, None, discover=False, test="npm test")
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8"))["test"], ["npm", "test"])
        self.assertIn("自分のテスト: npm test", self.run_pa(self.impl, "show").stdout)
        init.init_repo(self.impl, None, None, discover=False, test="")
        self.assertNotIn("test", json.loads(cfg.read_text(encoding="utf-8")))

    def test_verify_apply_needs_a_verified_plan(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("計画を検査したときの印がありません", r.stderr)
        self.assert_plan_ok()
        self.assertTrue((self.impl / ".codd/before.json").is_file())

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
        init.init_repo(self.impl, None, None)
        self.assertFalse(stale.exists())
        cfg = json.loads((self.impl / ".statemachine/codd/codd.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg, {"side": "impl", "refs": [{"path": "../design"}],
                               "skills": {"plan": [], "apply": []}, "graphify": "auto"})
        # 既にある codd.json は、手で書いた形のまま残す（並べ直し・書き足しもしない）。
        path = self.impl / ".statemachine/codd/codd.json"
        hand = ('{"side": "impl", "refs": [{"name": "design", "path": "../design", "rules": ["docs/r.md"],'
                ' "scope": ["docs"]}],\n "skills": {"plan": [], "apply": []}, "graphify": "auto"}\n')
        path.write_text(hand, encoding="utf-8")
        init.init_repo(self.impl, None, None)
        self.assertEqual(path.read_text(encoding="utf-8"), hand)
        init.init_repo(self.impl, "impl", ["../design"])  # 同じ値を渡しても書き直さない
        self.assertEqual(path.read_text(encoding="utf-8"), hand)
        # --ref で参照先を入れ替えても、同じ名前の参照先に手で書いた rules・scope は残す。
        init.init_repo(self.impl, None, ["design=../design-v2"])
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["refs"],
                         [{"name": "design", "path": "../design-v2", "rules": ["docs/r.md"], "scope": ["docs"]}])
        self.assertEqual((self.impl / ".gitignore").read_text(encoding="utf-8").splitlines().count(".codd/"), 1)
        self.assertEqual((self.impl / ".graphifyignore").read_text(encoding="utf-8").splitlines(),
                         [".statemachine/codd/"])
        fresh = self.tmp / "fresh"
        fresh.mkdir()
        git(fresh, "init", "-q")
        with self.assertRaises(SystemExit):
            init.init_repo(fresh, "impl", [])

    # ------------------------------------------------------------ 参照先が複数

    def add_second_ref(self) -> Path:
        api = self.tmp / "api"
        api.mkdir()
        git(api, "init", "-q", "-b", "main")
        commit(api, {"docs/api.md": "# API\n\n## hello\n\nHTTP でも hello を返す。\n",
                     "spec/hello.md": "# hello\n"}, "init")
        init.init_repo(api, "design", ["../impl"])
        self.set_check(api, [sys.executable, "-c", "print('api ok')"])
        init.init_repo(self.impl, None, ["design=../design", "api=../api"])
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
                        .replace("docs/api.md:3", "api:docs/api.md:3")
                        .replace("## 参照先のその他\n\nなし", "## 参照先のその他\n\n- 見出しだけ（根拠: api:spec/hello.md）"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        # spec/hello.md は api にしか無いので、名前なしでよい。
        self.write_plan(PLAN_ALIGNED.replace("docs/api.md#hello", "spec/hello.md")
                        .replace("docs/api.md:3", "design:docs/api.md:3")
                        .replace("## 参照先のその他\n\nなし", "## 参照先のその他\n\n- HTTP の口（根拠: api:docs/api.md）"))
        self.assert_plan_ok()

    def test_verify_apply_checks_each_ref_against_the_plan(self) -> None:
        api = self.add_second_ref()
        plan = (PLAN_DRIFT.replace("（根拠: docs/api.md）", "（根拠: design:docs/api.md）")
                .replace("docs/api.md:3", "design:docs/api.md:3")
                .replace("- docs/api.md — `hello`", "- api:docs/api.md — `hello`")
                .replace("- docs/api.md — 文書の書式", "- api:docs/api.md — 文書の書式")
                .replace("## 参照先のその他\n\n- なし", "## 参照先のその他\n\n- 見出しだけ（根拠: api:spec/hello.md）"))
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

    def test_doc_format_is_a_rule_to_keep(self) -> None:
        # 文書の今の書式（見出しの並び）はコードの決まりと同じ。守る決まりに見本を挙げ、変えたあとも守る。
        self.write_plan(PLAN_DRIFT.replace("- docs/api.md — 文書の書式: 今の見出しの並び（hello の節）と書き方を保つ", "なし"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        # 見本と見出しの並びを守る決まりに書き足す（formats.md を開かなくても守り方を書ける）
        self.assertIn("- design:docs/api.md — 未判断: docs/api.md の書式の見本（見出し: hello）",
                      (self.impl / PLAN).read_text(encoding="utf-8"))
        self.assertIn("  - ## hello", (self.impl / ".codd/formats.md").read_text(encoding="utf-8"))
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\nhello は 2 を返す。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("文書の書式（見出しの並び）が今の書式から外れています: docs/api.md — ## hello", r.stderr)
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertNotIn("文書の書式", r.stderr)
        # 見出しを変えると計画に `## 見出し` と書いたなら、その見出しは外れてよい。
        self.write_plan(PLAN_DRIFT.replace("と書き直す", "と書き直し、`## hello` を `## hello()` にする"))
        self.assert_plan_ok()
        (self.design / "docs/api.md").write_text("# API\n\n## hello()\n\nhello は 2 を返す。\n", encoding="utf-8")
        self.assertNotIn("文書の書式", self.run_pa(self.impl, "verify-apply").stderr)

    def test_new_doc_follows_the_format_of_its_folder(self) -> None:
        commit(self.design, {
            "docs/screens/login.md": "# ログイン\n\n## 目的\n\nx\n\n## 画面\n\nx\n\n## 入力\n\nx\n",
            "docs/screens/home.md": "# ホーム\n\n## 目的\n\nx\n\n## 画面\n\nx\n\n## 操作\n\nx\n",
            "docs/screens/README.md": "# 画面の一覧\n\n## 一覧\n",
        }, "screens")
        plan = PLAN_DRIFT.replace("- docs/api.md — `hello`", "- docs/screens/hello.md — 新しく書く。`hello`")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("design:docs/screens/home.md — 未判断: docs/screens/hello.md の書式の見本（見出し: 目的 / 画面）",
                      (self.impl / PLAN).read_text(encoding="utf-8"))
        formats = (self.impl / ".codd/formats.md").read_text(encoding="utf-8")
        self.assertIn("  - ## 目的\n  - ## 画面\n", formats)
        self.assertNotIn("## 一覧", formats)   # 索引の README は見本にしない
        self.write_plan(plan.replace("## 守る決まり\n\n- docs/api.md", "## 守る決まり\n\n- docs/screens/login.md — 書式\n- docs/api.md"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/screens/hello.md").write_text("# hello\n\n## 画面\n\nx\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertIn("docs/screens/hello.md — ## 目的", r.stderr)

    def write_evidence(self, items: list[dict]) -> None:
        cfg = self.impl / ".statemachine/codd/codd.json"
        config = json.loads(cfg.read_text(encoding="utf-8"))
        config["evidence"] = ["results/ui-evidence.json"]
        cfg.write_text(json.dumps(config), encoding="utf-8")
        out = self.impl / "results"
        out.mkdir(exist_ok=True)
        (out / ".gitignore").write_text("*\n", encoding="utf-8")
        (out / "ui-evidence.json").write_text(json.dumps({"version": 1, "root": "..", "items": items}, ensure_ascii=False), encoding="utf-8")

    def test_evidence_requires_explicit_paths_and_preserves_settings(self) -> None:
        cfg = self.impl / ".statemachine/codd/codd.json"
        out = self.impl / "results"
        out.mkdir()
        (out / "ui-evidence.json").write_text(json.dumps({"items": self.evidence_items(850)}), encoding="utf-8")
        # ファイルがあっても、パスを指定するまで読み込まない。
        self.assertIn("evidence）がありません", self.run_pa(self.impl, "evidence").stdout)
        r = subprocess.run([sys.executable, str(TOOL / "init.py"), str(self.impl), "--no-discover-rules",
                            "--no-agents", "--evidence", "results/ui-evidence.json", "--evidence", "other/*.json"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("自分: 2 件（results/ui-evidence.json）", self.run_pa(self.impl, "evidence").stdout)
        init.init_repo(self.impl, None, None, discover=False)
        self.assertEqual(json.loads(cfg.read_text())["evidence"], ["results/ui-evidence.json", "other/*.json"])
        init.init_repo(self.impl, None, None, discover=False, evidence=[""])
        self.assertEqual(json.loads(cfg.read_text())["evidence"], [])
        self.assertIn("evidence）がありません", self.run_pa(self.impl, "evidence").stdout)

    def evidence_items(self, load: int, status: str = "passed") -> list[dict]:
        base = {"file": "tests/login.yaml", "doc": ["design:docs/api.md"]}
        return [{"id": "login/S-01", "kind": "behavior", "title": "ログイン できる", "status": status, **base},
                {"id": "login/S-01/load", "kind": "metric", "title": "ログイン（読み込み）", "value": load, "unit": "ms", **base}]

    def test_test_evidence_is_written_into_docs_and_kept_current(self) -> None:
        # テストで得たもの（振る舞い・時間）を文書の印で写し、今と違えば・目安を超えれば止める。
        commit(self.design, {"docs/api.md": "# API\n\n## hello\n\nhello は 1 を返す。\n\n"
                             "読み込み: <!-- evidence: login/S-01/load max=1000 -->800 ms<!-- /evidence -->\n\n"
                             "確かめた振る舞い:<!-- evidence: login/* --><!-- /evidence -->\n"}, "marks")
        self.write_evidence(self.evidence_items(850))
        r = self.run_pa(self.impl, "evidence")
        self.assertEqual(r.returncode, 1, r.stdout)       # 一覧の印はまだ空
        self.assertIn("自分: 2 件", r.stdout)
        self.assertIn("docs/api.md:9 login/*（書いてある 空 → 今 - ✓ ログイン できる - 850 ms）", r.stdout)
        self.assertNotIn("login/S-01/load（", r.stdout)    # 2 割までの揺れは同じとみなす
        r = self.run_pa(self.impl, "evidence", "--write", "design:docs/api.md")
        self.assertEqual(r.returncode, 0, r.stdout)
        text = (self.design / "docs/api.md").read_text(encoding="utf-8")
        self.assertIn("-->\n- ✓ ログイン できる\n- 850 ms\n<!--", text)
        self.assertIn("-->800 ms<!--", text, "揺れの幅の中なら書き換えない")
        self.write_evidence(self.evidence_items(1300, "failed"))
        r = self.run_pa(self.impl, "evidence")
        self.assertEqual(r.returncode, 1)
        self.assertIn("1300 ms で、目安の 1000 を超えています", r.stdout)
        self.assertIn("確かめた振る舞いが失敗しています: ログイン できる", r.stdout)
        self.assertIn("書いてある 800 ms → 今 1300 ms", r.stdout)

    def test_evidence_examples_in_code_fences_are_not_real_marks(self) -> None:
        # 書き方の例（コードブロック・`…` の中の印）は、一覧・検査・書き戻しのどれにも数えない。
        example = ("```markdown\n"
                   "読み込み: <!-- evidence: login/S-01/load max=10 -->999 ms<!-- /evidence -->\n"
                   "無い id: <!-- evidence: nothing/here -->例<!-- /evidence -->\n"
                   "閉じ忘れの例: <!-- evidence: login/S-01/load -->\n"
                   "```\n")
        inline = "書き方: `<!-- evidence: inline/only -->値<!-- /evidence -->`\n"
        commit(self.design, {"docs/api.md": "# API\n\n## hello\n\n" + example + inline +
                             "\n読み込み: <!-- evidence: login/S-01/load -->1 ms<!-- /evidence -->\n"}, "marks")
        self.write_evidence(self.evidence_items(850))
        r = self.run_pa(self.impl, "evidence")
        self.assertIn("文書の印: 1 件", r.stdout)                    # 一覧: 本物の 1 つだけ
        self.assertNotIn("nothing/here", r.stdout)                   # 検査: 例の知らない id で落とさない
        self.assertNotIn("inline/only", r.stdout)
        self.assertNotIn("目安の 10 を超えています", r.stdout)       # 例の目安で落とさない
        self.assertIn("docs/api.md:12 login/S-01/load（書いてある 1 ms → 今 850 ms）", r.stdout)
        r = self.run_pa(self.impl, "evidence", "--write")
        self.assertIn("写し直した: docs/api.md", r.stdout)
        text = (self.design / "docs/api.md").read_text(encoding="utf-8")
        self.assertIn(example + inline, text)                        # 書き戻し: 例はそのまま
        self.assertIn("\n読み込み: <!-- evidence: login/S-01/load -->850 ms<!-- /evidence -->\n", text)
        self.assertEqual(self.run_pa(self.impl, "evidence").returncode, 0)

    def test_changed_screens_replace_the_images_docs_show(self) -> None:
        # テストの側は文書を知らない。画面のこれまでの版と同じ画像を文書のリポジトリから sha256 で見つけ、差し替える。
        old, new, gone = b"PNG-v1 login", b"PNG-v2 login", b"PNG-old menu"
        sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
        commit(self.design, {"docs/login.md": "# ログイン\n\n![ログイン](images/login.png)\n",
                             "docs/menu.md": "# メニュー\n\n<img src=\"images/menu.png\">\n"}, "screens")
        (self.design / "docs/images").mkdir(parents=True, exist_ok=True)
        (self.design / "docs/images/login.png").write_bytes(old)
        (self.design / "docs/images/menu.png").write_bytes(gone)
        git(self.design, "add", "-A")
        git(self.design, "commit", "-q", "-m", "images")
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n", encoding="utf-8")
        screens = self.impl / "results/screens"
        screens.mkdir(parents=True)
        (screens.parent / ".gitignore").write_text("*\n", encoding="utf-8")
        (screens / "login.png").write_bytes(new)
        base = {"kind": "image", "file": "tests/login.yaml"}
        self.write_evidence([
            {**base, "id": "login/S-01/login", "status": "changed", "path": "results/screens/login.png",
             "sha256": sha(new), "history": [sha(new), sha(old)]},
            {**base, "id": "menu/S-01/menu", "status": "removed", "sha256": sha(gone), "history": [sha(gone)]},
        ])
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)   # ハーネスの差し替えは「計画に無い変更」に数えない
        self.assertEqual((self.design / "docs/images/login.png").read_bytes(), new)
        self.assertIn("テストの画面から: docs/images/login.png ← login/S-01/login（貼っている文書: docs/login.md）", r.stdout)
        self.assertIn("docs/images/menu.png — テストで撮らなくなった画面です（menu/S-01/menu）。貼っている文書: docs/menu.md",
                      r.stdout)
        report = self.run_pa(self.impl, "report")
        self.assertEqual(report.returncode, 0, report.stdout)
        self.assertIn("## テストの画面から差し替えた文書の画像", report.stdout)
        # もう一度検査しても、差し替えた画像は今の画面なので何もしない
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)

    def test_plan_must_handle_docs_showing_results_of_affected_tests(self) -> None:
        commit(self.impl, {"tests/login.yaml": "suite: ログイン\n"}, "case")
        commit(self.design, {"docs/perf.md": "# 性能\n\n- <!-- evidence: login/S-01/load -->800 ms<!-- /evidence -->\n"},
               "perf")
        self.write_evidence(self.evidence_items(800))
        plan = PLAN_ALIGNED.replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）",
                                    "- tests/login.yaml — `hello` のログも確かめる")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("変更が響くテストの結果を写している文書が、計画にありません", r.stderr)
        self.assertIn("docs/perf.md", r.stderr)
        evidence = (self.impl / ".codd/evidence.md").read_text(encoding="utf-8")
        self.assertIn("login/S-01/load — metric: 800 ms", evidence)
        self.write_plan(plan.replace("## 参照先のその他\n\nなし",
                                     "## 参照先のその他\n\n- 読み込みの時間を写している（根拠: docs/perf.md）"))
        self.assert_plan_ok()
        # 変えたあと、時間が大きく変わったら写し直させ、報告に変化を出す。
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n", encoding="utf-8")
        (self.impl / "tests/login.yaml").write_text("suite: ログイン（ログも）\n", encoding="utf-8")
        self.write_evidence(self.evidence_items(1600))
        r = self.run_pa(self.impl, "verify-apply")
        self.assertIn("文書に写したテストの結果が今と違います", r.stderr)
        self.assertIn("書いてある 800 ms → 今 1600 ms", r.stderr)
        report = self.run_pa(self.impl, "report").stdout
        self.assertIn("## テストで得たものの変化（計画のときと比べて）", report)
        self.assertIn("login/S-01/load — 800 → 1600 ms", report)

    def test_stale_marks_in_unrelated_docs_do_not_block_apply(self) -> None:
        # 計画に無い文書にもとからある古い印は、直すと計画に無いファイルを変えることになるので、ここでは止めない（lint が拾う）。
        commit(self.design, {"docs/other.md": "# other\n\n<!-- evidence: login/S-01/load -->100 ms<!-- /evidence -->\n"},
               "marks")
        self.write_evidence(self.evidence_items(850))
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1 # changed\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("docs/other.md", self.run_pa(self.impl, "lint", "--no-test").stdout)

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
        self.assertIn("使うスキルと道具（計画を練るとき）:\n  - 自分: `domain-modeler` スキル", r.stdout)
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

    # ------------------------------------------------------------ 根拠を箇所まで確かめる

    def test_evidence_points_at_real_lines_headings_and_names(self) -> None:
        cases = {
            "6 行目はありません": PLAN_ALIGNED.replace("docs/api.md:3", "docs/api.md:6"),
            "3-9 行目はありません": PLAN_ALIGNED.replace("docs/api.md:3", "docs/api.md:3-9"),
            "見出し #goodbye がありません": PLAN_ALIGNED.replace("docs/api.md#hello", "docs/api.md#goodbye"),
            "根拠のファイルに見当たりません: `hello_world`": PLAN_ALIGNED.replace(
                "- hello は整数を返す", "- `hello_world` は整数を返す"),
        }
        for expected, plan in cases.items():
            with self.subTest(expected=expected):
                self.write_plan(plan)
                r = self.run_pa(self.impl, "verify-plan")
                self.assertEqual(r.returncode, 1)
                self.assertIn(expected, r.stderr)
        # 書かれている名前・実在する行と見出しなら通る（`hello()` は hello として探す）。
        self.write_plan(PLAN_ALIGNED.replace("- hello は整数を返す", "- `hello()` は整数を返す")
                        .replace("docs/api.md:3", "docs/api.md:3-5").replace("#hello", "#HELLO"))
        self.assert_plan_ok()

    def test_own_change_items_name_own_files(self) -> None:
        self.write_plan(PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す", "- ログを出す"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自分の変更案の項目に、自分のリポジトリのパスがありません", r.stderr)
        # まだ無いファイル（親のフォルダはある）は書ける。
        self.write_plan(PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                             "- src/app.py — `hello` でログを出す\n- src/log.py — 新しく書く"))
        self.assert_plan_ok()

    # ------------------------------------------------------------ 1 回で終わる大きさ

    def test_plan_must_fit_in_one_run(self) -> None:
        path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["max_files"] = 1
        path.write_text(json.dumps(cfg), encoding="utf-8")
        self.write_plan(PLAN_DRIFT)   # src/app.py と design の docs/api.md で 2 つ
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("1 回で変えるファイルが 2 あり、上限 1 を超えています", r.stderr)
        self.assertIn("## 今回やらないこと", r.stderr)
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.assertIn("1 回で変えるファイルの上限: 1", self.run_pa(self.impl, "show").stdout)
        tpl = PLAN_ALIGNED.replace("## 今回やらないこと\n\nなし\n", "")
        self.write_plan(tpl)
        self.assertIn("見出しがありません: ## 今回やらないこと", self.run_pa(self.impl, "verify-plan").stderr)

    # ------------------------------------------------------------ 計画に無い変更を止める

    def test_verify_apply_rejects_files_outside_the_plan(self) -> None:
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.impl / "src/extra.py").write_text("x = 1\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n", encoding="utf-8")
        (self.design / "docs/other.md").write_text("# other\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("計画に無いファイルを変えています", r.stderr)
        self.assertIn("src/extra.py", r.stderr)
        self.assertIn("design で参照先の変更案に無いファイルを変えています", r.stderr)
        self.assertIn("docs/other.md", r.stderr)
        (self.impl / "src/extra.py").unlink()
        (self.design / "docs/other.md").unlink()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    # ------------------------------------------------------------ テストもコード・仕様書と同じに扱う

    TESTS_WAIVED = "- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）"

    def with_tests(self, plan: str, body: str) -> str:
        return plan.replace(self.TESTS_WAIVED, body)

    def test_plan_must_handle_tests_hit_by_the_change(self) -> None:
        # 名前で響く単体テストと、仕様書をパスで指している e2e のケース（同じ側のコードを指すものも）。
        commit(self.impl, {
            "tests/test_app.py": "from src.app import hello\n\ndef test_hello():\n    assert hello() == 1\n",
            "tests/e2e/hello.yaml": "# coherence: doc=docs/api.md\nsuite: hello\n",
            "tests/e2e/page.yaml": "# coherence: code=src/app.py\nsuite: page\n",
        }, "tests")
        self.write_plan(PLAN_DRIFT)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        plan = (self.impl / PLAN).read_text(encoding="utf-8")
        tests_part = plan.split("## テストの変更案", 1)[1].split("\n## ", 1)[0]
        for rel in ("tests/test_app.py", "tests/e2e/hello.yaml", "tests/e2e/page.yaml"):
            self.assertIn(rel, r.stderr)
            self.assertIn(f"- {rel} — 未判断", tests_part)   # テストはテストの変更案に（影響範囲には入れない）
            self.assertEqual(plan.count(f"- {rel} — "), 1)
        report = (self.impl / ".codd/tests.md").read_text(encoding="utf-8")
        self.assertIn("tests/test_app.py — 名前", report)
        self.assertIn("tests/e2e/hello.yaml — つながり", report)
        self.write_plan(self.with_tests(PLAN_DRIFT, "\n".join([
            "- tests/test_app.py — `hello` が 2 を返すことを確かめるように直す",
            "- tests/e2e/hello.yaml — 2 を確かめるように直す",
            "- tests/e2e/page.yaml — 変更不要: 戻り値を見ていない",
        ])))
        self.assert_plan_ok()

        # 変えたあと: 挙げたテストを変えていなければ落とし、単体テスト（test）も codd が実行する。
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("## テストの変更案 のテストをまだ変えていません", r.stderr)
        self.assertIn("tests/test_app.py", r.stderr)
        (self.impl / "tests/test_app.py").write_text(
            "from src.app import hello\n\ndef test_hello():\n    assert hello() == 2\n", encoding="utf-8")
        (self.impl / "tests/e2e/hello.yaml").write_text("# coherence: doc=docs/api.md\nsuite: hello 2\n",
                                                        encoding="utf-8")
        path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["test"] = [sys.executable, "-c", "print('unit failed'); raise SystemExit(3)"]
        path.write_text(json.dumps(cfg), encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("実装のテストの検査が失敗しました（3）", r.stderr)
        self.assertIn("unit failed", r.stderr)
        cfg["test"] = [sys.executable, "-c", "print('unit ok')"]
        path.write_text(json.dumps(cfg), encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = self.run_pa(self.impl, "report").stdout
        self.assertIn("## テスト", report)
        self.assertIn("tests/test_app.py — 変えた", report)
        self.assertNotIn("tests/e2e/page.yaml — 変更不要", report)

    def test_test_plan_does_not_treat_search_paths_as_changes(self) -> None:
        commit(self.impl, {"tests/test_app.py": "hello()\n",
                           "tests/search_input.txt": "fixture\n",
                           "tests/search_output.txt": "result\n"}, "tests")
        self.set_config(self.impl, max_files=2)
        body = ("- `tests/test_app.py` — `hello` のログを確かめる。検索は "
                "`rg -n needle tests/search_input.txt > tests/search_output.txt`\n"
                "- 検索: `rg -n needle tests/search_input.txt > tests/search_output.txt`\n"
                "- `git grep needle -- tests/search_input.txt`\n"
                "- 変更不要: 参照先のテストは無い。検索は `rg needle design:docs/api.md`\n"
                "\n```sh\nrg needle tests/search_input.txt > tests/search_output.txt\n"
                "- tests/search_output.txt\n```\n")
        self.write_plan(self.with_tests(PLAN_ALIGNED, body))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n")
        (self.impl / "tests/test_app.py").write_text("hello() # check log\n")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = self.run_pa(self.impl, "report").stdout
        self.assertNotIn("search_input.txt", report)
        self.assertNotIn("search_output.txt", report)
        self.assertNotIn("docs/api.md — まだ", report)
        self.assertIn("tests/test_app.py — 変えた", report)

    def test_test_plan_preserves_explicit_multiple_and_reference_targets(self) -> None:
        commit(self.impl, {"tests/test_a.py": "fixture\n", "tests/test_b.py": "fixture\n"}, "tests")
        commit(self.design, {"tests/test_ref.py": "fixture\n"}, "tests")
        self.write_plan(self.with_tests(PLAN_ALIGNED,
                        "- `tests/test_a.py`, tests/test_b.py, design:tests/test_ref.py — ケースを足す"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1 # changed\n")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        for rel in ("tests/test_a.py", "tests/test_b.py", "tests/test_ref.py"):
            self.assertIn(rel, r.stderr)

    def test_test_plan_requires_a_reason_after_unchanged(self) -> None:
        commit(self.impl, {"tests/test_app.py": "fixture\n"}, "tests")
        for body in ("- 変更不要", "- 変更不要:", "- 変更不要：   ",
                     "- tests/test_app.py — 変更不要", "- tests/test_app.py — 変更不要: 。",
                     "- 変更不要: `rg hello src/app.py > tests/search_output.txt`"):
            with self.subTest(body=body):
                self.write_plan(self.with_tests(PLAN_ALIGNED, body))
                r = self.run_pa(self.impl, "verify-plan")
                self.assertEqual(r.returncode, 1)
                self.assertIn("変更不要の後に理由がありません", r.stderr)
        # テストの自動測定を切っても、理由の欠けた判断は通さない。
        self.set_config(self.impl, tests=[])
        self.set_config(self.design, tests=[])
        self.write_plan(self.with_tests(PLAN_ALIGNED, "- 変更不要:"))
        self.assertIn("変更不要の後に理由がありません", self.run_pa(self.impl, "verify-plan").stderr)

    def test_tests_section_cannot_be_none_when_something_changes(self) -> None:
        self.write_plan(self.with_tests(PLAN_ALIGNED, "なし"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("## テストの変更案 が「なし」です", r.stderr)

    def test_apply_catches_tests_hit_by_names_changed_beyond_the_plan(self) -> None:
        commit(self.impl, {"tests/test_util.py": "from src.app import greet\n"}, "tests")
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        # 計画に無い名前（greet）まで足すと、それを使うテストが響く。
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n\n\ndef greet():\n"
                                              "    return 'hi'\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("変更が響くテストのうち、直していないファイルがあります", r.stderr)
        self.assertIn("tests/test_util.py", r.stderr)

    def test_tests_can_be_turned_off(self) -> None:
        path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["tests"] = []
        path.write_text(json.dumps(cfg), encoding="utf-8")
        # 参照先にも置いてあるので、参照先の tests も空にする。
        dpath = self.design / ".statemachine/codd/codd.json"
        dcfg = json.loads(dpath.read_text(encoding="utf-8"))
        dcfg["tests"] = []
        dpath.write_text(json.dumps(dcfg), encoding="utf-8")
        self.write_plan(self.with_tests(PLAN_ALIGNED, "なし"))
        self.assert_plan_ok()

    def test_files_changed_before_the_plan_are_not_counted(self) -> None:
        (self.impl / "src/wip.py").write_text("x = 1\n", encoding="utf-8")   # 計画より前からの作業中の変更
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    # ------------------------------------------------------------ 実装と設計書が同じリポジトリ

    def make_mono(self, **kw) -> Path:
        mono = self.tmp / "mono"
        mono.mkdir()
        git(mono, "init", "-q", "-b", "main")
        commit(mono, {"src/app.py": "def hello():\n    return 1\n",
                      "docs/api.md": "# API\n\n## hello\n\nhello は 1 を返す。\n"}, "init")
        init.init_repo(mono, "impl", ["docs=."], **kw)
        return mono

    def test_same_repo_needs_scopes(self) -> None:
        mono = self.make_mono()
        r = self.run_pa(mono, "show")
        self.assertEqual(r.returncode, 2)
        self.assertIn("同じリポジトリです", r.stderr)
        init.init_repo(mono, None, None, scope=["src"], ref_scopes=["docs=src/docs"])
        r = self.run_pa(mono, "show")
        self.assertEqual(r.returncode, 2)
        self.assertIn("scope が重なっています", r.stderr)

    def test_own_rules_are_found_only_near_the_scope(self) -> None:
        # 同じリポジトリのほかの道具の決まりは拾わない（自分の scope の中と、その上のフォルダだけ）。
        mono = self.make_mono(scope=["tools/app"], ref_scopes=["docs=docs"])
        commit(mono, {"CONTRIBUTING.md": "# 手引き\n", "tools/coding-rules.md": "# 共通の規約\n",
                      "tools/app/style-guide.md": "# 書き方\n", "tools/other/rules.md": "# 別の道具の決まり\n"},
               "rules")
        r = self.run_pa(mono, "rules")
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in ("CONTRIBUTING.md", "tools/coding-rules.md", "tools/app/style-guide.md"):
            self.assertIn(rel, r.stdout)
        self.assertNotIn("tools/other/rules.md", r.stdout)

    def test_same_repo_with_scopes(self) -> None:
        mono = self.make_mono(scope=["src"], ref_scopes=["docs=docs"])
        cfg = json.loads((mono / ".statemachine/codd/codd.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["scope"], ["src"])
        self.assertEqual(cfg["refs"], [{"name": "docs", "path": ".", "scope": ["docs"]}])
        commit(mono, {"docs/coding-rules.md": "# コーディングルール\n"}, "rules")
        r = self.run_pa(mono, "show")
        self.assertIn("- docs: 設計書", r.stdout)
        self.assertIn("  - docs:docs/coding-rules.md", r.stdout)   # 参照先の候補として挙がる（自分の分に重ねない）
        self.assertNotIn("  - docs/coding-rules.md", r.stdout)
        self.assertIn("受け持つフォルダ: docs", r.stdout)

        # 参照先を探すのは docs の中だけ（src/app.py の hello は拾わない）。
        r = self.run_pa(mono, "explore", "--term", "hello")
        self.assertIn("FOUND 1 files", r.stdout)
        # 根拠は参照先の scope の中、影響範囲は自分の scope の中だけを認める。
        plan = PLAN_DRIFT
        (mono / ".plans").mkdir(parents=True, exist_ok=True)
        (mono / PLAN).write_text(plan.replace("（根拠: docs/api.md）", "（根拠: src/app.py）"),
                                            encoding="utf-8")
        r = self.run_pa(mono, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("参照先に実在する根拠のパスがありません", r.stderr)
        (mono / PLAN).write_text(plan, encoding="utf-8")
        self.assert_plan_ok(mono)

        # 同じリポジトリの中でも、自分と参照先の変更を scope で分けて測る。
        (mono / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        r = self.run_pa(mono, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs を変えるはずなのに、docs が変わっていません", r.stderr)
        (mono / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n", encoding="utf-8")
        r = self.run_pa(mono, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own=changed refs=docs", r.stdout)

    def test_exclude_filters_each_side_within_scope(self) -> None:
        commit(self.impl, {"src/settings.json": "hello", "src/config/local.yaml": "hello",
                           "src/nested/settings.json": "hello", "src/keep.py": "hello"}, "settings")
        commit(self.design, {"docs/settings.json": "hello", "docs/config/local.yaml": "hello"}, "settings")
        # 設定を変える前に記録された検索結果も、その後の表示に残さない。
        self.run_pa(self.impl, "explore", "--term", "hello")
        self.set_config(self.impl, scope=["src"], exclude=["src/*.json", "src/config/"],
                        refs=[{"name": "design", "path": "../design", "scope": ["docs"],
                               "exclude": ["**/*.json", "docs/config"]}])
        r = self.run_pa(self.impl, "impact", "--term", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = (self.impl / ".codd/impact.md").read_text()
        self.assertNotIn("src/settings.json", report)
        self.assertNotIn("src/config/local.yaml", report)
        self.assertIn("src/nested/settings.json", report)  # * はフォルダをまたがない
        self.assertIn("src/keep.py", report)
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FOUND 1 files", r.stdout)
        self.assertNotIn("settings.json", r.stdout)
        self.assertNotIn("settings.json", (self.impl / ".codd/explore.md").read_text())
        self.assertIn("除外: src/*.json, src/config/", self.run_pa(self.impl, "show").stdout)

    def test_exclude_ignores_tracked_deleted_and_untracked_changes(self) -> None:
        commit(self.impl, {"src/settings.json": "hello"}, "settings")
        commit(self.design, {"docs/settings.json": "hello"}, "settings")
        self.set_config(self.impl, exclude=["**/*.json"],
                        refs=[{"name": "design", "path": "../design", "exclude": ["**/*.json"]}])
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/settings.json").unlink()
        (self.impl / "src/new.json").write_text("hello")
        (self.design / "docs/settings.json").write_text("changed hello")
        (self.impl / "src/app.py").write_text("def hello():\n    return 1 # changed\n")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own=changed refs=none", r.stdout)
        # 途中でコミットした場合の変更一覧にも適用する。
        git(self.impl, "add", "-A")
        git(self.impl, "commit", "-q", "-m", "apply")
        git(self.design, "add", "-A")
        git(self.design, "commit", "-q", "-m", "settings")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_excluded_files_cannot_be_planned_or_cited(self) -> None:
        self.set_config(self.impl, exclude=["src/app.py"])
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自分の変更案の項目に", r.stderr)
        self.set_config(self.impl, exclude=[],
                        refs=[{"name": "design", "path": "../design", "exclude": ["docs/api.md"]}])
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("根拠のパスがありません", r.stderr)

    def test_exclude_filters_graphify_output_and_test_files(self) -> None:
        self.use_graphify_stub()
        commit(self.impl, {"src/use.py": "hello", "tests/config.test.py": "hello",
                           "tests/app.test.py": "hello"}, "tests")
        self.set_config(self.impl, exclude=["src/use.py", "**/config.test.py"],
                        refs=[{"name": "design", "path": "../design", "exclude": ["docs/api.md"]}])
        self.assertIn("自分 1 files", self.run_pa(self.impl, "show").stdout)
        self.run_pa(self.impl, "impact", "--term", "hello")
        self.assertNotIn("src/use.py:L4", (self.impl / ".codd/impact.md").read_text())
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertIn("FOUND 0 files", r.stdout)
        self.assertNotIn("src=docs/api.md", (self.impl / ".codd/explore.md").read_text())

    def test_exclude_does_not_hide_rules(self) -> None:
        commit(self.impl, {"AGENTS.md": "# Rules\n", "docs/coding-rules.md": "# Rules\n"}, "rules")
        commit(self.design, {"AGENTS.md": "# Rules\n", "docs/design-rules.md": "# Rules\n"}, "rules")
        self.set_config(self.impl, exclude=["**/*.md"], rules=["docs/coding-rules.md"],
                        refs=[{"name": "design", "path": "../design", "exclude": ["**/*.md"]}])
        r = self.run_pa(self.impl, "rule", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# 守る決まり AGENTS.md", r.stdout)
        self.assertIn("# 守る決まり design:AGENTS.md", r.stdout)
        self.assertIn("# 守る決まり docs/coding-rules.md", r.stdout)
        self.assertIn("design:docs/design-rules.md", self.run_pa(self.impl, "rules").stdout)

    def test_exclude_globs_match_root_nested_and_character_classes(self) -> None:
        files = {p: "hello" for p in ("settings.json", "src/settings.json", "src/config1.yaml",
                                      "src/configA.yaml", "src/x.ini", "src/xx.ini")}
        commit(self.impl, files, "settings")
        self.set_config(self.impl, exclude=["**/*.json", "src/config[0-9].yaml", "**/?.ini"])
        r = self.run_pa(self.impl, "impact", "--term", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = (self.impl / ".codd/impact.md").read_text()
        for p in ("settings.json", "src/settings.json", "src/config1.yaml", "src/x.ini"):
            self.assertNotIn("- " + p, report)
        self.assertIn("- src/configA.yaml", report)
        self.assertIn("- src/xx.ini", report)

    def test_exclude_validation(self) -> None:
        for value in ("*.json", [1], [""], ["../secret"], ["/tmp/config"], ["C:/config"], ["!keep.py"]):
            for ref in (False, True):
                with self.subTest(value=value, ref=ref):
                    self.set_config(self.impl, exclude=[] if ref else value,
                                    refs=[{"name": "design", "path": "../design", **({"exclude": value} if ref else {})}])
                    r = self.run_pa(self.impl, "show")
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn("exclude", r.stderr)

    def test_init_exclude_options_replace_clear_and_preserve(self) -> None:
        r = subprocess.run([sys.executable, str(TOOL / "init.py"), str(self.impl),
                            "--exclude", "**/*.json", "--exclude", ".github/",
                            "--ref-exclude", "design=**/*.yaml", "--ref-exclude", "design=docs/config/"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text())
        self.assertEqual(cfg["exclude"], ["**/*.json", ".github/"])
        self.assertEqual(cfg["refs"][0]["exclude"], ["**/*.yaml", "docs/config/"])
        init.init_repo(self.impl, None, ["design=../design"])
        self.assertEqual(json.loads(path.read_text())["refs"][0]["exclude"], cfg["refs"][0]["exclude"])
        init.init_repo(self.impl, None, None, exclude=[""], ref_excludes=["design="])
        cfg = json.loads(path.read_text())
        self.assertEqual(cfg["exclude"], [])
        self.assertEqual(cfg["refs"][0]["exclude"], [])

    def test_unknown_config_keys_are_reported(self) -> None:
        path = self.impl / ".statemachine/codd/codd.json"
        for patch, word in (({"refz": []}, "refz"), ({"refs": [{"path": "../design", "scopes": ["docs"]}]}, "scopes"),
                            ({"max_files": 0}, "max_files")):
            with self.subTest(word=word):
                cfg = {"side": "impl", "refs": [{"path": "../design"}], **patch}
                path.write_text(json.dumps(cfg), encoding="utf-8")
                r = self.run_pa(self.impl, "show")
                self.assertEqual(r.returncode, 2)
                self.assertIn(word, r.stderr)

    # ------------------------------------------------------------ 決まり・スキル・道具を確かに使う

    def set_config(self, repo: Path, **values) -> None:
        path = repo / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg.update(values)
        path.write_text(json.dumps(cfg), encoding="utf-8")

    def test_plan_must_name_every_rule_file(self) -> None:
        commit(self.impl, {"CLAUDE.md": "# 約束\n", "docs/style.md": "# 書き方\n"}, "rules")
        commit(self.design, {"CLAUDE.md": "# 設計書の約束\n"}, "rules")
        self.set_config(self.impl, rules=["docs/style.md"])
        r = self.run_pa(self.impl, "show")
        self.assertIn("  - CLAUDE.md\n  - docs/style.md\n  - design:CLAUDE.md", r.stdout)
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("守る決まりに、決まりのファイルを読んで挙げてください", r.stderr)
        # 自分にも同じ名前があるので、参照先の決まりは名前付きで挙げる。
        self.write_plan(PLAN_ALIGNED.replace("## 守る決まり\n\nなし",
                                             "## 守る決まり\n\n- CLAUDE.md — テストを通す\n- docs/style.md — 敬体"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("design:CLAUDE.md", r.stderr)
        self.assertNotIn("docs/style.md", r.stderr)
        self.write_plan(PLAN_ALIGNED.replace("## 守る決まり\n\nなし",
                                             "## 守る決まり\n\n- CLAUDE.md — テストを通す\n- docs/style.md — 敬体\n"
                                             "- design:CLAUDE.md — 用語をそろえる"))
        self.assert_plan_ok()

    def test_rule_files_must_be_read_through_the_machine(self) -> None:
        commit(self.impl, {"CLAUDE.md": "# 約束\n\nテストを通す。\n"}, "rules")
        plan = PLAN_ALIGNED.replace("## 守る決まり\n\nなし", "## 守る決まり\n\n- CLAUDE.md — テストを通す")
        self.write_plan(plan, read=False)
        self.run_pa(self.impl, "explore", "--term", "hello")
        r = self.run_pa(self.impl, "verify-plan")   # 挙げただけで、読み込んでいない
        self.assertEqual(r.returncode, 1)
        self.assertIn("守る決まりのファイルを読み込んでいません", r.stderr)
        r = self.run_pa(self.impl, "rule", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# 守る決まり CLAUDE.md", r.stdout)
        self.assertIn("テストを通す。", r.stdout)
        # 練り直しで呼び直しても、読み込み済みで変わっていないものは出し直さない（--again で出す）。
        r = self.run_pa(self.impl, "rule", "--all")
        self.assertIn("CLAUDE.md（この回で読み込み済み・変わっていない", r.stdout)
        self.assertNotIn("テストを通す。", r.stdout)
        self.assertIn("テストを通す。", self.run_pa(self.impl, "rule", "--all", "--again").stdout)
        self.assert_plan_ok()
        # 読み込んだあとに決まりが変わったら、読み直させる。
        (self.impl / "CLAUDE.md").write_text("# 約束\n\n型を付ける。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("読み直す）: CLAUDE.md", r.stderr)
        self.assertEqual(self.run_pa(self.impl, "rule", "nothing.md").returncode, 1)

    def test_plan_needs_exploring_and_handling_what_was_found(self) -> None:
        commit(self.design, {"docs/guide.md": "# 使い方\n\nログは標準出力に出す。\n"}, "guide")
        self.write_plan(PLAN_ALIGNED, read=False)
        self.run_pa(self.impl, "rule", "--all")
        r = self.run_pa(self.impl, "verify-plan")   # 探さずに「なし」で済ませる計画は通さない
        self.assertEqual(r.returncode, 1)
        self.assertIn("参照先を探していません: design", r.stderr)
        self.run_pa(self.impl, "explore", "--term", "hello", "--term", "ログ")
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        self.assertIn("docs/guide.md", r.stderr)
        self.assertNotIn("docs/api.md,", r.stderr)   # 根拠に挙げたものは扱った
        self.write_plan(PLAN_ALIGNED.replace("## 参照先のその他\n\nなし",
                                             "## 参照先のその他\n\n- ログは標準出力に出す（根拠: docs/guide.md）"),
                        read=False)
        self.assert_plan_ok()
        # 終わりの報告で、探した・読んだ記録は消える（次の回に持ち越さない）。
        (self.impl / "src/app.py").write_text("def hello():\n    print('log')\n    return 1\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        self.run_pa(self.impl, "report")
        self.assertFalse((self.impl / ".codd/explore.json").exists())
        self.assertFalse((self.impl / ".codd/rules-read.json").exists())

    def test_unrelated_found_files_can_be_waived_in_one_line(self) -> None:
        # 見つかったファイルは一致した行で判断し、関係しないものは 1 行にまとめて扱える（全文を読ませない）。
        commit(self.design, {"docs/a.md": "# A\n\nhello の綴りの話。\n", "docs/b.md": "# B\n\nhello world の例。\n"},
               "more")
        self.write_plan(PLAN_ALIGNED.replace(
            "## 参照先のその他\n\nなし",
            "## 参照先のその他\n\n- 関係なし: docs/a.md:3, docs/b.md:3 — 綴りと例の話で、hello の振る舞いには触れない"),
            read=False)
        self.read_up(self.impl, "hello")
        self.assert_plan_ok()
        plan = (self.impl / PLAN).read_text(encoding="utf-8")
        (self.impl / PLAN).write_text(plan.replace(", docs/b.md:3", ""), encoding="utf-8")
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/b.md", r.stderr)

    def test_plan_and_apply_must_use_configured_skills_and_tools(self) -> None:
        self.set_config(self.impl, skills={"plan": ["domain-modeler"], "apply": ["tdd"]},
                        tools={"plan": ["github"], "apply": []})
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("`domain-modeler`, `github`", r.stderr)
        used = PLAN_ALIGNED.replace("## 使ったスキルと道具\n\nなし",
                                    "## 使ったスキルと道具\n\n- `domain-modeler` — 用語を確かめた\n- `github` — 関連 PR を見た")
        self.write_plan(used)
        self.assert_plan_ok()
        self.assertIn("道具: `github`", self.run_pa(self.impl, "show").stdout)

        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn(".codd/apply.md に、変えるときに使ったスキル・道具", r.stderr)
        (self.impl / ".codd/apply.md").write_text("- 先にテストを書いた\n", encoding="utf-8")
        self.assertIn("`tdd`", self.run_pa(self.impl, "verify-apply").stderr)
        (self.impl / ".codd/apply.md").write_text("- `tdd` — 先にテストを書いた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    # ------------------------------------------------------------ 最後までやり切る

    def test_verify_apply_needs_every_planned_file(self) -> None:
        self.write_plan(PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                             "- src/app.py — `hello` の中でログを出す\n- src/log.py — `hello` から使うログを新しく書く"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自分の変更案のファイルをまだ変えていません", r.stderr)
        self.assertIn("src/log.py", r.stderr)
        (self.impl / "src/log.py").write_text("def log(m):\n    print(m)\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_verify_apply_catches_callers_of_a_renamed_function(self) -> None:
        # 計画より広く（名前まで）変えたら、その呼び出し元を直したかを実際の差分から測る。
        self.add_caller()
        commit(self.impl, {"src/greet_user.py": "from app import greet\n"}, "another caller")
        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/use.py — 変更不要: 戻り値は同じ"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def greet():\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("直していないファイル", r.stderr)
        self.assertIn("src/greet_user.py", r.stderr)

    def test_unchanged_quoted_words_on_edited_lines_are_not_measured(self) -> None:
        # 直した行に元からある `…` は変わった名前ではない。拾うと、無関係なファイルを「直していない」として必ず落とす。
        commit(self.impl, {"docs/use.md": "# use\n\nrun `make build` then `hello`.\n",
                           "src/other.py": "# make build\n", "src/renamed.py": "# old-flag\n"}, "docs")
        self.write_plan(PLAN_ALIGNED.replace(
            "- src/app.py — `hello` の中でログを出す",
            "- src/app.py — `hello` の中でログを出す\n- docs/use.md — `hello` の説明を足す"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('x')\n    return 1\n", encoding="utf-8")
        (self.impl / "docs/use.md").write_text("# use\n\nrun `make build` then `hello` (logs).\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        # 足した・消した `…` は今までどおり測る。
        (self.impl / "docs/use.md").write_text("# use\n\nrun `make build` then `hello` with `old-flag`.\n",
                                               encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("src/renamed.py", r.stderr)
        self.assertNotIn("src/other.py", r.stderr)

    def test_plan_compacts_waivers_without_losing_verification(self) -> None:
        commit(self.impl, {"src/a.py": "hello()\n", "src/b.py": "hello()\n",
                           "tests/test_a.py": "hello()\n", "tests/test_b.py": "hello()\n"}, "callers")
        self.set_config(self.impl, max_files=1)
        plan = PLAN_ALIGNED.replace("## 影響範囲\n\nなし",
                                   "## 影響範囲\n\n- src/a.py — 変更不要: 契約は同じ\n"
                                   "- src/b.py — 変更不要: 契約は同じ")
        plan = plan.replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）",
                            "- tests/test_a.py — 変更不要: 期待値は同じ\n"
                            "- tests/test_b.py — 変更不要: 期待値は同じ")
        self.write_plan("<!-- 説明 -->\n" + plan)
        self.assert_plan_ok()
        compact = (self.impl / PLAN).read_text()
        self.assertNotIn("<!--", compact)
        self.assertNotIn("変更不要", compact)
        self.assertNotIn("src/a.py", compact)
        self.assertNotIn("tests/test_a.py", compact)
        self.assert_plan_ok()  # 繰り返し検査しても形が変わらない
        self.assertEqual((self.impl / PLAN).read_text(), compact)
        (self.impl / "src/app.py").write_text("def hello():\n    return 1 # changed\n")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_pa(self.impl, "report")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("変更不要", r.stdout)
        self.assertNotIn("src/a.py", r.stdout)
        self.assertNotIn("tests/test_a.py", r.stdout)
        self.assertEqual(self.run_pa(self.impl, "record").returncode, 0)
        self.assertNotIn("変更不要", (self.impl / PLAN).read_text())

    def test_replanning_keeps_waivers_hidden_from_the_plan(self) -> None:
        # 通ったときに計画から省いた変更不要の判断は、確認で NG になって計画を直しても失わない
        # （失うと、判断済みのファイルがまた未判断になり、テストの変更案が「なし」で必ず落ちる）。
        commit(self.impl, {"src/a.py": "hello()\n"}, "callers")
        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/a.py — 変更不要: 契約は同じ"))
        self.assert_plan_ok()
        self.assertNotIn("変更不要", (self.impl / PLAN).read_text())
        self.run_pa(self.impl, "decide", "NG", "--note", "info で出して")
        plan = self.impl / PLAN
        plan.write_text(plan.read_text().replace("hello にログを足す。", "hello にログを info で足す。"))
        self.assert_plan_ok()
        saved = json.loads((self.impl / ".codd/plan-check.json").read_text())["text"]
        self.assertIn("- src/a.py — 変更不要: 契約は同じ", saved)
        self.assertIn("- 変更不要: このリポジトリにテストはまだ無い", saved)
        self.assertIn("info で足す", saved)

    def test_plan_keeps_distinct_waiver_reasons(self) -> None:
        commit(self.impl, {"src/a.py": "hello()\n", "src/b.py": "hello()\n"}, "callers")
        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし",
                       "## 影響範囲\n\n- src/a.py — 変更不要: 契約は同じ\n"
                       "- src/b.py — 変更不要: 表示だけ"))
        self.assert_plan_ok()
        text = (self.impl / PLAN).read_text()
        self.assertNotIn("変更不要", text)
        saved = json.loads((self.impl / ".codd/plan-check.json").read_text())["text"]
        self.assertIn("- src/a.py — 変更不要: 契約は同じ", saved)
        self.assertIn("- src/b.py — 変更不要: 表示だけ", saved)
        self.assert_plan_ok()

    def test_hidden_plan_judgments_cannot_change_after_approval(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        saved = self.impl / ".codd/plan-check.json"
        value = json.loads(saved.read_text())
        value["text"] = value["text"].replace("このリポジトリにテストはまだ無い", "理由を書き換えた")
        saved.write_text(json.dumps(value))
        (self.impl / "src/app.py").write_text("def hello():\n    return 1 # changed\n")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("利用者が確かめたものではありません", r.stderr)

    def test_legacy_plan_records_are_excluded_and_not_read(self) -> None:
        commit(self.impl, {"src/app.py": "def hello():\n    return 1 # hello\n"}, "outside codd")
        for folder in ("docs/.plans", "docs/.plan"):
            old = folder + "/2026-10-01-0000-old.md"
            commit(self.impl, {old: "# hello\n\n- src/app.py\n\n## 結果\n"}, "old record")
        r = self.run_pa(self.impl, "lint", "--no-test")
        self.assertIn("codd を通らずに入った変更（", r.stdout)
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.run_pa(self.design, "explore", "--term", "hello")
        self.assertNotIn("docs/.plan", (self.design / ".codd/explore.md").read_text())
        (self.impl / "docs/.plans/2026-10-01-0000-old.md").write_text("changed")
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_plan_is_kept_as_a_decision_record(self) -> None:
        # 計画は .plans/日時-名前.md に書き、終わりに確認の答えと結果を書き足して、その名前のまま残す。
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        self.run_pa(self.impl, "decide", "NG", "--note", "戻り値の説明も直して")
        self.assert_plan_ok()   # 同じ回の練り直しでは同じ計画を直す
        self.run_pa(self.impl, "decide", "OK")
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n", encoding="utf-8")
        # 計画のフォルダは変えたファイルに数えない（記録のために置くもの）。
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        self.assertEqual(self.run_pa(self.impl, "report").returncode, 0)
        # 記録に要らないもの（使わなかったスキル・関係なしとしたファイル・中身が「なし」の見出し）は残さない。
        plan = self.impl / PLAN
        plan.write_text(plan.read_text(encoding="utf-8").replace(
            "## 使ったスキルと道具\n\nなし",
            "## 使ったスキルと道具\n\n- `tdd-lite` — 使わない: テストは無い\n- `grep` — 呼び出し元を探した").replace(
            "## 参照先のその他\n\n- なし", "## 参照先のその他\n\n- 関係なし: docs/api.md:1 — 題名だけ\n  続きの説明"),
            encoding="utf-8")
        r = self.run_pa(self.impl, "record")
        self.assertEqual(r.returncode, 0, r.stderr)
        records = list((self.impl / ".plans").glob("*.md"))
        self.assertEqual(records, [plan])     # 置いたときの名前のまま残す
        self.assertIn(f"計画の記録: {PLAN}", r.stdout)
        text = plan.read_text(encoding="utf-8")
        self.assertIn("## 自分の変更案", text)
        self.assertIn("- `grep` — 呼び出し元を探した", text)
        self.assertNotIn("使わない", text)
        self.assertNotIn("関係なし", text)
        self.assertNotIn("続きの説明", text)
        self.assertNotIn("## 参照先のその他", text)          # 中身が無くなった見出しは消す
        self.assertNotIn("<!--", text)
        self.assertRegex(text, r"## 確認と判断\n\n- [-0-9: ]+ NG（計画を直す）: 戻り値の説明も直して\n- [-0-9: ]+ OK（計画で進める）")
        self.assertIn("## 結果\n\n- 変えたあとの検査: 通った", text)
        self.assertIn("### 自分（実装）", text)
        self.assertFalse((self.impl / ".codd/decisions.json").exists())
        self.assertEqual(self.run_pa(self.impl, "record").returncode, 1)   # 記録は 1 回だけ
        # 終わった回の記録は書き換えさせない（次の回の検査で落とす）。
        git(self.impl, "add", "-A")
        git(self.impl, "commit", "-q", "-m", "change")
        plan.write_text(text.replace("通った", "通らなかった"), encoding="utf-8")
        (self.impl / ".plans/2026-10-02-0000-next.md").write_text(PLAN_ALIGNED, encoding="utf-8")
        self.read_up(self.impl)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("終わった回の計画の記録を書き換えています", r.stderr)

    def test_record_when_stopped_before_changing(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.run_pa(self.impl, "decide", "STOP", "--note", "今回はやめる")
        r = self.run_pa(self.impl, "record")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = next((self.impl / ".plans").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("STOP（やめる）: 今回はやめる", text)
        self.assertIn("## 結果\n\n- 変えていない", text)

    def test_report_summarises_the_result(self) -> None:
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        self.assertEqual(self.run_pa(self.impl, "report").returncode, 1)  # まだ変えたあとの検査を通っていない
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        r = self.run_pa(self.impl, "report")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("- 変えたあとの検査: 通った", r.stdout)
        self.assertIn("- src/app.py — 変えた", r.stdout)
        self.assertIn("## design（設計書）", r.stdout)
        self.assertIn("- docs/api.md — 変えた", r.stdout)
        self.assertIn("- `hello` の引数を足す — 別の回に回す", r.stdout)
        self.assertTrue((self.impl / ".codd/report.md").is_file())
        (self.impl / "src/app.py").write_text("def hello():\n    return 3\n", encoding="utf-8")
        r = self.run_pa(self.impl, "report")
        self.assertEqual(r.returncode, 1)
        self.assertIn("通ったあとに、さらに変わっている", r.stdout)

    def test_rules_in_the_ref_can_be_configured(self) -> None:
        commit(self.design, {"docs/coding-rules.md": "# コーディングルール\n\n関数名は動詞で始める。\n"}, "rules")
        self.set_config(self.impl, refs=[{"name": "design", "path": "../design", "rules": ["docs/coding-rules.md"]}])
        self.assertIn("  - design:docs/coding-rules.md", self.run_pa(self.impl, "show").stdout)
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/coding-rules.md", r.stderr)
        self.write_plan(PLAN_ALIGNED.replace("## 守る決まり\n\nなし",
                                             "## 守る決まり\n\n- docs/coding-rules.md — `hello` は動詞で始まる"))
        self.assert_plan_ok()

    def test_rules_are_discovered_and_written(self) -> None:
        commit(self.design, {"docs/coding-rules.md": "# コーディングルール\n",
                             "docs/conventions/naming.md": "# 名前\n",
                             "docs/guide.md": "# コーディング規約\n\n本文\n",
                             "CHANGELOG.md": "# rules の変更履歴\n",
                             # スキルの中の決まり（そのスキルを使うときに読むもの）は候補にしない。
                             ".agents/skills/react/SKILL.md": "---\nname: react\n---\n# React のルール\n",
                             ".agents/skills/react/rules/naming.md": "# 名前のルール\n",
                             # 日付で始まる計画や記録は、語が当たっても決まりではない。
                             "docs/plans/2026-08-15-execution-policy-design.md": "# 実行ポリシー\n"}, "rules")
        r = self.run_pa(self.impl, "rules")
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in ("docs/coding-rules.md", "docs/conventions/naming.md", "docs/guide.md"):
            self.assertIn(f"  - design:{rel}", r.stdout)
        self.assertNotIn("docs/api.md", r.stdout)
        self.assertNotIn("CHANGELOG.md", r.stdout)
        self.assertNotIn(".agents/skills", r.stdout)
        self.assertNotIn("execution-policy", r.stdout)
        self.assertIn("決まりの候補（設定に無い", self.run_pa(self.impl, "show").stdout)
        # 絞って書ける。書いたものは決まりになり、候補からは消える。
        r = self.run_pa(self.impl, "rules", "--write", "--only", "design:docs/coding-rules.md")
        self.assertIn("1 件を codd.json に書きました", r.stdout)
        cfg = json.loads((self.impl / ".statemachine/codd/codd.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["refs"], [{"path": "../design", "name": "design", "rules": ["docs/coding-rules.md"]}])
        r = self.run_pa(self.impl, "rules")
        self.assertIn("守る決まり:\n  - design:docs/coding-rules.md", r.stdout)
        self.assertNotIn("  - design:docs/coding-rules.md\n  - design:docs/conventions", r.stdout)

    def test_install_discovers_rules(self) -> None:
        commit(self.design, {"docs/coding-rules.md": "# コーディングルール\n"}, "rules")
        app = self.tmp / "app"
        app.mkdir()
        git(app, "init", "-q", "-b", "main")
        init.init_repo(app, "impl", ["../design"])
        path = app / ".statemachine/codd/codd.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["refs"][0]["rules"], ["docs/coding-rules.md"])
        # 既にある codd.json には探し直して書き足さない（手で消した決まりが戻らない）。
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["refs"][0]["rules"] = []
        path.write_text(json.dumps(cfg), encoding="utf-8")
        init.init_repo(app, None, None)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["refs"][0]["rules"], [])

    def test_rules_accept_globs(self) -> None:
        commit(self.design, {"docs/rules/coding.md": "# a\n", "docs/rules/naming/api.md": "# b\n",
                             "docs/rules/notes.txt": "x\n", "docs/api-rules.md": "# c\n"}, "rules")
        self.set_config(self.impl, refs=[{"name": "design", "path": "../design",
                                          "rules": ["docs/rules/**/*.md", "docs/*-rules.md", "docs/gone/*.md"]}])
        r = self.run_pa(self.impl, "rules")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("守る決まり:\n  - design:docs/rules/coding.md\n  - design:docs/rules/naming/api.md\n"
                      "  - design:docs/api-rules.md\n", r.stdout)
        self.assertNotIn("notes.txt", r.stdout)
        self.assertIn("! design:docs/gone/*.md に当たるファイルがありません", r.stdout)
        self.assertNotIn("候補:\n  - design:docs/rules", r.stdout)  # glob で当たったものは候補に出さない
        self.write_plan(PLAN_ALIGNED.replace("## 守る決まり\n\nなし",
                                             "## 守る決まり\n\n- docs/rules/coding.md — 動詞で始める"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/rules/naming/api.md", r.stderr)
        self.set_config(self.impl, rules=["../outside.md"])
        self.assertEqual(self.run_pa(self.impl, "show").returncode, 2)

    def test_scoped_long_folder_still_finds_tests_and_skips_machine_files(self) -> None:
        # git 2.43 などでは、scope の最初のフォルダ名が .codd より長いと `:(exclude).codd` を付けた ls-files が
        # 何も返さない。除外は自前で行い、テストを取りこぼさない。マシンのファイル（計画など）は数えない。
        commit(self.impl, {"services/api/handler.py": "def hello():\n    return 1\n",
                           "services/api/test_handler.py": "from handler import hello\n"}, "service")
        self.set_config(self.impl, scope=["services"])
        self.assertIn("自分 1 files", self.run_pa(self.impl, "show").stdout)
        (self.impl / ".plans").mkdir(parents=True, exist_ok=True)
        (self.impl / PLAN).write_text("# 下書き\n", encoding="utf-8")
        self.set_config(self.impl, scope=[])
        r = self.run_pa(self.impl, "explore", "--term", "下書き")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(".plans", (self.impl / ".codd/explore.md").read_text(encoding="utf-8"))

    def test_graph_is_not_rebuilt_when_only_the_plan_changes(self) -> None:
        # 練り直しで計画を書き直すたびに、グラフを作り直さない（作り直しは長くかかる）。
        self.use_graphify_stub()
        self.assertIn("graphify: updated", self.run_pa(self.impl, "explore", "--term", "hello").stdout)
        (self.impl / ".plans").mkdir(parents=True, exist_ok=True)
        (self.impl / PLAN).write_text("# 計画\n", encoding="utf-8")
        self.set_config(self.design, graphify="auto")
        r = self.run_pa(self.design, "explore", "--term", "hello")   # 設計書の側から自分（impl）を引く
        self.assertIn("graphify: updated", r.stdout)
        (self.impl / PLAN).write_text("# 計画を書き直した\n", encoding="utf-8")
        self.assertIn("graphify: fresh", self.run_pa(self.design, "explore", "--term", "hello").stdout)
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        self.assertIn("graphify: updated", self.run_pa(self.design, "explore", "--term", "hello").stdout)

    def test_verify_plan_reports_everything_at_once_and_briefly(self) -> None:
        # 形の指摘があっても測り、1 回で出し切る。出す長さは実行ハーネスがやり直しに渡す 2000 文字に収める。
        self.add_caller()
        commit(self.design, {f"docs/n{i}.md": f"# n{i}\n\n`hello` の話 {i}\n" for i in range(30)}, "many")
        self.write_plan(PLAN_ALIGNED.replace("## ずれ\n\nなし", "## ずれ\n\n根拠の無いずれ"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        first = r.stderr.splitlines()[0]
        self.assertRegex(first, r"^NG 計画の検査: \d+ 件（全文: \.codd/problems\.json）$")
        self.assertIn("ずれ は箇条書きにしてください", r.stderr)   # 形の指摘と
        self.assertIn("「未判断」として書き足しました", r.stderr)   # 測った結果を同じ回に
        self.assertLessEqual(len(r.stderr), 2000)
        texts = [p["text"] for p in json.loads((self.impl / ".codd/problems.json").read_text(encoding="utf-8"))["problems"]]
        named = lambda text: len(re.findall(r"docs/n\d+\.md", text))
        self.assertGreater(sum(named(t) for t in texts), named(r.stderr))   # 全文は控えに残る
        plan = (self.impl / PLAN).read_text(encoding="utf-8")
        self.assertIn("- src/use.py — 未判断", plan)
        self.assertIn("- 未判断: docs/n0.md:3", plan)

    # ------------------------------------------------------------ 点検（本流とは別）

    def test_lint_finds_what_the_main_flow_cannot_see(self) -> None:
        r = self.run_pa(self.impl, "lint", "--no-test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)   # 置いたあとに何も変わっていない
        self.assertTrue(r.stdout.startswith("OK 点検"))

        commit(self.impl, {"src/app.py": "def hello():\n    return 3\n"}, "codd を通さずに変えた")
        commit(self.design, {
            "docs/guide.md": "# 使い方\n\n[呼ぶもの](../src/old.py)\n\n書き方: `[説明](../src/none.py)`\n\n"
                             "```\n<!-- evidence: x -->1<!-- /evidence -->\n```\n",
            "docs/specs/a.md": "# A\n\n## 目的\n\n## 振る舞い\n",
            "docs/specs/b.md": "# B\n\n## 目的\n\n## 振る舞い\n",
            "docs/specs/c.md": "# C\n\n## 目的\n",
        }, "設計書を足した")
        before = git(self.impl, "status", "--porcelain") + git(self.design, "status", "--porcelain")
        r = self.run_pa(self.impl, "lint", "--no-test")
        self.assertEqual(r.returncode, 1, r.stderr)
        text = (self.impl / ".codd/lint.md").read_text(encoding="utf-8")
        self.assertIn("docs/guide.md:3 → ../src/old.py", text)                # 壊れたパス
        self.assertNotIn("none.py", text)                                     # `…` の中のリンクは書き方の例
        self.assertIn("## テストの結果を写した文書\n\n- なし", text)            # コードブロックの中の印も例
        self.assertIn("docs/specs/c.md — 欠けている見出し: 振る舞い", text)  # 書式
        self.assertNotIn("docs/specs/a.md —", text.split("## 文書の書式")[1].split("##")[0])
        self.assertIn("src/app.py", text.split("## codd を通らなかった変更")[1])   # 記録の無い変更
        self.assertIn("## 本流に渡すやりたいこと", text)
        self.assertIn("codd を通らずに入った変更", r.stdout)
        self.assertEqual(r.stdout.count("codd を通らずに入った変更"), 2)   # コミットごとに 1 行
        # 点検は何も直さない（書くのは作業フォルダだけ）
        self.assertEqual(before, git(self.impl, "status", "--porcelain") + git(self.design, "status", "--porcelain"))

        commit(self.impl, {".plans/2026-10-01-0000-x.md": "## 自分の変更案\n\n- src/app.py — 戻り値\n\n## 結果\n"}, "記録")
        text = (self.run_pa(self.impl, "lint", "--no-test"), (self.impl / ".codd/lint.md").read_text(encoding="utf-8"))[1]
        self.assertNotIn("src/app.py", text.split("## codd を通らなかった変更")[1])  # 記録に出てくれば通った変更

    def test_lint_runs_the_tests_of_both_sides(self) -> None:
        config = self.impl / ".statemachine/codd/codd.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        data["test"] = [sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"]
        config.write_text(json.dumps(data), encoding="utf-8")
        r = self.run_pa(self.impl, "lint")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自分のテスト", r.stdout)
        self.assertIn("boom", (self.impl / ".codd/lint.md").read_text(encoding="utf-8"))

    def test_workflow_gives_checks_enough_time(self) -> None:
        # 実行ハーネスの既定（120 秒）では、グラフの作り直しやテストで切られて「落ちた」扱いになる。
        text = (TOOL / "machine/workflow.yaml").read_text(encoding="utf-8")
        found = dict(re.findall(r"args: \[\.statemachine/codd/codd\.py, (verify-\w+)\], timeout_sec: (\d+)", text))
        self.assertGreaterEqual(int(found["verify-plan"]), 900)
        self.assertGreaterEqual(int(found["verify-apply"]), 1800)

    def test_workflow_lets_the_user_stop_wherever_it_asks(self) -> None:
        # 利用者に訊く段（計画の確認・止まったときの相談）では、どちらでもやめられる。
        text = (TOOL / "machine/workflow.yaml").read_text(encoding="utf-8")
        self.assertIn('{from: confirm, to: stopped, condition_rule: "startswith:answer:STOP"', text)
        self.assertIn('{from: stuck, to: stopped, condition_rule: "startswith:choice:STOP"', text)

    # ------------------------------------------------------------ 意味のずれのサンプル（samples/drift）

    def drift(self, mode: str) -> subprocess.CompletedProcess:
        dest = self.impl / ".statemachine/codd-drift"
        if not dest.is_dir():
            shutil.copytree(TOOL / "samples/drift", dest)
        return subprocess.run([sys.executable, ".statemachine/codd-drift/check_drift.py", mode], cwd=self.impl,
                              capture_output=True, text=True, env={**os.environ, **GIT_ENV})

    def test_drift_sample_checks_the_shape_of_what_the_model_wrote(self) -> None:
        data = self.impl / ".codd"
        data.mkdir(exist_ok=True)
        (data / "drift-candidates.md").write_text("なし\n", encoding="utf-8")
        self.assertEqual(self.drift("candidates").returncode, 0)
        self.assertIn("比べる組はありませんでした", self.drift("report").stdout)

        (data / "drift-candidates.md").write_text(
            "- src/app.py:2 ⇔ design:docs/api.md:5 — hello の戻り値\n"
            "- src/app.py:9 ⇔ design:docs/none.md:1 — 無い\n", encoding="utf-8")
        r = self.drift("candidates")
        self.assertEqual(r.returncode, 1)
        self.assertIn("src/app.py:9 — 2 行までのファイルです", r.stderr)   # 指す行が実在するか
        self.assertIn("design:docs/none.md:1 — ファイルがありません", r.stderr)

        (data / "drift-candidates.md").write_text("- src/app.py:2 ⇔ design:docs/api.md:5 — hello の戻り値\n",
                                                  encoding="utf-8")
        self.assertEqual(self.drift("candidates").returncode, 0, self.drift("candidates").stderr)
        (data / "drift.md").write_text("- ずれ: src/app.py:2 ⇔ design:docs/api.md:5 — 実装は 1、設計書も 1\n",
                                       encoding="utf-8")
        r = self.drift("judged")
        self.assertEqual(r.returncode, 1)
        self.assertIn("やりたいこと", r.stderr)            # ずれには本流に渡す 1 行を添える
        (data / "drift.md").write_text("- ずれ: src/app.py:2 ⇔ design:docs/api.md:5 — 値が違う\n"
                                       "  - やりたいこと: hello の戻り値を合わせたい\n", encoding="utf-8")
        self.assertEqual(self.drift("judged").returncode, 0)
        out = self.drift("report").stdout
        self.assertIn("ずれ 1、合っている 0、判断できない 0", out)
        self.assertIn("  - やりたいこと: hello の戻り値を合わせたい", out)

    def test_drift_sample_passes_engine_validation(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML がありません")
        runner = REPO / ".github/skills/statemachine-use/scripts/run_machine.py"
        r = subprocess.run([sys.executable, str(runner), str(TOOL / "samples/drift/workflow.yaml"), "--dry-run"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

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
