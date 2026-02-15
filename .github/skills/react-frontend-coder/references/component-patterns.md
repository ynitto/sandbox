# React コンポーネント設計パターン

## 基本原則

### 1. 単一責任の原則（SRP）

1つのコンポーネントは 1つの役割を持つ。

```tsx
// ❌ 複数の責務を持つコンポーネント
export const TodoItem = ({ todo, onToggle, onDelete, filters, sort }) => {
  return (
    <div>
      {/* Todo表示 */}
      {/* フィルター機能 */}
      {/* ソート機能 */}
      {/* 削除機能 */}
    </div>
  );
};

// ✅ 役割を分割
export const TodoItem = ({ todo, onToggle, onDelete }) => (
  <div onClick={onToggle}>
    <span>{todo.title}</span>
    <button onClick={onDelete}>削除</button>
  </div>
);

export const TodoList = ({ todos, filters, sort }) => (
  <div>
    {filterAndSort(todos, filters, sort).map(todo => (
      <TodoItem key={todo.id} todo={todo} ... />
    ))}
  </div>
);
```

### 2. Props vs State

**何を Props か State か判定**:

| 外部から渡される？ | 複数コンポーネント共有？ | →決定 |
|----------------|-------------------|-------|
| Yes | - | Props |
| No | Yes | State（store） |
| No | No | Component state |

```tsx
// ✅ Props で受け取る：親が管理する値
const UserCard = ({ name, email }) => <div>{name} {email}</div>;

// ✅ State で管理：コンポーネント内でのみ使用
const SearchBox = () => {
  const [query, setQuery] = useState('');
  return <input value={query} onChange={e => setQuery(e.target.value)} />;
};

// ✅ Store で管理：複数コンポーネント間で共有
const { currentUser } = useUserStore();
```

## パターン集

### 1. プレゼンテーション vs コンテナ

**プレゼンテーショナルコンポーネント**: UI のみ責務

```tsx
// Button.tsx
export const Button = ({ 
  children, 
  onClick, 
  disabled 
}: { 
  children: React.ReactNode; 
  onClick: () => void; 
  disabled?: boolean; 
}) => (
  <button onClick={onClick} disabled={disabled} className="btn">
    {children}
  </button>
);
```

**コンテナコンポーネント**: ロジック + store連携

```tsx
// TodoListContainer.tsx
import { useStore } from './store';
import { TodoList } from './TodoList'; // プレゼンテーション

export const TodoListContainer = () => {
  const todos = useStore(state => state.todos);
  const toggleTodo = useStore(state => state.toggleTodo);
  
  return (
    <TodoList 
      todos={todos} 
      onToggle={toggleTodo}
    />
  );
};
```

### 2. Custom Hooks でロジックを再利用

複数コンポーネント間で同じロジックを共有する場合：

```tsx
// useFormInput.ts - よく使う入力フィールドロジック
export const useFormInput = (initialValue = '') => {
  const [value, setValue] = useState(initialValue);
  const reset = () => setValue(initialValue);
  
  return {
    value,
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setValue(e.target.value),
    reset
  };
};

// コンポーネントで使用
const LoginForm = () => {
  const email = useFormInput();
  const password = useFormInput();
  
  return (
    <>
      <input {...email.bind} type="email" />
      <input {...password.bind} type="password" />
    </>
  );
};
```

### 3. Compound Components パターン

複数コンポーネントで state を共有する場合：

```tsx
// Dialog.tsx
const DialogContext = React.createContext<{
  isOpen: boolean;
  close: () => void;
} | null>(null);

export const Dialog = ({ children }: { children: React.ReactNode }) => {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <DialogContext.Provider value={{ isOpen, close: () => setIsOpen(false) }}>
      {children}
    </DialogContext.Provider>
  );
};

export const DialogTrigger = ({ children }: { children: React.ReactNode }) => {
  const ctx = useContext(DialogContext);
  return <button onClick={() => ctx?.isOpen}>{children}</button>;
};

export const DialogContent = ({ children }: { children: React.ReactNode }) => {
  const ctx = useContext(DialogContext);
  return ctx?.isOpen && <div>{children}</div>;
};

// 使用
<Dialog>
  <DialogTrigger>Open</DialogTrigger>
  <DialogContent>Content</DialogContent>
</Dialog>
```

## ファイル構成

```
src/
├── components/
│   ├── common/              # 全体で再利用
│   │   ├── Button.tsx
│   │   ├── Card.tsx
│   │   └── ...
│   ├── layout/
│   │   ├── Header.tsx
│   │   ├── Sidebar.tsx
│   │   └── ...
│   └── features/            # 機能ごと
│       ├── todo/
│       │   ├── TodoItem.tsx
│       │   ├── TodoList.tsx
│       │   └── TodoContainer.tsx
│       └── user/
│           ├── UserProfile.tsx
│           └── ...
├── hooks/
│   ├── useFormInput.ts
│   ├── useAuth.ts
│   └── ...
├── store/
│   ├── todoStore.ts
│   ├── userStore.ts
│   └── ...
└── App.tsx
```

## パフォーマンス最適化

### Re-render最小化

```tsx
// ❌ 毎回新しいオブジェクトを作成 → 子が毎回re-render
const Parent = () => {
  const value = { count: 1 }; // 毎回新規生成
  return <Child data={value} />;
};

// ✅ メモ化
const Parent = () => {
  const value = useMemo(() => ({ count: 1 }), []);
  return <Child data={value} />;
};

// ✅ または storeに移動
const Parent = () => {
  const value = useStore(state => state.value);
  return <Child data={value} />;
};
```

### コンポーネントメモ化

```tsx
// 複雑な計算を持つ子は React.memo でラップ
const ListItem = React.memo(({ item, onDelete }: Props) => {
  // 重い計算...
  return <div>{item}</div>;
});
```
