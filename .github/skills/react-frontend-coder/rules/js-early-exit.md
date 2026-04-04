---
title: 関数からの早期復帰
impact: LOW-MEDIUM
impactDescription: 不必要な計算を回避します
tags: javascript, functions, optimization, early-return
---

## 関数からの早期復帰

結果が判明した場合は早めにリターンし、不要な処理を省略します。

**誤り（答えが見つかった後でもすべての項目を処理します）:**

```typescript
function validateUsers(users: User[]) {
  let hasError = false
  let errorMessage = ''
  
  for (const user of users) {
    if (!user.email) {
      hasError = true
      errorMessage = 'Email required'
    }
    if (!user.name) {
      hasError = true
      errorMessage = 'Name required'
    }
    // Continues checking all users even after error found
  }
  
  return hasError ? { valid: false, error: errorMessage } : { valid: true }
}
```

**正しい例（最初のエラーですぐに戻ります）:**

```typescript
function validateUsers(users: User[]) {
  for (const user of users) {
    if (!user.email) {
      return { valid: false, error: 'Email required' }
    }
    if (!user.name) {
      return { valid: false, error: 'Name required' }
    }
  }

  return { valid: true }
}
```
