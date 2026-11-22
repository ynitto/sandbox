"""reverse-spec の通しのテスト。ソースと仕様書リポジトリを一時フォルダに作り、エージェントの代わりにファイルを書いて
step を回す（LLM は呼ばない）。run は、1 段だけ進める偽のエージェント（このファイル自身）で確かめる。"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
INIT = TOOL_DIR / "init.py"

APP = {
    "src/orders.py": textwrap.dedent("""\
        def create_order(items):
            if not items:
                raise ValueError("empty")
            return {"items": items, "status": "new"}


        def cancel(order):
            order["status"] = "cancelled"
            return order
        """),
    "src/schema.sql": "CREATE TABLE orders (\n  id INTEGER PRIMARY KEY,\n  status TEXT NOT NULL\n);\n",
    "vendor/lib.js": "console.log('x');\n",
    "README.md": "# app\n\nOrders app.\n",
}

UNITS = """\
unit orders-table data 受注テーブル
  src/schema.sql
unit orders module 受注の作成と取消
  src/orders.py
unit docs out-of-scope 説明書き（仕様にしない）
  README.md
unit vendor out-of-scope 同梱した外部ライブラリ
  vendor/
"""

ORDERS_SPEC = {
    "責務": "受注を作り、取り消す `src/orders.py:1-9`。",
    "機能要件": "- REQ-orders-01: 品目が空なら作らない `src/orders.py:2-3`\n- REQ-orders-02: 作った受注の状態は new `src/orders.py:4`",
    "処理フロー": "1. 品目を確かめる `src/orders.py:2`\n2. 取消では状態を cancelled にする `src/orders.py:8`",
    "依存": "[[SPEC-data-orders-table]]",
    "未確定事項": "なし",
}
TABLE_SPEC = {
    "スキーマ": "| 列 | 型 | 根拠 |\n|---|---|---|\n| id | INTEGER | `src/schema.sql:2` |\n| status | TEXT | `src/schema.sql:3` |",
    "制約と不変条件": "status は NULL にできない `src/schema.sql:3`",
    "読み書きする箇所": "UNKNOWN[deferred] 書き込む箇所はコードに無い",
    "未確定事項": "なし",
}


def fill(path: Path, bodies: dict) -> None:
    text = path.read_text(encoding="utf-8")
    for head, body in bodies.items():
        text = re.sub(rf"(## {re.escape(head)}\n\n)<!-- 書く:.*?-->", lambda m: m.group(1) + body, text, flags=re.S)
    path.write_text(text, encoding="utf-8")


def fill_sheet(path: Path, verdict="OK", reason="") -> None:
    text = path.read_text(encoding="utf-8").replace("- 判定: ?", f"- 判定: {verdict}")
    if reason:
        text = text.replace("- 理由: ", f"- 理由: {reason}")
    path.write_text(text, encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.src = base / "app"
        for rel, text in APP.items():
            (self.src / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.src / rel).write_text(text, encoding="utf-8")
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run(["git", "init", "-q"], cwd=self.src, check=True)
        subprocess.run(["git", "add", "."], cwd=self.src, check=True)
        subprocess.run(g + ["commit", "-qm", "base"], cwd=self.src, check=True)
        self.out = base / "app-spec"
        self.init()

    def init(self, *extra):
        proc = subprocess.run([sys.executable, str(INIT), str(self.src), "--out", str(self.out), *extra],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def rspec(self, *args, env=None, code=0):
        proc = subprocess.run([sys.executable, str(self.out / ".rspec" / "rspec.py"), *args], cwd=self.out,
                              capture_output=True, text=True, env=env)
        if code is not None:
            self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
        return proc.stdout + proc.stderr

    def state(self):
        return json.loads((self.out / ".rspec" / "state.json").read_text(encoding="utf-8"))

    def set_config(self, **kv):
        path = self.out / "rspec.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg.update(kv)
        path.write_text(json.dumps(cfg), encoding="utf-8")

    def write_scope(self):
        (self.out / "extraction" / "scope.md").write_text(
            "## 目的\n\n受注アプリの仕様を起こす。\n\n## 対象と対象外\n\nvendor は対象外。\n", encoding="utf-8")

    def to_drafting(self):
        self.write_scope()
        self.rspec("step", "OK")
        (self.out / "extraction" / "units.txt").write_text(UNITS, encoding="utf-8")
        self.rspec("step", "OK")
        self.assertEqual(self.state()["state"], "confirm")
        self.rspec("step", "OK")
        self.assertEqual(self.state()["state"], "draft")


class InitTest(Base):
    def test_places_files_and_ingests(self):
        for rel in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".claude/agents/rspec-verifier.md",
                    ".github/agents/rspec-verifier.agent.md", ".kiro/agents/rspec-verifier.json",
                    "rspec.json", ".rspec/rspec.py", "extraction/inventory.tsv"):
            self.assertTrue((self.out / rel).exists(), rel)
        inv = (self.out / "extraction" / "inventory.tsv").read_text(encoding="utf-8")
        self.assertIn("src/orders.py\t9\t", inv)
        self.assertRegex(inv.splitlines()[0], r"# source-commit: [0-9a-f]{40}$")
        self.assertEqual(json.loads((self.out / "rspec.json").read_text())["source"], "../app")
        log = subprocess.run(["git", "log", "--oneline"], cwd=self.out, capture_output=True, text=True).stdout
        self.assertIn("rspec:", log)

    def test_reinit_keeps_config_and_state(self):
        self.set_config(verify_sample=3)
        self.write_scope()
        self.rspec("step", "OK")
        self.init("--host", "codex")
        self.assertEqual(json.loads((self.out / "rspec.json").read_text())["verify_sample"], 3)
        self.assertEqual(self.state()["state"], "units")

    def test_refuses_output_inside_source(self):
        proc = subprocess.run([sys.executable, str(INIT), str(self.src), "--out", str(self.src / "spec")],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)


class FlowTest(Base):
    def test_drift_detects_newly_excluded_file(self):
        self.set_config(exclude=["src/orders.py"])
        output = self.rspec("drift", code=1)
        self.assertIn("消えた: 1\n  src/orders.py", output)

    def test_gates_report_invalid_unit_category(self):
        self.to_drafting()
        path = self.out / "extraction" / "units.txt"
        path.write_text(UNITS.replace(" module ", " typo ", 1), encoding="utf-8")
        output = self.rspec("gates", code=1)
        self.assertIn("不通過 G-DENOM", output)
        self.assertIn("種類は", output)

    def test_full_flow_to_done(self):
        self.assertIn("対象の範囲を確かめる", self.rspec("step"))
        out = self.rspec("step", "OK")  # scope.md がまだ無い
        self.assertIn("scope.md がありません", out)
        self.to_drafting()

        st = self.state()
        self.assertEqual(st["current"], "orders-table")
        spec = self.out / "specs" / "data" / "orders-table.md"
        self.assertIn("spec-id: SPEC-data-orders-table", spec.read_text(encoding="utf-8"))
        out = self.rspec("step", "OK")  # ひな形のまま
        self.assertIn("「書く:」のコメントが残っている", out)
        fill(spec, TABLE_SPEC)
        self.rspec("step", "OK")
        self.assertEqual(self.state()["state"], "verify")
        sheet = self.out / self.state()["units"]["orders-table"]["sheet"]
        self.assertIn("src/schema.sql:", sheet.read_text(encoding="utf-8"))
        out = self.rspec("step", "OK")  # まだ判定していない
        self.assertIn("判定が OK / NG になっていない", out)
        fill_sheet(sheet)
        self.rspec("step", "OK")
        st = self.state()
        self.assertEqual(st["units"]["orders-table"]["status"], "verified")
        self.assertEqual((st["state"], st["current"]), ("draft", "orders"))

        # 2 つ目の単位: 検証で否とされ、直してから通る
        spec2 = self.out / "specs" / "modules" / "orders.md"
        fill(spec2, ORDERS_SPEC)
        self.rspec("step", "OK")
        sheet = self.out / self.state()["units"]["orders"]["sheet"]
        text = sheet.read_text(encoding="utf-8")
        text = text.replace("- 判定: ?", "- 判定: NG", 1).replace("- 理由: ", "- 理由: 行がずれている", 1)
        sheet.write_text(text.replace("- 判定: ?", "- 判定: OK"), encoding="utf-8")
        self.rspec("step", "OK")
        st = self.state()
        self.assertEqual(st["state"], "fix")
        ng_claim = st["units"]["orders"]["ng"][0]["claim"]
        self.assertIn("行がずれている", self.rspec("step"))
        out = self.rspec("step", "OK")  # 直していない
        self.assertIn("否とされた記述がそのまま残っている", out)
        text = spec2.read_text(encoding="utf-8")
        spec2.write_text(text.replace(ng_claim, ng_claim + "（確かめ直した）"), encoding="utf-8")
        self.rspec("step", "OK")
        st = self.state()
        self.assertEqual((st["state"], st["units"]["orders"]["round"]), ("verify", 2))
        fill_sheet(self.out / st["units"]["orders"]["sheet"])
        self.rspec("step", "OK")
        self.assertEqual(self.state()["state"], "synthesize")
        self.assertTrue((self.out / "specs" / "index.md").exists())

        out = self.rspec("step", "OK")
        self.assertIn("ひな形", out)
        fill(self.out / "specs" / "overview.md", {
            "目的": "受注を扱う [[SPEC-module-orders]]", "スコープ": "vendor は対象外 [[SPEC-data-orders-table]]",
            "用語": "なし", "全体像": "[[SPEC-module-orders]] が [[SPEC-data-orders-table]] を使う",
            "外部とのつながり": "UNKNOWN[undecidable] 外部とのやりとりはコードに無い",
            "制約と前提": "Python で書かれている `src/orders.py:1`", "非機能の特徴": "UNKNOWN[deferred] 測っていない",
            "未確定事項": "なし"})
        fill(self.out / "specs" / "cross-cutting.md", {k: "なし" for k in
             ["認証と認可", "エラー処理", "ログと監視", "設定", "トランザクションと整合性", "未確定事項"]})
        out = self.rspec("step", "OK")
        st = self.state()
        self.assertEqual(st["state"], "done", out)
        self.assertIn("END", self.rspec("step"))
        self.assertIn("検証済み 2", (self.out / "extraction" / "report.md").read_text(encoding="utf-8"))
        self.rspec("gates")
        src_index = (self.out / "specs" / "source-index.md").read_text(encoding="utf-8")
        self.assertIn("| `src/orders.py` | orders | SPEC-module-orders |", src_index)
        log = subprocess.run(["git", "log", "--oneline"], cwd=self.out, capture_output=True, text=True).stdout
        self.assertGreater(len(log.splitlines()), 8)

        # ソースが変わったら最後の検査は通らず、drift --apply で作り直しに回る
        (self.src / "src" / "orders.py").write_text(APP["src/orders.py"] + "\n# changed\n", encoding="utf-8")
        self.assertIn("G-FRESH", self.rspec("gates", code=1))
        self.assertIn("作り直しが要る単位: orders", self.rspec("drift", code=1))
        self.rspec("drift", "--apply")
        self.assertEqual(self.state()["state"], "units")
        self.rspec("step", "OK")
        st = self.state()
        self.assertEqual(st["units"]["orders"]["status"], "pending")
        self.assertEqual(st["units"]["orders-table"]["status"], "verified")


class GateTest(Base):
    def test_units_must_cover_denominator_and_size(self):
        self.write_scope()
        self.rspec("step", "OK")
        (self.out / "extraction" / "units.txt").write_text("unit orders module 受注\n  src/orders.py\n", encoding="utf-8")
        out = self.rspec("step", "OK")
        self.assertIn("どの単位にも入っていないファイルが 3 件", out)
        self.assertIn("src/schema.sql", out)
        self.set_config(max_unit_lines=5)
        (self.out / "extraction" / "units.txt").write_text(
            "unit all module 全部\n  src/**\nunit x out-of-scope 外\n  README.md\n  vendor/**\n", encoding="utf-8")
        out = self.rspec("step", "OK")
        self.assertIn("上限 5 を超える", out)
        self.assertEqual(self.state()["state"], "units")

    def test_bad_citations_and_unknowns(self):
        self.to_drafting()
        spec = self.out / "specs" / "data" / "orders-table.md"
        bad = dict(TABLE_SPEC)
        bad["スキーマ"] = "id `src/schema.sql:99` と `src/nope.sql:1` と `vendor/lib.js:1`"
        bad["制約と不変条件"] = "UNKNOWN 何か"
        fill(spec, bad)
        out = self.rspec("check", code=1)
        self.assertIn("行が範囲外", out)
        self.assertIn("src/nope.sql", out)
        self.assertIn("UNKNOWN は UNKNOWN[", out)

    def test_retries_then_stuck_and_skip(self):
        self.to_drafting()
        for _ in range(3):
            self.rspec("step", "OK")
        st = self.state()
        self.assertEqual(st["state"], "stuck")
        self.assertIn("SKIP", self.rspec("step"))
        self.rspec("step", "SKIP 後回し")
        st = self.state()
        self.assertEqual(st["units"]["orders-table"]["status"], "blocked")
        self.assertEqual((st["state"], st["current"]), ("draft", "orders"))
        self.rspec("requeue", "--blocked")
        self.assertEqual(self.state()["units"]["orders-table"]["status"], "pending")

    def test_on_stuck_skip_goes_on_unattended(self):
        self.set_config(on_stuck="skip", confirm="auto")
        self.write_scope()
        self.rspec("step", "OK")
        (self.out / "extraction" / "units.txt").write_text(UNITS, encoding="utf-8")
        self.rspec("step", "OK")
        self.assertEqual(self.state()["state"], "draft")  # confirm は省かれる
        self.rspec("step", "FAILED 読めない")
        st = self.state()
        self.assertEqual(st["units"]["orders-table"]["status"], "blocked")
        self.assertEqual(st["current"], "orders")

    def test_wrong_first_word_is_rejected(self):
        out = self.rspec("step", "done", code=2)
        self.assertIn("OK / FAILED", out)
        self.assertEqual(self.state()["seq"], 0)


FAKE_AGENT = """
import json, os, re, subprocess, sys
from pathlib import Path
root = Path.cwd()
tool = [sys.executable, str(root / '.rspec' / 'rspec.py')]
st = json.loads((root / '.rspec' / 'state.json').read_text(encoding='utf-8')) if (root / '.rspec' / 'state.json').exists() else {'state': 'scope'}
sys.path.insert(0, %(tests)r)
import test_reverse_spec as t
s = st['state']
if s == 'scope':
    (root / 'extraction' / 'scope.md').write_text('## 目的\\n\\nx\\n\\n## 対象と対象外\\n\\ny\\n', encoding='utf-8')
elif s == 'units':
    (root / 'extraction' / 'units.txt').write_text(t.UNITS, encoding='utf-8')
elif s == 'draft':
    path = root / 'specs' / ('data/orders-table.md' if st['current'] == 'orders-table' else 'modules/orders.md')
    t.fill(path, t.TABLE_SPEC if st['current'] == 'orders-table' else t.ORDERS_SPEC)
elif s == 'verify':
    t.fill_sheet(root / st['units'][st['current']]['sheet'])
elif s == 'synthesize':
    t.fill(root / 'specs' / 'overview.md', {k: '[[SPEC-module-orders]]' for k in
        ['目的', 'スコープ', '用語', '全体像', '外部とのつながり', '制約と前提', '非機能の特徴', '未確定事項']})
    t.fill(root / 'specs' / 'cross-cutting.md', {k: 'なし' for k in
        ['認証と認可', 'エラー処理', 'ログと監視', '設定', 'トランザクションと整合性', '未確定事項']})
print(subprocess.run(tool + ['step', 'OK'], capture_output=True, text=True).stdout)
# 2 段目は断られる
print(subprocess.run(tool + ['step', 'OK'], capture_output=True, text=True).stdout)
"""


class RunTest(Base):
    def test_run_drives_fresh_agent_per_step(self):
        agent = Path(self.tmp.name) / "fake_agent.py"
        agent.write_text(FAKE_AGENT % {"tests": str(Path(__file__).resolve().parent)}, encoding="utf-8")
        self.set_config(confirm="auto", agents={"fake": [sys.executable, str(agent)]})
        out = self.rspec("run", "--agent", "fake", "--backoff", "0")
        st = self.state()
        self.assertEqual(st["state"], "done", out)
        # 段ごとに 1 回ずつ起こした（scope, units, draft×2, verify×2, synthesize）
        logs = list((self.out / ".rspec" / "logs").glob("*.log"))
        self.assertEqual(len(logs), 7, out)
        self.assertTrue(any("この実行で進める段は終わりました" in x.read_text(encoding="utf-8") for x in logs))

    def test_run_stops_for_human_and_on_stall(self):
        self.write_scope()
        self.rspec("step", "OK")
        (self.out / "extraction" / "units.txt").write_text(UNITS, encoding="utf-8")
        self.rspec("step", "OK")
        out = self.rspec("run", "--agent", "fake", code=3)
        self.assertIn("人の判断が要る段", out)
        self.rspec("step", "OK")
        self.set_config(agents={"idle": [sys.executable, "-c", "pass"]})
        out = self.rspec("run", "--agent", "idle", "--retries", "2", "--backoff", "0", code=4)
        self.assertIn("進まなかった", out)

    def test_dry_run_shows_preset(self):
        out = self.rspec("run", "--agent", "codex", "--dry-run")
        self.assertIn("codex exec", out)


if __name__ == "__main__":
    unittest.main()
