---
name: flow-planner
description: kiro-flow の orchestrator 向け高精度タスク分解・戦略選択スキル。要求を分析し、7パターン（map-reduce 含む）＋複合パターンから最適な戦略を選定し、実行可能なタスクグラフを生成する。decomposition スキルの分解能力を内包し、kiro-flow の `--planner flow-planner` で利用する。
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
kiro-flow の7パターン戦略（記事の6パターン＋ kiro-flow 追加の map-reduce）に特化した計画を生成する。

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
- バリアント（基本パターンの実行モード。`variants`）

## バリアント（pilot-then-batch / 見本先行）

`variants.pilot-then-batch` は **map-reduce の実行モード**で、同様手順を多数の対象に
繰り返すとき「まず 1 件(pilot)を走らせて検証・レビューで指示を固め、その定義で残りを
生成・実行する」。全件を一斉に流して全滅する無駄を避ける。2 実装がある:

- **kiro-flow `exemplar_first`**（自動ゲート）: split→pilot map→verify ゲート→残り map→reduce。
  設定 `exemplar_first: true` か `--exemplar-first` で有効化。
- **kiro-autonomous `cohort`**（人ゲート）: pilot に `review:human`。人が approve(+feedback)
  で指示を固めてから残りを生成。`enqueue --cohort-items a,b,c` か charter プランナーが
  `{title, verify, cohort_items:[…]}` で自動生成。

**バリアントは `patterns` ではない**（`patterns` 配列には書かない）。基本パターン
（map-reduce）を選んだうえで、繰り返し量産・見本先行が要るときに上記フラグ/cohort で
有効化する選択肢。詳細な when_to_use / when_not_to_use / 例示 / 適用具体例は
`patterns-catalog.yaml` の `variants` を参照。

## ユースケース別推奨戦略

要求の「型」から複合テンプレート（`patterns-catalog.yaml` の `composites`）と
その正規パターン構成を引くための索引。**表に現れる語はすべて
`patterns-catalog.yaml` に実在する正規名のみ**で、Phase 2 はこの語彙の外に出ない。
トリガキーワードは `use_case_mapping` のキーワードと同じものを使うため、
人間が読む本表と Phase 2 の機械的マッチングは常に一致する。

| ユースケース | トリガキーワード（例） | 複合テンプレート | 正規パターン構成 |
|-------------|----------------------|----------------|----------------|
| マイグレーション・大規模リファクタリング | マイグレーション, 移行, リファクタリング, 一括変更 | `migration-pipeline` | fan-out-and-synthesize → adversarial-verification → loop-until-done |
| 根本原因の調査・デバッグ | 原因, 根本, なぜ, 障害, root cause, debug | `root-cause-analysis` | generate-and-filter → adversarial-verification → loop-until-done |
| 深いリサーチ・多観点調査 | リサーチ, 調査, 深く, research, investigate | `deep-research` | fan-out-and-synthesize → adversarial-verification |
| 多観点の並列レビュー（精度ゲート） | レビュー, 監査, 観点, セキュリティ, パフォーマンス, 可読性 | `fanout-then-verify` | fan-out-and-synthesize → adversarial-verification |
| 大規模トリアージ・振り分け | トリアージ, 振り分け, 分類, 仕分け, triage, classify | `classify-then-fanout` | classify-and-act → fan-out-and-synthesize |
| 大量アイテムの順位付け・ソート | ソート, 順位, ランキング, pairwise, sort, rank | `tournament-rank` | tournament（ペアワイズ比較。候補生成は伴わない） |
| デザイン・命名・案の探索 | デザイン, 命名, ネーミング, 案, design, naming | `generate-filter-tournament` | generate-and-filter → tournament |
| 軽量 Eval（実行+採点+改善） | eval, 評価, 採点, ベンチ, grade, benchmark | `lightweight-eval` | fan-out-and-synthesize → adversarial-verification → loop-until-done |
| 件数不定の一覧・コレクション処理 | それぞれ, 各, ごとに, 一覧, 件 | （単体パターン） | map-reduce |
| 完了条件付きの反復改善 | テスト通過, lint, 型チェック, 緑, 反復, until done | （単体パターン） | loop-until-done |

### 語彙ロック（決定がブレないための規約）

ユースケースとパターンの取り違えを防ぐため、Phase 2 は次の閉じた語彙だけを使う。
派生語・同義語の即興導入を禁じることで、戦略選定の再現性を保証する。

- **`patterns` に書ける名前は7つの基本パターンのみ**:
  `fan-out-and-synthesize` / `adversarial-verification` / `classify-and-act` /
  `generate-and-filter` / `tournament` / `loop-until-done` / `map-reduce`
- **`composite_template` は `composites` のキーか `null`**:
  `migration-pipeline` / `root-cause-analysis` / `deep-research` /
  `fanout-then-verify` / `classify-then-fanout` / `tournament-rank` /
  `generate-filter-tournament` / `lightweight-eval`
- **`synthesize` / `generate` / `verify` / `judge` / `filter` / `reduce` /
  `split` / `map` / `classify` / `work` はノード種別（`kind`）であって
  パターンではない**。`patterns` には書かない。
  旧版にあった "panel of verifiers"・"tournament with rubric"・"synthesize" 単体
  のような派生語は、対応する正規名（順に adversarial-verification・tournament・
  fan-out-and-synthesize）へ読み替える。

### 該当ユースケースが無いとき

表のどれにも当てはまらない要求は、**戦略を即興で作らず** Phase 2 の
Decision Matrix（`data_flow` / `quality_focus` / `complexity` のスコアリング）で
上位の基本パターンを 1–2 個組み合わせる。集約パターン
（fan-out-and-synthesize / map-reduce）を含む場合は、統合前に検証 gate
（adversarial-verification）を挟むかどうかを `review` で判断する。

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
