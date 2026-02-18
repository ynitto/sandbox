---
title: イベントハンドラーにインタラクションロジックを組み込む
impact: MEDIUM
impactDescription: エフェクトの再実行と副作用の重複を回避します
tags: rerender, useEffect, events, side-effects, dependencies
---

## イベントハンドラーにインタラクションロジックを組み込む

特定のユーザー アクション（送信、クリック、ドラッグ) によって副作用がトリガーされた場合は、そのイベント ハンドラーで実行します。アクションを状態 + Impactとしてモデル化しないでください。無関係な変更に対してエフェクトが再実行され、アクションが重複する可能性があります。

**誤り（状態 + Impactとしてモデル化されたイベント）:**

```tsx
function Form() {
  const [submitted, setSubmitted] = useState(false)
  const theme = useContext(ThemeContext)

  useEffect(() => {
    if (submitted) {
      post('/api/register')
      showToast('Registered', theme)
    }
  }, [submitted, theme])

  return <button onClick={() => setSubmitted(true)}>Submit</button>
}
```

**正しい例（ハンドラー内で実行します）:**

```tsx
function Form() {
  const theme = useContext(ThemeContext)

  function handleSubmit() {
    post('/api/register')
    showToast('Registered', theme)
  }

  return <button onClick={handleSubmit}>Submit</button>
}
```

参照: [Should this code move to an event handler?](https://react.dev/learn/removing-effect-dependencies#should-this-code-move-to-an-event-handler)
