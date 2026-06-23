# kiro-flow

kiro-cli で **Claude 風の Dynamic Workflow**（動的にタスクを分解 → ワーカーへ委譲 → 結果統合）を
実現する基盤。通信は **ファイルのみ**で行い、バスを git に差し替えれば**複数 PC へ分散**できる設計。

> **現状: M7＋P1–P4（7 パターン戦略）**
> orchestrator が [Claude Dynamic Workflows の 6 パターン](https://zenn.dev/aria3/articles/claude-code-dynamic-workflows-6-patterns)
> ＋ kiro-flow 追加の `map-reduce`（計 7）をカタログとして持ち、**要求に応じてパターンの組み合わせと
> 並列数を選んで**タスクグラフを形作る。

## できること

- **7 つのワークフローパターン**を orchestrator が知っていて、**要求からパターンと並列数（fan-out 幅）を
  自動選択**してグラフを形作る（下記）。kiro 評価役はパターンを踏まえて継続（ルーティング/再生成/統合）を判断。
- **`daemon`**：常駐し、投入された要求を拾って orchestrator を起動、claim 可能タスク量に応じて
  **ワーカーをオンデマンド起動**（仕事が無くなれば自然終了）。**分散時は各 PC でデーモンを動かす**だけ。
  起動は**冪等**——同一バスのデーモンが既に稼働していれば二重起動せず終了する。冪等判定のロックは
  バスを `realpath` で正規化したキーで `$TMPDIR/kiro-flow-locks/`（設定 `lock_dir` / `--lock-dir` で変更可）に
  置き、PID を記録する。これにより symlink 経由や別 cwd で起動した外部デーモンも、別ツール（kiro-autonomous 等）
  から同一デーモンとして発見できる（`flock` 不能環境では PID 生存で判定）。
- **`status`**：公式 Dynamic Workflows 風のダッシュボード（進捗バー・エージェント状態ツリー・
  アクティビティ・最終結果）。`--follow` でライブ監視。
- **`result`**：完了した run の**最終結果を探し出して提示**。集約／末端（sink）ノードの全文出力を
  自動特定して返す（`status` の要約に対し全文）。run-id 省略で最新を選択、`--json` で機械可読。
- **`submit`**：要求を inbox に投入。デーモンが拾う（要求は claim で 1 台だけが orchestrate を担当）。
- **`run`**：単発実行。**既存 run-id なら再開、無ければ新規**と状態で自動判断（旧 `up`/`resume` を統合）。
- 要求をタスクに分解し、**依存関係を尊重**しつつ複数ワーカーが**競合せず** claim して並列実行。
- **動的な再計画**：全タスク完了後に結果を評価し、不足があればタスクを追加して反復（最大 `--max-iterations`）。
  達成不可能な完了条件で無限に再タスクを積まないよう、同一系統の作り直しは **サーキットブレーカー**（`--max-retries`、既定 3）で打ち切る。
- **中間成果物のファイル参照プロトコル**：`output`/`data`（JSON）に乗らない大きな成果物は、ノードごとの決定的ディレクトリ
  `artifacts/<id>/` に書き出す。後続タスクは**依存ノードの同じパス**を読んで成果物を発見でき（中身は本文に貼らずパス参照）、
  依存成果物が大きくても kiro-cli 起動時のコマンドライン長制限（ARG_MAX）に達しない（超過分は一時ファイルへ退避）。
- **`--git` で複数 PC 分散**：各ノードが共有リポジトリの自分専用クローンで作業し、push/pull で通信。
  `--git-subdir` でリポジトリ内のサブディレクトリをバスにでき（既存リポジトリの間借り）、clone/pull/push は
  **sparse checkout** でそのサブツリーだけを展開する（無関係なファイルを取得しない）。
- **`--repo` で成果物リポジトリを分離作業**：`run`/`submit` に `--repo <url>`（複数可・設定 `repos` と同義）を渡すと、
  その run の **worker が各リポジトリを temp 領域へ clone してから作業し、clone パスをエージェントへ渡す**（「ここで作業して
  push せよ。他の場所は編集しない」）。**作業後（worker 終了時）に clone を必ず削除**。push が必要・中身を読む必要がある
  タスク用で、orchestrator の作業ツリーを汚さない。repos は run の bus メタに載るため local/daemon/remote で同一に働く。
  `--repo` 値は素の URL でも、構造化 JSON（`{url,name,path,base,target,readonly,desc}`）でも受ける。**JSON なら worker は
  `base` ブランチを checkout して clone し、`repo_instruction` で「フォルダ(path)配下のみ変更・push 先=target・参照のみ
  (readonly)は push しない」を出し分ける**。この指示は gitlab executor 経由でイシュー本文にもそのまま載る
  （kiro-autonomous の charter `## repos` がこの JSON をタスク単位で組み立てて渡す）。
- **分解の粒度（`granularity` / `--granularity`）**：タスク分解の細かさを設定ファイルで調整できる。`coarse`（現状）/
  `fine`（1段細かい）/ `finest`（2段細かい・**既定**）の3段。細かいほどプランナーへ「原子的に分解せよ」と指示し、
  並列ノード数を 1/2/3 倍にスケールする（上限 16・全 planner 共通／flow-planner にも `--granularity` で伝搬）。
  要求に `x3`・`並列3` の明示があればそれを尊重し倍率を効かせない。kiro-autonomous から呼ぶ場合も `kiro-flow.yaml` の
  `granularity` がそのまま効く。
- **見本先行分解（`exemplar_first` / `--exemplar-first`）**：map-reduce の fan-out を「1件先行 → 自動検証ゲート →
  残り展開」にする（既定 off）。split 完了直後は **先頭1件(pilot map)とその verify ゲートだけ**を出し、ゲート通過後に
  残りの map（pilot に依存＝見本を範に取る）と reduce を展開する。同様手順の繰り返しを 1 件で固めてから一気に流せる。
  **人のフィードバックを介す版**は kiro-autonomous の cohort が担う（こちらは agent 自動版）。
  選択肢としての when_to_use / when_not_to_use / 例示 / 適用具体例は flow-planner カタログの
  `variants.pilot-then-batch`（`.github/skills/flow-planner/patterns-catalog.yaml`）にまとめてある。
- **再開**：`run --run-id <id>` で中断した run を再開（計画はやり直さず未完タスクから継続）。
- **lease ハートビート**：実行中はリースを延長し続け、長時間タスクでも他ノードに横取りされない。
- **`status`**：状態を 1 回表示。`--follow` でライブ監視（tmux ペインに置けば監視ダッシュボード）。
- **`gc`**：古い・完了済みの run をバスから削除（git バスでは git rm＋push）。対応する inbox 要求と
  claim も併せて消す（残すとデーモンが完了済み要求を拾い直して再実行してしまうため）。
- **一時ファイルの自動掃除**：`daemon` が `cleanup_interval` ごとに、バス外に溜まる一時ファイルを掃除する
  — 未使用ロック（`$TMPDIR/kiro-flow-locks/`）・`*.tmp.<pid>` 中間ファイル残骸・孤立した git クローン。
  保持中のロックや稼働中クローンは消さない。`--no-cleanup` で無効化。
- **作業後にクローンを削除**：`--git` 利用時、各コマンド（`run`/`work`/`orchestrate`/`daemon` など）が作る
  sparse-checkout クローンを、作業後（プロセス終了時）に丸ごと削除する（既定の挙動）。
  push 済みデータは共有リポジトリ側にあるため失われない。`--keep-clone` で残して次回再利用（再クローン回避）。
- LLM は **kiro-cli** がデフォルト。kiro-cli 無しでも動く **stub** モードでプロトコル検証可能。
- **gitlab ワーカーバス（opt-in）**：`--executor gitlab` で各タスクを GitLab イシューに委譲し、
  リモートのワーカーに実装させる（下記）。

## ワーカーバス（executor）— プラグイン方式

ワーカーがタスクを実際に実行するバックエンド。`--executor`（または設定 `executor`）で選ぶ。
組み込みの `kiro` / `stub` に加え、**kiro-loop の hooks と同じ流儀でプラグイン化**されている。

| executor | 実行のしかた | 用途 |
|----------|-------------|------|
| `kiro`（既定） | ローカルで `kiro-cli` を呼ぶ（組み込み） | 通常運用 |
| `stub` | LLM を呼ばず擬似実行（組み込み） | kiro-cli 無しのプロトコル検証 |
| `gitlab`（opt-in / プラグイン） | タスクを GitLab イシュー化し委譲、`status:approved` まで待つ | リモート/他者への作業委譲 |
| `<名前>` / `<.py パス>` | 任意の executor プラグイン | 独自バックエンドの追加 |

### executor プラグイン

`--executor` には次のいずれかを指定できます:

- 組み込み名 `kiro` / `stub`
- **プラグイン名**（例 `gitlab`）— 検索ディレクトリの `executors/<name>.py` を解決
- **`.py` への明示パス** — そのファイルをプラグインとしてロード

プラグインは標準ライブラリのみで書ける単一ファイルで、次の関数を公開します（kiro-loop の
`event_hook` の `check()` に相当）。本体が `importlib` で動的ロードし（mtime キャッシュ付き）、
ワーカーが各タスクで呼び出します。

```python
def execute(kind, goal, dep_results, model=None, art_dir=None, dep_arts=None):
    ...  # (text, data) を返す
```

- **検索順**：`<スクリプトと同階層>/executors/` → リポジトリの `tools/kiro-flow/executors/` →
  `~/.kiro/kiro-flow/executors/`（旧インストーラ配置・後方互換）→ 設定 `executor_dir`（`--executor-dir`）。
- **プラグイン設定**：同名のトップレベル設定ブロック（例 `gitlab:`）を JSON 化し、環境変数
  `KIRO_FLOW_EXECUTOR_CONFIG` でプラグインへ渡します。プラグインは個別の環境変数で上書きも可能。
- **インストール**：`install.sh` が同梱プラグインを **本体と同じフォルダ**（`<install-prefix>/executors/`、
  既定 `~/.local/bin/executors/`）へコピーするため、単一ファイル配布後も `--executor <name>` が検索順 #1
  「スクリプト同階層の `executors/`」で名前解決できます（kiro-loop と同じ「本体隣」の補助アセット配置）。

### gitlab ワーカーバス（同梱プラグイン）

`--executor gitlab` を選ぶと、各ワーカータスクを [gitlab-idd](../../.github/skills/gitlab-idd/) スキルの
`gl.py` で **GitLab イシュー** にして起票する。リモートの（別マシン・別人の）ワーカーがイシューを拾って
実装し、レビュアーが受け入れ条件を満たすと `status:approved` を付与する。kiro-flow はイシューを
`get-issue` で**ポーリング**し、`status:approved`（または `status:done` / クローズ）に達したら
そのタスクを完了とみなす。ローカルに kiro-cli が無くても、GitLab 越しに作業を委譲できる。

```
worker タスク ──▶ gl.py create-issue（status:open,assignee:any ＋ priority）
                     │
       （リモートのワーカーが拾って実装 → レビュアーが承認）
                     │
   gl.py get-issue で承認ラベルをポーリング ──▶ status:approved ⇒ 完了
```

- **opt-in**：既定の executor は `kiro` のまま。`--executor gitlab`（または設定 `executor: gitlab`）で
  明示的に選んだときだけ有効になる。`gitlab-idd` スキル未導入や接続未設定なら起票時に明確に失敗する。
- **前提**：`.github/skills/gitlab-idd` が導入済みで、`connections.yaml` か `GITLAB_TOKEN` で接続設定済み。
- **再計画はローカル**：evaluator-optimizer の継続判断（再タスク生成）はオーケストレータ側で `kiro` を使う。
  GitLab へ委譲するのは**ワーカータスクの実行**だけ。
- **設定**：ポーリング間隔・タイムアウト・付与ラベルは設定ファイルの `gitlab:` ブロックで調整する
  （[`kiro-flow.yaml.example`](kiro-flow.yaml.example) 参照）。`timeout` を `0` にすると無限待ち。
- **委譲先リポジトリ**：`gitlab:` ブロックの `repo_url` で委譲先の GitLab プロジェクト URL を明示できる。
  空の場合は `conn_label` の接続（`connections.yaml`）か、無ければ作業ディレクトリの `git remote origin`
  から解決する。手元とは別のリポジトリへ委譲したいときに指定する。

```bash
# 例: タスクを GitLab に委譲して承認まで待つ（要 gitlab-idd 接続設定）
kiro-flow --bus /tmp/flowbus run "ログイン機能を実装" --executor gitlab
```

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

## 7 つのワークフローパターン

orchestrator は要求を見て、以下の 7 パターン（最初の 6 つは
[参考記事](https://zenn.dev/aria3/articles/claude-code-dynamic-workflows-6-patterns)、
`map-reduce` は kiro-flow が追加した 7 つ目）から組み合わせと並列数を選び、各ノードに **kind** を
付けたタスクグラフを生成する。`map-reduce` も他の 6 つと同格の選択可能パターンで、`split` 完了後の
`map`/`reduce` 展開だけが実行時の動的メカニズム。

| パターン | 形（ノード kind） | 使いどころ |
|---------|------------------|-----------|
| **classify-and-act** | `classify` → 結果で `work` を追加（ルーティング） | 種別判定して専門処理へ振り分け |
| **fan-out-and-synthesize** | 並列 `work`/`generate` ×N → `synthesize` | 分割して並列処理し統合 |
| **adversarial-verification** | `generate` → `verify`（fail なら作り直し） | 成果を批判的に検証 |
| **generate-and-filter** | `generate` ×N → `filter` | 候補を多数出して絞り込み |
| **tournament** | `generate` ×N → `judge` | 複数案から最良を選ぶ |
| **loop-until-done** | `work` → `verify`（条件を満たすまで反復） | テスト通過・品質達成まで繰り返す |
| **map-reduce** | `split` → 実行時に `map` ×N を動的展開 → `reduce` | 件数を事前に固定せずデータ駆動で並列処理し集約 |

- **パターン選択**: `--planner flow-planner` なら3段パイプライン（要求分析→戦略選定→グラフ生成）で
  高精度な分解を行う（`.github/skills/flow-planner/` スキル）。`--planner kiro` なら kiro-cli が
  1回の呼び出しで選ぶ。`--planner stub` は要求のキーワードで判定
  （「分類/振り分け」→classify、「tournament/最良」→tournament、「候補/フィルタ」→filter、
  「検証/レビュー」→adversarial、「繰り返し/通るまで」→loop、それ以外→fan-out）。
- **並列数**: 要求中の `xN` / `並列N` を拾う。無ければ並列タスク数から既定（2〜6）。
- **継続判断**: 静止（claim 可能・実行中タスクが無い）するたびに評価し、`classify` 結果でルーティング、
  `verify` が fail なら依存を作り直して再検証（`replaces` で後続の依存を付け替え）、失敗タスクは retry。
- **構造化成果（structured results）**: 各ノードの結果はテキスト `output` に加え、任意の **`data`（JSON）**
  を持てる。依存先へはテキスト＋構造化データの両方を渡す。`reduce` kind は依存の `data`（リスト等）を
  畳み込んで集約する集約ノード。kiro executor は出力を寛容パースして `data` に格納する。
- **データ駆動の動的 fan-out（map-reduce）**: `split` ノードが実行時にリスト（`data`）を返すと、継続段階で
  **要素数ぶんの `map` タスクを動的展開**し（件数を事前に固定しない）、`reduce` で集約する。展開数は
  `--max-fanout`（既定 50）で上限クランプ。初期グラフは `split` のみで、`reduce` は展開時に生成するため
  先走り実行されない。
- **統合前の事前チェック / 敵対的レビュー（既定で自動）**: 公式 Claude Dynamic Workflows の
  「集約前に互いの成果を独立レビューする品質パターン」に倣い、**集約点を持つパターン
  （`map-reduce` / `fan-out-and-synthesize`）では検証 gate を既定で自動挿入**する（`review: auto`）。
  集約点を内包する `generate-and-filter` / `tournament` / `adversarial-verification` や、集約の無い
  `classify-and-act` 等には付けない（"add complexity only when it improves outcomes"）。
  gate は統合（synthesize/reduce）の前に入り、成果を鵜呑みにせず**独立に検算**し（件数・合計の整合、
  抜け漏れ・重複、要素の抜き取り検査）`{"ok": ...}` を返す。fail なら依存を作り直して再検証
  （verify-loop）。gate の判定は集約入力からは除外される。`--review` で常時有効化、`--no-review` で無効化、
  設定ファイルの `review: auto|true|false` でも制御できる。
- **グラフ健全性検査**: 計画・再計画のたびに**未知の依存 ID を除去・循環依存を断ち切る**（planner 誤出力の防御）。
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
kiro 評価役は 7 パターンのカタログ付きプロンプトで `{"decision","reason","new_tasks"}` を出力させる。

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
設定できるキー: `bus` / `git` / `git_branch` / `git_subdir` / `planner` / `executor` / `executor_dir`
（プラグイン検索ディレクトリ）/ `model` / `max_workers` / `workers` / `max_iterations` / `poll` / `lease` /
`kiro_timeout`（kiro-cli タイムアウト秒）/ `stub_sleep_max`（stub スリープ上限秒）/
`gitlab`（gitlab executor プラグイン用）。例は
[`kiro-flow.yaml.example`](kiro-flow.yaml.example) を参照。

`kiro_timeout`（kiro-cli 1 呼び出しのタイムアウト秒。既定 600、`0` で無効化）。
心拍が lease を延長し続けるため、**ハングした kiro-cli はこのタイムアウトでしか止められない**
（超過時はそのタスクを失敗扱いにして再計画の retry に回し、run 全体の停止を防ぐ）。
設定ファイルに無い場合は環境変数 `KIRO_FLOW_KIRO_TIMEOUT` → 既定 600 にフォールバックする（後方互換）。

## 使い方

### デーモン（推奨・オンデマンド起動）

```bash
# 1) デーモンを常駐起動（このマシンのワーカー上限は --max-workers）
kiro-flow --bus /tmp/flowbus daemon --max-workers 4 &
# サブコマンドを省略すると daemon として起動する（値は設定ファイル/既定から）
kiro-flow &

# 2) 要求を投入（run-id が標準出力に返る）。デーモンが拾って自動実行する
#    submit の前に daemon を確保すること（daemon は冪等なので、そのまま起動コマンドを実行してよい）
RID=$(kiro-flow --bus /tmp/flowbus submit "要件整理; API設計; テスト")
kiro-flow --bus /tmp/flowbus --run-id "$RID" status --follow --until-done

# 分散: 各 PC で同じ --git を指すデーモンを起動するだけ。要求はどの PC から submit してもよい。
# 既存リポジトリ（GitHub 等）を間借りするなら専用ブランチ（例 kiro-flow-bus）を使うと main を汚さない
kiro-flow --git git@example.com:team/repo.git --git-branch kiro-flow-bus daemon --max-workers 4 &   # PC ごとに
kiro-flow --git git@example.com:team/repo.git --git-branch kiro-flow-bus submit "<要求>"
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

# 状態確認 / 最終結果 / ライブ監視 / 掃除
kiro-flow --bus /tmp/flowbus --run-id <run-id> status            # 1 回だけ表示
kiro-flow --bus /tmp/flowbus --run-id <run-id> status --follow   # ライブ監視
kiro-flow --bus /tmp/flowbus result                              # 最終結果（run_id 省略で最新）
kiro-flow --bus /tmp/flowbus --run-id <run-id> result --json     # 機械可読な最終結果
kiro-flow --bus /tmp/flowbus gc --older-than 7 --keep 5 --status done --dry-run
```

### tmux で「実行 ＋ 監視」を一画面に

```bash
RID=run-XXXX
tmux new-session -d -s flow "kiro-flow --run-id $RID run '<要求>' --workers 3"
tmux split-window -h "kiro-flow --run-id $RID status --follow --until-done"
tmux attach -t flow
```

### 稼働診断（doctor）

```bash
kiro-flow --bus /tmp/flowbus doctor          # 診断のみ（無害・既定）
kiro-flow --bus /tmp/flowbus doctor --fix    # env/config を修正し program を gitlab-idd で起票
kiro-flow --bus /tmp/flowbus doctor --json   # 連携呼び出し用の findings を JSON で出力
```

**収集と適用を決定的に・診断と分類は kiro-cli へ委譲** して稼働の問題を洗い出し、原因を分類する。

- **env**（ユーザー環境固有）… `kiro-cli`/`git` 不在・バスに書き込めない・worker/daemon 未起動 等。
- **config**（設定）… 有限停止の無効化（`max_iterations`/`max_retries` ≤0）・`lease`/`argv_limit` 不正・バス未作成 等。
- **program**（プログラム上の不具合）… 正しい環境・設定でも再現する failed・グラフ生成や claim/再計画のロジック欠陥。**コード修正が必要なものだけ**。

材料は決定的チェック（依存コマンド・バス・有限停止設定）＋稼働シグナル（直近 run の状態・滞留・失敗ノード・
kiro-cli エラー）。これを kiro-cli に渡して分類済みの所見を得る（kiro-cli 不在・解析不能なら**決定的チェックのみ**で続行）。

`--fix` のとき env/config は既知の修正（`ensure-bus`＝バス作成）を適用、判断が要るものは提案表示のみ。
**program は `gitlab-idd` スキルで GitLab イシューを起票**（スキルが無ければ出力のみ）。終了コード `0`=健康/`1`=所見あり/`2`=未解決の critical。

`--json` の `findings` は kiro-autonomous の `doctor` と同一スキーマ。**`kiro-autonomous doctor` が `--with-flow`（既定 on）で
この `kiro-flow doctor --json` を同じバスに対して呼び、実行層の所見を統合する**（連携時は kiro-flow 側が自分の env/config 修正と
program 起票を担い、二重作業を避ける）。

### サブコマンド

| コマンド | 役割 |
|---------|------|
| `daemon` | 常駐し orchestrator/worker をオンデマンド起動（`--max-workers`）。**サブコマンド省略時の既定**・**冪等（同一バスは 1 つだけ）** |
| `submit <要求>` | 要求を inbox に投入（run-id を返す）。デーモンが拾う |
| `run [要求]` | 単発実行。**既存 --run-id なら再開、無ければ新規**（状態で自動判断） |
| `status` | ダッシュボード表示（進捗バー/エージェント状態/アクティビティ）。`--follow` ライブ / `--list` 一覧 |
| `result` | 完了した run の**最終結果**（集約／末端ノードの全文出力）を提示。run-id 省略で最新を自動選択。`--json` で機械可読 |
| `gc` | 古い run を削除（対応する inbox 要求・claim も）（`--older-than` 日 / `--keep` 件 / `--status` / `--dry-run`） |
| `doctor` | 稼働診断。run 状態/イベント/環境から問題を env/config/program に分類。`--fix` で env/config 修正・program は gitlab-idd 起票。`--json` は連携呼び出し用 |
| `orchestrate` / `work` | 計画役・ワーカー役の内部コマンド（`run`/`daemon` が起動する） |

### 主なオプション

| オプション | 既定 | 意味 |
|-----------|------|------|
| `--bus` | `./.kiro-flow` | ローカルバスのルート / git モードでは各ノードのクローン親 |
| `--git` | （なし） | 共有 git リポジトリ URL/パス。指定で複数 PC 分散モード |
| `--git-branch` | `main` | バスに使う git ブランチ |
| `--git-subdir` | （直下） | リポジトリ内でバスにするサブディレクトリ（sparse checkout 対象） |
| `--lease` | 1800 | claim のリース秒数（実行中はハートビートが延長） |
| `--workers` | 2 | 起動するワーカー数（`run`） |
| `--max-workers` | 4 | デーモンが同時に走らせる worker 上限（`daemon`） |
| `--planner` / `--executor` | `flow-planner` / `kiro` | planner は `flow-planner`（3段パイプライン、既定）/ `kiro`（kiro-cli 1回）/ `stub`（オフライン検証）。executor は評価役にも使う |
| `--max-iterations` | 3 | 再計画（evaluator-optimizer）の最大反復回数 |
| `--max-fanout` | 50 | データ駆動 fan-out（split→map）の最大展開数 |
| `--max-retries` | 3 | サーキットブレーカー：同一系統の作り直し（verify=fail 再生成・失敗 retry）の打ち切り回数。達成不可能な完了条件での無限再タスクを防ぐ |
| `--argv-limit` | 100000 | kiro-cli へ argv で渡すプロンプトの最大バイト数（設定 `argv_limit`）。超過分は一時ファイルへ退避し参照渡しにして ARG_MAX 失敗を回避 |
| `--review` / `--no-review` | auto | 検証 gate の有効化。既定 `auto`（集約パターンで自動 ON）／`--review` 常時 ON ／`--no-review` OFF。設定は `review: auto\|true\|false` |
| `--poll` | 2.0 | ポーリング間隔（秒） |
| `--cleanup-interval` / `--no-cleanup` | 3600 | 一時ファイル自動掃除の間隔（秒, `daemon`）。`--no-cleanup` または `0` で無効化 |
| `--cleanup-age` | 24 | 孤立クローンを掃除するまでのアイドル時間（時間, `daemon`） |
| `--keep-clone` | off | 作業後も sparse-checkout クローンを削除せず残す（既定は削除。設定は `cleanup_clone: true\|false`） |
| `--keep-alive` / `--idle-exit` | off | run 完了後も待機 / claim 可能タスクが尽きたら終了（`work`） |

## 依存

- Python 3.9+（標準ライブラリのみ）
- git モードでは `git` コマンド（共有リポジトリは初期化済みであること）
- 実運用では `kiro-cli`（`--planner kiro` / `--executor kiro`）

## スキル

`.github/skills/kiro-flow/` に、この CLI を呼び出すスキルを同梱。「ワークフローを実行して」「要求を投入して」
「デーモンを起動して」「run の状態を見て」などで発動し、`run`/`submit`/`daemon`/`status`/`gc` の使い分けや
要求の書き方（パターン/並列数/`--review`）を案内する。

## テスト

kiro-cli 不要（stub のみ）。プロトコル・障害注入・依存分解・再計画・end-to-end を検証する。

```bash
python3 tools/kiro-flow/tests/test_kiro_flow.py
# または: python3 -m unittest discover -s tools/kiro-flow/tests
```

主なケース: 決定的タイブレーク、**lease 切れ claim の回収（死んだワーカー）**、
**同時 claim でも勝者は 1 人**、逐次依存の分解、失敗 → 再計画 → retry 成功（end-to-end）、
**要求 claim でデーモンが 1 台に決まる**・`run_claimable_count` の依存考慮、
**6 パターン検出・並列数抽出・fan-out/tournament のグラフ形・classify ルーティング・verify fail の作り直し**、
**構造化成果 + reduce 集約・データ駆動 fan-out（split→map→reduce）・統合前 gate（--review）・
グラフ健全性検査（未知依存/循環/自己ループ）・kind 正規化**。

stub の擬似実行スリープは設定ファイルの `stub_sleep_max`（既定 1〜5 秒）で調整でき、テストは `0` で
高速に完走する（約 3 秒）。設定ファイルに無い場合は環境変数 `KIRO_FLOW_STUB_SLEEP_MAX` → 既定 5 に
フォールバックする（後方互換）。

### 実動作（kiro-cli）パターン確認

実 planner/executor で 7 パターンが期待どおり動くかを手動/半自動で確認するケース集を
[`tests/pattern_cases.yaml`](tests/pattern_cases.yaml) に記録している。各ケースに request・
狙いのパターン・観察する仕組み・`review` gate の有無・決定的チェックと、点検用スニペットを含む。
集約パターン（map-reduce / fan-out-and-synthesize）では検証 gate が自動挿入されることの確認も兼ねる。

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
- **P1–P4**: 構造化成果＋`reduce` / 7 つ目のパターン `map-reduce`（データ駆動 fan-out: split→map→reduce）/
  複合パターン＋統合前 gate＋グラフ健全性検査 / planner 出力の正規化。✅
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
