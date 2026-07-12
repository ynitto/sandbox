#!/usr/bin/env python3
"""flow-planner — kiro-flow 向け高精度タスク分解・戦略選択。

3段階パイプラインで要求を分析→戦略選定→グラフ生成する。
kiro-flow の --planner flow-planner で呼び出される。

Usage:
    python3 plan.py "<要求>" [--model <model>] [--review auto|true|false]
                    [--granularity coarse|fine|finest]
    → JSON を stdout に出力: {"strategy": {...}, "tasks": [...]}
    granularity は分解の細かさ（coarse=現状/fine=1段細/finest=2段細）。kiro-flow は finest を渡す。
"""
from __future__ import annotations

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
7. **constraints**: 制約条件（順序依存、リソース制限等）
8. **domain_hints**: ドメインのヒント（コード変更、リサーチ、データ処理等）

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
  "constraints": ["..."],
  "domain_hints": ["..."]
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

## グラフ設計ルール

1. 各ノードには kind を付ける: work/generate/classify/synthesize/verify/filter/judge/reduce/split/map
2. 並列にできるタスクは deps を空に
3. 統合・検証ノードは先行タスクに依存させる
4. map-reduce では split ノードを1つだけ置く（map/reduce は実行時に動的展開される）
5. review=true の場合、統合（synthesize/reduce）の前に verify gate を1つ挟む
6. 依存は既存タスク id のみ、循環は作らない
7. id は短く（t1, t2, ... / classify, filter, synth, gate 等）

## サブタスク（Phase 1 で特定済み）

{subtasks}

## 出力

JSON 配列のみ:
```json
[
  {{"id": "t1", "goal": "具体的な目標", "deps": [], "kind": "work"}},
  ...
]
```

goal は要求に対して具体的に書くこと（「サブタスク1」のような抽象的記述は不可）。

## 元の要求

{request}"""


# --------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------
import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# 呼び出すエージェント CLI（kiro / claude / copilot / codex）。--agent-cli で切り替える。
# 既定は kiro（従来動作）。呼び出し側（kiro-flow）は planner に設定された agent_cli を渡す。
AGENT_CLI = "kiro"


def _agent_cmd(cli: str, model: str | None, prompt: str):
    """エージェント CLI 1 回分の (argv, stdin テキスト, 最終応答ファイル) を組み立てる。
    kiro-flow / kiro-project の _agent_cmd と同じ規約に揃える（ヘッドレス・応答本文のみ）。
    最終応答ファイルは codex のみ（stdout がイベントログのため）。"""
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
    cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"]
    if model:
        cmd += ["--model", model]
    return cmd + [prompt], None, None


def run_kiro(prompt: str, model: str | None) -> str:
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
def phase1_analyze(request: str, model: str | None) -> dict:
    """Phase 1: 要求分析。"""
    prompt = ANALYZE_PROMPT.format(request=request)
    raw = run_kiro(prompt, model)
    analysis = extract_json(raw)
    if not isinstance(analysis, dict):
        raise ValueError("Phase 1: analysis is not a dict")
    return analysis


def phase2_select(request: str, analysis: dict, catalog: dict,
                  model: str | None, review="auto") -> dict:
    """Phase 2: 戦略選定。"""
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
        analysis=json.dumps(analysis, ensure_ascii=False, indent=2),
    )
    raw = run_kiro(prompt, model)
    strategy = extract_json(raw)
    if not isinstance(strategy, dict):
        raise ValueError("Phase 2: strategy is not a dict")

    # review の確定
    if review == "auto":
        # 集約パターンがあれば auto で有効化
        pats = strategy.get("patterns", [])
        has_aggregation = any(p in ("fan-out-and-synthesize", "map-reduce") for p in pats)
        strategy["review"] = has_aggregation
    elif isinstance(review, bool):
        strategy["review"] = review

    return strategy


GRANULARITY_FACTORS = {"coarse": 1, "fine": 2, "finest": 3}


def granularity_directive(level: str) -> str:
    """分解の細かさ指示。coarse は空（現状どおり）。fine/finest で原子的な細分化を促す。"""
    f = GRANULARITY_FACTORS.get((level or "coarse").lower(), 1)
    if f <= 1:
        return ""
    unit = "1ファイル/1関数/1観点" if f >= 3 else "意味のある最小単位"
    return (f"分解の粒度: 通常より細かく、各タスクを{unit}まで原子的に分解すること。"
            f"目安は通常の約{f}倍の数の小さなタスク（ただし無意味な細分化・重複は避け、"
            "各タスクは独立に検証可能に保つこと）。")


def phase3_build(request: str, analysis: dict, strategy: dict,
                 model: str | None, granularity: str = "coarse") -> list[dict]:
    """Phase 3: グラフ生成。"""
    subtasks = "\n".join(
        f"- {s}" for s in analysis.get("subtasks", [])
    )
    prompt = BUILD_PROMPT.format(
        patterns=strategy.get("patterns", []),
        parallelism=strategy.get("parallelism", 3),
        reason=strategy.get("reason", ""),
        composite_template=strategy.get("composite_template"),
        review=strategy.get("review", False),
        subtasks=subtasks or "(Phase 1 で特定されず)",
        request=request,
    )
    note = granularity_directive(granularity)
    if note:
        prompt = note + "\n\n" + prompt
    raw = run_kiro(prompt, model)
    tasks = extract_json(raw)
    if not isinstance(tasks, list):
        if isinstance(tasks, dict) and "tasks" in tasks:
            tasks = tasks["tasks"]
        else:
            raise ValueError("Phase 3: tasks is not a list")
    return tasks


def plan(request: str, model: str | None = None, review="auto",
         granularity: str = "coarse") -> tuple[dict, list[dict]]:
    """3段パイプラインを実行し (strategy, tasks) を返す。"""
    catalog = load_catalog()
    if catalog is None:
        raise FileNotFoundError("patterns-catalog.yaml not found")

    # Phase 1
    analysis = phase1_analyze(request, model)

    # Phase 2
    strategy = phase2_select(request, analysis, catalog, model, review)

    # Phase 3（granularity で分解の細かさを指示）
    tasks = phase3_build(request, analysis, strategy, model, granularity)

    # 正規化（kiro-flow 互換）
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
        normalized.append({
            "id": tid,
            "goal": str(t.get("goal", "")),
            "deps": [str(d) for d in (t.get("deps") or [])],
            "kind": kind,
        })

    if not normalized:
        raise ValueError("No valid tasks generated")

    # strategy を kiro-flow 互換形式に整形
    final_strategy = {
        "patterns": strategy.get("patterns", ["fan-out-and-synthesize"]),
        "parallelism": int(strategy.get("parallelism", 3)),
        "review": bool(strategy.get("review", False)),
        "reason": str(strategy.get("reason", "")),
    }

    return final_strategy, normalized


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="flow-planner: 3段パイプラインでタスクグラフを生成")
    parser.add_argument("request", help="要求テキスト")
    parser.add_argument("--agent-cli", dest="agent_cli", default="kiro",
                        choices=["kiro", "claude", "copilot", "codex"],
                        help="計画に使うエージェント CLI（既定 kiro）。"
                             "kiro-flow から呼ばれるときは planner に設定された CLI が渡る")
    parser.add_argument("--model", default=None, help="エージェント CLI に渡すモデル")
    parser.add_argument("--review", default="auto",
                        help="検証gate: auto/true/false")
    parser.add_argument("--granularity", default="coarse",
                        choices=["coarse", "fine", "finest"],
                        help="分解の細かさ: coarse(現状)/fine(1段細)/finest(2段細)")
    args = parser.parse_args()

    global AGENT_CLI
    AGENT_CLI = args.agent_cli

    review = args.review
    if review == "true":
        review = True
    elif review == "false":
        review = False

    try:
        strategy, tasks = plan(args.request, args.model, review, args.granularity)
        result = {"strategy": strategy, "tasks": tasks}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
