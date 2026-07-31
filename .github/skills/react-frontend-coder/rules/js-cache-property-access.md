---
title: ループ内のキャッシュ プロパティ アクセス
impact: LOW-MEDIUM
impactDescription: ルックアップを減らす
tags: javascript, loops, optimization, caching
---

## ループ内のキャッシュ プロパティ アクセス

オブジェクト プロパティのルックアップをホット パスにキャッシュします。

**誤り（3 回の検索 × N 回の反復）:**

```typescript
for (let i = 0; i < arr.length; i++) {
  process(obj.config.settings.value)
}
```

**正しい例（合計 1 件のルックアップ）:**

```typescript
const value = obj.config.settings.value
const len = arr.length
for (let i = 0; i < len; i++) {
  process(value)
}
```
