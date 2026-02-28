# Vitest/Jest セットアップガイド

## Vitestセットアップ（推奨 - Vite プロジェクト）

### 1. 依存インストール

```bash
npm install -D vitest @testing-library/react @testing-library/jest-dom
npm install -D jsdom @vitest/ui
```

### 2. vitest.config.ts 作成

```typescript
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      exclude: ['node_modules/', 'src/test/']
    }
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  }
})
```

### 3. setup.ts 作成

`src/test/setup.ts`:
```typescript
import '@testing-library/jest-dom'
import { expect, afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
})

// グローバルモック等をここに追加
```

### 4. package.json スクリプト追加

```json
{
  "scripts": {
    "test": "vitest",
    "test:ui": "vitest --ui",
    "test:run": "vitest run",
    "test:coverage": "vitest run --coverage"
  }
}
```

## Jest セットアップ（Vite非使用時）

### 1. 依存インストール

```bash
npm install -D jest @testing-library/react @testing-library/jest-dom
npm install -D @babel/preset-react @babel/preset-typescript babel-jest
npm install -D ts-jest identity-obj-proxy jsdom
```

### 2. jest.config.js

```javascript
export default {
  preset: 'ts-jest',
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/src/test/setup.ts'],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
    '\\.(css|less)$': 'identity-obj-proxy'
  },
  collectCoverageFrom: [
    'src/**/*.{ts,tsx}',
    '!src/**/*.d.ts',
    '!src/main.tsx'
  ]
}
```

### 3. package.json スクリプト

```json
{
  "scripts": {
    "test": "jest --watch",
    "test:run": "jest",
    "test:coverage": "jest --coverage"
  }
}
```

## 実行

```bash
# 監視モード
npm run test

# 一度だけ実行
npm run test:run

# カバレッジ確認
npm run test:coverage

# Vitest UI（Vitest のみ）
npm run test:ui
```
