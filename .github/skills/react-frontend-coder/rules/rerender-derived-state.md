---
title: 派生状態を購読する
impact: MEDIUM
impactDescription: 再レンダーの頻度を減らす
tags: rerender, derived-state, media-query, optimization
---

## 派生状態を購読する

再レンダーの頻度を減らすために、連続値の代わりに派生ブール状態をサブスクライブします。

**誤り（ピクセルが変更されるたびに再レンダー）:**

```tsx
function Sidebar() {
  const width = useWindowWidth()  // updates continuously
  const isMobile = width < 768
  return <nav className={isMobile ? 'mobile' : 'desktop'} />
}
```

**正しい例（ブール値が変更された場合にのみ再レンダーされます）:**

```tsx
function Sidebar() {
  const isMobile = useMediaQuery('(max-width: 767px)')
  return <nav className={isMobile ? 'mobile' : 'desktop'} />
}
```
