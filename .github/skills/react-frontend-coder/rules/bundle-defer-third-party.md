---
title: クリティカルでないサードパーティ ライブラリの延期
impact: MEDIUM
impactDescription: 水分補給後の負荷
tags: bundle, third-party, analytics, defer
---

## クリティカルでないサードパーティ ライブラリの延期

分析、ログ記録、エラー追跡はユーザーの操作をブロックしません。水分補給後にロードしてください。

**誤り（初期バンドルをブロック）:**

```tsx
import { Analytics } from '@vercel/analytics/react'

export default function RootLayout({ children }) {
  return (
    <html>
      <body>
        {children}
        <Analytics />
      </body>
    </html>
  )
}
```

**正しい例（水分補給後の負荷）：**

```tsx
import dynamic from 'next/dynamic'

const Analytics = dynamic(
  () => import('@vercel/analytics/react').then(m => m.Analytics),
  { ssr: false }
)

export default function RootLayout({ children }) {
  return (
    <html>
      <body>
        {children}
        <Analytics />
      </body>
    </html>
  )
}
```
