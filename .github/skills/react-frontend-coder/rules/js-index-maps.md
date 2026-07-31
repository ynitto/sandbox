---
title: 繰り返し検索するためのインデックス マップを構築する
impact: LOW-MEDIUM
impactDescription: 100 万オペレーションから 2,000 オペレーション
tags: javascript, map, indexing, optimization, performance
---

## 繰り返し検索するためのインデックス マップを構築する

同じキーによる複数の `.find()` 呼び出しでは、Map を使用する必要があります。

**誤り（検索ごとに O(n)）:**

```typescript
function processOrders(orders: Order[], users: User[]) {
  return orders.map(order => ({
    ...order,
    user: users.find(u => u.id === order.userId)
  }))
}
```

**正しい例（検索ごとに O(1)）:**

```typescript
function processOrders(orders: Order[], users: User[]) {
  const userById = new Map(users.map(u => [u.id, u]))

  return orders.map(order => ({
    ...order,
    user: userById.get(order.userId)
  }))
}
```

マップを 1 回構築すると（O(n))、その後はすべてのルックアップが O(1) になります。
1000 オーダー × 1000 ユーザーの場合: 100 万オペレーション → 2K オペレーション。
