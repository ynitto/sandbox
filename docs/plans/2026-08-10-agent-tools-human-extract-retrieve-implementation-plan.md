# human / extract / retrieve ノード実装計画

> 前提設計: [2026-08-10-agent-tools-human-extract-retrieve-design.md](./2026-08-10-agent-tools-human-extract-retrieve-design.md)  
> 方針: 新しい状態管理・外部依存・条件付き DAG は追加せず、既存の agentcore、file bus、park & poll、dashboard feature 境界を使う。

## 完了条件

- standalone agent-flow のユーザー定義フローで `human` を実行・回答・再開できる。
- `extract` / `retrieve` が根拠付きの型付き data を返し、不正出力を done にしない。
- dashboard で 3 kind を編集でき、human に tier / model を要求しない。
- unresolved human interaction が要対応に表示され、dashboard は response だけを書く。
- 人の承認後も project の機械検証が省略されない。
- schema、agentcore、agent-flow、dashboard、文書の公開 kind が一致する。

## 0. 変更前の固定

### 対象

- 既存テストだけを実行し、現在の失敗を記録する。
- unrelated な作業ツリー変更を stage しない。

### 確認コマンド

```bash
cd tools/agent-tools/agentcore
python3 -m unittest discover -s tests

cd ../../agent-flow
python3 -m unittest tests.test_user_plan tests.test_waits tests.test_agent_cli

cd ../agent-dashboard
node test/adhoc-flow.test.js
node test/hitl-review.test.js
node test/flow-park-cancel.test.js
```

## 1. 共通契約と単一 validator

### 変更ファイル

- 新規 `schemas/agent-interaction.schema.json`
- 新規 `schemas/agent-node-data.schema.json`
- 更新 `schemas/README.md`
- 新規 `tools/agent-tools/agentcore/agentcore/interaction.py`
- 新規 `tools/agent-tools/agentcore/agentcore/nodecontract.py`
- 更新 `tools/agent-tools/agentcore/agentcore/__init__.py`
- 新規 `tools/agent-tools/agentcore/tests/test_interaction.py`
- 新規 `tools/agent-tools/agentcore/tests/test_nodecontract.py`

### 実装

1. `nodecontract.py` に次の定数を置く。
   - 全ユーザー定義 kind
   - planner が生成可能な kind（`human` を除外）
   - 構造化 kind
2. `validate_node_data(kind, data)` を 1 つだけ実装する。
   - human resolution data
   - extract records / evidence
   - retrieve sources
3. `interaction.py` に次だけを実装する。
   - request / response の正規化と検証
   - run id / node id からパス安全な interaction id を決定的に生成
   - serialized request / response の 64 KiB 上限検証
   - expires 判定
   - response 集合から resolution を決める純粋関数
4. JSON Schema と Python 定数を直接比較する golden test を置く。
5. JSON Schema ランタイム依存は追加しない。

### テスト

- 3 mode の正常値と mode ごとの不正 answer
- choice option / default の整合
- timeout は正数のみ
- response の決定順と immutable resolution
- extract / retrieve の空配列は有効
- evidence の無い extract record は不正
- schema enum と agentcore 定数の一致

## 2. agent-flow の公開 kind と user-plan

### 変更ファイル

- 更新 `tools/agent-flow/agent_flow/_head.py`
- 更新 `tools/agent-flow/agent_flow/patterns.py`
- 更新 `tools/agent-flow/agent_flow/config.py`
- 更新 `tools/agent-flow/agent_flow/agent.py`
- 更新 `tools/agent-flow/tests/test_user_plan.py`
- 更新 `tools/agent-flow/tests/test_agent_cli.py`

### 実装

1. `VALID_KINDS` / `STRUCTURED_KINDS` のローカル列挙を agentcore の定数へ寄せる。
2. planner prompt には planner 可能 kind だけを渡し、`human` を候補へ出さない。
3. `_coerce_tasks` でも planner 由来の human を `work` へ丸める。
4. `plan_strategy_user` は human を厳格に受理し、`interaction` を保持する。
5. human では `agent` を拒否する。extract / retrieve は従来どおり agent を許可する。
6. kind 別 agent 設定の説明と既定値一覧を更新する。

### テスト

- user-plan の human interaction が graph / task へ失われず届く
- human に agent がある場合は投入時エラー
- planner 出力の human は採用されない
- extract / retrieve は planner / user-plan の両方で有効
- 公開 kind 一覧の golden test

## 3. human の park / resolve

### 変更ファイル

- 更新 `tools/agent-flow/agent_flow/bus.py`
- 更新 `tools/agent-flow/agent_flow/work.py`
- 更新 `tools/agent-flow/agent_flow/waits.py`
- 更新 `tools/agent-flow/agent_flow/continuation.py`
- 新規 `tools/agent-flow/tests/test_interactions.py`
- 更新 `tools/agent-flow/tests/test_waits.py`

### 実装

1. Bus に interaction の原子的な read / write を追加する。
   - request: create-once
   - response: append-only
   - resolution: create-once
2. human node を claim した場合は executor を呼ばず、request と human wait を作って claim を解放する。
3. crash window を避けるため request → wait → claim release の順に書く。
4. `service_waits` は executor plugin の有無を判定する前に human wait を処理する。
5. resolution を先に固定し、そこから既存 `write_result` を冪等生成して wait を消す。
6. choice default 以外の timeout は expired / failed にする。
7. continuation の汎用 failed retry から human を除外する。
8. run cancel / gc は既存 waits と一緒に human wait を停止する。interaction は監査用に残す。

### テスト

- approval / choice / input の done
- reject / expired の failed
- choice default の defaulted / done
- response 競合の決定順
- resolution 後、result 前の再起動復元
- resolution 後の late response が結果を変えない
- human wait が worker slot を占有しない
- executor が `agent` でも human wait を resolver が処理する
- human failed が retry node を作らない

## 4. extract / retrieve の実行と結果検証

### 変更ファイル

- 更新 `tools/agent-flow/agent_flow/agent.py`
- 必要最小限の更新 `tools/agent-flow/agent_flow/repair.py`
- 更新 `tools/agent-flow/tests/test_agent_cli.py`
- 更新 `tools/agent-flow/tests/test_repair.py`

### 実装

1. extract の prompt に records / evidence 契約を付ける。
2. retrieve の prompt に sources 契約と read tool 利用を付ける。
3. extract は JSON-capable variant を使う。
4. retrieve は read-capable variant を使い、末尾の構造化 data を抽出する。
5. `nodecontract.validate_node_data` で検証し、不正なら既存 repair 呼び出しを 1 回だけ使う。
6. 修復後も不正なら例外を result の failed へ変換する。text-only done へ縮退しない。
7. retrieve のためだけの新 CLI variant や新依存は追加しない。

### テスト

- 正常な extract / retrieve
- 空 records / sources
- evidence 欠落
- 不正 JSON → 修復成功
- 不正 JSON → 修復失敗
- retrieve が read-capable CLI を選ぶ
- 修復が 2 回以上走らない

## 5. workflow editor

### 変更ファイル

- 更新 `tools/agent-dashboard/src/features/adhoc-flow/main/adhoc.js`
- 更新 `tools/agent-dashboard/src/renderer/features/adhoc-flow.js`
- 更新 `tools/agent-dashboard/src/renderer/styles.css`
- 更新 `tools/agent-dashboard/test/adhoc-flow.test.js`
- 新規または更新 `tools/agent-dashboard/test/agent-node-kinds-golden.test.js`
- 更新 `tools/agent-dashboard/package.json`（新規テストを test script に追加する場合だけ）

### 実装

1. `KINDS` に human / extract / retrieve を追加する。
2. kind と role の対応を修正する。
   - human → 人間
   - verify → 検証
   - その他の編集 kind → 作業
3. `classify → planner`、`judge → evaluator`、`ROLE_KIND` による逆変換を廃止する。
4. human node の inspector に interaction fields を表示する。
5. human では tier / method UI を隠し、保存時にも要求しない。
6. extract / retrieve の既定目的と結果説明を追加する。
7. `normalizeWorkflow` と `planFromWorkflow` で human だけ profile 解決を省く。
8. `methodWorkflowPattern` は system role を kind へ偽装しない。複数案の方式は既存並列統合パターンの option として扱う。
9. engine の公開 kind と dashboard の kind が一致する golden test を置く。

### テスト

- human workflow の保存・再読込・plan 化
- human の tier 不要、extract / retrieve の tier 必須
- interaction field が保存時に消えない
- classify / judge の表示ロールが作業
- human inspector に agent / method が出ない
- 公開 kind の engine / dashboard 一致

## 6. 要対応への投影と回答

### 変更ファイル

- 更新 `tools/agent-dashboard/src/features/agent-project/main/flow.js`
- 更新 `tools/agent-dashboard/src/features/agent-project/main/ipc.js`
- 更新 `tools/agent-dashboard/src/renderer/sections/needs.js`
- 必要に応じて更新 `tools/agent-dashboard/src/preload.js`
- 更新 `tools/agent-dashboard/test/hitl-review.test.js`
- 新規 `tools/agent-dashboard/test/flow-interaction.test.js`

### 実装

1. `readRun` が unresolved interaction を read-only で投影する。
2. needs view model に `source: project | flow` と response contract を持たせる。
3. flow interaction の回答 IPC は response ファイルを原子的に追加するだけにする。
4. dashboard は resolution、wait、result を書かない。
5. mode ごとに最小 UI を使う。
   - approval: 承認 / 却下 + comment
   - choice: radio/select + comment
   - input: textarea
6. audience、期限、入力が同期される旨を表示する。

### テスト

- unresolved interaction だけが表示される
- resolved / expired 後は回答操作を無効化する
- 回答が正しい response directory にだけ作られる
- project needs の既存回答経路が変わらない
- renderer から resolution / result を書けない

## 7. managed project の決定投影

### 変更ファイル

- 更新 `tools/agent-project/agent_project/flow.py`
- 更新 `tools/agent-project/agent_project/decisions.py`
- 更新 `tools/agent-project/tests/test_decisions.py`
- 更新 `tools/agent-project/tests/test_project_layer.py`

### 実装

1. managed run の settle 時に resolution を読む。
2. 未投影の resolution だけを既存 decisions append 経路へ渡す。
3. interaction id、mode、outcome、actor、resolution digest を記録する。
4. projection の有無を flow state 判定に使わない。
5. human approval が project verification を短絡しないことを明示的に保持する。

### テスト

- 同じ resolution の重複投影をしない
- digest が元 resolution と一致する
- projection 書き込み失敗でも flow result は変わらない
- human approve 後も verification plan が実行される

## 8. 正典・利用文書・一貫性ゲート

### 変更ファイル

- 更新 `docs/designs/agent-flow-design.md`
- 更新 `docs/designs/agent-dashboard-design.md`
- 更新 `docs/designs/agent-project-design.md`
- 更新 `docs/designs/agent-tools-concept.md`
- 更新 `tools/agent-flow/README.md`
- 更新 `tools/agent-dashboard/README.md`
- 更新 `tools/agent-project/README.md`
- 更新 `schemas/README.md`

### 接続表

| 契約 | 正典 | 実装 | テスト |
|---|---|---|---|
| node kind | agent-node-data schema | agentcore.nodecontract | nodecontract golden + dashboard golden |
| interaction | agent-interaction schema | agentcore.interaction | agentcore interaction tests |
| flow wait / resolution | agent-flow design | bus / work / waits | flow interaction / waits tests |
| editor | dashboard design | adhoc main / renderer | adhoc-flow tests |
| managed projection | project design | flow / decisions | project decision tests |

必要なら codd-gate の `coherence:` 注釈でこの接続を明示し、推定だけに頼らない。

### 最終確認

```bash
cd tools/agent-tools/agentcore
python3 -m unittest discover -s tests

cd ../../agent-flow
python3 -m unittest discover -s tests

cd ../agent-project
python3 -m unittest discover -s tests

cd ../agent-dashboard
npm test

cd ../..
codd-gate impact --base HEAD~1
codd-gate verify --base HEAD~1
```

## 実装時に増やさないもの

- 条件付き edge / branching DSL
- 個人 assignment / ACL / ID provider
- human 専用 daemon
- human 専用 waiting state
- retrieve 専用 CLI variant
- extract / retrieve の独立パターンカード
- agent-loop / agent-amigos の DAG node
- project verification を迂回する human approval

これらは現行要求を満たさない追加複雑性なので、再評価条件が成立するまで作らない。
