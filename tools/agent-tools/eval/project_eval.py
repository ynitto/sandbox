#!/usr/bin/env python3
"""agent-project の LLM 呼び出し面を測る（`coverage.json` の missing を埋める）。

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

**本番の関数をそのまま走らせる。** 多くの処理は `agent_run=` の注入点を持ち、持たないものも
`_run_agent_cli` を差し替えれば本番の経路（プロンプト組み立て → CLI → 受け方 → 正規化 →
リトライ）が丸ごと動く。ハーネスがやるのは argv を本番と同じに組んで実行することだけで、
判定はすべて**本番の戻り値**に対して行う。写しを書かないので、受け方が変わった日に測定だけ
古いまま通る、が起きない。

使い方: python3 project_eval.py [--model gemma4:e4b] [--repeat 5] [--cases RO1,PD1] [--selfcheck]
"""
from __future__ import annotations

import argparse
import collections
import inspect
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import types

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

# prioritize の素材。正解は**構成から従う**——priority の大きい順で、同値は依存
# （t4 は t1 の後）で決まる。t3(9) → t1(5) → t4(5・t1 の後) → t2(1)。
def _ptask(tid: str, title: str, priority: int, **extra) -> "ap.Task":
    return ap.Task(id=tid, title=title, priority=priority,
                   extra=[(k, v) for k, v in extra.items()])


PRIORITIZE_TASKS = [
    _ptask("t1", "集計の日次合計を実装する", 5),
    _ptask("t2", "README に使い方を追記する", 1),
    _ptask("t3", "本番で落ちている読み込みエラーを直す", 9),
    _ptask("t4", "集計結果を Markdown 表で出力する", 5, after="t1"),
]

ESCALATE_TASK = ap.Task(
    id="t9", title="課金プランの料金体系を実装する", retries=3,
    verify="", extra=[("acceptance", "新しい料金体系で請求額が正しい"),
                      ("note", "月額か従量かが未決。過去に 2 度差し戻している")])

# assess の素材は**呼ぶたびに作り直す**。本番は採点結果を `task.extra` へ書き戻し、
# 既に `assess` があるタスクは LLM を呼ばずに返す（1 タスク 1 回）。使い回すと 2 回目以降が
# 「呼ばれなかった」になり、モデルの数字ではなく素材の汚染を測ることになる。
def assess_risky() -> "ap.Task":
    return ap.Task(id="t10", title="決済テーブルのスキーマを移行する", verify="",
                   extra=[("acceptance", "既存の支払い履歴が失われない"),
                          ("note", "payments/ と migrations/ を触る。本番データの移行を伴う")])


def assess_clear() -> "ap.Task":
    return ap.Task(id="t11", title="README のタイポを直す",
                   verify="python -m pytest -q tests/test_docs.py",
                   extra=[("acceptance", "tests/test_docs.py が通る")])


def _sample_repo(cwd: str) -> str:
    """repo_map 用の小さな git リポジトリを作って返す（本番は URL を shallow clone する）。"""
    root = pathlib.Path(cwd) / "sample-repo"
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "src" / "ingest.py").write_text("def load(path):\n    return open(path).read()\n",
                                            encoding="utf-8")
    (root / "tests" / "test_ingest.py").write_text("def test_load():\n    assert True\n",
                                                   encoding="utf-8")
    (root / "Makefile").write_text("test:\n\tpython -m pytest -q tests\n", encoding="utf-8")
    (root / "README.md").write_text("# sample\n\nビルドは `make test`。\n", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "eval", "GIT_AUTHOR_EMAIL": "eval@example.invalid",
           "GIT_COMMITTER_NAME": "eval", "GIT_COMMITTER_EMAIL": "eval@example.invalid"}
    for argv in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                 ["git", "commit", "-qm", "init"]):
        subprocess.run(argv, cwd=str(root), env=env, check=True,
                       capture_output=True, text=True)
    return str(root)


# ------------------------------------------------------------------ 本番を走らせる土台
# 多くの処理は `agent_run=` を受けるが、plan / review / repo_map は内部で `_run_agent_cli` を
# 直接呼ぶ。agent_project は断片を 1 名前空間へ合成するので、そこを差し替えれば**本番の経路が
# 丸ごと動く**（プロンプト・受け方・正規化・リトライまで）。ハーネスは argv を本番と同じに
# 組んで実行するだけで、判定は本番の戻り値に対して行う。

_LAST_CALLS: "list[dict]" = []   # 1 ケース内で本番が何回 LLM を呼んだか（リトライの可視化）


def _agent_runner(cwd: str):
    """本番の `_run_agent_cli` と同じ形（prompt, model, purpose=...）で呼べる実行器。"""
    def run(prompt: str, model: "str | None" = None, purpose: str = "") -> str:
        argv, _src = cmd_for(purpose or "plan")
        rc, out, err, wall = call(prompt, argv, cwd)
        _LAST_CALLS.append({"purpose": purpose, "rc": rc, "wall": round(wall, 1),
                            "prompt_chars": len(prompt), "out_chars": len(out),
                            "err": (err or "").strip()[-200:],
                            "tail": (out or "").strip()[-300:]})
        if rc != 0 or not out.strip():
            # 本番と同じ形の失敗（呼び出し側が握って決定的フォールバックへ倒す）
            raise RuntimeError(err.strip()[-160:] or f"rc={rc}")
        return out
    return run


_ARGS_FIELD = re.compile(r"""args\.([A-Za-z_][A-Za-z0-9_]*)|getattr\(args,\s*["']([A-Za-z_][A-Za-z0-9_]*)["']""")


def project_config(root: str, **overrides):
    """本番の `build_config` で Config を組む（`_RUNTIME_CONFIG` もこれで確定する）。

    引数の名前空間は**本番の実装から読む**——`build_config` が触る `args.<name>` を拾って
    既定 None で埋める。写しの一覧を置くと、本番が新しい設定を足した日に静かにずれる
    （足りなければ `AttributeError` で止まるので、黙って別物を測ることにはならない）。
    """
    fields = {a or b for a, b in _ARGS_FIELD.findall(inspect.getsource(ap.build_config))}
    args = types.SimpleNamespace(**{f: None for f in fields})
    for key, value in dict(root=root, agent_cli=BASE_CLI, model=MODEL,
                           agent_timeout=WALL_LIMIT, executor="agent",
                           granularity="coarse", distill_learn=True, **overrides).items():
        setattr(args, key, value)
    return ap.build_config(args)


CHARTER_TEXT = """# Charter: レポート基盤

## goal
run ログから日次のトークン消費レポートを作る仕組みを用意する。収集・集計・出力の 3 段で、
追加の依存は増やさない方針。

## deliverables
- ingest.py（run ログの読み込み）
- aggregate.py（日次のトークン集計）
- render.py（Markdown 表の出力）

## constraints
- 追加の依存を増やさない（標準ライブラリのみ）
- 既存の decisions/ の形式を壊さない

## repos
- agent-tools = https://example.invalid/agent-tools.git
"""


def charter() -> "ap.Charter":
    return ap.parse_charter(CHARTER_TEXT)


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


# 「書込先を名乗らなかった」と「無いものを名乗った」は別物である。本番は空欄なら決定的解決
# （rule → owns → 既定 → 候補が 1 つ）へ倒すので、空欄・「なし」は捏造として数えない。
_NO_WORKSPACE = {"", "なし", "none", "null", "n/a", "未定", "-"}


def _named_workspace(spec: dict) -> str:
    name = str(spec.get("workspace") or "").strip()
    return "" if name.lower() in _NO_WORKSPACE else name


def check_plan(specs):
    """分解。判定は**本番の決定的ゲート**（`_validate_backlog_spec`）をそのまま使う。

    見るのは 3 点——(1) タスクが 1 件以上あるか (2) 必須セクション
    （why / desc / scope / risks / acceptance / size）が全タスクで埋まっているか
    (3) 書込先が charter の repos の中か。本番は欠落を 1 回だけ再要求し、それでも欠ける
    ものは人の目へ回す（捨てない）——その 1 回もこのハーネスでは本番の関数が回している。
    """
    if not specs:
        return False, "タスクが 0 件"
    names = {r.get("name") for r in charter().repo_specs} or {"agent-tools"}
    bad = [(sp.get("title", "?")[:20], m) for sp in specs if (m := ap._validate_backlog_spec(sp))]
    if bad:
        return False, f"必須セクション欠落 {len(bad)} 件: {bad[0][0]}→{bad[0][1]}"
    stray = [w for sp in specs if (w := _named_workspace(sp)) and w not in names]
    if stray:
        return False, f"charter に無い書込先: {stray[0]}"
    return True, f"{len(specs)} 件・必須セクションはすべて充足"


def check_repo_map(text: str, want: "list[list[str]]"):
    """リポジトリ理解の要約。**構成から決まる事実**へ触れているかだけを見る。

    各要素は綴りの揺れを許す候補群（`["make test", "pytest"]` のどれかが出ればよい）。
    文章の質は測らない——本番はこの本文をそのまま `context/<repo>.md` に保存して
    planner へ渡すので、要るのは「実在する構成を読めたか」だけである。
    """
    body = text or ""
    if not body.strip():
        return False, "要約が空（clone / 実行に失敗）"
    missing = [group[0] for group in want if not any(w in body for w in group)]
    if missing:
        return False, f"実在するのに触れていない: {missing}"
    return True, f"{len(body)} 字・{len(want)} 個の構成要素に言及"


def check_review(specs):
    """敵対的レビュー。**判断の当否は測らない**——本番のプロンプトには憲章しか入らず、
    現状の成果物は 1 文字も届かないからである（CLI も `ollama-json` で道具なし＝
    PV1 と同じ「材料が無い」構造）。何が未達かを当てられる材料が無い以上、当否を採点すると
    モデルではなく配線を測ることになる。

    代わりに、材料なしでも決定的に見える 2 点を見る——(1) 本番の受け方を通った各項目が
    契約（title / acceptance / workspace）を満たすか (2) **charter に無いリポジトリを
    書いていないか**（捏造の面）。空配列も正解（本番が「問題が無ければ []」と言っている）。
    """
    names = {r.get("name") for r in charter().repo_specs}
    if not specs:
        return True, "所見なし＝空配列（材料が無い面での安全側）"
    broken = [sp.get("title", "?")[:20] for sp in specs
              if not str(sp.get("title") or "").strip() or not sp.get("acceptance")]
    if broken:
        return False, f"契約違反（title/acceptance 欠落）: {broken[0]}"
    stray = [w for sp in specs if (w := _named_workspace(sp)) and w not in names]
    if stray:
        return False, f"charter に無いリポジトリ: {stray[0]}"
    return True, f"{len(specs)} 件・契約充足・捏造なし"


def check_distill(pair, verbatim, forbidden: "list[str]"):
    """学習ルールの蒸留。本番は `<条件> :: <指針>` の 1 行だけを採り、形式外は生の指摘へ倒す。

    見るのは (1) 生フォールバックでないか (2) タスク固有の固有名詞を一般化したか
    （イシュー番号・ファイル名を残したら蒸留になっていない）。
    """
    if pair == verbatim:
        return False, "蒸留できず生フォールバック（形式外の出力）"
    cond, guide = pair
    left = [w for w in forbidden if w in cond or w in guide]
    if left:
        return False, f"固有名詞が残った: {left}"
    return True, f"{cond[:24]} :: {guide[:24]}"


def check_priority(order, want: "list[str]"):
    """優先順位付け。本番は ID 配列を受けて `ready` を並べ替える（未知 ID は無視）。"""
    if order is None:
        return False, "並べ替えを返さなかった（本番は元順のまま進む）"
    got = [t.id for t in order]
    if got == want:
        return True, f"順序 {got}"
    return False, f"順序 {got}（期待 {want}）"


def check_adjudicate(result, want: str):
    """門番。本番は `requeue` 以外をすべて `escalate` へ倒す（安全側）。"""
    decision, guidance = result
    if decision != want:
        return False, f"decision={decision}（期待 {want}）"
    if want == "requeue" and not guidance:
        return False, "requeue なのに次の試行への指示が空"
    return True, f"decision={decision}" + (f"・指示 {len(guidance)} 字" if guidance else "")


def check_assess(value: str, want: dict):
    """事前アセスメント。本番は `c=… r=… a=…` の文字列を返し、失敗時はヒューリスティックへ倒す。

    採点の主観は測らない——**仕込んだ軸だけ**（決済・移行なら r=3、具体的な verify があれば
    a=1）を見る。ヒューリスティックと同じ値になった回は、モデルが答えたのか倒れたのかを
    区別できないので note に残す。
    """
    got = dict(pair.split("=") for pair in (value or "").split())
    if set(got) != {"c", "r", "a"}:
        return False, f"契約外: {value}"
    bad = {k: got[k] for k, v in want.items() if got.get(k) != str(v)}
    if bad:
        return False, f"{bad}（期待 {want}）・全体 {value}"
    return True, value


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
    # --- 本番の関数をそのまま走らせる面（プロンプト・受け方・リトライまで本番）
    "PP1": dict(purpose="plan", expect="必須セクションを満たすタスク群",
                driver=lambda cwd: ap.plan_via_agent(project_config(cwd), charter()),
                check=check_plan),
    "RM1": dict(purpose="repo_map", expect="実在する構成へ言及",
                driver=lambda cwd: ap._repo_map_generate(project_config(cwd),
                                                         {"url": _sample_repo(cwd), "base": ""}),
                check=lambda t: check_repo_map(
                    t, [["ingest"], ["tests", "テスト"], ["make test", "pytest", "Makefile"]])),
    "RV1": dict(purpose="review", expect="契約充足・捏造なし（材料が無い面）",
                driver=lambda cwd: ap.review_via_agent(project_config(cwd), charter()),
                check=check_review),
    "DS1": dict(purpose="distill", expect="固有名詞を引き上げた 1 行",
                driver=lambda cwd: ap.distill_learn(
                    project_config(cwd), "請求書 PDF の出力を追加する",
                    "issue #4821 で指摘したとおり、billing/pdf.py の丸めが仕様と違います。"
                    "税込みの端数は切り上げに統一してください。"),
                check=lambda r: check_distill(
                    r, ("請求書 PDF の出力を追加する",
                        "issue #4821 で指摘したとおり、billing/pdf.py の丸めが仕様と違います。"
                        "税込みの端数は切り上げに統一してください。"),
                    ["#4821", "billing/pdf.py"])),
    "PR1": dict(purpose="prioritize", expect="priority と依存を加味した順序",
                driver=lambda cwd: ap.rank_agent(PRIORITIZE_TASKS, MODEL),
                check=lambda o: check_priority(o, ["t3", "t1", "t4", "t2"])),
    "AD1": dict(purpose="adjudicate", expect="requeue（指示があれば次で解ける）",
                driver=lambda cwd: ap.adjudicate_escalation(
                    project_config(cwd),
                    task("集計スクリプトに日次の合計を足す",
                         "python -m pytest -q tests/test_aggregate.py が通る"),
                    "テストが 1 件失敗: test_daily_total で合計が 1 日ぶんずれている"),
                check=lambda r: check_adjudicate(r, "requeue")),
    "AD2": dict(purpose="adjudicate", expect="escalate（人の意思決定が要る）",
                driver=lambda cwd: ap.adjudicate_escalation(
                    project_config(cwd), ESCALATE_TASK,
                    "3 回とも同じ理由で失敗: 料金体系のどれを採るか決まっていない"),
                check=lambda r: check_adjudicate(r, "escalate")),
    "AS1": dict(purpose="assess", expect="r=3（決済・移行のリスク）",
                driver=lambda cwd: ap.assess_task(project_config(cwd), assess_risky()),
                check=lambda v: check_assess(v, {"r": 3})),
    "AS2": dict(purpose="assess", expect="a=1（verify が具体的）",
                driver=lambda cwd: ap.assess_task(project_config(cwd), assess_clear()),
                check=lambda v: check_assess(v, {"a": 1})),
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


def run_driver(cid: str, i: int, case: dict) -> dict:
    """本番の関数をそのまま走らせる経路。`_run_agent_cli` を差し替えるだけで、
    プロンプト・受け方・正規化・リトライは本番のコードが回す。"""
    cwd = workdir_for(cid, i)
    _LAST_CALLS.clear()
    original = ap._run_agent_cli
    ap._run_agent_cli = _agent_runner(cwd)
    try:
        result = case["driver"](cwd)
    except Exception as exc:  # noqa: BLE001 — 本番も握る失敗（決定的フォールバックへ倒れる）
        ok, note, mode = False, f"{type(exc).__name__}: {exc}"[:140], "cli_error"
        result = None
    else:
        ok, note = case["check"](result)
        mode = "correct" if ok else "wrong"
    finally:
        ap._run_agent_cli = original
    wall = sum(c["wall"] for c in _LAST_CALLS)
    # 本番は CLI の失敗を握って決定的フォールバックへ倒す。握られた理由をここで残さないと、
    # 「モデルが答えられなかった」と「起動できていない」が台帳から区別できない。
    failed = [c for c in _LAST_CALLS if c["rc"] != 0 or c["out_chars"] == 0]
    if failed and not ok:
        note = f"{note}｜CLI 失敗 {len(failed)}/{len(_LAST_CALLS)}: {failed[0]['err'][:80]}"
    if not _LAST_CALLS:
        ok, note, mode = False, "本番が LLM を 1 回も呼ばなかった（決定層で完結）", "no_call"
    return dict(case=cid, purpose=case["purpose"], iter=i, model=MODEL, ok=ok, mode=mode,
                wall=round(wall, 1), note=note, calls=len(_LAST_CALLS),
                prompt_chars=max((c["prompt_chars"] for c in _LAST_CALLS), default=0),
                out_chars=max((c["out_chars"] for c in _LAST_CALLS), default=0),
                answer=json.dumps(result, ensure_ascii=False, default=str)[:300], log="",
                tail=(_LAST_CALLS[-1]["tail"] if _LAST_CALLS else ""))


def run_one(cid: str, i: int) -> dict:
    case = CASES[cid]
    if "driver" in case:
        rec = run_driver(cid, i, case)
        if engine.missing():
            rec["engine_missing"] = engine.missing()
        print(f"  {cid}#{i}: {'PASS' if rec['ok'] else 'FAIL':4s} {rec['mode']:10s} "
              f"{rec['wall']:6.1f}s  呼び出し {rec['calls']}  {rec['note'][:60]}", flush=True)
        return rec
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
        # driver ケースは本番の戻り値をそのまま渡す
        "PP1": [{"title": "ingest.py を実装する", "why": "読み込みが無い",
                 "desc": "run ログを読む", "scope": "ingest.py", "risks": "なし",
                 "acceptance": ["テストが通る"], "size": "S", "workspace": "agent-tools"}],
        "RM1": "構造: src/ingest.py と tests。テストは make test で実行する。",
        "RV1": [{"title": "テストを足す", "acceptance": ["pytest が通る"],
                 "workspace": "agent-tools"}],
        "DS1": ("請求まわりの丸め仕様の変更", "税込みの端数処理は仕様の丸め方向へ統一する"),
        "PR1": [PRIORITIZE_TASKS[2], PRIORITIZE_TASKS[0], PRIORITIZE_TASKS[3],
                PRIORITIZE_TASKS[1]],
        "AD1": ("requeue", "test_daily_total の期待値を 1 日ぶんずらして再実装する"),
        "AD2": ("escalate", ""),
        "AS1": "c=2 r=3 a=2",
        "AS2": "c=1 r=1 a=1",
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
        "PP1": [[], [{"title": "x", "acceptance": ["a"]}],            # 0 件 / 必須セクション欠落
                [{"title": "x", "why": "w", "desc": "d", "scope": "s", "risks": "r",
                  "acceptance": ["a"], "size": "S", "workspace": "docs-site"}]],  # 無い書込先
        "RM1": ["", "このリポジトリは Python 製です。", "ingest だけ読みました"],
        "RV1": [[{"title": "", "acceptance": ["a"]}],                  # title 欠落
                [{"title": "x", "acceptance": ["a"], "workspace": "unknown-repo"}]],
        "DS1": [("請求書 PDF の出力を追加する",
                 "issue #4821 で指摘したとおり、billing/pdf.py の丸めが仕様と違います。"
                 "税込みの端数は切り上げに統一してください。"),           # 生フォールバック
                ("issue #4821 の指摘", "billing/pdf.py の丸めを直す")],  # 固有名詞が残った
        "PR1": [None, [PRIORITIZE_TASKS[0], PRIORITIZE_TASKS[2], PRIORITIZE_TASKS[3],
                       PRIORITIZE_TASKS[1]]],
        "AD1": [("escalate", ""), ("requeue", "")],
        "AD2": [("requeue", "もう一度やってみる")],
        "AS1": ["c=2 r=1 a=2", "c=2 a=2"],
        "AS2": ["c=1 r=1 a=3"],
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
        if "driver" in case:
            print(f"  {cid:<4} {case['purpose']:<10} 本番の関数を実行      期待 {case['expect']}")
        else:
            prompt = build_prompt(case)
            print(f"  {cid:<4} {case['purpose']:<10} prompt {len(prompt):>5,} 字  "
                  f"期待 {case['expect']}")
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
