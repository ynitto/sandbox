# Agent Dashboard 設計・実装ライフサイクル統合 実装計画

## 目的

[設計書](./2026-08-15-agent-dashboard-design-implementation-lifecycle-design.md)に従い、設計用内部フローを
一般ライブラリから分離し、プロジェクトとワークフローへ共通の作業準備モデルを導入する。

## 実装原則

- 1テスト・1振る舞いのRed-Green-Refactorで進める。
- 公開APIと画面上の観測可能な振る舞いをテストする。
- agent-flowとagent-projectの既存run契約は変更しない。
- 既存の設計セッション、ワークフロー実行待ち、プロジェクトinbox投入を再利用する。
- 旧保存物は読込時に互換化し、一括移行しない。

## インクリメント

### 1. フロー分類契約

期待動作:

- 既存フローは `implementation/library` として正規化される。
- 同梱設計フローは `design/internal` として読み込める。
- ワークフロー設定のライブラリにはinternalフローを表示しない。
- 設計セッションはinternalフローをIDで解決して実行できる。

変更候補:

- `workflows/design-interactive.json`
- `workflows/design-auto.json`
- `tools/agent-dashboard/src/features/adhoc-flow/main/adhoc.js`
- `tools/agent-dashboard/src/renderer/features/adhoc-flow.js`
- `tools/agent-dashboard/test/adhoc-flow.test.js`

### 2. 作業準備ドメイン

期待動作:

- 入力の充足度から `agent-design`、`external-design`、`direct` を決定的に推奨する。
- 利用者が選んだ経路は推奨と別に保持する。
- 材料を正規化・重複排除し、設計結果を実装材料へ追加できる。
- 状態遷移で設計未完了の項目を実装待ちへ移せない。
- 既存ワークフロー実行待ちタスクをdirect/implementation-readyとして読める。

変更候補:

- `tools/agent-dashboard/src/features/preparation/main/preparation.js`（新規）
- `tools/agent-dashboard/test/preparation.test.js`（新規）
- `tools/agent-dashboard/src/features/adhoc-flow/main/task-queue.js`

### 3. ワークフローの作業準備UI

期待動作:

- 作成順序を「やりたいこと→進め方→材料→確認」にする。
- 進め方には推奨理由を表示し、三経路を変更できる。
- 経路選択後に対象フォルダとファイルを指定できる。
- external/directは設計runなしで実装準備完了になる。
- agent-designは準備項目を保存して設計セッションへ接続する。
- 一覧は設計中、確認待ち、実装待ちを同じ仕事の状態として表示する。

変更候補:

- `tools/agent-dashboard/src/renderer/features/adhoc-flow.js`
- `tools/agent-dashboard/src/features/adhoc-flow/main/ipc.js`
- `tools/agent-dashboard/src/base/main/preload.js`
- `tools/agent-dashboard/src/renderer/styles.css`
- `tools/agent-dashboard/test/adhoc-flow.test.js`
- `tools/agent-dashboard/test/user-centered-ui.test.js`

### 4. プロジェクト準備パッケージと子候補

期待動作:

- 一つの入力を複数候補へ分解した結果を親パッケージと子準備項目として保存する。
- 共通材料を子へ継承し、子固有材料を追加できる。
- 子ごとに経路推奨と上書きを持つ。
- 準備完了した子だけを既存inboxへ投入する。
- 同じ反映IDで二重投入しない。

変更候補:

- `tools/agent-dashboard/src/features/preparation/main/preparation.js`
- `tools/agent-dashboard/src/features/agent-project/main/ipc.js`
- `tools/agent-dashboard/src/renderer/sections/backlog.js`
- `tools/agent-dashboard/src/base/main/preload.js`
- `tools/agent-dashboard/test/preparation.test.js`
- `tools/agent-dashboard/test/backlog-planning.test.js`

### 5. 子項目ごとの設計runとhandoff

期待動作:

- `agent-design` の子だけ既存設計セッションを開始する。
- 設計書と質問を子項目詳細で表示し、回答して次ラウンドへ進める。
- 設計結果を材料へ追加し、実装準備完了へ移せる。
- external/directでは設計セッションを作らない。
- プロジェクトはinboxタスクID、ワークフローはrun IDをhandoffへ記録する。

変更候補:

- `tools/agent-dashboard/src/features/adhoc-flow/main/design-session.js`
- `tools/agent-dashboard/src/features/preparation/main/preparation.js`
- `tools/agent-dashboard/src/renderer/features/adhoc-flow.js`
- `tools/agent-dashboard/src/renderer/sections/backlog.js`
- `tools/agent-dashboard/test/preparation.test.js`
- `tools/agent-dashboard/test/adhoc-flow.test.js`

### 6. 回帰・アクセシビリティ・カバレッジ

- 新規対象モジュールをNode組込みカバレッジでC1 100%にする。
- `npm test` と `npm run lint` を実行する。
- キーボード操作、可視フォーカス、44px操作領域、処理中無効化を確認する。
- 375px、768px、1024px、1440pxで不要な横スクロールがないことを確認する。
- 既存ワークフロー実行、設計セッション、プロジェクト実行サイクルが回帰しないことを確認する。

## トレーサーバレット

最初の垂直スライスは「同梱設計フローが一般ライブラリへ出ず、設計セッションからは解決できる」とする。
利用者が最初に報告した違和感を解消しつつ、新しい分類契約がmainからrendererまで通ることを証明できる。

## 完了条件

- 設計フローが保存済み一般ワークフローと同列に表示されない。
- プロジェクトとワークフローが同じ三経路・同じ作成順序を使う。
- 選択した材料が設計から実装へ引き継がれる。
- プロジェクトの複数候補が子ごとに設計runまたは迂回経路を選べる。
- 設計未完了の候補をagent-projectが実装しない。
- 全関連テストとlintが通り、新規ドメインモジュールのC1が100%である。

