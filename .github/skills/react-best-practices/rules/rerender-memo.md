---
title: メモ化されたコンポーネントへの抽出
impact: MEDIUM
impactDescription: 早期返品を可能にする
tags: rerender, memo, useMemo, optimization
---

## メモ化されたコンポーネントへの抽出

コストのかかる作業をメモ化されたコンポーネントに抽出して、計算前の早期リターンを可能にします。

**誤り（読み込み中もアバターを計算します）:**

```tsx
function Profile({ user, loading }: Props) {
  const avatar = useMemo(() => {
    const id = computeAvatarId(user)
    return <Avatar id={id} />
  }, [user])

  if (loading) return <Skeleton />
  return <div>{avatar}</div>
}
```

**正しい例（ロード時に計算をスキップします）:**

```tsx
const UserAvatar = memo(function UserAvatar({ user }: { user: User }) {
  const id = useMemo(() => computeAvatarId(user), [user])
  return <Avatar id={id} />
})

function Profile({ user, loading }: Props) {
  if (loading) return <Skeleton />
  return (
    <div>
      <UserAvatar user={user} />
    </div>
  )
}
```

**注意:** プロジェクトで [React Compiler](https://react.dev/learn/react-compiler) が有効になっている場合、`memo()` および `useMemo()` による手動メモ化は必要ありません。コンパイラは再レンダーを自動的に最適化します。
