# Zustand Store テスト

## 基本パターン

### Store定義（例）

```typescript
// src/store.ts
import { create } from 'zustand'

export interface TodoItem {
  id: string
  title: string
  done: boolean
}

interface TodoStore {
  todos: TodoItem[]
  addTodo: (title: string) => void
  removeTodo: (id: string) => void
  toggleTodo: (id: string) => void
}

export const useTodoStore = create<TodoStore>((set) => ({
  todos: [],
  addTodo: (title) =>
    set((state) => ({
      todos: [...state.todos, { id: Date.now().toString(), title, done: false }]
    })),
  removeTodo: (id) =>
    set((state) => ({
      todos: state.todos.filter((t) => t.id !== id)
    })),
  toggleTodo: (id) =>
    set((state) => ({
      todos: state.todos.map((t) =>
        t.id === id ? { ...t, done: !t.done } : t
      )
    }))
}))
```

### Store テスト

```typescript
// src/store.test.ts
import { describe, it, expect, beforeEach } from 'vitest'
import { useTodoStore } from './store'

describe('useTodoStore', () => {
  beforeEach(() => {
    // 各テスト前にストア状態をリセット
    useTodoStore.setState({ todos: [] })
  })

  it('初期状態は空の todos を持つ', () => {
    const { todos } = useTodoStore.getState()
    expect(todos).toEqual([])
  })

  it('addTodo で新しい todo を追加できる', () => {
    const { addTodo, todos } = useTodoStore.getState()
    addTodo('Learn Testing')

    const state = useTodoStore.getState()
    expect(state.todos).toHaveLength(1)
    expect(state.todos[0].title).toBe('Learn Testing')
    expect(state.todos[0].done).toBe(false)
  })

  it('removeTodo で todo を削除できる', () => {
    const store = useTodoStore.getState()
    store.addTodo('Todo 1')
    store.addTodo('Todo 2')

    const id = useTodoStore.getState().todos[0].id
    store.removeTodo(id)

    const state = useTodoStore.getState()
    expect(state.todos).toHaveLength(1)
    expect(state.todos[0].title).toBe('Todo 2')
  })

  it('toggleTodo で done 状態を切り替える', () => {
    const store = useTodoStore.getState()
    store.addTodo('Test')

    const id = useTodoStore.getState().todos[0].id
    store.toggleTodo(id)

    const todo = useTodoStore.getState().todos[0]
    expect(todo.done).toBe(true)

    store.toggleTodo(id)
    const updatedTodo = useTodoStore.getState().todos[0]
    expect(updatedTodo.done).toBe(false)
  })
})
```

## Selector テスト

```typescript
// Store に selector を追加
export const useTodoStore = create<TodoStore>((set) => ({
  // ...
}))

export const selectCompletedCount = (state: TodoStore) =>
  state.todos.filter((t) => t.done).length

export const selectTodoCount = (state: TodoStore) => state.todos.length

// テスト
describe('selectors', () => {
  beforeEach(() => {
    useTodoStore.setState({ todos: [] })
  })

  it('selectCompletedCount は完了済み todo 数を返す', () => {
    const store = useTodoStore.getState()
    store.addTodo('A')
    store.addTodo('B')

    const todos = useTodoStore.getState().todos
    todos[0].done = true // 1つ完了済みに

    expect(selectCompletedCount(useTodoStore.getState())).toBe(1)
    expect(selectTodoCount(useTodoStore.getState())).toBe(2)
  })
})
```

## ベストプラクティス

1. **各テスト前にリセット** - `beforeEach`で状態をクリア
2. **getState() で直接アクセス** - コンポーネント統合テストでないため
3. **setter ではなく action を呼ぶ** - ビジネスロジックをテスト
4. **副作用を避ける** - async action は別途テスト
5. **selector は純粋関数** - 同じ input で常に同じ output

## Async Action テスト

```typescript
interface AsyncStore {
  data: string | null
  loading: boolean
  fetchData: () => Promise<void>
}

export const useAsyncStore = create<AsyncStore>((set) => ({
  data: null,
  loading: false,
  fetchData: async () => {
    set({ loading: true })
    try {
      const response = await fetch('/api/data')
      const json = await response.json()
      set({ data: json.text, loading: false })
    } catch {
      set({ loading: false })
    }
  }
}))

// テスト
import { vi } from 'vitest'

describe('useAsyncStore', () => {
  beforeEach(() => {
    useAsyncStore.setState({ data: null, loading: false })
    vi.clearAllMocks()
  })

  it('fetchData は loading を切り替える', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        json: () => Promise.resolve({ text: 'Hello' })
      } as Response)
    )

    const store = useAsyncStore.getState()
    const promise = store.fetchData()

    expect(useAsyncStore.getState().loading).toBe(true)

    await promise

    expect(useAsyncStore.getState().data).toBe('Hello')
    expect(useAsyncStore.getState().loading).toBe(false)
  })
})
```
