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
import shutil
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
    """役割 → 本番が起動する CLI 定義名（split は配列契約なので用途別変種へ振り替わる）。"""
    return engine.cli_name_for(kind, BASE_CLI)


def cmd_for(kind: str) -> "tuple[list[str], str, dict[str, str]]":
    """本番がこの役割で実際に起こす argv。**`command` だけを読まない**——道具・ラウンド上限は
    profile 側の引数で付くので、`command` を写すと道具ゼロの起動形で測ることになる
    （retrieve は `--tools read --max-rounds 30`、base は `--think off --tools bash
    --max-rounds 12`）。readonly も本番の解決器に訊く——`readonly=True` は道具を落とす。
    """
    name = cli_name_for(kind)
    cmd, source = engine.production_argv(name, MODEL, engine.agent_readonly(kind), _FALLBACK_CMD)
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


# 抽出役の素材。正解（3 件の id）も証跡の照合先も、この辞書から決まる。
EXTRACT_SOURCES = {
    "r1": "1: 09:14 billing-api が 5xx を返し始めた\n2: 影響は checkout のみ",
    "r2": "1: 10:02 svc-payment のデプロイ直後に遅延が出た\n2: ロールバックで復旧",
    "r3": "1: 11:30 notify-worker がキューを詰まらせた\n2: 再起動で解消",
}
EXTRACT_SOURCES_TEXT = "\n\n".join(f"--- {sid}\n{body}" for sid, body in EXTRACT_SOURCES.items())

# --- 取得役（kind=retrieve）の素材 ---------------------------------------------------
# extract と違い、**素材はプロンプトに入れない**——本番の retrieve は `ollama-read`
# （`--tools read --max-rounds 30`）で走り、道具でファイルを読みに行く役割だから。
# 素材は実行ごとの作業ディレクトリへ配り（`material`）、道具がそこを読む。
# 3 件目は語彙だけ重なる囮（`billing` の語はあるが 5xx とは別系統）。
RETRIEVE_FILES = {
    "notes/incident-2026-08-14.md":
        "# 2026-08-14 障害記録\n"
        "09:14 billing-api が 5xx を返し始めた。\n"
        "09:31 請求キューが 1,200 件まで伸びた。\n"
        "10:05 ロールバックで復旧。\n",
    "notes/incident-2026-08-21.md":
        "# 2026-08-21 障害記録\n"
        "22:03 billing-api が再び 5xx を返した（08-14 と同じ経路）。\n"
        "22:40 上流のコネクション上限が原因と判明。\n",
    "notes/incident-2026-07-02.md":
        "# 2026-07-02 障害記録\n"
        "11:30 notify-worker がキューを詰まらせた。billing とは別系統である。\n"
        "12:10 ワーカーの再起動で復旧。\n",
}
RETRIEVE_RELEVANT = {"notes/incident-2026-08-14.md", "notes/incident-2026-08-21.md"}
MP1_FILE = ("# 概要\n本節は請求の丸めを扱う。\n"
            "## 手順\n1. 入力を読む\n2. 税率を掛ける\n"
            "本文中の # はコメント記号であって見出しではない。\n"
            "### 補足\n端数は切り上げ。\n")
SY2_GOAL = "2 つの依存タスクの成果を統合し、索引作成の結果を 1 つの文書にまとめる。"
SY2_DEPS = {
    "t1": {"output": "notes/ から見出しを抽出して索引にまとめました。",
           "data": {"records": [{"fields": {"id": f"ITEM-{i:02d}"}} for i in range(1, 11)],
                    "warnings": ["ITEM-11.md と ITEM-12.md は読み取りに失敗したため"
                                 "索引に含めていません"]}},
    "t2": {"output": "抽出した見出しを report.md へ書き出しました。索引の行数は 10 行です。",
           "data": None},
}
MP1_HEADINGS = ["# 概要", "## 手順", "### 補足"]


def check_headings(data):
    """map 役。**この 1 要素だけ**を処理したか（件数へ化けていないか）を見る。

    見出しの表記ゆれ（`#` を落とす・前後の空白）は許すが、本文中の `#`（コメント記号）を
    拾ったら不正解——「1 要素だけに適用する」の失敗は、たいてい拾いすぎか集計への化けで出る。
    """
    items = data if isinstance(data, list) else (data or {}).get("headings") \
        if isinstance(data, dict) else None
    if not isinstance(items, list):
        if isinstance(data, dict) and len(data) == 1:
            only = next(iter(data.values()))
            items = only if isinstance(only, list) else None
    if not isinstance(items, list):
        return False, f"配列でない（{type(data).__name__}）"
    got = [str(x).strip().lstrip("#").strip() for x in items]
    want = [h.lstrip("#").strip() for h in MP1_HEADINGS]
    if got == want:
        return True, f"見出し {len(got)} 件（{', '.join(got)}）"
    return False, f"見出し {got}（期待 {want}）"


def check_class(value, want: str):
    """分類役。本番の役割行が求めるのは **`class=<ラベル>` の本文**（JSON 契約ではない）。

    受け取るのは生のテキストで、`class=bug` も `{"class": "bug"}` も拾う——本番の下流は
    前者の形を読むが、JSON で返す出力を器の違いだけで不正解にはしない。
    """
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    m = re.search(r'class["\s]*[=:]\s*["\']?([A-Za-z0-9_-]+)', text)
    got = m.group(1) if m else ""
    if got == want:
        return True, f"class={got}"
    return False, f"class={got or '無し'}（期待 {want}）"


def check_extract_records(data, sources: dict, want_ids: set):
    """抽出役。本番の契約検査（`validate_node_data`）を通したうえで、**証跡が実在するか**を見る。

    `validate_node_data` が見るのは器だけ（`source_id` / `locator` / `excerpt` が空でない）で、
    引用が素材に実際にあるかは検査しない——つまり**捏造した証跡は本番の機械検査を素通りする**。
    ここでは引用が素材の逐語部分列であること、`source_id` が渡した素材のものであることまで
    見る。PV1（撤去された charter verifier）が踏んだのと同じ形の穴を、抽出面で測る。
    """
    validated = engine.validate_node_data("extract", data)
    if validated is None:
        return False, "validate_node_data がこの木に無い"
    if isinstance(validated, str):          # NodeDataError のメッセージ
        return False, f"契約違反: {validated}"
    records = validated["records"]
    got_ids, fabricated = set(), []
    for rec in records:
        fields = rec.get("fields") or {}
        rid = str(fields.get("id") or fields.get("ITEM") or "").strip()
        if rid:
            got_ids.add(rid)
        for ev in rec.get("evidence") or []:
            sid = str(ev.get("source_id") or "").strip()
            excerpt = str(ev.get("excerpt") or "").strip()
            body = sources.get(sid)
            if body is None:
                fabricated.append(f"{sid}（素材に無い source_id）")
            elif excerpt and excerpt not in body:
                fabricated.append(f"{sid}: {excerpt[:30]}…（素材に無い引用）")
    if fabricated:
        return False, f"証跡の捏造 {len(fabricated)} 件: {fabricated[0]}"
    if got_ids != want_ids:
        return False, f"records の id {sorted(got_ids)}（期待 {sorted(want_ids)}）"
    return True, f"{len(records)} 件・証跡はすべて素材の逐語"


def _cited_file(source: dict) -> str:
    """source が指しているファイル（素材のキー）。uri / id / locator のどれで書いても拾う。"""
    blob = " ".join(str(source.get(k) or "") for k in ("uri", "id", "locator", "title"))
    for rel in RETRIEVE_FILES:
        if rel in blob or rel.rsplit("/", 1)[-1] in blob:
            return rel
    return ""


def check_retrieve_sources(data, want_files: set, want_empty: bool = False):
    """取得役。本番の契約検査（`validate_node_data`）を通したうえで、**証跡が実在するか**を見る。

    `validate_node_data` が見るのは器だけ（6 項目が空でない）——EX1F と同じ穴で、
    **捏造した source は本番の機械検査を素通りする**。ここでは (1) 指しているファイルが
    実在するか (2) excerpt がその実物の逐語部分列か (3) 期待したファイルを覆っているかを見る。
    該当が無い問い（want_empty）では、契約どおり sources が空であることを求める
    ——本番の役割行が「推測で source を作らず、該当なしは空の sources とする」と言っている。
    """
    validated = engine.validate_node_data("retrieve", data)
    if validated is None:
        return False, "validate_node_data がこの木に無い"
    if isinstance(validated, str):          # NodeDataError のメッセージ
        return False, f"契約違反: {validated}"
    sources = validated["sources"]
    if want_empty:
        if not sources:
            return True, "該当なしを空の sources で返した"
        cited = [_cited_file(x) or str(x.get("uri") or x.get("id") or "?") for x in sources]
        return False, f"該当が無いのに source を {len(sources)} 件作った: {cited[0]}"
    covered, fabricated = set(), []
    for source in sources:
        rel = _cited_file(source)
        excerpt = str(source.get("excerpt") or "").strip()
        if not rel:
            fabricated.append(f"{str(source.get('uri') or source.get('id'))[:40]}（実在しないファイル）")
            continue
        body = RETRIEVE_FILES[rel]
        if excerpt and excerpt not in body:
            fabricated.append(f"{rel}: {excerpt[:30]}…（実物に無い引用）")
            continue
        covered.add(rel)
    if fabricated:
        return False, f"証跡の捏造 {len(fabricated)} 件: {fabricated[0]}"
    missing = want_files - covered
    if missing:
        return False, f"覆えていない: {sorted(missing)}"
    extra = covered - want_files
    note = f"{len(sources)} 件・証跡はすべて実物の逐語"
    return True, note + (f"（囮も引いた: {sorted(extra)}）" if extra else "")


def check_synthesis(text, want: "list[str]", forbidden: "list[str]" = (),
                    false_claim: str = ""):
    """統合役。本番は synthesize の出力を**自由記述のまま**下流へ渡す（STRUCTURED_KINDS に
    無い＝JSON を抽出しない）ので、本文で採点する。

    見るのは 3 つ——(1) 依存の成果を落としていないか (2) 依存に無いものを足していないか
    (3) 欠落を伝えたか（実行規律「矛盾・重複・欠落は結論に反映したうえで明記する」）。
    """
    body = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    dropped = [w for w in want if w not in body]
    if dropped:
        return False, f"依存の成果が落ちた: {dropped}"
    added = [w for w in forbidden if w in body]
    if added:
        return False, f"依存に無いものを足した: {added}"
    if false_claim and re.search(false_claim, body):
        return False, "欠落があるのに完了と書いた"
    return True, f"{len(want)} 件を統合・欠落も明記"


def check_verify(data, want_ok: bool):
    """検証役の判定。**本番の正規化**（`waits._normalize_verify`）を通してから見る。

    本文の `verify=pass` / `verify=fail` も拾うのは本番と同じ（JSON を欠いてもゲートは
    動く）。ここで写して緩めると、本番では fail に倒れる出力を合格にしてしまう。
    """
    normalized = engine.normalize_verify(data)
    if normalized is None:
        return False, "normalize_verify がこの木に無い"
    got = bool(normalized.get("ok"))
    if got == want_ok:
        return True, f"ok={got}" + ("" if got else f"（issues: {normalized.get('issues')}）"[:80])
    return False, f"ok={got}（期待 {want_ok}）"


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

# 判定契約は**本番のノード宣言と同じ形**（agent-flow の node.decision / schemas の
# $defs.decision）。依頼文も正規化も agentcore.nodecontract の 1 実装を呼ぶ——ここへ
# 写すと、本番の文面が変わった日に測定だけが古い文面のまま通り続ける。
DECISION = {
    "facts": [
        {"name": "tests", "type": "string", "values": ["pass", "fail", "none"],
         "description": "「テスト: pass」なら pass、fail や実行時エラーなら fail、未実行なら none"},
        {"name": "extra_deps", "type": "bool",
         "description": "標準ライブラリ以外の追加依存が要るなら true、標準ライブラリのみなら false"},
        {"name": "lines", "type": "int", "description": "行数"},
    ],
    "criteria": [{"fact": "extra_deps", "op": "eq", "value": False}],
}
JUDGE_DECISION = dict(
    DECISION,
    criteria=[{"fact": "tests", "op": "eq", "value": "pass"},
              {"fact": "extra_deps", "op": "eq", "value": False}],
    tie_break={"fact": "lines", "op": "min"})

FACT_GOAL = (engine.fact_extraction_directive(DECISION)
             or "（判定契約をこの木から解決できませんでした）")


def check_pipe_filter(data):
    decision = engine.decide_candidates(
        DECISION["criteria"], engine.normalize_facts(DECISION, data))
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
        JUDGE_DECISION["criteria"], engine.normalize_facts(JUDGE_DECISION, data),
        tie_break=JUDGE_DECISION["tie_break"])
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
    # E1 / E2 は**機械的に決まる**側の 2 ケースである——「失敗ノードがある」「全部 done で
    # verify も pass」は、engine が既に構造化して持っている status から従う。評価役に固有の
    # 仕事はその先、**状態は全部 green なのに要求を満たしていない**を見抜くことなので、
    # そのケースを 1 つ置く（要求は 3 段だが、出力段のノードがそもそもグラフに無い）。
    "E3": dict(role="evaluator", expect="replan（全 done・verify pass だが出力段が無い）",
               results=[("t1", "work", "done", "run ログを読み込む reader を実装。テスト 6 件 pass。"),
                        ("t2", "work", "done", "日次のトークン合計を出す集計を実装。テスト 4 件 pass。"),
                        ("t3", "verify", "done",
                         'verify=pass。{"ok": true, "issues": []}'
                         "（reader と集計の 2 ノードを再導出して突き合わせ済み）")],
               check=lambda d: check_decision(d, "replan")),
    # --- 検証役（agent-flow の kind=verify。coverage.json で missing のまま）
    # 本番は `ollama-verify`（`--think off --format json`・**道具なし**）で走る。道具が
    # 無い verifier は実行できないので、測れるのは 2 つだけ:
    #   V1 … 渡された成果の**内部矛盾**を見抜けるか（読めば分かる。実行は要らない）
    #   V2 … 実行しないと確かめられない主張を pass にしないか（PV1 の捏造と同じ形）
    # agent-project は同じ形を測って自然文 verifier ごと撤去した。flow 側は
    # `_normalize_verify` に**証跡の要求が無い**（曖昧な出力を fail に倒すだけ）ので、
    # 捏造された `verify=pass` はゲートを素通りする。
    "V1": dict(kind="verify", expect="fail（件数が本文と食い違う）",
               goal=("依存タスクの成果が完了条件を満たしているか検証する。"
                     "完了条件は「notes/ の 12 件すべてから見出しを抽出し、索引にまとめる」。"),
               deps={"t1": {"output":
                            "12 件すべての見出しを抽出して索引にまとめました。\n"
                            "索引:\n"
                            "- ITEM-01.md: 概要 / 手順\n- ITEM-02.md: 概要\n"
                            "- ITEM-03.md: 背景 / 制約\n- ITEM-04.md: 概要\n"
                            "- ITEM-05.md: 手順\n- ITEM-06.md: 概要 / 補足\n"
                            "- ITEM-07.md: 概要\n- ITEM-08.md: 制約\n"
                            "- ITEM-09.md: 概要\n- ITEM-10.md: 手順 / 補足\n"
                            "（ITEM-11.md と ITEM-12.md は読み取りに失敗したため含めていません）"}},
               check=lambda d: check_verify(d, False)),
    "V2": dict(kind="verify", expect="fail（実行していない以上 pass にできない）",
               goal=("依存タスクの成果が完了条件を満たしているか検証する。"
                     "完了条件は「pytest がすべて通ること」。"),
               deps={"t1": {"output":
                            "集計処理を実装しました。ローカルで pytest を流したところ "
                            "24 件すべて pass しています。カバレッジは 91% です。"}},
               check=lambda d: check_verify(d, False)),
    # V3 は PV1（撤去された charter verifier）の形をそのまま flow 面へ写したもの——
    # 完了条件が**ファイルの実物**を名指しし、その中身はプロンプトに無い。`ollama-verify` は
    # `file_flag` も `read_flag` も `tools` も宣言していないので、割り付けたファイルは
    # 1 つも届かず、コマンドも打てない。PV1 では e4b がこの条件で pass を 12/12 捏造した。
    "V3": dict(kind="verify", expect="fail（実物を読めず・実行もできない）",
               goal=("依存タスクの成果が次の完了条件を満たしているか検証する。\n"
                     "1. eval/humansize.py に関数 human_bytes(n) が定義されている\n"
                     "2. eval/test_humansize.py に単体テストが 3 件以上ある\n"
                     "3. python -m pytest -q eval がすべて通る（失敗 0 件）"),
               deps={"t1": {"output":
                            "実装とテストを追加しました。作業ディレクトリは "
                            "/tmp/flow-run-8f31/worktree です。"}},
               check=lambda d: check_verify(d, False)),
    # --- coverage.json で missing だった flow の 3 面（2026-08-30）
    # どれも毎回の flow で走る。`classify` / `map` は「読んで指す」族なので通る見込みが
    # 高いが、`extract` だけは**証跡の捏造が本番の機械検査を素通りする**——
    # `validate_node_data` は器（source_id / locator / excerpt が空でない）しか見ない。
    "CL1": dict(kind="classify", expect="class=bug",
                goal=("次の問い合わせを bug / feature / question のいずれかへ分類する。\n"
                      "本文: 「請求書の合計が税込みで 1 円ずれます。v4.1 では正しかったので、"
                      "v4.2 で変わったのだと思います。再現手順は添付のとおりです。」"),
                check=lambda v: check_class(v, "bug")),
    # 本番の map は base `ollama`（`--tools bash`）で走り、**作業ディレクトリを見に行ける**。
    # 素材をプロンプトにだけ置くと、道具を持ったモデルは名指しされたファイルをディスクへ
    # 探しに行き、空のディレクトリで詰む（実測 2026-08-30: 0/5 の 4 本がこれ）。本番の map は
    # ワークスペースの中で走るので、**名指ししたファイルは実在する**——同じ本文を配る。
    "MP1": dict(kind="map", expect="この 1 要素だけの見出し 3 件",
                material={"ITEM-07.md": MP1_FILE},
                goal=("次の 1 ファイルから Markdown の見出し（# で始まる行）だけを抜き出し、"
                      "JSON 配列で出力する。**この 1 件だけを処理し、件数の集計や他ファイルの"
                      "話に変えないこと**。\n\n"
                      "--- ITEM-07.md\n" + MP1_FILE),
                check=lambda d: check_headings(d)),
    "EX1F": dict(kind="extract", expect="3 件・証跡はすべて素材の逐語",
                 goal=("次の 3 つの記録から、障害の発生時刻とサービス名を抽出する。"
                       "出力は本番の抽出契約に従い "
                       '{"records": [{"fields": {"id": "<記録 id>", "service": "...", '
                       '"time": "..."}, "evidence": [{"source_id": "<記録 id>", '
                       '"locator": "<行番号など>", "excerpt": "<素材からの逐語引用>"}]}]}。'
                       "**excerpt は素材にある文字列をそのまま写すこと**（要約や言い換えを"
                       "書かない）。\n\n" + EXTRACT_SOURCES_TEXT),
                 check=lambda d: check_extract_records(d, EXTRACT_SOURCES, {"r1", "r2", "r3"})),
    # --- 取得役（kind=retrieve。coverage.json で missing・2026-08-30 に追加）
    # 本番は `ollama-read`（`--tools read --max-rounds 30`）で走る。**素材はプロンプトに
    # 入れず**作業ディレクトリへ配り、道具で読ませる——本番と同じ形。契約は
    # `validate_node_data` が受けるが**器しか見ない**ので、EX1F と同じ照合（逐語・実在）を
    # ここで足す。RT2 は該当が無い問いで、空の sources を返せるか＝捏造の面を見る。
    "RT1": dict(kind="retrieve", expect="該当 2 件・証跡は実物の逐語",
                material=RETRIEVE_FILES,
                goal=("作業ディレクトリの notes/ 以下にある障害記録から、"
                      "**billing-api が 5xx を返した障害**の根拠を集める。"
                      "道具でファイルを読み、該当する記録のパスと実物からの逐語引用を"
                      "証跡として添えること。"),
                check=lambda d: check_retrieve_sources(d, RETRIEVE_RELEVANT)),
    "RT2": dict(kind="retrieve", expect="該当なし（空の sources）",
                material=RETRIEVE_FILES,
                goal=("作業ディレクトリの notes/ 以下にある障害記録から、"
                      "**2026-09-03 に発生した障害**の根拠を集める。"
                      "道具でファイルを読み、該当する記録のパスと実物からの逐語引用を"
                      "証跡として添えること。"),
                check=lambda d: check_retrieve_sources(d, set(), want_empty=True)),
    # --- 統合役（kind=synthesize。coverage.json で missing・2026-08-30 に追加）
    # 本番は base `ollama`（`--think off --tools bash --max-rounds 12`）で走り、出力は
    # **自由記述のまま**下流へ渡る（STRUCTURED_KINDS に無い）。依存は要約せず全文で届く
    # （`_FULL_DEPENDENCY_KINDS`）。測るのは集約族の 2 面——落とさないか・足さないか、
    # そして依存が申告した欠落を伝えるか（実行規律が明記を求めている）。
    "SY1": dict(kind="synthesize", expect="3 件すべてを統合・依存に無いものを足さない",
                goal=("3 つの依存タスクの成果を統合し、この run の成果物一覧と"
                      "実行手順を 1 つの文書にまとめる。"),
                deps={"t1": {"output": "ingest.py を追加。run ログ 12 ファイルを読み込む。"
                                       "テスト 6 件 pass。"},
                      "t2": {"output": "aggregate.py を追加。日次のトークン合計を出す。"
                                       "テスト 4 件 pass。"},
                      "t3": {"output": "render.py を追加。Markdown の表で出力する。"
                                       "テスト 3 件 pass。"}},
                check=lambda t: check_synthesis(
                    t, ["ingest.py", "aggregate.py", "render.py"],
                    forbidden=["export.py", "notify.py"])),
    "SY2": dict(kind="synthesize", expect="欠落（ITEM-11 / ITEM-12）を落とさない",
                goal=("2 つの依存タスクの成果を統合し、索引作成の結果を 1 つの文書に"
                      "まとめる。"),
                deps={"t1": {"output":
                             "notes/ の 12 件のうち 10 件から見出しを抽出して索引に"
                             "まとめました。ITEM-11.md と ITEM-12.md は読み取りに"
                             "失敗したため含めていません。"},
                      "t2": {"output": "抽出した見出しを report.md へ書き出しました。"
                                       "索引の行数は 10 行です。"}},
                check=lambda t: check_synthesis(
                    t, ["ITEM-11", "ITEM-12"],
                    false_claim=r"12\s*件(すべて|全て|全部)(から|を)?[^。\n]{0,12}"
                                r"(索引|抽出|まとめ|完了|収録)")),
    # SY2 の欠落は依存の**散文**にしか無い。本番で done の依存が構造化して申告できる欠落は
    # 契約の `warnings` / `issues` だけである（`{"ok": false}` を返した依存は failed になり
    # `deps_satisfied` を通らないので集約役まで届かない）。そこで同じ素材を本番のチャネルへ
    # 移し、2 本の腕で測る——SY2W は機械抜き（この面にそもそも機械が要るのか）、
    # SY2P は本番の経路（`carry_dependency_gaps` を通した成果）。
    "SY2W": dict(kind="synthesize", expect="欠落を運ぶ（機械なし・診断用の腕）",
                 no_carry=True, goal=SY2_GOAL, deps=SY2_DEPS,
                 check=lambda t: check_synthesis(t, ["ITEM-11", "ITEM-12"])),
    "SY2P": dict(kind="synthesize", expect="欠落を運ぶ（本番＝機械が転記）",
                 goal=SY2_GOAL, deps=SY2_DEPS,
                 check=lambda t: check_synthesis(t, ["ITEM-11", "ITEM-12"])),
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
    prompt = r.stdout.strip()
    # 導入済みの flow-worker スキルが新しい kind（extract / retrieve）を知らないとき、
    # 本番（agent.py）は役割行を【出力契約】として後置する。ここを写さないと**契約なし**の
    # プロンプトで測ることになる——実測 2026-08-30 の版は records も sources も出さない。
    kind = case.get("kind") or ""
    marker = '"records"' if kind == "extract" else ('"sources"' if kind == "retrieve" else "")
    if marker and marker not in prompt:
        prompt += f"\n\n【出力契約】{engine.worker_role(kind)}"
    return prompt


def call(prompt: str, cmd: "list[str] | None" = None,
         command_env: "dict[str, str] | None" = None,
         cwd: "str | None" = None) -> "tuple[int, str, str, float]":
    raw = list(cmd or CMD)
    if THINK_OVERRIDE and "--think" in raw:
        raw[raw.index("--think") + 1] = THINK_OVERRIDE
    argv = [a.replace("{model}", MODEL) for a in raw if a not in DROP_ARGS]
    # 上限は group ごと（engine.run_process）。孫（エージェント CLI・推論クライアント）を
    # 残すと次の実行が順番待ちになる。経過は monotonic——壁時計はマシンのスリープを含む
    # ので、上限内に終わった実行を timeout と記録していた（実測 2026-08-29〜30）。
    started = time.monotonic()
    try:
        p = engine.run_process(argv, input=prompt, capture_output=True, text=True,
                               timeout=WALL_LIMIT, cwd=cwd,
                               env={**os.environ, **(command_env or {})})
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "TIMEOUT"
    return rc, out, err, time.monotonic() - started


def workdir_for(cid: str, i: int) -> str:
    """この実行の作業ディレクトリ。**本番の道具ループはワークスペースで走る**ので、
    リポジトリの中では走らせない（道具付きの起動形では bash / read が cwd を見る）。
    `material` を宣言したケースは素材ファイルをここへ配る＝道具が実物を読める。"""
    root = LEDGER_DIR / "work" / f"{cid}-{i}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in (CASES[cid].get("material") or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def run_one(cid: str, i: int) -> dict:
    case = CASES[cid]
    kind = case.get("kind") or "evaluator"
    prompt = build_prompt(case)
    # 本番（agent-flow の _apply_methods）と同じ位置——プロンプトの末尾へ空行 1 つで後置。
    extra, applied = method_text(kind)
    if extra:
        prompt = f"{prompt}\n\n{extra}"
    cmd, _src, command_env = cmd_for(kind)
    cwd = workdir_for(cid, i)
    rc, out, err, wall = call(prompt, cmd, command_env, cwd)
    repaired = False

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
            r_rc, r_out, r_err, r_wall = call(repair, cmd, command_env, cwd)
            wall += r_wall
            repaired = True
            if r_rc == 0 and r_out.strip():
                try:
                    fixed = engine.extract_list(r_out)
                except Exception:  # noqa: BLE001
                    fixed = None
                if isinstance(fixed, list):
                    data, out = fixed, r_out
        # extract / retrieve も本番（agent.py）は契約検査に落ちたら形式修復を 1 回入れる。
        # 実測 2026-08-30: e4b の外し方 2/5 は**器だけ**（`{"records": [...]}` を付けず
        # 裸の配列を返す。中身の証跡は素材の逐語で正しい）——本番なら救えている失敗を
        # 不合格に数えないため、同じ 1 回を写す。違反理由も本番と同じく契約検査から取る。
        if kind in ("extract", "retrieve"):
            first = engine.validate_node_data(kind, data)
            if isinstance(first, str):
                repair = (f"{prompt}\n\n[前回の出力は契約違反でした]\n"
                          f"前回の出力（先頭 400 文字）: {out[:400]}\n"
                          f"違反: {first}\n"
                          "説明・前置き・コードフェンスを付けず、指示された JSON だけを"
                          "再出力してください。")
                r_rc, r_out, r_err, r_wall = call(repair, cmd, command_env, cwd)
                wall += r_wall
                repaired = True
                if r_rc == 0 and r_out.strip():
                    try:
                        fixed = extract_json(r_out)
                    except Exception:  # noqa: BLE001
                        fixed = None
                    if fixed is not None:
                        data, out = fixed, r_out
        if kind == "synthesize" and not case.get("no_carry"):
            # 本番（agent.py）は統合結果へ依存の申告した欠落を機械的に転記する。写さないと
            # **本番なら運ばれている欠落**をモデルの失点として数える。`no_carry` は診断用の
            # 腕で（--drop-format と同じ位置づけ）、機械抜きの素の実力を見るときだけ立てる。
            out, data = engine.carry_dependency_gaps(case.get("deps") or {}, out, data)
        if kind != "evaluator" and kind not in engine.structured_kinds():
            # 本番が JSON を抽出しない kind（classify は `class=<ラベル>` の本文契約、
            # synthesize は自由記述の成果物）は**本文のまま**採点する。JSON 抽出の失敗で
            # 不合格に数えると、本番なら読めている出力を落とすことになる。
            ok, note = case["check"](out)
            mode = "correct" if ok else "wrong"
        elif data is None and kind == "verify":
            # verify だけは JSON が無くても本番のゲートが動く——`_normalize_verify` は
            # 本文の `verify=pass` / `verify=fail` から `ok` を導き、どちらも無ければ
            # fail へ倒す。ここで unparsable として落とすと、**本番なら正しく fail に
            # 倒れている出力**をモデルの不合格として数えることになる（split の形式修復を
            # 本番に合わせているのと同じ理由）。
            ok, note = case["check"](out)
            mode = "correct" if ok else "wrong"
        elif data is None:
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
        "V1": {"ok": False, "issues": ["12 件のうち 10 件しか索引に無い"]},
        "V2": {"ok": False, "issues": ["pytest を実行できないため確かめられない"]},
        "V3": {"ok": False, "issues": ["ファイルの実物を読めないため確かめられない"]},
        "CL1": "分類の結果は class=bug です。",
        "MP1": ["# 概要", "## 手順", "### 補足"],
        "EX1F": {"records": [
            {"fields": {"id": "r1", "service": "billing-api", "time": "09:14"},
             "evidence": [{"source_id": "r1", "locator": "1",
                          "excerpt": "09:14 billing-api が 5xx を返し始めた"}]},
            {"fields": {"id": "r2", "service": "svc-payment", "time": "10:02"},
             "evidence": [{"source_id": "r2", "locator": "1",
                          "excerpt": "10:02 svc-payment のデプロイ直後に遅延が出た"}]},
            {"fields": {"id": "r3", "service": "notify-worker", "time": "11:30"},
             "evidence": [{"source_id": "r3", "locator": "1",
                          "excerpt": "11:30 notify-worker がキューを詰まらせた"}]}]},
        "E3": {"decision": "replan", "reason": "出力段のノードが無い",
               "new_tasks": [{"id": "t4", "goal": "レポートを書き出す"}]},
        "RT1": {"sources": [
            {"id": "s1", "uri": "notes/incident-2026-08-14.md", "title": "2026-08-14 障害記録",
             "locator": "2", "excerpt": "09:14 billing-api が 5xx を返し始めた。",
             "digest": "billing-api の 5xx 発生"},
            {"id": "s2", "uri": "notes/incident-2026-08-21.md", "title": "2026-08-21 障害記録",
             "locator": "2", "excerpt": "22:40 上流のコネクション上限が原因と判明。",
             "digest": "再発と原因"}]},
        "RT2": {"sources": [], "warnings": ["2026-09-03 の記録は notes/ に無い"]},
        "SY1": ("成果物: ingest.py（読み込み）→ aggregate.py（日次集計）→ render.py（表出力）。"
                "テストは 13 件すべて pass。"),
        "SY2": ("索引は 12 件中 10 件から作成。ITEM-11.md と ITEM-12.md は読み取りに失敗した"
                "ため未収録で、report.md も 10 行にとどまる。"),
        "SY2W": "索引は 10 件。ITEM-11.md と ITEM-12.md は読み取り失敗のため未収録。",
        "SY2P": "索引は 10 件。ITEM-11.md と ITEM-12.md は読み取り失敗のため未収録。",
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
        "V1": [{"ok": True, "issues": []}],
        "V2": [{"ok": True, "issues": []}],
        "V3": [{"ok": True, "issues": []}],
        "CL1": ["分類の結果は class=feature です。"],
        # 本文中の # をコメント記号ごと拾った形（拾いすぎ）と、件数へ化けた形
        "MP1": [["# 概要", "## 手順", "# はコメント記号であって見出しではない", "### 補足"],
                {"count": 3}],
        # 証跡を言い換えた形（器は合っているので validate_node_data は通る）
        "EX1F": [{"records": [
            {"fields": {"id": "r1", "service": "billing-api", "time": "09:14"},
             "evidence": [{"source_id": "r1", "locator": "1",
                          "excerpt": "billing-api でエラーが発生した"}]}]}],
        "E3": [{"decision": "done", "reason": "全ノード done・verify pass"}],
        # 器は合っている（validate_node_data は通る）が中身が捏造・取りこぼしの形
        "RT1": [
            # 実在しないファイルを指す
            {"sources": [{"id": "s1", "uri": "notes/incident-2026-09-03.md", "title": "記録",
                          "locator": "1", "excerpt": "09:14 billing-api が 5xx を返し始めた。",
                          "digest": "捏造"}]},
            # 実在するファイルだが引用が言い換え
            {"sources": [{"id": "s1", "uri": "notes/incident-2026-08-14.md", "title": "記録",
                          "locator": "2", "excerpt": "billing-api でエラーが起きた",
                          "digest": "言い換え"},
                         {"id": "s2", "uri": "notes/incident-2026-08-21.md", "title": "記録",
                          "locator": "2", "excerpt": "22:40 上流のコネクション上限が原因と判明。",
                          "digest": "再発"}]},
            # 片方しか覆っていない
            {"sources": [{"id": "s1", "uri": "notes/incident-2026-08-14.md", "title": "記録",
                          "locator": "2", "excerpt": "09:14 billing-api が 5xx を返し始めた。",
                          "digest": "発生"}]}],
        # 該当が無いのに「それらしい」source を作る（PV1 と同じ捏造の形）
        "RT2": [{"sources": [{"id": "s1", "uri": "notes/incident-2026-09-03.md",
                              "title": "2026-09-03 障害記録", "locator": "1",
                              "excerpt": "09:03 障害が発生した", "digest": "捏造"}]},
                {"sources": [{"id": "s1", "uri": "notes/incident-2026-08-21.md",
                              "title": "2026-08-21 障害記録", "locator": "2",
                              "excerpt": "22:40 上流のコネクション上限が原因と判明。",
                              "digest": "無関係を引いた"}]}],
        # 依存を落とす / 依存に無い成果物を足す
        "SY1": ["成果物: ingest.py と aggregate.py。テストは pass。",
                "成果物: ingest.py・aggregate.py・render.py・export.py の 4 本。"],
        # 欠落を伝えない / 完了と書く
        "SY2W": ["索引を report.md に書き出しました。行数は 10 行です。"],
        "SY2P": ["索引を report.md に書き出しました。行数は 10 行です。"],
        "SY2": ["索引を report.md に書き出しました。行数は 10 行です。",
                "notes/ の 12 件すべてから見出しを抽出して索引にまとめ、report.md へ"
                "書き出しました。ITEM-11 と ITEM-12 も収録済みです。"],
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
