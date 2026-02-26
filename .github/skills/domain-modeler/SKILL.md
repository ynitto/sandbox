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
- **Mermaid図の記法と表現方法**: [references/mermaid-notation.md](references/mermaid-notation.md)

## クイックリファレンス：判断フローチャート

### Entity vs Value Object

```
「この概念は追跡が必要か（ライフサイクルがあるか）？」
  YES → Entity（識別子を持つ）
  NO  → 「値として等価判定が自然か？」
          YES → Value Object
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

## よくある失敗パターン（必読）

1. **God Aggregate**: Order が Cart・Payment・Shipping・Inventory をすべて含む
   → 解決: ドメインイベントで集約間連携に分割

2. **貧血ドメインモデル**: ドメインオブジェクトが getter/setter のみ、ロジックはすべてサービス層
   → 解決: 不変条件の保護・状態遷移をエンティティ自身に移動

3. **DBスキーマ思考のドメインモデル**: テーブル設計をそのままクラスにしたモデル
   → 解決: ドメイン概念から設計し、Repositoryで永続化を分離

4. **Bounded Context 未設定のまま単一モデル**: "Product" が在庫・EC・物流で同じクラス
   → 解決: コンテキストごとに独立したモデルを定義

5. **双方向参照の多用**: Order ↔ Customer ↔ OrderItem が相互参照
   → 解決: 主たる方向を一方向に固定し、逆方向はクエリで取得

詳細な設計原則・具体例・Mermaid記法は上記参照ドキュメントを読み込む。
