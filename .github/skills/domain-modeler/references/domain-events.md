# Domain Events（ドメインイベント）設計ガイド

---

## ドメインイベントとは

「ドメインで起きた重要な出来事」を表すオブジェクト。

### 特徴
- 過去に起きたことを表す（過去形の名前）
- イミュータブル（変更不可）
- ドメインの観点から意味のある出来事のみ
- 技術的な処理の結果ではない

---

## いつドメインイベントを使うか

### 使うべきケース

**1. 集約の状態変化を他の集約・コンテキストに伝播させる**
```
Order.confirm() → OrderConfirmed イベント
→ Inventory が在庫を引き当てる
→ Notification が確認メールを送る
→ Analytics が売上を記録する
```

**2. 副作用を疎結合で実行する**
```
メール送信・プッシュ通知・ログ記録・外部API呼び出し
これらを集約の操作に直接書かず、イベント経由で実行する
```

**3. Eventual Consistency を実現する**
```
同一トランザクションで整合性を保てない
= 別の集約への影響
= ドメインイベント + 非同期ハンドラー
```

**4. 監査ログ（Audit Trail）の記録**
```
「いつ誰が何をしたか」をイベントとして永続化する
Event Sourcing パターンへの発展が可能
```

**5. プロセス・ワークフローのトリガー**
```
OrderConfirmed → PaymentProcess 開始
PaymentProcessed → ShipmentCreation 開始
ShipmentShipped → Notification 送信
```

### 使わなくていいケース

```
単一集約内の操作で副作用がない場合
  例: Order の合計金額を計算するだけ → イベント不要

読み取り操作（クエリ）
  例: 注文一覧の取得 → イベント不要

同一境界コンテキスト内の単純な連携
  例: 同一トランザクション内で完結する操作
```

---

## 命名規則

### 基本: 「対象 + 過去形動詞」

| 良い例 | 悪い例 | 理由 |
|--------|--------|------|
| `OrderConfirmed` | `OrderStatusChanged` | 具体的な出来事を表す |
| `PaymentProcessed` | `ProcessPayment` | 過去形（既に起きたこと） |
| `StockDepleted` | `UpdateInventory` | 命令形ではなく事実を表す |
| `CustomerRegistered` | `CustomerEvent` | 具体的な出来事を示す |
| `ReservationCancelled` | `CancelReservation` | 「〜された」という事実 |
| `ShipmentDispatched` | `ShipmentUpdated` | 何が起きたかが明確 |

### 命名パターン

```
[集約名][過去形動詞]

例:
  Order + Confirmed → OrderConfirmed
  Payment + Failed → PaymentFailed
  Inventory + Reserved → InventoryReserved
  Customer + Registered → CustomerRegistered
  Shipment + Dispatched → ShipmentDispatched
```

---

## ドメインイベントの実装

### 最小限のイベント構造

```typescript
// 基底クラス（オプション）
abstract class DomainEvent {
  readonly occurredAt: Date;
  readonly eventId: string;

  constructor() {
    this.occurredAt = new Date();
    this.eventId = crypto.randomUUID();
  }
}

// 具体的なドメインイベント
class OrderConfirmed extends DomainEvent {
  constructor(
    readonly orderId: OrderId,
    readonly customerId: CustomerId,
    readonly items: ReadonlyArray<OrderItemSnapshot>,
    readonly totalAmount: Money
  ) {
    super();
  }
}

class PaymentFailed extends DomainEvent {
  constructor(
    readonly orderId: OrderId,
    readonly reason: PaymentFailureReason,
    readonly attemptedAmount: Money
  ) {
    super();
  }
}
```

### 集約へのイベント記録

```typescript
class Order {
  private domainEvents: DomainEvent[] = [];

  confirm(): void {
    this.ensureNotEmpty();
    this.ensureDraftStatus();
    this.status = OrderStatus.CONFIRMED;

    // イベントを記録（まだ発行しない）
    this.domainEvents.push(new OrderConfirmed(
      this.id,
      this.customerId,
      this.items.map(i => i.toSnapshot()),
      this.totalAmount
    ));
  }

  // Application Service がイベントを取り出す
  pullDomainEvents(): DomainEvent[] {
    const events = [...this.domainEvents];
    this.domainEvents = [];
    return events;
  }
}
```

### Application Service でのイベント発行

```typescript
class OrderApplicationService {
  async confirmOrder(orderId: string): Promise<void> {
    // 1. 集約をロード
    const order = await this.orderRepository.findById(new OrderId(orderId));
    if (!order) throw new NotFoundError(`Order not found: ${orderId}`);

    // 2. ドメイン操作（集約が不変条件を守る）
    order.confirm();

    // 3. 永続化（イベントも一緒に保存するのが理想）
    await this.orderRepository.save(order);

    // 4. イベントを発行
    const events = order.pullDomainEvents();
    for (const event of events) {
      await this.eventBus.publish(event);
    }
  }
}
```

### イベントハンドラー

```typescript
// 在庫サービスが OrderConfirmed を受け取り在庫を引き当てる
class InventoryEventHandler {
  @EventHandler(OrderConfirmed)
  async onOrderConfirmed(event: OrderConfirmed): Promise<void> {
    for (const item of event.items) {
      const inventory = await this.inventoryRepository.findByProductId(item.productId);
      if (!inventory) {
        // 補償トランザクション: 在庫がない場合の処理
        await this.eventBus.publish(new InventoryReservationFailed(event.orderId, item.productId));
        return;
      }
      inventory.reserve(item.quantity);
      await this.inventoryRepository.save(inventory);
    }
  }
}

// 通知サービスが OrderConfirmed を受け取りメールを送る
class NotificationEventHandler {
  @EventHandler(OrderConfirmed)
  async onOrderConfirmed(event: OrderConfirmed): Promise<void> {
    const customer = await this.customerRepository.findById(event.customerId);
    await this.emailService.sendOrderConfirmation(customer.email, event);
  }
}
```

---

## 集約間通信でのドメインイベントの役割

### パターン: Choreography（コレオグラフィ）

各サービスが自律的にイベントに反応する。中央オーケストレーターなし。

```
OrderConfirmed
  ├→ InventoryEventHandler → 在庫引当
  ├→ NotificationHandler → 確認メール送信
  └→ AnalyticsHandler → 売上記録
```

**メリット:** 疎結合、スケーラブル
**デメリット:** フロー全体の把握が難しい

### パターン: Orchestration（オーケストレーション）

中央のプロセスマネージャーが各ステップを管理。

```
OrderConfirmed
  → OrderFulfillmentSaga
    Step 1: InventoryService.reserve() → 成功 → Step 2
    Step 2: PaymentService.charge()   → 成功 → Step 3
    Step 3: ShippingService.create()  → 成功 → 完了
    失敗時: 補償トランザクションを実行
```

**メリット:** フローが明確、エラーハンドリングが集中
**デメリット:** 結合度が上がる

---

## ドメインイベントに含めるべきデータ

### 原則: 「その時点のスナップショット」を含める

```typescript
// 悪い例: IDのみ（受信者が再クエリする必要がある）
class OrderConfirmed {
  orderId: OrderId;
  // 他の情報なし → ハンドラーが DB を再クエリしなければならない
}

// 良い例: 必要な情報を含む（ただし過剰にしない）
class OrderConfirmed {
  orderId: OrderId;
  customerId: CustomerId;
  items: OrderItemSnapshot[];  // 注文時点の商品・数量・価格
  totalAmount: Money;
  confirmedAt: Date;
}
```

### 含めるべき情報の基準

1. **イベントを受け取る全ハンドラーが必要とする情報**を含める
2. **現在の状態でなく、イベント発生時点の状態**（スナップショット）
3. **変更された部分の前後の状態**（監査目的の場合）

### 含めすぎに注意

```
悪い例: 集約全体をイベントに詰め込む
  class OrderConfirmed {
    entireOrder: Order;  // 集約全体 → 過剰
  }

良い例: ハンドラーが実際に必要とする最小限
  class OrderConfirmed {
    orderId: OrderId;
    customerId: CustomerId;
    items: Array<{ productId: string, quantity: number, unitPrice: number }>;
    totalAmount: number;
  }
```

---

## ドメインイベントの失敗パターン

### 失敗1: イベントを発行し忘れる

```typescript
// 悪い例: confirm() の実装でイベントを記録していない
class Order {
  confirm(): void {
    this.status = OrderStatus.CONFIRMED;
    // ← イベント記録なし！在庫引当が実行されない
  }
}
```

### 失敗2: 技術的な処理をドメインイベントにする

```typescript
// 悪い例: 技術的な操作をドメインイベントとして発行
class DatabaseSaved {}       // 技術的
class CacheInvalidated {}    // 技術的
class HttpRequestCompleted {} // 技術的

// 良い例: ビジネス上の出来事のみ
class OrderConfirmed {}
class PaymentFailed {}
class CustomerRegistered {}
```

### 失敗3: イベントに可変オブジェクトを含める

```typescript
// 悪い例: イベントが可変オブジェクトを参照
class OrderConfirmed {
  order: Order;  // Orderは後から変更される可能性
}

// 良い例: スナップショット（不変の値）を保持
class OrderConfirmed {
  items: ReadonlyArray<OrderItemSnapshot>;  // スナップショット
}
```

### 失敗4: 同期的なイベント処理で失敗を無視する

```typescript
// 悪い例: 例外を飲み込む
eventBus.on(OrderConfirmed, async (event) => {
  try {
    await inventory.reserve(event.items);
  } catch (e) {
    console.error(e);  // エラーを無視して処理を続ける
  }
});

// 良い例: 補償トランザクションを実行
eventBus.on(OrderConfirmed, async (event) => {
  try {
    await inventory.reserve(event.items);
  } catch (e) {
    await eventBus.publish(new InventoryReservationFailed(event.orderId, e.reason));
    // Saga が補償トランザクション（注文キャンセル等）を実行
  }
});
```
