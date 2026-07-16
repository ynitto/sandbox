# kiro-loop 制御面

Windows の agent-dashboard から、WSL 上の kiro-loop **tmux セッションを視聴**する制御面。

- ジョブ一覧・設定・実行ボタンは **Cowork** 側
- この feature は **生きている tmux の capture-pane 視聴**（Phase A）
- 将来 Phase B で `node-pty` + `xterm.js` の `tmux attach` に差し替える想定

設計: [`docs/designs/agent-dashboard-kiro-loop-terminal-design.md`](../../../../../docs/designs/agent-dashboard-kiro-loop-terminal-design.md)

## IPC

| API | チャネル |
|-----|----------|
| `api.kiroLoopListSessions({ repo? })` | `kiroLoop:listSessions` |
| `api.kiroLoopCapture({ target, lines? })` | `kiroLoop:capture` |

## UI

Cowork タブの各ジョブ「端末」→ 読み取り専用パネル。セッションが無いときは空状態。
