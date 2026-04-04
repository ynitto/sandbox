---
title: localStorage データのバージョン管理と最小化
impact: MEDIUM
impactDescription: スキーマの競合を防止し、ストレージ サイズを削減します
tags: client, localStorage, storage, versioning, data-minimization
---

## localStorage データのバージョン管理と最小化

キーにバージョンプレフィックスを追加し、必要なフィールドのみを保存します。スキーマの競合や機密データの誤った保存を防ぎます。

**正しくない：**

```typescript
// No version, stores everything, no error handling
localStorage.setItem('userConfig', JSON.stringify(fullUserObject))
const data = localStorage.getItem('userConfig')
```

**正しい：**

```typescript
const VERSION = 'v2'

function saveConfig(config: { theme: string; language: string }) {
  try {
    localStorage.setItem(`userConfig:${VERSION}`, JSON.stringify(config))
  } catch {
    // Throws in incognito/private browsing, quota exceeded, or disabled
  }
}

function loadConfig() {
  try {
    const data = localStorage.getItem(`userConfig:${VERSION}`)
    return data ? JSON.parse(data) : null
  } catch {
    return null
  }
}

// Migration from v1 to v2
function migrate() {
  try {
    const v1 = localStorage.getItem('userConfig:v1')
    if (v1) {
      const old = JSON.parse(v1)
      saveConfig({ theme: old.darkMode ? 'dark' : 'light', language: old.lang })
      localStorage.removeItem('userConfig:v1')
    }
  } catch {}
}
```

**サーバー応答からの最小限のフィールドを保存します。**

```typescript
// User object has 20+ fields, only store what UI needs
function cachePrefs(user: FullUser) {
  try {
    localStorage.setItem('prefs:v1', JSON.stringify({
      theme: user.preferences.theme,
      notifications: user.preferences.notifications
    }))
  } catch {}
}
```

**常に try-catch で囲みます:** `getItem()` および `setItem()` は、クォータを超えた場合、または無効になっている場合に、シークレット/プライベート ブラウジング（Safari、Firefox) でスローされます。

**利点:** バージョン管理によるスキーマの進化、ストレージ サイズの削減、トークン/PII/内部フラグの保存を防止します。
