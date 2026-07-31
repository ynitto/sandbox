# リファクタリング手法コード例

## 目次

- [関数の抽出（Extract Method）](#関数の抽出extract-method)
- [ガード節による平坦化（Replace Nested Conditional with Guard Clauses）](#ガード節による平坦化replace-nested-conditional-with-guard-clauses)
- [型安全性の向上（Introduce Type Safety）](#型安全性の向上introduce-type-safety)
- [Strategyパターン（条件分岐の置き換え）](#strategyパターン条件分岐の置き換え)
- [Chain of Responsibilityパターン（バリデーション処理の分解）](#chain-of-responsibilityパターンバリデーション処理の分解)

---

## 関数の抽出（Extract Method）

長い関数から意味のある処理のまとまりを関数として切り出す。

**Before:**
```typescript
function printReport(users: User[]) {
  // ヘッダー印刷
  console.log("=".repeat(40));
  console.log("ユーザーレポート");
  console.log(`生成日時: ${new Date().toLocaleDateString("ja-JP")}`);
  console.log("=".repeat(40));

  // ユーザー一覧印刷
  for (const user of users) {
    const status = user.active ? "有効" : "無効";
    console.log(`${user.name} (${user.email}) - ${status}`);
  }
  console.log(`合計: ${users.length}名`);
}
```

**After:**
```typescript
function printReport(users: User[]) {
  printHeader();
  printUserSection(users);
}

function printHeader() {
  console.log("=".repeat(40));
  console.log("ユーザーレポート");
  console.log(`生成日時: ${new Date().toLocaleDateString("ja-JP")}`);
  console.log("=".repeat(40));
}

function printUserSection(users: User[]) {
  for (const user of users) {
    const status = user.active ? "有効" : "無効";
    console.log(`${user.name} (${user.email}) - ${status}`);
  }
  console.log(`合計: ${users.length}名`);
}
```

---

## ガード節による平坦化（Replace Nested Conditional with Guard Clauses）

**Before:**
```typescript
function getDiscount(user: User): number {
  if (user !== null) {
    if (user.active) {
      if (user.membershipYears >= 5) {
        return 0.2;
      } else {
        return 0.1;
      }
    } else {
      return 0;
    }
  } else {
    return 0;
  }
}
```

**After:**
```typescript
function getDiscount(user: User): number {
  if (user === null || !user.active) return 0;
  if (user.membershipYears >= 5) return 0.2;
  return 0.1;
}
```

---

## 型安全性の向上（Introduce Type Safety）

**Before:**
```typescript
function createUser(name: string, role: string, age: number) {
  if (role !== "admin" && role !== "member") throw new Error("不正なロール");
  if (age < 0 || age > 150) throw new Error("不正な年齢");
  // ...
}
```

**After:**
```typescript
type Role = "admin" | "member";

class Age {
  constructor(readonly value: number) {
    if (value < 0 || value > 150) throw new Error("不正な年齢");
  }
}

function createUser(name: string, role: Role, age: Age) {
  // コンパイラが不正な値を排除してくれる
}
```

---

## Strategyパターン（条件分岐の置き換え）

**Before:**
```typescript
function calculateTax(amount: number, country: string): number {
  if (country === "JP") return amount * 0.1;
  if (country === "US") return amount * 0.08;
  if (country === "DE") return amount * 0.19;
  throw new Error(`未対応の国: ${country}`);
}
```

**After:**
```typescript
interface TaxStrategy {
  calculate(amount: number): number;
}

const taxStrategies: Record<string, TaxStrategy> = {
  JP: { calculate: (amount) => amount * 0.1 },
  US: { calculate: (amount) => amount * 0.08 },
  DE: { calculate: (amount) => amount * 0.19 },
};

function calculateTax(amount: number, country: string): number {
  const strategy = taxStrategies[country];
  if (!strategy) throw new Error(`未対応の国: ${country}`);
  return strategy.calculate(amount);
}
```

---

## Chain of Responsibilityパターン（バリデーション処理の分解）

**Before:**
```typescript
function validateOrder(order: Order): string[] {
  const errors: string[] = [];
  if (!order.userId) errors.push("ユーザーIDが必要");
  if (!order.items || order.items.length === 0) errors.push("商品が必要");
  if (order.total <= 0) errors.push("合計金額が不正");
  if (!order.shippingAddress) errors.push("配送先が必要");
  return errors;
}
```

**After:**
```typescript
type Validator = (order: Order) => string | null;

const validators: Validator[] = [
  (o) => (!o.userId ? "ユーザーIDが必要" : null),
  (o) => (!o.items?.length ? "商品が必要" : null),
  (o) => (o.total <= 0 ? "合計金額が不正" : null),
  (o) => (!o.shippingAddress ? "配送先が必要" : null),
];

function validateOrder(order: Order): string[] {
  return validators.map((v) => v(order)).filter((e): e is string => e !== null);
}
```
