---
title: 独立した操作のための Promise.all()
impact: CRITICAL
impactDescription: 2～10倍の改善
tags: async, parallelization, promises, waterfalls
---

## 独立した操作のための Promise.all()

非同期操作に相互依存関係がない場合は、`Promise.all()` を使って同時に実行します。

**誤り（順次実行、3 往復）:**

```typescript
const user = await fetchUser()
const posts = await fetchPosts()
const comments = await fetchComments()
```

**正しい例（並列実行、1 往復）:**

```typescript
const [user, posts, comments] = await Promise.all([
  fetchUser(),
  fetchPosts(),
  fetchComments()
])
```
