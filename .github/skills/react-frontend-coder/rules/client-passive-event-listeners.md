---
title: スクロールパフォーマンスにパッシブイベントリスナーを使用する
impact: MEDIUM
impactDescription: イベントリスナーによるスクロール遅延を解消します。
tags: client, event-listeners, scrolling, performance, touch, wheel
---

## スクロールパフォーマンスにパッシブイベントリスナーを使用する

`{ passive: true }` をタッチおよびホイール イベント リスナーに追加して、即時スクロールを有効にします。ブラウザは通常、リスナーが `preventDefault()` が呼び出されているかどうかの確認を完了するまで待機するため、スクロール遅延が発生します。

**正しくない：**

```typescript
useEffect(() => {
  const handleTouch = (e: TouchEvent) => console.log(e.touches[0].clientX)
  const handleWheel = (e: WheelEvent) => console.log(e.deltaY)
  
  document.addEventListener('touchstart', handleTouch)
  document.addEventListener('wheel', handleWheel)
  
  return () => {
    document.removeEventListener('touchstart', handleTouch)
    document.removeEventListener('wheel', handleWheel)
  }
}, [])
```

**正しい：**

```typescript
useEffect(() => {
  const handleTouch = (e: TouchEvent) => console.log(e.touches[0].clientX)
  const handleWheel = (e: WheelEvent) => console.log(e.deltaY)
  
  document.addEventListener('touchstart', handleTouch, { passive: true })
  document.addEventListener('wheel', handleWheel, { passive: true })
  
  return () => {
    document.removeEventListener('touchstart', handleTouch)
    document.removeEventListener('wheel', handleWheel)
  }
}, [])
```

**パッシブは次の場合に使用します。** 追跡/分析、ロギング、`preventDefault()` を呼び出さないリスナー。

**次の場合にはパッシブを使用しないでください。** カスタム スワイプ ジェスチャ、カスタム ズーム コントロール、または `preventDefault()` を必要とするリスナーを実装する場合。
