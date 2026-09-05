# agent-app 自動化ワークベンチ統合 実装計画

> 作成: 2026-09-05  
> 設計: [agent-app 自動化ワークベンチ統合設計](./2026-09-05-agent-app-statemachine-integration-design.md)

## 元のタスク

> statemachine-makerに搭載した機能をagent-appの一機能として取り込んで。UIデザインはstatemachine-makerを参考に。agent-appのUXを最大化しシンプルでわかりやすいように整えて。

追加確認により、一般的なエージェントアプリの構成を参考に、`作業 / 自動化` をアプリ全体の主要領域として
分ける方針で承認済み。

## 実装原則

- 既存 agent-app の会話、ファイル、差分、tmux、worktree の挙動を回帰させない。
- 自動化は選択中セッションではなく登録済みリポジトリに属する。
- renderer は表示と入力に限定し、ファイル・コマンド・実行状態は main process へ委譲する。
- statemachine-use、agent-loop、agent-tools の既存契約を再実装しない。
- 一つのタスクごとに対象テストを通し、最後に統合試験を行う。

## 分解した実装タスク

### 1. 既存の回帰基準を固定する

What: 変更前の agent-app、statemachine-maker、agent-loop の関連テスト結果を記録する。  
Where: `tools/agent-app/test/`、`tools/statemachine-maker/test/`、`tools/agent-loop/test/test_repository_ui.py`。  
How: 現在の依存を使い、Node テストと関連 Python テストを個別に実行する。skip は理由を記録する。  
Why: 大規模な移植中に既存不具合と新しい回帰を区別するため。  
完了条件: `tools/agent-app` と `tools/statemachine-maker` の `npm test`、agent-loop の repository UI テストが成功するか、環境依存 skip の理由が明確になっている。

### 2. automation feature の依存とディレクトリ境界を追加する

What: agent-app に YAML 依存と `src/main/automation/`、対応テスト置き場を追加する。  
Where: `tools/agent-app/package.json`、`package-lock.json`、`src/main/automation/`、`test/automation-*.test.js`。  
How: statemachine-maker と同じ `yaml` バージョンを使い、外部 package を増やさず CommonJS の既存規約に合わせる。  
Why: agent-app 単体の `npm install` で自動化機能を起動できるようにするため。  
完了条件: `npm install` 後に automation module から `yaml` を読み込め、既存 vendor 処理も成功する。

### 3. 定義の正規化・コンパイル・読み戻しを移植する

What: 工程モデル、入力変数抽出、YAML/actions 生成、既存定義の読み戻しを移植する。  
Where: `src/main/automation/model.js`、`template-parameters.js`、`test/automation-model.test.js`。  
How: statemachine-maker の純粋関数を基準に移し、同じ fixture に対して compile/read-back/validate の結果を比較する。  
Why: UI より先に成果物の互換性を固定するため。  
完了条件: 空工程、直列、分岐、確認、独自終端、既存詳細条件、入力変数のテストが通る。

### 4. 登録済みリポジトリ限定の保存層を追加する

What: `.statemachine/` の一覧、存在確認、読み込み、原子的保存、生成ファイル表示を追加する。  
Where: `src/main/automation/store.js`、`test/automation-store.test.js`。  
How: statemachine-maker の store を移植し、root は呼び出し側で検査済みの repo のみ受ける。machine ID、絶対パス、`..`、symlink escape を拒否する。  
Why: 自動化機能が agent-app の repo 境界を越えて読み書きしないようにするため。  
完了条件: 正常な往復保存と、root 外参照・不正 ID・途中失敗時に元定義を壊さないテストが通る。

### 5. 外部ツール診断とプロセス実行基盤を移植する `[並列可: 6, 7]`

What: Python、statemachine-use、agent-tools、操作記録ツールの検出と、安全な stream/capture/stop を追加する。  
Where: `src/main/automation/tools.js`、`runner.js`、`command.js`、`test/automation-tools.test.js`、`automation-runner.test.js`。  
How: shell を使わず argv で起動し、出力上限、末尾改行なし、重複実行、停止、環境変数を既存テストで固定する。  
Why: AI、構成確認、実行、記録が共通の安全なプロセス境界を使うため。  
完了条件: capture/stream/stop と全診断状態の単体テストが通る。

### 6. AI 下書き・見直し境界を移植する `[並列可: 5, 7]`

What: 下書き、確認質問、見直し、候補差分、選択反映を追加する。  
Where: `src/main/automation/ai.js`、`ai-diff.js`、`test/automation-ai.test.js`、`automation-ai-diff.test.js`。  
How: AI は構造化 envelope の候補だけを返し、不正形式は一度だけ修復する。見直し後の変更は fingerprint で拒否する。  
Why: AI に直接ファイルを変更させず、人の確認を保存ゲートにするため。  
完了条件: draft/review/question/repair/scope/fingerprint/部分適用のテストが通る。

### 7. 操作記録を移植する `[並列可: 5, 6]`

What: browser、Windows、貼り付け記録から工程候補を作る機能を追加する。  
Where: `src/main/automation/recording.js`、`test/automation-recording.test.js`。  
How: statemachine-maker の正規化規則を移植し、開始・停止・進行中状態を一つに制限する。  
Why: 人の操作を手本に自動化を作る機能を維持するため。  
完了条件: Playwright 行、Windows JSONL、異常入力、ゼロ件、停止失敗のテストが通る。

### 8. agent-loop adapter を移植する

What: snapshot、手動実行、schedule、daemon、履歴ログの adapter を追加する。  
Where: `src/main/automation/agent-loop.js`、`test/automation-agent-loop.test.js`。  
How: statemachine-maker と同じ機械可読契約を使い、RESULT 欠落、終了コード 0/1/3、log path 許可を検査する。  
Why: scheduler、実行ロック、履歴の正典を agent-loop に維持するため。  
完了条件: inspect/runSpec/parseResult/saveSchedule/start-stop/readLog の契約テストが通る。

### 9. agent-app の設定を主要領域と自動化状態へ拡張する

What: `work | automation`、作業内 view、自動化内 view、repo ごとの最後の workflow、実行環境上書きを保存する。  
Where: `src/main/store.js`、`test/app.test.js`。  
How: 旧 `view` を読み込める後方互換 normalize を追加し、会話設定と自動化設定を別フィールドにする。  
Why: 領域切替後に文脈を復元し、既存 userData を壊さないため。  
完了条件: 旧設定、新設定、不正値、削除済み repo を正規化するテストが通る。

### 10. automation IPC と preload 契約を追加する

What: domain、AI、recording、runtime の IPC handler と `api.automation.*` を公開する。  
Where: `src/main/automation/ipc.js`、`src/main/ipc.js`、`src/preload.js`、`test/automation-preload.test.js`。  
How: 既存の `{ok,data|error}` wrapper を再利用し、全 handler の先頭で `store.isRegistered` と実在 directory を検査する。イベントに request ID と workflow ID を付ける。  
Why: renderer に Node 権限を渡さず、既存 API と名前衝突しない境界を作るため。  
完了条件: preload と IPC channel の静的対応、未登録 repo 拒否、event filtering のテストが通る。

### 11. アプリ全体の `作業 / 自動化` ナビゲーションを追加する

What: サイドバー最上部へ主要領域スイッチを追加し、既存作業 UI を壊さず automation host を用意する。  
Where: `src/renderer/index.html`、`styles.css`、`renderer.js`。  
How: work area では既存 DOM を維持し、automation area では会話一覧、会話用設定、composer を隠す。DOM 順は主要ナビゲーション→repo→領域固有一覧→中央内容とする。  
Why: セッション内表示とリポジトリ単位の自動化を明確に分けるため。  
完了条件: 主要領域を往復して repo/session/view/変更パネルの状態が保たれ、キーボードで切り替えられる。

### 12. 自動化一覧と概要画面を実装する

What: repo ごとの workflow 一覧、状態、次回予定、要確認フィルター、選択中詳細を表示する。  
Where: `src/renderer/automation.js`、`automation.css`、`index.html`。  
How: statemachine-maker の固定ニュートラル配色、一覧と詳細、単一主操作を踏襲する。最初の描画は snapshot、詳細本体とログは遅延取得する。  
Why: 起動直後に何を実行でき、何が対応待ちか判断できるようにするため。  
完了条件: loading、空、未実行、実行中、要確認、失敗、完了を fixture で描画できる。

### 13. 手動実行・定期実行・履歴を接続する

What: 必要入力、構成確認、実行、進捗、停止、結果、schedule、daemon、履歴、ログを概要へ接続する。  
Where: `src/renderer/automation.js`、`automation.css`。  
How: request ID が一致するイベントだけを適用し、実行中は主ボタンを状態表示へ替える。schedule は日次、週次、一定間隔だけを編集し、高度な設定は読み取り専用にする。  
Why: 実行と監督を一画面で完結させるため。  
完了条件: 成功、失敗、要確認、重複、停止、未反映、daemon 停止、log 遅延取得を操作できる。

### 14. 工程フローと設定パネルを実装する

What: 新規／既存定義を工程カードと inspector で編集できるようにする。  
Where: `src/renderer/automation.js`、`automation.css`。  
How: statemachine-maker の editor state、要約、分岐表示、insert/move/remove、詳細設定、dirty guard を移植し、画面文言から内部用語を除く。  
Why: 複雑な YAML を直接見せずに、実行順と条件を理解できるようにするため。  
完了条件: 作成、往復編集、分岐、再試行、独自終端、未保存離脱、保存後の概要復帰を操作できる。

### 15. AI・操作記録・生成ファイル・実行環境ダイアログを実装する

What: statemachine-maker の補助フローを用途別 dialog として追加する。  
Where: `src/renderer/index.html`、`automation.js`、`automation.css`。  
How: AI は入力→確認質問→候補→選択反映、記録は種類→開始／貼り付け→工程候補、設定は診断→回復手順の順で必要な内容だけを表示する。  
Why: 高度な操作を通常画面から隠しつつ全機能を保持するため。  
完了条件: 各 dialog を開閉・中断でき、busy 中の二重送信を防ぎ、エラー位置に回復操作が出る。

### 16. レスポンシブとアクセシビリティを仕上げる

What: 720px、980px、1440px のレイアウト、focus、status/alert、reduced motion を整える。  
Where: `src/renderer/automation.css`、`styles.css`、`index.html`、`automation.js`。  
How: 中幅では workflow 一覧を横選択へ、狭幅では flow と inspector を切り替える。icon-only button に aria-label、フォームに label、状態に文言を付ける。  
Why: 情報量を減らさず、画面幅と入力方法にかかわらず操作可能にするため。  
完了条件: 対象 3 幅でページ横スクロールがなく、Tab 順、focus、読み上げ用 state が正しい。

### 17. 統合テストと文言契約を追加する

What: 主要領域から作成、保存、実行、確認までの renderer 契約を追加する。  
Where: `test/app.test.js`、`test/automation-app.test.js`、必要な fixture。  
How: DOM source の主要導線、preload 契約、内部用語禁止、登録 repo 境界、既存画面の ID を検査する。  
Why: 見た目の変更で機能入口や安全境界が消えないようにするため。  
完了条件: 全 Node テストが成功し、既存 agent-app テストを一件も削除していない。

### 18. README と利用手順を更新する

What: 自動化の目的、作成、実行、定期実行、必要ツール、責務、トラブル対応を追記する。  
Where: `tools/agent-app/README.md`。  
How: UI と同じ言葉を使い、YAML や内部コマンドは必要な診断節だけに置く。statemachine-maker は比較対象として残ることを明記する。  
Why: アプリだけを見た利用者が導入と回復を完了できるようにするため。  
完了条件: README の手順が実装済み UI と一致し、起動・テストコマンドが実際に通る。

### 19. Electron 実機と全回帰を検証する

What: 実 Electron で描画・操作・コンソールエラーを確認し、関連全テストを再実行する。  
Where: `tools/agent-app` と関連 package。  
How: 既存 smoke に automation fixture を追加し、作業／自動化、repo 選択、概要、editor、dialog を開く。最後に Node、agent-loop Python、statemachine-maker の回帰を通す。  
Why: preload 名衝突、CSP、hidden CSS、実際のレイアウトは静的テストだけでは検出できないため。  
完了条件: Electron が白画面や console error なく起動し、対象 3 幅のスクリーンショット確認と全関連テストが成功する。

## 依存関係

```text
1 baseline
   ↓
2 feature boundary
   ↓
3 model ─→ 4 store
   ├──────────────┐
   ↓              ↓
5 tools/runner   6 AI   7 recording
   └──────┬───────┴──────┘
          ↓
8 agent-loop adapter
          ↓
9 config → 10 IPC/preload
                   ↓
11 app navigation → 12 overview → 13 runtime
                         └──────→ 14 editor → 15 dialogs
                                             ↓
16 responsive/a11y → 17 integration tests → 18 docs → 19 full verification
```

タスク 5、6、7 は task 3 のデータ形が確定した後なら並行可能。それ以外は安全境界と UI 契約の依存が強いため、
記載順に進める。

## オープンクエスチョン

承認が必要な未解決事項はない。以下は実装中に測定して決める局所判断とする。

- 自動化一覧の中幅切替点は、既存 agent-app の右変更パネルを開いた状態でも 980px 相当を確保できる値に調整する。
- 独立版との共通 package 化は今回行わない。重複が次の機能追加で実害になった時点で再評価する。
- Electron smoke が CI 環境で実行できない場合は skip 理由を保持し、ローカル実機結果を記録する。
