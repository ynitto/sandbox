# agent-audit 利用ガイド兼 CLI 仕様

agent-audit は、agent-project、agent-flow、agent-amigos、agent-loop とエージェント CLI の実行記録を集め、利用量と失敗傾向を調べるための CLI です。収集と集計には LLM を使いません。実行記録から改善案を作る `extract` と `distill` だけが LLM を使います。

本書の前半は導入と調査手順、後半は収集元、保存形式、設定、CLI のリファレンスです。設計判断の背景は[設計書](../designs/agent-audit-design.md)、定期実行を含む導入手順は[セットアップガイド](../guides/agent-audit-setup.md)を参照してください。

対象は `tools/agent-audit/` の `agent_audit` パッケージです。正典スキーマは [`audit-record`](../../schemas/audit-record.schema.json)、[`audit-insight`](../../schemas/audit-insight.schema.json)、[`agent-cli`](../../schemas/agent-cli.schema.json) の `session_log` ブロックです。

## まず動かす

### インストール

Python 3.11 以降が必要です。YAML 設定を使う場合だけ PyYAML も必要です。

```bash
bash tools/agent-tools/install.sh --only agent-audit
agent-audit --help
```

### 収集して月間利用量を見る

まず、現在の設定で到達できる収集元を確認します。

```bash
agent-audit doctor
```

問題がなければ記録を増分収集し、月間利用量と実行品質を表示します。

```bash
agent-audit collect
agent-audit usage --period month --by agent_cli
agent-audit stats --period month
```

`collect` は同じ記録を重複登録しません。定期実行しても、前回以降に増えた分だけが反映されます。

### レポートを保存する

```bash
mkdir -p reports
agent-audit report --kind all --out reports/agent-audit.md
```

機械処理する場合は各コマンドの `--json` を使います。

```bash
agent-audit usage --period month --by agent_cli --json
agent-audit stats --period month --json
agent-audit report --kind knowledge --json
```

## よく使う調査

### CLI セッションを探す

```bash
agent-audit sessions --cli codex
agent-audit sessions --cli codex --messages SESSION_ID
```

会話本文も保存して検索したい場合は、収集時に明示します。

```bash
agent-audit collect --with-transcripts
```

transcript には会話本文が入ります。既定ではノード内の audit ディレクトリに置かれ、レポートにはそのまま転記されません。

### 特定期間だけ集計する

```bash
agent-audit collect --since 2026-08-01T00:00:00+09:00
agent-audit usage --period day --by agent_cli
agent-audit usage --period total --by workload
```

### 改善候補を作る

```bash
agent-audit extract
agent-audit distill
agent-audit tasks
agent-audit tune
```

ここでは `extract` と `distill` がエージェント CLI を呼びます。`tune` は既定で提案を表示するだけです。設定へ反映するときだけ `tune --apply` を使ってください。同様に、利用レートの書き戻しは `calibrate --write` を明示した場合だけ行われます。

## 収集元を追加する

agent-audit は自動発見できる収集元と、設定に場所を書く必要がある収集元を分けています。

- 自動発見: node-budget 台帳、対応 CLI のセッションログと quota
- 要設定: agent-flow バス、agent-project の状態ディレクトリ、agent-amigos バス、agent-loop ログ

設定ファイルは `agent-audit.yaml` を使います。探索順は `--config`、カレントディレクトリ、`.agents/`、`~/.agents/` です。

```yaml
project_roots:
  - /srv/agents/project-a
flow_buses:
  - /srv/agents/project-a/bus
amigos_buses:
  - /srv/agents/amigos-bus
loop_logs:
  - /var/log/agent-loop.log
```

明示した収集元に到達できない場合、`collect` は部分集計を成功扱いせず exit 2 で終了します。まず `doctor` でパスを直してください。

---

## CLI リファレンス

## 1. パイプライン

```
collect ─┬─ 決定的（LLM 不使用）─ qualify / usage / stats / ratings / trials / report / calibrate
         └─ extract（map）─▶ cluster（決定的・distill の内部段）─▶ distill（reduce）
                                                                    └─ report / tasks / tune
```

LLM を使うのは extract と distill（と任意の review）だけで、それ以外の全段は決定的です。
各サブコマンドは単発・有界で、watch / daemon を持ちません。定期実行は agent-loop / cron / CI の
側に置きます。

`cluster` はサブコマンドではありません。`distill` の内部段（`distill.cluster_observations`）で、
観測を決定的にまとめてから reduce へ渡します。

---

## 2. 源泉（collect）

`sources:` は有効にする収集器を絞る設定で、空なら全種が有効です。場所を自動発見するのは
`budget-ledger` / `cli-native` / `cli-quota` だけで、`flow-bus` / `project-root` / `amigos-bus` /
`loop-log` は設定に明示された場所だけを読みます。

すべて読み取り専用・増分・冪等（`state.json` のカーソル: mtime / ファイル末尾オフセット /
セッション updated_at）。源泉の場所も引数 / 設定 > 契約上の既定パスで解決し、環境変数は
見ません（定期実行の環境差で読む場所が黙って変わることを避ける）。

| source | 読む場所 | 取るもの |
|---|---|---|
| `budget-ledger` | 設定 `budget_dir`（既定 `~/.agents/budget/`）の `ledger/*.jsonl` | ledger 行 → `kind: ledger`（消費の一次事実）。観測行（`quota` / `model_escalation`・消費 0）は `kind: event` へ分ける |
| `cli-native` | `agents/<name>.json` の `session_log` 宣言（§3） | CLI 自身のセッション → `kind: session`。実測トークン・turn 数・transcript（`--with-transcripts` / 設定 `with_transcripts` で本文を統一セッションログ [`audit-session-log`](../../schemas/audit-session-log.schema.json) として副作用保存。保持は `gc_keep_days.transcripts`） |
| `cli-quota` | 各 CLI が自分で表示する契約枠（`claude` / `codex` / `copilot` / `kiro-cli` が PATH にあるときだけ） | 残枠のスナップショット → `kind: event`。モデル実行なし |
| `flow-bus` | 設定 `flow_buses` ＋ `project_roots` 配下の `bus/` | 終端 run の `meta.json` / `graph.json` / `events/*.jsonl` → `kind: run`、`results/*.json` → `kind: result` |
| `project-root` | 設定 `project_roots` | `run-log.jsonl` → `kind: run` |
| `amigos-bus` | 設定 `amigos_buses` | 終端 mission の `events/*.jsonl` → `kind: run`（turn 数と `cli_seconds`） |
| `loop-log` | 設定 `loop_logs` のファイル | ERROR / WARNING 行の粗い run 化 |
| `memory-store` | `skill-registry.json` の `skill_configs` から自動発見（`memory_stores` で上書き） | 記憶 3 層 + 共有路のメタデータ → `kind: memory` の snapshot。persona は件数と滞留日数だけ |

レコードの `kind` は `ledger` / `run` / `result` / `session` / `event` / `memory` /
`calibration` の 7 種です。

読めない源泉（明示設定されているのに到達できない）は「未収集」と明示して exit 2 で止まります。
黙って部分集計を全体と偽らないためです。

---

## 3. `session_log` 契約

「CLI のセッションログがどこに・どの形式であるか」は CLI の作法なので、
`agents/<name>.json` に additive な `session_log` ブロックとして宣言します。

```jsonc
"session_log": {
  "format": "jsonl-dir",           // 閉じた enum（下表）
  "paths": ["~/.claude/projects"], // グロブ可・列挙した場所をすべて読む
  "usage": true                    // 実測トークンを含むか
}
```

| format | 読む相手 | 備考 |
|---|---|---|
| `jsonl-dir` | 1 セッション = 1 `*.jsonl`（claude / codex / ollama 系） | 行直下に `role` / `content` を持つ行を会話として読む |
| `kiro-sqlite` | `~/.kiro/store.db` | 1 行に会話配列が丸ごと入る |

パーサは `agent_audit/readers.py` に format ごと 1 実装です。新しい CLI が既存 format なら
JSON への追記だけで収集できます。

`usage` は申告であり、飾りではありません。パーサが数字を取れても、定義が
`usage: false` と言っている CLI のセッションは実測（`measured`）として数えません。
数字そのものは記録に残るので、後から `true` へ変えれば読み直せます
（`SESSION_PARSER_REVISION` のカーソルが既存セッションを 1 度だけ読み直します）。

実測が入る CLI は秒レート（budget `rates.per_cli`）を持ちません。推定（保持秒 ×
レート）と実測は同じ実行を二度数えるので、`calibrate` はそれらの CLI を較正の対象から
外し、設定に残っている古いレートを `--write` で落とします。切替日は台帳へ 1 行だけ
残します（`event: usage_switch`）。切替の前後で同じ実行の記帳の意味が変わるため、
後から数字を読む人が境目を知れる必要があるからです。器は `quota_snapshot` と同じ
台帳イベント行で、別系統は作りません。

未知の format・`session_log` 未宣言の CLI は「未収集」と明示し、黙ってスキップしません。
`agent-audit sessions --cli <名前>` は 0 件のとき `cli.declared` / `cli.supported` を返すので、
読み手は「条件に当たらなかった」のか「その CLI は会話を残さない」のかを言い分けられます。

---

## 4. ストア

書き先は audit ディレクトリ。解決は `--audit-dir` > 設定 `audit_dir` > 既定
`~/.agents/audit/` の 3 段だけで、agent-audit 固有の環境変数は導入しないし、見ません。

```
~/.agents/audit/
  state.json                     # 収集カーソル {"<source>::<store>::<sid>": {"cursor": …}}
  records/<YYYYMMDD>.jsonl       # 正規化レコード（追記専用・O_APPEND）
  transcripts/<src>/<sid>.jsonl  # 任意（--with-transcripts / 設定 with_transcripts）。
                                 # 統一セッションログ（audit-session-log.schema.json:
                                 # meta 1 行 + message 行）。ノード外へ出さない
  observations/<YYYYMMDD>.jsonl  # extract の出力（追記専用）
  insights/<id>.json             # distill の出力（1 洞察 1 ファイル）
  decisions/<id>.json            # tune の型付き調整候補・適用と退役の記録
  reports/<ts>-<kind>.md         # report の出力
```

### 4.1 audit ディレクトリの外へ書く 2 経路

いずれも明示フラグが要ります。フラグ無しでは提案を表示するだけです。

| 経路 | 書き先 | 何を守るか |
|---|---|---|
| `calibrate --write` | budget `config.json` の `rates` | 契約が「較正は管理面が行い書き戻す」と定めたキーだけ |
| `tune --apply` | `agent-tuning` の `profiles.<name>.injections\|env`、`agent-profiles` の `tiers.<name>.candidates`、budget `config.json` の `rates.per_cli.<cli>` | 型付きの許可パスだけ。任意パス・任意コマンドは受け付けない |

書いた事実は `updated_by`（`agent-audit` / `agent-audit-retire`）と decision の `applied` に残します。

---

## 5. CLI

すべて単発・有界。終了コードは 0 = 成功（ゲートによる見送りを含む）/ 1 = LLM 段の停止・
更新の取り込み失敗 / 2 = 源泉が読めない・使い方の誤り。

| サブコマンド | LLM | 概要 |
|---|---|---|
| `collect [--source S]... [--since D] [--with-transcripts]` | 不使用 | 増分収集・正規化 |
| `usage [--period P] [--by K] [--json]` | 不使用 | トークン・コスト集計（measured / estimated 別掲） |
| `stats [--period P] [--json]` | 不使用 | 実行品質集計 + LLM 判断ごとの決定的ルール一致率 |
| `ratings [--period P] [--methods] [--json]` | 不使用 | 仕事種別×モデルの格付け |
| `trials [--period P] [--json]` | 不使用 | 2 variant trial の PASS 率・平均消費と差分判定 |
| `calibrate [--write]` | 不使用 | rates 較正の提案（`--write` で budget config へ反映） |
| `tune [--apply] [--period P] [--json]` | 不使用 | 洞察 → 型付き調整候補。`--apply` で許可パスだけ宣言へ昇格し、悪化すれば退役 |
| `qualify [--apply] [--window-days N]` | 不使用 | 本番 receipt から候補適格性を昇格・降格・期限切れ。`--apply` で qualifications.json へ原子書換（無変化なら書かない） |
| `seed --from-recommendation F [--apply] [--force]` | 不使用 | おすすめ構成の適格性ブロックを qualifications.json へ置く。生成はしない |
| `extract [--limit N] [--force]` | map | レコード → 観測。ゲート（§6）を通ったときだけ LLM を呼ぶ |
| `distill [--limit N] [--review] [--force]` | reduce | 観測クラスタ → 洞察 |
| `report [--kind K] [--out F] [--json]` | 不使用 | Markdown レポート（`--kind knowledge` は記憶層の健全性。`--json` は knowledge 専用） |
| `tasks [--mark-exported]` | 不使用 | 洞察 → 改善タスク（`task.schema.json`）。明示時だけ出力済み印を付ける |
| `gc [--dry-run]` | 不使用 | 種別別保持日数での掃除（`gc_auto` で collect へ相乗り） |
| `reclean [--agent-cli N] [--dry-run]` | 不使用 | クリーニングルール改訂後の transcript 再生成 |
| `sessions [--cli N] [--since T] [--until T] [--cwd-contains S] [--limit N] [--messages ID]` | 不使用 | CLI ネイティブセッションの検索・本文取得 |
| `doctor` | 不使用 | 源泉の到達性・`session_log` 宣言の有無・未収集 CLI・clean ルールのスキップ・記憶ストアの到達性・効かない設定キー（§7.1） |
| `update [--check] [--now]` | 不使用 | 自己更新 |

`run`（collect → extract → distill → report の一括）は設けません。定期駆動は agent-loop 同梱の
`audit-calibrate-hook.py` が collect → qualify → calibrate → extract → distill → tune を順に呼びます。本体に
複合コマンドを持つと「どこまで進んで止まったか」の状態管理が増えるためです。

---

## 6. LLM 段のゲートと上限

extract / distill は、次を全部通ったときだけ LLM を呼びます。

| 段 | 間隔ゲート | 蓄積ゲート | 呼び出し上限 |
|---|---|---|---|
| `extract` | `extract_min_interval_hours`（既定 6） | `extract_min_records`（既定 10） | `extract_max_calls`（既定 40） |
| `distill` | `distill_min_interval_hours`（既定 24） | `distill_min_new_observations`（既定 5）／ クラスタは `distill_min_occurrences`（既定 2）以上 | `distill_max_calls`（既定 10） |

`--force` が外せるのはゲートだけで、呼び出し上限と node-budget は外せません。実行前に
node-budget を読み、超過中は LLM 段を実行しません。

段ごとにエージェントとモデルを選べます（設定 `agents.<purpose>`。purpose は
`extract` / `distill` / `review`）。管理面（`agent-control`）の purpose 別上書きも効きます。

---

## 7. 設定ファイル

`agent-audit.yaml` / `.yml` / `.json`。探索順は `--config` → `<cwd>` → `<cwd>/.agents` →
`<cwd>/.agent` → `<agent_home>`。優先順位は CLI > 設定 > 組み込み既定。

| キー | 既定 | 意味 |
|---|---|---|
| `audit_dir` | `~/.agents/audit` | 書き先 |
| `budget_dir` | `~/.agents/budget` | node-budget の場所 |
| `sources` | `[]` | 空 = 全種を有効。絞りたいときだけ列挙 |
| `with_transcripts` | `false` | collect の副作用でセッション本文を統一セッションログ（`transcripts/<cli>/<sid>.jsonl`）として保存。`--with-transcripts` と同じ（定期実行の有効化はこちらで） |
| `flow_buses` / `project_roots` / `amigos_buses` / `loop_logs` | `[]` | 明示指定が要る源泉の場所 |
| `memory_stores` | `{}` | `ltm_dirs` / `wiki_root` / `persona_home` / `moltbook_home`。自動発見の上書きだけ書けばよい |
| `memory_dormant_days` | `30` | `access_count=0` のまま眠っている日数（＝退役候補） |
| `memory_share_threshold` | `70` | publish 待ちとみなす `share_score` |
| `memory_retention_risk` | `0.3` | 忘却リスク帯（`retention_score` がこれ未満） |
| `agent_cli` | `claude` | LLM 段の既定 CLI |
| `model` | `null` | 既定モデル |
| `agents` | `{}` | purpose 別の上書き（`extract` / `distill` / `review`） |
| `agent_timeout` | `300` 秒 | LLM 1 回の実行 |
| `argv_limit` | `100000` | argv 渡しの最大バイト数 |
| `extract_input_chars` | `8000` | 1 レコードから渡す最大文字数 |
| `extract_filters` | `[failed, retried, verify-flip, needs, long-session]` | extract に回すレコードの絞り込み |
| `extract_min_interval_hours` / `extract_min_records` / `extract_max_calls` | `6` / `10` / `40` | §6 |
| `distill_min_occurrences` / `distill_min_interval_hours` / `distill_min_new_observations` / `distill_max_calls` | `2` / `24` / `5` / `10` | §6 |
| `tune_period` | `month` | tune の集計期間 |
| `tune_min_occurrences` / `tune_min_outcomes` / `tune_min_confidence` | `3` / `3` / `high` | 昇格ゲート |
| `tune_quality_floor` / `tune_max_quality_drop` / `tune_evaluation_runs` | `0.8` / `0.05` / `3` | 昇格後の評価と退役 |
| `tune_max_promotions_per_run` / `tune_max_total_promotions` | `1` / `20` | 昇格の総量規制 |
| `trial_min_outcomes` | `3` | trial 比較の判定に要る片側あたりの下限 |
| `tuning_file` / `profiles_file` | `""` | agent-tuning / agent-profiles の場所（既定は契約位置） |
| `join_slack_sec` | `120.0` | 相関の時刻許容幅 |
| `gc_auto` / `gc_interval_hours` | `true` / `24` | collect 末尾の定期クリーンアップ |
| `gc_keep_days` | `{records: 90, transcripts: 30, observations: 90, reports: 30}` | 種別別保持日数（0 = その種別を消さない） |
| `update_enabled` | `true` | false で自己更新を止める（`update` は何もせず 0 で終わる） |
| `update_repo` / `update_branch` / `update_subdir` / `update_installer` | `""` / `main` / — / `install.sh` | 自己更新の取得先 |

`insights` と `state.json` は gc の対象外です（洞察を消すと同じクラスタを再蒸留して二重に払う）。

### 7.1 受け付けるが効かないキー

| キー | 理由 |
|---|---|
| `update_check_interval` | agent-audit は単発・有界で、更新を定期チェックする常駐経路を持たない（間隔で律速する相手がいない）。更新は `agent-audit update` を叩いたときだけ調べる |

`doctor` が、設定ファイルに書かれているこれらのキーを報告します。黙って無視すると
「設定したのに効かない」が原因不明の不具合になるためです。一覧の正典は
`agent_audit/configfile.py` の `INERT_KEYS`。

---

## 8. 不変条件

1. 読み手に徹する。他ツールのバス・状態リポジトリ・台帳・CLI ストアへ書かない。書くのは
   audit ディレクトリと、明示フラグを付けたときの型付き許可パスだけ（§4.1）。
2. 決定的にできる処理に LLM を使わない。収集・正規化・クリーニング・相関・集計・
   クラスタリング・レポート描画は stdlib のみで再現可能。LLM は extract / distill / review の
   3 purpose に閉じる。
3. 偽の実測を作らない。measured と estimated を混ぜない。相関が一意でなければ結合しない。
   読めない源泉は「未収集」と明示して exit 2。
4. 必ず止まる。全サブコマンド単発・有界。LLM 段は段別上限 × ゲート × node-budget ×
   処理済みカーソルで停止し、`--force` が外せるのはゲートだけ。
5. 設定の解決は 引数 > 設定ファイル > 組み込み既定 のみ。書き先・源泉の場所を含め、
   agent-audit 固有の環境変数を導入しない・見ない。
6. 生の会話・秘密・絶対パスをノード外へ出さない。共有可能な層（集計・観測・洞察・タスク）と
   不可の層（transcript）を物理的に分け、export は決定的スクラバを通す。
7. 蒸留の根拠を追跡できる。洞察 → 観測 → レコード → 源泉の参照鎖を欠かさない。根拠の無い
   洞察は生成しない（distill はクラスタ単位でだけ走る）。
8. CLI の作法の変更は `agents/<name>.json` 1 ファイルで完結する。

---

## 9. パッケージングと自己更新

| 項目 | 値 |
|---|---|
| 配置 | `agent-audit.py`（shim）+ `agent_audit/`（通常 import のパッケージ。fragment 分割はしない） |
| インストール | `tools/agent-tools/install.sh` のビルド対象。agentcore 同梱 zipapp → `~/.local/bin/agent-audit` |
| Python 下限 | 3.11。pip 依存なし（PyYAML は YAML 設定時のみ任意） |
| 自己更新の取得範囲 | `TOOL_SUBDIR = "tools/agent-audit tools/agent-tools"`（2 パス sparse-checkout。engine のみ取得すると installer が agentcore を束ねられず自己更新が永久に失敗する） |
| 更新状態 | `<agent_home>/agent-audit.update.json` |

---

## 付録. テスト

`tools/agent-audit/tests/` に 16 ファイル・167 件。LLM 段は差し替えで決定的にテストします。

```bash
cd tools/agent-audit && python3 -m unittest discover -s tests
```

`tests/_shared.py` が `KIRO_SKILL_REGISTRY` / `KIRO_AGENTS_DIR` / `HOME` を一時ディレクトリへ
逃がします。同梱定義（リポジトリの `agents/`）は `KIRO_AGENTS_DIR` では消せない。探索順の
最後に必ず入るので、その `session_log.paths` の `~` が開発者の実ストアを指し、収集件数が環境
依存になります。ホームごと逃がすのはそのためです。
