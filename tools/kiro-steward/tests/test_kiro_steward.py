"""kiro-steward の単体テスト（標準ライブラリ unittest）。

正準ループ（優先順位付け・検証ゲート・積み直し・収束）と、人の判断機構
（policy 上書き・決定記録・通知の dedup）を kiro-flow を呼ばずに検証する。
kiro-flow stub を 1 回叩く統合テストも持つ（無ければ skip）。

    python -m unittest discover -s tools/kiro-steward/tests
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

_MOD = Path(__file__).resolve().parent.parent / "kiro-steward.py"
_spec = importlib.util.spec_from_file_location("kiro_steward", _MOD)
ks = importlib.util.module_from_spec(_spec)
sys.modules["kiro_steward"] = ks  # dataclass の前方参照解決に必要
_spec.loader.exec_module(ks)


def write(d: Path, name: str, body: str) -> Path:
    p = d / name
    p.write_text(body, encoding="utf-8")
    return p


def cfg_for(d: Path, **kw):
    base = dict(
        backlog=d / "backlog.md", policy=d / "policy.md", decisions=d / "DECISIONS.md",
        journal=d / "journal.md", needs=d / "NEEDS_YOU.md", workdir=d, bus=d / "bus",
        planner="stub", executor="stub", dry_run=True,
    )
    base.update(kw)
    return ks.Config(**base)


# --------------------------------------------------------------------------
class TestParse(unittest.TestCase):
    def test_roundtrip_with_source(self):
        text = ("# Backlog\n\n"
                "## T1: a\n- status: ready\n- source: human\n- verify: `true`\n- retries: 0\n- note: x\n\n"
                "## T2: b\n- status: inbox\n- source: triage\n- verify: \n- retries: 2\n")
        pre, tasks = ks.parse_backlog(text)
        self.assertEqual([t.id for t in tasks], ["T1", "T2"])
        self.assertEqual(tasks[0].source, "human")
        self.assertEqual(tasks[0].verify, "true")
        self.assertEqual(tasks[0].extra, [("note", "x")])
        self.assertEqual(tasks[1].status, "inbox")
        # 書き戻して等価
        _, tasks2 = ks.parse_backlog(ks.serialize_backlog(pre, tasks))
        self.assertEqual(tasks2[1].source, "triage")
        self.assertEqual(tasks2[0].extra, [("note", "x")])


class TestPolicy(unittest.TestCase):
    def test_parse_and_match(self):
        pol = ks.parse_policy("# c\ndeny: prod\npin: T3\ndefer: cleanup\ndeny: secret\n")
        self.assertEqual(pol.deny, ["prod", "secret"])
        self.assertEqual(pol.pin, ["T3"])
        t = ks.Task(id="T9", title="deploy prod api")
        self.assertTrue(t.matches("prod"))
        self.assertFalse(t.matches("staging"))


class TestPrioritize(unittest.TestCase):
    def test_stub_is_oldest_first(self):
        tasks = [ks.Task(id=f"T{i}", title=str(i), status="ready") for i in range(3)]
        order = ks.prioritize(tasks, ks.Policy(), planner="stub")
        self.assertEqual([t.id for t in order], ["T0", "T1", "T2"])  # ファイル順=最古優先

    def test_policy_pin_and_defer(self):
        tasks = [ks.Task(id="T0", title="a", status="ready"),
                 ks.Task(id="T1", title="cleanup logs", status="ready"),
                 ks.Task(id="T2", title="urgent", status="ready")]
        pol = ks.Policy(pin=["T2"], defer=["cleanup"])
        order = ks.prioritize(tasks, pol, planner="stub")
        self.assertEqual([t.id for t in order], ["T2", "T0", "T1"])  # pin→先頭, defer→末尾

    def test_agent_rank_with_fallback(self):
        ready = [ks.Task(id="T0", title="a"), ks.Task(id="T1", title="b")]
        # 正常: エージェントが逆順を返す
        ranked = ks.rank_agent(ready, None, kiro_run=lambda p, m: '["T1","T0"]')
        self.assertEqual([t.id for t in ranked], ["T1", "T0"])
        # 失敗: 例外 → None（呼び出し側で最古優先にフォールバック）
        def boom(p, m):
            raise RuntimeError("no kiro-cli")
        self.assertIsNone(ks.rank_agent(ready, None, kiro_run=boom))


class TestTriage(unittest.TestCase):
    def test_inbox_with_verify_promoted(self):
        tasks = [ks.Task(id="T1", title="a", status="inbox", verify="true"),
                 ks.Task(id="T2", title="b", status="inbox", verify="")]
        ks.triage(tasks, ks.Policy())
        self.assertEqual(tasks[0].status, "ready")   # verify あり→昇格
        self.assertEqual(tasks[1].status, "inbox")   # verify なし→据え置き（need_intake）

    def test_deny_blocks(self):
        tasks = [ks.Task(id="T1", title="deploy prod", status="ready", verify="true")]
        trans = ks.triage(tasks, ks.Policy(deny=["prod"]))
        self.assertEqual(tasks[0].status, "blocked")
        self.assertEqual(len(trans), 1)


class TestRunLoop(unittest.TestCase):
    def test_drains_all_pass(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "backlog.md",
                  "# B\n\n## T1: a\n- status: ready\n- verify: `true`\n- retries: 0\n\n"
                  "## T2: b\n- status: ready\n- verify: `true`\n- retries: 0\n")
            res = ks.run_loop(cfg_for(d))
            self.assertEqual(res["reason"], ks.REASON_DRAINED)
            self.assertEqual(res["counts"]["done"], 2)
            self.assertEqual(ks.exit_code_for(res), 0)

    def test_ng_restacks_then_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "backlog.md",
                  "# B\n\n## T1: a\n- status: ready\n- verify: `false`\n- retries: 0\n")
            res = ks.run_loop(cfg_for(d, max_retries=2))
            # NG で積み直し → retries>2 で人の判断（blocked）
            self.assertEqual(res["counts"]["blocked"], 1)
            self.assertGreater(res["tasks"][0].retries, 2)
            self.assertEqual(ks.exit_code_for(res), 1)

    def test_budget_stop_by_cycles(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "backlog.md",
                  "# B\n\n## T1: a\n- status: ready\n- verify: `false`\n- retries: 0\n")
            res = ks.run_loop(cfg_for(d, max_retries=999, max_cycles=4))
            self.assertEqual(res["reason"], ks.REASON_BUDGET)
            self.assertEqual(res["cycles"], 4)
            self.assertEqual(ks.exit_code_for(res), 2)

    def test_no_verify_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "backlog.md", "# B\n\n## T1: a\n- status: ready\n- verify: \n- retries: 0\n")
            res = ks.run_loop(cfg_for(d))
            self.assertEqual(res["counts"]["blocked"], 1)
            self.assertEqual(res["tasks"][0].retries, 1)

    def test_act_injection(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            marker = d / "acted"
            write(d, "backlog.md",
                  f"# B\n\n## T1: a\n- status: ready\n- verify: `test -f {marker}`\n- retries: 0\n")
            calls = []

            def fake_act(task, cfg, location="local"):
                calls.append((task.id, location))
                marker.write_text("x")
                return True, "ok"

            res = ks.run_loop(cfg_for(d, dry_run=False), act=fake_act)
            self.assertEqual(calls, [("T1", "local")])
            self.assertEqual(res["counts"]["done"], 1)


class TestLocation(unittest.TestCase):
    def test_policy_offload_parsed(self):
        pol = ks.parse_policy("offload: heavy\ndeny: prod\n")
        self.assertEqual(pol.offload, ["heavy"])

    def test_decide_location(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            t = ks.Task(id="T1", title="heavy batch job")
            pol = ks.Policy(offload=["heavy"])
            # git バス未設定 → 常に local
            self.assertEqual(ks.decide_location(t, pol, cfg_for(d)), "local")
            # git バス設定＋offload 一致 → remote
            c = cfg_for(d, git_bus="git@x:team/bus.git")
            self.assertEqual(ks.decide_location(t, pol, c), "remote")
            # offload 不一致 → local
            self.assertEqual(ks.decide_location(ks.Task(id="T2", title="light"), pol, c), "local")

    def test_build_cmd_includes_git_when_remote(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            t = ks.Task(id="T1", title="a", verify="true")
            c = cfg_for(d, git_bus="git@x:team/bus.git", git_branch="main")
            local_cmd = ks.build_kiro_flow_cmd(t, c, "local")
            remote_cmd = ks.build_kiro_flow_cmd(t, c, "remote")
            self.assertNotIn("--git", local_cmd)
            self.assertIn("--git", remote_cmd)
            self.assertIn("git@x:team/bus.git", remote_cmd)

    def test_run_offloads_matching_task(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "policy.md", "offload: heavy\n")
            write(d, "backlog.md",
                  "# B\n\n## T1: heavy job\n- status: ready\n- verify: `true`\n- retries: 0\n\n"
                  "## T2: light job\n- status: ready\n- verify: `true`\n- retries: 0\n")
            seen = {}

            def fake_act(task, cfg, location="local"):
                seen[task.id] = location
                return True, "ok"

            ks.run_loop(cfg_for(d, dry_run=False, git_bus="git@x:team/bus.git"), act=fake_act)
            self.assertEqual(seen["T1"], "remote")  # offload 一致
            self.assertEqual(seen["T2"], "local")


class TestArchive(unittest.TestCase):
    def test_done_moved_to_archive(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "backlog.md",
                  "# B\n\n## T1: a\n- status: ready\n- verify: `true`\n- retries: 0\n\n"
                  "## T2: b\n- status: ready\n- verify: `false`\n- retries: 0\n")
            res = ks.run_loop(cfg_for(d, max_retries=0))  # T1 done, T2 即 blocked
            self.assertEqual(res["archived"], 1)
            self.assertEqual(res["counts"]["done"], 1)  # counts はアーカイブ前で確定
            arch = (d / "ARCHIVE.md").read_text()
            self.assertIn("## T1: a", arch)
            # backlog からは done が消え、blocked は残る
            _, tasks = ks.load_backlog(d / "backlog.md")
            ids = [t.id for t in tasks]
            self.assertNotIn("T1", ids)
            self.assertIn("T2", ids)

    def test_no_archive_keeps_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "backlog.md",
                  "# B\n\n## T1: a\n- status: ready\n- verify: `true`\n- retries: 0\n")
            res = ks.run_loop(cfg_for(d, do_archive=False))
            self.assertEqual(res["archived"], 0)
            self.assertFalse((d / "ARCHIVE.md").exists())
            _, tasks = ks.load_backlog(d / "backlog.md")
            self.assertEqual(tasks[0].status, "done")


class TestPace(unittest.TestCase):
    def test_decide_pace_fixed_and_budget(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # 固定 pace=5、経過2秒 → 残り3秒待つ
            self.assertAlmostEqual(ks.decide_pace(cfg_for(d, pace=5.0), 2.0), 3.0)
            # 既に下限を超過 → 待たない
            self.assertEqual(ks.decide_pace(cfg_for(d, pace=5.0), 9.0), 0.0)
            # 予算で均す: max_seconds=20 / max_cycles=10 → 目標2秒/サイクル
            c = cfg_for(d, pace=0.0, max_seconds=20.0, max_cycles=10)
            self.assertAlmostEqual(ks.decide_pace(c, 0.5), 1.5)

    def test_run_loop_calls_sleeper(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "backlog.md",
                  "# B\n\n## T1: a\n- status: ready\n- verify: `true`\n- retries: 0\n\n"
                  "## T2: b\n- status: ready\n- verify: `true`\n- retries: 0\n")
            slept = []
            ks.run_loop(cfg_for(d, pace=3.0), sleeper=lambda s: slept.append(s))
            self.assertTrue(slept and all(s > 0 for s in slept))

    def test_no_pace_no_sleep(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "backlog.md", "# B\n\n## T1: a\n- status: ready\n- verify: `true`\n- retries: 0\n")
            slept = []
            ks.run_loop(cfg_for(d), sleeper=lambda s: slept.append(s))  # pace=0 既定
            self.assertEqual(slept, [])


class TestNotify(unittest.TestCase):
    def test_notify_only_on_transition(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # verify 未定義 → blocked 遷移が起き、NEEDS_YOU.md が書かれる
            write(d, "backlog.md", "# B\n\n## T1: a\n- status: ready\n- verify: \n- retries: 0\n")
            res = ks.run_loop(cfg_for(d))
            self.assertTrue(res["notified"])
            self.assertTrue((d / "NEEDS_YOU.md").exists())

    def test_no_notify_when_no_transition(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "backlog.md", "# B\n\n## T1: a\n- status: ready\n- verify: `true`\n- retries: 0\n")
            res = ks.run_loop(cfg_for(d))
            self.assertFalse(res["notified"])
            self.assertFalse((d / "NEEDS_YOU.md").exists())


class TestDecisionRecords(unittest.TestCase):
    def test_approve_writes_dr_and_restacks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "backlog.md", "# B\n\n## T1: a\n- status: blocked\n- verify: `true`\n- retries: 3\n")
            rc = ks.cmd_approve(cfg_for(d, actor="alice"), "T1", "verify を修正")
            self.assertEqual(rc, 0)
            _, tasks = ks.load_backlog(d / "backlog.md")
            self.assertEqual(tasks[0].status, "ready")
            dec = (d / "DECISIONS.md").read_text()
            self.assertIn("DR-0001", dec)
            self.assertIn("alice", dec)
            self.assertIn("verify を修正", dec)

    def test_hold_adds_deny_and_dr(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "backlog.md", "# B\n\n## T1: a\n- status: ready\n- verify: `true`\n- retries: 0\n")
            ks.cmd_hold(cfg_for(d), "T1", "本番関連は手動")
            self.assertIn("deny: T1", (d / "policy.md").read_text())
            _, tasks = ks.load_backlog(d / "backlog.md")
            self.assertEqual(tasks[0].status, "blocked")
            self.assertIn("DR-0001", (d / "DECISIONS.md").read_text())

    def test_reprioritize_pin_and_incrementing_dr(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write(d, "backlog.md", "# B\n\n## T1: a\n- status: ready\n- verify: `true`\n- retries: 0\n")
            c = cfg_for(d)
            ks.cmd_reprioritize(c, "T1", "pin", "急ぎ")
            ks.cmd_reprioritize(c, "T1", "defer", "やっぱり後で")
            pol = (d / "policy.md").read_text()
            self.assertIn("pin: T1", pol)
            self.assertIn("defer: T1", pol)
            dec = (d / "DECISIONS.md").read_text()
            self.assertIn("DR-0001", dec)
            self.assertIn("DR-0002", dec)  # 連番


class TestKiroFlowIntegration(unittest.TestCase):
    def test_stub_end_to_end(self):
        kf = Path(__file__).resolve().parents[2] / "kiro-flow" / "kiro-flow.py"
        if not kf.exists():
            self.skipTest("kiro-flow.py が見つからない")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            out = d / "out.txt"
            out.write_text("done")
            write(d, "backlog.md",
                  f"# B\n\n## T1: 何か\n- status: ready\n- verify: `test -f {out}`\n- retries: 0\n")
            os.environ["KIRO_FLOW_STUB_SLEEP_MAX"] = "0"
            res = ks.run_loop(cfg_for(d, dry_run=False, act_timeout=120, max_cycles=3))
            self.assertEqual(res["counts"]["done"], 1)
            self.assertEqual(res["reason"], ks.REASON_DRAINED)


if __name__ == "__main__":
    unittest.main()
