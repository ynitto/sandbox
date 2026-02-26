---
name: domain-model-diagram
description: ドメインモデルの構成要素（Entity・Value Object・Aggregate）を整理し Mermaid classDiagram として出力する。「ドメインモデル図を作って」「Mermaid でクラス図を作って」「エンティティの関係を図にして」「モデルを可視化して」「クラス図を描いて」などで発動。DDD・非DDD 両対応、図の生成に特化。
---

# domain-model-diagram

ドメインモデルの構成要素（Entity・Value Object・Aggregate）とその関係を整理し、Mermaid `classDiagram` として出力する。DDD 採用の有無に関わらず使用できる。

## ワークフロー

### Step 1: コンテキストの把握

以下を確認する（不明な場合はユーザーに質問する）:

- ビジネスドメインの概要（ECサイト・予約システム・医療など）
- 主要なユースケース（3〜5個）
- DDD 採用有無（集約・境界コンテキストの厳密な適用が必要か）
- 既存ドキュメント（要件定義書など）があれば読み込んで活用する

### Step 2: ユビキタス言語の収集

ユーザーの説明からドメイン語彙を抽出する:

| 抽出元 | 候補の種類 |
|--------|-----------|
| 名詞 | Entity / Value Object / Aggregate |
| 動詞・出来事 | Domain Event / Domain Service |
| 「〜は〜でなければならない」 | 不変条件（Invariant） |

**原則**: ドメインエキスパートが使う言葉をそのまま使う。技術用語（`UserRecord`、`OrderDTO` など）に翻訳しない。

### Step 3: 構成要素の分類

各候補を以下の基準で分類し、表形式でユーザーに確認を取る:

| クラス名 | 分類 | 理由 |
|---------|------|------|
| Order | Aggregate Root | 注文ライフサイクル全体を管理、外部から参照される |
| OrderLine | Entity | 注文内で LineId で識別される |
| Money | Value Object | 金額+通貨の組み合わせで定義、不変 |
| Address | Value Object | 配送先は属性値で識別、交換可能 |

分類の詳細基準 → [references/ddd-patterns.md](references/ddd-patterns.md)

### Step 4: 関係性の整理

各クラス間の関係を決める際に確認すること:

1. **ライフサイクルは共有されるか**（コンポジション `*--` vs 関連 `-->`）
2. **参照の方向**: 双方向は本当に必要か（単方向を優先する）
3. **多重度**: 1対1 / 1対多 / 多対多
4. **集約間の参照**: 集約境界を越える参照は ID のみ（オブジェクト直接参照不可）

### Step 5: Mermaid classDiagram の生成

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
  class OrderLine {
    <<Entity>>
    +OrderLineId id
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

  Order "1" *-- "1..*" OrderLine : contains
  OrderLine *-- Money : unitPrice
  Order *-- Money : totalAmount
  Order *-- OrderStatus : status
  Order ..> OrderPlaced : raises
```

### Step 6: レビューと調整

図をユーザーに提示し、以下を確認する:

- ドメインエキスパートの言葉と一致しているか
- 1トランザクションで変更される範囲が1集約に収まっているか
- 欠落しているエンティティ・関係はないか
- 双方向関連を単方向に簡素化できないか
- 集約が大きすぎないか（3〜7クラスが目安）

---

## DDD 詳細ガイド

Entity/VO 判定フロー・集約設計の落とし穴・Bounded Context・よくある失敗パターン:

[references/ddd-patterns.md](references/ddd-patterns.md)
