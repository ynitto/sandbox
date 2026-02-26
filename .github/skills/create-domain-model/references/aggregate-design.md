# Aggregate（集約）設計の詳細と失敗パターン

Vernon の "Implementing Domain-Driven Design"（IDDD, Red Book）を基準とした集約設計ガイド。

---

## Vernon の4原則

### 原則1: 真の不変条件のみを集約境界内で保護する

境界を決める唯一の基準は「同一トランザクションで整合性を保つ必要があるか」。

```
問い: 「A と B が一緒に変わらなければビジネスが壊れるか？」
  YES → 同じ Aggregate
  NO  → 別の Aggregate（Eventually Consistent で十分）
```

**悪い判断の例:**
```
注文確定 → 在庫を即時減らす必要があるか？
  表面上は YES に見える

実際は:
  1. 注文を確定する（Order Aggregate）
  2. OrderConfirmed イベントを発行
  3. 在庫サービスが非同期にイベントを受け取り在庫を減らす
  → Eventually Consistent で十分なケースがほとんど
```

### 原則2: 小さな集約を設計する

大きな集約は以下の問題を引き起こす:
- パフォーマンス問題（毎回大量データをロード）
- ロック競合（複数ユーザーが同じ集約を同時に変更）
- テストの困難さ（大きなオブジェクトグラフ）

**サイズの目安:**
- Entity 数: 1〜3個程度（Aggregate Root + 子 Entity 1〜2個）
- データサイズ: 単一トランザクションで扱えるサイズ
- 「Aggregate Root が直接の子として持つ Entity」を最小化する

### 原則3: 他の集約はIDのみで参照する

```typescript
// 悪い例: 集約境界を越えた直接参照
class Order {
  customer: Customer;      // Customer 集約全体を持っている
  items: OrderItem[];
}

// 良い例: IDのみで参照
class Order {
  customerId: CustomerId;  // IDのみ
  items: OrderItem[];
}
```

**なぜIDのみか:**
- 直接参照すると、Order ロード時に Customer も必ず ロードされる（パフォーマンス）
- Customer の変更が Order に影響する（不適切な結合）
- トランザクション境界が不明確になる

**Customer 情報が必要なユースケース:**
```typescript
// Application Service でそれぞれの Repository から取得
class OrderApplicationService {
  async confirmOrder(orderId: string): Promise<void> {
    const order = await this.orderRepository.findById(orderId);
    const customer = await this.customerRepository.findById(order.customerId);
    // ... 使用する
  }
}
```

### 原則4: Eventually Consistency を受け入れる

```
集約間の整合性は最終的整合性で十分なケースがほとんど。

例: 注文確定 → 在庫減算
  同一トランザクション:
    - 必ず成功する保証があるが、Order と Inventory が同じ集約になってしまう
    - ロック競合が増える
    - スケールしにくい

  Eventually Consistent（Domain Event 経由）:
    - Order の確定は即時
    - 在庫減算は非同期（数秒以内）
    - 在庫不足は別途補償トランザクションで対応
    → ほとんどのビジネスシナリオで許容可能
```

---

## 集約境界の決め方：実践チェックリスト

境界を引く前に以下の問いに答える:

### チェック1: 不変条件の確認
```
「集約 A のオブジェクトが変わったとき、
 集約 B のオブジェクトも同時に整合している必要があるか？」

YES → 同じ集約にする候補
NO  → 別の集約にして Domain Event で連携
```

### チェック2: ユースケースの同時実行確認
```
「A と B を同時に変更するユースケースが存在するか？
 かつ、その変更は同じユーザーの同じ操作で発生するか？」

YES → 同じ集約の候補（ただし慎重に）
NO  → 別の集約
```

### チェック3: ライフサイクルの確認
```
「B は A がなければ存在意義がないか？
 A が削除されたとき B も削除されるか？」

YES → B は A の集約内（Composition）
NO  → B は独立した集約
```

---

## Aggregate Root の選定基準

Aggregate Root は集約の「番人」。外部から集約にアクセスする唯一の入口。

**選定基準:**
1. 集約全体のライフサイクルを制御するオブジェクト
2. 集約内の不変条件を最終的に保護する責任を持つ
3. 集約の外部から唯一参照できるオブジェクト
4. 識別子（ID）を持つ（集約全体を識別する）

**ECサイトの例:**
```
Order Aggregate:
  Root: Order（注文全体のライフサイクルを管理）
  子: OrderItem（Order なしに OrderItem は存在しない）
  子: ShippingAddress（この注文の配送先）

Order が Aggregate Root である理由:
  - 「注文を確定する」「注文をキャンセルする」は Order が制御
  - OrderItem の追加・削除は必ず Order を通じて行う
  - 外部から OrderItem に直接アクセスしない

Cart Aggregate:
  Root: Cart（カート全体を管理）
  子: CartItem（カートなしに CartItem は存在しない）
```

---

## 集約間の参照ルール

### ルール1: 別集約への参照はIDのみ

```typescript
class Order {
  // 顧客への参照はIDのみ
  private customerId: CustomerId;

  // 商品への参照もIDのみ
  // OrderItem 内に ProductId を持つ
}

class OrderItem {
  private productId: ProductId;  // Product Aggregate へのID参照
  private quantity: Quantity;
  private unitPrice: Money;      // 注文時の価格（Productの現在価格ではない）
}
```

**注意: 注文明細に単価を持たせる理由**
```
Product の価格は変わる。
注文明細は「注文した時点の価格」を記録する必要がある。
→ OrderItem に unitPrice（注文時点の価格）を持たせる
```

### ルール2: 集約内オブジェクトは Aggregate Root 経由でのみアクセス

```typescript
// 悪い例
const orderItem = orderItemRepository.findById(itemId);  // OrderItem に直接アクセス
orderItem.updateQuantity(3);

// 良い例
const order = orderRepository.findById(orderId);
order.updateItemQuantity(itemId, 3);  // Order（Aggregate Root）経由
orderRepository.save(order);
```

---

## よくある失敗パターンとその解決策

### 失敗1: God Aggregate（巨大集約）

**症状:**
```typescript
class Order {
  customer: Customer;          // Customer 集約全体
  items: OrderItem[];
  cart: Cart;                  // Cart 集約全体
  payment: Payment;            // Payment 集約全体
  shipment: Shipment;          // Shipment 集約全体
  inventory: InventoryItem[];  // Inventory 集約全体
}
```

**問題:**
- Order を変更するたびに全関連データをロック
- 複数ユーザーが注文処理をすると高確率でロック競合
- Order クラスが肥大化し理解困難

**解決策:**
```typescript
// それぞれ独立した集約に分割
class Order {
  customerId: CustomerId;    // IDのみ
  items: OrderItem[];        // OrderItem は Order の子（同一集約）
  // Payment, Shipment は別集約
}

class Payment {
  orderId: OrderId;          // Order への ID 参照
  amount: Money;
  status: PaymentStatus;
}

class Shipment {
  orderId: OrderId;          // Order への ID 参照
  destination: Address;
  status: ShipmentStatus;
}
```

### 失敗2: 細かすぎる集約（過度な分割）

**症状:**
```typescript
// OrderItem を独立した集約にしてしまった
class OrderItemAggregate {
  id: OrderItemId;
  orderId: OrderId;
  productId: ProductId;
  quantity: Quantity;
}

// Order は OrderItem の集約を参照
class Order {
  items: OrderItemId[];  // IDのみ参照
}
```

**問題:**
- 「注文の合計金額が0以上」という不変条件を単一トランザクションで守れない
- Order と OrderItem の整合性を保つために複数トランザクションが必要

**解決策:**
```
Order と OrderItem は同一集約にする。
OrderItem は Order の子 Entity（ライフサイクルを共にする）。
```

### 失敗3: 集約をまたいだ直接操作

**症状:**
```typescript
// Application Service が集約の内部を直接操作
class OrderService {
  confirmOrder(orderId: string): void {
    const order = this.orderRepository.findById(orderId);
    order.items.forEach(item => {
      const inventory = this.inventoryRepository.findByProductId(item.productId);
      inventory.reserve(item.quantity);  // Inventory 集約の内部を直接変更
      this.inventoryRepository.save(inventory);
    });
    order.status = 'CONFIRMED';  // Order の内部を直接変更
    this.orderRepository.save(order);
  }
}
```

**問題:**
- 不変条件が守られていない（在庫が足りなくても注文確定できる）
- ビジネスルールが Application Service に漏れている

**解決策:**
```typescript
class Order {
  confirm(): OrderConfirmed {
    this.ensureNotEmpty();
    this.ensureDraftStatus();
    this.status = OrderStatus.CONFIRMED;
    return new OrderConfirmed(this.id, this.items, new Date());
  }
}

class OrderApplicationService {
  async confirmOrder(orderId: string): Promise<void> {
    const order = await this.orderRepository.findById(orderId);
    const event = order.confirm();  // 不変条件の保護は Order 自身に
    await this.orderRepository.save(order);
    await this.eventBus.publish(event);
    // InventoryService がイベントを受け取って非同期に在庫を引当
  }
}
```

### 失敗4: 集約の状態をすべて公開するpublicプロパティ

**症状:**
```typescript
class Order {
  public items: OrderItem[];  // 直接配列を公開
  public status: string;      // 直接変更可能
}

// 呼び出し側で直接操作
order.items.push(new OrderItem(...));  // 不変条件チェックをバイパス
order.status = 'CONFIRMED';            // ビジネスルールをバイパス
```

**解決策:**
```typescript
class Order {
  private items: OrderItem[];
  private status: OrderStatus;

  // 意味のある操作のみ公開
  addItem(product: Product, quantity: Quantity): void { ... }
  removeItem(itemId: OrderItemId): void { ... }
  confirm(): OrderConfirmed { ... }

  // 読み取りはOK（コピーを返す）
  get orderItems(): ReadonlyArray<OrderItem> {
    return [...this.items];
  }
}
```

---

## 集約サイズのガイドライン

### 小さな集約が良い理由

1. **パフォーマンス**: 小さいほどロード・保存が速い
2. **ロック競合の減少**: 変更される頻度が低い = 競合が少ない
3. **テストの容易さ**: 必要な状態のセットアップが少ない
4. **理解のしやすさ**: 一度に把握できる範囲が小さい

### 目安

```
推奨:
  - Aggregate Root: 1
  - 直接の子 Entity: 0〜3
  - Value Object: 制限なし（小さいほど良い）

警告サイン:
  - 集約に10個以上のフィールド
  - 子 Entity が5個以上
  - 集約ロード時に JOIN が3テーブル以上
```

---

## ドメインイベントによる集約間通信

### パターン: Publish-Subscribe

```typescript
// Step 1: 集約がイベントを発行
class Order {
  confirm(): void {
    // ... 不変条件チェック
    this.status = OrderStatus.CONFIRMED;
    this.record(new OrderConfirmed({
      orderId: this.id,
      customerId: this.customerId,
      items: this.items.map(i => i.toSnapshot()),
      confirmedAt: new Date()
    }));
  }
}

// Step 2: Application Service がイベントを取り出してバスに発行
class OrderApplicationService {
  async confirmOrder(id: string): Promise<void> {
    const order = await this.repo.findById(id);
    order.confirm();
    await this.repo.save(order);  // イベントはここで永続化
    for (const event of order.domainEvents) {
      await this.eventBus.publish(event);
    }
    order.clearDomainEvents();
  }
}

// Step 3: 別の集約のハンドラーがイベントを受信
class InventoryEventHandler {
  @On(OrderConfirmed)
  async handle(event: OrderConfirmed): Promise<void> {
    for (const item of event.items) {
      const inventory = await this.inventoryRepo.findByProductId(item.productId);
      inventory.reserve(item.quantity);
      await this.inventoryRepo.save(inventory);
    }
  }
}
```

### ドメインイベントの命名規則

| 良い例 | 悪い例 | 理由 |
|--------|--------|------|
| `OrderConfirmed` | `OrderStatusChanged` | 「何が起きたか」を表す |
| `PaymentProcessed` | `ProcessPayment` | 過去形（既に起きたこと） |
| `StockDepleted` | `UpdateInventory` | 命令形ではなく事実を表す |
| `CustomerRegistered` | `CustomerEvent` | 具体的な出来事を示す |
