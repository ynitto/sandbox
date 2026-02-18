---
title: ユーザーの意図に基づいたプリロード
impact: MEDIUM
impactDescription: 知覚される遅延を軽減します
tags: bundle, preload, user-intent, hover
---

## ユーザーの意図に基づいたプリロード

重いバンドルを必要になる前にプリロードして、体感的な遅延を軽減します。

**例（ホバー/フォーカス時のプリロード）:**

```tsx
function EditorButton({ onClick }: { onClick: () => void }) {
  const preload = () => {
    if (typeof window !== 'undefined') {
      void import('./monaco-editor')
    }
  }

  return (
    <button
      onMouseEnter={preload}
      onFocus={preload}
      onClick={onClick}
    >
      Open Editor
    </button>
  )
}
```

**例（機能フラグが有効な場合のプリロード）:**

```tsx
function FlagsProvider({ children, flags }: Props) {
  useEffect(() => {
    if (flags.editorEnabled && typeof window !== 'undefined') {
      void import('./monaco-editor').then(mod => mod.init())
    }
  }, [flags.editorEnabled])

  return <FlagsContext.Provider value={flags}>
    {children}
  </FlagsContext.Provider>
}
```

`typeof window !== 'undefined'` チェックは、SSR のプリロードされたモジュールのバンドルを防止し、サーバー バンドルのサイズとビルド速度を最適化します。
