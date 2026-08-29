# agent-amigos 利用ガイド兼 CLI 仕様

agent-amigos は、1 つの成果物を複数の役割に分け、担当エージェント同士で相談させながら統合する CLI です。依頼は「設計書と役割表を渡す」方法と、「ゴールだけ渡して役割表から作らせる」方法の 2 通りがあります。

本書の前半はミッションの出し方と受け取り方、後半はバス、状態、設定、CLI のリファレンスです。設計判断の背景は[設計書](../designs/agent-amigos-design.md)、追加の運用例は [`tools/agent-amigos/README.md`](../../tools/agent-amigos/README.md) を参照してください。

対象は `tools/agent-amigos/` の `agent_amigos` パッケージです。正典スキーマは [`mission`](../../schemas/mission.schema.json)、[`delivery`](../../schemas/delivery.schema.json)、[`amigos-command`](../../schemas/amigos-command.schema.json)、[`node-budget`](../../schemas/node-budget.schema.json) です。

## まず動かす

### インストール

Python 3 が必要です。共通インストーラで agent-project、agent-flow とまとめて入れると、共有ライブラリと契約の版が揃います。

```bash
bash tools/agent-tools/install.sh
agent-amigos --help
```

agent-amigos だけを入れる場合は次の通りです。

```bash
bash tools/agent-tools/install.sh --only agent-amigos
```

### 1 台でプロトコルを試す

最初はローカルバスと `stub` を使います。`stub` は LLM を呼ばないため、役割の募集、進行、統合、受入の流れだけを確認できます。

作業ディレクトリに、次のような `design-doc.md` を用意します。

```markdown
# 動作確認

## 目的
agent-amigos の協働フローを確認する。

## 成果物
役割ごとの成果を統合し、動作確認結果を納品する。

## 受入基準
必須ロールが完了し、統合成果物が作られていること。
```

続けて `roles.yaml` を用意します。動作確認では待ち時間をなくすため、オーナーがすぐに役割を引き受ける設定にします。

```yaml
mission:
  title: agent-amigos の動作確認
  goal: design-doc.md の受入基準を満たす
  staffing_policy: self-staff
  staffing_timeout: 0
  acceptance: manual
  convergence:
    done_when: all-required-done
    quiescence_turns: 0
roles:
  - id: worker
    title: 動作確認担当
    mission: 動作確認結果を result.md にまとめる
    deliverables: [result.md]
    required: true
```

ローカルバスを初期化してミッションを公示します。

```bash
agent-amigos init-bus --bus .agents/amigos-bus
agent-amigos post \
  --bus .agents/amigos-bus \
  --design design-doc.md \
  --roles roles.yaml \
  --agent-cli stub
```

`post` が表示したミッション ID を使って進行させます。

```bash
agent-amigos drive \
  --bus .agents/amigos-bus \
  --mission-id MISSION_ID \
  --agent-cli stub
```

別の端末で状態を確認します。`reviewing` になったら成果を確認し、受け入れます。

```bash
agent-amigos status MISSION_ID --bus .agents/amigos-bus
agent-amigos accept MISSION_ID --bus .agents/amigos-bus
agent-amigos deliveries -v
```

`accept` すると統合成果物がホームの `deliveries/` へ搬出されます。手元の別ディレクトリへ確認用に取り出すだけなら `collect` を使います。

```bash
agent-amigos collect MISSION_ID \
  --bus .agents/amigos-bus \
  --out ./mission-result
```

## ミッションを依頼する

### 役割を自分で決める

設計書と役割表がある場合は `post` を使います。

```bash
agent-amigos post \
  --bus .agents/amigos-bus \
  --design design-doc.md \
  --roles roles.yaml \
  --agent-cli codex
```

役割表には、各ロールの目的、成果物、必須かどうか、必要な能力タグ、使用するエージェント CLI を書きます。書式は [`tools/agent-amigos/roles.yaml.example`](../../tools/agent-amigos/roles.yaml.example) を起点にしてください。

公示後、その端末で完了まで進めるなら `--drive` を付けます。受入方式が `manual` の場合は、別の端末または dashboard から結果を確認して `accept` か `reject` を実行します。

### ゴールから役割を作る

役割分担を決めていない場合は `build-team` を使います。既定では役割表を表示するだけで、公示しません。

```bash
agent-amigos build-team \
  --goal "社内 FAQ ボットの MVP を納品する" \
  --capabilities python,frontend \
  --agent-cli codex \
  --out roles.yaml
```

出力を確認してから `post` するのが通常の流れです。設計から公示、実行まで一度に進める場合は明示します。

```bash
agent-amigos build-team \
  --goal "社内 FAQ ボットの MVP を納品する" \
  --title "FAQ ボット" \
  --agent-cli codex \
  --post \
  --drive
```

`build-team` の役割設計には実際のエージェント CLI が必要で、`stub` は使えません。

## 進行中の操作

状態を見るだけなら `status`、エージェントへ連絡するなら `say` を使います。

```bash
agent-amigos status MISSION_ID --bus .agents/amigos-bus
agent-amigos say MISSION_ID \
  --bus .agents/amigos-bus \
  --to reviewer \
  --type question \
  --subject "受入条件の確認" \
  --body "性能測定は今回の受入範囲に含めますか"
```

成果を受け入れられない場合は、修正内容を具体的に返します。

```bash
agent-amigos reject MISSION_ID \
  --bus .agents/amigos-bus \
  --feedback "README に実行例を追加して再提出してください"
```

`reject` は新しいラウンドを開始します。前ラウンドの完了宣言は引き継がれません。

## 常駐運用と複数 PC

agent-amigos 自身は daemon ではありません。PC ごとの常駐処理は `agent-project serve` が担い、募集への参加とエージェントの手番を必要なときだけ起動します。二重実行を避けるため、常駐環境で `join` を別途起動し続けないでください。

1 台ではローカルディレクトリをバスにします。複数 PC では専用の Git リポジトリを用意し、全ノードで同じ `git+<url>` を指定します。

```bash
agent-amigos post \
  --bus git+ssh://git@git.example.local/team/amigos-bus.git \
  --design design-doc.md \
  --roles roles.yaml
```

各ノードの能力タグ、使用する CLI、対象バスは `.agents/agent-amigos.yaml` に置けます。探索順はカレントディレクトリ、`.agents/`、`~/.agents/` です。

---

## CLI リファレンス

## 1. 用語

| 語 | 意味 |
|---|---|
| ミッション | 1 つの成果物を作る協働の単位。id は `am-<UTC タイムスタンプ>-<乱数 4 桁>` |
| ロール | 役割ミッション表の 1 行 |
| amigo | あるロールを引き受けたエージェント実体。`<node-id>--<role-id>` で一意 |
| バス | ミッションの全状態が置かれるファイル空間。真実は常にここにある |
| オーナー | ミッションを公示した側。`final.json` を書けるのはオーナーノードだけ |

`all` / `owner` はロール id の予約語です。`/` と `#` は使えません（`#` は席展開が使う）。

---

## 2. バス

### 2.1 レイアウト

```
<bus>/missions/<mission-id>/
  mission.json                       # 公示本体
  design-doc.md                      # 設計書
  roles/<role-id>.json               # 役割ミッション表の 1 行 = 1 ファイル
  assignments/<role-id>/<who>.json   # 担当の claim
  roster.json                        # 確定名簿
  status/<who>.json                  # amigo の自己申告状態・心拍・引き継ぎメモ
  channels/all/<who>/<ulid>.json     # 全体チャンネル
  inbox/<role-id>/<ulid>-<from>.json # ロール宛メッセージ
  artifacts/<role-id>/…              # 各ロールの成果物
  decisions.jsonl                    # 決定記録
  rejections/<NNNN>.json             # 差し戻し。件数 = ラウンド番号
  pruned/<role-id>.json              # 実行中に停止したロールの印
  conductor.json                     # 自律コンダクタの評価状態
  deliverable/…                      # 統合成果物
  final.json / cancelled.json        # 受入 / 中止の記録
  events/<who>.jsonl                 # 追記専用の監査ログ。予算会計の原本
```

公示は正規化 JSON で置きます。オーナーの入力は YAML でよいが、post の時点で変換します
（読み手に PyYAML を要求しないため）。

### 2.2 書き込み所有権

git バスでコンフリクトを起こさないよう、書き込み権をパス単位で分けます。この表が
「3-way 裁定は不要」の根拠です。

| パス | 書く人 |
|---|---|
| `mission.json` / `design-doc.md` / `roles/*` / `roster.json` / `decisions.jsonl` / `rejections/*` / `pruned/*` / `final.json` / `cancelled.json` | オーナーのみ |
| `assignments/<role>/<who>.json` | 応募する各ノード（ファイル名が自分なので衝突しない） |
| `status/<who>.json` / `events/<who>.jsonl` / `channels/all/<who>/*` | 各 amigo が自分名義の分だけ |
| `inbox/<role>/<ulid>-<from>.json` | 送信者（ulid と送信者名で衝突しない） |
| `artifacts/<role>/*` | そのロールの確定 amigo のみ |
| `deliverable/*` | integrator のみ |

既読フラグはバスに書きません。各 amigo が自分の status にカーソル（最後に見た ulid）を
持つだけです。

### 2.3 転送層

| 実装 | 転送 | 想定 |
|---|---|---|
| `LocalBus` | no-op（同一ディレクトリ） | 1 マシン。テストと単機運用 |
| `GitBus`（`git+<url>`） | `pull --rebase` / `add`＋`commit`＋`push` | 複数ノード分散 |

GitBus は専用のバスリポジトリを切って使います。`main` には公示インデックス
（`index/<mid>.json`）だけを置き、ミッション本体は `mission/<mid>` ブランチに分離します。
参加ノードは `main` を軽く poll して募集を見つけ、daemon のサイクルは毎回 `list_missions()` の
全件を `bus.mission(mid)` に通して未終端のミッションブランチをすべて clone します。

pull は間隔律速（既定 15 秒）。ただし claim の勝者確認だけは常に最新化します
（鮮度がプロトコルの正しさに効くのはそこだけだから）。push 競合は `pull --rebase` からの
指数バックオフで、force push はしません。転送の実体は `agentcore.transport.GitTransport` で、
3 エンジンが同じ実装を共有します。

各ノードは自分専用のクローンを持つのでローカルの変更はすべて自プロセス由来になり、
ステージは `add -A` で足ります。

`hub+<url>` は移行案内を出して終了します（中継サーバは撤去済み）。

---

## 3. 状態と収束

### 3.1 ライフサイクル

状態は専用フィールドを持たず、ファイルの存在から導出します（`derive_phase`）。

```
 open（募集中）──必須ロール充足──▶ working ──収束──▶ integrating ──▶ reviewing
                                     │                                │accept  │reject
                                     │ cancel                         ▼        ▼
                                     ▼                              done    working へ差し戻し
                                 cancelled
```

終端は `done` / `cancelled` / `failed` の 3 つです。`failed` になるのは、予算が尽きて
`on_exhausted: fail` のとき、または `staffing_policy: fail` で募集が `staffing_timeout` を
超えても必須ロールが埋まらなかったときで、いずれもまだ誰も手番を取っていないミッションに
限ります（走り出した後の欠員は再募集の領分）。

差し戻しは `rejections/` にファイルを 1 つ増やし、その件数がそのままラウンド番号になります。
旧ラウンドの完了宣言は自動的に無効になります。

### 3.2 収束条件

次のいずれか早いほうで収束します。

| 条件 | 成立 |
|---|---|
| `done_when: all-required-done`（既定） | 全必須ロールの完了宣言 |
| `done_when: reviewer-approved` | 加えて `approver` ロールの承認 |
| `done_when: consensus` | 席グループの最頻回答が `consensus_ratio` を占め、回答席が `consensus_min` 以上 |
| 静穏化 | integrator を除く必須ワーカーの idle が全員 `quiescence_turns` 続き、未回答質問が 0（owner 宛は数えない） |
| 予算枯渇の wrap-up | §5.1 |

後ろの 2 つで収束した場合、`deliverable/MANIFEST.json` に `partial: true` が付きます。
partial 統合の後で本来の完了へ到達した場合は完全版で統合し直します。

### 3.3 claim と lease

lease 内の全 claim のうち `(ts, node)` 昇順の先頭 1 件が勝者です。全ノードが同じ集合から
同じ勝者を導くので、ローカルでも git でも二重アサインが起きません。実体は
`agentcore.protocol` で、agent-flow のタスク claim・委譲板の入札と同じ仕様です。

| `assignment_policy` | 確定のしかた |
|---|---|
| `first-come`（既定） | claim 勝者がそのまま確定。オーナーは `roster.json` へ鏡写しするだけ |
| `owner-picks` | claim は応募止まり。オーナーが `assign` で書いた者だけが確定 |

lease が切れ、かつ away 宣言も無ければクラッシュとみなして再募集します。lease は liveness の
信号であって progress ではありません。ハングは CLI 定義の `timeout` で塞ぎます。

`state: away`（`resume_at` 付き）の間は lease が切れてもロールを奪いません。`resume_at` +
grace（既定 7,200 秒）を超えたら通常の再募集へ戻ります。

---

## 4. メッセージとアクション封筒

### 4.1 経路と型

経路は 3 つ。全体連絡は `channels/all/`、特定ロール宛は `inbox/<role-id>/`、オーナー宛の
エスカレーションは `inbox/owner/` です。

メッセージ型は 10 種です。

| 分類 | 型 |
|---|---|
| amigo が使う | `question` / `answer` / `request` / `review` / `status` / `decision-request` / `info` |
| システムが使う | `wrap-up` / `approve` / `feedback` |

### 4.2 アクション封筒

エージェント CLI の出力は封筒として受け取り、ランナーが検証してからバスへ書きます
（LLM にファイルを触らせない）。`kind` は 4 種だけです。

```json
{"actions": [
  {"kind": "send", "to": "architect", "type": "question", "subject": "...", "body": "..."},
  {"kind": "write_artifact", "path": "openapi.yaml", "content": "<ファイル全文>"},
  {"kind": "update_status", "note": "エンドポイント 3/5 完了"},
  {"kind": "declare_done"}
]}
```

検証するのは 3 点です。宛先が実在するか、パスが自ロールの `artifacts/` 内に収まるか
（`..` は拒否）、`approve` を名乗れる `approver` ロールか。不正なアクションは棄却して
events に残し、次ターンのプロンプトで LLM へ差し戻します。

### 4.3 会話の規約

- question には answer か owner へのエスカレーションで必ず応じる。`question_timeout`
  （既定 2 ターン）を過ぎた未回答はランナーが `decision-request` へ昇格します。ただし
  宛先が away の間は時計を止め、代わりに送信側へ不在を 1 度だけ知らせます
- 設計を左右する合意はオーナーが `decisions.jsonl` に書いて確定する。design doc の改訂も
  オーナーのみで、amigo は `request` で提案するに留まります

---

## 5. 予算

### 5.1 ミッション予算（依頼側・バスに宣言）

```yaml
budget:
  execution_minutes: 120   # 全 amigo のエージェント CLI 実行秒の総和。0 = 無制限
  per_role_turns: 30       # ロールあたりターン上限
  soft_ratio: 0.9          # これを超えたら wrap-up モードへ
  on_exhausted: wrap-up    # wrap-up | fail
```

消費は events の `cli_seconds` の総和です（誰が計算しても同じ値になる）。soft を超えると
ランナーは次の作業ターンから wrap-up モードへ切り替え、最初に検知したノードが全体チャンネルへ
`wrap-up` を宣言します。hard（100%）以降は integrator と受入以外の CLI 呼び出しを開始しません。

進行中のターンは CLI 定義の timeout まで走り得るので、超過は最大〈ターン timeout × 同時実行
amigo 数〉に収まります。追加はオーナーのみ（`budget add <mid> --minutes N`）。

### 5.2 ノード予算（請負側・ノード横断の共有台帳）

正典は [`node-budget`](../../schemas/node-budget.schema.json)（v2）。

```
$AGENT_BUDGET_DIR（既定 ~/.agents/budget/）
  config.json               # 上限設定
  ledger/<YYYYMMDD>.jsonl   # 記帳（UTC 日付・追記専用、1 実行 1 行）
```

amigos は `workload: amigos`、`ref: <mission-id>/<role>` で記帳します。トークンは実測できた
行だけ台帳に書き、未報告行は読み出し側が `rates` で推定します（台帳には事実だけを書き、
推定値は書かない）。

超過するとそのノードの amigo は CLI ターンを開始せず paused になります（`[node-budget]` タグ
付きでオーナーへ 1 度だけ通知）。ミッションは殺しません。

---

## 6. 設定ファイル

`agent-amigos.yaml`（PyYAML 無し環境は `agent-amigos.json`・同じキー）。優先順位は
CLI > 設定ファイル > 組み込み既定。探索順は `--config` 明示 → `<cwd>/agent-amigos.*` →
`<cwd>/.agents/agent-amigos.*` → `<cwd>/.agent/agent-amigos.*` → `~/.agents/agent-amigos.*`。

設定ファイルのあるディレクトリがノードのホーム（＝既定のバス）になり、agent-dashboard の
自動発見マーカーも兼ねます。グローバル設定（`~/.agents/`）のときのホームは cwd です。

| キー | 既定 | 意味 |
|---|---|---|
| `bus` | `"."` | バスの場所（ローカル dir / `git+<url>`）。ホーム自身が既定 |
| `bus_workdir` | `null` | `git+` バスのクローン作業領域（既定は自動） |
| `node_id` | `null` | ノード名（未設定ならホスト名から導出） |
| `agent_cli` | `null` | このノードの既定 CLI |
| `argv_limit` | `100000` | argv 渡しのプロンプト最大バイト数。超過分は一時ファイルへ退避 |
| `tags` | `[]` | 能力宣言。ロール `requires.tags` の選別に使う |
| `repos` | `{}` | 担当リポジトリ（`repos.schema.json` 形）。`requires.repos` の選別に使う |
| `roles` | `[]` | 応募するロールの絞り込み（空 = 全ロール） |
| `interval` | `5.0` | ターンループの間隔（秒） |
| `resume_hours` | `12.0` | away からの復帰予定（`resume_at` の算出） |
| `manual_claim` | `false` | true で自動応募しない（`commands/` 経由の手動引き受けのみ） |
| `board` | `null` | 委譲公示板の場所。与えると巡回して `workload: amigos` に入札する |
| `board_workdir` | `null` | `git+` 板のクローン作業領域 |
| `board_lease` | `900.0` | 板入札の lease（秒） |

キーはここに無いものが黙って無視されます。設定に書いたのに効かないときは、まず綴りを
確認してください。

### 6.1 環境変数

| 変数 | 既定 | 効く先 |
|---|---|---|
| `AGENT_AMIGOS_BUS` | — | バスの場所（設定 `bus` より優先、CLI より下） |
| `AGENT_AMIGOS_NODE` | ホスト名由来 | ノード名 |
| `AGENT_AMIGOS_LEASE` | `600` 秒 | claim の lease |
| `AGENT_AMIGOS_AWAY_GRACE` | `7200` 秒 | away を再募集へ倒すまでの猶予 |
| `AGENT_AMIGOS_PULL_INTERVAL` | `15` 秒 | GitBus の pull 間隔 |
| `AGENT_AMIGOS_TURNS_DIR` | `~/.agents/amigos/turns` | 同時実行マーカーの置き場 |
| `AGENT_BUDGET_DIR` | `~/.agents/budget` | ノード予算台帳 |

---

## 7. 役割ミッション表

正典は [`mission.schema.json`](../../schemas/mission.schema.json)、雛形は
[`roles.yaml.example`](../../tools/agent-amigos/roles.yaml.example)。

### 7.1 ミッション側

| キー | 既定 | 意味 |
|---|---|---|
| `assignment_policy` | `first-come` | `first-come` / `owner-picks` |
| `staffing_policy` | `self-staff` | `self-staff`（オーナーノードが未充足ロールを立てる）/ `wait` / `fail` |
| `staffing_timeout` | `600` 秒 | 募集の待ち時間 |
| `acceptance` | `manual` | `manual` / `agent`。`codd-gate` は将来拡張で、現状は起動時に拒否 |
| `deadline` | — | 任意。超過はオーナーへ 1 度通知するだけで、自動 fail はしない |
| `convergence.done_when` | `all-required-done` | §3.2 |
| `convergence.quiescence_turns` | `3` | 静穏化の判定ターン数 |
| `convergence.review_rounds` | `2` | `acceptance: agent` の差し戻し上限 |
| `convergence.question_timeout` | `2` | 未回答質問を owner へ昇格するまでの自ターン数 |
| `convergence.consensus_ratio` | `0.6` | `done_when: consensus` の占有率しきい値 |
| `convergence.consensus_min` | `2` | 合意判定に要る最小回答席数 |
| `budget.*` | §5.1 | |
| `conductor.enabled` | `false` | 自律コンダクタ。`cli` / `max_ops`(3) / `max_total_ops`(12) / `interval_rounds`(1) |
| `workspace.repo` | — | コード成果物用。opaque passthrough（§9） |

### 7.2 ロール側

| キー | 意味 |
|---|---|
| `id` / `title` / `mission` | 識別子・表示名・ミッション文 |
| `deliverables` | 成果物のヒント |
| `required` | 必須ロールか（収束判定と募集の対象） |
| `agent_cli` / `model` | このロールを演じる CLI とモデル |
| `requires.tags` / `.cli` / `.repos` | 応募できるノードの条件 |
| `collaborates_with` | 会話のヒント。実行順序の強制ではない |
| `approver` | `done_when: reviewer-approved` の承認者 |
| `builtin: integrator` | 統合ロール。省略時はオーナーノードが自動補充 |
| `seats: N` | `<role>#0..#N-1` の具体席へ静的に展開する |
| `aggregate` | `majority` / `consensus` / `weighted-vote` / `approval-count` / `gather` |
| `rounds: N` | 同期討論ラウンド（`seats>=2` のみ） |
| `topology` | `complete`（既定）/ `ring` / `star` / `tree`（`rounds>=1` のみ） |
| `aggregate_answer` / `aggregate_score` | 集約が読むファイル名（既定 `ANSWER.md` / `SCORE`） |

同期討論は全席の round-(k-1) が揃うまで round-k へ進めないバリアを課します。バリアは
ファイルの存在で判定するので、非同期のターンループ上でも決定的に同期します。

---

## 8. エージェント CLI

LLM 実行は [`agent-cli`](../../schemas/agent-cli.schema.json) のプラグイン契約
（`agents/<name>.json`）をそのまま使い、解釈は `agentcore.agentcli` の 1 実装です
（amigos 側の `agentcli.py` は薄い再輸出）。amigos 側に CLI 分岐コードは書きません。

同梱定義はリポジトリ直下 `agents/` にあり、base 8 種（`aider` / `claude` / `codex` /
`copilot` / `cursor` / `kiro` / `ollama` / `vscode-copilot`）です。用途別の起動差は
`ollama` の `profiles`（`json` / `list` / `list-thinking` / `read` / `verify`）が持ちます。amigos は headless 呼び出し（1 ターン 1 回・封筒を返させる）なので、
`interactive` 節の有無は問いません。

CLI の解決順は 管理面 > ノード既定 > ロール指定 です。管理面は
[`agent-control`](../../schemas/agent-control.schema.json)（`~/.agents/control/control.json`）で、
ロール別に CLI とモデルを横断上書きできます。

どこからも決まらない場合は `stub` へ落とさず環境エラーにします。既定を stub にすると、
設定を読み落とした経路でダミー応答の成果物がそのまま統合・納品まで進みます。

`stub` は LLM を使わず決定的に封筒を組み立てる検証用の実装で、プロトコル層のテストはすべて
これで回ります。

失敗は決定的トリアージ（`[agent-error:quota|auth|env|transient]`）で読み分けます。

| クラス | 挙動 |
|---|---|
| `transient` | そのターンをリトライ |
| `quota` / `auth` / `env` | その amigo を paused にして status へタグ付き理由を書き、オーナーへ 1 度だけ通知。ロールは lease を保持したまま待機し、環境を直せば続きから走る |

---

## 9. 受入と納品

integrator の完了で reviewing に入ります。integrator は LLM を使いません。`artifacts/*` を
走査して `deliverable/` へコピーし、由来ロールと SHA-256 の先頭 16 桁を `MANIFEST.json` に
残すだけの決定的な処理です。

`acceptance: manual` なら人が `accept` / `reject` を叩きます。`agent` ならオーナーノードの
エージェント CLI が design doc と deliverable を突き合わせて自動判定し、`review_rounds` 回
差し戻しても受からなければ人へ `decision-request` を上げて止まります（無限ループを作らない）。
`final.json` を書けるのはオーナーノードだけ、という不変条件は自動判定でも変わりません。

accept は納品棚への搬出を伴います。バスの `deliverable/` は gc の対象なので、そこにしか
成果物が無い状態を残しません。accept が成立した時点でオーナーホームの
`<home>/deliveries/<mission-id>/` へ搬出し、納品書 `delivery.json`
（正典: [`delivery.schema.json`](../../schemas/delivery.schema.json)）と受領一覧
`<home>/DELIVERY.md` を書きます。納品棚は gc の既定では消しません
（消すのは `gc --deliveries-keep-days` を指定した人の判断だけ）。

正本の置き場は種別で分けます。

| 種別 | 正本 | 納品棚 |
|---|---|---|
| 文書・調査結果・小さい画像 | 納品棚 | 本体を置く |
| コード | `workspace.repo` の統合ブランチ | 参照だけ |
| 10MB 超のファイル | 元の場所 | 参照だけ（`exported: false`） |

`workspace` は opaque passthrough です。`export_delivery` が納品書に書くのは
`{"repo": …, "branch": "amigos/<mission-id>/integration"}` という参照文字列だけで、
checkout・ブランチ作成・マージを行うコードはありません。amigo 自身が対象リポジトリを
どう扱うかは各ロールの裁量です。

---

## 10. CLI

```
agent-amigos init-bus     --bus <dir|git+url>
agent-amigos post         --design <md> --roles <yaml> [--drive]
agent-amigos build-team   --goal "..." --agent-cli <cli> [--pattern <id>] [--out <f>|--post]
agent-amigos participate  [--json] [--tags ...] [--roles ...] [--board ...]
agent-amigos drive        [--mission-id <id>] [--cycles N]
agent-amigos join         [--roles ...] [--tags ...] [--agent-cli ...]
agent-amigos run          --mission <mid> --role <role> [--once]
agent-amigos status       [<mid>]
agent-amigos assign       <mid> <role> [<node>]
agent-amigos restaff      <mid> [--add <roles>] [--prune <id,…>]
agent-amigos accept       <mid>  /  reject <mid> --feedback "..."
agent-amigos deliveries   [-v]
agent-amigos collect      <mid> --out <dir>
agent-amigos budget       add <mid> --minutes N  /
                          node [--limit-minutes N] [--limit-tokens N] [--period day]
                               [--amigos-minutes N]
agent-amigos say          <mid> --to <role|all|owner> --body "..."
agent-amigos cancel       <mid>  /  gc [--keep-days N] [--deliveries-keep-days N]
```

サブコマンド無しの裸起動は案内を出して終わります。黙って常駐すると常駐体
（`agent-project serve`）と二重に回って claim を奪い合うためです。

### 10.1 外部からの指示

`<home>/.agents/agent-amigos/commands/*.json` に JSON を 1 ファイル置くだけです
（正典: [`amigos-command.schema.json`](../../schemas/amigos-command.schema.json)）。
プロセス間 API を持たず、結合は常にデータの一方向。処理済みは削除し、失敗は `.rejected` へ
改名します（壊れた指示を無限に噛み続けないため）。

---

## 11. パターンカタログ

役割設計の手順は [`.github/skills/team-builder/`](../../.github/skills/team-builder/) にあり、
`build-team` はそれを呼び出します。設計には実際のエージェント CLI が要ります
（`stub` と未指定は不可）。

出力契約は `{"target": "amigos", "pattern": "<id|none>", "mission": {…}, "roles": [ … ]}` で、
`normalize_mission` で検証してから post 経路へ合流します。

カタログは [`patterns/<id>.json`](../../.github/skills/team-builder/patterns/) に 37 件
（契約は同ディレクトリの `references/pattern.schema.json`）。

| tier | 件数 | 扱い |
|---|---:|---|
| `high` | 8 | `build-team` 実行時にカタログをプロンプトへ注入し、自動選択の対象にする |
| `medium` | 29 | 自動選択に入れない。`--pattern <id>` で明示指定したときだけ使う |

`high` の 8 件は `self-refine` / `metagpt-sop` / `agentcoder` / `multiagent-debate` /
`mixture-of-agents` / `chateval` / `self-consistency` / `least-to-most` です。

うち 3 件（`tree-of-thoughts` / `graph-of-thoughts` / `lats`）は `target: agent-flow` で
登録されており、選ばれると roles ではなく agent-flow への委譲封筒
（`schemas/delegation.schema.json` の op=post / workload=flow）を出力します。

カタログを持たない 3 件（DyLAN / AgentVerse / meta-prompting）は自律コンダクタで表現します。

---

## 12. 上限と間隔

| 対象 | 値 | 変えられるか |
|---|---|---|
| claim の lease | 600 秒 | `AGENT_AMIGOS_LEASE` |
| away の猶予 | 7,200 秒 | `AGENT_AMIGOS_AWAY_GRACE` |
| GitBus の pull 間隔 | 15 秒 | `AGENT_AMIGOS_PULL_INTERVAL` |
| ターンループの間隔 | 5 秒（無風なら最大 8 倍まで伸ばす） | 設定 `interval` |
| 募集の待ち | 600 秒 | `staffing_timeout` |
| ロールあたりターン | 30 | `per_role_turns` |
| 板入札の lease | 900 秒 | 設定 `board_lease` |
| argv 渡しのプロンプト | 100,000 バイト（超過は一時ファイルへ退避） | 設定 `argv_limit` |
| 1 ターンの CLI 実行 | CLI 定義の `timeout` | 定義側 |
| 同時実行 amigo | PC 単位のマーカー（`~/.agents/amigos/turns/*.json`、pid 入り）で律速 | — |

新着もやることも無ければエージェント CLI を呼びません（idle ターン）。idle が続いたら
status の書き込み自体も止めます（心拍の鮮度維持だけ 60 秒おき）。

---

## 13. 未実装

| 項目 | 状態 |
|---|---|
| コード成果物のブランチ運用 | 納品書に参照文字列を書くだけ。checkout・ブランチ作成・マージのコードは無い（§9） |
| 前任の引き継ぎ材料の自動合成 | `_build_prompt` が組み込むのは後任自身の status とアーティファクト一覧だけ。前任の status / events / artifacts はバスに残るが、参照は後任の裁量 |
| ノードの可用性ウィンドウ宣言 | 未実装。`owner-picks` の判断材料にできない |
| `acceptance: codd-gate` | 将来拡張。現状は `manual` / `agent` のみ受け付ける |
| `pairwise-rank` パターン | 設計方針として ranker ロール（approver）に委ねる。プリミティブは足さない |

---

## 付録. テスト

`tools/agent-amigos/tests/` に 12 ファイル・197 件。stub エージェント（LLM 不要）で回ります。

```bash
cd tools/agent-amigos && python3 -m unittest discover -s tests
```
