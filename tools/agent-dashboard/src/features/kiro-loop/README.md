# kiro-loop 制御面

Windows の agent-dashboard から、WSL 上の kiro-loop **tmux セッションを視聴**する制御面。

- ジョブ一覧・設定・実行ボタンは **Cowork** 側
- この feature は **生きている tmux の capture-pane 視聴**（Phase A）
- 将来 Phase B で `node-pty` + `xterm.js` の `tmux attach` に差し替える想定

設計: [`docs/designs/agent-dashboard-kiro-loop-terminal-design.md`](../../../../../docs/designs/agent-dashboard-kiro-loop-terminal-design.md)

## セッションの発見

`tmux ls` のセッション名だけに頼らない（頼れない）：

1. **kiro-loop 状態ファイル**（`~/.kiro/loop-state/*.json`）— デーモンが記録した
   ワーカーペインの pane_id を直接視聴する。kiro-loop を **tmux セッションの中で起動**
   した場合、ペインは人のセッション（名前は任意）内に分割で作られ、セッション名では
   見つけられないため、この経路が必須。pane_id は tmux サーバ全体で一意なので
   `capture-pane -t %N` はセッション名に依存しない。
2. **セッション名の接頭辞**（既定 `kiro`）— スタンドアロン起動の自動命名
   （`kiro-loop-<label>-<digest>-<id>`）と `kiro-loop send` の既定セッション（`kiro`）
   の両方を拾い、repo の digest／ペイン cwd で絞る。

repo が Windows ドライブ上（`C:\...`）でも `/mnt/c/...` へ寄せて cwd と突き合わせる。

## IPC

| API | チャネル |
|-----|----------|
| `api.kiroLoopListSessions({ repo? })` | `kiroLoop:listSessions` |
| `api.kiroLoopCapture({ target, lines? })` | `kiroLoop:capture` |

## UI

Cowork タブの各ジョブ「端末」→ 読み取り専用パネル。セッションが無いときは空状態。
