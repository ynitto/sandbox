# Agent Dashboard: 定常業務・実行状況の段階表示UI 実装計画

## 実装方針

[設計書](./2026-07-19-agent-dashboard-routine-flow-progressive-ui-design.md)に従い、表示専用の選択状態と
キャッシュ層を先に追加し、その上で定常業務、実行状況の順に画面を変更する。既存IPC契約は可能な
限り維持し、概要データは既存取得結果をクライアント側で絞り込む。

## Task 1: 表示状態と詳細キャッシュの基盤

対象:

- `tools/agent-dashboard/src/renderer/renderer.js`
- `tools/agent-dashboard/test/cowork.test.js`
- `tools/agent-dashboard/test/flow-relationship.test.js`

作業:

1. 定常業務ID、実行ID、工程IDの選択状態をプロジェクト単位で保持する。
2. 実行詳細と定常業務履歴の上限付きキャッシュを追加する。
3. 同一キーの取得中Promiseを共有し、重複取得を防ぐ。
4. 取得開始時の選択トークンを保持し、古い応答が新しい選択を上書きしないようにする。
5. 実行、編集、削除、キャンセル、再実行、手動更新の無効化関数を追加する。
6. キャッシュのヒット、重複取得集約、上限、無効化、競合応答を単体テストする。

確認:

```sh
cd tools/agent-dashboard
node test/cowork.test.js
node test/flow-relationship.test.js
```

## Task 2: 定常業務の上部セレクター

対象:

- `tools/agent-dashboard/src/renderer/renderer.js`
- `tools/agent-dashboard/src/renderer/styles.css`
- `tools/agent-dashboard/test/cowork.test.js`
- `tools/agent-dashboard/test/user-centered-ui.test.js`

作業:

1. `coworkVisibleEntries` の結果から、名前、状態、最終結果、次回予定を持つ表示モデルを作る。
2. 選択中IDを維持し、未選択時は要対応、直近更新の優先順で初期選択する。
3. 全件カードを、上部のコンパクトなセレクターへ置き換える。
4. `aria-selected`、roving tabindex、矢印キー操作を実装する。
5. 長い名前と多数項目をセレクター領域内で処理し、詳細領域を押し出さないCSSを追加する。
6. 空状態、削除後の選択移動、再描画後の選択維持をテストする。

確認:

```sh
cd tools/agent-dashboard
node test/cowork.test.js
node test/user-centered-ui.test.js
```

## Task 3: 選択中の定常業務だけを段階表示

対象:

- `tools/agent-dashboard/src/renderer/renderer.js`
- `tools/agent-dashboard/src/renderer/styles.css`
- `tools/agent-dashboard/test/cowork.test.js`
- `tools/agent-dashboard/test/user-centered-ui.test.js`

作業:

1. 選択中業務の「現在の状態」領域を追加し、最終結果、次回予定、実行を固定位置に置く。
2. 基本情報を短い定義リストまたはグリッドへ整理する。
3. 最新結果とログの短いプレビューを追加し、履歴・ログ全文は要求時だけ取得する。
4. 編集と削除を詳細操作へ移し、実行を唯一の主操作にする。
5. 実行中、成功、失敗、未実行、無効の各状態で同じ領域サイズを維持する。
6. 履歴取得をキャッシュ経由へ変更し、閉じた後の再表示で再取得しないことをテストする。

確認:

```sh
cd tools/agent-dashboard
node test/cowork.test.js
node test/user-centered-ui.test.js
```

## Task 4: 実行状況を定常業務で絞り込む

対象:

- `tools/agent-dashboard/src/renderer/renderer.js`
- `tools/agent-dashboard/src/renderer/styles.css`
- `tools/agent-dashboard/test/flow-relationship.test.js`
- `tools/agent-dashboard/test/user-centered-ui.test.js`

作業:

1. 実行概要と定常業務を結び付ける表示キーを整理し、選択中業務の実行だけを抽出する。
2. 画面上部へ定常業務セレクターを追加し、必要時のみ「すべて」を選べるようにする。
3. 業務選択では `state.flowRuns` を再取得せず、既存スナップショットを絞り込む。
4. 対象業務内で現在の実行選択を維持し、無効になった場合だけ最新実行へ移す。
5. 業務に実行が無い場合の空状態と実行導線を追加する。
6. 業務選択だけではIPC呼び出しが増えないことをテストする。

確認:

```sh
cd tools/agent-dashboard
node test/flow-relationship.test.js
node test/user-centered-ui.test.js
```

## Task 5: 実行概要・工程・詳細の段階表示

対象:

- `tools/agent-dashboard/src/renderer/renderer.js`
- `tools/agent-dashboard/src/renderer/styles.css`
- `tools/agent-dashboard/test/flow-relationship.test.js`
- `tools/agent-dashboard/test/flow-advice.test.js`
- `tools/agent-dashboard/test/user-centered-ui.test.js`

作業:

1. `renderFlowDetail` を実行概要、工程タイムライン、選択工程詳細へ分割する。
2. 初期表示からイベント全文とログを外し、選択工程についてだけ展開する。
3. 失敗時は失敗工程を選択候補にするが、概要と主要操作の配置を維持する。
4. `selectFlowRun` と `reloadProject` を詳細キャッシュ経由へ変更する。
5. 一覧の `status` または `updatedAt` 変化で該当詳細を無効化する。
6. 既存の再実行系統、タスク遷移、助言、GitLab突き合わせを新しい階層内で維持する。
7. 工程選択、詳細開閉、失敗工程の初期選択、キャッシュ再利用をテストする。

確認:

```sh
cd tools/agent-dashboard
node test/flow-relationship.test.js
node test/flow-advice.test.js
node test/user-centered-ui.test.js
```

## Task 6: レイアウト安定性とアクセシビリティ

対象:

- `tools/agent-dashboard/src/renderer/styles.css`
- `tools/agent-dashboard/src/renderer/renderer.js`
- `tools/agent-dashboard/test/user-centered-ui.test.js`

作業:

1. セレクター、概要、詳細の各コンテナへ適切な `min-width: 0`、`min-height: 0`、overflowを設定する。
2. 長い名前、要約、ログ、大量項目用の省略と専用スクロール領域を追加する。
3. 読み込み、空、エラーで同じ枠を維持するプレースホルダーを追加する。
4. 状態表示にテキストを併記し、フォーカス表示と `aria-live` を確認する。
5. reduced motionと狭い画面での1列化を確認する。

確認:

```sh
cd tools/agent-dashboard
node test/user-centered-ui.test.js
```

## Task 7: 統合検証

1. 全テストを実行する。
2. Electron画面を375px、768px、1024px、1440px相当で確認する。
3. 定常業務の選択、実行、履歴再表示、実行状況への遷移をキーボードだけで確認する。
4. 開発用のIPCカウンターまたはテストスパイで、選択操作時に概要再取得が発生しないことを確認する。
5. 長い業務名、多数の業務、多数の工程、大きなログで、はみ出し・重畳・画面全体の不要なスクロールが
   発生しないことを確認する。

確認:

```sh
cd tools/agent-dashboard
npm test
```

## 完了条件

- 定常業務画面は上部で業務を選び、下部には選択中の業務だけが表示される。
- 初期表示で現在状態、最終結果、次回予定、実行操作を把握できる。
- 実行状況画面は選択した定常業務の実行だけを、概要から工程詳細へ段階表示する。
- 選択操作だけでは一覧APIを再取得せず、有効な詳細キャッシュを再利用する。
- 状態変更後は該当キャッシュだけが無効化される。
- データ量と文字量が変わっても主要領域が移動せず、はみ出しや重畳が発生しない。
- 全テストと主要画面幅の手動確認が完了する。
