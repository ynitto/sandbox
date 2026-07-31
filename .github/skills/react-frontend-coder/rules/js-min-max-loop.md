---
title: ソートの代わりに最小/最大のループを使用する
impact: LOW
impactDescription: O(n log n) の代わりに O(n)
tags: javascript, arrays, performance, sorting, algorithms
---

## ソートの代わりに最小/最大のループを使用する

最小または最大の要素を見つけるには、配列を 1 回通過するだけで済みます。並べ替えは無駄が多く、時間がかかります。

**誤り（O(n log n) - 最新のものを見つけるために並べ替えます）:**

```typescript
interface Project {
  id: string
  name: string
  updatedAt: number
}

function getLatestProject(projects: Project[]) {
  const sorted = [...projects].sort((a, b) => b.updatedAt - a.updatedAt)
  return sorted[0]
}
```

最大値を見つけるためだけに配列全体をソートします。

**誤り（O(n log n) - 古い順と新しい順に並べ替えます）:**

```typescript
function getOldestAndNewest(projects: Project[]) {
  const sorted = [...projects].sort((a, b) => a.updatedAt - b.updatedAt)
  return { oldest: sorted[0], newest: sorted[sorted.length - 1] }
}
```

最小/最大のみが必要な場合でも、不必要にソートされます。

**正しい例（O(n) - 単一ループ）:**

```typescript
function getLatestProject(projects: Project[]) {
  if (projects.length === 0) return null
  
  let latest = projects[0]
  
  for (let i = 1; i < projects.length; i++) {
    if (projects[i].updatedAt > latest.updatedAt) {
      latest = projects[i]
    }
  }
  
  return latest
}

function getOldestAndNewest(projects: Project[]) {
  if (projects.length === 0) return { oldest: null, newest: null }
  
  let oldest = projects[0]
  let newest = projects[0]
  
  for (let i = 1; i < projects.length; i++) {
    if (projects[i].updatedAt < oldest.updatedAt) oldest = projects[i]
    if (projects[i].updatedAt > newest.updatedAt) newest = projects[i]
  }
  
  return { oldest, newest }
}
```

配列を 1 回通過し、コピーや並べ替えは行いません。

**代替案（小さな配列の場合は Math.min/Math.max）:**

```typescript
const numbers = [5, 2, 8, 1, 9]
const min = Math.min(...numbers)
const max = Math.max(...numbers)
```

これは小さな配列では機能しますが、スプレッド演算子の制限により、非常に大きな配列では速度が低下したり、単にエラーが発生したりする可能性があります。配列の最大長は、Chrome 143 では約 124000、Safari 18 では約 638000 です。正確な数値は異なる場合があります - [the fiddle](https://jsfiddle.net/qw1jabsx/4/) を参照してください。信頼性を高めるためにループ アプローチを使用します。
