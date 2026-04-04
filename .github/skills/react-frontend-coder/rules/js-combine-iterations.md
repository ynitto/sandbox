---
title: 複数の配列の反復を結合する
impact: LOW-MEDIUM
impactDescription: 反復を減らす
tags: javascript, arrays, loops, performance
---

## 複数の配列の反復を結合する

`.filter()` または `.map()` を複数回呼び出すと、配列が複数回反復されます。 1つのループに結合します。

**誤り（3 回繰り返し）:**

```typescript
const admins = users.filter(u => u.isAdmin)
const testers = users.filter(u => u.isTester)
const inactive = users.filter(u => !u.isActive)
```

**正しい例（1 回繰り返し）:**

```typescript
const admins: User[] = []
const testers: User[] = []
const inactive: User[] = []

for (const user of users) {
  if (user.isAdmin) admins.push(user)
  if (user.isTester) testers.push(user)
  if (!user.isActive) inactive.push(user)
}
```
