---
title: イベント ハンドラーを Refs に保存する
impact: LOW
impactDescription: 安定したサブスクリプション
tags: advanced, hooks, refs, event-handlers, optimization
---

## イベント ハンドラーを Refs に保存する

コールバックの変更時に再サブスクライブすべきでないエフェクトで使用される場合は、コールバックを refs に保存します。

**誤り（レンダーごとに再サブスクライブ）:**

```tsx
function useWindowEvent(event: string, handler: (e) => void) {
  useEffect(() => {
    window.addEventListener(event, handler)
    return () => window.removeEventListener(event, handler)
  }, [event, handler])
}
```

**正しい例（安定したサブスクリプション）:**

```tsx
function useWindowEvent(event: string, handler: (e) => void) {
  const handlerRef = useRef(handler)
  useEffect(() => {
    handlerRef.current = handler
  }, [handler])

  useEffect(() => {
    const listener = (e) => handlerRef.current(e)
    window.addEventListener(event, listener)
    return () => window.removeEventListener(event, listener)
  }, [event])
}
```

**代替案: 最新の React を使用している場合は、`useEffectEvent` を使用します。**

```tsx
import { useEffectEvent } from 'react'

function useWindowEvent(event: string, handler: (e) => void) {
  const onEvent = useEffectEvent(handler)

  useEffect(() => {
    window.addEventListener(event, onEvent)
    return () => window.removeEventListener(event, onEvent)
  }, [event])
}
```

`useEffectEvent` は、同じパターンに対してよりクリーンな API を提供します。これは、常に最新バージョンのハンドラーを呼び出す安定した関数参照を作成します。
