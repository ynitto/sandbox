import { useTodoStore } from "./store/todoStore";

export default function App() {
  const todos = useTodoStore((state) => state.todos);

  return (
    <div className="page">
      <header className="header">
        <h1>ToDo</h1>
        <p>Fixed list rendered from Zustand state.</p>
      </header>
      <section className="card">
        <ul className="list">
          {todos.map((todo) => (
            <li
              key={todo.id}
              className={`item ${todo.completed ? "done" : ""}`}
            >
              <span className="title">{todo.title}</span>
              <span className="status">
                {todo.completed ? "Done" : "Todo"}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
