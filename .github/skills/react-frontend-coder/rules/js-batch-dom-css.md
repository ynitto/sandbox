---
title: レイアウトのスラッシングを回避する
impact: MEDIUM
impactDescription: 強制的な同期レイアウトを防止し、パフォーマンスのボトルネックを軽減します。
tags: javascript, dom, css, performance, reflow, layout-thrashing
---

## レイアウトのスラッシングを回避する

スタイルの書き込みとレイアウトの読み取りをインターリーブしないでください。スタイルの変更の間にレイアウト プロパティ（`offsetWidth`、`getBoundingClientRect()`、`getComputedStyle()` など) を読み取ると、ブラウザーは強制的に同期リフローをトリガーします。

**これで問題ありません（ブラウザはスタイルの変更をバッチ処理します)。**
```typescript
function updateElementStyles(element: HTMLElement) {
  // Each line invalidates style, but browser batches the recalculation
  element.style.width = '100px'
  element.style.height = '200px'
  element.style.backgroundColor = 'blue'
  element.style.border = '1px solid black'
}
```

**誤り（インターリーブされた読み取りと書き込みによりリフローが強制される）:**
```typescript
function layoutThrashing(element: HTMLElement) {
  element.style.width = '100px'
  const width = element.offsetWidth  // Forces reflow
  element.style.height = '200px'
  const height = element.offsetHeight  // Forces another reflow
}
```

**正しい例（バッチ書き込み、その後 1 回読み取り）:**
```typescript
function updateElementStyles(element: HTMLElement) {
  // Batch all writes together
  element.style.width = '100px'
  element.style.height = '200px'
  element.style.backgroundColor = 'blue'
  element.style.border = '1px solid black'
  
  // Read after all writes are done (single reflow)
  const { width, height } = element.getBoundingClientRect()
}
```

**正しい例（バッチ読み取り、その後書き込み）:**
```typescript
function avoidThrashing(element: HTMLElement) {
  // Read phase - all layout queries first
  const rect1 = element.getBoundingClientRect()
  const offsetWidth = element.offsetWidth
  const offsetHeight = element.offsetHeight
  
  // Write phase - all style changes after
  element.style.width = '100px'
  element.style.height = '200px'
}
```

**より良い: CSS クラスを使用する**
```css
.highlighted-box {
  width: 100px;
  height: 200px;
  background-color: blue;
  border: 1px solid black;
}
```
```typescript
function updateElementStyles(element: HTMLElement) {
  element.classList.add('highlighted-box')
  
  const { width, height } = element.getBoundingClientRect()
}
```

**反応例:**
```tsx
// Incorrect: interleaving style changes with layout queries
function Box({ isHighlighted }: { isHighlighted: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  
  useEffect(() => {
    if (ref.current && isHighlighted) {
      ref.current.style.width = '100px'
      const width = ref.current.offsetWidth // Forces layout
      ref.current.style.height = '200px'
    }
  }, [isHighlighted])
  
  return <div ref={ref}>Content</div>
}

// Correct: toggle class
function Box({ isHighlighted }: { isHighlighted: boolean }) {
  return (
    <div className={isHighlighted ? 'highlighted-box' : ''}>
      Content
    </div>
  )
}
```

可能な場合は、インライン スタイルよりも CSS クラスを優先します。 CSS ファイルはブラウザによってキャッシュされ、クラスにより懸念事項がより適切に分離され、保守が容易になります。

レイアウト強制操作の詳細については、[this gist](https://gist.github.com/paulirish/5d52fb081b3570c81e3a) および [CSS Triggers](https://csstriggers.com/) を参照してください。
