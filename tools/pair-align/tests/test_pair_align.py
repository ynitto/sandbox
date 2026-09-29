"""pair-align の結合テスト。実装・設計書の 2 リポジトリを一時フォルダに作り、往復を通す。

LLM は呼ばない。アクションがやる判断・依頼文の執筆は、テストが代わりにファイルを書いて進める。
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


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout


def commit(repo: Path, files: dict[str, str], message: str) -> None:
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


class PairAlignTest(unittest.TestCase):
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
        install.install(self.impl, "impl", "../design")
        install.install(self.design, "design", "../impl")
        # 置いたこと自体（.gitignore とマシン）はコミットしておく。
        git(self.impl, "add", "-A"); git(self.impl, "commit", "-q", "-m", "add pair align")
        git(self.design, "add", "-A"); git(self.design, "commit", "-q", "-m", "add pair align")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()

    def run_pa(self, repo: Path, *args: str, path_env: str | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, **GIT_ENV}
        env["PATH"] = path_env if path_env is not None else f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin"
        return subprocess.run([sys.executable, ".statemachine/pair_align/pair_align.py", *args],
                              cwd=repo, capture_output=True, text=True, env=env)

    def write_prompt(self, repo: Path, pid: str) -> None:
        tpl = (repo / ".statemachine/pair_align/templates/prompt.md").read_text(encoding="utf-8")
        body = tpl.replace("<ID>", pid).replace("<相手の側（実装 / 設計書）>", "設計書")
        filled = []
        for line in body.splitlines():
            filled.append("- 記入済み" if line.startswith("<!-- TODO") else line)
        (repo / ".pair-align/work/prompt.md").write_text("\n".join(filled) + "\n", encoding="utf-8")

    # ------------------------------------------------------------ 往復

    def test_round_trip_does_not_bounce_back(self) -> None:
        commit(self.impl, {"src/app.py": "def hello():\n    return 1\n\ndef goodbye_world():\n    return 2\n"},
               "add goodbye")
        r = self.run_pa(self.impl, "collect")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("CHANGES 1 commits, 1 files"), r.stdout)
        current = json.loads((self.impl / ".pair-align/work/current.json").read_text(encoding="utf-8"))
        self.assertIn("goodbye_world", current["terms"])
        changes = (self.impl / ".pair-align/work/changes.md").read_text(encoding="utf-8")
        self.assertIn("+def goodbye_world", changes)
        self.assertNotIn("add pair align", changes)  # 初回は直前の 1 コミットだけ

        pid = current["id"]
        self.write_prompt(self.impl, pid)
        self.assertEqual(self.run_pa(self.impl, "verify-prompt").returncode, 0)
        r = self.run_pa(self.impl, "record")
        self.assertTrue(r.stdout.startswith("RECORDED .pair-align/outbox/"), r.stdout + r.stderr)
        self.assertTrue(self.run_pa(self.impl, "collect").stdout.startswith("NO_CHANGES"))

        # 設計書側: まず届いた依頼を片付けるよう求められる。
        r = self.run_pa(self.design, "collect")
        self.assertTrue(r.stdout.startswith("INBOUND_PENDING 1"), r.stdout + r.stderr)
        inbound = (self.design / ".pair-align/work/inbound.md").read_text(encoding="utf-8")
        self.assertIn(f"Pair-Align-Id: {pid}", inbound)

        # 反映コミット（合図の行つき）は、設計書側から実装への依頼にならない。
        commit(self.design, {"docs/api.md": "# API\n\n## hello\n\n## goodbye_world\n\n2 を返す。\n"},
               f"docs: goodbye_world を追記\n\nPair-Align: {pid}")
        r = self.run_pa(self.design, "collect")
        self.assertTrue(r.stdout.startswith("NO_CHANGES"), r.stdout + r.stderr)
        self.assertIn("反映として外したコミット: 1 件", r.stdout)

        status = self.run_pa(self.impl, "status").stdout
        self.assertIn("送って相手が未処理の依頼: 0 件", status)

    def test_mixed_commit_after_ack_is_still_sent(self) -> None:
        commit(self.design, {"docs/api.md": "# API\n\n## hello\n\nhello は 2 を返す。\n"}, "spec change")
        r = self.run_pa(self.design, "collect")
        self.assertTrue(r.stdout.startswith("CHANGES"), r.stdout + r.stderr)
        pid = json.loads((self.design / ".pair-align/work/current.json").read_text(encoding="utf-8"))["id"]
        self.assertTrue(pid.startswith("design-"))
        self.write_prompt(self.design, pid)
        self.run_pa(self.design, "record")

        # 実装側は反映不要と判断して ack。その後の通常の変更は依頼になる。
        self.assertTrue(self.run_pa(self.impl, "collect").stdout.startswith("INBOUND_PENDING"))
        r = self.run_pa(self.impl, "ack", pid)
        self.assertEqual(r.stdout.strip(), f"ACKED {pid}")
        self.assertEqual(self.run_pa(self.impl, "ack", "design-unknown").returncode, 2)
        commit(self.impl, {"src/app.py": "def hello():\n    return 2\n"}, "fix")
        self.assertTrue(self.run_pa(self.impl, "collect").stdout.startswith("CHANGES 1 commits"))

    def test_skip_advances_baseline(self) -> None:
        commit(self.impl, {"src/app.py": "def hello():\n    return 1  # comment\n"}, "comment only")
        self.assertTrue(self.run_pa(self.impl, "collect").stdout.startswith("CHANGES"))
        r = self.run_pa(self.impl, "record", "--skip")
        self.assertTrue(r.stdout.startswith("SKIPPED"), r.stdout + r.stderr)
        self.assertTrue(self.run_pa(self.impl, "collect").stdout.startswith("NO_CHANGES"))
        self.assertEqual(list((self.impl / ".pair-align").glob("outbox/*.md")), [])

    def test_since_and_uncommitted(self) -> None:
        (self.impl / "src/app.py").write_text("def hello():\n    return 9\n", encoding="utf-8")
        r = self.run_pa(self.impl, "collect", "--since", "HEAD")
        self.assertTrue(r.stdout.startswith("NO_CHANGES"), r.stdout + r.stderr)
        self.assertIn("コミットしていない変更", r.stdout)
        self.assertEqual(self.run_pa(self.impl, "collect", "--since", "nope").returncode, 2)

    # ------------------------------------------------------------ 依頼文の検査

    def test_verify_prompt_rejects_template_and_wrong_id(self) -> None:
        commit(self.impl, {"src/app.py": "def hello():\n    return 3\n"}, "change")
        self.run_pa(self.impl, "collect")
        pid = json.loads((self.impl / ".pair-align/work/current.json").read_text(encoding="utf-8"))["id"]
        self.assertEqual(self.run_pa(self.impl, "verify-prompt").returncode, 1)  # まだ無い

        tpl = (self.impl / ".statemachine/pair_align/templates/prompt.md").read_text(encoding="utf-8")
        (self.impl / ".pair-align/work/prompt.md").write_text(tpl.replace("<ID>", pid), encoding="utf-8")
        r = self.run_pa(self.impl, "verify-prompt")
        self.assertEqual(r.returncode, 1)
        self.assertIn("見出しの中身が空です", r.stderr)

        self.write_prompt(self.impl, "impl-0000000000")
        r = self.run_pa(self.impl, "verify-prompt")
        self.assertEqual(r.returncode, 1)
        self.assertIn(pid, r.stderr)
        self.assertEqual(self.run_pa(self.impl, "record").returncode, 2)

    # ------------------------------------------------------------ 相手側の検索

    def test_locate_uses_graphify_when_graph_exists(self) -> None:
        stub = self.bin / "graphify"
        log = self.tmp / "graphify.log"
        stub.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\necho 'Graph: graphify-out/graph.json'\necho 'NODE hello [src=docs/api.md loc=L3]'\n", encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        (self.design / "graphify-out").mkdir()
        (self.design / "graphify-out/graph.json").write_text("{}", encoding="utf-8")

        commit(self.impl, {"src/app.py": "def hello():\n    return 5\n"}, "change")
        self.run_pa(self.impl, "collect")
        r = self.run_pa(self.impl, "locate", "--term", "hello", "--refresh")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("graphify: used", r.stdout)
        calls = log.read_text(encoding="utf-8")
        self.assertIn(f"update {self.design}", calls)
        self.assertIn("query hello --graph", calls)
        cand = (self.impl / ".pair-align/work/candidates.md").read_text(encoding="utf-8")
        self.assertIn("NODE hello", cand)
        self.assertIn("- docs/api.md", cand)
        self.assertNotIn("- graphify-out/graph.json", cand)

    def test_locate_falls_back_to_grep(self) -> None:
        commit(self.impl, {"src/app.py": "def hello():\n    return 5\n"}, "change")
        self.run_pa(self.impl, "collect")
        r = self.run_pa(self.impl, "locate", "--term", "hello")
        self.assertIn("graphify: not-installed", r.stdout)
        self.assertIn("CANDIDATES 1 files", r.stdout)
        cand = (self.impl / ".pair-align/work/candidates.md").read_text(encoding="utf-8")
        self.assertIn("docs/api.md:3:## hello", cand)

    def test_missing_pair_is_reported(self) -> None:
        cfg = self.impl / ".statemachine/pair_align/pair.json"
        cfg.write_text(json.dumps({"side": "impl", "pair_path": "../nowhere"}), encoding="utf-8")
        r = self.run_pa(self.impl, "collect")
        self.assertEqual(r.returncode, 2)
        self.assertIn("pair_path", r.stderr)

    # ------------------------------------------------------------ 設置と定義

    def test_install_is_idempotent_and_keeps_config(self) -> None:
        install.install(self.impl, None, None)
        cfg = json.loads((self.impl / ".statemachine/pair_align/pair.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg, {"graphify": "auto", "side": "impl", "pair_path": "../design"})
        ignore = (self.impl / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(ignore.count(".pair-align/"), 1)
        gignore = (self.impl / ".graphifyignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(gignore, [".statemachine/pair_align/"])
        fresh = self.tmp / "fresh"
        fresh.mkdir()
        git(fresh, "init", "-q")
        with self.assertRaises(SystemExit):
            install.install(fresh, "impl", None)

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
