---
title: ちらつきを発生させずに水分補給の不一致を防止
impact: MEDIUM
impactDescription: 視覚的なちらつきや水分補給エラーを回避します
tags: rendering, ssr, hydration, localStorage, flicker
---

## ちらつきを発生させずに水分補給の不一致を防止

クライアントのストレージ（localStorage、Cookie) に依存するコンテンツをレンダーするときは、React がハイドレートする前に DOM を更新する同期スクリプトを挿入することで、SSR の破損とハイドレーション後のちらつきの両方を回避します。

**誤り（SSRを破壊）：**

```tsx
function ThemeWrapper({ children }: { children: ReactNode }) {
  // localStorage is not available on server - throws error
  const theme = localStorage.getItem('theme') || 'light'
  
  return (
    <div className={theme}>
      {children}
    </div>
  )
}
```

`localStorage` が定義されていないため、サーバーサイドのレンダーは失敗します。

**誤り（視覚的にちらつき）:**

```tsx
function ThemeWrapper({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState('light')
  
  useEffect(() => {
    // Runs after hydration - causes visible flash
    const stored = localStorage.getItem('theme')
    if (stored) {
      setTheme(stored)
    }
  }, [])
  
  return (
    <div className={theme}>
      {children}
    </div>
  )
}
```

コンポーネントは最初にデフォルト値（`light`) でレンダーされ、ハイドレーション後に更新されるため、誤ったコンテンツが表示されます。

**正しい（ちらつきなし、水分補給の不一致なし）：**

```tsx
function ThemeWrapper({ children }: { children: ReactNode }) {
  return (
    <>
      <div id="theme-wrapper">
        {children}
      </div>
      <script
        dangerouslySetInnerHTML={{
          __html: `
            (function() {
              try {
                var theme = localStorage.getItem('theme') || 'light';
                var el = document.getElementById('theme-wrapper');
                if (el) el.className = theme;
              } catch (e) {}
            })();
          `,
        }}
      />
    </>
  )
}
```

インライン スクリプトは要素を表示する前に同期的に実行され、DOM がすでに正しい値を持っていることが保証されます。ちらつきや水分の不一致はありません。

このパターンは、テーマの切り替え、ユーザー設定、認証状態、およびデフォルト値をフラッシュせずにすぐにレンダーする必要があるクライアント専用データに特に役立ちます。
