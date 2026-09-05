# statemachine-maker に agent-flow ワークフローを載せる（UI 抜きの実装検討）

> 作成: 2026-09-05  
> 対象: `tools/statemachine-maker`（agent-app の「自動化」は maker の IPC と renderer をそのまま埋め込んでいるので、maker に足せば agent-app にも載る）  
> 参考実装: `tools/agent-dashboard/src/features/adhoc-flow/`（契約と読み手だけ借りる。画面は借りない）

## 結論

ワークフロー（agent-flow のユーザー定義フロー）はステートマシンと別ドメインとして、maker に 3 つのモジュールと十数本の IPC を足す。

| 層 | 足すもの | 借りるもの |
|---|---|---|
| 定義 | `flow-store.js`: `<root>/.agents/workflows/<id>.json` の一覧・読み書き | `store.js` の root 境界の流儀 |
| 変換 | `flow-model.js`: 保存形の正規化と、投入 plan への変換 | `schemas/agent-workflow.schema.json`、`template-parameters.js` |
| 実行 | `agent-flow.js`: inbox 投函、切り離し起動、bus 読み取り、停止、人の回答、結果 | `runner.js`（`capture` / `startDetached`）、`agent-loop.js` と同じ境界の置き方 |
| 露出 | `ipc.js` に `flow:*` を register | agent-app は `automation:` prefix で自動的に得る（preload は 2 か所に足す） |

ステートマシンと共有するのは、登録フォルダ（`requireRoot`）、AI 定義一覧（`tools.agentDefinitions`）、外部コマンド起動（`runner` / `command.spawnSpec`）、`{{key}}` の扱い（`template-parameters.js`）、設定の `agent` / `model` だけ。定義の置き場、正規化、実行基盤、run の読み手は共有しない。

## 何を作るか

### 1. 定義の置き場と形

`<root>/.agents/workflows/<id>.json`。形は `schemas/agent-workflow.schema.json` のルート（v2 ライブラリ形）をそのまま使う。

agent-dashboard はこの場所を「リポジトリ共有フロー（読み取り専用）」として既に読む。maker がここの編集者になれば、リポジトリと一緒に配布でき、dashboard からも同じ定義が見える。

- `id` は `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$`。`store.js` の識別名検査と同じく、画面から届いた文字列でパスを組み立てない。
- 保存前に正規化し、`entry` / `exit` は根と葉から導出して書く（v2 は明示宣言だが、初版の編集器は自動導出で足りる）。
- `tier` はスキーマが human 以外で必須にしているので常に `"auto"` を書く。maker は tier を解決しない（dashboard が読んだときは実行方針で決まる）。
- `purpose` は `implementation` 固定。`design` は dashboard の設計セッション向けの制約が多く、今回は入れない。
- `methods` / `continuation` は書かない。`x` / `y` は編集器が使うなら書く。
- 書き込みは tmp へ書いて rename（dashboard の `writeJsonAtomic` と同じ）。

却下した置き場: `.statemachine/` 配下（別ドメインを混ぜる）、userData（リポジトリと一緒に配布できない）、独自形式（契約が二重になる）。

### 2. 正規化と plan 変換（`flow-model.js`）

dashboard の `normalizeWorkflow` から purpose=design、tier 解決、methods、continuation を削った縮約。残す検査は次のとおりで、どれも `plan_strategy_user`（agent-flow 側の厳格検証）が弾く条件を保存時に先取りするもの。

- id の一意性、`goal` 非空、`kind` が `VALID_KINDS`（13 種）に入る
- `deps` の実在、自己依存なし、循環なし（Kahn 法）
- split ノードへの静的依存を禁止
- `human` は `interaction` 必須（`agentcore.interaction.normalize_spec` と同じ: mode は approval / choice / input、prompt 必須、choice は options 2 件以上）
- ノード数 64 以下

plan 変換は `{ name, nodes: [{id, goal, kind, deps, interaction?}] }` を返すだけ。tier も agent も載せない。run 単位の AI とモデルは起動引数 `--agent-cli` / `--model` で渡す（設定の `agent` / `model`）。

ノードごとの AI 指定はライブラリ形に置き場が無い（`additionalProperties: false`）ので初版は持たない。必要になったらスキーマ側に `agent` を足す判断が先。

`{{key}}` は投函前に人の入力で埋める。検出・検証・置換は maker が持つ `template-parameters.js`（定常業務の実行条件と同じ 1 実装）。`{{request}}` は予約語として素通しし、置換はエンジンの 1 か所に残す。

### 3. 実行（`agent-flow.js`）

`agent-loop.js` と同じく、コマンドの綴りをここに閉じ込める。

**投函と起動**

1. run-id を採番する（`app-<yyyymmdd-hhmmss>-<4 桁>`）。
2. `<bus>/inbox/<run-id>.json` を書く。dashboard の `submit` が書く submit_request 契約と同じ鍵: `id`、`title`、`request`、`submitter: 'agent-app'`、`purpose: 'implementation'`、`workspace`、`references`、`plan`、`submitted_at`。読み取り専用のときは `readonly: true` と `workspace: null`。
3. `agent-flow --bus <bus> --run-id <run-id> run --from-inbox [--agent-cli X] [--model Y]` を切り離して起動する。`runner.startDetached` に `logFile` を足し、stdout / stderr を `~/.agents/flow/logs/<run-id>.log` へ落とす（今は `stdio: 'ignore'`）。cwd は root にする（`<root>/agent-flow.yaml` や `.agents/agent-flow.yaml` が効く。`bus` キーがあっても `--bus` 明示が勝つ）。

run は自己完結（生存リース、park 監視、停滞回収を run 側が持つ）なので、アプリを閉じても走り続け、再起動後も bus を読めば続きが見える。

却下: `runner.stream`（1 本しか走らせられず、アプリ終了で死ぬ）、`--plan-file`（request / workspace / plan を別々に運ぶことになり、再投入も別形になる）、agent-project serve 経由（常駐が前提）。

**bus の場所**: `~/.agents/flow/bus`。dashboard の既定と同じにして、同じ run が両方から見えるようにする。この root の一覧は inbox 記録の `submitter === 'agent-app'` かつ `workspace.local === root`（読み取り専用 run は `references[0].local`）で絞る。

却下: `<root>/.agents/flow/bus`（リポジトリを汚し、ignore の管理が要る）。

**書込先（workspace）**: 既定は `{ url: origin の URL, local: root, base: 現在のブランチ, path: '', desc: 'workflow' }`。成果は origin の `af/<run-id>` ブランチに push される。origin が無いリポジトリは初版では読み取り専用へ倒す（ローカルパスを url にして push できるかは未実測）。読み取り専用のトグルを持ち、調査だけの run は workspace 無しで投函する。

成果ブランチは agent-app の既存 `wt:create`（既にあるブランチを指定すると持ってくる）で worktree 化でき、そのまま「変更」ビューで差分を読める。maker 側に新しい git 操作は要らない。

**進捗の読み取り**: bus のファイルを直接読む。CLI の `status` は人向けテキストで JSON が無い。

| 読むもの | 得るもの |
|---|---|
| `runs/<id>/meta.json` | status、created_at、heartbeat_at、failure_reason、workspace、request |
| `runs/<id>/graph.json` | nodes（id、kind、deps、goal）、strategy、iteration |
| `runs/<id>/results/<node>.json` | ノードの status、output、data、agent_cli / model |
| `runs/<id>/interactions/<ix>/request.json` と `resolution.json` | 人の確認の待ち・決着 |
| `runs/<id>/final.json` | finished_at、summary、verification、ci |

ノードの状態は agent-flow の `node_state` と同じ順で導出する（results の終端 > claim の生存 > pending）。claim の生存判定は dashboard の `claimWinner` を写す。dashboard の `readRun` は gitlab 突き合わせを含めて 240 行あるが、必要な部分は 60 行程度。

取得は renderer が `flow:run:read` を 2 〜 5 秒でポーリングする（ステートマシンの `run:snapshot` と同じ流儀）。main からの push イベントは持たない。

却下: `fs.watch`（bus は atomic rename で書かれ、macOS と WSL で通知の挙動が違う）。

**停止**: `agent-flow --bus <bus> cancel <run-id> --reason <text>` を `capture` で叩く。dashboard はファイル操作で同じ 3 手を再現しているが、CLI が正典なので写さない。

**人の確認への回答**: `runs/<id>/interactions/<ix>/responses/<response-id>.json` を append-only で書く。CLI に回答の口が無いので、dashboard の `writeInteractionResponse`（約 50 行。`agentcore.interaction.validate_response` の契約: interaction_id の一致、期限、mode ごとの answer 形、64KB 上限、`wx` で作って link）をそのまま移植する。`actor` は `agent-app-user`。

**結果**: `agent-flow --bus <bus> --run-id <id> result --json` を `capture` で叩く。sink ノードの全文出力（`final_nodes[].output / data / artifacts`）が JSON で返る。

### 4. IPC（`ipc.js` に register。agent-app では `automation:flow:…` になる）

> 型・状態の合成・エラー契約・ポーリングの規約は [UI 境界（IF）設計](2026-09-05-statemachine-maker-agent-flow-ui-interface-design.md) が正典。ここは一覧だけ残す。

| 群 | チャネル |
|---|---|
| 語彙 | `flow:catalog` |
| 定義 | `flow:list` `flow:read` `flow:save` `flow:delete` `flow:preview` |
| 準備 | `flow:context` |
| 実行 | `flow:run:start` `flow:run:list` `flow:run:read` `flow:run:cancel` `flow:run:respond` `flow:run:result` `flow:run:log` `flow:run:delete` `flow:run:openDelivery` |

全チャネルで `requireRoot` を通す。run 系は runId が `path.basename(runId) === runId` であることも見る。

`tools:status` に agent-flow の有無を足す（`agent-flow --help` 相当の `capture`）。新しいイベントチャネルは要らない。

preload は maker の `src/preload.js` と agent-app の `src/preload.js`（`automation.*`）の 2 か所。agent-app の `app.test.js` が maker の `register('…')` と preload の `invoke('automation:…')` を 1 対 1 で照合するので、片方だけ足すとテストで止まる。

### 5. renderer が持つ状態（配置は決めない）

ステートマシン用の `state.machines` / `state.current` / `state.run` とは別枠で持つ。

- `state.flows`: 一覧
- `state.flow`: 編集中 `{ id, isNew, workflow, dirty, errors }`
- `state.flowRuns`: この root の run 一覧
- `state.flowRun`: 選択中 run の読み取り結果とポーリングのタイマー

view は既存の `home | editor` に `flow-editor` を足し、home 側は `homeTab` に `flows` を足すか、実行タブの対象にワークフローを混ぜるかのどちらか。ここは UI 設計で決める。

`api.` の呼び出しは agent-app 側で `automationBridge.` に機械置換される（`scripts/vendor.js`）ので、新しい呼び出しも `api.flowXxx()` の形で書けばそのまま載る。

## 入れないもの（初版）

- AI による下書き・見直し。既存の `ai.js` はステートマシンの envelope 専用で、フロー用はプロンプトも envelope も別物になる。
- 定期実行。agent-flow に scheduler は無く、agent-loop の schedule はステートマシン向け。
- ノードごとの AI / tier / 作業ルール（methods）。ライブラリ形に置き場が無いか、dashboard の実行方針基盤が要る。
- 設計フロー（purpose=design）と設計セッション。
- 再投入（fork）と蒸留。inbox 記録が残っているので後から足せる。
- Windows。agent-flow は WSL 側にあり、maker の `runner` はネイティブ spawn なので動かない。既存のステートマシン実行（agent-loop）も同じ穴で、agent-app README の前提「CLI は WSL の中」と食い違っている。今回は解かず、記録だけ残す。

## テスト

- `flow-model.test.js`: 循環、未知の deps、split の後段、human への tier / agent、interaction の欠落を失敗させる。plan 変換のゴールデン。`{{key}}` の検出。
- `flow-store.test.js`: root 境界、id の字種、往復、atomic 書き込み。
- `agent-flow.test.js`: inbox 記録の鍵集合、起動 argv、`readRun` の状態導出（results > claim > pending）、interaction response の検証と `wx`。
- 既存の `preload-contract.test.js` と agent-app の `app.test.js` は足したチャネルを自動で照合する。
- 結合: `agent-flow --planner stub --executor stub` はユーザー定義 plan でも走るはずなので、inbox に stub 指定を入れて（`executor` は run 引数）オフラインで 1 本回す。走らなければ結合は agent-flow のテスト（`tests/test_user_plan.py`）に任せる。

## 開いた点と置いた既定

| 点 | 既定 |
|---|---|
| origin 無しリポジトリの書込先 | 読み取り専用に倒す。ローカルパス url の push は別途実測 |
| dashboard の保持期間掃除が同じ bus を消す | 共有のまま行く。困ったら agent-app 用に bus を分ける |
| `agent-flow.yaml` の `bus` と `--bus` の食い違い | `--bus` 明示が勝つ（CLI > 設定ファイル）。cwd は root |
| 一覧の絞り込みに inbox 記録を使う | inbox が gc で消えた run は一覧から落ちる。meta にも `submitter` が写るなら meta を優先する |

## 検討した案

| 案 | 判断 |
|---|---|
| A. maker に別ドメインとして足す（本案） | 採用。agent-app への露出は既存の prefix 配線で済み、契約は agent-flow と dashboard の既存物を使う |
| B. dashboard の adhoc-flow feature を maker へ移植 | 却下。4,000 行の renderer と tier / methods / profiles の基盤ごと持ち込むことになる |
| C. agent-app 側に直接 `flow` feature を作る | 却下。自動化の入口が maker と agent-app の 2 か所に割れ、登録フォルダの境界も二重になる |
| D. `--plan-file` で起動し bus を読まない | 却下。進捗も人の確認も bus にしか無い |

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-09-05 |
| 決定者 | 未承認（検討段階） |
| 提案 | maker に `flow-store` / `flow-model` / `agent-flow` の 3 モジュールと `flow:*` IPC を足し、定義は `<root>/.agents/workflows/<id>.json`、実行は inbox 投函と切り離し起動、進捗は bus 読み取り |
| 正典 | 定義の形は `schemas/agent-workflow.schema.json`、実行時検証は agent-flow の `plan_strategy_user`、run 状態は bus のファイル |
| トレードオフ | dashboard の `normalizeWorkflow` / `readRun` / `writeInteractionResponse` の縮約を maker 側に持つ（3 か所目の読み手）。契約が変わったら 3 つを揃える |
| 再評価条件 | ノードごとの AI 指定や作業ルールが要る、Windows で動かす必要が出る、maker と dashboard の読み手を共通 package にできる |
