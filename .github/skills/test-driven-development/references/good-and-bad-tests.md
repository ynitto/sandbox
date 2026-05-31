# 良いテストと悪いテスト

テストは**公開インターフェースを通じて「振る舞い」を検証する**。実装の詳細には依存しない。実装は丸ごと変わっても、テストは壊れてはならない。

## 良いテスト

**統合スタイル**: 内部部品のモックではなく、実際のインターフェースを通してテストする。

```typescript
// GOOD: 観測可能な振る舞いをテストする
test("user can checkout with valid cart", async () => {
  const cart = createCart();
  cart.add(product);
  const result = await checkout(cart, paymentMethod);
  expect(result.status).toBe("confirmed");
});
```

特徴:

- 利用者（ユーザー・呼び出し側）が気にする振る舞いをテストする
- 公開 API のみを使う
- 内部リファクタリングを生き延びる
- HOW（どう動くか）ではなく WHAT（何をするか）を記述する
- 1 テストにつき論理的なアサーションは 1 つ

## 悪いテスト

**実装詳細テスト**: 内部構造に結合している。

```typescript
// BAD: 実装の詳細をテストしている
test("checkout calls paymentService.process", async () => {
  const mockPayment = jest.mock(paymentService);
  await checkout(cart, payment);
  expect(mockPayment.process).toHaveBeenCalledWith(cart.total);
});
```

危険信号:

- 内部の協力オブジェクトをモックしている
- プライベートメソッドをテストしている
- 呼び出し回数・順序をアサートしている
- 振る舞いが変わっていないのにリファクタで壊れる
- テスト名が WHAT ではなく HOW を説明している
- インターフェースを経由せず外部手段（DB 直接クエリ等）で検証している

```typescript
// BAD: インターフェースを迂回して検証している
test("createUser saves to database", async () => {
  await createUser({ name: "Alice" });
  const row = await db.query("SELECT * FROM users WHERE name = ?", ["Alice"]);
  expect(row).toBeDefined();
});

// GOOD: インターフェースを通して検証する
test("createUser makes user retrievable", async () => {
  const user = await createUser({ name: "Alice" });
  const retrieved = await getUser(user.id);
  expect(retrieved.name).toBe("Alice");
});
```

## C1 カバレッジとの関係

C1（分岐カバレッジ）100% は**網羅性の検査**であり、テスト**設計**の指針ではない。
分岐を埋めるためだけにテストを書くのではなく、まず「振る舞い」を表すテストを書き、
その結果として分岐が埋まる状態を目指す。残った未カバー分岐は「まだ記述していない振る舞い」
または「到達不能な分岐（=設計の見直し対象）」のシグナルとして扱う。
