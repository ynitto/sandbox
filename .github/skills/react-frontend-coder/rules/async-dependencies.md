---
title: 依存関係に基づく並列化
impact: CRITICAL
impactDescription: 2～10倍の改善
tags: async, parallelization, dependencies, better-all
---

## 依存関係に基づく並列化

部分的な依存関係のある操作の場合は、`better-all` を使用して並列処理を最大化します。各タスクをできるだけ早いタイミングで自動的に開始します。

**誤り（プロファイルは不必要に構成を待機します）:**

```typescript
const [user, config] = await Promise.all([
  fetchUser(),
  fetchConfig()
])
const profile = await fetchProfile(user.id)
```

**正しい例（構成とプロファイルは並行して実行されます）:**

```typescript
import { all } from 'better-all'

const { user, config, profile } = await all({
  async user() { return fetchUser() },
  async config() { return fetchConfig() },
  async profile() {
    return fetchProfile((await this.$.user).id)
  }
})
```

**追加の依存関係のない代替案:**

最初にすべての Promise を作成し、最後に `Promise.all()` を実行することもできます。

```typescript
const userPromise = fetchUser()
const profilePromise = userPromise.then(user => fetchProfile(user.id))

const [user, config, profile] = await Promise.all([
  userPromise,
  fetchConfig(),
  profilePromise
])
```

参照: [https://github.com/shuding/better-all](https://github.com/shuding/better-all)
