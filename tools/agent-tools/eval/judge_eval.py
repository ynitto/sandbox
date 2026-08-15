#!/usr/bin/env python3
"""判定・分類の役割（split / filter / judge / reduce / evaluator）の正解率を測る。

worker_eval.py の型をそのまま継ぐ——決定的チェッカーだけで合否を出し、走行中に
判定役（LLM）を呼ばない。同一ケースを反復し、`--model` の差し替えだけで別モデルを
測れる。変えたのは**正解の出どころ**だけで、ここでは「入力を作るときに正解が決まる」
構成的なラベルを使う（次の計画 §3 の 1）。

本番との同一性:
  - argv: agents/ollama-json.json の command（起動時に**読む**。写さない）
  - プロンプト: flow-worker スキルの prompt.py（agent-flow が実際に呼ぶビルダー）
  - 上限: agent-flow の agent_timeout 既定 600 秒
  - 応答の解釈・起動形の解決・手法の適用: engine.py 経由で本番の実装を呼ぶ

使い方: python3 judge_eval.py [--model qwen3.5:9b] [--repeat 3] [--cases S1,F1]
        [--methods restate-task,plan-first] [--tier small]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
# エンジンへ触るのは engine.py だけ。本番の実装を呼びつつ、未着地のシンボルで
# 全 run が死なないようにする窓口（実際に 2 度死んだ）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402

extract_json = engine.extract_json

PROMPT_BUILDER = Path(os.environ.get(
    "FLOW_WORKER_PROMPT",
    str(Path.home() / ".claude/skills/flow-worker/scripts/prompt.py")))
LEDGER_DIR = Path(os.environ.get("JUDGE_EVAL_DIR", "/tmp/agent-judge-eval"))
MODEL = "qwen3.5:9b"
BASE_CLI = "ollama"
WALL_LIMIT = 600.0
_FALLBACK_CMD = ["agent-ollama", "--think", "off", "--format", "json", "{model}"]


def load_cmd(name: str = "ollama-json") -> "tuple[list[str], str]":
    """役割の変種が本番で起動される argv（`agents/<name>.json` の command）。"""
    return engine.load_cmd(name, _FALLBACK_CMD)


def cli_name_for(kind: str) -> str:
    """役割 → 本番が起動する CLI 定義名（split は配列契約なので `list_variant` へ）。"""
    return engine.cli_name_for(kind, BASE_CLI)


def cmd_for(kind: str) -> "tuple[list[str], str, dict[str, str]]":
    name = cli_name_for(kind)
    cmd, source = load_cmd(name)
    return cmd, source, engine.load_env(name)


CMD, CMD_SOURCE = load_cmd()
# `--drop-format` で外す引数。engine 側の制約とモデルの能力を切り分けるための診断用スイッチで、
# 基準線の測定には使わない。
DROP_ARGS: "set[str]" = set()
# `--methods` で有効化した手法パック（tuning.json と同じ形）と、`--tier` で名乗る実行段。
METHODS: "dict | None" = None
TIER = "small"
THINK_OVERRIDE = ""


def load_methods(ids: str) -> "dict | None":
    """カタログ（methods/*.json）を tuning.json と同じ形へ組む（enabled を立てるだけ）。

    id のかわりにファイルパスも受ける。カタログは golden で件数もハッシュも固定されて
    いる（`test_methods_catalog.py`）ので、**採否を決める前の候補はカタログの外で測る**
    ——測るために同梱カタログへ足すと、効かないと分かった手法が goldens ごと残る。
    """
    picked = []
    for mid in [m.strip() for m in ids.split(",") if m.strip()]:
        path = Path(mid) if mid.endswith(".json") else REPO / "methods" / f"{mid}.json"
        if not path.exists():
            raise SystemExit(f"手法パックが見つかりません: {path}")
        picked.append({**json.loads(path.read_text(encoding="utf-8")), "enabled": True})
    return {"methods": picked} if picked else None


def method_text(kind: str) -> "tuple[str, list[str]]":
    """この役割へ本番が注入する追補と、実際に効いた手法 id。

    適用判定は写さず `agentcore.methods.select` をそのまま呼ぶ——`when` の解釈が
    ここと本番でずれると、「効かない条件」を効いた前提で測ることになる。本番の
    context のうちこの測定に無いのは tier だけ（段は agent-control が run へ投函する
    もので、単発の呼び出しには存在しない）。`--tier` で明示的に名乗る。
    """
    if METHODS is None:
        return "", []
    cli = cli_name_for(kind)
    app = engine.method_apply(METHODS, {
        "engine": "agent-flow", "workload": "flow", "purpose": kind,
        "role": engine.method_role(kind), "agent_cli": cli, "model": MODEL,
        "tier": TIER, "relative_cost": engine.method_cost(cli),
    })
    return app["text"], app["methods"]

# ------------------------------------------------------------------ チェッカー
# すべて (ok, note) を返す。data は本番と同じ extract_json の結果（None = 抽出不能）。


def _ids(data) -> set:
    """JSON のどこかに現れた候補 id を集める（出力の器の違いに引きずられないため）。"""
    return set(re.findall(r"\bc\d+\b", json.dumps(data, ensure_ascii=False, default=str)))


def check_ranges(data, want_n: int, lo: int, hi: int):
    if not isinstance(data, list):
        return False, f"配列でない（{type(data).__name__}）"
    if len(data) != want_n:
        return False, f"要素数 {len(data)}（期待 {want_n}）"
    spans = []
    for item in data:
        m = re.findall(r"\d+", str(item))
        if len(m) < 2:
            return False, f"区間として読めない要素: {str(item)[:40]}"
        spans.append((int(m[0]), int(m[1])))
    spans.sort()
    if spans[0][0] != lo or spans[-1][1] != hi:
        return False, f"範囲が {spans[0][0]}〜{spans[-1][1]}（期待 {lo}〜{hi}）"
    for (_, end), (start, _) in zip(spans, spans[1:]):
        if start != end + 1:
            return False, f"区間が連続していない（{end} → {start}）"
    return True, f"{want_n} 分割・{lo}〜{hi} を隙間も重複もなく被覆"


def check_split_files(data, files: "list[str]", want_n: int):
    if not isinstance(data, list):
        return False, f"配列でない（{type(data).__name__}）"
    if len(data) != want_n:
        return False, f"要素数 {len(data)}（期待 {want_n}）"
    if not all(isinstance(group, str) for group in data):
        return False, "文字列でないグループを含む"
    members = [item.strip() for group in data for item in group.split(",") if item.strip()]
    counts = {f: members.count(f) for f in files}
    missing = [f for f, c in counts.items() if c == 0]
    dup = [f for f, c in counts.items() if c > 1]
    unknown = [item for item in members if item not in counts]
    if missing or dup or unknown:
        return False, f"欠落 {missing} / 重複 {dup} / 未知 {unknown}"
    return True, f"{want_n} グループ・{len(files)} 件を過不足なく配分"


def check_id_set(data, want: set):
    """採用集合。器（配列 / {"kept": [...]} 等）の違いは許すが、**答えの場所**は見る。

    初版は JSON 全体から id を拾っていたが、それだと理由文に落選 id を書いた出力を
    不正解にしてしまう（本番の下流は答えのフィールドしか読まない）。リスト値を持つ
    キーがあればそれを答えとみなし、無いときだけ全体から拾う。
    """
    if isinstance(data, list):
        got = _ids(data)
    elif isinstance(data, dict):
        named = [v for k, v in data.items()
                 if isinstance(v, list) and re.search(r"kept|keep|採用|selected", str(k), re.I)]
        lists = [v for v in data.values() if isinstance(v, list)]
        got = _ids(named[0] if named else lists[0] if len(lists) == 1 else data)
    else:
        got = _ids(data)
    if got == want:
        return True, f"採用 {sorted(got)}"
    return False, f"採用 {sorted(got) or '無し'}（期待 {sorted(want)}）"


def check_winner(data, want: str):
    """勝者。`winner` フィールドがあればそこだけを見る（理由文の id は答えではない）。"""
    if isinstance(data, dict) and data.get("winner") is not None:
        got = str(data["winner"]).strip().strip("[]").strip()
        return (got == want), f"winner={got or '無し'}" + ("" if got == want else f"（期待 {want}）")
    got = _ids(data)
    if got == {want}:
        return True, f"winner={want}"
    return False, f"winner フィールドが無い（拾えた id {sorted(got) or '無し'}・期待 {want}）"


def check_reduce(data, want_items: "list[str]"):
    if not isinstance(data, dict):
        return False, f"オブジェクトでない（{type(data).__name__}）"
    blob = json.dumps(data, ensure_ascii=False)
    missing = [x for x in want_items if x not in blob]
    count = data.get("count")
    if missing:
        return False, f"{len(missing)} 件が欠落: {missing[:3]}"
    if count != len(want_items):
        return False, f"count={count}（期待 {len(want_items)}・本文の件数は揃っている）"
    return True, f"{len(want_items)} 件・count 一致"


def check_decision(data, want: str):
    got = (data or {}).get("decision") if isinstance(data, dict) else None
    if got == want:
        return True, f"decision={got}"
    return False, f"decision={got}（期待 {want}）"


# ------------------------------------------------------------------ ケース定義
# 正解は入力を作る規則から従う（ラベル付けの手間がゼロ・人の判断が混じらない）。

CANDIDATES = """[c1] 集計スクリプト案 A: pandas を追加して 30 行。テスト: pass（12 件）
[c2] 集計スクリプト案 B: 標準ライブラリのみで 48 行。テスト: fail（3 件が失敗）
[c3] 集計スクリプト案 C: 標準ライブラリのみで 41 行。テスト: pass（12 件）
[c4] 集計スクリプト案 D: polars を追加して 27 行。テスト: pass（12 件）
[c5] 集計スクリプト案 E: 標準ライブラリのみで 35 行。テスト: fail（実行時エラー）
[c6] 集計スクリプト案 F: 標準ライブラリのみで 52 行。テスト: 未実行"""

FILES = ["ingest.py", "normalize.py", "aggregate.py", "render.py",
         "export.py", "notify.py", "retry.py", "config.py"]

REQUEST = ("run ログから日次のトークン消費レポートを作る仕組みを用意する。"
           "収集・集計・出力の 3 段で、追加の依存は増やさない方針。")

# --- 決定化パイプ（P4 / 実装計画 E6）--------------------------------------------------
# 多基準 filter / judge をモデルに訊かない。モデルの仕事は**事実の抽出だけ**
# （e4b の適格領域 6/6）で、判定は agentcore.nodecontract.decide_candidates（機械）。
# F2 / J1 と同じ素材・同じ正解で、パイプに組み替えた形が F1 並み（3/3 帯）に届くかを測る。

FACT_GOAL = (
    "各候補について機械可読の事実だけを抽出する。**判定・選択はしない**。出力は JSON "
    '{"facts":[{"id":"c1","tests":"pass","extra_deps":false,"lines":30}, ...]} だけ。\n'
    "- tests: 「テスト: pass」なら \"pass\"、fail や実行時エラーなら \"fail\"、"
    "未実行なら \"none\"\n"
    "- extra_deps: 標準ライブラリ以外の追加依存が要るなら true、標準ライブラリのみなら false\n"
    "- lines: 行数の整数\n"
    "6 候補（c1〜c6）すべてを facts に含めること。")


def _norm_facts(data) -> "list[dict]":
    """モデル出力の facts を decide_candidates の入力へ正規化（表記ゆれの吸収だけ。
    値の欠落・不明値は None のまま渡す——undecided として機械側が扱う）。"""
    facts = data.get("facts") if isinstance(data, dict) else None
    out = []
    for f in facts if isinstance(facts, list) else []:
        if not isinstance(f, dict):
            continue
        rec: dict = {"id": str(f.get("id") or "").strip()}
        tests = str(f.get("tests") or "").strip().lower()
        rec["tests"] = tests if tests in ("pass", "fail", "none") else None
        deps = f.get("extra_deps")
        rec["extra_deps"] = deps if isinstance(deps, bool) else None
        lines = f.get("lines")
        rec["lines"] = lines if isinstance(lines, int) and not isinstance(lines, bool) else None
        out.append(rec)
    return out


def check_pipe_filter(data):
    decision = engine.decide_candidates(
        [{"fact": "extra_deps", "op": "eq", "value": False}], _norm_facts(data))
    if decision is None:
        return False, "decide_candidates がこの木に無い"
    if decision["undecided"]:
        return False, f"事実の欠測で未決: {decision['undecided']}"
    kept, want = set(decision["kept"]), {"c2", "c3", "c5", "c6"}
    if kept == want:
        return True, f"抽出→機械判定 kept={sorted(kept)}"
    return False, f"kept={sorted(kept)}（期待 {sorted(want)}）"


def check_pipe_judge(data):
    decision = engine.decide_candidates(
        [{"fact": "tests", "op": "eq", "value": "pass"},
         {"fact": "extra_deps", "op": "eq", "value": False}],
        _norm_facts(data), tie_break={"fact": "lines", "op": "min"})
    if decision is None:
        return False, "decide_candidates がこの木に無い"
    if decision["undecided"]:
        return False, f"事実の欠測で未決: {decision['undecided']}"
    if decision["winner"] == "c3":
        return True, "抽出→機械判定 winner=c3"
    return False, f"winner={decision['winner']}（期待 c3）"


CASES = {
    "S1": dict(kind="split", expect="4 分割・1〜1000 を被覆",
               goal=("処理対象の ID 範囲 1〜1000 を、4 つの連続する区間へ等分する。"
                     '各要素を "開始-終了" 形式の文字列とする JSON 配列だけを出力し、'
                     "区間に隙間も重複も作らないこと。"),
               check=lambda d: check_ranges(d, 4, 1, 1000)),
    "S2": dict(kind="split", expect="4 グループ・8 ファイルを過不足なく",
               goal=("次の 8 ファイルを 4 つのグループへ分ける。各グループを 1 つの文字列"
                     "（含むファイル名をカンマ区切り）とする JSON 配列だけを出力する。"
                     "どのファイルも必ず 1 回だけ現れること。\n" + "\n".join(FILES)),
               check=lambda d: check_split_files(d, FILES, 4)),
    "F1": dict(kind="filter", expect="c1,c3,c4",
               goal=("候補のうち**テストが通っているものだけ**を残す。"
                     '採用した候補の id だけを JSON 配列で出力する（例: ["c1","c2"]）。'
                     "落とした候補の id は出力に含めないこと。"),
               deps={"gen": {"output": CANDIDATES}},
               check=lambda d: check_id_set(d, {"c1", "c3", "c4"})),
    "F2": dict(kind="filter", expect="c2,c3,c5,c6",
               goal=("候補のうち**追加の依存を必要としないもの**（標準ライブラリのみ）を残す。"
                     '採用した候補の id だけを JSON 配列で出力する（例: ["c1","c2"]）。'
                     "落とした候補の id は出力に含めないこと。"),
               deps={"gen": {"output": CANDIDATES}},
               check=lambda d: check_id_set(d, {"c2", "c3", "c5", "c6"})),
    "J1": dict(kind="judge", expect="c3",
               goal=("候補から最良の 1 案を選ぶ。基準は 2 つで、"
                     "(1) テストが通っていること (2) その中で追加の依存が不要なこと。"
                     "両方を満たす案が複数あるときは行数が少ないほうを選ぶ。"
                     '出力は JSON {"winner":"<id>","reason":"..."} だけ。'
                     "reason に他の候補の id を書かないこと。"),
               deps={"gen": {"output": CANDIDATES}},
               check=lambda d: check_winner(d, "c3")),
    # 決定化パイプでは抽出を filter / judge の役割で走らせない——role 行の出力契約
    # （kept / winner）がゴールを上書きし、モデルが判定へ滑り戻る（実測: 旧契約の
    # 即答が混ざり F2P 1/3）。本番でも抽出は独立ノード（extract）にする。
    "F2P": dict(kind="extract", expect="決定化: 抽出→機械判定で kept=c2,c3,c5,c6",
                goal=FACT_GOAL,
                deps={"gen": {"output": CANDIDATES}},
                check=check_pipe_filter),
    "J1P": dict(kind="extract", expect="決定化: 抽出→機械判定で winner=c3",
                goal=FACT_GOAL,
                deps={"gen": {"output": CANDIDATES}},
                check=check_pipe_judge),
    "J2": dict(kind="judge", expect="c4",
               goal=("候補から最良の 1 案を選ぶ。基準は「テストが通っている案のうち"
                     "最も行数が少ないもの」だけで、依存の有無は問わない。"
                     '出力は JSON {"winner":"<id>","reason":"..."} だけ。'
                     "reason に他の候補の id を書かないこと。"),
               deps={"gen": {"output": CANDIDATES}},
               check=lambda d: check_winner(d, "c4")),
    "R1": dict(kind="reduce", expect="12 件・count=12",
               goal=('依存の data を 1 つのリストへ畳み込む。出力は JSON '
                     '{"items":[...],"count":<件数>} だけ。count は items の実際の要素数と'
                     "一致させること。要素の中身は変更しない。"),
               deps={"m1": {"output": "3 件", "data": ["ingest", "normalize", "aggregate"]},
                     "m2": {"output": "5 件", "data": ["render", "export", "notify",
                                                        "retry", "config"]},
                     "m3": {"output": "4 件", "data": ["auth", "quota", "audit", "purge"]}},
               check=lambda d: check_reduce(d, ["ingest", "normalize", "aggregate", "render",
                                                "export", "notify", "retry", "config",
                                                "auth", "quota", "audit", "purge"])),
    "R2": dict(kind="reduce", expect="8 件・count=8（重複除去）",
               goal=('依存の data を 1 つのリストへ畳み込む。**同じ要素は 1 つにまとめる**。'
                     '出力は JSON {"items":[...],"count":<件数>} だけで、count は重複を'
                     "除いたあとの実際の要素数と一致させること。"),
               deps={"m1": {"output": "5 件", "data": ["ingest", "normalize", "aggregate",
                                                        "render", "export"]},
                     "m2": {"output": "4 件", "data": ["aggregate", "render", "notify", "retry"]},
                     "m3": {"output": "3 件", "data": ["ingest", "retry", "config"]}},
               check=lambda d: check_reduce(d, ["ingest", "normalize", "aggregate", "render",
                                                "export", "notify", "retry", "config"])),
    # evaluator の results_summary は本番（continuation.py）と同じ 1 行形式で作る。
    "E1": dict(role="evaluator", expect="replan",
               results=[("t1", "work", "done", "run ログを読み込む reader を実装。テスト 6 件 pass。"),
                        ("t2", "work", "done", "日次のトークン合計を出す集計を実装。テスト 4 件 pass。"),
                        ("t3", "work", "failed", "時間切れ。成果物なし（writer は未作成）。"),
                        ("t4", "verify", "done",
                         'verify=fail。{"ok": false, "issues": ["出力段が無いためレポートを'
                         '生成できない"]}')],
               check=lambda d: check_decision(d, "replan")),
    "E2": dict(role="evaluator", expect="done",
               results=[("t1", "work", "done", "run ログを読み込む reader を実装。テスト 6 件 pass。"),
                        ("t2", "work", "done", "日次のトークン合計を出す集計を実装。テスト 4 件 pass。"),
                        ("t3", "work", "done",
                         "日次レポートを Markdown で書き出す writer を実装。テスト 3 件 pass。追加依存なし。"),
                        ("t4", "verify", "done",
                         'verify=pass。{"ok": true, "issues": []}（3 段すべてを再導出して突き合わせ済み）')],
               check=lambda d: check_decision(d, "done")),
}

# ------------------------------------------------------------------ 実行


def build_prompt(case: dict) -> str:
    if case.get("role") == "evaluator":
        # パターン目録と結果要約の作り方まで本番（continuation.py）に合わせる。
        catalog = "\n".join(f"- {k}: {v}" for k, v in engine.patterns().items())
        summary = "\n".join(f"- {nid} ({kind}) [{status}]: {out[:160]}"
                            for nid, kind, status, out in case["results"])
        payload = {"role": "evaluator", "request": REQUEST, "max_retries": 3,
                   "results_summary": summary, "patterns_catalog": catalog,
                   "human_feedback": "", "iteration": 1}
    else:
        payload = {"role": "worker", "kind": case["kind"], "goal": case["goal"],
                   "request": REQUEST, "deps": case.get("deps") or {},
                   "repo_instruction": "", "artifact_note": "", "workspace": {},
                   "references": [], "instructions": "", "repair_note": "", "read_note": ""}
    r = subprocess.run([sys.executable, str(PROMPT_BUILDER)],
                       input=json.dumps(payload, ensure_ascii=False),
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"prompt.py が失敗: {r.stderr[:300]}")
    return r.stdout.strip()


def call(prompt: str, cmd: "list[str] | None" = None,
         command_env: "dict[str, str] | None" = None) -> "tuple[int, str, str, float]":
    raw = list(cmd or CMD)
    if THINK_OVERRIDE and "--think" in raw:
        raw[raw.index("--think") + 1] = THINK_OVERRIDE
    argv = [a.replace("{model}", MODEL) for a in raw if a not in DROP_ARGS]
    started = time.time()
    try:
        p = subprocess.run(argv, input=prompt, capture_output=True, text=True,
                           timeout=WALL_LIMIT, env={**os.environ, **(command_env or {})})
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "TIMEOUT"
    return rc, out, err, time.time() - started


def run_one(cid: str, i: int) -> dict:
    case = CASES[cid]
    kind = case.get("kind") or "evaluator"
    prompt = build_prompt(case)
    # 本番（agent-flow の _apply_methods）と同じ位置——プロンプトの末尾へ空行 1 つで後置。
    extra, applied = method_text(kind)
    if extra:
        prompt = f"{prompt}\n\n{extra}"
    cmd, _src, command_env = cmd_for(kind)
    rc, out, err, wall = call(prompt, cmd, command_env)
    repaired = False

    data = None
    if wall >= WALL_LIMIT:
        mode, ok, note = "timeout", False, "上限超過"
    elif rc != 0:
        mode, ok, note = "cli_error", False, (err.strip()[-120:] or f"rc={rc}")
    elif not out.strip():
        mode, ok, note = "empty", False, "本文が空"
    else:
        try:
            data = engine.extract_list(out) if kind == "split" else extract_json(out)
        except Exception as e:  # noqa: BLE001 — 本番も同じ失敗をする（そこが測定対象）
            data, why = None, str(e)
        else:
            why = "JSON としては解釈できたが配列でない"
        # split は本番（agent.py）が「器を剥がす → それでも配列でなければレイヤ2 の形式修復を
        # 1 回」の順で受ける。ここを省くと、**本番なら救えている失敗**をモデルの不合格として
        # 数えてしまう。剥がし方は写さず本番の関数をそのまま呼ぶ（写すとずれる）。
        if case.get("kind") == "split" and not isinstance(data, list):
            repair = (f"{prompt}\n\n[前回の出力は契約違反でした]\n"
                      f"前回の出力（先頭 400 文字）: {out[:400]}\n"
                      f"違反: {why}\n"
                      "説明・前置き・コードフェンスを付けず、指示された JSON 配列だけを"
                      "再出力してください。")
            r_rc, r_out, r_err, r_wall = call(repair, cmd, command_env)
            wall += r_wall
            repaired = True
            if r_rc == 0 and r_out.strip():
                try:
                    fixed = engine.extract_list(r_out)
                except Exception:  # noqa: BLE001
                    fixed = None
                if isinstance(fixed, list):
                    data, out = fixed, r_out
        if data is None:
            mode, ok, note = "unparsable", False, f"JSON を抽出できない: {why}"
        else:
            ok, note = case["check"](data)
            mode = "correct" if ok else "wrong"
            if repaired:
                mode += "_after_repair"
    log = ""
    for line in err.splitlines():
        if line.startswith("@agent-log"):
            log = line.split(None, 1)[-1]
    rec = dict(case=cid, kind=kind, iter=i, ok=ok, mode=mode,
               repaired=repaired, wall=round(wall, 1), note=note, prompt_chars=len(prompt),
               out_chars=len(out), answer=json.dumps(data, ensure_ascii=False,
                                                     default=str)[:200], log=log,
               think_override=THINK_OVERRIDE or None, format_dropped=bool(DROP_ARGS))
    if applied:  # 何が効いた行かは台帳から読めないと、後で比較できない（空なら書かない）
        rec["methods"] = applied
    if engine.missing():  # 欠けた木で取った行を、揃った木の行として読まないため
        rec["engine_missing"] = engine.missing()
    print(f"  {cid}#{i}: {'PASS' if ok else 'FAIL':4s} {mode:11s} {wall:6.1f}s  {note[:66]}",
          flush=True)
    return rec


def selfcheck() -> int:
    """チェッカーを LLM 抜きで検証する（正解は通り、典型的な外し方は落ちる）。

    ハーネスの不具合をモデルの不合格として報告しないための最低限。worker_eval の
    README と同じ規律で、正解・不正解・器の違いの 3 方向を見る。
    """
    good = {
        "S1": ["1-250", "251-500", "501-750", "751-1000"],
        "S2": [f"{FILES[0]},{FILES[1]}", f"{FILES[2]},{FILES[3]}",
               f"{FILES[4]},{FILES[5]}", f"{FILES[6]},{FILES[7]}"],
        "F1": ["c1", "c3", "c4"],
        # 器が違っても・落選理由を併記していても、答えの場所が正しければ通る
        "F2": {"kept": ["c2", "c3", "c5", "c6"], "rejected": ["c1", "c4"]},
        "J1": {"winner": "c3", "reason": "c1 と c4 は依存を足すため除外した"},
        "J2": {"winner": "[c4]", "reason": "最短"},
        "R1": {"items": ["ingest", "normalize", "aggregate", "render", "export", "notify",
                         "retry", "config", "auth", "quota", "audit", "purge"], "count": 12},
        "R2": {"items": ["ingest", "normalize", "aggregate", "render", "export", "notify",
                         "retry", "config"], "count": 8},
        "E1": {"decision": "replan", "reason": "出力段が無い", "new_tasks": [{"id": "t5"}]},
        "E2": {"decision": "done", "reason": "全項目 pass", "new_tasks": []},
        "F2P": {"facts": [
            {"id": "c1", "tests": "pass", "extra_deps": True, "lines": 30},
            {"id": "c2", "tests": "fail", "extra_deps": False, "lines": 48},
            {"id": "c3", "tests": "pass", "extra_deps": False, "lines": 41},
            {"id": "c4", "tests": "pass", "extra_deps": True, "lines": 27},
            {"id": "c5", "tests": "fail", "extra_deps": False, "lines": 35},
            {"id": "c6", "tests": "none", "extra_deps": False, "lines": 52}]},
    }
    good["J1P"] = good["F2P"]
    bad = {
        "S1": [["1-250", "260-500", "501-750", "751-1000"],   # 隙間
               ["1-500", "501-1000"],                         # 分割数
               "1-250,251-500"],                              # 配列でない
        "S2": [[f"{FILES[0]},{FILES[1]}", f"{FILES[2]}", f"{FILES[3]}", f"{FILES[4]}"],
               [",".join(FILES[:4]), ",".join(FILES[4:]), "...", "..."]],
        "F1": [["c1", "c3"], ["c1", "c2", "c3", "c4"], ["c1", "c3", "c4", "c6"]],
        "F2": [{"kept": ["c2", "c3"]}, {"kept": ["c2", "c3", "c5", "c6", "c1"]},
               # 器を使わず候補ごとに講評だけを返す形（実測で出た外し方）
               {"c1": "採用", "c3": "採用"}],
        # winner フィールドが無いときは「本文に現れる id がちょうど 1 つ」まで緩めて
        # 拾うが、複数を並べた出力は勝者を決めていないので落とす。
        "J1": [{"winner": "c4"}, {"reason": "c1 と c3 が同点で決めきれない"}],
        "J2": [{"winner": "c1"}, {"winner": "c3"}],
        "R1": [{"items": ["ingest"], "count": 12}, {"items": [], "count": 0}],
        "R2": [{"items": ["ingest", "normalize", "aggregate", "render", "export", "notify",
                          "retry", "config"], "count": 12}],   # 件数だけ合わない
        "E1": [{"decision": "done"}, {}, None],
        "E2": [{"decision": "replan"}],
        # 抽出の取り違え（c1 の依存を false と誤抽出）→ kept/winner がずれて落ちる。
        # 欠測（c3 の extra_deps 抜け）→ 未決として落ちる（静かに合格させない）。
        "F2P": [{"facts": [
            {"id": "c1", "tests": "pass", "extra_deps": False, "lines": 30},
            {"id": "c2", "tests": "fail", "extra_deps": False, "lines": 48},
            {"id": "c3", "tests": "pass", "extra_deps": False, "lines": 41},
            {"id": "c4", "tests": "pass", "extra_deps": True, "lines": 27},
            {"id": "c5", "tests": "fail", "extra_deps": False, "lines": 35},
            {"id": "c6", "tests": "none", "extra_deps": False, "lines": 52}]},
            {"facts": [{"id": "c2", "tests": "fail", "extra_deps": False, "lines": 48}]}],
        "J1P": [{"facts": [
            {"id": "c1", "tests": "pass", "extra_deps": True, "lines": 30},
            {"id": "c2", "tests": "fail", "extra_deps": False, "lines": 48},
            {"id": "c3", "tests": "pass", "lines": 41},
            {"id": "c4", "tests": "pass", "extra_deps": True, "lines": 27},
            {"id": "c5", "tests": "fail", "extra_deps": False, "lines": 35},
            {"id": "c6", "tests": "none", "extra_deps": False, "lines": 52}]}],
    }
    fails = []
    for cid, case in CASES.items():
        ok, note = case["check"](good[cid])
        if not ok:
            fails.append(f"{cid}: 正解が落ちた（{note}）")
        for i, wrong in enumerate(bad.get(cid, [])):
            ok, note = case["check"](wrong)
            if ok:
                fails.append(f"{cid}: 不正解 #{i + 1} を通した（{note}）")
        prompt = build_prompt(case)
        print(f"  {cid:<4} {case.get('kind') or 'evaluator':<10} "
              f"prompt {len(prompt):>5,} 字  期待 {case['expect']}")
    for f in fails:
        print(f"  NG {f}")
    print(f"\nチェッカー自己診断: {'OK' if not fails else f'{len(fails)} 件 NG'}")
    return 1 if fails else 0


def main() -> None:
    global WALL_LIMIT, MODEL, BASE_CLI, METHODS, TIER, THINK_OVERRIDE
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--base-cli", default=BASE_CLI,
                    help="役割別variantを解決する基底agent CLI定義（例: ollama, aider）")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--wall", type=float, default=WALL_LIMIT)
    ap.add_argument("--selfcheck", action="store_true",
                    help="LLM を呼ばずにチェッカーとプロンプトだけ確かめる")
    ap.add_argument("--drop-format", action="store_true",
                    help="診断用: argv から --format json / array を外す（基準線には使わない）")
    ap.add_argument("--methods", default="",
                    help="カタログ（methods/*.json）の id をカンマ区切りで有効化する。"
                         "適用条件は本番の agentcore.methods.select が判定するので、"
                         "`when` に合わない役割へは注入されない。"
                         "候補プリセットは .json のパスでも指定できる（カタログに入れずに測る）")
    ap.add_argument("--think", choices=("on", "off", "prompt"), default="",
                    help="評価専用: 定義中の --think 値を上書きする。"
                         "prompt は system prompt 先頭の <|think|> 方式（Gemma 4 系の作法）で、"
                         "API フィールドとは経路が違うため --format と併用できる")
    ap.add_argument("--tier", default=TIER,
                    help="この測定が名乗る実行段（手法の when.tiers と突き合わせる）")
    args = ap.parse_args()
    if args.drop_format:
        DROP_ARGS.update({"--format", "json", "array"})
    THINK_OVERRIDE = args.think
    if args.selfcheck:
        raise SystemExit(selfcheck())
    MODEL, BASE_CLI, WALL_LIMIT = args.model, args.base_cli, args.wall
    METHODS, TIER = load_methods(args.methods), args.tier
    cids = [c.strip() for c in args.cases.split(",") if c.strip() in CASES]

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger = LEDGER_DIR / "ledger.jsonl"
    seen = []
    for kind in dict.fromkeys(CASES[c].get("kind") or "evaluator" for c in cids):
        cmd, src, _command_env = cmd_for(kind)
        line = f"{kind}: {' '.join(a for a in cmd if a not in DROP_ARGS)} （出所: {src}）"
        # 宣言した手法が when で落ちて 1 つも効いていない、を黙って測らない。
        applied = method_text(kind)[1]
        if METHODS is not None:
            line += f" 手法: {','.join(applied) if applied else 'なし（when 不一致）'}"
        if line not in seen:
            seen.append(line)
    for gap in engine.missing():
        print(f"   ⚠ この木にはエンジン機能が無い: {gap}（その分は測れていない）")
    print(f"model={MODEL}{'・--drop-format 適用' if DROP_ARGS else ''}\n  " + "\n  ".join(seen)
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
        # 自己一貫性: 同じ入力を引き直したとき、判定（合否ではなく答え）が揃うか
        same = collections.Counter(x["answer"] for x in r).most_common(1)[0][1]
        print(f"  {cid} ({CASES[cid].get('kind') or 'evaluator'}): "
              f"{sum(1 for x in r if x['ok'])}/{len(r)}  "
              f"中央値 {sorted(x['wall'] for x in r)[len(r)//2]:.0f}s  "
              f"自己一貫性 {same}/{len(r)}  様式 {sorted(set(x['mode'] for x in r))}")
    by_kind = collections.defaultdict(list)
    for x in rows:
        by_kind[x["kind"]].append(x)
    print("\n=== 役割別")
    for kind, r in by_kind.items():
        print(f"  {kind}: {sum(1 for x in r if x['ok'])}/{len(r)}")
    empty = sum(1 for x in rows if x["mode"] in ("empty", "unparsable"))
    print(f"\n  合計: {sum(1 for x in rows if x['ok'])}/{len(rows)}  "
          f"空・形式違反 {empty}/{len(rows)}  "
          f"プロンプト長 中央値 {sorted(x['prompt_chars'] for x in rows)[len(rows)//2]:,} 字")
    print(f"\n台帳: {ledger}")


if __name__ == "__main__":
    main()
