---
title: RSC Props での重複したシリアル化の回避
impact: LOW
impactDescription: 重複したシリアル化を回避してネットワーク ペイロードを削減する
tags: server, rsc, serialization, props, client-components
---

## RSC Props での重複したシリアル化の回避

**Impact:** 低（重複したシリアル化を回避することでネットワーク ペイロードを削減します）**

RSC→クライアントのシリアル化では、値ではなくオブジェクト参照によって重複排除が行われます。同じ参照 = 1 回シリアル化されます。新しい参照 = 再度シリアル化されます。変換（`.toSorted()`、`.filter()`、`.map()`) はサーバーではなくクライアントで実行します。

**誤り（配列が重複します）:**

```tsx
// RSC: sends 6 strings (2 arrays × 3 items)
<ClientList usernames={usernames} usernamesOrdered={usernames.toSorted()} />
```

**正しい例（3 つの文字列を送信）:**

```tsx
// RSC: send once
<ClientList usernames={usernames} />

// Client: transform there
'use client'
const sorted = useMemo(() => [...usernames].sort(), [usernames])
```

**ネストされた重複排除の動作:**

重複排除は再帰的に機能します。影響はデータの種類によって異なります。

- `string[]`、`number[]`、`boolean[]`: **大きな影響** - 配列 + すべてのプリミティブが完全に複製されています
- `object[]`: **影響は低い** - 配列は重複していますが、ネストされたオブジェクトは参照によって重複排除されています

```tsx
// string[] - duplicates everything
usernames={['a','b']} sorted={usernames.toSorted()} // sends 4 strings

// object[] - duplicates array structure only
users={[{id:1},{id:2}]} sorted={users.toSorted()} // sends 2 arrays + 2 unique objects (not 4)
```

**重複排除を解除する操作（新しい参照の作成）:**

- 配列: `.toSorted()`、`.filter()`、`.map()`、`.slice()`、`[...arr]`
- オブジェクト: `{...obj}`、`Object.assign()`、`structuredClone()`、`JSON.parse(JSON.stringify())`

**その他の例:**

```tsx
// ❌ Bad
<C users={users} active={users.filter(u => u.active)} />
<C product={product} productName={product.name} />

// ✅ Good
<C users={users} />
<C product={product} />
// Do filtering/destructuring in client
```

**例外:** 変換にコストがかかる場合、またはクライアントがオリジナルを必要としない場合は、派生データを渡します。
