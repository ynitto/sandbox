# agent-flow 利用ガイド兼 CLI 仕様

`agent-flow` は、1 件の要求をタスクグラフへ分け、複数の worker で実行し、検証と作り直しまで
進めるワークフローエンジンである。状態はバス上の JSON ファイルに残るため、同じ run ID から
再開できる。共有 git リポジトリをバスにすれば複数 PC でも動く。

前半は単発実行と監視の手順、後半はグラフ、バス、設定、終了条件の契約を扱う。設計判断は
[設計書](../designs/agent-flow-design.md)に分けてある。

## まず動かす

### 前提とインストール

- Python 3.11 以上
- YAML 設定を使う場合は PyYAML
- git バスや書き込み先リポジトリを使う場合は git
- 実運用では `agents/*.json` に定義されたエージェント CLI

リポジトリのルートでインストールする。

```bash
bash tools/agent-tools/install.sh
export PATH="$HOME/.local/bin:$PATH"
```

`agent-flow` だけを入れ直す場合は `--only agent-flow` を付ける。

### CLI なしでプロトコルを確認する

stub planner と stub executor は LLM を呼ばない。最初はこの組み合わせで、バスと run の
ライフサイクルを確認する。

```bash
mkdir -p .agents/flow-bus
agent-flow --bus .agents/flow-bus run \
  "setup -> build -> test; write docs" \
  --workers 2 \
  --planner stub \
  --executor stub \
  --poll 0.2
```

標準出力に run ID と進捗が出る。終了後に一覧と結果を確認する。

```bash
agent-flow --bus .agents/flow-bus status --list
agent-flow --bus .agents/flow-bus result --json
```

`result` が最終結果を返せば、計画、worker、終端処理まで動いている。

## 作業別の使い方

### エージェント CLI で1件実行する

```bash
agent-flow --agent-cli codex run \
  "認証エラーの原因を調べ、修正案と検証結果をまとめて" \
  --workers 3
```

書き込み先を省略した run は読み取り専用。リポジトリを変更する場合は `--workspace` を指定する。

```bash
agent-flow \
  --agent-cli codex \
  --workspace /path/to/YOUR_REPOSITORY \
  run "README の導入手順を直し、リンクを検証して"
```

agent-flow は作業用 clone と `af/RUN_ID` ブランチを作り、変更があれば commit と push を行う。
push できない run は完了にならない。

### 実行中の run を監視する

```bash
agent-flow --bus .agents/flow-bus --run-id YOUR_RUN_ID status
agent-flow --bus .agents/flow-bus --run-id YOUR_RUN_ID status --follow --until-done
agent-flow --bus .agents/flow-bus --run-id YOUR_RUN_ID result --json
```

`status` は進捗、`result` は成果を返す。`phase` は planning、executing、evaluating、verifying、
finalizing のいずれか。終端 `status` は done、failed、cancelled のいずれかになる。

### 中断した run を再開する

```bash
agent-flow --bus .agents/flow-bus --run-id YOUR_RUN_ID run
```

要求を省略すると、保存済みの計画と結果を読み、未完了ノードから続ける。完了済みノードは
再実行しない。

### グラフを自分で固定する

```bash
agent-flow run "YOUR_REQUEST" --plan-file YOUR_PLAN.json
```

`--plan-file` の `nodes` を検証して、そのまま実行する。不正な plan を自動 planner へ
切り替える処理は行わず、run を failed にする。

### inbox の要求を受理する

`participate` は受理と回収を 1 巡だけ行い、実行すべき run ID を返す。run 自体は起動しない。

```bash
agent-flow --bus .agents/flow-bus participate --json
agent-flow --bus .agents/flow-bus --run-id YOUR_RUN_ID run --from-inbox
```

通常は `agent-project serve` がこの2段を周期実行する。手動実行は診断や復旧に使う。

### 複数 PC で分散する

```bash
agent-flow \
  --git git@example.com:team/flow-bus.git \
  --git-branch agent-flow-bus \
  run "YOUR_REQUEST" \
  --workers 3
```

各 PC は同じ `--git` と branch を指定する。claim の勝者だけがノードを実行する。成果物の
リポジトリとバスは分ける。

### 停止、診断、掃除を行う

```bash
agent-flow --bus .agents/flow-bus cancel YOUR_RUN_ID --reason "要件変更"
agent-flow --bus .agents/flow-bus doctor
agent-flow --bus .agents/flow-bus gc --dry-run
```

`cancel` は run を cancelled に確定する。`gc` は最初に `--dry-run` で対象を確認する。
環境や設定を安全に修復する場合だけ `doctor --fix` を使う。

## コマンドの選び方

| コマンド | 動作 | 常駐プロセス |
|---|---|---|
| `run` | 新規 run または既存 run を完走させる | 不要 |
| `participate` | 受理、回収、孤児検出を1巡する | 不要 |
| `status` | 状態と進捗を表示する | 不要 |
| `result` | 終端した成果を表示する | 不要 |
| `orchestrate`、`work` | 内部の役割を単独起動する | 通常は直接使わない |

ここまでが通常の利用手順である。以降は run の状態、設定キー、バス上のファイル、上限を固定する。

## CLI リファレンス

### 1. できること

#### 1.1 run のライフサイクルと完了条件

要求は `inbox/<run-id>.json` に置かれ、`participate` の 1 巡が受理します。受理された run は `run --from-inbox` の 1 プロセスが完走させます。`run` は orchestrator 1 本と worker を `workers` 個（既定 2）起こし、自分は生存リースの維持・park の再確認・キャンセル検知・終端確認の待機ループに入ります。

run の現在段階は `meta.json` の `phase` で表します。グラフ進捗（作業ステップ n/N）とも終端 `status`（`running` / `done` / `failed` / `cancelled`）とも独立した表示契約です。

| phase | 意味 |
|---|---|
| `planning` | タスクグラフを計画している |
| `executing` | グラフ上のノードを実行・待機している |
| `evaluating` | 継続・再計画・修復要否を判定している |
| `verifying` | verification plan を実行している |
| `finalizing` | receipt と最終結果を確定している |

phase が無い古い run や未知の値は、status とグラフから汎用表示へ縮退します（完了率 100% なら「完了処理中」、グラフ無しなら「計画中」、それ以外は「実行中」）。

完了条件は「全ノード done」ではなく「終端の検証が緑」です。どこからも依存されていない `kind: verify` ノードの判定を `_normalize_verify` の 1 実装（構造化 `data.ok` → 本文の verify=pass/fail の順に読み、どちらも無い曖昧な出力は fail）で読み、1 つでも赤・判定不能なら run は `failed` で終端し、`meta.failure_reason` に `[verification]` タグが付きます。判定結果は `final.json` の `verification`（`state`: passed / failed / pending / none）に残ります。終端 verify を持たない run（`state: none`）の終端条件は従来どおりです。

書込先（workspace）のある run は、さらにリモートへ push できたことが完了条件に入ります（§3.8）。

#### 1.2 計画の 4 経路

| 経路 | 実体 | 使われる条件 |
|---|---|---|
| flow-planner（既定） | スキルの 3 段パイプライン（分析 → 戦略選定 → グラフ構築） | スキルが見つかるとき。名前は `planner_skill` で差し替え可 |
| agent | エージェント CLI に 1 回問い合わせ | スキルが見つからないときの縮退先 |
| stub | キーワード判定と正規表現。LLM なし | agent も解釈できないときの縮退先。`--planner stub` で明示も可 |
| ユーザー定義フロー | inbox 要求の `plan`（または `--plan-file`）のノード列 | `plan.nodes` があるとき。planner を呼ばず検証だけしてグラフを固定 |

縮退は必ずログと `strategy.reason` に理由が残ります。ユーザー定義フローだけは逆に厳格で、不正な plan は planner へフォールバックせず `[user-plan]` タグ付きで failed 終端します（§3.2）。

#### 1.3 パターンと kind

orchestrator は 7 つのパターンをカタログとして持ち、組み合わせて使います。`patterns --json` で一覧と初期グラフ形（Dashboard の選択 UI が読む正典）を出力します。

| パターン | 形 | 使いどころ |
|---|---|---|
| classify-and-act | `classify` → 結果に応じた `work` を追加 | 種別を判定して専門処理へ振り分ける |
| fan-out-and-synthesize | 並列 `work`/`generate` × N → `synthesize` | 分割して並列処理し統合する |
| adversarial-verification | `generate` → `verify`（fail なら作り直し） | 成果を批判的に検証する |
| generate-and-filter | `generate` × N → `filter` | 候補を多数出して絞り込む |
| tournament | `generate` × N → `judge` | 複数案から最良を選ぶ |
| loop-until-done | `work` → `verify` を条件達成まで反復 | テスト通過や品質達成まで繰り返す |
| map-reduce | `split` → 実行時に `map` × N を展開 → `reduce` | 件数を事前に固定せずデータ駆動で並列処理する |

kind は 13 種で、正典は `agentcore.nodecontract.VALID_KINDS` です。

| 区分 | kind | 契約 |
|---|---|---|
| 自由記述 | `work` `generate` | 本文が成果。末尾の `{"ok": ...}` だけを完了可否の envelope として読む（`{"ok": false}` は失敗扱い） |
| 判定・振り分け | `classify` `synthesize` | 本文が成果 |
| 構造化 | `split` `map` `reduce` `filter` `judge` `verify` `extract` `retrieve` | `data` に構造化成果を期待。`split` はトップレベルが JSON 配列でないと展開されない |
| 人の確認 | `human` | ユーザー定義フロー専用。planner は生成しない。`interaction` 宣言が必須で、tier も agent も readonly も付けられない |

`extract` は根拠付きの項目取り出し（`data.records[].fields` + `evidence[]`）、`retrieve` は read 系ツールで根拠を実際に読む取得（`data.sources[]`）です。planner が未知の kind を出したら `work` に丸めます。自由記述の kind では本文中の JSON 風断片を `data` に昇格させません。

`filter` / `judge` は `decision`（判定契約）を宣言できます。宣言があるとモデルは候補ごとの事実だけを抽出し、採否と最良案は `agentcore.nodecontract.decide_candidates` が機械的に決めます。宣言は `facts`（転記できる項目。型は `bool` / `int` / `string`）と `criteria`（残す条件・AND）から成り、`judge` は `tie_break`（`min` / `max`。同値は id 昇順）で順位を付けます。正典はスキーマの `$defs.decision` です。多基準の採否をモデルに訊くと、宣言していない条件を自分で足して絞り込むためこの形にしています。

宣言があるノードでは役割行を「抽出役」へ差し替え、抽出契約の依頼文を goal の末尾へ足し、プロンプトは組み込みに固定します（flow-worker スキルの古い版が「選べ」の役割行を出すと契約と食い違うため）。成果は `data` に `kept` / `undecided` / `winner` / `facts` / `decided_by: "machine"` で返ります。事実が欠けた候補は `undecided` に残り、`undecided` があるとき・`judge` で勝者が 1 つに絞れないときは `{"ok": false}` を立てて失敗終端させます。黙って合否へ倒さず、再試行・評価役・人へ返すためです。

宣言の受け口は 2 つあります。planner には filter / judge へ必ず付けるよう求め、形が壊れた宣言や filter / judge 以外への宣言は剥がして従来のモデル判定へ倒します。ユーザー定義フロー（§3.2）では同じ不正を `UserPlanError` で拒みます。人が書いた宣言を黙って無効化すると、機械判定のつもりでモデル判定が走るためです。

`work` / `generate` は処理契約 `operation` の `deliverables`（成果物スロット）を宣言できます。2 つ以上あるノードは、planner 経路（`_coerce_tasks`）で **1 スロット 1 ノードの直列**へ機械が割ります（`<id>-d1` → `<id>-d2` …・後続の依存は最後のスロットへ付け替え・差し替え宣言 `replaces` も最後のスロットにだけ残す）。各スロットは goal の末尾に「この手順で作る成果物は 1 つだけ」の定型文を持ち、`deliverables` と `scope.write` も自分のスロットだけへ絞られます。分割そのものは `agentcore.nodecontract.split_by_deliverables` が 1 実装で持ち、上限は 4 スロット（超える宣言は割りません）。

小さいモデルは成果物を 2 つ同時に渡されると片方を丸ごと落とし、再投入を何度積んでも同じ落ち方をするためです（実測: 一括 0/3・機械分割 3/3・再投入 0）。ユーザー定義フロー（§3.2）では割りません——人が描いた形は意図そのものだからです。

`kind: verify` は run 内の反復と完了条件を制御する工程で、agent-project の verification plan を判定する専用 verifier（§3.3）とは別物です。前者が task の done を主張することはできません。

#### 1.4 操作コマンド

| コマンド | 用途 |
|---|---|
| `run [要求]` | 単発実行。既存 run-id なら再開、なければ新規。`--from-inbox` で要求を inbox から読む。`--workers` のほか計画パラメータ（下記）を受ける |
| `participate` | 受理と回収の 1 巡（cancel 受理・park 再確認・孤児 run の引き継ぎ判断・板巡回・inbox 受理）。実行はせず、実行すべき run-id を返す。`--running` に自分が走らせている run-id を必ず渡す |
| `orchestrate` / `work` | 内部コマンド。`run` が起こす |
| `cancel <run-id>` | 恒久停止。`--close-issues` を付けると park 済みノードを executor の `on_cancel` フックへ渡してから止める（同梱 gitlab プラグインならイシューを閉じる） |
| `force-complete <run-id>` | 公開失敗（§3.8）で failed になった run を、手動 push を remote で検証してから復旧する。`--reason` 必須（監査イベントへ記録） |
| `status` / `result` | 進捗表示（`--follow` / `--list`） / 最終成果（`--json`） |
| `patterns` | パターンカタログの出力（`--json`） |
| `verify-plan` | 検証計画（§3.3）を digest 付き JSON で標準出力へ組み立てる読み取り専用コマンド。`--task-id`（必須）`--command` / `--criterion`（繰り返し可）`--workspace`。digest の canonical JSON を投入側（dashboard 等）に再実装させないための口 |
| `gc` / `cleanup` | 古い run と孤児 inbox の削除（既定 7 日より古い run が対象・新しい 3 件は保護） / バス外の一時ファイルの掃除 |
| `doctor` | 稼働診断。所見を env / config / program に分類（`--fix` / `--json`） |
| `update` | スキルリポジトリからの自己更新（`--check` / `--now`） |

サブコマンドを省略すると案内を出して rc=2 で終了します。裸起動を黙って常駐にすると、常駐体（`agent-project serve`）と二重に回って inbox の要求を奪い合うためです。未知の executor 名も起動前に rc=2 で断ります。

グローバル引数は run 全体の器（バス・転送・実行資源）に関わるものだけです: `--config` `--bus` `--run-id` `--git` `--git-branch` `--git-subdir` `--board` `--node-declaration` `--state-git`（`-branch` / `-subdir` / `-interval`）`--executor-dir` `--workspace` `--verification-plan` `--reference`（繰り返し可）`--agent-cli` `--lease` `--argv-limit` `--keep-clone` `--cleanup-per-node` `--no-global-instructions` `--context-file` `--knowledge-file` `--no-session-commands`。`--tier` はありません。tier は agent-control の workload 宣言から読みます（§2.5）。これに加えて `--execution-overrides` が 1 つありますが `--help` には出しません。親（`run`）が解決済みの L4 固定を子（`orchestrate`）へ argv で運ぶための内部の口で、人が書く引数ではありません（人が渡す経路は inbox 要求の `execution_overrides`＝§3.1）。

計画パラメータは `run` / `orchestrate`（＝実際に計画するサブコマンド）の引数で、グローバルではありません。計画しないサブコマンドに書くと usage エラー（rc=2）で断ります。グローバルに置いていた頃は `agent-flow --granularity finest doctor` のような指定を受理して黙って捨てていました。`run` と `orchestrate` は同じ定義を共有するので、同じ名前・同じ既定・同じ choices です。`--help` では 2 群に分かれます:

| 群 | 引数 | 決まるタイミング |
|---|---|---|
| 計画（形と分け方） | `--planner` `--pattern` `--plan-file` `--granularity` `--review` / `--no-review` `--plan-gate` / `--no-plan-gate` / `--plan-gate-timeout` | 計画時 |
| 動的 fan-out（split → map → reduce） | `--split-policy` `--max-fanout` `--exemplar-first` | 実行中（split の出力で展開数が決まる） |

いずれも設定ファイルの同名キー（snake_case）と同義で、CLI 指定が優先します（§2.1）。子プロセス（orchestrator）へは親が解決済みの値を argv で運びます。子の cwd が親と同じ設定ファイルを見つけられるとは限らないためです。ワーカー（`work`）には渡しません（計画しないので意味がない）。

---

### 2. 設定

#### 2.1 ファイルの場所と優先順位

ファイル名は `agent-flow.{yaml,yml,json}`。探索順は `--config` の明示指定、カレントディレクトリ直下、`./.agents/`、`./.agent/`（旧ホーム互換。cwd が `~` のときは見ない）、`~/.agents/` の順で、最初に見つかった 1 つだけを読みます。優先順位は CLI 引数 > inbox 要求（§3.1）> 設定ファイル > 組み込み既定。PyYAML がなければ JSON で同じキーが使えます。旧キー `kiro_timeout` は `agent_timeout` の別名として受理します。

キーと既定値の正典は `CONFIG_DEFAULTS`（`agent_flow/config.py`）、注釈つきの実例は `tools/agent-flow/agent-flow.yaml.example` です。以下は主なキーです。

#### 2.2 バスと分散

| キー | 既定 | 意味 |
|---|---|---|
| `bus` | `./bus` | バスのディレクトリ |
| `git` / `git_branch` / `git_subdir` | なし / main / なし | バスを共有 git リポジトリにする（GitBus） |
| `node_id` | ホスト名由来 | このノードの名義 |
| `state_git`（`-branch` / `-subdir` / `-interval`） | なし / main / agent-flow / 300 秒 | 状態の鏡の共有先（§3.6）。`--git` 併用時は無効 |
| `lease` | 1800 秒 | claim のリース窓。inbox 受理では拾い直しの猶予になる（run の生存リース窓は別で `max(poll×10, 120)` 秒） |
| `poll` | 2 秒 | 待機ループの間隔 |
| `board`（`board_workdir` / `board_branch` / `board_lease`） | なし / なし / main / 900 秒 | 委譲公示板（§3.5） |
| `board_repos` / `board_tags` / `board_agent_cli` | なし | 入札選別の宣言の明示上書き。正典は `agent-project.host.yaml` |
| `node_declaration` | なし | 宣言ファイルの明示指定（プロジェクトを持たない PC 用） |

#### 2.3 計画と実行

| キー | 既定 | 意味 |
|---|---|---|
| `planner` | flow-planner | 計画役（flow-planner / agent / stub） |
| `planner_skill` / `worker_skill` | flow-planner / flow-worker | 使うスキル名 |
| `pattern` | なし | 標準パターンの明示選択（L1 形）。空なら planner が選ぶ。不正な名前は起動前に断る |
| `granularity` | auto | ノード分解の粒度（auto / coarse / fine / finest）。auto は複雑度から導出 |
| `split_policy` | behavior | 分割の単位（behavior = 振る舞いを 1 ノードが縦に持つ / file = ファイル境界の水平分割） |
| `agent_cli` | kiro | 既定のエージェント CLI |
| `model` | 定義の既定 | 既定モデル |
| `agents` | なし | 役割別の差し替え（§2.5）。YAML 専用 |
| `executor` | agent | 実行バックエンド（agent / stub / プラグイン名） |
| `executor_dir` | なし | プラグインの追加検索先 |
| `workers` | 2 | `run` が起こす worker 数 |
| `review` | auto | fan-out 統合前の verify gate（auto / true / false）。auto は集約系パターンのときだけ有効。tier=basic では常時有効へ倒れる |
| `exemplar_first` | false | fan-out を見本先行にする（先頭 1 件 + 検証ゲートが通ってから残りを展開） |
| `plan_gate` / `plan_gate_timeout` | false / なし | 計画承認ゲート（人の承認まで実行しない） |
| `max_iterations` | 3 | 評価と再計画の反復上限 |
| `max_fanout` | 50 | データ駆動 fan-out の展開上限。超過は切り捨てを記録して先頭だけ処理 |
| `reduce_width` | 8 | 集約木の幅。map がこれを超えると中間 reduce を積む |
| `agent_timeout` | なし（600 秒） | エージェント CLI 1 回の上限。`0` だけが無制限の口 |
| `argv_limit` | 100000 | argv に載せる文脈の上限バイト。超過分は一時ファイルへ退避して参照渡し |
| `stub_sleep_max` | なし（5 秒） | stub executor の擬似実行時間の上限。テストでは 0 |

#### 2.4 失敗と回復・掃除・更新

| キー | 既定 | 意味 |
|---|---|---|
| `transient_retries` / `transient_backoff` | 2 / 5 秒 | レイヤ 1: transient の in-place 再試行（指数バックオフ + ジッタ） |
| `format_retries` | 1 | レイヤ 2: 出力契約違反の言い直し |
| `max_retries` | 3 | レイヤ 3: 再計画のサーキットブレーカー（系統ごと）。plan-gate 差し戻し回数の上限も兼ねる |
| `auto_heal` / `heal_backoff` / `max_heals` | true / 300 秒 / 2 | レイヤ 4: transient 起因で failed 終端した run の自動再開 |
| `heal_quota` / `quota_cooldown` | false / 3600 秒 | 利用上限による失敗も長い cooldown で回収するか |
| `max_resumes` | 3 | 孤児 run の自動再開上限（進捗があれば数え直し） |
| `max_runs` | 8 | `participate` が同時に抱える run 数。全ノードが park の run は数えない |
| `cleanup_age` / `cleanup_clone` / `cleanup_per_node` | 24 時間 / true / false | 一時ファイルと clone の掃除 |
| `update_enabled` / `update_check_interval` | true / 6 時間 | `participate` のアイドル巡回での更新確認。取り込みは切り離した子プロセス |

#### 2.5 役割別エージェントと agent-control の上書き

CLI 定義そのものの契約（探索順・フィールド・`variants` / `relative_cost` / `readonly` の意味・失敗トリアージのクラス）は [`docs/specs/agent-cli-spec.md`](./agent-cli-spec.md) が正典です。ここではエンジン側の割り当てだけを定めます。

`agents:` のキーは役割（`planner` / `evaluator` / `worker`）と個別の kind で、値は `{agent_cli, model, readonly, fallbacks}` です。解決順は agent-control の上書き、`agents[役割]`、kind なら `agents.worker`、グローバル `agent_cli` の順。planner と evaluator は明示しない限り readonly で起動します。`fallbacks` は内容失敗の初回だけ、`relative_cost` が厳密に大きい最初の候補へ昇格する宣言です（定義ファイル側のフィールドではなくエンジン側の設定です。CLI 定義が持つのは `relative_cost` だけ）。

JSON 契約の役割（planner / evaluator / filter / judge / reduce / extract）・配列契約の split・根拠を読む retrieve・検証専用チューニングを使う verify は、CLI 定義の用途別の変種（`variants`。用途キー→振り替え先の agent_cli 名）へそれぞれ起動形を振り替えます。variant は「1 つのエージェント（例 ollama）を用途で使い分ける」実体で、振り替え後のモデルも明示指定が無ければ変種自身の既定モデルへ寄せます（例: `verify` → `ollama-verify`・`gemma4:12b`）。

タイムアウトの解決順は次のとおりです。上から順に最初に見つかった値（0 以下は「次へ委ねる」）を使い、control は呼び出しごとに読み直します（すでに動いている subprocess の期限は変えません）。

1. verification plan の `policy.agent.timeout_sec`（検証の 1 タスクだけの明示指定）
2. `control.workloads.flow.agents.<用途>.timeout_sec`（用途がノード kind なら `agents.worker` へフォールバック）
3. `control.workloads.flow.timeout_sec`
4. CLI 定義 `agents/<name>.json` の `timeout`
5. 設定の `agent_timeout`
6. 環境変数 `AGENT_FLOW_TIMEOUT`（旧名 `AGENT_FLOW_KIRO_TIMEOUT`）、最後は 600 秒

flow-planner のスキル待ちだけは `max(300 秒, agent_timeout × 3)` です。固定検証コマンド、park の決着待ち、lease、poll のタイマーはこの解決順の対象外です。

tier（実行段）は `control.workloads.flow.tier` を読みます。`basic` のときは計画・評価・split のプロンプトへ細分化指示が入り、`granularity: auto` は finest へ、`review: auto` は常時有効へ倒れます。機能・役割ごとの実行可能 tier の検証は投入側（agent-dashboard の plan 生成）の仕事で、エンジンは plan の `tier` を保持・記録するだけです。

#### 2.6 オプトインの追加機能（すべて既定 off）

宣言しない環境では、これらの機能は 1 バイトもプロンプトや記録を変えません。

| キー | 既定 | 意味 |
|---|---|---|
| `repair_retry` / `repair_excerpt_bytes` | false / 4000 | 作り直しノードへ前回の出力（有界抜粋）・成果物パス・verify の指摘を渡し、全作り直しではなく差分修復を促す。同一系統 1 回だけ |
| `prompt_table` | false | doctor の稼働シグナルと worker プロンプトの依存 data のうち、均質な dict 配列を表形式へ畳んでトークンを削る（内容は不変） |
| `ci_status_command` / `ci_wait_seconds` / `ci_poll_seconds` | 空 / 0 / 30 秒 | 公開後の CI 結果の取り込み（§3.8） |
| `<executor 名>`（例 `gitlab`） | — | executor プラグイン固有の設定ブロック。JSON 化して `AGENT_FLOW_EXECUTOR_CONFIG` でプラグインへ渡す。park の再確認間隔 `watch_interval`（既定 90 秒）と `defer_waits`（既定 true）もここから読む |

同梱の `gitlab` プラグインは推奨構成ではありません（`poll_interval` 300 秒、`timeout` 7 日、`approved_timeout` 14 日、`auto_merge` true などの既定を持ち、`AGENT_FLOW_GITLAB_<KEY>` で個別上書きできます）。人の承認を挟みたいだけなら `human` ノードと `plan_gate` で足ります。

---

### 3. 外部との契約

#### 3.1 inbox 要求

`inbox/<run-id>.json` の中核キーの書き手は `Bus.submit_request()` です。計画パラメータと `execution_overrides` は投入側（dashboard 等）が同じレコードへ足します。

| キー | 型 | 意味 |
|---|---|---|
| `id` | str | 要求 id = run-id（ファイル名と同一） |
| `request` | str | 要求文 |
| `submitter` | str | 投入者名義 |
| `workspace` | dict \| null | 唯一の書込先リポジトリ spec（`{url, local, path, base, target, branch, desc}`）。null なら読み取り専用 run |
| `references` | list[dict] | 参照リポジトリ（読むだけ） |
| `readonly` | bool | true なら動的追加ノードを含む run 全体で executor の書き込み権限を禁止 |
| `submitted_at` | str (ISO) | 投入時刻。孤児 inbox の gc 年齢判定に使う |
| `inherit_from` | str | リトライ時の先行 run-id。done の成果を引き継ぐ（世代交代の判断は[設計書](../designs/agent-flow-design.md)） |
| `delegation` | dict | 委譲公示板由来の来歴 `{id, board}` |
| `verification_plan` | dict | 検証計画（§3.3）。digest 付き・依頼側確定 |
| `plan` | dict | ユーザー定義フロー（§3.2）。`plan.nodes` があるときだけ |
| `pattern` | str | 標準パターン名の明示選択（L1 形） |
| `granularity` | str | 分解の粒度（`auto` / `coarse` / `fine` / `finest`）。L2 分け方 |
| `split_policy` | str | 分割の単位（`behavior` / `file`）。L2 分け方 |
| `execution_overrides` | dict | 役割・kind ごとの実行資源の固定（L4）。`{version: 1, roles: {...}, kinds: {...}}` の各項が `{tier, agent_cli, model}`。受理した項目は `pinned` として結果に残る。未知キーは加算的互換のため黙って無視する |

`plan` と `verification_plan` は inbox が唯一の権威です。呼び出し側が argv へ転記する必要はなく、argv とバスの両方にあるときだけ CLI 引数が勝ちます。`tier` は inbox のキーではありません（agent-control の workload 宣言から読みます）。

計画パラメータ `pattern` / `granularity` / `split_policy` のキー名は設定ファイルのキー（snake_case）と同じで、値の語彙も CLI と同一です。優先順位は CLI 引数 > inbox 要求 > 設定ファイル > 組み込み既定 — 要求は run 単位の意思なのでそのノードの `agent-flow.yaml` より強く、人がその場で打った CLI 引数には負けます。キーが無い・空文字なら設定ファイルと既定に従います（投入側が「指定しない」を表現できます）。語彙外の値は起動前に断ります（rc=2）: 解決関数は未知値を既定へ丸めるため、素通しすると誤記が「指定したのに効かない run」として静かに走るからです。

`workspace` に作業ブランチと別の `target` がある run は、system node `base-sync` が先頭に入り、全 root ノードはその完了後に開始します。target がすでに作業ブランチの祖先なら no-op、進んでいれば通常 merge、競合時だけ worker に競合ファイルの編集を任せます（履歴操作は渡さない）。競合解消に失敗した run は integration failure として終端し、fetch 失敗だけが transient です。

同じ run-id の再投入では、既存 meta に `workspace` が無ければ今回の投入値で補い（既存 spec の `base` は差し替えません。世代交代が旧ブランチへ差した base を壊さないため）、`verification_plan` は最新の投入正本へ更新します。古い plan のままだと receipt が fail-close で捨てられ続けるためです。

キャンセルは `inbox/cancels/<run-id>.json` にマーカー `{id, who, reason, close_issues, requested_at}` を置きます。マーカーは外部の適用側では消さず、実行所有者が停止を確認してから消します。

#### 3.2 ユーザー定義フロー（plan）

```jsonc
{
  "name": "レビューフロー",            // 任意。strategy.plan_name へ
  "evaluate": false,                    // true のときだけ評価役の再計画に載る（既定は無効）
  "review": "auto",                     // true / false / "auto" のみ。auto は tier=basic でだけ gate が入る
  "nodes": [
    {"id": "a", "goal": "{{request}} を調査", "deps": [], "kind": "work",
     "agent": {"agent_cli": "claude", "model": "..."},   // この経路でだけ受理
     "tier": "small",                   // pinned-tier として記録。手法判定の when.tiers にも効く
     "readonly": true,                   // executor まで伝播。書き込み権限を付与しない
     "read_allocation": [...], "dependency_input": "full", "retries": 2},
    {"id": "pick", "kind": "judge", "deps": ["a"],   // decision は filter / judge だけ（§1.3）
     "goal": "候補から 1 つ選ぶ",
     "decision": {"facts": [{"name": "extra_deps", "type": "bool"},
                            {"name": "lines", "type": "int"}],
                  "criteria": [{"fact": "extra_deps", "op": "eq", "value": false}],
                  "tie_break": {"fact": "lines", "op": "min"}}},
    {"id": "ok?", "kind": "human", "deps": ["pick"],
     "interaction": {"mode": "approval", "prompt": "...", "timeout_seconds": 604800}}
  ]
}
```

`goal` の `{{request}}` は要求テキストへ置換されます。置換はエンジン側のこの 1 か所だけで、投入側は複製実装しません。

検証は厳格で、次のいずれかに当たると planner へフォールバックせず `[user-plan]` タグ付きで failed 終端します: `nodes` が空か list でない、ノード数が 64 を超える、`id` の欠落・重複、`goal` が置換後に空、`kind` が 13 種の外、`readonly` が bool でない、`retries` が数値でない、`human` への `agent` / `tier` / `readonly` 指定、`interaction` の不正、未知依存・自己依存・循環、`split` ノードへの静的依存（後段は実行時に自動生成される契約のため）、`review` が三値の外、`--pattern` との同時指定。

`readonly: true` は graph と task に保存され、worker から executor まで伝播します。組み込み `agent` executor は書き込み権限を付与せず、workspace が無い run では最初の既存ローカル参照を作業ディレクトリとして読み取ります。readonly 引数を受け取れないカスタム executor は契約を保証できないため fail-close します。

`evaluate` が無効でも、データ駆動 fan-out（`split` の機械展開）だけは通ります。評価役を使わない契約は LLM 判断の話で、LLM を通らない展開は対象外だからです。`human` ノードの決着は park と `interactions/` の append-only response で行います。

#### 3.3 検証計画と receipt

実装の正典は `agentcore.verifycontract` の 1 実装で、agent-project の local runner と共有します。schema は `schemas/verification-plan.schema.json` と `schemas/verification-receipt.schema.json` です。

plan は `{version, task_id, workspace, commands[], criteria[], integration?, policy?, digest}`。`digest` は digest 自身を除いた canonical JSON の SHA-256 で、`policy` も digest 対象です（条件を変えた検証は別の plan）。criterion id は出現順 `C1, C2, ...` が正典です。壊れた plan（digest 不一致・未知版）は実行せず receipt も書きません。

実行規則は次のとおりです。

| 事象 | 判定 |
|---|---|
| 固定コマンドの終了コード非 0 | fail（成果物の欠陥） |
| exit 127（コマンド不在）・実行場所が用意できない | inconclusive（環境の欠落。修正リトライを消費しない） |
| タイムアウト | fail（exit 124） |
| `policy.confirm` > 1 で PASS/FAIL を跨いだ | `flaky: true`。全体判定は fail |
| 自然文 criterion の verdict が読めない | fail（フェイルクローズ） |
| verifier の CLI 不在・利用上限・タイムアウト | 全基準 inconclusive |

実行場所は workspace 宣言のある run なら該当 repo の clone、無い run ならプロセスの cwd です。宣言があるのに clone を用意できなければ cwd に倒さず inconclusive にします。固定コマンドには差分基準 `$AGENT_BASE_REV` を渡します（clone では成果 HEAD、cwd では投入時に meta へ固定した `base_rev`）。同じ `plan_digest` × 同じ `result_rev` の receipt があれば再実行しません。

receipt の必須フィールドは `version` `task_id` `plan_digest` `result_rev` `verdict` `commands` `criteria`。実際に使った条件と所要時間は `verified_with` に残しますが、採否の判定には使いません。全体判定は receipt の自称 `verdict` を信じず再導出します（証跡の無い pass は fail）。version 2 では `integration.target` を固定し、target revision が成果 revision の祖先でない限り integration は pass になりません。

`fail` は同じ run の修正ループへ戻します。不合格点を列挙した `verify-fix-<n>` ノード（integration fail なら `base-sync-<n>`）を LLM なしで決定的に注入し、`max_iterations` で有界です。

#### 3.4 executor プラグイン

組み込みは `agent`（エージェント CLI へ委譲）と `stub`（LLM なしの擬似実行）で、それ以外は `<name>.py` を動的ロードします。検索順は設定 `executor_dir`、本体隣の `executors/`、git toplevel の `tools/agent-flow/executors`、`~/.agents/agent-flow/executors` です。

```python
def execute(kind, goal, dep_results, model, art_dir, dep_arts) -> (text, data)
```

追加のキーワード引数（`repo_instruction` `workspace` `references` `request` `instructions` `prompt_table` `repair` `context` `read_allocation` `agent`）は、シグネチャを調べて受け取れるプラグインにだけ渡します。どれも受け取れない古いプラグインには、指示を goal の先頭へ結合する後方互換の経路が残っています。決着していない待ちは `DeferDecision` 例外（`defer` 属性のダックタイピング）で伝え、秘密は載せません。任意フックとして `poll`（park の決着確認）と `on_cancel` を公開できます。

プラグイン固有の設定は同名のトップレベル設定ブロックを JSON 化し、環境変数 `AGENT_FLOW_EXECUTOR_CONFIG` で渡します。同梱の `gitlab` プラグイン（推奨構成ではありません。§2.6）は、各タスクを GitLab イシューにして委譲し、`status:approved` が付いてクリーンな MR があれば自動でマージしてイシューを閉じます。MR が未マージで close されたら却下（`[gitlab-reject]`）です。

再計画の判断だけは executor に委譲しません。`stub` のときだけ stub の継続ルールを使い、それ以外はローカルのエージェント CLI で判断します。

#### 3.5 委譲公示板（board）

`participate` の板巡回が読むのは `delegations/<id>/` の `post.json`（`workload == "flow"` かつ `op == "post"` のみ対象）、`bids/`、`award.json`、`status/`、終端の `result.json` / `cancelled.json` です。書くのは自分の入札、落札後の `status/<自分>.json`（dispatched・heartbeat・終端）、完了時の `result.json`（receipt をそのまま載せる）です。

入札してよいかの判定は `agentcore.board.eligible` の 1 実装で、agent-amigos と共有します。判定材料（担当リポジトリ・タグ・使える CLI・引き受ける workload・同時実行の枠）の正典は各 PC の `agent-project.host.yaml` で、設定の `board_repos` / `board_tags` / `board_agent_cli` はその明示上書きです。

判定の向きは項目で逆です。公示が要ると言う条件（タグ・CLI・契約バージョン・リポジトリ）は宣言の欠落を「無い」と読んで入札しない側に倒し（fail-close）、ノードが「これしかやらない」と言う条件（`workloads`）は欠落を「制限しない」と読みます（fail-open）。契約バージョンの照合は完全一致で、要求を載せていない公示は不問です。枠（`budget.max_concurrent`）は板の上の自分名義の非終端 `status/` を数えて判断し、`0` は無制限。巡回の頭で 1 度だけ数え、落札するたびに減らします。

自分名義の失効していない入札がある公示は、選別を問わず取り込みます。人が dashboard から手動入札した意思表示を自動の自己抑制で握り潰さないためです。落札した公示は、自ノードのローカル clone 宣言をマージしてから自分の inbox へ `submit_request` します。

プロジェクトを 1 つも持たない PC でも形は同じで、巡回がプロジェクトではなくノードのスコープで回り、取り込み先が PC に 1 つのバス（`~/.agents/flow-node/bus`）になります。その PC には agent-flow の設定ファイルが無いので、板の所在と入札選別の宣言（`--node-declaration`）だけは常駐体が argv で渡します。

#### 3.6 状態の鏡（state_git）

実行はローカルのまま、`runs/` と `inbox/` を共有リポジトリの自分の subdir へ双方向同期し、リモートの agent-dashboard が進捗を読めるようにします。run の実行と終端は state_git に依存せず、同期の失敗はログに残して続行します。

同期は前回スナップショット（manifest）基準の 3-way で、同時に変わったファイルだけを裁定します。規則は決定的で、`inbox/`（`inbox/claims/` を除く）はリモート優先、それ以外（`runs/` と `inbox/claims/`）はローカル優先です。片側の削除も伝播します。`.` 始まりと `.tmp` 系の書きかけファイルは同期しません。force push はせず、rebase 競合も同じ裁定で決着します。間隔は `state_git_interval`（既定 300 秒）、run 終端時は間隔を待たず同期します。

#### 3.7 バスのレイアウトと書き込み所有権

```
<bus>/
  inbox/<run-id>.json               要求（§3.1）
  inbox/claims/<run-id>/<who>.json  受理の claim
  inbox/cancels/<run-id>.json       キャンセルマーカー
  runs/<run-id>/
    meta.json          request・status・phase・workspace・references・readonly・instructions・
                       リース簿記（orch_lease_until / heartbeat_at）・resume_*・heal_*・
                       superseded・failure_reason・base_rev・manual_publication_recovery
    graph.json         strategy + nodes{id: {goal, deps, kind, retries?, agent?, tier?, readonly?,
                       read_allocation?, dependency_input?, interaction?}} + iteration
    tasks/<id>.json    ノード仕様
    claims/<id>/<who>.json
    waits/<id>.json    park 記録（wait_lease_until を含む。秘密は載せない）
    interactions/<iid>/  human の決着（request.json / responses/ / resolution.json）
    results/<id>.json  成果（下記）
    artifacts/<id>/    中間成果物（node-id で決定的にアドレス）
    events/<who>.jsonl 追記専用ログ
    final.json         全結果のサマリ + verification + 任意の ci
    receipt.json       統一 verify の receipt（§3.3）
    inherited/<旧run-id>.json  リトライで消した先行 run の墓標
```

| パス | 書く人 |
|---|---|
| `meta.json` / `graph.json` / `tasks/*` | orchestrator のみ（`force-complete` は例外的に meta と result を修復する） |
| `claims/<id>/<who>.json` | claim を試みる各ワーカー（ファイル名が衝突しない） |
| `results/<id>.json` | claim に勝ったワーカー、または park を決着させた監視主体 |
| `receipt.json` | orchestrator（成果確定後の専用 verifier セッション）のみ |
| `waits/<id>.json` | park したワーカーと、それを再確認する監視主体 |
| `events/<who>.jsonl` | 各ノードが自分のファイルにだけ追記 |

`node_state(id)` はファイルの有無から毎回導出します。優先順位は result（終端）、生存リース内の claim、生存リース内の wait、`tasks/` があれば pending、なければ unknown です。

`results/<id>.json` の必須フィールドは `id` `who` `status` `output` `finished_at`。値があるときだけ `node`（実行した PC）、`kind`、`agent_cli` / `model`（実行に使ったエージェントの実効解決値）、`tier` / `pinned` / `selection_reason`、`data`、`artifacts`（run ディレクトリ相対パス）、`escalation`、`methods` / `trial`、`context_allocation` / `dependency_context` を書きます。読み手が `who` の綴りや設定から再解決しなくて済むように、書き手が事実を残す契約です。

#### 3.8 公開（push）と CI の記録

書込先のある run は、リモートへの push 成功を完了条件に含めます。`workspace` は保存場所と公開先を別の値で受けます: `url` が公開先の canonical remote、`local` が worktree 作成と緊急復旧に使う手元の git top-level です。

成果ノードの `data.publication`（`delivery.publication` の形もある）が公開レコードで、`state` は次の 3 値です。

| state | 意味 |
|---|---|
| `published` | push 成功。`url` / `branch` / `commit` / `attempted_at` を持つ |
| `failed` | push 失敗。`data.error_class` は `workspace_publish`。`recovery` に手元 clone のパスと復旧 ref を持つ |
| `published-manually` | 人が手で push し、`force-complete` が remote 上で検証した |

commit 後・push 前に、agent-flow は復旧 ref `refs/agent-flow/recovery/<run-id>` を `workspace.local` に張ります。push が成功したら消し、失敗したら残します。押せなかった理由の見分けは決定的で、リモートが進んでいたことを示す語（non-fast-forward / fetch first / stale info など）を含むときだけ fetch → rebase → 再 push を最大 5 回試み、それ以外（認証切れ・権限不足・保護ブランチ・ネットワーク断）は即座に公開失敗にします。rebase で解けない失敗を rebase へ倒すと、本当の理由がログから消えるためです。

`force-complete <run-id> --reason <理由>` は、`publication.state == "failed"` のノードだけを対象に remote の該当ブランチを問い合わせ、期待 commit が remote tip の祖先であることを確かめてから `published-manually` へ書き換え、run を done へ戻します。検証なしで done にする口はありません。理由・検証時刻・remote tip は `meta.manual_publication_recovery` と監査イベントに残ります。

公開後の CI は `ci_status_command` を宣言したときだけ取り込みます（§2.6）。コマンドは標準出力へ `{"state": ..., "url": ..., "checks": [...]}` を返し、実行時に `AGENT_CI_URL` / `AGENT_CI_BRANCH` / `AGENT_CI_COMMIT` / `AGENT_CI_REPOSITORY` が環境変数で渡ります。状態は `passed` / `failed` / `running` / `unknown` の 4 値へ正規化し（GitHub の success、GitLab の failed など系統ごとの語も吸収します）、読めない出力・コマンド失敗・タイムアウトはすべて `unknown` です。緑には倒しません。結果は各ノードの `publication.ci` と `final.ci` へ書き戻します。

---

### 4. 規約

名義の綴り: バスと板に書く名義は `agentcore.protocol.safe_name` の 1 規則で揃えます。読みも同じ規則です。claim の `<who>` は `<node_id>-w<i>`（auto-heal の世代は `<node_id>-h<n>w<i>`）で、PC 名が入ります。

終端語彙: 書くのは正典の綴り（`cancelled`）だけ。読みは旧綴り（`canceled`）も終端として寛容に受けます。

作業ブランチ: 既定 `af/<run-id>`（spec で `branch` を明示すればそちら）。エージェントは編集だけを行い、commit と push は agent-flow が行います。変更がなければ push しません。commit 失敗（hook・identity 未設定・index.lock）は無視せず明示的に失敗させます。無視して push すると、エージェントの編集を含まない古い HEAD が「変更が入ったつもりの成果」として done になるためです。ステージ済みの差分には末尾空白の自動修正と競合マーカーの検査が入ります。リトライの世代交代では新 run の `base` を旧ブランチへ差し替え、確定済み commit を失いません。

park の順序: wait 記録を書いてから claim を解放します。逆にすると、その隙間で死んだときに wait を失います。park 記録の生存リースは `max(watch_interval × 3, 300)` 秒で、監視主体が消えるとノードは `pending` へ縮退します。

心拍: 生存リースは orchestrator 自身が張り、リース窓の 1/3 ごとに `meta.json` を書いて push まで行います（git バスで未コミットのまま残すと pull --rebase が失敗し続けるため）。計画・評価・検証のようにメインスレッドが長く塞がる区間も別スレッドが同じ間隔で更新します。

イベントの計画差分: 初期計画は `planned.tasks`、以後の `replan` と `inflight_amend` は理由と `changes`（`added` / `replaced` / `updated` / `removed`）を残します。`inflight_amend` は静止を待たず、決着済みノードに載った人の指摘（`data.guidance` / `notes` / 差し戻しコメント）を待機中のノードの spec だけへ決定的に反映する経路です（実行中・監視中・終端のノードは触りません。ノードの追加は静止時の評価役に委ねます）。`graph.json` の `deps` は実行上の依存、イベントの差分は計画変更の時系列で、意味が違います。利用側は混ぜて表示しません。

手法注入（tuning）: `$AGENT_TUNING_DIR/tuning.json` の `methods` / `trials` を role 別に注入します。variant を名乗るのは手法を実際に注入できた実行だけで、1 つも効かなかった実行は trial として記録しません。

---

### 5. 制約

#### 5.1 予算と上限

| 対象 | 値 | 変えられるか |
|---|---|---|
| transient の in-place 再試行 | 2 回・指数バックオフ | `transient_retries` / `transient_backoff` |
| 出力契約違反の言い直し | 1 回 | `format_retries` |
| 再計画のサーキットブレーカー | 系統ごと 3 回 | `max_retries` |
| auto-heal | 2 回（done が増えれば数え直し）・cooldown 300 秒 | `max_heals` / `heal_backoff` |
| 孤児 run の自動再開 | 3 回（進捗か生存 park があれば数え直し） | `max_resumes` |
| 評価と再計画の反復 | 3 周 | `max_iterations` |
| データ駆動 fan-out | 50 件。超過は切り捨てを 3 か所（ログ・replan 理由・reduce の goal）に記録 | `max_fanout` |
| 集約木の幅 | 8 | `reduce_width` |
| ユーザー定義フローのノード数 | 64 | 不可 |
| 並列数 | 明示 1〜8、非明示 2〜6、granularity 倍率込みで上限 16 | 不可 |
| エージェント CLI 1 回 | 600 秒（`0` のみ無制限） | §2.5 の解決順 |
| 1 呼び出しの最悪時間 | `(1 + transient_retries) × (1 + format_retries) × agent_timeout + Σbackoff`（既定で約 1 時間） | 派生値 |
| argv に載せる文脈 | 100000 バイト（超過は一時ファイル参照へ） | `argv_limit` |
| inbox claim のリース | 1800 秒 | `--lease` |
| run の生存リース窓 | `max(poll×10, 120)` 秒 | `poll` から派生 |
| park の再確認間隔 / 生存リース | 90 秒 / `max(間隔×3, 300)` 秒 | `<executor>.watch_interval` |
| workspace push の rebase 再試行 | 5 回 | 不可 |
| CI 状態の待機 / 1 回の問い合わせ | 上限 1800 秒 / 120 秒・checks は先頭 50 件 | `ci_wait_seconds`（上限は不可） |
| state_git の同期間隔 | 300 秒 | `state_git_interval` |

#### 5.2 保証

done は温存されます。確定済みノードの result・成果物・作業ブランチの commit は、駆動プロセスが消えても、auto-heal でも、世代交代（`inherit_from`）でも失われません。push できなかった commit も復旧 ref として手元に残ります。auto-heal と消費者（agent-project）の引き継ぎはどちらも冪等な操作で、最悪でも重複実行であって破壊は起きません。

二重実行の防止は claim の決定的タイブレーク（`(ts, who)` 最小）です。同じ claim 集合を見た全ノードは必ず同じ勝者を選びます。同一ホスト内の並行 claim は flock で直列化します。git バスでは pull の間隔ぶんだけ他ノードの結果が遅れて見えます。

受理から実行までに窓が 1 つあります。`participate` が要求を受理した直後は run がまだ無く、生存リースを張れません。この間に呼び出し側が落ちると、その要求は inbox claim のリース（既定 1800 秒）が失効するまで誰も拾い直しません。リースを短くすると起動待ちの run を別ノードが二重に拾うため、この窓は意図的に縮めていません。

環境要因（認証切れ・利用上限・CLI 不在・管理面による停止）はどの層でも再試行せず、run を failed で終端します。利用上限だけは `heal_quota` を立てれば長い cooldown で回収されます。

#### 5.3 効かない組合せと未対応

- 定期実行はありません。周期駆動は `agent-project serve` が持ちます
- run 内のステップは PC 間に分散しません。配る単位は run です
- 公平な負荷分配はしません。起動位相のずらしとジッタだけです
- LLM にワークフロースクリプトを生成させる実行形態はありません
- `--pattern` とユーザー定義フロー（plan）は同時に指定できません
- `human` ノードに `agent` / `tier` / `readonly` は付けられません。自動 planner は `human` を生成しません
- ユーザー定義フローの静的な形には tier 補償が届きません（動的生成部分にだけ届きます）。`classify` + route が追加する `-act` ノードの basic 向け具体化も未対応です
- planner のプロンプトが列挙する kind は 9 種で、`map` は split の展開でだけ、`extract` / `retrieve` / `human` はユーザー定義フローでだけ現れます
- 対話 CLI の差し替えは呼び出し境界でだけ効きます。動いている subprocess の期限・エージェントは変わりません
- CI の状態は取り込むだけで、赤を理由に再実行や再計画はしません。`unknown` を緑として扱う口もありません

---

### 付録: ファイル・ディレクトリと環境変数

| パス | 中身 |
|---|---|
| `<bus>/` | §3.7 のレイアウト |
| `<bus>/.state-git` | state_git の管理クローン（blob なし・sparse。壊れたら作り直す） |
| `agent-flow.yaml`（探索順は §2.1） | 設定 |
| `<workspace.local>/refs/agent-flow/recovery/<run-id>` | 公開失敗時の復旧 ref（§3.8） |
| `~/.agents/agent-flow.update.json` | 自動更新の状態 |
| `~/.agents/agent-flow/executors/` | executor プラグインの追加置き場 |
| `~/.agents/control/control.json` | agent-control（tier・timeout・エージェント上書き） |
| `~/.agents/tuning/tuning.json` | 手法注入（`$AGENT_TUNING_DIR` で変更可） |
| `agents/<name>.json` | エージェント CLI 定義（agentcore の 1 実装で解決） |
| `agent-project.host.yaml` | 板の入札選別と手元 clone 宣言の正典 |

| 環境変数 | 用途 |
|---|---|
| `AGENT_FLOW_TIMEOUT`（旧 `AGENT_FLOW_KIRO_TIMEOUT`） | エージェント CLI 1 回の上限秒 |
| `AGENT_FLOW_STUB_SLEEP_MAX` | stub の擬似実行時間の上限（テストで 0 にする） |
| `AGENT_FLOW_EXECUTOR_CONFIG` | executor プラグインへ渡す設定 JSON |
| `AGENT_FLOW_GITLAB_<KEY>` | gitlab executor 設定の個別上書き |
| `AGENT_FLOW_DEFER_WAITS` | park & poll の有効化（`1`）。監視主体が worker へ渡す |
| `AGENT_FLOW_NO_GLOBAL_INSTRUCTIONS` / `AGENT_FLOW_NO_SESSION_COMMANDS` | 子プロセスへのフラグ伝播 |
| `AGENT_BASE_REV`（別名 `KIRO_BASE_REV`） | 検証コマンドへ渡す差分基準 revision |
| `AGENT_CI_URL` / `AGENT_CI_BRANCH` / `AGENT_CI_COMMIT` / `AGENT_CI_REPOSITORY` | `ci_status_command` へ渡す公開先の座標 |
| `AGENT_CONTROL_DIR` / `AGENT_BUDGET_DIR` / `AGENT_TUNING_DIR` / `AGENT_INSTRUCTIONS_DIR` / `AGENT_SESSION_DIR` | `~/.agents/` 配下の置き場の上書き |
| `KIRO_GIT_CACHE_DIR` | 共有 git キャッシュ（bare ミラー）の置き場 |
| `KIRO_SKILLS_HOME` / `KIRO_SKILL_REGISTRY` / `KIRO_STATE_HOME` | スキルの探索先・スキルレジストリ・自己更新の適用済み SHA の置き場 |
| `AGENT_RESERVATION_ID` | node-budget の枠予約 ID（呼び出し元が渡すと run のレコードより優先する） |
| `GITLAB_TOKEN` / `GL_TOKEN` / `GITLAB_NODE_ID` | 同梱 `gitlab` プラグインのトークンとノード名義（既定構成では使いません） |

テストはエージェント CLI なしで実行できます。`test_config.py` の `ConfigKeyConsumptionTests` は、宣言しただけで実装から参照されない設定キーを検出します。

```bash
AGENT_FLOW_STUB_SLEEP_MAX=0 python3 -m unittest discover -s tools/agent-flow/tests
```
