---
title: API ルートでのウォーターフォール チェーンの防止
impact: CRITICAL
impactDescription: 2～10倍の改善
tags: api-routes, server-actions, waterfalls, parallelization
---

## API ルートでのウォーターフォール チェーンの防止

API ルートとサーバー アクションでは、まだ待機していなくても、独立した操作をすぐに開始します。

**誤り（構成は認証を待機し、データは両方を待機します）:**

```typescript
export async function GET(request: Request) {
  const session = await auth()
  const config = await fetchConfig()
  const data = await fetchData(session.user.id)
  return Response.json({ data, config })
}
```

**正しい例（認証と構成がすぐに開始されます）:**

```typescript
export async function GET(request: Request) {
  const sessionPromise = auth()
  const configPromise = fetchConfig()
  const session = await sessionPromise
  const [config, data] = await Promise.all([
    configPromise,
    fetchData(session.user.id)
  ])
  return Response.json({ data, config })
}
```

より複雑な依存関係チェーンを持つ操作の場合は、`better-all` を使用して並列処理を自動的に最大化します（依存関係ベースの並列化を参照)。
