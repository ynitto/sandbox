---
title: SVG要素の代わりにSVGラッパーをアニメーション化する
impact: LOW
impactDescription: ハードウェアアクセラレーションを有効にする
tags: rendering, svg, css, animation, performance
---

## SVG要素の代わりにSVGラッパーをアニメーション化する

多くのブラウザには、SVG 要素の CSS3 アニメーション用のハードウェア アクセラレーションがありません。 SVG を `<div>` でラップし、代わりにラッパーをアニメーション化します。

**誤り（SVG を直接アニメーション化 - ハードウェア アクセラレーションなし）:**

```tsx
function LoadingSpinner() {
  return (
    <svg 
      className="animate-spin"
      width="24" 
      height="24" 
      viewBox="0 0 24 24"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" />
    </svg>
  )
}
```

**正しい例（アニメーション化ラッパー div - ハードウェア アクセラレーション）:**

```tsx
function LoadingSpinner() {
  return (
    <div className="animate-spin">
      <svg 
        width="24" 
        height="24" 
        viewBox="0 0 24 24"
      >
        <circle cx="12" cy="12" r="10" stroke="currentColor" />
      </svg>
    </div>
  )
}
```

これは、すべての CSS 変換とトランジション（`transform`、`opacity`、`translate`、`scale`、`rotate`) に適用されます。ラッパー div を使用すると、ブラウザーで GPU アクセラレーションを使用してアニメーションをよりスムーズにできるようになります。
