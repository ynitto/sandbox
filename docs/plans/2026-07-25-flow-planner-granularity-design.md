# flow-planner タスク粒度制御 — 設計

## 背景

agent-flow の `--planner flow-planner` は戦略選定・タスクグラフ構造は概ね満足できる一方、
**ノード粒度にばらつき**があり、大雑把すぎる／細かすぎる計画が混在する。
自律開発（agent-project 外側 backlog × agent-flow 内側 DAG）で安定利用するには、
内側の粒度を操作定義し、複雑度に応じて目標レンジへ収束させる必要がある。

## 目的

- 内側（flow-planner）の work 系ノード粒度を **スコープ上限** で安定させる
- 外側（agent-project backlog の INVEST / verify）は **変更しない**
- Phase 2 戦略選定・パターンカタログの強みは維持する

## 非目標

- agent-project plan / 外側への `inner_budget` 追加
- 内側ノードへの機械 `verify` 必須化
- 分解批評 Phase 3.5（別 LLM）の導入（将来フックのみ想定）
- 失敗時自動細分化（ADaPT 型）の本実装（将来拡張）

## 二層モデル（役割分担）

```
外側 agent-project backlog     … INVEST + verify（現状維持・本設計の改修対象外）
        ↓ 各タスクを agent-flow run へ
内側 flow-planner DAG          … スコープ上限で原子化（本設計の改修対象）
```

同じ設定キー名 `granularity`（coarse / fine / finest）を共有するが、
**意味と既定値は層ごとに異なる**（外側既定 coarse、内側は complexity から導出）。
本改修で語彙を無理に統一しない。

## 採用アプローチ

**案 A: 複雑度連動＋スコープ契約＋決定的ゲート**

| 案 | 概要 | 判定 |
|----|------|------|
| A | Phase1 で目標粒度、Phase3 で scope 契約、決定的ゲートで拒否・再生成 | **採用** |
| B | 分解批評 Phase 3.5（別 LLM） | 却下（コスト・揺れ。A の後に載せられる） |
| C | 初回粗め＋失敗時細分化 | 却下（初手失敗増。continuation 拡張として将来） |

## 内側ノードの操作定義

成果を生むノード（`work` / `generate` / `map` 等）は次を満たす:

1. **1 モジュール相当**（または明示された単一結合点）
2. **想定変更 ≤ 約 30 行**（プロンプト上の見積り。実行時の厳密計測はしない）
3. goal に **触ってよい範囲（scope）** と **やらないこと（out_of_scope）** を含める

対象外: `verify` / `synthesize` / `reduce` / `filter` / `judge` / `classify` / `split`
（統合・検証・ルーティング役。スコープ上限ゲートの対象外）

### goal の記述形式（executor 互換優先）

スキーマに新フィールドを増やさず、goal 先頭の短い構造化ブロックとする:

```
[scope] path/or/symbol
[out_of_scope] やらないこと
本文の具体的目標
```

flow-worker の「範囲を守る」と自然接続する。将来フィールド化する余地は残すが本設計ではやらない。

## Phase 1 拡張

既存出力に以下を追加する:

```json
{
  "estimated_steps": 6,
  "granularity_target": "fine"
}
```

- `estimated_steps`: 最小必要ステップの整数見積り（アナリスト LLM）
- `granularity_target`: 原則 **complexity から決定的に導出**（LLM 出力があっても上書き可）

### complexity → granularity_target / work ノード目安

| complexity | granularity_target | work 系ノード数レンジ |
|------------|--------------------|----------------------|
| simple | coarse | 1–3 |
| moderate | fine | 3–8 |
| complex | finest | 6–12（ハード上限 16） |

CLI / 設定の `--granularity` は **明示指定時のみ優先**。
未指定時は上表の導出値を使う（現状の「常に finest」をやめる）。

Phase 1 の `subtasks`（3–8）は「分解軸の骨格」として残し、
最終 work ノード数は Phase 3 が `granularity_target` に合わせて増減する。

## Phase 2

変更なし（語彙ロック・Decision Matrix・複合テンプレートを維持）。

## Phase 3

1. 「通常の約 N 倍に細分化」という **相対指示を廃止**
2. **絶対レンジ＋スコープ契約**をプロンプトに埋め込む
3. 可能ならパターン別テンプレ骨格を先に置き、LLM は leaf の goal/scope 肉付けに限定
   （SKILL.md の「テンプレート駆動」宣言に実装を寄せる）

## 決定的ゲート（Phase 3 後・LLM なし）

生成結果を検査し、不合格なら指示を強めて **最大 1 回** 再生成する:

| 検査 | 条件 |
|------|------|
| ノード数 | work 系ノード数が target レンジ外 |
| scope | work 系 goal に scope 相当（`[scope]` またはパス/モジュール名のヒューリスティック）が無い |
| 重複 | 正規化後 goal の類似度が高すぎるペアがある |

**検査しないもの**: 機械 verify コマンドの有無（一次指標外）。

## 設定・互換

| 項目 | 方針 |
|------|------|
| `agent-flow` 既定 `granularity` | `auto`（complexity 導出）を推奨。既存 `finest` は明示細分化用に残す |
| フォールバック | flow-planner 失敗時の `plan_strategy_agent` は現状維持（本設計のゲートは flow-planner 経路のみ） |
| stub planner | テスト用。レンジ検査は任意（既存 stub グラフを壊さない） |

## 将来フック（本設計では実装しない）

- 分解批評 Phase 3.5（gitlab-idd 案C-Lite の分解レビュー関門に相当）
- 失敗時細分化（ADaPT / continuation 拡張）
- `--learnings` による粒度フィードバック（agent-flow design §18.3）
- goal 構造化ブロックの正式フィールド化

## 受け入れ条件

1. simple / moderate / complex の代表要求で、work 系ノード数が上表レンジに収まる
2. `--granularity finest` 明示時のみ強制細分化される（auto 時は complexity 導出）
3. 既存のパターン選定・フォールバック挙動を壊さない
4. 外側 agent-project の plan / verify 契約に変更が無い

## 実装計画（概略）

1. `plan.py`: complexity→target 導出、ANALYZE/BUILD プロンプト更新、相対指示削除
2. `plan.py`: 決定的ゲート＋1 回再生成
3. `patterns.py` / `config.py`: `granularity=auto` と明示優先の伝搬
4. SKILL.md: 粒度操作定義・二層役割・非目標を追記
5. テスト: レンジ収束・明示 finest・ゲート拒否のユニット／フィクスチャ

## Decision Record

| 項目 | 内容 |
|------|------|
| 決定日 | 2026-07-25 |
| 決定者 | ユーザー |
| 採用案 | 案 A: 複雑度連動＋スコープ契約＋決定的ゲート（内側のみ改修） |
| 却下案 | 案 B 分解批評 Phase 3.5（理由: LLM コストと揺れ。A の後付け可）／案 C 失敗時細分化（理由: 初手失敗増。将来 continuation 拡張）／外側 `inner_budget`（理由: 現状維持で十分）／内側 verify 必須（理由: 一次指標はスコープ上限） |
| 主な理由 | グラフ構造は満足済みで、ばらつきの主因は相対粒度指示と固定 `finest`。複雑度連動とスコープ契約で最小変更かつ自律開発の二層モデルに適合する |
| トレードオフ | verify 非必須のため偽完了は flow-worker／外側 verify に依存。scope はヒューリスティック検査のため抜けうる |
| 再評価条件 | work ノードの成功率や手戻りが改善しない／スコープ逸脱が多発する → 案 B または内側 verify 契約を再検討 |
