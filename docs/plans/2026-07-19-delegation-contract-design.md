# 委譲契約（delegation contract）設計 — agent-dashboard から agent-flow / agent-amigos へ同じ形で入札を扱う

- 日付: 2026-07-19
- 状態: 実装済み（agent-dashboard 側・エンジン無変更）。契約・アダプタ・IPC・renderer UI まで完了
- 契約: [`schemas/delegation.schema.json`](../../schemas/delegation.schema.json)
- 実装: `tools/agent-dashboard/src/features/delegation/`（`main/contract.js` / `main/amigos-adapter.js` / `main/flow-adapter.js` / `main/ipc.js`）、テスト `test/delegation.test.js`
- 関連: `docs/plans/2026-07-19-agent-dashboard-orchestration-token-budget-design.md`（agent-control / node-budget）、
  `schemas/amigos-command.schema.json`、`schemas/mission.schema.json`、`schemas/task.schema.json`

## 1. 背景と目的

agent-flow と agent-amigos はどちらも「バス上のファイルが真実・パス単位の書き込み所有権・
自分名義 claim ＋ `(ts, who)` 決定的タイブレーク ＋ lease」という同一の設計思想を**別実装**で
共有している。違いは委譲の単位（flow = タスクグラフのノード / amigos = ミッションのロール）、
入札フェーズの有無（flow = 先着 claim のみ / amigos = `owner-picks` で応募→オーナー確定）、
そしてダッシュボードとの接点の形（flow = `inbox/<id>.json` 直書き / amigos = `commands/*.json` 投函）だけ。

**目的**: バス・claim プロトコル自体は統一しない（独立性の担保）。代わりに、リポジトリの
既存原則「結合はデータ契約のみ」（`schemas/README.md`）に従い、**公示（post）→ 入札（bid）→
落札（award）→ 受入（accept）のライフサイクルをエンジン非依存に表す契約を 1 枚**定義し、
agent-dashboard のエンジン別アダプタがエンジンネイティブ形式へ決定的に変換する。
両エンジンのコードは v1 では**無変更**（既存の公式入力契約だけを使う）。

予算配分設計で「エンジン間の分散協調（台帳上での入札）」を却下し中央アロケータに寄せた判断
（orchestration-token-budget-design §配分方式・案C）と整合させる: 入札の調停はエンジン内の
既存機構（決定的 claim / roster）に任せ、ダッシュボードは**契約の変換と観測だけ**を行う。

## 2. 決定事項（フィールド設計の根拠）

### D1. 共通 `id` を両エンジンの native id にそのまま採用する（対応表を持たない）

- agent-amigos: `post` コマンドは `mission_id` の明示指定を受け付ける（`agent_amigos/commands.py:43`
  `rec.get("mission_id") or new_mission_id()`）。
- agent-flow: inbox の req-id は任意文字列で、そのまま run-id になる。ダッシュボードの
  `REQ_ID_RE`（`features/agent-project/main/flow.js:172`）は agent-project 形式の系統解析専用で、
  任意 id を拒否しない（resubmit 実装が既に任意接尾辞付き id を投函している）。

よって共通契約の `id`（推奨形 `dg-<YYYYMMDDHHMMSS>-<hex4>`、規約 `[A-Za-z0-9_-]{1,64}`）を
amigos の `mission_id` / flow の req-id にそのまま渡せば、**マッピングファイルなしで決定的に
突き合わせられる**。ただし契約上の正は読取ビューの `native_id` であり、同一文字列であることに
は依存しない（エンジン側の id 規約が将来変わってもアダプタで吸収できる）。

### D2. workload 語彙は agent-control / node-budget の既存語彙を再利用する

`workload: "flow" | "amigos"`。`routine` / `project` への拡張は additive（未知の workload は
無害に無視、`agent-control.schema.json` と同じ前方互換規約）。

### D3. 共通コアと engine ブロックの線引き

- **共通コア** = 依頼側の関心事（何を・どこで・どう割り当て・いくらまで・いつまでに）。
  エンジンが今解釈しない項目も、概念がエンジン非依存なら共通コアに置き「解釈マトリクス」
  （§4）で今の解釈状況を明示する（未解釈は無視 = additive）。
- **`engine.<workload>` ブロック** = エンジンの内部語彙そのもの（amigos の `roles[]` /
  `mission` 上書き、flow の `executor` / `inherit_from`）。共通コアに漏らさない。

### D4. `policy.assignment` の非対称は fail-fast で扱う

`first-come | owner-picks`。amigos は両対応（`assign.py` の `claim_role` / `apply_role` +
`confirm_assignment`）。flow は first-come のみ（claim = 即落札）。
`workload: flow` × `assignment: owner-picks` はアダプタが**投函前に拒否**する
（黙って first-come に落とさない。UI はフォーム段階で無効化）。
flow への応募→選定の追加は将来拡張（§7）であり v1 のスコープ外。

### D5. award / accept / reject は v1 では amigos 専用

flow のタスク単位の承認は gitlab executor の GitLab 台帳（`status:approved` ラベル / MR）が
正であり、gitlab-review-viewer という既存の承認面がある。二重の承認面を作らない。
`cancel` は両エンジン対応（amigos = `cancel` コマンド / flow = `inbox/cancels/` マーカー、
ダッシュボード実装済み `flow.js:cancelRun`）。

### D6. v1 の置き場はダッシュボード IPC 契約（ドロップディレクトリは将来拡張）

本契約が v1 で規定するのは (a) ダッシュボード renderer→main の投函ペイロード、
(b) アダプタが返す正規化ビュー（§5）。ファイルドロップ（`$AGENT_DELEGATION_DIR` 等）は
スキル・人からの投函が必要になった時点の将来拡張とし、その際も封筒は同形を使う
（処理成功で削除・失敗は `.rejected` 改名、amigos commands と同じ規約）。

### D7. agent-control の `delegation`（誘導）とは役割を分けて併存する

`agent-control.schema.json` の `delegation: {prefer, max_open_issues}` は「このノードで
抱え込まず外へ出せ」という**実行場所の誘導**（flow だけが解釈）。本契約は「何をどう公示し
誰が落札したか」という**委譲そのもののライフサイクル**。関心が直交するので統合しない。
両方が同時に効いてよい（例: 本契約で公示した flow run のタスクを、agent-control の誘導で
gitlab executor 経由の park & poll に流す）。

### D8. 応答なし（stalled）はフェーズではなく重畳フラグ `stale` で表す

ライフサイクル（§5.2 のフェーズ）と生死観測は直交する関心なので、フェーズ enum には
含めない。理由: (a) フェーズ写像がエンジン既存の導出（flow `meta.status` / amigos
`derive_phase`）と 1:1 のまま保てる、(b) flow の「run lease 失効」と amigos の「ノード
heartbeat 途絶」は意味の異なる信号で、1 つのフェーズ値に畳むと歪む、(c) 沈黙時に元の状態
（working だったか waiting だったか）が失われない — UI は「working + 応答なし」と重畳表示
できる。ビューは `stale: boolean`（+ amigos は該当ユニットを `stale_units[]` で列挙）。

## 3. 委譲要求封筒（書き込み契約）

全 op 共通: `op` / `version: 1` / `id` / `workload` が必須。未知キーは無視（前方互換・追加は additive）。

### 3.1 `post` — 公示

| フィールド | 型 / 既定 | 意味 |
|---|---|---|
| `id` | string（必須） | 冪等キー。amigos `mission_id` / flow req-id にそのまま採用（D1）。再投函は同一公示（二重公示防止） |
| `workload` | `flow` \| `amigos`（必須） | ルーティング先エンジン（D2） |
| `goal` | string（必須） | 何を達成するか。flow: `submit_request.request` 本文 / amigos: `mission.goal` |
| `title` | string | 表示名。amigos: `mission.title` / flow: 未解釈（ビューの表示にのみ使用） |
| `design` | string (markdown) | 設計文書本文。**契約上は省略可**。amigos: design doc（amigos 側の post は design を要求するため、省略時はアダプタが goal + references から合成して投函）/ flow: request 本文へ「## 設計」節として前置 |
| `workspace` | object | 成果物の書込先リポジトリ。`repos.schema.json` のエントリ形 `{url, path, base}`。flow: `submit_request.workspace` / amigos: `mission.workspace`（現状 opaque passthrough） |
| `references` | array of object | 参照リポジトリ（読むだけ）。同エントリ形。flow: `submit_request.references` / amigos: design doc に「## 参照リポジトリ」節として描画 |
| `policy.assignment` | `first-come`（既定）\| `owner-picks` | 入札方式。amigos: `mission.assignment_policy` へ透過 / flow: `first-come` のみ（`owner-picks` は投函前拒否、D4） |
| `policy.staffing` | `self-staff`（既定）\| `wait` \| `fail` | 未充足時の扱い。amigos: `mission.staffing_policy` / flow: 未解釈（daemon 常駐が前提） |
| `policy.staffing_timeout_sec` | number（既定 600） | amigos: `mission.staffing_timeout` / flow: 未解釈 |
| `acceptance` | `manual`（既定）\| `agent` | 受入判定。amigos: `mission.acceptance` / flow: 未解釈（gitlab executor の承認台帳が正、D5） |
| `budget.execution_minutes` | number（既定 0 = 無制限） | 依頼側予算。amigos: `mission.budget.execution_minutes` / flow: 未解釈（node-budget 契約が既にノード側でカバー） |
| `budget.per_unit_turns` | integer（既定 30） | amigos: `mission.budget.per_role_turns` / flow: 未解釈 |
| `deadline` | string (ISO8601) | amigos: `mission.deadline`（通知のみ・自動 fail しない）/ flow: 未解釈 |
| `priority` | `low` \| `normal`（既定）\| `high` | flow: gitlab executor の `priority:*` ラベル / amigos: 未解釈（将来 additive） |
| `requested_by` | string | 出自（`dashboard` / `human:<name>` / スキル名）。flow: `submit_request.submitter` / amigos: 監査用（design doc 冒頭に記録） |
| `requested_at` | string (ISO8601) | 投函時刻（アダプタが刻印） |
| `engine.amigos.roles` | array（amigos では必須） | 役割ミッション表（`mission.schema.json` の `roles` と同形。`post` コマンドへ透過） |
| `engine.amigos.mission` | object | `mission` ブロックの追加上書き（convergence 等。共通コア由来の値より優先） |
| `engine.flow.executor` | string | executor プラグイン指定（例 `gitlab`） |
| `engine.flow.inherit_from` | string | リトライ時の引き継ぎ元 run-id（`submit_request.inherit_from`） |

### 3.2 `award` — 落札確定（owner-picks のみ / v1 は amigos のみ）

`{op, version, id, workload, unit, node}` — `unit` = ロール id、`node` = 落札ノード。
amigos: `assign` コマンド `{command:"assign", mission:id, role:unit, node}` へ変換（owner-only）。
flow: v1 非対応（拒否）。

### 3.3 `accept` / `reject` — 受入 / 差し戻し（v1 は amigos のみ）

`{op:"accept", version, id, workload}` / `{op:"reject", version, id, workload, feedback}`。
amigos: 同名コマンドへ透過。flow: v1 非対応（D5）。

### 3.4 `cancel` — 中止（両エンジン対応）

`{op:"cancel", version, id, workload, reason}`。
amigos: `cancel` コマンド。flow: `flow.js:cancelRun` の 3 手（`inbox/cancels/<run-id>.json`
マーカー + meta 終端 + waits 掃除）。

## 4. 解釈マトリクス（v1）

「解釈」= エンジン側の挙動に反映される。「未解釈」= 無視（additive、将来拡張の余地を予約）。

| フィールド | flow | amigos |
|---|---|---|
| `id` | 解釈（req-id → run-id） | 解釈（mission_id） |
| `goal` / `design` / `references` | 解釈（request 本文へ合成） | 解釈（goal / design doc） |
| `title` | 未解釈 | 解釈 |
| `workspace` | 解釈 | passthrough（checkout 未実装） |
| `policy.assignment` | `first-come` 固定（他は拒否） | 解釈 |
| `policy.staffing*` | 未解釈 | 解釈 |
| `acceptance` | 未解釈（GitLab 台帳が正） | 解釈 |
| `budget.*` | 未解釈（node-budget が正） | 解釈 |
| `deadline` | 未解釈 | 解釈 |
| `priority` | 解釈（gitlab executor ラベル） | 未解釈 |
| `engine.flow.*` / `engine.amigos.*` | 各自解釈 | 各自解釈 |

## 5. 正規化ビュー（読み取り契約 — `$defs.delegation_view`）

各アダプタがバス上のファイルだけから導出する（両エンジンとも読取専用・CLI に聞かない、
現行アダプタの原則を維持）。導出元はエンジンの既存語彙:

- flow: `inbox/` / `runs/<id>/{meta,graph}.json` / `claims/<node>/<who>.json` /
  `waits/` / `results/` — 状態導出はエンジン本体の `node_state` と同一規則
  （既にダッシュボード `flow.js:readRun` が実装済み）。
- amigos: `mission.json` / `roles/` / `assignments/<role>/<node>.json` / `roster.json` /
  `status/` / `rejections/` / `deliverable/MANIFEST.json` / `final.json` / `cancelled.json`
  — `derive_phase` と同一規則（`missions.js` 実装済み）。

### 5.1 ビューの形

| フィールド | 意味と写像 |
|---|---|
| `id` / `workload` / `native_id` | 共通 id とエンジン側 id（通常同一文字列だが `native_id` が正、D1） |
| `phase` | 正規化フェーズ（§5.2） |
| `title` / `goal` | 表示用 |
| `units[]` | 入札対象の単位。amigos = ロール / flow = グラフノード |
| `units[].unit` | ロール id / ノード id |
| `units[].kind` | flow: ノード種別（work/verify/…）/ amigos: ロール title |
| `units[].state` | `open`（入札受付中）\| `claimed`（落札・実行中）\| `waiting`（park / away）\| `done` \| `failed` |
| `units[].bids[]` | 入札一覧。flow `claims/<node>/*.json` / amigos `assignments/<role>/*.json` の正規化: `{who, ts, claimed_at, lease_until, agent_cli?, state}` |
| `units[].bids[].state` | `applied`（応募中・owner-picks 未確定）\| `winner`（決定的タイブレーク勝者 or roster 確定）\| `lost` \| `expired`（lease 失効） |
| `units[].assignee` | 確定担当（amigos: `roster.json` 優先 / flow: claim 勝者） |
| `bids_open` | owner-picks で未確定ユニットが存在（UI の「落札」操作を活性化） |
| `stale` | 応答なしの重畳フラグ（フェーズと直交、D8）。flow: 非終端 run の `orch_lease_until` 失効（`runAlive` と同一規則）/ amigos: roster 確定ノードの heartbeat 途絶 |
| `stale_units[]` | 応答なしのユニット列挙（amigos のノード heartbeat はユニット単位のため） |
| `progress` | `{units_total, units_done, units_failed, units_open}` |
| `budget` | `{spent_seconds, limit_minutes}`。amigos: `events/*.jsonl` の `cli_seconds` 総和 / flow: `null`（node-budget 台帳が別契約） |
| `result` | `{status, accepted?, by?, ts?, path?}`。flow: `final.json` / amigos: `final.json` + `deliverable/MANIFEST.json` |
| `updated_at` | アダプタ導出時刻 |

### 5.2 フェーズ写像

| 正規化 | flow | amigos |
|---|---|---|
| `open` | inbox に要求あり・run 未生成 | `derive_phase = open`（必須ロール未充足） |
| `working` | run 生存（`orch_lease_until` 内）・実行中 | `working` / `integrating` |
| `waiting` | 生存 `waits/` あり（park = 承認待ち） | —（ユニット単位の away はビューでは `units[].state` で表現） |
| `reviewing` | —（GitLab 側で表現） | `reviewing`（accept 待ち） |
| `done` / `failed` / `cancelled` | `meta.status` | `derive_phase` |

応答なしはフェーズに含めず `stale` フラグの重畳で表す（D8）。フェーズはエンジン既存の
導出規則との 1:1 写像を保つ。

## 6. 実装状況（すべて agent-dashboard 側・エンジン無変更）

新 feature `src/features/delegation/` として実装した（`features/index.js` に登録）。

1. ✅ `schemas/delegation.schema.json` を正典化。
2. ✅ 共通投函口 `delegation:post|award|accept|reject|cancel` と一覧 `delegation:list` の IPC
   （`main/ipc.js`）。実体は既存アダプタ／契約への薄い委譲:
   - amigos = `features/amigos/main/homes.js:writeCommand`（`amigos-command` 契約）
   - flow = `main/flow-adapter.js:submitPost`（inbox 投函）/ `flow.js:cancelRun`（cancel の 3 手）
3. ✅ 契約コア `main/contract.js`（封筒の検証・id 採番・エンジン非対称の fail-fast）と
   各アダプタの `toView()`（`main/amigos-adapter.js` / `main/flow-adapter.js`）。amigos は
   `readMissionSummary` の出力 + バスの `assignments/<role>/` を読んで入札の勝者/応募/失効を
   決定的タイブレークで判定（`assign.py:winner` と同一規則）。flow は `readRun` の出力から
   先着=勝者 1 件を射影。
4. ✅ renderer UI。「委譲」タブで workload 選択 → 共通フォーム + engine 固有セクション
   （amigos のロール表 + ホーム / flow の executor + バス）→ 入札状況（`units[].bids` を
   落札/応募中/不落/失効で表示）と落札（owner-picks の applied に「落札」ボタン）・受入/差し戻し/
   中止の操作。実装は `src/renderer/features/delegation.js`（独立モジュール）。
   renderer.js には**フィーチャータブ登録簿**（`registerFeatureTab` / `featureTabs` /
   `renderFeatureTab`）という拡張シームを入れ、コアを触らずタブを差し込めるようにした
   （保守性向上 — 今後の feature も同パターンで追加）。差し戻しは Electron で動かない
   `window.prompt` を使わずインライン入力で受ける（amigos の既存方針に合わせた）。
5. ✅ テスト `test/delegation.test.js`（封筒検証・両アダプタの変換とビュー・IPC 配線・
   `amigos-command.schema.json` の enum 一致を突き合わせ）。`test/feature-split.test.js` に
   feature 配線の確認を追加。Python 側は既存の `CommandSchemaTests` / inbox 形式テストが引き続き正。

## 7. 将来拡張（v1 スコープ外・契約は additive に受け入れ可能な形にしてある）

- **flow への応募→選定**: amigos の `apply_role` / `confirm_assignment` と同じ 2 段階 claim
  （`claims-applied/` 名前空間 + orchestrator 確定）を flow 側に**別実装**で追加すれば、
  `workload: flow` × `owner-picks` の拒否を外すだけで契約は不変。
- **ドロップディレクトリ**（`$AGENT_DELEGATION_DIR`）: スキル・人からの投函口。封筒は同形。
- **flow への commands 形式投函口**: flow は既に `inbox/` と `inbox/cancels/` というコマンド的な
  口を持つため v1 はアダプタ変換で足りるが、操作の種類が増えるなら amigos と同じ
  `commands/*.json` 契約（処理成功で削除・失敗は `.rejected` 改名）へ寄せる余地がある。
- **`workload: project` / `routine`**: `task.schema.json` との橋渡し（enqueueToInbox への写像）。
- **共通 claim 仕様書**: `(ts, who)` タイブレーク・lease・書く→push→pull→再判定の手順を
  `docs/designs/` に「同じ仕様・別実装」として明文化し、片側だけの変更でズレる事故を防ぐ。
