# agent-app 利用ガイド兼実装仕様

agent-app は、ローカルリポジトリを登録し、`agents/*.json` に定義したエージェント CLI と会話形式で作業する
Electron アプリです。CLI は tmux 上で対話起動し、画面はそのまま端末ミラーとして見せます。同じ画面から、
リポジトリのタスク（ステートマシン）とワークフロー（複数 AI の工程）も扱えます。

本書の前半はセットアップと画面の使い方、後半は IPC、設定、保存形式、tmux、上限の実装仕様です。
設計判断の背景は[設計書](../designs/agent-app-design.md)、CLI 定義ファイルの項目は
[agent-cli 仕様書](./agent-cli-spec.md)、画面ごとの補足は
[`tools/agent-app/README.md`](../../tools/agent-app/README.md) を参照してください。

対象は `tools/agent-app/` です。

## まず動かす

### 前提

| | Linux / macOS | Windows |
|---|---|---|
| Node.js と npm | 開発起動に必要 | 同左 |
| CLI（claude / codex / copilot / kiro …） | この OS の PATH（ログインシェル） | **WSL の中**の PATH |
| tmux | `apt install tmux` など | WSL の中に `sudo apt install tmux` |
| git（変更ビュー・worktree） | ローカル | WSL の中 |
| 登録するフォルダ | そのまま | `\\wsl$\<ディストロ>\…` か `C:\…` |

tmux が無くても動きます。その場合は 1 ターン 1 プロセスのヘッドレス実行（`-p` 相当）になり、
設定画面の「実行環境」に「tmux なし（ヘッドレスで動く）」と出ます。

タスクは statemachine-use スキル（同梱）と Python 3 + PyYAML があれば作成も実行もできます。
agent-tools（agent-herd / agent-loop / agent-flow）は任意で、あると `herd`（ローカル LLM）、定期実行と
実行履歴、ワークフローが増えます。不足は「タスク」画面（手順 → その他 → 実行環境）の接続診断で確認
できます（任意の道具は「任意」と出ます）。タスクの作成・変更は会話と同じ CLI を tmux で起こすので、
tmux が要ります。

### 開発起動

```bash
cd tools/agent-app
npm install        # scripts/vendor.js が画面用ライブラリを src/renderer/vendor/ へ写す
npm start
```

テストは次で走ります。tmux、git、Electron のバイナリ、表示先が無い環境では該当するテストを skip します。

```bash
npm test
```

### 初回設定

1. サイドバーの「リポジトリ」右の `•••` から「リポジトリを追加」でローカルフォルダを登録します。
2. 中央の入力欄に依頼を書いて Enter を押すと、新しい会話が始まります。
3. 使う CLI を変えたいときは、入力欄の「実行設定」を開きます。既定は「おすすめ」で、
   設定 > 実行制御の medium tier に割り当てた CLI を使います。tier が未設定なら送信前に案内が出ます。

Windows で `C:\…` のリポジトリを使う場合は、設定 > アプリの「WSL ディストリビューション」で
動かすディストロを指定します（空なら既定のディストロ）。`\\wsl$\<ディストロ>\…` のリポジトリは
パスからディストロが決まります。

設定は Electron のユーザーデータディレクトリの `config.json` に保存されます
（macOS は `~/Library/Application Support/agent-app`、Windows は `%APPDATA%\agent-app`）。

## 画面の使い方

### 会話する

会話画面は、上から会話ヘッダー、端末ミラー（tmux 会話のとき）、会話履歴、入力欄の順です。

1. 入力欄に依頼を書き、Enter で送ります（Shift+Enter は改行）。
2. 送信直後に `受付済み・<agent>を準備中`、tmux へ届いたら `✓ <agent>へ送信済み HH:mm:ss` と出ます。
   失敗したときは本文を消さず `送信失敗・入力内容を保持しました` と出ます。
3. CLI の画面は端末ミラーにそのまま出ます。ツール実行の許可や y/n を CLI が聞いてきたら、
   会話一覧と会話名の横に「確認待ち」と出るので、端末ミラーで答えます。
4. 応答が終わると会話履歴に「思考・進捗」「回答」「実行情報」の三層で残ります。
   回答は常に展開され、他の二つは折りたたみです。

CLI が処理中や質問待ちに見えても、入力欄からの送信は止まりません。文章での回答（質問への返事や
追加指示）はそのまま入力欄から送れます。

#### 別のリポジトリへ分岐する

エージェントが別の登録リポジトリへ書き込む必要があると判断すると、返答に `@fork <フォルダ>` の 1 行と
依頼の本文を書き、回答の下に「<フォルダ名> で続ける（新しい会話を分岐）」が出ます。

1. 押すと確認が出ます。フォルダが未登録なら、先に「リポジトリを追加」のダイアログで登録します。
2. 分岐先のリポジトリに新しい会話ができ、リポジトリ選択がそちらへ切り替わります。元の会話の所在を
   添えた依頼が最初のターンとして自動で送られます。
3. 分岐先の会話ヘッダーには「分岐元: <リポジトリ> › <会話名>」が出ます。押すと元の会話へ戻ります。
   元の会話では、その回答の下が「→ <リポジトリ>: <会話名>」に変わり、押すと分岐先を開きます。

分岐先はふつうの会話と同じに扱えます（削除・エージェントの切替・作業フォルダの変更ビュー）。
この作法を添えないようにするには「設定 > 共通指示」のチェックを外します。

### 端末を操作する

矢印キー、Tab、Escape、Ctrl+C のような端末操作は「端末操作」モードで行います。

- 端末ミラーをクリックするか、入力欄上の「端末操作」を選ぶと切り替わります。
- 端末操作中は入力欄が `Esc / Tab / Enter / ↑ / ↓ / ← / → / Ctrl+C` の仮想キーに置き換わります。
- Escape を 2 回続けて押すと「メッセージ」へ戻ります（1 回目は CLI へ送られます）。
  入力欄をクリックしても戻ります。
- 書きかけの文章はモードを切り替えても残ります。

人が別の端末から同じ画面を覗くときは、端末ミラーの見出しに出る
`tmux -L agent-app attach -t <名前>` を使います。

### 実行設定を変える

入力欄の「実行設定」を開くと、次の依頼だけに使う条件を選べます。要約行は
`おすすめ · codex · スキル 自動 · 実行 · リポジトリ本体` のように表示されます。

| 項目 | 選択肢 | 意味 |
|---|---|---|
| 起動方針 | おすすめ / 節約 / 品質重視 / 直接指定 | tier（medium / small / large）に割り当てた CLI を使う。直接指定では CLI とモデルをその場で選ぶ。`herd` を選ぶとローカル実行系の共通 TUI（agent-herd）を開き、用途は依頼の形で決まるスラッシュ行で伝える（§6.3） |
| スキル | 自動 / 手動選択 / 使用しない | 設定 > 共通指示の候補から、依頼に合うスキルを選んで渡す |
| Ask モード | on / off | 読み取り専用の起動引数で CLI を起動する。保証できない CLI では警告が出る |
| 作業フォルダ | リポジトリ本体 / `.worktrees/<名前>` | 会話を作る前だけ選べる。作ったあとは変えられない |

会話の途中で起動方針・CLI・モデル・Ask を変えると、次の依頼のときに CLI を起動し直します。
claude / copilot は `--resume`、codex は `resume <id>` で文脈を引き継ぎ、再開手段の無い CLI は
これまでのやり取りを最初の依頼に添えて起動します。

同じ会話の中で claude → codex → claude のように渡り歩けます。別の CLI で進めた分は、戻ってきた
ときに差分だけを「あなたのセッションの外で進んだやり取り」として添えます。

### 作業フォルダを分ける

同じリポジトリで会話を並行すると、1 つの作業ツリーを複数の CLI が同時に書き換えます。
「実行設定 > 作業フォルダ > 管理」から git worktree を作ると、会話ごとに別のフォルダ・別のブランチで
作業できます。

- ブランチ名を入れるとフォルダ名は自動で決まります（`feature/foo` → `.worktrees/feature-foo`）。
  既にあるブランチを指定すると、新しく作らずそれを持ってきます。
- 削除は未コミットの変更が残っていると断られます。確認のうえ「変更ごと削除」で押し切れます。
  ブランチは残ります。
- 設定 > アプリの「会話ごとに作業を分離」を外すと、新しい会話は常にリポジトリ本体で始まります。
  既にある worktree の会話はそのまま動きます。

### ファイルを添付する

依頼にファイルを付ける方法は 4 つあります。

| 方法 | 扱い |
|---|---|
| 「添付」ボタン | 選んだファイルを userData へ写し、パスを依頼文の末尾に添える |
| 入力欄へドロップ | 同上 |
| 画像の貼り付け | 同上（名前は `paste-<id>.<拡張子>`） |
| 「ファイル」画面の「会話に添付」 | 写さず、作業フォルダ内の相対パスを添える |

CLI は依頼文末尾の「添付ファイル: <パス>」を自分のファイル読み取りツールで読みます（画像も同じ）。
写した添付は会話を削除すると消えます。

### 変更を確認する

「変更を確認」で右側に差分パネルが開きます。会話の作業フォルダの `git status` / `git diff` を、
ターンが終わるたびに更新します。worktree の会話では「作業ツリー」（未コミット）と「ブランチ」
（分岐元から積んだコミット）を切り替えられます。行をクリックでそのファイルの差分、ダブルクリックで
ビュアーに開きます。コミットや push はここからは行いません。CLI に頼むか、端末で行います。

### タスクとワークフロー

サイドバーの「タスク」「ワークフロー」は、選択中リポジトリの `.statemachine/` と agent-loop の設定、
`.agents/workflows/` を、同じウィンドウの共有ワークベンチ（旧 statemachine-maker）で扱います。

- タスクの「＋」は作成フォームを開きます。目的を書いて「AIと作成を始める」と、会話と同じ CLI が
  リポジトリで起動し、**端末がタスク画面の中に出ます**（手動実行の画面と同じ埋め込み）。AI は
  `statemachine-use` スキルの作成モードで定義を書き、検証してから要約します。会話の利用者メッセージの
  「この依頼をタスクにする」からも、依頼本文を引き継いで同じフォームに入れます。
- AI が画面操作の見本を求めると（返答の `@record …` 行）、「操作の見本」のカードが開きます。画面と
  開始 URL（アプリ名）を確かめて「記録を始める」→ 操作 →「終了してAIへ渡す」。記録はこの PC で取り
  （Windows では Windows 側。AI は WSL の tmux にいます）、記録の場所が AI に届きます。
- 定義ができたタスクは実行詳細（概要 / 手順 / 履歴）から開き、実行・定期実行・履歴を扱えます。
  変更は「手順」の「編集」から。その場に AI との端末が出て（「‹ 工程に戻る」で戻ります）、
  戻ったときには AI が書き換えた工程を読み直しています。
- ワークフローの「＋」は「新しいワークフローを教える」画面を開きます。実現したいことを普段の言葉で
  書いて「AIに相談する」と、AI が質問するか候補の構成を返します。「手動で作成」なら従来の工程エディタ
  で直接組み立てます。
- 教示中のワークフローは一覧の先頭に「理解中 / 試運転待ち / 確認待ち」の状態で並びます。候補ができたら
  「代表的な依頼で試運転」し、結果画面で「期待どおり」か「修正が必要」を選びます。「期待どおり」の後に
  「この内容で利用可能にする」を押すと定義として保存され、一覧の「利用可能」へ移ります。
- ワークフローの工程には、後の工程（人の確認・検証）から前の工程へ戻す「差し戻し」を付けられます。
  通常の依存関係とは別に、きっかけ（人が却下 / 検証失敗）・戻り先・最大回数・やり直す指示を持ち、
  画面ではグラフの外側の専用レーンに描かれます。
- ワークフローの実行で納品ブランチが公開された場合、「納品を開く」でそのブランチを作業フォルダとして
  開けます。

画面の詳しい使い方（生成する定義の形、次の工程の決め方、記録がうまくいかないとき、画面の言葉）は
[`tools/agent-app/README.md`](../../tools/agent-app/README.md) を参照してください。

### 設定

サイドバー最下部の「設定」に三つの画面があります。生の JSON は編集しません。

| 画面 | 項目 |
|---|---|
| アプリ | 対話セッションを維持（tmux）、会話ごとに作業を分離（worktree）、WSL ディストリビューション、実行環境の状態 |
| 共通指示 | 共通指示の有効・本文（8000 字まで）、別のフォルダへの書き込みを会話の分岐で受ける（既定 ON）、スキル選択の有効・既定の選択・自動選択の候補、起動時アクション |
| 実行制御 | エージェントを最適化する（既定 ON。agent-herd が使えるときだけ効き、効いていなければ起動方針は おすすめ / 直接指定 だけ、tier は medium だけ）、既定の起動方針、tier ごとのエージェントとモデル（ローカルは `herd` の 1 語でよい）、既定を Ask にする、同時実行数（1〜8） |

起動時アクションは「スキル」か「コマンド」で、CLI ごとの新しいセッションで上から一度だけ適用します。
コマンドは作業フォルダで実行し、失敗時は「続行」か「停止」を選べます。

## よくある失敗

| 症状 | 確認すること |
|---|---|
| `<tier> Tier のエージェントを設定してください` | 設定 > 実行制御でその tier に CLI を割り当てる |
| `<cli> はこの実行環境で利用できません` | ログインシェル（Windows は WSL）の PATH にその CLI があるか |
| `使う AI「<名前>」はこの環境で使えません` | タスク・AI 支援に選んだ CLI がホストの PATH に無い。実行環境の「使える AI」で確認する |
| `このタスクの実行には agent-loop が要ります` | プロンプトのタスクは agent-loop の設定にしか無い。agent-loop を入れる |
| `tmux セッションを作れません` | WSL、tmux、定義の `interactive.command` |
| 「起動中」のまま進まない | CLI が端末で信頼確認や権限確認を出していないか。端末ミラーで答える |
| `（応答を画面から読み取れなかった。端末を確認）` | 端末ミラーに本文はある。定義の `ready_pattern` が入力欄に合っていない |
| `送信失敗・入力内容を保持しました` | tmux が生きているか。「再接続」を押して再送 |
| `セッション終了` | CLI が終了した。「再接続」か次の依頼で作り直す |
| `件実行中で、同時実行上限 … に達しています` | 他の会話が終わってから送る。上限は設定 > 実行制御 |
| `成功した試運転を確認してから利用可能にしてください` | ワークフローの候補を試運転し、結果画面で「期待どおり」を選ぶ |
| `試運転後に候補が変更されています` | AI に相談して候補が変わった。もう一度試運転する |
| `選択したスキルが見つかりません` | 手動選択したスキルが `~/.agents/skills` などに無い |
| `作業フォルダに未コミットの変更が残っています` | 変更を退避するか「変更ごと削除」 |

ここまでが利用手順です。以降は、実装者と agent-app を呼ぶ側の外部仕様を固定します。

---

## 実装リファレンス

### 1. 実行形態

| 項目 | 値 |
|---|---|
| エントリ | `src/main/main.js`（`package.json` の `main`） |
| 本番依存 | `yaml` 2.9.0（タスク定義の読み書き） |
| 開発依存 | `electron` 43.6.0、`electron-builder`、画面用ライブラリ（`@xterm/xterm` 6.0.0、`@xterm/addon-fit` 0.11.0、`@highlightjs/cdn-assets` 11.12.0、`marked` 18.0.11、`dompurify` 3.4.14、`mermaid` 11.17.2、`diff2html` 3.4.56。`npm install` 時に `vendor/` へ写す） |
| 設定ファイル | userData の `config.json` |
| ウィンドウ | `contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`。`will-navigate` と `window.open` は拒否し、`http(s)` だけ `shell.openExternal` |
| CSP | `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:` |
| 起動 | `npm start`（開発）。配布ビルドは持たない |

#### 1.1 ディレクトリ

```text
src/
├── main/
│   ├── main.js          ウィンドウ作成と IPC 登録
│   ├── ipc.js           全チャネル。requireRepo / dirsOf で登録リポジトリの内側に限定
│   ├── agentCli.js      agents/*.json の読取りと argv 組立（会話に要る分だけ）
│   ├── tmux.js          Conversation（tmux セッション 1 つ分の駆動）、画面判定、応答抽出
│   ├── host.js          常駐シェル（bash -l / wsl.exe）、パス変換、tmux・git の有無
│   ├── store.js         config.json と sessions/<id>.json
│   ├── settings.js      共通指示・実行制御の正規化と起動方針の解決
│   ├── sessionSetup.js  共通指示ブロック、開始アクションの分解と実行
│   ├── skills.js        スキル・コマンドの候補読取り
│   ├── skillSelection.js 依頼単位のスキル選定と渡し方
│   ├── response.js      codex JSONL / Aider / copilot の思考・回答分離
│   ├── executionGate.js 同時実行枠
│   ├── worktree.js      git worktree の一覧・作成・削除
│   ├── attachments.js   添付の写し・解決・掃除
│   ├── files.js         ツリー・本文・検索（読むだけ）
│   ├── git.js           変更ビュー（読むだけ）
│   ├── text.js          ANSI 剥がし、ERE → RegExp
│   └── automation/      タスク・ワークフローの共有ワークベンチ（旧 statemachine-maker の main）
│       ├── ipc.js       handlers.js を automation: 接頭辞で載せ、登録リポジトリと設定をアダプトする
│       ├── handlers.js  全チャネル（定義・実行・記録・AI 下書き/見直し・ワークフロー・下書き一覧）
│       ├── teaching.js  タスクを AI と作る会話の材料（下書きの sidecar、最初の依頼文、見本の Markdown）
│       ├── model.js / store.js  工程列の正規化・コンパイル・読み戻し、.statemachine/ の読み書き
│       ├── recording.js 操作の記録（playwright-cli / winauto）→ 工程列
│       ├── runner.js / command.js / tools.js  外部コマンドの起動と実行環境の診断
│       ├── agent-loop.js / agent-flow.js      実行基盤との境界
│       ├── ai.js / ai-diff.js                 AI 下書き・見直し（agent-herd -p）
│       └── flow-*.js                          ワークフローの定義・教示
├── preload.js           window.api（api.automation.* を含む）
└── renderer/
    ├── index.html       会話・ファイル・変更・設定・worktree ダイアログ・タスクの会話（slot）
    ├── renderer.js      画面状態と描画、送信、設定
    ├── files.js         ファイルビュー
    ├── term.js          端末ミラー（xterm.js）。createTerm() で会話用 Term とタスク用 TaskTerm を持つ
    ├── taskTeaching.js  タスクを AI と作る会話（端末ミラー・入力欄・操作の見本・作成フォーム）
    ├── teachingProtocol.js  見本の依頼の約束事（@record 行。main も読む純粋モジュール）
    ├── md.js            Markdown（marked + DOMPurify + mermaid）
    ├── inputMode.js     入力 2 モードの遷移（純粋モジュール）
    ├── navigation.js    領域名と旧設定の読み替え（純粋モジュール）
    ├── taskIntent.js    タスク作成 intent（純粋モジュール）
    ├── automation-workbench.css  共有編集面の host stylesheet（:host への上書きだけ）
    ├── automation/      共有ワークベンチの renderer（Shadow DOM の中で動く）
    │   ├── workbench-element.js  カスタム要素 <statemachine-workbench>
    │   ├── renderer.js / flow.js 概要・手順・履歴・ワークフロー
    │   ├── teaching.js           作成・編集の置き場（<slot name="teaching">）
    │   └── styles.css            共有ワークベンチの見た目
    └── vendor/          npm install 時に scripts/vendor.js が写す外部ライブラリ（git 管理外）
```

`index.html` は共有ワークベンチを `automation/workbench-element` → `teaching` → `flow` → `renderer` の順に
読み、`<statemachine-workbench data-statemachine-workbench embedded stylesheet="automation/styles.css"
host-stylesheet="automation-workbench.css">` を `#automation` に置く。その光の DOM の子 `#task-teaching`
（`slot="teaching"`）がタスクの会話の置き場である。

### 2. IPC 契約

#### 2.1 envelope

すべてのハンドラは `handle(channel, fn)` で包み、次の形で返す。

```json
{ "ok": true, "data": {} }
```

```json
{ "ok": false, "error": "利用者向けの失敗理由", "code": "…", "detail": {}, "issues": [] }
```

`code` / `detail` / `issues` は例外に付いているときだけ入る。preload の `invoke` は失敗 envelope を
`Error` に変換し、`code` / `detail` / `issues` を同名プロパティへ写す。

| code | 意味 |
|---|---|
| `AGENT_UNAVAILABLE` | 解決した CLI がホストの PATH に無い |
| `TURN_RUNNING` | その会話は応答中 |
| `CONCURRENCY_LIMIT` | 同時実行上限。`detail: { active, limit }` |
| `STARTUP_ACTION_FAILED` | 開始コマンドが `onError: fail` で失敗、または全体タイムアウト |
| `SKILL_NOT_FOUND` | 手動選択したスキルが候補に無い |

#### 2.2 チャネル

引数の `repo` は登録済みリポジトリの絶対パス、`worktree` は作業フォルダの名前（`''` は本体）。
`repo` を取るチャネルは `requireRepo` を通し、未登録・実在しないフォルダは断る。

| チャネル | preload | 引数 | 戻り |
|---|---|---|---|
| `host:info` | `hostInfo()` | — | `{ platform, distro, ok, tmux, git, home, tmuxVersion, error, socket }` |
| `config:get` | `getConfig()` | — | 正規化済み設定 |
| `config:save` | `saveConfig(patch)` | `patch` | 正規化済み設定。`wslDistro` が変わると常駐シェルとキャッシュを捨てる |
| `repo:add` | `addRepo()` | —（ダイアログ） | 設定、または `null`（キャンセル） |
| `repo:remove` | `removeRepo(repo)` | `repo` | 設定 |
| `agents:list` | `listAgents(repo)` | `repo?` | `[{ name, command, available, readonly, session, interactive }]`。`available` はホストの PATH で判定（60 秒キャッシュ）。agent-herd 一族（aider / ollama）が 1 つでもあれば末尾に仮想の `herd`（`virtual: true, members: [...]`）を足す（§6.3）。実体は `src/main/agents.js` で、タスク・ワークフローの `automation:agents:list` も同じ一覧（使えるものの名前だけ）を返す |
| `skills:list` | `listSkills(repo)` | `repo?` | スキル名の配列 |
| `skills:select` | `selectSkills(repo, text, mode, selected)` | — | 選定結果（`content` / `path` を除く） |
| `session:list` | `listSessions(repo)` | `repo?` | 会話の要約配列（更新日時の降順） |
| `session:create` | `createSession(payload)` | `{ repo, policy?, cli?, model?, readonly?, transport, worktree? }` | 会話 |
| `session:read` | `readSession(id)` | `id` | 会話（`presentSession` 適用後。`originSession: { id, repo, title } | null` と `forks: [{ id, repo, title, index }]` を添える） |
| `session:update` | `updateSession(id, patch)` | 許可キー: `title` `cli` `model` `readonly` `policy` `tier` `transport` `live` | 会話 |
| `session:remove` | `removeSession(id)` | `id` | `true`。応答中なら止め、tmux を kill し、添付を消す |
| `session:fork` | `forkSession(payload)` | `{ originId, repo, prompt, index?, skillMode? }` | `{ session, turn }`。元の会話の起動条件を写した会話を分岐先（登録済み・元と別のリポジトリ）の本体に作り、`forkPrompt`（元の会話の所在 + 本文）を最初のターンとして `turn:send` と同じ経路で送る。`index` は元の会話の応答メッセージの位置（`messages` の添字） |
| `turn:send` | `send(id, prompt, opts)` | §5 | tmux: `{ name, restarted, warning }`、headless: `{ pid, argv }` |
| `turn:stop` | `stop(id)` | `id` | 止めたか |
| `turn:running` | `running()` | — | 応答中の会話 ID 配列 |
| `attach:pick` | `pickAttachments()` | —（ダイアログ） | `[{ id, name, size }]` |
| `attach:stage` | `stageAttachment(name, bytes)` | `Uint8Array` / `ArrayBuffer` | `{ id, name, size }` |
| `attach:discard` | `discardAttachment(id)` | `id` | `true` |
| `attach:open` | `openAttachment(id, name)` | — | 既定のアプリで開く |
| `term:open` | `termOpen(id, cols, rows)` | — | `{ name, phase, detail, reused, restarted, warning, argv, launch }` |
| `term:restart` | `termRestart(id, cols, rows)` | — | 同上。残っているセッションを消して起動し直す |
| `term:state` | `termState(id)` | — | `{ name, phase, detail, busy }` または `null` |
| `term:watch` / `term:unwatch` | `termWatch(id)` / `termUnwatch(id)` | — | 追跡しているか。watch 中は 0.25 秒間隔で `term:screen` を流す |
| `term:submit` | `termSubmit(id, text)` | — | `{ accepted, text, acceptedAt, followup: true, warning }` |
| `term:keys` | `termKeys(id, data)` | xterm の `onData` 文字列 | — |
| `term:resize` | `termResize(id, cols, rows)` | — | — |
| `term:kill` | `termKill(id)` | — | 追跡していたか |
| `wt:list` | `listWorktrees(repo, { withStatus })` | `withStatus?`（既定 true） | `{ items, root, error }`。`withStatus: false` は `git worktree list` だけで返す（変更数・先行コミット数は 0）。画面は先にこれで一覧を出し、あとから true で数え直す |
| `wt:create` | `createWorktree(repo, branch, base, name)` | — | `{ name, branch, path, reusedBranch, trackedRemote }` |
| `wt:remove` | `removeWorktree(repo, name, { force, deleteBranch, forceBranch })` | — | `{ removed, branch, branchRemoved, branchError }` |
| `fs:list` / `fs:read` / `fs:find` | `listDir` / `readFile` / `findFiles` | `repo, worktree, rel|query, refresh?` | §11。`fs:find` は `{ hits: [{ rel, type, language }], truncated, indexed }` |
| `git:changes` / `git:file` | `changes(repo, worktree, scope)` / `fileDiff(repo, worktree, file, scope)` | `scope: worktree|branch` | §11 |
| `shell:openFolder` / `shell:openFile` / `shell:showFile` | `openFolder` / `openFile` / `showFile` | — | OS で開く |
| `automation:teach:start` | `automation.teachStart(payload)` | `{ repo, machine?, purpose?, policy?, cli?, model?, autoApprove? }` | §12.3。下書きと kind: task の会話を作り、最初の依頼を送る |
| `automation:teach:session` | `automation.teachSession(repo, machine)` | — | `{ machine, session, sidecar, published, tools }` |
| `automation:teach:demonstration` | `automation.teachDemonstration(repo, machine, recording)` | 記録（`recording:stop` の結果） | `{ file, relative, hostPath, source, steps, sent }` |
| `automation:*` | `api.automation.*` | §12 | 共有ワークベンチの契約 |

#### 2.3 main → renderer のイベント

| チャネル | preload | payload |
|---|---|---|
| `turn:started` | `onTurnStarted` | `{ id, argv, warning }` |
| `turn:progress` | `onTurnProgress` | `{ id, item: { text, status } }`（思考・進捗） |
| `turn:info` | `onTurnInfo` | `{ id, item }`（実行情報。§4.3 の型） |
| `turn:line` | `onTurnLine` | `{ id, kind: stdout|stderr, text }`（ヘッドレスの生ログ） |
| `turn:done` | `onTurnDone` | `{ id, message }`（保存済みの応答メッセージ） |
| `term:screen` | `onTermScreen` | `{ id, text, cursor: { x, y }, cols, rows, tail }`（色付き画面と末尾 14 行） |
| `term:phase` | `onTermPhase` | `{ id, phase, detail, name }` |
| `automation:ai:progress` / `automation:ai:result` / `automation:run:line` / `automation:run:exit` | `api.automation.on*` | 共有ワークベンチの契約 |

タスクの会話（kind: task）は `turn:*` / `term:*` を会話と同じ形で受ける。

### 3. 設定（`config.json`）

`store.normalize` が既定値と重ね、`settings.normalize` が `instructions` / `execution` を正規化する。
未知キーは保持する。保存は temp ファイルへ書いてから rename する。読取りまたは parse に失敗した場合は
既定値で起動する（通知は無い）。

| キー | 既定 | 意味 |
|---|---|---|
| `repos` | `[]` | 登録リポジトリ（重複除去、最大 30） |
| `lastRepo` | `''` | 最後に選んだリポジトリ。`repos` に無ければ先頭 |
| `lastCli` / `lastModel` / `lastReadonly` | `copilot` / `''` / `false` | 直接指定の最後の値。旧設定から tier への移行元 |
| `wslDistro` | `''` | Windows でドライブパスのリポジトリを扱うディストロ |
| `transport` | `tmux` | `tmux` または `headless` |
| `useWorktree` | `true` | 会話ごとに worktree を選べるか |
| `area` | `conversation` | `conversation` / `tasks` / `workflows`。旧値 `work` → `conversation`、`automation` → `tasks` |
| `view` | `chat` | 会話領域の表示（`chat` / `files`） |
| `lastFiles` / `lastWorktree` / `lastTask` / `lastWorkflow` | `{}` | リポジトリ → 最後の対象 |
| `automationSkillDir` / `automationAgent` / `automationModel` | `''` / `aider` / `''` | 共有ワークベンチへ渡す設定（`skillDir` / `agent` / `model`） |
| `instructions.enabled` | `true` | 共通指示を使うか |
| `instructions.text` | `''` | 共通指示本文。8000 字で切る |
| `instructions.skills` | `[]` | 自動選択の候補（`skillSelection.candidates` と同じ値。旧「推奨スキル」の移行元） |
| `instructions.skillSelection` | `{ enabled: true, defaultMode: auto, candidates: [] }` | `defaultMode` は `auto` / `manual` / `off` |
| `instructions.startupActions` | `[]` | `[{ type: skill|command, value, onError: warn|fail }]`。空の `value` は落とす |
| `execution.defaultPolicy` | `recommended` | `recommended` / `saving` / `quality` |
| `execution.optimizeAgents` | `true` | `false` なら（または agent-herd が無ければ）`saving` / `quality` を `recommended` として解決する（`settings.effectivePolicy`）。画面は同じ規則で選べなくする |
| `execution.defaultReadonly` | `lastReadonly` | 新規会話の既定 Ask |
| `execution.maxConcurrent` | `2` | 1〜8 に丸める |
| `execution.tiers.{small,medium,large}` | 各 `{ cli: lastCli, model: lastModel }` | tier ごとの CLI とモデル |

`config:save` の `patch` は浅く重ねるが、`execution.tiers` は tier ごと、`instructions` はキーごとに
既存へ重ねる。

### 4. 会話（`sessions/<id>.json`）

#### 4.1 会話

| フィールド | 意味 |
|---|---|
| `id` | UUID。ファイル名と tmux セッション名の元 |
| `kind` / `task` | `conversation`（既定）か `task`。`task` は `{ machine }`（保存名）を持ち、会話一覧（`session:list`）には出ない |
| `repo` / `worktree` / `branch` | リポジトリ、作業フォルダ名（`''` は本体）、そのブランチ。作ったあと変えない |
| `origin` | 別のリポジトリの会話から分岐したときの分岐元 `{ sessionId, repo, index }`（`index` は分岐の依頼を書いた応答の `messages` での位置。不明なら -1）。分岐していなければ `null`。分岐先の一覧は保存せず、`origin` から引く（`listForks`） |
| `cli` / `model` / `readonly` / `policy` / `tier` | **次のターン**の既定。`policy` は `recommended` / `saving` / `quality` / `direct` |
| `transport` | 最後のターンの経路（`tmux` / `headless`） |
| `title` | 最初の利用者メッセージの 1 行目（60 字） |
| `cliSessions` | CLI 名 → `{ id, seen, setupApplied? }`。`id` は CLI 側のセッション ID（`''` は再開手段なし）、`seen` はその CLI の文脈に入っているメッセージ数 |
| `live` | tmux で動いている CLI の起動条件 `{ cli, model, readonly }`、無ければ `null` |
| `terminalSession` | `{ name, state, ownerInstanceId, lastUsedAt, expiresAt, cli, model }`、無ければ `null`。`state` は `starting` / `active` / `idle` / `dead` |
| `terminalSnapshots` | `[{ id, agentCli, model, capturedAt, reason, screenText }]`。`reason` は `agent_switch` / `pane_dead` / `archive` |
| `messages` | §4.2 |
| `createdAt` / `updatedAt` | ISO 8601 |

旧形式の `cliSession`（1 つ）は読出し時に `cliSessions[cli]` へ写す。欠けているフィールドは既定値へ
正規化する。

#### 4.2 メッセージ

利用者メッセージ:

```js
{ at, role: 'user', text, cli, model, readonly, policy, tier, attachments: [{ id, name, size } | { rel, name }], skillSelection }
```

応答メッセージ:

```js
{ at, role: 'assistant', text, cli, model, policy, tier, code, elapsedMs, stopped, error,
  parts: { thinking: [{ text, status }], information: [{ type, title, status, detail, action }] } }
```

`text` が回答の正典で、`parts` は任意。`parts` の無い旧メッセージも回答表示を妨げない。
読出し時に `presentSession` が Aider（`► **THINKING**` / `► **ANSWER**`）と copilot（`●` の活動行）の
応答を `text` と `parts.thinking` へ分ける。ディスク上の生データは変えない。

#### 4.3 実行情報の型

| `type` | 出どころ |
|---|---|
| `status` | 実行の開始・終了、所要時間、終了コード |
| `command` | 開始コマンド、開始スキル、codex の `command_execution` |
| `file` | codex の `file_change`（`action`: `created` / `modified` / `deleted`） |
| `skill` | 選定・省略したスキルと理由 |
| `tool` / `reference` / `error` | 予約（現在の adapter は出さない） |

`status` は `success` / `error` / `running`。件数は思考・実行情報ともに 200 件で打ち切る。

### 5. ターンの起動条件（`turn:send`）

```js
{ id, prompt, policy, cli?, model?, readonly, skillMode, skills, attachments }
```

| 項目 | 意味 |
|---|---|
| `prompt` | 利用者が書いた本文。添付だけの依頼も可。両方空なら拒否 |
| `policy` | `recommended` / `saving` / `quality` / `direct`。無い場合は `cli` を直接指定とみなす（旧画面互換） |
| `cli` / `model` | `direct` のときだけ使う |
| `readonly` | Ask モード |
| `skillMode` / `skills` | `auto` / `manual` / `off` と、手動選択のスキル名 |
| `attachments` | `[{ id, name } | { rel }]`。最大 20 件 |

解決順（`settings.resolve`）:

```text
policy=direct、または policy 無しで cli あり → その CLI / model（source: direct）
policy=recommended → medium、saving → small、quality → large
policy なし        → execution.defaultPolicy の tier
tier → execution.tiers[tier]。cli が空ならエラー
```

CLI に渡す本文の組立順:

```text
[ヘッドレスのみ] 開始スキル・選定スキルの呼び出し行
[非ネイティブ CLI] 選定スキルの SKILL.md 本文（## 適用スキル: <name>）
<!-- agent-app-instructions --> ## 共通指示 … ## 今回の依頼
利用者の本文
添付ファイル（必要に応じて読んで参照すること）: - <パス>
```

tmux 経路では開始スキル・選定スキルの呼び出しを本文へ混ぜず、1 件ずつ先に送る。

### 6. エージェント定義の使い方

定義の探索順と全項目は agent-cli 仕様書のとおり。agent-app が読むのは `command` / `command_suffix` /
`prompt_via` / `prompt_flag` / `model_flag` / `file_flag` / `skill_command_prefix` / `slash_native` /
`default_model` / `output` / `env` / `write_args` / `readonly_args` / `readonly` / `continue_args` /
`resume_args` / `errors` / `headless_autonomy` と、`interactive` 節である。

#### 6.3 `herd`（ローカル実行系の 1 語。`src/main/herd.js`）

agent-dashboard の実行レベルと同じく、ローカルを使いたい所には `herd` と書けばよく、aider / ollama を
人が選び分けない。`agents/herd.json` は**作らない**——一族は `command[0]`（対話起動なら
`interactive.command[0]`）が `agent-herd` の定義から機械的に導く（dashboard の `herd-family.js`、
agentcore の `is_herd_family` と同じ規則）。

**agent-app は aider と ollama を選ばない。** 入口は agent-herd の 1 つで、用途は agent-herd 自身の
作法で伝える。agent-herd はクラウド CLI と同型のトップレベル入口（引数なし＝共通 TUI、`-p`＝単発）を持ち、
共通 TUI にはスラッシュの実行形（`/ask` `/find` `/edit` `/sm`。agentcore の `slashroute` 種別 B）がある。
`/edit` は編集ハーネスへ回し、どのエージェントで直すかは宣言側（agent-herd）が決める。計画・評価・抽出
などの用途は定義の `variants` が振り替える。

| 場面 | 起動 | 伝え方 |
|---|---|---|
| 会話・Ask | 共通 TUI（agent-herd の既定バックエンド `ollama` の定義。無ければ一族の他の定義） | 本文の先頭に `/find` |
| 会話・実行、作業フォルダの中のファイルを添付 | 同じセッション | 本文の先頭に `/edit` |
| 会話・実行、添付なし（userData へ写した添付は数えない） | 同じセッション | そのまま |
| タスク実行（`automation:run:start`） | agent-loop（`agent-herd harness statemachine`） | `--agent-cli` を渡さない（agent-herd の既定と宣言）。agent-loop が無いときは一族の共通 TUI と同じ定義（既定バックエンド）を名指しして同梱スキルで回す |
| AI 支援（`automation:ai:start`。読み取り専用） | `agent-herd --purpose plan` | `--agent` を渡さない（`herd` 以外の CLI は agent-herd を経由せず、定義の単発 argv で直接起こす） |
| ワークフロー実行（`flow:run:start`） | agent-flow | `--agent-cli` は省けないので harness の既定と同じ `aider` |

会話ではターンごとに CLI を入れ替えないので、tmux セッションと文脈はそのまま続く（用途が変わっても
起動し直さない。Ask ⇄ 実行の切り替えは従来どおり readonly の違いで起動し直す）。スラッシュ行は共通指示や
履歴の再送より前、本文の一番上に置く（slashroute は先頭から連続する `/name` 行だけを読む）。一族の定義が
ホストの PATH に無ければ断り、一族の外へは倒さない（ADR-3）。モデルは tier / 直接指定の値をそのまま渡し、
空なら定義の `default_model`。会話の「次のターンの既定」（`session.cli`）は `herd` のまま残し、メッセージには
実際に起こした `cli` と `family: 'herd'` を残す。実行情報に `herd → ollama /edit` のように理由を 1 行出す。
会話を開いただけ・再起動（`term:open` / `term:restart`）でも共通 TUI を開く。

タスク・ワークフローでは、statemachine-maker の `registerIpcHandlers` に `agentDefinitions`
（`agents.js` の一覧から使えるものの名前。一族が使えれば `herd` も並ぶ。agent-herd には聞かない）、
`hooks.resolveAgent`（`{ root, agent, purpose: 'task' | 'plan' | 'flow' | 'direct' }` → `{ agent }`。`''` は
「渡さない」、`direct` は agent-loop の無いときで定義を名指しする）、`hooks.assistRunSpec`（AI 支援の起動
仕様。`herd` は agent-herd、それ以外は `agentCli.oneShotCmd` の argv）を渡す。
`prepareRun` のスキルの渡し方は、渡さないときは harness の既定の定義（aider）で決める。

#### 6.1 セッション ID の作法（`agentCli.SESSION`）

定義ファイルに昇格するまでコード側に置く表。

| CLI | kind | 初回 | 再開 | 備考 |
|---|---|---|---|---|
| claude | mint | `--session-id <UUID>` | `--resume <UUID>`（定義の `resume_args`） | UUID はこちらで発行 |
| copilot | mint | `--session-id <UUID>` | 同上 | |
| codex | capture | `--json` を足し、stdout の `"thread_id"` を拾う | サブコマンド直後に `resume <id>`（ヘッドレスは `codex exec resume <id>`、対話は `codex resume <id>`） | ID が無ければ新規起動 |
| kiro | list | ターン後に `kiro-cli chat --list-sessions --format json` から cwd 一致・最新を拾う | `--resume-id <id>` | |
| その他 | — | `continue_args` があればそれ、無ければ履歴を依頼に添える | 同左 | |

#### 6.2 argv の組立

ヘッドレス（`turnCmd`）:

```text
command + [session extraArgs] + (continue|resume をサブコマンド直後へ)
        + (write_args | readonly_args) + model_flag model + file_flag <添付>… + command_suffix
        + (prompt_via=argv なら prompt_flag 本文)
```

対話（`interactiveCmd`）:

```text
interactive.command + (continue|resume をサブコマンド直後へ)
                    + (interactive.write_args | readonly_args) + model_flag model
```

`{model}` を含むトークンは model が空なら落とす。`{session}` と `{output_file}` を置換する。
`readonly` かつ定義の `readonly` が `enforced` でない場合は警告文を返す。

#### 6.3 `interactive` 節の既定

| 項目 | 既定 |
|---|---|
| `ready_pattern` | 共通パターン `^[[:space:]]*[>?❯›][[:space:]]*$` ほか（素のプロンプト、枠付き入力欄、各 CLI の入力プレースホルダ）。定義の値は共通パターンに **追加** される |
| `busy_pattern` | `working.*esc[[:space:]]+interrupt\|pending.*ctrl\+c to cancel`。同じく追加 |
| `ready_tail_lines` | 3 |
| `ready_timeout_sec` | 60 |
| `idle_quiet_sec` | 0（無効） |
| `failure_pattern` | なし |
| `prompt_inject` | `send-keys` |

パターンは `grep -E` の ERE として書き、`text.ereToRegExp` が POSIX ブラケットクラスを JS へ写す。

### 7. tmux

| 項目 | 値 |
|---|---|
| ソケット | `tmux -L agent-app` |
| セッション名 | `agent-app-<会話 ID の英数字先頭 12 桁>` |
| 起動 | `new-session -d -s <名前> -c <cwd> -x <cols> -y <rows> bash -lc 'exec <argv>'`。`history-limit 50000`、`status off`、`remain-on-exit on`、`window-size manual`、`mouse off` |
| 画面 | `display-message -p '#{cursor_x}|…|#{pane_in_mode}'` + `capture-pane -p -e`。履歴は `capture-pane -p -J -S -` |
| 送信 | `send-keys -l -- <1 行に畳んだ本文>`、350 ms 後に `send-keys -- Enter`（開始スキルで `$` 始まりの codex は 2 回） |
| 停止 | `busy_pattern` に `esc` を含む CLI は `Escape`、それ以外は `C-c` |
| 終了 | `kill-session -t =<名前>` |
| サイズ | cols 20〜400、rows 5〜200（既定 120×36） |
| ポーリング | watch 中または応答中 0.25 秒、待機 1.2 秒、終了・消失 5 秒 |

#### 7.1 phase

| phase | 意味 |
|---|---|
| `starting` | 起動中、または既存セッションへ再接続中 |
| `ready` | 入力欄が見えている |
| `busy` | 処理中 |
| `attention` | 端末で人の判断を待っている（`detail` に該当箇所を最大 1200 字） |
| `dead` | pane が終了した（`detail` に終了コード） |
| `gone` | tmux セッションが無い |

`classify` の順序は attention → busy → ready（共通）→ starting → ready（定義）→ unknown。
`unknown` が `idle_quiet_sec` の間続けば ready とみなす。ターン完了は「busy を見た後、または 2.5 秒経過後に
ready が 2 回連続」。`waitReady` は `ready_timeout_sec + 2` 秒で待機扱いに倒すが、attention 中は待ち続ける。

#### 7.2 保持と回収

| 項目 | 値 |
|---|---|
| 保持期限 | 最終利用から 24 時間（`terminalSession.expiresAt`） |
| 期限の更新 | `term:open`、`term:submit`、アプリ終了時（`idle`）。`term:keys` では更新しない |
| 回収 | 起動直後と 1 時間ごと。期限超過・会話が追跡外・名前が期待値と一致するものだけ kill |
| スナップショット | 会話ごとに最新 12 件、1 件 120,000 字（末尾） |

### 8. ホストと WSL

`host.HostShell` はディストロごとに 1 本の常駐シェル（Linux / macOS は `bash -l`、Windows は
`wsl.exe [-d <distro>] -e bash -l`）を持ち、`LANG=C.UTF-8 LC_ALL=C.UTF-8 TERM=xterm-256color` を
設定する。1 コマンドは開始マーカー + サブシェル本体（`2>&1`）+ 終了マーカーと終了コードで区切る。
既定タイムアウトは 15 秒で、超えるとシェルごと落として次回起こし直す。

パス変換は agent-dashboard と同じ規則。

| 入力 | ホスト表記 |
|---|---|
| `\\wsl$\Ubuntu\home\me` / `\\wsl.localhost\…` | `/home/me`（ディストロは UNC から） |
| `C:\Users\me\repo` | `/mnt/c/Users/me/repo`（ディストロは `wslDistro`） |
| それ以外（Linux / macOS） | 入力のまま |

`host:info` / `probe` は `tmux` / `git` / `$HOME` / `tmux -V` を 60 秒キャッシュする。ヘッドレスの
spawn は Windows では `wsl.exe -e bash -lc 'export …; cd <cwd> && exec <argv>'` に載せる。

### 9. 共通指示・開始アクション・スキル

| 項目 | 値 |
|---|---|
| 共通指示のマーカー | `<!-- agent-app-instructions -->`。本文に既にあれば再注入しない |
| 共通指示の上限 | 8000 字 |
| 分岐の作法 | `instructions.forkEnabled`（既定 true）で、`kind: conversation` の会話の依頼にだけ「別のフォルダへの書き込み」の節（`@fork <フォルダ>` の 1 行 + 本文、選べるフォルダ = 登録済みリポジトリから現在のものを除いた一覧）を共通指示の中に添える。共通指示の本文が空でも添える。本文は 20,000 字で打ち切る |
| 開始コマンド | 1 件 60 秒、全体 120 秒。作業フォルダで `cd <cwd> && <command>` |
| 開始スキル | `skill_command_prefix` + 名前。`slash_native: false` の CLI では候補に実在する名前だけ |
| 適用回数 | CLI ごとに初回のターンだけ（`cliSessions[cli].setupApplied`）。既存セッションの再開では再実行しない |
| スキル候補の探索先 | `<repo>/.agents/skills`、`<repo>/.codex/skills`、`~/.agents/skills`、`~/.codex/skills`（`SKILL.md`）、`~/.claude/commands`、`~/.kiro/commands`（`*.md`）。同名は先勝ち |
| 自動選択 | 依頼に `/name` `$name` があれば明示、次に候補を bigram 一致で採点（閾値 0.08、名前を含めば 10）。最大 3 件（プライマリ 1 + 補助）。成果物を作る依頼で候補に `self-checking` があれば補助に足す |
| 手動選択 | 候補に無い名前は `SKILL_NOT_FOUND` |
| 渡し方 | `slash_native: true` は呼び出し行（`native-command`）、それ以外は `SKILL.md` 本文をインライン（`inline-context`、合計 12,000 字。はみ出す補助は丸ごと省略） |

選定結果は利用者メッセージの `skillSelection` に残す。

```json
{ "mode": "auto", "requested": [], "selected": [{ "name": "ui-designer", "role": "primary", "reason": "依頼内容に一致" }], "omitted": [] }
```

### 10. 作業フォルダと添付

| 項目 | 値 |
|---|---|
| 置き場 | `<リポジトリ>/.worktrees/<名前>` |
| 名前 | `^[A-Za-z0-9][\w.@+-]{0,59}$`。ブランチ名からは非許容文字を `-` に置換して作る |
| 除外 | `.git/info/exclude` に `/.worktrees/` を 1 行足す |
| 作成 | ブランチが既にあれば `worktree add <dir> <branch>`、`fetchRemote` で origin にあれば `--track -b`、無ければ `-b <branch> [base]` |
| 削除 | `worktree remove [--force]`。`deleteBranch` で `branch -d`（`forceBranch` で `-D`） |
| 一覧 | `worktree list --porcelain` + まとめて撃つ `status --porcelain` 件数と `rev-list --count` |
| 添付の置き場 | userData の `attachments/<UUID>/<名前>` |
| 添付の上限 | 1 件 25 MB、1 ターン 20 件。名前は 1 要素・120 字に丸める |
| 掃除 | 起動時に、どの会話からも参照されない添付を消す。会話削除でその会話の添付を消す |

### 11. ファイルビューと変更ビュー

| 項目 | 値 |
|---|---|
| 境界 | `files.resolveInside` が realpath で登録フォルダの内側を検査。symlink 越えも拒否 |
| 読み方 | すべて `fs.promises`（非同期）。main の同期 I/O は IPC 全体と端末ミラーを止めるので使わない。stat / readdir は 16 並列 |
| 除外 | 根の `.git` と `.worktrees`。検索では `.git` `.worktrees` `node_modules` `__pycache__` `.venv` `venv` `.tox` `.mypy_cache` `.pytest_cache` `.cache` `.gradle` `.idea` `.vs` `.next` `.nuxt` `dist` `build` `target` `coverage` に（どの深さでも）潜らない |
| テキスト | 2 MB まで（超えたら先頭だけ `truncated`）。NUL を含めば `binary` |
| 画像 | 8 MB まで data URL |
| 検索 | フォルダ全体を幅優先で歩いた**索引**を root ごとに 60 秒覚え、名前の部分一致で引く（前方一致 → 部分一致、それぞれ浅い順。`/` を含む問い合わせはパス全体）。最大 200 件。索引は 100,000 件か 10 秒で打ち切り `truncated`（浅い階層は必ず載る）。ツリーの「更新」で作り直す（`refresh`） |
| 変更（worktree） | `status --porcelain --untracked-files=all` + `diff HEAD`（HEAD が無ければ `diff`） |
| 変更（branch） | `merge-base <本体のブランチ> HEAD` から `HEAD` までの `diff --name-status` と `diff` |
| 未追跡の差分 | `diff --no-index /dev/null <file>` |

### 12. タスク・ワークフロー（`automation:*`）

`registerAutomationIpc` が `src/main/automation/handlers.js` の `registerIpcHandlers` を
`channelPrefix: 'automation:'` で呼ぶ。チャネルは preload の `api.automation.*` と 1 対 1。

| 群 | メソッド |
|---|---|
| 設定・ルート | `getConfig` `saveConfig` `catalog` `addRoot` `removeRoot` `selectRoot` |
| 定義 | `listMachines` `readMachine` `machineExists` `previewMachine` `saveMachine` `openMachineFolder` |
| 実行環境 | `listAgents` `selectSkills` `toolStatus` `capabilities`（`{ herd, agentLoop, agentFlow }`。60 秒キャッシュ。使えない機能を薄くするための 1 つの答え） |
| 操作記録 | `recordingStart` `recordingStop` `recordingImport` `recordingSnapshot` `recordingExtract` `recordingState` |
| AI | `aiStart`（`mode`: `draft` / `review` / `teach` / `flow-teach`）`aiStop` `aiApply` `onAiProgress` `onAiResult` |
| タスクの下書き・会話 | `teachingList`（定義がまだ無い下書き）、`teachStart` `teachSession` `teachDemonstration`（§12.3） |
| ワークフロー | `flowCatalog` `flowList` `flowRead` `flowSave` `flowDelete` `flowPreview` `flowContext` `flowRunStart` `flowRunList` `flowRunRead` `flowRunCancel` `flowRunRespond` `flowRunResult` `flowRunLog` `flowRunDelete` `flowRunOpenDelivery` |
| ワークフロー教示 | `flowTeachingList` `flowTeachingCreate` `flowTeachingRead` `flowTeachingSave` `flowTeachingRecordTrial` `flowTeachingConfirm`（§12.2） |
| 実行 | `runSnapshot` `saveRunSchedule` `setRunDaemon` `runLog` `runStart` `runStop` `onRunLine` `onRunExit` |

agent-app 側のアダプト:

| maker の項目 | agent-app の値 |
|---|---|
| `roots` / `lastRoot` | `config.repos` / `config.lastRepo` |
| `skillDir` / `agent` / `model` | `automationSkillDir` / `automationAgent` / `automationModel` |
| `instructions` / `execution` | 同名キーをそのまま渡す |
| `isRegistered(root)` | `store.isRegistered`。全チャネルが登録リポジトリを要求する |
| `hooks.prepareRun` | 共通指示・開始アクション・スキル選択を `{ instruction, warning, information, skillSelection }` に合成 |
| `hooks.selectSkills` | `skills:select` と同じ選定 |
| `hooks.openDelivery` | 納品ブランチを `fetchRemote` 付きで worktree に作り `{ kind: 'worktree', name, branch }` |

タスクの列挙・定期設定・実行・履歴は共有ワークベンチ経由で agent-loop の機械可読な境界へ届きます。
agent-app は設定ファイルの探索も `.statemachine/` の走査も自前では行いません。

| preload | 実体 |
|---|---|
| `runSnapshot(root)` | `agent-loop inspect --json --dir <root>` |
| `saveRunSchedule(root, schedule)` | `agent-loop schedule --json --dir <root>`（stdin に JSON） |
| `runLog(root, identity)` | `agent-loop log --json --dir <root>`（stdin に `{ workflow, runId }`） |
| `runStart(payload)` | `agent-loop statemachine --workflow … --instruction <合成した指示>`、プロンプトのタスクは `agent-loop run`。`runSnapshot` が `available: false`（agent-loop が無い）なら、ステートマシンのタスクだけ同梱スキルの `run_machine.py <workflow> --agent exec --agent-command <定義から組んだ argv の JSON> --prompt-via stdin\|argv --instruction … --context k=v --input … --result-line` をこの場で回す（`automation/direct-run.js`。Windows は WSL の `python3`）。結果は同じ `RESULT {json}` 行 |

契約の全項目は [agent-loop 仕様書 §3.9](./agent-loop-spec.md#39-リポジトリ実行-ui-境界) にあります。
agent-loop の無いときの実行は履歴・定期実行・台帳を持たない（画面はそれを 1 行で言う）。

#### 12.1 親と共有編集面の同期

画面は共有 renderer をカスタム要素 `<statemachine-workbench>`（Shadow DOM）で同じウィンドウに
載せる。共有 renderer は `[data-statemachine-workbench]` の有無で埋め込みを判定し、preload の窓口は
`window.api.automation` だけを使う。

| 手段 | 向き | フィールド |
|---|---|---|
| `element.navigate(payload)`。`payload.type` は `agent-app:navigate` | 親 → 子 | `area`、`root`、`selected`、`action`（`''` / `new` / `teach`）。controller 登録前は最後の 1 件を保留 |
| `element.refresh()` | 親 → 子 | 定義と実行状態を読み直す |
| CustomEvent `statemachine:changed`（`bubbles: true`）。`detail.type` は `agent-app:changed` | 子 → 親 | `root`、`area`、`selected?` |
| CustomEvent `statemachine:teaching-view`（`bubbles: true`）。`detail.type` は `agent-app:teaching-view` | 子 → 親 | `root`、`machine`、`creating`、`published`、`title`。`hidden: true` で「出していない」 |

親は `agent-app:changed` を受け取ると一覧を再読込し、`lastTask` / `lastWorkflow` を保存する。
`selected` が来なければ選択は変えない。`teaching-view` を受け取ると、親は `#task-teaching`
（`slot="teaching"`）にそのタスクの会話を描く（同じ内容なら何もしない）。

`automation-workbench.css` は `:host` に対する上書きだけを持ち、フォルダ欄（`.folder-pane`）、
ホームタブ、見出し（`.machine-head` / `.flow-home-head`）、実行一覧（`.execution-list`）を隠し、
編集中以外は `#bar` を出さない。共有 renderer の本文に Host ごとの分岐は足さない。

タスク作成 intent（`taskIntent.create`）:

```json
{ "version": 1, "id": "<uuid>", "root": "<repo>", "purpose": "<利用者メッセージ本文>",
  "attachments": [{ "name": "a.png", "size": 1234 }], "agent": "codex", "model": "" }
```

利用者メッセージ以外、空の本文、リポジトリ未選択は作らない。親は intent があるとタスク領域を
`action: 'new'` で開き、作成フォームの目的欄に本文を入れて intent を捨てる。

#### 12.3 タスクを AI と作る会話（`automation:teach:*`）

会話基盤（`runTurn` / tmux）をそのまま使い、`kind: 'task'` の会話を保存名に紐づける。

| チャネル | 動作 |
|---|---|
| `teach:start` | `machine`（省略時は `teaching.machineNameFor(purpose)`）を検査し、定義が無ければ下書き `.statemachine/<machine>/teaching.json` を作る。会話が無ければ `settings.resolve` で CLI を決めて `kind: 'task'` の会話を作り、まだ何も送っていなければ最初の依頼（`teaching.prompt`）を `runTurn` で送る。返り値は `teach:session` と同じ + `existing` / `started` |
| `teach:session` | `{ machine, session（無ければ null）, sidecar, published, tools: { browser, windows } }`。`tools` はこの端末の PATH に `playwright-cli` / `winauto` があるか |
| `teach:demonstration` | 記録を `.statemachine/<machine>/recordings/<時刻>-<種類>.md` に書き（`teaching.recordingMarkdown`）、sidecar に控え、会話があれば所在を `host.toHostPath` で直して次の依頼（`teaching.demonstrationPrompt`）として送る。AI が応答中なら記録は保存したまま送信だけ断る |

最初の依頼文が伝えること: 保存先（`.statemachine/<machine>/`）の外を変えないこと、`statemachine-use`
スキルの作成モード（scaffold → 本文 → `run_machine.py --dry-run`）、曖昧な点だけ質問すること、見本の頼み方
（`@record browser <URL>` / `@record windows <アプリ名>` の 1 行）、記録はこのアプリが取るので自分では
`playwright-cli` / `winauto` の記録を起こさないこと（Windows では「あなたは WSL の tmux、画面は Windows 側」）、
この端末で見本を取れる道具、書き終えたら検証して要約すること。

下書き（`teaching.json`）:

| フィールド | 意味 |
|---|---|
| `version` | `2` |
| `machine` / `title` / `purpose` | 保存名、表示名（300 字）、目的 |
| `sessionId` | この下書きの会話（agent-app の会話 ID） |
| `recordings` | `[{ file, source, target, steps, capturedAt }]`。見本の控え |

定義（workflow.yaml）があれば状態は「利用可能」、無ければ「下書き」。

#### 12.2 ワークフロー教示（`automation:flow:teaching:*`）

実装は maker 側（`flow-teaching-model.js` / `flow-teaching-store.js` / `flow-teaching-compiler.js`）で、
agent-app は `automation:` 接頭辞で呼ぶだけである。

| チャネル | preload | 引数 | 動作 |
|---|---|---|---|
| `flow:teaching:list` | `flowTeachingList(root)` | — | `[{ workflowId, title, purpose, status, lastTrial }]`。読めない sidecar は飛ばす |
| `flow:teaching:create` | `flowTeachingCreate(root, purpose, { workflowId?, title? })` | 本文必須 | `workflowId` 既定は `flow-<uuid 先頭 8 桁>`、`title` 既定は本文 1 行目（80 字）。同名の下書きがあれば断る |
| `flow:teaching:read` | `flowTeachingRead(root, workflowId)` | — | sidecar。無ければ空のセッション |
| `flow:teaching:save` | `flowTeachingSave(root, workflowId, session)` | — | 正規化して temp + rename で保存 |
| `flow:teaching:trial` | `flowTeachingRecordTrial(root, workflowId, trial)` | `{ id?, generationId?, runId, outcome, assessment }` | `outcome` が `passed` なら `awaiting-confirmation`、それ以外は `needs-trial` |
| `flow:teaching:confirm` | `flowTeachingConfirm(root, workflowId, generationId, digest)` | — | その世代に `passed` の試運転があり、`digest` が一致するときだけ `ready` にし、定義を `flow:save`（create / update）で書く |
| `ai:start` | `aiStart({ root, mode: 'flow-teach', workflowId, message?, agent?, model? })` | — | `message` があれば会話へ足してから AI を呼ぶ。応答は `questions`（`understanding.unknowns` を更新）か `candidate`（`understanding` を置き換え、世代を追加して `needs-trial`） |

試運転は `flow:run:start` に `source: { type: 'draft', workflow: <世代の workflow> }` を渡す通常の実行で、
結果画面の「期待どおり / 修正が必要」が `flow:teaching:trial` を呼ぶ。

sidecar（`<repo>/.agents/workflows/.teaching/<workflowId>.json`）:

| フィールド | 意味 |
|---|---|
| `version` | `1` |
| `workflowId` / `title` | 保存名（`flow-model.ID_RE`）と表示名（300 字） |
| `status` | `draft`（理解中）/ `needs-trial`（試運転待ち）/ `awaiting-confirmation`（確認待ち）/ `ready`（利用可能） |
| `messages` | `[{ role: user|assistant, text, kind? }]`。1 件 4000 字。秘密値は `teaching-model.redact` で除く |
| `evidence` | `{ requestExamples, resultExamples, references }` |
| `understanding` | `purpose`、`scope`、`inputs`、`outputContract`、`constraints`、`nonGoals`、`decompositionPolicy`、`replanningPolicy`、`humanCheckpoints`、`qualityCriteria`、`unknowns` |
| `generations` | `[{ id, createdAt, summary, workflowSpec, workflow, digest }]`。AI が候補を返すたびに追加 |
| `activeGenerationId` / `lastSuccessfulGenerationId` | 編集中の世代と、最後に承認した世代 |
| `trials` | `[{ id, generationId, runId, outcome: passed|failed|approval-required, assessment }]` |

ワークフロー定義の差し戻し（`rework[]`、正典は `schemas/agent-workflow.schema.json`）:

| フィールド | 制約 |
|---|---|
| `id` | 定義内で一意 |
| `from` / `to` | 実在するノード。`to` は `from` の祖先（`deps` をたどって到達できる）で、同一は不可 |
| `trigger` | `human-rejected`（`from` は `human`）/ `verification-failed`（`from` は `verify`） |
| `instruction` | 必須。再計画へ渡す指示 |
| `maxIterations` | 1〜20 |
| `onExhausted` | `human` / `fail` / `continue` |

`deps` には混ぜず、`flow-model.normalize` が保存前に検査する。投入 plan では `max_iterations` /
`on_exhausted` に写す。

### 13. 上限一覧

| 対象 | 値 | 変更方法 |
|---|---|---|
| 登録リポジトリ | 30 | 変更不可 |
| 共通指示 | 8000 字 | 変更不可 |
| 同時実行 | 1〜8（既定 2） | 設定 > 実行制御 |
| 開始コマンド | 1 件 60 秒、全体 120 秒 | 変更不可 |
| スキル選定 | 3 件、インライン 12,000 字 | 変更不可 |
| 添付 | 25 MB × 20 件 | 変更不可 |
| 思考・実行情報 | 各 200 件、detail 4000 字 | 変更不可 |
| 生ログ（画面） | 2000 行 | 変更不可 |
| tmux 履歴 | 50,000 行 | 変更不可 |
| スナップショット | 12 件 × 120,000 字 | 変更不可 |
| tmux 保持 | 24 時間 | 変更不可 |
| ワークフロー教示 | 会話 1 件 4000 字、表示名 300 字、差し戻し 1〜20 回 | 変更不可 |
| ファイル本文 | テキスト 2 MB、画像 8 MB | 変更不可 |
| 名前検索 | 200 件、深さ 12、索引 100,000 件 / 10 秒、索引の保持 60 秒 | 変更不可 |
| ホストコマンド | 既定 15 秒（git 20〜120 秒、tmux 起動 30 秒） | 呼び出し側 |

### 14. リポジトリ側に置くもの

| 場所 | 内容 | 書き手 |
|---|---|---|
| `<リポジトリ>/.worktrees/<名前>` | 作業フォルダ | git（agent-app の `worktree add`） |
| `<リポジトリ>/.git/info/exclude` | `/.worktrees/` の 1 行 | agent-app |
| `<リポジトリ>/.statemachine/<名前>/` | タスク定義（AI との会話で CLI が書く。「手順」タブの保存も）、下書きの印 `teaching.json`、見本の記録 `recordings/*.md` | CLI / 共有ワークベンチ |
| `<リポジトリ>/.agents/workflows/<id>.json` | ワークフロー定義（`rework` を含む） | 共有ワークベンチ |
| `<リポジトリ>/.agents/workflows/.teaching/<id>.json` | ワークフロー教示の sidecar | 共有ワークベンチ |
| `<リポジトリ>/.agents/agent-loop.yaml` など | 定期実行の設定 | 共有ワークベンチ / agent-loop |

会話、設定、添付は userData にだけ書く。CLI 自身のセッションログ（`~/.claude/projects` など）は
CLI の管轄で、agent-app は ID を覚えるだけである。

### 付録. テスト

`npm test` は `node --test test/*.test.js` を実行する。

| ファイル | 内容 | skip 条件 |
|---|---|---|
| `app.test.js` | 構文、画面構造、preload と IPC の対応、vendor の対応、共有編集面の接続、ワークフロー教示と差し戻しの表示、argv、店、tmux 保持、git、ファイル、添付 | なし |
| `automation-teaching.test.js` | `@record` 行の解析、下書き、最初の依頼文、見本の Markdown、kind: task の会話 | なし |
| `automation-*.test.js` | 共有ワークベンチ（旧 statemachine-maker）の domain と境界。`automation-skill-engine.test.js` は statemachine-use の `run_machine.py --dry-run` を通す | skill-engine のみ python + PyYAML が無い |
| `tmux.test.js` | パス変換、画面判定、送信、抽出、キー変換、常駐シェル、疑似 CLI との統合 | 統合のみ tmux が無い |
| `worktree.test.js` | 名前、パス、`--porcelain`、作成・削除・納品ブランチの統合 | 統合のみ git が無い |
| `herd.test.js` | `herd` の一族判定、共通 TUI とスラッシュ行、タスク・ワークフローの名前の渡し方、配線 | なし |
| `settings.test.js` / `session-setup.test.js` / `skill-selection.test.js` / `skills.test.js` / `response.test.js` / `input-mode.test.js` / `task-intent.test.js` / `execution-gate.test.js` | 各モジュールの純粋関数 | なし |
| `electron-smoke.test.js` | Electron 実機で三領域を移動し、タスクの「手順」→「編集」と＋の作成フォーム（親の slot）を開き、ワークフローの＋で教示画面を開く | electron バイナリ、Playwright の Electron ドライバ、表示先のいずれかが無い |

`test/smoke.js` は `npm test` に含めない手動スモークで、画面のある環境で疑似 CLI と会話しスクリーンショットを
撮る（Linux では `SMOKE_OUT=/tmp/shots xvfb-run -a npx electron --no-sandbox test/smoke.js`）。
