---
title: 予想される水分補給の不一致を抑制
impact: LOW-MEDIUM
impactDescription: 既知の違いに対する騒々しい水分補給警告を回避します
tags: rendering, hydration, ssr, nextjs
---

## 予想される水分補給の不一致を抑制

SSR フレームワーク（Next.js など) では、一部の値がサーバーとクライアントで意図的に異なります（ランダム ID、日付、ロケール/タイムゾーンの形式)。これらの *予想される* 不一致の場合は、要素内のダイナミック テキストを `suppressHydrationWarning` で囲み、ノイズの多い警告が表示されるのを防ぎます。本当のバグを隠すためにこれを使用しないでください。使いすぎないでください。

**誤り（既知の不一致の警告）:**

```tsx
function Timestamp() {
  return <span>{new Date().toLocaleString()}</span>
}
```

**正しい例（予想される不一致のみを抑制）:**

```tsx
function Timestamp() {
  return (
    <span suppressHydrationWarning>
      {new Date().toLocaleString()}
    </span>
  )
}
```
