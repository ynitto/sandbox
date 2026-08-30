#!/usr/bin/env python3
"""局所修正モデルを **worker 専任**で置いたときの受入率を測る。

判定役は一切使わない（クラウドへ戻す決定に従い、測定対象から外す）。合否は決定的な
チェッカーが出すので、実行のたびに揺れない。agent-project も agent-flow も bus も
通さず、agent-ollama を本番と同じ argv・同じプロンプトで直接叩く。

本番との同一性で担保するもの:
  - argv: agents/ollama.json の write_args そのまま
  - プロンプト: flow-worker スキルの prompt.py（agent-flow が使う正典のビルダー）
  - 上限: agent-flow の agent_timeout 既定 600 秒。超過は fail

使い方: python3 worker_eval.py [--model qwen3.5:9b] [--repeat 3]
        [--tasks T1,T2,T3] [--wall 600]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402
VENV_PY = REPO / ".venv/bin/python"
# ハーネス自身が決定的チェッカー（pytest / probe）を回すときの実行系。実機（開発機）では
# リポジトリの .venv を使い、それが無い木——CI のクリーンなチェックアウト——では走っている
# インタプリタで代替する。**測定の道具が環境で落ちると、道具の壊れがモデルの数字として
# 残る**ので、チェッカーは venv の有無に依存させない（同じ受けは project_verify_eval.py に既にある）。
CHECK_PY = VENV_PY if VENV_PY.exists() else Path(sys.executable)
PROMPT_BUILDER = Path(os.environ.get(
    "FLOW_WORKER_PROMPT",
    str(Path.home() / ".claude/skills/flow-worker/scripts/prompt.py")))
# worktree はリポジトリの**外**に置く。中に作ると評価の残骸が作業ツリーを汚す。
WORK = Path(os.environ.get("WORKER_EVAL_DIR",
                           str(Path(tempfile.gettempdir()) / "agent-worker-eval")))
MODEL = "qwen3.5:9b"        # --model で上書き
CLI = "agent-ollama"        # --cli で上書き（agent-ollama | aider）
AGENT_POLICY = None          # None = agents/aider.json の本番設定をそのまま継承
NUM_PREDICT = 0             # --num-predict で上書き（0 = 上限なし。aider 経路のみ）
# ツールループの呼び出し回数上限（agent-ollama 経路のみ。0 = 宣言しない＝定義の write_args
# のまま）。制限付き実行案 §6 の「回数制限のみ」腕を引くための口。
# **環境変数（AGENT_MAX_TOOL_ROUNDS*）では引けない**——定義の write_args が
# `--max-rounds 12` を宣言していて、宣言は環境変数に勝つ（agentcore.limits の優先順）。
# 測定条件が定義の予算を黙って上書きしない設計なので、腕もここで宣言して台帳へ残す。
MAX_ROUNDS = 0
NUM_CTX = 0                 # --num-ctx で上書き（0 = Aider / model の既定）
SAMPLING: dict = {}         # --temperature / --top-p / --top-k で上書き（空 = 宣言しない）
RESAMPLE = 1                # --resample で上書き（1 = 引き直さない＝従来と同一の道）
# 実行方針（ハーネス構成）の腕。**台帳の軸として必ず残す**——呼び出しの形やプロンプトを
# 変えた測定を、変える前の数字と同じ表に並べないための識別子である（agent-herd 設計
# 2026-08-27 §9 の段 12・13、制限付き実行案 §3.5 と同じ軸名）。
# 値は「名前だけ」にしない: 名前ごとに**実際に効く設定**を持ち、名前が挙動を説明する。
HARNESS = "default"
HARNESS_COMMANDS_DIR = ""   # templates:<dir> の腕が使う宣言ディレクトリ
AIDER_VERSION = None
OLLAMA_API_BASE = os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434")
WALL_LIMIT = 600.0          # agent-flow の agent_timeout 既定
# 本番の argv は agents/ollama.json の write_args。**書き写さずに読む**——初版は
# ここへ literal を写していたが、定義側が予算を 30 → 12 へ絞った当日にずれた。
# 「ここを変えたら本番を測っていない」を人の注意力で守らせない（ずれても静かに
# 測定が別物になるだけで、誰も気づけない形の失敗だった）。
_FALLBACK_WRITE_ARGS = ["--think", "off", "--tools", "bash",
                        "--max-rounds", "12", "--command-timeout", "900"]


def load_write_args() -> "tuple[list[str], str]":
    """`agents/ollama.json` の `write_args` と、その出所を返す。

    定義が読めないときに黙って止めないのは、ハーネスが測定の道具だから——ただし
    どちらを使ったかは起動時に必ず表示する（測定条件を隠さない）。
    """
    try:
        spec = json.loads((REPO / "agents/ollama.json").read_text(encoding="utf-8"))
        args = spec.get("write_args")
        if isinstance(args, list) and args:
            return [str(a) for a in args], "agents/ollama.json"
    except (OSError, ValueError):
        pass
    return list(_FALLBACK_WRITE_ARGS), "fallback（定義を読めませんでした）"


WRITE_ARGS, WRITE_ARGS_SOURCE = load_write_args()

REPO_INSTRUCTION = (
    "作業ディレクトリはこのタスク専用の worktree。テストの実行には "
    f"`{CHECK_PY} -m pytest` を使うこと（この worktree に .venv は無い）。"
)

# ---------------------------------------------------------------- タスク定義
# 各タスクは seed（実行前の仕込み）と check（決定的な合否）を持つ。
# check は (ok: bool, note: str) を返す。


def _pytest(cwd: Path, *targets: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(CHECK_PY), "-m", "pytest", "-q", *targets],
                          cwd=cwd, capture_output=True, text=True, timeout=300)


def seed_t1(wt: Path) -> None:
    (wt / "eval").mkdir(exist_ok=True)


def check_t1min(wt: Path) -> tuple[bool, str]:
    """T1 からテスト追加の契約を外した版。**実装だけ**できるかを見る。

    「粒度を極小化すれば使えるのか」を切り分けるための最小セル。ここも落ちるなら
    タスクの割り方では救えない。"""
    ok, note = check_t1(wt, require_tests=False)
    return ok, note


def _probe_cases(wt: Path, module: str, func: str, cases: list) -> tuple[bool, str]:
    """仕様ケースを実際に呼んで突き合わせる。合否と**機械が出した不一致**を返す。

    チェッカーと多段セルのゲートが同じ 1 実装を見る（C7）。ゲートが返す不一致は
    課題文がすでに列挙している例そのものなので、仕様を機械化しただけで、
    一発版に無い情報を多段版へ足してはいない——ここがずれると比較が成立しない。
    """
    mod = wt / "eval" / f"{module}.py"
    if not mod.exists():
        return False, f"eval/{module}.py が無い"
    probe = wt / "_probe.py"
    probe.write_text(
        "import sys; sys.path.insert(0, 'eval')\n"
        f"from {module} import {func} as f\n"
        f"cases = {cases!r}\n"
        "bad = [(n, f(n), want) for n, want in cases if f(n) != want]\n"
        "print('BAD' if bad else 'OK', bad)\n", encoding="utf-8")
    r = subprocess.run([str(CHECK_PY), "_probe.py"], cwd=wt,
                       capture_output=True, text=True, timeout=60)
    probe.unlink(missing_ok=True)
    if r.returncode != 0:
        return False, f"import/実行が失敗: {r.stderr.strip().splitlines()[-1:] }"
    if not r.stdout.startswith("OK"):
        return False, f"振る舞いが仕様と違う: {r.stdout.strip()[:200]}"
    return True, f"仕様 {len(cases)} ケース pass"


HUMANSIZE_CASES = [(0, "0 B"), (512, "512 B"), (1024, "1.0 KiB"), (1536, "1.5 KiB"),
                   (1048576, "1.0 MiB"), (1073741824, "1.0 GiB")]
DURATION_CASES = [(0, "0s"), (45, "45s"), (600, "10m"), (3600, "1h"),
                  (5400, "1h30m"), (125, "2m5s"), (3630, "1h30s")]


def probe_humansize(wt: Path) -> tuple[bool, str]:
    return _probe_cases(wt, "humansize", "human_bytes", HUMANSIZE_CASES)


def probe_duration(wt: Path) -> tuple[bool, str]:
    """T4 のゲート。humansize と同型の省略規則（0 の単位は書かない）を別の関数で試す。"""
    return _probe_cases(wt, "duration", "format_duration", DURATION_CASES)


def check_t1(wt: Path, require_tests: bool = True) -> tuple[bool, str]:
    """仕様どおりの human_bytes があるか。実装は見ず、振る舞いだけ突き合わせる。"""
    ok, note = probe_humansize(wt)
    if not ok:
        return ok, note
    if not require_tests:
        return True, "仕様 6 ケース pass（テスト契約は課していない）"
    tests = [p for p in (wt / "eval").glob("test_*.py")
             if "human_bytes" in p.read_text(encoding="utf-8", errors="replace")]
    if not tests:
        return False, "テストが追加されていない（仕様どおり動くが契約は未達）"
    t = _pytest(wt, *[str(p.relative_to(wt)) for p in tests])
    if t.returncode != 0:
        return False, "追加されたテスト自体が落ちる"
    return True, f"仕様 6 ケース pass / テスト {len(tests)} ファイル pass"


def blind(gate) -> "callable":
    """合否は同じゲートで決めるが、**測った不一致は渡さない**対照用ラッパー。

    「1 回やり直させれば直る」のか「機械が測った値を見せて初めて直る」のかを分ける。
    前者なら、statemachine の検査に診断の受け渡しは要らない（真偽で足りる）。
    """
    def g(wt: Path) -> tuple[bool, str]:
        ok, note = gate(wt)
        return ok, note if ok else "仕様を満たしていない。実装を見直して直すこと。"
    return g


gate_blind = blind(probe_humansize)


def gate_humansize_tests(wt: Path) -> tuple[bool, str]:
    """テスト追加ステップのゲート。追加されたか・通るかだけを機械で見る。"""
    tests = [p for p in (wt / "eval").glob("test_*.py")
             if "human_bytes" in p.read_text(encoding="utf-8", errors="replace")]
    if not tests:
        return False, "eval/test_*.py に human_bytes のテストが追加されていない"
    t = _pytest(wt, *[str(p.relative_to(wt)) for p in tests])
    if t.returncode != 0:
        tail = [l for l in t.stdout.splitlines() if l.strip()][-3:]
        return False, f"追加されたテストが落ちる: {tail}"
    return True, f"テスト {len(tests)} ファイル pass"


BILLING_BUGGY = '''"""日割り課金の計算。"""


def prorate(monthly_fee: int, days_used: int, days_in_month: int) -> int:
    """日割り額を出す。端数は**切り上げ**（事業者側の仕様）。"""
    return monthly_fee * days_used // days_in_month


def invoice_total(items, discount_pct: int = 0) -> int:
    """明細の合計に割引率を **1 度だけ** 適用する。端数は切り捨て。"""
    total = 0
    for it in items:
        line = it["unit_price"] * it["qty"]
        total += line - line * discount_pct // 100
    return total
'''

BILLING_TEST = '''from billing import invoice_total, prorate


def test_prorate_full_month():
    assert prorate(3000, 30, 30) == 3000


def test_prorate_rounds_up():
    # 3000 * 10 / 31 = 967.74... → 切り上げて 968
    assert prorate(3000, 10, 31) == 968


def test_invoice_applies_discount_once_to_the_total():
    """割引は明細ごとではなく合計に 1 度だけ掛ける（明細ごとに丸めない）。"""
    items = [{"unit_price": 105, "qty": 1} for _ in range(3)]
    # 小計 315 に 10% を 1 度 → 315 - 31 = 284（明細ごとだと 285 になる）
    assert invoice_total(items, 10) == 284
'''


def seed_t2(wt: Path) -> None:
    d = wt / "eval"
    d.mkdir(exist_ok=True)
    (d / "billing.py").write_text(BILLING_BUGGY, encoding="utf-8")
    (d / "test_billing.py").write_text(BILLING_TEST, encoding="utf-8")


def check_t2(wt: Path) -> tuple[bool, str]:
    """テストを通したか。ただし**テストを書き換えて通したらズル**として落とす。"""
    tf = wt / "eval" / "test_billing.py"
    if not tf.exists():
        return False, "テストファイルが消えている"
    if hashlib.sha256(tf.read_bytes()).hexdigest() != hashlib.sha256(
            BILLING_TEST.encode("utf-8")).hexdigest():
        return False, "テストを書き換えて通した（ズル）"
    r = _pytest(wt / "eval", "test_billing.py")
    if r.returncode != 0:
        tail = [l for l in r.stdout.splitlines() if l.strip()][-1:]
        return False, f"テストが落ちたまま: {tail}"
    return True, "テスト無改変で 3 件 pass"


def gate_billing(wt: Path) -> tuple[bool, str]:
    """T2 のゲート。ズル検査を先に通し、pytest の落ちた尾を診断として返す。

    チェッカー check_t2 と同じ 2 点（無改変・pass）を見る（C7）。診断はテストの
    失敗出力そのもので、課題文が「テストが仕様の正」と言っている以上の情報は無い。
    """
    tf = wt / "eval" / "test_billing.py"
    if not tf.exists() or hashlib.sha256(tf.read_bytes()).hexdigest() != hashlib.sha256(
            BILLING_TEST.encode("utf-8")).hexdigest():
        return False, ("eval/test_billing.py を変更している。テストが仕様の正なので"
                       "元の内容へ戻し、eval/billing.py だけを直すこと。")
    r = _pytest(wt / "eval", "test_billing.py")
    if r.returncode != 0:
        tail = [l for l in r.stdout.splitlines() if l.strip()][-12:]
        return False, "テストが落ちている:\n" + "\n".join(tail)
    return True, "テスト 3 件 pass"


# ---------------------------------------------------------------- T5: 大きい参照材料（案 2 スライシング）
# 編集対象は小さい report.py だが、直すには 600 行級の bigmod.py の中に埋めた apply_tax の
# 単位（ベーシスポイント）を読み当てる必要がある。案 2（決定的コンテキスト・スライシング）が
# 測るのは「見るべき範囲を機械が先に確定してやると、見落としが減るか」。腕は read の渡し方だけ
# が違う: T5（bigmod 全文を --read）/ T5slice（関係シンボルの抜粋を --read）/ T5noread（渡さない）。

def _bigmod_source(pre: int = 27, post: int = 28) -> str:
    """決定的に生成するモジュール。関係する 2 関数は pre 個の詰め物の後（＝真ん中）に埋める。
    pre=27/post=28 で 570 行（T5）。pre=post=100 で 2 千行級（T6。約 2 万 token——
    「入れれば読める」が崩れるかを規模で測る腕）。"""
    lines = ['"""課金まわりの共通モジュール（合成）。', "",
             "多数のユーティリティが並ぶ。金額は円（int）。税率はベーシスポイント（bp）で扱う。",
             '"""', "", "TAX_BP_DEFAULT = 1000   # 10% = 1000 bp", ""]
    def filler(n: int) -> list:
        return [f"def util_{n:03d}(value: int, scale: int = {n + 1}) -> int:",
                f'    """合成ユーティリティ {n}。value を scale 倍して {n} を足す。',
                "", "    互換のため残している。呼び出し側は少ない。", '    """',
                f"    if value < 0:", f"        return -util_{n:03d}(-value, scale)",
                f"    return value * scale + {n}", "", ""]
    for n in range(1, pre + 1):
        lines += filler(n)
    lines += ["def prorate(monthly_fee: int, days_used: int, days_in_month: int) -> int:",
              '    """月額を日割りする。端数は切り上げ（円）。"""',
              "    return -(-monthly_fee * days_used // days_in_month)", "", "",
              "def apply_tax(amount: int, rate_bp: int) -> int:",
              '    """税込みにする。rate_bp は **ベーシスポイント**（1% = 100 bp、10% = 1000 bp）。',
              "",
              "    小数の税率（0.10 など）を渡すと 0 bp 扱いになり、税が付かない。",
              '    """',
              "    return amount + amount * int(rate_bp) // 10000", "", ""]
    for n in range(pre + 1, pre + post + 1):
        lines += filler(n)
    return "\n".join(lines) + "\n"


BIGMOD = _bigmod_source()
BIGMOD_XL = _bigmod_source(100, 100)
REPORT_BUGGY = (
    "from bigmod import prorate, apply_tax\n\n\n"
    "def invoice(monthly_fee: int, days_used: int, days_in_month: int, tax_rate: float = 0.10) -> int:\n"
    '    """日割り額に税を乗せた請求額（円）。tax_rate は小数（0.10 = 10%）。"""\n'
    "    net = prorate(monthly_fee, days_used, days_in_month)\n"
    "    return apply_tax(net, tax_rate)\n")
REPORT_TEST = (
    "from report import invoice\n\n\n"
    "def test_invoice_full_month_with_tax():\n    assert invoice(3000, 30, 30) == 3300\n\n\n"
    "def test_invoice_partial_month_rounds_up_then_tax():\n    assert invoice(3000, 1, 30) == 110\n\n\n"
    "def test_invoice_zero_tax():\n    assert invoice(3000, 30, 30, tax_rate=0) == 3000\n")
T5_REQUEST = "請求額の計算がおかしい"
T5_GOAL = ("eval/test_report.py が失敗している。eval/report.py の invoice を直して 3 件すべて通るように"
           "する。invoice の引数 tax_rate は小数（0.10 = 10%）のまま変えない（テストが仕様の正）。"
           "税の計算は bigmod.apply_tax を呼んで行うこと（自前で税を掛けない。apply_tax の引数の"
           "仕様は bigmod.py を読んで確かめる）。"
           "**eval/bigmod.py と eval/test_report.py は変更しないこと**。eval/ 以外も変更しない。")


def seed_t5(wt: Path, bigmod: str = BIGMOD) -> None:
    d = wt / "eval"
    d.mkdir(exist_ok=True)
    (d / "bigmod.py").write_text(bigmod, encoding="utf-8")
    (d / "report.py").write_text(REPORT_BUGGY, encoding="utf-8")
    (d / "test_report.py").write_text(REPORT_TEST, encoding="utf-8")


def seed_t6(wt: Path) -> None:
    seed_t5(wt, bigmod=BIGMOD_XL)


def _calls_apply_tax(source: str) -> bool:
    """report.py の invoice が apply_tax を呼んでいるか（ast）。自前で税を掛けて逃げる修正を落とす。"""
    import ast as _ast  # noqa: PLC0415
    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return False
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, _ast.Name) else getattr(fn, "attr", "")
            if name == "apply_tax":
                return True
    return False


def check_t6(wt: Path) -> tuple[bool, str]:
    return check_t5(wt, bigmod=BIGMOD_XL)


def check_t5(wt: Path, bigmod: str = BIGMOD) -> tuple[bool, str]:
    """テストを通したか。bigmod / テストを書き換えて通したらズル。apply_tax を使わずに自前で税を
    掛けた修正も落とす——それでもテストは通るが、参照材料（apply_tax の単位）を読んだかを測れなくなる
    （実測 2026-08-24: read なしの腕が `int(net * (1 + tax_rate))` で逃げて 3 件通した）。"""
    for name, body in (("bigmod.py", bigmod), ("test_report.py", REPORT_TEST)):
        f = wt / "eval" / name
        if not f.exists() or f.read_text(encoding="utf-8") != body:
            return False, f"{name} を書き換えた（ズル・対象外の変更）"
    report = wt / "eval" / "report.py"
    if not report.exists() or not _calls_apply_tax(report.read_text(encoding="utf-8")):
        return False, "apply_tax を呼ばずに税を自前計算した（参照材料を読まない逃げ）"
    r = _pytest(wt / "eval", "test_report.py")
    if r.returncode != 0:
        tail = [l for l in r.stdout.splitlines() if l.strip()][-1:]
        return False, f"テストが落ちたまま: {tail}"
    return True, "bigmod / テスト無改変・apply_tax 経由で 3 件 pass"


def seed_t3(wt: Path) -> None:
    pass


def check_t3_schema(wt: Path) -> tuple[bool, str]:
    """T3 の schema 成果物だけを判定する。一成果物/node arm の第1 gate。"""
    schema = wt / "schemas" / "node-budget-summary.schema.json"
    if not schema.exists():
        return False, "C1 fail: schemas/node-budget-summary.schema.json が無い"
    try:
        doc = json.loads(schema.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return False, f"C1 fail: schema が JSON として壊れている ({e})"
    if not isinstance(doc, dict) or "properties" not in doc:
        return False, "C1 fail: properties を持たない（JSON Schema になっていない）"
    return True, "C1 pass: node-budget-summary schema が有効"


def check_t3_contract(wt: Path) -> tuple[bool, str]:
    """T3 の新規契約テスト成果物だけを判定する。legacy T3/T3gate の C3 gate。"""
    tests = [p for p in (wt / "tools/agent-project").rglob("test_*.py")
             if _is_new(wt, p)]
    if not tests:
        return False, "C3 fail: 契約テストが追加されていない"
    r = _pytest(wt / "tools/agent-project", *[str(p) for p in tests])
    if r.returncode != 0:
        return False, "C3 fail: 追加された契約テストが落ちる"
    return True, f"C3 pass（テスト {len(tests)} ファイル）"


T3_CONTRACT_TEST = "tools/agent-project/tests/test_node_budget_summary_compat_eval.py"


def check_t3_split_contract(wt: Path) -> tuple[bool, str]:
    """分解 arm の第2 gate。planner が指定した単一成果物だけを判定する。"""
    test_file = wt / T3_CONTRACT_TEST
    if not test_file.exists() or not _is_new(wt, test_file):
        return False, f"C3 fail: {T3_CONTRACT_TEST} が追加されていない"
    r = _pytest(wt / "tools/agent-project", str(test_file))
    if r.returncode != 0:
        return False, "C3 fail: 追加された契約テストが落ちる"
    return True, "C3 pass（指定した契約テスト 1 ファイル）"


def check_t3(wt: Path) -> tuple[bool, str]:
    """本番で 4/4 fail した実タスク。receipt の C1・C3 を同じ順序で機械判定する。"""
    ok, note = check_t3_schema(wt)
    if not ok:
        return ok, note
    ok, note = check_t3_contract(wt)
    if not ok:
        return ok, note
    return True, f"C1 pass / {note}"


def _is_new(wt: Path, p: Path) -> bool:
    r = subprocess.run(["git", "ls-files", "--error-unmatch", str(p.relative_to(wt))],
                       cwd=wt, capture_output=True, text=True)
    return r.returncode != 0


T1_REQUEST = "agentcore の周辺ユーティリティを整える"
# 実装ステップの課題文。T1min（一発・実装のみ）と多段セルが**同じ 1 本**を見る。
# 多段が勝ったときに「文面を書き直したから勝った」を疑わなくて済む。
T1_IMPL_GOAL = ("eval/humansize.py というファイルを 1 つだけ作り、その中に関数 "
                "human_bytes(n: int) -> str を実装する。仕様は次のとおり: "
                "1024 未満は '512 B' のようにバイト表記、1024 以上は KiB / MiB / GiB へ"
                "1024 ごとに繰り上げ、小数第 1 位まで出す。"
                "例: human_bytes(0)=='0 B'、human_bytes(512)=='512 B'、"
                "human_bytes(1024)=='1.0 KiB'、human_bytes(1536)=='1.5 KiB'、"
                "human_bytes(1048576)=='1.0 MiB'、human_bytes(1073741824)=='1.0 GiB'。"
                "テストは書かなくてよい。このファイル以外は一切変更しない。")
T1_TEST_GOAL = ("既にある eval/humansize.py の human_bytes に対する単体テストを "
                "eval/test_humansize.py に追加する。実装は変更しない。"
                "テストは pytest で実行でき、すべて通ること。"
                "eval/test_humansize.py 以外は一切変更しない。")
# T4: humansize と別の関数・別のバグ族（文字列組み立ての省略規則）で、
# 「初回が決定的に同じ壊れ方をする」性質が課題固有かを測る。文面の作りは
# T1_IMPL_GOAL と同じ（単一ファイル・仕様例の列挙・テスト無し）。
T4_IMPL_GOAL = ("eval/duration.py というファイルを 1 つだけ作り、その中に関数 "
                "format_duration(n: int) -> str を実装する。仕様は次のとおり: "
                "秒数を h / m / s の単位へ分解し 'XhYmZs' の形で出す。値が 0 の単位は"
                "書かない。n が 0 のときだけ '0s' を返す。"
                "例: format_duration(0)=='0s'、format_duration(45)=='45s'、"
                "format_duration(600)=='10m'、format_duration(3600)=='1h'、"
                "format_duration(5400)=='1h30m'、format_duration(125)=='2m5s'、"
                "format_duration(3630)=='1h30s'。"
                "テストは書かなくてよい。このファイル以外は一切変更しない。")
T2_REQUEST = "課金計算の不具合を直す"
T2_GOAL = ("eval/test_billing.py が失敗している。eval/billing.py の実装を直して "
           "3 件すべて通るようにする。**テストファイルは変更しないこと**"
           "（テストが仕様の正）。eval/ 以外は変更しない。")
T3_REQUEST = ("node-budget-summary スキーマを追加し status/<node>.json へ埋め込む。"
              "Phase1 の前提である射影 schema を先に固定し、互換性テストで安全に出すため。")
T3_GOAL = ("schemas/node-budget-summary.schema.json を追加し、status/<node>.json の "
           "budget block に additive に埋める仕様を定義する。reader が optional で"
           "壊れない互換性を担保する契約テストを tools/agent-project 配下に追加する。"
           "変更してよいのは tools/agent-project 配下とリポジトリルートの schemas/ のみ。")
T3_SCHEMA_GOAL = ("成果物は schemas/node-budget-summary.schema.json の 1 ファイルだけ。"
                  "status/<node>.json の budget block に additive に埋める node-budget-summary "
                  "JSON Schema を定義する。他のファイルやテストは変更しない。")
T3_CONTRACT_GOAL = (f"成果物は {T3_CONTRACT_TEST} の 1ファイルだけ。既存の実装・schema・テストは"
                    "変更せず、status/<node>.json の budget block が additive で、budget を持たない"
                    "既存 reader も壊れないことを固定する契約テストを追加する。")

# ---------------------------------------------------------------- T7 / T8（テキスト成果物）
# ローカル LLM を agent-loop の定期プロンプトへ置けるかを、コード生成ではなく
# 「スキルの書式に従う要約」（tech-harvester のダイジェスト生成）と「ログの因果を遡る解析」
# で測る（2026-08-24 ユーザー要望）。フィード取得（ネットワーク）は評価に含めない——
# 測りたいのはステップ2（テーマ分け・日本語要約・書式遵守）で、取得はスクリプトが担う。

T7_ARTICLES = [
    {"feed": "AI Weekly", "feed_tags": ["ai"], "title": "Meta releases Llama 5 with 1M context window",
     "link": "https://example.com/ai/llama5-release",
     "description": "Meta announced Llama 5, extending the context window to one million tokens "
                    "and improving tool-use benchmarks by 18 percent.", "date": "Mon, 18 Aug 2026"},
    {"feed": "AI Weekly", "feed_tags": ["ai"], "title": "Study finds RAG pipelines degrade beyond 100k documents",
     "link": "https://example.com/ai/rag-degradation-study",
     "description": "A new benchmark shows retrieval quality drops sharply as corpus size grows, "
                    "unless hybrid rerankers are used.", "date": "Tue, 19 Aug 2026"},
    {"feed": "Security Feed", "feed_tags": ["security"], "title": "Critical RCE in OpenSSH 10.2 patched",
     "link": "https://example.com/sec/openssh-rce",
     "description": "Maintainers released 10.2p1 fixing a pre-auth remote code execution reachable "
                    "when GSSAPI is enabled.", "date": "Wed, 20 Aug 2026"},
    {"feed": "Security Feed", "feed_tags": ["security"], "title": "npm supply-chain attack hits 40 packages",
     "link": "https://example.com/sec/npm-supply-chain",
     "description": "Attackers published typosquatted packages that exfiltrate CI environment "
                    "variables during postinstall.", "date": "Wed, 20 Aug 2026"},
    {"feed": "Cloud Blog", "feed_tags": ["cloud"], "title": "AWS cuts S3 Glacier retrieval pricing by 40%",
     "link": "https://example.com/cloud/s3-glacier-pricing",
     "description": "The price cut applies to bulk retrievals in all regions starting September.",
     "date": "Thu, 21 Aug 2026"},
    {"feed": "Cloud Blog", "feed_tags": ["cloud"], "title": "Kubernetes 1.34 promotes in-place pod resize to GA",
     "link": "https://example.com/cloud/k8s-134-inplace-resize",
     "description": "Pods can now change CPU and memory without restart, closing a long-standing "
                    "gap for stateful workloads.", "date": "Fri, 22 Aug 2026"},
]

T7_REQUEST = ("tech-harvester スキルの出力フォーマットで、取得済み記事から日本語要約付きの"
              "ダイジェスト Markdown を作る。定期プロンプト（ローカル LLM）で回せるかの適性測定。")
T7_GOAL = ("`.github/skills/tech-harvester/SKILL.md` の「出力フォーマット」に従い、"
           "eval/articles.json の記事から eval/digest.md を生成する。"
           "フィード取得（ステップ1）は済んでいて eval/articles.json がその出力。"
           "やることはステップ2だけ: 記事内容からテーマを 2 つ以上作って記事をグループ化し、"
           "各記事を `### [記事タイトル](URL)` の見出しと日本語 1〜2 文の要約で載せる。"
           "全 6 記事を漏れなく載せ、URL は articles.json のものをそのまま使う。"
           "eval/ 以外は変更しない。")


def seed_t7(wt: Path) -> None:
    d = wt / "eval"
    d.mkdir(exist_ok=True)
    (d / "articles.json").write_text(json.dumps(
        {"generated_at": "2026-08-24 09:00 UTC", "total": len(T7_ARTICLES),
         "articles": T7_ARTICLES}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check_t7(wt: Path) -> tuple[bool, str]:
    """決定的チェッカー: 書式（# Tech Digest / テーマ ## が 2+）・リンク網羅・日本語要約。

    要約の質は測らない（LLM 判定は置かない決定に従う）。「全記事に日本語の文が付いて
    いるか」までを機械で見る——ここが落ちる=定期プロンプトの成果物として使えない。"""
    import re as _re  # noqa: PLC0415
    ja = _re.compile(r"[ぁ-ゖァ-ヶ]")
    f = wt / "eval" / "digest.md"
    if not f.exists():
        return False, "eval/digest.md が作られていない"
    text = f.read_text(encoding="utf-8")
    if "# Tech Digest" not in text:
        return False, "見出し '# Tech Digest' が無い（出力フォーマット不遵守）"
    missing = [a["link"] for a in T7_ARTICLES if a["link"] not in text]
    if missing:
        return False, f"リンク未掲載 {len(missing)} 件（例: {missing[0]}）"
    lines = text.splitlines()
    themes = [ln for ln in lines if ln.startswith("## ") and "注目キーワード" not in ln]
    if len(themes) < 2:
        return False, f"テーマ見出し（##）が {len(themes)} 件（2 件以上必要）"
    for a in T7_ARTICLES:
        idx = next(i for i, ln in enumerate(lines) if a["link"] in ln)
        seg: list[str] = [lines[idx]]
        for ln in lines[idx + 1:]:
            if ln.startswith("#"):
                break
            seg.append(ln)
        if not ja.search(" ".join(seg)):
            return False, f"日本語要約が無い: {a['title'][:40]}"
    return True, f"全 {len(T7_ARTICLES)} 記事・テーマ {len(themes)} 件・日本語要約つき"


# --- T7 / T8 の手続最適化版（ステートマシン化の前処理を写した多段セル）
# 一発版（T7digest / T8log）が落ちても、agent-loop の statemachine と同じ
# 「狭い state + 決定的ゲート + 限定 retry」へ分解すれば回るのかを測る対。
# run_steps がその写像（決定的遷移・診断つき再投入・自己申告不採用）を担う。

T7_THEMES_GOAL = ("eval/articles.json の全 6 記事を内容から 2 つ以上のテーマへ分類し、"
                  'eval/themes.json に {"テーマ名": ["URL", ...]} の JSON で書く。'
                  "URL は articles.json の link をそのまま使い、全記事をちょうど 1 回ずつ"
                  "どこかのテーマへ入れる。テーマ名は日本語。JSON 以外は書かない。"
                  "eval/ 以外は変更しない。")
T7_RENDER_GOAL = ("eval/themes.json のテーマ分けに従い、"
                  "`.github/skills/tech-harvester/SKILL.md` の「出力フォーマット」で "
                  "eval/digest.md を生成する。テーマごとに `## テーマ名`、各記事は "
                  "`### [記事タイトル](URL)` の見出しと日本語 1〜2 文の要約。"
                  "URL は eval/articles.json のものをそのまま使い、全 6 記事を漏れなく載せる。"
                  "eval/ 以外は変更しない。")


def gate_t7_themes(wt: Path) -> tuple[bool, str]:
    """step1 のゲート: themes.json が「全記事をちょうど 1 回・テーマ 2+」を満たすか。"""
    f = wt / "eval" / "themes.json"
    if not f.exists():
        return False, "eval/themes.json が作られていない"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        return False, f"JSON として読めない: {e}"
    if not isinstance(data, dict) or len(data) < 2:
        return False, "テーマが 2 つ未満（{テーマ名: [URL, ...]} の dict が必要）"
    assigned: list = []
    for theme, links in data.items():
        if not isinstance(links, list):
            return False, f"テーマ {theme!r} の値が配列ではない"
        assigned += [str(u) for u in links]
    expected = [a["link"] for a in T7_ARTICLES]
    missing = [u for u in expected if u not in assigned]
    if missing:
        return False, f"未割当の記事 {len(missing)} 件（例: {missing[0]}）"
    dupes = [u for u in expected if assigned.count(u) > 1]
    if dupes:
        return False, f"複数テーマへ重複割当（例: {dupes[0]}）"
    extra = [u for u in assigned if u not in expected]
    if extra:
        return False, f"articles.json に無い URL（例: {extra[0]}）"
    return True, f"テーマ {len(data)} 件・全 {len(expected)} 記事を 1 回ずつ割当"


T8_EVIDENCE_GOAL = ("eval/service.log から、時系列で**最初に現れた ERROR 行**を探し、"
                    "eval/evidence.md の 1 行目にその行を**そのまま**引用する。"
                    "2 行目以降に「この行が後続のエラーの起点である理由」を日本語 1〜2 文で書く。"
                    "eval/ 以外は変更しない。")


def gate_t8_evidence(wt: Path) -> tuple[bool, str]:
    """step1 のゲート: 起点の ERROR 行（ディスク枯渇）へ到達したか。"""
    f = wt / "eval" / "evidence.md"
    if not f.exists():
        return False, "eval/evidence.md が作られていない"
    text = f.read_text(encoding="utf-8")
    if "No space left on device" not in text or "payments-db" not in text:
        return False, ("起点の ERROR 行を引用していない"
                       "（時系列で最初の ERROR を探す。timeout は後続の波及）")
    return True, "起点 ERROR（payments-db のディスク枯渇）を引用"


T8_LOG_LINES = []
for _m in range(0, 12):
    T8_LOG_LINES.append(f"2026-08-24T09:{_m:02d}:10Z INFO  api-gateway: health check ok (upstreams: 4/4)")
    T8_LOG_LINES.append(f"2026-08-24T09:{_m:02d}:40Z INFO  checkout-service: processed 1{_m} orders")
T8_LOG_LINES += [
    "2026-08-24T09:12:03Z WARN  auth-service: config key 'session_ttl' is deprecated, use 'session_ttl_sec'",
    "2026-08-24T09:14:10Z ERROR payments-db: write failed: No space left on device (/var/lib/postgresql, disk usage 100%)",
    "2026-08-24T09:14:11Z ERROR payments-db: checkpoint failure, database is read-only until space is freed",
]
for _s in range(15, 55, 5):
    T8_LOG_LINES += [
        f"2026-08-24T09:14:{_s}Z ERROR api-gateway: upstream timeout to payments-db:5432 after 5000ms",
        f"2026-08-24T09:14:{_s + 2}Z ERROR checkout-service: POST /orders -> 500 (payment persistence failed)",
    ]
T8_LOG_LINES.append("2026-08-24T09:15:20Z INFO  auth-service: login rate normal (42 rpm)")
T8_LOG = "\n".join(T8_LOG_LINES) + "\n"

T8_REQUEST = ("障害ログを解析して根本原因を報告する。定期プロンプト（ローカル LLM）で"
              "ログ監視・一次切り分けを回せるかの適性測定。")
T8_GOAL = ("eval/service.log を読み、障害の根本原因を特定して eval/analysis.md に日本語で"
           "まとめる。書式: `## 根本原因` セクションに原因となったサービス名と直接原因を明記し、"
           "`## 波及` セクションに他サービスへの影響を時系列で書く。症状（タイムアウトや 500）と"
           "原因を取り違えないこと。eval/ 以外は変更しない。")


def seed_t8(wt: Path) -> None:
    d = wt / "eval"
    d.mkdir(exist_ok=True)
    (d / "service.log").write_text(T8_LOG, encoding="utf-8")


def check_t8(wt: Path) -> tuple[bool, str]:
    """決定的チェッカー: 根本原因（payments-db のディスク枯渇）へ到達したか。

    タイムアウトだけを見た解析は disk のトークンへ到達できない——「症状で止まったか、
    因果を 1 段遡れたか」がこの 1 点で機械判定できる。囮（auth-service の deprecated
    警告）を根本原因にしたら落とす。"""
    import re as _re  # noqa: PLC0415
    f = wt / "eval" / "analysis.md"
    if not f.exists():
        return False, "eval/analysis.md が作られていない"
    text = f.read_text(encoding="utf-8")
    if not _re.search(r"[ぁ-ゖァ-ヶ]", text):
        return False, "日本語で書かれていない"
    m = _re.search(r"##\s*根本原因(.*?)(?=\n##\s|\Z)", text, _re.S)
    if m is None:
        return False, "`## 根本原因` セクションが無い（書式不遵守）"
    cause = m.group(1)
    if "payments-db" not in cause:
        return False, "根本原因に payments-db が挙がっていない"
    if not _re.search(r"No space left|disk|ディスク|空き容量|容量", cause, _re.I):
        return False, "ディスク枯渇へ到達していない（症状で止まっている）"
    if "auth-service" in cause:
        return False, "囮（auth-service の deprecated 警告）を根本原因に含めている"
    if "## 波及" not in text:
        return False, "`## 波及` セクションが無い（書式不遵守）"
    return True, "根本原因=payments-db のディスク枯渇へ到達・書式遵守"


# 失敗の族（gate-generality 2026-08-14 / 計画 2026-08-22 §4.2 A1）。
#   (a) 仕様の読み違い族 —— 真偽ゲート + 再投入で直る。T1* / T2* / T4*。
#   (b) 作業の丸ごと欠落族 —— 成果物が 2 つ以上で、片方を一度も作らない。T3 / T3gate
#       （9 attempt が同文 `C3 fail: 契約テストが追加されていない` で 0/3）。
# 引き直し（--resample）が拾えるのは**揺れる**失敗で、(a) で観測された（P10）。(b) を
# 引き直した記録は無い。escalate 率は族を分けて読む——混ぜると「下がらなかった」の原因が
# 引き直しなのか (b) なのか分離できない。(b) が動かないときの答えは引き直しではなく、
# 成果物を 1 つに割ること（nodecontract.local_patch_blockers の適格条件を満たす形へ）。
# 機械分割 arm の入力: 人が書くのは「成果物のスロット」だけで、手順の文面は書かない。
T3_OPERATION = {
    "operation_class": "feature",
    "scope": {"read": ["tools/agent-project", "schemas"],
              "write": ["schemas/node-budget-summary.schema.json", T3_CONTRACT_TEST]},
    "deliverables": ["schemas/node-budget-summary.schema.json", T3_CONTRACT_TEST],
    "verification": {"commands": [[str(CHECK_PY), "-m", "pytest", "-q", "tools/agent-project"]]},
}
# 本番の分割器が返すスロット（agentcore が無い木では None → arm ごと出さない）。
_T3_AUTOSPLIT = engine.split_by_deliverables(
    {"id": "t3", "kind": "work", "goal": T3_GOAL, "operation": T3_OPERATION}) or []

TASKS = {
    "T1": dict(
        family="a",
        seed=seed_t1, check=check_t1,
        files=("eval/humansize.py", "eval/test_humansize.py"),
        test_cmd=f"{CHECK_PY} -m pytest -q eval",
        request=T1_REQUEST,
        goal=("eval/humansize.py に関数 human_bytes(n: int) -> str を実装する。"
              "1024 未満は '512 B' のようにバイト表記、以降は KiB / MiB / GiB へ丸め、"
              "小数第 1 位まで出す（例: 1024 -> '1.0 KiB'、1536 -> '1.5 KiB'、"
              "1048576 -> '1.0 MiB'）。あわせて eval/test_*.py に単体テストを追加し、"
              "テストが通ることを確認する。eval/ 以外は変更しない。"),
    ),
    "T1min": dict(
        family="a",
        seed=seed_t1, check=check_t1min,
        files=("eval/humansize.py",),
        request=T1_REQUEST, goal=T1_IMPL_GOAL,
    ),
    # --- T1 の多段版（tier:basic 向けの「事前分解」が効くかを測る 2 本）
    # 一発版 T1 は同じ check で 0/9。goal の文面は T1/T1min と同じ材料しか使わない。
    # 実装ステップだけを使った対照 2 本。再試行の回数は同じ 1 回で、**渡す材料だけ**が違う。
    # 一発の T1min（再試行なし）が基準線。
    "T1impl_diag": dict(
        family="a",
        seed=seed_t1, check=check_t1min, request=T1_REQUEST,
        steps=[dict(request=T1_REQUEST, goal=T1_IMPL_GOAL, files=("eval/humansize.py",),
                    gate=probe_humansize, max_retries=1)],
    ),
    "T1impl_blind": dict(
        family="a",
        seed=seed_t1, check=check_t1min, request=T1_REQUEST,
        steps=[dict(request=T1_REQUEST, goal=T1_IMPL_GOAL, files=("eval/humansize.py",),
                    gate=gate_blind, max_retries=1)],
    ),
    # ゲートは付くが `max_retries=0` なので**一度も作用しない**（呼び出し回数は T1seq と
    # 同じ 2 回）。合否を分けずに「どの手順で壊れたか」だけを台帳へ残すための観測。
    "T1seq": dict(
        family="a",
        seed=seed_t1, check=check_t1, request=T1_REQUEST,
        steps=[dict(request=T1_REQUEST, goal=T1_IMPL_GOAL, files=("eval/humansize.py",),
                    gate=probe_humansize, max_retries=0),
               dict(request=T1_REQUEST, goal=T1_TEST_GOAL,
                    files=("eval/test_humansize.py",), read=("eval/humansize.py",),
                    test_cmd=f"{CHECK_PY} -m pytest -q eval",
                    gate=gate_humansize_tests, max_retries=0)],
    ),
    "T1gate": dict(
        family="a",
        seed=seed_t1, check=check_t1, request=T1_REQUEST,
        steps=[dict(request=T1_REQUEST, goal=T1_IMPL_GOAL, files=("eval/humansize.py",),
                    gate=probe_humansize, max_retries=2),
               dict(request=T1_REQUEST, goal=T1_TEST_GOAL,
                    files=("eval/test_humansize.py",), read=("eval/humansize.py",),
                    test_cmd=f"{CHECK_PY} -m pytest -q eval",
                    gate=gate_humansize_tests, max_retries=1)],
    ),
    "T2": dict(
        family="a",
        seed=seed_t2, check=check_t2,
        # テストは仕様の正なので読み取り専用で渡す（書き換えはチェッカーがズルとして落とす）。
        files=("eval/billing.py",), read=("eval/test_billing.py",),
        # aider 経路でだけ使う（--test-cmd + --auto-test）。agent-ollama 経路は
        # プロンプトでテスト実行を指示しており、道具の作法がそれぞれ違う。
        test_cmd=f"{CHECK_PY} -m pytest -q eval/test_billing.py",
        request=T2_REQUEST, goal=T2_GOAL,
    ),
    "T3": dict(
        family="b",
        seed=seed_t3, check=check_t3,
        # 実タスクなので置き場所の探索が要る。ここだけリポジトリマップに予算を与える。
        files=("schemas/node-budget-summary.schema.json",), map_tokens=1024,
        test_cmd=f"{CHECK_PY} -m pytest -q tools/agent-project",
        request=T3_REQUEST, goal=T3_GOAL,
    ),
    # --- 追試（分解レポート 2026-08-13 の未検証を潰すアーム群）
    # (1) ゲート + 再投入の一般化: バグ修正（T2*）・実課題（T3gate）・別実装課題（T4*）
    # (2) auto-test 交絡の切り分け: T2noat は --auto-test 無しの素の一発
    # (3) 「初回が決定的に同じ壊れ方」の固有性: T4 初回の gate_note を突き合わせる
    # ゲート付き T2 アームは test_cmd を持たない（aider 内部のオラクルを切り、
    # ハーネスのゲートだけを検査に残す）。
    "T2noat": dict(
        family="a",
        seed=seed_t2, check=check_t2,
        files=("eval/billing.py",), read=("eval/test_billing.py",),
        request=T2_REQUEST, goal=T2_GOAL,
    ),
    "T2gate": dict(
        family="a",
        seed=seed_t2, check=check_t2, request=T2_REQUEST,
        steps=[dict(request=T2_REQUEST, goal=T2_GOAL,
                    files=("eval/billing.py",), read=("eval/test_billing.py",),
                    gate=gate_billing, max_retries=2)],
    ),
    "T2blind": dict(
        family="a",
        seed=seed_t2, check=check_t2, request=T2_REQUEST,
        steps=[dict(request=T2_REQUEST, goal=T2_GOAL,
                    files=("eval/billing.py",), read=("eval/test_billing.py",),
                    gate=blind(gate_billing), max_retries=2)],
    ),
    "T4min": dict(
        family="a",
        seed=seed_t1, check=probe_duration,
        files=("eval/duration.py",),
        request=T1_REQUEST, goal=T4_IMPL_GOAL,
    ),
    "T4gate": dict(
        family="a",
        seed=seed_t1, check=probe_duration, request=T1_REQUEST,
        steps=[dict(request=T1_REQUEST, goal=T4_IMPL_GOAL, files=("eval/duration.py",),
                    gate=probe_duration, max_retries=2)],
    ),
    "T4blind": dict(
        family="a",
        seed=seed_t1, check=probe_duration, request=T1_REQUEST,
        steps=[dict(request=T1_REQUEST, goal=T4_IMPL_GOAL, files=("eval/duration.py",),
                    gate=blind(probe_duration), max_retries=2)],
    ),
    # 実課題でゲート + 再投入が効くか。argv は一発版 T3 と同一（auto-test 込み）で、
    # 差はゲートと再試行だけ。診断はチェッカーの C1/C3 fail 文そのもの。
    # --- 案 2（決定的コンテキスト・スライシング）の A/B。差は --read の渡し方だけ。
    "T5": dict(
        family="a",
        seed=seed_t5, check=check_t5, read_mode="whole",
        files=("eval/report.py",), read=("eval/bigmod.py",),
        test_cmd=f"{CHECK_PY} -m pytest -q eval/test_report.py",
        request=T5_REQUEST, goal=T5_GOAL,
    ),
    "T5slice": dict(
        family="a",
        seed=seed_t5, check=check_t5, read_mode="slice",
        files=("eval/report.py",), read=("eval/bigmod.py",),
        # 抜粋するシンボルは「編集対象が参照している名前」——計画時に機械で決まる（import 行）。
        slice={"eval/bigmod.py": ("apply_tax", "prorate")},
        test_cmd=f"{CHECK_PY} -m pytest -q eval/test_report.py",
        request=T5_REQUEST, goal=T5_GOAL,
    ),
    # T6: T5 と同じ課題・材料だけ 2 千行級（約 2 万 token）。「入れれば読める」が規模で
    # 崩れるか（MRCR の帯へ近づける）。--num-ctx 32768 を宣言して回す。
    "T6": dict(
        family="a",
        seed=seed_t6, check=check_t6, read_mode="whole",
        files=("eval/report.py",), read=("eval/bigmod.py",),
        test_cmd=f"{CHECK_PY} -m pytest -q eval/test_report.py",
        request=T5_REQUEST, goal=T5_GOAL,
    ),
    "T6slice": dict(
        family="a",
        seed=seed_t6, check=check_t6, read_mode="slice",
        files=("eval/report.py",), read=("eval/bigmod.py",),
        slice={"eval/bigmod.py": ("apply_tax", "prorate")},
        test_cmd=f"{CHECK_PY} -m pytest -q eval/test_report.py",
        request=T5_REQUEST, goal=T5_GOAL,
    ),
    # T6noat / T6slicenoat: auto-test（失敗出力の往復）を切った一発。T6 が 3/3 なのは
    # 「入れれば読めた」のか「テストの失敗出力が探索を代行した」のかを切り分ける——
    # 見落とし面積の縮小（案 2 の主目的）は、フィードバック無しの一発でしか測れない。
    "T6noat": dict(
        family="a",
        seed=seed_t6, check=check_t6, read_mode="whole",
        files=("eval/report.py",), read=("eval/bigmod.py",),
        request=T5_REQUEST, goal=T5_GOAL,
    ),
    "T6slicenoat": dict(
        family="a",
        seed=seed_t6, check=check_t6, read_mode="slice",
        files=("eval/report.py",), read=("eval/bigmod.py",),
        slice={"eval/bigmod.py": ("apply_tax", "prorate")},
        request=T5_REQUEST, goal=T5_GOAL,
    ),
    "T5noread": dict(
        family="a",
        seed=seed_t5, check=check_t5, read_mode="none",
        files=("eval/report.py",),
        test_cmd=f"{CHECK_PY} -m pytest -q eval/test_report.py",
        request=T5_REQUEST, goal=T5_GOAL,
    ),
    "T3gate": dict(
        family="b",
        seed=seed_t3, check=check_t3, request=T3_REQUEST,
        steps=[dict(request=T3_REQUEST, goal=T3_GOAL,
                    files=("schemas/node-budget-summary.schema.json",), map_tokens=1024,
                    test_cmd=f"{CHECK_PY} -m pytest -q tools/agent-project",
                    gate=check_t3, max_retries=2)],
    ),
    # 丸ごと欠落族（b）への答えは引き直しではなく成果物を割ること（README §「次の arm」）。
    # T3gate の 1 手順を「schema 1ファイル」「契約テスト 1ファイル」の 2 手順へ分け、各手順の
    # 直後に C1 / C3 の決定的 checker を置いて、落ちた手順だけを有界再投入する。
    # **seed と最終 checker は T3gate と共通**——変えるのは成果物の粒度と gate の位置だけで、
    # ほかを動かすと T3gate との同条件比較にならない（それが測りたい唯一の差である）。
    # 機械が割った arm（計画 2026-08-29 §2）。**人が書いた per-step goal を使わない**——
    # 宣言するのは処理契約の deliverables（成果物スロット）だけで、割り方は本番の
    # agentcore.nodecontract.split_by_deliverables に書かせる。seed・最終 checker・gate の
    # 位置は T3splitgate と同一で、変えるのは「割り方を人が書いたか機械が書いたか」だけ。
    **({"T3autosplit": dict(
        family="b",
        seed=seed_t3, check=check_t3, request=T3_REQUEST,
        steps=[dict(request=T3_REQUEST, goal=slot["goal"],
                    files=(slot["operation"]["deliverables"][0],), map_tokens=1024,
                    test_cmd=f"{CHECK_PY} -m pytest -q tools/agent-project",
                    gate=gate, max_retries=2)
               for slot, gate in zip(_T3_AUTOSPLIT,
                                     (check_t3_schema, check_t3_split_contract))])}
       if len(_T3_AUTOSPLIT) == 2 else {}),
    "T3splitgate": dict(
        family="b",
        seed=seed_t3, check=check_t3, request=T3_REQUEST,
        steps=[dict(request=T3_REQUEST, goal=T3_SCHEMA_GOAL,
                    files=("schemas/node-budget-summary.schema.json",), map_tokens=1024,
                    test_cmd=f"{CHECK_PY} -m pytest -q tools/agent-project",
                    gate=check_t3_schema, max_retries=2),
               dict(request=T3_REQUEST, goal=T3_CONTRACT_GOAL,
                    files=(T3_CONTRACT_TEST,), map_tokens=1024,
                    test_cmd=f"{CHECK_PY} -m pytest -q tools/agent-project",
                    gate=check_t3_split_contract, max_retries=2)],
    ),
    # --- ローカル LLM の定常業務適性（テキスト成果物 2 本。2026-08-24 ユーザー要望）
    # 走らせ方: python3 worker_eval.py --cli aider --model gemma4:e4b --tasks T7digest,T8log --repeat 3
    "T7digest": dict(
        family="a",
        seed=seed_t7, check=check_t7,
        files=("eval/digest.md",),
        read=("eval/articles.json", ".github/skills/tech-harvester/SKILL.md"),
        request=T7_REQUEST, goal=T7_GOAL,
    ),
    "T8log": dict(
        family="a",
        seed=seed_t8, check=check_t8,
        files=("eval/analysis.md",),
        read=("eval/service.log",),
        request=T8_REQUEST, goal=T8_GOAL,
    ),
    # 手続最適化版（ステートマシン化の前処理を写した多段セル）。一発版との差は
    # 「狭い state への分解 + 決定的ゲート + 診断つき再投入」だけで、最終チェッカーは同一。
    # 一発が落ちてこちらが通るなら、agent-loop では statemachine 経由で使える。
    "T7gate": dict(
        family="a",
        seed=seed_t7, check=check_t7, request=T7_REQUEST,
        steps=[dict(request=T7_REQUEST, goal=T7_THEMES_GOAL,
                    files=("eval/themes.json",), read=("eval/articles.json",),
                    gate=gate_t7_themes, max_retries=2),
               dict(request=T7_REQUEST, goal=T7_RENDER_GOAL,
                    files=("eval/digest.md",),
                    read=("eval/articles.json", "eval/themes.json",
                          ".github/skills/tech-harvester/SKILL.md"),
                    gate=check_t7, max_retries=2)],
    ),
    "T8gate": dict(
        family="a",
        seed=seed_t8, check=check_t8, request=T8_REQUEST,
        steps=[dict(request=T8_REQUEST, goal=T8_EVIDENCE_GOAL,
                    files=("eval/evidence.md",), read=("eval/service.log",),
                    gate=gate_t8_evidence, max_retries=2),
               dict(request=T8_REQUEST,
                    goal=T8_GOAL + " eval/evidence.md に引用した起点の ERROR 行を"
                         "根本原因の根拠として使う。",
                    files=("eval/analysis.md",),
                    read=("eval/service.log", "eval/evidence.md"),
                    gate=check_t8, max_retries=2)],
    ),
}

# ---------------------------------------------------------------- 実行


def build_prompt(task: dict) -> str:
    payload = {
        "role": "worker", "kind": "work", "goal": task["goal"],
        "request": task["request"], "deps": {},
        "repo_instruction": REPO_INSTRUCTION, "artifact_note": "",
        "workspace": {}, "references": [], "instructions": "",
        "repair_note": "", "read_note": "",
    }
    r = subprocess.run([sys.executable, str(PROMPT_BUILDER)],
                       input=json.dumps(payload, ensure_ascii=False),
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"prompt.py が失敗: {r.stderr[:300]}")
    return r.stdout.strip()


def aider_settings(model: str, num_ctx: "int | None" = None, num_predict: int = 0,
                   sampling: "dict | None" = None, base: bool = True) -> Path:
    """aider へ渡すモデル設定（文脈・1 ターンの生成上限・sampling）。

    aider の直し直しは 3 回で止まる（`max_reflections`・CLI フラグは無い）ので、壁時計を
    焼くのは回数ではなく **1 ターンの生成の長さ**である——実測で最後のターンが受信 3.7k
    トークン、26.5 tok/s で約 140 秒。`num_predict` はそこへ効く上限で、**失敗を安く切る**
    ためのレバー。合否そのものは変わらない（途中で切られた編集は適用されず fail になる）。

    `base=False` は**指定されたものだけ**を書く。sampling の腕はこれを使う——`base=True`
    は `edit_format` / `use_repo_map` / `num_ctx` も一緒に書くので、それで sampling を
    測ると「温度を変えた」ではなく「温度と文脈長と編集形式を変えた」を測ることになる。
    **1 度に 1 つだけ変える**（README の測定規律）を設定ファイルの粒度でも守るための口。
    """
    path = WORK / "aider.model.settings.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"- name: ollama_chat/{model}"]
    if base:
        lines += ["  edit_format: diff", "  use_repo_map: false"]
    params = []
    # num_ctx は base では既定 32768、base=False では**明示されたときだけ**書く
    # （「指定されたものだけを書く」の規律のまま、sampling 腕でも文脈長を宣言できる口）。
    if base:
        params.append(f"    num_ctx: {num_ctx if num_ctx else 32768}")
    elif num_ctx:
        params.append(f"    num_ctx: {num_ctx}")
    if num_predict > 0:
        params.append(f"    num_predict: {num_predict}")
    # sampling は宣言したものだけ。未宣言のキーは書かない = aider / ollama の既定のまま。
    for key in ("temperature", "top_p", "top_k"):
        value = (sampling or {}).get(key)
        if value is not None:
            params.append(f"    {key}: {value}")
    if params:
        lines.append("  extra_params:")
        lines += params
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def ollama_argv() -> "list[str]":
    """agent-ollama を worker として 1 回だけ回す argv（`agents/ollama.json` を読んで組む）。

    定義に無いのは呼び出し回数上限の腕（`--max-rounds`）だけ。**環境変数では引けない**
    ——定義の `write_args` が既に `--max-rounds` を宣言していて、宣言は環境変数に勝つ
    （`agentcore.limits` の優先順）。測定条件が運用の宣言を黙って上書きしない設計なので、
    腕はここで宣言して台帳へ残す。
    """
    args = list(WRITE_ARGS)
    if MAX_ROUNDS > 0:
        # 定義側の宣言を**消してから**置き換える。同じフラグを 2 回並べて後勝ちに賭けると、
        # 定義が並び順を変えた日に静かに元へ戻る（aider の --map-tokens で踏んだ罠）。
        while "--max-rounds" in args:
            index = args.index("--max-rounds")
            del args[index:index + 2]
        args += ["--max-rounds", str(MAX_ROUNDS)]
    return ["agent-ollama", MODEL, *args]


def aider_argv(task: dict) -> "list[str]":
    """aider を worker として 1 回だけ回す argv（`agents/aider.json` を読んで組む）。

    定義に無いのは 2 つだけ——テストのある課題の `--test-cmd` + `--auto-test`（課題ごとに
    違う）と、探索が要る課題の `--map-tokens`（定義は 0 で固定し、必要な課題だけ上書く）。
    ここを外すと aider を編集器としてしか測らないことになる。
    """
    # 起動形は `agents/aider.json` を**読む**（写さない）。ファイルの受け渡しも定義の
    # `file_flag` / `read_flag` に従う。エンジンへの参照は engine.py に閉じてある。
    built = engine.headless_cmd("aider", MODEL, task["goal"],
                                files=task.get("files") or (),
                                read_files=task.get("read") or ())
    argv = built["argv"]
    extra = []
    policy_flag = "--agent-policy"
    if AGENT_POLICY is not None:
        while policy_flag in argv:
            index = argv.index(policy_flag)
            del argv[index:index + 2]
        if AGENT_POLICY != "off":
            extra += [policy_flag, AGENT_POLICY]
    if NUM_PREDICT > 0:
        extra += ["--agent-num-predict", str(NUM_PREDICT)]
    if NUM_CTX > 0 and not SAMPLING:
        extra += ["--agent-num-ctx", str(NUM_CTX)]
    if SAMPLING:
        # sampling だけの腕では base を書かない（上の docstring 参照——余計な差を
        # 一緒に入れると、測っているものが「温度の効果」でなくなる）。--num-ctx を併用する
        # 腕では同じ settings ファイルへ寄せる（adapter の managed settings と
        # --model-settings-file が二重になるのを避ける。宣言はどちらも台帳に残る）。
        extra += ["--model-settings-file", str(aider_settings(
            MODEL, num_ctx=NUM_CTX or None, sampling=SAMPLING, base=False))]
    if task.get("map_tokens"):
        # 定義の `--map-tokens 0` を**消してから**置き換える。同じフラグを 2 回並べて
        # 後勝ちに賭けると、定義側が並び順を変えた日に静かに 0 へ戻る。
        drop = argv.index("--map-tokens")
        argv = argv[:drop] + argv[drop + 2:]
        extra += ["--map-tokens", str(task["map_tokens"])]
    if task.get("test_cmd"):
        extra += ["--test-cmd", task["test_cmd"], "--auto-test"]
    # 追補は --message より前に置く（プロンプトは argv の末尾で受ける契約）。
    return argv[:-2] + extra + argv[-2:]


def _aider_argv_legacy(task: dict) -> "list[str]":
    """写しで組んでいた版（定義ファイル導入前）。比較用に残す。"""
    argv = ["aider", "--model", f"ollama_chat/{MODEL}",
            "--model-settings-file", str(aider_settings(MODEL)),
            # リポジトリマップは既定で切る（1,777 ファイルのマップだけで文脈が尽きる）。
            # 探索が要る課題だけ課題側で予算を与える。
            "--map-tokens", str(task.get("map_tokens", 0)),
            "--no-auto-commits", "--yes-always",
            "--no-check-update", "--analytics-disable", "--no-stream", "--no-pretty",
            "--no-gitignore"]
    # aider は「チャットに入っているファイル」しか編集しない。渡さないと本文で
    # 「ファイルを追加してくれ」と要求して終わる——`--message` は一発なので、
    # 答える人がいない＝課題に着手すらしない（実測で T2 が 3/3 これだった）。
    for path in task.get("read") or ():
        argv += ["--read", path]
    if task.get("test_cmd"):
        argv += ["--test-cmd", task["test_cmd"], "--auto-test"]
    return argv + list(task.get("files") or ()) + ["--message", task["goal"]]


def classify(rc: int, wall: float, out: str, err: str) -> str:
    """失敗様式のラベル。台帳を後から数えられるようにする。

    上限超過は**打ち切った事実**（rc と TIMEOUT マーカー）で決める。壁時計との比較は
    マシンのスリープを含むので、受入が PASS した実行に timeout の札を貼っていた
    （実測 2026-08-30: T3autosplit#2）。壁時計は人が読む所要時間としてだけ残す。
    """
    if rc == -1 and "TIMEOUT" in (err or ""):
        return "timeout"
    if rc != 0:
        return "cli_error"
    if not out.strip():
        return "empty"
    if '"ok": false' in out or '"ok":false' in out:
        return "self_reported_incomplete"
    if "max_rounds" in err or "ラウンド上限" in err:
        return "max_rounds"
    return "returned"


def _agent_markers(stderr: str) -> dict:
    """Extract stable adapter markers without duplicating policy text in the harness."""
    markers = {"policy_id": None, "policy_sha256": None,
               "tokens_in": None, "tokens_out": None, "model_settings": None}
    for line in stderr.splitlines():
        if line.startswith("@agent-policy "):
            fields = dict(field.split("=", 1) for field in line.split()[1:] if "=" in field)
            markers["policy_id"] = fields.get("id")
            markers["policy_sha256"] = fields.get("sha256")
        elif line.startswith("@agent-settings "):
            # adapter が管理する実効 settings。**ここでは組み立てない**——組み立てると
            # 「adapter が何を書いたか」ではなく「ハーネスが何を書いたと思っているか」を
            # 台帳へ残すことになる（08-18 §7.2）。
            try:
                markers["model_settings"] = json.loads(line.split(None, 1)[1])
            except (IndexError, ValueError):
                pass
        elif line.startswith("@agent-usage "):
            fields = dict(field.split("=", 1) for field in line.split()[1:] if "=" in field)
            try:
                markers["tokens_in"] = int(fields["tokens_in"])
                markers["tokens_out"] = int(fields["tokens_out"])
            except (KeyError, ValueError):
                pass
    return markers


REPAIR_NOTE = ("\n\n【前回の実行を機械で検証した結果】次の不一致が残っている:\n{}\n"
               "この不一致だけを直すこと。他の振る舞いと他のファイルは変えない。")


def slice_reads(step: dict, wt: Path) -> "tuple[dict, dict]":
    """`slice` を持つ手順の --read 材料を、agentcore.context_slice の抜粋へ差し替える。

    抜粋は作業ツリー内の `<path>.slice.py` に書く（編集対象ではないので追跡外でよい）。
    切れなければ原本へ倒し、倒したことを台帳に残す（静かに倒れると、抜粋が効いていない条件で
    測ってしまう——context_slice の CLI と同じ規律）。LLM は使わない。
    """
    spec = step.get("slice")
    if not spec:
        return step, {}
    sys.path.insert(0, str(REPO / "tools/agent-tools/agentcore"))
    from agentcore import context_slice  # noqa: PLC0415
    reads, info = [], {}
    for rel in step.get("read") or ():
        symbols = spec.get(rel)
        if not symbols:
            reads.append(rel)
            continue
        result = context_slice.slice_file(wt / rel, symbols)
        if result is None:
            reads.append(rel)
            info[rel] = {"sliced": False, "reason": "切れないので原本へ倒した"}
            continue
        out = Path(rel).with_suffix(".slice.py")
        (wt / out).write_text(result.text, encoding="utf-8")
        reads.append(str(out))
        info[rel] = {"sliced": True, "kept_lines": result.kept_lines,
                     "total_lines": result.total_lines, "omitted": len(result.omitted)}
    return {**step, "read": tuple(reads)}, info


def _settings_from_argv(argv: "list[str]") -> "str | None":
    """argv が名指しした `--model-settings-file` の中身（無ければ None）。

    腕の条件を台帳だけで復元するための材料。sampling の腕はこのファイルで宣言するので、
    ファイル名だけ残しても後から中身が分からない（一時ディレクトリは消える）。
    """
    if "--model-settings-file" not in argv:
        return None
    path = Path(argv[argv.index("--model-settings-file") + 1])
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def invoke(step: dict, wt: Path) -> "tuple[int, str, str, float, list]":
    """1 回だけエージェントを起こす。argv とプロンプトの作り方は経路ごとの正典に従う。"""
    step, _slice_info = slice_reads(step, wt)
    # selfedit は aider と同じ「対象ファイルが決まった single-shot の編集」なので、
    # プロンプトも argv も aider 経路と同じ作り方にする——ここを変えると、測っているのが
    # 「編集適用の実装差」ではなく「渡し方の差」になる（未決 5 の対照条件）。
    prompt = "" if CLI in ("aider", "selfedit") else build_prompt(step)
    if CLI == "aider":
        argv = aider_argv(step)
    elif CLI == "selfedit":
        argv = engine.headless_cmd("selfedit", MODEL, step["goal"],
                                   files=step.get("files") or (),
                                   read_files=step.get("read") or ())["argv"]
    else:
        argv = ollama_argv()
    # 上限は group ごと（engine.run_process）。孫（エージェント CLI・推論クライアント）を
    # 残すと次の実行が順番待ちになる。経過は monotonic——壁時計はマシンのスリープを含む
    # ので、上限内に終わった実行を timeout と記録していた（実測 2026-08-29〜30）。
    started = time.monotonic()
    try:
        p = engine.run_process(argv, input=prompt, cwd=wt,
                               env={**os.environ, "OLLAMA_API_BASE": OLLAMA_API_BASE,
                                    **({"AGENT_COMMANDS_DIR": HARNESS_COMMANDS_DIR}
                                       if HARNESS_COMMANDS_DIR else {})},
                               capture_output=True, text=True, timeout=WALL_LIMIT)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        def captured(value):
            if isinstance(value, bytes):
                return value.decode("utf-8", "replace")
            return value or ""
        rc, out, err = -1, captured(exc.stdout), captured(exc.stderr)
        err = err + ("\n" if err else "") + "TIMEOUT"
    return rc, out, err, time.monotonic() - started, argv


def snapshot_worktree(wt: Path) -> dict:
    """手順を始める前の作業ツリーを控える（引き直しで戻すため）。

    追跡済みファイルは git が戻せるので控えない。控えるのは**未追跡**——課題の仕込み
    （`seed`）が置くのがまさにそれで、`git clean` だけで戻すと仕込みごと消える。
    """
    def ls(*flags: str) -> "list[str]":
        r = subprocess.run(["git", "ls-files", "--others", "--exclude-standard",
                            *flags, "-z"], cwd=wt, capture_output=True, check=True)
        return [p for p in r.stdout.decode("utf-8", "surrogateescape").split("\0") if p]
    # `--directory` は中身のないディレクトリも返す。seed_t1 が作る eval/ がこれ。
    return {"files": {p: (wt / p).read_bytes() for p in ls() if (wt / p).is_file()},
            "dirs": [p for p in ls("--directory") if (wt / p).is_dir()]}


def restore_worktree(wt: Path, snapshot: dict) -> None:
    """作業ツリーを控えた時点へ戻す。抽選と抽選のあいだで呼ぶ。

    引き直しは**独立な抽選**なので、前の抽選が書いた成果は残さない——残すと
    「診断を渡さない再投入」（既存の blind 腕）と同じものを測ることになる。
    """
    if wt.resolve() == REPO.resolve():
        # リポジトリ本体で clean を回すと作業中の変更ごと消える。呼び出し側の事故
        # （worktree ではなくリポジトリを渡す）は、実行する前にここで落とす。
        raise SystemExit("restore_worktree はリポジトリ本体に対しては実行しません")
    subprocess.run(["git", "checkout", "--", "."], cwd=wt, capture_output=True)
    subprocess.run(["git", "clean", "-fdq"], cwd=wt, capture_output=True)
    for rel in snapshot["dirs"]:
        (wt / rel).mkdir(parents=True, exist_ok=True)
    for rel, blob in snapshot["files"].items():
        path = wt / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)


def escalated(trace: "list[dict]") -> bool:
    """ゲート付き手順が、再投入も引き直しも使い切って通らなかったか。

    statemachine の `escalate`（「この段では無理」を機械が宣告する口）に対応する。
    受入率とは別に数える——上限到達の頻度がそのままクラウド昇格の頻度になるので、
    引き直しの採否はここが下がるかどうかで決まる。
    """
    last: "dict[int, bool]" = {}
    for rec in trace:
        if "gate" in rec:
            last[rec["step"]] = rec["gate"]
    return any(value is False for value in last.values())


def run_steps(task: dict, wt: Path) -> "tuple[list[dict], str, str]":
    """タスクの手順を順に回す。一発版は「手順が 1 つ」として同じ道を通る。

    ゲートを持つ手順は 2 段で受ける。

    1. **診断つき再投入**（`max_retries`）——機械が出した不一致を課題文へ足して直させる。
    2. **引き直し**（`--resample`）——再投入を使い切っても通らなければ、作業ツリーを
       手順開始時点へ戻して独立に抽選し直す。温度 0 では同じ出力が返るだけなので
       （実測 P10・2026-08-15）、効くのは sampling を宣言した腕だけである。

    どちらの段でも**採択するのは決定的ゲートだけ**で、モデルの自己申告は見ない。
    ここが agent-loop の statemachine（決定的遷移 + 限定 retry + 上限到達で escalate）に
    対応する部分で、遷移判断に LLM を使わないことを写している。ハーネス本体ではなく
    ここに置いているのは、測りたいのが「分解と決定的ゲートが効くか」であって
    statemachine 実装の出来ではないため（1 回の呼び出しの argv は本番と同一）。
    """
    steps = task.get("steps") or [task]
    trace: "list[dict]" = []
    out = err = ""
    for n, step in enumerate(steps, 1):
        gate = step.get("gate")
        # 引き直すのはゲートのある手順だけ。採択する機械がいなければ、引き直しは
        # 「同じ課題を何度も呼んだ」だけになる（自己申告での採択は P1 が外した道）。
        draws = RESAMPLE if gate is not None else 1
        snapshot = snapshot_worktree(wt) if draws > 1 else None
        for draw in range(1, draws + 1):
            if draw > 1:
                restore_worktree(wt, snapshot)
            goal, ok = step["goal"], False
            for attempt in range(1 + int(step.get("max_retries") or 0)):
                rc, out, err, wall, argv = invoke({**step, "goal": goal}, wt)
                markers = _agent_markers(err)
                # 実行 argv 全体と実効 model settings を残す（08-18 §7.2 の残り 2 項目）。
                # adapter が管理する settings は marker が正、それが無い腕
                # （--agent-policy off 等）は argv が名指ししたファイルの中身が正。
                rec = dict(step=n, draw=draw, attempt=attempt + 1, wall=round(wall, 1),
                           mode=classify(rc, wall, out, err), argv=list(argv),
                           **{**markers,
                              "model_settings": markers["model_settings"]
                              or _settings_from_argv(argv)})
                if gate is None:
                    trace.append(rec)
                    break
                ok, feedback = gate(wt)
                trace.append({**rec, "gate": ok, "gate_note": feedback[:160]})
                if ok:
                    break
                goal = step["goal"] + REPAIR_NOTE.format(feedback)
            if ok:
                break
    return trace, out, err


def run_one(tid: str, i: int) -> dict:
    task = TASKS[tid]
    wt = WORK / f"{tid}-{i}"
    if wt.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                       cwd=REPO, capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)
    # 登録だけ残った worktree を掃除してから足す。WORK ごと消して測り直すのは普通の
    # 手順なので、その次の run が「already registered」で死ぬのを毎回踏む。
    subprocess.run(["git", "worktree", "prune"], cwd=REPO, capture_output=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(wt), "HEAD"],
                   cwd=REPO, capture_output=True, text=True, check=True)
    task["seed"](wt)
    # aider は自前のシステムプロンプトと編集ループを持つので、flow-worker の
    # プロンプト（報告契約・worktree 規約）は渡さない——渡すと道具の作法と二重になる。
    # 課題文（goal）とチェッカーは両経路で同一なので、比較は成立する。
    trace, out, err = run_steps(task, wt)
    wall = sum(s["wall"] for s in trace)

    mode = trace[-1]["mode"] if trace else "empty"
    try:
        ok, note = task["check"](wt)
    except Exception as e:  # noqa: BLE001 — チェッカーの事故は fail 扱いで記録
        ok, note = False, f"checker error: {e}"
    log = ""
    policy_id = policy_sha256 = None
    for line in err.splitlines():
        if line.startswith("@agent-log"):
            log = line.split(None, 1)[-1]
    for call in trace:
        policy_id = call.get("policy_id") or policy_id
        policy_sha256 = call.get("policy_sha256") or policy_sha256
    token_calls = [call for call in trace if call.get("tokens_in") is not None]
    tokens_in = sum(call["tokens_in"] for call in token_calls) if token_calls else None
    tokens_out = sum(call["tokens_out"] for call in token_calls) if token_calls else None
    rec = dict(task=tid, family=task["family"], iter=i, cli=CLI, model=MODEL,
               # 実行方針の腕。呼び出しの形（設計 段 12）とプロンプト（段 13）は
               # ここが同じでなければ比べられない。既定は "default"。
               harness=HARNESS,
               aider_version=AIDER_VERSION,
               # 参照材料の渡し方（案 2 の腕）。whole / slice / none。無指定の課題は null。
               read_mode=task.get("read_mode"),
               slice=(slice_reads(task, wt)[1] or None) if task.get("slice") else None,
               num_ctx=NUM_CTX or None, num_predict=NUM_PREDICT or None,
               # 呼び出し回数上限の腕。null は「宣言しなかった」＝定義の write_args のまま。
               max_rounds=MAX_ROUNDS or None,
               policy_id=policy_id,
               policy_sha256=policy_sha256, ok=ok, mode=mode,
               # sampling は台帳に必ず残す。null は「宣言しなかった」＝ aider / ollama の
               # 既定で走ったという意味で、**空欄と同義ではない**——腕の条件を後から
               # 台帳だけで復元できないと、条件の違う数字が同じ表に並ぶ。
               sampling=(SAMPLING or None),
               # 引き直しの条件も同じ理由で必ず残す。`resample` は宣言した上限、
               # `draws` は実際に使った本数——上限を上げても使われていないなら、
               # 受入の差は引き直し以外の何かで説明しないといけない。
               resample=RESAMPLE,
               draws=max((call.get("draw", 1) for call in trace), default=1),
               escalate=escalated(trace),
               wall_limit=WALL_LIMIT, wall=round(wall, 1), tokens_in=tokens_in,
               tokens_out=tokens_out, checker_pass=ok, checker_diagnostic=note,
               map_tokens=task.get("map_tokens", 0), auto_test=bool(task.get("test_cmd")),
               note=note, log=log, out_chars=len(out), calls=len(trace),
               # 診断つき再投入の回数。引き直し（draw）は初回投入なので数に入れない
               # ——`len(trace) - 手順数` で数えると両者が混ざる。引き直しの無い
               # 既存の腕では、この式は従来と同じ値を返す。
               retry_count=sum(1 for call in trace if call.get("attempt", 1) > 1),
               trace=trace)
    draws = rec["draws"]
    print(f"  {tid}#{i}: {'PASS' if ok else 'FAIL':4s} {mode:24s} "
          f"{wall:6.1f}s {len(trace)}call{f' {draws}draw' if draws > 1 else ''}  "
          f"{note[:66]}", flush=True)
    return rec


def main() -> None:
    global WALL_LIMIT, MODEL, CLI, AGENT_POLICY, NUM_CTX, NUM_PREDICT, SAMPLING
    global RESAMPLE, AIDER_VERSION, HARNESS, HARNESS_COMMANDS_DIR, MAX_ROUNDS
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL,
                    help="測るモデル。別モデルの判定はここだけ変えればよい")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--tasks", default="T1,T2,T3")
    ap.add_argument("--wall", type=float, default=WALL_LIMIT,
                    help="1 run の壁時計上限（既定は agent_timeout の 600 秒）")
    ap.add_argument("--cli", default=CLI, choices=("agent-ollama", "aider", "selfedit"),
                    help="worker として回すエージェント層。道具の作法はそれぞれのものを使う")
    ap.add_argument("--agent-policy", default=AGENT_POLICY,
                    choices=("off", "gemma4-e4b-reliability-v1"),
                    help="aider reliability policy の A/B arm（未指定は本番定義を継承）")
    ap.add_argument("--num-predict", type=int, default=NUM_PREDICT,
                    help="1 ターンの生成上限（aider 経路のみ・0 で無効）。"
                         "収束しない課題の壁時計を切るレバー")
    ap.add_argument("--num-ctx", type=int, default=NUM_CTX,
                    help="Aider managed model settings の context size（0 で未指定）")
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS,
                    help="ツールループの呼び出し回数上限（agent-ollama 経路のみ・0 で未指定＝"
                         "定義の write_args のまま）。制限付き実行案 §6 の「回数制限のみ」腕")
    # sampling（aider 経路のみ）。**未指定なら 1 バイトも宣言しない**ので、
    # これまでの測定条件（aider / ollama の既定）がそのまま再現される。
    ap.add_argument("--temperature", type=float, default=None,
                    help="sampling の温度。未指定なら aider の既定のまま。"
                         "Gemma 系は低温が推奨されない可能性がある（要実測・計画 P10）")
    ap.add_argument("--top-p", type=float, default=None, help="nucleus sampling。同上")
    ap.add_argument("--top-k", type=int, default=None, help="top-k sampling。同上")
    # 引き直し（best-of-N）。採択は決定的ゲートだけが行い、全滅したときだけ escalate。
    ap.add_argument("--harness", default=HARNESS, metavar="ARM",
                    help="実行方針の腕。default（現行）| templates:<宣言ディレクトリ>"
                         "（段 13 のプロンプト差し替え）。台帳の harness 軸に残る")
    ap.add_argument("--resample", type=int, default=RESAMPLE, metavar="N",
                    help="ゲート付き手順を最大 N 回まで引き直す（既定 1 = 引き直さない）。"
                         "再投入を使い切ってから作業ツリーを戻して独立に抽選し直す")
    args = ap.parse_args()
    WALL_LIMIT = args.wall
    MODEL = args.model
    CLI = args.cli
    AGENT_POLICY = args.agent_policy
    NUM_PREDICT = args.num_predict
    NUM_CTX = args.num_ctx
    MAX_ROUNDS = args.max_rounds
    SAMPLING = {k: v for k, v in (("temperature", args.temperature),
                                  ("top_p", args.top_p), ("top_k", args.top_k))
                if v is not None}
    if SAMPLING and CLI != "aider":
        raise SystemExit("--temperature / --top-p / --top-k は aider 経路のみです"
                         "（agent-ollama は AGENT_OLLAMA_OPTIONS で渡してください）")
    if SAMPLING and AGENT_POLICY != "off":
        raise SystemExit("sampling arm は --agent-policy off と独立に評価してください")
    if AGENT_POLICY is not None and CLI != "aider":
        raise SystemExit("--agent-policy は aider 経路のみです")
    if CLI == "selfedit":
        # 対照実装の定義は**同梱しない**（運用の候補に見せない）。探索順の先頭へ
        # 評価専用の置き場を足すだけなので、同梱定義（aider 等）はそのまま解決できる。
        os.environ.setdefault("KIRO_AGENTS_DIR",
                              str(Path(__file__).resolve().parent / "agents"))
    if CLI == "selfedit" and (SAMPLING or NUM_CTX or NUM_PREDICT):
        raise SystemExit("selfedit 経路は aider 専用の腕（sampling / num-ctx / num-predict）"
                         "と併用できません。編集適用の実装差だけを測ってください")
    if NUM_CTX < 0 or NUM_PREDICT < 0:
        raise SystemExit("--num-ctx / --num-predict は 0 以上で指定してください")
    if MAX_ROUNDS < 0:
        raise SystemExit("--max-rounds は 0 以上で指定してください（0 = 定義のまま）")
    if MAX_ROUNDS and CLI != "agent-ollama":
        # aider は自分の直し直し（max_reflections）を持ち、ハーネスは周を数えない。
        # 効かない宣言を受け取ると、腕の名前だけが台帳に残って中身が伴わない。
        raise SystemExit("--max-rounds は agent-ollama 経路のみです"
                         "（aider は単発 worker でツールループを持ちません）")
    HARNESS = str(args.harness or "default").strip()
    if HARNESS != "default":
        # 腕は**名前だけにしない**。名前が実際の設定を指していないと、条件の違う数字が
        # 同じ harness 値で台帳に並ぶ——軸を足した意味が消える。
        if not HARNESS.startswith("templates:"):
            raise SystemExit(f"知らない --harness です: {HARNESS}"
                             "（default | templates:<宣言ディレクトリ>）")
        HARNESS_COMMANDS_DIR = HARNESS.split(":", 1)[1].strip()
        if not HARNESS_COMMANDS_DIR or not os.path.isdir(HARNESS_COMMANDS_DIR):
            raise SystemExit(f"--harness templates: の宣言ディレクトリがありません: "
                             f"{HARNESS_COMMANDS_DIR!r}")
        HARNESS_COMMANDS_DIR = os.path.abspath(HARNESS_COMMANDS_DIR)
        if CLI == "aider":
            # aider は自前のシステムプロンプトで走るので、我々のテンプレート宣言は
            # 1 バイトも効かない。効かない条件に別の腕名を付けると、同じ実行が 2 つの
            # 腕として台帳に並ぶ。
            raise SystemExit("--harness templates: は agent-ollama 経路のみです"
                             "（aider は自前のシステムプロンプトで走ります）")
    RESAMPLE = args.resample
    if RESAMPLE < 1:
        raise SystemExit("--resample は 1 以上で指定してください")
    if RESAMPLE > 1 and CLI == "aider" and not SAMPLING:
        # 貪欲デコードでの引き直しは、同じ壁時計を払って同じ出力を受け取るだけ。
        # 「引き直しても揺れない」を対照として測りたいなら --temperature 0 と明示する
        # ——このハーネスは「未宣言」と「既定値を明示」を別物として扱う。
        raise SystemExit(
            "--resample > 1 は sampling を宣言してから使ってください。aider 経路の実効 "
            "temperature は 0（貪欲デコード）で、引き直しても同じ出力が返ります"
            "（実測 P10・2026-08-15）。対照として測るなら --temperature 0 と明示してください")

    WORK.mkdir(parents=True, exist_ok=True)
    ledger = WORK / "ledger.jsonl"
    tids = [t.strip() for t in args.tasks.split(",") if t.strip()]
    if CLI == "aider":
        try:
            version = subprocess.run(["aider", "--version"], capture_output=True,
                                     text=True, timeout=10)
            AIDER_VERSION = ((version.stdout or version.stderr).strip() or None
                             if version.returncode == 0 else None)
        except (OSError, subprocess.SubprocessError):
            AIDER_VERSION = None
        first = TASKS[tids[0]]
        sample = " ".join(aider_argv((first.get("steps") or [first])[0])[:-2])
        print(f"model={MODEL} cli=aider argv={sample} …（出所: agents/aider.json）")
    else:
        # **実際に打つ argv を出す**（定義の write_args そのままではない）。腕で上限を
        # 差し替えたのにヘッダが定義の値を出していると、条件を隠したまま測ることになる
        # ——実測 2026-08-29 でこれを踏んだ（ヘッダは 12、実行は 3 / 2）。
        print(f"model={MODEL} cli={CLI} argv={' '.join(ollama_argv()[2:])} "
              f"（出所: {WRITE_ARGS_SOURCE}"
              + (f" + 腕 --max-rounds {MAX_ROUNDS}" if MAX_ROUNDS else "") + "）")
    print(f"wall_limit={WALL_LIMIT:.0f}s tasks={tids} repeat={args.repeat}")
    print(f"agent_policy={AGENT_POLICY or '本番定義を継承'}")
    # 腕の条件を起動行にも出す。「宣言しなかった」と「既定値を宣言した」は別物なので、
    # 前者は既定と明示する——実効値が不明なまま数字だけが残るのを避ける
    # （実効 sampling の未確認は計画 P10 が潰す当の交絡）。
    print("sampling=" + (", ".join(f"{k}={v}" for k, v in SAMPLING.items())
                         if SAMPLING else "未宣言（aider / ollama の既定のまま）"))
    print(f"resample={RESAMPLE}" + ("（引き直さない）" if RESAMPLE == 1 else
                                    "（ゲート付き手順を最大 N 回・採択は決定的ゲート）") + "\n")

    rows = []
    for tid in tids:
        for i in range(1, args.repeat + 1):
            rec = run_one(tid, i)
            rows.append(rec)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("\n=== 受入率（決定的チェッカー）")
    for tid in tids:
        r = [x for x in rows if x["task"] == tid]
        if not r:
            continue        # --repeat 0（条件だけ見たいとき）で集計へ落ちない
        n = len(r); ok = sum(1 for x in r if x["ok"])
        walls = sorted(x["wall"] for x in r)
        calls = sum(x["calls"] for x in r)
        # escalate は受入と別に出す。引き直しの採否はここが下がるかで決まるので、
        # 「受入は同じだが escalate だけ減った」を読み落とさない形にしておく。
        esc = sum(1 for x in r if x.get("escalate"))
        print(f"  {tid}: {ok}/{n}  中央値 {walls[len(walls)//2]:.0f}s  "
              f"呼び出し {calls}回  escalate {esc}/{n}  "
              f"様式 {sorted(set(x['mode'] for x in r))}")
    ok = sum(1 for x in rows if x["ok"])
    print(f"  合計: {ok}/{len(rows)}")
    # 族別の escalate。引き直しの採否は (a) 族で読む。(b) 族が動かないのは引き直しの
    # 失敗ではなく適用範囲の外（答えは成果物を 1 つに割ること）。
    families = sorted({x["family"] for x in rows})
    if len(families) > 1:
        print("  族別 escalate:", "  ".join(
            f"({fam}) {sum(1 for x in rows if x['family'] == fam and x.get('escalate'))}"
            f"/{sum(1 for x in rows if x['family'] == fam)}" for fam in families),
            "—— 引き直しの採否は (a) で読む。(b) は成果物を割る側の話")
    print(f"\n台帳: {ledger}")


if __name__ == "__main__":
    os.environ.setdefault("AGENT_OLLAMA_THINK", "off")
    main()
