---
title: 不変性を実現するには、sort() の代わりに toSorted() を使用します
impact: MEDIUM-HIGH
impactDescription: React 状態での突然変異のバグを防止します
tags: javascript, arrays, immutability, react, state, mutation
---

## 不変性を実現するには、sort() の代わりに toSorted() を使用します

`.sort()` は配列をその場で変更します。これにより、React の状態とプロパティにバグが発生する可能性があります。 `.toSorted()` を使用して、変更のない新しい並べ替えられた配列を作成します。

**誤り（元の配列を変更します）:**

```typescript
function UserList({ users }: { users: User[] }) {
  // Mutates the users prop array!
  const sorted = useMemo(
    () => users.sort((a, b) => a.name.localeCompare(b.name)),
    [users]
  )
  return <div>{sorted.map(renderUser)}</div>
}
```

**正しい例（新しい配列を作成します）:**

```typescript
function UserList({ users }: { users: User[] }) {
  // Creates new sorted array, original unchanged
  const sorted = useMemo(
    () => users.toSorted((a, b) => a.name.localeCompare(b.name)),
    [users]
  )
  return <div>{sorted.map(renderUser)}</div>
}
```

**React においてこれが重要な理由:**

1. Props/state の突然変異は React の不変性モデルを破壊します - React は props と state が読み取り専用として扱われることを期待しています
2. 古いクロージャのバグを引き起こす - クロージャ（コールバック、エフェクト) 内の配列を変更すると、予期しない動作が発生する可能性があります

**ブラウザのサポート（古いブラウザのフォールバック）:**

`.toSorted()` は、すべての最新ブラウザ（Chrome 110 以降、Safari 16 以降、Firefox 115 以降、Node.js 20 以降) で利用できます。古い環境の場合は、スプレッド演算子を使用します。

```typescript
// Fallback for older browsers
const sorted = [...items].sort((a, b) => a.value - b.value)
```

**他の不変配列メソッド:**

- `.toSorted()` - 不変のソート
- `.toReversed()` - 不変のリバース
- `.toSpliced()` - 不変のスプライス
- `.with()` - 不変要素の置換
