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

    # ------------------------------------------------------------ 意図: 読む → 分ける → 合うか

    def begin(self, repo: Path, intent: str | None = None, decision: str | None = None) -> str:
        args = ["begin"]
        if intent is not None:
            f = repo / ".pair-align/work/intent_input.md"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(intent, encoding="utf-8")
            args += ["--intent-file", str(f)]
        if decision is not None:
            args += ["--decision", decision]
        r = self.run_pa(repo, *args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def write_reading(self, repo: Path, cite: str = "docs/api.md") -> None:
        (repo / ".pair-align/work/reading.md").write_text(textwrap.dedent(f"""\
            # 相手の側を読んだ結果

            hello の戻り値について設計書を読んだ。

            ## 前提

            - hello は整数を返す（根拠: {cite}#hello）

            ## 制約

            - hello は 1 を返さなければならない（根拠: {cite}:3）

            ## 自由

            - なし
            """), encoding="utf-8")

    def write_question(self, repo: Path) -> None:
        (repo / ".pair-align/work/question.md").write_text(textwrap.dedent("""\
            # 確認

            ## 意図

            hello が 2 を返すようにしたい。

            ## ぶつかっている点

            - 制約: hello は 1 を返す（根拠: docs/api.md:3）

            ## 相手を直す場合に頼むこと

            1. hello の戻り値を 2 にする

            ## 波及してこちらで直すこと

            なし
            """), encoding="utf-8")

    def state(self, repo: Path) -> dict:
        return json.loads((repo / ".pair-align/state.json").read_text(encoding="utf-8"))

    def test_fit_changes_self_then_propagates(self) -> None:
        out = self.begin(self.impl, "hello にログを足したい")
        self.assertTrue(out.startswith("INTENT "), out)
        self.assertEqual((self.impl / ".pair-align/work/intent.md").read_text(encoding="utf-8"),
                         "hello にログを足したい")
        r = self.run_pa(self.impl, "locate", "--term", "hello")
        self.assertIn("CANDIDATES 1 files", r.stdout)

        self.assertEqual(self.run_pa(self.impl, "verify-reading").returncode, 1)  # まだ無い
        self.write_reading(self.impl, cite="docs/nowhere.md")
        r = self.run_pa(self.impl, "verify-reading")
        self.assertEqual(r.returncode, 1)
        self.assertIn("実在する根拠のパスがありません", r.stderr)
        self.write_reading(self.impl)
        self.assertEqual(self.run_pa(self.impl, "verify-reading").returncode, 0)

        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "self-check").returncode, 0)
        r = self.run_pa(self.impl, "commit", "-m", "hello にログ")
        self.assertTrue(r.stdout.startswith("COMMITTED_USER"), r.stdout + r.stderr)
        self.assertNotIn("Pair-Align:", git(self.impl, "log", "-1", "--format=%B"))
        self.assertIsNone(self.state(self.impl)["active"])
        # 利用者の意図のコミットは、ふつうに相手へ伝える候補になる。
        self.assertTrue(self.run_pa(self.impl, "collect").stdout.startswith("CHANGES 1 commits"))

    def test_self_check_runs_configured_command(self) -> None:
        cfg_path = self.impl / ".statemachine/pair_align/pair.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["check"] = [sys.executable, "-c", "import sys; sys.exit(open('src/app.py').read().count('return 1') != 1)"]
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        self.begin(self.impl, "意図")
        self.assertEqual(self.run_pa(self.impl, "self-check").returncode, 0)
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        r = self.run_pa(self.impl, "self-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("check が失敗しました", r.stderr)

    def test_misfit_asks_user_then_pair_fixes_and_ripples_back(self) -> None:
        # 実装側: 意図が設計書の制約とぶつかる → 確認して止まる。
        sid = self.begin(self.impl, "hello が 2 を返すようにしたい").split()[1]
        self.write_reading(self.impl)
        self.assertEqual(self.run_pa(self.impl, "verify-question").returncode, 1)
        self.write_question(self.impl)
        self.assertEqual(self.run_pa(self.impl, "verify-question").returncode, 0)
        self.assertTrue(self.run_pa(self.impl, "pause").stdout.startswith("PAUSED"))
        self.assertEqual(self.state(self.impl)["active"]["phase"], "confirm")

        out = self.begin(self.impl)  # 答えが無いうちは待つ
        self.assertTrue(out.startswith("AWAITING_DECISION"), out)
        self.assertTrue((self.impl / ".pair-align/work/question.md").is_file())
        self.assertEqual(self.run_pa(self.impl, "begin", "--decision", "たぶん").returncode, 2)

        # 利用者は「相手を直す」を選ぶ → 相手への依頼を作って待つ。
        out = self.begin(self.impl, decision="相手を直す")
        self.assertTrue(out.startswith("DECIDED_PAIR"), out)
        request_id = f"impl-{sid}"
        self.write_prompt(self.impl, request_id)
        self.assertEqual(self.run_pa(self.impl, "verify-prompt").returncode, 0)
        r = self.run_pa(self.impl, "record")
        self.assertTrue(r.stdout.startswith("RECORDED_REQUEST"), r.stdout + r.stderr)
        self.assertEqual([w["outbound_id"] for w in self.state(self.impl)["waiting"]], [request_id])
        self.assertTrue(self.begin(self.impl).startswith("WAITING_PAIR " + request_id))

        # 設計書側: 届いた依頼が意図になる → 読んで・直して・合図つきでコミット。
        out = self.begin(self.design, "ついでにやりたいこと")
        self.assertTrue(out.startswith("INBOUND "), out)
        self.assertIn("片付けたあとでもう一度", out)
        self.assertIn(f"Pair-Align-Id: {request_id}",
                      (self.design / ".pair-align/work/intent.md").read_text(encoding="utf-8"))
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n", encoding="utf-8")
        r = self.run_pa(self.design, "commit", "-m", "hello の戻り値を 2 に")
        self.assertTrue(r.stdout.startswith("COMMITTED_LINKED"), r.stdout + r.stderr)
        self.assertIn(f"Pair-Align: {request_id}", git(self.design, "log", "-1", "--format=%B"))
        self.assertTrue(self.run_pa(self.design, "collect").stdout.startswith("NO_CHANGES"))

        # 実装側: 相手が反映したので、波及として自分を直す。
        out = self.begin(self.impl)
        self.assertTrue(out.startswith(f"RIPPLE {sid}"), out)
        self.assertEqual((self.impl / ".pair-align/work/intent.md").read_text(encoding="utf-8"),
                         "hello が 2 を返すようにしたい")
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        r = self.run_pa(self.impl, "commit", "-m", "hello は 2 を返す")
        self.assertTrue(r.stdout.startswith("COMMITTED_LINKED"), r.stdout + r.stderr)
        self.assertIn(f"Pair-Align: {request_id}", git(self.impl, "log", "-1", "--format=%B"))
        st = self.state(self.impl)
        self.assertIsNone(st["active"])
        self.assertEqual(st["waiting"], [])
        # 波及のコミットも相手への依頼にならない。往復はここで止まる。
        self.assertTrue(self.run_pa(self.impl, "collect").stdout.startswith("NO_CHANGES"))
        self.assertTrue(self.begin(self.design).startswith("PROPAGATE"))

    def test_revise_and_abort(self) -> None:
        self.begin(self.impl, "最初の意図")
        self.write_reading(self.impl)
        self.write_question(self.impl)
        self.run_pa(self.impl, "pause")
        out = self.begin(self.impl, decision="意図を直す")
        self.assertTrue(out.startswith("AWAITING_DECISION"), out)
        self.assertIn("直した意図も入れて", out)
        out = self.begin(self.impl, "直した意図", decision="意図を直す")
        self.assertTrue(out.startswith("REVISED"), out)
        self.assertEqual((self.impl / ".pair-align/work/intent.md").read_text(encoding="utf-8"), "直した意図")

        # 途中で止まっても、次の begin は同じ意図の続きから。
        self.assertTrue(self.begin(self.impl).startswith("RESUME"))
        self.write_reading(self.impl)
        self.write_question(self.impl)
        self.run_pa(self.impl, "pause")
        self.assertTrue(self.begin(self.impl, decision="やめる").startswith("ABORTED"))
        self.assertIsNone(self.state(self.impl)["active"])
        self.assertTrue(self.begin(self.impl).startswith("PROPAGATE"))

    def test_inbound_without_change_is_closed(self) -> None:
        commit(self.design, {"docs/api.md": "# API\n\n## hello\n\n補足だけ。\n"}, "spec note")
        self.run_pa(self.design, "collect")
        pid = json.loads((self.design / ".pair-align/work/current.json").read_text(encoding="utf-8"))["id"]
        self.write_prompt(self.design, pid)
        self.run_pa(self.design, "record")

        self.assertTrue(self.begin(self.impl).startswith("INBOUND"))
        r = self.run_pa(self.impl, "commit", "-m", "x")
        self.assertTrue(r.stdout.startswith("UNCHANGED_LINKED"), r.stdout + r.stderr)
        self.assertIn(pid, self.state(self.impl)["acked"])
        self.assertTrue(self.begin(self.impl).startswith("PROPAGATE"))

    def test_abort_on_inbound_closes_it(self) -> None:
        commit(self.design, {"docs/api.md": "# API\n\n## hello\n\n3 を返す。\n"}, "spec")
        self.run_pa(self.design, "collect")
        pid = json.loads((self.design / ".pair-align/work/current.json").read_text(encoding="utf-8"))["id"]
        self.write_prompt(self.design, pid)
        self.run_pa(self.design, "record")
        self.begin(self.impl)
        self.write_reading(self.impl)
        self.write_question(self.impl)
        self.run_pa(self.impl, "pause")
        self.assertTrue(self.begin(self.impl, decision="やめる").startswith("ABORTED"))
        self.assertIn(pid, self.state(self.impl)["acked"])
        self.assertTrue(self.begin(self.impl).startswith("PROPAGATE"))

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
