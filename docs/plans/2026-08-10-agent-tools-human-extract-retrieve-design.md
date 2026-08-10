# agent-tools family: human / extract / retrieve ノード設計

> 状態: 承認済み（2026-08-10）  
> 対象: `schemas/`、`agentcore`、`agent-flow`、`agent-dashboard`、`agent-project`  
> 関連正典: [agent-tools concept](../designs/agent-tools-concept.md)、[agent-flow design](../designs/agent-flow-design.md)、[agent-project design](../designs/agent-project-design.md)、[agent-dashboard design](../designs/agent-dashboard-design.md)

## 1. 背景

ワークフロー編集では、モデルが実行する工程だけでなく、次の工程を明示的に表現する必要がある。

- 人が承認・選択・入力する工程
- 与えられた材料から構造化データを抽出する工程
- リポジトリや外部情報から根拠を取得する工程

現状の agent-flow は `work` 等の汎用ノードと、GitLab executor が使う `waits/` を持つが、人の介在をワークフローの契約として表現できない。`extract` と `retrieve` を `work` の自然文だけで区別すると、出力検証、利用可能な道具、UI 表示が実装ごとに分岐する。

また、ダッシュボードには `classify → planner`、`judge → evaluator` という誤った表示対応があり、ノード種別とエンジン内部ロールが混同されている。このまま種類を追加すると、表示と実行の不整合が増える。

## 2. 目的と非目的

### 目的

1. standalone の agent-flow でも使える人間介在契約を定義する。
2. `human`、`extract`、`retrieve` をノード種別として追加する。
3. 共通契約を `schemas/`、検証と決着規則を `agentcore` に集約する。
4. agent-flow の既存 park & poll、ファイルバス、決定的裁定を再利用する。
5. agent-dashboard から人間ノードを作成・確認・回答できるようにする。
6. agent-project の品質保証と人間の承認を混同しない。

### 非目的

- 人を個人名で割り当てる仕組み
- 認証・権限管理・ID プロバイダの追加
- 回答を契機に分岐辺を選ぶ条件付き DAG
- agent-loop や agent-amigos へのノード機構の移植
- `extract` / `retrieve` を新しいワークフローパターンとして増やすこと
- 人の承認による機械検証の省略

## 3. 採用案

共通の型付き契約を `schemas/` に置き、検証・正規化・回答競合の裁定を `agentcore` に一度だけ実装する。各エンジンは保存先、状態遷移、UI への投影だけを担当する。

| 案 | 実装コスト | リスク | 保守性 | 拡張性 | 学習コスト | 推奨度 |
|---|---|---|---|---|---|---|
| A. 共通スキーマ + agentcore | 中 | 低 | 高 | 高 | 中 | ★★★ |
| B. `work` / method / 既存 wait の組合せだけで表現 | 低 | 高 | 低 | 低 | 低 | ★☆☆ |
| C. agent-flow / project / dashboard ごとに個別実装 | 中 | 高 | 低 | 中 | 高 | ★☆☆ |

案 A は新しい状態管理を増やさず、契約差だけを共通化できる。案 B は入力・出力検証と UI が自然文依存になり、案 C は既に起きているロール表示のずれを再生産するため採らない。

## 4. 語彙とロール境界

| kind | UI 種別名 | 実行主体 | モデル選定 | 自動 planner | 主な結果 |
|---|---|---|---|---|---|
| `human` | 人間 | 人 | なし | 生成禁止 | 承認・選択・入力の決着 |
| `extract` | 抽出 | worker | あり | 生成可 | 根拠付き records |
| `retrieve` | 取得 | worker | あり | 生成可 | 根拠付き sources |

`human` は人間ロールとして UI に表示するが、モデル選定用の `AGENT_ROLES` には加えない。エージェント CLI を起動しないためである。

`extract` と `retrieve` はロールではなく worker が行う作業種別である。同様に `classify`、`judge`、`split` 等も worker の作業種別であり、planner / evaluator ではない。planner、evaluator、session は実行エンジン内部のシステム工程として UI の編集対象にしない。`verify` は既存どおり検証用途の表示を持つ。

## 5. 共通契約

### 5.1 ファイル

- `schemas/agent-interaction.schema.json`
  - `request`、`response`、`resolution` の公開契約
- `schemas/agent-node-data.schema.json`
  - `human`、`extract`、`retrieve` の結果データ契約
- `agentcore.interaction`
  - interaction の検証、正規化、期限判定、回答競合の裁定
- `agentcore.nodecontract`
  - 公開 kind、planner が生成可能な kind、各 kind の結果検証

JSON Schema を実行時に解釈する外部依存は追加しない。公開契約は schema、実行時の単一実装は agentcore とし、両者の enum と制約が一致することを golden test で保証する。

### 5.2 interaction request

```json
{
  "version": 1,
  "interaction_id": "ix-6e8f4a1d2c3b4a50",
  "run_id": "run-id",
  "node_id": "node-id",
  "mode": "approval",
  "prompt": "この内容で次へ進めてよいですか",
  "audience": ["reviewer"],
  "options": [],
  "default_option": null,
  "created_at": "2026-08-10T00:00:00Z",
  "expires_at": "2026-08-17T00:00:00Z"
}
```

`mode` は次の 3 種だけとする。

- `approval`: approve / reject と任意コメント
- `choice`: 定義済み option id の選択と任意コメント
- `input`: 自由入力

`audience` は `reviewer` や `owner` 等のグループラベルであり ACL ではない。repository / transport に書き込めることが実際の回答権限となる。個人指名は行わない。

`interaction_id` は run id と node id から生成する安全な決定的 ID とし、パス文字を含めない。

期限は必須かつ有限で、既定は 7 日。0 や負数による無期限は human では拒否する。request / response の serialized JSON は各 64 KiB を上限とし、超過時は切り詰めず検証エラーにする。秘密情報を入力しない注意を UI に表示する。

### 5.3 response と resolution

回答者は immutable な response ファイルだけを追加する。`actor` は監査表示用であり、認可根拠にはしない。

```json
{
  "version": 1,
  "interaction_id": "ix-6e8f4a1d2c3b4a50",
  "response_id": "01J...",
  "actor": "dashboard-user",
  "answer": {"decision": "approved", "comment": "確認済み"},
  "submitted_at": "2026-08-10T01:00:00Z"
}
```

エンジンだけが authoritative な `resolution.json` を作る。一度作られた resolution は置換しない。同じ監視スナップショットに複数の有効回答がある場合は `response_id` の辞書順で勝者を決める。既に resolution がある場合、後着回答は結果を変えず監査対象にだけ残る。

### 5.4 配置

```text
runs/<run-id>/
├── interactions/<interaction-id>/
│   ├── request.json
│   ├── responses/<response-id>.json
│   └── resolution.json
├── waits/<node-id>.json
└── results/<node-id>.json
```

interaction は判断材料と回答、`waits/` はノード状態の正典である。別の waiting 状態は作らない。

## 6. human の状態遷移

```text
pending
  └─ request 作成 + park ─→ waiting
       ├─ approve / choice / input ─→ resolution ─→ done
       ├─ reject ───────────────────→ resolution ─→ failed
       ├─ timeout + choice default ─→ resolution(defaulted) ─→ done
       └─ timeout ──────────────────→ resolution(expired) ─→ failed
```

1. worker が human ノードを claim したら、LLM を呼ばず request を作る。
2. 既存の `park_node` で wait を先に書き、claim を解放する。
3. `service_waits` は human wait を agentcore の resolver へ渡す。
4. resolver が resolution を原子的に書いた後、既存の result を書いて wait を消す。
5. resolution 後・result 前にクラッシュしても、再起動時に resolution から同じ result を再生成する。

既存 `service_waits` は executor plugin の `poll()` が無いと早期 return するため、human wait の処理をその前に置く。GitLab wait の規則は変更しない。

human の reject / expired は自動再試行・自動再計画の対象外とする。継続処理では `kind == "human"` の failed node を明示的に除外する。回答内容による条件付き分岐は v1 では持たず、決着 data を後続ノードが読む。

## 7. extract / retrieve の結果契約

### 7.1 extract

```json
{
  "records": [
    {
      "fields": {"name": "example"},
      "evidence": [
        {"source_id": "input-1", "locator": "line:10", "excerpt": "..."}
      ]
    }
  ],
  "warnings": []
}
```

- 与えられた入力から構造化する。
- 各 record は 1 件以上の evidence を必要とする。
- records が空であることは有効な否定結果とする。
- JSON 契約で実行し、不正なら形式修復を 1 回だけ行う。
- 修復後も不正なら node を failed にする。散文だけで done にしない。

### 7.2 retrieve

```json
{
  "sources": [
    {
      "id": "source-1",
      "uri": "repo://path/to/file",
      "title": "設定定義",
      "locator": "line:42",
      "excerpt": "...",
      "digest": "sha256:..."
    }
  ],
  "warnings": []
}
```

- 読み取り可能な道具を使い、根拠を取得する。
- sources が空であることは有効な否定結果とする。
- 現在の CLI 定義では `ollama-read` に read tool、`ollama-json` に JSON grammar が分かれているため、JSON-only variant を強制しない。
- read-capable CLI の末尾構造化データを検証し、不正なら形式修復を 1 回だけ行う。
- `agentic-search` は retrieve の契約ではなく、retrieve を実装する method / skill として利用できる。

## 8. planner とユーザー定義フロー

- 自動 planner が生成できる kind と、ユーザー定義フローが受理できる kind を分ける。
- `extract` / `retrieve` は自動 planner で利用できる。
- `human` は明示的なユーザー定義フローでのみ受理し、自動 planner は生成しない。
- `plan_strategy_user` は human の `interaction` を保持し、mode、期限、options、default の整合を検証する。
- dashboard の保存形式では human だけ `tier` と `agent` を不要にする。
- `planFromWorkflow` は human に対して profile 解決を行わない。

これにより「機械が判断できることまで人へ聞かない」という C3 を守りつつ、明示された human-in-the-loop は standalone flow でも実行できる。

## 9. dashboard と agent-project

### 9.1 ワークフロー編集

human ノードの工程設定では、mode、prompt、audience、timeout、options、choice の default を表示する。段、エージェント、モデル、実行手法は隠す。

extract / retrieve は通常の段・エージェント設定を使い、種別固有の目的と結果契約を説明する。新しい雛形カードは増やさず、既存パターンや手動編集で追加する。

ロール表示は次の原則へ修正する。

- `human` → 人間
- `verify` → 検証
- その他の編集可能な実行 kind → 作業
- planner / evaluator / session → hidden system phase

### 9.2 要対応

dashboard は project の `needs/` と flow の unresolved interaction を同じ要対応一覧へ投影できる。ただし UI 内で source と回答契約を保持し、回答は必ず発生元へ書く。

- project need の回答 → 既存 `needs/` / `commands/`
- flow interaction の回答 → `interactions/.../responses/`

dashboard は resolution や flow result を書かない。

### 9.3 決定記録への投影

agent-project 管理下の run では、resolved interaction を `decisions/` へ学習材料として投影してよい。投影には interaction id と resolution digest を含め、元 resolution と一致することを契約テストで確認する。

この投影は flow の状態を駆動しない。standalone agent-flow では archived run 内の interaction / resolution が監査記録になる。

## 10. エラーハンドリング

- 不正 response は resolution に採用せず、検証エラーとして表示する。
- reject / expired は fail-close し、自動承認しない。
- choice の timeout default は明示設定時だけ使う。
- resolution は immutable、result は resolution から冪等生成する。
- extract / retrieve の形式修復は 1 回だけで、意味的な再作成ループにしない。
- extract / retrieve の空配列は有効。空でよいかの業務判断は後続 verify / judge が行う。
- human input、excerpt、URI は共有バスへ同期されるため、秘密を載せない注意と有限サイズ検証を置く。

## 11. 整合性監査

| 既存仕様との衝突 | 解消 |
|---|---|
| agent-project の `needs/` と human が二重の人間契約になる | project と flow のスコープを分け、UI 投影だけを統合する |
| 人の承認で done が成立すると C5 に反する | human node の完了と project の品質検証を分離し、検証は省略しない |
| waiting の状態源が増える | 状態は既存 `waits/` のみ。interaction は材料と回答 |
| 複数 PC で回答順が曖昧 | resolution は単一監視者が作り、同一 snapshot の競合は response_id で裁定 |
| human に tier / model が必要になる | dashboard と user-plan validator の両方で human を免除 |
| retrieve の read tool と JSON grammar が同居しない | read-capable CLI + trailing structured data + validator を使う |
| failed node の汎用 retry が human を再質問する | continuation で human failed を除外 |
| classify / judge が planner / evaluator と表示される | 編集 kind は worker として表示し、システムロールと分離 |
| `VALID_KINDS` が複数箇所に重複する | agentcore の実装 + schema 契約 + golden test で同期する |
| agent-audit の `extract` と名前が衝突する | engine scoped の command / kind であり意味も互換。共通実装を強制しない |
| agent-loop / amigos にも新 kind が必要に見える | family は契約共有を意味し、全エンジンへのノード機構強制ではない |

## 12. テスト方針

1. schema と agentcore の kind / mode / outcome が一致する golden test
2. request / response / resolution の正常・不正・期限切れ検証
3. 同時回答、後着回答、resolution 後クラッシュの冪等性
4. approval reject、expired、choice default の状態遷移
5. human failed が continuation で再試行されないこと
6. extract / retrieve の正常出力、空結果、根拠欠落、1 回修復、修復失敗
7. user-plan が human interaction を保持し、自動 planner が human を生成しないこと
8. dashboard が human の tier を要求せず、回答を response としてだけ書くこと
9. UI の kind / role 対応と公開 kind 一覧が engine と一致すること
10. managed run の decision projection digest が resolution と一致すること

## 13. 段階導入

1. 共通 schema / agentcore と golden test
2. agent-flow の kind、結果検証、human park / resolve
3. workflow editor と flow interaction 回答 UI
4. agent-project の任意 decision projection
5. 正典文書・README の同期と codd-gate 差分検査

1〜3 を v1 の完了条件とする。4 は学習用途であり flow 実行を阻害しないため、同一変更系列の後段へ置く。

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-08-10 |
| 決定者 | チーム |
| 採用案 | 共通スキーマ + agentcore の型付き契約を使い、各エンジンは adapter に限定する |
| 却下案 | 汎用 work / method / wait だけで表現（出力・UI が自然文依存になるため）、エンジンごとの個別実装（契約と状態遷移が重複するため） |
| 主な理由 | standalone agent-flow と managed project の両方で同じ human interaction を使え、既存 park & poll と single-writer 原則を再利用できる |
| トレードオフ | schema、agentcore、flow、dashboard の同時更新が必要。human は v1 では自動計画・条件分岐・個人指名を持たない |
| 再評価条件 | 条件付き DAG が実運用で必要になったとき、repository access では不足する認可要件が生じたとき、複数 transport で回答順の強い保証が必要になったとき |
