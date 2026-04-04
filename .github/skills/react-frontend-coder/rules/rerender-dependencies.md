---
title: 狭いImpactの依存関係
impact: LOW
impactDescription: エフェクトの再実行を最小限に抑える
tags: rerender, useEffect, dependencies, optimization
---

## 狭いImpactの依存関係

エフェクトの再実行を最小限に抑えるために、オブジェクトではなくプリミティブな依存関係を指定します。

**誤り（ユーザーフィールドが変更されると再実行されます）:**

```tsx
useEffect(() => {
  console.log(user.id)
}, [user])
```

**正しい例（ID が変更された場合に場合のみ再実行）:**

```tsx
useEffect(() => {
  console.log(user.id)
}, [user.id])
```

**派生状態の場合、外部Impactを計算します。**

```tsx
// Incorrect: runs on width=767, 766, 765...
useEffect(() => {
  if (width < 768) {
    enableMobileMode()
  }
}, [width])

// Correct: runs only on boolean transition
const isMobile = width < 768
useEffect(() => {
  if (isMobile) {
    enableMobileMode()
  }
}, [isMobile])
```
