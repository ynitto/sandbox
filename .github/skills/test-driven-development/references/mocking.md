# モックの使いどころ

モックは**システム境界でのみ**使う:

- 外部 API（決済・メール等）
- データベース（場合による。可能ならテスト用 DB を優先）
- 時刻・乱数
- ファイルシステム（場合による）

モックしてはいけないもの:

- 自分のクラス・モジュール
- 内部の協力オブジェクト
- 自分が制御できるもの

内部をモックすると、テストが実装詳細に結合し、リファクタで壊れる「悪いテスト」になる。

## モックしやすい設計

システム境界では、モックしやすいインターフェースを設計する。

**1. 依存性注入を使う**

外部依存は内部で生成せず、外から渡す:

```typescript
// モックしやすい
function processPayment(order, paymentClient) {
  return paymentClient.charge(order.total);
}

// モックしにくい
function processPayment(order) {
  const client = new StripeClient(process.env.STRIPE_KEY);
  return client.charge(order.total);
}
```

**2. 汎用フェッチャーより SDK スタイルのインターフェースを好む**

条件分岐を持つ 1 つの汎用関数ではなく、外部操作ごとに専用関数を作る:

```typescript
// GOOD: 各関数が独立してモック可能
const api = {
  getUser: (id) => fetch(`/users/${id}`),
  getOrders: (userId) => fetch(`/users/${userId}/orders`),
  createOrder: (data) => fetch('/orders', { method: 'POST', body: data }),
};

// BAD: モックの中に条件分岐が必要になる
const api = {
  fetch: (endpoint, options) => fetch(endpoint, options),
};
```

SDK スタイルの利点:

- 各モックが 1 つの具体的な形を返す
- テストセットアップに条件分岐が不要
- どのエンドポイントをテストが使うか一目で分かる
- エンドポイントごとに型安全
