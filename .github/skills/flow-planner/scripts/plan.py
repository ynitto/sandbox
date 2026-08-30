#!/usr/bin/env python3
"""flow-planner — agent-flow 向け高精度タスク分解・戦略選択。

3段階パイプラインで要求を分析→戦略選定→グラフ生成する。
agent-flow の --planner flow-planner で呼び出される。

Usage:
    python3 plan.py "<要求>" [--model <model>] [--review auto|true|false]
                    [--granularity auto|coarse|fine|finest] [--probe-root <dir>]
                    [--context <text>] [--tier <tier>]
    → JSON を stdout に出力: {"strategy": {...}, "tasks": [...]}
    granularity: auto=complexity から導出（既定）/ coarse|fine|finest=明示指定が優先。
    context: プロジェクト文脈（案 H・オプトイン）。agent-flow から渡され Phase 1/3 へ前置される。
    tier: 実行ティア（agent-control の workloads.flow.tier）。basic なら auto 粒度を finest へ
          倒し、Phase 3 へ basic 向けの分解指示を足し、review=auto を有効へ倒す。
"""
from __future__ import annotations

import glob as globlib
import json
import os
import subprocess
import sys
import tempfile

# スキルのルート（このスクリプトの2階層上）
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import yaml  # type: ignore
    def _load_yaml(path):
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
except ImportError:
    yaml = None
    def _load_yaml(path):
        raise RuntimeError("PyYAML required for patterns-catalog.yaml")


def load_catalog():
    """パターンカタログを読み込む。"""
    cat_path = os.path.join(SKILL_DIR, "patterns-catalog.yaml")
    if not os.path.exists(cat_path):
        return None
    return _load_yaml(cat_path)


# --------------------------------------------------------------------------
# Phase 1: 要求分析
# --------------------------------------------------------------------------
ANALYZE_PROMPT = """\
あなたは分散 Dynamic Workflow の計画アナリストです。
以下の要求を分析し、タスク分解と戦略選定に必要な属性を構造化してください。

## 分析観点

1. **intent**: 要求の本質を1文で要約
2. **decomposition_axes**: この要求をどの軸で分割すべきか（機能別/フェーズ別/データ別/観点別 等）
3. **subtasks**: 分割した場合の主要サブタスク（3-8個）
4. **data_flow**: 入力データの性質
   - static: 事前に確定している（ファイル一覧、固定リスト等）
   - dynamic: 実行時に判明する（API応答、分類結果に依存等）
   - unknown: 不明
5. **quality_focus**: 重視すべき品質軸
   - speed: 速度（多くを素早く処理）
   - accuracy: 正確性（間違いを許さない）
   - coverage: 網羅性（漏れなく調べる）
   - exploration: 探索性（多様な案を出す）
6. **complexity**: simple / moderate / complex
7. **estimated_steps**: この要求を満たすのに**最小限**必要な作業ステップ数（整数）。
   実装・調査・検証をまとめて数え、余裕や理想の分割ではなく「これ以下では終わらない」数を出す
8. **constraints**: 制約条件（順序依存、リソース制限等）
9. **domain_hints**: ドメインのヒント（コード変更、リサーチ、データ処理等）
10. **enumerable**: 「**同一手順を多数の独立した対象へ繰り返す**」タスクかどうか。
    次の 3 条件を**それぞれ独立に**判定すること（1 つでも false なら列挙駆動にしない）:
    - `same_procedure`: 対象ごとに手順が同一か（対象が変わっても作業内容は同じか）
    - `independent`: 対象間に依存が無いか（先の対象の結果が次の対象に要らないか）
    - `per_target_deliverable`: 成果が対象単位で完結するか（対象ごとに独立した成果物になるか）

    3 条件がすべて true のときだけ、対象の種別 `target_kind` と、**実行時に対象一覧を得る
    手順** `how_to_enumerate`（どのディレクトリ・ファイルをどう走査すれば一覧が得られるか。
    可能なら具体的なパスやグロブを書く）を記入する。件数が要求から確定できるなら
    `estimated_count` に整数を、確定できなければ null を書く（**推測で埋めない**）。

    ⚠ 注意: ファイル・関数・モジュールは「見ようと思えば常に列挙可能」だが、それは
    ここで言う列挙駆動ではない。**単一の成果物を作る作業**（新機能の実装・バグ修正・
    設計など）は多数のファイルに触れても `independent` / `per_target_deliverable` が
    false になる。逆に「各 API のドキュメント化」「全ファイルへ同じ規約を適用」
    「各モジュールの現状調査」は 3 条件を満たす。安易に true にしないこと。

## 出力

JSON オブジェクトのみを出力してください:
```json
{{
  "intent": "...",
  "decomposition_axes": ["..."],
  "subtasks": ["..."],
  "data_flow": "static|dynamic|unknown",
  "quality_focus": "speed|accuracy|coverage|exploration",
  "complexity": "simple|moderate|complex",
  "estimated_steps": 6,
  "constraints": ["..."],
  "domain_hints": ["..."],
  "enumerable": {{
    "same_procedure": true,
    "independent": true,
    "per_target_deliverable": true,
    "target_kind": "対象の種別（例: API エンドポイント）",
    "how_to_enumerate": "対象一覧の得かた（例: src/routes/**/*.ts のルート定義を走査）",
    "estimated_count": null
  }}
}}
```

## 要求

{request}"""


# --------------------------------------------------------------------------
# Phase 2: 戦略選定
# --------------------------------------------------------------------------
SELECT_PROMPT = """\
あなたは分散 Dynamic Workflow の戦略選定エキスパートです。
要求分析の結果に基づき、最適なワークフローパターンを選んでください。

## 利用可能なパターン

{patterns_desc}

## 複合テンプレート（よく使う組み合わせ）

{composites_desc}

## ユースケース別の推奨

{use_cases_desc}

## Decision Matrix によるスコアリング結果

要求の属性（data_flow={data_flow}, quality_focus={quality_focus}, complexity={complexity}）に基づく候補:
{scored_candidates}
{enumeration_note}
## 指示

上記の候補から最適なパターン（組み合わせ）を選び、並列数を決定してください。
複合テンプレートが適合する場合はそれを使い、適合しない場合は基本パターンを組み合わせてください。

### 語彙ロック（厳守）

- `patterns` に書けるのは次の7つの基本パターン名のみ:
  fan-out-and-synthesize / adversarial-verification / classify-and-act /
  generate-and-filter / tournament / loop-until-done / map-reduce
- `composite_template` は上記「複合テンプレート」のキー名か null のみ。
- synthesize / generate / verify / judge / filter / reduce / split / map /
  classify / work は**ノード種別であってパターンではない**。`patterns` に書かない。
- 派生語・同義語（例: "panel of verifiers", "tournament with rubric"）は使わず、
  対応する正規名（adversarial-verification, tournament）へ読み替える。

出力は JSON オブジェクトのみ:
```json
{{
  "patterns": ["pattern1", "pattern2"],
  "parallelism": N,
  "reason": "選定理由",
  "composite_template": "テンプレート名 or null",
  "review": true
}}
```

review は統合（synthesize/reduce）前に検証gateを挟むかどうか。精度重視なら true。

## 要求分析結果

{analysis}"""


# --------------------------------------------------------------------------
# Phase 3: グラフ生成
# --------------------------------------------------------------------------
BUILD_PROMPT = """\
あなたは分散 Dynamic Workflow のグラフ設計者です。
選定された戦略に従い、実行可能なタスクグラフを生成してください。

## 選定戦略

パターン: {patterns}
並列数: {parallelism}
理由: {reason}
テンプレート: {composite_template}
検証gate: {review}

{enumeration_note}{tier_note}{split_note}
## 粒度（厳守）

目標粒度: {granularity_target}
成果ノード（kind が work/generate/map）の数: {work_lo}–{work_hi} 個（上限16）{steps_hint}
各成果ノードのスコープ上限:
- 1 モジュール相当（または明示された単一結合点）
- 想定変更は約 30 行以内
- goal 先頭に必ず次の2行を付ける:
  [scope] 触ってよいパスまたは記号
  [out_of_scope] このノードでやらないこと
verify/synthesize/reduce/filter/judge/classify/split は上記の個数・スコープ上限の対象外。
map-reduce は split を1つだけ（map は実行時展開）。classify-and-act は classify のみでよい。

## グラフ設計ルール

1. 各ノードには kind を付ける: work/generate/classify/synthesize/verify/filter/judge/reduce/split/map
2. 並列にできるタスクは deps を空に
3. 統合・検証ノードは先行タスクに依存させる
4. map-reduce では split ノードを1つだけ置く（map/reduce は実行時に動的展開される）
5. review=true の場合、統合（synthesize/reduce）の前に verify gate を1つ挟む
6. 依存は既存タスク id のみ、循環は作らない
7. id は短く（t1, t2, ... / classify, filter, synth, gate 等）
8. work/generate ノードには最初に読むべき範囲を read_allocation=[{{"path":"...","range":"任意","reason":"..."}}] で割り付ける。大きい Python 参照で対象を正確に特定できる場合だけ slice=true, symbols=["Class.method"] を追加する
9. 依存成果は既定 digest（要約・成果物参照のみ）。完全な構造化データが不可欠なノードだけ dependency_input="full" を宣言する
10. work/generate ノードには処理契約 operation を付け、そのノードが作る成果物のパスを deliverables に列挙する（scope.write と一致させ、検証コマンドがあれば verification.commands に argv の配列で書く）。**成果物が 2 つ以上あるノードを自分で 2 つに割らない**——エンジンが 1 成果物 1 ノードの直列へ割る
11. filter/judge ノードには判定契約 decision を付ける。facts は候補の本文から転記できる項目だけ（type は bool/int/string、string は values で取りうる値を列挙）、criteria は残す条件（AND・op は eq/ne）、tie_break は最良案を 1 つに絞る順位基準（fact と min/max）。**選別・比較の観点を goal の自由文に書かず、decision の条件として宣言する**（採否はモデルではなく機械が決める）

## サブタスク（Phase 1 で特定済み・骨格）

{subtasks}

## 出力

JSON オブジェクトのみ（`tasks` 配列を 1 つ持つ。配列を裸で返さない）:
```json
{{"tasks": [
  {{"id": "t1", "goal": "[scope] path\\n[out_of_scope] ...\\n具体的な目標", "deps": [], "kind": "work", "read_allocation": [{{"path": "src/x.py", "range": "10-40", "reason": "変更箇所", "slice": true, "symbols": ["Class.method"]}}], "dependency_input": "digest", "operation": {{"operation_class": "feature", "scope": {{"read": ["src"], "write": ["src/x.py", "tests/test_x.py"]}}, "deliverables": ["src/x.py", "tests/test_x.py"], "verification": {{"commands": [["python", "-m", "pytest", "-q", "tests"]]}}}}}},
  {{"id": "t2", "goal": "候補から条件を満たすものを残す", "deps": ["t1"], "kind": "filter", "decision": {{"facts": [{{"name": "extra_deps", "type": "bool", "description": "追加依存が要るか"}}], "criteria": [{{"fact": "extra_deps", "op": "eq", "value": false}}]}}}},
  ...
]}}
```

goal は要求に対して具体的に書くこと（「サブタスク1」のような抽象的記述は不可）。

## 元の要求

{request}"""


# --------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------
import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# 呼び出すエージェント CLI。--agent-cli で切り替える（名前は agents/<name>.json の定義名）。
# 既定は kiro（従来動作）。呼び出し側（agent-flow）は planner に設定された agent_cli を渡す。
AGENT_CLI = "kiro"


def _agentcli():
    """agentcore.agentcli（定義ファイル駆動の argv 組み立て）。使えなければ None。

    agent-flow は起動時に PYTHONPATH でこれを渡す。使えるときは組み込み 4 種を特別扱いせず、
    `agents/<name>.json` を置いただけの CLI（agent-ollama 等）もそのまま呼べる。"""
    try:
        from agentcore import agentcli  # type: ignore
        return agentcli
    except ImportError:
        return None


def _agent_cmd(cli: str, model: str | None, prompt: str):
    """エージェント CLI 1 回分の (argv, stdin テキスト, 最終応答ファイル) を組み立てる。
    agent-flow / agent-project の _agent_cmd と同じ規約に揃える（ヘッドレス・応答本文のみ）。
    最終応答ファイルは codex のみ（stdout がイベントログのため）。

    agentcore が使えるならそちらへ委譲する。3 フェーズはいずれも材料を全部プロンプトで
    受け取って JSON を返すだけの「読まない系」なので readonly で呼ぶ——道具を持たせると、
    ツールループ型の CLI（agent-ollama の --tools bash 等）が契約どおりの JSON 応答を
    「規約から外れています」と蹴って空回りする。"""
    core = _agentcli()
    if core is not None:
        spec = core.load_cli(cli)
        built = core.headless_cmd(spec, model, prompt, readonly=True)
        return built["argv"], built["stdin"], built["output_file"]
    if cli == "claude":
        # Claude Code ヘッドレス。プロンプトは stdin 渡し（ARG_MAX に当たらない）。
        cmd = ["claude", "-p", "--output-format", "text", "--dangerously-skip-permissions"]
        if model:
            cmd += ["--model", model]
        return cmd, prompt, None
    if cli == "copilot":
        cmd = ["copilot", "-s", "--allow-all-tools", "--allow-all-paths", "--no-color"]
        if model:
            cmd += ["--model", model]
        return cmd + ["-p", prompt], None, None
    if cli == "codex":
        # codex exec は stdout にイベントログを混ぜるため、最終応答は別ファイルから読む。
        fd, out_file = tempfile.mkstemp(prefix="flow-planner-codex-", suffix=".txt")
        os.close(fd)
        cmd = ["codex", "exec", "--skip-git-repo-check",
               "--dangerously-bypass-approvals-and-sandbox", "--color", "never",
               "--output-last-message", out_file]
        if model:
            cmd += ["--model", model]
        return cmd + ["-"], prompt, out_file
    if cli != "kiro":
        # agentcore なしで定義ファイル駆動の CLI は組み立てられない。黙って kiro-cli へ
        # 倒すと「指定した CLI で計画したつもりが別物」になるので、失敗として上げる。
        raise RuntimeError(
            f"agent_cli={cli!r} の argv を組み立てられません（agentcore を import できず、"
            "組み込みの kiro/claude/copilot/codex でもありません）")
    cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"]
    if model:
        cmd += ["--model", model]
    return cmd + [prompt], None, None


def run_agent(prompt: str, model: str | None) -> str:
    """設定されたエージェント CLI（AGENT_CLI）を 1 回呼び出して応答本文を返す。

    rc=0 でも本文が空で返る CLI がある（例: kiro-cli は AWS 認証が切れるとバナーだけ出して
    rc=0 で終わる）。空応答を成功として扱うと、この後の JSON 解析が黙って失敗し、呼び出し元は
    stub 戦略へフォールバックする＝「LLM を呼べていないのに計画できたように見える」。
    空はここでエラーにして、呼び出し元が失敗と分かるようにする。"""
    cmd, stdin_text, out_file = _agent_cmd(AGENT_CLI, model, prompt)
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, input=stdin_text, env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"{cmd[0]} failed (rc={proc.returncode}): {proc.stderr[:500]}")
        text = _ANSI_RE.sub("", proc.stdout).strip()
        if out_file:                     # codex: 最終応答ファイルが取れればそれを正とする
            try:
                with open(out_file, encoding="utf-8") as f:
                    text = f.read().strip() or text
            except OSError:
                pass
        if not text:
            raise RuntimeError(f"{cmd[0]} returned an empty response"
                               f" (rc=0). 認証切れ・モデル指定の誤りを疑ってください。")
        return text
    finally:
        if out_file:
            try:
                os.remove(out_file)
            except OSError:
                pass


def extract_json(text: str):
    """テキストから JSON を抽出（コードブロック対応）。"""
    # ```json ... ``` ブロック
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1)
    text = text.strip()
    # 最初の { or [ から最後の } or ] まで
    starts = [text.find(c) for c in "{[" if text.find(c) >= 0]
    ends = [text.rfind(c) for c in "}]" if text.rfind(c) >= 0]
    if not starts or not ends:
        return json.loads(text)
    start = min(starts)
    end = max(ends)
    return json.loads(text[start:end + 1])


def score_patterns(catalog: dict, analysis: dict) -> list[tuple[str, int]]:
    """Decision Matrix でパターンをスコアリング。"""
    matrix = catalog.get("decision_matrix", {})
    scores: dict[str, int] = {}
    patterns = catalog.get("patterns", {})
    for p in patterns:
        scores[p] = 0

    # data_flow
    df = analysis.get("data_flow", "unknown")
    for pat, sc in matrix.get("data_flow", {}).get(df, {}).items():
        scores[pat] = scores.get(pat, 0) + sc

    # quality_focus
    qf = analysis.get("quality_focus", "speed")
    for pat, sc in matrix.get("quality_focus", {}).get(qf, {}).items():
        scores[pat] = scores.get(pat, 0) + sc

    # complexity
    cx = analysis.get("complexity", "moderate")
    for pat, sc in matrix.get("complexity", {}).get(cx, {}).items():
        scores[pat] = scores.get(pat, 0) + sc

    # use_case_mapping によるキーワードマッチボーナス
    intent = analysis.get("intent", "") + " " + " ".join(analysis.get("domain_hints", []))
    for mapping in catalog.get("use_case_mapping", []):
        if any(kw in intent.lower() for kw in mapping.get("keywords", [])):
            comp = mapping.get("composite")
            if comp and comp in catalog.get("composites", {}):
                for pat in catalog["composites"][comp].get("patterns", []):
                    scores[pat] = scores.get(pat, 0) + 3

    # 列挙駆動の加点（boost。件数が確定している force は Phase 2 の後で決定的に強制する）。
    # data_flow=static でも「同一手順×独立多対象」なら map-reduce が正しい——Matrix だけだと
    # 静的な一覧（リポジトリ内の API・ファイル群）が fan-out-and-synthesize に吸われる。
    if (analysis.get("enumeration_decision") or {}).get("mode") == "boost":
        scores["map-reduce"] = scores.get("map-reduce", 0) + ENUMERATION_BOOST

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return ranked


def match_composite(catalog: dict, analysis: dict) -> str | None:
    """ユースケースマッピングから複合テンプレートを探す。"""
    intent = str(analysis.get("intent") or "")
    hints = " ".join(str(h) for h in (analysis.get("domain_hints") or []))
    text = (intent + " " + hints).lower()
    for mapping in catalog.get("use_case_mapping", []):
        if any(kw in text for kw in mapping.get("keywords", [])):
            return mapping.get("composite")
    return None


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------
def normalize_estimated_steps(value) -> "int | None":
    """`estimated_steps` を整数へ正規化する（読めない・非正なら None）。

    LLM は "6" / 6.0 / "約6ステップ" / null と揺れる。Phase 3 のプロンプトへ埋める値なので、
    数として読めないものは黙って落とす——「約6ステップ」をそのまま渡すと、目安のつもりの
    文字列が指示文の中で別の意味を持ちうる。"""
    if isinstance(value, bool):     # bool は int の subclass。ステップ数ではない
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        return n if n > 0 else None
    m = re.search(r"\d+", str(value or ""))
    if not m:
        return None
    n = int(m.group())
    return n if n > 0 else None


def phase1_analyze(request: str, model: str | None, context: str = "") -> dict:
    """Phase 1: 要求分析。

    `context`（案 H・オプトイン）はプロジェクト文脈（charter/rules.md/リポジトリ理解）の
    agent-flow 側スナップショット。プロジェクト内で不変なので、request 本体より前へ
    プレフィックスとして置く（agent-flow / agent-project と同じ「安定部を先に」の規約を
    ここでも手で踏襲する。標準ライブラリのみで動く独立スクリプトのため agentcore は
    importせず、規約だけを揃える）。空なら従来どおり request 単体のプロンプト。"""
    prompt = ANALYZE_PROMPT.format(request=request)
    if context:
        prompt = f"{context}\n\n{prompt}"
    raw = run_agent(prompt, model)
    analysis = extract_json(raw)
    if not isinstance(analysis, dict):
        raise ValueError("Phase 1: analysis is not a dict")
    analysis["estimated_steps"] = normalize_estimated_steps(analysis.get("estimated_steps"))
    return analysis


# --------------------------------------------------------------------------
# 列挙駆動の分解（設計: docs/plans/2026-07-28-dynamic-enumeration-decomposition-design.md）
#   「同一手順×独立多対象」のタスクでは、ノード数を計画時に決めず**実行時の列挙**から
#   導出する（split → map×N 動的展開）。要求文だけを見る Decision Matrix ではこの類型が
#   fan-out-and-synthesize に吸われ、対象単位のノードが生まれない。
# --------------------------------------------------------------------------
# 3 条件を個別に持つのが要点。「列挙できるか」ではなく「列挙駆動にしてよいか」を見る——
# ファイル・関数は常に列挙可能なので、単一フラグを LLM に判定させると単一成果物の実装まで
# map-reduce へ倒れる（＝他パターンを侵食する単一戦略への崩壊）。
ENUMERABLE_CONDITIONS = ("same_procedure", "independent", "per_target_deliverable")
ENUMERABLE_LABELS = {
    "same_procedure": "手順が対象ごとに同一",
    "independent": "対象間に依存が無い",
    "per_target_deliverable": "成果が対象単位で完結",
}
# これ以下の件数は静的 fan-out で十分（map-reduce のオーバーヘッドが見合わない）。
ENUMERATION_MIN_COUNT = 3
# 件数不明時に map-reduce へ与える加点。Matrix の最大加点（data_flow=dynamic の +3）より
# 大きく取り、静的一覧が fan-out へ吸われるのを覆せるようにする（強制はしない）。
ENUMERATION_BOOST = 5

# 件数の正規化は最小ステップ見積りと同じ形（int / float / "12件" / null の揺れ）なので共用する。
normalize_count = normalize_estimated_steps


def _as_bool(v) -> bool:
    """LLM が返す真偽値の揺れ（true / "true" / "yes" / 1）を bool へ。不明は False。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "y", "1", "はい")
    if isinstance(v, (int, float)):
        return bool(v)
    return False


def normalize_enumerable(raw) -> dict:
    """Phase 1 の `enumerable` を正規化する。`is_enumerable` は 3 条件の AND。"""
    d = raw if isinstance(raw, dict) else {}
    conds = {k: _as_bool(d.get(k)) for k in ENUMERABLE_CONDITIONS}
    return {
        **conds,
        "is_enumerable": all(conds.values()),
        "target_kind": str(d.get("target_kind") or "").strip(),
        "how_to_enumerate": str(d.get("how_to_enumerate") or "").strip(),
        "estimated_count": normalize_count(d.get("estimated_count")),
    }


# probe（決定的走査）で無視するディレクトリ。数えたいのは成果物の対象であって依存物ではない。
_PROBE_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
                    "target", "vendor", ".mypy_cache", ".pytest_cache", ".tox", ".next"}
# 走査の打ち切り。判定に要るのは「3 件より多いか」だけなので、上限で止めても結論は変わらない。
_PROBE_CAP = 2000
_PROBE_GLOB_RE = re.compile(r"[\w./\\*-]*\*[\w./\\*-]*")
_PROBE_DIR_RE = re.compile(r"(?<![\w*])((?:[\w.-]+/)+[\w.-]*)")


def _probe_excluded(path: str, root: str) -> bool:
    """依存物・隠しディレクトリ配下のファイルか。**グロブ経路にも同じ除外を効かせる**——
    `src/**/*.ts` は node_modules 配下まで拾うため、除外しないと件数が水増しされる。"""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:                      # 別ドライブ等（Windows）は絶対パスのまま見る
        rel = path
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    if not parts:
        return False
    return (parts[-1].startswith(".")
            or any(p in _PROBE_SKIP_DIRS or p.startswith(".") for p in parts[:-1]))


def _probe_walk(base: str, seen: set) -> None:
    """base 配下のファイルを数える（隠しディレクトリ・依存物は除外・上限で打ち切り）。"""
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames
                       if d not in _PROBE_SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if f.startswith("."):
                continue
            seen.add(os.path.join(dirpath, f))
            if len(seen) >= _PROBE_CAP:
                return


def probe_target_count(hint: str, root: str = ".") -> "int | None":
    """列挙手順のヒントから**決定的に**（LLM 無しで）対象件数を数える。

    ヒント中のグロブ（`src/routes/**/*.ts`）を優先し、無ければディレクトリパス
    （`src/routes/`）配下のファイル数を数える。数えられなければ None（＝不明）を返し、
    呼び出し側はハイブリッド発動の boost へ倒す（＝従来どおり LLM が判断する）。

    **0 件は None として扱う**。計画時点ではワークスペースが手元に無いことがあり
    （worker がノード単位で clone する）、その 0 を「対象なし」と読むと列挙駆動を
    誤って止めてしまう。probe は判定材料であって、列挙そのものは split が実行時に行う。
    """
    text = str(hint or "")
    if not text.strip():
        return None
    seen: set = set()
    for pat in _PROBE_GLOB_RE.findall(text):
        pat = pat.strip("`'\"")
        if "*" not in pat or len(pat) < 3:
            continue
        try:
            for p in globlib.iglob(os.path.join(root, pat), recursive=True):
                if os.path.isfile(p) and not _probe_excluded(p, root):
                    seen.add(p)
                    if len(seen) >= _PROBE_CAP:
                        return _PROBE_CAP
        except (OSError, ValueError):
            continue
    if not seen:
        for d in _PROBE_DIR_RE.findall(text):
            base = os.path.join(root, d.strip("`'\""))
            if os.path.isdir(base):
                _probe_walk(base, seen)
            if len(seen) >= _PROBE_CAP:
                return _PROBE_CAP
    return len(seen) or None


def enumeration_decision(enumerable: dict, probed: "int | None" = None) -> dict:
    """ハイブリッド発動の判定（決定: 2026-07-28）。

    force … 3 条件全充足 **かつ** 件数 > 3 が確定 → Decision Matrix のスコアに関わらず
            patterns に map-reduce を含める（決定的強制）
    boost … 3 条件全充足だが件数不明 → Matrix へ大幅加点にとどめ、Phase 2 の LLM が最終判断
    off  … 3 条件のどれかが偽、または件数 ≤ 3 が確定 → **従来経路と完全に同一**

    件数は probe（決定的走査の実測）を Phase 1 の LLM 見積りより優先する。
    off が現行動作と一致することが、この機能を加法的（回帰なし）に保つ要。
    """
    if not enumerable.get("is_enumerable"):
        missing = [ENUMERABLE_LABELS[k] for k in ENUMERABLE_CONDITIONS if not enumerable.get(k)]
        return {"mode": "off", "count": None,
                "reason": ("列挙駆動の条件を満たさない（未充足: " + " / ".join(missing) + "）"
                           if missing else "列挙駆動の対象ではない")}
    count = probed if probed is not None else enumerable.get("estimated_count")
    source = "probe 実測" if probed is not None else "Phase 1 見積り"
    kind = enumerable.get("target_kind") or "対象"
    if count is None:
        return {"mode": "boost", "count": None,
                "reason": f"列挙駆動の 3 条件を満たすが{kind}の件数が不明 → "
                          "map-reduce へ加点し Phase 2 が最終判断"}
    if count <= ENUMERATION_MIN_COUNT:
        return {"mode": "off", "count": count,
                "reason": f"列挙可能だが{kind}は {count} 件（≤{ENUMERATION_MIN_COUNT}・{source}）"
                          " → 静的 fan-out で十分"}
    return {"mode": "force", "count": count,
            "reason": f"列挙駆動の 3 条件を満たし{kind}は {count} 件（{source}）"
                      " → map-reduce を強制"}


def enumeration_note(analysis: dict, phase: str) -> str:
    """Phase 2 / Phase 3 のプロンプトへ差し込む列挙駆動の指示（off なら空＝従来どおり）。"""
    decision = analysis.get("enumeration_decision") or {}
    mode = decision.get("mode")
    if mode not in ("force", "boost"):
        return ""
    enum = analysis.get("enumerable") or {}
    kind = enum.get("target_kind") or "対象"
    how = enum.get("how_to_enumerate") or ""
    count = decision.get("count")
    scale = f"（推定 {count} 件）" if count else "（件数は実行時に確定）"
    if phase == "select":
        head = ("**この要求は列挙駆動と判定された（強制）**。patterns に map-reduce を必ず含める"
                if mode == "force" else
                "**この要求は列挙駆動の 3 条件を満たす**。map-reduce を第一候補として検討する")
        return (f"\n## 列挙駆動の判定\n\n{head}。\n"
                f"- 対象: {kind}{scale}\n"
                f"- 判定理由: {decision.get('reason', '')}\n"
                "- 同一手順を多数の独立した対象へ繰り返すため、対象数を計画時に固定せず"
                " split（実行時列挙）→ map×N 動的展開 → reduce で処理する。\n"
                "- 精度が要るなら adversarial-verification と複合してよい（review=true）。\n")
    return (f"\n## 列挙駆動（厳守）\n\n"
            f"この要求は「同一手順を多数の{kind}へ繰り返す」と判定された{scale}。\n"
            f"- **split ノードを 1 つだけ**置き、その goal に**実行時の列挙手順**を書くこと。\n"
            f"- 列挙手順: {how or '対象一覧が得られる場所を実際に走査する'}\n"
            "- split の goal には「実際にワークスペースを走査して一覧を作る／推測で列挙しない」"
            "ことを明記し、出力は各対象を文字列とする JSON 配列だけにする。\n"
            f"- **{kind}ごとの作業を静的な work ノードへ展開しないこと**"
            "（map と reduce は split 完了後に実行時展開される）。列挙より前に必要な下準備"
            "（方針・様式の決定など）があるときだけ、split の前段に work ノードを置いてよい。\n")


def phase2_select(request: str, analysis: dict, catalog: dict,
                  model: str | None, review="auto", tier: str = "") -> dict:
    """Phase 2: 戦略選定。tier=basic では review=auto を有効へ倒す
    （basic の成果を無検証で集約・終端しない）。"""
    patterns = catalog.get("patterns", {})
    composites = catalog.get("composites", {})

    # パターン説明
    patterns_desc = "\n".join(
        f"- **{k}**: {v['description'].strip()}\n"
        f"  使いどころ: {', '.join(v.get('when_to_use', [])[:3])}\n"
        f"  並列数目安: {v.get('typical_parallelism', [2,4])}"
        for k, v in patterns.items()
    )

    # 複合テンプレート説明
    composites_desc = "\n".join(
        f"- **{k}**: {v.get('description', '').strip()} (patterns: {v.get('patterns', [])})"
        for k, v in composites.items()
    )

    # ユースケース説明
    use_cases = catalog.get("use_case_mapping", [])
    use_cases_desc = "\n".join(
        f"- {', '.join(m.get('keywords', [])[:3])}... → {m.get('composite') or 'map-reduce/loop-until-done（基本パターン）'} ({m.get('reason', '')})"
        for m in use_cases
    )

    # スコアリング
    scored = score_patterns(catalog, analysis)
    scored_top = scored[:4]
    scored_candidates = "\n".join(
        f"  {i+1}. {pat} (score={sc})" for i, (pat, sc) in enumerate(scored_top)
    )

    # 複合テンプレートのマッチ
    matched = match_composite(catalog, analysis)

    prompt = SELECT_PROMPT.format(
        patterns_desc=patterns_desc,
        composites_desc=composites_desc,
        use_cases_desc=use_cases_desc,
        data_flow=analysis.get("data_flow", "unknown"),
        quality_focus=analysis.get("quality_focus", "speed"),
        complexity=analysis.get("complexity", "moderate"),
        scored_candidates=scored_candidates,
        enumeration_note=enumeration_note(analysis, "select"),
        analysis=json.dumps(analysis, ensure_ascii=False, indent=2),
    )
    raw = run_agent(prompt, model)
    strategy = extract_json(raw)
    if not isinstance(strategy, dict):
        raise ValueError("Phase 2: strategy is not a dict")

    # 列挙駆動の決定的強制（force）。LLM が別パターンを選んでも map-reduce を先頭へ入れる。
    # 排他ではなく**追加**なのは、複合（前段の方針決め work / 集約前の verify gate）を
    # 潰さないため。発動根拠は reason に残す（観測できないと誤爆に気づけない）。
    decision = analysis.get("enumeration_decision") or {}
    if decision.get("mode") == "force":
        pats = [p for p in (strategy.get("patterns") or []) if p != "map-reduce"]
        strategy["patterns"] = ["map-reduce"] + pats
        strategy["reason"] = (str(strategy.get("reason") or "").strip()
                              + f"／[列挙駆動] {decision.get('reason', '')}").strip("／")
    elif decision.get("mode") == "boost":
        strategy["reason"] = (str(strategy.get("reason") or "").strip()
                              + f"／[列挙駆動: 加点] {decision.get('reason', '')}").strip("／")

    # review の確定
    if review == "auto":
        if str(tier or "") == BASIC_TIER:
            # basic ティアでは常時有効（明示 true/false は従来どおり尊重）
            strategy["review"] = True
        else:
            # 集約パターンがあれば auto で有効化
            pats = strategy.get("patterns", [])
            has_aggregation = any(p in ("fan-out-and-synthesize", "map-reduce") for p in pats)
            strategy["review"] = has_aggregation
    elif isinstance(review, bool):
        strategy["review"] = review

    return strategy


# --------------------------------------------------------------------------
# 粒度（複雑度連動 + スコープ契約）
# --------------------------------------------------------------------------
WORK_KINDS = frozenset({"work", "generate", "map"})
COMPLEXITY_TO_GRANULARITY = {
    "simple": "coarse",
    "moderate": "fine",
    "complex": "finest",
}
WORK_NODE_RANGES = {
    "coarse": (1, 3),
    "fine": (3, 8),
    "finest": (6, 12),
}
_SCOPE_MARKER_RE = re.compile(r"\[scope\]", re.I)
_SCOPE_PATH_RE = re.compile(
    r"`[^`]+`"
    r"|\b[\w./+-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|kt|rb|md|yaml|yml|json|toml)\b"
    r"|/(?:[\w.-]+/)*[\w.-]+",
)
_NORM_RE = re.compile(r"\s+")


def resolve_granularity(level: str | None, complexity: str | None, tier: str = "") -> str:
    """明示 coarse/fine/finest を優先。auto/未指定は complexity から導出。
    tier=basic では auto を finest へ倒す——ノードの大きさは要求の複雑さではなく
    ワーカーの能力が律速になる（明示指定は人の意思なので覆さない）。"""
    lv = (level or "auto").lower()
    if lv in WORK_NODE_RANGES:
        return lv
    if str(tier or "") == BASIC_TIER:
        return "finest"
    return COMPLEXITY_TO_GRANULARITY.get((complexity or "moderate").lower(), "fine")


# 実行ティア（agent-control の workloads.<wl>.tier。標準語彙は下から basic/small/medium/large）。
# basic は「短い一手順だけを任せる」候補向け——予算逼迫の緊急時に普段は任せない役割へ
# 投入されるため、計画側が basic でも渡せる形（1 ノード = 1 短手順・具体的 goal）へ寄せる。
BASIC_TIER = "basic"
TIER_BUILD_NOTES = {
    BASIC_TIER: (
        "\n## 実行ティア（厳守）\n\n"
        "この計画は basic ティア（最小能力のワーカー）で実行される。\n"
        "- 各成果ノード（work/generate/map）には 1 つの短い手順だけを任せる。\n"
        "- goal には対象パス・期待する成果・確認方法まで具体的に書き、ワーカーの推測・判断に任せない。\n"
        "- 判断・統合が要る工程は verify/synthesize 等の別ノードへ分ける。\n"
    ),
}


def tier_build_note(tier: str | None) -> str:
    """Phase 3 のプロンプトへ差し込む実行ティアの指示（該当なしは空＝従来どおり）。"""
    return TIER_BUILD_NOTES.get(str(tier or ""), "")


def work_node_range(target: str) -> tuple[int, int]:
    lo, hi = WORK_NODE_RANGES.get(target, WORK_NODE_RANGES["fine"])
    return lo, min(hi, 16)


def has_scope(goal: str) -> bool:
    """goal に scope 相当の記述があるか（マーカー or パス/記号ヒューリスティック）。"""
    text = goal or ""
    if _SCOPE_MARKER_RE.search(text):
        return True
    return bool(_SCOPE_PATH_RE.search(text))


def _normalize_goal(goal: str) -> str:
    g = (goal or "").lower()
    g = _SCOPE_MARKER_RE.sub("", g)
    g = re.sub(r"\[out_of_scope\]", "", g, flags=re.I)
    return _NORM_RE.sub(" ", g).strip()


def gate_tasks(tasks: list[dict], target: str, require_split: bool = False) -> list[str]:
    """決定的ゲート。不合格理由のリスト（空なら合格）。

    work 系が無いグラフ（split のみ / classify のみ等の実行時展開）は個数・scope を検査しない。
    require_split=True（列挙駆動の force）のときは split ノードの存在も検査する——強制した
    のに split が出ないと、対象単位のノードが実行時に展開されず「まとめて 1 ノード」に戻る。
    """
    issues: list[str] = []
    if require_split and not any(isinstance(t, dict) and t.get("kind") == "split" for t in tasks):
        issues.append("列挙駆動と判定されたが split ノードが無い"
                      "（対象ごとの作業を静的ノードに展開せず、split 1 つへ集約すること）")
    # split の後ろに静的な map / reduce を書いてはいけない。engine は split 完了後に要素ごとの
    # `<split>-m*` と `<split>-reduce` を動的生成するので、静的に置いた map は全件を 1 ノードで
    # 受け、reduce は展開結果を見ない（planner_eval 2026-08-23: e4b が 2/3 でこの形を書いた）。
    split_ids = {str(t.get("id")) for t in tasks if isinstance(t, dict) and t.get("kind") == "split"}
    for t in tasks:
        if not isinstance(t, dict) or not split_ids:
            continue
        if any(str(d) in split_ids for d in (t.get("deps") or [])):
            # kind を問わず落とす。engine（plan_strategy_user）は split への静的依存を
            # kind に関係なく拒む。map / reduce だけ見ていたとき、e4b は同じ形を
            # work / generate の kind で書いて素通りしていた（planner_eval PL3 0/3）。
            issues.append(f"{t.get('id')}: split の後ろに静的 {t.get('kind')} ノードを置かない"
                          "（要素ごとの処理と集約は split 完了後に実行時へ動的展開される。"
                          "split 1 つに留めること）")
    # 宣言（operation / decision）は agent-flow 側の機構の唯一の入口で、形が崩れた宣言は
    # engine が剥がす＝宣言したのに効かない状態になる。ここでは**在るか・器が合っているか**
    # だけを見る（値の正当性は engine の 1 実装が判定する）。
    for t in tasks:
        if not isinstance(t, dict):
            continue
        kind = t.get("kind", "work")
        if kind in ("work", "generate"):
            contract = t.get("operation")
            # ファイルを作らないノード（調査・締めくくり等）もあるので**宣言は必須にしない**。
            # 宣言したのに器が壊れている（engine が剥がす）形だけを落とす。
            if isinstance(contract, dict) and not [
                    d for d in (contract.get("deliverables") or []) if str(d).strip()]:
                issues.append(f"{t.get('id')}: operation.deliverables が空"
                              "（成果物を作るノードならパスを列挙し、作らないなら operation を付けない）")
        elif kind in ("filter", "judge"):
            decision = t.get("decision")
            if not isinstance(decision, dict):
                issues.append(f"{t.get('id')}: decision が無い"
                              "（残す条件を facts / criteria として宣言すること）")
                continue
            if not isinstance(decision.get("facts"), list) or not decision["facts"]:
                issues.append(f"{t.get('id')}: decision.facts が空"
                              "（候補から転記できる項目を列挙すること）")
            if not isinstance(decision.get("criteria"), list) or not decision["criteria"]:
                issues.append(f"{t.get('id')}: decision.criteria が空"
                              "（残す条件を fact / op / value で宣言すること）")
            names = {str(f.get("name")) for f in (decision.get("facts") or [])
                     if isinstance(f, dict) and str(f.get("name") or "").strip()}
            for c in (decision.get("criteria") or []):
                if isinstance(c, dict) and names and str(c.get("fact")) not in names:
                    issues.append(f"{t.get('id')}: criteria の fact '{c.get('fact')}' を "
                                  "facts で宣言していない（宣言していない項目では絞れない）")
            tie = decision.get("tie_break")
            if tie is None:
                pass
            elif not isinstance(tie, dict):
                issues.append(f"{t.get('id')}: decision.tie_break は "
                              '{{"fact": "...", "op": "min"|"max"}} のオブジェクトで書くこと')
            elif kind == "filter":
                # 順位基準は「最良案を 1 つ選ぶ」ための宣言。filter は残す/落とすだけなので
                # 使われないうえ、器が崩れていると engine が decision ごと剥がす。
                issues.append(f"{t.get('id')}: filter に tie_break は付けない"
                              "（順位基準が要るのは judge）")
            elif names and str(tie.get("fact")) not in names:
                issues.append(f"{t.get('id')}: tie_break の fact '{tie.get('fact')}' を "
                              "facts で宣言していない")

    owners: dict = {}
    for t in tasks:
        if not isinstance(t, dict) or not isinstance(t.get("operation"), dict):
            continue
        for d in (t["operation"].get("deliverables") or []):
            owners.setdefault(str(d).strip(), []).append(str(t.get("id")))
    for path, ids in owners.items():
        if path and len(ids) > 1:
            issues.append(f"{path} を {len(ids)} ノードが作る（{', '.join(ids[:3])}）"
                          "——成果物 1 つの作り手は 1 ノードにすること")

    work = [t for t in tasks if isinstance(t, dict) and t.get("kind") in WORK_KINDS]
    if not work:
        return issues
    lo, hi = work_node_range(target)
    n = len(work)
    if n < lo or n > hi:
        issues.append(f"work系ノード数 {n} がレンジ [{lo},{hi}] 外（粒度 {target}）")
    for t in work:
        if not has_scope(str(t.get("goal", ""))):
            issues.append(f"{t.get('id')}: scope 欠落（[scope] またはパス/モジュール名が必要）")
    norms = [_normalize_goal(str(t.get("goal", ""))) for t in work]
    for i in range(len(norms)):
        if not norms[i]:
            continue
        for j in range(i + 1, len(norms)):
            if norms[i] == norms[j]:
                issues.append(
                    f"重複 goal: {work[i].get('id')} と {work[j].get('id')}")
    return issues


def phase3_build(request: str, analysis: dict, strategy: dict,
                 model: str | None, granularity_target: str, context: str = "",
                 tier: str = "", split_directive: str = "") -> list[dict]:
    """Phase 3: グラフ生成。ゲート不合格なら指示を強めて最大1回再生成。
    tier=basic では basic ワーカー向けの分解指示（tier_build_note）を差し込む。

    `split_directive`（分割の単位）は**呼び出し側が解決済みのテキスト**を渡す。
    tier の指示文（TIER_BUILD_NOTES）と違ってスキル側に複製を置かないのは、この文面の
    正典が手法カタログ（`split-policy-<policy>`）にあり、対象リポジトリの
    `.agents/methods/` による差し替えを効かせたいため——スキルが自前の文面を持つと
    差し替えがこの経路にだけ届かなくなる。"""
    subtasks = "\n".join(
        f"- {s}" for s in analysis.get("subtasks", [])
    )
    lo, hi = work_node_range(granularity_target)
    # Phase 1 の最小ステップ見積り。**レンジは上書きしない**（レンジは granularity_target が
    # 決めるという設計の約束を崩さない）。レンジ内のどこを狙うかの手掛かりとしてだけ渡す。
    steps = normalize_estimated_steps(analysis.get("estimated_steps"))
    steps_hint = (f"\nPhase 1 の最小ステップ見積り: {steps}"
                  "（上のレンジ内で目安にする。レンジより優先しない）" if steps else "")

    def _build(extra: str = "") -> list[dict]:
        prompt = BUILD_PROMPT.format(
            steps_hint=steps_hint,
            patterns=strategy.get("patterns", []),
            parallelism=strategy.get("parallelism", 3),
            reason=strategy.get("reason", ""),
            composite_template=strategy.get("composite_template"),
            review=strategy.get("review", False),
            enumeration_note=enumeration_note(analysis, "build"),
            tier_note=tier_build_note(tier),
            split_note=(f"{split_directive}\n\n" if split_directive else ""),
            granularity_target=granularity_target,
            work_lo=lo,
            work_hi=hi,
            subtasks=subtasks or "(Phase 1 で特定されず)",
            request=request,
        )
        # 安定部（context）→ 可変部（extra の再生成指示・本体）の順（案 H）。
        if extra:
            prompt = extra + "\n\n" + prompt
        if context:
            prompt = f"{context}\n\n{prompt}"
        raw = run_agent(prompt, model)
        tasks = extract_json(raw)
        # 契約は {"tasks": [...]}。裸の配列も受ける（配列で返す CLI の過去出力と互換）。
        # オブジェクトで縛るのは、ollama の JSON モード（--format json）が配列を返せないため
        # ——配列契約のままだと agent-ollama 経路の Phase 3 は構造的に必ず落ちる
        # （planner_eval 2026-08-23 で発見。JSON モードの配列不能は eval README 参照）。
        if isinstance(tasks, dict) and "tasks" in tasks:
            tasks = tasks["tasks"]
        if not isinstance(tasks, list):
            raise ValueError("Phase 3: tasks is not a list")
        return tasks

    # 列挙駆動を強制したときは split の存在も決定的に見る（強制の実効性はここで担保される）
    need_split = (analysis.get("enumeration_decision") or {}).get("mode") == "force"
    tasks = _build()
    issues = gate_tasks(tasks, granularity_target, require_split=need_split)
    if issues:
        retry_note = (
            "直前のグラフは粒度ゲート不合格。次を必ず守って作り直すこと:\n- "
            + "\n- ".join(issues)
        )
        tasks = _build(retry_note)
        # 再生成後も不合格ならそのまま返す（呼び出し側で使える最小成果を落とさない）
    return tasks


def normalize_tasks(tasks: list) -> list[dict]:
    """agent-flow 互換に正規化。"""
    valid_kinds = {"work", "generate", "classify", "synthesize", "verify",
                   "filter", "judge", "reduce", "split", "map"}
    seen_ids = set()
    normalized = []
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or f"t{i+1}")
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        kind = str(t.get("kind", "work"))
        if kind not in valid_kinds:
            kind = "work"
        node = {
            "id": tid,
            "goal": str(t.get("goal", "")),
            "deps": [str(d) for d in (t.get("deps") or [])],
            "kind": kind,
        }
        reads = []
        for r in t.get("read_allocation") if isinstance(t.get("read_allocation"), list) else []:
            if isinstance(r, dict) and str(r.get("path") or "").strip():
                row = {k: str(r[k]).strip() for k in ("path", "range", "reason")
                       if str(r.get(k) or "").strip()}
                symbols = r.get("symbols")
                if isinstance(symbols, str):
                    symbols = [symbols]
                if isinstance(symbols, list):
                    clean = list(dict.fromkeys(str(s).strip() for s in symbols if str(s).strip()))
                    if clean:
                        row["symbols"] = clean[:16]
                if r.get("slice") is True:
                    row["slice"] = True
                reads.append(row)
        if reads:
            node["read_allocation"] = reads[:32]
        if t.get("dependency_input") == "full":
            node["dependency_input"] = "full"
        # 処理契約（operation）と判定契約（decision）はそのまま運ぶ。形の検査は
        # agent-flow 側の 1 実装（agentcore.nodecontract）が持つ——ここで写して
        # 検査すると、契約が変わった日にスキルだけ古い規則で落とすようになる。
        for key in ("operation", "decision"):
            if isinstance(t.get(key), dict) and t[key]:
                node[key] = t[key]
        # filter の tie_break は使われない（順位基準は judge のためのもの）。器が崩れていると
        # engine は decision を丸ごと剥がすので、**使われない宣言のために判定契約ごと失う**。
        # ゲートで 1 度は書き直させたうえで、それでも残るこの 1 語だけは落として運ぶ
        # （実測 2026-08-30: e4b は作り直しても filter に tie_break を書き続けた）。
        if node.get("kind") == "filter" and isinstance(node.get("decision"), dict) \
                and "tie_break" in node["decision"]:
            node["decision"] = {k: v for k, v in node["decision"].items() if k != "tie_break"}
            print(f"[flow-planner] {tid}: filter の tie_break を落としました"
                  "（順位基準は judge のもの。decision 本体はそのまま運ぶ）", file=sys.stderr)
        normalized.append(node)
    if not normalized:
        raise ValueError("No valid tasks generated")
    return normalized


def resolve_enumeration(analysis: dict, probe_root: str = ".") -> dict:
    """Phase 1 の分析から列挙駆動の判定を確定し、analysis へ書き戻して decision を返す。

    probe（決定的走査）は 3 条件を満たしたときだけ走らせる——満たさないなら判定に使わない
    数字を取りに行くだけで、無駄にディスクを舐めることになる。
    """
    enumerable = normalize_enumerable(analysis.get("enumerable"))
    probed = None
    if enumerable["is_enumerable"]:
        probed = probe_target_count(
            " ".join(x for x in (enumerable["how_to_enumerate"],
                                 enumerable["target_kind"]) if x), probe_root)
    decision = enumeration_decision(enumerable, probed)
    analysis["enumerable"] = {**enumerable, "probed_count": probed}
    analysis["enumeration_decision"] = decision
    return decision


def plan(request: str, model: str | None = None, review="auto",
         granularity: str = "auto", probe_root: str = ".",
         context: str = "", tier: str = "",
         split_directive: str = "") -> tuple[dict, list[dict]]:
    """3段パイプラインを実行し (strategy, tasks) を返す。

    `context`（案 H・オプトイン）: agent-flow が run の meta へ固定したプロジェクト文脈
    （charter/rules.md/リポジトリ理解）スナップショット。stable_prefix 有効時、agent-project
    はこれらを request 本体から外して渡すため、分解の質を落とさないよう Phase 1（分析）と
    Phase 3（グラフ生成）—— request を直接プロンプトへ埋め込む段——にだけ前置する
    （Phase 2 は Phase 1 の構造化出力だけを使い request を埋め込まないため対象外）。"""
    catalog = load_catalog()
    if catalog is None:
        raise FileNotFoundError("patterns-catalog.yaml not found")

    analysis = phase1_analyze(request, model, context)
    target = resolve_granularity(granularity, analysis.get("complexity"), tier)
    analysis["granularity_target"] = target
    decision = resolve_enumeration(analysis, probe_root)

    strategy = phase2_select(request, analysis, catalog, model, review, tier)
    tasks = phase3_build(request, analysis, strategy, model, target, context, tier,
                         split_directive)
    normalized = normalize_tasks(tasks)

    final_strategy = {
        "patterns": strategy.get("patterns", ["fan-out-and-synthesize"]),
        "parallelism": int(strategy.get("parallelism", 3)),
        "review": bool(strategy.get("review", False)),
        "reason": str(strategy.get("reason", "")),
        "granularity": target,
        # Phase 1 の見積り（設計「Phase 1 拡張」）。agent-flow 側はログ・診断で使う。
        "estimated_steps": analysis.get("estimated_steps"),
        # 列挙駆動の発動根拠（force/boost/off）。誤爆に気づくための観測点なので必ず載せる。
        "enumeration": decision,
    }
    if tier:
        final_strategy["tier"] = str(tier)

    return final_strategy, normalized


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="flow-planner: 3段パイプラインでタスクグラフを生成")
    parser.add_argument("request", help="要求テキスト")
    parser.add_argument("--agent-cli", dest="agent_cli", default="kiro",
                        help="計画に使うエージェント CLI（既定 kiro）。agents/<name>.json の定義名を"
                             "受け付ける（組み込み 4 種に限らない）。agent-flow から呼ばれるときは"
                             " planner に設定された CLI が渡る")
    parser.add_argument("--model", default=None, help="エージェント CLI に渡すモデル")
    parser.add_argument("--review", default="auto",
                        help="検証gate: auto/true/false")
    parser.add_argument("--granularity", default="auto",
                        choices=["auto", "coarse", "fine", "finest"],
                        help="分解の細かさ: auto(complexity導出・既定)/coarse/fine/finest(明示優先)")
    parser.add_argument("--probe-root", dest="probe_root", default=".",
                        help="列挙 probe（LLM 無しの決定的走査で対象件数を数える）の起点ディレクトリ"
                             "（既定 cwd）。対象が見つからなければ件数は不明として扱う")
    parser.add_argument("--context", default="",
                        help="プロジェクト文脈（案 H・オプトイン）。agent-flow が run の meta から"
                             "渡す charter/rules.md/リポジトリ理解のスナップショット。"
                             "Phase 1 / Phase 3 のプロンプト先頭へ前置する")
    parser.add_argument("--split-directive", dest="split_directive", default="",
                        help="分割の単位（どこで切るか）の指示文。**解決済みのテキスト**を"
                             "agent-flow が渡す（値名ではない）——文面の正典は手法カタログの"
                             " split-policy-<policy> で、対象リポジトリの .agents/methods/ に"
                             "同 id を置いた差し替えもこの経路へ届く。空なら従来どおり")
    parser.add_argument("--tier", default="",
                        help="実行ティア（agent-control の workloads.flow.tier。agent-flow が渡す）。"
                             "basic なら auto 粒度を finest へ倒し、Phase 3 へ basic 向けの分解指示を"
                             "足し、review=auto を有効へ倒す。空なら従来どおり")
    args = parser.parse_args()

    global AGENT_CLI
    AGENT_CLI = args.agent_cli

    review = args.review
    if review == "true":
        review = True
    elif review == "false":
        review = False

    try:
        strategy, tasks = plan(args.request, args.model, review, args.granularity,
                               args.probe_root, args.context, args.tier,
                               args.split_directive)
        result = {"strategy": strategy, "tasks": tasks}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
