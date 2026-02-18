---
title: レンダー中に派生状態を計算する
impact: MEDIUM
impactDescription: 冗長なレンダーと状態のドリフトを回避します
tags: rerender, derived-state, useEffect, state
---

## レンダー中に派生状態を計算する

現在のプロパティ/ステートから値を計算できる場合は、それをステートに保存したり、エフェクトで更新したりしないでください。余分なレンダーや状態のドリフトを避けるために、レンダー中にこれを派生させます。プロップの変更のみに応答してエフェクトの状態を設定しないでください。代わりに、派生値またはキー付きリセットを優先します。

**誤り（冗長な状態とImpact）:**

```tsx
function Form() {
  const [firstName, setFirstName] = useState('First')
  const [lastName, setLastName] = useState('Last')
  const [fullName, setFullName] = useState('')

  useEffect(() => {
    setFullName(firstName + ' ' + lastName)
  }, [firstName, lastName])

  return <p>{fullName}</p>
}
```

**正しい例（レンダー中に派生）:**

```tsx
function Form() {
  const [firstName, setFirstName] = useState('First')
  const [lastName, setLastName] = useState('Last')
  const fullName = firstName + ' ' + lastName

  return <p>{fullName}</p>
}
```

参考文献: [You Might Not Need an Effect](https://react.dev/learn/you-might-not-need-an-effect)
