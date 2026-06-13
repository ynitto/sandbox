# kiro-flow

kiro-cli で **Claude 風の Dynamic Workflow**（動的にタスクを分解 → ワーカーへ委譲 → 結果統合）を
実現する基盤。通信は **ファイルのみ**で行い、バスを git に差し替えれば**複数 PC へ分散**できる設計。

> **現状: M7（6 パターン戦略）**
> orchestrator が [Claude Dynamic Workflows の 6 パターン](https://zenn.dev/aria3/articles/claude-code-dynamic-workflows-6-patterns)
> をカタログとして持ち、**要求に応じてパターンの組み合わせと並列数を選んで**タスクグラフを形作る。

## できること

- **6 つのワークフローパターン**を orchestrator が知っていて、**要求からパターンと並列数（fan-out 幅）を
  自動選択**してグラフを形作る（下記）。kiro 評価役はパターンを踏まえて継続（ルーティング/再生成/統合）を判断。
- **`daemon`**：常駐し、投入された要求を拾って orchestrator を起動、claim 可能タスク量に応じて
  **ワーカーをオンデマンド起動**（仕事が無くなれば自然終了）。**分散時は各 PC でデーモンを動かす**だけ。
- **`submit`**：要求を inbox に投入。デーモンが拾う（要求は claim で 1 台だけが orchestrate を担当）。
- **`run`**：単発実行。**既存 run-id なら再開、無ければ新規**と状態で自動判断（旧 `up`/`resume` を統合）。
- 要求をタスクに分解し、**依存関係を尊重**しつつ複数ワーカーが**競合せず** claim して並列実行。
- **動的な再計画**：全タスク完了後に結果を評価し、不足があればタスクを追加して反復（最大 `--max-iterations`）。
- **`--git` で複数 PC 分散**：各ノードが共有リポジトリの自分専用クローンで作業し、push/pull で通信。
- **再開**：`run --run-id <id>` で中断した run を再開（計画はやり直さず未完タスクから継続）。
- **lease ハートビート**：実行中はリースを延長し続け、長時間タスクでも他ノードに横取りされない。
- **`status`**：状態を 1 回表示。`--follow` でライブ監視（tmux ペインに置けば監視ダッシュボード）。
- **`gc`**：古い・完了済みの run をバスから削除（git バスでは git rm＋push）。
- LLM は **kiro-cli** がデフォルト。kiro-cli 無しでも動く **stub** モードでプロトコル検証可能。

## デーモン構成（オンデマンド起動）

```
submit "要求" ─▶ inbox/<id>.json
                     │  （要求を claim：分散時は 1 台のデーモンだけが担当）
  ┌──────────────────▼───────────────────────────────────────────┐
  │ daemon（各 PC で常駐）                                          │
  │   1) inbox を監視 → 新要求を claim → orchestrator をオンデマンド起動 │
  │   2) バス上の claim 可能タスク数を見て worker をオンデマンド起動      │
  │      （max-workers 上限・短命/ idle-exit で仕事が尽きたら終了）       │
  └──────────────────────────────────────────────────────────────┘
        分散時: 共有 git バスを複数デーモンが見て各自 worker を湧かせる
```

## 6 つのワークフローパターン

orchestrator は要求を見て、以下の 6 パターン（[参考記事](https://zenn.dev/aria3/articles/claude-code-dynamic-workflows-6-patterns)）
から組み合わせと並列数を選び、各ノードに **kind** を付けたタスクグラフを生成する。

| パターン | 形（ノード kind） | 使いどころ |
|---------|------------------|-----------|
| **classify-and-act** | `classify` → 結果で `work` を追加（ルーティング） | 種別判定して専門処理へ振り分け |
| **fan-out-and-synthesize** | 並列 `work`/`generate` ×N → `synthesize` | 分割して並列処理し統合 |
| **adversarial-verification** | `generate` → `verify`（fail なら作り直し） | 成果を批判的に検証 |
| **generate-and-filter** | `generate` ×N → `filter` | 候補を多数出して絞り込み |
| **tournament** | `generate` ×N → `judge` | 複数案から最良を選ぶ |
| **loop-until-done** | `work` → `verify`（条件を満たすまで反復） | テスト通過・品質達成まで繰り返す |

- **パターン選択**: `--planner kiro` なら kiro-cli が選ぶ。`--planner stub` は要求のキーワードで判定
  （「分類/振り分け」→classify、「tournament/最良」→tournament、「候補/フィルタ」→filter、
  「検証/レビュー」→adversarial、「繰り返し/通るまで」→loop、それ以外→fan-out）。
- **並列数**: 要求中の `xN` / `並列N` を拾う。無ければ並列タスク数から既定（2〜6）。
- **継続判断**: 静止（claim 可能・実行中タスクが無い）するたびに評価し、`classify` 結果でルーティング、
  `verify` が fail なら依存を作り直して再検証（`replaces` で後続の依存を付け替え）、失敗タスクは retry。
- 選んだ戦略は `graph.json` / `final.json` に記録され、`status` でも表示される。

## 動的ワークフロー（evaluator-optimizer ループ）

```
要求 → [パターン選択+分解] → タスク投入 → ワーカーが claim/実行 → 静止
                  ▲                                      │
                  │                                      ▼
            タスク追加 ◀── replan ── [評価] done? ──→ 統合(final.json)
                          （最大 max-iterations 回）
```

orchestrator は run が静止するたびに結果を評価し、`done` なら統合、`replan` ならパターンに応じた
タスクをグラフへ追加して継続する（最大 `--max-iterations`）。stub では `FAIL` を含むゴールは失敗 →
retry、`FLAKY` を含むゴールは検証で issue 扱い → 作り直しが走るので、ループ動作を確認できる。
kiro 評価役は 6 パターンのカタログ付きプロンプトで `{"decision","reason","new_tasks"}` を出力させる。

## 設計の肝 — 衝突しない通信

タスクの状態は**ファイルの存在**から導出するため、ノードが同じファイルを書き換えることがない。

| 状態 | 条件 |
|------|------|
| pending | `tasks/<id>.json` があり、有効な claim も `results/<id>.json` も無い |
| claimed | `claims/<id>/` に lease 内の claim があり、勝者が確定している |
| done / failed | `results/<id>.json` があり `status` がそれ |

**claim — 名前空間付き claim ＋ 決定的タイブレーク**：各ワーカーは自分専用の
`claims/<id>/<who>.json` を書く（ファイル名が衝突しないので git で add/add コンフリクトに
ならない）。勝者は lease 内の全 claim のうち **`(ts, who)` が最小**の 1 件に決定的に定まる。
ローカル転送でも git 転送でも、同じロジックで唯一の勝者が決まる。クラッシュ等で放置された
claim は lease 超過で自動的に無効化され、別ノードが再 claim できる。

```
<bus>/inbox/<req-id>.json          # 投入された要求（submit が書く）
<bus>/inbox/claims/<req-id>/<who>.json  # 要求の取得マーカー（どのデーモンが担当か）
<bus>/runs/<run-id>/
  meta.json            # 要求・status（planning/running/done）
  graph.json           # タスクグラフ（orchestrator のみ書く）
  tasks/<id>.json      # タスク仕様
  claims/<id>/<who>.json  # 取得マーカー（ノードごとに名前空間化）
  results/<id>.json    # 成果（claim 成功者のみ書く）
  events/<who>.jsonl   # 追記専用ログ（各ノードが自分のファイルだけ）
  final.json           # 統合結果
```

## インストール

```bash
bash tools/kiro-flow/install.sh          # ~/.local/bin/kiro-flow にインストール
bash tools/kiro-flow/install.sh --prefix /usr/local/bin   # 任意の場所へ
```

標準ライブラリのみで動作（pip 依存なし）。git は分散モードで必要、kiro-cli は実運用で必要
（無くても `--planner stub --executor stub` で動作確認できる）。以降の例は `kiro-flow`
コマンド前提（未インストールなら `python3 tools/kiro-flow/kiro-flow.py` で代用可）。

## 設定ファイル

環境ごとに決まる値（バス、git リポジトリ、planner/executor、`max_workers`、`poll`、`lease` 等）は
設定ファイルに書ける。**優先順位は CLI 引数 > 設定ファイル > 組み込み既定**。

```bash
cp tools/kiro-flow/kiro-flow.yaml.example ~/.kiro/kiro-flow.yaml   # 編集して配置
```

検索順序（フォールバック、kiro-loop と同じ流儀）:

1. `--config <path>` で明示指定
2. カレントディレクトリの `.kiro/kiro-flow.{yaml,yml,json}`
3. `~/.kiro/kiro-flow.{yaml,yml,json}`

YAML を使うには PyYAML が必要（任意）。無い環境では **JSON**（`kiro-flow.json`、同じキー）でよい。
設定できるキー: `bus` / `git` / `git_branch` / `planner` / `executor` / `model` /
`max_workers` / `workers` / `max_iterations` / `poll` / `lease`。例は
[`kiro-flow.yaml.example`](kiro-flow.yaml.example) を参照。

## 使い方

### デーモン（推奨・オンデマンド起動）

```bash
# 1) デーモンを常駐起動（このマシンのワーカー上限は --max-workers）
kiro-flow --bus /tmp/flowbus daemon --max-workers 4 &

# 2) 要求を投入（run-id が標準出力に返る）。デーモンが拾って自動実行する
RID=$(kiro-flow --bus /tmp/flowbus submit "要件整理; API設計; テスト")
kiro-flow --bus /tmp/flowbus --run-id "$RID" status --follow --until-done

# 分散: 各 PC で同じ --git を指すデーモンを起動するだけ。要求はどの PC から submit してもよい
kiro-flow --git git@example.com:team/flow-bus.git daemon --max-workers 4 &   # PC ごとに
kiro-flow --git git@example.com:team/flow-bus.git submit "<要求>"
```

### ワンショット（単発実行・既存 run-id なら自動で再開）

```bash
# kiro-cli 無しでプロトコルを確認（まずこれ）
kiro-flow --bus /tmp/flowbus run \
  "要件を整理する; APIを設計する; テストを書く; READMEを書く" \
  --workers 3 --planner stub --executor stub --poll 0.5

# kiro-cli を使った実運用（既定）
kiro-flow run "<要求>" --workers 3

# 中断した run を再開（要求は省略。状態を見て自動的に未完タスクから続行）
kiro-flow --bus /tmp/flowbus --run-id <run-id> run

# 依存関係つきの分解（stub）: ';' は並列、'->' は逐次依存チェーン
kiro-flow run "setup -> build -> test; write docs" --planner stub --executor stub

# 複数 PC 分散（共有 git リポジトリをバスにする）
kiro-flow --git git@example.com:team/flow-bus.git run "<要求>" --workers 3
#   ローカルのベアリポジトリで動作確認:
#     git init --bare -b main /tmp/flowbus.git
#     kiro-flow --git /tmp/flowbus.git run "A; B; C" --planner stub --executor stub

# 状態確認 / ライブ監視 / 掃除
kiro-flow --bus /tmp/flowbus --run-id <run-id> status            # 1 回だけ表示
kiro-flow --bus /tmp/flowbus --run-id <run-id> status --follow   # ライブ監視
kiro-flow --bus /tmp/flowbus gc --older-than 7 --keep 5 --status done --dry-run
```

### tmux で「実行 ＋ 監視」を一画面に

```bash
RID=run-XXXX
tmux new-session -d -s flow "kiro-flow --run-id $RID run '<要求>' --workers 3"
tmux split-window -h "kiro-flow --run-id $RID status --follow --until-done"
tmux attach -t flow
```

### サブコマンド

| コマンド | 役割 |
|---------|------|
| `daemon` | 常駐し要求に応じて orchestrator/worker をオンデマンド起動（`--max-workers`） |
| `submit <要求>` | 要求を inbox に投入（run-id を返す）。デーモンが拾う |
| `run [要求]` | 単発実行。**既存 --run-id なら再開、無ければ新規**（状態で自動判断） |
| `status` | 状態表示。既定 1 回 / `--follow` でライブ監視（`--until-done` で自動終了） |
| `gc` | 古い run を削除（`--older-than` 日 / `--keep` 件 / `--status` / `--dry-run`） |
| `orchestrate` / `work` | 計画役・ワーカー役の内部コマンド（`run`/`daemon` が起動する） |

### 主なオプション

| オプション | 既定 | 意味 |
|-----------|------|------|
| `--bus` | `./.kiro-flow` | ローカルバスのルート / git モードでは各ノードのクローン親 |
| `--git` | （なし） | 共有 git リポジトリ URL/パス。指定で複数 PC 分散モード |
| `--git-branch` | `main` | バスに使う git ブランチ |
| `--lease` | 1800 | claim のリース秒数（実行中はハートビートが延長） |
| `--workers` | 2 | 起動するワーカー数（`run`） |
| `--max-workers` | 4 | デーモンが同時に走らせる worker 上限（`daemon`） |
| `--planner` / `--executor` | `kiro` | `kiro`（kiro-cli）/ `stub`（オフライン検証）。executor は評価役にも使う |
| `--max-iterations` | 3 | 再計画（evaluator-optimizer）の最大反復回数 |
| `--poll` | 2.0 | ポーリング間隔（秒） |
| `--keep-alive` / `--idle-exit` | off | run 完了後も待機 / claim 可能タスクが尽きたら終了（`work`） |

## 依存

- Python 3.9+（標準ライブラリのみ）
- git モードでは `git` コマンド（共有リポジトリは初期化済みであること）
- 実運用では `kiro-cli`（`--planner kiro` / `--executor kiro`）

## テスト

kiro-cli 不要（stub のみ）。プロトコル・障害注入・依存分解・再計画・end-to-end を検証する。

```bash
python3 tools/kiro-flow/tests/test_kiro_flow.py
# または: python3 -m unittest discover -s tools/kiro-flow/tests
```

主なケース: 決定的タイブレーク、**lease 切れ claim の回収（死んだワーカー）**、
**同時 claim でも勝者は 1 人**、逐次依存の分解、失敗 → 再計画 → retry 成功（end-to-end）、
**要求 claim でデーモンが 1 台に決まる**・`run_claimable_count` の依存考慮、
**6 パターン検出・並列数抽出・fan-out/tournament のグラフ形・classify ルーティング・verify fail の作り直し**。

## ロードマップ

- **M1**: ローカルバス・claim プロトコル・一発起動。✅
- **M2**: git バスで複数 PC 分散。名前空間付き claim ＋ 決定的タイブレーク、
  push 競合の rebase リトライ、lease による孤児 claim の自動回収。✅
- **M3**: 結果評価に基づく**再計画ループ**（evaluator-optimizer）・`resume`（中断再開）・
  lease ハートビート（長時間タスクの claim 更新）・負荷分散の位相ずらし。✅
- **M4**: 依存付き分解（`;` 並列 / `->` 逐次）・ライブ可視化 `watch`・
  `gc`（古い run 掃除）・障害注入を含むテストスイート。✅
- **M5**: **常駐デーモン**による orchestrator/worker のオンデマンド起動・
  `submit`/inbox 要求キュー・要求 claim によるデーモン選出。✅
- **M6**: `install.sh` で `kiro-flow` コマンド化・サブコマンド整理
  （`up`+`resume`→`run`、`status`+`watch`→`status --follow`）。✅
- **M7（本実装）**: orchestrator が **Claude Dynamic Workflows の 6 パターン**を持ち、要求から
  パターンの組み合わせと並列数を選択。ノード kind とパターン別継続（ルーティング/再生成/統合）。✅
- **今後**: 公平な負荷分散（work-stealing）・成果物の大容量対応（git-lfs）。

## 既知の制限

- **負荷分散は heuristic**: 起動位相ずらし＋タスク後ジッタで緩和するが、ウォームなノードが
  連続 claim しやすい傾向は残る（瞬時に終わる stub では顕著）。実 kiro-cli はタスク実行に
  時間がかかり遅延も入るため自然に分散する。厳密な公平分配は今後の課題。
- **ハートビート間隔の下限**: `max(2 秒, lease/3)`。極端に短い lease では下限が効くため、
  lease は実タスク時間に対して十分大きく設定すること。
- **inbox の蓄積**: `submit` した要求ファイルは残る（run 作成済みなら再処理はされない）。
  デーモンは run 終了を別途検知しない設計なので、不要な run は `gc` で掃除する。

## 既存ツールとの関係

| ツール | 構造 | 決定タイミング |
|--------|------|--------------|
| `kiro-loop` | 定期プロンプト送信 | 静的 |
| `multi-agent-shogun-kiro` | 将軍/家老/足軽の固定階層 | 静的 |
| **`kiro-flow`** | **タスクグラフ** | **実行時に LLM が生成** |

`git-file-sync`（git をハブにした同期）と `gitlab-idd`（キューからの claim→実行→報告）の発想を、
タスクグラフの動的生成に組み合わせたもの。
