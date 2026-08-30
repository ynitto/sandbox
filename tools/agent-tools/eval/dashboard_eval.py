#!/usr/bin/env python3
"""agent-dashboard の構造化アシスト 8 面を測る（`coverage.json` の missing を埋める）。

**プロンプトも受け方も本番を呼ぶ**（写さない）。ビルダー（`taskAssistPrompt` /
`charterDraftPrompt` / `charterRefinePrompt` / `methodDraftPrompt` /
`routineAcceptancePrompt`）と、応答の受け方（`stripFence` → `extractJson` →
`normalize*`）はすべて `agent.js` の export を node 経由でそのまま使う。ハーネスがやるのは
argv を本番と同じに組んで実行することだけで、判定は**本番の正規化を通した後**の値に対して行う。

起動形は doctor と同じ読み取り専用（`readonly=True`）。dashboard のアシストは
「コマンド実行・ファイル変更をしない」と本文で明示している読み取り面で、`buildDoctorCommand`
経由で起動される。

測るのは決定的に判定できるものだけ——**契約**（必須キー・件数・型）と**捏造**（材料に無い
ID・パス・リポジトリを書かないか）。文章の質は測らない。

使い方: python3 dashboard_eval.py [--model gemma4:e4b] [--repeat 5] [--cases MD1,FS1] [--selfcheck]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import engine  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
AGENT_JS = REPO / "tools/agent-dashboard/src/features/agent-project/main/agent.js"
LEDGER_DIR = pathlib.Path(os.environ.get("DASHBOARD_EVAL_DIR", "/tmp/agent-dashboard-eval"))
MODEL = "gemma4:e4b"
AGENT_CLI = "ollama"        # dashboard のアシストは読み取り専用（変種の申告なし）
WALL_LIMIT = 600.0

# ------------------------------------------------------------------ 本番の JS を呼ぶ

_NODE_BRIDGE = """
const a = require(process.argv[1]);
let buf = '';
process.stdin.on('data', (d) => { buf += d; });
process.stdin.on('end', () => {
  const req = JSON.parse(buf);
  let out;
  if (req.op === 'prompt') {
    if (req.builder === 'taskAssist') out = a.taskAssistPrompt(req.mode, req.context, req.note || '');
    else if (req.builder === 'charterDraft') out = a.charterDraftPrompt(req.spec);
    else if (req.builder === 'charterRefine') out = a.charterRefinePrompt(req.content);
    else if (req.builder === 'methodDraft') out = a.methodDraftPrompt(req.brief, req.current);
    else if (req.builder === 'routineAcceptance') out = a.routineAcceptancePrompt(req.name, req.prompt, req.extra || '');
    else throw new Error('unknown builder ' + req.builder);
    process.stdout.write(JSON.stringify({ text: out }));
    return;
  }
  // op === 'parse': 本番と同じ受け方（フェンス剥がし → JSON 抽出 → モード別の正規化）
  const raw = req.raw || '';
  if (req.mode === 'charter-refine') {          // refine は全文テキストがそのまま成果
    process.stdout.write(JSON.stringify({ fields: { content: a.stripFence(raw).trim() } }));
    return;
  }
  const obj = a.extractJson(a.stripFence(raw));
  if (!obj) { process.stdout.write(JSON.stringify({ error: 'no-json' })); return; }
  let fields;
  if (req.mode === 'followup-suggest') fields = a.normalizeFollowupSuggestions(obj);
  else if (req.mode === 'source-task-candidates') fields = a.normalizeTaskCandidates(obj, []);
  else if (req.mode === 'task-guide') fields = a.normalizeTaskGuide(obj);
  else if (req.mode === 'enqueue-assist') fields = a.normalizeEnqueueAssist(obj);
  else if (req.mode === 'method-draft') fields = a.normalizeMethodDraft(obj);
  else if (req.mode === 'acceptance-draft') fields = a.normalizeAcceptanceDraft(obj);
  else if (req.mode === 'charter-draft') fields = a.normalizeDraftFields(obj);
  else throw new Error('unknown mode ' + req.mode);
  process.stdout.write(JSON.stringify({ fields }));
});
"""


def _node(payload: dict) -> dict:
    r = subprocess.run(["node", "-e", _NODE_BRIDGE, str(AGENT_JS)],
                       input=json.dumps(payload, ensure_ascii=False),
                       capture_output=True, text=True, timeout=60, cwd=str(AGENT_JS.parent))
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"agent.js の呼び出しに失敗: {r.stderr[:300]}")
    return json.loads(r.stdout)


# ------------------------------------------------------------------ 素材（正解は構成から従う）

CHARTER = {"goal": "run ログから日次のトークン消費レポートを作る",
           "acceptance": "python -m pytest -q tests が通る\nreports/digest.md が生成される"}

# 既存 backlog。**ID の集合が捏造判定の土台**——ここに無い ID を after に書いたら不合格。
BACKLOG = [
    {"id": "T1", "title": "run ログの読み込みを実装する", "status": "done"},
    {"id": "T2", "title": "日次のトークン集計を実装する", "status": "ready"},
    {"id": "T3", "title": "Markdown 表の出力を実装する", "status": "ready"},
]
BACKLOG_IDS = {t["id"] for t in BACKLOG}

SELECTED = {"id": "T2", "title": "日次のトークン集計を実装する",
            "verify": "python -m pytest -q tests/test_aggregate.py",
            "result": "PASS",
            "delivery": "集計は実装したが、月またぎの集計は未対応のまま残っている"}

NOTE_SOURCE = {"kind": "note", "name": "運用メモ",
               "content": "日次レポートに前日比を出したい。あと、失敗した run を一覧で見たい。"}

TASK_FOR_GUIDE = {"id": "T3", "title": "Markdown 表の出力を実装する",
                  "verify": "python -m pytest -q tests/test_render.py",
                  "acceptance": "reports/digest.md に日次の表が出る"}

ENQUEUE_DRAFT = {"title": "日次レポートを Markdown で書き出す",
                 "verify": "python -m pytest -q tests/test_render.py",
                 "note": "集計結果を使う"}

METHOD_BRIEF = "実装のとき、変更したファイルの一覧を最後に必ず書き出してほしい"

CHARTER_SPEC = {"name": "レポート基盤", "goal": "",
                "deliverables": "ingest.py\naggregate.py", "constraints": "", "assumptions": "",
                "acceptance": "",
                "memo": "run ログから日次のトークン消費を集計して Markdown で出す。"
                        "追加の依存は増やさない。"}

CHARTER_MD = """# Charter: レポート基盤

## goal
run ログから日次のトークン消費レポートを作る。

## deliverables
- ingest.py
- aggregate.py

## acceptance
- python -m pytest -q tests が通る

## repos
- agent-tools = https://example.invalid/agent-tools.git
"""

# ------------------------------------------------------------------ チェッカー
# すべて (ok, note) を返す。入力は**本番の正規化を通した後**の fields。


def _after_ids(items, key="after") -> "list[str]":
    out = []
    for item in items:
        for tid in (item.get(key) or []):
            out.append(str(tid))
    return out


def check_method_draft(f):
    """追加ルールの下書き。契約（id / role / text）と、**発明しない**を見る。"""
    role_ok = f.get("role") in {"session", "planner", "worker", "verify", "evaluator"}
    if not f.get("id") or not f.get("text"):
        return False, f"id / text が空: {f}"
    if not role_ok:
        return False, f"role={f.get('role')}（契約外）"
    if not all(c.islower() or c.isdigit() or c == "-" for c in f["id"]):
        return False, f"id が英小文字・数字・ハイフンでない: {f['id']}"
    when = f.get("when") or {}
    invented = [k for k, v in when.items() if v and k in ("purposes", "workloads", "agent_cli")]
    if invented:
        return False, f"要望に無い適用条件を発明した: {invented}"
    return True, f"id={f['id']} role={f['role']}・text {len(f['text'])} 字"


def check_acceptance_draft(f, want_path: str):
    """定期タスクの受入基準。件数（3〜7）と、**成果物パスがバッククォート表記**かを見る
    （本番はこの表記だけを機械照合の対象にする）。"""
    items = f.get("acceptance") or []
    if not (3 <= len(items) <= 7):
        return False, f"受入基準 {len(items)} 件（契約は 3〜7）"
    if not f.get("prompt"):
        return False, "prompt が空"
    quoted = [a for a in items if f"`{want_path}`" in a]
    if not quoted:
        return False, f"`{want_path}` のバッククォート表記が無い（機械照合できない）"
    return True, f"{len(items)} 件・パス表記あり"


def check_followup(f, want_token: str):
    """検収後のフォローアップ提案。件数（0〜5）・**既存 ID だけ**・不足への言及を見る。"""
    items = f.get("suggestions") or []
    if not items:
        return False, "提案 0 件（未対応が残っている検収結果を渡している）"
    if len(items) > 5:
        return False, f"{len(items)} 件（契約は 0〜5）"
    stray = [t for t in _after_ids(items) if t not in BACKLOG_IDS]
    if stray:
        return False, f"存在しない ID を after に書いた: {stray[0]}"
    blob = json.dumps(items, ensure_ascii=False)
    if want_token not in blob:
        return False, f"{want_token} に触れていない: {items[0].get('title', '')[:30]}"
    if any(not s.get("why") for s in items):
        return False, "why（必要な理由）が空の提案がある"
    return True, f"{len(items)} 件・既存 ID のみ・{want_token} を拾った"


def check_enqueue_assist(f, want_after: str):
    """投入補助。**既存 ID だけ**を使い、実在する前提タスクを after に置けるか。"""
    after = [str(t) for t in (f.get("after") or [])]
    stray = [t for t in after if t not in BACKLOG_IDS]
    adj_stray = [a["id"] for a in (f.get("adjustments") or [])
                 if a.get("id") and a["id"] not in BACKLOG_IDS]
    if stray or adj_stray:
        return False, f"存在しない ID: {(stray + adj_stray)[0]}"
    if want_after not in after:
        return False, f"after={after}（期待に {want_after} を含む）"
    return True, f"after={after} priority={f.get('priority')}"


def check_task_guide(f, forbidden: "list[str]"):
    """意図と境界の補完。必須キーの型と、**材料に無い固有名を書かない**を見る。"""
    for key in ("why", "desc", "scope", "acceptance", "risks", "size"):
        if key not in f:
            return False, f"{key} が無い（契約のキー欠落）"
    if not isinstance(f.get("risks"), list) or not isinstance(f.get("acceptance"), list):
        return False, "risks / acceptance が配列でない"
    if not f.get("why") or not f.get("acceptance"):
        return False, "why / acceptance が空"
    if f.get("size") not in ("S", "M", "L"):
        return False, f"size={f.get('size')}（契約は S/M/L）"
    blob = json.dumps(f, ensure_ascii=False)
    invented = [w for w in forbidden if w in blob]
    if invented:
        return False, f"材料に無い固有名を書いた: {invented[0]}"
    return True, f"size={f['size']}・受入 {len(f['acceptance'])} 件"


def check_task_candidates(f):
    """要望メモの分解。件数（1〜8）・受入基準あり・**既存 ID だけ**を見る。"""
    tasks = f.get("tasks") or []
    if not (1 <= len(tasks) <= 8):
        return False, f"{len(tasks)} 件（要望メモは 1〜8 件・空にしない契約）"
    empty = [t.get("title", "?") for t in tasks if not (t.get("acceptance") or [])]
    if empty:
        return False, f"受入基準が空の候補: {empty[0][:24]}"
    stray = [t for t in _after_ids(tasks) if t not in BACKLOG_IDS]
    if stray:
        return False, f"存在しない ID を after に書いた: {stray[0]}"
    return True, f"{len(tasks)} 件・受入基準あり・既存 ID のみ"


def check_charter_draft(f):
    """憲章の下書き。書きかけを尊重しつつ、空欄を材料の範囲で埋められるか。"""
    if not f.get("goal"):
        return False, "goal が空（メモに目的が書いてある）"
    for key in ("deliverables", "acceptance"):
        if not f.get(key):
            return False, f"{key} が空"
    if "ingest.py" not in f["deliverables"]:
        return False, "書きかけの deliverables（ingest.py）を落とした"
    return True, f"goal {len(f['goal'])} 字・成果物 {len(f['deliverables'].splitlines())} 行"


def check_charter_refine(f):
    """憲章の推敲。**書式を保つ**（見出し）と、repos を変更しないを見る。"""
    body = f.get("content") or ""
    if not body.strip():
        return False, "本文が空"
    missing = [h for h in ("# Charter:", "## goal", "## deliverables", "## acceptance", "## repos")
               if h not in body]
    if missing:
        return False, f"見出しが落ちた: {missing}"
    if "https://example.invalid/agent-tools.git" not in body:
        return False, "repos の URL を変えた（本番は変更禁止と明示）"
    return True, f"{len(body)} 字・書式と repos を保持"


# ------------------------------------------------------------------ ケース定義

CASES = {
    "MD1": dict(mode="method-draft", expect="契約充足・条件を発明しない",
                prompt=dict(builder="methodDraft", brief=METHOD_BRIEF, current={}),
                check=check_method_draft),
    "AC1": dict(mode="acceptance-draft", expect="3〜7 件・パスはバッククォート",
                prompt=dict(builder="routineAcceptance", name="日次ダイジェスト",
                            prompt="毎朝、前日の run ログをまとめて reports/digest.md に書く",
                            extra=""),
                check=lambda f: check_acceptance_draft(f, "reports/digest.md")),
    "FS1": dict(mode="followup-suggest", expect="未対応（月またぎ）を拾う・既存 ID のみ",
                prompt=dict(builder="taskAssist", mode="followup-suggest",
                            context={"charter": CHARTER, "backlog": BACKLOG,
                                     "selected": SELECTED}),
                check=lambda f: check_followup(f, "月またぎ")),
    "EA1": dict(mode="enqueue-assist", expect="after に T2（集計）・存在しない ID を作らない",
                prompt=dict(builder="taskAssist", mode="enqueue-assist",
                            context={"charter": CHARTER, "backlog": BACKLOG,
                                     "draft": ENQUEUE_DRAFT}),
                check=lambda f: check_enqueue_assist(f, "T2")),
    "TG1": dict(mode="task-guide", expect="必須キー充足・材料に無い固有名を書かない",
                prompt=dict(builder="taskAssist", mode="task-guide",
                            context={"charter": CHARTER, "backlog": BACKLOG,
                                     "task": TASK_FOR_GUIDE}),
                check=lambda f: check_task_guide(f, ["payments/", "Django", "PostgreSQL"])),
    "SC1": dict(mode="source-task-candidates", expect="要望メモを 1〜8 件へ・受入基準あり",
                prompt=dict(builder="taskAssist", mode="source-task-candidates",
                            context={"charter": CHARTER, "backlog": BACKLOG,
                                     "source": NOTE_SOURCE}),
                check=check_task_candidates),
    "CD1": dict(mode="charter-draft", expect="空欄を埋め、書きかけを落とさない",
                prompt=dict(builder="charterDraft", spec=CHARTER_SPEC),
                check=check_charter_draft),
    "CR1": dict(mode="charter-refine", expect="書式と repos を保った全文",
                prompt=dict(builder="charterRefine", content=CHARTER_MD),
                check=check_charter_refine),
}

# ------------------------------------------------------------------ 実行


def build_prompt(case: dict) -> str:
    return _node({"op": "prompt", **case["prompt"]})["text"]


def parse_output(mode: str, raw: str):
    """本番の受け方（stripFence → extractJson → normalize*）を通す。None は抽出不能。"""
    got = _node({"op": "parse", "mode": mode, "raw": raw})
    return None if got.get("error") else got["fields"]


def call(prompt: str, cwd: str) -> "tuple[int, str, str, float]":
    built = engine.headless_cmd(AGENT_CLI, MODEL, prompt, readonly=True)
    started = time.monotonic()
    try:
        p = engine.run_process(built["argv"], input=built["stdin"], capture_output=True,
                               text=True, timeout=WALL_LIMIT, cwd=cwd,
                               env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"})
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "TIMEOUT"
    return rc, out, err, time.monotonic() - started


def workdir_for(cid: str, i: int) -> str:
    root = LEDGER_DIR / "work" / f"{cid}-{i}"
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def run_one(cid: str, i: int) -> dict:
    case = CASES[cid]
    prompt = build_prompt(case)
    rc, out, err, wall = call(prompt, workdir_for(cid, i))
    fields = None
    if rc == -1 and "TIMEOUT" in (err or ""):
        mode, ok, note = "timeout", False, f"上限超過（{WALL_LIMIT:.0f}s で打ち切り）"
    elif rc != 0:
        mode, ok, note = "cli_error", False, (err.strip()[-120:] or f"rc={rc}")
    elif not out.strip():
        mode, ok, note = "empty", False, "本文が空"
    else:
        fields = parse_output(case["mode"], out)
        if fields is None:
            mode, ok, note = "unparsable", False, "本番の受け方で JSON を取り出せない"
        else:
            ok, note = case["check"](fields)
            mode = "correct" if ok else "wrong"
    rec = dict(case=cid, assist_mode=case["mode"], iter=i, model=MODEL, ok=ok, mode=mode,
               wall=round(wall, 1), note=note, prompt_chars=len(prompt), out_chars=len(out),
               answer=json.dumps(fields, ensure_ascii=False, default=str)[:300])
    if engine.missing():
        rec["engine_missing"] = engine.missing()
    print(f"  {cid}#{i}: {'PASS' if ok else 'FAIL':4s} {mode:11s} {wall:6.1f}s  {note[:66]}",
          flush=True)
    return rec


# ------------------------------------------------------------------ selfcheck


def selfcheck() -> int:
    """チェッカーを LLM 抜きで検証する（正解は通り、典型的な外し方は落ちる）。"""
    good = {
        "MD1": {"id": "list-changed-files", "description": "変更ファイルを列挙する",
                "role": "worker", "text": "作業の最後に変更したファイルの一覧を書き出すこと。",
                "when": {"tiers": [], "purposes": [], "workloads": [], "agent_cli": [],
                         "models": []}},
        "AC1": {"prompt": "毎朝 reports/digest.md を更新する",
                "acceptance": ["`reports/digest.md` が当日の日付で更新されている",
                               "前日の run 件数が本文にある", "失敗した run が一覧されている"]},
        "FS1": {"suggestions": [{"title": "月またぎの集計に対応する", "verify": "pytest -q",
                                 "accept": "", "priority": 3, "after": ["T2"],
                                 "note": "", "why": "月をまたぐと合計がずれるため"}]},
        "EA1": {"after": ["T2"], "priority": 5, "note": "", "rationale": "集計の後",
                "adjustments": []},
        "TG1": {"why": "レポートを人が読めるようにするため", "desc": "集計結果を表にする",
                "scope": "render.py", "out_of_scope": "", "constraints": "", "hints": "",
                "risks": ["なし"], "acceptance": ["reports/digest.md に表が出る"], "size": "S",
                "demo": "", "rationale": ""},
        "SC1": {"tasks": [{"title": "前日比を出す", "desc": "", "acceptance": ["前日比が出る"],
                           "priority": 0, "after": [], "why": ""},
                          {"title": "失敗 run の一覧を出す", "desc": "",
                           "acceptance": ["失敗が一覧される"], "priority": 0, "after": ["T2"],
                           "why": ""}]},
        "CD1": {"goal": "run ログから日次のトークン消費レポートを作る",
                "constraints": "追加の依存を増やさない", "assumptions": "",
                "deliverables": "ingest.py\naggregate.py\nrender.py",
                "acceptance": "pytest が通る"},
        "CR1": {"content": CHARTER_MD + "\n## constraints\n- 追加の依存を増やさない\n"},
    }
    bad = {
        "MD1": [{"id": "Rule One", "description": "", "role": "worker", "text": "x",
                 "when": {}},                                   # id の綴り
                {"id": "ok-id", "description": "", "role": "boss", "text": "x", "when": {}},
                {"id": "ok-id", "description": "", "role": "worker", "text": "x",
                 "when": {"purposes": ["plan"]}}],              # 要望に無い条件
        "AC1": [{"prompt": "x", "acceptance": ["a", "b"]},      # 件数
                {"prompt": "x", "acceptance": ["reports/digest.md がある", "b", "c"]}],  # 表記
        "FS1": [{"suggestions": []},
                {"suggestions": [{"title": "月またぎ", "after": ["T9"], "why": "x"}]},
                {"suggestions": [{"title": "別のこと", "after": [], "why": "x"}]}],
        "EA1": [{"after": ["T9"], "adjustments": []},
                {"after": [], "adjustments": []}],
        "TG1": [{"why": "", "desc": "", "scope": "", "acceptance": [], "risks": [], "size": "S"},
                {"why": "w", "desc": "d", "scope": "payments/", "out_of_scope": "",
                 "constraints": "", "hints": "", "risks": ["なし"], "acceptance": ["a"],
                 "size": "S"}],
        "SC1": [{"tasks": []},
                {"tasks": [{"title": "x", "acceptance": [], "after": []}]},
                {"tasks": [{"title": "x", "acceptance": ["a"], "after": ["T9"]}]}],
        "CD1": [{"goal": "", "deliverables": "a", "acceptance": "b"},
                {"goal": "g", "deliverables": "render.py", "acceptance": "b"}],
        "CR1": [{"content": "# Charter: x\n## goal\ny\n"},
                {"content": CHARTER_MD.replace("https://example.invalid/agent-tools.git",
                                               "https://other.invalid/x.git")}],
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
        print(f"  {cid:<4} {case['mode']:<22} prompt {len(prompt):>5,} 字  期待 {case['expect']}")
    for f in fails:
        print(f"  NG {f}")
    print(f"\nチェッカー自己診断: {'OK' if not fails else f'{len(fails)} 件 NG'}")
    return 1 if fails else 0


def main() -> None:
    global MODEL, AGENT_CLI, WALL_LIMIT
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--agent-cli", default=AGENT_CLI)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--wall", type=float, default=WALL_LIMIT)
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.selfcheck:
        raise SystemExit(selfcheck())
    MODEL, AGENT_CLI, WALL_LIMIT = args.model, args.agent_cli, args.wall
    cids = [c.strip() for c in args.cases.split(",") if c.strip() in CASES]

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger = LEDGER_DIR / "ledger.jsonl"
    built = engine.headless_cmd(AGENT_CLI, MODEL, "", readonly=True)
    for gap in engine.missing():
        print(f"   ⚠ この木にはエンジン機能が無い: {gap}（その分は測れていない）")
    print(f"model={MODEL}\n  assist: {' '.join(built['argv'])}"
          f"\nwall_limit={WALL_LIMIT:.0f}s cases={cids} repeat={args.repeat}\n")

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
        print(f"  {cid} ({CASES[cid]['mode']}): {sum(1 for x in r if x['ok'])}/{len(r)}  "
              f"中央値 {sorted(x['wall'] for x in r)[len(r) // 2]:.0f}s  "
              f"自己一貫性 {same}/{len(r)}  様式 {sorted(set(x['mode'] for x in r))}")
    print(f"\n  合計: {sum(1 for x in rows if x['ok'])}/{len(rows)}\n\n台帳: {ledger}")


if __name__ == "__main__":
    main()
