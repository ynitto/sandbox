# モデル階層（strong / weak）ルーティング — 利用枠逼迫への opt-in 対策

> 作成 2026-08-04
> 対象: `tools/agent-tools/agentcore`（1 実装の置き場）/ `agent-project` / `agent-flow` / `agent-audit`
> 効く柱・原則: **柱1 / C1・C3・C7** — 個人の利用枠（クレジット）の消費を、判断の質を落とさずに
> 減らす。判断（purpose → 階層の分類）は 1 実装へ集約し、解決経路は増やさない。

---

## 1. 背景と課題

利用枠の制限が厳しくなり、agent-tools ファミリーの LLM 呼び出し全部を単一の（強い）モデルで
賄う運用が持続しなくなった。ファミリーの LLM 呼び出しには性格の差がある:

- **判断が重く、失敗が高くつく処理** — 計画（plan / planner）、敵対的レビュー、裁定
  （adjudicate）、検証ゲート（verify / judge）。ここでモデルをけちると差し戻しと再実行が
  増え、人の注意力（最も高価な資源）まで浪費する。
- **大量に走る・機械的・失敗が安全側に倒れる処理** — 採点（assess）、優先順位付け、
  ルーティング、分類（classify / filter / split）、監査の抽出（extract）。後段に閾値ゲート・
  決定論・人の目があり、弱いモデルの間違いが品質事故に直結しない。

この使い分けは既存の役割別上書き（設定 `agents:`。project / flow / audit / amigos すべてに
ある）で今日でも表現**できる**。できるのに使われないのは、そのために

1. 各エンジンの purpose 語彙（project: plan/review/…、flow: planner/`<kind>`…、audit:
   extract/…）を全部知り、
2. 「どの処理が強いモデルに値するか」を自分で判断し、
3. その結果を 3 つの設定ファイルへ手書きする

必要があるから。この判断はツール側の設計知識であって、ユーザーごとに再発明させるものでは
ない（C7: 同じ判断を 2 度実装しない、の設定版）。

## 2. 解き方 — `model_tiers:`（完全オプトイン）

ユーザーが宣言するのは **2 段のモデルだけ**:

```yaml
# agent-project.yaml / agent-flow.yaml / agent-audit.yaml（各エンジンの設定に同じキー）
model_tiers:
  strong: { model: opus }     # {agent_cli, model}（どちらか片方でも可）
  weak:   { model: haiku }
  # purposes:                 # 任意: 既定分類の上書き。値は strong / weak / off（適用除外）
  #   assess: strong
```

purpose → 階層の**既定分類**とその展開規則は `agentcore.modeltier` の 1 実装に置く。
各エンジンは設定解決の時点で `model_tiers:` を**既存の `agents:` マップへ展開してから**
従来どおりの解決に流す。つまり解決経路そのものは 1 本も増えない:

```
agent-control > CLI > agents:（明示） > model_tiers（既定分類の展開） > グローバル agent_cli/model
```

- `model_tiers:` が無ければ何もしない（**挙動不変**。既存ユーザーへの影響ゼロ）。
- 明示の `agents:` は purpose 単位で丸ごと勝つ（部分マージしない——「agent_cli だけ明示したら
  model は階層から来る」という遠隔作用を作らない）。
- node-budget の soft 縮退（agent-control の `degraded`）は従来どおり最上位に重なる。
  「予算が減ってきたら全部弱いモデルへ」は今までどおり degraded の仕事で、`model_tiers` は
  「平常時から処理の性格で使い分ける」層。役割が違うので統合しない。

## 3. 既定分類

| workload | strong（判断が重い） | weak（大量・機械的） | 触らない（成果物を直接作る） |
|---|---|---|---|
| project | plan / review / adjudicate / verify | prioritize / route / distill / assess / repo_map / doctor | —（act 本体は agent-flow 側） |
| flow | planner / evaluator / judge / verify | classify / filter / split | worker / work / generate / synthesize / map / reduce |
| audit | review | extract / distill | — |

分類の方針:

- **strong** — 間違いが再実行・差し戻し・人の介入を生む処理。C3（機械で決められることを人に
  聞かない）を支える判断はここで、弱いモデルにすると「人へ倒れる回数」が増えて本末転倒。
- **weak** — 間違っても後段（閾値ゲート・決定論・人レビュー・C5 の機械検証）が受け止める処理。
  ここが呼び出し回数の大半を占めるので、コスト削減の主戦場。
- **成果物を直接作る処理は既定で触らない** — 弱いモデルの成果物は verify で落ちて作り直しに
  なり、かえって高くつく（リトライも財布から出る——C1）。使いたい場合は `purposes:` での明示
  （例 `generate: weak`）だけに開く。

この表は各エンジンの語彙（`AGENT_PURPOSES` / `VALID_KINDS` 等）の**写し**なので、各エンジンの
テストが正典と突き合わせて縛る（C7: 写しは突き合わせテストで縛る）。

## 4. 対象外と、その理由

- **agent-amigos** — ロールはミッション作者が定義する自由語彙で、ツール側に既定分類を置けない。
  かつ `roles[].agent_cli / model` で作者が今日でも役割ごとに使い分けを表現できる。ミッション
  契約（mission.schema.json）へ `tier:` を足して「作者は性格だけ宣言し、モデルは実行ノードの
  財布が決める」形にするのは、契約変更を伴うため別件（このファイルの将来案）。
- **agent-loop** — ルーチンは purpose が 1 つ（ユーザーのプロンプト）で分類の対象がない。
  ルーチンごとのモデル指定（`kiro_opts.model`）で既に足りる。
- **料金表・自動コスト最適化は持たない** — 「どのモデルがいくらか」はプロバイダ都合で変わる
  ノード局所の知識で、ここに埋めると必ず腐る。宣言するのは強弱の 2 段だけ。実測は既存の
  node-budget 台帳と agent-audit（`--by model`）が担う。

## 5. テスト

- `agentcore/tests/test_modeltier.py` — 正規化（不正値は黙って落とす。既存 `agents:` と同じ
  流儀）・オプトイン不変（未設定 → 明示マップをそのまま返す）・明示優先・`purposes:` 上書きと
  `off`・語彙フィルタ。
- 各エンジンのテスト — (a) 既定分類のキーがそのエンジンの語彙に収まる（写しの縛り・C7）、
  (b) `model_tiers` を設定した config が実効 `agents:` に反映され、明示 `agents:` が勝つ。

## 6. §7 作業ゲート（コンセプト正典 §8）

- 柱1: 個人の利用枠の消費を、処理の性格に合わせて宣言的に抑える（C1: 予算は持ち主の宣言の内側）。
- C3: 判断が重い処理のモデルは落とさない既定にし、「人へ倒れる回数」を増やさない。
- C7: purpose → 階層の判断は agentcore の 1 実装。解決経路・状態の書き手は増やさない。
  写し（語彙の複製）は各エンジンのテストで正典と突き合わせる。
