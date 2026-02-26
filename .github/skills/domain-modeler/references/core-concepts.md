# ドメインモデル設計：コア概念と判断基準

DDD（Domain-Driven Design）における設計判断の根拠となる原則集。
各概念の「なぜ」と「どこで失敗するか」に焦点を当てる。

---

## 1. Entity vs Value Object の区別基準

### 判断の根本原則

**同一性（Identity）の有無が唯一の判断軸。**

| 観点 | Entity | Value Object |
|------|--------|-------------|
| 識別子 | 持つ（ID） | 持たない |
| 等価性 | IDが同じ = 同一 | 全属性が同じ = 同一 |
| 可変性 | 可変（ミュータブル） | 不変（イミュータブル）が原則 |
| ライフサイクル | 独自に持つ | 所有者のライフサイクルに従う |
| 交換可能性 | 交換不可 | 同一値なら交換可 |

### 判断フロー

```
「この概念を時間の経過とともに追跡する必要があるか？」
  YES → Entity
    └→ 属性が変わっても「同じもの」として識別される
    └→ 例: 顧客（メアドが変わっても同じ顧客）、注文、商品

  NO  → 「同じ値なら入れ替え可能か？」
          YES → Value Object
            └→ 例: 金額、住所、座標、色、日付範囲
          NO  → ドメインの理解が不足している可能性
```

### 具体例：ECサイトの場合

**Entity:**
- `Customer` (顧客): メールアドレスが変わっても同じ顧客
- `Order` (注文): 注文内容が変更されても同じ注文
- `Product` (商品): 価格が変わっても同じ商品

**Value Object:**
- `Money` (金額): `Money(1000, JPY)` は同じ値なら同一
- `Address` (住所): 全フィールドが等しければ同一
- `EmailAddress` (メールアドレス): フォーマット検証を内包する値
- `DateRange` (日付範囲): 開始日・終了日のペア
- `Quantity` (数量): 単位付き数値

### よくある間違い・落とし穴

**間違い1: 住所を Entity にする**
```
悪い例: Address { addressId, street, city }
問題: 住所 ID を管理する理由がない。同じ住所を「同じもの」として識別したいのか？
正しい: Address は Value Object。不変で、同じ住所なら入れ替え可能。
```

**間違い2: DB の技術都合で Entity にする**
```
悪い例: OrderItemVO にもサロゲートキーを持たせる（ORMの都合）
問題: ドメインの概念がDBの都合に引きずられている
正しい: ドメインモデルはVO、インフラ層でマッピング時に技術的IDを付与
```

**間違い3: 注文明細を VO にする**
```
悪い例: OrderItem を Value Object として扱う
問題: 「注文明細ID: 5 の数量を変更する」という操作が必要なら Entity
判断: 明細単位での状態変化・追跡が必要かどうかで決める
```

**間違い4: Entityが不変条件を持たない**
```
悪い例:
  order.setStatus("CONFIRMED");  // 直接セット
  order.setTotalAmount(0);       // 不正な値でも通る

正しい:
  order.confirm();               // ビジネスルールを含むメソッド
  // 内部で: if (items.isEmpty()) throw new DomainException("空の注文は確定できない");
```

---

## 2. ユビキタス言語（Ubiquitous Language）

### 構築プロセス

1. **イベントストーミング**: ドメインエキスパートとステッカーで時系列にドメインイベントを並べる
2. **語彙の収集**: 会話に出てくる用語を全て記録する
3. **定義の合意**: 曖昧な言葉の意味を明確化・合意する
4. **コードへの反映**: クラス名・メソッド名・変数名に正確に反映する
5. **継続的な洗練**: 「モデルと会話が一致しているか」を常にチェックする

### 重要原則

- 同じ Bounded Context 内では用語の意味が一意である
- 「注文」と「オーダー」を混在させない
- コードが語彙の最終的な真実の源泉（living documentation）
- 「翻訳が必要な会話」はモデルが間違っているサイン

### 境界コンテキストをまたぐ言語の違い（正常な状態）

```
Sales Context の "Product":
  - 商品名、説明、価格、カテゴリ

Inventory Context の "Product":
  - SKU、在庫数、倉庫ロケーション、補充閾値

Shipping Context の "Product":
  - 重量、寸法、危険物フラグ

同じ「Product」でも異なるモデル → 境界コンテキストの分離が正解
```

---

## 3. Domain Service の識別

### 識別基準

「どのEntityやValue Objectにも自然に属さないドメインロジック」がDomain Serviceになる。

**Domain Serviceになるべき操作の見分け方:**
1. 複数の集約をまたぐ操作（どちらの集約にも自然に入らない）
2. 外部サービスとの協調が必要（ただしインフラ詳細はDomain Serviceに入れない）
3. ドメインの概念として名前が付けられる（「〜サービス」「〜ポリシー」）
4. ステートレスである

### 具体例

```
// 送金サービス: fromAccount と toAccount の両方が関与
class TransferService {
  transfer(fromAccount: Account, toAccount: Account, amount: Money): void {
    fromAccount.withdraw(amount);  // 各集約の不変条件を守りつつ
    toAccount.deposit(amount);     // 複数集約をまたぐ操作
  }
}

// 価格計算サービス: 顧客ランク・商品・クーポンをまたぐ計算
class PricingService {
  calculate(product: Product, customer: Customer, coupon?: Coupon): Money
}

// 在庫引当サービス: 注文と在庫の両集約をまたぐ
class AllocationService {
  allocate(order: Order, inventory: Inventory): AllocationResult
}
```

### Application Service との区別

```
Domain Service:
  - ドメインロジック（ビジネスルール）を持つ
  - ドメイン層に属する
  - 例: TransferService, PricingService

Application Service:
  - ユースケースのオーケストレーション（ロジックを持たない）
  - インフラ層を呼び出す（Repository, 外部API）
  - 例: OrderApplicationService.placeOrder()
```

---

## 4. 貧血ドメインモデル vs 充血ドメインモデル

### 貧血ドメインモデル（アンチパターン）の症状

```typescript
// 貧血: データクラス + Serviceにロジック
class Order {
  id: string;
  items: OrderItem[];
  status: string;
  totalAmount: number;
  // getter/setterのみ
}

class OrderService {
  // ビジネスルールが全てServiceに
  confirm(order: Order): void {
    if (order.items.length === 0) throw new Error(...);
    if (order.status !== 'DRAFT') throw new Error(...);
    order.status = 'CONFIRMED';  // 直接書き換え
  }

  calculateTotal(order: Order): number {
    return order.items.reduce((sum, item) => sum + item.price * item.quantity, 0);
  }
}
```

**問題点:**
- ドメインオブジェクトが不変条件を自分で守れない
- ロジックが散在し、同じルールが複数箇所に重複
- 「Order を使うコードはどこでも OrderService を通さなければならない」という暗黙ルール

### 充血ドメインモデル（正しいアプローチ）

```typescript
class Order {
  private id: OrderId;
  private items: OrderItem[] = [];
  private status: OrderStatus = OrderStatus.DRAFT;

  // ビジネス操作はモデル自身に
  addItem(product: Product, quantity: Quantity): void {
    if (this.status !== OrderStatus.DRAFT) {
      throw new DomainException('確定済みの注文には商品を追加できません');
    }
    this.items.push(new OrderItem(product, quantity));
  }

  confirm(): DomainEvent[] {
    if (this.items.length === 0) {
      throw new DomainException('商品が空の注文は確定できません');
    }
    this.status = OrderStatus.CONFIRMED;
    return [new OrderConfirmed(this.id, new Date())];
  }

  // 計算ロジックもモデル自身に
  get totalAmount(): Money {
    return this.items.reduce(
      (sum, item) => sum.add(item.subtotal),
      Money.zero(Currency.JPY)
    );
  }
}
```

### 移行方針

1. Service のメソッドをリストアップし「このロジックはどの Entity に属するか」で分類
2. 単一 Entity のデータのみを使うロジック → その Entity に移動
3. 複数 Entity にまたがるロジック → Domain Service に移動
4. 不変条件の検証をコンストラクタ・コマンドメソッドに移動
5. setter を削除し、意味のあるコマンドメソッドに置き換える

---

## 5. ドメインモデルとDBスキーマの分離

### なぜ分離すべきか

| 観点 | ドメインモデル | DBスキーマ |
|------|-------------|----------|
| 目的 | ビジネス概念の表現 | データの効率的な永続化 |
| 変化の理由 | ビジネスルールの変化 | パフォーマンス要件・技術選定の変化 |
| 設計原則 | ユビキタス言語に忠実 | 正規化・インデックス・トランザクション |
| 制約の発生源 | ドメインの不変条件 | DB制約（NULL, FK, UNIQUE） |

### よくある混同パターン

```java
// 悪い例: ドメインクラスにDBアノテーションが混入
@Entity
@Table(name = "orders")
public class Order {
    @Id
    @GeneratedValue
    private Long id;  // ドメインのIDか、DBのサロゲートキーか不明

    @ManyToOne
    @JoinColumn(name = "customer_id")
    private Customer customer;  // 集約境界を越えた直接参照

    @Column(nullable = true)  // DBの都合でnullableになっているが、本当にnullでいいのか？
    private LocalDate deliveryDate;
}
```

**問題点:**
- ドメインクラスがORMの制約に縛られる
- 集約境界がDBのFKで決まってしまう
- ドメインを変えるとDBも変わる（逆も然り）

### 正しい分離パターン

```typescript
// ドメイン層: DBを知らない純粋なモデル
class Order {
  constructor(
    private readonly id: OrderId,
    private readonly customerId: CustomerId,  // IDのみ参照（直接参照しない）
    private items: OrderItem[]
  ) {}
}

// インフラ層: DBとドメインのマッピング
class OrderRepository implements IOrderRepository {
  async findById(id: OrderId): Promise<Order | null> {
    const record = await this.db.query('SELECT * FROM orders WHERE id = ?', [id.value]);
    if (!record) return null;
    return this.toDomain(record);  // DBレコード → ドメインオブジェクト
  }

  private toDomain(record: OrderRecord): Order {
    return Order.reconstruct(
      new OrderId(record.id),
      new CustomerId(record.customer_id),
      record.items.map(item => this.toOrderItem(item))
    );
  }
}
```

---

## 6. 不変条件（Invariants）の管理

### 不変条件とは

「集約が常に満たすべきビジネスルール」。集約の目的は不変条件の保護にある。

### 具体例（ECサイト）

```
Order（注文集約）の不変条件:
  - 注文には少なくとも1つの注文明細が必要
  - 合計金額は0より大きくなければならない
  - 確定済み注文の商品は変更できない
  - キャンセル済みの注文は再確定できない

OrderItem（注文明細）の不変条件:
  - 数量は1以上でなければならない
  - 単価は0より大きくなければならない
```

### 実装パターン

**パターン1: コンストラクタでの検証（生成時の不変条件）**
```typescript
class Money {
  constructor(
    private readonly amount: number,
    private readonly currency: Currency
  ) {
    if (amount < 0) throw new DomainException('金額は0以上でなければなりません');
    if (!currency) throw new DomainException('通貨は必須です');
  }
}
```

**パターン2: コマンドメソッド内での検証（状態変化時）**
```typescript
class Order {
  confirm(): void {
    this.ensureNotEmpty();
    this.ensureDraftStatus();
    this.status = OrderStatus.CONFIRMED;
    this.domainEvents.push(new OrderConfirmed(this.id));
  }

  private ensureNotEmpty(): void {
    if (this.items.length === 0) {
      throw new DomainException('商品が空の注文は確定できません');
    }
  }
}
```

**パターン3: ファクトリーメソッド（複雑な生成ロジック）**
```typescript
class Order {
  static create(customerId: CustomerId, items: CartItem[]): Order {
    if (items.length === 0) throw new DomainException('カートが空です');
    const orderItems = items.map(item => OrderItem.from(item));
    return new Order(OrderId.generate(), customerId, orderItems);
  }
}
```

**パターン4: Specification パターン（複雑な条件の表現）**
```typescript
class CanBeConfirmedSpecification {
  isSatisfiedBy(order: Order): boolean {
    return order.items.length > 0
      && order.status === OrderStatus.DRAFT
      && order.totalAmount.isGreaterThan(Money.zero(Currency.JPY));
  }
}
```

---

## 7. 戦略的設計 vs 戦術的設計の使い分け

### 戦略的設計（Strategic Design）

大局的な構造・チーム間の関係を決める設計。

| 概念 | 目的 |
|------|------|
| Bounded Context | モデルの適用範囲を定義する |
| Context Map | BC間の関係・依存を可視化する |
| Ubiquitous Language | チームの共通言語を確立する |
| Core/Supporting/Generic Subdomain | 投資優先度を決める |

**どんなプロジェクトにも必要:** 小規模でも BC の概念は有用。複数チームがいるなら必須。

### 戦術的設計（Tactical Design）

コードレベルの実装パターン。

| 概念 | 目的 |
|------|------|
| Entity / Value Object | オブジェクトの性質を表現 |
| Aggregate | トランザクション境界の定義 |
| Repository | 集約の永続化インターフェース |
| Factory | 複雑な集約の生成ロジック |
| Domain Service | エンティティに属さないロジック |
| Domain Event | 集約間の疎結合な通信 |

### プロジェクト規模に応じた適用範囲

```
単純なCRUDアプリ:
  → 戦術的設計の全パターンは不要
  → Entity/VO の区別 + Repository パターンで十分

中規模の業務システム:
  → 戦略的設計: BC の特定 + Context Map
  → 戦術的設計: コアドメインに集中して適用

大規模なマイクロサービス:
  → 戦略的設計: 全パターンを適用
  → 各サービスが1つの BC に対応
  → BC間の連携パターンを明示的に選択

原則: コアサブドメイン（競争優位の源泉）に重点投資
      サポート・汎用サブドメインは軽量な実装で十分
```
