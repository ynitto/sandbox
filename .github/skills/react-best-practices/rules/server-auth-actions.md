---
title: API ルートなどのサーバー アクションを認証する
impact: CRITICAL
impactDescription: サーバーの突然変異への不正アクセスを防止します
tags: server, server-actions, authentication, security, authorization
---

## API ルートなどのサーバー アクションを認証する

**Impact:** クリティカル（サーバーの突然変異への不正アクセスを防止）**

サーバー アクション（`"use server"` の関数) は、API ルートと同様に、パブリック エンドポイントとして公開されます。認証と認可は常に各サーバー アクションの**内部**で検証してください。サーバー アクションは直接呼び出すことができるため、ミドルウェア、レイアウト ガード、またはページ レベルのチェックだけに依存しないでください。

Next.js のドキュメントには、「サーバー アクションを公開 API エンドポイントと同じセキュリティ上の考慮事項で扱い、ユーザーが変更の実行を許可されているかどうかを確認する」と明示的に記載されています。

**誤り（認証チェックなし）:**

```typescript
'use server'

export async function deleteUser(userId: string) {
  // Anyone can call this! No auth check
  await db.user.delete({ where: { id: userId } })
  return { success: true }
}
```

**正しい例（アクション内の認証）:**

```typescript
'use server'

import { verifySession } from '@/lib/auth'
import { unauthorized } from '@/lib/errors'

export async function deleteUser(userId: string) {
  // Always check auth inside the action
  const session = await verifySession()
  
  if (!session) {
    throw unauthorized('Must be logged in')
  }
  
  // Check authorization too
  if (session.user.role !== 'admin' && session.user.id !== userId) {
    throw unauthorized('Cannot delete other users')
  }
  
  await db.user.delete({ where: { id: userId } })
  return { success: true }
}
```

**入力検証を使用する場合:**

```typescript
'use server'

import { verifySession } from '@/lib/auth'
import { z } from 'zod'

const updateProfileSchema = z.object({
  userId: z.string().uuid(),
  name: z.string().min(1).max(100),
  email: z.string().email()
})

export async function updateProfile(data: unknown) {
  // Validate input first
  const validated = updateProfileSchema.parse(data)
  
  // Then authenticate
  const session = await verifySession()
  if (!session) {
    throw new Error('Unauthorized')
  }
  
  // Then authorize
  if (session.user.id !== validated.userId) {
    throw new Error('Can only update own profile')
  }
  
  // Finally perform the mutation
  await db.user.update({
    where: { id: validated.userId },
    data: {
      name: validated.name,
      email: validated.email
    }
  })
  
  return { success: true }
}
```

参照: [https://nextjs.org/docs/app/guides/authentication](https://nextjs.org/docs/app/guides/authentication)
