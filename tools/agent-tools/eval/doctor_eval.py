#!/usr/bin/env python3
"""agent-dashboard の Doctor（読み取り専用の助言 4 モード）を測る。P9 の 3 セル目（計画 2026-08-22 §4.2 B1）。

プロンプトは**本番のビルダー**（`src/features/agent-project/main/agent.js` の `doctorPrompt`）を
node で呼んで組む（写さない）。応答は Markdown の自由記述なので、判定は決定的に測れる 2 点に絞る:

  1. **見出し契約** — 指示した N 見出しだけを使っているか（過不足・余計な見出しは契約違反）
  2. **構成的な言及** — スナップショットを組んだ時点で決まる正解トークン（失敗ログのモジュール名・
     取りこぼした acceptance の id・差分の値 等）が、**その見出しの節**に出ているか

読みやすさ・網羅性（C5）はここでは測らない。判定役（LLM）も使わない。

使い方: python3 doctor_eval.py [--model gemma4:e4b] [--repeat 3] [--cases DR1,DR2,DR3,DR4]
        [--selfcheck]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import engine  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
AGENT_JS = REPO / "tools/agent-dashboard/src/features/agent-project/main/agent.js"
LEDGER_DIR = pathlib.Path(os.environ.get("DOCTOR_EVAL_DIR", "/tmp/agent-doctor-eval"))
MODEL = "gemma4:e4b"
AGENT_CLI = "ollama"            # dashboard の doctor は読み取り専用のテキスト応答（変種なし）
WALL_LIMIT = 600.0

# ------------------------------------------------------------------ 素材（正解は構成から従う）

DR1_LOG = (
    "$ python3 -m pytest -q tests\n"
    "ImportError while loading conftest '/work/tests/conftest.py'.\n"
    "tests/conftest.py:3: in <module>\n"
    "    import yaml\n"
    "E   ModuleNotFoundError: No module named 'yaml'\n"
    "(verify exit 4 / 12.3s)\n")
DR1_CONTEXT = {
    "tab": "tasks", "project": "billing-kit",
    "selected": {"id": "T-7", "title": "請求 CSV の取り込みを実装", "status": "failed",
                 "failureSummary": "verify が exit 4 で失敗",
                 "task": {"id": "T-7", "status": "failed", "retries": 2,
                          "verify": "python3 -m pytest -q tests"},
                 "fullOutput": DR1_LOG},
}
DR2_CONTEXT = {
    "tab": "needs", "project": "billing-kit",
    "needs": [{"id": "N-12", "kind": "approval", "task": "T-3",
               "title": "schemas/invoice.json の必須項目追加（破壊的変更）を承認するか",
               "waiting_since": "2026-08-23T01:00:00Z"}],
    "selected": {"id": "N-12", "title": "schemas/invoice.json の必須項目追加（破壊的変更）を承認するか"},
}
DR3_CONTEXT = {
    "tab": "plan", "project": "billing-kit",
    "charter": {"goal": "請求 CSV を安全に取り込む",
                "acceptance": ["A1: CSV を取り込める", "A2: 取り込んだ行数を画面に表示する",
                               "A3: 不正な行はスキップしてログに残す"]},
    "proposedTasks": [{"id": "P-1", "title": "CSV パーサを実装する", "covers": "A1"},
                      {"id": "P-2", "title": "取り込み件数の表示 UI", "covers": "A2"}],
    "siblings": [],
}
DR4_CONTEXT = {
    "tab": "deliveries", "project": "billing-kit",
    "selected": {"id": "T-9", "title": "税率を 10% に直す",
                 "task": {"id": "T-9", "verify": "pytest -q tests",
                          "accept": ["apply_tax(1000) == 1100"]},
                 "diff": "--- a/billing.py\n+++ b/billing.py\n@@ -1 +1 @@\n-TAX_RATE = 0.08\n+TAX_RATE = 0.10\n"},
}

HEADINGS = {
    "consultation": ["## 現在起きていること", "## 次にすること", "## 判断の根拠"],
    "failure-diagnosis": ["## 結論", "## 根本原因候補と確度", "## 対処対象", "## 確認手順",
                          "## 修正候補", "## 再実行方法", "## 不足している情報"],
    "plan-critique": ["## 総評", "## 取りこぼし・重複", "## 依存と優先度", "## acceptance対応",
                      "## 推薦", "## 差し戻し文面案"],
    "delivery-rationale": ["## 変更の意図", "## acceptance対応", "## リスクと注意点", "## 推薦",
                           "## 差し戻し文面案"],
}


def sections(text: str) -> "dict[str, str]":
    """`## ` 見出しごとに本文を切る。見出し行は `## 名前`（末尾の空白・記号は無視）。"""
    out: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^\s*(##\s+.+?)\s*$", line)
        if m and not line.lstrip().startswith("###"):
            current = m.group(1).replace("## ", "## ", 1).rstrip(" :：")
            out[current] = ""
            continue
        if current is not None:
            out[current] += line + "\n"
    return out


def check_headings(text: str, mode: str) -> "str | None":
    want = HEADINGS[mode]
    got = [h for h in sections(text)]
    norm = lambda h: re.sub(r"\s+", "", h)  # noqa: E731
    missing = [h for h in want if norm(h) not in {norm(g) for g in got}]
    extra = [g for g in got if norm(g) not in {norm(h) for h in want}]
    if missing or extra:
        return f"見出し契約違反: 欠落 {missing} / 余計 {extra}"
    return None


def _sec(text: str, heading: str) -> str:
    norm = lambda h: re.sub(r"\s+", "", h)  # noqa: E731
    for h, body in sections(text).items():
        if norm(h) == norm(heading):
            return body
    return ""


def check_dr1(text: str) -> tuple[bool, str]:
    bad = check_headings(text, "failure-diagnosis")
    if bad:
        return False, bad
    cause = _sec(text, "## 結論") + _sec(text, "## 根本原因候補と確度")
    if "yaml" not in cause.lower():
        return False, "結論 / 根本原因に yaml（ログの ModuleNotFoundError）が出ない"
    target = _sec(text, "## 対処対象")
    if not re.search(r"実行環境|環境|依存", target):
        return False, f"対処対象が実行環境になっていない: {target.strip()[:60]!r}"
    if "成果物" in target and not re.search(r"実行環境|環境", target):
        return False, "対処対象を成果物にした（原因は環境の依存不足）"
    return True, "yaml を根本原因に挙げ、対処対象は実行環境"


def check_dr2(text: str) -> tuple[bool, str]:
    bad = check_headings(text, "consultation")
    if bad:
        return False, bad
    nxt = _sec(text, "## 次にすること")
    if not re.search(r"N-12|承認|schemas/invoice", nxt):
        return False, "次にすること に承認待ち N-12 が出ない"
    return True, "次の一手が承認待ち N-12 を指す"


def check_dr3(text: str) -> tuple[bool, str]:
    bad = check_headings(text, "plan-critique")
    if bad:
        return False, bad
    gap = _sec(text, "## 取りこぼし・重複") + _sec(text, "## acceptance対応")
    if not re.search(r"A3|不正な行|不正行|スキップ", gap):
        return False, "取りこぼし（A3: 不正行のスキップ）を指摘していない"
    return True, "A3 の取りこぼしを指摘"


def check_dr4(text: str) -> tuple[bool, str]:
    bad = check_headings(text, "delivery-rationale")
    if bad:
        return False, bad
    intent = _sec(text, "## 変更の意図")
    if not re.search(r"0\.10|10\s*%|10％", intent) or not re.search(r"税率|TAX_RATE", intent):
        return False, "変更の意図に『税率を 0.10（10%）へ』が出ない"
    acc = _sec(text, "## acceptance対応")
    if not re.search(r"1100|apply_tax", acc):
        return False, "acceptance 対応に apply_tax(1000) == 1100 が出ない"
    return True, "意図（税率 10%）と acceptance（1100）を結んでいる"


CASES = {
    "DR1": dict(mode="failure-diagnosis", context=DR1_CONTEXT, check=check_dr1, note=""),
    "DR2": dict(mode="consultation", context=DR2_CONTEXT, check=check_dr2, note=""),
    "DR3": dict(mode="plan-critique", context=DR3_CONTEXT, check=check_dr3, note=""),
    "DR4": dict(mode="delivery-rationale", context=DR4_CONTEXT, check=check_dr4, note=""),
}

# ------------------------------------------------------------------ 実行


def build_prompt(case: dict) -> str:
    """本番の doctorPrompt（agent.js）を node で呼ぶ。text = 完全なプロンプト本文。"""
    script = (
        "const a = require(process.argv[1]);"
        "let buf = '';process.stdin.on('data', d => buf += d);"
        "process.stdin.on('end', () => {const i = JSON.parse(buf);"
        "const p = a.doctorPrompt(i.context, i.note || '', {mode: i.mode});"
        "process.stdout.write(p.text);});")
    r = subprocess.run(["node", "-e", script, str(AGENT_JS)],
                       input=json.dumps({"context": case["context"], "mode": case["mode"],
                                         "note": case.get("note", "")}, ensure_ascii=False),
                       capture_output=True, text=True, timeout=60, cwd=AGENT_JS.parent)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"doctorPrompt の呼び出しに失敗: {r.stderr[:300]}")
    return r.stdout


def call(prompt: str) -> "tuple[int, str, str, float]":
    built = engine.headless_cmd(AGENT_CLI, MODEL, prompt, readonly=True)
    # 上限は group ごと（engine.run_process）。経過は monotonic、上限超過の判定は
    # 打ち切りの事実で行う（理由は engine.run_process と worker_eval.classify）。
    started = time.monotonic()
    try:
        p = engine.run_process(built["argv"], input=built["stdin"], capture_output=True,
                               text=True, timeout=WALL_LIMIT,
                               env={**os.environ, "AGENT_OLLAMA_THINK": "off", "NO_COLOR": "1"})
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "TIMEOUT"
    return rc, out, err, time.monotonic() - started


def run_one(cid: str, i: int) -> dict:
    case = CASES[cid]
    prompt = build_prompt(case)
    rc, out, err, wall = call(prompt)
    # 上限超過は打ち切った事実で判定する（壁時計はマシンのスリープを含む）。
    if rc == -1 and "TIMEOUT" in (err or ""):
        mode, ok, note = "timeout", False, f"上限超過（{WALL_LIMIT:.0f}s で打ち切り）"
    elif rc != 0:
        mode, ok, note = "cli_error", False, (err.strip()[-160:] or f"rc={rc}")
    elif not out.strip():
        mode, ok, note = "empty", False, "本文が空"
    else:
        ok, note = case["check"](out)
        mode = "correct" if ok else ("contract" if note.startswith("見出し契約違反") else "wrong")
    rec = dict(case=cid, doctor_mode=case["mode"], iter=i, model=MODEL, agent_cli=AGENT_CLI,
               ok=ok, mode=mode, wall=round(wall, 1), note=note, prompt_chars=len(prompt),
               out_chars=len(out), headings=list(sections(out).keys()), answer=out[:600])
    print(f"  {cid}#{i}: {'PASS' if ok else 'FAIL':4s} {mode:10s} {wall:6.1f}s  {note[:80]}",
          flush=True)
    return rec


# ------------------------------------------------------------------ selfcheck


def selfcheck() -> int:
    def md(mode, fill):
        return "\n".join(f"{h}\n{fill.get(h, '…')}\n" for h in HEADINGS[mode])

    good = {
        "DR1": md("failure-diagnosis", {"## 結論": "conftest が yaml を import できず落ちた（推測ではなくログ）",
                                        "## 対処対象": "実行環境（PyYAML の依存が入っていない）"}),
        "DR2": md("consultation", {"## 次にすること": "N-12 の承認可否を決める"}),
        "DR3": md("plan-critique", {"## 取りこぼし・重複": "A3（不正な行のスキップとログ）を担うタスクが無い"}),
        "DR4": md("delivery-rationale", {"## 変更の意図": "税率 TAX_RATE を 0.08 から 0.10 へ",
                                         "## acceptance対応": "apply_tax(1000) == 1100 が通る"}),
    }
    bad = {
        "DR1": [md("failure-diagnosis", {"## 結論": "テストが落ちた", "## 対処対象": "成果物"}),  # yaml 無し
                "## 結論\nyaml\n## 余計な見出し\nx\n"],                                      # 契約違反
        "DR2": [md("consultation", {"## 次にすること": "様子を見る"})],
        "DR3": [md("plan-critique", {"## 取りこぼし・重複": "特になし"})],
        "DR4": [md("delivery-rationale", {"## 変更の意図": "定数を変更した", "## acceptance対応": "OK"})],
    }
    failures = 0
    for cid, case in CASES.items():
        ok, note = case["check"](good[cid])
        if not ok:
            print(f"  NG {cid}: 正解が落ちた: {note}"); failures += 1
        for k, sample in enumerate(bad[cid]):
            ok, note = case["check"](sample)
            if ok:
                print(f"  NG {cid} bad#{k}: 外し方が通った"); failures += 1
    if not AGENT_JS.exists():
        print(f"  NG: {AGENT_JS} が無い"); failures += 1
    print("selfcheck:", "OK" if not failures else f"{failures} 件 NG")
    return int(bool(failures))


def main() -> None:
    global MODEL, AGENT_CLI, WALL_LIMIT
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--agent-cli", default=AGENT_CLI)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--wall", type=float, default=WALL_LIMIT)
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.selfcheck:
        raise SystemExit(selfcheck())
    MODEL, AGENT_CLI, WALL_LIMIT = args.model, args.agent_cli, args.wall
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger = LEDGER_DIR / "ledger.jsonl"
    cids = [c.strip() for c in args.cases.split(",") if c.strip()]
    sample = engine.headless_cmd(AGENT_CLI, MODEL, "x", readonly=True)["argv"]
    print(f"model={MODEL} agent_cli={AGENT_CLI} argv={' '.join(sample)} builder={AGENT_JS.name}")
    print(f"wall_limit={WALL_LIMIT:.0f}s cases={cids} repeat={args.repeat}\n")
    rows = []
    for cid in cids:
        for i in range(1, args.repeat + 1):
            rec = run_one(cid, i)
            rows.append(rec)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("\n=== 正解率（決定的チェッカー: 見出し契約 + 構成的な言及）")
    for cid in cids:
        r = [x for x in rows if x["case"] == cid]
        if not r:
            continue
        n = len(r); ok = sum(1 for x in r if x["ok"])
        walls = sorted(x["wall"] for x in r)
        print(f"  {cid} ({CASES[cid]['mode']}): {ok}/{n}  中央値 {walls[len(walls)//2]:.0f}s  "
              f"様式 {sorted(set(x['mode'] for x in r))}")
    print(f"  合計: {sum(1 for x in rows if x['ok'])}/{len(rows)}")
    print(f"\n台帳: {ledger}")


if __name__ == "__main__":
    main()
