---
title: 遅延状態の初期化を使用する
impact: MEDIUM
impactDescription: レンダーごとに無駄な計算が行われる
tags: react, hooks, useState, performance, initialization
---

## 遅延状態の初期化を使用する

高価な初期値を得るには、関数を `useState` に渡します。関数形式を使用しない場合、値が 1 回しか使用されない場合でも、初期化子はレンダーのたびに実行されます。

**誤り（レンダーごとに実行）:**

```tsx
function FilteredList({ items }: { items: Item[] }) {
  // buildSearchIndex() runs on EVERY render, even after initialization
  const [searchIndex, setSearchIndex] = useState(buildSearchIndex(items))
  const [query, setQuery] = useState('')
  
  // When query changes, buildSearchIndex runs again unnecessarily
  return <SearchResults index={searchIndex} query={query} />
}

function UserProfile() {
  // JSON.parse runs on every render
  const [settings, setSettings] = useState(
    JSON.parse(localStorage.getItem('settings') || '{}')
  )
  
  return <SettingsForm settings={settings} onChange={setSettings} />
}
```

**正しい例（初回のみ実行）:**

```tsx
function FilteredList({ items }: { items: Item[] }) {
  // buildSearchIndex() runs ONLY on initial render
  const [searchIndex, setSearchIndex] = useState(() => buildSearchIndex(items))
  const [query, setQuery] = useState('')
  
  return <SearchResults index={searchIndex} query={query} />
}

function UserProfile() {
  // JSON.parse runs only on initial render
  const [settings, setSettings] = useState(() => {
    const stored = localStorage.getItem('settings')
    return stored ? JSON.parse(stored) : {}
  })
  
  return <SettingsForm settings={settings} onChange={setSettings} />
}
```

`localStorage` / `sessionStorage` から初期値を計算するとき、データ構造（インデックス、マップ) を構築するとき、DOM から読み取るとき、または大量の変換を実行するときに、遅延初期化を使用します。

単純なプリミティブ（`useState(0)`)、直接参照（`useState(props.value)`)、または安価なリテラル（`useState({})`) の場合、関数形式は不要です。
