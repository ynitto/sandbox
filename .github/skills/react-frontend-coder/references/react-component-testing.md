# React コンポーネント テスト

## 目次

- [基本パターン](#基本パターン)

## 基本パターン

### コンポーネント例

```typescript
// src/components/TodoItem.tsx
import { FC } from 'react'
import { useTodoStore } from '../store'

interface TodoItemProps {
  id: string
  title: string
  done: boolean
}

export const TodoItem: FC<TodoItemProps> = ({ id, title, done }) => {
  const { toggleTodo, removeTodo } = useTodoStore()

  return (
    <div className="todo-item">
      <input
        type="checkbox"
        checked={done}
        onChange={() => toggleTodo(id)}
        data-testid={`toggle-${id}`}
      />
      <span className={done ? 'line-through' : ''}>{title}</span>
      <button onClick={() => removeTodo(id)} data-testid={`remove-${id}`}>
        Delete
      </button>
    </div>
  )
}
```

### テスト

```typescript
// src/components/TodoItem.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TodoItem } from './TodoItem'
import * as store from '../store'

// Store をモック
vi.mock('../store', () => ({
  useTodoStore: vi.fn()
}))

const mockUseTodoStore = store.useTodoStore as any

describe('TodoItem', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseTodoStore.mockReturnValue({
      toggleTodo: vi.fn(),
      removeTodo: vi.fn()
    })
  })

  it('props を正しく表示する', () => {
    render(
      <TodoItem id="1" title="Learn Testing" done={false} />
    )

    expect(screen.getByText('Learn Testing')).toBeInTheDocument()
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('done=true の場合、line-through クラスを適用', () => {
    render(
      <TodoItem id="1" title="Done Task" done={true} />
    )

    expect(screen.getByText('Done Task')).toHaveClass('line-through')
  })

  it('チェックボックス変更時に toggleTodo を呼ぶ', async () => {
    const toggleTodo = vi.fn()
    mockUseTodoStore.mockReturnValue({
      toggleTodo,
      removeTodo: vi.fn()
    })

    render(
      <TodoItem id="1" title="Task" done={false} />
    )

    const checkbox = screen.getByRole('checkbox')
    await userEvent.click(checkbox)

    expect(toggleTodo).toHaveBeenCalledWith('1')
  })

  it('削除ボタン クリック時に removeTodo を呼ぶ', async () => {
    const removeTodo = vi.fn()
    mockUseTodoStore.mockReturnValue({
      toggleTodo: vi.fn(),
      removeTodo
    })

    render(
      <TodoItem id="42" title="Task" done={false} />
    )

    const deleteButton = screen.getByTestId('remove-42')
    await userEvent.click(deleteButton)

    expect(removeTodo).toHaveBeenCalledWith('42')
  })
})
```

## リスト/条件付きレンダリング テスト

```typescript
// コンポーネント
interface TodoListProps {
  items: TodoItem[]
}

export const TodoList: FC<TodoListProps> = ({ items }) => {
  if (items.length === 0) {
    return <p>No todos</p>
  }
  return (
    <ul>
      {items.map((item) => (
        <li key={item.id}>
          <TodoItem {...item} />
        </li>
      ))}
    </ul>
  )
}

// テスト
describe('TodoList', () => {
  it('空リストの場合、"No todos" を表示', () => {
    render(<TodoList items={[]} />)
    expect(screen.getByText('No todos')).toBeInTheDocument()
  })

  it('アイテム数分の TodoItem をレンダリング', () => {
    const items = [
      { id: '1', title: 'Task 1', done: false },
      { id: '2', title: 'Task 2', done: true }
    ]

    render(<TodoList items={items} />)

    expect(screen.getByText('Task 1')).toBeInTheDocument()
    expect(screen.getByText('Task 2')).toBeInTheDocument()
  })
})
```

## フォーム入力テスト

```typescript
// コンポーネント
export const TodoInput: FC = () => {
  const [input, setInput] = useState('')
  const { addTodo } = useTodoStore()

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (input.trim()) {
      addTodo(input)
      setInput('')
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <input
        value={input}
        onChange={(e) => setInput(e.target.value)}
        placeholder="Add a todo..."
        data-testid="todo-input"
      />
      <button type="submit">Add</button>
    </form>
  )
}

// テスト
describe('TodoInput', () => {
  beforeEach(() => {
    mockUseTodoStore.mockReturnValue({
      addTodo: vi.fn(),
      toggleTodo: vi.fn(),
      removeTodo: vi.fn()
    })
  })

  it('入力値をフォーム送信で addTodo に渡す', async () => {
    const addTodo = vi.fn()
    mockUseTodoStore.mockReturnValue({
      addTodo,
      toggleTodo: vi.fn(),
      removeTodo: vi.fn()
    })

    render(<TodoInput />)

    const input = screen.getByTestId('todo-input')
    await userEvent.type(input, 'New Task')
    await userEvent.click(screen.getByText('Add'))

    expect(addTodo).toHaveBeenCalledWith('New Task')
    expect((input as HTMLInputElement).value).toBe('')
  })

  it('空入力では addTodo を呼ばない', async () => {
    const addTodo = vi.fn()
    mockUseTodoStore.mockReturnValue({
      addTodo,
      toggleTodo: vi.fn(),
      removeTodo: vi.fn()
    })

    render(<TodoInput />)

    await userEvent.click(screen.getByText('Add'))

    expect(addTodo).not.toHaveBeenCalled()
  })
})
```

## userEvent vs fireEvent

- **userEvent**: ユーザーの実際の操作をシミュレート（推奨）
- **fireEvent**: 低レベルのDOMイベント（特殊なケースのみ）

```typescript
import userEvent from '@testing-library/user-event'

// 推奨
const user = userEvent.setup()
await user.type(input, 'text')
await user.click(button)

// 非推奨（特殊なケースのみ）
fireEvent.change(input, { target: { value: 'text' } })
```

## ベストプラクティス

1. **data-testid を適切に使用** - role・text での選択が優先
2. **Store をモック** - 単位テストに集中
3. **async/await を使用** - userEvent は async
4. **screen クエリを使用** - container クエリより堅牢
5. **実装詳細をテストしない** - 外部インターフェースに焦点
