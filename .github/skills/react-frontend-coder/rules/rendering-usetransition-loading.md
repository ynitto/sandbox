---
title: useTransition Over Manual Loading States を使用する
impact: LOW
impactDescription: 再レンダーが減り、コードの明瞭さが向上します。
tags: rendering, transitions, useTransition, loading, state
---

## useTransition Over Manual Loading States を使用する

状態をロードするには、手動の `useState` の代わりに `useTransition` を使用します。これにより、組み込みの `isPending` 状態が提供され、遷移が自動的に管理されます。

**誤り（手動ロード状態）:**

```tsx
function SearchResults() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [isLoading, setIsLoading] = useState(false)

  const handleSearch = async (value: string) => {
    setIsLoading(true)
    setQuery(value)
    const data = await fetchResults(value)
    setResults(data)
    setIsLoading(false)
  }

  return (
    <>
      <input onChange={(e) => handleSearch(e.target.value)} />
      {isLoading && <Spinner />}
      <ResultsList results={results} />
    </>
  )
}
```

**正しい例（組み込みの保留状態で Transition を使用）:**

```tsx
import { useTransition, useState } from 'react'

function SearchResults() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [isPending, startTransition] = useTransition()

  const handleSearch = (value: string) => {
    setQuery(value) // Update input immediately
    
    startTransition(async () => {
      // Fetch and update results
      const data = await fetchResults(value)
      setResults(data)
    })
  }

  return (
    <>
      <input onChange={(e) => handleSearch(e.target.value)} />
      {isPending && <Spinner />}
      <ResultsList results={results} />
    </>
  )
}
```

**利点：**

- **自動保留状態**: `setIsLoading(true/false)` を手動で管理する必要はありません
- **エラー耐性**: 遷移がスローされた場合でも、保留状態は正しくリセットされます。
- **応答性の向上**: 更新中に UI の応答性を維持します。
- **割り込み処理**: 新しいトランジションは保留中のトランジションを自動的にキャンセルします

参照: [useTransition](https://react.dev/reference/react/useTransition)
