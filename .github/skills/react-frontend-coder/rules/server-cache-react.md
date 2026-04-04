---
title: React.cache() によるリクエストごとの重複排除
impact: MEDIUM
impactDescription: リクエスト内の重複排除
tags: server, cache, react-cache, deduplication
---

## React.cache() によるリクエストごとの重複排除

サーバーサイドのリクエストの重複排除には `React.cache()` を使用します。認証とデータベース クエリが最もメリットをもたらします。

**使用法：**

```typescript
import { cache } from 'react'

export const getCurrentUser = cache(async () => {
  const session = await auth()
  if (!session?.user?.id) return null
  return await db.user.findUnique({
    where: { id: session.user.id }
  })
})
```

1 つのリクエスト内で `getCurrentUser()` を複数回呼び出しても、クエリは 1 回だけ実行されます。

**インラインオブジェクトを引数として使用することは避けてください。**

`React.cache()` は、浅い等価性（`Object.is`) を使用してキャッシュ ヒットを決定します。インライン オブジェクトは呼び出しごとに新しい参照を作成し、キャッシュ ヒットを防ぎます。

**誤り（常にキャッシュミス）:**

```typescript
const getUser = cache(async (params: { uid: number }) => {
  return await db.user.findUnique({ where: { id: params.uid } })
})

// Each call creates new object, never hits cache
getUser({ uid: 1 })
getUser({ uid: 1 })  // Cache miss, runs query again
```

**正しい例（キャッシュヒット）：**

```typescript
const getUser = cache(async (uid: number) => {
  return await db.user.findUnique({ where: { id: uid } })
})

// Primitive args use value equality
getUser(1)
getUser(1)  // Cache hit, returns cached result
```

オブジェクトを渡す必要がある場合は、同じ参照を渡します。

```typescript
const params = { uid: 1 }
getUser(params)  // Query runs
getUser(params)  // Cache hit (same reference)
```

**Next.js 固有の注意:**

Next.js では、`fetch` API がリクエストのメモ化によって自動的に拡張されます。同じ URL とオプションを持つリクエストは 1 つのリクエスト内で自動的に重複排除されるため、`fetch` 呼び出しに `React.cache()` は必要ありません。ただし、`React.cache()` は他の非同期タスクにとって依然として不可欠です。

- データベースクエリ（Prisma、Drizzle など)
- 大量の計算
- 認証チェック
- ファイルシステムの操作
- フェッチ以外の非同期作業

`React.cache()` を使用して、コンポーネント ツリー全体でこれらの操作を重複排除します。

参照: [React.cache documentation](https://react.dev/reference/react/cache)
