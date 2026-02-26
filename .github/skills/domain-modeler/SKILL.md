---
name: domain-modeler
description: Domain-Driven Design (DDD) に基づくドメインモデル設計を支援するスキル。「ドメインモデルを設計して」「DDDで設計して」「集約を設計して」「境界コンテキストを整理して」「ドメインモデル図を作って」「Entityとバリューオブジェクトを整理して」「ユビキタス言語をまとめて」などのリクエストで発動する。
---

# Domain Modeler

DDD（Domain-Driven Design）に基づいてドメインモデルを設計・文書化するスキル。

## 設計フロー

```
Step 1: ドメイン理解         ← ユビキタス言語・コアドメイン特定
Step 2: 戦略的設計           ← Bounded Context・Context Map
Step 3: 戦術的設計           ← Entity / Value Object / Aggregate
Step 4: ドメインサービス特定  ← Entity/VOに収まらない操作
Step 5: ドメインイベント設計  ← 集約間通信・副作用
Step 6: 図として表現         ← Mermaid classDiagram
```

各ステップの詳細は以下の参照ドキュメントを読み込む。

## 参照ドキュメント

- **全体の設計原則・判断基準**: [references/core-concepts.md](references/core-concepts.md)
- **Aggregate設計の詳細と失敗パターン**: [references/aggregate-design.md](references/aggregate-design.md)
- **Bounded Context・Context Map**: [references/bounded-context.md](references/bounded-context.md)
- **関係性の種類と使い分け**: [references/relationships.md](references/relationships.md)
- **Domain Events 設計ガイド**: [references/domain-events.md](references/domain-events.md)
- **Mermaid図の記法と表現方法**: [references/mermaid-notation.md](references/mermaid-notation.md)

## クイックリファレンス：判断フローチャート

### Entity vs Value Object

```
「この概念は追跡が必要か（ライフサイクルがあるか）？」
  YES → Entity（識別子を持つ）
  NO  → 「値として等価判定が自然か？」
          YES → Value Object（イミュータブルにする）
          NO  → 再検討（ドメイン知識が足りない可能性）
```

### Aggregate 境界の決め方

```
「このオブジェクト群は、常に一貫した状態でなければならないか？」
  YES → 同じAggregate
  NO  → 別のAggregate（IDで参照する）

「整合性が必要なのはいつか？」
  即時（同一トランザクション）  → 同じAggregateを検討
  最終的整合性でよい            → 別Aggregateにして Domain Event で連携
```

### Domain Event を使うか判断する

```
「集約の状態変化を他の集約・コンテキストに伝える必要があるか？」
  YES → Domain Event を発行する

「副作用（メール・在庫更新・ログ）を集約から分離したいか？」
  YES → Domain Event で疎結合にする
```

### Bounded Context の境界

```
「同じ言葉が異なるチームで異なる意味を持っているか？」
  YES → 別の Bounded Context

「このチームの変更が別のチームの変更を強制するか？」
  YES → 境界が必要 → Context Map でパターンを選択
```

## よくある失敗パターン（必読）

1. **God Aggregate**: Order が Cart・Payment・Shipping・Inventory をすべて含む
   解決: ドメインイベントで集約間連携に分割（Vernon の原則2: 小さな集約）

2. **貧血ドメインモデル**: ドメインオブジェクトが getter/setter のみ、ロジックはすべてサービス層
   解決: 不変条件の保護・状態遷移をエンティティ自身に移動

3. **DBスキーマ思考のドメインモデル**: テーブル設計をそのままクラスにしたモデル
   解決: ドメイン概念から設計し、Repository で永続化を分離

4. **Bounded Context 未設定のまま単一モデル**: "Product" が在庫・EC・物流で同じクラス
   解決: コンテキストごとに独立したモデルを定義

5. **双方向参照の多用**: Order ↔ Customer ↔ OrderItem が相互参照
   解決: 主たる方向を一方向に固定し、逆方向はクエリで取得

6. **イミュータブルでない Value Object**: Money の amount を直接変更している
   解決: VO は新しいオブジェクトを返す（`money.add(other)` → 新しい `Money` を返す）

7. **集約間で直接オブジェクト参照**: `order.customer.email` のようなアクセス
   解決: 別集約への参照は ID のみ（`order.customerId`）

8. **技術的 ID をドメインイベントに含める**: DB のサロゲートキーをそのままイベントに
   解決: ドメインの識別子（`OrderId` 型等）を使う

詳細な設計原則・具体例・Mermaid 記法は上記参照ドキュメントを読み込む。

## 出力テンプレート

設計結果は以下の形式でまとめる:

```markdown
## ユビキタス言語

| 用語 | 定義 | 文脈 |
|------|------|------|
| 注文 | 顧客が確定した購入意思 | Order Context |

## Bounded Context

| Context | 責務 |
|---------|------|
| Order Management | 注文の作成から完了まで |

## Context Map

[Mermaid graph で BC 間の関係を表現]

## ドメインモデル図

[Mermaid classDiagram で集約・Entity・VO を表現]

## 設計判断の根拠

- なぜ X を Entity にしたか
- なぜ Y を VO にしたか
- なぜ Z を別集約にしたか
```
