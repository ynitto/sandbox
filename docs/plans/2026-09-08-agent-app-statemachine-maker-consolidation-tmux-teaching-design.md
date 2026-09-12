# agent-app: statemachine-maker の完全統合と、タスクの作成・変更を tmux 会話へ移す設計

> 決定日: 2026-09-08  
> 対象: `tools/agent-app`（`tools/statemachine-maker` は廃止）  
> 関連: [agent-app 設計書](../designs/agent-app-design.md) ADR-4 / ADR-9、
> [2026-09-05 統合設計](2026-09-05-agent-app-statemachine-integration-design.md)、
> [2026-09-06 共有編集面](2026-09-06-agent-app-shared-editor-workbench-design.md)、
> [2026-09-06 maker AI ティーチング](2026-09-06-statemachine-maker-ai-teaching-workspace-design.md)、
> [2026-09-08 タスクポータル](2026-09-08-agent-app-task-portal-tabbed-workbench-design.md)

## 背景

agent-app は statemachine-maker の domain と IPC を `file:` リンクで借り、renderer を `npm install` 時に
`vendor/statemachine/` へ写して、Shadow DOM のカスタム要素で動かしていた。統合の骨格はできていたが、
次が残っていた。

- 同じ画面を 2 つのアプリ（独立版 maker と agent-app）で保守している。maker 側の変更が vendor の写しを
  通じて agent-app に波及し、読込契約のテストで揃え続ける必要があった。
- タスクの作成・変更は「AI に教える」構造化の往復（`agent-herd -p` に JSON の候補を返させる → 分離した
  試運転 → 承認 → 昇格）で、会話（tmux の端末ミラー）とは別の作法だった。利用者は会話では CLI と端末で
  話しているのに、タスクだけは質問カードと試運転カードを往復する。
- Windows では CLI は WSL の tmux にいて、ブラウザや Windows アプリは Windows 側にある。AI 自身に
  playwright-cli / winauto の記録を起こさせる形は成り立たない。

## 決定

1. **statemachine-maker を agent-app へ統合し、独立版を廃止する。** main は `src/main/automation/`
   （旧 `ipc.js` は `handlers.js`）、renderer は `src/renderer/automation/`、テストは `test/automation-*.test.js`。
   `file:` リンク・vendor への写し・Host Adapter（`editor-host.js`）・standalone の `main.js` / `preload.js` /
   `config.js` は無くなり、本番依存は `yaml` だけになる。CI の `statemachine-maker` ジョブは `agent-app` ジョブに
   置き換える（`npm install --ignore-scripts` + `npm run vendor` + `npm test`。electron のバイナリは取らず、
   実機の起動は skip）。
2. **タスクの作成・変更は AI との tmux 会話で行う。** 手動実行の画面と同じく、タスク画面の中に端末ミラーを
   埋め込む。会話は agent-app の会話基盤（`runTurn` / `tmux.Conversation`）の `kind: 'task'` セッションで、
   `task.machine`（保存名）に紐づき、会話一覧には出ない。CLI は会話の既定の起動方針で解決し、cwd は
   リポジトリ本体。最初の依頼（`automation/teaching.js` の `prompt`）が保存先・`statemachine-use` の作成モード・
   `--dry-run` の検証・見本の頼み方を伝える。AI は定義を直接書き、定義があれば「利用可能」。
3. **見本の記録はこの端末で取り、AI には `@record` の 1 行で頼ませる。** AI が画面操作の見本を要るときは
   返答に `@record browser <URL>` / `@record windows <アプリ名>` を書く。親（`taskTeaching.js`）が
   `turn:done` の本文から拾って「操作の見本」のカードを開き、記録（`playwright-cli` / `winauto`）は
   agent-app 自身がこの端末で起こす。Windows では AI は WSL、画面と道具は Windows 側なので、記録の所在
   （`.statemachine/<名前>/recordings/<時刻>-<種類>.md`）を `host.toHostPath` で WSL 表記へ直して次の
   ターンとして送る（`automation:teach:demonstration`）。依頼文では「自分では記録を起こさない」と伝える。
4. **構造化の教示（`teaching-model` / `teaching-store` / `teaching-trial` / `approval-policy`、`ai.js` の
   `teach` モード）は撤去する。** 下書きの印 `teaching.json` は残すが、持つのは保存名・表示名・目的・会話 ID・
   見本の控えだけ（version 2）。状態語は「利用可能 / 下書き」の 2 つ。ワークフローの教示（`flow-teach`）は
   agent-flow の投入契約が要るので従来どおり。**（2026-09-12 追記: ワークフローも同じ理由で会話へ移し、
   `flow-teach` モードと `flow-teaching-compiler` は撤去した。`docs/designs/agent-app-design.md` §3.4）**

## 画面

```text
タスク詳細
  概要   … 手動実行（実行ログ）・定期実行            ← 従来どおり
  手順   … 工程エディタ                                ← 従来どおり
  AI相談 … [端末ミラー（tmux）]                        ← 新: 親の slot に載る
           [メッセージ | 端末操作] 入力欄  [操作の見本] [送信]
           操作の見本のカード（AI の @record で自動で開く）
  履歴   … 実行履歴                                    ← 従来どおり

新しいタスク（＋ / 会話の「この依頼をタスクにする」）
  目的のフォーム →「AIと作り始める」→ 上の AI相談 と同じ端末（一覧には「下書き」で出る）
```

共有ワークベンチ（Shadow DOM）の `teaching.js` は見出しと `<slot name="teaching">` を描き、
`statemachine:teaching-view` で「どのタスクの会話を出しているか」を親へ伝える。親の
`taskTeaching.js` は光の DOM の `#task-teaching` を描く（端末ミラーは `createTerm()` で会話用とは別の
`TaskTerm`）。ターンが終わるたびに親は `element.refresh()` で定義と実行状態を読み直す。

## 検討した案

| 案 | 判断 |
|---|---|
| maker を独立版として残したまま tmux 教示を足す | 同じ画面を 2 つで保守し続ける。廃止の見直し条件（ADR-4）を満たしたので統合する |
| AI に記録を起こさせる（依頼文で playwright-cli / winauto を指示） | WSL からは画面が無く、Windows 側の道具も見えない。Linux でも記録は人の操作なのでアプリが起こすべき |
| 試運転と承認の往復を tmux の上に再現する | 会話で聞けばよいことを画面が二重に聞く。確認は「構成を確認」と「実行」で足りる |
| タスクの会話を会話領域の 1 会話として出す | タスクとの紐づけが消え、一覧が混ざる。`kind: 'task'` で分けてタスク画面の中に出す |
| 端末ミラーを Shadow DOM の中に置く | xterm の測定と選択が Shadow Root で崩れうる。slot で光の DOM に置き、親の CSS で描く |

## 影響と移行

- 既存の `.statemachine/<名前>/teaching.json`（version 1。会話・世代・試運転を含む）は読めない形として
  扱わず、`normalize` が version 2 の項目だけを拾う（定義があるタスクは影響なし）。
- 画面の状態語から「試運転待ち / 確認待ち / 変更中」が消える。
- `api.automation.teachingCreate` … `teachingRestore` は無くなり、`teachStart` / `teachSession` /
  `teachDemonstration` になる。`automation:ai:start` の `mode: 'teach'` は無くなる。
- 会話ファイルに `kind` / `task` が増える。旧ファイルは `conversation` として読む。

## 検証

- `test/automation-teaching.test.js`: `@record` 行の解析、下書き、最初の依頼文（Windows/WSL の注意と
  道具の有無）、見本の Markdown、kind: task の会話、記録の所在を WSL 表記で送る配線。
- `test/automation-*.test.js`（旧 maker のテスト）と `test/app.test.js` / `packaging.test.js` の更新。
- `test/electron-smoke.test.js`: AI相談タブに親の置き場と「AIとの相談を始める」が出て、＋が作成フォームを開く。
- 手元では、擬似の対話 CLI を tmux で起こし、`teach:start` → 最初の依頼 → `@record` 行 →
  `teach:demonstration` → 追送、を Electron 無しで通した。

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-09-08 |
| 決定者 | ユーザー |
| 採用案 | statemachine-maker を agent-app へ統合し、タスクの作成・変更を tmux 会話（端末ミラー埋め込み）へ移す。見本の記録はアプリがこの端末で取り、所在を WSL 表記で AI へ渡す |
| 却下案 | 独立版の維持、AI による記録の起動、試運転・承認の往復の再現 |
| 主な理由 | 会話とタスクの作法を 1 つにし、二重の保守と二重の実行経路を無くす。Windows の tmux（WSL）と画面（Windows）の分離を正しく扱う |
| トレードオフ | AI がリポジトリのファイルを直接書く（会話と同じ信頼境界）。重要操作の承認は CLI の許可確認に任せる |
| 再評価条件 | 無人実行に組織的な承認・監査が要る場合、複数の CLI が同じ `.statemachine/` を同時に書く運用が主になった場合 |
