# maker ワークフロー機能の UI 境界（IF）設計

> 作成: 2026-09-05  
> 前提: [statemachine-maker に agent-flow ワークフローを載せる（UI 抜きの実装検討）](2026-09-05-statemachine-maker-agent-flow-workflow-design.md)  
> 対象: `tools/statemachine-maker/src/preload.js` と `src/main/ipc.js` の `flow:*`、agent-app の `automation.flow*`  
> 画面の配置・見た目は決めない。renderer がこの境界だけを見て画面を組めることを目標にする。

## 結論

境界は preload の `api.flow*`（agent-app では `api.automation.flow*`）14 本と、それに 1 対 1 で対応する IPC チャネル `flow:*`。renderer は「定義の下書き」「選択中の run」「ポーリングのタイマー」だけを持ち、真実はファイル（定義）と bus（run）にある。

| 群 | チャネル | 役割 |
|---|---|---|
| 語彙 | `flow:catalog` | ノード種別と標準パターンの雛形。UI が固定文字列を持たないための正典 |
| 定義 | `flow:list` `flow:read` `flow:save` `flow:delete` `flow:preview` | `<root>/.agents/workflows/<id>.json` の往復と、保存前の検証 |
| 準備 | `flow:context` | この root で実行できるか（AI 定義、書込先、道具、ホストの能力） |
| 実行 | `flow:run:start` `flow:run:list` `flow:run:read` `flow:run:cancel` `flow:run:respond` `flow:run:result` `flow:run:log` `flow:run:delete` `flow:run:openDelivery` | 投函、一覧、詳細、停止、人の回答、成果、起動ログ、削除、成果ブランチを開く |

## 0. 原則

1. **main は状態を持たない。** 定義は `.agents/workflows/`、run は `~/.agents/flow/bus` が正典。renderer が持つのは選択と下書きだけで、再起動しても同じ呼び出しで同じ画面に戻れる。
2. **定義の検証は throw しない。** `flow:preview` と `flow:save` は `issues[]` を返す。ノードへ紐付く `path` を持つので、UI は該当ノードに印を付けられる。操作の失敗（未登録 root、無い run、道具が無い）は throw で、`code` を持つ。
3. **renderer からパスを受け取らない。** 受けるのは root（登録済み）、id、runId、interactionId だけ。bus の場所は main が決める。
4. **時刻は ISO 8601 UTC 文字列。** bus が持つ epoch 秒（lease）は境界で ISO に直す。
5. **列挙は英語の固定語、表示語は UI。** engine 由来の日本語文（failure_reason、hint）は `message`（人向け一文）と `detail`（生の文字列）に分けて渡す。UI は `detail` をそのまま出さなくてよい。
6. **操作の結果は戻り値で分かる。** `start` は runId を、`cancel` は反映後の状態を、`respond` は書いた response を返す。次のポーリングを待たないと結果が分からない設計にしない。
7. **push イベントを持たない。** run は切り離した別プロセスが bus に書く。renderer が `flow:run:read` をポーリングし、`revision` が変わったときだけ描き直す。

## 1. 名前と配線

maker の preload はフラットに `api.flowXxx` を置く。`test/preload-contract.test.js` が 2 スペース字下げのキーと `api.xxx(` の形で照合するので、入れ子（`api.flow.list`）にすると検査から漏れる。

agent-app 側は `automation.flowXxx` として同名を置き、`invoke('automation:flow:…')` へ繋ぐ。`scripts/vendor.js` が maker の renderer の `api.` を `automationBridge.` へ置換するので、renderer の綴りは両方で同じになる。agent-app の `app.test.js` は maker の `register('flow:…')` と preload の `invoke('automation:flow:…')` を 1 対 1 で照合する。

| preload（maker） | preload（agent-app） | IPC |
|---|---|---|
| `flowCatalog()` | `automation.flowCatalog()` | `flow:catalog` |
| `flowList(root)` | `automation.flowList(root)` | `flow:list` |
| `flowRead(root, id)` | 同 | `flow:read` |
| `flowSave(root, workflow, mode)` | 同 | `flow:save` |
| `flowDelete(root, id)` | 同 | `flow:delete` |
| `flowPreview(root, workflow, request, parameters)` | 同 | `flow:preview` |
| `flowContext(root)` | 同 | `flow:context` |
| `flowRunStart(payload)` | 同 | `flow:run:start` |
| `flowRunList(root, limit)` | 同 | `flow:run:list` |
| `flowRunRead(root, runId)` | 同 | `flow:run:read` |
| `flowRunCancel(root, runId, reason)` | 同 | `flow:run:cancel` |
| `flowRunRespond(root, runId, interactionId, answer)` | 同 | `flow:run:respond` |
| `flowRunResult(root, runId)` | 同 | `flow:run:result` |
| `flowRunLog(root, runId)` | 同 | `flow:run:log` |
| `flowRunDelete(root, runId)` | 同 | `flow:run:delete` |
| `flowRunOpenDelivery(root, runId)` | 同 | `flow:run:openDelivery` |

戻りは既存の `handle()` と同じ `{ ok, data }` / `{ ok, error, code }`。preload の `invoke` は `ok: false` を `Error` にして投げ、`code` を `err.code` に写す（既存の呼び出しはそのまま動く）。

## 2. 型

TypeScript 表記。`?` は省略可、`| null` は「無いことを明示」。

### 2.1 定義

```ts
type NodeKind = 'work' | 'generate' | 'classify' | 'synthesize' | 'verify' | 'filter'
  | 'judge' | 'reduce' | 'split' | 'map' | 'human' | 'extract' | 'retrieve';

interface Workflow {                 // 保存形。schemas/agent-workflow.schema.json のルート
  version: 2;
  id: string;                        // ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$
  name: string;
  description: string;
  purpose: 'implementation';         // 固定。UI は編集させない
  entry: string[];                   // 根（deps が空）と一致。save が導出して書く
  exit: string[];                    // 葉（誰からも依存されない）と一致。同上
  nodes: WorkflowNode[];
  createdAt: string; updatedAt: string;
}

interface WorkflowNode {
  id: string;                        // 同一定義内で一意。字種は Workflow.id と同じ
  label: string;                     // 表示名。空なら id
  kind: NodeKind;
  goal: string;                      // {{key}} は入力パラメータ、{{request}} は要求テキスト
  deps: string[];                    // 依存するノード id
  tier: 'auto';                      // human 以外は常に 'auto'。UI は出さない
  interaction?: InteractionSpec;     // human のみ・必須
  x?: number; y?: number;            // 編集器の表示位置。実行に影響しない
}

interface InteractionSpec {          // agentcore.interaction.normalize_spec と同じ
  mode: 'approval' | 'choice' | 'input';
  prompt: string;
  options?: string[];                // choice のみ。重複なし 2 件以上
  default_option?: string;           // choice のみ。options の 1 つ。期限切れ時の既定
  timeout_seconds?: number;          // 正数。既定 604800（7 日）
  audience?: string[];               // 既定 ['reviewer']。UI は出さなくてよい
}
```

### 2.2 検証結果

```ts
interface Issue {
  level: 'error' | 'warning';
  code: IssueCode;
  message: string;                   // 人向け一文（日本語）
  path: string;                      // 'name' | 'nodes[2].goal' | 'nodes[2].deps[0]' | 'nodes[2].interaction.options'
  nodeId?: string;                   // path がノード配下ならその id
}

type IssueCode =
  | 'name-required' | 'id-invalid' | 'id-duplicate'
  | 'nodes-empty' | 'nodes-too-many'            // 64 超
  | 'node-id-required' | 'node-id-duplicate' | 'goal-required' | 'kind-invalid'
  | 'dep-unknown' | 'dep-self' | 'dep-cycle' | 'dep-on-split'
  | 'interaction-required' | 'interaction-invalid' | 'interaction-not-allowed'
  | 'parameter-reserved'                          // goal に {{today}} 等の実行器変数
  | 'goal-has-unfilled-parameter';                // preview で request/parameters を渡したのに残った {{key}}

interface PreviewResult {
  ok: boolean;                       // error が 0 件
  issues: Issue[];
  workflow: Workflow | null;         // 正規化後（ok のときだけ）。UI は下書きをこれで置き換えてよい
  parameterKeys: string[];           // goal と request に現れた {{key}}（予約語を除く、出現順）
  plan: Plan | null;                 // 投入形（ok のときだけ）。確認画面で見せる用
  digest: string;                    // 正規化後の定義の sha256 先頭 16 桁。表示位置と日時は含まない
}

interface Plan {                     // 投入形。schemas の $defs.plan
  name: string;
  nodes: { id: string; goal: string; kind: NodeKind; deps: string[]; interaction?: InteractionSpec }[];
}
```

### 2.3 一覧と語彙

```ts
interface WorkflowSummary {
  id: string; name: string; description: string;
  nodes: number;                     // ノード数
  humanNodes: number;                // human の数。「途中で人が答える」印に使う
  parameterKeys: string[];           // 実行時に入力が要るキー
  updatedAt: string;
  file: string;                      // 表示用の相対パス '.agents/workflows/<id>.json'
}

interface KindInfo {
  kind: NodeKind;
  label: string;                     // 日本語の短い名前
  description: string;               // 1 文
  group: 'work' | 'decision' | 'aggregate' | 'fanout' | 'human';
  planner: boolean;                  // planner が生成する種別か（human だけ false）
  constraints: {
    dependable: boolean;             // このノードに依存してよいか（split だけ false）
    needsInteraction: boolean;       // human だけ true
  };
}

interface PatternInfo {              // agent-flow patterns --json と同じ
  id: string; label: string; description: string;
  template: { name: string; nodes: Plan['nodes'] };  // 雛形。goal に {{request}} を含む
}

interface Catalog {
  kinds: KindInfo[];
  patterns: PatternInfo[];           // agent-flow が起動できないときは []
  limits: { maxNodes: 64; idPattern: string };
}
```

### 2.4 実行前の文脈

```ts
interface FlowContext {
  root: string;
  agents: string[];                  // agent-herd defs --json。空なら実行不可
  defaults: { agent: string; model: string };   // 設定（automationAgent / automationModel）
  workspace: {
    ok: boolean;                     // 書込ありで実行できるか
    branch: string;                  // 現在のブランチ（detached なら commit）
    origin: string;                  // origin の URL。無ければ ''
    reason: string;                  // ok=false の理由（'origin が無い' など）
  };
  tools: { agentFlow: ToolItem };    // tools:status と同じ形の 1 行
  capabilities: { openDelivery: boolean };      // ホストが成果ブランチを開けるか（agent-app: true）
  bus: string;                       // 表示用。ログや説明に出す
}

interface ToolItem { id: string; label: string; ok: boolean; summary: string; hint: string }
```

### 2.5 run

```ts
type RunState =
  | 'launching'      // inbox は書いたが meta.json がまだ無い（起動直後）
  | 'launch-failed'  // 60 秒経っても meta.json が無い。ログを見る
  | 'planning' | 'executing' | 'evaluating' | 'verifying' | 'finalizing'
  | 'waiting'        // 未回答の人の確認がある（他の phase より優先して見せる）
  | 'stalled'        // 非終端なのに駆動プロセスの生存が確認できない
  | 'done' | 'failed' | 'cancelled';

interface RunSummary {
  runId: string;
  title: string;                     // 投函時の title。無ければ request の先頭 60 字
  workflowId: string | null;         // 起動元の定義。要求だけの run は null
  state: RunState;
  terminal: boolean;
  createdAt: string;
  updatedAt: string | null;
  progress: { done: number; failed: number; total: number };   // total=0 は計画前
  waiting: number;                   // 未回答の確認の数
  readonly: boolean;
}

interface RunDetail extends RunSummary {
  revision: string;                  // 変化検知用。同じなら描き直さない
  request: string;                   // {{key}} を埋めた後の要求テキスト
  input: {                           // 再実行の初期値
    workflowId: string | null; request: string; parameters: Record<string, string>;
    readonly: boolean; agent: string; model: string; pattern: string;
  };
  workspace: { url: string; local: string; base: string } | null;
  failure: { kind: FailureKind; message: string; detail: string } | null;
  alive: boolean | null;             // 非終端: 駆動中か。終端: null
  phase: string | null;              // engine の phase そのまま（表示の補助）
  strategy: { patterns: string[]; reason: string } | null;
  nodes: RunNode[];                  // graph の順（deps を満たす順に main が並べる）
  interactions: RunInteraction[];
  final: RunFinal | null;
  delivery: Delivery | null;         // 書込あり run の公開結果。読み取り専用は null
  log: { path: string };             // 起動ログの場所。中身は flow:run:log
}

type FailureKind = 'plan' | 'verification' | 'publication' | 'workset' | 'agent' | 'orphaned' | 'cancelled' | 'other';

interface RunNode {
  id: string; kind: NodeKind; goal: string; deps: string[];
  state: 'pending' | 'claimed' | 'parked' | 'waiting' | 'done' | 'failed';
  who: string | null;                // 実行した名義
  agent: { cli: string; model: string } | null;   // 実行に使った AI（結果に記録がある場合）
  startedAt: string | null;          // claim 時刻
  finishedAt: string | null;
  output: string | null;             // 全文。UI は必要なら先頭だけ出す
  data: unknown | null;              // 構造化成果
  artifacts: string[];               // artifacts/<id>/ のパス
  interactionId: string | null;      // human ノードの確認 id
  dynamic: boolean;                  // split の fan-out で実行時に増えたノード
}

interface RunInteraction {
  interactionId: string; nodeId: string;
  mode: 'approval' | 'choice' | 'input';
  prompt: string; options: string[]; defaultOption: string | null;
  createdAt: string; expiresAt: string;
  state: 'open' | 'answered' | 'resolved' | 'expired';
  resolution: { outcome: string; answer: Record<string, unknown>; actor: string; resolvedAt: string } | null;
}

interface RunFinal {
  finishedAt: string;
  summary: string;                   // engine の 1 行 × ノード数
  verification: { state: string; failed: string[] } | null;
  ci: { state: 'passed' | 'failed' | 'running' | 'unknown'; url: string } | null;
}

interface Delivery {
  state: 'published' | 'published-manually' | 'not-required' | 'failed' | 'unknown';
  branch: string;                    // 'af/<run-id>'
  url: string; commit: string;
  error: string;                     // failed のとき
  recovery: { repository: string; ref: string } | null;   // failed のとき。手動復旧の場所
}

interface RunResult {                // agent-flow result --json
  runId: string; status: string; done: boolean; request: string;
  finalNodes: { id: string; kind: NodeKind; output: string; data: unknown; artifacts: string[] }[];
}

interface RunLog { path: string; tail: string; truncated: boolean; exists: boolean }
```

## 3. チャネル仕様

各項目は 入力 / 出力 / throw / 副作用 / 呼ぶ場面 の順。throw の `code` は §7。

### `flow:catalog`

- 入力: なし
- 出力: `Catalog`
- throw: しない。`patterns` は agent-flow を起動できなければ `[]`
- 副作用: なし。`agent-flow patterns --json` を 10 秒上限で 1 回叩き、プロセス内にキャッシュする
- 呼ぶ場面: 起動時に 1 回。ノード追加メニュー、雛形選択、表示語の正典

### `flow:list`

- 入力: `{ root }`
- 出力: `WorkflowSummary[]`（name 昇順）
- throw: `root-unregistered`
- 副作用: なし。壊れた JSON は一覧から落とさず `name: '(読めません) <id>'`、`nodes: 0` で返す（UI が削除へ導ける）
- 呼ぶ場面: root 選択時、保存・削除の後

### `flow:read`

- 入力: `{ root, id }`
- 出力: `{ workflow: Workflow; issues: Issue[]; digest: string }`。ファイルは正規化して返す（v1 や dashboard が書いた `methods` / `continuation` は読み捨てる）
- throw: `root-unregistered` `flow-not-found` `flow-unreadable`（JSON でない）
- 副作用: なし。ファイルは書き換えない
- 呼ぶ場面: 編集開始。`issues` が空でなければ「読めたが直しが要る」として編集器に印を出す

### `flow:save`

- 入力: `{ root, workflow: Workflow, mode: 'create' | 'update' }`
- 出力: `PreviewResult & { saved: boolean; file: string }`。`ok=false` なら書かず `saved: false`
- throw: `root-unregistered`、`flow-exists`（create で既にある）、`flow-not-found`（update で無い）、`flow-write-failed`
- 副作用: `.agents/workflows/<id>.json` を tmp へ書いて rename。`updatedAt` を更新、`createdAt` は create のときだけ付ける
- 冪等: update は同じ内容でも `updatedAt` が進む。UI は戻りの `workflow` で下書きを置き換える
- 呼ぶ場面: 保存ボタン。競合検出（`expectedUpdatedAt`）は初版に持たない。dashboard はこの場所を読み取り専用にしており、書き手は maker だけ

### `flow:delete`

- 入力: `{ root, id }`
- 出力: `{ deleted: boolean }`
- throw: `root-unregistered` `flow-not-found`
- 副作用: ファイルを消す。実行中の run には影響しない（run は投函時の plan を持つ）

### `flow:preview`

- 入力: `{ root, workflow, request?: string, parameters?: Record<string,string> }`
- 出力: `PreviewResult`
- throw: `root-unregistered` だけ。定義の不正は `issues` へ
- 副作用: なし
- 挙動: 正規化、グラフ検査、`parameterKeys` の抽出。`request` か `parameters` が渡されたら `plan.nodes[].goal` に値を埋め、埋まらなかった `{{key}}` を `goal-has-unfilled-parameter`（error）にする。`{{request}}` は埋めない
- 呼ぶ場面: 編集中（debounce 300ms 程度）、保存前、実行前の確認。同じ実装を `flow:save` と `flow:run:start` が内側で呼ぶので、preview が ok なら save も start も定義では失敗しない

### `flow:context`

- 入力: `{ root }`
- 出力: `FlowContext`
- throw: `root-unregistered`
- 副作用: なし。`agent-herd defs --json`、`git` 3 回（toplevel / branch / origin）、`agent-flow patterns --json` を叩く。合計 2 〜 5 秒かかりうるので UI は結果を root ごとに保持し、実行画面を開くたびに取り直さなくてよい
- 呼ぶ場面: 実行画面を開くとき。`agents` が空か `tools.agentFlow.ok=false` なら実行ボタンを無効にし、`hint` を出す。`workspace.ok=false` なら「書込あり」を選べなくする

### `flow:run:start`

- 入力:
  ```ts
  {
    root: string;
    source: { type: 'workflow'; id: string }        // 保存済み定義
          | { type: 'draft'; workflow: Workflow }    // 未保存の下書き（試し実行）
          | { type: 'pattern'; pattern: string }     // 標準パターン。要求だけで走る
          | { type: 'auto' };                        // planner に任せる
    title?: string;
    request: string;                                 // 必須。{{request}} と planner の入力
    parameters?: Record<string, string>;             // parameterKeys に対する値
    readonly?: boolean;                              // 既定 false。true なら workspace を付けない
    agent?: string; model?: string;                  // 既定は FlowContext.defaults
  }
  ```
- 出力: `{ runId: string; state: 'launching'; request: string; plan: Plan | null; log: { path } }`
- throw（この順で検査し、どれも bus に何も書かない）:
  1. `root-unregistered`
  2. `request-required`
  3. `flow-not-found`（source.workflow）/ `flow-invalid`（source.draft で issues に error。`issues` を `err.issues` に添える）
  4. `parameters-invalid`（未定義キー / 未入力。`err.detail` にキー名）
  5. `agent-unknown`（`agents` に無い）
  6. `workspace-unavailable`（readonly=false で origin が無い。UI は readonly=true で再送するか止める）
  7. `tool-missing`（agent-flow を起動できない）
  8. `launch-failed`（spawn 自体の失敗）
- 副作用: `inbox/<runId>.json` を書き、`agent-flow --bus <bus> --run-id <runId> run --from-inbox --agent-cli <agent> [--model <model>]` を切り離して起動する。cwd は root、stdout / stderr は `~/.agents/flow/logs/<runId>.log`
- inbox 記録（engine が読む鍵は submit_request 契約どおり。`submitter_context` は engine が読まない自分用の鍵）:
  ```json
  {
    "id": "app-20260905-143012-4821",
    "title": "…",
    "request": "…（{{key}} を埋めた後）",
    "submitter": "agent-app",
    "purpose": "implementation",
    "readonly": false,
    "workspace": { "url": "<origin>", "local": "<root>", "base": "<branch>", "path": "", "desc": "workflow" },
    "references": [],
    "plan": { "name": "…", "nodes": [ … ] },
    "pattern": "…（source.pattern のときだけ）",
    "submitted_at": "2026-09-05T05:30:12Z",
    "submitter_context": { "root": "<root>", "workflow": "<id or null>", "digest": "…", "parameters": { … }, "agent": "…", "model": "…" }
  }
  ```
  読み取り専用のときは `"readonly": true, "workspace": null`。
- 同時実行: 制限しない。同じ定義を続けて 2 回投げれば 2 本走る。UI 側で二重クリックを抑える
- 呼ぶ場面: 実行ボタン。戻った `runId` で即 `flow:run:read` を始める

### `flow:run:list`

- 入力: `{ root, limit?: number }`（既定 30）
- 出力: `RunSummary[]`（createdAt 降順）
- throw: `root-unregistered`
- 副作用: なし。bus が無ければ `[]`
- 絞り込み: `inbox/<id>.json` の `submitter === 'agent-app'` かつ `submitter_context.root === root`。inbox が消えた run（gc 後）は meta の `workspace.local === root` で拾う。それも無ければ出さない
- コスト: run 1 件につき inbox と meta.json、results の件数（ディレクトリ列挙）だけ。graph や結果本文は読まない
- 呼ぶ場面: 実行画面の一覧。非終端の run があるあいだ 5 秒ごと、無ければ操作時だけ

### `flow:run:read`

- 入力: `{ root, runId }`
- 出力: `RunDetail`
- throw: `root-unregistered` `run-not-found`（inbox も run dir も無い、または別 root の run）
- 副作用: なし
- 状態の合成（§4）と `revision` の算出（§6）はここで行う
- 呼ぶ場面: 選択中の run。非終端なら 2 秒ごと、`waiting` / `stalled` でも同じ。終端になったら止める。`revision` が前回と同じなら描き直さない

### `flow:run:cancel`

- 入力: `{ root, runId, reason?: string }`
- 出力: `{ state: RunState }`（反映後。通常 `cancelled`）
- throw: `root-unregistered` `run-not-found` `run-terminal`（既に終わっている。冪等にせず知らせる）`tool-missing` `cancel-failed`（CLI が非 0。`detail` に stderr）
- 副作用: `agent-flow --bus <bus> cancel <runId> --reason <reason>` を 30 秒上限で実行。engine が即 `status=cancelled` にする
- 呼ぶ場面: 停止ボタン。`launching` のときも呼べる（inbox にマーカーが置かれ、起動しても即終端する）

### `flow:run:respond`

- 入力: `{ root, runId, interactionId, answer }`
  ```ts
  answer = { decision: 'approved' | 'rejected'; comment?: string }   // approval
         | { option: string; comment?: string }                      // choice
         | { text: string }                                          // input
  ```
- 出力: `{ responseId: string; submittedAt: string; interaction: RunInteraction }`（`state: 'answered'`）
- throw: `root-unregistered` `run-not-found` `interaction-not-found` `interaction-closed`（resolution 済みか期限切れ）`answer-invalid`（mode と形が合わない。`detail` に理由）`answer-too-large`（64KB）
- 副作用: `interactions/<ix>/responses/<responseId>.json` を append-only で書く（`wx` で作って link）。engine が次の巡回で `resolution.json` を書き、ノードが進む
- 呼ぶ場面: 確認カードの回答。戻りで `answered` にし、次の read で `resolved` に変わる。二重送信は engine が最小 responseId を採用するので害は無いが、UI は `answered` の間はボタンを無効にする

### `flow:run:result`

- 入力: `{ root, runId }`
- 出力: `RunResult`
- throw: `root-unregistered` `run-not-found` `tool-missing` `result-failed`
- 副作用: `agent-flow --bus <bus> --run-id <runId> result --json` を 30 秒上限で実行
- 呼ぶ場面: 終端後に成果を全文で見るとき。非終端でも呼べる（`done: false` と確定済みの末端だけ）。一覧や進捗には使わない（read の `nodes[].output` で足りる）

### `flow:run:log`

- 入力: `{ root, runId, bytes?: number }`（既定 16KB）
- 出力: `RunLog`
- throw: `root-unregistered` `run-not-found`
- 副作用: なし。ファイルの末尾だけ読む
- 呼ぶ場面: `launch-failed` と `failed` の詳細、`stalled` の調査。通常の進捗には使わない

### `flow:run:delete`

- 入力: `{ root, runId }`
- 出力: `{ deleted: boolean }`
- throw: `root-unregistered` `run-not-found` `run-active`（非終端。先に cancel）
- 副作用: `runs/<runId>/`、`inbox/<runId>.json`、`inbox/claims/<runId>/`、`inbox/cancels/<runId>.json`、起動ログを消す。dashboard の `remove_run` と同じ範囲

### `flow:run:openDelivery`

- 入力: `{ root, runId }`
- 出力: `{ kind: 'worktree'; name: string; branch: string }`
- throw: `root-unregistered` `run-not-found` `delivery-unavailable`（`delivery.state` が published 系でない）`not-supported`（ホストがこの操作を提供しない）
- 実装: maker の `registerIpcHandlers(getWindow, options)` に `options.hooks.openDelivery(root, delivery)` を足す。maker 単体は hook が無いので `not-supported`。agent-app は hook で `worktree.create(repo, branch)`（既にあるブランチはそのまま持ってくる）を呼び、名前を返す。UI は `FlowContext.capabilities.openDelivery` を見てボタンの有無を決める
- 呼ぶ場面: `done` で `delivery.state` が published のとき。開いた後に会話領域へ切り替えるかは agent-app 側の話で、この境界には入れない

### `tools:status` の拡張（既存）

`agent-flow` の行を足す。`flow:context.tools.agentFlow` と同じ `ToolItem`。判定は `agent-flow patterns --json` が 10 秒以内に 0 で終わること（bus に触らない一番軽いコマンド）。

## 4. run の状態の合成

`RunState` は maker が bus の 4 つの事実から決める。UI は再計算しない。

| 優先 | 条件 | state |
|---|---|---|
| 1 | meta.status が done / failed / cancelled | そのまま。`canceled` 綴りは `cancelled` に寄せる |
| 2 | meta.json が無く inbox がある | 投函から 60 秒未満なら `launching`、以上なら `launch-failed` |
| 3 | 未回答（resolution 無し・期限内・responses 無し）の interaction がある | `waiting` |
| 4 | `alive === false`（生存リースが切れている、または更新が 600 秒無い） | `stalled` |
| 5 | meta.phase が planning / executing / evaluating / verifying / finalizing | そのまま |
| 6 | それ以外 | 全ノード終端なら `finalizing`、graph が無ければ `planning`、あれば `executing` |

`alive` の導出は dashboard の `runAlive` と同じ（`orch_lease_until` があればそれ、無ければ `updated_at` の経過 600 秒）。`stalled` は「止まっているかもしれない」であって「失敗」ではない。engine の孤児回収（次の `run` / `participate`）で `running` に戻ることがある。

`failure.kind` は `meta.failure_reason` 先頭のタグから引く。

| タグ | kind | UI の勧め |
|---|---|---|
| `[user-plan]` | `plan` | 定義を直して再実行。preview で捕まるはずの穴なので issue として記録 |
| `[verification]` | `verification` | 終端の検証が赤。`final.verification.failed` のノードを見る |
| `[workset]` `publication` を含む | `publication` | `delivery.recovery` を見せる |
| `[agent-flow]` `[agent-control]` `[node-budget]` | `agent` | ログを見る |
| `orphaned` を含む | `orphaned` | 再開は engine に任せる。手動なら再実行 |
| status が cancelled | `cancelled` | なし |
| その他 | `other` | ログ |

`message` はタグを剥いだ最初の 1 文、`detail` は元の文字列。

### 状態ごとに UI が出せる操作

| state | cancel | respond | result | openDelivery | delete | 再実行（start） |
|---|---|---|---|---|---|---|
| launching | 可 | 不可 | 不可 | 不可 | 不可 | 可 |
| launch-failed | 不可（run が無い） | 不可 | 不可 | 不可 | 可 | 可 |
| planning / executing / evaluating / verifying / finalizing | 可 | 不可 | 可（部分） | 不可 | 不可 | 可 |
| waiting | 可 | 可 | 可（部分） | 不可 | 不可 | 可 |
| stalled | 可 | 状況次第 | 可（部分） | 不可 | 不可 | 可 |
| done | 不可 | 不可 | 可 | delivery 次第 | 可 | 可 |
| failed / cancelled | 不可 | 不可 | 可 | delivery 次第 | 可 | 可 |

「再実行」は `flow:run:read.input` を初期値にして `flow:run:start` を呼ぶだけ。engine の resume（同じ run-id で続きから）は初版の境界に出さない。

## 5. 定義のライフサイクル

```
新規:  flow:catalog ─→ 下書き（UI）─→ flow:preview（随時）─→ flow:save(create)
編集:  flow:read ─→ 下書き ─→ flow:preview ─→ flow:save(update)
複製:  flow:read ─→ id を変えて flow:save(create)
雛形:  flow:catalog.patterns[].template ─→ 下書き（goal の {{request}} はそのまま残す）
```

- 下書きは `Workflow` の形をそのまま持つ。`entry` / `exit` は UI が触らない（save が導出）。
- `Issue.path` の規約: `nodes[<index>]` は配列の位置、`nodeId` は id。UI はどちらで印を付けてもよい。
- preview の `digest` は「保存すると何が変わるか」の判定に使える（保存済みの digest と比べる）。`x` / `y` の移動だけでは digest は変わらない。
- 保存直後に `flow:list` を取り直す（`updatedAt` と `parameterKeys` が変わる）。

## 6. run のライフサイクルとポーリング

```
start ─→ launching ─→ planning ─→ executing ⇄ waiting ─→ … ─→ finalizing ─→ done | failed
   │                                                │
   └── launch-failed                                └── cancel ─→ cancelled
```

- `flow:run:start` の戻りで runId を得たら、即 `flow:run:read` を 1 回呼び、以後 2 秒間隔。`terminal` になったら止める。
- `revision` は次の文字列の sha1: `meta.json` / `graph.json` / `final.json` の mtime、`results/` `claims/` `waits/` `interactions/` 配下のファイル名と mtime の並び。UI は前回の `revision` と同じなら DOM を触らない。
- 一覧（`flow:run:list`）は非終端の run があるあいだ 5 秒。終端だけなら操作後にだけ取り直す。
- `waiting` は `interactions[].state === 'open'` と同義。回答後は `answered` になり、engine が `resolution.json` を書くと `resolved`。`open` に戻ることはない。
- `launching` が 60 秒続くと `launch-failed` に落ちる。原因はほぼ「`agent-flow` が起動して即死」なので `flow:run:log` の `tail` を出す。`launch-failed` の run は inbox だけが残っている。delete で消せる。
- 終端後の `delivery` は sink ノードの `data.publication`（1 要素）または `data.deliveries[0].publication` から読む。無ければ `state: 'unknown'`。

## 7. エラー契約

`handle()` を `{ ok: false, error: message, code }` に広げる（`code` は `err.code` があるときだけ）。preload の `invoke` は `Error` に `code` と `detail`、`issues` を写す。既存チャネルは `code` 無しのまま。

| code | 意味 | UI の回復 |
|---|---|---|
| `root-unregistered` | 登録に無い root | フォルダを登録し直す |
| `flow-not-found` / `flow-exists` / `flow-unreadable` / `flow-write-failed` | 定義ファイル | 一覧を取り直す、id を変える、ファイルを見る |
| `flow-invalid` | start(draft) で error が残る | `err.issues` を編集器に出す |
| `request-required` / `parameters-invalid` | 入力欄 | 該当欄に印。`detail` にキー名 |
| `agent-unknown` / `tool-missing` / `workspace-unavailable` | 環境 | `flow:context` を取り直し、hint を出す |
| `launch-failed` / `cancel-failed` / `result-failed` | 外部コマンド | `detail` を詳細表示。ログへ |
| `run-not-found` / `run-terminal` / `run-active` | run の状態が操作に合わない | 一覧と詳細を取り直す |
| `interaction-not-found` / `interaction-closed` / `answer-invalid` / `answer-too-large` | 回答 | 詳細を取り直す。`answer-invalid` は欄に印 |
| `delivery-unavailable` / `not-supported` | 成果を開く | ボタンを出さない |

`message` は日本語の一文で、何をすればよいかを先に書く。engine の stderr や終了コードは `detail` に置く。

## 8. 境界と安全

- 全チャネルで `requireRoot`。run 系は `runId === path.basename(runId)` かつ §3 の絞り込みで root に属することを確かめる。別 root や dashboard の run は `run-not-found`。
- bus は `~/.agents/flow/bus` 固定。テストは `AGENT_APP_FLOW_BUS` 環境変数で差し替える。設定画面には出さない。
- `interactionId` は `^ix-[a-f0-9]{16}$`、`responseId` は main が採番。
- `output` / `goal` / `prompt` / `detail` は生の文字列。UI が必ずエスケープする。
- 外部コマンドは argv で起動し shell を通さない。`--reason` や `--model` に renderer の文字列がそのまま入るが、引数として渡すだけなので展開されない。

## 9. 境界を固定するテスト

- `test/flow-contract.test.js`: preload の `flowXxx` 16 本と `register('flow:…')` の 1 対 1。`app.test.js` は agent-app 側で同じことを見る。
- `test/flow-model.test.js`: `Issue` のゴールデン（各 IssueCode を 1 件ずつ出す最小の定義）、`parameterKeys` の順序と予約語の除外、`digest` が x / y / 日時に依存しないこと。
- `test/flow-run-read.test.js`: `test/fixtures/flow-bus/` に小さな bus（inbox だけ、planning、waiting、stalled、done with delivery、failed with `[user-plan]`）を置き、`RunDetail` のゴールデンと §4 の表を 1 ケースずつ。
- `test/agent-flow.test.js`: inbox 記録のゴールデン、起動 argv、`startDetached` をモックして log path、`respond` の `wx` と mode 検査、`cancel` の非 0 を `cancel-failed` に写すこと。
- 結合（任意）: `agent-flow run --from-inbox --executor stub` で 1 本回し、`flow:run:read` が `done` まで進むこと。stub が無ければ skip。

## 10. 決めなかったこと

- 保存の競合検出（`expectedUpdatedAt`）。書き手が maker だけなので初版は持たない。
- 同じ run-id での resume。engine は持っているが、UI からの再実行は新規投函で足りる。
- run の保持期間と自動掃除。dashboard が同じ bus を 1 日 1 回掃除する。maker は `flow:run:delete` だけ。
- 進捗の push。ポーリングで足りなくなったら main 側で `fs.watch` を試し、境界は `revision` のまま変えない。
