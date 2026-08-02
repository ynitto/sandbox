# 分散クレジット協調とプロジェクト知識循環 実装計画

> 作成: 2026-07-29 / 改訂: 2026-08-02（実装照合・board 非依存化）  
> 上位コンセプト: [agent-tools コンセプト正典](../designs/agent-tools-concept.md)  
> 対象課題: P1〜P5 / 柱 1・柱 2 / C1・C2・C5・C7・C8  
> 状態: 計画（本書は現行実装を置き換えず、additive に拡張する）

## 0. 2026-08-02 改訂の要点

初版を 2026-08-02 時点のコードと照合した結果と、それを受けた設計変更。

### 0.1 照合で判明した初版とのずれ

1. **統一検証（plan / receipt）が実装済みになった**（2026-07-31）。
   `schemas/verification-plan.schema.json` / `verification-receipt.schema.json` と
   `agentcore.verifycontract` が唯一実装で、receipt（`plan_digest`・`result_rev`・
   証拠付き verdict）が done 確定の唯一の根拠になっている（板経由の外部検証も
   `verifications/<task>/<rev>.external.json` で同じ契約に載る）。初版 §3.2 が
   新設しようとした「証跡 URI / hash」は作らず、receipt の digest を参照する。
2. **learn の scope・失効・outcome 追跡（W10）が実装済み**。`decisions.py` の
   `split_learn_scope` / `learn_suppressed` / `record_learn_outcome`（misfire 上限
   既定 3、`learn-worked` で復権）が learn 単位の評価・失効を既に持つ。初版
   Phase 4 の「outcome 条件」は新設ではなく、この仕組みの rule 単位への一般化になる。
3. **agent-project には board を使わないマルチノード協調層が既にある**
   （`coordination.py`）。状態リポジトリで同期される `status/<node>.json`
   （`updated_iso` + `fresh_after_sec` の鮮度契約、`availability`）、
   `allocate_distributed_tasks`（生存 active ノードへの決定的割当）、
   `claim_distributed_task`（fast-forward push CAS + fencing token）、controller
   lease。初版はこの層を棚卸しから落としており、利用枠の共有先を板に限定していた。
4. **node-budget v2 の読取・推定・state 計算が 5 箇所に重複実装されている**
   （agent-amigos `nodebudget.py`、agent-flow `agent.py`、agent-project
   `prioritize.py`、agent-loop `control.py`、kiro-loop。加えて dashboard の JS 実装）。
   C7（同じ判断の実装は 1 つ）に反する現状であり、射影を足す前に agentcore へ
   集約する（§4 Phase 1）。
5. **コンセプト正典の課題は P1〜P6**（P6 = 品質責任の集中は本計画の対象外のまま）。
   参照番号は正典に合わせる。
6. **run brief の書き手は agent-project のみ**。agent-flow は sink 結果の
   `data.constraints` / `data.notes` を返すだけで brief ファイルには触れない。
   Phase 3 の付加位置はこの実態に合わせて明記した。
7. 板の eligibility（`agentcore.board.eligible`）が見るのは tags / repos /
   agent_cli / contract_version / workload / `max_concurrent` のみで、初版 §2 の
   認識どおり利用枠・鮮度は未考慮。`fresh_after_sec` は表示・診断にしか使われて
   いない。ここは初版の計画のまま有効。

### 0.2 設計変更（本改訂の主目的）

- **クレジット共有を board 非依存にする。** 利用枠射影（node-budget-summary）の
  一次置き場を agent-project の状態リポジトリ（`status/<node>.json`）にし、
  agent-board の `nodes/<id>.json` は「同じ契約の追加マウント」とする。board を
  構成しないプロジェクトでも、複数 PC が同じ状態リポジトリを共有していれば
  利用枠を見た割当・claim 抑制が働く。board にしか置けない契約を作らない。
- **ナレッジ共有も同じチャネルに乗せる。** 知識観測 envelope も状態リポジトリ
  経由でノード間を移動する（`decisions/` / `brief/` は既に同期対象）。板には
  知識を流さない。利用枠と知識で「ノード射影契約」（§3.0）を共通化し、
  書き手・鮮度・版・redaction の規約を 1 つにする。

## 1. 目的と設計境界

個人 PC 上のエージェント CLI を実行主体のまま、利用枠をチームの仕事へ協調的に割り当てる。
同時に、各ノードで得た成功・失敗・指摘をプロジェクトの強制可能なルールへ蒸留し、適用結果から
継続的に改善する。共有 API キー、中央スケジューラ、常時稼働 SaaS は導入しない。

守る境界は次のとおり。

- **共有チャネルの最小構成は agent-project の状態リポジトリである。** agent-board は
  クロスプロジェクト分担のための任意の追加マウントであり、新たな必須依存にしない。
- agent-board は契約だけを持ち、実行も落札の中央判断も持たない。
- node-budget の所有者は各ノードであり、他ノードが上限を書き換えない。
- agent-flow は作業を実行するが、プロジェクトルールの正本を持たない。
- agent-project は知識の捕捉・再利用・昇格を担うが、決定的検査は verify / codd-gate へ委ねる。
- git へ出すのは最小化した契約と証跡で、資格情報、ローカルパス、生の会話は共有しない。

## 2. 現実装の棚卸し（2026-08-02 時点）

| 領域 | 現在使えるもの | 不足しているもの |
|---|---|---|
| ノード分担（板） | `nodes/<id>.json` の能力宣言、bid / award、claim / lease、away、単一実装の `agentcore.board.eligible` | 利用枠・鮮度を考慮した適格性と決定理由 |
| ノード分担（プロジェクト内） | `status/<node>.json` の鮮度契約、`allocate_distributed_tasks`、CAS claim + fencing、controller lease、drain | 割当・claim が利用枠を見ない（生存と availability のみ） |
| ノード予算 | `node-budget` v2 のノード内 config / append-only ledger、token 推定、workload 配分、soft/degrade/stop | 実装が 5 箇所に重複、共有可能な秘匿化射影、予約と実績の照合、CLI ベンダー残量 adapter |
| 知識捕捉 | run ブリーフ、archive、learn / avoid（scope タグ・misfire 失効・outcome 追跡 = W10）、`decisions/`（append-only） | 安定 ID・content hash・発生ノード・根拠参照を統一した envelope、重複取込の冪等化 |
| 再利用・昇格 | 類似 learn の決定的解決（Jaccard 閾値）、hit 閾値昇格、`rules.md` 常時注入、linked project、ltm-use | 複数ノード候補の同一性・競合処理、rule 単位の効果評価、失効・退役、昇格監査 |
| 強制と検証 | `rules.md` の prompt 注入、統一 verification plan / receipt（digest・証拠必須・fail-close）、codd-gate | 「注入した」ではなく「適用された」の確認、rule version 固定、機械化可能なルールの gate 化 |
| 人の操作面 | dashboard の検収・needs・board・予算管理（rebalance / calibrate） | ノード横断残量の鮮度表示、割当・落札理由、知識候補の比較と裁定 UI |

現状にも `learn → rules.md → ltm-use` の縦の経路はある。新しい知識ストアを並立させず、この経路に
provenance、評価、ライフサイクルを足す。node-budget も置き換えず、ローカル台帳から共有可能な
要約を射影する。分散協調も新設せず、`coordination.py` の既存プリミティブへ利用枠を接続する。

## 3. 追加するデータ契約

### 3.0 ノード射影契約（利用枠と知識の共通規約）

利用枠射影と知識観測は、同じ「ノード射影」の規約に従う。

- **書き手はノード自身のみ**（1 パス 1 書き手）。board の `nodes/<id>.json` と同じ
  規約で、git の構造的無競合を保つ（C7）。
- **鮮度は既存契約を再利用する**: `updated_iso` + `fresh_after_sec`。射影独自の
  時計を持たない。読み手は期限切れを fail-close で「不明」に落とす。
- **`contract_version` を持ち、additive にのみ進化する。** 版不一致・未知値は
  非適格（fail-close）。ただし人が明示した「無制限」は「未知」と区別する。
- **redaction を書き手側で強制する**: 金額、契約 ID、ユーザー名、アクセストークン、
  ローカルパス、生プロンプトを含めない。共有前検査はテストで固定する（Phase 0）。
- **置き場は 2 つ、契約は 1 つ**:
  - (a) **状態リポジトリ**（一次・必須）— board 構成の有無に関係なく機能する。
  - (b) **agent-board**（任意ミラー）— クロスプロジェクト分担時のみ。resident の
    board tick が同じ値を `nodes/<id>.json` へ転記する。

### 3.1 `node-budget-summary`（ノードが書く利用枠の射影）

正本は従来どおり各ノードの node-budget（`$AGENT_BUDGET_DIR`）で、射影は判断用の
期限付き要約にすぎない。`schemas/node-budget-summary.schema.json` に block を 1 回
定義し、`status/<node>.json`（`budget` キーとして additive に埋め込み）と板の
`nodes/<id>.json` の両方から参照する。

必須候補フィールド:

- `contract_version`, `observed_at`, `source`: `local-ledger | cli-reported | manual | unavailable`
- `capacity`: token または実行時間の `limit`, `used`, `reserved`（未知値は `null`）
- `can_accept`: 所有者ノードが計算した bool と、固定語彙の `reason_codes[]`
- workload（routine / project / flow / amigos）ごとの実効上限

他ノードはこの値を再計算しない。`can_accept` の計算は所有者側の 1 実装
（agentcore、§4 Phase 1 で 5 重複を集約したもの）だけが行う。予約は §3.2 ではなく
Phase 2 の reservation 契約で扱う。

### 3.2 `knowledge-observation`（実行が書く観測）

新しい正本を作るのではなく、run ブリーフ / archive / decisions に共通メタデータを持たせる。
移動経路は §3.0 と同じ状態リポジトリ（`decisions/` / `brief/` は既に同期対象）。

- identity: observation ID、project、task、run、発生 node、時刻
- input: `rules.md` の content hash、使用 skill 名と version、関連 learn の参照
  （現状 learn の同一性は「発生元 decisions ファイル stem」しかないため、安定 ID を導入する）
- outcome: verify verdict と **receipt の `plan_digest` / `result_rev` への参照**
  （証跡 hash を新設しない）、差し戻し分類、rollback の有無
- candidate: 一般化した guidance、scope（W10 の scope タグ語彙を再利用）、
  applicability、expiry、provenance ID 群
- privacy: redaction 結果と共有可否。生プロンプトを必須にしない

既存 Markdown を人が編集できる性質を残す。構造化 sidecar を導入する場合も Markdown を正として
一方向に生成し、二重書き込みにしない。取込は observation ID で冪等化し、git merge で
順序が変わっても候補集合と hit 計上が変わらないようにする（現状の `count_learn_hits` は
行出現数を数えており、重複取込に対して脆い）。

### 3.3 `project-rule` ライフサイクル

`rules.md` の各自動昇格項目に安定 ID と状態を付ける。

```text
candidate → trial → active → deprecated
              └────→ rejected
active → suspended → trial | deprecated
```

- candidate: 捕捉済み、まだ全タスクへ強制しない
- trial: 適用範囲を限定し、必ず評価を採る
- active: 再現した効果と根拠を持ち、全該当タスクへ注入する
- suspended: 悪化または競合を検出し、新規適用を止める
- deprecated / rejected: 履歴は消さず、適用しない

遷移は append-only の decision で説明可能にする。自動遷移は trial までと suspension に限定し、
active への既定経路は既存の hit 閾値（`promote_threshold`）に outcome 条件を加える。
**評価・失効の原始は W10 の learn outcome（worked / misfire / suppressed）を rule 単位へ
一般化して使い、並行する第二の評価系を作らない。** セキュリティや破壊的操作に関する
ルールは人の承認を要求する。

## 4. 実装フェーズ

### Phase 0 — 観測と互換性の固定

1. 実 fixture を使い、node-budget、`status/<node>.json`、board node 宣言、decisions、
   rules 昇格の現行形式を契約テスト化。
2. `agent-project stats` / doctor に、rule 注入数、learn hit、昇格数、根拠欠落数を
   読み取り専用で追加（現状 stats は decisions の action 生集計のみで、知識ループの
   指標を一切持たない）。
3. 共有禁止項目の redaction テストを先に追加する。

**完了条件**: 挙動を変えずに基準値が取れ、旧ファイルだけでも全ツールが動く。

### Phase 1 — 分散利用枠の観測（board 非依存）

1. **node-budget 読取・推定・state 計算を agentcore へ集約し、5 箇所の重複実装を
   置換する**（C7 回復。射影の前提作業）。dashboard の JS 実装は正本と突き合わせる
   テストで拘束する。
2. `schemas/node-budget-summary.schema.json` を追加する（§3.0 / §3.1）。
3. `write_status` が集約実装から `budget` block を計算し、`status/<node>.json` へ
   additive に埋め込む。状態リポジトリ同期だけで全ピアへ届く（追加 push を生まない、
   既存 status 書込への相乗り）。
4. `allocate_distributed_tasks` と `claim_distributed_task` の適格判定に
   `can_accept` と鮮度を加える（`budget_summary.enforce: false` 既定、§6）。
   決定理由（reason_codes）を journal / dashboard に出す。
5. board 構成時のみ: resident の board tick が同じ block を `nodes/<id>.json` へ
   転記し、`agentcore.board.eligible` に `can_accept` + freshness 判定を加える。
   判定実装はプロジェクト内割当（項 4）と共通の 1 実装。
6. CLI ごとの残量取得は任意 adapter とし、取得不能時は台帳推定または
   `unavailable` に倒す。

**完了条件**: board を構成しないマルチノードプロジェクトで、アカウント画面を巡回せず
全ノードの「受けられる / 受けられない / 不明」と鮮度を一覧でき、期限切れ・枯渇ノードへ
自動割当しない。board 構成時は同じ値で自動落札も抑止される。

### Phase 2 — 予約による協調分担

1. プロジェクト内は claim と同時に、板は award と同時に予算 reservation を作成し、
   同一ノードの並行受注が残量を二重利用しないようにする。プロジェクト内の予約は
   既存の `state_transaction`（fast-forward push CAS）で作り、第二の排他機構を
   作らない。
2. agent-flow / amigos / project の開始時に予約を引き継ぎ、実績 ledger と結び付けて close する。
3. 未開始・ノード停止は lease / claim expiry で解放し、回収を冪等にする。
4. タイブレークは既存の決定性（`(load, name)` / `(ts, who)`）を維持し、残量を連続的な
   優先度にせず適格性と bucket だけに使う。

**完了条件**: 同じスナップショットを読んだノードが同じ割当・落札結果を導き、合計予約が
所有者上限を超えず、プロセス kill 後にも有限時間で再割当できる。

### Phase 3 — 知識観測 envelope

1. agent-project の `build_request` が注入時の `rules.md` content hash と skill 参照を
   run meta へ渡し、agent-flow は receipt / result にそれを素通しで返す（agent-flow に
   知識の解釈を持たせない。brief の書き手が agent-project である現実装に合わせる）。
2. agent-project が既存の brief / decisions 捕捉時に observation ID と provenance を保存する。
3. 同一 observation の再取込を ID で冪等化し、git merge で順序が変わっても候補集合と
   hit 計数を同じにする。
4. linked project / ltm へ渡す前に project scope と privacy を検査する。

**完了条件**: 別ノードの結果を発生元まで辿れ、同じ観測を二重に hit 計上せず、秘密を共有しない。

### Phase 4 — 蒸留・強制・継続改善

1. 既存 learn 昇格器に candidate / trial / active と outcome 集計を加える（W10 の
   worked / misfire / suppressed を rule 単位へ一般化）。
2. title 類似だけに依存せず、scope と applicability を先に決定的フィルタし、意味的統合は候補提示に
   留める。競合候補は自動マージせず needs へ送る。
3. active rule の hash を実行要求へ固定し、実行結果で適用版を照合する（Phase 3 項 1 の経路）。
4. 決定的に表せる rule は codd-gate recipe への提案を生成する。生成物は既存 gate のレビューと
   テストを通るまで有効化しない。
5. active 適用後に verify fail / rollback が閾値を超えたら fail-close で suspended にし、人へ
   根拠を一度だけ提示する。期限切れ候補も退役候補にする。

**完了条件**: 「共有された」ではなく「どの実行へ適用され、品質へどう効いたか」を説明でき、
悪化するルールが新規タスクへ適用され続けない。

### Phase 5 — dashboard と運用移行

1. fleet 画面にノードごとの capacity bucket、鮮度、予約、reason code を表示する。
   一次データ源は状態リポジトリの `status/<node>.json` とし、board はある場合のみ重ねる。
2. board の入札・落札詳細に、能力・利用枠・repo 担当の適格性説明を表示する。
3. knowledge 画面に provenance、適用数、PASS / fail / rollback、競合、状態遷移を集約する。
4. 人の操作は promote / suspend / revise / deprecate の契約投函だけにし、dashboard を第二の書き手にしない。
5. doctor に stale 射影、孤立 reservation、根拠なし active rule、未知 rule hash を追加する
   （状態リポジトリ・board の両置き場を対象）。

**完了条件**: 管理者は各 CLI の資格情報を預からずに分担状況を把握でき、ルール管理者は根拠を
別画面へ探しに行かず一度で裁定できる。

## 5. テスト戦略

- **契約テスト**: 新旧 schema、未知フィールドの additive 互換、版不一致の fail-close。
  `status/<node>.json` の `budget` block を知らない旧 viewer が壊れないこと。
- **単一実装テスト**: agentcore の budget 集約実装と dashboard JS 実装の突き合わせ
  （C7。同一 fixture で同一出力）。
- **決定性テスト**: git 取込順、時計差、同票、再実行が変わっても割当 / eligible / award /
  candidate 集合が同じ。
- **故障注入**: publish 中断、期限切れ、ノード停止、予約後未開始、重複 observation、merge conflict、
  状態リポジトリ不通（`claim_fence_state` の unknown 隔離と同様、不通を枯渇と同一視しない）。
- **予算不変条件**: `used + live reservations <= limit`（無制限・未知を別値として扱う）。
- **知識不変条件**: active は provenance と成功 outcome を持つ、suspended は新規要求へ入らない、
  rule hash 不一致は成功扱いしない。
- **プライバシーテスト**: token、ホームディレクトリ、リモート URL の credential、生プロンプトを fixture に
  混ぜ、共有ファイルへ出ないことを確認する。
- **E2E（第一形態: board なし）**: 同一状態リポジトリを共有する 3 ノード（余裕あり / 枯渇 / stale）で
  割当 → 実行 → 観測共有 → trial → active → 反証 → suspended を通す。
- **E2E（第二形態: board あり）**: 同構成に板を足し、落札抑止と `nodes/<id>.json` ミラーの
  一致を確認する。外部 API は fake adapter とローカル bare git で代替する。

## 6. 移行とロールバック

1. すべての新フィールドは optional で開始し、reader → shadow writer → 観測 UI → enforcement の順に出す。
2. Phase 1 は `budget_summary.enforce: false` を既定にし、観測値と従来割当・落札の差を監査してから
   有効化する。
3. 知識ライフサイクル導入時、既存の人手 `rules.md` は `active/manual` として保持し、自動退役しない。
4. 新 envelope を読めない旧ノードは contract version で識別し、混在期間は新契約必須の仕事だけを
   fail-close にする。
5. 機能 flag を戻せば従来の node-budget、割当、board、learn 昇格へ戻れる。append-only の
   reservation / decision / observation は削除せず、reader が無視する。

## 7. 非目標と見送り

- 組織の請求 API を一元管理すること、契約枠をノード間で技術的に移転すること。
- 最安モデル選択や金額最適化を中央で行うこと。一次指標は所有者が宣言した利用可能性である。
- 生の会話ログを全社ナレッジとして収集すること。
- LLM が生成したルールを無検証で active や codd-gate にすること。
- agent-board を利用枠・知識共有の必須依存にすること（board はクロスプロジェクト分担の
  追加マウントに留める）。
- スキル配布基盤を置き換えること。スキルの同期・系譜は node-federation に残し、本計画は
  「プロジェクトがいつ使うか」と「使った結果」を接続する。

## 8. 実装順と成果物

| 順 | 主な成果物 | 依存 | 解決する課題 |
|---|---|---|---|
| 0 | 現行 fixture、stats、privacy guard | なし | P4・P5 の基準線 |
| 1 | budget 集約（C7 回復）/ summary schema / status 埋め込み / 割当・eligible 接続 | 0 | P1・P2 |
| 2 | reservation（CAS / award 両経路）/ workload 接続 | 1 | P1 |
| 3 | observation envelope / provenance / rule hash 素通し | 0 | P4・P5 |
| 4 | rule lifecycle / enforcement / suspension | 3 | P3・P4・P5 |
| 5 | dashboard / doctor / 移行完了 | 1〜4 | P1〜P5 の運用 |

Phase 1 と Phase 3 は並行可能だが、各 phase 内は schema と reader を writer より先に実装する。
各 PR は上位コンセプトの課題 ID と原則を明記し、中央調整主体や第二の正本を増やしていないことを
レビューする。
