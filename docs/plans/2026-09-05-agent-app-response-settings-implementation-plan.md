# agent-app レスポンス表示・共通設定 実装計画

> 設計: [2026-09-05-agent-app-response-settings-design.md](./2026-09-05-agent-app-response-settings-design.md)

## 実装原則

- 既存の未コミット変更を保持し、重なるファイルは現在の内容へ差分を積み重ねる。
- テスト駆動で、各段階を Red → Green → Refactor の順に進める。
- 新しい依存パッケージは追加しない。
- `message.text`、既存の session JSON、既存 IPC を後方互換に保つ。
- 構造化情報がなくても、回答の送受信を失敗させない。

## Task 1: 設定契約と旧設定移行

対象:

- `tools/agent-app/src/main/store.js`
- `tools/agent-app/src/main/settings.js`（新規）
- `tools/agent-app/test/settings.test.js`（新規）
- `tools/agent-app/test/app.test.js`

手順:

1. 旧 `config.json` から `instructions` と `execution` を補完する失敗テストを書く。
2. `lastCli` / `lastModel` が `small` / `medium` / `large` の初期値へ移行されるテストを書く。
3. 方針、Tier、同時実行数、共通指示、開始アクションの不正値を補正または拒否するテストを書く。
4. 未知キーを保持するテストを書く。
5. 設定の正規化・検証を `settings.js` の純粋関数として実装する。
6. `store.saveConfig` を一時ファイル＋置換へ変更し、保存失敗時に既存ファイルが残るテストを追加する。

完了条件:

- 旧設定でアプリを開いても既存値を失わない。
- UI に必要な全ユーザー設定が正規化済みで返る。

## Task 2: 起動方針と Tier の解決

対象:

- `tools/agent-app/src/main/settings.js`
- `tools/agent-app/src/main/ipc.js`
- `tools/agent-app/src/main/store.js`
- `tools/agent-app/test/settings.test.js`
- `tools/agent-app/test/app.test.js`

手順:

1. `recommended → medium`、`saving → small`、`quality → large` の決定表テストを書く。
2. ターン単位の方針指定、直接指定、設定既定の優先順位テストを書く。
3. 未設定 Tier と利用不能 CLI を明示エラーにするテストを書く。
4. 解決結果へ `policy`、`tier`、`cli`、`model`、`source` を含める純粋関数を実装する。
5. session と user/assistant message に実行時の解決結果を保存する。
6. renderer から届いた CLI 名をそのまま信頼せず、main で再解決する。

完了条件:

- 同じ設定と入力から常に同じ CLI / model が選ばれる。
- 品質順位やコスト推測を行わない。

## Task 3: 共通指示・スキル・開始アクション

対象:

- `tools/agent-app/src/main/sessionSetup.js`（新規）
- `tools/agent-app/src/main/agentCli.js`
- `tools/agent-app/src/main/ipc.js`
- `tools/agent-app/src/main/store.js`
- `tools/agent-app/test/session-setup.test.js`（新規）
- `tools/agent-app/test/app.test.js`

手順:

1. 空・無効時 no-op、固定ヘッダー、二重注入防止、文字数上限のテストを書く。
2. 推奨スキルを決定的な順序で指示ブロックへ描画するテストを書く。
3. `skill_command_prefix` を agent 定義から読み、Codex の `$` と他 CLI の `/` を変換するテストを書く。
4. 開始アクションの計画を、shell command と skill action に分ける純粋関数を実装する。
5. shell command を作業フォルダで順番に実行し、60 秒／合計 120 秒を守る処理を実装する。
6. `warn` と `fail` の挙動をテストする。
7. CLI session entry に適用済み状態を保持し、初回だけ適用するテストを書く。
8. 固定出力契約を持つ automation の内部 AI 経路には共通指示を渡さないことを回帰テストにする。

完了条件:

- 新規 CLI セッションの最初だけ開始アクションが働く。
- 保存されるユーザーメッセージには注入用本文が混ざらない。

## Task 4: 同時実行ゲート

対象:

- `tools/agent-app/src/main/executionGate.js`（新規）
- `tools/agent-app/src/main/ipc.js`
- `tools/agent-app/src/preload.js`
- `tools/agent-app/test/execution-gate.test.js`（新規）

手順:

1. 1〜8 の上限、同一 session 二重取得、上限拒否のテストを書く。
2. 正常終了、例外、停止、spawn 失敗で枠が解放されるテストを書く。
3. 取得・解放を `try/finally` で扱う小さな gate を実装する。
4. 実行中件数と上限を返す IPC を追加する。
5. renderer の事前表示に関係なく main が上限を強制する統合テストを書く。

完了条件:

- どの終了経路でも枠がリークしない。
- 上限時に待機中の依頼を保持しない。

## Task 5: 共通レスポンスモデルと CLI adapter

対象:

- `tools/agent-app/src/main/response.js`（新規）
- `tools/agent-app/src/main/ipc.js`
- `tools/agent-app/src/main/tmux.js`
- `tools/agent-app/test/response.test.js`（新規）
- `tools/agent-app/test/fixtures/`（新規 fixture）

手順:

1. `thinking` と `information` の正規化・上限・未知型除外のテストを書く。
2. Codex JSONL の thread、reasoning、command、file change、tool、turn completion fixture を用意する。
3. 壊れた JSONL 行を詳細ログへ回し、有効行を失わないテストを書く。
4. Codex adapter を実装し、最終回答は output file を優先する。
5. generic adapter を実装し、開始、処理中、確認待ち、終了、経過時間、終了コードを共通形式へ変換する。
6. tmux の端末本文を推測分類しない回帰テストを書く。
7. message の `parts` は内容がある場合だけ保存する。

完了条件:

- adapter の失敗で最終回答が消えない。
- 既存の session ID capture と error classification が維持される。

## Task 6: ターン IPC のストリーミング統合

対象:

- `tools/agent-app/src/main/ipc.js`
- `tools/agent-app/src/main/tmux.js`
- `tools/agent-app/src/preload.js`
- `tools/agent-app/test/app.test.js`
- `tools/agent-app/test/tmux.test.js`

手順:

1. `turn:progress` と `turn:info` の payload 契約テストを書く。
2. headless stdout の一行を adapter に通し、ユーザー向けイベントだけを送る。
3. tmux phase を generic progress へ変換する。
4. main 側でターン中の parts を集約し、`turn:done` 前に session へ保存する。
5. 中断・エラーでも収集済み情報が保存されるテストを書く。
6. renderer が古い main と接続して新イベントを受け取れなくても回答表示できる状態を保つ。

完了条件:

- 作業中イベントと保存後メッセージが同じ内容になる。

## Task 7: 三層レスポンス UI

対象:

- `tools/agent-app/src/renderer/renderer.js`
- `tools/agent-app/src/renderer/styles.css`
- `tools/agent-app/src/renderer/index.html`
- `tools/agent-app/test/app.test.js`
- `tools/agent-app/test/electron-smoke.test.js`

手順:

1. 回答が assistant の会話吹き出しとして常時表示される静的テストを書く。
2. thinking / information が空なら区分を作らないテストを書く。
3. 作業中は thinking を展開し、完了後は閉じる状態遷移を実装する。
4. error / stopped / attention で information を開く。
5. command、file、tool、status、error の最小 renderer を実装する。
6. 生ログを information の詳細表示へ移す。
7. 作業中カードを同じ要素のまま更新し、不要なスクロールジャンプを防ぐ。
8. 375 / 768 / 1024 / 1440px のレイアウト、focus-visible、reduced-motion を CSS へ追加する。
9. Electron smoke で三層、開閉、古い回答を確認する。

完了条件:

- 回答が最も強い視覚階層を持ち、進捗と証跡は必要時だけ開ける。

## Task 8: 設定ダイアログの再構築

対象:

- `tools/agent-app/src/renderer/index.html`
- `tools/agent-app/src/renderer/renderer.js`
- `tools/agent-app/src/renderer/styles.css`
- `tools/agent-app/src/preload.js`
- `tools/agent-app/test/app.test.js`
- `tools/agent-app/test/electron-smoke.test.js`

手順:

1. 「アプリ／共通指示／実行制御」の三分類と tab semantics のテストを書く。
2. 狭幅用 select または一列タブへの切替を実装する。
3. 推奨スキル inventory の main / preload API を追加し、検索＋chip UI を実装する。
4. 開始アクションの追加、種類、内容、失敗時、並び替え、削除 UI を実装する。
5. 起動方針 radio cards と三 Tier の agent/model controls を実装する。
6. Ask、同時実行数、tmux、worktree、WSL を対応する分類へ配置する。
7. 一括保存、項目直下エラー、保存成功表示を実装する。
8. 内部状態キーが UI に出ないこと、未知キーが保存後も残ることをテストする。

完了条件:

- 生 JSON を触らず、すべてのユーザー設定を UI から変更できる。

## Task 9: Composer と実行状態の統合

対象:

- `tools/agent-app/src/renderer/index.html`
- `tools/agent-app/src/renderer/renderer.js`
- `tools/agent-app/src/renderer/styles.css`
- `tools/agent-app/test/app.test.js`

手順:

1. 実行設定の既定が `defaultPolicy` になるテストを書く。
2. おすすめ／節約／品質重視／直接指定の切替 UI を実装する。
3. policy 選択時に解決済み Tier、CLI、model を要約表示する。
4. 直接指定時だけ既存 CLI / model controls を表示する。
5. 利用不能 CLI、未設定 Tier、同時実行上限を composer 内へ表示して送信を止める。
6. 既存会話の CLI / model と新しい policy 表示の互換を確認する。

完了条件:

- 利用者が送信前に「どの方針・Tier・エージェントで動くか」を確認できる。

## Task 10: 全体検証と整理

1. `rtk npm test` を `tools/agent-app` で実行する。
2. Electron smoke を実行し、起動、設定保存、会話、停止、再起動を確認する。
3. Codex structured fixture と generic / tmux fallback の双方を確認する。
4. 旧 `config.json` と旧 session fixture で移行を確認する。
5. 変更差分を見直し、重複した renderer / IPC ロジックを整理する。
6. 要件ごとのテスト証跡を最終報告へまとめる。

最終完了条件:

- 会話の回答が吹き出しとして表示され、取得可能な思考・進捗と実行情報を別層で確認できる。
- 共通指示、スキル、開始アクション、起動方針、Tier、実行制御を UI から保存・適用できる。
- 既存の会話、設定、タスク、ワークフローを壊さない。
- 全テストと Electron smoke が成功する。

