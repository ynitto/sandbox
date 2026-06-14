# kiro-flow — git 共有型・分散 Dynamic Workflow 設計書

> 作成日: 2026-06-13
> 対象ブランチ: `claude/kiro-cli-dynamic-workflow-90ezwe`
> 関連ファイル: `tools/kiro-flow/kiro-flow.py`, `tools/kiro-flow/install.sh`,
> `tools/kiro-flow/tests/test_kiro_flow.py`, `tools/kiro-flow/README.md`

---

## 1. 概要

kiro-flow は、kiro-cli を頭脳にして **Claude 風の Dynamic Workflow**
（実行時にタスク構造を動的生成 → ワーカーへ委譲 → 結果を評価して再計画 → 統合）を実現する基盤。

特徴:

- **通信はファイルのみ**。メッセージバスをローカルディレクトリにも **共有 git リポジトリ**にもでき、
  後者にすると**複数 PC へそのまま分散**できる。
- orchestrator は [Claude Dynamic Workflows の 6 パターン](https://zenn.dev/aria3/articles/claude-code-dynamic-workflows-6-patterns)
  をカタログとして持ち、**要求からパターンの組み合わせと並列数を選んで**タスクグラフを形作る。
- **常駐デーモン**が要求に応じて orchestrator / worker を**オンデマンド起動**する。
- LLM 実行は kiro-cli が既定。kiro-cli 無しでも動く **stub** モードでプロトコルを検証できる。

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
**Agent / Orchestrator-Workers** を区別する。kiro-flow は後者を志向し、さらに以下を満たす:

| 要件 | 実現方法 |
|------|---------|
| 実行時の動的タスク分解と再計画 | orchestrator が kiro-cli でグラフ生成・評価・追加 |
| 複数 PC への分散 | git リポジトリをバスにし、各ノードが自分のクローンで push/pull |
| 競合しない協調 | ファイル存在で状態を導出 + 名前空間付き claim + 決定的タイブレーク |
| 要求に応じたパターン選択 | 7 パターン（記事の 6 ＋ kiro-flow 追加の map-reduce）のカタログから組み合わせ・並列数を選択 |
| オンデマンド起動 | 常駐デーモンが要求量・タスク量に応じてプロセスを起動 |

### 既存ツールとの差別化

| ツール | 構造 | 決定タイミング |
|--------|------|--------------|
| `kiro-loop` | 定期プロンプト送信 | 静的 |
| `multi-agent-shogun-kiro` | 将軍/家老/足軽の固定階層 | 静的 |
| **`kiro-flow`** | **タスクグラフ** | **実行時に LLM が生成・更新** |

設計思想は `git-file-sync`（git をハブにした同期）と `gitlab-idd`（キューからの claim→実行→報告）の
組み合わせに、タスクグラフの動的生成を加えたもの。

---

## 3. 全体アーキテクチャ

役割は固定でなく**起動モード**で決まる（同一スクリプト）。

| 役割 | 起動 | 仕事 |
|------|------|------|
| **daemon** | `kiro-flow daemon` | inbox 監視→orchestrator 起動 / タスク量に応じ worker 起動 |
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
  results/<id>.json    # 成果（claim 成功者のみ書く）
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

---

## 7. ワークフローパターン（7 パターン）

orchestrator は要求を見て、7 パターンから組み合わせと並列数を選び、各ノードに **kind** を付けた
タスクグラフを生成する。**最初の 6 つ**は [Claude Dynamic Workflows の 6 パターン](https://zenn.dev/aria3/articles/claude-code-dynamic-workflows-6-patterns)
をそのままカタログ化したもの、**`map-reduce`** は kiro-flow が P2 で追加した 7 つ目の
**正規の選択可能パターン**（後述 7.3）。

| パターン | 形（ノード kind） | 使いどころ |
|---------|------------------|-----------|
| **classify-and-act** | `classify` → 結果で `work` を追加（ルーティング） | 種別判定して専門処理へ振り分け |
| **fan-out-and-synthesize** | 並列 `work`/`generate` ×N → `synthesize` | 分割して並列処理し統合 |
| **adversarial-verification** | `generate` → `verify`（fail なら作り直し） | 成果を批判的に検証 |
| **generate-and-filter** | `generate` ×N → `filter` | 候補を多数出して絞り込み |
| **tournament** | `generate` ×N → `judge` | 複数案から最良を選ぶ |
| **loop-until-done** | `work` → `verify`（条件達成まで反復） | テスト通過・品質達成まで繰り返す |
| **map-reduce**（kiro-flow 追加） | `split` → 実行時に `map` ×N を動的展開 → `reduce` | 件数を事前に固定せずデータ駆動で並列処理し集約 |

ノード kind: `work`(通常) / `generate`(候補生成) / `classify`(分類) / `synthesize`(統合) /
`verify`(検証) / `filter`(絞り込み) / `judge`(最良選択) / `reduce`(構造化データの集約) /
`split`(リスト化＝データ駆動 fan-out の起点) / `map`(要素ごとの処理)。
worker は kind 別のプロンプトで実行する。

> **「7 つ目のパターン」か「内部的な仕組み」か**: `map-reduce` は `PATTERNS` カタログに載る
> **正規の選択可能パターン**（planner=kiro はカタログから選び、planner=stub はキーワードで検出）であり、
> その点で他の 6 パターンと同格。一方、`split` 完了後に `map`/`reduce` ノードを**実行時に動的生成**する
> `_expand_splits` の挙動はパターンの**実行（継続）メカニズム**であって、別個のパターンではない
> （classify-and-act の継続ルーティングや adversarial-verification の作り直しと同じ層）。

### 7.2 構造化成果（structured results, P1）

Claude Dynamic Workflows の「エージェント間を構造化データが流れる」特徴の取り込み。

- 各 `results/<id>.json` はテキスト `output` に加え、任意の **`data`（JSON）** を持てる（無ければ後方互換）。
- worker は依存の**完全な result dict（output＋data）**を実行へ渡す。kiro executor は出力を寛容パースして
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
  kiro planner も kind に `split`/`reduce` を選べる。`_expand_splits` は stub/kiro 両方の継続で機械的に走る。
### 7.4 複合パターン・統合前 gate・グラフ健全性検査（P3）

- **統合前の事前チェック / 敵対的レビュー（`--review`、config `review`）**: 統合（synthesize/reduce）の
  直前に `verify` gate を挟む。fan-out では `gens → gate(verify) → synth`、map-reduce では
  `map → gate → reduce`。gate が fail なら既存の verify-loop が依存を作り直して再検証（`replaces` で
  後続を付け替え）。adversarial-verification を他パターンに複合した形。
- **複合パターン**: strategy の `patterns` は複数を持てる。kiro planner は多段グラフへ複合できる
  （例: classify-and-act の各分岐を fan-out-and-synthesize に / generate-and-filter の通過案で tournament）。
  stub は `--review` による gate 複合を提供。review 時、統合ノード（synthesize/reduce）は「成果＋gate」に
  依存し、集約時は gate の判定（`{"ok":...}`）を除いて実際の成果だけを畳み込む。
- **グラフ健全性検査（`_sanitize_graph`）**: 計画時・再計画時に、未知の依存 ID と自己ループを除去し、
  Kahn 法で到達不能（循環）ノードの残依存を断ち切る。planner（kiro）の誤出力や継続での混入に対する防御。

### 7.5 planner 出力の正規化（P4）

- **`_coerce_tasks`**: kiro の planner / 評価役の生出力を正規化する共通処理。id 重複除去・既存 id 回避・
  **不正 kind の `work` 丸め**（有効 kind は `VALID_KINDS`）・deps の文字列化を行い、`plan_strategy_kiro` と
  `continue_kiro` の両方で使う。これに `_sanitize_graph` が重なり、LLM 出力の崩れに二段で耐える。
- **stub 擬似実行時間の調整**: `execute_stub` のスリープ（既定 1〜5 秒）は環境変数
  `KIRO_FLOW_STUB_SLEEP_MAX` で変更可能（テストでは `0` にして高速化、約 3 秒で全テスト完走）。

### 7.1 パターン・並列数の選択

| 項目 | `--planner kiro` | `--planner stub` |
|------|------------------|------------------|
| パターン選択 | kiro-cli にカタログ付きプロンプトで選ばせる | 要求のキーワードで判定 |
| 並列数 | kiro-cli が決める | 要求中の `xN` / `並列N`、無ければタスク数（2〜6） |
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

`--max-iterations`（既定 3）で再計画の暴走を防ぐ。

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
  取り、既に稼働中なら何もせず終了する。`kiro-flow daemon` の重複呼び出しは安全（多重起動しない）。
- 分散は各 PC で `kiro-flow --git <repo> daemon` を動かすだけ。要求はどの PC から `submit` してもよい。

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
`--planner` / `--executor`（`kiro` | `stub`）、`--max-iterations`、`--poll`、`--workers`。

インストール: `bash tools/kiro-flow/install.sh` で `~/.local/bin/kiro-flow` に導入（標準ライブラリのみ、
pip 依存なし。git は分散用、kiro-cli は実運用用で無くても stub で動く）。

`status` は公式 Dynamic Workflows 風のダッシュボード（進捗バー・エージェント状態ツリー（依存深さで字下げ）・
直近アクティビティ・最終結果）を表示する。`--follow` でライブ監視、`--list` で run 一覧。

Claude Code スキル `.github/skills/kiro-flow/` がこの CLI の呼び出し（run/submit/daemon/status/gc の
使い分け・要求の書き方）を案内する。

### 11.1 設定ファイル（環境依存値の外部化）

環境ごとに決まる値を設定ファイルへ外出しできる（kiro-loop と同じ流儀）。

- **探索順序（フォールバック）**: `--config <path>` → `./.kiro/` → `~/.kiro/` の
  `kiro-flow.{yaml,yml,json}`。
- **形式**: PyYAML があれば YAML、無ければ JSON（同じキー。PyYAML は任意）。
- **優先順位**: CLI 引数 > 設定ファイル > 組み込み既定（`CONFIG_DEFAULTS`）。
- **実装**: 設定対象オプションの argparse 既定を `None` にし、parse 後に `resolve_config(args)` が
  「CLI 未指定（None）の値だけ」を設定ファイル→既定で埋める。`--model_opt ""`（子プロセスが渡す
  「モデル指定なし」）は resolve 後に `None` へ正規化するため、設定ファイルの `model` が子へ漏れない。
- **キー**: `bus` / `git` / `git_branch` / `git_subdir` / `planner` / `executor` / `model` /
  `max_workers` / `workers` / `max_iterations` / `poll` / `lease`。
- 子プロセス（orchestrate/work）へはこれらを**明示フラグ**で渡すため、子側 resolve は同じ値を保ち整合する。

サンプル: `tools/kiro-flow/kiro-flow.yaml.example`。

---

## 12. 整合性・障害対応

| 懸念 | 対処 |
|------|------|
| 二重実行 | 名前空間付き claim ＋ 決定的タイブレークで勝者は 1 人 |
| push 衝突 | 書き込み所有権の分割（disjoint）＋ `pull --rebase` リトライ |
| 孤児タスク（ワーカー死） | lease 期限切れで `_winner` が無視 → 再 claim 可能 |
| 長時間タスクの横取り | Heartbeat が lease を延長 |
| 失敗依存によるデッドロック | 静止判定 ＋ `replaces` による依存付け替え |
| 無限再計画 | `--max-iterations` |
| 空ディレクトリ未追跡（git） | 書き込み時に親ディレクトリを自動生成 |
| run の蓄積 | `gc` で掃除（git バスは git rm＋push） |

---

## 13. テスト

`tools/kiro-flow/tests/test_kiro_flow.py`（kiro-cli 不要・標準ライブラリのみ）。

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
python3 tools/kiro-flow/tests/test_kiro_flow.py
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
| M6 | `install.sh` で `kiro-flow` コマンド化・サブコマンド整理（`run`/`status --follow`） |
| M7 | 6 ワークフローパターン（記事準拠）の戦略選択・ノード kind・パターン別継続 |
| P1–P4 | 構造化成果＋reduce / 7 つ目のパターン map-reduce（データ駆動 fan-out: split→map→reduce）/ 複合パターン＋統合前 gate＋健全性検査 / planner 正規化 |

---

## 16. ADR: ワークフロースクリプトの動的生成は当面採用しない

- **ステータス**: 採用（2026-06-13）。当面コードハーネス生成は導入しない。
- **文脈**: 公式 Claude Dynamic Workflows は、Claude が**タスク専用ハーネス（コード）**を生成し、
  サブエージェントの spawn と JS（Math/JSON/Array）でのデータ加工を**実行**することで動的な
  オーケストレーションを実現する。kiro-flow は宣言的タスクグラフ＋継続ルール＋データ駆動 fan-out で
  同等の動的性を、コードを実行せずに表現している。

- **決定**: LLM 生成コードの実行（コードハーネス）は**現時点では採用しない**。表現力が不足する場面が出たら、
  まず**宣言的語彙の拡張**（条件付きエッジ、reduce/transform 演算の指定、新ノード kind 等）で対応する。

- **理由**:
  1. **セキュリティ**: LLM 生成コードの実行＝任意コード実行。分散＋git バス文脈では、ハーネスがバス
     リポジトリへ push・情報持ち出し・無制限 spawn を行いうる。安全に運ぶにはプロセス分離・資源/時間
     上限・FS/ネットワーク遮断のサンドボックスが必須で、コストとリスクが大きい。
  2. **分散との不整合**: kiro-flow の核は「git バス＋claim による分散実行」。プロセス内 spawn 型の
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
