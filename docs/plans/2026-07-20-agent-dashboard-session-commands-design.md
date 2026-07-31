# Agent Dashboard: セッション開始コマンド（agent-session-commands）設計

> 日付: 2026-07-20
> 対象: `tools/agent-dashboard/` `tools/kiro-loop/` `tools/agent-loop/` `tools/agent-flow/` `schemas/`
> 関連: [`2026-07-19-agent-dashboard-global-instructions-design.md`](2026-07-19-agent-dashboard-global-instructions-design.md)（agent-instructions 契約・本設計の直接の先例）・
> [`2026-07-19-agent-dashboard-orchestration-token-budget-design.md`](2026-07-19-agent-dashboard-orchestration-token-budget-design.md)（agent-control 契約と status ハートビート）・
> [`../designs/agent-cli-plugin-design.md`](../designs/agent-cli-plugin-design.md)（drop-in 契約）

## 背景と目的

エージェントのセッションが始まった直後に、決まった前準備を毎回走らせたい。具体的には次の
2 種類の要求がある。

1. **ホスト側の環境準備** — `git fetch --prune` / `docker compose up -d` / venv 有効化 /
   認証トークンの更新。これらは AI CLI が動き出す前に完了していなければ意味がない。
2. **セッションへの初手投入** — kiro-cli のスラッシュコマンド、特定スキルの暖機、
   「作業前に `docs/` を読め」のような最初の 1 手。CLI の内部コマンドは、
   プロセス起動引数からは撃てず、セッションへ送るしかない。

現状この層は存在しない。近いものはあるが、いずれも目的が違う。

| 既存機構 | 範囲 | 実行できるもの | セッション開始で 1 回 |
|---|---|---|---|
| `instructions.json`（agent-instructions） | ノード横断 | **テキストのみ**（プロンプトへ前置） | ×（revision 差分で毎送信を判定） |
| `agents/<name>.json`（agent-cli drop-in） | CLI 1 種 | argv / env の宣言 | ×（毎呼び出し。組み込み 4 種には書けない） |
| kiro-cli `--agent` の `hooks.stop` | kiro 系のみ | 停止時のコマンド | ×（開始側は未使用） |
| `install.sh` / `.bashrc` | 端末全体 | 任意 | ×（dashboard から変更できない） |

本設計は、この空白を **`agent-session-commands`** という新しいデータ契約で埋める。
本リポジトリの原則（**結合はデータ契約のみ・pull 型・原子書換・エンジンは単純、知能は管理面**）を
維持し、push 型 IPC やエンジン間のコード依存は導入しない。

## 前提となる調査結果

### エンジンは起動方式で二分される

| 系統 | 実体 | 「セッション開始」に相当する点 | 該当エンジン |
|---|---|---|---|
| **常駐系** | tmux ペイン = 長寿命チャット | `_start_pane()`（`kiro-loop.py:1602` / `agent_loop/session.py:211`）。プロンプト ID ごとに 1 ペイン | kiro-loop / agent-loop / dashboard cowork |
| **単発系** | 1 LLM 呼び出し = 1 プロセス | セッション概念なし。`subprocess.run`（`agent_flow/agent.py:711`）は使い捨て | agent-flow / agent-project / agent-amigos |

「セッション開始直後に 1 回」が素直に成立するのは**常駐系だけ**である。単発系で同じ位置
（CLI spawn の直前）に置くと、ノード実行のたびにコマンドが走り、run あたり N 回の
オーバーヘッドになる。単発系はワーカープロセスの起動時へ寄せる。

### 常駐系の起動点

`_start_pane()` は tmux ペインを作って `kiro-cli chat` を起動し、以降は
`_send_to_pane()`（`kiro-loop.py:1360`）が `set-buffer` → `paste-buffer` → `Enter` で
プロンプトを投げ込む。ペインは `_START_WAIT_TIMEOUT = 60.0`（`kiro-loop.py:718`）まで
プロンプト待ちを行うため、**ペイン生成と「CLI が入力を受け付ける状態」は別の瞬間**である。
chat モードのコマンドはこの待機を通過してから送る必要がある。

dashboard の cowork も同じ形を持つ。`loopProvider.js:244` の `chatWindowScript()` が
`tmux has-session` で既存を判定し、無ければ `new-session` する。セッション名は
cwd の SHA1 先頭 8 桁（`kiro-dash-<digest>`）でリポジトリごとに安定する。

### agent-flow のワーカー

`run.py:317` `_spawn_worker()` が `agent-flow ... work --node-id ... --idle-exit` を Popen し、
`AGENT_FLOW_EXECUTOR_CONFIG` などを環境変数で子へ渡す。opt-out フラグの前例として
`AGENT_FLOW_NO_GLOBAL_INSTRUCTIONS`（`run.py:329`）がある。

## 検討した案

### 案 A: agent-instructions に `session_commands` を足す

置き場所・revision・status ハートビート・編集 UI がすべて揃っており、実装は最小で済む。
しかし採らない。理由は 2 つある。

1. agent-instructions の契約は「**プロンプトへ前置する有界テキスト**」と定義して出荷済みで、
   副作用・終了コード・タイムアウト・失敗時の続行判断を持ち込むと意味論が濁る。
   これは control.json への相乗りを却下したときと同じ判断軸である。
2. 決定的な問題として、instructions は agent-flow の `meta.json` スナップショット
   （`bus.py:64` `snapshot_instructions()`）で**他ノードへ伝播する**。同じ器に入れると、
   任意のシェルコマンドが GitBus 経由でリモートノードへ配られる。受け入れられない。

### 案 B: `agents/<name>.json`（drop-in）に `pre_command` を足す

CLI 定義の単位なので「この端末の全エージェント共通」にならない。加えて組み込み 4 種
（kiro / claude / copilot / codex）は上書き・削除が禁止されている（`agents.js:141`）ため、
既定の CLI を使っている限り書き込む場所が無い。**不採用**。

### 案 C: kiro-cli `--agent` JSON の `hooks` を使う

`~/.kiro/agents/agent-loop-concurrency.json` の `hooks.stop` は
`agent-loop slot-release` として実用されており（`install.sh:245`）、機構としては存在する。
しかし kiro 系限定で、他の CLI（claude / copilot / codex）に等価物が無い。さらに
install.sh が所有するファイルをペイン起動ごとに書き換える副作用と、反映点がペイン再起動に
固定される制約が付く。**v1 では不採用**。開始側フックの有無を確認したうえで、
native 反映として将来検討する（agent-instructions の `tools` 反映を見送ったのと同じ理由）。

### 案 D: 新契約 agent-session-commands（採用）

`$AGENT_SESSION_DIR`（既定 `~/.agents/session/`）の `session.json` を正典とする独立契約。
budget（`~/.agents/budget/`）・control（`~/.agents/control/`）・instructions
（`~/.agents/instructions/`）・drop-in（`~/.agents/agents/`）と同じ置き場所の流儀・
同じ pull 型・同じ原子書換・同じ revision 単調増加。

**伝播しない点だけが instructions と非対称**である。各ノードは自分のローカル
`session.json` だけを読み、`meta.json` にもバスにも載せない。任意コマンド実行の到達範囲を、
その端末に置いた設定ファイルの範囲へ閉じ込める。

## データ契約

正典: `schemas/agent-session-commands.schema.json`（新設。stdlib の json だけで読める）。
置き場所: `$AGENT_SESSION_DIR`（既定 `~/.agents/session/`）の `session.json`。
書き手は管理面（agent-dashboard / CLI / 人）のみ・原子書換。読み手は各エンジン。

```json
{
  "version": 1,
  "revision": 3,
  "enabled": true,
  "commands": [
    {
      "id": "sync-repo",
      "mode": "process",
      "run": "git -C {cwd} fetch --prune",
      "cwd": "{cwd}",
      "env": {},
      "timeout": 30,
      "on_error": "warn",
      "when": { "engines": ["kiro-loop", "agent-loop"], "workloads": ["routine"] }
    },
    {
      "id": "prime-context",
      "mode": "chat",
      "run": "作業を始める前に docs/ 配下を読み込んで、プロジェクト固有のルールを把握して",
      "when": { "engines": ["kiro-loop"] }
    }
  ],
  "max_total_timeout": 120,
  "updated_at": "2026-07-20T12:00:00Z",
  "updated_by": "dashboard"
}
```

| キー | 意味 |
|---|---|
| `revision` | 単調増加。適用状況の突き合わせに使う（agent-control / agent-instructions と同じ流儀） |
| `enabled` | false なら全エンジンで完全 no-op（削除せず一時停止できる） |
| `commands[]` | 実行するコマンドの順序付きリスト。**配列順に逐次実行**する（並列にしない） |
| `max_total_timeout` | 全コマンド合計の上限秒（既定 120・ハード上限 600）。超過分は打ち切って警告 |

`commands[]` の 1 要素:

| キー | 型 | 意味 |
|---|---|---|
| `id` | string（必須） | 一意な識別子。ログ・実行済み記録・UI の行キー |
| `mode` | `process` \| `chat`（既定 `process`） | `process` = ホストでシェル実行し完了を待つ / `chat` = セッションへ最初のプロンプトとして送信 |
| `run` | string（必須） | `process` ならシェルコマンド文字列、`chat` なら送信するテキスト |
| `cwd` | string \| null | 実行ディレクトリ（`process` のみ）。省略時はセッションの cwd |
| `env` | object | 追加の環境変数（`process` のみ。`os.environ` に上書きマージ） |
| `timeout` | number \| null | このコマンド単体の上限秒（既定 60。`process` のみ） |
| `on_error` | `warn` \| `fail`（既定 `warn`） | `warn` = 失敗しても続行 / `fail` = セッション開始を中止 |
| `when` | object \| null | 適用条件。省略時は全適用 |

`when` の絞り込み条件（すべて省略可・AND 結合）:

| キー | 値の例 | 突き合わせ先 |
|---|---|---|
| `engines` | `["kiro-loop", "agent-loop", "agent-flow", "dashboard"]` | 実行中のエンジン名 |
| `workloads` | `["routine", "flow", "project", "amigos"]` | agent-control のワークロード名（既存語彙を流用） |
| `agent_cli` | `["kiro", "claude"]` | 解決済みの CLI 名 |

### プレースホルダ

`run` / `cwd` / `env` の値で使える。未定義のものは**空文字**へ落とす（エラーにしない）。

| プレースホルダ | 意味 |
|---|---|
| `{cwd}` | セッションの作業ディレクトリ |
| `{workspace}` | ワークスペース（リポジトリ）ルート |
| `{engine}` | `kiro-loop` / `agent-loop` / `agent-flow` / `dashboard` |
| `{workload}` | `routine` / `flow` / `project` / `amigos` |
| `{agent_cli}` | 解決済み CLI 名 |
| `{model}` | 解決済みモデル名 |
| `{run_id}` / `{node_id}` | agent-flow のみ（他エンジンでは空） |

置換は決定的な単純文字列置換（LLM 不使用）。**シェルクォートは行わない** —
`run` はシェルへ渡す文字列としてユーザーが書く前提で、プレースホルダに空白を含む
パスが入りうる場合は利用者が引用符で囲む（`git -C "{cwd}" fetch` のように）。
この挙動は UI のプレビューと注記で明示する。

未知キーは無害に無視（additive 進化）。ファイル不在・parse 失敗・`enabled: false` は
すべて「コマンドなし」と同義で、**エンジンの動作を止めない**（警告ログのみ）。

### 実行の意味論

- **逐次**: 配列順に 1 つずつ。並列実行はしない（前準備は順序に意味があることが多い）
- **べき等**: 1 セッション（ペイン / ワーカープロセス）につき 1 回だけ。実行済みは
  セッション識別子ごとに記録する
- **有界**: コマンド単体の `timeout` と `max_total_timeout` の二段で必ず打ち切る
- **フェイルセーフ**: 既定 `on_error: warn`。`fail` を明示したときだけセッション開始を中止する
- **出力**: `process` の stdout / stderr はエンジンのログへ流す。プロンプトへは混ぜない

### 適用状況ハートビート

新しい status ファイル系統は作らない。エンジンは既存の agent-control
`status/<tool>-<pid>.json`（`additionalProperties: true`）へ
**`session_commands_revision_applied`（整数）** を additive に追記する。
dashboard はこれを読み、「rev 3 を配ったが kiro-loop はまだ rev 2」を可視化する。

agent-loop には status ハートビートを書く仕組みがそもそも無かった（kiro-loop の
`_write_status` に相当するものが未クローンで、agent-control 対応自体が欠けていた）。
本設計の実装にあわせて `agent_loop/control.py` へクローンし、lifecycle の適用と
ノード予算も揃えた。これで agent-loop の定常業務も dashboard の反映状況に現れる。

## 実行点（エンジン別）

### kiro-loop / agent-loop（本命）

`_start_pane()`（`kiro-loop.py:1602` / `agent_loop/session.py:211`）を境に 2 段で入れる。

1. **`process` モード** — ペイン生成の**前**にホストで逐次実行する。ここで
   `on_error: fail` のコマンドが失敗したら `_start_pane` を中止し、ペインを作らない
   （準備できていない環境でエージェントを走らせない）。
2. **`chat` モード** — ペイン生成後、既存の起動待ち（`_START_WAIT_TIMEOUT = 60.0`）を
   通過してから、業務プロンプトより**先に** `_send_to_pane()` で送る。
   `_maybe_prepend_instructions` の共通指示ブロックはこの後の業務プロンプトに付くため、
   順序は「chat コマンド → 共通指示 + 業務プロンプト」になる。

実行済み記録は `self._instr_rev`（`session.py:310`）と同型で、`self._session_cmd_rev` に
`prompt_id -> revision` を持つ。ペインが落ちて再起動されれば再実行される（新しい
セッションなので正しい）。revision を上げても**既存ペインには遡及しない** —
反映点はペイン再起動で、これは agent-instructions の skills / tools と同じ既知の制約。
UI にこの旨を注記する。

### agent-dashboard cowork

`loopProvider.js` `chatWindowScript()` で、`tmux has-session` が失敗した
（= `new-session` する）分岐にだけ挿入する。既存セッションへの paste では走らない。
dashboard は書き手であると同時に、自分が起動する CLI に対する読み手でもある
（`withGlobalInstructions` と同じ立ち位置）。

ここだけは Electron main で実行せず、**起動スクリプトの中へシェル片として差し込む**。
cowork の cwd は WSL 側にあり、Windows 側の Node から `git -C` を撃っても対象が違う。
`cowork.js` の `planSessionCommands()` が `toWslCwd()` で Linux パスへ揃えた文脈で計画を作り、
`sessionProcessLines()` / `sessionChatLines()` がそれをシェル片へ落とす。
`process` は `timeout <n> sh -c '<run>'`、`on_error: fail` は `exit 1` でスクリプトを抜けるため
tmux セッション自体が作られない。

### agent-flow

`run.py:317` `_spawn_worker()` と `run.py:286` `_spawn_orchestrator()` の直後、
ワーカープロセスの初期化で 1 回。**ノードごとの `subprocess.run`（`agent.py:711`）には
入れない** — run あたり N 回走って費用対効果が壊れる。

`chat` モードは単発系にセッションが無いため**適用しない**（`when` で除外されていなくても
スキップし、その旨をログに残す）。`process` モードのみ効く。

opt-out として `AGENT_FLOW_NO_SESSION_COMMANDS` 環境変数と
`agent-flow run --no-session-commands` を足す（`AGENT_FLOW_NO_GLOBAL_INSTRUCTIONS` と同型）。

### agent-project / agent-amigos

v1 では対象外。act（実作業）は agent-flow へ委譲されるため上記でカバーされ、
メタ LLM 呼び出し（prioritize / route / adjudicate / verify / doctor）へ環境準備を
走らせる必然性が無い。将来 `run --watch` のループ開始時（`state.py:476`）へ足す余地は
残しておく（`when.engines` に `agent-project` を書けば効くように語彙だけ先に確保する）。

## agent-dashboard 側の変更

orchestration feature（`src/features/orchestration/`）に追加する。budget / control /
instructions / drop-in と同じ管理面の一部として扱う。

- **main**: `sessionCommands.js` — `load()` / `save(patch)`。`instructions.js` とほぼ同型で、
  revision 自動インクリメント・スキーマ検証・tmp → rename の原子書換・`_raw` を土台に
  した未知キー保持（`instructions.js:149` と同じ）・`updated_by = 'dashboard'` の刻印。
  ディレクトリ解決は `resolveSessionDir(cfg)`（`cfg.orchestration.sessionDir` →
  `$AGENT_SESSION_DIR` → `agentHomeSubdir('session')` の順）。
  IPC: `orchestration:sessionCommandsSave` と `orchestration:sessionCommandsPreview`
  （現在値の取得は overview に相乗りするので Get は要らない。プレビューは編集中のフォームを
  そのまま渡して計画を組ませるため、保存前でも「保存したら何が起きるか」を見せられる）。
- **overview への合流**: `orchestration:overview` の戻りに `sessionCommands` を additive に
  足す。UI は既存どおり 1 本の IPC で描ける。
- **renderer**: 「全体設定 → エージェント → 共通設定」セクションの、共通指示カードの直下に
  `orchSessionCommandsPanelHtml` を追加する（`orchestration.js:637-641` の
  `orchInstructionsPanelHtml` の隣）。
  - `enabled` トグル / `max_total_timeout`
  - コマンド行の追加・削除・並べ替え（`id` / `mode` / `run` / `cwd` / `timeout` /
    `on_error` / `when` の 3 条件）
  - **プレビュー**: プレースホルダ展開後の実行順リストを、エンジン別（kiro-loop /
    agent-flow / dashboard）に切り替えて表示する。`when` で除外される行はグレーで残す
  - **適用状況**: `status/*.json` の `session_commands_revision_applied` を revision と
    突き合わせ、未反映のエンジンをハイライトし「常駐系はペイン再起動が反映点」を注記
  - **注意書き**: シェルクォートを行わないこと、`fail` を選ぶとセッションが立たなくなり
    うることを、入力欄の近くに 1 行で出す

## 不変条件

- **契約のみで結合**: エンジン間・dashboard 間にコード依存を増やさない。ローダは各ツールが
  stdlib（json / 文字列操作 / subprocess）だけで持つ。依存パッケージを増やさない
- **非伝播**: `meta.json` にもバスにも載せない。任意コマンドの到達範囲はその端末に閉じる
- **フェイルセーフ**: ファイル不在・破損・disabled・例外はすべて no-op。既定 `warn` で、
  コマンド失敗のせいでセッションが立たなくなることはない（`fail` を明示したときだけ）
- **有界**: コマンド単体 `timeout`（既定 60）と `max_total_timeout`（既定 120・上限 600）
- **べき等**: 1 セッションにつき 1 回。revision を上げても既存セッションには遡及しない
- **決定的**: プレースホルダ展開・`when` 判定・実行順は決定的（LLM 不使用）
- **メタ LLM 非対象**: planner / 裁定 / prioritize / doctor 等の内部呼び出しでは走らせない

## 互換性と移行

- 全変更が additive: 新スキーマ・status の新キー・overview の新キー・新 IPC。
  旧エンジンは `session.json` を読まないだけで壊れない
- `session.json` が無いノードは従来どおり動く（インストール手順の変更なし）
- `chat` モードを単発系（agent-flow）で指定しても、スキップしてログに残すだけ

## 段階導入

1. **契約と管理面**: `schemas/agent-session-commands.schema.json` + dashboard の
   編集 UI / IPC / プレビュー（書けるだけで読み手ゼロの状態。無害）
2. **agent-loop / kiro-loop**: `_start_pane` の前後 2 段 + revision 追跡 + status 追記
   （本命。常駐系がユースケースの中心）
3. **dashboard cowork**: `chatWindowScript` の新規セッション分岐へ挿入
4. **agent-flow**: ワーカー / orchestrator 起動時 + opt-out フラグ + status 追記

## テスト

- スキーマ: 例の妥当性・未知キー許容・`mode` / `on_error` / `when` の既定値
- ローダ（各ツール）: 不在 / 破損 / `enabled: false` がすべて no-op になる
- プレースホルダ: 展開が決定的・未定義は空文字・置換後もクォートを足さない
- `when`: engines / workloads / agent_cli の AND 結合、省略時は全適用
- 有界: `timeout` 超過で単体を打ち切る / `max_total_timeout` 超過で残りをスキップ
- `on_error`: `warn` は続行、`fail` はセッション開始を中止（ペインを作らない）
- べき等: 同一ペインで 2 回目は走らない / ペイン再起動後は走る
- kiro-loop: `chat` コマンドが業務プロンプトより先に送られる / 起動待ちを通過してから送る
- agent-flow: ワーカー起動時に 1 回だけ / ノード実行ごとには走らない /
  `chat` モードはスキップされる / opt-out フラグが効く
- dashboard: save で revision が増える / 未反映ハイライト / プレビューの `when` 反映

## 非目標

- **委譲先ノードへの伝播**（設計上の明確な拒否。各ノードのローカル設定のみ）
- **プロジェクト単位・ワークロード単位の上書き階層**（`when` で絞れる範囲を超える階層は
  作らない。プロジェクト固有の前準備は既存の `.kiro/kiro-loop.yml` が担う）
- **セッション終了時のコマンド**（`hooks.stop` が既にあり、用途も違う）
- **kiro-cli `--agent` JSON への native 反映**（案 C。開始側フックの有無を確認してから）
- **agent-project / agent-amigos への展開**（語彙だけ確保し、実装は範囲外）
- **実行結果のプロンプトへの混入**（stdout はログへ。プロンプトを汚さない）
