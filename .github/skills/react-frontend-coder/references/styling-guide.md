# Tailwind CSS + CSS スタイリングガイド

## Tailwind CSS の基本

### Utility-First アプローチ

Tailwind CSS は**ユーティリティクラス**を組み合わせてUIを構築。CSS ファイルを書かない。

```tsx
// ❌ 従来：CSS を別ファイルで定義
// styles.css: .button { background: blue; padding: 8px; ... }
// <button className="button">Click</button>

// ✅ Tailwind：ユーティリティクラスを inline で指定
<button className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600">
  Click
</button>
```

### よく使うクラス例

| 用途 | クラス | 説明 |
|-----|--------|------|
| パディング | `p-4` `px-2` `py-3` | 全方向 / 左右 / 上下 |
| マージン | `m-4` `mx-auto` `mb-2` | 全方向 / 左右中央 / 下部 |
| 背景色 | `bg-blue-500` `bg-gray-100` | 色とレベル（50-950） |
| テキスト色 | `text-white` `text-gray-700` | 文字色 |
| サイズ | `w-full` `h-12` `max-w-md` | 幅 / 高さ / 最大幅 |
| フレックス | `flex` `justify-between` `items-center` | Flexレイアウト |
| グリッド | `grid` `grid-cols-3` `gap-4` | グリッドレイアウト |
| ホバー | `hover:bg-blue-600` `hover:scale-105` | ホバー状態 |
| レスポンシブ | `md:p-6` `lg:grid-cols-4` | ブレークポイント |

```tsx
// レイアウト例
<div className="flex flex-col md:flex-row gap-4">
  <div className="w-full md:w-1/3 bg-white p-4 rounded shadow">
    Sidebar
  </div>
  <div className="w-full md:w-2/3">
    Main content
  </div>
</div>
```

## いつ CSS を使うか

### Tailwind だけでは対応できない場合

1. **複雑な独自スタイル**
   ```tsx
   // CSS Animation
   <style>{`
     @keyframes fadeIn {
       from { opacity: 0; }
       to { opacity: 1; }
     }
   `}</style>
   ```

2. **複数要素にまたがるスタイル**
   ```tsx
   // CSS で兄弟セレクター指定が必要
   <style>{`
     .form-group:has(input:invalid) .error-message {
       display: block;
     }
   `}</style>
   ```

3. **大量の CSS を共有するコンポーネント**
   ```tsx
   // styles.module.css で管理
   // → コンポーネント固有の複雑なスタイル
   ```

## Tailwind CSS + 手書きCSS の使い分け

### パターン1：ほぼ Tailwind + 一部CSS

```tsx
// Button.tsx
import styles from './Button.module.css';

export const Button = ({ children, variant = 'primary' }: Props) => {
  const baseClasses = 'px-4 py-2 rounded font-semibold transition';
  
  const variants = {
    primary: 'bg-blue-500 text-white hover:bg-blue-600',
    secondary: 'bg-gray-200 text-gray-800 hover:bg-gray-300',
  };
  
  return (
    <button className={`${baseClasses} ${variants[variant]}`}>
      {children}
    </button>
  );
};
```

### パターン2：複雑なレイアウト → CSS Module

```tsx
// Card.tsx
import styles from './Card.module.css';

export const Card = ({ children, title }: Props) => {
  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <h3 className="text-lg font-bold">{title}</h3>
      </div>
      <div className={styles.content}>
        {children}
      </div>
    </div>
  );
};

/* Card.module.css */
.card {
  display: grid;
  grid-template-rows: auto 1fr;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
}

.header {
  background: #f9fafb;
  padding: 1rem;
  border-bottom: 1px solid #e5e7eb;
}

.content {
  padding: 1rem;
  overflow-y: auto;
}
```

### パターン3：グローバルスタイル（CSS）

```tsx
/* src/index.css - グローバルスタイル */
@tailwind base;
@tailwind components;
@tailwind utilities;

/* Base layer でデフォルトスタイル定義 */
@layer base {
  html {
    scroll-behavior: smooth;
  }
  
  body {
    @apply bg-white text-gray-900;
  }
}

/* Components layer で再利用可能な class 定義 */
@layer components {
  .btn-primary {
    @apply px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600;
  }
}
```

## レスポンシブデザイン

Tailwind のブレークポイント：`sm` (640px), `md` (768px), `lg` (1024px), `xl` (1280px)

```tsx
<div className="
  grid 
  grid-cols-1       // Mobile: 1列
  md:grid-cols-2    // Tablet: 2列
  lg:grid-cols-3    // Desktop: 3列
  gap-4
">
  {items.map(item => <Card key={item.id} {...item} />)}
</div>
```

## ダークモード

### 自動パターン（ OS設定に従う）

```tsx
// tailwind.config.js
module.exports = {
  darkMode: 'media', // OS設定
};

// コンポーネント
<div className="bg-white dark:bg-gray-800 text-black dark:text-white">
  Content
</div>
```

### 手動パターン（トグルで切り替え）

```tsx
// tailwind.config.js
module.exports = {
  darkMode: 'class', // .dark class で制御
};

// App.tsx
const [isDark, setIsDark] = useState(false);

return (
  <div className={isDark ? 'dark' : ''}>
    <div className="bg-white dark:bg-gray-800">
      Content
    </div>
    <button onClick={() => setIsDark(!isDark)}>Toggle Dark</button>
  </div>
);
```

## パフォーマンス

### 不要なクラスの削除

Tailwind はビルド時にテンプレート内のクラスをスキャンし、未使用のクラスを削除（PurgeCSS）。

```js
// tailwind.config.js
module.exports = {
  content: [
    './src/**/*.{js,ts,jsx,tsx}',
  ],
};
```

### インライン style とクラスの違い

```tsx
// ❌ インライン style：ブラウザで計算 → パフォーマンス低下
<div style={{ backgroundColor: 'rgb(59, 130, 246)', padding: '1rem' }}>

// ✅ Tailwind クラス：CSS で一括管理 → 高速
<div className="bg-blue-500 p-4">
```

## コンポーネント例

### フォーム

```tsx
export const FormInput = ({ label, name, error }: Props) => (
  <div className="mb-4">
    <label className="block text-sm font-semibold text-gray-700 mb-1">
      {label}
    </label>
    <input
      name={name}
      className="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring focus:ring-blue-300"
    />
    {error && <p className="text-red-500 text-sm mt-1">{error}</p>}
  </div>
);
```

### カード

```tsx
export const Card = ({ title, children }: Props) => (
  <div className="bg-white rounded-lg shadow-md p-6 mb-4">
    <h2 className="text-xl font-bold text-gray-800 mb-3">{title}</h2>
    <div className="text-gray-600">{children}</div>
  </div>
);
```

### ナビゲーションメニュー

```tsx
export const Navbar = () => (
  <nav className="bg-blue-600 text-white p-4">
    <div className="container mx-auto flex justify-between items-center">
      <div className="text-xl font-bold">Logo</div>
      <ul className="flex gap-6">
        <li><a href="#" className="hover:text-blue-200">Home</a></li>
        <li><a href="#" className="hover:text-blue-200">About</a></li>
      </ul>
    </div>
  </nav>
);
```
