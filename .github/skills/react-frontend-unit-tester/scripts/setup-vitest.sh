#!/usr/bin/env bash

# setup-vitest.sh
# Vitest + React Testing Library の依存インストール・基本設定自動化

set -e

echo "🧪 Vitest セットアップを開始します..."

# 1. 依存インストール
echo "📦 Vitest 依存をインストール中..."
npm install -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom @vitest/ui @vitest/coverage-v8

# 2. setup.ts を作成
echo "📝 テスト setup ファイルを作成中..."
mkdir -p src/test

cat > src/test/setup.ts << 'EOF'
import '@testing-library/jest-dom'
import { expect, afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
})

// グローバルモック等があればここに追加
EOF

# 3. vitest.config.ts を検査・作成
if [ ! -f vitest.config.ts ]; then
  echo "⚙️  vitest.config.ts を作成中..."
  cat > vitest.config.ts << 'EOF'
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
      exclude: [
        'node_modules/',
        'src/test/',
        '**/*.d.ts',
        '**/dist/**'
      ]
    }
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  }
})
EOF
else
  echo "✅ vitest.config.ts は既に存在します。スキップします。"
fi

# 4. package.json スクリプト追加（存在しない場合）
echo "📄 package.json スクリプトを確認中..."
if ! grep -q '"test":' package.json; then
  echo "⚠️  package.json にテストスクリプトを手動で追加してください："
  echo '  "scripts": {'
  echo '    "test": "vitest",'
  echo '    "test:ui": "vitest --ui",'
  echo '    "test:run": "vitest run",'
  echo '    "test:coverage": "vitest run --coverage"'
  echo '  }'
else
  echo "✅ テストスクリプトは既に存在します。"
fi

echo "✨ Vitest セットアップが完了しました！"
echo ""
echo "📚 次のステップ:"
echo "  1. npm run test        （監視モード）"
echo "  2. npm run test:ui     （UI 表示、Vitest UI）"
echo "  3. npm run test:run    （一度だけ実行）"
echo "  4. npm run test:coverage （カバレッジ確認）"
echo ""
echo "📖 参考: references/setup-guide.md"
