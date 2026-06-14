---
name: flow-planner
description: kiro-flow の orchestrator 向け高精度タスク分解・戦略選択スキル。要求を分析し、6パターン＋複合パターンから最適な戦略を選定し、実行可能なタスクグラフを生成する。decomposition スキルの分解能力を内包し、kiro-flow の `--planner flow-planner` で利用する。
metadata:
  version: 1.0.0
  tier: experimental
  category: planning
  tags:
    - kiro-flow
    - orchestration
    - task-decomposition
    - strategy-selection
    - dynamic-workflow
    - planning
---

# flow-planner — kiro-flow 向け高精度タスク分解・戦略選択

## 概要

kiro-flow の orchestrator がタスクグラフを生成する際に、3段階パイプラインで
高精度な分解と最適な戦略選択を行うスキル。

既存の `decomposition` スキルのタスク分解能力を内包しつつ、
kiro-flow の6パターン戦略に特化した計画を生成する。

## アーキテクチャ

```
要求 → [Phase 1: 要求分析] → [Phase 2: 戦略選定] → [Phase 3: グラフ生成] → タスクグラフ
              ↑                      ↑                      ↑
        (分解軸の特定)         (パターンDB+            (テンプレート駆動
         WBS的分析)            Decision Matrix)         + 制約検証)
```

単一LLM呼び出しでの一発生成（現行 `plan_strategy_kiro`）を、
制約付きの3フェーズに分割して各段の精度を向上させる。

## 利用方法

### kiro-flow CLI から

```bash
# flow-planner を計画役に指定
kiro-flow run "<要求>" --planner flow-planner

# 設定ファイルで既定に
# kiro-flow.yaml:
#   planner: flow-planner
```

### スクリプト直接呼び出し

```bash
# 全段パイプライン（kiro-flow が内部で呼ぶ）
python3 .github/skills/flow-planner/scripts/plan.py "<要求>" [--model <model>] [--review auto|true|false]
```

## 3段階パイプライン

### Phase 1: 要求分析（Request Analysis）

要求を構造化し、戦略選定に必要な属性を抽出する。
`decomposition` スキルの Step 1–2（コンポーネント特定・依存分析）を内包。

**出力**:
```json
{
  "intent": "要求の本質（1文要約）",
  "decomposition_axes": ["分割軸1", "分割軸2"],
  "subtasks": ["サブタスク1", "サブタスク2"],
  "data_flow": "static|dynamic|unknown",
  "quality_focus": "speed|accuracy|coverage|exploration",
  "complexity": "simple|moderate|complex",
  "constraints": ["制約1"],
  "domain_hints": ["ヒント1"]
}
```

- `data_flow`: 入力データが事前確定（static）か実行時に判明（dynamic）か
- `quality_focus`: 速度重視か精度重視か網羅性重視か探索重視か
- `decomposition_axes`: WBS的に分割する観点（機能別、フェーズ別、データ別等）

### Phase 2: 戦略選定（Strategy Selection）

Phase 1 の分析結果から最適なパターン（複合含む）を選ぶ。

**Decision Matrix**: 属性とパターンのスコアリングで候補を2-3に絞り、
LLMには「候補から最適を選べ」と制約付き選択をさせる。

**出力**:
```json
{
  "patterns": ["fan-out-and-synthesize", "adversarial-verification"],
  "parallelism": 4,
  "reason": "選定理由",
  "composite_template": "fanout-then-verify",
  "review": true
}
```

### Phase 3: グラフ生成（Graph Construction）

選定した戦略をタスクグラフに変換する。テンプレート駆動で構造を保証し、
LLMには各ノードの goal 具体化のみを依頼。

**出力**: kiro-flow 互換の `{strategy, tasks}` 形式。

## パターンカタログ

`patterns-catalog.yaml` に以下を定義:

- 各パターンの詳細な使用条件（when_to_use / when_not_to_use）
- 典型的な並列数レンジ
- 組み合わせ可能なパターン
- ユースケース別推奨パターン（複合テンプレート）

## ユースケース別推奨戦略

| ユースケース | パターン構成 |
|-------------|-------------|
| マイグレーション・リファクタリング | fan-out → adversarial-verification → loop-until-done |
| 深いリサーチ | fan-out（並列検索）→ adversarial-verification → synthesize |
| 大量アイテムのソート | tournament（ペアワイズ比較） |
| 根本原因の調査 | generate（仮説）→ panel of verifiers → loop |
| 大規模トリアージ | classify-and-act → fan-out（修正）→ synthesize |
| デザイン・命名の探索 | generate-and-filter → tournament with rubric |
| 軽量 Eval | fan-out（worktree実行）→ adversarial-verification → loop |

## decomposition スキルとの統合

本スキルは `decomposition` スキルの以下の能力を Phase 1 に統合している:

- **コードベース探索**（Step 1）: プロジェクト構造の把握
- **コンポーネント特定**（Step 2）: 依存関係・並列化の分析
- **不明点の整理**（Step 3）: 制約の洗い出し

違い:
- `decomposition`: 人間が実行する ToDo リストを生成（20-60分粒度）
- `flow-planner`: kiro-flow worker が実行するタスクグラフを生成（LLM実行粒度）

## 設定

kiro-flow の設定ファイル（`kiro-flow.yaml`）で planner を指定:

```yaml
planner: flow-planner   # flow-planner | kiro | stub
```

または CLI で `--planner flow-planner`。

## 注意事項

- kiro-cli が必要（LLM呼び出しに使用）
- 3段パイプラインのため、現行 `kiro` planner より LLM 呼び出し回数が多い（2-3回）
- フォールバック: いずれかの段で失敗した場合は現行 `plan_strategy_kiro` に倒す
