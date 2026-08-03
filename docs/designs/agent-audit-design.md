# agent-audit — 設計書

> 最終更新: 2026-08-03 ／ 関連: `tools/agent-tools/`（agentcore・共通インストーラ）,
> `tools/kiro-log-exporter/`（収集の先例）, `schemas/node-budget.schema.json`,
> `schemas/agent-cli.schema.json`, `docs/designs/agent-tools-concept.md`（上位文書）
>
> 本書は agent-audit の**唯一の設計正典**。実装と差が出たら本書を更新する。
>
> **効く柱・原則**: 柱2 × 学習ループ（P4: 効果測定の再現、P5: ノードに死蔵される知見の捕捉）
> ／ C3（決定的にできる集計を LLM・人に回さない）・C7（読み手に徹し書き手を増やさない・
> 必ず止まる）・C8（配布で終わらせず適用・検証・蒸留まで閉じる）・C1（生の会話・秘密は
> ノードに留める）。

`agent-audit` は、**agent-project / agent-flow / agent-amigos / agent-loop の実行証跡と、
エージェント CLI 自身のセッションログを収集・正規化し、(a) トークン使用量の実測集計と
(b) 知見・スキル改善点の蒸留を行う独立 CLI**。集計・結合・レポートは決定的（LLM 不使用）、
LLM は「観測の抽出」と「洞察への蒸留」の 2 段だけに限定し、段ごとにエージェント CLI と
モデルを選べる（弱いモデルへ分担可能）。

**位置づけ（codd-gate と同型の独立ツール）**: agent-audit は 4 ツールのどれにも依存しない。
結合は**ファイルの読み取りとデータ契約のみ**——各ツールが既に書いているバス・状態ファイル・
台帳を読むだけで、どのツールの実装も無改造。エージェント CLI 単独利用（agent-* エンジンを
使っていないノード）でも、CLI ネイティブのセッションストアだけを源泉にして完結する。
実装は Python（stdlib のみ・PyYAML は設定の任意依存）、agentcore を共有し、インストーラ・
自己更新・設定の慣習を他の agent-tools と揃える。

---

## 1. 背景と課題

調査（2026-08-03、実装横断）で確定した現状:

1. **トークン計測の配管は完成しているが、実測が流れていない。**
   `agentcore.agentcli.parse_usage`（stderr の `@agent-usage tokens_in= tokens_out=`）→
   `UsageText` → node-budget 台帳（`~/.agents/budget/ledger/<YYYYMMDD>.jsonl` の
   `tokens_in/tokens_out/usd`）の経路は agent-flow / agent-project / agent-amigos に実装済み。
   しかし同梱の CLI 定義（claude / codex / kiro / …）はどれも `@agent-usage` を出さないため、
   台帳の実態は「実測秒 + rates による読み出し時推定」。一方で **CLI 自身のセッションログ
   には実測トークンが残っている**（claude: `~/.claude/projects/**/*.jsonl` の usage、
   codex: `~/.codex/sessions/**/rollout-*.jsonl`）のに、読むツールが無い。
2. **どのツールも transcript を保存していない。** flow は `results/<id>.json.output` に
   stdout を残すが、amigos は JSON パース後に生テキストを破棄、project は抜粋
   （needs / 検証 evidence 2000 字）だけ、loop は tmux ペインに打鍵するため stdout 自体を
   見ない。セッション ID もどのツールも記録しない。CLI ネイティブストアを読む先例は
   `tools/kiro-log-exporter`（`~/.kiro/store.db` → 正規化 → 増分 state）のみ。
3. **決定的に読める実行証跡は既に豊富。** flow バスの `events/*.jsonl` / `meta.json` /
   `final.json`、project の `run-log.jsonl` / `run-log/<node>/<run-id>.json` / `journal.md` /
   `archive/` / `decisions/`、amigos の `events/*.jsonl`（`cli_seconds`）/ メッセージ /
   `delivery.json`、失敗トリアージの `[agent-error:<class>]` タグ、verify receipt。
   これらの集計に LLM は要らない（C3）。
4. **知見はノードに死蔵される（P5）。** 学習ループの「捕捉→共有→蒸留→強制→評価」のうち、
   タスク経由の decisions / learn は agent-project が担うが、**セッションそのもの**
   （失敗の経緯、リトライの内訳、プロンプトの癖、スキルが選ばれなかった事実）を横断的に
   観測する道具が無い。効果測定（P4）も `kiro-log-exporter` 集計を手作業で突き合わせている。

agent-audit はこの 4 点を「読むだけの独立 CLI + 限定された LLM 蒸留」で埋める。

## 2. 全体像

```
                （読み取りのみ・無改造）                        audit ディレクトリ（唯一の書き先）
  ┌─ 源泉 ──────────────────────────────┐   collect    ┌──────────────────────────────┐
  │ node-budget 台帳 ledger/*.jsonl     │ ──────────▶ │ records/<YYYYMMDD>.jsonl      │
  │ agent-flow バス runs/<id>/…         │  決定的      │   （正規化レコード・追記専用）  │
  │ agent-project run-log.jsonl 他      │  増分        │ transcripts/…（任意・ローカル）│
  │ agent-amigos missions/<mid>/…       │  冪等        │ state.json（収集カーソル）     │
  │ agent-loop agent-loop.log / slots   │             └──────────────┬───────────────┘
  │ CLI ネイティブストア                 │                            │
  │  （agents/<name>.json session_log） │        ┌───────────────────┼───────────────────┐
  └─────────────────────────────────────┘        │ 決定的（LLM 不使用）│ LLM（段別にモデル選択）
                                                 ▼                   ▼
                                      usage / stats / report   extract（map・弱モデル可）
                                      calibrate（rates 提案）        │ observations/*.jsonl
                                                 ▲                   ▼
                                                 │             cluster（決定的）
                                                 │                   ▼
                                                 │             distill（reduce・中〜強モデル）
                                                 │                   │ insights/<id>.json
                                                 └────────┬──────────┘
                                                          ▼
                                            report（Markdown/JSON）・tasks（task.schema.json）
                                            → agent-project の汎用 intake / 人が読む
```

パイプラインは **collect → (usage|stats|report) と collect → extract → cluster → distill →
(report|tasks)** の 2 系統。LLM を使うのは extract と distill（と任意の review）だけで、
それ以外の全段は決定的。各サブコマンドは codd-gate と同じく**単発・有界**（watch / daemon を
持たない）。定期実行は agent-loop / cron / CI の側に置く。

## 3. データモデルとストア

書き先は audit ディレクトリ。解決は **`--audit-dir` 引数 > 設定 `audit_dir` > 組み込み既定
`~/.agents/audit/`** の 3 段だけで、**agent-audit 固有の環境変数は導入しないし、見ない**。
理由: 本ツールは cron・agent-loop の定期プロンプト・手動と実行環境が変わりやすく、環境変数
依存があると「同じ設定ファイルなのに置き場所が違う」事故を作る（他ツールの `$AGENT_*_DIR`
契約はその契約の所有者のもの——agent-audit は**源泉の読み取り位置も含めて**引数と設定
ファイルだけで決める。§4.1）。**agent-audit がここ以外へ書くことはない**（例外は §5.3 の
`calibrate --write` だけ。C7: 他ツールのバス・状態リポジトリ・台帳の書き手にならない）。

```
~/.agents/audit/
  state.json                     # 収集カーソル {"<source>::<store>::<sid>": {"cursor": …}}
  records/<YYYYMMDD>.jsonl       # 正規化レコード（追記専用・O_APPEND）
  transcripts/<src>/<sid>.log    # 任意（--with-transcripts）。ノード外へ出さない
  observations/<YYYYMMDD>.jsonl  # extract の出力（追記専用）
  insights/<id>.json             # distill の出力（1 洞察 1 ファイル）
  reports/<ts>-<kind>.md         # report の出力
```

### 3.1 正規化レコード（`schemas/audit-record.schema.json` を新設）

1 行 = 1 観測単位。`kind` で 3 種を同じ封筒に入れる:

```jsonc
{"id":"aud-…",                    // sha256(source, store, native_id) の短縮。冪等キー
 "ts":"2026-08-03T10:00:00Z","kind":"session|run|ledger",
 "source":"claude-native|kiro-native|flow-bus|project-runlog|amigos-bus|loop-log|budget-ledger",
 "node":"pc-a","cwd":"~/repo",    // cwd はホーム相対へ正規化（絶対パスを残さない・C1）
 "tool":"agent-flow","workload":"flow","ref":"worker",         // ledger/run 系
 "agent_cli":"claude","model":"sonnet","session_id":"…",       // 判った範囲で
 "seconds":42.3,"tokens_in":12000,"tokens_out":800,"usd":0.05,
 "measured":true,                 // 実測（セッションログ由来）か推定か
 "status":"done|failed|…","error_class":"quota|auth|env|transient|…",
 "turns":14,"retries":2,"verify":"pass|fail",
 "links":["aud-…"],              // 相関済みレコードへの参照（§5.3）
 "excerpt_ref":"transcripts/claude/<sid>.log"}                 // 本文は records に入れない
```

**transcript 本文はレコードに入れない**。records は集計・抽出の索引で、本文は任意保存の
`transcripts/` にだけ置く（レコードを軽く保ち、共有可能な層と不可の層を物理的に分ける）。

### 3.2 観測と洞察（`schemas/audit-insight.schema.json` を新設）

```jsonc
// observations/<YYYYMMDD>.jsonl — extract（map）の出力。1 行 1 観測
{"id":"obs-…","record_id":"aud-…","ts":"…",
 "kind":"learn|avoid|skill-gap|prompt-issue|config-issue",
 "text":"検証コマンドの timeout 不足で transient 失敗が 3 回連続した",
 "evidence":["aud-…#L120-L140"],  // レコード/transcript 内の位置参照。生テキストは持たない
 "extract_agent":"ollama","extract_model":"qwen3"}            // どのモデルが抽出したか（P4）

// insights/<id>.json — distill（reduce）の出力
{"id":"ins-…","ts":"…","statement":"…（一般化した知見）",
 "kind":"rule-candidate|skill-improvement|config-fix|usage-optimization",
 "scope":{"tool":"agent-flow","repo":null},
 "observation_ids":["obs-…"],"occurrences":3,
 "suggested_action":"rules.md 候補文 / skill の修正案 / 設定変更案",
 "confidence":"low|medium|high","review":null,               // review 段を通せば verdict が入る
 "exported":false}
```

観測・洞察は**証跡への参照**を必ず持つ（C8: 昇格根拠を追跡できる）。単発の観測を
そのまま洞察にしない——distill は同種観測のクラスタ単位でだけ走る（§6.3）。

### 3.3 保持と定期クリーンアップ

audit ディレクトリは放置すると transcript を中心に際限なく育つ。掃除は 2 系統で、
どちらも**種別ごとに保持日数を調整できる**:

- **明示 gc**: `agent-audit gc [--dry-run]`。`gc_keep_days` の種別別日数
  （records / transcripts / observations / reports）を超えたファイルを削除する。
  **insights と state.json は gc 対象外**——洞察は蒸留の成果そのもので小さく、消すと
  同じクラスタを再蒸留してトークンを二重に払う（削除は人が明示的にファイルを消す）。
- **自動 gc（定期クリーンアップ）**: `gc_auto: true`（既定）のとき、`collect` の末尾で
  前回 gc から `gc_interval_hours`（既定 24）以上経過していれば同じ掃除を 1 回走らせ、
  実行時刻を state.json に記録する。daemon を持たない本ツールで「定期」を成立させる
  方法は、定期に走る唯一のコマンド（collect）への相乗りだけ——agent-project の
  enforce_retention が gc に相乗りするのと同じ構図で、新しい常駐や書き手を増やさない
  （C7）。頻度は `gc_interval_hours` で、無効化は `gc_auto: false` でノードごとに選べる。

処理済みカーソル（state.json）は records の削除より長く生き残る——records を消しても
「収集済み」の事実は残るので、gc 後の collect が同じセッションを再収集して LLM 段へ
再投入することはない。

## 4. 収集（collect・決定的）

### 4.1 源泉と収集器

収集器は `sources:` 設定（省略時は全種を自動発見）で有効化する。すべて読み取り専用・
増分・冪等（`state.json` のカーソル: mtime / ファイル末尾オフセット / セッション
updated_at。`kiro-log-exporter` の `.kiro_export_state.json` と同じ規律）。
源泉の場所も **引数 / 設定 > 契約上の既定パス** で解決し、環境変数は見ない（読む相手の
契約が env 上書きを持っていても、agent-audit 側は設定ファイルへ明示させる——定期実行の
環境差で読む場所が黙って変わることを避ける）。

| source | 読む場所 | 取るもの |
|---|---|---|
| `budget-ledger` | 設定 `budget_dir`（既定は契約の `~/.agents/budget/`）の `ledger/*.jsonl` | ledger 行 → `kind:ledger` レコード（消費の一次事実） |
| `flow-bus` | 設定 `flow_buses:`（既定はプロジェクト root の `bus/`） | `runs/<id>/meta.json`・`events/*.jsonl`・`results/*.json`・`final.json` → `kind:run`。error_class・retries・verify 判定を抽出 |
| `project-root` | 設定 `project_roots:` | `run-log.jsonl`・`run-log/<node>/*.json`・`archive/`（納品書の cost 行）・`needs/`・`decisions/` → `kind:run` |
| `amigos-bus` | 設定 `amigos_buses:` ＋ `<home>/deliveries/` | `missions/<mid>/events/*.jsonl`（turn/cli_seconds）・`delivery.json` → `kind:run` |
| `loop-log` | `~/.agents/agent-loop.log`・`~/.agents/slots/` | 送信・失敗行の粗い run 化（loop は計測点が薄い現実をそのまま記録） |
| `cli-native` | `agents/<name>.json` の `session_log` 宣言（§4.2） | CLI 自身のセッション → `kind:session`。**実測トークン・turn 数・transcript** |

### 4.2 CLI ネイティブストアの汎用化 — `session_log` 契約（additive）

「CLI のセッションログがどこに・どの形式であるか」は CLI の作法なので、
**`agents/<name>.json` に additive な `session_log` ブロックとして宣言する**
（`schemas/agent-cli.schema.json` の改訂。受入条件「CLI の作法の変更は JSON 1 ファイルで
完結」をログの所在にも適用する）:

```jsonc
// agents/claude.json への追記例
"session_log": {
  "format": "jsonl-dir",                      // パーサ実装は agent-audit に 1 実装（C7）
  "paths": ["~/.claude/projects"],            // グロブ可・先勝ち
  "usage": true                               // 実測トークンを含むか
}
// kiro.json: {"format": "kiro-sqlite", "paths": ["~/.kiro/store.db"], "usage": false}
// codex.json: {"format": "jsonl-dir", "paths": ["~/.codex/sessions"], "usage": true}
```

- **format は閉じた enum**（初期: `jsonl-dir` / `kiro-sqlite`）。パーサは
  `agent_audit/readers/` に format ごと 1 実装。新 CLI が既存 format なら JSON 追記だけで
  収集できる。未知の format・`session_log` 未宣言の CLI は「未収集」と明示して黙って
  スキップしない（fail-close の报告、codd-gate の「未スキャン repo」と同型）。
- kiro の SQLite 読みは `kiro-log-exporter` の探索・パース手順（テーブル自動検出・
  WSL→Windows パス探索）を移植する。`kiro-log-exporter` 自体は IDE 含む単体エクスポータ
  として残し、agent-audit は CLI セッションの正規化・集計側を担う（重複コードは
  移植時に exporter 側から関数単位で借用し、二重管理になる共通化はしない——
  用途が「人が読むログ書き出し」と「機械集計」で違う）。

### 4.3 相関（join・決定的）

セッション ID をどのツールも記録していないため、結合は**決定的なヒューリスティクスに
限定し、確度を必ず併記する**（LLM に推測させない・C3）:

1. `kind:ledger` × `kind:session`: `agent_cli`・`model` が一致し、セッションの時間範囲が
   ledger 行の `[ts - seconds, ts]` と重なる（±`join_slack_sec`、既定 120）。一意に決まれば
   `links` を張り、セッション実測トークンを ledger 行由来レコードの `measured` 集計に使う。
2. `kind:run` × `kind:ledger`: `ref`（`<mission>/<role>` / purpose）・`node`・時間窓。
3. 複数候補・候補ゼロは**結合しない**。集計は未結合分を
   「実測不能（推定のみ）」の行として別掲する。偽の実測を作らない（no fake green と同じ
   フェイルクローズ）。
4. 相関は**読み出し時に毎回同じ入力から導出する**（usage / calibrate が呼ぶ純関数）。
   records は追記専用なので相関を書き戻さない——レコードの `links` は将来の収集時
   相関（源泉がセッション ID を持つ場合）のための予約フィールド。

将来エンジン側がセッション ID を記録するようになれば（additive な改善提案として別途）、
この節のヒューリスティクスは自然に不要へ退化する。本設計はエンジン無改造を前提に置く。

## 5. 決定的集計（usage / stats / calibrate）

### 5.1 `agent-audit usage` — トークン・コスト集計

- 一次事実は台帳（ledger 行）。セッション実測（§4.3 で結合済み）があれば **measured** 列、
  無ければ rates 推定で **estimated** 列に計上し、**両者を混ぜた単一の数字を出さない**。
- 軸: `--period day|month|total`（台帳と同じ UTC 区切り）× `--by workload|tool|agent_cli|model|ref|node`。
- 出力: 表（人向け）と `--json`。dashboard が読む場合もこの JSON を契約にする
  （dashboard は現在 ledger の seconds しか集計していない——トークン表示はこの出力を
  読む表示層の変更で足せる）。

### 5.2 `agent-audit stats` — 実行品質の集計

records の run 系から決定的に導く: 完了率、`[agent-error:<class>]` 別の失敗内訳、
リトライ回数分布、verify pass/fail 率、needs エスカレーション数、heal 発動数、
ツール別・期間別。すべて既存タグ・既存ファイルの再集計であり LLM 不使用。

### 5.3 `agent-audit calibrate` — rates 較正の管理面実装

node-budget 契約は「rates の較正（実測行の中央値）は管理面が行い書き戻す」と定めている。
agent-audit はこの管理面の CLI 実装になる: 実測レコードから `<agent_cli>:<model>` 別の
tokens/秒 中央値を計算し、既定では**提案として表示するだけ**。`--write` を明示したときに
限り `budget_dir` の `config.json` の `rates` を更新する（唯一の外部書き込み。
契約上の書き手が管理面と定義されているキーだけに触れ、`updated_by: "agent-audit"` を残す）。

## 6. LLM 蒸留パイプライン（extract → cluster → distill［→ review］）

### 6.1 段の分割とモデル選択

LLM 処理は「1 レコードを観測に落とす map」と「観測クラスタを洞察に畳む reduce」に分割し、
段ごとに要求能力を変える。設定は他エンジンと同じ `agents:` パターン
（`agentcore.agentcli` の purposes 流用）:

```yaml
agent_cli: claude            # 既定
agents:
  extract:  {agent_cli: ollama, model: qwen3}   # map: 局所要約。弱モデル・ローカルで十分
  distill:  {agent_cli: claude, model: sonnet}  # reduce: 一般化。中〜強モデル
  review:   {agent_cli: claude, model: opus}    # 任意: 洞察の検証。使わなければ無効
```

- 呼び出しは 3 エンジンと同一の型: `agentcore.agentcli.load_cli / headless_cmd /
  spill_prompt`、`NO_COLOR=1`・タイムアウト・空応答は失敗・`parse_usage` → `UsageText`。
- **agent-audit 自身の消費も台帳へ記帳する**（workload は additive な `audit`）。
  実行前に node-budget を読み、超過中は LLM 段を実行しない（`[agent-error:quota]` と同じ
  分類で報告して終了。C1・C7: 集計のためのループが財布を燃やさない）。
- agent-control の lifecycle / cli / model 上書きにも従う（他エンジンと同順位:
  control > CLI 引数 > 設定 > 既定）。

### 6.2 extract（map・弱モデル可）

- 入力は 1 レコードの**ダイジェスト**（status・error_class・retries・verify・
  transcript 抜粋を `extract_input_chars`（既定 8000）に決定的に切り詰め）。長大
  transcript を丸ごと食わせない——弱いモデルに分担できるのはこの入力制限があるから。
- **対象の選抜は決定的**: 既定では「失敗 run・リトライ ≥ 2・verify fail→pass・needs 化・
  異常に長いセッション」だけを抽出対象にする（`extract_filters`）。成功して何も起きなかった
  レコードに LLM を使わない（C3）。
- 出力は観測 JSON（§3.2）。スキーマ不一致は 1 回だけ修復再問い合わせ
  （agent-flow の format_retries と同じ L2）、それでも駄目なら**その観測を捨てて先へ進む**
  （抽出漏れは次回 run で再試行可能。パイプラインは止めない）。
- 冪等: `record_id` 単位で処理済み管理（state.json）。同じレコードを二度抽出しない。

### 6.3 cluster（決定的）と distill（reduce）

- クラスタリングは stdlib だけの決定的処理: `kind` × `tool` × error_class ×
  正規化キーワード（形態素解析は使わず、記号除去 + 小文字化 + n-gram 重なり率の固定閾値）。
  賢さより再現性を優先する——同じ観測集合からは必ず同じクラスタが出る。
- distill は**クラスタ単位でだけ**呼ぶ。1 洞察 = 同種観測 ≥ `distill_min_occurrences`
  （既定 2）。プロンプトには観測文と証跡参照だけを入れ、洞察文・適用範囲・提案アクション
  （rules.md 候補文 / skill 修正案 / 設定変更案）を出させる。
- クラスタのハッシュで冪等管理。既存洞察と同じクラスタに観測が増えたときは
  「洞察の改訂」として同 id を上書きし、`occurrences` を更新する（append ではなく
  1 洞察 1 ファイルにした理由——観測が育つと洞察も育つ）。
- review（任意）: 洞察を証跡と突き合わせて verdict（supported / weak / refuted）を
  付ける第三の purpose。refuted は `exported` 対象から外す。既定は無効
  （LLM 消費を増やす段は opt-in）。

### 6.4 実行タイミングの調整（トークン削減）

LLM を「いつ・どれだけ」使うかは extract / distill で**独立に**調整できる。すべて
決定的なゲートで、ゲートを通らない実行は LLM を 1 回も呼ばずに即終了する（cron や
agent-loop に高頻度で `collect && extract && distill` を書いても、LLM 消費は設定した
リズムを超えない——駆動の頻度と LLM の頻度を分離する）:

- **間隔ゲート**: `extract_min_interval_hours`（既定 6）/ `distill_min_interval_hours`
  （既定 24）。前回成功時刻（state.json）からの経過で判定。extract を細かく・distill を
  粗くといった段別のリズムが作れる（distill はクラスタが育ってから 1 回呼ぶ方が
  観測あたりのトークン効率が良い）。
- **蓄積ゲート**: `extract_min_records`（既定 10。未抽出の候補レコードがこの数に満たなければ
  走らない）/ `distill_min_new_observations`（既定 5。新規観測がこの数未満なら走らない）。
  少量を細切れに処理する呼び出し回数の無駄を防ぐ。
- **段別上限**: `extract_max_calls`（既定 40）/ `distill_max_calls`（既定 10）。
  1 実行あたりの LLM 呼び出し上限を段ごとに持つ（弱いモデルの extract は多め、
  強いモデルの distill は少なめ、が既定の意図）。
- `--force` はゲート（間隔・蓄積）だけを飛ばす。上限と node-budget は `--force` でも
  飛ばせない（人の手でも財布のガードは外れない・C1）。

### 6.5 有限性

LLM 段には停止条件を重ねる（C7): 段別上限（`extract_max_calls` / `distill_max_calls`）・
間隔と蓄積のゲート・node-budget 超過での停止・処理済みカーソルによる再実行の自然減。
全段が単発サブコマンドなので「止まらない自動化」は構造的に作れない。

## 7. 出力と学習ループへの接続

- `agent-audit report [--kind usage|quality|insights|all]` — Markdown（`reports/` へ保存 +
  stdout）。usage・stats・洞察一覧を 1 枚に束ねる。人が読む面はこれだけ。
- `agent-audit tasks [--json]` — `exported: false` の洞察のうち `suggested_action` が
  具体化しているものを **`schemas/task.schema.json` 形の改善タスク**として出力する
  （codd-gate `tasks` と同じ導線）。agent-project 側は既存の汎用 intake
  （`intake_cmd` / `enqueue --json`）で読める——**agent-audit から state repo へ直接
  書かない**（C7）。出力したタスクには洞察 id と証跡参照が残り、採用されて rules.md へ
  昇格したかは agent-project 側の学習ループが追跡する（C8 の分業: 蒸留までが agent-audit、
  強制・評価は agent-project）。
- **共有してよい層の境界（C1）**: ノード外へ出せるのは records の集計値・観測・洞察・
  タスクだけ。transcript 本文と `excerpt_ref` の実体はローカルに留める。export 系出力
  （report / tasks / --json）は決定的スクラバ（資格情報パターン・絶対パスのホーム相対化）
  を必ず通す。

## 8. CLI

すべて単発・有界。終了コードは 0=成功 / 1=検出あり（stats の閾値超過等、CI 向け）/
2=源泉が読めない（フェイルクローズ）。

| サブコマンド | LLM | 概要 |
|---|---|---|
| `collect [--source S]... [--since D] [--with-transcripts]` | 不使用 | 増分収集・正規化 |
| `usage [--period P] [--by K] [--json]` | 不使用 | トークン・コスト集計（measured / estimated 別掲） |
| `stats [--json]` | 不使用 | 実行品質集計 |
| `calibrate [--write]` | 不使用 | rates 較正の提案（--write で budget config へ反映） |
| `extract [--limit N] [--force]` | map | レコード → 観測。間隔・蓄積ゲート（§6.4）を通ったときだけ LLM を呼ぶ |
| `distill [--limit N] [--review] [--force]` | reduce | 観測クラスタ → 洞察。同上 |
| `report [--kind K] [--out F]` | 不使用 | Markdown レポート |
| `tasks [--json]` | 不使用 | 洞察 → 改善タスク（task.schema.json） |
| `gc [--dry-run]` | 不使用 | 種別別保持日数での掃除（§3.3。`gc_auto` で collect へ相乗り） |
| `doctor` | 不使用 | 源泉の到達性・session_log 宣言の有無・未収集 CLI の一覧 |
| `update [--check]` | 不使用 | 自己更新（§9） |

`run`（collect → extract → distill → report の一括）は**設けない**。段を跨ぐ一括は
agent-loop の定期プロンプトや cron に `collect && extract && distill` を書けば足り、
本体に複合コマンドを持つと「どこまで進んで止まったか」の状態管理が増える。

## 9. パッケージング・設定・自己更新・テスト（家族の慣習に揃える）

- **配置**: `tools/agent-audit/`＝`agent-audit.py`（shim）+ `agent_audit/`（通常 import の
  パッケージ。fragment 分割はしない——新規実装に単一ファイル時代の互換制約は無い）+
  `agent-audit.yaml.example` + `install.sh`（shim）+ `tests/`。
- **インストール**: `tools/agent-tools/install.sh` に 4 本目のビルド対象として追加
  （agentcore 同梱 zipapp → `~/.local/bin/agent-audit`、`--only agent-audit` 対応、
  smoke test `--help`）。`tools/agent-audit/install.sh` は他と同じ 20 行 shim。
  Python 下限は trio と同じ **3.11**。pip 依存なし（PyYAML は YAML 設定時のみ任意）。
- **自己更新**: `agent_flow/update.py` と同型の `agent_audit/update.py`。
  `TOOL_SUBDIR = "tools/agent-audit tools/agent-tools"`（agentcore 同梱のための 2 パス
  sparse-checkout。既存の罠——engine のみ取得すると installer が agentcore を束ねられず
  自己更新が永久に失敗する——をそのまま踏襲して回避）。状態は
  `<agent_home>/agent-audit.update.json`、`update_check_interval` 既定 21600。
- **設定**: `DEFAULT_CONFIG_NAMES = ["agent-audit.yaml", "agent-audit.yml",
  "agent-audit.json"]`、探索順は `--config` → `<cwd>` → `<cwd>/.agents` → `<cwd>/.agent` →
  `<agent_home>`。CLI > 設定 > 既定。主キー:

```yaml
# agent-audit.yaml.example（抜粋）
audit_dir: ""                 # 既定 ~/.agents/audit（--audit-dir > この値 > 既定。環境変数は見ない）
budget_dir: ""                # node-budget の場所（既定 ~/.agents/budget）
sources: []                   # 空 = 自動発見（budget-ledger / cli-native は常時、他は宣言時）
flow_buses: []                # 追加で読む flow バスのパス
project_roots: []             # agent-project の state clone ルート
amigos_buses: []
agent_cli: claude
agents:
  extract:  {agent_cli: ollama, model: qwen3}
  distill:  {}
  review:   {}
agent_timeout: 300
argv_limit: 100000
extract_input_chars: 8000
extract_filters: [failed, retried, verify-flip, needs, long-session]
extract_min_interval_hours: 6   # LLM 実行タイミング（§6.4）。0 = ゲート無効
extract_min_records: 10
extract_max_calls: 40
distill_min_occurrences: 2
distill_min_interval_hours: 24
distill_min_new_observations: 5
distill_max_calls: 10
join_slack_sec: 120
gc_auto: true                   # collect 末尾の定期クリーンアップ（§3.3）
gc_interval_hours: 24
gc_keep_days:                   # 種別ごとに調整可（0 = その種別を消さない）
  records: 90
  transcripts: 30
  observations: 90
  reports: 30
update_repo: ""               # 空 = skill-registry.json から解決
update_branch: main
update_check_interval: 21600
```

- **テスト**: `tools/agent-audit/tests/` + `_shared.py`（tempdir へ chdir・
  `KIRO_SKILL_REGISTRY` 無効化・`AGENT_CONTROL_DIR` 隔離・`AGENT_BUDGET_DIR` 隔離——
  trio の `_shared.py` と同じ事故防止リスト）。stdlib `unittest`、CI の python matrix へ
  `agent-audit` を追加。LLM 段は `stub` CLI（`agents/` の stub 定義 or 固定応答）で
  決定的にテストする。
- **schemas**: `schemas/audit-record.schema.json`・`schemas/audit-insight.schema.json` を
  新設（所有者: agent-audit、正典は schemas/README.md の表へ追記）。
  `schemas/agent-cli.schema.json` へ `session_log` を additive 追加（所有者: 共有。
  ローダは agentcli が無視し、読むのは agent-audit だけ——既存ツールへの影響ゼロ）。

## 10. 不変条件

1. **読み手に徹する。** 他ツールのバス・状態リポジトリ・台帳・CLI ストアへ書かない。
   書くのは audit ディレクトリと、明示 `--write` 時の budget `rates`（契約上の管理面
   キー）だけ。
2. **決定的にできる処理に LLM を使わない。** 収集・正規化・相関・集計・クラスタリング・
   レポート描画は stdlib のみで再現可能。LLM は extract / distill / review の 3 purpose に
   閉じる。
3. **偽の実測を作らない。** measured と estimated を混ぜない。相関が一意でなければ結合
   しない。読めない源泉は「未収集」と明示して exit 2（黙って部分集計を全体と偽らない）。
4. **必ず止まる。** 全サブコマンド単発・有界。LLM 段は段別上限 × 間隔・蓄積ゲート ×
   node-budget × 処理済みカーソルで停止し、`--force` が外せるのはゲートだけで上限と
   予算は外せない。
5. **設定の解決は 引数 > 設定ファイル > 組み込み既定 のみ。** 書き先・源泉の場所を含め、
   agent-audit 固有の環境変数を導入しない・見ない（実行環境の差で挙動が変わらない）。
6. **生の会話・秘密・絶対パスをノード外へ出さない。** 共有可能な層（集計・観測・洞察・
   タスク）と不可の層（transcript）を物理的に分け、export は決定的スクラバを通す。
7. **蒸留の根拠を追跡できる。** 洞察 → 観測 → レコード → 源泉の参照鎖を欠かさない。
   根拠の無い洞察は生成しない（distill はクラスタ単位でだけ走る）。
8. **CLI の作法の変更は `agents/<name>.json` 1 ファイルで完結する**（session_log にも
   agent-cli プラグイン契約の受入条件を適用）。

## 11. 非目標

- リアルタイム監視・可視化 UI（agent-dashboard の領分。audit は JSON を出すまで）。
- done 判定・品質ゲート（agent-project / codd-gate の領分。audit は洞察とタスク候補まで）。
- rules.md / skills への自動昇格（強制・評価は agent-project の学習ループ。C8 の分業）。
- エンジン改造（セッション ID 記録・`@agent-usage` 出力の追加は別提案。本設計は無改造で
  成立する範囲に閉じる）。
- 中央収集サーバ・ノード横断の生ログ集約（C1・C2 違反。ノード間で交換してよいのは
  蒸留済みの洞察・タスク・集計だけで、その転送は既存の state repo / 板の仕組みに委ねる）。

## 12. 段階導入

| 段 | 内容 | 出口条件 |
|---|---|---|
| M1 | collect（budget-ledger / cli-native: claude・kiro）+ usage + doctor + 慣習一式（installer / update / config / tests / schemas） | 単独ノードで実測トークン集計が出る。エンジン無しでも動く |
| M2 | collect の flow-bus / project-root / amigos-bus / loop-log + stats + 相関 + calibrate | 4 ツールの実行品質が 1 コマンドで見える。rates 較正が回る |
| M3 | extract + cluster + distill + report | 弱モデル分担で観測→洞察が出る。消費は台帳で追える |
| M4 | tasks + review + codex ほか session_log 宣言の拡充 | 洞察が agent-project の intake へ流れ、昇格まで追跡できる |

各段は独立にリリース可能で、M1 の時点から他ツール・CLI 単独利用の両方で価値が出る
（usage 集計はエンジン利用の有無に依らない）。
