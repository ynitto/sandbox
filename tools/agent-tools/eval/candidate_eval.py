#!/usr/bin/env python3
"""候補生成 + 決定的検算 —— 生成側の「モデルは候補だけ・機械が検算」を測る。

next-eval-plan §2 の順 4（grep パターン・パス候補・テスト名。計画 2026-08-22 §4.2 C2）。
E6 が決定化したのは filter / judge の**判定**側で、こちらは**生成**側。候補の誤りは
存在チェックで無害化される（存在しないパス・当たらない正規表現・無いテスト名は機械が落とす）ので、
測るのは「機械が落とした後に正解が残るか」である。

規律は text_eval と同じ: 材料は全部プロンプト内（小さな合成リポジトリ）、出力は JSON
**オブジェクト**（`{"candidates": [...]}`。ollama の JSON モードは配列を返せない）、
合否は決定的チェッカーだけ、`--selfcheck` で LLM 抜きに検証できる。

使い方: python3 candidate_eval.py [--model gemma4:e4b] [--repeat 3] [--cases CG1,CG2,CG3]
        [--selfcheck]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402

extract_json = engine.extract_json
PROMPT_BUILDER = Path(os.environ.get(
    "FLOW_WORKER_PROMPT",
    str(Path.home() / ".claude/skills/flow-worker/scripts/prompt.py")))
LEDGER_DIR = Path(os.environ.get("CANDIDATE_EVAL_DIR", "/tmp/agent-candidate-eval"))
MODEL = "gemma4:e4b"
WALL_LIMIT = 600.0
_FALLBACK_CMD = ["agent-ollama", "--think", "off", "--format", "json", "{model}"]
CMD, CMD_SOURCE = engine.load_cmd("ollama-json", _FALLBACK_CMD)
CMD_ENV = engine.load_env("ollama-json")
REQUEST = "課金モジュールの保守"

# ------------------------------------------------------------------ 合成リポジトリ
# 正解は**この内容から決定的に導く**（gold を手書きしない）。grep の正解は正規の regex を
# ここで走らせた結果、パスの正解は TAX_RATE を含む唯一のファイル、テスト名の正解は
# test_billing.py を ast で集めた名前のうち「丸め」を確かめるもの。

FIXTURE = {
    "README.md": "# billing-kit\n\n月額課金の日割りと請求合計を計算する小さなライブラリ。\n",
    "billing.py": (
        "TAX_RATE = 0.08   # 税率（本来は 0.10）\n\n\n"
        "def prorate(monthly_fee: int, days_used: int, days_in_month: int) -> int:\n"
        "    \"\"\"日割り。端数は切り上げ。\"\"\"\n"
        "    return -(-monthly_fee * days_used // days_in_month)\n\n\n"
        "def apply_tax(amount: int) -> int:\n"
        "    return round(amount * (1 + TAX_RATE))\n\n\n"
        "def invoice_total(items, discount_pct: int = 0) -> int:\n"
        "    subtotal = sum(prorate(i['fee'], i['days'], i['dim']) for i in items)\n"
        "    return apply_tax(subtotal * (100 - discount_pct) // 100)\n"),
    "report.py": (
        "from billing import prorate, invoice_total\n\n\n"
        "def monthly_report(customers):\n"
        "    rows = []\n"
        "    for c in customers:\n"
        "        rows.append((c['name'], prorate(c['fee'], c['days'], 30)))\n"
        "    return rows\n"),
    "cli.py": (
        "import sys\nfrom report import monthly_report\n\n\n"
        "def main():\n    print(monthly_report([]))\n\n\nif __name__ == '__main__':\n    main()\n"),
    "config.toml": "[billing]\ncurrency = \"JPY\"\nround = \"up\"\n",
    "tests/test_billing.py": (
        "from billing import prorate, invoice_total, apply_tax\n\n\n"
        "def test_prorate_full_month():\n    assert prorate(3000, 30, 30) == 3000\n\n\n"
        "def test_prorate_rounds_up():\n    assert prorate(3000, 1, 30) == 100\n\n\n"
        "def test_invoice_applies_discount_once():\n"
        "    items = [{'fee': 3000, 'days': 30, 'dim': 30}]\n"
        "    assert invoice_total(items, 10) == apply_tax(2700)\n\n\n"
        "def test_apply_tax_is_ten_percent():\n    assert apply_tax(1000) == 1100\n"),
    "tests/test_report.py": (
        "from report import monthly_report\n\n\n"
        "def test_monthly_report_empty():\n    assert monthly_report([]) == []\n"),
    "docs/pricing.md": (
        "# 料金\n\n日割り（prorate）は切り上げで計算する。税率は 10%。\n"),
}

_CANONICAL_GREP = re.compile(r"\bprorate\s*\(")


def fixture_listing() -> str:
    return "\n".join(f"--- {path}\n{body}" for path, body in FIXTURE.items())


def grep_lines(pattern: str) -> "set[tuple[str, int]] | None":
    """合成リポジトリ全体へ regex を掛けた (path, line) の集合。コンパイルできなければ None。"""
    try:
        rx = re.compile(pattern)
    except re.error:
        return None
    hits = set()
    for path, body in FIXTURE.items():
        for n, line in enumerate(body.splitlines(), 1):
            if rx.search(line):
                hits.add((path, n))
    return hits


GOLD_GREP = grep_lines(_CANONICAL_GREP.pattern)
GOLD_PATH = [p for p, b in FIXTURE.items() if "TAX_RATE" in b]
assert len(GOLD_PATH) == 1, GOLD_PATH
GOLD_PATH = GOLD_PATH[0]


def collected_tests() -> "set[str]":
    names = set()
    for path, body in FIXTURE.items():
        if Path(path).name.startswith("test_") and path.endswith(".py"):
            for node in ast.parse(body).body:
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    names.add(node.name)
    return names


TESTS = collected_tests()
GOLD_TEST = "test_prorate_rounds_up"
assert GOLD_TEST in TESTS


def _candidates(data) -> list[str]:
    if isinstance(data, dict):
        raw = data.get("candidates")
    else:
        raw = data
    return [str(x) for x in (raw or []) if isinstance(x, (str, int, float))] \
        if isinstance(raw, list) else []


# ------------------------------------------------------------------ チェッカー
# 返すのは (ok, note) に加え、機械が落とした候補数（無害化の実測）を note に含める。


def check_cg1(data) -> tuple[bool, str]:
    cands = _candidates(data)[:5]
    if not cands:
        return False, "候補が無い"
    dropped = exact = 0
    superset = []
    for c in cands:
        hits = grep_lines(c)
        if hits is None or not hits:
            dropped += 1
            continue
        if hits == GOLD_GREP:
            exact += 1
        elif hits > GOLD_GREP:
            superset.append(len(hits - GOLD_GREP))
    if exact:
        return True, f"正解と一致する regex {exact}/{len(cands)}（機械が落とした {dropped}）"
    if superset:
        return False, (f"正解を含むが余計な行 {min(superset)} 行（機械が落とした {dropped}）"
                       "——見落としは無いが絞れていない")
    return False, f"正解の行集合を出す regex が無い（候補 {len(cands)}・機械が落とした {dropped}）"


def check_cg2(data) -> tuple[bool, str]:
    cands = _candidates(data)[:5]
    if not cands:
        return False, "候補が無い"
    norm = [c.strip().lstrip("./") for c in cands]
    existing = [c for c in norm if c in FIXTURE]
    dropped = len(norm) - len(existing)
    if GOLD_PATH not in existing:
        return False, f"正解 {GOLD_PATH} が無い（存在する候補 {existing}・機械が落とした {dropped}）"
    return True, f"{GOLD_PATH} を含む（存在する候補 {len(existing)}・機械が落とした {dropped}）"


def check_cg3(data) -> tuple[bool, str]:
    cands = _candidates(data)[:5]
    if not cands:
        return False, "候補が無い"
    norm = [c.strip().split("::")[-1].split("(")[0] for c in cands]
    existing = [c for c in norm if c in TESTS]
    dropped = len(norm) - len(existing)
    if GOLD_TEST not in existing:
        return False, f"正解 {GOLD_TEST} が無い（実在する候補 {existing}・機械が落とした {dropped}）"
    return True, f"{GOLD_TEST} を含む（実在する候補 {len(existing)}・機械が落とした {dropped}）"


CASES = {
    "CG1": dict(
        genre="grep パターン", check=check_cg1,
        goal=("次の小さなリポジトリで、関数 prorate が**定義されている行と呼び出されている行**を"
              "すべて（そしてそれだけを）当てる Python 正規表現の候補を最大 3 つ挙げる。"
              "散文中の単語（docs の説明）には当てない。出力は JSON オブジェクト "
              '{"candidates": ["<regex>", ...]} のみ。\n\n' + fixture_listing())),
    "CG2": dict(
        genre="パス候補", check=check_cg2,
        goal=("次の小さなリポジトリで、「税率が 10% のはずなのに 8% で計算される」バグを直すために"
              "**変更が要るファイル**のパス候補を、確からしい順に最大 3 つ挙げる（存在するパスだけ。"
              "リポジトリルートからの相対パス）。出力は JSON オブジェクト "
              '{"candidates": ["<path>", ...]} のみ。\n\n' + fixture_listing())),
    "CG3": dict(
        genre="テスト名", check=check_cg3,
        goal=("次の小さなリポジトリで、prorate の**端数の丸め（切り上げ）**の挙動を確かめている"
              "既存の pytest テスト関数名の候補を最大 3 つ挙げる（実在する関数名だけ）。出力は "
              'JSON オブジェクト {"candidates": ["<test_name>", ...]} のみ。\n\n' + fixture_listing())),
}

# ------------------------------------------------------------------ 実行


def build_prompt(case: dict) -> str:
    payload = {"role": "worker", "kind": "work", "goal": case["goal"],
               "request": REQUEST, "deps": {}, "repo_instruction": "",
               "artifact_note": "", "workspace": {}, "references": [],
               "instructions": "", "repair_note": "", "read_note": ""}
    r = subprocess.run([sys.executable, str(PROMPT_BUILDER)],
                       input=json.dumps(payload, ensure_ascii=False),
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"prompt.py が失敗: {r.stderr[:300]}")
    return r.stdout.strip()


def call(prompt: str) -> "tuple[int, str, str, float]":
    argv = [a.replace("{model}", MODEL) for a in CMD]
    # 上限は group ごと（engine.run_process）。孫（エージェント CLI・推論クライアント）を
    # 残すと次の実行が順番待ちになる。経過は monotonic——壁時計はマシンのスリープを含む
    # ので、上限内に終わった実行を timeout と記録していた（実測 2026-08-29〜30）。
    started = time.monotonic()
    try:
        p = engine.run_process(argv, input=prompt, capture_output=True, text=True,
                               timeout=WALL_LIMIT, env={**os.environ, **CMD_ENV})
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "TIMEOUT"
    return rc, out, err, time.monotonic() - started


def judge(case: dict, rc: int, out: str, err: str, wall: float):
    data = None
    # 上限超過は**打ち切った事実**（rc と TIMEOUT マーカー）で判定する。壁時計との比較は
    # マシンのスリープを含むので、上限内に終わった実行を timeout と記録していた
    # （実測 2026-08-30: 受入 PASS のまま mode=timeout）。
    if rc == -1 and "TIMEOUT" in (err or ""):
        mode, ok, note = "timeout", False, f"上限超過（{WALL_LIMIT:.0f}s で打ち切り）"
    elif rc != 0:
        mode, ok, note = "cli_error", False, (err.strip()[-120:] or f"rc={rc}")
    elif not out.strip():
        mode, ok, note = "empty", False, "本文が空"
    else:
        try:
            data = extract_json(out)
        except Exception as e:  # noqa: BLE001
            mode, ok, note = "unparsable", False, f"JSON を抽出できない: {e}"
        else:
            ok, note = case["check"](data)
            mode = "correct" if ok else "wrong"
    return data, mode, ok, note


def run_one(cid: str, i: int) -> dict:
    case = CASES[cid]
    prompt = build_prompt(case)
    rc, out, err, wall = call(prompt)
    data, mode, ok, note = judge(case, rc, out, err, wall)
    rec = dict(case=cid, genre=case["genre"], iter=i, model=MODEL, ok=ok, mode=mode,
               wall=round(wall, 1), note=note, prompt_chars=len(prompt), out_chars=len(out),
               candidates=_candidates(data)[:5] if data is not None else None)
    if engine.missing():
        rec["engine_missing"] = engine.missing()
    print(f"  {cid}#{i}: {'PASS' if ok else 'FAIL':4s} {mode:10s} {wall:6.1f}s  {note[:72]}",
          flush=True)
    return rec


# ------------------------------------------------------------------ selfcheck


def selfcheck() -> int:
    good = {
        "CG1": {"candidates": [r"\bprorate\s*\("]},
        "CG2": {"candidates": ["billing.py", "docs/pricing.md"]},
        "CG3": {"candidates": ["test_prorate_rounds_up", "test_prorate_full_month"]},
    }
    bad = {
        "CG1": [{"candidates": ["prorate"]},             # 散文の単語にも当たる（余計な行）
                {"candidates": ["(prorate"]},            # コンパイルできない → 機械が落とす
                {"candidates": ["def prorate"]}],        # 定義だけで呼び出しを落とす
        "CG2": [{"candidates": ["tax.py", "billing/rate.py"]},   # 存在しない → 全部落ちる
                {"candidates": ["report.py"]}],                  # 実在するが正解でない
        "CG3": [{"candidates": ["test_prorate_rounding"]},       # 捏造名 → 落ちる
                {"candidates": ["test_prorate_full_month"]}],    # 実在するが正解でない
    }
    failures = 0
    for cid, case in CASES.items():
        ok, note = case["check"](good[cid])
        if not ok:
            print(f"  NG {cid}: 正解が落ちた: {note}"); failures += 1
        for k, sample in enumerate(bad[cid]):
            ok, note = case["check"](sample)
            if ok:
                print(f"  NG {cid} bad#{k}: 外し方が通った: {note}"); failures += 1
    # 機械が落とした数が note に出る（無害化の実測）
    _, note = check_cg2({"candidates": ["nope.py", "billing.py"]})
    if "機械が落とした 1" not in note:
        print(f"  NG CG2: 落とした数が出ない: {note}"); failures += 1
    print("selfcheck:", "OK" if not failures else f"{failures} 件 NG")
    return int(bool(failures))


def main() -> None:
    global MODEL, WALL_LIMIT
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--wall", type=float, default=WALL_LIMIT)
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.selfcheck:
        raise SystemExit(selfcheck())
    MODEL, WALL_LIMIT = args.model, args.wall
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger = LEDGER_DIR / "ledger.jsonl"
    cids = [c.strip() for c in args.cases.split(",") if c.strip()]
    print(f"model={MODEL} argv={' '.join(CMD)}（出所: {CMD_SOURCE}）")
    if engine.missing():
        print(f"engine 欠落: {engine.missing()}")
    print(f"wall_limit={WALL_LIMIT:.0f}s cases={cids} repeat={args.repeat}\n")
    rows = []
    for cid in cids:
        for i in range(1, args.repeat + 1):
            rec = run_one(cid, i)
            rows.append(rec)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("\n=== 正解率（決定的チェッカー）")
    for cid in cids:
        r = [x for x in rows if x["case"] == cid]
        if not r:
            continue
        n = len(r); ok = sum(1 for x in r if x["ok"])
        walls = sorted(x["wall"] for x in r)
        print(f"  {cid} ({CASES[cid]['genre']}): {ok}/{n}  中央値 {walls[len(walls)//2]:.0f}s  "
              f"様式 {sorted(set(x['mode'] for x in r))}")
    print(f"  合計: {sum(1 for x in rows if x['ok'])}/{len(rows)}")
    print(f"\n台帳: {ledger}")


if __name__ == "__main__":
    os.environ.setdefault("AGENT_OLLAMA_THINK", "off")
    main()
