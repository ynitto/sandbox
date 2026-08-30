#!/usr/bin/env python3
"""agent-project の `route` / `doctor` を測る（`coverage.json` の missing を 2 面埋める）。

**プロンプトも受け方も本番を呼ぶ**（写さない）。agent_project は断片を 1 つの名前空間へ
exec 合成するパッケージなので、`import agent_project` すれば `_route_agent_prompt` /
`_doctor_prompt` / `_extract_json_obj` / `_parse_doctor_findings` がそのまま使える。

起動形も本番から取る（`engine.production_argv`）——`command` だけを読むと profile が足す
道具とラウンド上限が落ちる（2026-08-30 の実測: それで map の 5/5 が 1/5 になった）。
本番の振り替えは `route → ollama-json`（道具なし・JSON モード）、`doctor` は申告が無いので
base `ollama`（`--think off --tools bash --max-rounds 12`）。道具付きで走る面は
ワークスペース相当の一時ディレクトリを cwd にする。

測るのは決定的に判定できるものだけ:

  route  … 候補のうち**正しい 1 つ**を選ぶか／決められない問いで**空を返す**か
           （本番の契約が「判断できなければ `{"workspace": ""}`」と言っている）
  doctor … 所見を**正しいカテゴリ**へ置くか。`env` / `config` で説明できるものを
           `program` にしないか（本番のプロンプトが「保守的に」と明示している）

使い方: python3 project_eval.py [--model gemma4:e4b] [--repeat 5] [--cases RO1,PD1] [--selfcheck]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import engine  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools/agent-project"))
sys.path.insert(0, str(REPO / "tools/agent-tools/agentcore"))

try:
    import agent_project as ap  # noqa: E402
except Exception as exc:  # noqa: BLE001 — 本番が読めない木では測らない（黙って代替しない）
    raise SystemExit(f"agent_project を import できません: {exc}")

LEDGER_DIR = pathlib.Path(os.environ.get("PROJECT_EVAL_DIR", "/tmp/agent-project-eval"))
MODEL = "gemma4:e4b"
BASE_CLI = "ollama"
WALL_LIMIT = 600.0

# ------------------------------------------------------------------ 素材（正解は構成から従う）

# route の候補。owns: のグロブでは決まらない（タスクにパスのヒントが無い）ので、本番は
# ここで初めてエージェントに訊く——決定論で決まる問いはそもそも LLM まで来ない。
WORKSPACES = [
    {"name": "agent-tools", "owns": ["tools/agent-tools/**"],
     "desc": "評価ハーネスと共通基盤（agentcore・eval）"},
    {"name": "agent-dashboard", "owns": ["src/features/**"],
     "desc": "運用の管理 UI（オーケストレーション・工程の画面）"},
    {"name": "docs-site", "owns": ["docs/**"],
     "desc": "公開ドキュメント（利用者向けの手引き）"},
]


def task(title: str, acceptance: str) -> "ap.Task":
    return ap.Task(id="t1", title=title, verify="", extra=[("acceptance", acceptance)])


# doctor のシグナル。各ケースは**1 つの故障だけ**を仕込み、その故障が属するカテゴリを正解に
# する（複数を混ぜると、どれを見落としたのかが数字から読めない）。
def signals(runlog: "list[str]", **extra) -> dict:
    base = {"stats": {"done": 0, "failed": 3, "ready": 5},
            "runlog_tail": runlog, "needs": [], "blocked": []}
    base.update(extra)
    return base


PD1_SIGNALS = signals([
    "[worker] タスク t-101 を開始",
    "sh: agent-herd: command not found",
    "[worker] タスク t-101 失敗 (rc=127)",
    "sh: agent-herd: command not found",
    "[worker] タスク t-102 失敗 (rc=127)",
])
# config の故障は**決定層が覆っていないもの**を選ぶ。protect 未設定を使っていた初版は、
# 本番が既に決定的に検出して自動修正まで持っている（`doctor_audit_findings` が
# `fix_action: policy-protect` を付け、`_ensure_policy_protect` が追記する）——
# モデルに訊く必要が無い面を測っていた（「その面は本番にあるか」の一段深い形）。
# いま仕込むのは**設定どうしの矛盾**: 上限 20 秒に対して実行が毎回それを超えている。
# 決定的チェックにこの組み合わせは無く、シグナルから読むしかない。
PD2_SIGNALS = signals([
    "[worker] タスク t-201 開始",
    "[worker] タスク t-201 打ち切り（agent_timeout=20s 超過）",
    "[worker] タスク t-202 開始",
    "[worker] タスク t-202 打ち切り（agent_timeout=20s 超過）",
    "[worker] タスク t-203 打ち切り（agent_timeout=20s 超過）",
], config={"agent_timeout": 20, "agent_cli": "ollama", "model": "gemma4:e4b"})
PD3_SIGNALS = signals([
    "[worker] タスク t-301 を開始",
    'Traceback (most recent call last):',
    '  File "agent_project/needs.py", line 714, in _plan_rework',
    "    out = _run_agent_cli(_plan_rework_prompt(t, feedback), cfg.model)",
    "AttributeError: 'NoneType' object has no attribute 'model'",
    "[worker] タスク t-301 失敗 (rc=1)",
])

# 決定的チェックの既出所見（本番は必ず渡す）。エージェントの仕事は「これに足す」ことなので、
# 同じものを繰り返しても不合格にはしない——見るのはカテゴリの置き場所だけ。
DETERMINISTIC = [{"category": "config", "severity": "info",
                  "title": "decisions/ が空です", "evidence": "決定ログが 0 件",
                  "fix": "決定を書き残す", "source": "deterministic"}]

# ------------------------------------------------------------------ チェッカー
# すべて (ok, note) を返す。入力は**本番の受け方を通した後**の値。


def check_route(text: str, want: str):
    """書込先の選択。本番の受け方（`_extract_json_obj` → `workspace` → `_strip_code`）を通す。

    候補に無い名前は本番でも捨てられる（`smap.get(nm)` が None なら既定へ倒れる）ので、
    ここでも不合格にする。`want=""` は「判断できなければ空」の契約を測るケース。
    """
    data = ap._extract_json_obj(text)
    if data is None:
        return False, "JSON を抽出できない"
    got = ap._strip_code(str((data or {}).get("workspace") or "").strip())
    names = {w["name"] for w in WORKSPACES}
    if got and got not in names:
        return False, f"候補に無い名前: {got}"
    if got == want:
        return True, f"workspace={got or '（空＝判断できない）'}"
    return False, f"workspace={got or '（空）'}（期待 {want or '（空）'}）"


def check_doctor(text: str, want_category: str, evidence_token: str,
                 forbid: "tuple[str, ...]" = ()):
    """稼働診断。本番の受け方（`_parse_doctor_findings`）を通してからカテゴリだけを見る。

    見るのは 3 点——(1) 所見が 1 件以上あるか (2) 期待カテゴリの所見が、仕込んだ故障の
    語（コマンド名・例外名など）を根拠に挙げているか (3) 本番が禁じた置き場所
    （env / config で説明できるものを `program` にする）へ流していないか。
    文章の質は測らない。
    """
    findings = ap._parse_doctor_findings(text)
    if findings is None:
        return False, "所見を抽出できない"
    if not findings:
        return False, "所見 0 件（故障を仕込んである）"
    hit = [f for f in findings if f["category"] == want_category
           and evidence_token.lower() in f"{f['title']} {f['evidence']} {f['fix']}".lower()]
    stray = sorted({f["category"] for f in findings} & set(forbid))
    if not hit:
        cats = collections.Counter(f["category"] for f in findings)
        return False, f"{want_category} に {evidence_token} の所見が無い（内訳 {dict(cats)}）"
    if stray:
        return False, f"{evidence_token} を {stray} へも流した（本番は保守的な分類を求める）"
    return True, f"{want_category}: {hit[0]['title'][:40]}（所見 {len(findings)} 件）"


# ------------------------------------------------------------------ ケース定義

CASES = {
    # --- route（本番は決定論で決まらなかったときだけ訊く）
    "RO1": dict(purpose="route", expect="agent-tools",
                task=task("評価ハーネスに retrieve のケースを足す",
                          "judge_eval に RT1 / RT2 が入っている"),
                check=lambda t: check_route(t, "agent-tools")),
    "RO2": dict(purpose="route", expect="agent-dashboard",
                task=task("オーケストレーションタブに一時停止ボタンを足す",
                          "画面から run を pause できる"),
                check=lambda t: check_route(t, "agent-dashboard")),
    # 候補のどれにも属さない仕事。本番の契約は「判断できなければ空」——RT2 と同じ、
    # **決められないと言えるか**の面である（無理に 1 つ選ぶと誤った repo へコミットする）。
    "RO3": dict(purpose="route", expect="（空＝判断できない）",
                task=task("社内の勉強会で使う発表スライドを作る",
                          "スライドが 10 枚ある"),
                check=lambda t: check_route(t, "")),
    # --- doctor（本番は base ollama＝道具ループの中で走る）
    "PD1": dict(purpose="doctor", expect="env（コマンド不在）",
                signals=PD1_SIGNALS,
                check=lambda t: check_doctor(t, "env", "agent-herd", forbid=("program",))),
    "PD2": dict(purpose="doctor", expect="config（上限が短すぎる）",
                signals=PD2_SIGNALS,
                check=lambda t: check_doctor(t, "config", "timeout", forbid=("program",))),
    # 逆向きの面: 正しい環境・正しい設定でも再現する不具合を program と言えるか。
    "PD3": dict(purpose="doctor", expect="program（AttributeError）",
                signals=PD3_SIGNALS,
                check=lambda t: check_doctor(t, "program", "AttributeError")),
}

# ------------------------------------------------------------------ 実行


def build_prompt(case: dict) -> str:
    """本番のビルダーをそのまま呼ぶ（写さない）。"""
    if case["purpose"] == "route":
        return ap._route_agent_prompt(case["task"], WORKSPACES)
    return ap._doctor_prompt(case["signals"], DETERMINISTIC)


def cmd_for(purpose: str) -> "tuple[list[str], str]":
    """本番がこの処理で起こす argv。

    振り替えも readonly も **agent-project の解決器**に訊く（agent-flow のものではない）
    ——同じ `variants` 宣言へ行き着くが、経路は別実装（`_slashroute.resolve` と
    `_agent_readonly`）で、readonly の既定も違う（flow は役割集合・project は設定のみ）。
    測る側が別の規則を持つと、本番と食い違ったまま緑になる。
    """
    routed = ap._slashroute.resolve(command=purpose, cli=BASE_CLI, model=None,
                                    explicit_model=False)
    return engine.production_argv(routed["agent_cli"], MODEL, ap._agent_readonly(purpose))


def workdir_for(cid: str, i: int) -> str:
    """道具付きの起動形はワークスペース相当のディレクトリで走らせる（リポジトリの中では走らせない）。"""
    root = LEDGER_DIR / "work" / f"{cid}-{i}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def call(prompt: str, argv: "list[str]", cwd: str) -> "tuple[int, str, str, float]":
    # 上限は group ごと（engine.run_process）。孫を残すと次の実行が順番待ちになる。
    # 経過は monotonic——壁時計はマシンのスリープを含む。
    started = time.monotonic()
    try:
        p = engine.run_process(argv, input=prompt, capture_output=True, text=True,
                               timeout=WALL_LIMIT, cwd=cwd,
                               env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"})
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "TIMEOUT"
    return rc, out, err, time.monotonic() - started


def run_one(cid: str, i: int) -> dict:
    case = CASES[cid]
    prompt = build_prompt(case)
    argv, _source = cmd_for(case["purpose"])
    rc, out, err, wall = call(prompt, argv, workdir_for(cid, i))
    # 上限超過は**打ち切った事実**（rc と TIMEOUT マーカー）で判定する。
    if rc == -1 and "TIMEOUT" in (err or ""):
        mode, ok, note = "timeout", False, f"上限超過（{WALL_LIMIT:.0f}s で打ち切り）"
    elif rc != 0:
        mode, ok, note = "cli_error", False, (err.strip()[-120:] or f"rc={rc}")
    elif not out.strip():
        mode, ok, note = "empty", False, "本文が空"
    else:
        ok, note = case["check"](out)
        mode = "correct" if ok else "wrong"
    log = ""
    for line in err.splitlines():
        if line.startswith("@agent-log"):
            log = line.split(None, 1)[-1]
    rec = dict(case=cid, purpose=case["purpose"], iter=i, model=MODEL, ok=ok, mode=mode,
               wall=round(wall, 1), note=note, prompt_chars=len(prompt),
               out_chars=len(out), answer=out[:300], log=log)
    if engine.missing():  # 欠けた木で取った行を、揃った木の行として読まないため
        rec["engine_missing"] = engine.missing()
    print(f"  {cid}#{i}: {'PASS' if ok else 'FAIL':4s} {mode:10s} {wall:6.1f}s  {note[:70]}",
          flush=True)
    return rec


# ------------------------------------------------------------------ selfcheck


def selfcheck() -> int:
    """チェッカーを LLM 抜きで検証する（正解は通り、典型的な外し方は落ちる）。"""
    def finding(cat, title, evidence="", sev="warn"):
        return {"category": cat, "severity": sev, "title": title,
                "evidence": evidence, "fix": ""}

    good = {
        "RO1": '{"workspace": "agent-tools"}',
        "RO2": 'JSON: {"workspace": "agent-dashboard"}',
        "RO3": '{"workspace": ""}',
        "PD1": json.dumps([finding("env", "agent-herd が PATH にありません",
                                   "run-log に command not found")], ensure_ascii=False),
        "PD2": json.dumps([finding("config", "agent_timeout が短すぎます",
                                   "20s の timeout で全タスクが打ち切られている")],
                          ensure_ascii=False),
        "PD3": json.dumps([finding("program", "AttributeError で worker が落ちる",
                                   "needs.py:714 の traceback")], ensure_ascii=False),
    }
    bad = {
        "RO1": ['{"workspace": "agent-dashboard"}', '{"workspace": ""}',
                '{"workspace": "agent-core"}',        # 候補に無い名前
                "agent-tools です"],                  # JSON でない
        "RO2": ['{"workspace": "docs-site"}'],
        # 決められない問いで無理に 1 つ選ぶ形（本番なら誤った repo へコミットする）
        "RO3": ['{"workspace": "docs-site"}'],
        "PD1": [json.dumps([finding("program", "agent-herd の呼び出しが落ちる",
                                    "command not found")], ensure_ascii=False),
                json.dumps([finding("env", "agent-herd が無い", "command not found"),
                            finding("program", "同じ不具合", "command not found")],
                           ensure_ascii=False),
                "[]", "所見はありません"],
        "PD2": [json.dumps([finding("program", "timeout で打ち切られる", "agent_timeout")],
                           ensure_ascii=False),
                json.dumps([finding("config", "agent_timeout が短い", "timeout"),
                            finding("program", "打ち切りの実装が悪い", "timeout")],
                           ensure_ascii=False)],
        "PD3": [json.dumps([finding("env", "AttributeError が出る", "traceback")],
                           ensure_ascii=False), "[]"],
    }
    fails = []
    for cid, case in CASES.items():
        ok, note = case["check"](good[cid])
        if not ok:
            fails.append(f"{cid}: 正解が落ちた（{note}）")
        for n, wrong in enumerate(bad.get(cid, []), 1):
            ok, note = case["check"](wrong)
            if ok:
                fails.append(f"{cid}: 不正解 #{n} を通した（{note}）")
        prompt = build_prompt(case)
        print(f"  {cid:<4} {case['purpose']:<7} prompt {len(prompt):>5,} 字  期待 {case['expect']}")
    for f in fails:
        print(f"  NG {f}")
    print(f"\nチェッカー自己診断: {'OK' if not fails else f'{len(fails)} 件 NG'}")
    return 1 if fails else 0


def main() -> None:
    global MODEL, BASE_CLI, WALL_LIMIT
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--model", default=MODEL)
    ap_.add_argument("--base-cli", default=BASE_CLI)
    ap_.add_argument("--repeat", type=int, default=5)
    ap_.add_argument("--cases", default=",".join(CASES))
    ap_.add_argument("--wall", type=float, default=WALL_LIMIT)
    ap_.add_argument("--selfcheck", action="store_true",
                     help="LLM を呼ばずにチェッカーとプロンプトだけ確かめる")
    args = ap_.parse_args()
    if args.selfcheck:
        raise SystemExit(selfcheck())
    MODEL, BASE_CLI, WALL_LIMIT = args.model, args.base_cli, args.wall
    cids = [c.strip() for c in args.cases.split(",") if c.strip() in CASES]

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger = LEDGER_DIR / "ledger.jsonl"
    seen = []
    for purpose in dict.fromkeys(CASES[c]["purpose"] for c in cids):
        argv, source = cmd_for(purpose)
        line = f"{purpose}: {' '.join(argv)} （出所: {source}）"
        if line not in seen:
            seen.append(line)
    for gap in engine.missing():
        print(f"   ⚠ この木にはエンジン機能が無い: {gap}（その分は測れていない）")
    print(f"model={MODEL}\n  " + "\n  ".join(seen)
          + f"\nwall_limit={WALL_LIMIT:.0f}s cases={cids} repeat={args.repeat}\n")

    rows = []
    for cid in cids:
        for i in range(1, args.repeat + 1):
            rec = run_one(cid, i)
            rows.append(rec)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("\n=== 正解一致率（構成的ラベル）")
    for cid in cids:
        r = [x for x in rows if x["case"] == cid]
        same = collections.Counter(x["answer"] for x in r).most_common(1)[0][1]
        print(f"  {cid} ({CASES[cid]['purpose']}): {sum(1 for x in r if x['ok'])}/{len(r)}  "
              f"中央値 {sorted(x['wall'] for x in r)[len(r) // 2]:.0f}s  "
              f"自己一貫性 {same}/{len(r)}  様式 {sorted(set(x['mode'] for x in r))}")
    by_purpose = collections.defaultdict(list)
    for x in rows:
        by_purpose[x["purpose"]].append(x)
    print("\n=== 処理別")
    for purpose, r in by_purpose.items():
        print(f"  {purpose}: {sum(1 for x in r if x['ok'])}/{len(r)}")
    print(f"\n  合計: {sum(1 for x in rows if x['ok'])}/{len(rows)}\n\n台帳: {ledger}")


if __name__ == "__main__":
    main()
