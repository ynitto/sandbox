---
title: ホイスト静的 JSX 要素
impact: LOW
impactDescription: 再作成を避ける
tags: rendering, jsx, static, optimization
---

## ホイスト静的 JSX 要素

再作成を避けるためにコンポーネントの外部に静的 JSX を抽出します。

**誤り（レンダーごとに要素を再作成します）:**

```tsx
function LoadingSkeleton() {
  return <div className="animate-pulse h-20 bg-gray-200" />
}

function Container() {
  return (
    <div>
      {loading && <LoadingSkeleton />}
    </div>
  )
}
```

**正しい例（同じ要素を再利用します）:**

```tsx
const loadingSkeleton = (
  <div className="animate-pulse h-20 bg-gray-200" />
)

function Container() {
  return (
    <div>
      {loading && loadingSkeleton}
    </div>
  )
}
```

これは、レンダーのたびに再作成するとコストがかかる可能性がある、大規模で静的な SVG ノードの場合に特に役立ちます。

**注意:** プロジェクトで [React Compiler](https://react.dev/learn/react-compiler) が有効になっている場合、コンパイラは静的 JSX 要素を自動的にホイストし、コンポーネントの再レンダーを最適化するため、手動でのホイストは不要になります。
