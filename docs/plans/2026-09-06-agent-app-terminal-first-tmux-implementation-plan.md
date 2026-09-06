# agent-app ターミナル中心tmux会話UI 実装計画

## 方針

既存の未コミット修正を保ったまま、テストを先に追加し、小さい段階でtmuxを画面の正へ切り替える。CLI出力解析は一度に削除せず、UI依存を外してから不要部分を整理する。

## 1. 永続モデルと互換読み込み

対象:

- `tools/agent-app/src/main/store.js`
- `tools/agent-app/test/app.test.js`

作業:

1. セッションへ `terminalSession` と `terminalSnapshots` を追加する。
2. 既存JSONを読む際、欠落フィールドを既定値へ正規化する。
3. `terminalSession` の状態、時刻、所有者を更新する専用関数を追加する。
4. スナップショット追加時に件数と容量の上限を適用する。
5. 会話削除とアーカイブを区別できる永続状態を追加する。

テスト:

- 旧形式の会話をそのまま読み込める。
- terminal sessionの作成、更新、期限計算を保存できる。
- スナップショットが上限を超えた場合に古いものから回収される。
- 会話削除とアーカイブの状態が混同されない。

## 2. tmuxライフサイクル管理

対象:

- `tools/agent-app/src/main/tmux.js`
- `tools/agent-app/src/main/tmuxLifecycle.js`（新規）
- `tools/agent-app/test/tmux.test.js`
- `tools/agent-app/test/tmux-lifecycle.test.js`（新規）

作業:

1. セッション名を会話IDから決定的に生成し、1会話1セッションを保証する。
2. `ensureSession`、`switchAgent`、`archiveSession`、`deleteSession`、`reconcileSessions` をライフサイクル管理へ分離する。
3. 最終利用時刻と24時間の `expiresAt` を更新する。
4. 起動時と定期実行で、DB記録と `tmux list-sessions` を照合する。
5. 期限超過かつ非実行中の管理対象セッションだけを削除する。
6. 所有権不明、実行中、管理外プレフィックスのセッションは削除しない。
7. pane dead時に最終キャプチャを返し、終了済みへ遷移する。

テスト:

- 同じ会話で `ensureSession` を繰り返しても作成は1回だけ。
- エージェント切替でtmuxセッション名と総数が変わらない。
- 最終利用から23時間59分は保持、24時間超過は回収対象。
- active、所有権不明、agent-app管理外は回収しない。
- killやcapture失敗時に他セッションへ影響しない。

## 3. IPCとアプリ起動・終了処理

対象:

- `tools/agent-app/src/main/ipc.js`
- `tools/agent-app/src/main/main.js`
- `tools/agent-app/src/preload.js`
- `tools/agent-app/test/app.test.js`

作業:

1. 送信APIを「受付」「tmuxへ送信」「失敗」のアプリ既知状態として返す。
2. 文字列解析由来の完了状態を送信欄の制御条件から外す。
3. エージェント切替APIに、最終キャプチャ、旧CLI終了、新CLI起動、切替イベント保存をまとめる。
4. 会話削除時にtmux即時削除を実行してから永続データを削除する。
5. 会話アーカイブ時はtmuxを終了し、履歴とスナップショットを残す。
6. アプリ起動時にreconcileを1回実行し、その後は低頻度タイマーで清掃する。
7. 正常終了ではセッションをkillせず、idleと24時間後の期限を記録する。

テスト:

- `send-keys` とEnterの成功後だけ `sent` を返す。
- 送信失敗でユーザー入力を消去する指示を返さない。
- 削除、アーカイブ、アプリ終了が正しいライフサイクル関数を呼ぶ。
- reconcileの重複起動を防止する。

## 4. ターミナル中心の会話画面

対象:

- `tools/agent-app/src/renderer/index.html`
- `tools/agent-app/src/renderer/styles.css`
- `tools/agent-app/src/renderer/renderer.js`
- `tools/agent-app/src/renderer/term.js`
- `tools/agent-app/test/app.test.js`
- `tools/agent-app/test/electron-smoke.test.js`

作業:

1. 現在の折りたたみ端末を会話の主表示へ移す。
2. ヘッダーにエージェント、モデル、接続状態を表示する。
3. 入力欄をCLIの推定busy状態から独立させ、常に追加入力可能にする。
4. `準備中`、`送信済み`、`送信失敗`、`セッション終了` のみを送信欄付近へ表示する。
5. 送信成功まで入力内容を保持し、成功時のみ消去する。
6. 端末フォーカス時は特殊キーを既存のxterm入力経路へ渡す。
7. スナップショットをエージェント、モデル、時刻付きの折りたたみ表示にする。
8. 切替イベントを会話タイムラインへ表示する。

テスト:

- セッション作成待ちの間に `準備中` が即時表示される。
- 送信済み表示に対象エージェントと時刻が含まれる。
- busy表示中でも入力欄から回答を送れる。
- エージェント切替後に旧画面が読み取り専用で残る。
- 端末への特殊キーと入力欄の通常送信が競合しない。

## 5. CLI意味解析への依存縮小

対象:

- `tools/agent-app/src/main/response.js`
- `tools/agent-app/src/main/agentCli.js`
- `agents/*.json`
- `tools/agent-app/test/response.test.js`

作業:

1. `thinking`、`answer`、`attention` 等の推定結果がターミナル表示や入力可否を支配している箇所を洗い出す。
2. 会話履歴への補助記録として必要な解析だけを残し、ライブUIの正はtmux画面へ統一する。
3. Cursor、Aider、Codex、Copilot、Kiro、Claude固有の文言パターンを、必須制御ではなく任意メタデータへ降格する。
4. 既存の回帰テストを新しい責務へ合わせて更新する。

完了条件:

- 未知の質問表示やspinner文字列でも入力欄が停止しない。
- 回答分離に失敗しても端末上の回答は欠落しない。
- CLI設定ファイルへパターンを追加しなくても基本対話が成立する。

## 6. Windows/WSL境界テスト

対象:

- `tools/agent-app/src/main/host.js`
- `tools/agent-app/test/host.test.js`
- Windows実機のスモーク手順書

自動テスト:

1. `C:\\...`、`/mnt/c/...`、`\\\\wsl$\\...` のパス変換。
2. `wsl.exe`常駐プロセス切断後の再接続。
3. UTF-8、ANSI色、画面サイズ情報の受け渡し。
4. tmuxコマンドが必ずWSL内部で実行されること。

実機テスト:

1. Windows版agent-appから初回送信し、即時に準備中表示が出る。
2. 各CLIの質問待ちへ入力欄と端末直接入力の両方で回答できる。
3. app終了後24時間以内に再接続できる。
4. 期限超過セッション、会話削除、アーカイブで期待どおり回収される。
5. WSL停止後にセッションを再作成し、会話UIが復帰する。
6. 日本語、絵文字、結合文字、リサイズを確認する。
7. WSL ext4と `/mnt/c` で初回起動時間、送信遅延、CPU使用率を比較する。

## 7. 段階導入と検証ゲート

1. 機能フラグ `terminalFirstConversation` を開発時だけ有効にする。
2. 単体テストを通す: `cd tools/agent-app && npm test`
3. macOS上で既存CLIの回帰スモークを実施する。
4. Windows/WSL実機スモークを通す。
5. tmuxセッション数、capture失敗、send失敗、再接続結果を診断ログへ記録する。
6. 既存UIと比較し、質問応答、エージェント切替、再接続の完了条件を満たしたら既定を切り替える。
7. 安定後に旧チャット描画と不要な意味解析を削除する。

## 完了条件

- 1会話につきライブtmuxセッションが最大1個である。
- 追加送信とエージェント切替でセッションが増えない。
- app終了後24時間以内は再接続でき、期限後は安全に回収される。
- 会話削除で対象tmuxだけが即時削除される。
- CLIが未知の質問形式を出してもユーザーが確認・入力できる。
- 送信受付、成功、失敗がCLI文言に依存せず表示される。
- 切替前の画面を読み取り専用スナップショットで確認できる。
- Windows版agent-appとWSL上のtmuxで実機スモークを通過する。

