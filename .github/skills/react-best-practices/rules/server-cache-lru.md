---
title: クロスリクエスト LRU キャッシング
impact: HIGH
impactDescription: リクエスト間でのキャッシュ
tags: server, cache, lru, cross-request
---

## クロスリクエスト LRU キャッシング

`React.cache()` は 1 つのリクエスト内でのみ機能します。連続したリクエスト（ユーザーがボタン A をクリックしてからボタン B をクリック) 間で共有されるデータの場合は、LRU キャッシュを使用します。

**実装：**

```typescript
import { LRUCache } from 'lru-cache'

const cache = new LRUCache<string, any>({
  max: 1000,
  ttl: 5 * 60 * 1000  // 5 minutes
})

export async function getUser(id: string) {
  const cached = cache.get(id)
  if (cached) return cached

  const user = await db.user.findUnique({ where: { id } })
  cache.set(id, user)
  return user
}

// Request 1: DB query, result cached
// Request 2: cache hit, no DB query
```

連続したユーザーアクションが数秒以内に同じデータを必要とする複数のエンドポイントにヒットする場合に使用します。

**Vercel の [Fluid Compute](https://vercel.com/docs/fluid-compute) を使用する場合:** 複数の同時リクエストが同じ関数インスタンスとキャッシュを共有できるため、LRU キャッシュは特にImpact的です。これは、Redis などの外部ストレージを必要とせずに、キャッシュがリクエスト間で持続することを意味します。

**従来のサーバーレスの場合:** 各呼び出しは分離して実行されるため、クロスプロセス キャッシュには Redis を検討してください。

参照: [https://github.com/isaacs/node-lru-cache](https://github.com/isaacs/node-lru-cache)
