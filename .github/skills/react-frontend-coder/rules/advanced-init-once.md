---
title: マウントごとではなくアプリを 1 回初期化する
impact: LOW-MEDIUM
impactDescription: 開発中の重複した初期化を回避します
tags: initialization, useEffect, app-startup, side-effects
---

## マウントごとではなくアプリを 1 回初期化する

アプリのロードごとに 1 回実行する必要があるアプリ全体の初期化をコンポーネントの `useEffect([])` 内に置かないでください。コンポーネントは再マウントでき、エフェクトは再実行されます。代わりに、エントリ モジュールのモジュール レベルのガードまたはトップレベルの init を使用してください。

**誤り（開発時に 2 回実行され、再マウント時に再実行）:**

```tsx
function Comp() {
  useEffect(() => {
    loadFromStorage()
    checkAuthToken()
  }, [])

  // ...
}
```

**正しい例（アプリのロードごとに 1 回）:**

```tsx
let didInit = false

function Comp() {
  useEffect(() => {
    if (didInit) return
    didInit = true
    loadFromStorage()
    checkAuthToken()
  }, [])

  // ...
}
```

参照: [Initializing the application](https://react.dev/learn/you-might-not-need-an-effect#initializing-the-application)
