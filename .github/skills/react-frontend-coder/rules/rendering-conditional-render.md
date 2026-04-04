---
title: 明示的な条件付きレンダーを使用する
impact: LOW
impactDescription: 0 または NaN のレンダーを防止します
tags: rendering, conditional, jsx, falsy-values
---

## 明示的な条件付きレンダーを使用する

条件が `0`、`NaN`、またはレンダーされるその他の偽の値である可能性がある場合は、条件付きレンダーに `&&` の代わりに明示的な三項演算子（`? :`) を使用します。

**誤り（カウントが 0 の場合は「0」をレンダーします）:**

```tsx
function Badge({ count }: { count: number }) {
  return (
    <div>
      {count && <span className="badge">{count}</span>}
    </div>
  )
}

// When count = 0, renders: <div>0</div>
// When count = 5, renders: <div><span class="badge">5</span></div>
```

**正しい例（カウントが 0 の場合は何も表示されません）:**

```tsx
function Badge({ count }: { count: number }) {
  return (
    <div>
      {count > 0 ? <span className="badge">{count}</span> : null}
    </div>
  )
}

// When count = 0, renders: <div></div>
// When count = 5, renders: <div><span class="badge">5</span></div>
```
