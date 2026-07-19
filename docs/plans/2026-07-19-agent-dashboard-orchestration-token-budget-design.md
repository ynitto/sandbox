# Agent Dashboard: エージェント CLI オーケストレーションとノード予算 v2（トークン配分）設計

## 背景と目的

エンジン側（agent-project / agent-amigos / agent-flow / kiro-loop）には、実行エージェントを
差し替える仕組みがすでに揃っている。CLI 引数と設定ファイルに加えて、`agents/<name>.json` の
ファイルドロップイン（契約: `schemas/agent-cli.schema.json`）で組み込み外の CLI を追加でき、
agent-project は用途別（`agents:` の plan/verify/…）、agent-amigos はロール別、agent-flow は
役割・ノード種別（planner/evaluator/worker/kind）にエージェントとモデルを選べる。agent-flow の
ワーカーノードは gitlab executor の park & poll（`waits/`）で他ノードへ委譲して完了を待てるし、
GitBus の複数ノード構成ではあるノードが手を止めれば他ノードがタスクを引き取る。kiro-loop は
ノード予算超過をサイクル先頭で検知して送信を抑止する。

一方 agent-dashboard はこれらを個別断片としてしか扱えない。予算は Amigos タブの一角で
実行時間（分）の上限を編集するだけ、モデル設定は dashboard 自身が起動するアシスタント CLI の
設定に留まり、稼働中エンジンのエージェント/モデルを横断して見る・変える面がない。停止・委譲も
エンジンごとに手段がバラバラ（signal / commands ドロップ / cancel マーカー / tmux）で、
「ノード全体でいま何がどのモデルでトークンをいくら使っていて、どこへ配分すべきか」に答えられない。

本設計は次の 2 点を行う。

1. **予算概念の刷新（node-budget v2）** — 一次単位を実行時間（分）から**トークン**へ移し、
   ワークロード間の**配分（最適配分）**を予算の一部として扱う。
2. **オーケストレーション契約（agent-control）の新設** — エージェント変更・モデル変更・
   委譲誘導・停止/一時停止という運用操作を、dashboard が書きエンジンが読む宣言的な
   データ契約に統一し、dashboard に管理面（オーケストレーションタブ）を設ける。

いずれも本リポジトリの原則（**結合はデータ契約のみ・pull 型・追記/原子書換・エンジンは単純、
知能は管理面**）を維持する。エンジン間のコード依存や push 型 IPC は導入しない。

## 現状の整理

### 予算は 3 系統ある

| 系統 | 単位 | 対象 | 置き場所 | 執行点 |
|---|---|---|---|---|
| node-budget（共有契約） | 実行時間（分） | ノード（マシン）×ワークロード | `$AGENT_BUDGET_DIR`（既定 `~/.agent/budget/`）config.json + ledger/*.jsonl | 各エンジンの LLM チョークポイント / kiro-loop サイクル先頭 |
| mission budget（amigos） | 実行時間（分） | 1 ミッション（依頼側） | mission.yaml `budget:` + バス events の `cli_seconds` | runner のターン前後（soft_ratio → wrap-up） |
| cost budget（agent-project） | トークン / USD | 1 run（`@cost tokens=… usd=…` 行の決定的パース） | agent-project.yaml `max_tokens` / `max_cost` | `_budget_reason`（mr.py） |

トークンを数えられる場所（agent-project の `@cost`）とノード横断の予算（node-budget）が
分断されており、ノード予算は「速い安いモデルの 1 分」と「高価なモデルの 1 分」を同じ消費として
扱う。ワークロード別上限は静的な AND でしかなく、余った枠を他ワークロードへ回せない。

### 制御チャネルはエンジンごとに異なる

- agent-project: `commands/*.json` ドロップ（pause/resume/stop・タスク操作）
- agent-flow: バスへの `inbox/`（submit）・`inbox/cancels/`（cancel）、state_git 経由で遠隔可
- agent-amigos: `<home>/.agent/agent-amigos/commands/*.json`（post/claim/accept/…）
- kiro-loop: signal / stdin `quit` / pid のみ。予算超過は pull 検知で**一時停止（自動再開）**であり
  「停止」はできない

エージェント/モデルの差し替えは全エンジンとも**起動時の設定**であり、稼働中に運用者の意思で
横断的に切り替える面がない。

## 予算概念の再検討

### 検討した案（予算の一次単位）

| アプローチ | 決定性 | 計測可能性 | モデル差の反映 | 移行コスト | 推奨度 |
|---|---|---|---|---|---|
| A. 実行時間（分）を継続 | 高 | 全 CLI で可 | 無い（1 分は 1 分） | 0 | ★☆☆ |
| B. USD（コスト）一次 | 中 | 単価表の保守が必要・ローカルモデルは 0 円 | 単価表次第 | 高 | ★☆☆ |
| C. トークン一次（実測のみ） | 高 | トークンを報告しない CLI（kiro-cli / ollama 等）が脱落 | 直接 | 高 | ★★☆ |
| D. トークン一次＋実測秒からの推定（ハイブリッド） | 高（推定規則が決定的） | 全 CLI で可 | 直接（推定はレート表） | 低（additive） | ★★★ |

**案 D を採用する。** 台帳の必須項目は従来どおり `seconds`（常に計測できる事実）とし、
トークンを取得できる実行（agent-project の `@cost`、JSON 出力を持つ CLI 等）は
`tokens_in` / `tokens_out` を**実測値として**追記する。トークンが無い行は、読む側が
`seconds × レート（tokens/秒）`で**読み出し時に推定**する。台帳には事実だけを書き、推定は
消費集計の側で行う——追記専用台帳の性格を守り、レート表の改善が過去の消費集計にも一貫して効く。

- レート表は config.json の `rates`（`agent_cli[:model]` → tokens/秒、無ければ
  `default_tokens_per_second`）。**エンジンは config のレートだけを使う**（決定的・単純）。
  dashboard は台帳のうち seconds と tokens が両方入った行から実効レートを較正し、
  `rates` を書き戻す（知能は管理面に置く）。
- USD は補助情報として台帳に残せる（`usd`）が、予算の一次単位にはしない（ローカルモデルの
  扱いと単価表の保守が執行系に混ざるため）。
- 時間上限（`execution_minutes`）は v1 互換としてそのまま有効（トークン上限と AND）。
  「壁時計時間で締める」運用（電気代・占有時間）は引き続き表現できる。

### 検討した案（配分方式）

| アプローチ | エンジンの複雑さ | ロックの要否 | 余剰の融通 | 推奨度 |
|---|---|---|---|---|
| A. 静的な内訳上限（現行） | 最小 | 不要 | 不可 | ★☆☆ |
| B. dashboard がアロケータとして実効上限を再計算し config に書く | 最小（従来と同じ比較） | 不要 | 可（再計算のたび） | ★★★ |
| C. エンジン間の分散協調（台帳上での入札等） | 大 | 事実上必要 | 可 | ★☆☆ |

**案 B を採用する。** 各ワークロードに `weight`（配分比）と `min_tokens` / `max_tokens`
（下限・上限クランプ）を宣言し、dashboard（または任意の管理 CLI）が定期的に

```
R      = max(0, トークン上限 − Σ 全消費)            # 期間内の残り
active = lifecycle=run のワークロード
cap_w  = clamp(consumed_w + R × weight_w / Σ weight_active,
               min_tokens_w, max_tokens_w)
```

を計算して `computed.workloads.<w>.tokens` に書く。エンジン側の判定は従来と同じ
「自分の消費 ≥ 自分の上限、または全体消費 ≥ 全体上限なら控える」だけで、配分の知能は
すべて管理面にある。使わなかったワークロードの残り枠は次回の再計算で自動的に他へ回る
（work-conserving）。dashboard が閉じていても直近の `computed` と静的上限が効き続けるので、
アロケータは可用性の前提にならない。

### 超過時のふるまい（on_exhausted）と段階縮退

mission budget の soft_ratio → wrap-up の考え方をノード予算にも一般化する。

- `allocation.soft_ratio`（既定 0.9）: 自ワークロードの消費が実効上限の soft_ratio に達したら
  **縮退（degrade）** — agent-control の `degraded` 指定（例: 安価なモデルへの切替）を適用する。
- 実効上限に達したら、ワークロードごとの `on_exhausted` に従う:
  - `pause`（既定）: 新規実行を控える。上限引き上げ・期間ロールで自動再開（現行の挙動）。
  - `stop`: **プロセスを graceful に終了する**。再開は明示的な再起動のみ。
    定常業務（kiro-loop）のように「予算が尽きたら黙って待つのではなく止まってほしい」
    ワークロードのための指定で、**本設計で routine の既定を stop にする**。
  - `degrade`: 縮退指定があるあいだは縮退のまま実行を継続する（縮退指定が無ければ pause と同じ）。

mission budget と agent-project の cost budget は対象が違う（依頼 1 件 / run 1 件）ため
統合せず存置する。ただし単位語彙（tokens_in/out）とレート推定の考え方は本契約に揃え、
将来 mission budget をトークン宣言へ拡張する余地を残す（additive）。

## データ契約

### node-budget v2（`schemas/node-budget.schema.json` を additive に改訂）

v1 のキーは意味を変えずすべて残す（互換性の規則どおり）。`version: 2` で追加キーの解釈を宣言する。

```jsonc
// $AGENT_BUDGET_DIR/config.json
{
  "version": 2,
  "execution_minutes": 0,          // v1 互換: 時間上限（分）。0=無制限。トークン上限と AND
  "period": "day",                 // day | month | total（UTC）
  "workloads": {},                 // v1 互換: 分の内訳上限
  "tokens": 2000000,               // v2: 期間内トークン合計上限。0=無制限
  "allocation": {                  // v2: 配分宣言（人 / dashboard が書く）
    "mode": "auto",                // static: computed を書かず min/max だけ | auto: 管理面が再計算
    "soft_ratio": 0.9,
    "rebalance_interval_sec": 300,
    "workloads": {
      "routine": {"weight": 1, "min_tokens": 100000, "on_exhausted": "stop"},
      "project": {"weight": 3, "on_exhausted": "pause"},
      "flow":    {"weight": 3, "on_exhausted": "degrade"},
      "amigos":  {"weight": 1, "max_tokens": 500000, "on_exhausted": "pause"}
    }
  },
  "computed": {                    // v2: アロケータの出力（管理面だけが書く）
    "workloads": {"routine": {"tokens": 210000}, "project": {"tokens": 890000}},
    "computed_at": "2026-07-19T03:00:00Z",
    "computed_by": "dashboard"
  },
  "rates": {                       // v2: 推定レート表（tokens/秒）。エンジンはこれだけを使う
    "default_tokens_per_second": 120,
    "per_cli": {"kiro": 100, "claude:opus": 180, "ollama:qwen3": 40}
  },
  "updated_at": "…", "updated_by": "dashboard"
}
```

```jsonc
// ledger/<YYYYMMDD>.jsonl の 1 行（required は v1 と同じ ts / workload / seconds）
{"ts": "…", "workload": "project", "tool": "agent-project", "seconds": 42.1,
 "ref": "project:app/task-12", "node": "…",
 "agent_cli": "claude", "model": "opus",        // v2: 実行したエージェントの帰属
 "tokens_in": 12000, "tokens_out": 3400,        // v2: 実測できたときだけ書く（推定値は書かない）
 "usd": 0.31}                                   // v2: 補助情報（任意）
```

消費集計（読み出し側の共通規則）:

```
tokens(row) = tokens_in + tokens_out            （実測がある行）
            | seconds × rate(agent_cli, model)  （無い行。rate は per_cli["cli:model"]
                                                 → per_cli["cli"] → default の順）
```

超過判定はロックなしの読み合計で行い、上振れが「進行中実行 × 同時実行数」に有界という
v1 の性質は変わらない。

### agent-control（`schemas/agent-control.schema.json` を新設）

置き場所は `$AGENT_CONTROL_DIR`（既定 `~/.agent/control/`）。管理面（dashboard / 各ツール CLI /
人）が `control.json` に**望ましい状態**を書き、各エンジンは既存のチョークポイント / サイクル
先頭で mtime を見て再読込・適用する（pull 型。push 型 IPC は作らない）。適用状況は各エンジンが
`status/<tool>-<pid>.json` にハートビートとして書き、管理面が読む。

```jsonc
// $AGENT_CONTROL_DIR/control.json
{
  "version": 1,
  "revision": 17,                        // 単調増加。status 側の revision_applied と突き合わせる
  "defaults": {"agent_cli": null, "model": null},   // 全ワークロード共通の上書き（null = 指定なし）
  "workloads": {
    "flow": {
      "agent_cli": null, "model": null,
      "agents": {                        // 各エンジンの既存語彙をそのまま使う
        "planner": {"model": "opus"},    //   flow: planner / evaluator / worker / <kind>
        "worker":  {"agent_cli": "cursor", "model": null}
      },
      "degraded": {"model": "haiku"},    // soft_ratio 到達中に適用する縮退指定
      "lifecycle": "run",                // run | pause | stop
      "delegation": {"prefer": "remote", "max_open_issues": 8}   // flow だけが解釈
    },
    "project": {"agents": {"plan": {"model": "opus"}, "verify": {"agent_cli": "kiro"}}},
    "amigos":  {"agents": {"reviewer": {"model": "opus"}}},      // キーはロール id
    "routine": {"lifecycle": "run"}
  },
  "updated_at": "…", "updated_by": "dashboard"
}
```

```jsonc
// status/<tool>-<pid>.json（各エンジンが interval ごとに原子書換）
{"tool": "kiro-loop", "workload": "routine", "node": "…", "pid": 4242,
 "revision_applied": 17,
 "effective": {"agent_cli": "kiro", "model": null},
 "lifecycle": "run",                       // 実際の状態（stopping 中は "stop"）
 "budget": {"exceeded": false, "soft": false},
 "fresh_after_sec": 120, "ts": "…"}
```

設計上の規則:

- **優先順位は control > CLI 引数 > 設定ファイル > 組み込み既定。** control は「いまの運用者の
  意思」であり、エントリを消せば（null にすれば）即座に元へ戻る明示的・可逆な上書きとする。
  値の解決は各エンジンの既存の解決関数（`_agent_for` 等）の先頭に 1 段足すだけ。
- **`agents` のキーは各エンジンの既存語彙**（project: `AGENT_PURPOSES`、flow:
  planner/evaluator/worker/kind、amigos: ロール id）。未知のワークロード・未知のキーは無害に
  無視する（repos と同じ規則）。
- **lifecycle の意味**: `pause` = 新規の LLM 実行・クレーム・ディスパッチを控える（進行中は
  完走。予算超過時の pause と同じ機構に乗せる）。`stop` = graceful 終了。**desired state であり、
  stop のまま手で再起動されたエンジンは起動時チェックで即終了する**（再開するには管理面が
  run に戻してから再起動する）。
- **ファイルドロップインとの関係**: control が指す `agent_cli` の実体解決は従来どおり
  組み込み → `agents/<name>.json` 探索（`$KIRO_AGENTS_DIR` → `<root>/agents/` →
  `~/.agent/agents/` → `~/.kiro/agents/`）。control は「どれを使うか」だけを言い、
  「何が使えるか」は引き続きドロップインが定義する。

### 委譲（delegation）の位置づけ

委譲はエンジンの新機能ではなく**既存機構の運用操作**として扱う。

- agent-flow は gitlab executor の park & poll（`DeferDecision` → `waits/` へ退避・クレーム解放 →
  `service_waits` が監視）で「他へ出して完了を待つ」を既に持つ。GitBus 構成なら、あるノードの
  flow を `pause` するだけで未クレームのタスクは他ノードのワーカーが引き取る——これが
  ノード間委譲の基本形。
- control の `delegation.prefer: remote` は「このノードでは抱え込まず外へ出せ」の誘導で、
  flow のワーカーが対応 executor（gitlab）を持つ場合に委譲経路を優先し、`max_open_issues`
  で外へ出す量を絞る。予算の `degrade` と組み合わせると「枠が細ったら安いモデルに落とし、
  それでも足りなければ外へ出す」という段階縮退がデータ契約だけで表現できる。

## エンジン側の変更（実装済み）

いずれも「チョークポイントに読みを 1 段足す＋台帳の追記列を増やす」に留める。4 エンジンとも
実装済み（各ツールに v2 予算＋control の単体テストを追加、全スイート green）。

| エンジン | 変更点 |
|---|---|
| agent-project | `_run_agent_cli`（prioritize.py）: ① control 読込（mtime キャッシュ）を `_agent_for` の前段に追加 ② node-budget 判定をトークン集計対応へ ③ `@cost` パース結果（tokens/usd）と agent_cli/model を台帳行に追記 ④ lifecycle=pause/stop を quota 分類の環境要因として即終端（既存フローに乗る） |
| agent-amigos | `runner.py` のロール解決の前段に control 上書き（ロール別）。新 `control.py` を追加。`nodebudget.py` を v2 化（`_totals` でトークン集計・`save_config` は v2 キーを保持）。lifecycle=pause/stop と超過（非 degrade）で amigo を paused にし owner へ通知（現行の paused 経路を流用） |
| agent-flow | `run_agent`（agent.py）: 同上。`_agent_for` の先頭に control 上書き＋soft/degrade の縮退適用。lifecycle=pause/stop・超過（非 degrade）は quota 分類で run 終端。台帳へ agent_cli/model 帰属。status ハートビート書出し |
| kiro-loop | `_run_loop` サイクル先頭: 予算判定をトークン対応にし、**超過時は `on_exhausted` を見て `stop` なら `_request_shutdown()`（自 SIGTERM → 既存 `_signal_handler`/`_cleanup`）で graceful 終了**（`_STATE_DIR/stopped-<pid>.json` に `stopped_reason` を残す）。control の lifecycle=stop も同経路、pause は送信抑止。トークンは計測できないため台帳は従来どおり seconds のみ（スロット保持時間近似）＋ `agent_cli: kiro` を付す |

kiro-loop の停止は「一時停止（現行）」から「既定で停止」へ変わるが、`on_exhausted` を
`pause` に戻せば現行挙動を選べる。停止後の再開導線は dashboard の cowork（`runLoop`）を使う。

> 実装メモ: エンジン間はコード共有せず、v2 予算リーダ（`_node_budget_rate` / `_row_tokens` /
> `_node_budget_state`）と control リーダ（`_load_control` / `_control_override` /
> `_control_lifecycle` / `_write_status`）を各ツールが自前で持つ（データ契約のみ結合の流儀）。
> agent-flow の worker（work.py）ノードでのクレーム前 lifecycle 確認と daemon の
> `write_daemon_status` への revision 統合は後続（現状は run_agent チョークポイントで抑止済み）。

## agent-dashboard 側の変更

### 新しい制御面 `orchestration`

feature-split 設計に従い `src/features/orchestration/` を追加する（フルプラグインではなく
ソース分離）。amigos 機能に間借りしていた node-budget の実装（`amigos/main/budget.js`）は
ここへ移管して v2 対応し、Amigos タブの予算パネルは新タブへの参照に置き換える
（IPC `amigos:budgetSave` は互換のため 1 リリース残す）。

```
src/features/orchestration/
  config.js        # budgetDir / controlDir / refreshSec
  main/budget.js   # v2 集計（トークン推定・配分計算・rates 較正）と config 書換
  main/control.js  # control.json の読み書き（revision 管理・原子書換）と status/ 読取
  main/agents.js   # agents/<name>.json ドロップインの棚卸し・検証・編集
  preload.js
```

IPC（すべて `{ok,data,error}` 包み）:

- `orchestration:overview` — 予算 usage v2（実測＋推定の内訳つき）・control 現在値・
  status/ 一覧（fresh 判定つき）・エージェントドロップイン棚卸しをまとめて返す
- `orchestration:budgetSave` — 上限・期間・allocation（weight/min/max/on_exhausted/soft_ratio）
- `orchestration:rebalance` — アロケータの手動実行（auto では refreshSec ごとに自動）
- `orchestration:controlSave` — overrides / degraded / delegation の保存（revision +1）
- `orchestration:lifecycle` — `{workload, action: run|pause|stop}` の近道
- `orchestration:agentSave` / `orchestration:agentDelete` — ドロップイン定義の作成・編集・削除
  （書込先は既定 `~/.agent/agents/`。契約フィールドの静的検証つき）

### オーケストレーションタブの画面構成

1. **予算ゲージ** — 期間内のトークン消費（実測/推定の内訳を明示）と時間消費。ワークロード別の
   消費 / 実効上限（computed）バー、soft/exceeded バッジ
2. **配分エディタ** — weight・min/max・on_exhausted・soft_ratio。auto/static 切替と
   「いま再配分」。変更は config.json へ原子書換
3. **エージェント割当マトリクス** — 行 = ワークロード（＋展開で purpose/role/kind）、
   列 = agent_cli / model / 縮退時。値は control.json へ。空欄 = 上書きなし（各エンジンの
   設定ファイルに従う）ことを明示する
4. **エンジン状態** — status/ の一覧（tool / pid / lifecycle / revision applied↔desired の一致・
   乖離、budget soft/exceeded）。行ごとに 一時停止 / 再開 / 停止。停止済み行には再起動導線
   （project: start、routine: cowork runLoop）
5. **エージェント CLI 棚卸し** — 組み込み一覧＋探索 4 ディレクトリのドロップイン
   （first-wins の陰り表示つき）。スキーマ検証エラーと errors[] トリアージ規則を表示し、
   その場で新規作成・編集できる

表示は色だけに頼らず状態語を併記する（既存 UI 方針を踏襲）。

### レート較正（管理面の知能）

dashboard は台帳のうち `seconds` と `tokens_in/out` が両方ある行から
`(agent_cli, model)` ごとの実効 tokens/秒（外れ値に強い中央値）を求め、`rates.per_cli` へ
書き戻す。エンジンは較正の存在を知らず、config のレート表を読むだけ——推定の質は管理面の
改善だけで上がる。

## 精度と限界

- **推定はあくまで推定**: トークン未報告 CLI の消費はレート × 秒の近似。UI では実測分と
  推定分を分けて表示し、混同させない。
- **上振れ有界**: ロックなし読み合計のため、同時実行分の上振れは v1 と同じく有界。
- **アロケータの可用性**: auto の再配分は管理面が開いているときだけ進む。閉じていても
  直近 computed ＋静的上限＋全体上限が効くため、安全側に倒れる。
- **control の反映遅延**: pull 型のため反映はエンジンのサイクル / 次のチョークポイント到達時。
  status の revision 突き合わせで「未反映」を可視化して補う。

## 互換性と移行

1. **additive evolution を厳守**: v1 のキーは意味を変えない。v1 しか知らないエンジンは
   分上限だけを執行し続ける（安全側）。台帳の追加列は未知キーとして従来リーダに無害。
2. スキーマは本ディレクトリ（`schemas/`）を先に更新し、各ツールの正典から参照を張る
   （互換性の規則 2）。検証は各ツールの stdlib パーサ＋スキーマ突き合わせテスト
   （amigos-command と同じ両側テスト方式）。
3. `agent-loop`（kiro-loop の未統合クローン）へは次回クローン同期で反映する。

## 段階導入

- **P0（契約と停止）**: node-budget v2 スキーマ・agent-control スキーマの正典化。
  kiro-loop の on_exhausted=stop と lifecycle 適用。dashboard の予算パネル v2
  （トークン上限・ゲージ・配分エディタ、amigos からの移管）
- **P1（横断オーバーライド）**: 4 エンジンのチョークポイントへ control 読込を追加。
  割当マトリクスとエンジン状態（status/）表示。台帳への tokens/agent_cli/model 追記
  （まず agent-project の `@cost` 経路から）
- **P2（最適配分と縮退）**: auto アロケータ・レート較正・degraded 適用・delegation 誘導

## テスト

- スキーマ突き合わせ: config/ledger/control/status のサンプルを両側（Python 各エンジン /
  dashboard の jest）で読ませ、enum・既定値・必須キーの一致を担保
- 配分計算: R の枯渇・weight 偏り・min/max クランプ・lifecycle 除外の表駆動テスト
- kiro-loop 停止: 超過 → stop 経路で `_cleanup()` が走り state に stopped_reason が残ること、
  `pause` 設定で現行挙動が維持されること
- 互換: v1 config を v2 リーダが、v2 config を v1 リーダ相当が読めること（分上限のみ執行）
