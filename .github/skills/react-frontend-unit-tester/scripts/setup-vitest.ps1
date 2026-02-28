# setup-vitest.ps1
# Vitest + React Testing Library の依存インストール・基本設定自動化

$ErrorActionPreference = 'Stop'

Write-Host "🧪 Vitest セットアップを開始します..."

# 1. 依存インストール
Write-Host "📦 Vitest 依存をインストール中..."
npm install -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom @vitest/ui @vitest/coverage-v8

# 2. setup.ts を作成
Write-Host "📝 テスト setup ファイルを作成中..."
New-Item -ItemType Directory -Path "src/test" -Force | Out-Null

$setupTs = @"
import '@testing-library/jest-dom'
import { expect, afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
})

// グローバルモック等があればここに追加
"@
Set-Content -Path "src/test/setup.ts" -Value $setupTs -Encoding UTF8

# 3. vitest.config.ts を検査・作成
if (-not (Test-Path "vitest.config.ts")) {
    Write-Host "⚙️  vitest.config.ts を作成中..."

    $vitestConfig = @"
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
"@

    Set-Content -Path "vitest.config.ts" -Value $vitestConfig -Encoding UTF8
} else {
    Write-Host "✅ vitest.config.ts は既に存在します。スキップします。"
}

# 4. package.json スクリプト追加（存在しない場合）
Write-Host "📄 package.json スクリプトを確認中..."
if (-not (Select-String -Path "package.json" -Pattern '"test":' -Quiet)) {
    Write-Host "⚠️  package.json にテストスクリプトを手動で追加してください："
    Write-Host '  "scripts": {'
    Write-Host '    "test": "vitest",'
    Write-Host '    "test:ui": "vitest --ui",'
    Write-Host '    "test:run": "vitest run",'
    Write-Host '    "test:coverage": "vitest run --coverage"'
    Write-Host '  }'
} else {
    Write-Host "✅ テストスクリプトは既に存在します。"
}

Write-Host "✨ Vitest セットアップが完了しました！"
Write-Host ""
Write-Host "📚 次のステップ:"
Write-Host "  1. npm run test          （監視モード）"
Write-Host "  2. npm run test:ui       （UI 表示、Vitest UI）"
Write-Host "  3. npm run test:run      （一度だけ実行）"
Write-Host "  4. npm run test:coverage （カバレッジ確認）"
Write-Host ""
Write-Host "📖 参考: references/setup-guide.md"
