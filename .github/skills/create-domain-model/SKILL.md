---
name: create-domain-model
description: DDD に基づくドメインモデルを設計し Mermaid classDiagram として出力する。「ドメインモデルを設計して」「DDDで設計して」「集約を設計して」「境界コンテキストを整理して」「ドメインモデル図を作って」「クラス図を描いて」「Entityとバリューオブジェクトを整理して」「ユビキタス言語をまとめて」などで発動。DDD・非DDD 両対応。
---

# create-domain-model

DDD（Domain-Driven Design）に基づいてドメインモデルを設計し、Mermaid `classDiagram` として出力するスキル。

## 設計フロー

```
Step 1: ドメイン理解         ← ビジネスドメイン・ユースケース・ユビキタス言語
Step 2: 戦略的設計           ← Bounded Context・Context Map
Step 3: 戦術的設計           ← Entity / Value Object / Aggregate
Step 4: ドメインサービス特定  ← Entity/VOに収まらない操作
Step 5: ドメインイベント設計  ← 集約間通信・副作用
Step 6: 図として表現         ← Mermaid classDiagram
```

---

### Step 1: ドメイン理解

以下を確認する（不明な場合はユーザーに質問する）:

- ビジネスドメインの概要（ECサイト・予約システム・医療など）
- 主要なユースケース（3〜5個）
- DDD 採用有無（集約・境界コンテキストの厳密な適用が必要か）
- 既存ドキュメント（要件定義書など）があれば読み込んで活用する

ユーザーの説明からドメイン語彙を抽出する:

| 抽出元 | 候補の種類 |
|--------|-----------|
| 名詞 | Entity / Value Object / Aggregate |
| 動詞・出来事 | Domain Event / Domain Service |
| 「〜は〜でなければならない」 | 不変条件（Invariant） |

**原則**: ドメインエキスパートが使う言葉をそのまま使う。技術用語（`UserRecord`、`OrderDTO` など）に翻訳しない。

### Step 2: 戦略的設計

Bounded Context と Context Map を設計する:

- 同じ言葉が異なる意味を持つ場所に Bounded Context の境界を引く
- Context Map でコンテキスト間の連携パターンを選択する（ACL・OHS・Customer-Supplier など）
- Core / Supporting / Generic サブドメインを分類し、投資優先度を判断する

詳細 → [references/bounded-context.md](references/bounded-context.md)

### Step 3: 戦術的設計

各候補を以下の基準で分類し、表形式でユーザーに確認を取る:

| クラス名 | 分類 | 理由 |
|---------|------|------|
| Order | Aggregate Root | 注文ライフサイクル全体を管理、外部から参照される |
| OrderItem | Entity | 注文内で ItemId で識別される |
| Money | Value Object | 金額+通貨の組み合わせで定義、不変 |
| Address | Value Object | 配送先は属性値で識別、交換可能 |

各クラス間の関係を決める際に確認すること:

1. **ライフサイクルは共有されるか**（コンポジション `*--` vs 関連 `-->`）
2. **参照の方向**: 双方向は本当に必要か（単方向を優先する）
3. **多重度**: 1対1 / 1対多 / 多対多
4. **集約間の参照**: 集約境界を越える参照は ID のみ（オブジェクト直接参照不可）

詳細 → [references/core-concepts.md](references/core-concepts.md) / [references/aggregate-design.md](references/aggregate-design.md) / [references/relationships.md](references/relationships.md)

### Step 4: ドメインサービス特定

以下の場合に Domain Service を設計する:

- 複数集約をまたぐドメインロジック
- 外部サービスとの協調
- ステートレスで Entity/VO に自然に属さない操作

### Step 5: ドメインイベント設計

以下の場合に Domain Event を設計する:

- 集約の状態変化を他集約・コンテキストに伝播する
- メール送信・在庫更新などの副作用を疎結合で実行する
- Eventual Consistency を実現する

詳細 → [references/domain-events.md](references/domain-events.md)

### Step 6: Mermaid classDiagram の生成

以下の規則に従って図を出力する。

#### ステレオタイプ

```
<<Aggregate Root>>  集約ルート
<<Entity>>          集約内エンティティ
<<Value Object>>    値オブジェクト
<<Domain Event>>    ドメインイベント
<<Domain Service>>  ドメインサービス
```

#### 関係記号

| 記号 | 種別 | 用途 |
|------|------|------|
| `A "1" *-- "1..*" B` | コンポジション | 集約内のエンティティ（ライフサイクル共有） |
| `A o-- B` | 集約 | 参照するが独立したライフサイクルを持つ |
| `A --> B` | 関連 | 方向付き参照（A が B を知っている） |
| `A ..> B` | 依存 | イベント発行・一時的な使用 |
| `A <\|-- B` | 継承 | B が A の is-a 関係 |

#### 図に含めるもの・含めないもの

含める:
- ビジネス的に意味のある属性（`status`、`totalAmount` など）
- ドメインロジックを表すメソッド（`place()`、`cancel()` など）
- 集約境界をコメントで明示（`%% ── Aggregate: Order ──`）

含めない:
- `createdAt` / `updatedAt` などの監査フィールド
- getter / setter
- インフラ依存の実装詳細（`@Column`、DB の型など）

#### 出力例（ECサイト：注文集約）

```
classDiagram
  %% ── Aggregate: Order ──
  class Order {
    <<Aggregate Root>>
    +OrderId id
    +CustomerId customerId
    +Money totalAmount
    +OrderStatus status
    +place() void
    +cancel() void
  }
  class OrderItem {
    <<Entity>>
    +OrderItemId id
    +ProductId productId
    +Quantity quantity
    +Money unitPrice
    +subtotal() Money
  }
  class Money {
    <<Value Object>>
    +Decimal amount
    +Currency currency
    +add(Money) Money
  }
  class OrderStatus {
    <<Value Object>>
    PLACED
    CONFIRMED
    SHIPPED
    CANCELLED
  }
  class OrderPlaced {
    <<Domain Event>>
    +OrderId orderId
    +DateTime occurredAt
  }

  Order "1" *-- "1..*" OrderItem : contains
  OrderItem *-- Money : unitPrice
  Order *-- Money : totalAmount
  Order *-- OrderStatus : status
  Order ..> OrderPlaced : raises
```

#### 図のレビュー

図をユーザーに提示し、以下を確認する:

- ドメインエキスパートの言葉と一致しているか
- 1トランザクションで変更される範囲が1集約に収まっているか
- 欠落しているエンティティ・関係はないか
- 双方向関連を単方向に簡素化できないか
- 集約が大きすぎないか（3〜7クラスが目安）

Mermaid 記法の詳細 → [references/mermaid-notation.md](references/mermaid-notation.md)

---

## 参照ドキュメント

- **全体の設計原則・判断基準**: [references/core-concepts.md](references/core-concepts.md)
- **Aggregate設計の詳細と失敗パターン**: [references/aggregate-design.md](references/aggregate-design.md)
- **Bounded Context・Context Map**: [references/bounded-context.md](references/bounded-context.md)
- **関係性の種類と使い分け**: [references/relationships.md](references/relationships.md)
- **Domain Events 設計ガイド**: [references/domain-events.md](references/domain-events.md)
- **Mermaid図の記法と表現方法**: [references/mermaid-notation.md](references/mermaid-notation.md)
- **DDD パターン総合ガイド**: [references/ddd-patterns.md](references/ddd-patterns.md)

---

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

---

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

---

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
