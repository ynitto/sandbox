---
title: 状態読み取りを使用ポイントまで延期する
impact: MEDIUM
impactDescription: 不必要なサブスクリプションを回避します
tags: rerender, searchParams, localStorage, optimization
---

## 状態読み取りを使用ポイントまで延期する

コールバック内でのみ読み取る場合は、動的状態（searchParams、localStorage) をサブスクライブしないでください。

**誤り（すべての searchParams の変更をサブスクライブします）:**

```tsx
function ShareButton({ chatId }: { chatId: string }) {
  const searchParams = useSearchParams()

  const handleShare = () => {
    const ref = searchParams.get('ref')
    shareChat(chatId, { ref })
  }

  return <button onClick={handleShare}>Share</button>
}
```

**正しい例（オンデマンドで読み取り、サブスクリプションなし）:**

```tsx
function ShareButton({ chatId }: { chatId: string }) {
  const handleShare = () => {
    const params = new URLSearchParams(window.location.search)
    const ref = params.get('ref')
    shareChat(chatId, { ref })
  }

  return <button onClick={handleShare}>Share</button>
}
```
