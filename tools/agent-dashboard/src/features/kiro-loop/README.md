# kiro-loop 制御面

Windows の agent-dashboard から、WSL 上の kiro-loop **tmux セッションを視聴**する制御面。

- ジョブ一覧・設定・実行ボタンは **Cowork** 側
- この feature は **生きている tmux の capture-pane 視聴**（Phase A）
- 将来 Phase B で `node-pty` + `xterm.js` の `tmux attach` に差し替える想定
- Phase C の構造化状態（最終実行時刻・alive/busy）と `kiro-loop send` 経由の復旧送信は実装済み
  （busy 拒否は renderer が「送信待機」に変換して自動再送する）

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
| `api.kiroLoopState({ repo? })` | `kiroLoop:state`（loop-state の last_sent_at ＋ slot の busy） |
| `api.kiroLoopSend({ repo, target, prompt })` | `kiroLoop:send`（`kiro-loop send` 経由。busy 拒否は `busy: true`） |

## UI

Cowork タブの各ジョブ「実行状況」→ 稼働状態テーブル（予定別の最終実行時刻・状態）＋
送信フォーム（予定の名前 or 自由文）＋読み取り専用パネル。セッションが無いときは空状態。

文言は tmux / セッション / プロンプトといった内部語を出さず、「予定の名前」「応答中」
「エージェントの画面をそのまま映しています」のように、何が起きているかで表す
（予定名が設定ファイル由来であることが分からず「MR コメント返答とは何か」が伝わらなかったため）。
