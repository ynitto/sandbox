---
title: キャッシュストレージ API 呼び出し
impact: LOW-MEDIUM
impactDescription: 高価な I/O を削減
tags: javascript, localStorage, storage, caching, performance
---

## キャッシュストレージ API 呼び出し

`localStorage`、`sessionStorage`、および `document.cookie` は同期的であり、高価です。キャッシュはメモリ内で読み取ります。

**誤り（呼び出しごとにストレージを読み取ります）:**

```typescript
function getTheme() {
  return localStorage.getItem('theme') ?? 'light'
}
// Called 10 times = 10 storage reads
```

**正しい例（マップ キャッシュ）:**

```typescript
const storageCache = new Map<string, string | null>()

function getLocalStorage(key: string) {
  if (!storageCache.has(key)) {
    storageCache.set(key, localStorage.getItem(key))
  }
  return storageCache.get(key)
}

function setLocalStorage(key: string, value: string) {
  localStorage.setItem(key, value)
  storageCache.set(key, value)  // keep cache in sync
}
```

Map（フックではなく) を使用すると、React コンポーネントだけでなく、ユーティリティ、イベント ハンドラーなど、どこでも機能します。

**Cookie キャッシュ:**

```typescript
let cookieCache: Record<string, string> | null = null

function getCookie(name: string) {
  if (!cookieCache) {
    cookieCache = Object.fromEntries(
      document.cookie.split('; ').map(c => c.split('='))
    )
  }
  return cookieCache[name]
}
```

**重要（外部変更時に無効化）:**

ストレージが外部で変更される可能性がある場合（別のタブ、サーバー設定の Cookie)、キャッシュを無効にします。

```typescript
window.addEventListener('storage', (e) => {
  if (e.key) storageCache.delete(e.key)
})

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    storageCache.clear()
  }
})
```
