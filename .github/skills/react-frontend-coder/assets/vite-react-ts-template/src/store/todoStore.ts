import { create } from "zustand";

export type Todo = {
  id: string;
  title: string;
  completed: boolean;
};

type TodoState = {
  todos: Todo[];
  addTodo: (title: string) => void;
  toggleTodo: (id: string) => void;
};

const initialTodos: Todo[] = [
  { id: "t1", title: "Review backlog", completed: true },
  { id: "t2", title: "Prepare sprint board", completed: false },
  { id: "t3", title: "Share summary notes", completed: false }
];

export const useTodoStore = create<TodoState>((set) => ({
  todos: initialTodos,
  addTodo: (title) =>
    set((state) => ({
      todos: [
        ...state.todos,
        { id: crypto.randomUUID(), title, completed: false }
      ]
    })),
  toggleTodo: (id) =>
    set((state) => ({
      todos: state.todos.map((todo) =>
        todo.id === id ? { ...todo, completed: !todo.completed } : todo
      )
    }))
}));
