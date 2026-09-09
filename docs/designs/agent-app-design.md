# agent-app 設計書

> 最終更新: 2026-09-08  
> 実装: [`tools/agent-app/`](../../tools/agent-app/)  
> 外部契約: [`agent-app-spec.md`](../specs/agent-app-spec.md)  
> 操作方法: [`tools/agent-app/README.md`](../../tools/agent-app/README.md)  
> 関連設計: [エージェント CLI プラグイン](./agent-cli-plugin-design.md) / [agent-loop](./agent-loop-design.md) / [agent-dashboard](./agent-dashboard-design.md)

## TL;DR

agent-app は、ローカルリポジトリを登録し、`agents/*.json` に定義したエージェント CLI（copilot / claude /
codex / kiro / cursor / aider …）と会話形式で作業する Electron アプリである。GitHub 連携は持たず、
見に行くのは登録したフォルダだけ、呼ぶのはこの PC（Windows なら WSL）に入っている CLI だけである。
対象読者は、agent-app に機能を足す人と、agent-app から起動される CLI 定義やタスク・ワークフローの
共有ワークベンチ（旧 statemachine-maker。2026-09-08 に統合し、単体アプリは廃止）の契約を変更する人。

設計上の要点は四つある。

1. 会話 1 つ = tmux セッション 1 つ。CLI は定義の `interactive` 節で対話起動し、画面は `capture-pane`
   の写し（端末ミラー）を正として見せる。CLI の文言解析は履歴の補助記録に留め、入力可否や送信状態を
   支配させない。
2. renderer は表示と入力だけを持つ。触ってよいのは登録済みリポジトリの内側だけで、生のパスは画面から
   受け取らない（worktree は名前、添付は ID、ファイルは相対パス）。
3. 起動方針（おすすめ / 節約 / 品質重視）は設定の tier へ決定的に写す。利用不能でも別 tier へ黙って
   倒さない。共通指示・開始アクション・スキル選択も同じ 1 か所（`runTurn`）で合成する。
4. タスク・ワークフローは共有ワークベンチ（`src/main/automation/` の domain と IPC、
   `src/renderer/automation/` の画面。旧 statemachine-maker を統合したもの）が担い、agent-app は登録
   リポジトリと設定を渡す。画面は同じウィンドウのカスタム要素 `<statemachine-workbench>`（Shadow DOM）で
   動かす。**タスクの作成と変更は AI との tmux 会話**で行い、手動実行と同じくタスク画面の中に端末ミラーを
   埋め込む（ADR-9）。実行・定期発火・履歴は agent-loop が正典。

却下した中心案は、node-pty で tmux へ直接 attach する構成と、CLI ごとに構造化出力アダプターを書いて
共通メッセージへ変換する構成である。前者は Windows 配布と PTY 境界を増やし、後者は CLI の出力形式が
変わるたびに追跡が要る。既存の capture-pane / send-keys ミラーを主表示へ昇格させる案を採った。
タスク・ワークフローの埋め込みでは、初版の iframe + `postMessage` + vendor 時の文字列置換を、
共有編集面（カスタム要素）へ置き換え（ADR-4）、その後 statemachine-maker 自体を agent-app へ統合した。
タスクを AI に教える往復（JSON の候補 → 分離した試運転 → 承認）は、会話と同じ tmux の端末へ置き換えた（ADR-9）。

## 1. 目的と境界

### 1.1 解く問題

エージェント CLI は端末で対話するのが本来の姿だが、リポジトリを切り替えながら複数の会話を並行し、
別の CLI へ渡り歩き、その差分を確認する作業は端末だけでは散らばる。特に次が見落としやすい。

- どの会話が応答中で、どれが端末で許可を求めて止まっているか
- 同じ作業ツリーを複数の CLI が同時に書き換えて混ざること
- 会話の続きを別の CLI へ渡すときに、相手がまだ見ていないやり取り
- CLI 自身が管理するセッション ID と再開の作法が CLI ごとに違うこと

agent-app は次を一つの操作面へまとめる。

- 会話ごとの依頼入力、端末ミラー、応答履歴、送信状態
- 起動方針・エージェント・モデル・Ask モード・作業フォルダ・スキルの、ターンごとの実行設定
- 作業フォルダの差分（作業ツリー / ブランチ）とファイルビュアー
- 共通指示、開始アクション、スキル候補、tier 割当の設定
- 同じリポジトリのタスク（ステートマシン）とワークフロー（複数 AI の工程）。どちらも AI に目的を
  教え、試運転の結果を確認してから利用可能にする

### 1.2 担当しないこと

- GitHub / GitLab 連携（PR、Issue、クラウドセッション）
- ツール呼び出しの逐次承認 UI。CLI が端末で聞いてきたら端末ミラーで答える
- 差分の適用・取り消し、コミット・マージ・push。git へ書くのは worktree の追加・削除とブランチ作成だけ
- ファイルの編集。ビュアーは読むだけで、「開く」で既定のアプリへ渡す
- CLI の思考・回答・質問を意味解析して共通形式へ完全変換すること
- 独自の scheduler、cron evaluator、run journal。定期実行は agent-loop に任せる
- 品質評価によるエージェント・モデルの自動順位付け、予算連動の tier 降格

### 1.3 守る不変条件

- 触ってよいのは登録したリポジトリだけ。登録に無いパスは実在していても断る（`requireRepo`）。
- renderer が送る値を信用しない。worktree は名前、添付は ID、ファイルは相対パスを main で検査する。
- 会話 1 つにつきライブの CLI は 1 つ。tmux とヘッドレスを同時に持たず、別 CLI へ移るときは前を止める。
- 送信成功は「tmux へ本文と Enter の両方が届いた」時点で確定する。CLI の文言から完了を推測しない。
- 会話の作業フォルダは作ったあと変えない（tmux の cwd も CLI 側の文脈もそこで始まっている）。
- 起動方針は tier へ決定的に解決し、自動フォールバックしない。解決結果はメッセージに残す。
- 設定・会話の保存先は Electron の userData だけ。リポジトリ側に置くのは `.worktrees/` と
  `.git/info/exclude` の 1 行だけ。

## 2. 実行時構成

### 2.1 プロセスと境界

```mermaid
flowchart LR
  U[利用者] --> R[renderer<br/>renderer.js / files.js / term.js]
  U --> A[statemachine-workbench<br/>共有編集面 Shadow DOM<br/>renderer/automation の renderer / teaching / flow]
  R -->|navigate / DOM イベント| A
  A -->|slot name=teaching| T[taskTeaching.js<br/>タスクを AI と作る会話<br/>端末ミラー + 見本の記録]
  R -->|window.api| P[preload]
  A -->|window.api.automation| P
  P -->|IPC invoke| M[main<br/>ipc.js]

  M --> S[store / settings<br/>config.json / sessions]
  M --> C[agentCli<br/>agents/*.json]
  M --> H[host<br/>常駐 bash / wsl.exe]
  H --> T[tmux -L agent-app]
  T --> CLI[エージェント CLI]
  H --> G[git 読取り / worktree]
  M --> X[headless spawn]
  X --> CLI
  M --> K[automation/ipc.js + handlers.js<br/>共有ワークベンチの IPC]
  K --> L[agent-loop / statemachine-use]
  M --> Q[automation:teach:*<br/>kind: task の会話 → tmux]
```

CLI との会話は二つの経路を持つ。対話定義（`interactive`）を持つ CLI で tmux が使える場合は tmux 経路、
それ以外は 1 ターン 1 プロセスのヘッドレス経路である。どちらも `runTurn` が起動条件を確定してから分岐し、
renderer は経路の違いを `transport` の値として受け取るだけである。

### 2.2 Electron の三層

| 層 | 主な実装 | 責務 |
|---|---|---|
| main | `src/main/*.js`、`src/main/automation/ipc.js` | 設定と会話の保存、CLI 定義の解決、tmux とヘッドレスの起動、git 読取り、worktree、添付、ダイアログ |
| preload | `src/preload.js` | IPC チャネルを `window.api` へ写し、失敗 envelope を `Error` へ戻す。`api.automation.*` も同じ窓口 |
| renderer | `src/renderer/*.js`、`automation-workbench.css`、`vendor/statemachine/*`（maker の共有 renderer） | 画面状態、描画、入力、端末ミラーの描画。ファイル・OS・git には触れない |

`BrowserWindow` は `contextIsolation` 有効、`nodeIntegration` 無効、preload の `sandbox` も有効である。
renderer の CSP は `script-src 'self'` で、外部ライブラリは `npm install` 時に `scripts/vendor.js` が
`src/renderer/vendor/` へ写す。CDN は使わない。`will-navigate` と `window.open` は止め、`http(s)` だけを
既定ブラウザへ渡す。

### 2.3 起動手順

1. main が IPC を登録する。先に `registerAutomationIpc` が共有ワークベンチの IPC（`automation/handlers.js`）を
   `automation:` 接頭辞で載せ、続いてタスクを AI と作る会話の `automation:teach:*` と agent-app 自身のチャネルを登録する。
2. 送らずに閉じた添付を `attachments.sweep` で掃除する。
3. ウィンドウを作り、`index.html` を読む。
4. renderer は `config:get` と `host:info`（tmux / git の有無）を取り、応答中の会話 ID を `turn:running` で
   引き継ぐ。
5. 最後のリポジトリを選び、会話一覧・エージェント一覧・worktree 一覧を読む。最後の領域と表示を復元する。
6. 起動直後と 1 時間ごとに、期限切れの tmux セッションを `sweepTerminalSessions` で回収する。

タスク・ワークフローの共有編集面は `index.html` が同時に読み込む。共有 renderer が初期化を終えて
controller を登録するまでの `navigate` は要素が保留し、登録時に最後の 1 件だけを渡す。

## 3. 画面の情報構造

### 3.1 三領域と共通リポジトリ

左サイドバーは上から、アプリ名、主要メニュー `会話 / タスク / ワークフロー`、共通のリポジトリ選択、
選択中領域の一覧見出しと作成操作（＋）、対象一覧、設定の順に並ぶ。主要メニューはタブではなく
ページナビゲーションで、`aria-current="page"` で現在地を示す。

| 領域 | 対象 | 中央 | 一覧の出どころ |
|---|---|---|---|
| 会話 | 対話セッション | 会話ヘッダー、端末ミラー、会話履歴、入力欄 / ファイルビュー | `session:list` |
| タスク | `.statemachine/` の定義と agent-loop の設定エントリ | 共有ワークベンチの概要・手順（AI との編集を含む）・履歴 | `automation:run:snapshot` + `machine:list` + `teaching:list` |
| ワークフロー | 複数 AI の工程定義と、教示中の下書き | 共有ワークベンチの教示・概要・編集・実行履歴 | `automation:flow:list` + `flow:run:list` + `flow:teaching:list` |

ワークフロー一覧は、教示中の下書き（`ready` 以外で、まだ定義として保存されていないもの）を先頭に、
利用可能な定義をその後ろに並べる。下書きの副題は工程数の代わりに状態ラベル
（理解中 / 試運転待ち / 確認待ち）を出す。

リポジトリは三領域の共通文脈で、領域を切り替えても変えない。領域ごと・リポジトリごとの最後の対象は
`config.json` の `lastTask` / `lastWorkflow` / `lastWorktree` に覚える。旧設定の `work` / `automation`
は `conversation` / `tasks` へ読み替える（`navigation.js`）。

### 3.2 会話画面

会話画面は開始前後で共通の CSS Grid を使い、入力欄を常に最下段の同じ位置に置く。

```text
会話ヘッダー   会話名 / phase / 会話・ファイル切替 / 変更を確認 / その他
中央面         開始前はニュートラルな開始面、tmux 会話では端末ミラー
会話履歴       折りたたみ。tmux 会話では既定で閉じ、ヘッドレス会話では常時展開
入力欄         入力モード切替 / 本文 or 端末キー / 添付・実行設定・送信
```

依頼の実行条件は入力欄付近の「実行設定」ポップオーバーへ段階表示する。要約行は
`おすすめ · codex / model · スキル 自動 · 実行 · 分離フォルダ` の形で、ラベルの無いアイコンだけでは表さない。

応答は「思考・進捗」「回答」「実行情報」の三層で表示する。回答は常に展開した吹き出し、思考・進捗と
実行情報は折りたたみで、エラー・停止・非 0 終了のときだけ実行情報を自動展開する。空の区分は出さない。

### 3.3 タスク・ワークフローの共有編集面

`#automation` セクションに置いた `<statemachine-workbench>` が、共有 renderer
（`src/renderer/automation/` の `workbench-element.js` / `teaching.js` / `flow.js` / `renderer.js`）を同じ
ウィンドウの Shadow DOM で動かす。iframe も `postMessage` も vendor への写しも使わない。

```text
<statemachine-workbench data-statemachine-workbench embedded
  stylesheet="automation/styles.css" host-stylesheet="automation-workbench.css">
  <div slot="teaching" id="task-teaching">…タスクを AI と作る会話（親が描く）…</div>
</statemachine-workbench>
```

| 部品 | 所在 | 役割 |
|---|---|---|
| `workbench-element.js` | renderer/automation | カスタム要素。Shadow DOM に `#bar` / `#main` / ダイアログを作り、`stylesheet` と `host-stylesheet` を読む。`navigate(payload)` を controller 登録まで保留し、`refresh()` で定義と実行状態を読み直す |
| `renderer.js` / `flow.js` | renderer/automation | 概要・手順（工程エディタ）・履歴・ワークフロー。DOM 参照は Shadow Root に対して行い、preload の窓口は `window.api.automation` だけ |
| `teaching.js` | renderer/automation | タスクの作成（`html()`）と「手順」の編集（`editorSlotHtml()`）の**置き場**。見出しと `<slot name="teaching">` を描き、どのタスクの会話を出しているかを `statemachine:teaching-view` で親へ伝える |
| `taskTeaching.js` | renderer（親） | slot に載る光の DOM。tmux の端末ミラー（`TaskTerm`）、入力 2 モードの入力欄、操作の見本のカード、作成フォーム。**見た目は会話画面と同じ実体**（`.terminal-stage` / `.composer-shell`）を使い、見出しと説明は持たない |
| `automation-workbench.css` | renderer（親） | host stylesheet。`:host` に対する上書きだけで、フォルダ欄・ホームタブ・見出しを隠し、三領域の語彙に揃える |

見出しと説明はワークベンチ側だけが描く（親は操作面だけを置く）。同じ事実を 2 つの層が言わない
ための境界で、`test/ui-consistency.test.js` が機械的に押さえる（規則はリポジトリ直下の `CLAUDE.md`）。

親と共有編集面は、メソッド呼び出しと DOM イベントで同期する。

| 向き | 手段 | 内容 |
|---|---|---|
| 親 → 子 | `element.navigate({ type: 'agent-app:navigate', … })` | `area`、`root`、`selected`、`action`（`new` / `teach`）。子は最新の設定を読み直してから画面を切り替える |
| 親 → 子 | `element.refresh()` | 定義と実行状態を読み直す（AI との会話の 1 ターンが終わるたび） |
| 子 → 親 | CustomEvent `statemachine:changed`（`detail.type = 'agent-app:changed'`） | `root`、`area`、`selected`。親は一覧を再読込し、最後の対象を保存する |
| 子 → 親 | CustomEvent `statemachine:teaching-view`（`detail.type = 'agent-app:teaching-view'`） | `root`、`machine`、`creating`、`published`、`title`、または `hidden`。親は同じ内容なら何もしない |

`session-new`（＋）はタスク領域では `action: 'new'` を渡し、子は作成の置き場を出す。親は作成フォームに
会話からの intent（`taskIntent.js`。依頼本文だけ）を入れる。ワークフロー領域では「新しいワークフローを教える」画面を開く。

### 3.5 タスクを AI と作る会話（tmux）

タスクの作成と変更は、会話と同じ経路で起こした CLI と**tmux の端末ミラーの中で**進める（手動実行の
画面と同じ埋め込み）。会話は `kind: 'task'` のセッションで、`task.machine`（保存名）に紐づき、会話一覧には出ない。

```text
目的を書く → automation:teach:start
  → 下書き .statemachine/<名前>/teaching.json（定義が無い間だけ一覧に「下書き」で出す）
  → kind: task の会話を作る（CLI は既定の起動方針で解決。cwd はリポジトリ本体）
  → 最初の依頼（teaching.prompt）を runTurn で送る
       保存先・statemachine-use の作成モード・--dry-run の検証・見本の頼み方（@record 行）
AI が定義を書く（workflow.yaml / actions/*.md）→ 定義があれば「利用可能」。確認は概要の実行と構成確認
```

見本の依頼は AI の返答の `@record browser <URL>` / `@record windows <アプリ名>` の 1 行で受ける
（`renderer/teachingProtocol.js`。main の依頼文と renderer の解析・固定文が同じ約束事を読む）。
Windows では AI は WSL の tmux にいて画面は Windows 側にあり、WSL から Windows 側の `playwright-cli` を
起こすことはできない。そこで見本の取り方は画面の種類で分ける（ADR-10）:

```text
ブラウザ（AI が CDP 越しに記録する。ボタンは固定文を tmux へ流すだけ）
  AI: @record browser <URL> → 見本のカードが開く → 利用者「記録を始める」
    → automation:teach:browser  main が Edge（無ければ Chrome）を記録専用プロファイルで
                                --remote-debugging-port=9222 付きで起こし、/json/version の応答を待つ
    → renderer が固定文 recordingStartMessage（@recording start + 接続先）を会話の送信経路
      （turn:send / term:submit = tmux）で AI へ渡す
  AI: playwright-cli attach --cdp=http://localhost:9222 → recording-start → 待つ
  利用者が操作 → 「終了してAIへ渡す」→ 固定文 recordingStopMessage（@recording stop）
  AI: recording-stop → 記録の行を .statemachine/<名前>/recordings/<時刻>-browser.md に保存 → detach → 工程を組む
Windows アプリ（agent-app が winauto で記録する）
  「記録を始める」→ automation:recording:start（winauto record）→ 操作 →「終了してAIへ渡す」
    → automation:recording:stop → Markdown（recordings/<時刻>-windows.md）→ 所在を host.toHostPath で
      WSL 表記へ直して次のターンとして送る（automation:teach:demonstration）
```

依頼文（`teaching.prompt`）にこの流れをすべて仕込む: 固定文が届く前にブラウザを起こしたり記録を始めたり
しないこと、利用者が操作している間はブラウザを操作しないこと、winauto の記録は自分で起こさないこと。
Edge の起動（`main/automation/browser.js`）は Electron に触れず、起動・応答確認の関数を引数で受ける。

### 3.4 ワークフローの教示と差し戻し

ワークフローもタスクと同じ骨格で作る。実装は maker 側（`flow.js`、`flow-teaching-model.js`、
`flow-teaching-store.js`、`flow-teaching-compiler.js`、`ai.js` の `flow-teach` モード）にあり、agent-app は
`automation:` 経由で呼び、下書きを一覧へ合流させるだけである。

```text
目的を書く → AI に相談（質問 or 候補） → 代表的な依頼で試運転 → 期待どおり → 利用可能にする
   draft   →        needs-trial          →   awaiting-confirmation   →        ready
```

- 教えた内容は定義とは別の sidecar `<repo>/.agents/workflows/.teaching/<id>.json` に持ち、定義の一覧探索
  （`flow:list`）には混ざらない。候補は世代（`generations`）として積み、試運転（`trials`）は agent-flow の
  `runId` で参照する。
- 試運転は `flow:run:start` に `source: { type: 'draft', workflow }` で候補をそのまま渡す。結果画面で
  「期待どおり」を選ぶと `awaiting-confirmation`、「修正が必要」なら `needs-trial` に戻る。
- 「この内容で利用可能にする」（`flow:teaching:confirm`）は、成功した試運転がその世代にあり、digest が
  試運転時と一致するときだけ、定義を `<repo>/.agents/workflows/<id>.json` へ保存する。
- 「手動で作成」と「編集」は従来の DAG エディタで、AI を介さずに作れる。
- 差し戻し（`rework`）は `deps` に混ぜず、定義の別配列に持つ。戻り先は祖先ノード、きっかけは `human`
  ノードの却下か `verify` ノードの失敗、最大回数 1〜20 と上限後の動作（人に確認 / 失敗終了 / 続行）を
  必須とし、`flow-model.normalize` が保存前に検査する。画面ではグラフ外側の専用レーンに描き、実行時は
  agent-flow が置換ノードを生成する（実行グラフは常に DAG）。

検討記録は [`2026-09-06-agent-app-agent-flow-teaching-workspace-design.md`](../plans/2026-09-06-agent-app-agent-flow-teaching-workspace-design.md)
にある。

## 4. 会話の実行経路

### 4.1 ターンの流れ

```text
renderer: turnOptions()（方針 / 直接指定 / Ask / スキル）+ 本文 + 添付
  → turn:send
main: guardedRunTurn … 同時実行枠を取る
  → runTurn
      1. executionSpec … 方針を tier へ解決し、CLI / model / readonly を確定
         concreteCli   … CLI が `herd` なら一族の共通 TUI（agent-herd の既定バックエンド）に写し、依頼の形
                         （Ask / 作業フォルダのファイル添付 / それ以外）を `/find` `/edit` の行で表す（`herd.js`）
      2. agentCli.load … 定義を読む。listAgents でホスト側の PATH に実体があるか確認
      3. transport を決める（tmux か headless か）
      4. withAttachments … 添付を確かめ、依頼文の末尾に所在を添える
      5. その CLI の初回だけ: 開始アクション（コマンドは先に実行、スキルは後で送る）
      6. skillSelection.select / deliver … 依頼単位のスキルを選び、CLI の能力に合わせて渡し方を決める
      7. sessionSetup.withInstructions … 共通指示をマーカー付きで前置
      8. 会話の「次のターンの既定」を更新
  → runTmux | runHeadless
```

同時実行枠（`executionGate`）は main が正典で、上限到達時はキューへ積まず `CONCURRENCY_LIMIT` で断る。
正常終了・失敗・停止・spawn 失敗のすべてで枠を解放する。

### 4.2 起動方針の解決

`settings.resolve` は副作用のない一関数で、次の順に決める。

```text
policy=direct、または policy 無しで cli がある → 指定の CLI / model（source: direct）
policy が recommended / saving / quality      → medium / small / large の tier
指定なし                                       → config.execution.defaultPolicy の tier
tier → config.execution.tiers[tier] の CLI / model（source: policy）
```

tier の CLI が空なら送信前に止める。CLI がホストで利用不能なら `AGENT_UNAVAILABLE` で断り、別 tier へ
倒さない。解決結果（`policy` / `tier` / `cli` / `model`、`herd` なら `family`）は利用者メッセージと応答
メッセージの両方に残す。`herd` は一族の外へ倒さない（ADR-8）。

### 4.3 transport の選択

| 条件 | transport |
|---|---|
| 設定 `transport: tmux` かつ定義に `interactive` 節があり、ホストに tmux がある | `tmux` |
| それ以外（設定が headless、対話定義なし、tmux なし） | `headless` |

ヘッドレスへ移るターンでは、動いていた tmux の CLI を先に止める。会話の `transport` はターンごとに
更新され、一覧の印と画面の構成（端末ミラーの有無）はこの値で決まる。

### 4.4 tmux 経路

1. `openConversation` が会話の tmux セッションを持つ。無ければ `interactiveCmd` で argv を組んで起動し、
   生きていれば再接続する。起動条件（CLI / model / readonly）が動いているものと違えば起動し直す。
2. 起動し直す前に、旧 CLI の画面を `agent_switch` スナップショットとして会話へ残す。
3. `waitReady` が定義の `ready_pattern` を待つ。起動中の信頼確認や権限確認（attention）はタイムアウト
   させず、人が端末で答えるまで待つ。
4. 開始スキルは本依頼へ連結せず、1 件ずつ独立した入力として送り、完了を待つ（codex の `$skill` は
   候補確定のため Enter を 2 回）。
5. その CLI がまだ見ていないやり取りがあれば `replayPrompt` で依頼の前に添える。
6. `Conversation.send` が本文を 1 行へ畳んで `send-keys -l`、少し置いて Enter を送る。両方成功した時点で
   受付済みとし、応答は `ready_pattern` が 2 回続けて見えた時点でスクロールバックの差分から拾う。
7. 応答中に利用者が追加入力した場合は `term:submit` が既存ターンを壊さず本文を送り、履歴には利用者
   メッセージだけを足す（`followup: true`）。

### 4.5 ヘッドレス経路

`turnCmd` が 1 ターン分の argv と stdin を組み、`spawn` する。Windows では `wsl.exe -e bash -lc` に載せて
WSL の中で走らせる。stdout / stderr は行ごとに `turn:line` で流し、codex の JSONL は `response.js` が
reasoning / command / file change を共通イベントへ変換する。終了後に output file または stdout を回答とし、
定義の `errors` で失敗理由を分類する。

### 4.6 エージェントの渡り歩きとセッション継続

会話は CLI ごとに「セッション ID」と「そこまで見たメッセージ数」を `cliSessions` に覚える。

| 作法 | CLI | 初回 | 作り直すとき |
|---|---|---|---|
| mint | claude / copilot | こちらで UUID を発行して `--session-id` | `--resume <UUID>` |
| capture | codex | `--json` の `thread_id` を拾う | `codex resume <id>`（ID が無ければ新規起動） |
| list | kiro | ターン後に一覧から最新を拾う | `--resume-id` |
| continue | 定義に `continue_args` がある | なし | `--continue`（直前セッション。並行運転で混線しうる） |
| replay | 上記のいずれも無い | 会話全体を依頼に添える | 同左 |

別の CLI で進めた分は、戻ってきたときに「あなたのセッションの外で進んだやり取り」として差分だけを
添える。全部を毎回送り直しはしない。tmux の会話は 1 つの CLI しか持たないので、別の CLI へ移ると前の
CLI は止め、戻るときは resume で続ける。

## 5. 端末ミラーと入力

### 5.1 画面の写し方

tmux サーバは自前のソケット `-L agent-app` に持ち、利用者の tmux とは干渉しない。ホストとの会話は
`host.js` の常駐 `bash -l`（Windows は `wsl.exe -e bash -l`）1 本へコマンドを流し、開始・終了マーカーと
終了コードで区切る。毎回 spawn しないのは、Windows で wsl.exe を起こすと 1 回に数十〜数百 ms かかり、
画面が追いつかないためである。

画面は `capture-pane -e` を 0.25 秒（見ている画面がある、または応答中）〜1.2 秒（それ以外）ごとに写し、
xterm.js に丸ごと描き直す。xterm は表示とキーボードだけで、スクロールバックとカーソルは tmux が正である。
node-pty も attach も使わない。

### 5.2 画面の判定

`classify` は ANSI を剥がした画面を `attention → busy → ready → starting → unknown` の順に見る。
attention（y/n、許可、信頼確認）は末尾行から拾い、その後ろに入力欄が見えていれば解除する。定義の
`ready_pattern` / `busy_pattern` に加え、共通の既定パターンを常に有効にするので、配布済みの古い定義でも
起動待ちがタイムアウトまで続かない。

判定の使い道は次の三つに限る。

- ターン完了（ready が 2 回連続、または `idle_quiet_sec` の無変化）
- 会話一覧と phase 表示（起動中 / 待機 / 応答中 / 確認待ち / 終了 / セッション消失）
- 応答本文の抽出（送信前後のスクロールバック差分から入力欄・枠線・フッター・依頼の echo を除く）

入力欄の可否は判定に依存しない。CLI が処理中や質問待ちに見えても、メッセージモードから文章を送れる。

### 5.3 入力の 2 モード

入力先は CLI 出力から推測せず、利用者が見分けられる明示的な 2 モードとする（`inputMode.js`）。

| モード | 入口 | 動作 |
|---|---|---|
| メッセージ | 初期状態、入力欄クリック、Escape 2 回 | Enter で本文を tmux へ貼り付けて送信。Shift+Enter は改行。成功まで本文を保持 |
| 端末操作 | xterm クリック、切替ボタン、仮想キー | 文字・矢印・Tab・Escape・Ctrl+C を `send-keys` へ直接渡す。Escape 1 回目は CLI へ、600 ms 以内の 2 回目でメッセージへ戻る |

同じキーイベントを両方へ送らない。tmux が終了・消失したときは端末操作を無効化し、メッセージモードへ
戻す。送信状態は `受付済み・<agent>を準備中` / `✓ <agent>へ送信済み HH:mm:ss` / `送信失敗・入力内容を
保持しました` / `セッション終了` の、アプリ自身が確認できる 4 つだけである。

### 5.4 tmux セッションのライフサイクル

```text
未作成 ─初回送信→ 起動中 → 利用中
                        ├─ 起動条件の変更 → スナップショット保存 → 起動し直し → 利用中
                        ├─ pane 終了 → 終了済み（最終画面をスナップショット）
                        ├─ 会話削除 → 即時 kill
                        └─ アプリ終了 → idle（24 時間以内なら再接続、超過で回収）
```

会話の `terminalSession` に名前、状態、所有インスタンス、最終利用時刻、期限を持つ。回収するのは
「期限を過ぎ、会話が追跡外で、名前が会話 ID から導いた期待値と一致する」セッションだけで、実行中や
管理外のセッションは消さない。スナップショットは件数と文字数の上限つきで会話に残し、再実行用の
構造化メッセージには使わない。

## 6. 共通指示・開始アクション・スキル選択

三つは適用タイミングが違う。

| 設定 | 適用 | 実装 |
|---|---|---|
| 共通指示 | 依頼ごと | `sessionSetup.withInstructions` がマーカー付きブロックを前置。二重注入はマーカーで防ぐ |
| 開始アクション | CLI ごとの新しいセッションで一度だけ | コマンドは作業フォルダで先に実行（1 件 60 秒・全体 120 秒）、スキルは対話セッションへ先に 1 件ずつ送る |
| スキル選択 | 依頼ごと（自動 / 手動選択 / 使用しない） | `skillSelection.select` がローカルで採点（LLM 不使用）、`deliver` が CLI の能力で渡し方を決める |

スキルの渡し方は定義の `slash_native` で分かれる。ネイティブなら `skill_command_prefix` 付きの
呼び出し（`/name` や `$name`）を依頼より前に送り、そうでなければ選んだ `SKILL.md` の本文だけを予算内で
インライン化し、はみ出した補助スキルは丸ごと外して実行情報に理由を残す。候補一覧や全スキル本文は
モデルへ送らない。

同じ合成はタスクの手動実行にも使う。`automation/ipc.js` の `prepareRun` が共通指示・開始アクション・
スキル選択を 1 つの `instruction` にまとめ、共有ワークベンチの runner へ実行時オーバーレイとして渡す。
設定ファイルは書き換えない。

## 7. 作業フォルダと添付

### 7.1 作業フォルダ（git worktree）

置き場は `<リポジトリ>/.worktrees/<名前>` に決め打つ。名前だけを保存すれば、Windows でも
「登録したパス + `.worktrees` + 名前」から fs 用（`C:\…`）と git 用（`/mnt/c/…`）の両方を作れる。
初回に `.worktrees/` を `.git/info/exclude` へ足し、リポジトリの `.gitignore` は触らない。

この画面の外で作った worktree は一覧に出すが会話には選べない。削除は未コミットの変更が残っていると
git が断り、確認のうえ `--force` で押し切れる。その作業フォルダを使う会話の tmux は先に止める。
ワークフローの納品（agent-flow が origin へ公開したブランチ）は `fetchRemote` 付きの `create` で
作業フォルダとして開く。

### 7.2 添付

外のファイル（選択・ドロップ・貼り付け）は userData の `attachments/<ID>/<名前>` へ写し、CLI には
依頼文の末尾に「添付ファイル: <パス>」として伝える。画面は生のパスを持たず、以後の参照は ID だけ。
リポジトリの中のファイル（ファイル画面の「会話に添付」）は写さず相対パスを伝える。`file_flag` を
宣言する定義（aider 等）には argv でも渡す。会話を削除すると写した添付も消える。

## 8. 正典と保存データ

| 種類 | 置き場 | 所有者 | 扱い |
|---|---|---|---|
| 設定 | userData の `config.json` | agent-app | `settings.normalize` で既知キーを正規化し、未知キーは保持。temp + rename で保存 |
| 会話 | userData の `sessions/<id>.json` | agent-app | 1 会話 1 ファイル。読み出し時に旧形式（`cliSession` 1 つ）を `cliSessions` へ写す |
| 添付 | userData の `attachments/<id>/` | agent-app | 会話から参照されないものは起動時に掃除 |
| CLI 定義 | `agents/*.json`（探索順は agent-cli 仕様） | agent-tools | 読むだけ。同名は先勝ち |
| CLI 側のセッションログ | `~/.claude/projects` など | 各 CLI | 触らない。ID だけを会話に覚える |
| worktree | `<リポジトリ>/.worktrees/` | git | 追加・削除だけ書く |
| タスク・ワークフロー定義 | `<リポジトリ>/.statemachine/`（AI との会話で書く。`teaching.json` と `recordings/` を含む）、`.agents/workflows/`、agent-loop 設定 | 共有ワークベンチ / CLI / agent-loop | agent-app は登録リポジトリの検査だけを足す |

renderer の `state` は取得結果・選択・下書き・実行中 ID のキャッシュで、再起動後の正典にしない。
応答中の会話 ID は起動時に `turn:running` で main から引き継ぐ。

会話ファイルの `messages[]` には、利用者メッセージに起動条件（`cli` / `model` / `readonly` /
`policy` / `tier` / `attachments` / `skillSelection`）、応答メッセージに `text`（回答の正典）と
任意の `parts.thinking` / `parts.information` を残す。`parts` が無い旧メッセージも回答表示を妨げない。
Aider と copilot の応答は読み出し時に `presentSession` が思考と回答へ分け、ディスク上の生データは変えない。

## 9. タスク・ワークフローの統合

旧 statemachine-maker の domain module と IPC 実装は `src/main/automation/`（`handlers.js` が旧 `ipc.js`）に
あり、`automation/ipc.js` が次の三点をアダプトして `automation:` 接頭辞で載せる。

| アダプト | 実装 | 内容 |
|---|---|---|
| 設定 | `configAdapter` | 共有側の `roots` / `lastRoot` を agent-app の `repos` / `lastRepo` へ写す |
| 登録検査 | `isRegistered` | 共有側の全 IPC が agent-app の登録リポジトリを要求する |
| フック | `prepareRun` / `selectSkills` / `openDelivery` | 手動実行の指示合成、依頼単位のスキル選択、納品ブランチの worktree 展開 |

共有側の実行系（agent-loop の起動、`drain`、`log --json`、statemachine-use の検査）は
`automation/agent-loop.js` / `runner.js` が担い、agent-app の会話側はコマンドの綴りを持たない。
タスクを AI と作る会話（`automation:teach:*`）だけは会話基盤（`runTurn` / tmux）を使うので agent-app 側の
`ipc.js` にある。

タスクの列挙・定期設定・実行・履歴は agent-loop の機械可読な境界だけを通す。agent-app は
agent-loop の設定探索順（リポジトリ直下 → `.agents/` → `~/.agents/`）の写しを持たない。

| 用途 | 呼ぶもの |
|---|---|
| タスク一覧と daemon 状態 | `agent-loop inspect --json --dir <repo>` |
| 定期実行の保存 | `agent-loop schedule --json --dir <repo>`（stdin に JSON） |
| 実行ログの読み出し | `agent-loop log --json --dir <repo>`（stdin に `{ workflow, runId }`） |
| 手動実行 | `agent-loop statemachine --workflow … --instruction <共通指示>`、プロンプトのタスクは `agent-loop run` |

`prepareRun` が合成した共通指示・開始アクション・スキル選択は、設定ファイルへ書かず
`--instruction` の実行時オーバーレイとして渡す。契約は
[`agent-loop 仕様書 §3.9`](../specs/agent-loop-spec.md#39-リポジトリ実行-ui-境界)にある。

## 10. 失敗時の扱い

| 失敗 | 現在の動作 | 回復方法・残る課題 |
|---|---|---|
| tier の CLI が未設定 | 送信前に `<tier> Tier のエージェントを設定してください` | 設定 > 実行制御で割り当てる |
| CLI がホストの PATH に無い | `AGENT_UNAVAILABLE` で送信を止める。別 tier へ倒さない | WSL / ログインシェルの PATH を直す |
| tmux が無い | ヘッドレスで動く（設定画面に「tmux なし」） | 入れれば次のターンから tmux |
| tmux セッションを作れない | 入力を保持し、`tmux セッションを作れません: <理由>` | WSL、tmux、CLI 定義を確認 |
| `send-keys` / Enter が失敗 | 送信済みにせず、`送信失敗・入力内容を保持しました` | 再送 |
| ready を検出できない | `ready_timeout_sec` 後に待機扱いで送る（画面で分かる）。attention 中はタイムアウトしない | 定義の `ready_pattern` を直す |
| pane が終了 | 最終画面を `pane_dead` スナップショットに残し、phase を `dead`。次の依頼で作り直す | 「再接続」か次の依頼 |
| tmux セッションが消えた | phase を `gone`、応答中なら error で終える | 「再接続」（`term:restart`） |
| 応答を画面から読み取れない | `（応答を画面から読み取れなかった。端末を確認）` を残す | 端末ミラーで確認。判定パターンを直す |
| 開始コマンドが失敗 | 設定どおり続行（warn）または開始中止（fail）。実行情報に残す | 設定 > 共通指示 |
| 手動選択したスキルが無い | `SKILL_NOT_FOUND` で送信を止める | 選び直す |
| 同時実行上限 | `CONCURRENCY_LIMIT`。キューへ積まない | 終わってから再送 |
| worktree に未コミットの変更 | git が断り、「変更ごと削除」で押し切れる | 確認のうえ force |
| 会話ファイルへ保存できない | tmux へは送信済みなので失敗扱いにせず warning で伝える | userData の書込み権限 |
| `config.json` が壊れている | 既定値で起動する | 現在は警告も退避もない |
| 共有編集面が未初期化 | 要素が最後の `navigate` を保留し、共有 renderer が controller を登録した時点で一度だけ渡す | — |
| タスクの会話で AI が応答中に見本を渡す | 記録は保存し、`AI が応答中です` で送信だけ断る | 応答が終わってから「操作の見本」を送り直す |
| 見本の道具がこの端末に無い | 依頼文にその旨を書き、見本のカードにも出す | Edge（ブラウザ）/ `winauto` を入れる。Windows アプリは Windows 上でだけ |
| ブラウザの記録用 Edge がリモートデバッグに応答しない | 20 秒待って `ポート 9222）に応答しません` で断る（固定文は送らない） | ポートを使っている別のブラウザを閉じる |
| AI が CDP の接続先に届かない（WSL が NAT） | 依頼文で「その旨を利用者に伝える」と決めている | `.wslconfig` で `networkingMode=mirrored` |
| ワークフロー教示の候補が不正 | AI 応答を `flow-model.normalize` で検査し、循環・不正な差し戻し・保存名の変更は候補として受け取らない | AI に修正を相談 |
| 試運転前に利用可能化 | `flow:teaching:confirm` が `成功した試運転を確認してから…` で断る。試運転後に候補が変わっていれば digest 不一致で断る | 試運転をやり直す |

## 11. 検証

設計上の境界は次のテストで固定する（`npm test`。tmux / git / Electron が無い環境では該当分を skip）。

| テスト | 固定するもの |
|---|---|
| `test/app.test.js` | 画面の情報構造、三領域、preload と IPC の 1 対 1、vendor と index.html の対応、共有編集面が `window.api.automation` へ直接つなぐこと、ワークフロー教示と差し戻しが通常の依存と分離していること、argv の組み立て、店（store）、git、ファイル、添付、tmux セッションの保持とスナップショット |
| `test/automation-teaching.test.js` | `@record` 行の解析、ブラウザの見本の固定文（`@recording start` / `stop`）、下書きの sidecar、最初の依頼文（保存先・作成モード・Windows/WSL の注意・固定文を待って CDP で記録すること）、見本の Markdown、kind: task の会話、記録の所在を WSL 表記で送ること |
| `test/automation-browser.test.js` | Edge / Chrome の探し方、リモートデバッグと記録専用プロファイルの引数、応答を待って接続先を返すこと、無い・応答しない・起動失敗の断り方 |
| `test/ui-consistency.test.js` | 端末と入力欄が会話画面と同じ実体であること、その見た目の定義が 1 か所であること、直値の色を足していないこと、見出しと説明を 2 つの層が描かないこと |
| `test/automation-*.test.js` | 共有ワークベンチ（旧 statemachine-maker）の domain: 工程列の正規化とコンパイル、読み戻し、記録の変換、AI 下書き・見直し、agent-loop / agent-flow との境界、statemachine-use の `run_machine.py --dry-run` を通ること、画面の言葉に内部の綴りが混ざらないこと |
| `test/tmux.test.js` | パス変換、画面判定（Kiro / Codex / Copilot / Cursor / Claude の実画面）、`waitReady` の attention、send-keys の畳み方、応答抽出、キー変換、常駐シェル、疑似 CLI との統合 |
| `test/worktree.test.js` | 名前検査、パスの組み方、`--porcelain` の読み方、作成・削除・納品ブランチの統合 |
| `test/settings.test.js` | 旧設定の tier 移行、方針解決、未知キー保持、推奨スキルの候補移行 |
| `test/herd.test.js` | 一族の判定、共通 TUI とスラッシュ行、タスク・ワークフローの名前の渡し方、一族の外へ倒さないこと、配線 |
| `test/session-setup.test.js` | 共通指示の no-op、開始アクションの分解と順次実行 |
| `test/skill-selection.test.js`、`test/skills.test.js` | 自動 / 手動 / 明示の選定、ネイティブとインラインの渡し方、予算超過、候補の読み方 |
| `test/response.test.js` | codex JSONL、Aider、copilot の思考・回答分離 |
| `test/input-mode.test.js`、`test/task-intent.test.js`、`test/execution-gate.test.js` | 入力 2 モードの遷移、教示 intent の一回限り消費、同時実行枠 |
| `test/electron-smoke.test.js` | Electron 実機で三領域を移動し、登録済み項目を開け、タスクの「手順」→「編集」に親の会話の置き場が出て、＋が作成フォーム（親の slot）を開き、ワークフローの＋が「新しいワークフローを教える」画面を開く |

`test/smoke.js` は画面のある環境で疑似 CLI と会話しスクリーンショットを撮る手動スモークで、`npm test` には
含めない。Windows / WSL の実機確認は推奨だがリリース必須条件にはしない。

## 12. 既知の制約

- 端末ミラーは capture-pane のポーリングなので、キー入力の反映に最大 0.25 秒の遅れがある。
- ターン完了と応答抽出は画面判定に依存する。新しい CLI の入力欄が既定パターンに合わなければ、完了が
  `ready_timeout_sec` まで遅れるか、本文の切り出しが崩れる（端末ミラーの表示は影響を受けない）。
- 会話 1 つにつきライブ CLI は 1 つ。複数エージェントの端末を同時に維持しない。
- `--continue` 型の CLI は「直前のセッション」を拾うため、同じ CLI を並行して使うと混線しうる。
- 思考・進捗の詳しさは CLI と経路で違う。tmux 経路では Aider / copilot の画面解析だけで、他は
  アプリ自身の進捗しか出ない。
- `config.json` の破損を通知せず、既定値で静かに起動する。
- 同時実行枠は agent-app が起動したターンだけを数える。ワークフローエンジンの内部並列は対象外。
- Windows / WSL の CJK・絵文字の表示幅と、`/mnt/c` の I/O 低下は実機でしか確かめられない。起動時はホストの
  確認（WSL 起動 + ログインシェル）と git を待たずに画面を出し、CLI の有無・worktree の変更数は届き次第
  描き足す（送信だけはその返事を待つ）。ツリー・本文・名前検索は非同期 I/O と索引で main を止めない。
- `herd` の用途は依頼の形（Ask / 作業フォルダのファイル添付 / それ以外）だけで決め、dashboard のような
  用途別の実測（qualifications）は読まない。ヘッドレス経路（tmux なし）で本文先頭の `/edit` が編集
  ハーネスへ回るかは agent-herd 側の実装に依る（TUI では回る）。
- タスク・ワークフローの画面は共有 renderer を同じウィンドウの Shadow DOM で動かしているため、
  見た目の調整は `renderer/automation/styles.css` を土台に `:host` セレクタで上書きする形に縛られる。
  タスクの会話（slot）は光の DOM なので親の `styles.css` で描く。
- タスクを AI と作る会話は、AI がリポジトリの `.statemachine/<名前>/` を直接書く。依頼文で保存先の外を
  変えないよう伝えるが、強制はしない（会話と同じ信頼境界）。定義の検証は AI の `--dry-run` と、画面の
  「構成を確認」の 2 段で行う。
- ワークフロー教示の状態（理解中 / 試運転待ち / 確認待ち）は sidecar の `status` の写しで、agent-app は
  一覧の副題に出すだけである。試運転の run と通常の run は履歴上で区別しない。

見直しの優先順位は、設定破損の可視化、判定パターンの外部化と実測の拡充の順とする。

## 13. 変更時の見取り図

| 変更内容 | 主に触る場所 | 同時に確認するもの |
|---|---|---|
| 新しい IPC | `src/main/ipc.js`、`src/preload.js` | `requireRepo` / `dirsOf` を通すこと、`{ok, data|error}`、preload と 1 対 1 のテスト |
| CLI の作法（セッション ID、再開） | `src/main/agentCli.js` の `SESSION` 表、`agents/<name>.json` | argv テスト、README の作法表、agent-cli 仕様との整合 |
| 画面判定 | `src/main/tmux.js` の既定パターン、定義の `interactive` 節 | 実画面の fixture を `tmux.test.js` に足す |
| 実行設定の項目 | `src/main/settings.js`、`renderer.js` の `turnOptions` / `settingsPatch`、`index.html` | 正規化、移行、`executionSpec`、メッセージに残す項目 |
| 開始アクション・スキル | `src/main/sessionSetup.js`、`skillSelection.js`、`automation/ipc.js` の `prepareRun` | tmux とヘッドレスの両経路、タスク手動実行 |
| 保存形式 | `src/main/store.js` | `normalizeSession` の後方互換、`presentSession` |
| 外部ライブラリ・共有ファイルの追加 | `scripts/vendor.js`、`index.html` | vendor と index.html の対応テスト、CSP |
| タスク・ワークフローの機能 | `src/main/automation/`、`src/renderer/automation/` | `api.automation.*` と `handlers.js` の `register` の対応、`<statemachine-workbench>` の Shadow DOM、`navigate` payload と DOM イベント、`automation-workbench.css` の `:host` 上書き、`test/automation-*.test.js` |
| タスクを AI と作る会話 | `src/main/ipc.js` の `startTeaching` / `demonstrate` / `launchTeachingBrowser`、`src/main/automation/teaching.js`、`src/main/automation/browser.js`、`src/renderer/taskTeaching.js`、`src/renderer/teachingProtocol.js` | 依頼文の約束事（`@record`、固定文 `@recording start` / `stop`）は main と renderer が同じモジュールを読むこと、固定文は会話の送信経路（tmux）で送ること、記録の所在を WSL 表記へ直すこと、kind: task の会話が会話一覧に出ないこと |

## 付録 A. ADR

### ADR-1 tmux ミラーを主表示にし、直接 attach と CLI 別アダプターを採らない

- 決定: `capture-pane` / `send-keys` による端末ミラーを会話の主表示へ昇格し、CLI の文言解析は履歴の
  補助記録に留める。
- 背景: 思考・回答・質問・承認待ちの表現は CLI ごと、版ごとに違い、文字列解析で共通メッセージへ変換すると
  回答の欠落や「処理中のまま終わらない」誤判定が続いた。
- 却下: node-pty で tmux へ直接 attach（Electron のネイティブ依存、Windows 配布、WSL との PTY 境界が増える）、
  CLI 別の構造化出力アダプター（出力形式の変化を追い続ける必要がある）。
- 代償: 意味単位の回答分離は行わず、利用者が文章送信と端末操作を切り替える。ポーリング負荷と表示互換性を
  管理する。
- 見直し条件: capture-pane で応答性や再現性を満たせない場合、または node-pty の Windows 配布を安定運用できる場合。
- 確信度: 高。

### ADR-2 触るのは登録リポジトリの内側だけ。画面から生のパスを受け取らない

- 決定: 全 IPC は `requireRepo` を通し、worktree は名前、添付は ID、ファイルは相対パスで受ける。
- 背景: renderer は信頼境界の外側にあり、`..` や絶対パスを持ち込ませない形が最も単純な防御になる。
- 却下: renderer で組み立てたパスを main が検証して受ける案（検証漏れが起きやすい）。
- 代償: 画面の外で作った worktree は会話に選べない。任意の場所のファイルは添付として写す必要がある。
- 見直し条件: 複数リポジトリをまたぐ作業を 1 会話で扱う要件が出た場合。
- 確信度: 高。

### ADR-3 起動方針は tier へ決定的に写し、自動フォールバックしない

- 決定: おすすめ / 節約 / 品質重視を medium / small / large へ固定で写し、利用不能なら送信前に止める。
- 背景: agent-dashboard の予算・適格性・実行レベル切替は会話用途には重く、根拠データも agent-app には無い。
- 却下: 品質評価による自動順位付け、利用量に応じた降格、生 JSON の編集画面。
- 代償: tier の割り当ては利用者が設定する。CLI が落ちていても別の CLI へは移らない。
- 見直し条件: agent-app 自身が信頼できる品質データを持つ場合、または全実行の統一スケジューラが要る場合。
- 確信度: 高。

### ADR-8 `herd` は仮想エージェントとして扱い、`herd.json` を作らない

- 状況: agent-dashboard では実行レベルに `herd` と書けるが、agent-app の一覧は `agents/*.json` から作るので
  `herd` が出ない。dashboard の `herd` は用途別の実測（qualifications）で展開される管理面のラベルで、
  agent-app には用途の軸も実測の台帳も無い。
- 決定: `listAgents` の末尾に仮想の 1 行を足す（一族が 1 つでもあるとき）。**agent-app は aider と ollama を
  選ばず、入口を agent-herd の 1 つに揃える。** 会話は一族の共通 TUI（agent-herd の既定バックエンド）を
  1 本開き、用途は本文先頭のスラッシュ行（Ask → `/find`、作業フォルダのファイル添付 → `/edit`、それ以外は
  そのまま）で伝える。タスクと AI 支援は `--agent-cli` / `--agent` を渡さず agent-herd の既定と宣言に任せ、
  agent-flow だけ（省けないので）harness の既定と同じ `aider` を渡す。一族の判定は `command[0] === 'agent-herd'`
  で、`agents/herd.json` は作らない（dashboard・agentcore と同じ規則。作ると一族判定と衝突する）。
- 却下した案: ターンごとに aider / ollama を選ぶ。会話では添付の有無で tmux セッションが起動し直り文脈が
  切れる、aider の TUI は添付を `/add` しない、会話とタスクで写す先が違い分かりにくい。
- 代償: 用途別の最適なモデルは選ばない（モデル欄が空なら定義の `default_model`）。実測に基づく選択が
  要るなら dashboard の Execution Policy Compiler の展開結果（`(agent_cli, model)` の順位）を読む形へ
  進める。

### ADR-4 タスク・ワークフローは statemachine-maker を借り、agent-app は登録と設定だけをアダプトする

- 状態: **一部を ADR-9 で改訂（2026-09-08）。** 「maker を廃止できる場合」の見直し条件が満たされ、
  statemachine-maker を agent-app へ統合した（`src/main/automation/` / `src/renderer/automation/`。
  vendor への写し・Host Adapter・`file:` リンクは無くなった）。共有 renderer をカスタム要素（Shadow DOM）で
  動かし、親子を `navigate()` と DOM イベントで同期する骨格は残る。
- 決定: maker の domain と IPC を `require` し、`automation:` 接頭辞と config adapter、3 つのフックで載せる。
  画面は maker の共有 renderer を、同じウィンドウのカスタム要素 `<statemachine-workbench>`（Shadow DOM）で
  改変せずに動かす。アプリ固有の preload 差は maker の Host Adapter（`editor-host.js`）、表示差は agent-app
  の host stylesheet に閉じ込め、親子は `navigate()` と DOM イベントで同期する。
- 背景: 同じ仕様を agent-app 側で二重に発展させると必ずずれる。maker は独立版として残す必要もあった。
  初版は iframe + `postMessage` + vendor 時の `api.` → `automationBridge.` 置換で載せたが、置換の順序に
  依存して壊れやすく、iframe 境界のぶん状態同期と CSS 調整が二重になった。
- 却下: agent-app 独自の再実装、maker を別ウィンドウで起動する案（リポジトリ選択と実行環境が二重になる）、
  maker の renderer を agent-app へ複製して移植する案、iframe + 文字列置換の継続。
- 代償: maker 側の画面変更が直接波及する。共有ファイルを足すたびに `vendor.js` と読込契約テストを揃える。
  Host 差を共有 renderer の条件分岐として増やさない規律が要る。
- 見直し条件: maker を廃止できる場合、両アプリを同じ package graph で配布できる場合、第 3 の利用 UI が
  同じ domain を必要とした場合。
- 確信度: 中。

### ADR-5 会話 1 つに tmux セッション 1 つ。24 時間保持して再接続する

- 決定: セッション名は会話 ID から決定的に導き、エージェント切替でもセッション数を増やさない。
  アプリ終了では kill せず 24 時間保持し、期限超過かつ管理対象だけを回収する。
- 背景: メッセージ単位やエージェント単位でセッションを作ると無制限に増える。一方で、アプリを閉じるたびに
  CLI を落とすと文脈が失われる。
- 却下: 終了時に全 kill、無期限保持。
- 代償: 回収の照合が要る。所有権不明や管理外のセッションは残る。
- 見直し条件: 複数の agent-app が同じ WSL を共有する運用が主になった場合（所有権の調停を強める）。
- 確信度: 高。

### ADR-6 スキル選択はローカルで決め、追加の LLM 呼び出しをしない

- 決定: 候補（設定）から依頼ごとに最大 3 件（プライマリ 1、補助 2）を bigram の一致で選び、送信前に上書きできる。
- 背景: 全スキル本文を毎回投入するとコンテキストと指示の競合が増え、毎回手動選択は操作負担が大きい。
  選定専用の LLM 呼び出しは起動時間と障害点を増やす。
- 却下: 全件投入、毎回手動、リモートの推薦サービス。
- 代償: 採点は粗く、誤選定は手動上書きで直す。定期実行には適用しない。
- 見直し条件: 誤選定が多い、主要 CLI が統一的なスキル API を提供する、定期実行へ広げる場合。
- 確信度: 中。

### ADR-7 ワークフローもタスクと同じ教示の骨格で作り、試運転と承認なしに利用可能にしない

- 決定: 目的の説明 → AI の質問と候補 → 代表的な依頼での試運転 → 利用者の承認、という進行状態
  （理解中 / 試運転待ち / 確認待ち / 利用可能）をタスクと揃える。教えた内容は定義とは別の sidecar に持ち、
  承認時だけ `.agents/workflows/<id>.json` へ書く。差し戻しは `deps` ではなく別配列の再作業ポリシーとして持つ。
- 背景: 手動の DAG 編集は node の種類や依存を理解していないと使えず、タスク側の教示体験と分かれていた。
  一方で agent-flow は固定手順の再現ではなく、入力に応じて分解・再計画するので、ステートマシンの仕事仕様を
  そのまま流用すると柔軟性を失う。
- 却下: ステートマシンの教示機能の複製、定義形式までの完全共通化、循環する `deps` による差し戻し、
  試運転なしの自動公開。
- 代償: sidecar、世代、試運転評価、再作業ポリシーの管理が増える。試運転の run は通常の実行履歴に並ぶ。
- 見直し条件: agent-flow が高水準の workflow policy schema を正式に提供した場合、共通の教示基盤を第 3 の
  実行ドメインも使う場合。
- 確信度: 中。

### ADR-9 タスクの作成・変更は AI との tmux 会話で行い、statemachine-maker は agent-app へ統合する

- 決定: statemachine-maker を独立版として残さず agent-app へ統合する。タスクの作成と変更は、会話と同じ
  CLI を tmux で起こし、手動実行の画面と同じくタスク画面の中に端末ミラーを埋め込んで進める。AI は
  `statemachine-use` スキルの作成モードで `.statemachine/<名前>/` を直接書き、定義があれば「利用可能」。
  画面操作の見本は AI が `@record` の 1 行で頼み、記録はこの端末（Windows ではその Windows 側）で取って
  所在を WSL 表記で会話へ返す。
- 背景: 構造化した教示の往復（JSON の候補 → 分離した試運転 → 承認）は、AI の応答契約・世代・試運転の
  管理を maker 側に抱え込み、agent-app の会話（tmux）と二重の実行経路になっていた。利用者は会話では
  端末の中で CLI と話しているのに、タスクだけは別の作法で、質問カードと試運転カードを往復していた。
  Windows では AI（WSL）と画面（Windows）が別の側にあり、AI に記録を起こさせる形は成り立たない。
- 却下: maker を独立版として維持したまま tmux 教示を足す（同じ画面を 2 つのアプリで保守する）、
  AI に playwright-cli / winauto の記録を起こさせる（WSL からは画面が無い・Windows 側の道具が見えない）、
  試運転と承認の往復を tmux の上に再現する（会話で聞けばよいことを画面が二重に聞く）。
- 代償: AI がリポジトリのファイルを直接書く（会話と同じ信頼境界）。「AI はファイルを変更しない」という
  maker の原則は、タスクについては取り下げる。承認は CLI 自身の許可確認（端末で答える）に任せる。
  試運転の代わりに「構成を確認」と「実行」を使う。
- 見直し条件: 無人実行に組織的な承認・監査が要る場合（重要操作の承認台帳を会話の外に置き直す）。
- 追記: ブラウザの見本については ADR-10 で「AI に記録を起こさせない」を取り下げた。Windows アプリ（winauto）
  は引き続きこの端末で取る。

### ADR-10 ブラウザの見本は、この端末が Edge をリモートデバッグ付きで起こし、AI が CDP 越しに記録する

- 決定: ブラウザの見本は agent-app が `playwright-cli` を呼んで記録する形をやめる。「記録を始める」で
  agent-app が Edge（無ければ Chrome）を記録専用プロファイルと `--remote-debugging-port=9222` 付きで起こし、
  起動できたことを固定文（`@recording start`、接続先入り）として tmux 経由で AI へ渡す。AI が WSL 側の
  `playwright-cli attach --cdp=…` で接続して `recording-start` し、「終了してAIへ渡す」の固定文
  （`@recording stop`）で `recording-stop` して記録を保存する。流れはすべて最初の依頼文に仕込み、ボタンを
  押す番になったら AI が利用者にそう言う。
- 背景: Windows では AI（WSL）から Windows 側の `playwright-cli` を起こしてブラウザを記録することはできない。
  一方、Windows 側で Edge をリモートデバッグ付きで起こしておけば、WSL の AI がそこへ接続して記録できることが
  実地で確かめられた。記録の主体を AI にすると、記録の行の解釈・保存・工程化を AI の会話 1 本に寄せられ、
  agent-app 側の変換（recording.js）を経由しないぶん往復が減る。
- 却下: agent-app が Windows 側の `playwright-cli` で記録して Markdown を渡す（ADR-9 の形。Windows 側の
  `playwright-cli` の導入と版の管理を利用者に求め、うまく動かないことが分かった）、AI に Edge の起動まで
  任せる（WSL から Windows の GUI を起こす経路が要る）、固定文を main が直接 tmux へ書く（会話の送信経路
  —— 応答中は端末へ流す・待機中は新しいターン —— を renderer が既に 1 本持っているので、そこを通す）。
- 代償: ブラウザの記録は Edge の記録専用プロファイル（初回はログインし直す）でしか取れない。WSL の
  ネットワークが NAT のままだと `localhost` が Windows 側に届かず、mirrored への切り替えを利用者に求める。
  「手順」タブの工程エディタからの記録（この端末の `playwright-cli` を直接呼ぶ古い経路）は残してあり、
  ブラウザの記録経路が 2 つある。
- 見直し条件: 工程エディタからの記録を使う人がいなくなったら古い経路を消す。Edge 以外の既定ブラウザで
  記録したい要望が出たら、起動するブラウザを設定にする。
  複数の CLI が同じ `.statemachine/` を同時に書く運用が主になった場合。
- 確信度: 中。

## 付録 B. 関連文書

- [`agent-app-spec.md`](../specs/agent-app-spec.md): 利用手順、IPC、設定、保存形式、上限。
- [`agent-cli-spec.md`](../specs/agent-cli-spec.md): `agents/*.json` の探索順と `interactive` 節の項目。
- [`agent-loop-design.md`](./agent-loop-design.md): タスクの実行・定期発火・履歴の正典。
- [`2026-09-05-agent-app-statemachine-integration-design.md`](../plans/2026-09-05-agent-app-statemachine-integration-design.md): statemachine-maker 統合（初版）の検討記録。
- [`2026-09-08-agent-app-statemachine-maker-consolidation-tmux-teaching-design.md`](../plans/2026-09-08-agent-app-statemachine-maker-consolidation-tmux-teaching-design.md): statemachine-maker の完全統合と、タスクの作成・変更を tmux 会話へ移した決定記録（ADR-9）。
- [`2026-09-05-agent-app-response-settings-design.md`](../plans/2026-09-05-agent-app-response-settings-design.md): 三層レスポンス、三分類設定、tier の検討記録。
- [`2026-09-05-agent-app-simple-navigation-ux-design.md`](../plans/2026-09-05-agent-app-simple-navigation-ux-design.md)、[`2026-09-05-agent-app-task-workflow-navigation-design.md`](../plans/2026-09-05-agent-app-task-workflow-navigation-design.md): 三領域とサイドバーの検討記録。
- [`2026-09-06-agent-app-terminal-first-tmux-design.md`](../plans/2026-09-06-agent-app-terminal-first-tmux-design.md): 端末中心の会話 UI と tmux ライフサイクルの検討記録。
- [`2026-09-06-agent-app-agent-loop-task-catalog-design.md`](../plans/2026-09-06-agent-app-agent-loop-task-catalog-design.md): タスクカタログの統合規則。
- [`2026-09-06-agent-app-skill-selection-design.md`](../plans/2026-09-06-agent-app-skill-selection-design.md): 依頼単位スキル選択の検討記録。
- [`2026-09-06-agent-app-ai-teaching-integration-design.md`](../plans/2026-09-06-agent-app-ai-teaching-integration-design.md): 会話からタスク教示への引き継ぎ。
- [`2026-09-06-agent-app-agent-flow-teaching-workspace-design.md`](../plans/2026-09-06-agent-app-agent-flow-teaching-workspace-design.md)、[同 implementation-plan](../plans/2026-09-06-agent-app-agent-flow-teaching-workspace-implementation-plan.md): ワークフロー教示、世代と試運転、差し戻しの検討記録。
- [`2026-09-06-agent-app-shared-editor-workbench-design.md`](../plans/2026-09-06-agent-app-shared-editor-workbench-design.md): iframe から共有編集面（カスタム要素 + Host Adapter）への移行の決定記録。
- [`2026-09-07-agent-app-startup-and-herd-design.md`](../plans/2026-09-07-agent-app-startup-and-herd-design.md): 起動時の重さ（Windows）の原因と対処、`herd` を会話・タスク・ワークフローで使う規則の検討記録。

個別画面の検討経緯は `docs/plans/` に残す。本書は、現在の実装を変更するときに必要な境界、データの流れ、
実行経路、失敗時の扱いを持つ。
