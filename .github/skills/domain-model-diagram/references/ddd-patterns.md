# DDD パターン詳細ガイド

## 目次

1. [Entity vs Value Object の判定](#1-entity-vs-value-object-の判定)
2. [Aggregate の設計原則](#2-aggregate-の設計原則)
3. [関係性の種類と選択基準](#3-関係性の種類と選択基準)
4. [Bounded Context の設計](#4-bounded-context-の設計)
5. [Domain Event の設計](#5-domain-event-の設計)
6. [Domain Service の識別](#6-domain-service-の識別)
7. [よくある失敗パターン](#7-よくある失敗パターン)

---

## 1. Entity vs Value Object の判定

### 判定フロー

```
「このオブジェクトはIDで区別が必要か？」
  → Yes → Entity または Aggregate Root
  → No  → Value Object

（Entityの場合）「外部から直接参照・操作される起点か？」
  → Yes → Aggregate Root
  → No  → Entity（集約内部に留まる）
```

### 判定基準の比較

| 観点 | Entity | Value Object |
|------|--------|--------------|
| 同一性 | IDで識別（`UserId`、`OrderId`） | 属性値の組み合わせで識別 |
| ライフサイクル | 生成・変更・削除のライフサイクルがある | 変更せず、新しいインスタンスに置き換える |
| 可変性 | 状態が変化する（`status: PLACED → CONFIRMED`） | イミュータブル（変更したら新しいVOを生成） |
| 等値判定 | IDが同じ → 同一オブジェクト | 全属性が同じ → 同一オブジェクト |
| コピー | 参照を共有（別々のオブジェクトとして扱う） | 自由にコピー可能 |

### 具体例

**Entity にするもの**:
- `User`（ユーザーID で識別、プロフィール変更あり）
- `Order`（注文ID で識別、ステータスが変化する）
- `Reservation`（予約ID で識別、キャンセル・変更あり）

**Value Object にするもの**:
- `Money`（金額＋通貨の組み合わせ、変更時は新しいインスタンス）
- `Address`（郵便番号＋住所、配送先変更は新しいAddressに置き換え）
- `EmailAddress`（バリデーション済みのメールアドレス文字列）
- `DateRange`（開始日と終了日のペア）
- `Quantity`（数量と単位）
- `PhoneNumber`（国コード＋番号）

**迷いやすいケース**:

| オブジェクト | 判定 | 理由 |
|------------|------|------|
| `OrderStatus` | Value Object | `PLACED`/`CONFIRMED` などの列挙、IDは不要 |
| `ProductPrice` | Value Object | 価格は「その時点の値」として扱う（変更したら新しい値） |
| `Customer`（注文ドメイン内） | Value Object | 注文ドメインでは CustomerID だけで十分、詳細は顧客コンテキストが管理 |
| `OrderLine` | Entity | 同一注文内で LineId により区別が必要（削除・変更の追跡） |

---

## 2. Aggregate の設計原則

### 原則1: 集約境界 ＝ トランザクション境界

集約は「1回のトランザクションで変更される範囲」を示す。集約をまたぐ変更が必要な場合は、Domain Event を使って結果整合性で対応する。

```
✅ 正しい設計
  Order を確定する → Order 内のすべての変更が 1TX

❌ 誤った設計
  Order を確定する → Order と Inventory を同一TX で変更
  （→ Order と Inventory は別集約のはず）
```

### 原則2: 集約間はID参照のみ

集約の外から他集約のオブジェクトを直接参照してはいけない。IDのみを保持し、必要なら Repository で取得する。

```
✅ 正しい設計
  class Order {
    +CustomerId customerId    ← IDで参照
    +ProductId productId      ← IDで参照
  }

❌ 誤った設計
  class Order {
    +Customer customer        ← オブジェクト直接参照（集約をまたいでいる）
  }
```

### 原則3: 集約は小さく保つ（Vernon の原則）

集約に含める Entity は最小限に。「本当にこのEntity は同じトランザクションで変更されるか？」と問いかける。

- 目安: 1集約に含まれる Entity は 3〜7 クラス
- 大きすぎる集約 → 楽観ロックの競合が増える・パフォーマンス悪化

```
❌ 肥大化した集約（よくある間違い）
  Order → OrderLine → Product → ProductVariant → Category → Brand

✅ 適切に分割した集約
  Aggregate: Order
    OrderLine（OrderId + ProductId の参照のみ）

  Aggregate: Product（Product, ProductVariant, Category）
```

### 原則4: Aggregate Root だけが外部に公開される

集約内の Entity は Aggregate Root 経由でのみ操作する。外部から直接 `OrderLine` を変更してはいけない。

```
✅ 正しい設計
  order.addLine(productId, quantity, unitPrice)
  order.removeLine(lineId)

❌ 誤った設計
  orderLine.setQuantity(3)   // Order を介さずに直接変更
```

### 原則5: 不変条件（Invariant）は集約が保護する

集約が管理すべき不変条件を明確にする。これが集約境界を決める根拠になる。

**例: Order 集約の不変条件**
- 注文金額 = OrderLine の小計の合計（常に整合性を保つ）
- ステータスが `SHIPPED` の注文はキャンセルできない
- `PLACED` 状態でのみ商品の追加・削除が可能

不変条件が別の集約の状態に依存する場合 → 集約境界の引き直しを検討する。

### 集約の例: 予約システム

```
Aggregate: Reservation（予約集約）
  不変条件:
    - チェックアウトはチェックイン翌日以降
    - 同一期間に同一部屋の重複予約不可
    - CONFIRMED 状態でのみキャンセル可
  内部 Entity: （なし、シンプルな集約）
  Value Object: DateRange, GuestCount, RoomType

Aggregate: Room（部屋集約）
  不変条件:
    - 部屋タイプに応じた最大定員
    - メンテナンス中は予約不可
```

---

## 3. 関係性の種類と選択基準

### コンポジション vs 集約 vs 関連

| 関係 | Mermaid | ライフサイクル | 使用場面 |
|------|---------|--------------|---------|
| コンポジション | `A "1" *-- "N" B` | 共有（AがなくなるとBも消える） | 集約内 Entity、集約内 VO |
| 集約 | `A o-- B` | 独立（Aがなくなっても B は残る） | 参照するが独立して存在できる |
| 関連 | `A --> B` | 独立 | 集約間の参照（IDで参照するときに図示） |
| 依存 | `A ..> B` | 一時的 | メソッド引数、イベント発行 |
| 継承 | `A <\|-- B` | 共有 | is-a 関係（多用は避ける） |

### 双方向関連を避けるべき理由

双方向関連はコードの複雑性を高め、どちらが正を持つか曖昧になる。

```
❌ 双方向関連
  Order --> Customer
  Customer --> Order[]

✅ 単方向（ドメインの自然な方向）
  Order --> CustomerId   （注文は顧客を知っている）
  （顧客の注文一覧は Repository のクエリで取得する）
```

単方向化できない場合の検討材料:
- 両方向に参照が必要な本当のビジネス理由があるか？
- Repository でクエリすれば解決しないか？

### 継承の注意点

ドメインモデルでの継承は慎重に使う。

**適切なケース**: ポリモーフィズムが本当に必要な場合（`Notification` → `EmailNotification`, `SMSNotification`）

**代替パターン（推奨）**:
- 継承の代わりに Value Object でタイプを表現する（`PaymentMethod` 型の Value Object）
- Strategy パターンで振る舞いを差し替える

---

## 4. Bounded Context の設計

### 境界の引き方

**同じ言葉が異なる意味を持つ場所で境界を引く**

例: 「Customer（顧客）」の意味がコンテキストによって異なる

| コンテキスト | Customer の意味 |
|------------|----------------|
| 注文ドメイン | 注文主（氏名・配送先） |
| 請求ドメイン | 請求先（法人番号・与信） |
| マーケティングドメイン | セグメント・行動履歴を持つ分析対象 |

これらを1つの `Customer` クラスに詰め込まない。各コンテキストで独自の `Customer` を定義する。

### コンテキスト間の連携パターン

| パターン | 説明 | 使用場面 |
|---------|------|---------|
| Shared Kernel | 共通モデルを共有 | 密結合のチームで小さいもの。変更時は両チーム合意が必要 |
| Customer-Supplier | 上流が API を提供し下流が消費 | チーム間に明確な依存関係がある |
| Anti-Corruption Layer (ACL) | 外部モデルを自コンテキストモデルに変換 | 外部システムや古いシステムから自コンテキストを守る |
| Open Host Service | 汎用プロトコル（REST、gRPC）で公開 | 多数の下流がいる |
| Conformist | 上流のモデルをそのまま使う | 上流への影響力がなく、翻訳コストが高い |

### Bounded Context の Mermaid 表現

```
%% ── Context: Order ──
class Order { ... }
class OrderLine { ... }

%% ── Context: Catalog ──
class ProductSummary {
  <<Value Object>>
  +ProductId id
  +string name
  +Money price
}
note for ProductSummary "Order コンテキストの読み取り専用ビュー\n（Catalog コンテキストから ACL で変換）"
```

---

## 5. Domain Event の設計

### いつ Domain Event を使うか

- **集約間の状態同期が必要なとき**（別集約の状態変化を通知）
- **副作用を集約の外に出したいとき**（メール送信、在庫減算など）
- **監査ログ・イベントソーシングが必要なとき**

### 命名規則

**過去形の動詞＋名詞**: ビジネス上の出来事を表す

```
✅ OrderPlaced（注文が確定された）
✅ PaymentReceived（支払いが受領された）
✅ ReservationCancelled（予約がキャンセルされた）
✅ StockDepleted（在庫が切れた）

❌ OrderCreate（現在形・動詞のみは避ける）
❌ UpdateOrder（命令形は避ける）
```

### Domain Event が持つべき情報

```
class OrderPlaced {
  <<Domain Event>>
  +OrderId orderId          // 集約ID（必須）
  +CustomerId customerId    // ハンドラが必要とする最小限の情報
  +Money totalAmount
  +DateTime occurredAt      // 発生時刻（必須）
}
```

**原則**: イベントが「何が起きたか」を表すのに必要な最小限の情報だけ持つ。ハンドラが追加情報を必要とするなら Repository で取得する。

---

## 6. Domain Service の識別

### Domain Service にするべき操作

以下のいずれかに当てはまる操作は Domain Service の候補:

1. **複数の集約をまたぐ操作**（特定のエンティティに属せない）
2. **外部サービスとの連携が必要**（決済ゲートウェイ、メール送信）
3. **ドメインロジックだが状態を持たない**

```
Domain Service の例:
  TransferService.transfer(from: AccountId, to: AccountId, amount: Money)
  ← Account A とAccount B の両方を操作するため、どちらかの Entity に属せない

  PricingService.calculate(order: Order, coupon: Coupon) → Money
  ← 複雑な割引計算ロジック。Order でも Coupon でもない
```

### Entity に置くべき操作

**操作が1つの集約内で完結する場合は Entity のメソッドにする**。

```
✅ order.cancel()          // Order 内で完結
✅ account.withdraw(Money) // Account の残高を変更するだけ

❌ OrderService.cancel(orderId)  // 1集約の操作を Service に出すのは過剰
```

---

## 7. よくある失敗パターン

### パターン1: 神集約（Fat Aggregate）

**症状**: 集約が多数のエンティティを抱え込み、楽観ロックの競合やパフォーマンス問題が発生する。

```
❌ 悪い例
  Order（Aggregate Root）
    → OrderLine[]
    → Customer（別集約のはず）
    → Product[]（別集約のはず）
    → Invoice
    → Shipment
    → Payment
```

**対処**: 「1TX で同時に変更されるか？」を基準に集約を分割し、集約間は Domain Event で連携する。

### パターン2: 貧血ドメインモデル（Anemic Domain Model）

**症状**: Entity が getter/setter だけで、ビジネスロジックがすべて Service 層にある。

```
❌ 悪い例
  class Order {
    +OrderStatus getStatus()
    +void setStatus(OrderStatus status)  // 不変条件を保護できない
  }
  class OrderService {
    void cancel(Order order) {
      if (order.getStatus() == PLACED) {
        order.setStatus(CANCELLED);  // 不変条件のチェックがServiceに漏れる
      }
    }
  }

✅ 良い例
  class Order {
    +cancel() void  // ドメインロジックをEntityが持つ
      → ステータスチェック・不変条件の保護・イベント発行を内部で行う
  }
```

### パターン3: DBスキーマのそのままモデル化

**症状**: テーブル設計をそのままクラスに起こすため、ドメインの意図が埋もれる。

```
❌ 悪い例（DBテーブル to クラス）
  class OrderRecord {
    +int order_id
    +int customer_id
    +decimal total_amount
    +string currency_code
    +int status_code
  }

✅ 良い例（ドメイン語彙で表現）
  class Order {
    +OrderId id
    +CustomerId customerId
    +Money totalAmount     ← Money = amount + currency をVO化
    +OrderStatus status    ← コードではなくEnum
    +place() void
    +cancel() void
  }
```

### パターン4: 双方向関連の乱用

**症状**: 全クラスが互いを参照し、変更の影響範囲が追跡不能になる。

**対処**:
- ドメインの自然なナビゲーション方向（例: Order → Customer は自然だが Customer → Order[] は Repository のクエリで取得）
- 双方向が必要なら本当にそのユースケースがあるか確認する

### パターン5: Value Object を作らず文字列・数値を使い続ける

**症状**: `amount: Decimal`、`currency: String`、`email: String` が散在し、バリデーションが重複する。

```
❌ 悪い例
  class Order {
    +Decimal amount
    +String currency     ← 同じ概念がバラバラに存在
  }

✅ 良い例
  class Order {
    +Money totalAmount   ← Value Object で意味を明確化、バリデーションを集約
  }
  class Money {
    <<Value Object>>
    +Decimal amount
    +Currency currency
    +add(Money) Money
  }
```

### パターン6: 技術的関心事の混入

**症状**: ドメインモデルに `@Table`、`@Column`、HTTP レスポンスフィールドなどのインフラ詳細が入り込む。

**対処**: ドメインモデルは純粋なビジネスロジックのみ。インフラ詳細は永続化層・プレゼンテーション層で担う。ドメインモデルはフレームワークに依存しない。

---

## チェックリスト

設計レビューで確認する:

**Entity / Value Object**
- [ ] 各クラスの同一性基準（IDか？属性値か？）が明確
- [ ] Value Object はイミュータブルか
- [ ] ドメインエキスパートの言葉を使っているか

**Aggregate**
- [ ] 集約の不変条件が明文化されているか
- [ ] 集約間の参照はIDのみか
- [ ] 1集約が1トランザクションで変更完了するか
- [ ] 集約が3〜7クラス程度に収まっているか
- [ ] Aggregate Root 経由でのみ内部を操作できるか

**関係性**
- [ ] 双方向関連を最小化しているか
- [ ] コンポジション / 集約 / 関連の使い分けが正しいか

**その他**
- [ ] getter/setter のみのクラス（貧血モデル）がないか
- [ ] DB の都合でモデルを歪めていないか
- [ ] 技術的関心事がドメインモデルに混入していないか
