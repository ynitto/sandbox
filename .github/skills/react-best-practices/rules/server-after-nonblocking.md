---
title: 非ブロッキング操作には after() を使用します
impact: MEDIUM
impactDescription: より速い応答時間
tags: server, async, logging, analytics, side-effects
---

## 非ブロッキング操作には after() を使用します

Next.js の `after()` を使用して、応答の送信後に実行する作業をスケジュールします。これにより、ロギング、分析、その他の副作用によって応答がブロックされるのを防ぎます。

**誤り（応答をブロック）:**

```tsx
import { logUserAction } from '@/app/utils'

export async function POST(request: Request) {
  // Perform mutation
  await updateDatabase(request)
  
  // Logging blocks the response
  const userAgent = request.headers.get('user-agent') || 'unknown'
  await logUserAction({ userAgent })
  
  return new Response(JSON.stringify({ status: 'success' }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' }
  })
}
```

**正しい例（非ブロッキング）:**

```tsx
import { after } from 'next/server'
import { headers, cookies } from 'next/headers'
import { logUserAction } from '@/app/utils'

export async function POST(request: Request) {
  // Perform mutation
  await updateDatabase(request)
  
  // Log after response is sent
  after(async () => {
    const userAgent = (await headers()).get('user-agent') || 'unknown'
    const sessionCookie = (await cookies()).get('session-id')?.value || 'anonymous'
    
    logUserAction({ sessionCookie, userAgent })
  })
  
  return new Response(JSON.stringify({ status: 'success' }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' }
  })
}
```

バックグラウンドでロギングが行われている間、応答はすぐに送信されます。

**一般的な使用例:**

- 分析の追跡
- 監査ログ
- 通知の送信
- キャッシュの無効化
- クリーンアップタスク

**重要な注意事項:**

- `after()` は、応答が失敗した場合やリダイレクトされた場合でも実行されます。
- サーバー アクション、ルート ハンドラー、およびサーバー コンポーネントで動作します

参照: [https://nextjs.org/docs/app/api-reference/functions/after](https://nextjs.org/docs/app/api-reference/functions/after)
