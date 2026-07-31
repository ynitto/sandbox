---
title: アクティビティコンポーネントを使用して表示/非表示を切り替えます
impact: MEDIUM
impactDescription: 状態/DOMを保持します
tags: rendering, activity, visibility, state-preservation
---

## アクティビティコンポーネントを使用して表示/非表示を切り替えます

React の `<Activity>` を使用して、可視性を頻繁に切り替える高価なコンポーネントの状態/DOM を保存します。

**使用法：**

```tsx
import { Activity } from 'react'

function Dropdown({ isOpen }: Props) {
  return (
    <Activity mode={isOpen ? 'visible' : 'hidden'}>
      <ExpensiveMenu />
    </Activity>
  )
}
```

コストのかかる再レンダーと状態の損失を回避します。
