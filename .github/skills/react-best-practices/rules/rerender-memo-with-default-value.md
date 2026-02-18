---

title: メモ化されたコンポーネントからデフォルトの非プリミティブパラメータ値を抽出して定数にする
impact: MEDIUM
impactDescription: デフォルト値に定数を使用してメモ化を復元します
tags: rerender, memo, optimization

---

## メモ化されたコンポーネントからデフォルトの非プリミティブパラメータ値を抽出して定数にする

メモ化されたコンポーネントに配列、関数、オブジェクトなどの非プリミティブなオプション パラメーターのデフォルト値がある場合、そのパラメーターなしでコンポーネントを呼び出すとメモ化が壊れます。これは、再レンダーのたびに新しい値のインスタンスが作成され、`memo()` での厳密な等価比較に合格しないためです。

この問題に対処するには、デフォルト値を定数に抽出します。

**誤り（`onClick` は再レンダーごとに異なる値を持ちます）:**

```tsx
const UserAvatar = memo(function UserAvatar({ onClick = () => {} }: { onClick?: () => void }) {
  // ...
})

// Used without optional onClick
<UserAvatar />
```

**正しい例（安定したデフォルト値）:**

```tsx
const NOOP = () => {};

const UserAvatar = memo(function UserAvatar({ onClick = NOOP }: { onClick?: () => void }) {
  // ...
})

// Used without optional onClick
<UserAvatar />
```
