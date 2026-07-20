# チーム設計ヒューリスティクス

SKILL.md のプロセスを補う実践的な目安と、規模別のロール構成例。

## 規模の目安

| ゴールの規模 | ロール数の目安 | 典型構成 |
|------------|:---:|---------|
| 小（1 成果物・単純） | 1〜2 | worker（+ reviewer） |
| 中（設計 → 実装 → 検証） | 3〜4 | architect / impl / reviewer（+ integrator は自動） |
| 大（複数サブシステム） | 4〜6 | architect / impl-A / impl-B / reviewer / doc |

**6 を超えるなら分割を疑う**。ミッション自体を分けるか、サブチームに落とす方が収束が速い。
人数の増加は質問往復（メッセージ）の増加に直結し、予算を食う。

## ロール分割の判断

分けるべきとき:

- 成果物が**別の専門性**を要求する（設計 vs 実装 vs レビューは通常分ける）
- 成果物が**並行して**進められ、依存が薄い（impl-api と impl-web など）
- **承認の独立性**が要る（作った本人がレビューしない: reviewer を別ロールに）

束ねるべきとき:

- 成果物が同じ判断・同じコンテキストを共有する（設計と設計文書は 1 ロールでよい）
- 片方だけでは意味をなさない（実装とその単体テストは同一ロールが自然）
- 分けると質問往復が増えるだけで並行性が生まれない

## `mission` 文（プロンプト）のテンプレート

```
<何を作るか>を<何を根拠に>作る。<主な会話相手>と<何について>やり取りする。
<完了条件>を満たしたら declare_done する。判断に迷う<種類>は owner へ decision-request を上げる。
```

例（実装ロール）:

```
architecture.md に従い REST API を実装し、単体テストを通す。設計の疑問は architect へ
question を送り、実装済み範囲は reviewer に review を依頼する。API がひととおり動き
テストが緑で、未回答の質問が無くなったら declare_done する。DB スキーマの選択のように
後戻りの大きい判断は owner へ decision-request を上げる。
```

## `requires.tags` の付け方

- ノードの能力（`join --tags` / 設定 `tags`）と突き合わせる条件。
- 入力 `capabilities` に無いタグは要求しない（未充足で staffing が止まる）。
- 汎用ロール（architect / reviewer など、どのノードでも担えるもの）には付けない。
- 具体的な実行環境が要るロール（`python` / `frontend` / `gpu` …）にだけ付ける。

## approver と done_when

- `convergence.done_when: reviewer-approved` を選ぶなら、`approver: true` のロールを 1 つ以上置く。
- 逆に、承認ゲートが不要（成果物が揃えば完了でよい）なら `done_when` を省略する
  （既定 `all-required-done`）＝ reviewer を必須にしなくてよい。
- 承認者は原則「作らない人」にする（作った本人の自己承認は避ける）。

## 予算の見積もり

- `execution_minutes` は**全 amigo の agent CLI 実実行時間の合計**。壁時計ではない。
- 迷ったら省略（0 = 無制限）か、控えめな値を置いて足りなければ `budget add` で追加する。
- `on_exhausted: wrap-up`（既定）なら枯渇時は現状を partial 統合して納品する（fail より安全）。

## よくある失敗

- **必須の付けすぎ**: あれば嬉しい程度のロールまで `required: true` にして staffing を詰まらせる。
- **責務の重複**: 2 ロールが同じ成果物を書き、統合で衝突する。
- **存在しないタグ要求**: `capabilities` に無いタグを `requires.tags` に書き、永久に未充足。
- **承認者の欠落**: `reviewer-approved` を選んだのに `approver` ロールが無く、収束しない。
- **プロンプトが手順書化**: `mission` に細かな手順を全列挙し、かえって自律を殺す。
