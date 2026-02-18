---
title: 安定したコールバック参照用の useEffectEvent
impact: LOW
impactDescription: エフェクトの再実行を防止します
tags: advanced, hooks, useEffectEvent, refs, optimization
---

## 安定したコールバック参照用の useEffectEvent

依存関係の配列に値を追加せずに、コールバック内の最新の値にアクセスします。古いクロージャを回避しながら、エフェクトの再実行を防ぎます。

**誤り（コールバックが変更されるたびにエフェクトが再実行されます）:**

```tsx
function SearchInput({ onSearch }: { onSearch: (q: string) => void }) {
  const [query, setQuery] = useState('')

  useEffect(() => {
    const timeout = setTimeout(() => onSearch(query), 300)
    return () => clearTimeout(timeout)
  }, [query, onSearch])
}
```

**正しい例（React の useEffectEvent を使用）:**

```tsx
import { useEffectEvent } from 'react';

function SearchInput({ onSearch }: { onSearch: (q: string) => void }) {
  const [query, setQuery] = useState('')
  const onSearchEvent = useEffectEvent(onSearch)

  useEffect(() => {
    const timeout = setTimeout(() => onSearchEvent(query), 300)
    return () => clearTimeout(timeout)
  }, [query])
}
```
