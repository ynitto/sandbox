---
title: RSC 境界でのシリアル化を最小限に抑える
impact: HIGH
impactDescription: データ転送サイズを削減する
tags: server, rsc, serialization, props
---

## RSC 境界でのシリアル化を最小限に抑える

React サーバー/クライアント境界は、すべてのオブジェクト プロパティを文字列にシリアル化し、HTML 応答と後続の RSC リクエストに埋め込みます。このシリアル化されたデータはページの重さと読み込み時間に直接影響するため、**サイズは非常に重要です**。クライアントが実際に使用するフィールドのみを渡します。

**誤り（50 フィールドすべてをシリアル化します）:**

```tsx
async function Page() {
  const user = await fetchUser()  // 50 fields
  return <Profile user={user} />
}

'use client'
function Profile({ user }: { user: User }) {
  return <div>{user.name}</div>  // uses 1 field
}
```

**正しい例（1 つのフィールドのみをシリアル化します）:**

```tsx
async function Page() {
  const user = await fetchUser()
  return <Profile name={user.name} />
}

'use client'
function Profile({ name }: { name: string }) {
  return <div>{name}</div>
}
```
