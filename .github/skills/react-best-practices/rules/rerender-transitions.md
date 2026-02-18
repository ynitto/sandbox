---
title: 緊急でない更新にはトランジションを使用する
impact: MEDIUM
impactDescription: UIの応答性を維持します
tags: rerender, transitions, startTransition, performance
---

## 緊急でない更新にはトランジションを使用する

UI の応答性を維持するために、頻繁で緊急ではない状態更新を遷移としてマークします。

**誤り（スクロールごとに UI をブロック）:**

```tsx
function ScrollTracker() {
  const [scrollY, setScrollY] = useState(0)
  useEffect(() => {
    const handler = () => setScrollY(window.scrollY)
    window.addEventListener('scroll', handler, { passive: true })
    return () => window.removeEventListener('scroll', handler)
  }, [])
}
```

**正しい例（非ブロッキング更新）:**

```tsx
import { startTransition } from 'react'

function ScrollTracker() {
  const [scrollY, setScrollY] = useState(0)
  useEffect(() => {
    const handler = () => {
      startTransition(() => setScrollY(window.scrollY))
    }
    window.addEventListener('scroll', handler, { passive: true })
    return () => window.removeEventListener('scroll', handler)
  }, [])
}
```
