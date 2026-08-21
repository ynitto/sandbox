# 定常業務の agent-loop 制御面

Windows の agent-dashboard から、WSL 上の agent-loop **tmux セッションを視聴**する制御面。

- ジョブ一覧・設定・実行ボタンは **Cowork** 側
- この feature は **生きている tmux の capture-pane 視聴**（Phase A）
- 将来 Phase B で `node-pty` + `xterm.js` の `tmux attach` に差し替える想定
- Phase C の構造化状態（最終実行時刻・alive/busy）と `agent-loop send` 経由の復旧送信は実装済み
  （busy 拒否は renderer が「送信待機」に変換して自動再送する）

設計: [`docs/designs/agent-dashboard-design.md`](../../../../../docs/designs/agent-dashboard-design.md) §4、
IPC と設定キーは [`docs/specs/agent-dashboard-spec.md`](../../../../../docs/specs/agent-dashboard-spec.md) §2.3・§4.3

## セッションの発見

`tmux ls` のセッション名だけに頼らない（頼れない）：

1. **状態ファイル**（`~/.agents/loop-state/*.json`）— デーモンが記録した
   ワーカーペインの pane_id を直接視聴する。agent-loop を **tmux セッションの中で起動**
   した場合、ペインは人のセッション（名前は任意）内に分割で作られ、セッション名では
   見つけられないため、この経路が必須。pane_id は tmux サーバ全体で一意なので
   `capture-pane -t %N` はセッション名に依存しない。
2. **セッション名の接頭辞**（既定 `kiro`）— `agent-loop send` が作るスタンドアロン
   セッションを拾い、repo の digest／ペイン cwd で絞る。

repo が Windows ドライブ上（`C:\...`）でも `/mnt/c/...` へ寄せて cwd と突き合わせる。

## IPC

| API | チャネル |
|-----|----------|
| `api.routineAgentListSessions({ repo? })` | `routineAgent:listSessions` |
| `api.routineAgentCapture({ target, lines? })` | `routineAgent:capture` |
| `api.routineAgentState({ repo? })` | `routineAgent:state`（loop-state の last_sent_at ＋ slot の busy） |
| `api.routineAgentSend({ repo, target, prompt })` | `routineAgent:send`（`agent-loop send` 経由。busy 拒否は `busy: true`） |
| `api.routineAgentQueueMessage({ repo?, agent, subject?, body })` | `routineAgent:queueMessage`（`agent-loop msg --to <agent> --from agent-dashboard` 経由の受信ボックス投函） |
| `api.routineAgentQueue({ repo?, agent? })` | `routineAgent:queue`（`~/.kiro/agents/<name>/inbox` の待機中一覧 + `.processed` 件数。読み取り専用） |

## メッセージキュー投函

復旧送信（`send`）は相手が busy だと拒否されるが、**投函（`msg`）は常に受理される**——
受信側の InboxWatcher が手すきになった順に処理する。busy は失敗ではなく待機で、
UI（実行状況ダイアログの「依頼を積む」）も待機中／処理済みとして表示する。
投函は CLI 依頼のみ（生の send-keys もキューへのファイル直書きもしない）。
待ち行列の表示は inbox の読み取りだけで、ファイルの移動・削除は受信側に任せる。

## UI

Cowork タブの各ジョブ「実行状況」→ 稼働状態テーブル（予定別の最終実行時刻・状態）＋
送信フォーム（予定の名前 or 自由文）＋読み取り専用パネル。セッションが無いときは空状態。

文言は tmux / セッション / プロンプトといった内部語を出さず、「予定の名前」「応答中」
「エージェントの画面をそのまま映しています」のように、何が起きているかで表す
（予定名が設定ファイル由来であることが分からず「MR コメント返答とは何か」が伝わらなかったため）。
