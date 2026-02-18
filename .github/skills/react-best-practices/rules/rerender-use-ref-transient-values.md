---
title: 一時的な値には useRef を使用する
impact: MEDIUM
impactDescription: 頻繁な更新時に不必要な再レンダーを回避します
tags: rerender, useref, state, performance
---

## 一時的な値には useRef を使用する

値が頻繁に変更され、更新のたびに再レンダーしたくない場合（マウス トラッカー、間隔、一時的なフラグなど)、値を `useState` ではなく `useRef` に保存します。 UI のコンポーネントの状態を保持します。一時的な DOM に隣接する値には refs を使用します。 ref を更新しても再レンダーはトリガーされません。

**誤り（更新ごとにレンダー）:**

```tsx
function Tracker() {
  const [lastX, setLastX] = useState(0)

  useEffect(() => {
    const onMove = (e: MouseEvent) => setLastX(e.clientX)
    window.addEventListener('mousemove', onMove)
    return () => window.removeEventListener('mousemove', onMove)
  }, [])

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: lastX,
        width: 8,
        height: 8,
        background: 'black',
      }}
    />
  )
}
```

**正しい例（追跡のための再レンダーはありません）:**

```tsx
function Tracker() {
  const lastXRef = useRef(0)
  const dotRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      lastXRef.current = e.clientX
      const node = dotRef.current
      if (node) {
        node.style.transform = `translateX(${e.clientX}px)`
      }
    }
    window.addEventListener('mousemove', onMove)
    return () => window.removeEventListener('mousemove', onMove)
  }, [])

  return (
    <div
      ref={dotRef}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        width: 8,
        height: 8,
        background: 'black',
        transform: 'translateX(0px)',
      }}
    />
  )
}
```
