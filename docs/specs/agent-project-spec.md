# agent-project 利用ガイド兼 CLI 仕様

agent-project は、プロジェクトのバックログを取り込み、実行、検証、完了まで進める常駐ランナーです。実作業は agent-flow に渡し、検証を通ったタスクだけを `done` にします。

本書の前半は導入と日常操作、後半は状態、設定、ファイル形式のリファレンスです。設計判断の背景は[設計書](../designs/agent-project-design.md)、より細かな運用例は [`tools/agent-project/README.md`](../../tools/agent-project/README.md) と [`GUIDE.md`](../../tools/agent-project/GUIDE.md) を参照してください。

対象は `tools/agent-project/` の `agent_project` パッケージです。

## まず動かす

### 前提とインストール

Python 3 と git が必要です。YAML 設定を使う場合だけ PyYAML も必要です。リポジトリのルートで共通インストーラを実行します。

```bash
bash tools/agent-tools/install.sh
agent-project --help
```

動作確認だけならエージェント CLI は要りません。空のディレクトリを agent-project の状態置き場にして、固定結果を返す stub で 1 タスク流します。

```bash
mkdir project-state
cd project-state

agent-project enqueue \
  --root . \
  --id demo-task \
  --status ready \
  --title "agent-project の動作確認" \
  --verify "test -d ."

agent-project run \
  --root . \
  --planner none \
  --flow-planner stub \
  --executor stub \
  --no-delivery-review

agent-project status --root .
```

`demo-task` が `done` になれば、投入、実行委譲、検証、完了記録まで動いています。

### 実際のタスクを投入する

`enqueue` には、依頼内容と機械的に判定できる検証コマンドを渡します。次の例は README の見出し追加を依頼し、`grep` で結果を検証します。

```bash
agent-project enqueue \
  --title "README に概要見出しを追加" \
  --verify 'grep -q "## 概要" README.md'
```

既定では実行前レビューが有効です。投入されたタスクが `proposed` になったら、内容を確認して承認します。

```bash
agent-project needs
agent-project approve TASK_ID --reason "依頼内容と検証方法を確認した"
agent-project run --executor agent
```

`--executor agent` は設定されたエージェント CLI を使います。対象コードのリポジトリと agent-project の状態ディレクトリを分ける構成では、`agent-project.yaml` の `verify_cwd` などで作業場所を明示してください。

### 常駐させる

1 回だけバックログを処理するなら `run`、新しい投入や承認を待ちながら動かすなら `run --watch` を使います。

```bash
agent-project run --executor agent
agent-project run --watch --executor agent
```

複数プロジェクトを 1 台で常駐させる場合は host 設定を作り、`serve` を使います。

```bash
agent-project serve --host-config ~/.agents/agent-project.host.yaml
```

host 設定の作り方とサービス登録は[単一常駐体の導入ガイド](../guides/single-resident-setup.md)を参照してください。

## 日常の操作

### 状態を見る

```bash
agent-project status
agent-project status --json
agent-project needs
agent-project runlog
```

普段見るべきものは `status` と `needs` です。`needs` には、実行前レビュー、判断待ち、検収待ちなど、人の操作が必要な項目がまとまります。

### 判断待ちを処理する

```bash
agent-project approve TASK_ID --reason "実行してよい"
agent-project hold TASK_ID --reason "仕様確認が必要"
agent-project revise TASK_ID \
  --accept "期待する結果を具体的に書く" \
  --reason "受入条件を具体化する"
agent-project resume-run TASK_ID \
  --run RUN_ID \
  --reason "阻害要因を解消した"
agent-project reject TASK_ID --reason "対象外"
```

いずれも理由を残してください。判断履歴は後から監査できる形で保存されます。検証できないまま強制的に閉じる `force-complete` は例外処理です。通常の完了操作には使いません。

### 止まったときに調べる

```bash
agent-project doctor
agent-project audit
agent-project rot
agent-project impact TASK_ID
```

`doctor` は設定と実行環境、`audit` は自律運転の準備状況、`rot` は長期間動いていないタスク、`impact` は依存関係を調べます。`run` の終了理由は `status` または run log の `reason` で確認できます。

## タスク状態の読み方

通常の流れは次の通りです。

```text
inbox -> proposed -> ready -> doing -> done
                    |         |
                    |         +-> review / blocked
                    +------------> rejected
```

- `proposed`: 実行前レビュー待ち。`approve` すると `ready` へ進む
- `review`: 成果物の検収待ち
- `blocked`: 判断または外部条件待ち
- `done`: 検証を通り、完了が確定した状態

詳しい遷移条件と例外状態は以下の CLI リファレンスに記載します。

---

## CLI リファレンス

### 1. できること

#### 1.1 正準ループ（`run` の 1 サイクル）と停止

```
 ── サイクル予算が残る間くり返す ──────────────────────────────────
 S7 収束判定   予算（サイクル数・実時間・トークン・コスト・ソフト上限）超過なら停止
 S0 取り込み   needs の返事・commands の指示・inbox のドロップ・外部 intake を、
               重複照合と charter 帰属の整合を通して取り込み、triage で inbox を ready へ
               昇格し、受入基準と任意の固定コマンドを正規化して spec 前段を前置する
 S1 選択       優先順位付け（planner）→ policy で上書き → 依存未達と report を除外
               → 先頭から concurrency 件を原子的に claim
 S2 実行       要求文と verification plan を agent-flow へ委譲（local か board）
 S3 検証       receipt の digest・revision・証跡を検算 → 回帰 → パス保護 → 進捗ゲート
 S4 判定       done（archive + 納品書）／ review（人の検収待ち）／ retry（積み直し）
 S5 送出       人へ送る前に learn 適用 → 裁定ゲート → needs/<id>.md 生成
 S6 自走       完了タスクから派生タスクを backlog へ
 ── 脱出後 ── 通知、learn の昇格、バス掃除、run-log 追記
```

S3 のゲート順と失敗時の行き先は verify → 回帰 → パス保護 → 進捗で、回帰 NG と進捗 NG は blocked（人の判断へ）、パス保護違反は review（人の検収へ）です。verify の PASS はこの列の入場条件であって、通過しても done 確定ではありません。

停止理由（`reason`）の語彙は 5 つです。

| reason | 意味 |
|---|---|
| `drained` | 消化可能タスクが尽きた（実質完了） |
| `budget` | サイクル数（`max_cycles`）か実時間（`max_seconds`）の上限 |
| `cost` | トークン（`max_tokens`）か金額（`max_cost`）の上限 |
| `throttle` | ソフト上限。`--watch` は以降 report へ降格し、実行を止めて監視だけ続ける |
| `infrastructure` | 所有権など、安全に継続できない基盤状態を確認できない |

`report` / `once` は予算トリガーではなく実行モードによる終了です。`--watch` はパス終了後もプロセスを残しますが、idle 中にエージェントは起動しません。消化できるタスク、新しい inbox、人の指示、確定したフィードバックのいずれかを FS ポーリングで検知したときだけ次のパスを起こします。

#### 1.2 タスクの status

| status | 意味 |
|---|---|
| `inbox` | 取り込み直後。triage が受入基準の有無を見て ready へ昇格する |
| `proposed` | 実行前レビュー待ち（`plan_review`・既定 on）。人の承認まで実行しない |
| `draft` | 必須項目が欠けたまま人の目へ回した（`plan_review` off のときの行き先）。消化対象外 |
| `ready` | 実行待ち（`todo` は後方互換エイリアス） |
| `doing` | このノードが claim して実行中 |
| `offloaded` | 委譲公示板へ出して実行中（claim は依頼元が握ったまま） |
| `review` | 人の検収待ち |
| `blocked` | 人の判断待ち |
| `done` | 確定。archive と納品書へ移る |
| `rejected` | 却下。墓標が積まれ、再提案が抑止される |

#### 1.3 コマンド

| コマンド | 用途 |
|---|---|
| `serve` / `status` / `worker` | 常駐体の起動・状態表示・ワーカーノード（サブコマンド省略時は `serve`） |
| `worker init` | プロジェクトを持たないワーカーノード用の `host.yaml` を生成する（`--node-id` / `--tags` / `--agent-cli` / `--board` / `--max-concurrent` / `--out` / `--force`）。PC を 1 台足すときの入口 |
| `run` | 正準ループ。charter があれば目標駆動へ入る。`--watch` で常駐 |
| `enqueue` / `triage` / `needs` / `impact` | 投入、優先順位付けのみ、判断待ちの表示、依存の影響範囲 |
| `approve` / `hold` / `reprioritize` / `revise` / `reject` / `revive` / `resume-run` | 人の操作（すべて決定記録に残る） |
| `force-complete <id> --reason …` | 進まないタスクを未検証と明示して締める（§3.1・理由は必須） |
| `mr-create` | 検収待ちタスクの MR/PR を冪等に作る（旧名 `retry-mr`）。自動作成はしない |
| `replan` / `distill-notes` / `board-offload` | charter からの再分解（`--revive` で墓標を 1 回だけ無視）、観点メモのバックログ化、板への手動委譲 |
| `stats` / `audit` / `runlog` / `doctor` / `gc` / `update` | 計測、Loop Readiness 採点、ログ、診断、掃除、自己更新 |
| `promote` / `rot` | learn の長期記憶への昇格、腐ったタスクの検出 |
| `flow-participate` / `flow-run` | 常駐体の内部配線（help には出さない） |

実行の委譲先は `--location`（既定 `auto`）で決まります。`local` は agent-flow の単発 run、`board` は委譲公示板への post（非ブロッキング）、`auto` は offload ポリシーに一致しかつ板が設定されていれば `board`、それ以外は `local` です。

`doctor --node-id-cutover <旧 node_id>` は node_id 切替の事前チェックで、板と amigos バスに加えて状態リポジトリ側の残骸（旧名義の `status/<旧>.json`、旧名義の `claim_owner` を持つ doing タスク、人が旧名義で割り当てた `- node:`）も検査します。

#### 1.4 プロジェクト層（charter 駆動）

`<root>/charter.md` があると `run` は目標駆動のモードに入り、1 パスが分解（plan）→ 消化（execute = 正準ループ）→ 評価（evaluate）の 3 段になります。

分解が走るのは人の明示要求（`replan` 指示・dashboard の分解ボタン）があったときだけです。「消化可能タスクが無い」「charter が変わった」を契機に自動では走りません。評価も未達 acceptance から改善タスクを自動では起こさず、awaiting-plan（分解待ち）として milestone で人へ返します（opt-in の敵対的レビュー所見だけは起票します）。

反復は改善サイクル上限（`max_project_cycles`・既定 5）、累計コスト上限（`max_project_cost`）、停滞（`project_stall`・acceptance の PASS 数が増えない連続回数・既定 2）で必ず止まります。

`charters/` に複数の charter を置くと同じルートで複数バージョンを並行に進められます。ルートの `charter.md` に `## master` を付けるとマスター憲章になり、分解されずに全バージョンへ継承されます。

---

### 2. 設定

#### 2.1 2 つのファイルと層の契約

| ファイル | 置き場 | 中身 |
|---|---|---|
| `~/.agents/agent-project.host.yaml` | 各 PC（同期しない） | このノードの宣言: `node_id` / `projects` / `repos` / `availability` / `budget` / `tags` / `workloads` / 板参加 / 自動更新 |
| `agent-project.yaml` | 状態リポジトリ直下（全 PC へ配られる） | プロジェクトの合意: 計画・ゲート・予算・検証・タスク運用（全ノードで同一であるべき動作） |

キーは原則どちらか一方の専有で、置き間違いはキー名を列挙する fail-fast のエラーになります。分類は 4 群です。

| 群 | 意味 | 違反時 |
|---|---|---|
| `SHARED_KEYS` | 両方に書ける（ノードごとに違ってよく、違っても実行の意味が変わらないもの）: `agent_cli` `model` `agent_timeout` `agent_escalation_max` `act_timeout` `verify_timeout` `verify_cwd` `location` `concurrency` `actor` `notify_cmd` `argv_limit` `ltm_home` `flow_config` | — |
| `HOST_ONLY_KEYS` | host.yaml 専有（`node` `node_id` `projects` `repos` `availability` `budget` `tags` `amigos_bus` `residency` `defaults` `update*` `board_workdir` ほか） | プロジェクト yaml にあれば fail-fast |
| `HOST_SOURCED_KEYS` | host.yaml だけが値を持つ（`state_repo` `state_repo_branch` `board_workdir` `update_*`） | プロジェクト yaml からは読まない |
| プロジェクト yaml 専有 | 上のどれでもない全キー（差集合で定義。新しいキーは既定でここへ落ちる＝安全側） | host.yaml にあれば fail-fast |

優先順位は CLI 引数 > host.yaml の `projects[].overrides` > host.yaml の `defaults` > プロジェクト yaml > 組み込み既定。解決結果は 1 つの `Config` が実行時の正で、free 関数も同じインスタンスを参照します。

host.yaml のトップレベルの綻び（未知キー・層違い・型違い）だけは起動時の警告どまりです。共有された 1 行でフリート全台を一斉に起動不能にするほうが害が大きいためで、doctor が題別に内訳を数えます。型の救済が 1 つあり、`tags:` / `agent_cli:` / `workloads:` にスカラを書くと 1 要素の配列として読みます（素朴に列挙すると文字列が 1 文字ずつに分解され、板へ `["c","o",…]` が publish されて永久に入札しない）。`workloads:` は語彙も検査します。

`budget.max_concurrent` だけは「未宣言」と「0」を潰しません。板の契約で `0` は無制限、キーごと省略が「既定に従う（4）」で意味が違います。実効値の解決は 1 か所で、板へ宣言する値とワーカープールへ渡す値が同じ関数から出ます。この 1 か所は管理面の宣言（agent-control の `workloads.flow.concurrency.max_runs`）を host.yaml より優先して読み、枠は巡回のたびに引き直します。

どちらの設定ファイルも秘密の置き場ではありません。フォージのトークンは環境変数に置きます（付録）。

#### 2.2 ループと予算

| キー | 既定 | 意味 |
|---|---|---|
| `concurrency` | 1 | 1 パスで同時に claim して実行するタスク数 |
| `level` | unattended | 自律度（`report` / `assisted` / `unattended`） |
| `max_cycles` / `max_seconds` | 20 / 0 | サイクル数・実時間の上限（`reason: budget`） |
| `max_tokens` / `max_cost` | 0 / 0 | トークン・金額の上限（`reason: cost`。0 で無制限） |
| `throttle` | 0 | ソフト上限。超過で report へ降格 |
| `max_retries` | 2 | 内容の失敗で積み直す上限。超えたら人へ |
| `env_resume_limit` | 2 | 同じ環境要因が連続した上限。超えたら `policy.deny` へ入れて自動再開を止める |
| `act_timeout` | 0 | agent-flow run 1 本の壁時計上限（0 = 無制限） |
| `agent_escalation_max` | 3 | 分類不能な内容失敗を上位候補へ昇格させる回数（プロセス単位）。候補は設定の `fallbacks` 宣言のうち `relative_cost` が厳密に大きい最初の 1 件 |
| `poll` / `debounce` / `pace` | 5 / 3 / 0 秒 | `--watch` の FS ポーリング間隔・入力の落ち着き待ち・パス間の間合い |
| `max_project_cycles` / `max_project_cost` / `project_stall` | 5 / 0 / 2 | charter 駆動の有限停止 |

予算の単位は支払い元と進行状況のどちらに属するかで分かれます。トークン・コスト・実時間・`budget.max_concurrent` はノードの財布なので host.yaml が正で、ノード間では合算しません。改善サイクル数・停滞の連続回数・acceptance の PASS 数はプロジェクトの進行なので `project.json` で共有します。財布の上限に達したノードだけが throttle → report へ降格し、他ノードは走り続けます。

エージェント CLI そのものの契約（`agents/<name>.json` の探索順・全フィールド・用途別の変種 `variants`・`relative_cost`・失敗トリアージのクラス）は [`docs/specs/agent-cli-spec.md`](./agent-cli-spec.md) が正典です。agent-project が `variants` へ問い合わせる用途は `plan` / `review` / `prioritize` / `route` / `adjudicate` / `assess` の 6 つで（`JSON_CONTRACT_PURPOSES`）、`verify` は寛容パーサ + 証跡の本文を伴うため対象外です。

#### 2.3 検証

| キー | 既定 | 意味 |
|---|---|---|
| `verify_timeout` | — | 固定検証コマンド 1 本の上限秒 |
| `verify_confirm` | — | フレーク耐性。PASS/FAIL を跨いだら `flaky` として人へ隔離 |
| `verify_cwd` | — | 明示の検証作業ディレクトリ |
| `verify_side_effects` | workspace | 検証エージェントへ渡す副作用の許容範囲。`workspace` = 作業ツリー内のみ / `network` = 読み取りの HTTP 到達まで。DB・外部サービスへの書き込みはどちらでも禁止（指示で伝えるだけで、機構では強制しない。§5.3） |
| `verifications_keep` | 5 | `verifications/<id>/` に残す世代数 |
| `regression_cmd` | — | 回帰検査（E2）。`verification_plan` には畳まない（§3.3） |
| `require_progress` | — | 進捗ゲート（差分ゼロの done を疑う） |
| `remote_review` | settle | フォージ側の決定的シグナルから決着する（`observe` は journal に残すだけ） |
| `delivery_review` | true | verify PASS 後は常に review（検収待ち）→ 人の承認で done 確定 |

#### 2.4 計画と spec 前段

| キー | 既定 | 意味 |
|---|---|---|
| `plan_review` | true | 実行前レビュー。status を明示しない新規投入はすべて `proposed` で入る |
| `planner` / `route_planner` | — / agent | 優先順位付けとルーティングの計画役 |
| `planner_skill` | backlog-planner | 分解のプロンプト・出力契約を供給するスキル（見つからなければ組み込みへ落ちる） |
| `plan_sections` | required | 必須項目（why / desc / acceptance / size）の欠落を 1 回再要求し、なお欠ければ人の目へ回す（`warn` は注記だけ） |
| `assess` | true | 複雑さ・リスク・曖昧さの採点 |
| `spec_threshold_full` / `spec_threshold_light` | 3 / 2 | 採点がこの値以上ならフル spec（spec / design / tasks）／ライト spec（design.md 1 枚）を前置する。`light > full` は full へ丸める |
| `repo_map` | false | `context/<repo>.md` の生成。読み出しは常時で、plan と spec の経路は設定に関わらず生成する（既存コードの文脈が無いと必須セクションが書けないため） |
| `rules_capture` | true | 効いた learn の `rules.md` への昇格 |
| `learn_threshold` / `promote_threshold` / `learn_misfire_limit` | 0.5 / 2 / 3 | learn の適用・ltm への昇格・不発での失効 |
| `auto_adjudicate` / `adjudicate_max` | — / 1 | 人へ送る前の裁定ゲート |
| `agents` | なし | 処理ごとのエージェント上書き（YAML 専用）。キーは plan / review / prioritize / route / adjudicate / verify / distill / assess / repo_map / doctor |

#### 2.5 分散と同期

| キー | 既定 | 意味 |
|---|---|---|
| `state_repo` / `state_repo_branch` | — | 状態専用リポジトリ（host.yaml の `projects[]` が唯一の置き場） |
| `state_git_interval` | 300 秒 | 同期の最短間隔 |
| `status_interval` | 0（無効） | `--watch` アイドル中の生存信号の更新間隔 |
| `controller_heartbeat_sec` / `controller_lease_sec` | 30 / 120 秒 | controller リースの心拍と窓 |
| `coordination_retries` / `clock_skew_tolerance_sec` | — | CAS の再試行と時計ずれの許容 |
| `unknown_quarantine_max` | 3 | fencing が `unknown` で隔離される上限。超えたノードは throttle → report へ降格 |
| `board` / `board_workdir` / `board_workload` | — | 委譲公示板（§3.6） |
| `default_node` | — | 割当の既定ノード |
| `gc_retention_days` | 30 | `journal.md` / `run-log.jsonl` の退避と不変コピーの保持期間 |

#### 2.6 効かないキー

プロジェクト yaml に残っていても構造上ただの無効値になるキーは、警告して無視します（`_INERT_PROJECT_KEYS`）。エラーにしないのは、このファイルが状態リポジトリ経由で全 PC に配られるため、無害な残骸で全ノードを同時に落とすほうが害が大きいからです。

| キー | 案内 |
|---|---|
| `root` / `state_repo` / `state_repo_branch` / `state_repo_dir` / `state_git` / `state_commit_interval` | 置き場が host.yaml か、同期先が状態 clone の origin に一本化された |
| `verifier` / `verifier_skill` | 内蔵の LLM verifier は撤去済み。受入基準の判定は agent-flow の専用 verifier が行い、検証スキル名は `backlog-verifier` 固定（差し替えは上位 `.github/skills/` へ同名で置く） |

一方、従うと挙動が誤って変わる廃止キー（旧 worktree 方式の `state_worktree_dir` / `state_branch` / `state_commit` / `state_push` / `state_backup_branch`）は fail-fast で止めます。黙って無視すると「バックアップされているつもりの未バックアップ状態」が続くためです。

宣言したキーが実際に効くことは構造テストで固定します（`tests/test_config_keys.py`）。3 段あり、除外にはいずれも理由の記述を強制します。

| 段 | 見るもの | 捕まえた欠落 |
|---|---|---|
| 存在検査 | `CONFIG_DEFAULTS` の全キーが `Config` のフィールドとして存在する | `remote_review`（フィールドが無く、読み手の `getattr` 既定に落ちて常に settle だった） |
| 到達検査 | 各キーを設定ファイルに書くと `Config` の値が実際に変わる | `verifier` / `verifier_skill` / `verify_side_effects`（`CONFIG_DEFAULTS` にあるだけで `Config` へ渡っていなかった） |
| 消費検査 | `Config` へ届いた先で、`configfile.py` 以外の誰かがそのキーを読む | `verify_side_effects`（存在しない charter 属性から読まれており、`network` と宣言しても制約文は 1 文字も変わらなかった。2026-08-20） |

到達検査は必要条件でしかなく、届いた値を誰も読まなければ設定は黙って無効のままです。3 段目の消費検査はその隙間を塞ぐために足しました（キー名の文字列リテラルと属性アクセスをソースから探す静的検査で、名前を動的に組み立てて読むキーは検出できません＝安全側の偽陰性）。agent-flow にも同じ検査があります（`tests/test_config.py` の `ConfigKeyConsumptionTests`）。

---

### 3. 外部との契約

#### 3.1 タスク

タスクは Markdown 1 ファイル（`backlog/<id>.md`）。id はファイル名が正です。

```markdown
## <id>: <タイトル>
- status: inbox | proposed | draft | ready | doing | offloaded | review | blocked | done | rejected
- task_acceptance_criteria: <受入基準。1 行 1 基準で複数行可>
- verification_commands: <任意の固定検証コマンド。利用者が確認方法を知っている場合だけ使う>
- verify_agent: <任意。自然文基準を判定する CLI・モデル・待ち時間。1 件だけ検証条件を変える口>
- read_allocation: <任意。最初に読むパス・範囲・理由の JSON 配列>
- priority: <整数・大きいほど高>
- after: <依存タスク id（カンマ区切り）>
- review: human            <検収を要する>
- level: report | assisted | unattended
- size: S | M | L          <規模感（分解の妥当性判断用。表示のみ）>
- no_diff: <理由>          <差分を作らない仕事。差分基準を成果物の実在と参照へ差し替える>
- why / desc / scope / out_of_scope / constraints / hints / demo   <誘導記述>
```

旧 `acceptance` / `accept` / `verify` / `verify_template` は読み取り時に上の正規形へ変換します（新規の書き込みでは使いません）。charter の `acceptance` も `project_acceptance_criteria` として読み、task と同じ criterion / receipt 契約へ渡します。`verify_template` はプロジェクト yaml の名前付き verify の展開規則で、展開後は固定検証コマンドと完全同一に扱います（展開結果がノードごとに変わる書き方は禁止）。

`- planned_title:`（生成時の原題）と `- edited: human`（人が直した印）は系が書く保護マーカーで、再分解時の重複照合と再提案の抑止に使います。

`force-complete <id> --reason …` は「done は verify のみが根拠」の唯一の例外です。verify は実行せず、成果ブランチの自動統合もせず、委譲中の run を切り離してから done を確定します。未検証であることは 3 か所に残ります: 納品書（`archive/<id>.md` の `- 検収 : FORCED` と `verify … → 未実施`）、受領書（`DELIVERY.md` の検収欄）、決定記録（`action: force-complete`）。track の実績には手戻り（`clean=False`）として記録します。

#### 3.2 要対応カード（`needs/<id>.md`）

`needs/<id>.md` は独立した真実ではなく、タスクの status（proposed / blocked / review）の投影です。毎パス `reconcile_needs` が両方向へ整合させます。票が失われていれば status から作り直し、投影元のタスクが消えていれば票も消します。マイルストーン票（`kind: milestone`）は `reconcile_milestones` が持ち、`project.json` の status を正とします。

投影の例外は 1 つで、人が既に決めた判断は作り直しません。決定記録が判断待ち以外を指しているのに手元の status が判断待ちのままなら再投影せず、同期の裁定でもリモートでの票の削除に決定記録が伴っていれば削除に従います。

検証が決着しない票（検証不能・委譲も不決着）には決着カードが付きます。出口は 4 つに固定で、増やしません。

| 出口 | 意味 | 写す先 |
|---|---|---|
| `retry` | 何で確かめるかを変えて再検証 | `revise` の `verify_agent` |
| `amend` | 受入基準を書き直して再検証 | `revise` の acceptance |
| `park` | 止めて他を進める | `hold` |
| `accept-unverified` | 未検証と明示して締める | `force-complete` |

失敗の種類もエージェントの種類もこの語彙には出てきません。それらは*材料*であって*出口*ではなく、新しい失敗が出ても増えるのは材料だけです。カードには「どの基準が決着しなかったか」「何で・どれだけ待って確かめたか（receipt の `verified_with`）」「同じ理由が何回続いたか」が載ります。判定を緩めて通す設定はありません。

#### 3.3 検証計画と receipt

実装の正典は `agentcore.verifycontract` の 1 実装で、agent-flow と共有します。schema は `schemas/verification-plan.schema.json` と `schemas/verification-receipt.schema.json`、詳細な判定規則は[agent-flow 仕様書 §3.3](./agent-flow-spec.md) にあります。

agent-project 側の責務は plan の生成と receipt の検算です。

- 自然文の受入基準（`task_acceptance_criteria`）と任意の固定検証コマンド（`verification_commands`）を `verification_plan` に正規化し、digest を付けて `--verification-plan` で渡す
- 返された receipt の plan digest・成果 revision・判定・証跡を検算する
- 書込 workspace では、検証時の target revision と、その revision が成果へ統合済みという判定も照合する
- 固定コマンドがすべて終了コード 0、全基準が証跡付き pass、最新 target を含むときだけ done 候補にする

差分の常設基準は基準リストの最後に足します。`- no_diff: <理由>` を書いたタスクでは、この基準の述語が「宣言した成果物ファイルが対象 revision に実在し、その内容を判定で参照したこと」へ差し替わります（基準そのものは消えません）。決定的な no-progress ガードも同じ宣言で外れます。

回帰（`regression_cmd`）は `verification_plan` に畳みません。グローバル検査で、パスも差分基準（`$AGENT_BASE_REV`）も git-bus ルート（workdir）を前提に書かれており、成果 repo の clone 上で走らせるとゲート自体が壊れるためです。重複実行の解消（同一コマンドの digest 畳み込み）は plan の正規化段で plan 内にだけ効きます。

receipt を採用できないタスク（receipt 欠落・検算不一致・dry-run / stub 実行）は、agent-project 自身が local runner として plan の固定コマンドを一度だけ実行します。書込 workspace の plan では同じ target 統合判定も行い、両方を同じ契約の receipt にして検算します。target を取得できなければ pass を作らず inconclusive に倒します。自然文基準の判定は agent-flow runner だけが行い、local runner では inconclusive（委譲・人送り）に倒します。

`inconclusive` は修正リトライを消費させず、まず別ノードへ検証だけを委譲し、それでも決着しなければ人へ回します。1 件だけ検証条件を変えるときはタスクの `verify_agent`（CLI・モデル・待ち時間）を `verification_plan.policy.agent` に載せます。条件は digest の一部なので古い receipt は採用されず、実際の条件と所要時間は receipt の `verified_with` に残ります。

#### 3.4 Execution Envelope（実行前レビューの凍結点）

`plan_review` が on のとき、タスクは `proposed` で入り、要対応カード（`kind: plan-review`）と一緒に Execution Envelope（`backlog/<id>.envelope.json`）が作られます。承認（`approve`）は状態遷移より先に同じ入力から `approved` 版へ置き換えます。ready になった後で組み立てると、実行開始との競合で「承認した契約」が run ごとに変わりうるためです。

```jsonc
{
  "version": 1,
  "task_id": "T1",
  "approval": {"status": "approved | proposed", "actor": "...", "reason": "..."},
  "policy_snapshot": {"control_version": 1, "control_revision": 7,
                      "valid_until": "...", "selection_policies": {...}},
  "scope": {"repositories": [...], "paths": [...], "protected": [...]},
  "acceptance": ["..."],
  "verification": { /* §3.3 の verification_plan */ },
  "candidate_permissions": {"pins": [...], "trials": [...],
                            "tier_ceiling_override": "", "retry_limit": 1},
  "external_execution": {"allowed": false, "repositories": [], "paths": [],
                         "data_classes": [], "denied_paths": [], "redaction": "required"},
  "replan_when": ["scope expansion is required", "..."],
  "approved_at": "...",
  "digest": "<sha256>"
}
```

承認済み Envelope は run meta へ最初の一度だけ転記され、完了時には納品記録と同じ stem へ移して backlog 側の sidecar を退役させます。タスク側には `- execution_envelope:` と `- execution_envelope_digest:` が残ります。

#### 3.5 決定記録と learn

`decisions/<id>.md` は append-only で、人の操作（approve / hold / revise / reject / revive / force-complete / plan-approve …）と機械の判断がすべて残ります。`- learn:` 行が横断学習の材料です。

- 同じ種類の詰まりに二度目からは自動で効き、効いた回数が閾値を超えると `rules.md`（全タスクへ常時注入）へ、さらに ltm-use の長期記憶へ昇格する
- guide の末尾に `:: scope=charter:<名前>` または `:: scope=repo:<名前>` を書くと適用先が絞られ、無印は全体に効く
- 適用の結末は出典へ `learn-worked` / `learn-misfire` として書き戻す。成功を挟まない不発が `learn_misfire_limit` 回続いた learn と、人が `learn-disable` を書いた learn は適用しない

失効専用の台帳は持たず、append-only の記録を数えるだけです。

#### 3.6 委譲公示板（board）

依頼側として板へ post し、請負ノードの agent-flow / agent-amigos が入札して実行します。板は「リポジトリ＋契約」だけで処理を持ちません（`schemas/board.schema.json`）。

- 実装の委譲では、依頼元が claim を握ったまま実行先だけを変えてタスクを `offloaded` にし、local と同じ fencing を通す
- 検証だけの委譲は成果を変更しないため claim を取らず、成果 rev ごとの `verifications/<id>/<rev>.external.json` を受理点にする
- 検証委譲の公示には `verification_plan` を載せる。請負ノードは同じ plan を専用 runner で実行し、receipt を板の result に載せて返す。依頼側は返ってきた receipt を受理点へ置き、次の settle が内蔵の検算とまったく同じ規則を通す
- receipt が返らない終端は、成功でも受理せず人へ回す（板の run が成功終端で終わったことを根拠にしていた頃は、証跡が 1 つも無い pass が done へ通っていた）
- 外部ノードの判定の受理は「誰が出したか」ではなく「何を出したか」で決める。板のノード契約版が合わない判定は fail として扱う。allowlist は持たない

入札選別の宣言（`repos` / `tags` / `agent_cli` / `workloads` / `budget.max_concurrent`）の正典は host.yaml で、判定規則そのものは `agentcore.board.eligible` の 1 実装です。板へ配るのは「他のノードが読んで意味を持つもの」だけで、担当リポジトリは url だけを載せます（手元クローンのパス `repos[].local` は載せない）。

板への書き込みはすべてプロセス間ロックの内側で行い、ロックの中から push は呼びません（同一プロセスが同じロックを二重に取ると自分自身と競合して止まる）。push は指示を取り込んだ側が外側で 1 回だけ行います。入札は冪等ですが、書かなかったことは受理レシートに残します。常に「入札しました」と返すと、押したのに板へ届いていない場合と区別が付きません。

dashboard は板へ書きません。中止・落札・手動入札はノード宛て指示ドロップ（`~/.agents/commands/`・`schemas/agent-node-command.schema.json`）として投函し、板へ書いて push するのは常駐体だけです。ノードスコープの規約が 1 つあり、猶予に掛かったファイルより後ろは、その巡回では処理しません（指示はファイル名の時刻順が処理順で、同じ公示への「入札 → 中止」を飛び越えると中止済みの板へ入札を書くことになる）。

#### 3.7 状態リポジトリのレイアウトと同期

すべてプロジェクトルート直下に平たく置きます。

```
<root>/
  charter.md            人   目標・制約・受入 verify・links
  charters/<名前>.md    人   計画バージョン（複数 charter の並行進行）
  repos.yaml|json       人＋系 リポジトリレジストリ（手書きが正。ホスト固有の local は host.yaml へ）
  policy.md             人   順位・実行先・安全ゲートの上書き
  agent-project.yaml    人   プロジェクトの合意（§2.1）
  backlog/<id>.md       人＋系 タスク本体（1 ファイル = 1 タスク）
  backlog/<id>.envelope.json 系 Execution Envelope（§3.4）
  inbox/                外部  取り込み待ちドロップ口（.json / .md）
  commands/<name>.json  外部  指示のドロップ口（CLI と同一ロジックで実行して消す）
  claims/<id>.lock      系   実行権の原子的クレーム（同期しない）
  needs/<id>.md         系→人→系 判断待ち・検収待ちの通知とフィードバック欄
  decisions/<id>.md     系   決定記録（append-only。learn / avoid の材料）
  brief/<id>.md         系   run ブリーフ（タスク内で蓄積し、完了時に納品書へ退役）
  rules.md              人＋系 プロジェクトルール（全タスクへ常時注入）
  tombstones.md         人＋系 墓標（却下タスクの再生成抑止。1 行 1 墓標・revive で解除）
  notes/*.md            人   観点メモ（明示操作があるまで plan は消費しない）
  notes/.task-links.json 人＋系 dashboard でタスク化したメモ項目とタスク番号の対応
  archive/<id>.md       系   done の保全と納品書
  DELIVERY.md           系   納品一覧（受領書）
  specs/<id>/           系   spec 前段の成果（フル: spec/design/tasks・ライト: design.md のみ）
  verifications/<id>/   系   検証レポート（<rev>.md = 検証した成果コミット /
                             <rev>.external.json = 他ノードへ委譲した検証の受理点）
  context/<repo>.md     系＋人 リポジトリ理解（repo-map）
  autonomy/<track>.json 系   track の自動昇格状態
  project.json          系   プロジェクト層の収束状態
  journal.md            系   人間可読のサイクルログ（閾値でローテーション）
  run-log.jsonl         系   構造化 run-log（run ごと 1 行）
  *-archive/            系   ローテーション退避（journal-archive / run-log-archive）
  status.json / status/ 系   生存信号（単一ファイルとノード別）
  paused.json           系   一時停止マーカー
  bus/                  系   agent-flow の run 状態
  flow-archive/         系   viewer がバスから写し取る run のスナップショット（同期しない）
```

同期は `direct` の 1 方式です。ルート自身が状態専用リポジトリの clone で、origin へ直接コミットして push します。同期しないのは 2 つだけで、ホスト局所の実行権（`claims/`）と、バスの派生物（`flow-archive/`）です。除外は新規コミット側と追跡済みファイル側の両方に効かせます。片側だけだと、一度追跡されたファイルは配られ続けます。

状態ルートに使えるのは状態専用リポジトリの clone（remote 無しなら `git init` したローカル縮退）だけで、成果物リポジトリや他リポジトリの内側を状態ルートにする構成は起動時に fail-fast で拒否します。

同時変更の裁定は向きが 2 つに分かれます。実行権は remote が正で CAS でしか動きませんが、機械が書く状態（backlog / archive / 納品書 / 検証記録）の同時変更はローカルを採ります。

保持契約は `gc` が実行します。`archive/` は保持、`verifications/<id>/` は直近 `verifications_keep` 世代（settle が参照中の rev は世代の外でも残す）、`journal.md` と `run-log.jsonl` の退避・不変コピーは `gc_retention_days` で刈ります。契約はここが正で、`gc` はその実行者にすぎません。

#### 3.8 常駐体の状態（`engine/status.json`）

`~/.agents/engine/status.json` は常駐体だけが書き、dashboard が読む唯一の入口です。dashboard のプロジェクト発見もここから行います。

| キー | 中身 |
|---|---|
| `node` / `contract_version` | ノード名義とノード契約版（dashboard は版で「更新漏れの古いノード」を出す） |
| `heartbeat` / `tick_counts` | 親の心拍と tick ごとの回数 |
| `sync_health[]` | `{name, ahead, behind, last_error}` |
| `recent_errors[]` | 直近エラーのリングバッファ（既定 50 件） |
| `children[]` | `{name, alive, quarantined, deaths, root, paused}` |
| `running_runs[]` | 実行中の run-id |
| `board` | 板への参加状況。dashboard が「この端末は板に参加しているか・手動入札できるか」を判断する唯一の根拠（未設定なら `{"configured": false}`）。`board.node_direct` はノード直轄実行の可否 |

doctor の所見も横断エラーとしてこの経路に載せます。取り込みに失敗したノード宛て指示は、理由付きの `.err` 退避に加えてここにも載ります。出ないと「押したのに効かない」の追跡が `.err` の直接閲覧に依存します。

#### 3.9 知識観測（knowledge-observation）

新しい知識ストアは作らず、既存の brief / decisions 経路へ観測 ID と provenance を additive に載せます（`schemas/knowledge-observation.schema.json`）。`build_request` の `rules.md` content hash と skill 参照は run meta の `knowledge` キーへ渡り、agent-flow は解釈せず素通しします。

- `observation_id` は内容アドレス（`obs-<hex16>`）で、同一観測の再取込は同じ ID になる＝ hit の計上が冪等
- `kind` は `injection` / `brief` / `decision` / `learn-hit` / `learn-capture`
- outcome は verification-receipt の `plan_digest` / `result_rev` 参照のみ（新しい証跡 hash は作らない）
- 生プロンプトは必須にしない（参照は rules hash・skill 名・receipt digest だけ）。linked / ltm へ渡す前に privacy と scope を検査する

---

### 4. 規約

id とファイル名: タスクの id はファイル名が正です。明示 id は冪等キーで、同じ id は同じタスクを指します。

node_id: PC の身元で、板とプロトコル上の名義です。解決順は host.yaml の宣言、環境変数、hostname の順で、宣言が最優先です。どの経路から来た名義も、使う前に 1 つの正規形（小文字化と許容文字への置換）へ倒します。非正規形を黙って直すと「指定した名前で動いていない」ことに気付けないので、変換したときは 1 行警告します。タスク側の照合（`task_runnable_here`）も同じ正規形で行います。agent-flow / agent-amigos の明示指定（`--node-id` 等）は素通しのままで、doctor が所見にして切替を促します。切り替えは静止点でしか行えません（旧名義の claim・bid・status が孤立して二重入札の温床になる）。

実行権: 正本は remote の backlog / archive にある `owner/token/generation` の 3 つ組で、その変更を fast-forward push の CAS で確定できたノードだけが取得します。リースの時刻は「奪取を試みてよい」というヒントで、失効だけでは実行権は移りません。`claims/` は同期しないホスト局所のキャッシュで、正本とずれたら毎パスの投影整合で掃除します。

settle の順序: まず archive・納品書・needs・検証記録などの versioned state を 1 コミットにまとめて push し、その同期を試みたあとにホスト局所の `claims/` を解放します。push が通った時点だけが他ノードから見た確定点です。削除したパスを `git add` の pathspec に混ぜると git は全体を失敗させるので、実在するパスだけを add に渡します（archive の追加と backlog の削除が同じコミットに入ることをテストで固定しています）。

fencing の 3 値: settle の直前に remote の正本が claim と同じ 3 つ組の doing であることを確認し、`ok` なら確定、`lost` なら成果を捨てて正本へ戻し、`unknown`（リモートに届かない）は破棄も自動採用もせず人の判断へ隔離します。隔離には自動再試行を 1 回だけ持たせ、次のパスで確かめ直します。

停止の順序: SIGTERM / SIGINT のハンドラは起動処理の何よりも先に設置し、起動バナーを「この行が出たら停止要求を取りこぼさない」という観測可能な境界にします。停止要求が入っていたら status の書き出し（git 観測を含み数秒かかりうる）は行わず、部分的に起動した状態からでもそのまま畳みます。2 度目のシグナルは既定ハンドラへ戻します。子（`run --watch`）も同型で、SIGTERM の変換は state 同期や controller リースの取得より前に設置します。graceful 停止の締めくくりは、子を畳み、claim と controller リースを解放し、板へ離席宣言（`status/<who>.json` を `away`）を push する順です。この 2 ステップは失敗しても停止自体は止めません。

親の tick 表: 周期はコード定数で、yaml では変えません。

| tick | 周期 | 内容 |
|---|---|---|
| `supervise` | 5 秒 | 子の起動・再起動・隔離・計画停止 |
| `amigos` | 5 秒 | agent-amigos の参加 1 巡 |
| `flow` | 5 秒 | プロジェクトごとの `flow-participate`（逐次） |
| `board` | 30 秒 | 板の同期、`nodes/<pc>.json` の書き出し、ノード宛て指示の取り込み |
| `gc` | 600 秒 | 掃除 |

tick 内で周期を超えうる仕事（run の実行、amigos の手番）は絶対に実行しません。それらは `NodeWorkerPool` へ投げます。親自身のハングは self-watchdog（各 tick の心拍が `period + timeout + 猶予` を超えたら自プロセスを abort）が起動系の再起動に載せます。

親が子へ渡す argv は `run --watch --project <名前>` だけです。root や状態リポジトリを親が展開して渡すと宣言の解釈が親子の 2 実装になるので、子が自分で host.yaml を読み直します（`projects[].overrides` も自然に効きます）。

---

### 5. 制約

#### 5.1 予算と上限

| 対象 | 値 | 変えられるか |
|---|---|---|
| 内容の失敗での積み直し | 2 回 | `max_retries` |
| 同じ環境要因の連続 | 2 回（超えたら `policy.deny`。人の `approve` だけが解除） | `env_resume_limit` |
| サイクル数 / 実時間 | 20 / 無制限 | `max_cycles` / `max_seconds` |
| トークン / 金額 | 無制限 | `max_tokens` / `max_cost` |
| agent-flow run 1 本 | 無制限 | `act_timeout` |
| 改善サイクル / 停滞 | 5 / 2 | `max_project_cycles` / `project_stall` |
| 内容失敗の上位候補への昇格 | 3 回（プロセス単位） | `agent_escalation_max` |
| fencing `unknown` の隔離 | 3 回（超えたら throttle → report 降格） | `unknown_quarantine_max` |
| controller リース | 120 秒（心拍 30 秒） | `controller_lease_sec` / `controller_heartbeat_sec` |
| 状態同期の間隔 | 300 秒 | `state_git_interval` |
| 検証レポートの世代 | 5 | `verifications_keep` |
| ログ退避の保持 | 30 日 | `gc_retention_days` |
| ホスト局所 claim の mtime 更新 | 600 秒（無制限 run の回収窓 1,800 秒の 1/3） | 不可 |
| claim 読取・更新の一時失敗の再確認 | 5 秒間隔で 60 秒まで | 不可 |
| 委譲 run の失踪判定 | `expired` は 10 秒連続 / `unknown` は 600 秒継続 / `terminal` は auto-heal 待機でなければ 120 秒 | 不可 |
| 板 tick の心拍 push | 内容が変われば都度、心拍だけなら 5 分に 1 回 | 不可 |

#### 5.2 保証

done は、対象 revision と検証計画に一致する receipt の PASS でしか確定しません。固定検証コマンドは終了コード 0、受入基準は証跡付き pass を要求します。投入経路もスキルも設定も敵対的レビューも、自己申告の done を作れません。唯一の例外は `force-complete` で、記録の残る明示操作 1 つに閉じています。

必ず有限回で止まります。内側は drained と予算、上位ループは改善サイクル上限と停滞検知。`--watch` でも idle 中はエージェントを起動しません。

人の policy がエージェントの提案に勝ちます。設定ファイルは既定のレイヤで、`policy.md` と決定記録の優先には介入しません。人が revise やレビュー票の承認で直したタスクには `edited: human` が付き、以後の再分解で再提案されません。人がタイトルを書き換えても、生成時の原題（`planned_title`）が指紋として残ります。

標準ライブラリだけで動きます（PyYAML は任意。無ければ JSON）。`agent_flow` は import しません。別 venv、別バージョンで動く前提です。

同じタスクを二度実行しません。実行権は fast-forward push の CAS でしか動かず、clock skew による早期奪取は競合側の push 失敗として現れます。

フォージへ到達できないとき（回線断・トークン失効）は決着しません。「見えない = 未マージ = reject」と読むと、回線が切れただけで成果が却下されます。fencing の `unknown` と同じ思想です。

#### 5.3 効かない組合せと未対応

- 検証環境の隔離はしません。証跡の再実行検算、外部検証ノードの allowlist、verifier のサンドボックスや許可コマンド列挙は持ちません。`verify_side_effects` は検証エージェントへ渡す指示で、機構では強制しません。担保は receipt の証拠確認による事後検知に置きます
- フォージ実装は GitLab と GitHub だけです。gitea / codeberg は検出して 1 回警告し、MR/PR の自動作成と決着は行いません。未対応フォージとトークン欠落のどちらでも、検収は dashboard のボタン決着が正式な契約になります
- MR/PR は自動作成しません。作るのは人が `mr-create` を押したときだけです（統合は done 確定時に機械が行います）
- charter 分解は自動起動しません。人の明示要求（`replan` / dashboard のボタン）だけが分解を走らせます
- 意図の同一性を機械スコアで決めません。機械が投入を止めるのは、現役タスクまたは墓標と正規化タイトルが完全一致したときだけです。類似どまりの候補は止めず、プランナー入力への提示と注記に留めます。差し替えた `planner_skill` が言い換えを出す余地は残ります（隠さない設計上の限界）
- イベント台帳を持ちません。事実は「誰に属するか」で分けます。人の編集はタスク本体に、墓標は専用ファイルに。`tombstones.md` はイベントログではなく「現在の墓標一覧」です
- ノードをまたぐ予算の合算はしません。同じ仕事が別 PC でも計上されるのは、別の財布を数えているためです
- 複数プロジェクトの統合ビューは持ちません。束ねた可視化は agent-dashboard が git 越しに担います
- リアルタイム性はありません。ループは秒単位ではなくタスク単位で動きます
- charter の大改訂と人編集タスクの衝突は自動では解決しません（改訂の程度を測る決定的な指標が無い）
- 物理削除は決定記録を残しません（後から「なぜ消えたか」は追えない。それでよい操作にだけ使う）。実行中（doing / offloaded）のタスクの削除は拒否します

---

### 付録

#### A. ディレクトリと環境変数

| パス | 中身 |
|---|---|
| `<root>/` | §3.7 のレイアウト |
| `~/.agents/engine/status.json` | 常駐体の状態（§3.8。dashboard が読む唯一の入口） |
| `~/.agents/agent-project.host.yaml` | このノードの宣言（§2.1） |
| `~/.agents/commands/<name>.json` | ノード宛て指示のドロップ口（板の bid / cancel / award） |
| `~/.agents/flow-node/bus/` | ノード直轄実行のバス（プロジェクトを持たない PC の取り込み先） |
| `~/.agents/control/control.json` | agent-control（Envelope の `policy_snapshot` と枠の解決が読む） |

| 環境変数 | 用途 |
|---|---|
| `AGENT_PROJECT_NODE` | node_id（host.yaml の宣言に負ける） |
| `AGENT_PROJECT_HOME` / `AGENT_PROJECT_AGENTS_HOME` / `KIRO_STATE_HOME` | 置き場の上書き |
| `AGENT_CONTROL_DIR` / `AGENT_BUDGET_DIR` / `AGENT_COMMANDS_DIR` | `~/.agents/` 配下の置き場の上書き |
| `KIRO_LTM_HOME` / `KIRO_SKILLS_HOME` / `KIRO_SKILL_REGISTRY` | 長期記憶・スキル・レジストリの置き場 |
| `KIRO_GIT_CACHE_DIR` | 共有 git キャッシュ（bare ミラー）の置き場 |
| `GITLAB_TOKEN` / `GL_TOKEN` / `GITHUB_TOKEN` / `GH_TOKEN` | フォージのトークン（設定ファイルには置かない） |
| `NOTIFY_SOCKET` / `WATCHDOG_USEC` | systemd 連携（心拍の通知先） |

#### B. テスト

テストはエージェント CLI なしで実行できます。共有の前置きは `_shared.py`、設定キーの存在・到達・消費の検査は `test_config_keys.py` にあります。

```bash
python3 -m unittest discover -s tools/agent-project/tests
```

`resident/` は通常の Python パッケージとして単体テストできます。分散処理は `test_state_git.py` と `test_coordination.py` がローカルの git リポジトリを作り、CAS、controller lease、fencing、割り当てを検証します。
