---
title: 長いリストの CSS コンテンツの可視性
impact: HIGH
impactDescription: 初期レンダーの高速化
tags: rendering, css, content-visibility, long-lists
---

## 長いリストの CSS コンテンツの可視性

`content-visibility: auto` を適用して、オフスクリーン レンダーを延期します。

**CSS:**

```css
.message-item {
  content-visibility: auto;
  contain-intrinsic-size: 0 80px;
}
```

**例：**

```tsx
function MessageList({ messages }: { messages: Message[] }) {
  return (
    <div className="overflow-y-auto h-screen">
      {messages.map(msg => (
        <div key={msg.id} className="message-item">
          <Avatar user={msg.author} />
          <div>{msg.content}</div>
        </div>
      ))}
    </div>
  )
}
```

メッセージが 1000 件の場合、ブラウザーは最大 990 個のオフスクリーン アイテムのレイアウト/ペイントをスキップします（初期レンダーが 10 倍高速になります)。
