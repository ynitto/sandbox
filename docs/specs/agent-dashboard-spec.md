# agent-dashboard 仕様書

> 設計の「なぜ」は [`docs/designs/agent-dashboard-design.md`](../designs/agent-dashboard-design.md)、
> 画面ごとの使い方は [`tools/agent-dashboard/README.md`](../../tools/agent-dashboard/README.md)。
> 本書は**契約**（合成の約束・設定キー・読む/書くファイル・上限）を引く場所です。
> 対象: `tools/agent-dashboard/`（Electron。`src/` 135 ファイル・約 43,600 行、うち renderer が約 20,500 行）

---

## 1. 実行形態

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

### 1.1 ディレクトリ

```
src/
├── base/main/          Electron シェル・設定合成・git 読み取り・通知・共通 IPC（12 モジュール）
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
├── main/               旧パス互換シム（7 ファイル。実体は base/ と features/agent-project/ へ再エクスポート）
├── preload.js          base API ＋ 各 feature の preloadApi を合成
└── renderer/           画面（core → sections → features → bootstrap の順に読む）
```

**`preparation/` は制御面ではありません。** `src/features/` の下にありますが
`index.js`（feature 記述子）を持たず、`features/index.js` の列挙にも入りません。実体は
adhoc-flow・renderer・`base/main/design-contract.js` から呼ばれる共有モジュール 1 本
（`main/preparation.js`）です。ディレクトリの位置だけを見て制御面と数えないでください。
制御面は **9 つ**、`src/features/` のディレクトリは 10 個です。

---

## 2. 合成契約

### 2.1 feature 記述子

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

### 2.2 載せる順番

`src/features/index.js` の `loadFeatures()` が返す配列そのものが登録順です。

```
agent-project → routines → cowork → amigos → orchestration
→ delegation → participation → agent-audit → adhoc-flow
```

制御面を足す手順は 3 手だけです。`src/features/<id>/` を既存の制御面を雛形に作り、
この配列へ `require('./<id>')` を 1 行足し、必要なら renderer にタブを差し込みます。
動的ロード・サンドボックス・版管理は持ちません。

### 2.3 IPC

全チャネルが `{ ok: true, data }` または `{ ok: false, error }` に揃います
（`base/main/handle.js` が包む）。チャネル数は **161 本**です。

| 提供元 | チャネル数 | 主なチャネル |
|---|---|---|
| `base` | 9 | `config:get` / `config:save` / `git:diff` / `app:notify` / `shell:openExternal` / `shell:openPath` / `gitlab:enrich` / `gitlab:mrDiscussions` / `gitlab:projectIssues` |
| `agent-project` | 56 | プロジェクト発見・要対応・実行・オーサリング・検収 |
| `adhoc-flow` | 37 | 単発 run の投入と監視、フロービルダー、設計セッション |
| `orchestration` | 17 | ノード予算・agent-control・CLI ドロップイン・手法 |
| `cowork` | 11 | 定期実行と定型業務の一覧・実行 |
| `amigos` | 9 | ミッションの読み取り |
| `delegation` | 8 | 委譲封筒の読み取りとノード宛て指示の投函 |
| `agent-audit` | 7 | `collect` / `usage` / `stats` / `doctor` / `sessions` / `summary` / `knowledge` |
| `routines` | 6 | `listSessions` / `capture` / `state` / `send` / `queue` / `queueMessage` |
| `participation` | 1 | `participation:flowJoin`（唯一のプロセス起動経路） |

---

## 3. renderer の契約

ビルド工程を持ちません。`index.html`（906 行）の `<script>` 読み込み順がそのまま契約です。

| 順 | 対象 | 制約 |
|---|---|---|
| 1 | `renderer.js`（core、2,590 行） | `state` と共有ユーティリティ、3 つの登録簿を定義する |
| 2 | `sections/*.js`（15 本） | 関数宣言のみ。load 時実行を持たないので相互の順序は不問 |
| 3 | `features/*.js`（3 本） | 自分のタブ／カード／設定面を登録する |
| 4 | `bootstrap.js` | `init()` の定義と呼び出し。必ず最後 |

テストは `test/helpers/renderer-src.js` がこの順で結合して「元の全文」を復元し、
文字列走査で検査します。モジュール境界（`import` / `export`）を入れると検査が全部壊れます。

### 3.1 3 つの登録簿

core は差し込まれる中身を知りません。

| 登録簿 | 差し込み先 | フック |
|---|---|---|
| `registerFeatureTab(name, hooks)` | プロジェクトのタブ列 | `render()` / `refresh()` / `available()` / `badge()` |
| `registerPortalCard(id, hooks)` | ホーム（ポータル）画面 | `order`（既定 100）/ `html()` / `wire(container)` |
| `registerGlobalSettingsPanel(section, hooks)` | 全体設定の指定セクション | `id` / `html()` / `wire(c)` / `reveal(c)` / `refresh()` |

`registerPortalCard` の `html()` が `''` を返すとそのカードは出ません（未利用の制御面を隠す判断は
各制御面が持つ）。並び順は `order` → `id` の辞書順です。

`registerGlobalSettingsPanel` の面は自分の容れ物 `global-settings-slot-<id>` だけを描き直します
——全体設定ごと描き直すと、他の節で入力中の欄が飛びます。`reveal` は節が開いたときに呼ばれ、
重い取得（CLI 起動）はそこで初めて走らせます。

### 3.2 領域とタブ

第一ナビは**領域（ワークロード）**で、タブはその領域の内部ナビです。どのタブがどの領域かは
HTML の `data-area` 属性が正で、`AREAS` は列挙と表示名だけを持ちます。領域の絞り込み
（`area-off`）と制御面の出し分け（`.hidden`）は**別のしるし**です——片方を使い回すと、
領域を切り替えただけで制御面の判断が消えます。

---

## 4. 設定

`config.json` に保存し、欠けたキーは既定で補完します（バージョンアップで項目が増えても
既存の設定ファイルはそのまま使えます）。既定は base の `BASE_DEFAULT_CONFIG` と
各制御面の `configDefaults` を `deepMerge` した合成です。

### 4.1 base

| キー | 既定 | 意味 |
|---|---|---|
| `role` | `engineer` | `engineer` は全機能。`viewer` は閲覧・レビュー専用（WSL / CLI 設定不要） |
| `gitlab.baseUrl` | `https://gitlab.com` | 任意。イシューの最新状態を API で補完するときだけ使う |
| `gitlab.token` | `''` | 空なら API 補完を行わず、bus 上の結果ファイルの情報だけで表示する |
| `reviewViewer.mode` | `protocol` | `protocol` / `exe` / `command`。gitlab-review-viewer への引き継ぎ方法 |
| `reviewViewer.protocol` | `gitlab-review-viewer://open` | `protocol` モードのスキーム |
| `reviewViewer.exePath` | `''` | `exe` モード。portable exe は URL スキームを OS 登録できないためこちらを使う |
| `reviewViewer.command` | `''` | `command` モード。`{url}` `{projectPath}` `{type}` `{iid}` `{protocolUrl}` を置換 |

### 4.2 agent-project

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

### 4.3 その他の制御面

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
| `amigos.refreshSec` | `15` | ポーリング間隔 |
| `amigos.busDirs` / `homeDirs` | `[]` | バスとオーナーホームの明示指定（空なら自動発見） |
| `amigos.budgetDir` | `''` | ノード予算の置き場 |
| `amigos.scanDepth` | `2` | 自動発見の探索深さ |
| `orchestration.refreshSec` | `15` | ポーリング間隔 |
| `orchestration.budgetDir` / `controlDir` / `instructionsDir` / `sessionDir` / `tuningDir` / `methodsDir` / `qualificationsFile` | `''` | 各契約ファイルの置き場（空なら実行エンジンと同じホーム解決） |
| `delegation.refreshSec` | `15` | ポーリング間隔 |
| `delegation.flowBusDirs` / `boardRepos` | `[]` | flow のバスと、委譲板の**作業フォルダ** |
| `delegation.nodeCommandsDir` | `''` | この端末への指示の受け渡し先。空なら実行エンジンと同じ場所 |
| `adhocFlow.busDir` / `agentFlowCommand` / `distro` | `''` | 単発 run のバスと実行経路 |
| `adhocFlow.workflowDir` | `''` | 空なら `<agents home>/workflows/` |
| `adhocFlow.tuningRoot` | `''` | 手法カタログの探索起点 |
| `adhocFlow.retentionDays` | `30` | run 履歴の保持日数 |
| `adhocFlow.cwdHistory` / `presets` | `[]` | 起動先の履歴と投入プリセット |
| `agentAudit.command` / `distro` / `configPath` / `auditDir` | `''` | agent-audit CLI の呼び出し先 |
| `agentAudit.collectIntervalMin` | `5` | 収集の最短間隔 |
| （participation） | — | 設定を持たない（`configDefaults` は `{}`） |

**ポーリング間隔のまとめ**: agent-project 5 秒、cowork 10 秒、amigos / delegation /
orchestration 15 秒、routines の生画面 2 秒、agent-audit の収集 5 分。ダイアログを開いている間と
入力中は更新を止めます（書きかけの入力を消さないため）。

---

## 5. 読むファイルと書くファイル

### 5.1 読む（すべて読み取り専用・本体の稼働を前提にしない）

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

### 5.2 書く（公式契約への投函だけ）

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

**書かないもの**: `backlog/*.md` の status、`archive/`、`project.json`、agent-flow の run 状態、
リポジトリ共有版と同梱版のフロー定義・手法、フォージ（GitLab / GitHub）。

### 5.3 プロセスを起動する唯一の経路

`participation:flowJoin` だけです。人が明示的に押したときに agent-flow のワーカーを 1 つ立てます。
常駐体（`agent-project serve`）の起動・再起動の経路は持ちません（OS の起動系の担当）。

---

## 6. 設計 run と実装 run

フロー定義はドメイン属性を持ち、保存場所だけで用途を推測しません。

| 属性 | 許される値 | 意味 |
|---|---|---|
| `purpose` | `implementation` / `design` | 実装 run 用か、設計書を返す設計 run 用か。旧定義の既定は `implementation` |
| `libraryVisibility` | `library` / `internal` | 通常の保存済みライブラリへ表示するか。旧定義の既定は `library` |
| `scope`（参照時） | `repository` / `user` / `builtin` | 出所。`repository` は登録済み cwd にだけ許可 |

参照キーは **`id + scope + repository`** です。`id` だけで別の定義を選びません。設計カタログの候補は
対象 cwd の登録済みリポジトリ共有 → ユーザー共通 → 同梱の順に読み、同じ id の別 scope を省略しません。
同梱の `design-interactive` / `design-auto` は `design` / `internal` です。

選択時に main が scope 付き参照を再解決し、正規化した定義（entry / exit / nodes）と
`origin.scope` / `origin.repository` / `digest` を **snapshot** 化します。renderer が送る定義本文や
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

設計と実装の間には dashboard ローカルの**作業準備項目**が入ります。経路は `agent-design` /
`external-design` / `direct` の 3 つで、設計結果は `kind: design-result` の `設計結果.md` として
材料へ追加します。必須 4 節を満たした `implementation-ready` 項目だけが `adhoc.submit` または
project の `inbox/` へ handoff されます。親の準備パッケージから子へは、`agent-design` 子だけが
設計フロー snapshot を継承します。

旧形式で `designMode: auto` だけを持つ項目は一括移行しません。設計開始時に `design-auto` の
`scope: builtin` snapshot を遅延補完します。

---

## 7. 正典の写しと、それを縛るゴールデンテスト

判断の根拠を 1 か所へ集める原則に、**例外が 4 つ**あります。いずれも「候補を出すたびに Python を
起動すると描画がプロセス起動待ちになる」という理由で JS 側に写しを置いており、その代償として
**同じ入力から違う答えが出ても誰も気付かない**という壊れ方を抱えます。写しは 1 本化せず
（判定の入力源が別プロセス・別言語なので）、テストが正典を実際に読んで突き合わせます。

| 写し | 正典 | 縛るテスト |
|---|---|---|
| CLI 定義のローダ | `agents/<name>.json` | `test/agent-cli-golden.test.js` |
| ノード契約バージョンの期待値 | Python 側の定数 | `test/contract-version-golden.test.js` |
| git URL の正規化（symlink 解決まで） | Python 側の実装 | `test/repo-url-golden.test.js` |
| フォージの決着推定に使う手掛かり語とラベル | `tools/agent-flow/executors/gitlab.py` | `test/gitlab-decision-golden.test.js` |

---

## 8. 構造を固定しているテスト

設計判断の実体はテストです。以下は「レビューではなくテストで縛る」と決めた箇所です。

| テスト | 固定していること |
|---|---|
| `test/no-git-writes.test.js` | 状態を扱う層が `pull` / `push` / `commit` / `rebase` / `merge` / `worktree` / `checkout` / `branch` / `add` / `stash` を起動しない。**検査範囲そのものも検査する**（新しい制御面は自動で護りの下に入り、外すには `EXCLUDED` を触るしかない）。除外は `features/cowork` の 1 つだけ |
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

## 9. 配布

`index.html` はバンドラを使わないので CSS / JS を相対パスで直接読み、一部は
`node_modules/diff2html/…` を指します。開発起動では node_modules がそこに在るため気づけず、
`build.files` から漏れていると**パッケージ版だけ差分ビューが白紙**になります。

| 項目 | 値 |
|---|---|
| ターゲット | Windows の `portable` と `nsis` |
| `build.files` | `src/**/*`・`package.json`・`node_modules/diff2html/bundles/**`・`node_modules/yaml/**` |
| `extraResources` | リポジトリ直下の `agents/` `methods/` `workflows/`（`**/*.json` のみ） |
| プロトコル | `agent-dashboard` |

electron-builder が本番依存を暗黙に含めるかどうかに配布物を賭けず、本番依存はすべて
`build.files` へ明示します。

---

## 付録. テスト

`tools/agent-dashboard/test/` に **98 ファイル**。実行は次のとおりです。

```bash
cd tools/agent-dashboard
npm install      # 本番依存 2 つ + Electron / eslint
npm test         # 98 ファイルすべて（pretest が 2 本を先に走らせる）
npm run lint
```

`npm test` は個々のファイルを直列に並べた 1 本のコマンドです。**テストファイルを足したら
`package.json` の `scripts.test` へも足してください**——ディレクトリを走査する仕組みではないので、
置いただけのファイルは CI を含めてどこでも実行されません。
