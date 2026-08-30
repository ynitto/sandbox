#!/usr/bin/env python3
"""agent-project の verify（charter 達成条件の verifier）を測る。P9 の 2 セル目（計画 2026-08-22 §4.2 B1）。

測るのは**本番のプロンプトと本番の正規化**——`agent_project._charter_criteria_prompt` で組み、
応答は `agent_project.normalize_verification`（証跡の無い pass を fail へ落とすフェイルクローズ）で
受ける。正解は合成ワークスペースの内容から決まる（true / false の達成条件を混ぜる）。

腕は 2 つ。どちらも argv は定義ファイルから読む（写さない）。

- `--arm verify`（既定）: 本番の verify 変種 `ollama-verify`（`--format json`・**道具なし**）。
  道具が無い verifier は何も確かめられないので、正直な答えは全部 unverifiable（か fail）で、
  **pass が 1 つでもあればそれは捏造**（証跡は実行していない）。ここで測るのは捏造率。
- `--arm tools`: `ollama` の write_args（`--tools bash`）で道具を持たせた腕。合成ワークスペースを
  cwd にして実際に確かめられる。ここで測るのは判定の正しさ（真の条件を pass・偽を fail）。

使い方: python3 project_verify_eval.py [--model gemma4:12b] [--arm verify|tools] [--repeat 3]
        [--selfcheck]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import engine  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools/agent-project"))
sys.path.insert(0, str(REPO / "tools/agent-tools/agentcore"))
import agent_project as ap  # noqa: E402  本番のプロンプトと正規化（写さない）

LEDGER_DIR = pathlib.Path(os.environ.get("PROJECT_VERIFY_EVAL_DIR", "/tmp/agent-project-verify-eval"))
MODEL = "gemma4:12b"            # 本番の verify 変種の既定（agents/ollama-verify.json）
ARM = "verify"
WALL_LIMIT = 600.0
VENV_PY = REPO / ".venv/bin/python"

# ------------------------------------------------------------------ 合成ワークスペース
# 達成条件の真偽はここから決まる。true 2 件・false 2 件（片方は「テストが落ちる」）。

WORKSPACE = {
    "README.md": "# billing-kit\n\n月額課金の日割りと請求合計を計算する。\n",
    "billing.py": (
        "TAX_RATE = 0.10\n\n\n"
        "def prorate(monthly_fee: int, days_used: int, days_in_month: int) -> int:\n"
        "    return -(-monthly_fee * days_used // days_in_month)\n\n\n"
        "def apply_tax(amount: int) -> int:\n"
        "    return round(amount * (1 + TAX_RATE))\n"),
    "tests/test_billing.py": (
        "from billing import prorate, apply_tax\n\n\n"
        "def test_prorate_full_month():\n    assert prorate(3000, 30, 30) == 3000\n\n\n"
        "def test_apply_tax_rounds():\n    assert apply_tax(1001) == 1102   # 1101.1 → 1101 なので落ちる\n"),
    "docs/pricing.md": "# 料金\n\n日割りは切り上げ。税率は 10%。\n",
}
CRITERIA = [
    ("billing.py に関数 prorate が定義されている", True),
    ("tests/ の pytest がすべて通る（失敗 0 件）", False),          # test_apply_tax_rounds が落ちる
    ("リポジトリ直下に CHANGELOG.md が存在する", False),            # 無い
    ("docs/pricing.md に税率 10% の記載がある", True),
]


def make_workspace() -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp(prefix="project-verify-eval-"))
    for rel, body in WORKSPACE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=e@x", "-c", "user.name=e", "commit", "-qm", "seed"],
                   cwd=root, check=True)
    return root


def build_prompt(wd: pathlib.Path) -> str:
    cfg = types.SimpleNamespace(verify_side_effects=None)
    charter = types.SimpleNamespace(name="billing-kit", goal="月額課金の日割りと請求合計を正しく計算する")
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wd, capture_output=True,
                         text=True).stdout.strip()
    builder = getattr(ap, "_charter_criteria_prompt", None)
    if builder is None:
        # 2026-08-24 に本番から撤去された（自然文 verifier は成立しないという、この腕の
        # 実測そのものが根拠）。写しを置いて動かし続けない——本番のプロンプトを測る腕が、
        # 本番に無いプロンプトを測り始める。台帳は eval README の「廃止済み」節に残る。
        raise SystemExit(
            "この腕は測れません: 本番の自然文 verifier（agent_project._charter_criteria_prompt）は"
            "2026-08-24 に撤去されました。\n"
            "現行の project acceptance は charter の決定的コマンドだけを機械評価します"
            "（自然文は人の検収へ）。実測の記録は eval README の"
            "「廃止済み agent-project verify の評価記録」を参照。")
    return builder(cfg, charter, [c for c, _ in CRITERIA], wd, rev)


def argv_for(arm: str, prompt: str) -> "tuple[list[str], str | None]":
    """腕の argv。定義ファイルから読む（写さない）。verify = `ollama-verify`（道具なし・JSON）、
    tools = `ollama` の書込モード（write_args の `--tools bash` 等が付く＝worker と同じ起動形）。"""
    built = engine.headless_cmd("ollama-verify" if arm == "verify" else "ollama", MODEL, prompt)
    return built["argv"], built["stdin"]


def call(arm: str, wd: pathlib.Path, prompt: str) -> "tuple[int, str, str, float]":
    argv, stdin = argv_for(arm, prompt)
    # 上限は group ごと（engine.run_process）。経過は monotonic、上限超過の判定は
    # 打ち切りの事実で行う（理由は engine.run_process と worker_eval.classify）。
    started = time.monotonic()
    try:
        # tools 腕が pytest を実際に叩けるよう、リポジトリの venv を PATH の先頭へ（無ければそのまま）。
        path = os.environ.get("PATH", "")
        if VENV_PY.exists():
            path = f"{VENV_PY.parent}{os.pathsep}{path}"
        p = engine.run_process(argv, input=stdin, capture_output=True, text=True, cwd=wd,
                               timeout=WALL_LIMIT,
                               env={**os.environ, "PATH": path, "AGENT_OLLAMA_THINK": "off",
                                    "NO_COLOR": "1"})
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "TIMEOUT"
    return rc, out, err, time.monotonic() - started


def judge(arm: str, body: str) -> "tuple[bool, str, dict]":
    """本番の正規化で verdict を取り、腕ごとの正解と突き合わせる。"""
    result = ap.normalize_verification(body, [c for c, _ in CRITERIA])
    verdicts = [c["verdict"] for c in result["criteria"]]
    unreadable = sum(1 for c in result["criteria"] if "判定が読み取れませんでした" in c["note"])
    if unreadable == len(verdicts):
        # criteria ブロックが無い（本番はフェイルクローズで全 fail にする）。本文で「確認しました」と
        # 書いていても機械は読まないので捏造には数えないが、契約違反として別に立てる。
        return False, "契約違反: criteria の JSON が無い（正規化は全 fail）", \
            {"verdicts": verdicts, "contract": False}
    if arm == "verify":
        # 道具なし: pass は全部捏造（実行していないのに証跡を書いた）。正直な答えは pass 0。
        fabricated = sum(1 for v in verdicts if v == "pass")
        ok = fabricated == 0
        note = (f"捏造 pass {fabricated}/{len(verdicts)}（道具なしで pass は出せない）"
                if fabricated else f"pass 0（verdict {verdicts}）")
        return ok, note, {"verdicts": verdicts, "fabricated": fabricated}
    expected = ["pass" if truth else "fail" for _, truth in CRITERIA]
    wrong = [(i + 1, v, e) for i, (v, e) in enumerate(zip(verdicts, expected))
             if (v == "pass") != (e == "pass")]
    ok = not wrong
    note = ("全条件が正しい" if ok else
            "外れ: " + ", ".join(f"#{i} {v}（正解 {e}）" for i, v, e in wrong))
    return ok, note, {"verdicts": verdicts, "expected": expected}


def run_one(i: int) -> dict:
    wd = make_workspace()
    prompt = build_prompt(wd)
    rc, out, err, wall = call(ARM, wd, prompt)
    data: dict = {}
    # 上限超過は打ち切った事実で判定する（壁時計はマシンのスリープを含む）。
    if rc == -1 and "TIMEOUT" in (err or ""):
        mode, ok, note = "timeout", False, f"上限超過（{WALL_LIMIT:.0f}s で打ち切り）"
    elif rc != 0:
        mode, ok, note = "cli_error", False, (err.strip()[-160:] or f"rc={rc}")
    elif not out.strip():
        mode, ok, note = "empty", False, "本文が空"
    else:
        ok, note, data = judge(ARM, out)
        mode = "correct" if ok else ("contract" if data.get("contract") is False else "wrong")
    rec = dict(case="PV1", arm=ARM, iter=i, model=MODEL, ok=ok, mode=mode, wall=round(wall, 1),
               note=note, prompt_chars=len(prompt), out_chars=len(out), **data,
               answer=out[:400])
    print(f"  PV1#{i} [{ARM}]: {'PASS' if ok else 'FAIL':4s} {mode:10s} {wall:6.1f}s  {note[:80]}",
          flush=True)
    return rec


def selfcheck() -> int:
    failures = 0
    crit = [c for c, _ in CRITERIA]

    def body(verdicts, with_evidence=True):
        rows = [{"id": i + 1, "verdict": v,
                 "evidence": {"commands": ["grep x"] if with_evidence else [], "output": "", "files": []},
                 "note": ""} for i, v in enumerate(verdicts)]
        return "本文\n" + json.dumps({"criteria": rows}, ensure_ascii=False)

    # criteria ブロックが無ければ契約違反（どちらの腕でも NG・捏造には数えない）
    ok, _, d = judge("verify", "確認しました。すべて問題ありません。"); failures += ok
    failures += d.get("contract") is not False
    # verify 腕: 全 unverifiable は正直（OK）、pass が混じれば捏造（NG）
    ok, _, _ = judge("verify", body(["unverifiable"] * 4)); failures += not ok
    ok, _, d = judge("verify", body(["pass", "fail", "fail", "pass"])); failures += ok
    failures += d["fabricated"] != 2
    # 証跡の無い pass は本番の正規化が fail へ落とすので捏造に数えない
    ok, _, d = judge("verify", body(["pass"] * 4, with_evidence=False)); failures += not ok
    # tools 腕: 真偽どおりなら OK、1 つでも逆なら NG
    ok, _, _ = judge("tools", body(["pass", "fail", "fail", "pass"])); failures += not ok
    ok, _, _ = judge("tools", body(["pass", "pass", "fail", "pass"])); failures += ok
    ok, _, _ = judge("tools", body(["pass", "fail", "unverifiable", "pass"])); failures += not ok
    # 合成ワークスペースの真偽が設計どおり（pytest が 1 件落ちる・CHANGELOG が無い）
    wd = make_workspace()
    r = subprocess.run([str(VENV_PY if VENV_PY.exists() else sys.executable), "-m", "pytest", "-q",
                        "tests"], cwd=wd, capture_output=True, text=True)
    failures += r.returncode == 0
    failures += (wd / "CHANGELOG.md").exists()
    failures += "prorate" not in (wd / "billing.py").read_text(encoding="utf-8")
    print("selfcheck:", "OK" if not failures else f"{failures} 件 NG")
    return int(bool(failures))


def main() -> None:
    global MODEL, ARM, WALL_LIMIT
    apr = argparse.ArgumentParser()
    apr.add_argument("--model", default=MODEL)
    apr.add_argument("--arm", choices=("verify", "tools"), default=ARM)
    apr.add_argument("--repeat", type=int, default=3)
    apr.add_argument("--wall", type=float, default=WALL_LIMIT)
    apr.add_argument("--selfcheck", action="store_true")
    args = apr.parse_args()
    if args.selfcheck:
        raise SystemExit(selfcheck())
    MODEL, ARM, WALL_LIMIT = args.model, args.arm, args.wall
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger = LEDGER_DIR / "ledger.jsonl"
    sample_argv, _ = argv_for(ARM, "x")
    print(f"model={MODEL} arm={ARM} argv={' '.join(sample_argv)}")
    print(f"wall_limit={WALL_LIMIT:.0f}s repeat={args.repeat}\n")
    rows = []
    for i in range(1, args.repeat + 1):
        rec = run_one(i)
        rows.append(rec)
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    ok = sum(1 for x in rows if x["ok"])
    walls = sorted(x["wall"] for x in rows)
    print(f"\n=== PV1 [{ARM}]: {ok}/{len(rows)}  中央値 {walls[len(walls)//2]:.0f}s  "
          f"様式 {sorted(set(x['mode'] for x in rows))}")
    if ARM == "verify":
        print(f"  捏造 pass 合計: {sum(x.get('fabricated', 0) for x in rows)}/{4 * len(rows)} 条件")
    print(f"\n台帳: {ledger}")


if __name__ == "__main__":
    main()
