# Zustand 状態管理ガイド

## Zustand とは

Zustand は React 向けの**シンプルな状態管理ライブラリ**。Redux の複雑さなしに、グローバル状態を管理できる。

```tsx
import { create } from 'zustand';

// store を定義
const useCountStore = create((set) => ({
  count: 0,
  increment: () => set((state) => ({ count: state.count + 1 })),
  decrement: () => set((state) => ({ count: state.count - 1 })),
}));

// コンポーネントで使用
const Counter = () => {
  const count = useCountStore((state) => state.count);
  const increment = useCountStore((state) => state.increment);
  
  return (
    <div>
      <p>{count}</p>
      <button onClick={increment}>+1</button>
    </div>
  );
};
```

## 設計ポイント

### 1. 何を store に入れるか判定

**Store に入れるべき**:
- グローバルに共有する状態（ユーザー情報、認証トークン）
- 複数コンポーネント間で同期が必要な状態（フィルター、ソート設定）
- 画面遷移で保持すべき状態（入力フォーム、スクロール位置）

**Component state に留めるべき**:
- ローカル UI状態（dropdown の開閉、tooltip の表示）
- 1コンポーネント内でのみ使用する一時状態

### 2. Store の構成

```tsx
// ✅ 機能ごとに分割
const useTodoStore = create((set) => ({
  // State
  todos: [],
  filter: 'all',
  
  // Actions
  addTodo: (title) => set((state) => ({
    todos: [...state.todos, { id: Date.now(), title, completed: false }]
  })),
  
  toggleTodo: (id) => set((state) => ({
    todos: state.todos.map(todo =>
      todo.id === id ? { ...todo, completed: !todo.completed } : todo
    )
  })),
  
  setFilter: (filter) => set({ filter }),
}));
```

### 3. セレクター（切り出し）

複数の状態を取得する場合は、必要なものだけを明示的に選択：

```tsx
// ❌ 非効率：store 全体に依存
const component = () => {
  const state = useTodoStore(); // 全状態を取得
  // state 全体の変更で re-render が起動
};

// ✅ 必要なもだけ取得（re-render最小化）
const TodoCount = () => {
  const todos = useTodoStore((state) => state.todos);
  return <div>{todos.length}</div>;
};

const Filter = () => {
  const filter = useTodoStore((state) => state.filter);
  const setFilter = useTodoStore((state) => state.setFilter);
  return <select value={filter} onChange={e => setFilter(e.target.value)} />;
};
```

### 4. アクション設計

1つのアクション = 1つの明確な操作。複合操作は呼び出し側で組み立て。

```tsx
// ✅ シンプルなアクション
const useAuthStore = create((set) => ({
  user: null,
  token: null,
  
  setUser: (user) => set({ user }),
  setToken: (token) => set({ token }),
  
  logout: () => set({ user: null, token: null }),
}));

// 複合操作はコンポーネント側でハンdle
const handleLogin = async (email, password) => {
  const { user, token } = await loginAPI(email, password);
  useAuthStore.setState({ user, token });
};
```

### 5. 非同期操作（API呼び出し）

**パターン1：アクション内でAPIを呼び出し**

```tsx
const useTodoStore = create((set) => ({
  todos: [],
  loading: false,
  error: null,
  
  fetchTodos: async () => {
    set({ loading: true, error: null });
    try {
      const data = await fetch('/api/todos');
      set({ todos: data, loading: false });
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },
}));
```

**パターン2：コンポーネント側で API呼び出し → store に保存**

```tsx
const useTodoStore = create((set) => ({
  todos: [],
  setTodos: (todos) => set({ todos }),
}));

const TodoList = () => {
  const setTodos = useTodoStore((state) => state.setTodos);
  
  useEffect(() => {
    fetchTodos().then(data => setTodos(data));
  }, []);
};
```

### 6. Immer ミドルウェア（ネストされたstate の更新を簡潔に）

```tsx
import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';

const useTodoStore = create<TState>()(
  immer((set) => ({
    todos: [],
    
    // Immer を使わない場合の複雑な更新
    // updateTodo: (id, updates) => set((state) => ({
    //   todos: state.todos.map(...複雑...)
    // })),
    
    // Immer を使う場合：draft を直接編集
    updateTodo: (id, updates) => set((state) => {
      const todo = state.todos.find(t => t.id === id);
      if (todo) {
        Object.assign(todo, updates);
      }
    }),
  }))
);
```

## ファイル構成

```
src/store/
├── todoStore.ts          # Todo機能のstore
├── userStore.ts          # ユーザー認証のstore
├── uiStore.ts            # UI状態（モーダル開閉等）のstore
└── index.ts              # store エクスポート (optional)
```

## ベストプラクティス

### 1. Store の型付け

```tsx
// ✅ TypeScript 型安全
interface Todo {
  id: number;
  title: string;
  completed: boolean;
}

interface TodoStore {
  todos: Todo[];
  addTodo: (title: string) => void;
  toggleTodo: (id: number) => void;
}

const useTodoStore = create<TodoStore>((set) => ({
  todos: [],
  addTodo: (title) => set((state) => ({
    todos: [...state.todos, { id: Date.now(), title, completed: false }]
  })),
  toggleTodo: (id) => set((state) => ({
    todos: state.todos.map(todo =>
      todo.id === id ? { ...todo, completed: !todo.completed } : todo
    )
  })),
}));
```

### 2. DevTools でデバッグ

```tsx
import { devtools } from 'zustand/middleware';

const useTodoStore = create<TodoStore>()(
  devtools((set) => ({
    // ... store定義
  }), { name: 'TodoStore' })
);
```

ブラウザの Redux DevTools で store の state変化を追跡できる。

### 3. Store の分割

大規模な場合は機能ごとに分割：

```
src/store/
├── features/
│   ├── todo/
│   │   └── todoStore.ts
│   ├── user/
│   │   └── userStore.ts
│   └── ui/
│       └── uiStore.ts
└── index.ts  // 各storeを再エクスポート
```

### 4. Store 間の通信

```tsx
const useUserStore = create((set) => ({
  user: null,
  setUser: (user) => set({ user }),
}));

const useTodoStore = create((set) => ({
  todos: [],
  setTodos: (todos) => set({ todos }),
  
  // 別の store を参照
  fetchUserTodos: () => {
    const user = useUserStore.getState().user;
    if (user) {
      // fetch...
    }
  },
}));
```
