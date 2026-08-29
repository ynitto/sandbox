# agent-dashboard 利用ガイド兼実装仕様

agent-dashboard は、agent-project、agent-flow、agent-loop、agent-amigos、agent-audit の状態と操作を 1 つにまとめた Windows 向けアプリです。プロジェクトの登録元や実行状態を独自に持つのではなく、各ツールのファイル契約を読み、承認や停止などの指示も同じ契約へ書き込みます。

本書の前半はセットアップと画面の使い方、後半は Electron、IPC、設定、読み書きするファイルの実装仕様です。画面ごとの詳細は [`tools/agent-dashboard/README.md`](../../tools/agent-dashboard/README.md)、設計判断の背景は[設計書](../designs/agent-dashboard-design.md)を参照してください。

対象は `tools/agent-dashboard/` です。

## まず動かす

### 前提

開発起動には Node.js と npm が必要です。実運用では、Windows から WSL 上の agent-project などを操作する構成を想定しています。

プロジェクトを一覧に出すには、先に WSL 側で agent-project の常駐体を用意してください。プロジェクトは dashboard で登録するのではなく、`~/.agents/agent-project.host.yaml` の `projects` へ宣言します。

```bash
agent-project serve --host-config ~/.agents/agent-project.host.yaml
```

常駐体と host 設定の作り方は[単一常駐体の導入ガイド](../guides/single-resident-setup.md)を参照してください。

### 開発起動

```bash
cd tools/agent-dashboard
npm install
npm start
```

起動するとホームが開きます。プロジェクトが 1 件も表示されない場合は、dashboard 内で登録先を探すのではなく、次の順で確認します。

1. WSL 側で `agent-project serve` が動いているか
2. `agent-project.host.yaml` の `projects` に状態ディレクトリが宣言されているか
3. `engine/status.json` にそのプロジェクトが出ているか
4. dashboard の WSL ディストロとベースパスが実際の置き場を指しているか

### 初回設定

「全体設定」で、まずこの PC の役割を選びます。

- `engineer`: 実行や設定変更も行う。既定値
- `viewer`: 閲覧とレビューを中心に使い、実行者向けの画面を隠す

役割は表示範囲の切り替えです。`viewer` でも、プロジェクト発見と指示の同期には WSL 側の常駐体が必要です。

通常は WSL の読み先を変更する必要はありません。複数ディストロを使う場合や `$HOME/.agents` 以外へ置いた場合だけ、ディストロとベースパスを設定します。GitLab の URL とトークンは任意です。設定しなくても、状態リポジトリにある情報は表示できます。

設定は Electron のユーザーデータディレクトリにある `config.json` へ保存されます。Windows では `%APPDATA%/agent-dashboard/config.json` です。

## 画面の使い方

### まずホームを見る

起動時はホームに着地します。ホームには、全プロジェクトの対応待ちと各領域への入口が表示されます。前回開いていた対象へ自動で移動はしません。

日常の確認は次の順で進めると迷いません。

1. ホームで「あなたの対応待ち」を確認する
2. 左の「プロジェクト」から対象を選ぶ
3. 「概要」で停止理由と進捗を見る
4. 「要対応」で承認、保留、差し戻しを処理する
5. 「成果」で検証結果と差分を確認する

ボタン操作は状態ファイルを直接書き換えません。`commands/` や `inbox/` に指示を投函し、常駐体が取り込んだ後に状態へ反映されます。押した直後に表示が変わらない場合は、反映待ちの表示と常駐体の稼働状態を確認してください。

### 定常業務を実行する

agent-loop や statemachine の仕事は「定常業務」から扱います。agent-project 管理外の作業フォルダは、サイドバーの「作業フォルダ」の追加ボタン、または「定常業務の設定」から登録します。

プロジェクトの状態ディレクトリと定常業務の作業フォルダは登録元が違います。

| 対象 | 登録する場所 |
|---|---|
| agent-project のプロジェクト | WSL 側の `agent-project.host.yaml` |
| agent-loop / statemachine の作業フォルダ | dashboard の「定常業務」設定 |

### ミッションと参加状況を見る

「ミッション」には agent-amigos の進行状況が表示されます。ここからの依頼、引き受け、受入、差し戻しは agent-amigos の `commands/` へ投函されます。ミッションが見えない場合は agent-amigos のバス設定と WSL 側の常駐体を確認します。

「参加」は、他ノードから募集されている agent-flow の仕事を確認し、必要なものだけ引き受ける画面です。参加操作を押したときだけワーカーを起動します。

### 利用量を確認する

「利用状況」は agent-audit の集計結果を表示します。初めて開いた端末では 1 回だけ自動収集します。その後は「今すぐ収集」または設定した間隔で更新します。dashboard が呼ぶのは `collect`、`usage`、`stats`、`doctor` だけで、LLM を使う知見蒸留は起動しません。

## 配布用にビルドする

```bash
cd tools/agent-dashboard
npm run dist
```

Windows 用の portable 版と NSIS インストーラが `release/` に作られます。portable 版だけを作る場合は `npm run dist:portable` を使います。

---

## 実装リファレンス

### 1. 実行形態

Windows の Electron アプリとして動き、エンジン（agent-project / agent-flow / agent-loop）は
WSL または別 PC に居ます。WSL 側へ触る経路はすべて `wsl.exe -e …` を通ります。

| 項目 | 値 |
|---|---|
| エントリ | `src/base/main/main.js`（`package.json` の `main`） |
| 本番依存 | `diff2html` 3.4.56 ／ `yaml` 2.9.0 の 2 つだけ |
| 開発依存 | `electron` ^43 ／ `electron-builder` ^26.15.3 ／ `eslint` ^10.8 |
| 設定ファイル | Electron のユーザーデータディレクトリの `config.json` |
| カスタム URL スキーム | `agent-dashboard://`（ディープリンク。通知クリックの飛び先） |
| 起動 | `npm start`（開発）／ `npm run dist`（配布ビルド） |

#### 1.1 ディレクトリ

```
src/
├── base/main/          Electron シェル・設定合成・git 読み取り・通知・共通 IPC
├── features/
│   ├── index.js        載せる制御面の列挙（唯一の合成点）
│   ├── agent-project/  agent-project ＋ agent-flow
│   ├── routines/       agent-loop の端末ビューと復旧送信
│   ├── cowork/         定期実行と定型業務の一覧・実行入口
│   ├── amigos/         agent-amigos ミッションの読み取りビュー
│   ├── orchestration/  ノード予算・エージェント制御・CLI ドロップイン
│   ├── delegation/     エンジン間の委譲封筒（独立画面なし）
│   ├── participation/  募集中の仕事への参加操作
│   ├── agent-audit/    実測トークン利用量・実行品質・収集
│   ├── adhoc-flow/     プロジェクトを立てない単発 run とフロービルダー
│   └── preparation/    作業準備項目の保管庫（★制御面ではない。下記参照）
├── main/               旧パス互換シム（実体は base/ と features/agent-project/ へ再エクスポート）
├── preload.js          base API ＋ 各 feature の preloadApi を合成
└── renderer/           画面（core → sections → features → bootstrap の順に読む）
```

`preparation/` は制御面ではありません。`src/features/` の下にありますが
`index.js`（feature 記述子）を持たず、`features/index.js` の列挙にも入りません。実体は
adhoc-flow・renderer・`base/main/design-contract.js` から呼ばれる共有モジュール 1 本
（`main/preparation.js`）です。ディレクトリの位置だけを見て制御面と数えないでください。
制御面は 9 つ、`src/features/` のディレクトリは 10 個です。

---

### 2. 合成契約

#### 2.1 feature 記述子

各制御面は `src/features/<id>/index.js` から次を export します。

```js
{
  id: 'agent-project',
  configDefaults: { ... },          // base の既定設定へ deepMerge される
  registerIpc(ctx) { ... },         // ctx = { handle, loadConfig, saveConfig, git, GitLabClient, dialog, shell }
  preloadApi() {                    // window.api に生えるメソッドの工場
    return { foo: (invoke) => (a) => invoke('dashboard:foo', { a }) };
  },
}
```

`configDefaults` は必ずオブジェクト（持たない制御面は `{}`。participation がこれ）。
`registerIpc` / `preloadApi` は必ず関数。この 3 点は `test/feature-split.test.js` が固定します。

#### 2.2 載せる順番

`src/features/index.js` の `loadFeatures()` が返す配列そのものが登録順です。

```
agent-project → routines → cowork → amigos → orchestration
→ delegation → participation → agent-audit → adhoc-flow
```

制御面を足す手順は 3 手だけです。`src/features/<id>/` を既存の制御面を雛形に作り、
この配列へ `require('./<id>')` を 1 行足し、必要なら renderer にタブを差し込みます。
動的ロード・サンドボックス・版管理は持ちません。

#### 2.3 IPC

全チャネルが `{ ok: true, data }` または `{ ok: false, error }` に揃います
（`base/main/handle.js` が包む）。

| 提供元 | 主なチャネル |
|---|---|
| `base` | `config:get` / `config:save` / `git:diff` / `app:notify` / `shell:openExternal` / `shell:openPath` / `gitlab:enrich` / `gitlab:mrDiscussions` / `gitlab:projectIssues` |
| `agent-project` | プロジェクト発見・要対応・実行・オーサリング・検収 |
| `adhoc-flow` | 単発 run の投入と監視、フロービルダー、設計セッション |
| `orchestration` | ノード予算・agent-control・CLI ドロップイン・手法 |
| `cowork` | 定期実行と定型業務の一覧・実行 |
| `amigos` | ミッションの読み取り |
| `delegation` | 委譲封筒の読み取りとノード宛て指示の投函 |
| `agent-audit` | `collect` / `usage` / `stats` / `doctor` / `sessions` / `summary` / `knowledge` |
| `routines` | `listSessions` / `capture` / `state` / `send` / `queue` / `queueMessage` |
| `participation` | `participation:flowJoin`（人の操作からプロセスを起動する経路） |

---

### 3. renderer の契約

ビルド工程を持ちません。`index.html` の `<script>` 読み込み順がそのまま契約です。

| 順 | 対象 | 制約 |
|---|---|---|
| 1 | `renderer.js`（core） | `state` と共有ユーティリティ、3 つの登録簿を定義する |
| 2 | `sections/*.js` | 関数宣言のみ。load 時実行を持たないので相互の順序は不問 |
| 3 | `features/*.js` | 自分のタブ／カード／設定面を登録する |
| 4 | `bootstrap.js` | `init()` の定義と呼び出し。必ず最後 |

テストは `test/helpers/renderer-src.js` がこの順で結合して「元の全文」を復元し、
文字列走査で検査します。モジュール境界（`import` / `export`）を入れると検査が全部壊れます。

#### 3.1 3 つの登録簿

core は差し込まれる中身を知りません。

| 登録簿 | 差し込み先 | フック |
|---|---|---|
| `registerFeatureTab(name, hooks)` | プロジェクトのタブ列 | `render()` / `refresh()` / `available()` / `badge()` |
| `registerPortalCard(id, hooks)` | ホーム（ポータル）画面 | `order`（既定 100）/ `html()` / `wire(container)` |
| `registerGlobalSettingsPanel(section, hooks)` | 全体設定の指定セクション | `id` / `html()` / `wire(c)` / `reveal(c)` / `refresh()` |

`registerPortalCard` の `html()` が `''` を返すとそのカードは出ません（未利用の制御面を隠す判断は
各制御面が持つ）。並び順は `order` → `id` の辞書順です。

`registerGlobalSettingsPanel` の面は自分の容れ物 `global-settings-slot-<id>` だけを描き直します。
全体設定ごと描き直すと、他の節で入力中の欄が飛びます。`reveal` は節が開いたときに呼ばれ、
重い取得（CLI 起動）はそこで初めて走らせます。

#### 3.2 領域とタブ

第一ナビは領域（ワークロード）で、タブはその領域の内部ナビです。どのタブがどの領域かは
HTML の `data-area` 属性が正で、`AREAS` は列挙と表示名だけを持ちます。領域の絞り込み
（`area-off`）と制御面の出し分け（`.hidden`）は別のしるしです。片方を使い回すと、
領域を切り替えただけで制御面の判断が消えます。

---

### 4. 設定

`config.json` に保存し、欠けたキーは既定で補完します（バージョンアップで項目が増えても
既存の設定ファイルはそのまま使えます）。既定は base の `BASE_DEFAULT_CONFIG` と
各制御面の `configDefaults` を `deepMerge` した合成です。

#### 4.1 base

| キー | 既定 | 意味 |
|---|---|---|
| `role` | `engineer` | `engineer` は全機能。`viewer` は閲覧・レビュー専用（WSL / CLI 設定不要） |
| `gitlab.baseUrl` | `https://gitlab.com` | 任意。イシューの最新状態を API で補完するときだけ使う |
| `gitlab.token` | `''` | 空なら API 補完を行わず、bus 上の結果ファイルの情報だけで表示する |
| `reviewViewer.mode` | `protocol` | `protocol` / `exe` / `command`。gitlab-review-viewer への引き継ぎ方法 |
| `reviewViewer.protocol` | `gitlab-review-viewer://open` | `protocol` モードのスキーム |
| `reviewViewer.exePath` | `''` | `exe` モード。portable exe は URL スキームを OS 登録できないためこちらを使う |
| `reviewViewer.command` | `''` | `command` モード。`{url}` `{projectPath}` `{type}` `{iid}` `{protocolUrl}` を置換 |

#### 4.2 agent-project

| キー | 既定 | 意味 |
|---|---|---|
| `engine.distro` | `''` | 状況ファイルを読む WSL ディストロ（空ならホスト既定） |
| `engine.home` | `''` | エージェントホーム（空なら実行エンジンと同じ解決） |
| `projects.refreshSec` | `5` | ポーリング間隔。`0` で自動更新を止める |
| `projects.needsSlaHours` | `24` | 要対応の停滞しきい値。超過で赤、1/3 超で黄 |
| `projects.flowBus` | `''` | agent-flow のバス（全プロジェクト既定） |
| `projects.flowBusByProject` | `{}` | プロジェクトごとのバス上書き |
| `notifications.enabled` | `true` | OS 通知・バッジ・ウィンドウフラッシュ |
| `agent.cli` | `kiro` | Viewer アシスタントが使うエージェント CLI |
| `agent.model` | `''` | 空なら CLI の既定 |
| `agent.timeoutSec` | `180` | 下限 30 秒でクランプ |

#### 4.3 その他の制御面

| キー | 既定 | 意味 |
|---|---|---|
| `routines.captureSec` | `2` | `capture-pane` のポーリング間隔 |
| `routines.sessionPrefix` | `kiro` | 監視対象の tmux セッション名の接頭辞 |
| `cowork.refreshSec` | `10` | 一覧のポーリング間隔 |
| `cowork.loopCommand` | `agent-loop` | 定期実行の実行コマンド |
| `cowork.stateMachineCommand` | `statemachine-use` | 定型業務の実行コマンド |
| `cowork.runWindow` | `true` | 実行を tmux ウィンドウで見せる |
| `cowork.chatCommand` | `''` | 明示上書き。空なら CLI 定義の対話モードから argv を組む |
| `cowork.items` / `cowork.roots` | `[]` | 定型業務の項目と、定常業務専用フォルダの登録簿 |

定型業務の必須入力（`stateMachineInputSpec`）。`action` / `condition` / `on_enter` / `on_exit`（`action_file` / `condition_file` と自動探索ファイルを含む）と `condition_rule` から参照される変数のうち、人しか渡せないものだけを入力として要求します。実行器が自分で作る次の変数は、`workflow.yaml` の `context:` に値が無くても要求しません（分類の正典は statemachine-use の `references/schema.md`「Context Variable Reference」）。

| 分類 | 変数 | 供給元 |
|---|---|---|
| 組み込み（実行開始時） | `today` / `now` / `history` / `step_count` / `last_output` / `current_state` / `context` | 実行器が生成（`today` / `now` は `context:` で上書き可） |
| ステート実行中 | `check_status` / `check_ok` / `check_output` | 決定的検査（`check`）の実測結果 |
| 履歴 | `history.<state_id>` | 通過したステートの出力 |
| ステート出力 | `output_key` で宣言した名前 | そのステートの出力 |

残るのは `input`（`--input` で人が渡す）と、`context:` に宣言が無いか値が空の自由変数だけです。`context:` に値がある変数は既定値として提示し、必須にはしません。
| `amigos.refreshSec` | `15` | ポーリング間隔 |
| `amigos.busDirs` / `homeDirs` | `[]` | バスとオーナーホームの明示指定（空なら自動発見） |
| `amigos.budgetDir` | `''` | ノード予算の置き場 |
| `amigos.scanDepth` | `2` | 自動発見の探索深さ |
| `orchestration.refreshSec` | `15` | ポーリング間隔 |
| `orchestration.budgetDir` / `controlDir` / `instructionsDir` / `sessionDir` / `tuningDir` / `methodsDir` / `qualificationsFile` | `''` | 各契約ファイルの置き場（空なら実行エンジンと同じホーム解決） |
| `delegation.refreshSec` | `15` | ポーリング間隔 |
| `delegation.flowBusDirs` / `boardRepos` | `[]` | flow のバスと、委譲板の作業フォルダ |
| `delegation.nodeCommandsDir` | `''` | この端末への指示の受け渡し先。空なら実行エンジンと同じ場所 |
| `adhocFlow.busDir` / `agentFlowCommand` / `distro` | `''` | 単発 run のバスと実行経路 |
| `adhocFlow.workflowDir` | `''` | 空なら `<agents home>/workflows/` |
| `adhocFlow.tuningRoot` | `''` | 手法カタログの探索起点 |
| `adhocFlow.retentionDays` | `30` | run 履歴の保持日数 |
| `adhocFlow.cwdHistory` / `presets` | `[]` | 起動先の履歴と投入プリセット |
| `agentAudit.command` / `distro` / `configPath` / `auditDir` | `''` | agent-audit CLI の呼び出し先 |
| `agentAudit.collectIntervalMin` | `5` | 収集の最短間隔 |
| （participation） | — | 設定を持たない（`configDefaults` は `{}`） |

ポーリング間隔のまとめ: agent-project 5 秒、cowork 10 秒、amigos / delegation /
orchestration 15 秒、routines の生画面 2 秒、agent-audit の収集 5 分。ダイアログを開いている間と
入力中は更新を止めます（書きかけの入力を消さないため）。

---

### 5. 読むファイルと書くファイル

#### 5.1 読む（すべて読み取り専用・本体の稼働を前提にしない）

| 見るもの | 読むファイル |
|---|---|
| プロジェクトの存在・稼働・共有の健全性・板の可否 | `<agents home>/engine/status.json`（常駐体だけが書く唯一の根拠） |
| charter / バックログ / 要対応 | `charter.md`・`backlog/<id>.md`・`archive/<id>.md`・`needs/<id>.md`・`policy.md` |
| 実行（agent-flow） | `<bus>/runs/<run-id>/` の `graph.json` ＋ `results/` ＋ `claims/` ＋ `waits/` からノード状態を導出し、`events/*.jsonl` から計画変更の理由と差分を読む。ポーリングごとに `flow-archive/<run-id>.json` へ写し取り、bus から消えた run も追える |
| 履歴 | `run-log.jsonl`・`decisions/<id>.md`・`DELIVERY.md`・`journal.md` |
| ミッション | agent-amigos のバスとオーナーホームの納品棚 |
| 定期実行 | `~/.agents/loop-state/*.json` と tmux の `capture-pane`。設定は `.agents/agent-loop.{yaml,yml,json}` |
| ノード予算・エージェント制御 | `~/.agents/budget/`・`~/.agents/control/`・`~/.agents/session/`・`~/.agents/instructions/` |
| フロー定義・手法 | `~/.agents/workflows/`（ユーザー共通）・`.agents/workflows/`・`.agents/methods/`（リポジトリ共有・読み取り専用）・同梱版 |
| レビュー待ち（任意） | GitLab API を設定したときだけ、`repos.json` のリポジトリのオープンイシュー |

#### 5.2 書く（公式契約への投函だけ）

| 書き先 | 誰が | 中身 |
|---|---|---|
| `needs/<id>.md` | agent-project | 人の回答の記入 |
| `inbox/` | agent-project / adhoc-flow | タスク投入 |
| `commands/` | agent-project / amigos | approve / reject / pause / stop / reset / cancel / `force-complete` 等 |
| 上位入力ファイル | agent-project | `charter.md` / `policy.md` / `repos.json` の編集（ホワイトリスト経路） |
| `~/.agents/commands/`（ノード宛て指示） | delegation | 板への中止・落札・手動入札（[`agent-node-command`](../../schemas/agent-node-command.schema.json)） |
| `~/.agents/` 配下の契約ファイル | orchestration | ノード予算・agent-control・セッション開始コマンド |
| `~/.agents/workflows/*.json` | adhoc-flow | ユーザー共通フローの作成・編集・削除（削除は `.trash/` へ移動） |
| dashboard ローカル | preparation | 作業準備項目・設計セッション |
| viewer 管理のサイドカー | agent-project | `assignments.json`（監視担当）・`reviews/<task-id>/*.json`（レビューコメント） |
| 人の成果物リポジトリ | cowork | ブランチを切って push（状態リポジトリには触らない） |

書かないもの: `backlog/*.md` の status、`archive/`、`project.json`、agent-flow の run 状態、
リポジトリ共有版と同梱版のフロー定義・手法、フォージ（GitLab / GitHub）。

#### 5.3 プロセスを起動する唯一の経路

`participation:flowJoin` だけです。人が明示的に押したときに agent-flow のワーカーを 1 つ立てます。
常駐体（`agent-project serve`）の起動・再起動の経路は持ちません（OS の起動系の担当）。

---

### 6. 設計 run と実装 run

フロー定義はドメイン属性を持ち、保存場所だけで用途を推測しません。

| 属性 | 許される値 | 意味 |
|---|---|---|
| `purpose` | `implementation` / `design` | 実装 run 用か、設計書を返す設計 run 用か。旧定義の既定は `implementation` |
| `libraryVisibility` | `library` / `internal` | 通常の保存済みライブラリへ表示するか。旧定義の既定は `library` |
| `scope`（参照時） | `repository` / `user` / `builtin` | 出所。`repository` は登録済み cwd にだけ許可 |

参照キーは `id + scope + repository` です。`id` だけで別の定義を選びません。設計カタログの候補は
対象 cwd の登録済みリポジトリ共有 → ユーザー共通 → 同梱の順に読み、同じ id の別 scope を省略しません。
同梱の `design-interactive` / `design-auto` は `design` / `internal` です。

選択時に main が scope 付き参照を再解決し、正規化した定義（entry / exit / nodes）と
`origin.scope` / `origin.repository` / `digest` を snapshot 化します。renderer が送る定義本文や
plan は信頼せず、登録済み repository と `purpose` の一致を検証します。保存済みの作業準備項目・
設計 run・実装 handoff はこの snapshot を使うので、元定義の後変更で暗黙に変わりません。

設計 run は既存の `adhoc.submit` を使いますが、実装 run とは別の短命 run です。`workspace` を持たず、
対象リポジトリのファイル変更・commit・push・ブランチ作成をしません（したがって `af/<run-id>`
branch を作らず、agent-flow の run / plan / workspace 契約も変更しません）。human / split を使わず、
終了ノードは 1 つの `work` または `synthesize` に限ります。

実装へ handoff できる成果は次の必須 4 節を持ちます。

```markdown
## 目的
## 変更対象
## 受入基準
## 検証方法
```

未決事項は任意の `## 質問` 節に番号付きで残します。設計書は `final.summary` ではなく sink ノードの
出力から取得します。不足した成果は実装準備完了にせず、直前の成果・回答・材料を保持して再試行します。

設計と実装の間には dashboard ローカルの作業準備項目が入ります。経路は `agent-design` /
`external-design` / `direct` の 3 つで、設計結果は `kind: design-result` の `設計結果.md` として
材料へ追加します。必須 4 節を満たした `implementation-ready` 項目だけが `adhoc.submit` または
project の `inbox/` へ handoff されます。親の準備パッケージから子へは、`agent-design` 子だけが
設計フロー snapshot を継承します。

旧形式で `designMode: auto` だけを持つ項目は一括移行しません。設計開始時に `design-auto` の
`scope: builtin` snapshot を遅延補完します。

---

### 7. 正典の写しと、それを縛るゴールデンテスト

判断の根拠を 1 か所へ集める原則に、例外が 4 つあります。いずれも「候補を出すたびに Python を
起動すると描画がプロセス起動待ちになる」という理由で JS 側に写しを置いており、その代償として
同じ入力から違う答えが出ても誰も気付かないという壊れ方を抱えます。写しは 1 本化せず
（判定の入力源が別プロセス・別言語なので）、テストが正典を実際に読んで突き合わせます。

| 写し | 正典 | 縛るテスト |
|---|---|---|
| CLI 定義のローダ | `agents/<name>.json` | `test/agent-cli-golden.test.js` |
| ノード契約バージョンの期待値 | Python 側の定数 | `test/contract-version-golden.test.js` |
| git URL の正規化（symlink 解決まで） | Python 側の実装 | `test/repo-url-golden.test.js` |
| フォージの決着推定に使う手掛かり語とラベル | `tools/agent-flow/executors/gitlab.py` | `test/gitlab-decision-golden.test.js` |

---

### 8. 構造を固定しているテスト

設計判断の実体はテストです。以下は「レビューではなくテストで縛る」と決めた箇所です。

| テスト | 固定していること |
|---|---|
| `test/no-git-writes.test.js` | 状態を扱う層が `pull` / `push` / `commit` / `rebase` / `merge` / `worktree` / `checkout` / `branch` / `add` / `stash` を起動しない。検査範囲そのものも検査する（新しい制御面は自動で護りの下に入り、外すには `EXCLUDED` を触るしかない）。除外は `features/cowork` の 1 つだけ |
| `test/feature-split.test.js` | `loadFeatures()` の並びと、各制御面が記述子の 3 点を満たすこと |
| `test/discover-engine.test.js` | プロジェクト発見の入口が `engine/status.json` 1 枚であること |
| `test/portal-home.test.js` | ポータル登録簿と、横断要対応キューが `discover()` の `needsCount` だけで組まれること |
| `test/needs-notify.test.js` / `test/needs-sla.test.js` | 通知の増分検知と停滞の可視化 |
| `test/packaging-assets.test.js` | `index.html` の参照と `build.files` の同梱指定の対応 |
| `test/adhoc-flow.test.js` / `test/preparation.test.js` | 用途分類・snapshot・handoff・設計 run の読み取り専用契約 |
| `test/settlement-ui.test.js` | 検証の決着の 4 択 |
| `test/state-machine-window.test.js` | 定型業務の Windows → WSL 起動と tmux の扱い |
| `test/resource-control.test.js` | ヘッドレス資源制御（`npm run resources`） |

---

### 9. 配布

`index.html` はバンドラを使わないので CSS / JS を相対パスで直接読み、一部は
`node_modules/diff2html/…` を指します。開発起動では node_modules がそこに在るため気づけず、
`build.files` から漏れているとパッケージ版だけ差分ビューが白紙になります。

| 項目 | 値 |
|---|---|
| ターゲット | Windows の `portable` と `nsis` |
| `build.files` | `src/**/*`・`package.json`・`node_modules/diff2html/bundles/**`・`node_modules/yaml/**` |
| `extraResources` | リポジトリ直下の `agents/` `methods/` `workflows/`（`**/*.json` のみ） |
| プロトコル | `agent-dashboard` |

electron-builder が本番依存を暗黙に含めるかどうかに配布物を賭けず、本番依存はすべて
`build.files` へ明示します。

---

### 付録. テスト

テストと lint は次のコマンドで実行します。

```bash
cd tools/agent-dashboard
npm install
npm test
npm run lint
```

`npm test` は個々のファイルを直列に並べた 1 本のコマンドです。テストファイルを足したら
`package.json` の `scripts.test` へも足してください。ディレクトリを走査する仕組みではないので、
置いただけのファイルは CI を含めてどこでも実行されません。
