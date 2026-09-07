# agent-app 入力安定化・ワークフロー履歴表示 実装計画

設計: `docs/plans/2026-09-07-agent-app-stable-input-and-workflow-history-design.md`

## 実装原則

- 既存のリポジトリ別データ取得、IPC、run store を変更しない。
- 保存済みワークフローと実行履歴を同じ一覧へ混ぜない。
- statemachine-maker と agent-app の共有 renderer を正典とし、vendor コピーは既存手順で同期する。
- 表示変更を DOM、純粋状態、Electron smoke の順で検証する。

## ToDo

### 1. ベースラインを確認する

`tools/agent-app` と `tools/statemachine-maker` の関連テストを実行し、入力モード、埋め込みワークベンチ、flow run
の既存動作を確認する。環境依存の失敗は変更前からのものか記録する。

### 2. 入力ドックの共通レイアウトを実装する

`tools/agent-app/src/renderer/index.html` と `styles.css` を更新し、モード切替、入力面、操作面を共通グリッドへ置く。
メッセージモードと端末操作モードの基準高を一致させ、添付や textarea リサイズ時だけ拡張できるようにする。

### 3. 会話履歴の初期状態を修正する

`conversation-history` の初期 `open` を削除し、`renderer.js` の自動開閉条件を整理する。tmux 会話では既定で閉じ、
利用者が開いた状態を通常の render で上書きしない。ヘッドレス会話の主履歴表示は維持する。

### 4. ワークフロー詳細へ履歴表示を追加する

`tools/statemachine-maker/src/renderer/flow.js` で、埋め込み表示でも利用できる `実行履歴` 導線を中央詳細へ追加する。
既存 `runList`、`runRead`、`runHtml`、polling を再利用し、選択中 root 以外の履歴を保持しない。

### 5. vendor と埋め込みスタイルを同期する

既存 vendor スクリプトで agent-app 側の statemachine renderer を同期する。`automation-workbench.css` は内部一覧を
隠す現在の責務を維持し、中央履歴の新しい導線だけが表示されるよう調整する。

### 6. リポジトリスコープの回帰テストを追加する

タスクとワークフローのロードが常に選択中 `state.repo` を渡すこと、repo 切替で task/workflow/run の選択が混ざらない
ことをテストする。タスク画面の共通リポジトリ選択は維持する。

### 7. UIテストと実機確認を行う

入力モード切替、履歴の既定閉じ、履歴を開いた後の保持、ワークフロー履歴から run detail への遷移を自動テストする。
Electron smoke が利用可能なら、通常幅と狭幅で入力ドックの高さ、重なり、Tab 順を確認する。

### 8. 全体回帰を行う

`tools/statemachine-maker` と `tools/agent-app` の全テスト、`git diff --check`、配布物同期チェックを実行する。
失敗時は入力UI、共有 renderer、vendor 差分のどこに原因があるか切り分ける。

## 完了条件

- メッセージ／端末操作の切替前後で入力ドックの外形高が変わらない。
- 会話履歴は既定で閉じ、利用者の開閉を通常の再描画が妨げない。
- タスク一覧は選択中リポジトリだけを表示する。
- ワークフロー画面から選択中リポジトリの過去 run と詳細を確認できる。
- 既存の実行、成果取得、ログ、回答、再実行が回帰していない。

