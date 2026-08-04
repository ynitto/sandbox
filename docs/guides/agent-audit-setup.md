# agent-audit セットアップガイド — 実行証跡の収集から知見の蒸留まで

> 最終更新: 2026-08-04 ／ 関連: [`docs/designs/agent-audit-design.md`](../designs/agent-audit-design.md)（設計正典）,
> [`tools/agent-audit/README.md`](../../tools/agent-audit/README.md)（実装）,
> [`tools/agent-audit/agent-audit.yaml.example`](../../tools/agent-audit/agent-audit.yaml.example)（設定の雛形）
>
> 対象: エージェント CLI（claude / codex / kiro …）を使っている PC で、
> **トークン使用量・実行品質・そこから得られる知見**を手元で見えるようにしたい人。
> agent-project / agent-flow / agent-amigos / agent-loop を使っていなくても、
> §1〜§4 までで価値が出ます。

## 0. agent-audit は何をするツールか

すでにディスク上にある実行の痕跡を**読むだけ**で、3 つの答えを出します。

| 知りたいこと | コマンド | LLM |
|---|---|---|
| どの CLI・モデルにどれだけトークンを使ったか | `agent-audit usage` | 使わない |
| 実行はどれくらい失敗し、何が原因だったか | `agent-audit stats` | 使わない |
| 次から何を直すべきか（知見・改善タスク） | `agent-audit extract` → `distill` → `tasks` | 使う（2 段だけ） |

押さえておくと迷わない性質が 4 つあります。

- **読み手に徹する。** 書き先は audit ディレクトリ（既定 `~/.agents/audit/`）だけ。
  他ツールのバスや状態リポジトリには書きません（例外は §8.2 の `calibrate --write` のみ）。
- **常駐しない。** 全サブコマンドが単発で必ず終わります。定期実行は cron や agent-loop 側に置きます。
- **環境変数を見ない。** 書き先も源泉の場所も「CLI 引数 > 設定ファイル > 組み込み既定」だけで決まります。
  cron から実行しても手で叩いても同じ場所を読み書きします。
- **実測と推定を混ぜない。** セッションログ由来の実測トークンと、秒数からの推定を別の列で数えます。

---

## 1. インストール

前提は **python3.11 以上**だけです（YAML 設定を使うときだけ PyYAML）。

```bash
bash tools/agent-audit/install.sh
# または他の agent-* と一緒に入れる
bash tools/agent-tools/install.sh
```

`~/.local/bin/agent-audit` が作られます。PATH に無ければ通しておきます。

```bash
export PATH="$HOME/.local/bin:$PATH"   # 必要なら ~/.bashrc などへ
agent-audit --help
```

> **リポジトリから直接試す場合**は、インストールせずにこう実行できます。
> ```bash
> PYTHONPATH=tools/agent-tools/agentcore python3 tools/agent-audit/agent-audit.py doctor
> ```

インストーラは同時に、同梱のエージェント CLI 定義（`agents/*.json`）を
`~/.agents/agents/` へ配ります。ここに `session_log` の宣言が入っているおかげで、
**設定ファイルを 1 行も書かずにセッションログの収集が始められます**（§4 で詳述）。

---

## 2. まず動かす — 3 コマンドで現状を見る

### 2.1 `doctor` — 何が読めるかを先に確認する

設定を書く前に、**このマシンで何が読める状態か**を見ます。

```bash
agent-audit doctor
```

```
audit ディレクトリ: ~/.agents/audit（未作成 — collect が作成します）
node-budget: ~/.agents/budget（台帳なし — エンジン未使用なら正常）

エージェント CLI 定義:
  claude: session_log あり（format=jsonl-dir・到達可）
  codex: session_log あり（format=jsonl-dir・パス未検出（この CLI を未使用なら正常））
  copilot: session_log なし — セッションは未収集になります（agents/copilot.json への宣言で収集できます）
  cursor: session_log なし — セッションは未収集になります（agents/cursor.json への宣言で収集できます）
  kiro: session_log あり（format=kiro-sqlite・パス未検出（この CLI を未使用なら正常））
  ollama: session_log なし — セッションは未収集になります（agents/ollama.json への宣言で収集できます）

ストア: records=0 / observations=0 / insights=0
設定: なし（組み込み既定で動作。雛形: agent-audit.yaml.example）
```

読み方は 3 点だけです。

- **「到達可」が 1 つでもあれば収集できます。** 上の例なら claude のセッションが読めます。
- **「パス未検出」はその CLI を使っていなければ正常**です。エラーではありません。
- **「session_log なし」は黙ってスキップされたのではなく、未収集だと明示されている**状態です。
  収集したい CLI がここに出ていたら §4.3 で宣言を足します。

### 2.2 `collect` — 増分収集

```bash
agent-audit collect
```

```
[agent-audit] collect: 新規レコード 1 件（store: ~/.agents/audit）
```

読み取り専用・増分・冪等なので、**何度実行しても同じものを二重に取り込みません**。
2 回目以降は前回の続きだけを読みます。

### 2.3 `usage` / `stats` — 集計を見る

```bash
agent-audit usage --period total --by agent_cli
```

```
トークン・コスト集計（period=total / by=agent_cli）
group                      runs    seconds       実測in     実測out   推定tokens    未計測      usd
------------------------------------------------------------------------------------------
claude                        1        0.0    3065407      6868          0      0     0.00
```

列の意味は次のとおりです。**実測と推定は最後まで別の列**で、足した数字は出しません。

| 列 | 中身 |
|---|---|
| 実測in / 実測out | CLI のセッションログに残っていた本物のトークン数 |
| 推定tokens | 実測が無い行を「実行秒 × rates」で推定した値（§8.2 の `calibrate` で精度が上がる） |
| 未計測 | 実測も推定もできなかった行数（rates 未設定など） |
| usd | 台帳に金額が入っていた分だけ |

軸は自由に切り替えられます。

```bash
agent-audit usage                              # 既定: 当月 × workload 別
agent-audit usage --period month --by model    # モデル別に当月
agent-audit usage --period day --by tool       # 今日、ツール別
agent-audit usage --by node --json             # JSON で（ダッシュボード等へ渡す）
```

`--period` は `day` / `month` / `total`、`--by` は
`workload` / `tool` / `agent_cli` / `model` / `ref` / `node` から選べます。

実行品質のほうは `stats` です。

```bash
agent-audit stats
```

```
実行品質集計（period=month）

agent-flow: 42 run
  status done: 38
  status failed: 4
  error [transient]: 3
  error [quota]: 1
  リトライ合計: 7
  verify: pass=36 fail=2
```

`stats` は agent-flow / agent-project / agent-amigos / agent-loop の run レコードから集計します。
CLI 単独利用（エンジン未使用）なら「run レコードがありません」と出るのが正常です。

### 2.4 `report` — 1 枚にまとめる

```bash
agent-audit report
```

usage・品質・洞察を 1 枚の Markdown にして stdout へ出し、
同じ内容を `~/.agents/audit/reports/<日時>-all.md` に保存します。

```bash
agent-audit report --kind usage          # usage だけ
agent-audit report --out ./weekly.md     # 保存先を指定
```

出力は**必ずスクラバを通ります**（資格情報らしき文字列の伏せ字化、絶対パスのホーム相対化）。
人に見せる・リポジトリに貼るのはこの出力です。

---

## 3. 設定ファイルを置く

ここまでは設定ゼロで動きます。源泉を足す・LLM 段を使う段階になったら設定ファイルを 1 枚置きます。

### 3.1 置き場所と探索順

上から順に探し、**最初に見つかった 1 枚だけ**を使います。

1. `--config <path>` で明示したもの
2. `<カレントディレクトリ>/agent-audit.yaml`（`.yml` / `.json` も可）
3. `<カレントディレクトリ>/.agents/agent-audit.yaml`
4. `<カレントディレクトリ>/.agent/agent-audit.yaml`
5. `~/.agents/agent-audit.yaml`

**PC 全体で 1 つ**なら `~/.agents/agent-audit.yaml`、**プロジェクトごと**に変えたいなら
リポジトリ直下に置きます。cron から実行するときはカレントディレクトリが変わりやすいので、
`~/.agents/` に置くか `--config` を明示するのが安全です。

> **PyYAML が入っていない環境**では YAML を読めません（明示的にエラーになります）。
> `agent-audit.json` に**同じキー**で書けば PyYAML 無しで動きます。

### 3.2 最小の設定

まずはこれだけで十分です。

```yaml
# ~/.agents/agent-audit.yaml
audit_dir: ""                 # 空 = 既定 ~/.agents/audit
budget_dir: ""                # 空 = 既定 ~/.agents/budget
```

雛形は [`tools/agent-audit/agent-audit.yaml.example`](../../tools/agent-audit/agent-audit.yaml.example)
にあります。全キーの一覧は §10 の表を見てください。

優先順位は常に **CLI 引数 > 設定ファイル > 組み込み既定**です。
例えば書き先を一時的に変えたいときは `--audit-dir /tmp/audit` を付ければ設定を編集せずに済みます。

---

## 4. 源泉を足す

### 4.1 何もしなくても読まれるもの

| 源泉 | 場所 | 条件 |
|---|---|---|
| `budget-ledger` | `~/.agents/budget/ledger/*.jsonl` | node-budget の台帳があれば |
| `cli-native` | `agents/<name>.json` の `session_log` 宣言先 | 宣言があり、パスが存在すれば |

### 4.2 設定に書いて初めて読まれるもの

agent-* エンジンを使っている場合は、読ませたい場所を明示します。

```yaml
flow_buses:   [~/work/myproj/bus]          # agent-flow のバス
project_roots: [~/work/state-repo]         # agent-project の state clone（bus/ も自動で読む）
amigos_buses: [~/work/amigos-bus]          # agent-amigos のバス
loop_logs:    [~/.agents/agent-loop.log]   # agent-loop の本体ログ（ファイルを指定）
```

書いたら `doctor` で到達性を確認します。

```bash
agent-audit doctor
```

```
flow_buses: ~/work/myproj/bus — OK
loop_logs: ~/.agents/agent-loop.log — 見つかりません
```

> **設定に書いたパスが存在しないと `collect` は exit 2 で止まります。**
> これは仕様です——読めなかった源泉を黙って飛ばして「部分集計を全体のように見せる」ことをしません。
> パスを直すか、その行を設定から消してください。

収集する源泉を絞りたいときは `--source` を重ねます（設定の `sources:` でも同じ）。

```bash
agent-audit collect --source budget-ledger --source cli-native
agent-audit collect --since 2026-08-01T00:00:00Z    # この時刻以降のセッションだけ
```

### 4.3 未宣言の CLI からセッションを集める

`doctor` に「session_log なし」と出た CLI を収集したいときは、その CLI の定義に
`session_log` ブロックを足します。**触るのは JSON 1 ファイルだけ**です。

置き場所は `~/.agents/agents/<name>.json`（インストーラが配る同梱定義の置き場）か、
プロジェクト直下の `agents/<name>.json`（こちらが優先。リポジトリ内で完結させたいとき）。

```jsonc
// agents/mycli.json への追記例
"session_log": {
  "format": "jsonl-dir",          // 1 セッション = 1 *.jsonl のディレクトリ
  "paths": ["~/.mycli/sessions"], // グロブ可・先勝ち
  "usage": true                   // 実測トークンを含むか
}
```

`format` は閉じた列挙で、現在は次の 2 つです。

| format | 想定 | 同梱の例 |
|---|---|---|
| `jsonl-dir` | セッションごとに JSONL ファイルが並ぶディレクトリ | claude（`~/.claude/projects`）・codex（`~/.codex/sessions`） |
| `kiro-sqlite` | Kiro の SQLite ストア | kiro（`~/.kiro/store.db`） |

どちらにも当てはまらない形式の CLI は、パーサを足すまで収集できません
（未対応 format は `collect` 時に「未収集です」と明示されます）。

> **同梱定義（`~/.agents/agents/`）はインストーラの更新で上書きされます。**
> 独自の宣言はプロジェクトの `agents/` に置くと消えません。

---

## 5. 知見の蒸留（LLM を使う 2 段）

ここからが LLM を使う部分です。**収集・集計・クラスタリング・レポートは一切 LLM を使いません**。
LLM は「1 レコードを観測に落とす（extract）」と「観測の塊を洞察に畳む（distill）」の 2 段だけです。

```
records ──extract(map)──▶ observations ──cluster(決定的)──▶ distill(reduce) ──▶ insights
                                                                                  │
                                                                    report / tasks ┘
```

### 5.1 段ごとにモデルを選ぶ

トークン削減の要はここです。extract は 1 レコードずつの局所要約なので**弱いモデル・ローカルモデルで十分**、
distill は一般化なので中〜強モデルを使います。

```yaml
agent_cli: claude             # 既定の CLI
agents:
  extract: {agent_cli: ollama, model: qwen3}   # map: 弱モデル・ローカルで十分
  distill: {agent_cli: claude, model: sonnet}  # reduce: 一般化は中〜強モデルで
```

指定できる CLI 名は `agents/<name>.json` にある定義名（claude / codex / kiro / ollama …）です。

### 5.2 実行

```bash
agent-audit extract     # レコード → 観測
agent-audit distill     # 観測クラスタ → 洞察
agent-audit report      # 洞察を含めて 1 枚に
```

初回はよくこう出ます。

```
[agent-audit] extract: 実行を見送りました — 蓄積ゲート: 未抽出の候補 3 件 < 10 件（--force で強制）
```

これは**故障ではなく設計どおり**です。LLM を呼ぶ前に決定的なゲートで足切りしています。
試したいときだけ `--force` を付けます。

```bash
agent-audit extract --force --limit 3
agent-audit distill --force
```

### 5.3 LLM をどれだけ使うかの制御

`collect && extract && distill` を高頻度で回しても、**LLM 消費は設定したリズムを超えません**。
駆動の頻度と LLM の頻度が分離されているためです。

| 種類 | extract の既定 | distill の既定 | `--force` で飛ばせるか |
|---|---|---|---|
| 間隔ゲート | 6 時間（`extract_min_interval_hours`） | 24 時間（`distill_min_interval_hours`） | 飛ばせる |
| 蓄積ゲート | 候補 10 件（`extract_min_records`） | 新規観測 5 件（`distill_min_new_observations`） | 飛ばせる |
| 1 実行あたりの呼び出し上限 | 40 回（`extract_max_calls`） | 10 回（`distill_max_calls`） | **飛ばせない** |
| node-budget 超過での停止 | — | — | **飛ばせない** |

**`--force` はゲートだけを外します。** 上限と予算は人の手でも外れません。

extract に渡す対象も決定的に選抜されます（既定 `extract_filters`）。
成功して何も起きなかったレコードに LLM は使いません。

| フィルタ | 拾う条件 |
|---|---|
| `failed` | status が failed、または error_class が付いている |
| `retried` | リトライ 2 回以上 |
| `verify-flip` | verify が fail |
| `needs` | エスカレーションあり |
| `long-session` | 30 ターン以上、または 30 分以上のセッション |

### 5.4 洞察を改善タスクにする

```bash
agent-audit tasks
```

`exported: false` かつ具体的な提案を持つ洞察を、`schemas/task.schema.json` 形の JSON として
stdout へ出します。agent-project を使っているなら、その汎用 intake へそのまま流せます。

```bash
agent-audit tasks | agent-project enqueue --json         # 手で 1 回流す
agent-audit tasks --mark-exported > tasks.json           # 出した洞察に印を付けて二度出さない
```

継続的に流すなら agent-project 側の `intake_cmd` に登録すれば、watch の周期で勝手に取り込まれます。

```yaml
# agent-project の設定側
intake_cmd: agent-audit tasks --mark-exported
```

**agent-audit が state リポジトリへ直接書くことはありません。** JSON を出すところまでが担当で、
採用・昇格の判断は受け取った側の仕事です。

---

## 6. 定期実行

常駐機能は持たないので、定期実行は外側に置きます。cron ならこれで十分です。

```cron
# 1 時間ごと。ゲートがあるので LLM は設定したリズムでしか動かない
0 * * * * PATH=$HOME/.local/bin:$PATH agent-audit --config $HOME/.agents/agent-audit.yaml collect && agent-audit --config $HOME/.agents/agent-audit.yaml extract && agent-audit --config $HOME/.agents/agent-audit.yaml distill
```

agent-loop を使っているなら、定期プロンプトに
`agent-audit collect && agent-audit extract && agent-audit distill` を書くだけです。

ポイントが 2 つあります。

- **`--config` を明示する。** cron はカレントディレクトリが変わるため、
  設定ファイルの探索結果が手で叩いたときと変わることがあります。
- **`run` のような一括コマンドは用意していません。** 3 つを `&&` で並べるのが正規の書き方です。

`collect` の末尾では、前回から 24 時間（`gc_interval_hours`）以上経っていれば
自動クリーンアップ（§8.3）が 1 回だけ相乗りで走ります。掃除のための常駐は増えません。

---

## 7. ここまでの最短まとめ

```bash
bash tools/agent-audit/install.sh          # 1. 入れる
agent-audit doctor                         # 2. 何が読めるか見る
agent-audit collect                        # 3. 集める
agent-audit usage --by agent_cli           # 4. トークンを見る
agent-audit report                         # 5. 1 枚にまとめる
```

LLM 蒸留まで使うなら、`~/.agents/agent-audit.yaml` に `agents:` を書いて
cron に `collect && extract && distill` を仕込む——これで運用に乗ります。
以降は必要になったときだけ読めば十分な補足です。

---

## 8. 補足 — オプション的な使い方

### 8.1 transcript も保存する（`--with-transcripts`）

```bash
agent-audit collect --with-transcripts
```

会話本文を `~/.agents/audit/transcripts/<cli>/<session>.log` に保存します。効果は 2 つ。

- extract の入力に transcript の末尾抜粋が加わり、**観測の質が上がります**。
- ディスクを食います（既定の保持は 30 日）。

**共有可能な層の境界**は物理的に分かれています。ノード外へ出してよいのは
集計値・観測・洞察・タスク（= report / tasks / `--json` の出力）だけで、
**transcript 本文はローカルに留まります**。レコード側には本文を持たず参照だけを持つのはこのためです。

### 8.2 rates を較正する（`calibrate`）

実測トークンが取れている行から `<cli>:<model>` ごとの「tokens/秒」中央値を出します。
これが node-budget の推定精度をそのまま決めます。

```bash
agent-audit calibrate              # 提案を表示するだけ
agent-audit calibrate --write      # budget の config.json の rates.per_cli を更新する
```

`--write` は **agent-audit が audit ディレクトリの外へ書く唯一の操作**です。
書き込むのは node-budget 契約が「管理面が更新するもの」と定めている `rates` だけで、
`updated_by: "agent-audit"` の印が残ります。まず `--write` 無しで数字を見てから反映してください。

### 8.3 掃除と保持期間（`gc`）

```bash
agent-audit gc --dry-run     # 何が消えるか見るだけ
agent-audit gc               # 実行
```

```yaml
gc_auto: true                 # collect 末尾の自動クリーンアップ（false で無効）
gc_interval_hours: 24
gc_keep_days:                 # 0 にするとその種別は消さない
  records: 90
  transcripts: 30
  observations: 90
  reports: 30
```

**insights と state.json は gc の対象外**です。洞察は蒸留の成果そのもので、
消すと同じクラスタを再蒸留してトークンを二重に払うことになります（消したいときは手で消します）。
逆に処理済みカーソル（state.json）は records より長生きするので、
records を消しても同じセッションが再収集されて LLM に再投入されることはありません。

### 8.4 洞察を検証する（`distill --review`）

```yaml
agents:
  review: {agent_cli: claude, model: opus}
```

```bash
agent-audit distill --review
```

洞察を根拠の観測と突き合わせて `supported` / `weak` / `refuted` の判定を付けます。
`refuted` の洞察は `tasks` の出力から除外されます。
LLM 消費が増える段なので既定は無効です。

### 8.5 一時停止と予算（agent-control / node-budget）

- `~/.agents/control/control.json` の `lifecycle` が `pause` / `stop` のとき、LLM 段は実行されません
  （`workloads.audit` で agent-audit だけを止めることもできます）。`control` の CLI / モデル指定は設定より優先されます。
- node-budget の上限を超えていると、LLM 段は 1 回も呼ばずに `[agent-error:quota]` で終了します（exit 1）。
- agent-audit 自身の LLM 消費も `workload: audit` として台帳に記帳されます——
  **集計のためのループが財布を燃やさない**ように、自分の消費も自分で見えるようにしてあります。

### 8.6 自己更新

```bash
agent-audit update --check     # 更新があるか見るだけ
agent-audit update --now       # 取り込む
```

### 8.7 終了コード

| コード | 意味 | 典型的な場面 |
|---|---|---|
| 0 | 成功 | 通常（ゲートで見送った場合も 0） |
| 1 | LLM 段の停止 | 予算超過・agent-control による pause / stop |
| 2 | 源泉が読めない・使い方の誤り | 設定したパスが存在しない、不明な `--source`、引数の誤り |

CI やスクリプトから叩くときは、**2 を「設定ミス」、1 を「今日はもう回さない」**として扱うと素直です。

---

## 9. うまくいかないとき

| 症状 | 原因 | 対処 |
|---|---|---|
| `collect` で「新規レコード 0 件」 | 既に収集済み（増分なので正常）／読める源泉が無い | `doctor` で「到達可」が 1 つでもあるか確認 |
| `usage` が「レコードがありません」 | まだ `collect` していない／`--period` の期間外 | `collect` 後に `--period total` で確認 |
| `stats` が「run レコードがありません」 | agent-* エンジン未使用 | CLI 単独利用では正常。`usage` は動く |
| `collect` が exit 2 で止まる | 設定に書いたパスが存在しない | メッセージのパスを直すか、その行を設定から消す |
| `extract` が毎回「実行を見送りました」 | 間隔・蓄積ゲート | 正常。試すなら `--force --limit 3` |
| 「YAML 設定には PyYAML が必要です」 | PyYAML 未導入 | `pip install pyyaml`、または `agent-audit.json` に同じキーで書く |
| `[agent-error:env] エージェント CLI が見つかりません` | `agents:` に書いた CLI 名が未導入 | `doctor` の定義一覧にある名前を使う／その CLI を入れる |
| `[agent-error:quota]` で LLM 段が止まる | node-budget 超過 | 期間が変わるのを待つか、budget の上限を見直す |
| 設定を変えたのに反映されない | 別の設定ファイルが先に見つかっている | `doctor` 末尾の「設定: …」行で読まれている 1 枚を確認 |

`doctor` は**設定・源泉・ストアの現在地を 1 画面で見せる**コマンドです。困ったらまずこれを叩いてください。

---

## 10. 設定リファレンス

```yaml
# ---- 置き場 ----
audit_dir: ""                 # 書き先（既定 ~/.agents/audit）
budget_dir: ""                # node-budget の場所（既定 ~/.agents/budget）

# ---- 源泉 ----
sources: []                   # 収集する源泉を絞る（空 = 自動発見）
flow_buses: []                # agent-flow のバス
project_roots: []             # agent-project の state clone ルート
amigos_buses: []              # agent-amigos のバス
loop_logs: []                 # agent-loop の本体ログ（ファイル）

# ---- LLM ----
agent_cli: claude             # 既定の CLI
model:                        # 既定のモデル（省略時は CLI 定義の default_model）
agents:
  extract: {agent_cli: ollama, model: qwen3}
  distill: {}
  review: {}
agent_timeout: 300            # 1 回の CLI 実行のタイムアウト秒
argv_limit: 100000            # これを超えるプロンプトはファイルへ退避する

# ---- extract（map）----
extract_input_chars: 8000     # 1 レコードのダイジェスト上限
extract_filters: [failed, retried, verify-flip, needs, long-session]
extract_min_interval_hours: 6 # 0 で間隔ゲート無効
extract_min_records: 10
extract_max_calls: 40         # --force でも外れない

# ---- distill（reduce）----
distill_min_occurrences: 2    # 洞察になる最小の同種観測数
distill_min_interval_hours: 24
distill_min_new_observations: 5
distill_max_calls: 10         # --force でも外れない

# ---- 相関 ----
join_slack_sec: 120           # 台帳行とセッションを突き合わせる時間の遊び（秒）

# ---- 保持 ----
gc_auto: true
gc_interval_hours: 24
gc_keep_days: {records: 90, transcripts: 30, observations: 90, reports: 30}

# ---- 自己更新 ----
update_repo: ""               # 空 = skill-registry.json から解決
update_branch: main
update_check_interval: 21600
```

## 11. コマンド早見表

| コマンド | LLM | 何をするか |
|---|---|---|
| `collect [--source S]... [--since D] [--with-transcripts]` | — | 源泉の増分収集・正規化（末尾で自動 gc） |
| `usage [--period P] [--by K] [--json]` | — | トークン・コスト集計（実測 / 推定を別掲） |
| `stats [--period P] [--json]` | — | 実行品質集計（status・失敗クラス・verify） |
| `calibrate [--write]` | — | rates 較正の提案（`--write` で budget へ反映） |
| `extract [--limit N] [--force]` | map | レコード → 観測 |
| `distill [--limit N] [--review] [--force]` | reduce | 観測クラスタ → 洞察 |
| `report [--kind K] [--out F]` | — | Markdown レポート（`reports/` へ保存 + stdout） |
| `tasks [--mark-exported]` | — | 洞察 → 改善タスク JSON（stdout） |
| `gc [--dry-run]` | — | 種別別保持日数での掃除 |
| `doctor` | — | 源泉の到達性・設定・ストアの点検 |
| `update [--check] [--now]` | — | 自己更新 |
