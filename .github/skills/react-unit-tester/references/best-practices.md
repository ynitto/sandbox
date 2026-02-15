# テスト ベストプラクティス

## アサーション戦略

### 何をテストすべきか

✅ **テストすべき**:
- 出力・表示（画面に表示される内容）
- ユーザーインタラクション（クリック・入力後の動作）
- Error/Exception（例外が正しく発生するか）
- 境界値とエッジケース

❌ **テストすべきでない**:
- 実装詳細（内部の方法、変数名）
- React 自身の機能（`useState` 等は React が テスト済み）
- CSSの詳細（z-indexなど）

### 推奨・非推奨アサーション

```typescript
// ❌ 非推奨 - 実装詳細に依存
expect(component.querySelector('.hidden')).toHaveStyle('display: none')

// ✅ 推奨 - ユーザー視点
expect(element).not.toBeVisible()

// ❌ 非推奨 - コンポーネント構造に依存
expect(container.children[0].children[1].textContent).toBe('Done')

// ✅ 推奨 - アクセシブルな選択
expect(screen.getByRole('button', { name: /done/i })).toBeInTheDocument()
```

## テストデータ（Fixtures）の構成

### Fixture パターン

```typescript
// src/test/fixtures/todo.fixtures.ts
export const mockTodoPending = {
  id: '1',
  title: 'Learn Testing',
  done: false
}

export const mockTodoCompleted = {
  id: '2',
  title: 'Write Tests',
  done: true
}

export const mockTodos = [mockTodoPending, mockTodoCompleted]

// テストで使用
import { mockTodos } from '../fixtures/todo.fixtures'

it('複数の todo をレンダリング', () => {
  render(<TodoList items={mockTodos} />)
  expect(screen.getByText('Learn Testing')).toBeInTheDocument()
})
```

### Factory パターン

詳細な設定が必要な場合：

```typescript
// src/test/factories/todo.factory.ts
export const createTodo = (overrides = {}) => ({
  id: Date.now().toString(),
  title: 'Default Todo',
  done: false,
  createdAt: new Date(),
  ...overrides
})

// テストで使用
const todo = createTodo({ title: 'Custom Title', done: true })
```

## 非同期処理テスト

### waitFor を使用

```typescript
import { screen, waitFor } from '@testing-library/react'

it('非同期データ読み込み後に表示される', async () => {
  render(<DataComponent />)

  // ローディング状態を確認
  expect(screen.getByText('Loading...')).toBeInTheDocument()

  // データ表示を待つ
  await waitFor(() => {
    expect(screen.getByText('Data Loaded')).toBeInTheDocument()
  })
})
```

### findBy (推奨)

```typescript
// waitFor + QUERY_TIMEOUT のラッパー
it('非同期で要素が現れる', async () => {
  render(<DataComponent />)

  const element = await screen.findByText('Data Loaded', {}, { timeout: 3000 })
  expect(element).toBeInTheDocument()
})

// 並列で複数要素を待つ
const [heading, button] = await Promise.all([
  screen.findByRole('heading'),
  screen.findByRole('button')
])
```

## Mock と Spy

### モック関数

```typescript
import { vi } from 'vitest'

const mockFn = vi.fn()
mockFn('arg1', 'arg2')

expect(mockFn).toHaveBeenCalled()
expect(mockFn).toHaveBeenCalledWith('arg1', 'arg2')
expect(mockFn).toHaveBeenCalledTimes(1)
expect(mockFn.mock.results[0].value).toBe('result')
```

### Module Mock (vi.mock)

```typescript
// ファイル冒頭
vi.mock('../api', () => ({
  fetchData: vi.fn(() => Promise.resolve({ data: 'mocked' }))
}))

// テストで
import { fetchData } from '../api'

it('API呼び出しをモック', async () => {
  const result = await fetchData()
  expect(result.data).toBe('mocked')
})
```

## スナップショットテスト

### 使用場面

- 複雑な HTML 出力
- 定型のコンポーネント（エラーメッセージなど）
- リグレッション防止

### スナップショット

```typescript
it('HTMLが期待通り', () => {
  const { container } = render(<TodoItem {...props} />)
  expect(container).toMatchSnapshot()
})
```

⚠️ **注意**: 気軽にスナップショット更新しない（`-u`フラグ）。変更を都度確認する。

## テストカバレッジ

### カバレッジ目標

| メトリクス | 目標 |
|-----------|------|
| Statements | >= 80% |
| Branches | >= 70% |
| Functions | >= 80% |
| Lines | >= 80% |

### カバレッジレポート確認

```bash
npm run test:coverage

# 出力例:
# -------|----------|----------|----------|----------|
# File   | % Stmts  | % Branch | % Funcs  | % Lines  |
# -------|----------|----------|----------|----------|
# All    | 85.2     | 72.5     | 88.1     | 85.2     |
```

### カバレッジ除外

重要でない部分は除外：

```typescript
// コメント行で除外
/* c8 ignore next */
const neverReached = () => {}

// またはファイル単位
/* c8 ignore file */
```

## テスト実行戦略

### Watch モードで開発

```bash
npm run test         # 監視モード
npm run test:ui      # UI 表示
```

### CI でテスト実行

```bash
npm run test:run     # 一度だけ実行
```

### 特定テストのみ実行

```bash
# 部分文字列でフィルタ
npx vitest run --grep "useCounter"

# 単一ファイル
npx vitest run src/hooks/useCounter.test.ts
```

## デバッグ

### コンソール出力

```typescript
import { screen, debug } from '@testing-library/react'

render(<Component />)
debug() // 現在のDOM を出力
debug(screen.getByRole('button')) // 特定要素のみ
```

### Vitest UI

```bash
npm run test:ui      # ブラウザで対話的にテスト実行
```

### Debugger

```typescript
it('debug test', async () => {
  render(<Component />)
  debugger // DevTools で一時停止
  // 対話的に確認
})

// 実行時: node --inspect-brk ... npm run test:run
```
