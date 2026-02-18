---
title: 重量コンポーネントの動的インポート
impact: CRITICAL
impactDescription: TTI と LCP に直接影響します
tags: bundle, dynamic-import, code-splitting, next-dynamic
---

## 重量コンポーネントの動的インポート

`next/dynamic` を使用して、最初のレンダーでは必要のない大きなコンポーネントを遅延読み込みします。

**誤り（Monaco バンドルとメイン チャンク ~300KB）:**

```tsx
import { MonacoEditor } from './monaco-editor'

function CodePanel({ code }: { code: string }) {
  return <MonacoEditor value={code} />
}
```

**正しい例（Monaco はオンデマンドでロードされます）:**

```tsx
import dynamic from 'next/dynamic'

const MonacoEditor = dynamic(
  () => import('./monaco-editor').then(m => m.MonacoEditor),
  { ssr: false }
)

function CodePanel({ code }: { code: string }) {
  return <MonacoEditor value={code} />
}
```
