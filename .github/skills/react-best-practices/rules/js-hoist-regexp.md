---
title: ホイスト正規表現の作成
impact: LOW-MEDIUM
impactDescription: レクリエーションを避ける
tags: javascript, regexp, optimization, memoization
---

## ホイスト正規表現の作成

レンダー内で RegExp を作成しないでください。モジュールスコープにホイストするか、`useMemo()` でメモ化します。

**誤り（レンダーごとに新しい RegExp）:**

```tsx
function Highlighter({ text, query }: Props) {
  const regex = new RegExp(`(${query})`, 'gi')
  const parts = text.split(regex)
  return <>{parts.map((part, i) => ...)}</>
}
```

**正しい例（メモ化またはホイスト）:**

```tsx
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function Highlighter({ text, query }: Props) {
  const regex = useMemo(
    () => new RegExp(`(${escapeRegex(query)})`, 'gi'),
    [query]
  )
  const parts = text.split(regex)
  return <>{parts.map((part, i) => ...)}</>
}
```

**警告（グローバル正規表現には変更可能な状態があります）:**

グローバル正規表現（`/g`) には変更可能な `lastIndex` 状態があります。

```typescript
const regex = /foo/g
regex.test('foo')  // true, lastIndex = 3
regex.test('foo')  // false, lastIndex = 0
```
