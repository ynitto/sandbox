---
title: 戦略的サスペンスの境界線
impact: HIGH
impactDescription: 初期ペイントの高速化
tags: async, suspense, streaming, layout-shift
---

## 戦略的サスペンスの境界線

JSX を返す前に非同期コンポーネントでデータを待つ代わりに、サスペンス境界を使用して、データのロード中にラッパー UI をより速く表示します。

**誤り（データの取得によりラッパーがブロックされました）:**

```tsx
async function Page() {
  const data = await fetchData() // Blocks entire page
  
  return (
    <div>
      <div>Sidebar</div>
      <div>Header</div>
      <div>
        <DataDisplay data={data} />
      </div>
      <div>Footer</div>
    </div>
  )
}
```

中央のセクションだけがデータを必要とする場合でも、レイアウト全体がデータを待機します。

**正しい例（ラッパーはすぐに表示され、データは入力されます）:**

```tsx
function Page() {
  return (
    <div>
      <div>Sidebar</div>
      <div>Header</div>
      <div>
        <Suspense fallback={<Skeleton />}>
          <DataDisplay />
        </Suspense>
      </div>
      <div>Footer</div>
    </div>
  )
}

async function DataDisplay() {
  const data = await fetchData() // Only blocks this component
  return <div>{data.content}</div>
}
```

サイドバー、ヘッダー、フッターはすぐにレンダーされます。 DataDisplay のみがデータを待機します。

**代替案（コンポーネント間で Promise を共有する）:**

```tsx
function Page() {
  // Start fetch immediately, but don't await
  const dataPromise = fetchData()
  
  return (
    <div>
      <div>Sidebar</div>
      <div>Header</div>
      <Suspense fallback={<Skeleton />}>
        <DataDisplay dataPromise={dataPromise} />
        <DataSummary dataPromise={dataPromise} />
      </Suspense>
      <div>Footer</div>
    </div>
  )
}

function DataDisplay({ dataPromise }: { dataPromise: Promise<Data> }) {
  const data = use(dataPromise) // Unwraps the promise
  return <div>{data.content}</div>
}

function DataSummary({ dataPromise }: { dataPromise: Promise<Data> }) {
  const data = use(dataPromise) // Reuses the same promise
  return <div>{data.summary}</div>
}
```

両方のコンポーネントは同じ Promise を共有するため、フェッチは 1 回だけ行われます。両方のコンポーネントが一緒に待機している間、レイアウトはすぐにレンダーされます。

**このパターンを使用しない場合:**

- レイアウトの決定に必要な重要なデータ（位置に影響します)
- SEO に重要なコンテンツをスクロールせずに見える範囲に表示
- サスペンスのオーバーヘッドが価値のない小規模で高速なクエリ
- レイアウトずれ（読み込み→コンテンツジャンプ）を避けたい場合

**トレードオフ:** 初期ペイントの高速化とレイアウト変更の可能性。 UX の優先順位に基づいて選択してください。
