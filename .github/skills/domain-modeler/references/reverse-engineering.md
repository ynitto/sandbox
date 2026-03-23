# 逆引きエンジニアリング（Reverse Engineering）ガイド

既存コードから DDD ドメインモデルを抽出・評価するための詳細リファレンス。

---

## 対象言語別の抽出パターン

### TypeScript / JavaScript

**Entity を示す手がかり**

```typescript
// アノテーションベース（TypeORM など）
@Entity()
class Order {
  @PrimaryGeneratedColumn('uuid')
  id: string;
}

// 命名規約ベース
class OrderEntity { ... }
class OrderAggregate { ... }

// 基底クラス継承
class Order extends BaseEntity { ... }
class Order extends AggregateRoot { ... }
```

**Value Object を示す手がかり**

```typescript
// readonly フィールドのみ
class Money {
  constructor(
    readonly amount: number,
    readonly currency: string,
  ) {}
  add(other: Money): Money {
    return new Money(this.amount + other.amount, this.currency);
  }
}

// Object.freeze パターン
class Address {
  constructor(readonly street: string, readonly city: string) {
    Object.freeze(this);
  }
}
```

**Repository を示す手がかり**

```typescript
// インターフェース定義
interface OrderRepository {
  findById(id: OrderId): Promise<Order | null>;
  save(order: Order): Promise<void>;
  delete(id: OrderId): Promise<void>;
}

// TypeORM
@EntityRepository(Order)
class OrderRepositoryImpl extends Repository<Order> { ... }
```

---

### Java / Kotlin

**Entity を示す手がかり**

```java
// JPA アノテーション
@Entity
@Table(name = "orders")
public class Order {
  @Id
  @GeneratedValue
  private Long id;
}

// Kotlin
@Entity
data class Order(
  @Id val id: UUID,
  val status: OrderStatus,
)
```

**Value Object を示す手がかり**

```java
// @Embeddable（JPA の埋め込みオブジェクト）
@Embeddable
public class Money {
  private final BigDecimal amount;
  private final Currency currency;

  // getter のみ、setter なし
  public BigDecimal getAmount() { return amount; }
}

// Kotlin の data class
data class Money(val amount: BigDecimal, val currency: Currency)
```

**Repository を示す手がかり**

```java
// Spring Data JPA
public interface OrderRepository extends JpaRepository<Order, UUID> {
  Optional<Order> findByCustomerId(UUID customerId);
}

// ドメイン層インターフェース
public interface OrderRepository {
  Optional<Order> findById(OrderId id);
  void save(Order order);
}
```

---

### Python

**Entity を示す手がかり**

```python
# Django ORM
class Order(models.Model):
    status = models.CharField(...)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)

# SQLAlchemy
class Order(Base):
    __tablename__ = "orders"
    id = Column(UUID, primary_key=True)

# Pydantic ベースのドメインモデル
class Order(BaseModel):
    id: UUID
    status: OrderStatus

    def cancel(self) -> None: ...
```

**Value Object を示す手がかり**

```python
# dataclass(frozen=True)
@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def add(self, other: "Money") -> "Money":
        return Money(self.amount + other.amount, self.currency)

# NamedTuple
class Address(NamedTuple):
    street: str
    city: str
    zip_code: str
```

**Repository を示す手がかり**

```python
class OrderRepository(ABC):
    @abstractmethod
    def find_by_id(self, order_id: OrderId) -> Optional[Order]: ...

    @abstractmethod
    def save(self, order: Order) -> None: ...
```

---

### Go

**Entity を示す手がかり**

```go
// ID フィールドを持つ構造体
type Order struct {
    ID         uuid.UUID
    CustomerID uuid.UUID
    Status     OrderStatus
    Items      []OrderItem
}

func (o *Order) Cancel() error { ... }
func (o *Order) Place() error { ... }
```

**Value Object を示す手がかり**

```go
// 変換メソッドを返すパターン（ポインタレシーバなし）
type Money struct {
    Amount   decimal.Decimal
    Currency string
}

func (m Money) Add(other Money) Money {
    return Money{Amount: m.Amount.Add(other.Amount), Currency: m.Currency}
}
```

**Repository を示す手がかり**

```go
type OrderRepository interface {
    FindByID(ctx context.Context, id uuid.UUID) (*Order, error)
    Save(ctx context.Context, order *Order) error
    Delete(ctx context.Context, id uuid.UUID) error
}
```

---

## よくある「混合パターン」の解釈

実際のコードベースではドメインと技術の関心が混在していることが多い。
以下のパターンを識別して適切に分類する。

### アクティブレコードパターン（Rails / Django / Eloquent）

```ruby
class Order < ApplicationRecord
  belongs_to :customer
  has_many :order_items
  validates :status, inclusion: { in: %w[pending confirmed shipped cancelled] }

  def cancel!
    update!(status: 'cancelled')
    OrderCancelledEvent.publish(self)
  end
end
```

**解釈**: ActiveRecord は Entity + Repository + インフラを一体化したパターン。
- `cancel!` のような状態変化メソッド → ドメイン Entity の振る舞い
- `belongs_to`, `has_many` → 集約・関連の手がかり
- `validates` → 不変条件（ドメインルール）
- `update!`, `save!` → Repository 操作（インフラ）

図として表現する際は **Entity としてモデリング** し、DB 操作部分は含めない。

### Transaction Script パターン（手続き型サービス）

```typescript
class OrderService {
  async placeOrder(customerId: string, items: OrderItemDto[]): Promise<void> {
    const customer = await this.customerRepo.findById(customerId);
    const order = new Order();
    order.customerId = customerId;
    order.items = items.map(i => new OrderItem(i));
    order.status = 'PLACED';
    await this.orderRepo.save(order);
    await this.emailService.sendConfirmation(customer.email);
  }
}
```

**解釈**: ドメインロジックがサービス層に流出している（貧血ドメインモデル）。
- `Order` クラスはデータ保持のみ → 貧血ドメインモデルとして記録
- ドメインサービスとして `placeOrder` を図に含める
- ギャップ評価で「ビジネスロジックを Order に移動」を提案

---

## ギャップ評価の深刻度判定基準

| 深刻度 | 定義 | 例 |
|--------|------|-----|
| **高** | データ整合性・ドメインルール漏洩のリスク | 集約間の直接オブジェクト参照・不変条件がサービス層に分散 |
| **中** | 保守性・拡張性への影響 | 貧血ドメインモデル・双方向参照の多用 |
| **低** | コードの表現力・可読性 | 命名の不統一・VO が Entity になっている |

---

## 出力テンプレート（逆引きモード）

```markdown
## 解析対象

- ファイル: `src/domain/order/`, `src/domain/customer/`
- 言語: TypeScript
- フレームワーク: TypeORM + NestJS

## 抽出された要素

| クラス名 | 推定分類 | 判定根拠 |
|---------|---------|---------|
| Order | Aggregate Root | OrderRepository が存在、place()/cancel() を持つ |
| OrderItem | Entity | Order にコンポジション、id フィールドあり |
| Money | Value Object | readonly フィールドのみ、add() が新インスタンスを返す |
| Customer | Entity | CustomerRepository が存在、独立したライフサイクル |
| OrderService | Domain Service | ステートレス、複数集約をまたぐ |
| OrderRepository | Repository | findById()/save() インターフェース |

## As-Is ドメインモデル図

[Mermaid classDiagram]

## DDD ギャップ評価

| # | 問題 | 該当 | 深刻度 | 改善方針 |
|---|------|------|--------|---------|
| 1 | ... | ... | 高/中/低 | ... |

## To-Be ドメインモデル図（改善案）

[Mermaid classDiagram]

## リファクタリング優先度

### 即対応（深刻度:高）
- ...

### 次スプリント（深刻度:中）
- ...

### 将来的に検討（深刻度:低）
- ...
```

---

## 関連リファレンス

- **DDD コアコンセプト**: [core-concepts.md](core-concepts.md)
- **集約設計の原則**: [aggregate-design.md](aggregate-design.md)
- **関係の種類**: [relationships.md](relationships.md)
- **DDD パターン総合**: [ddd-patterns.md](ddd-patterns.md)
- **Mermaid 記法**: [mermaid-notation.md](mermaid-notation.md)
