---
title: 機能的なsetState更新を使用する
impact: MEDIUM
impactDescription: 古いクロージャや不要なコールバックの再作成を防ぎます。
tags: react, hooks, useState, useCallback, callbacks, closures
---

## 機能的なsetState更新を使用する

現在の状態値に基づいて状態を更新する場合は、状態変数を直接参照するのではなく、setState の関数更新形式を使用します。これにより、古いクロージャが防止され、不要な依存関係が排除され、安定したコールバック参照が作成されます。

**誤り（依存関係として状態が必要）:**

```tsx
function TodoList() {
  const [items, setItems] = useState(initialItems)
  
  // Callback must depend on items, recreated on every items change
  const addItems = useCallback((newItems: Item[]) => {
    setItems([...items, ...newItems])
  }, [items])  // ❌ items dependency causes recreations
  
  // Risk of stale closure if dependency is forgotten
  const removeItem = useCallback((id: string) => {
    setItems(items.filter(item => item.id !== id))
  }, [])  // ❌ Missing items dependency - will use stale items!
  
  return <ItemsEditor items={items} onAdd={addItems} onRemove={removeItem} />
}
```

最初のコールバックは `items` が変更されるたびに再作成されるため、子コンポーネントが不必要に再レンダーされる可能性があります。 2 番目のコールバックには古いクロージャのバグがあり、常に初期の `items` 値を参照します。

**正しい例（安定したコールバック、古いクロージャなし）:**

```tsx
function TodoList() {
  const [items, setItems] = useState(initialItems)
  
  // Stable callback, never recreated
  const addItems = useCallback((newItems: Item[]) => {
    setItems(curr => [...curr, ...newItems])
  }, [])  // ✅ No dependencies needed
  
  // Always uses latest state, no stale closure risk
  const removeItem = useCallback((id: string) => {
    setItems(curr => curr.filter(item => item.id !== id))
  }, [])  // ✅ Safe and stable
  
  return <ItemsEditor items={items} onAdd={addItems} onRemove={removeItem} />
}
```

**利点：**

1. **安定したコールバック参照** - 状態が変化したときにコールバックを再作成する必要はありません
2. **古いクロージャはありません** - 常に最新の状態値で動作します
3. **依存関係の減少** - 依存関係の配列を簡素化し、メモリ リークを削減します。
4. **バグの防止** - React クロージャーのバグの最も一般的な原因を排除します。

**機能アップデートを使用する場合:**

- 現在の状態値に依存する任意の setState
- useCallback/useMemo 内で状態が必要な場合
- 状態を参照するイベント ハンドラー
- 状態を更新する非同期操作

**直接更新が問題ない場合:**

- 状態を静的な値に設定: `setCount(0)`
- プロパティ/引数のみから状態を設定: `setName(newName)`
- 状態は以前の値に依存しません

**注意:** プロジェクトで [React Compiler](https://react.dev/learn/react-compiler) が有効になっている場合、コンパイラは場合によっては自動的に最適化できますが、正確性を確保し、古いクロージャのバグを防ぐために、機能の更新を行うことをお勧めします。
