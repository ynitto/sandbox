---
title: コンポーネント構成による並列データフェッチ
impact: CRITICAL
impactDescription: サーバーサイドのウォーターフォールを排除します
tags: server, rsc, parallel-fetching, composition
---

## コンポーネント構成による並列データフェッチ

React サーバー コンポーネントはツリー内で順番に実行されます。データフェッチを並列化するために合成を使用して再構築します。

**誤り（サイドバーはページのフェッチが完了するまで待機します）:**

```tsx
export default async function Page() {
  const header = await fetchHeader()
  return (
    <div>
      <div>{header}</div>
      <Sidebar />
    </div>
  )
}

async function Sidebar() {
  const items = await fetchSidebarItems()
  return <nav>{items.map(renderItem)}</nav>
}
```

**正しい例（両方を同時にフェッチする）:**

```tsx
async function Header() {
  const data = await fetchHeader()
  return <div>{data}</div>
}

async function Sidebar() {
  const items = await fetchSidebarItems()
  return <nav>{items.map(renderItem)}</nav>
}

export default function Page() {
  return (
    <div>
      <Header />
      <Sidebar />
    </div>
  )
}
```

**子プロップを使用した代替案:**

```tsx
async function Header() {
  const data = await fetchHeader()
  return <div>{data}</div>
}

async function Sidebar() {
  const items = await fetchSidebarItems()
  return <nav>{items.map(renderItem)}</nav>
}

function Layout({ children }: { children: ReactNode }) {
  return (
    <div>
      <Header />
      {children}
    </div>
  )
}

export default function Page() {
  return (
    <Layout>
      <Sidebar />
    </Layout>
  )
}
```
