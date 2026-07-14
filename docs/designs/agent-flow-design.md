# agent-flow — git 共有型・分散 Dynamic Workflow 設計書

> 作成日: 2026-06-13 ／ 改称クローン日: 2026-07-14
> 対象ブランチ: `claude/kiro-cli-dynamic-workflow-90ezwe`
> 関連ファイル: `tools/agent-flow/agent-flow.py`, `tools/agent-flow/install.sh`,
> `tools/agent-flow/tests/test_agent_flow.py`, `tools/agent-flow/README.md`
>
> **由来**: 旧 `kiro-flow` 系統から移行し、名称を `agent-flow` へ改称した設計。
> 旧実装と旧設計書は移行完了後に削除済み。

---

## 1. 概要

agent-flow は、kiro-cli を頭脳にして **Claude 風の Dynamic Workflow**
（実行時にタスク構造を動的生成 → ワーカーへ委譲 → 結果を評価して再計画 → 統合）を実現する基盤。

特徴:

- **通信はファイルのみ**。メッセージバスをローカルディレクトリにも **共有 git リポジトリ**にもでき、
  後者にすると**複数 PC へそのまま分散**できる。
- orchestrator は [Claude Dynamic Workflows の 6 パターン](https://zenn.dev/aria3/articles/claude-code-dynamic-workflows-6-patterns)
  をカタログとして持ち、**要求からパターンの組み合わせと並列数を選んで**タスクグラフを形作る。
- **常駐デーモン**が要求に応じて orchestrator / worker を**オンデマンド起動**する。
- LLM 実行は kiro-cli が既定（設定 `agent_cli: claude` で Claude Code ヘッドレス `claude -p` へ切替可。planner / executor / verify 等すべての LLM 呼び出しに効く）。kiro-cli 無しでも動く **stub** モードでプロトコルを検証できる。

```
                       ┌──────── 共有バス（ローカル dir または git repo）────────┐
   submit "<要求>" ───▶ │  inbox/ … 要求キュー                                    │
                       │  runs/<run-id>/ … strategy・タスクグラフ・claim・結果      │
                       └──▲──────────────▲───────────────────▲──────────────────┘
              pull/push  │      pull/push│           pull/push│
        ┌───────────────┴──┐   ┌─────────┴────────┐  ┌────────┴─────────┐
        │ daemon (PC-A)     │   │ daemon (PC-B)    │  │ daemon (PC-C)    │
        │  ├ orchestrator   │   │  └ worker ×N     │  │  └ worker ×N     │
        │  └ worker ×N      │   │   (オンデマンド)  │  │   (オンデマンド)  │
        └──────────────────┘   └──────────────────┘  └──────────────────┘
```

---

## 2. 背景・目的

Anthropic の *Building Effective Agents* では、固定経路の **Workflow** と、LLM が実行時に経路を決める
**Agent / Orchestrator-Workers** を区別する。agent-flow は後者を志向し、さらに以下を満たす:

| 要件 | 実現方法 |
|------|---------|
| 実行時の動的タスク分解と再計画 | orchestrator が kiro-cli でグラフ生成・評価・追加 |
| 複数 PC への分散 | git リポジトリをバスにし、各ノードが自分のクローンで push/pull |
| 競合しない協調 | ファイル存在で状態を導出 + 名前空間付き claim + 決定的タイブレーク |
| 要求に応じたパターン選択 | 7 パターン（記事の 6 ＋ agent-flow 追加の map-reduce）のカタログから組み合わせ・並列数を選択 |
| オンデマンド起動 | 常駐デーモンが要求量・タスク量に応じてプロセスを起動 |

### 既存ツールとの差別化

| ツール | 構造 | 決定タイミング |
|--------|------|--------------|
| `kiro-loop` | 定期プロンプト送信 | 静的 |
| `multi-agent-shogun-kiro` | 将軍/家老/足軽の固定階層 | 静的 |
| **`agent-flow`** | **タスクグラフ** | **実行時に LLM が生成・更新** |

設計思想は `git-file-sync`（git をハブにした同期）と `gitlab-idd`（キューからの claim→実行→報告）の
組み合わせに、タスクグラフの動的生成を加えたもの。

---

## 3. 全体アーキテクチャ

役割は固定でなく**起動モード**で決まる（同一スクリプト）。

| 役割 | 起動 | 仕事 |
|------|------|------|
| **daemon** | `agent-flow daemon` | inbox 監視→orchestrator 起動 / タスク量に応じ worker 起動 |
| **orchestrator** | `run` / daemon が起動 | 戦略決定→グラフ生成→静止待ち→評価/再計画→統合 |
| **worker** | `run` / daemon が起動 | claim→kiro-cli 実行→result 書き込み |
| **submit / status / gc** | CLI | 要求投入 / 状態表示 / 古い run 掃除 |

データの真実は常に **バス上のファイル**（`graph.json` と結果ファイル群）にあり、プロセスはステートレス。

---

## 4. メッセージバス設計

### 4.1 ファイルレイアウト

```
<bus>/inbox/<req-id>.json            # 投入された要求（submit が書く）
<bus>/inbox/claims/<req-id>/<who>.json  # 要求の取得マーカー（どのデーモンが担当か）
<bus>/runs/<run-id>/
  meta.json            # request・status（planning/running/done）・タイムスタンプ
  graph.json           # strategy + nodes{ id: {goal, deps, kind} } + iteration
  tasks/<id>.json      # タスク仕様（goal, deps, kind）
  claims/<id>/<who>.json  # 取得マーカー（ノードごとに名前空間化）
  results/<id>.json    # 成果（claim 成功者のみ書く。生成した artifacts のパスも記録）
  artifacts/<id>/      # 中間成果物（ファイル）。node-id で決定的＝後続が同じパスで発見
  events/<who>.jsonl   # 追記専用ログ（各ノードが自分のファイルだけ）
  final.json           # strategy + 全結果サマリ
```

### 4.2 衝突しない書き込み規律

ノードが同じファイルを書き換えないよう、**書き込み所有権をパス単位で分割**する。これにより git でも
ほぼ disjoint なマージになり、コンフリクトしない。

| ファイル | 書く人 |
|---|---|
| `meta.json` / `graph.json` / `tasks/*` | orchestrator のみ |
| `claims/<id>/<who>.json` | 取得を試みる各ワーカー（**ファイル名が衝突しない**） |
| `results/<id>.json` | claim に成功したワーカーのみ |
| `events/<who>.jsonl` | 各ノードが自分のファイルにだけ追記 |

### 4.3 状態はファイル存在から導出

タスクの状態は専用フィールドを持たず、**ファイルの存在**から導出する（書き換え競合を作らない）。

| 状態 | 条件 |
|------|------|
| pending | `tasks/<id>.json` があり、有効な claim も `results/<id>.json` も無い |
| claimed | `claims/<id>/` に lease 内の claim があり、勝者が確定 |
| done / failed | `results/<id>.json` があり `status` がそれ |

---

## 5. claim プロトコル（分散ロックの肝）

git は結果整合のため、「2 ノードが同じタスクを取る」「push が衝突する」を**設計で**防ぐ。

### 5.1 名前空間付き claim ＋ 決定的タイブレーク

各ワーカーは自分専用ファイル `claims/<id>/<who>.json` を書く（ファイル名が衝突しないので git で
add/add コンフリクトにならない）。勝者は lease 内の全 claim のうち **`(ts, who)` が最小**の 1 件に
**決定的に**定まる。ローカルでも git でも、すべてのノードが同じ集合から同じ勝者を導く。

```
try_claim(node, who):
  1. sync_pull()                         # 最新の claim 集合を取得
  2. results/<node> があれば False
  3. winner が居て自分でなければ False    # 既に他者が確定
  4. claims/<node>/<who>.json を書く（ts, lease_until）
  5. sync_push()                         # 自分の claim を共有
  6. sync_pull()                         # 他者の claim を取り込む
  7. winner == who を返す                # 決定的タイブレークで唯一の勝者
```

- **二重実行ゼロ**: 複数ワーカーが同時に書いても、勝者は 1 人に決まる（テストで検証）。
- git では push 競合は `pull --rebase` リトライで吸収（claim は名前空間化済みなので衝突しない）。

### 5.2 lease とハートビート

- claim には **lease（期限）** を持たせる。`_winner` は期限切れ claim を無視するため、ワーカーが
  クラッシュして放置された claim は**自動的に再 claim 可能**になる（孤児回収）。
- 実行が lease を超える長時間タスク向けに **Heartbeat スレッド**が `max(2 秒, lease/3)` 間隔で
  claim の `lease_until` を延長し続け、実行中の横取りを防ぐ。
- **lease/heartbeat は「プロセスの生存（liveness）」を伝える信号であり、「タスクの進捗（progress）」
  ではない**。ワーカー死亡（crash/OOM/kill）は心拍停止 → lease 失効 → 再 claim で回収できるが、
  **プロセスは生きたままタスクがハングした場合**（kiro-cli が無進捗で固まる等）は心拍が独立スレッドで
  鳴り続けて lease を延長し、永久に回収されない。この死角は **task timeout**（`run_kiro` の
  `subprocess` タイムアウト、既定 600s・`AGENT_FLOW_KIRO_TIMEOUT`）で塞ぐ。詳細は ADR §17。

---

## 6. 転送層（Bus 抽象）

`Bus` 基底クラスが `sync_pull()` / `sync_push(msg)` フックを持ち、実装で差し替える。

| 実装 | sync_pull / sync_push | 用途 |
|------|----------------------|------|
| `Bus`（Local） | no-op（同一ディレクトリ共有） | 単一マシン |
| `GitBus` | `git pull --rebase` / `add+commit+push`（競合は rebase リトライ） | 複数 PC 分散 |

- `GitBus` は各ノードが `<bus>/<node-id>` に**自分専用クローン**を作り、push/pull で同期。
- **バスサブディレクトリ**: `--git-subdir`（config `git_subdir`）でリポジトリ内のサブディレクトリを
  バスのルートにできる（既存リポジトリの一角を間借り）。git の作業ツリーは `clone_dir`、バスの
  ファイル群はその中の `clone_dir/<subdir>/{runs,inbox}` に置かれる。
- **sparse checkout**: clone は `--no-checkout --filter=blob:none`（非対応サーバはフォールバック）で
  取得し、cone モードの sparse-checkout でバスのサブツリー（`<subdir>` か、直下時は `runs`/`inbox`）
  だけを作業ツリーに展開する。無関係なファイルを取得・展開しないので、大きな共有リポジトリでも軽い。
  `remove_run`（gc）はサブディレクトリを考慮したリポジトリ相対パスで `git rm` する。
- 全書き込みヘルパは親ディレクトリを自動生成する（git は空ディレクトリを追跡しないため、
  クローンしたてのノードでも `results/` 等へ書ける）。
- `run_view(run_id)` で同一クローン内の別 run を**再クローンせず**読み取れる（デーモンの判断用）。

### 6.1 状態の git 保存・共有（state_git）— GitBus とは別物の「状態の鏡」

GitBus が「バスそのものを git にして実行を分散する」のに対し、`state_git`（config
`state_git[-branch/-subdir/-interval]`）は「**実行はローカルのまま、状態の鏡だけを共有する**」。
ローカルバスのワーク内容（`runs/`・`inbox/`）を共有 git リポジトリの `state_git_subdir`
（既定 `agent-flow`）へ双方向同期し、リモートの agent-dashboard（フロータブ）が run の
進捗/結果を読める。agent-project の同名機能と同じ設計:

- **負荷律速**: subdir だけの sparse・blob:none 管理クローン（`<bus>/.state-git`）を再利用し、
  fetch/push（バス走査も）は `state_git_interval`（既定 300s）で律速。push は共有すべき
  コミットがあるときだけ（run の終端時は間隔を待たず押し出す）。
- **多重コミッタ前提**: ステージは自 subdir のみ・push 競合は pull --rebase → 再 push の指数
  バックオフ・force push しない。同一リポジトリを agent-project の state_git や viewer 側の
  git-file-sync と共有できる。
- **3-way 裁定**: manifest（前回同期スナップショット）基準で発生源を判定し、同時変更のみ
  「`inbox/`（人の投入）はリモート優先・`runs/`（機械状態）はローカル優先」で決定的に裁定。
  `*.tmp`（書きかけ）と `.` 始まりは同期しない。gc/cleanup の削除も伝播する。
- **実行は依存しない**: 同期は daemon の poll ループ・run 終端・`run` の待機ループで走り、
  失敗はログに残して続行（run の実行・終端は state_git に一切依存しない）。`--git` 指定時は
  バス自体が共有 git なので無視される。
- **daemon の生存信号（status.json）**: daemon の稼働検知は本来 `$TMPDIR/agent-flow-locks/
  daemon-<sha1>.lock`（pid のみ）だが同一ホスト限定——state_git（鏡）越しの viewer からは
  daemon 自身の一時領域に届かない。`write_daemon_status` が `<bus>/status.json`
  （`host`/`pid`/`node_id`/`orchestrators`/`workers`/`updated_iso`/`fresh_after_sec`）を書き、
  これも state_git で同期することで、viewer 側にロック不在時のフォールバック判定材料を渡す。
  `bus.root` 直下に置くだけで `_scan()`（バス全体を走査）がそのまま同期対象に含めるため、
  GitBus 側のような sparse-checkout の追加設定は不要。GitBus（`--git`）モードでは書かない
  （sparse-checkout が対象外パスになり `git add -A` を壊しかねないため。state_git と `--git`
  は元々ここでも相互排他）。**アイドル中の追加コミットは既定でゼロ**: 起動時に一度だけ書き、
  以降は実イベント（run 終端・生存リース push）時に既存の sync/push へ相乗りする。
  `--status-interval`（daemon サブコマンド。既定 0＝無効）で、アイドル中もこの間隔で
  status.json だけを更新できる（鮮度と git 負荷のトレードオフ）。`fresh_after_sec` は
  daemon が自分の同期間隔（`state_git_interval`/`status_interval` の大きい方の 2 倍・
  下限 120 秒）から計算して埋め込むため、viewer 側は単純な経過時間比較で済む。

---

## 7. ワークフローパターン（7 パターン）

orchestrator は要求を見て、7 パターンから組み合わせと並列数を選び、各ノードに **kind** を付けた
タスクグラフを生成する。**最初の 6 つ**は [Claude Dynamic Workflows の 6 パターン](https://zenn.dev/aria3/articles/claude-code-dynamic-workflows-6-patterns)
をそのままカタログ化したもの、**`map-reduce`** は agent-flow が P2 で追加した 7 つ目の
**正規の選択可能パターン**（後述 7.3）。

| パターン | 形（ノード kind） | 使いどころ |
|---------|------------------|-----------|
| **classify-and-act** | `classify` → 結果で `work` を追加（ルーティング） | 種別判定して専門処理へ振り分け |
| **fan-out-and-synthesize** | 並列 `work`/`generate` ×N → `synthesize` | 分割して並列処理し統合 |
| **adversarial-verification** | `generate` → `verify`（fail なら作り直し） | 成果を批判的に検証 |
| **generate-and-filter** | `generate` ×N → `filter` | 候補を多数出して絞り込み |
| **tournament** | `generate` ×N → `judge` | 複数案から最良を選ぶ |
| **loop-until-done** | `work` → `verify`（条件達成まで反復） | テスト通過・品質達成まで繰り返す |
| **map-reduce**（agent-flow 追加） | `split` → 実行時に `map` ×N を動的展開 → `reduce` | 件数を事前に固定せずデータ駆動で並列処理し集約 |

ノード kind: `work`(通常) / `generate`(候補生成) / `classify`(分類) / `synthesize`(統合) /
`verify`(検証) / `filter`(絞り込み) / `judge`(最良選択) / `reduce`(構造化データの集約) /
`split`(リスト化＝データ駆動 fan-out の起点) / `map`(要素ごとの処理)。
worker は kind 別のプロンプトで実行する。

> **「7 つ目のパターン」か「内部的な仕組み」か**: `map-reduce` は `PATTERNS` カタログに載る
> **正規の選択可能パターン**（planner=agent はカタログから選び、planner=stub はキーワードで検出）であり、
> その点で他の 6 パターンと同格。一方、`split` 完了後に `map`/`reduce` ノードを**実行時に動的生成**する
> `_expand_splits` の挙動はパターンの**実行（継続）メカニズム**であって、別個のパターンではない
> （classify-and-act の継続ルーティングや adversarial-verification の作り直しと同じ層）。

### 7.2 構造化成果（structured results, P1）

Claude Dynamic Workflows の「エージェント間を構造化データが流れる」特徴の取り込み。

- 各 `results/<id>.json` はテキスト `output` に加え、任意の **`data`（JSON）** を持てる（無ければ後方互換）。
- worker は依存の**完全な result dict（output＋data）**を実行へ渡す。agent executor は出力を寛容パースして
  `data` に格納（失敗時はテキストのみ）。stub は kind ごとに決定的な `data` を返す
  （split→`[...]`(要素リスト)、classify→`{label}`、synthesize→`{merged}`、filter→`{kept}`、
  judge→`{winner}`、verify→`{ok}`、reduce→`{items, count}`。work/generate/map はテキストのみ）。
- **`reduce`** kind: 依存の `data`（リストは連結、その他は要素化）を畳み込み `{items, count}` に集約する。

### 7.3 map-reduce パターンとデータ駆動の動的 fan-out（P2）

7 つ目の `map-reduce` パターンの中身。「データに応じて実行時にサブエージェント数が決まる」特徴の取り込み。
パターンとしては `split` を起点に選択され、`map`/`reduce` への展開は下記の実行時メカニズムで行う。

- **`split`** ノードが実行時にリスト（`data`）を返す。継続段階の `_expand_splits` がそれを検知し、
  **要素数ぶんの `map` タスク**＋それらを集約する **`reduce`** タスクを動的生成する（件数は事前固定しない）。
- 展開数は `--max-fanout`（config `max_fanout`、既定 50）でクランプ。`max_iterations` と二重ガード。
- **先走り実行の回避**: 初期グラフは `split` のみとし、`reduce` は展開時に生成する。`reduce` が `split`
  完了直後に claim 可能になって早すぎる集約をしてしまう競合を避ける。
- stub 戦略はキーワード（「それぞれ/各/ごとに/一覧」等）で `map-reduce` を選び、初期グラフ `[split]` を作る。
  agent planner も kind に `split`/`reduce` を選べる。`_expand_splits` は stub/agent 両方の継続で機械的に走る。
### 7.4 複合パターン・統合前 gate・グラフ健全性検査（P3）

- **統合前の事前チェック / 敵対的レビュー（`--review`、config `review`）**: 統合（synthesize/reduce）の
  直前に `verify` gate を挟む。fan-out では `gens → gate(verify) → synth`、map-reduce では
  `map → gate → reduce`。gate が fail なら既存の verify-loop が依存を作り直して再検証（`replaces` で
  後続を付け替え）。adversarial-verification を他パターンに複合した形。
- **複合パターン**: strategy の `patterns` は複数を持てる。agent planner は多段グラフへ複合できる
  （例: classify-and-act の各分岐を fan-out-and-synthesize に / generate-and-filter の通過案で tournament）。
  stub は `--review` による gate 複合を提供。review 時、統合ノード（synthesize/reduce）は「成果＋gate」に
  依存し、集約時は gate の判定（`{"ok":...}`）を除いて実際の成果だけを畳み込む。
- **グラフ健全性検査（`_sanitize_graph`）**: 計画時・再計画時に、未知の依存 ID と自己ループを除去し、
  Kahn 法で到達不能（循環）ノードの残依存を断ち切る。planner（agent）の誤出力や継続での混入に対する防御。

### 7.5 planner 出力の正規化（P4）

- **`_coerce_tasks`**: agent の planner / 評価役の生出力を正規化する共通処理。id 重複除去・既存 id 回避・
  **不正 kind の `work` 丸め**（有効 kind は `VALID_KINDS`）・deps の文字列化を行い、`plan_strategy_kiro` と
  `continue_kiro` の両方で使う。これに `_sanitize_graph` が重なり、LLM 出力の崩れに二段で耐える。
- **stub 擬似実行時間の調整**: `execute_stub` のスリープ（既定 1〜5 秒）は環境変数
  `AGENT_FLOW_STUB_SLEEP_MAX` で変更可能（テストでは `0` にして高速化、約 3 秒で全テスト完走）。

### 7.1 パターン・並列数の選択

| 項目 | `--planner agent` | `--planner stub` |
|------|------------------|------------------|
| パターン選択 | エージェント CLI（`agent_cli`）にカタログ付きプロンプトで選ばせる | 要求のキーワードで判定 |
| 並列数 | エージェント CLI が決める | 要求中の `xN` / `並列N`、無ければタスク数（2〜6） |
| 失敗時 | 解釈不能なら stub にフォールバック | — |

stub のキーワード判定: 分類/振り分け→classify、tournament/最良→tournament、候補/フィルタ→filter、
検証/レビュー→adversarial、繰り返し/通るまで→loop、それ以外→fan-out。

選んだ戦略 `{patterns, parallelism, reason}` は `graph.json` / `final.json` に記録され、`status` でも表示。

---

## 8. orchestrator

```
新規 run:
  1. _plan_strategy(): 要求 → {strategy, tasks(kind付き)}
  2. graph.json に strategy + nodes を書き、tasks を投入、status=running
既存 run（resume）:
  1. graph があれば計画をやり直さず再開（未完タスクから継続）

evaluator-optimizer ループ:
  while True:
    while not 静止(quiesced): sync_pull; sleep      # claim可能/実行中が無くなるまで待つ
    decision, new_tasks = 継続判断(nodes, results, iteration)
    if replan and iteration < max-iterations:
        new_tasks をグラフへ追加（replaces 指定は依存付け替え）
        continue
    break
  統合 → final.json、status=done
```

### 8.1 静止（quiescence）判定

「全タスク終端」ではなく **静止**＝「claim 可能な pending も、実行中(claimed)も無い」状態で評価する。
依存が**失敗**してブロックされた pending（例: 失敗タスクに依存する `synthesize`）は静止扱いとし、
継続判断で依存を付け替える。これにより**デッドロックを回避**する。

### 8.2 パターン別の継続判断（replan）

| 契機 | 追加するタスク |
|------|---------------|
| `classify` 完了 | 分類結果に応じた専門 `work`（ルーティング） |
| `verify` が fail | 依存ノードを作り直し（`generate`）＋再 `verify`。`replaces` で後続の依存を付け替え |
| タスク失敗 | `retry` ノード。`replaces` で後続の依存を付け替え |

**`replaces` による依存付け替え**: 失敗/再生成したノード `X` を新ノード `X'` で置き換えるとき、
orchestrator は `X` をグラフから外し、`X` に依存していた全ノードの deps を `X'` に書き換える。
これにより `synthesize` 等の後続が、失敗した `X` ではなく新しい `X'` を待つようになり、再開できる。

`--max-iterations`（既定 3）で再計画の暴走を防ぐ。さらに **サーキットブレーカー**
（`--max-retries`、設定 `max_retries`、既定 3）が**系統ごと**の作り直し回数を打ち切る:
verify=fail の再生成・失敗タスクの retry は、新ノードに `retries` カウンタを引き継いで
計上し、上限に達した系統はそれ以上再タスクを生成せず `done` で打ち切る（評価役 agent でも
同じ上限を id の `-rN` 連鎖や `retries` から検知し、LLM 呼び出し前に短絡する）。これにより
**達成不可能な完了条件に対して無限に再タスクを積み続ける暴走**を防ぐ（`max_iterations` と
二重ガード）。

---

## 9. worker

```
（負荷分散のため起動位相をランダムに少しずらす）
while True:
  sync_pull()
  candidate = claim可能なノード（pending かつ依存が done）
  if 無い:
    run が終端 かつ not keep-alive → 終了
    idle-exit かつ仕事が尽きた → 終了（デーモンのオンデマンド用）
    else sleep して continue
  try_claim()（競り負けたら continue）
  Heartbeat 開始 → kind別に kiro/stub 実行 → Heartbeat 停止
  results/<id>.json を書く（done/failed）、sync_push
  （タスク後に短いジッタ＝他ノードへ claim 機会を渡す）
```

- `--keep-alive`: run 完了後も常駐待機。`--idle-exit`: 仕事が尽きたら終了（デーモンが使う）。
- 依存ノードの成果は `dep_results` として実行プロンプトへ注入。
- **中間成果物プロトコル**: `output`/`data`（JSON）に乗らない大きな成果物はファイルで
  受け渡す。ワーカーは自ノード用の決定的ディレクトリ `artifacts/<id>/` を用意して
  エージェントへ出力先として渡し、依存ノードの `artifacts/<dep>/` は**中身を本文に貼らず
  パスで参照**させる（後続が成果物を発見でき、かつプロンプト肥大を避ける）。実行後に
  生成された成果物パスを result に記録する。
- **コマンドライン長制限の回避**: 依存成果物が大きいとプロンプトが肥大し、kiro-cli を
  argv で起動する際に OS の ARG_MAX に達して失敗しうる。`run_kiro` は一定サイズ
  （設定 `argv_limit` / `--argv-limit`、既定 100000 bytes）を超えるプロンプトを一時ファイルへ退避し、
  「そのファイルを読んで実行」する短い指示に置き換える（実行後に一時ファイルは掃除）。

### 9.0 実行系プロンプトのスキル外出し（flow-worker）

planner を flow-planner スキルへ外出ししたのと同じ作戦で、`executor: agent` の
実行系プロンプト（worker の全 kind・verify・evaluator の継続判断）を
`flow-worker` スキル（`.github/skills/flow-worker/`）から供給する。

- **分担**: スキルの `scripts/prompt.py` は**決定的なプロンプトビルダー**（LLM を呼ばない。
  stdin JSON → stdout プロンプト）。LLM 呼び出し・役割別エージェント解決（設定 `agents:`）・
  argv スピル・タイムアウトは従来どおり `run_kiro` に残る。
- **入力**: agent-flow がインターフェースで持つ情報をそのまま渡す — kind / goal /
  依存成果（output+data）/ ワークスペース指示（repo_instruction）/ 中間成果物プロトコル /
  workspace・references の構造化 spec / run の元要求（request。worker が全体文脈として使う）、
  evaluator には要求・結果サマリー・人フィードバック・パターンカタログ・max_retries。
  agent-project には依存しない。
- **中身**: flow-worker 固有の実行規律。worker（work/generate/map）は「三つの約束」
  —— 前提を書く（曖昧さは推測解釈を明記）・範囲を守る（影響確認と最小変更・範囲外は報告のみ）・
  検証してから渡す（完了条件との突き合わせと報告契約）。verify は再導出検証
  （結論をなぞらず導き直す・minor/重大の判定規律・再作業者が着手できる粒度の issues）。
  evaluator は受け入れ・具体化・打ち切り（差し戻し goal への issues 転記・new_tasks の膨張禁止）。
- **git 利用規約（worktree 必須）**: 実装系・検証役のプロンプトには、git 操作をスキル同梱の
  `scripts/git_worktree.py`（共有キャッシュ + worktree の provision/release/push CLI。
  cache root は本ツールと同じ `KIRO_GIT_CACHE_DIR` / `$TMPDIR/kiro-git-cache`）に限定する
  規約を常に注入する。エージェントの自発的な clone / checkout / 共有チェックアウトへの
  commit を封じ、並行タスク・人の作業とのコミット衝突を防ぐ
  （パターン正典は docs/designs/git-worktree-cache-pattern.md）。
- **互換**: 出力契約（verify の `verify=pass|fail`＋`{"ok","issues"}`、split の JSON 配列、
  reduce の count 整合、evaluator の decision JSON）はスキル側でも同一に保つ
  （agent-flow のパーサ `_normalize_verify` / `_reconcile_count` / `_coerce_tasks` が前提）。
- **フォールバック**: 検索順は flow-planner と同一（cwd → git root → `~/.kiro/skills` →
  skill-registry.json の skill_home）。未インストール・生成失敗時は組み込みプロンプトで
  続行する（分散ワーカーにスキルが無いノードが混在しても run は成立する）。
  設定 `worker_skill: none` で常に組み込み（既定 `flow-worker`）。

### 9.1 ワーカーバス（executor）— プラグイン方式

ワーカーがタスクを実際に実行するバックエンド。`--executor` / 設定 `executor` で選ぶ。
組み込みの `agent` / `stub` に加え、**kiro-loop の hooks（event_hook）と同じ流儀で
プラグイン化**されている。`--executor` には次を指定できる:

- 組み込み名 `agent` / `stub`
- プラグイン名（例 `gitlab`）→ 検索ディレクトリの `executors/<name>.py` を解決
- `.py` への明示パス

| executor | 実行 | 構造化 data |
|----------|------|-------------|
| `kiro`（既定・組み込み） | ローカルで `kiro-cli` を呼ぶ | STRUCTURED_KINDS を寛容パース |
| `stub`（組み込み） | LLM 非依存の擬似実行 | kind ごとに決定的 |
| `gitlab`（opt-in・プラグイン） | GitLab イシューへ委譲し承認を待つ | イシューのメタ（iid/url/labels） |

**プラグイン契約**: 各プラグインは標準ライブラリのみの単一ファイルで、
`execute(kind, goal, dep_results, model, art_dir, dep_arts) -> (text, data)` を公開する。
`make_executor(args)` が解決し、`_load_executor_module` が `importlib` で動的ロードする
（mtime キャッシュで再ロード対応）。

- **検索順**: スクリプト同階層 `executors/` → リポジトリ `tools/agent-flow/executors/` →
  `~/.agent/agent-flow/executors/`（インストーラ配置）→ 設定 `executor_dir`。
- **設定渡し**: 同名のトップレベル設定ブロック（例 `gitlab:`）を JSON 化し、環境変数
  `AGENT_FLOW_EXECUTOR_CONFIG` でプラグインへ渡す（プラグインは個別環境変数で上書き可）。
  組み込み executor の実体は import 時参照を握らず呼び出し時に `globals()` から解決する
  （monkeypatch・ホットリロードを効かせるため）。
  - **daemon → worker への確実な伝搬**: 設定ブロックの解決は `resolve_executor_config_json(args)` に
    集約し、`make_executor`（worker 内）と `_spawn_worker`（daemon が worker を起動する側）で共有する。
    daemon は親で解決した JSON を `AGENT_FLOW_EXECUTOR_CONFIG` として **worker の起動 env に明示注入**する。
    これにより worker が `--config` を再解決できない／別の設定ファイルを拾う場合でも、親（daemon）が
    解決した値（例 gitlab の `repo_url`/`conn_label`）が確実に届く。worker 側 `make_executor` は、
    自分で設定ブロックを解決できたときだけ env を更新し、解決できない（空/None）ときは親が注入した
    値を尊重して上書きしない。
- **インストール**: `install.sh` が同梱プラグインを `~/.agent/agent-flow/executors/` へコピーする
  （kiro-loop が補助アセットを `~/.agent/` 配下へ置くのと同じ流儀）。単一ファイル配布後も
  `--executor <name>` が名前解決できる。

**gitlab ワーカーバス**（opt-in・`executors/gitlab.py`）: 各ワーカータスクを gitlab-idd
スキルの `gl.py` で GitLab イシュー化して委譲する。設計上の要点:

- **起票**: `gl.py create-issue` で `## 目的` ＋（依存成果）＋ `## 受け入れ条件` を本文に持つ
  イシューを `status:open,assignee:any`（＋優先度）で作る。本文は argv 長制限を避け
  `--body-file` 経由で渡す。リモートのワーカーが gitlab-idd の規約でこれを拾って実装する。
- **完了判定（自動承認・`auto_merge` 既定 on）**: イシューの状態をポーリングして決着を判定する
  （`_check_decision`・executor 内で完結）:
  - **自動マージ**（`_try_auto_merge`）: イシューが `status:approved`（レビュー通過）かつ関連 MR が
    **クリーン**（コンフリクト無し＝`has_conflicts`/`merge_status != cannot_be_merged`・未解決の
    レビューコメント（resolvable かつ未 resolved の議論）無し）→ executor が
    `PUT /merge`（`should_remove_source_branch`）で**マージし、イシューをクローズ（status:done）**して
    **成功** を返す（gitlab-review-viewer の承認ボタンと同じ規則）。特殊ケースも同じ:
    **差分なし MR**（/changes が空）はクローズ＋ソースブランチ削除、**MR 無しで approved** は
    イシュークローズのみで承認。verify はこの後 agent-project が downstream で実施する。
  - **差し戻し**: approved なのに未クリーンなら `# 差し戻し` 見出しの固定コメントを投稿し
    `status:approved` → `rework_label`（既定 status:needs-rework）へ付け替えて未決着のまま待つ
    （ワーカーの修正 → 再レビューのループへ。ラベル遷移自体が再発火ガード）。マージ API の失敗
    （権限 403・405 等・一過性障害）は決着させず次のポーリングで再試行する（run を殺さない）。
  - **人が先にすべてマージ** → 承認（従来経路・`_mr_decision`。`auto_merge: false` ならこの経路のみ）。
  - **一つでも未マージでクローズ** → 却下。イシューの**人コメント**を取り込み（無ければ空＝自動判断）、
    元イシューをクローズして `RuntimeError([gitlab-reject] …)` を送出。上位（agent-project）が通常
    リトライで再委譲し、コメントを次 act の指示に活かす。
  - どちらでもないうちは待機。MR が無いまま人が issue をクローズしたら取り下げ＝却下扱い。
  - **クローズ主体（`close_issues`）**: `auto`（既定）は決着時に executor がイシューをクローズ。
    `manual` は**クローズを人に委ね、人がクローズするのを監視**する — 承認条件（全 MR マージ）が
    揃ったら案内ノートを一度だけ投稿（`<!-- agent-flow:close-request -->` マーカーで冪等化）して
    未決着のまま待ち、人のクローズ（`_closed_issue_decision`）で承認決着する。却下（未マージ
    クローズ）と cancel の取消クローズは manual でも従来どおり executor が行う。
  レビューは遅延しうる前提で即応性は求めない: `poll_interval` 既定 300 秒、`timeout`（既定 7 日・
  全体上限）と `approved_timeout`（既定 14 日・MR 出現/approved ラベル検知後の猶予）。いずれも 0 で無限。
  自動マージには api スコープのトークンが必要（read 系のみだとマージ 403 → 人のマージ待ちに落ちる）。
  ポーリングは agent-flow（Python）であって LLM ではないため、gitlab-idd の「LLM ポーリング禁止」とは別物。
- **成果**: 承認時は `data` に issue iid/web_url/`decision:"approved"`/`merged_mrs`/closed を残す
  （成果物の実体は GitLab 上のマージ済み MR にある）。
- **再計画はローカル**: evaluator-optimizer の継続判断はオーケストレータ側で行う。`_continue`
  は executor が `stub` のときだけ stub 継続、それ以外（`agent` や任意のプラグイン）はローカル
  `agent` で判断する（プラグインはワーカータスクの実行のみを委譲し、メタ評価はローカルに残す）。
- **opt-in**: 既定 executor は `agent` のまま。明示選択時のみ有効で、`gl.py` 未発見/接続未設定なら
  起票時に明確に失敗する（誤選択で無限待ちにしない）。設定は `gitlab:` ブロック。

### 9.2 成果物リポジトリへの納品（delivery）

成果物（プログラム/ドキュメント）の実体は**バスではなく成果物リポジトリ**に置き、バスには
「サマリー＋リンク（どのブランチ/MR/イシューに成果があるか）」だけを残す。リポジトリのルーティング
（タスク→書込先）は制御層 agent-project が担う（詳細は `tools/agent-project/ROUTING.md`）。
agent-flow は **1 run = 1 ワークスペース（唯一の書込先）** に固定する。

- **ワークスペース（`--workspace`・ちょうど 1 つ）**: その run の唯一の書込先。素の URL か JSON
  `{url,path,base,target,desc,branch}`。`branch` は任意の**明示作業ブランチ**（agent-project の
  タスク単位ブランチ ap/<task-id> 等）。指定があれば run 毎の `af/<run-id>` の代わりにそこへ push する
  ＝リトライ（別 run-id）でも同一ブランチへ成果を積み増せる（provision の refs 優先順
  [branch, base] が既存ブランチから再開する）。agent-flow が作業ツリーを用意してワーカーへ渡す。**作業ツリーは URL 単位の
  ホスト共有 bare ミラー（`--mirror --filter=blob:none`）から detached worktree を生やして用意**し、フル clone を
  「初回 1 回+増分 fetch」へ圧縮して GitLab の pack 生成負荷を抑える（詳細は
  [git-worktree-cache-pattern.md](git-worktree-cache-pattern.md)）。detached のまま編集し、**変更があれば
  agent-flow が commit して `push HEAD:refs/heads/af/<run-id>`**（ブランチを checkout しないので「同一ブランチの
  二重 checkout 不可」制約を受けない／分散 worker は同じ `af/<run-id>` へ push し rebase リトライで統合）。
  毎回 fetch してから最新コミットで worktree を作るので**鮮度は都度 clone と同等**、ミラー不可なら従来の direct
  clone へフォールバック。**変更が無ければ push しない**＝調査だけの読み取り専用グラフでは何も書き込まない
  （`finalize_workspace`）。デリバリ（branch/commit/target）を result に記録。
- **参照リポジトリ（`--reference`・複数可・読むだけ）**: clone はせず、エージェントのプロンプト（参照節）と
  gitlab イシュー本文の『## 参照リポジトリ』節へ描画する。書込先は参照に含めない。
- **executor 横断インターフェース**: executor 契約に構造化 `workspace`（spec dict）と `references`（spec の列）を
  渡す。`workspace_instruction` が全 executor へ渡る指示文（LLM 向け）。gitlab executor は **workspace URL から
  起票先 GitLab プロジェクトを解決**（無ければ `gitlab.repo_url` フォールバック）し、対象/参照リポジトリ節を
  構造化 spec から Markdown 整形する（ローカル clone パスは載せない）。
- **gitlab の納品（自動承認・作業履歴を GitLab に残す）**: リモート worker が MR を用意し、レビューが
  `status:approved` に達したら executor が**クリーンな MR を自動マージしてイシューをクローズ**する
  （GitLab は Merged MR ＋ closed イシューの台帳として残る）。**人が先に全 MR をマージ＝承認**、
  **一つでも未マージクローズ＝却下**（人コメントを取り込み元イシューをクローズして失敗を送出。
  上位の通常リトライがコメントを活かして再委譲）も従来どおり効く。詳細は §9.1 完了判定。
  `approved_timeout`（長め・設定可能）でレビュー決着を待つ。`auto_merge: false` で従来の
  「人が関連 MR を管理」モードに戻せる。

---

## 10. デーモン（オンデマンド起動）

```
while 常駐:
  sync_pull()
  死んだ子（orchestrator/worker）を刈り取る
  (1) inbox の新要求 →（run 未作成なら）claim_request で 1 台に決定 → orchestrator 起動
  (2) 各 active run の claim 可能タスク数を見て、worker を起動
      （--max-workers 上限・idle-exit の短命ワーカー）
  sleep(poll)
```

- **要求 claim**: `inbox/claims/<req>/<who>` に対し claim プロトコルを適用し、分散時も**1 台の
  デーモンだけ**がその要求を orchestrate する。
- **オンデマンド worker**: claim 可能タスク量に応じて起動。仕事が尽きれば worker は自然終了し、
  新たな仕事が来れば再び起動される。
- **冪等な起動**: デーモンはバス単位の singleton。起動時に `_daemon_lock_path`（バス外の一時領域、
  ローカルは bus 絶対パス / git は remote@branch/subdir をキーに）へ `fcntl` 非ブロッキング排他ロックを
  取り、既に稼働中なら何もせず終了する。`agent-flow daemon` の重複呼び出しは安全（多重起動しない）。
- 分散は各 PC で `agent-flow --git <repo> daemon` を動かすだけ。要求はどの PC から `submit` してもよい。

---

## 11. CLI / サブコマンド

状態で挙動が決まるものは 1 コマンドに統合してある。

| コマンド | 役割 |
|---------|------|
| `daemon` | 常駐し orchestrator/worker をオンデマンド起動（`--max-workers`）。**サブコマンド省略時の既定**（global 引数と設定ファイルのみで起動） |
| `submit <要求>` | 要求を inbox に投入（run-id を返す） |
| `run [要求]` | 単発実行。**既存 --run-id なら再開、無ければ新規**（状態で自動判断） |
| `status` | 状態表示。既定 1 回 / `--follow` でライブ監視（`--until-done`） |
| `gc` | 古い run を削除（`--older-than` / `--keep` / `--status` / `--dry-run`） |
| `orchestrate` / `work` | 内部コマンド（`run` / `daemon` が起動） |

主なグローバル/共通オプション: `--bus`、`--git` / `--git-branch`（分散）、`--lease`、
`--planner` / `--executor`（`agent` | `stub`）、`--max-iterations`、`--poll`、`--workers`。

インストール: `bash tools/agent-flow/install.sh` で `~/.local/bin/agent-flow` に導入（標準ライブラリのみ、
pip 依存なし。git は分散用、kiro-cli は実運用用で無くても stub で動く）。

`status` は公式 Dynamic Workflows 風のダッシュボード（進捗バー・エージェント状態ツリー（依存深さで字下げ）・
直近アクティビティ・最終結果）を表示する。`--follow` でライブ監視、`--list` で run 一覧。

Claude Code スキル `.github/skills/agent-flow/` がこの CLI の呼び出し（run/submit/daemon/status/gc の
使い分け・要求の書き方）を案内する。

### 11.1 設定ファイル（環境依存値の外部化）

環境ごとに決まる値を設定ファイルへ外出しできる（kiro-loop と同じ流儀）。

- **探索順序（フォールバック）**: `--config <path>` → `./.agent/` → `~/.agent/` の
  `agent-flow.{yaml,yml,json}`。
- **形式**: PyYAML があれば YAML、無ければ JSON（同じキー。PyYAML は任意）。
- **優先順位**: CLI 引数 > 設定ファイル > 組み込み既定（`CONFIG_DEFAULTS`）。
- **実装**: 設定対象オプションの argparse 既定を `None` にし、parse 後に `resolve_config(args)` が
  「CLI 未指定（None）の値だけ」を設定ファイル→既定で埋める。`--model_opt ""`（子プロセスが渡す
  「モデル指定なし」）は resolve 後に `None` へ正規化するため、設定ファイルの `model` が子へ漏れない。
- **キー**: `bus` / `git` / `git_branch` / `git_subdir` / `planner` / `executor` / `model` /
  `max_workers` / `workers` / `max_iterations` / `max_fanout` / `max_retries` / `argv_limit` /
  `poll` / `lease`。閾値（`max_retries` のサーキットブレーカー、`argv_limit` の argv 上限）も
  環境変数ではなくこの設定ファイルで調整する。
- 子プロセス（orchestrate/work）へはこれらを**明示フラグ**で渡すため、子側 resolve は同じ値を保ち整合する
  （`argv_limit` は free 関数 `run_kiro` から参照するため、各プロセスの resolve 後にモジュール変数へ確定させる）。
- **役割毎のエージェント上書き（`agents:`・yaml 専用）**: LLM 呼び出しの単一チョークポイント
  `run_kiro(prompt, model, purpose)` に役割が通っており、`agents:` マップで**役割ごとに
  agent_cli / model を上書き**できる（`_agent_for`）。キーは `planner`（戦略選定・分解）/
  `evaluator`（継続判断・再計画）/ `worker`（全ノード kind の既定）/ 個別 kind
  （`VALID_KINDS`。worker より優先）。未指定はグローバル `agent_cli` / `model`。
  子プロセスへは `--config` の絶対パス伝搬（`_child_base`）で同じ設定が届く。未知キー・
  不正値は黙って落とす。用途例: planner は opus・大量に走る map は haiku・verify gate は
  別 CLI。agent-project 側の処理毎上書き（plan/assess/verify 等）は agent-project 設計書 §9。

サンプル: `tools/agent-flow/agent-flow.yaml.example`。

---

## 12. 整合性・障害対応

| 懸念 | 対処 |
|------|------|
| 二重実行 | 名前空間付き claim ＋ 決定的タイブレークで勝者は 1 人 |
| push 衝突 | 書き込み所有権の分割（disjoint）＋ `pull --rebase` リトライ |
| 孤児タスク（ワーカー死） | lease 期限切れで `_winner` が無視 → 再 claim 可能 |
| 孤児 run（daemon 消失＝PC シャットダウン/クラッシュ） | 生存リース（`orch_lease_until`）切れを検知 → reclaim して**同じ run-id で自動再開**（確定済み results/ を活かし続きから）。進捗なしの連続再開が `max_resumes`（既定 3・進捗で数え直し）を超えたときだけ failed に確定（消費者の永久待機を防ぐ） |
| 長時間タスクの横取り | Heartbeat が lease を延長 |
| タスクのハング（プロセス生・無進捗） | task timeout（`AGENT_FLOW_KIRO_TIMEOUT`）で kiro-cli を kill → failed → retry（ADR §17） |
| 失敗依存によるデッドロック | 静止判定 ＋ `replaces` による依存付け替え |
| 無限再計画 | `--max-iterations` |
| 達成不可能条件での無限作り直し | サーキットブレーカー（`--max-retries`、系統ごとの `retries` 計上で打ち切り） |
| 大きな依存成果物でコマンドライン長制限 | プロンプトを一時ファイルへ退避し参照渡し（設定 `argv_limit`） |
| 中間成果物のパスが不定で後続が発見不能 | ノードごとの決定的な `artifacts/<id>/` ディレクトリでファイル参照 |
| 空ディレクトリ未追跡（git） | 書き込み時に親ディレクトリを自動生成 |
| run の蓄積 | `gc` で掃除（git バスは git rm＋push） |

---

## 13. テスト

`tools/agent-flow/tests/test_agent_flow.py`（kiro-cli 不要・標準ライブラリのみ）。

- **プロトコル/障害注入**: 決定的タイブレーク、lease 切れ claim の回収（死んだワーカー）、
  同時 claim でも勝者は 1 人、状態遷移。
- **分解**: 並列 `;` / 逐次 `->` の依存抽出。
- **6 パターン**: パターン検出、並列数抽出、fan-out/tournament のグラフ形、classify ルーティング、
  verify fail の作り直し。
- **デーモン**: 要求 claim の単一勝者、run 既存時の claim 拒否、`run_claimable_count` の依存考慮。
- **構造化成果 / map-reduce / gate / 健全性検査 / kind 正規化**: P1〜P4 の各機能。
- **end-to-end**: stub で全完了（fan-out + 統合）、失敗 → 再計画 → retry 成功、map-reduce + review。
- **分散統合（`GitDistributedTests`、git 必須）**: ローカルのベアリポジトリを共有バスにし、ノードごとの
  独立クローン（＝別 PC 相当）から push/pull させて検証する。
  - 別クローンからの同一タスク claim → 勝者は 1 人（両クローンから見て同じ勝者）
  - 別クローンの 2 デーモンが同じ要求を claim → orchestrate 担当は 1 台
  - orchestrator + worker が各自の独立クローンから git バス越しに完走
  - `--git-subdir` ＋ sparse checkout で無関係ディレクトリを作業ツリーに展開しない

```bash
python3 tools/agent-flow/tests/test_agent_flow.py
```

---

## 14. 既知の制限・今後

- **負荷分散は heuristic**: 起動位相ずらし＋タスク後ジッタで緩和するが、ウォームなノードが連続
  claim しやすい（瞬時に終わる stub で顕著）。実 kiro-cli は実行に時間がかかり自然に分散する。
  公平分配（work-stealing）は今後の課題。
- **ハートビート間隔の下限** `max(2 秒, lease/3)`。lease は実タスク時間に対し十分大きく設定する。
- **inbox の蓄積**: `submit` した要求ファイルは残る（run 作成済みなら再処理されない）。`gc` で掃除。
- **成果物の大容量対応**（git-lfs 等）は未対応。

---

## 15. マイルストーン履歴

| M | 内容 |
|---|------|
| M1 | ローカルバス・claim プロトコル・一発起動 |
| M2 | git バスで複数 PC 分散（名前空間付き claim＋決定的タイブレーク、rebase リトライ、lease 回収） |
| M3 | 再計画ループ（evaluator-optimizer）・resume・lease ハートビート・負荷分散の位相ずらし |
| M4 | 依存付き分解（`;`/`->`）・ライブ可視化・`gc`・障害注入テスト |
| M5 | 常駐デーモン・`submit`/inbox 要求キュー・要求 claim によるデーモン選出 |
| M6 | `install.sh` で `agent-flow` コマンド化・サブコマンド整理（`run`/`status --follow`） |
| M7 | 6 ワークフローパターン（記事準拠）の戦略選択・ノード kind・パターン別継続 |
| P1–P4 | 構造化成果＋reduce / 7 つ目のパターン map-reduce（データ駆動 fan-out: split→map→reduce）/ 複合パターン＋統合前 gate＋健全性検査 / planner 正規化 |

---

## 16. ADR: ワークフロースクリプトの動的生成は当面採用しない

- **ステータス**: 採用（2026-06-13）。当面コードハーネス生成は導入しない。
- **文脈**: 公式 Claude Dynamic Workflows は、Claude が**タスク専用ハーネス（コード）**を生成し、
  サブエージェントの spawn と JS（Math/JSON/Array）でのデータ加工を**実行**することで動的な
  オーケストレーションを実現する。agent-flow は宣言的タスクグラフ＋継続ルール＋データ駆動 fan-out で
  同等の動的性を、コードを実行せずに表現している。

- **決定**: LLM 生成コードの実行（コードハーネス）は**現時点では採用しない**。表現力が不足する場面が出たら、
  まず**宣言的語彙の拡張**（条件付きエッジ、reduce/transform 演算の指定、新ノード kind 等）で対応する。

- **理由**:
  1. **セキュリティ**: LLM 生成コードの実行＝任意コード実行。分散＋git バス文脈では、ハーネスがバス
     リポジトリへ push・情報持ち出し・無制限 spawn を行いうる。安全に運ぶにはプロセス分離・資源/時間
     上限・FS/ネットワーク遮断のサンドボックスが必須で、コストとリスクが大きい。
  2. **分散との不整合**: agent-flow の核は「git バス＋claim による分散実行」。プロセス内 spawn 型の
     ハーネスは claim/lease/複数 PC に自然に乗らない。バスのタスクへ翻訳すれば結局現行の宣言的グラフと
     同じになる。
  3. **可観測性・再現性**: グラフは状態をファイル存在から導出でき、`status` で可視化、git で差分追跡、
     `resume` で再開できる。走るスクリプトは不透明で、中断再開・監視・監査・障害復旧が難しい。
  4. **暴走制御**: 宣言的ループは `max_iterations` / `max_fanout` で二重ガードできるが、任意コードは
     サンドボックスで強制しない限り無制限になりうる。

- **再検討の条件 / 代替**: どうしてもコード生成が必要になった場合は、**分散バスから切り離した
  「単一ノードのローカルハーネスモード」をオプトイン**で用意し、サンドボックス（別プロセス・タイムアウト・
  FS/ネット遮断・生成コードのバス書き込み禁止）を必須とする。分散実行・可観測性を損なわない範囲に閉じる。

- **影響**: 当面は宣言的グラフ路線を継続。表現力の不足は語彙拡張で吸収する。

---

## 17. ADR: タスクのハングは lease ではなく task timeout で守る

- **ステータス**: 採用（2026-06-14）。`run_kiro` に subprocess タイムアウトを導入。

- **文脈**: ワーカーが kiro-cli 呼び出しでハングし、run 全体が永久停止する事象が発生した
  （`split→…→gate→synth` で一部 work の kiro-cli が無進捗のまま固まり、gate/synth が待ち続けた）。
  原因は **lease/heartbeat が「プロセス生存（liveness）」の信号であって「タスク進捗（progress）」では
  ない**こと（§5.2）。心拍は別スレッドで `lease/3` ごとに鳴るため、メインスレッドが
  `subprocess.run(kiro-cli…)` でブロックされても、プロセスが生きている限り lease を延長し続け、
  lease 失効による孤児回収が発動しない。lease は元来**死んだワーカー**を検出する仕組みで、
  **生きているがハングしたワーカー**は構造的に検出できない。

- **決定**: 失敗モードを2つに分離し、**それぞれ別機構**で守る。
  - **ワーカー死亡** → lease/heartbeat（既存・据え置き）。
  - **タスクのハング** → **task timeout**：`run_kiro` の `subprocess.run(timeout=…)`
    （既定 600s、`AGENT_FLOW_KIRO_TIMEOUT` で調整、`0` で無効）。超過時はタスクを failed 記録 →
    再計画の retry に回し、run を前進させる。

- **理由**:
  1. **進捗信号の不在**: subprocess は進捗を出さないため、「遅いが動いている」と「ハング」を
     区別する唯一の実用手段が wall-clock の timeout。agent-flow のタスクは実質 LLM 1 コールで
     所要時間が元々有界なので、固定 timeout で十分カバーできる。
  2. **リソース解放**: `subprocess.run(timeout=…)` は超過時にハングした kiro-cli を kill する。
     lease を切るだけでは zombie プロセスが残るため、timeout の方が筋が良い。
  3. **機構の合成**: 心拍は `execute` 実行中のみ動き、その `execute` を timeout が有界化するので、
     心拍が無限延長することはなくなる。2機構は責務が分離したまま綺麗に合成される。

- **不採用の代替**:
  - **lease 延長に上限**（生存でも一定時間で回収可能化）: 正当に長いタスクを誤って横取りし、
    かつハングした kiro-cli を kill しない（リソースが残る）。timeout の方が直接的かつ確実。
  - **進捗連動の心拍**（`Popen` で stdout をストリーム読みし、バイトが流れている間だけ心拍を打つ）:
    liveness ではなく真の progress でゲートでき「長いが生成中」を延命・「無音で固着」のみ kill できる、
    本筋の改良。ただしタスクが元々有界な現状では複雑さに見合わないため見送り。**長尺タスクを将来
    サポートしたくなった時点で再検討**する。

- **影響**: ハングしても run は無限停止せず、bounded failure → retry で終端へ進む。固定 timeout が
  正当な長尺タスクに対して短すぎる場合は env で延長、または上記「進捗連動の心拍」へ移行する。

---

## 18. 設計提案: gitlab 人コメントの人/エージェント判別・emit と分解への還元（Draft・未実装）

> ステータス: Draft（設計案・未実装）。対になる agent-project 側は
> [`agent-project-design.md` §11](./agent-project-design.md)（統一学習バス・蒸留・recall・verify 品質）。
> 責務境界は「**gitlab executor（本ツール）はコメントを運ぶだけ／蒸留・learn・recall・verify は agent-project**」。

### 18.0 背景

gitlab executor で委譲したイシューは **gitlab-idd スキルのエージェント（worker / reviewer）** が実行する。
ユーザーがイシューに投稿したコメントは「そのイシュー内（同一タスクの次の試行）」にしか活きず、同様のタスクへ
還元されない（＝agent-project §11 の問題A）。本ツール側の欠落は 2 つ:

1. **人コメントの捕捉が却下時のみ**（`_rejected_payload:861-886` → `_human_comments:443-459`）。承認・作業中は拾わない。
2. **人/エージェント判別が緩い**。`_human_comments` の除外は `system` note・`gitlab-idd:creator-node-id` マーカー・
   `agent-flow:` 接頭辞だけ。gitlab-idd の worker/reviewer は着手・scout・設計記録・clarification・approach 等の
   自由文コメントを多数投稿し、**一部（設計記録等）はマーカー無し**。現状はこれらを「人コメント」として拾ってしまう
   ＝**エージェントの独り言が横断学習を汚染する**。

### 18.1 人コメントのみを確実に拾う（gitlab-idd 前提の判別）← 最優先

マーカー頼みでは不十分（マーカー無し自由文がある）。**著者アカウントベースの正判定を主軸に多層で守る**。
コメントは次の 3 層を **AND** で満たしたときだけ「人」とみなす（emit 側で著者情報を運び、最終判定は agent-project §11.2）。

1. **著者アカウントで正判定（主軸）**: `author.bot == true`（プロジェクトアクセストークン等のボット）を除外。
   設定 `gitlab.agent_authors`（worker/reviewer/requester が動くアカウント）に一致すれば除外。
   `gitlab.human_reviewers`（allowlist）があればそれ以外を除外（最も厳密）。
2. **プロトコルマーカーで除外（常時・全マーカー）**: `system` note、`agent-flow:` 接頭辞、**いずれかの
   `<!-- gitlab-idd:* -->` マーカー**（`creator-node-id` / `worker-node-id` / `scout-map` /
   `clarification-requested` / `approach-proposed` / `non-requester-reviewed`）。現状 creator のみ→**全マーカーへ拡張**。
3. **エージェント著者の自動学習（per-issue）**: 自分が起票したイシュー上の `worker-node-id` /
   `non-requester-reviewed` マーカーコメントの**著者アカウントを抽出**し、そのアカウントの**マーカー無しコメントも
   同イシューではエージェント扱い**にする（手設定なしでマーカー無し自由文を漏れなく除外）。

**既定の振る舞い（実装）**: エージェント除外は ①〜③（system / 全 gitlab-idd マーカー・`agent-flow:` /
bot・`agent_authors`・per-issue 自動学習）で担保する。**allowlist（`human_reviewers`）が無ければ、除外後に
残った通常コメントは人として拾う**（後方互換）。`human_reviewers` を指定すると「許可された人だけ」に絞る
厳密モードになり、著者情報の無いコメントは突き合わせ不能なので既定で落とす（`gitlab.trust_unmarked_comments:
true` で拾う）。＝「無暗にエージェントを拾わない」は ①〜③ で満たしつつ、過度な取りこぼしはしない。

> **実装状況**: §18.1（判別）と §18.2 の決着時 emit（却下＋承認の著者付き `notes`）は実装済み
> （`gitlab.py`: `_human_notes` / `_human_notes_payload` / `_finish_approved` / `_rejected_payload`、
> 設定 `agent_authors` / `human_reviewers` / `trust_unmarked_comments`、テスト
> `GitlabHumanAgentDiscriminationTests`）。§18.2 の**作業中の逐次 emit**（park&poll 相乗り）と §18.3
> （flow-planner `--learnings`）は未実装（フォローアップ）。

### 18.2 人コメントの統一 emit（却下・承認・作業中）

判別（§18.1）に必要な生データを、決着以外も含めて**著者情報つきで運ぶ**（判定・蒸留・learn は agent-project）。

- **決着時の対称化**: `_rejected_payload` に加え承認 payload でも人コメント候補を `data.notes` に載せる。
  従来 done の result は人コメントを運ばず正例を捨てていた。
- **作業中の逐次 emit**: park & poll の監視（`watch_interval` 既定 90 秒）に相乗りし、前回以降に増えた人コメントを
  `data.notes` 増分として運ぶ（GitLab API は `get-comments` 1 本・既存バッチに畳む＝負荷増やさない）。
- **emit する構造**: `data.notes = [{note_id, author:{id,username,bot}, system, body, ts}]`。生のまま運び、
  `note_id` で決着時・作業中の重複排除。

### 18.3 分解への還元 — flow-planner の `--learnings` 受け口

agent-project が recall した learn/avoid を、要求本文とは別の **`--learnings`**（構造化・有界）channel で
flow-planner（`.github/skills/flow-planner/`）へ渡す。要求本文に畳むと分解後の各ノードに薄まるため、
**戦略選定段の判断材料として独立注入**し、分解グラフ自体を変える（例: 「この種は 1 段細かく割れ（granularity 上げ）」
「集約前に verify gate を挟め」「この分割は避けよ」）。件数・文字数は有界化し planner を振り回さない。
`patterns-catalog.yaml` に learnings を受けた戦略調整例を variants として追記する。

### 18.4 途中の差し戻し ＋ 人フィードバック駆動の再計画（待機ノード変更/ノード追加）

**要件**: 作業中（claimed）ノードはリアルタイムに変えなくてよい。ただし人の指摘に応じて **run の待機ノードの
差し替え・ノード追加**ができ、さらに終端の approve/reject だけでなく**途中の差し戻し**も拾いたい。

**設計（executor 非依存のコントラクト ＋ プラグイン内の入力解釈、で分離）**:

- **差し戻しの検知はプラグイン内**（gitlab.py `_rework_requested`）: 人コメントの**見出し**（markdown 見出し or
  見出し的な先頭行）に差し戻し語（設定 `rework_heading`・既定「差し戻し」）があれば拾う。エージェントコメントは
  §18.1 の判別で除外。これを**汎用の結果コントラクト `data.decision="rejected" + guidance` に変換**して返す
  （`_check_decision` に組み込み。イシューを閉じない要修正でも approve/reject を待たず拾える）。
  → **本体（orchestrator）に gitlab 固有の分岐は作らない**。別 executor が rework を実装したければ同じ
  `data.decision/guidance` を埋めるだけで同じ経路に乗る。
- **再計画は結果コントラクトを汎用に読む**（agent-flow.py `human_feedback_from_results`）: 全ノード結果の
  `data.guidance` / `data.notes[].body` を executor 名で分岐せず集め、評価役（`continue_kiro`）のプロンプトへ
  **「人からの指摘（最優先）」**として注入する。評価役は new_tasks 追加や、**未着手の待機ノードの差し替え
  （`replaces`）**で対応する。
- **作業中ノードは構造的に不変**: 静止時の再計画は run が**静止（`_quiesced`＝claimed も waiting も無い）**したときだけ
  走るため実行中ノードを触らない。加えて **in-flight 反映**（下記）も待機（pending）ノードのみ書き換え、claimed/waiting/
  終端には及ばない（lease 保護と二重担保）。

- **in-flight 反映（静止を待たない待機ノードの即時書き換え）**（`_inflight_amend_pending`・実装済み）: orchestrator の
  待機ループが毎ポーリングで、settled ノードに新しく載った人フィードバック（`data.guidance`/`notes`）を**待機
  （pending）ノードの spec（goal）へ決定的に注入**する。これで「差し戻し → 静止を待たず待機ノードへ即反映」が成立する。
  **決定的・冪等**（発生源ノード＋長さで dedup。同一指摘は二度入れない）で LLM を使わないため二重生成の心配が無い。
  **ノード*追加*は静止時の評価役（`continue_kiro`）に委ねる**（in-flight で LLM 追加すると静止時と二重生成しうるため）。
  ＝ **待機ノードの変更は in-flight（即時・決定的）／ノード追加は静止時（評価役・人指摘駆動）** の役割分担。

これで「人がイシューにコメント（差し戻し/却下/承認）→ 決着 or 差し戻し検知 → **待機ノードは即時反映・ノード追加は次の
静止で人指摘駆動**、実行中は常に不変」が成立する。**gitlab 固有なのは入力解釈だけ**で、in-flight 反映・伝播・再計画は
全 executor 共通（`data.guidance`/`notes` を汎用に読む）。

### 18.5 スコープ外

- **agent-flow の `verify` ノード（LLM 判定・`execute_kiro(kind="verify")`）の CLI 化**は本案対象外。本案が対象にする
  「不確実性をなくす verify」は agent-project 側の verify（終了コードゲート・§11.6）。将来課題。
- **in-flight での *ノード追加***（静止を待たず新ノードを足す）は非対象。静止時と二重生成しうるため、追加は
  `_quiesced` 後の評価役に一本化する（待機ノードの*書き換え*のみ in-flight）。

### 18.5 影響ファイル（本ツール側）

| 箇所 | 変更 |
|------|------|
| `executors/gitlab.py` `_human_comments` 443-459 | §18.1 全 `gitlab-idd:*` マーカー除外・著者 bot/`agent_authors` 判定・per-issue エージェント著者学習（実装済み） |
| 〃 承認/却下 payload・`_human_notes_payload` | §18.2 承認・却下の著者付き `notes` emit・`note_id` 重複排除（実装済み。作業中の逐次 emit は未） |
| 〃 `_rework_requested` / `_heading_has` / `_check_decision` | §18.4 差し戻し見出し検知→汎用コントラクト（rejected+guidance）変換（実装済み） |
| `agent-flow.py` `human_feedback_from_results` / `continue_kiro` | §18.4 結果コントラクトの人指摘を replan へ汎用注入（実装済み・executor 非依存） |
| `agent-flow.py` `_inflight_amend_pending` / orchestrate 待機ループ | §18.4 in-flight で待機ノードへ人指摘を即時反映（実装済み・決定的・冪等・実行中不変） |
| `agent-flow.yaml.example` / `CONFIG_DEFAULTS` `gitlab:` | `agent_authors` / `human_reviewers` / `trust_unmarked_comments` / `rework_heading`（実装済み） |
| `.github/skills/flow-planner/` 戦略選定段 / `patterns-catalog.yaml` | §18.3 `--learnings` 受け口・戦略調整 variants（未実装。現状は要求本文経由で planner に届く） |
