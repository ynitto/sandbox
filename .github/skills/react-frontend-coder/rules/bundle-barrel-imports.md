---
title: バレルファイルのインポートを避ける
impact: CRITICAL
impactDescription: 200 ～ 800 ミリ秒のインポート コスト、ビルドが遅い
tags: bundle, imports, tree-shaking, barrel-files, performance
---

## バレルファイルのインポートを避ける

何千もの未使用モジュールのロードを避けるために、バレル ファイルではなくソース ファイルから直接インポートします。 **バレル ファイル**は、複数のモジュールを再エクスポートするエントリ ポイントです（例: `export * from './module'` を実行する `index.js`)。

人気のあるアイコンおよびコンポーネント ライブラリでは、エントリ ファイルに **最大 10,000 個の再エクスポート**を含めることができます。多くの React パッケージでは、*インポートするだけで 200 ～ 800 ミリ秒かかります**。これは、開発速度と本番コールド スタートの両方に影響します。

**ツリーシェイクが役に立たない理由:** ライブラリが外部（バンドルされていない) としてマークされている場合、バンドラーはそれを最適化できません。これをバンドルしてツリーシェイキングを有効にすると、モジュール グラフ全体の分析でビルドが大幅に遅くなります。

**誤り（ライブラリ全体をインポートします）:**

```tsx
import { Check, X, Menu } from 'lucide-react'
// Loads 1,583 modules, takes ~2.8s extra in dev
// Runtime cost: 200-800ms on every cold start

import { Button, TextField } from '@mui/material'
// Loads 2,225 modules, takes ~4.2s extra in dev
```

**正しい例（必要なものだけをインポートします）:**

```tsx
import Check from 'lucide-react/dist/esm/icons/check'
import X from 'lucide-react/dist/esm/icons/x'
import Menu from 'lucide-react/dist/esm/icons/menu'
// Loads only 3 modules (~2KB vs ~1MB)

import Button from '@mui/material/Button'
import TextField from '@mui/material/TextField'
// Loads only what you use
```

**代替案（Next.js 13.5+）:**

```js
// next.config.js - use optimizePackageImports
module.exports = {
  experimental: {
    optimizePackageImports: ['lucide-react', '@mui/material']
  }
}

// Then you can keep the ergonomic barrel imports:
import { Check, X, Menu } from 'lucide-react'
// Automatically transformed to direct imports at build time
```

直接インポートにより、開発ブートが 15 ～ 70% 高速化、ビルドが 28% 高速化、コールド スタートが 40% 高速化され、HMR が大幅に高速化されます。

一般的に影響を受けるライブラリ: `lucide-react`、`@mui/material`、`@mui/icons-material`、`@tabler/icons-react`、`react-icons`、`@headlessui/react`、`@radix-ui/react-*`、`lodash`、`ramda`、`date-fns`、`rxjs`、`react-use`。

参照: [How we optimized package imports in Next.js](https://vercel.com/blog/how-we-optimized-package-imports-in-next-js)
