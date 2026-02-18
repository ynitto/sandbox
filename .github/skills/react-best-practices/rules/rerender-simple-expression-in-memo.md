---
title: useMemo では単純な式をプリミティブな結果型でラップしないでください。
impact: LOW-MEDIUM
impactDescription: レンダーごとに無駄な計算が行われる
tags: rerender, useMemo, optimization
---

## useMemo では単純な式をプリミティブな結果型でラップしないでください。

式が単純で（論理演算子や算術演算子がほとんどない)、プリミティブな結果タイプ（ブール値、数値、文字列) を持つ場合は、それを `useMemo` でラップしないでください。
`useMemo` を呼び出してフックの依存関係を比較すると、式自体よりも多くのリソースが消費される可能性があります。

**正しくない：**

```tsx
function Header({ user, notifications }: Props) {
  const isLoading = useMemo(() => {
    return user.isLoading || notifications.isLoading
  }, [user.isLoading, notifications.isLoading])

  if (isLoading) return <Skeleton />
  // return some markup
}
```

**正しい：**

```tsx
function Header({ user, notifications }: Props) {
  const isLoading = user.isLoading || notifications.isLoading

  if (isLoading) return <Skeleton />
  // return some markup
}
```
