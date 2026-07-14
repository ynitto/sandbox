# agent-flow

> **由来**: `tools/kiro-flow/` を置換せずクローンし改称した系統。設計正典は
> [`docs/designs/agent-flow-design.md`](../../docs/designs/agent-flow-design.md)。
> 改称方針: [`docs/designs/agent-tools-rename-design.md`](../../docs/designs/agent-tools-rename-design.md)。

kiro-cli で **Claude 風の Dynamic Workflow**（動的にタスクを分解 → ワーカーへ委譲 → 結果統合）を
実現する基盤。通信は **ファイルのみ**で行い、バスを git に差し替えれば**複数 PC へ分散**できる設計。

> **現状: M7＋P1–P4（7 パターン戦略）**
> orchestrator が [Claude Dynamic Workflows の 6 パターン](https://zenn.dev/aria3/articles/claude-code-dynamic-workflows-6-patterns)
> ＋ agent-flow 追加の `map-reduce`（計 7）をカタログとして持ち、**要求に応じてパターンの組み合わせと
> 並列数を選んで**タスクグラフを形作る。

## できること

- **7 つのワークフローパターン**を orchestrator が知っていて、**要求からパターンと並列数（fan-out 幅）を
  自動選択**してグラフを形作る（下記）。kiro 評価役はパターンを踏まえて継続（ルーティング/再生成/統合）を判断。
- **`daemon`**：常駐し、投入された要求を拾って orchestrator を起動、claim 可能タスク量に応じて
  **ワーカーをオンデマンド起動**（仕事が無くなれば自然終了）。**分散時は各 PC でデーモンを動かす**だけ。
  起動は**冪等**——同一バスのデーモンが既に稼働していれば二重起動せず終了する。冪等判定のロックは
  バスを `realpath` で正規化したキーで `$TMPDIR/agent-flow-locks/`（設定 `lock_dir` / `--lock-dir` で変更可）に
  置き、PID を記録する。これにより symlink 経由や別 cwd で起動した外部デーモンも、別ツール（agent-project 等）
  から同一デーモンとして発見できる（`flock` 不能環境では PID 生存で判定）。
- **シャットダウン耐性（孤児 run の自動再開）**：PC の毎日シャットダウンやクラッシュで daemon ごと
  消えても run は失われない。次に起動した daemon が生存リース切れの非終端 run（孤児）を検知して
  reclaim し、**同じ run-id で orchestrator を再起動**する——確定済みの `results/` はバスに残っている
  ため、**未完了ノードだけが続きから実行される**。再開は「進捗なしの連続 `max_resumes` 回」
  （既定 3）まで。前回の再開以降に results が増えていれば数え直すため、進捗のある長期 run は
  何日でも再開を継続できる。上限超過・要求ファイル欠損だけが従来どおり failed に確定され
  （理由に `orphaned` が残る）、result を待つ消費者の永久待機を防ぐ。なお再開直後、死んだ worker の
  ノード claim が lease 内に残っている間は該当ノードの再実行を待つ（最大 `--lease` 秒。夜間
  シャットダウン→翌朝起動なら通常は失効済みで、即座に続きから走る）。ただし**新世代のリトライに
  `inherit_from` で引き継がれた旧 run（世代交代で消えるべき旧リトライ）は再開しない**。消費者
  （agent-project 等）はリトライ時に先行 run を明示 cancel せず `inherit_from` 付きで次世代を投入し、
  `inherit_from` は実行中の先行 run を安全のため殺さないため、旧世代が非終端のまま inbox に残る。
  これを素朴に全孤児 resume すると再起動時に旧世代が一斉に復活して**二重実行**になるので、次世代に
  引き継がれた旧 run は再開せず `superseded`（`failed` 相当・`superseded_by` を記録）で終端化する。
  次世代の `inherit_from` が確定済みノードを引き継いでから掃除するので**作業は失わない**。
- **電源断でのオブジェクト破損に耐える（durable write ＋ 自己修復）**：PC の定期シャットダウンや
  電源断が git の書き込み途中に起きると、loose object が **サイズ 0** で残り（`object file … is empty`）、
  以後 add/commit/push が全滅して「同期できない」状態になる。これを **予防**（管理クローンと
  ローカルパスの共有リポジトリ本体に `core.fsync=all`/`fsyncMethod=batch` を設定し rename 前に
  内容を fsync）と **自己修復**（`git fsck` で破損クローンを検知し捨ててリモートから作り直す。
  再利用時・`sync_push`/`sync_pull` 実行中のどちらでも）で塞ぐ。状態鏡の `state_git` にも同じ耐性を
  適用。リモート本体自体が壊れた場合は「リモート破損の可能性」を明示して中断し無限再クローンを
  避ける（復旧手順は「設計の肝」の「破損リポジトリの復旧」を参照）。
- **park & poll（承認待ちを worker スロットから切り離す）**：gitlab 等の executor が「人の承認待ち」で
  worker をブロックし続ける代わりに、ノードを **park（保留）** して claim を解放する（worker スロットが空く）。
  承認待ちは監視主体（daemon/run）が `service_waits` で `watch_interval`（既定 90 秒）毎に **まとめて再確認** し、
  決着したら結果を書く。これで「承認待ちが `max_workers` を食い潰して発行が止まる」問題と「N プロセスが各自
  30 秒毎に GitLab を叩く多重ポーリング」を同時に解消する（`max_workers` は小さいまま据え置ける）。park 記録
  `runs/<run>/waits/<node>.json` はバス上で **git 同期し daemon 消失を跨いで生存**——次に起きた daemon が
  引き継いで再確認する（孤児 run reclaim と同じ耐性）。生存リース（`wait_lease`）が失効すれば `node_state` は
  `pending` へ**縮退**し、full worker が **冪等な再アタッチ**（同一トークンの既存 open イシューに再接続）で拾い直す
  ——park を行き止まりにしない。監視主体が無い単発 `work` 実行では従来どおりブロック待機へ**フォールバック**する。
  **分散（git バス）では、起票は per-node の claim で全 PC に公平分散し、監視は各 run の駆動オーナー daemon 1 台に
  分担する**（`service_waits` は「自分が orchestrator を回している run」だけを見る）。これで N 台が全 park を重複
  ポーリングせず、run が各 PC に分散する分だけ監視も分散する。オーナー消失時は孤児 reclaim が run（＝監視）を
  別 PC へ移すので取りこぼさない。
- **同時イシュー上限（バックプレッシャ）**：`gitlab.max_open_issues`（0=無制限）で「同時に開ける未決着イシュー数」を
  絞れる。上限に達したら**起票を一時停止**する（**エラーにはしない**。人がレビューを捌いて枠が空けば自動で起票再開）。
  人のレビュー速度に発行をペーシングし、PC/GitLab サーバ負荷を抑えるための蛇口。既存の再タスク打ち切り
  （`--max-retries`）と同じく「これ以上作らない」思想の延長で、run を落とさない。
- **`cancel`（run スコープの恒久停止）**：`agent-flow cancel <run-id>` で run を **`canceled`** に終端化する。人の明示指示に
  よる唯一の hard-stop で、**承認待ちで park 中の run も暴走中の run も止められる緊急回避手段**。cancel マーカーは
  inbox に置かれ git 同期で全 PC / daemon へ伝わり、監視主体が **新規起票・park の再ポーリング・孤児 resume を
  同時に停止**する（`canceled` は終端なので `active_runs` から外れ reclaim 対象にもならない）。`--close-issues` で
  起票済みイシューに取消コメントを付けてクローズもできる（既定はイシューを残し、追跡だけやめる）。
- **`status`**：公式 Dynamic Workflows 風のダッシュボード（進捗バー・エージェント状態ツリー・
  アクティビティ・最終結果）。`--follow` でライブ監視。
- **`result`**：完了した run の**最終結果を探し出して提示**。集約／末端（sink）ノードの全文出力を
  自動特定して返す（`status` の要約に対し全文）。run-id 省略で最新を選択、`--json` で機械可読。
- **`submit`**：要求を inbox に投入。デーモンが拾う（要求は claim で 1 台だけが orchestrate を担当）。
- **`run`**：単発実行。**既存 run-id なら再開、無ければ新規**と状態で自動判断（旧 `up`/`resume` を統合）。
  既存 run-id が **`failed`** のときは**明示 retry** として扱い、**失敗ノードだけを `pending` へ戻して
  再実行**する（確定済み `done` ノードは温存＝続きから）。これが無いと failed run は再開しても全ノードが
  終端のまま静止し、何も再実行されない（`done`/`canceled` は終端として尊重し再実行しない）。
- 要求をタスクに分解し、**依存関係を尊重**しつつ複数ワーカーが**競合せず** claim して並列実行。
- **動的な再計画**：全タスク完了後に結果を評価し、不足があればタスクを追加して反復（最大 `--max-iterations`）。
  達成不可能な完了条件で無限に再タスクを積まないよう、同一系統の作り直しは **サーキットブレーカー**（`--max-retries`、既定 3）で打ち切る。
- **中間成果物のファイル参照プロトコル**：`output`/`data`（JSON）に乗らない大きな成果物は、ノードごとの決定的ディレクトリ
  `artifacts/<id>/` に書き出す。後続タスクは**依存ノードの同じパス**を読んで成果物を発見でき（中身は本文に貼らずパス参照）、
  依存成果物が大きくても kiro-cli 起動時のコマンドライン長制限（ARG_MAX）に達しない（超過分は一時ファイルへ退避）。
- **`--git` で複数 PC 分散**：各ノードが共有リポジトリの自分専用クローンで作業し、push/pull で通信。
  `--git-subdir` でリポジトリ内のサブディレクトリをバスにでき（既存リポジトリの間借り）、clone/pull/push は
  **sparse checkout** でそのサブツリーだけを展開する（無関係なファイルを取得しない）。各ノードは起動毎に
  バスを clone するため、**初回 clone もネットワーク障害に備えて指数バックオフでリトライ**する（push/pull と
  同様）。委譲される側（実作業ノード）のワークスペース clone も同様にリトライする。
- **1 run = 1 ワークスペース（唯一の書込先）**：`run`/`submit` に `--workspace <url|JSON>`（**ちょうど1つ**）を渡すと、その
  run の **worker がワークスペースを temp 領域に用意し、作業ブランチ `af/<run-id>` を `base` から作ってエージェントへ
  渡す**（「ここで編集せよ。commit/push は agent-flow がやる」）。作業ツリーは **URL 単位のホスト共有 bare ミラー
  （`--mirror --filter=blob:none`）から detached worktree を生やして用意**する（フル clone を「初回 1 回+増分」へ圧縮し
  GitLab の pack 生成負荷を抑える）。毎回 fetch してから最新コミットで worktree を作るので**鮮度は従来の都度 clone と同等**。
  ミラー/worktree が使えない環境では従来の direct clone に自動フォールバックする。共有ミラーの置き場は
  `KIRO_GIT_CACHE_DIR`（既定 `$TMPDIR/kiro-git-cache`、agent-project と共有）。詳細は
  [docs/designs/git-worktree-cache-pattern.md](../../docs/designs/git-worktree-cache-pattern.md)。**エージェントが編集したら agent-flow が commit して
  push**（分散の別 worker は同じ `af/<run-id>` へ push し、rebase リトライで統合）。**変更が無ければブランチを push しない**＝
  調査だけの読み取り専用グラフでは何も書き込まない。**作業後に clone を必ず削除**。ワークスペースは run の bus メタに載るため
  local/daemon/remote で同一に働く。`--workspace` 値は素の URL でも、構造化 JSON（`{url,path,base,target,desc}`）でも受ける。
  **リポジトリの同一性は (url, path, base)**（同 URL でも path（モノレポのフォルダ）や base（作業ブランチ）が違えば別）。
  作業指示は `workspace_instruction` で「path 配下のみ変更・MR/PR ターゲット=target」を伝え、gitlab executor 経由なら
  **起票先プロジェクトをこのワークスペース URL から解決**してイシュー本文にも載る。
- **書込先のルーティングは agent-project（制御層）が担当**：「どのタスクをどのワークスペースへ」は agent-project の
  charter `## repos`（`owns:` 担当パス）と `route:` ルールが1つに決め、`--workspace` として渡す。agent-flow は渡された
  ワークスペースを厳格に守る側に徹し、ノード単位の repo 割り当ては行わない（run 内の全ノードが同一ワークスペースを共有）。
- **参照リポジトリ（読むだけ）は `--reference` で構造化伝搬**：書き込まない参照用リポジトリは clone 管理せず、
  `--reference <url|JSON>`（複数可）として run メタに載せる。worker はそれをエージェントのプロンプト（参照節）と
  **gitlab イシュー本文の『## 参照リポジトリ』節**に描画する（要求本文へ畳むと分解後のノード/イシューに届かないため）。
  未注釈のノードは worker 側で全 repo にフォールバックする（取りこぼし防止）。これにより fan-out で多数のノードに
  分解されても、各ノードは自分に必要な repo だけを clone する（URL 単位の重複排除と併せて無駄 clone を最小化）。
- **分解の粒度（`granularity` / `--granularity`）**：タスク分解の細かさを設定ファイルで調整できる。`coarse`（現状）/
  `fine`（1段細かい）/ `finest`（2段細かい・**既定**）の3段。細かいほどプランナーへ「原子的に分解せよ」と指示し、
  並列ノード数を 1/2/3 倍にスケールする（上限 16・全 planner 共通／flow-planner にも `--granularity` で伝搬）。
  要求に `x3`・`並列3` の明示があればそれを尊重し倍率を効かせない。agent-project から呼ぶ場合も `agent-flow.yaml` の
  `granularity` がそのまま効く。
- **見本先行分解（`exemplar_first` / `--exemplar-first`）**：map-reduce の fan-out を「1件先行 → 自動検証ゲート →
  残り展開」にする（既定 off）。split 完了直後は **先頭1件(pilot map)とその verify ゲートだけ**を出し、ゲート通過後に
  残りの map（pilot に依存＝見本を範に取る）と reduce を展開する。同様手順の繰り返しを 1 件で固めてから一気に流せる。
  **人のフィードバックを介す版**は agent-project の cohort が担う（こちらは agent 自動版）。
  選択肢としての when_to_use / when_not_to_use / 例示 / 適用具体例は flow-planner カタログの
  `variants.pilot-then-batch`（`.github/skills/flow-planner/patterns-catalog.yaml`）にまとめてある。
- **再開**：`run --run-id <id>` で中断した run を再開（計画はやり直さず未完タスクから継続）。
- **lease ハートビート**：実行中はリースを延長し続け、長時間タスクでも他ノードに横取りされない。
- **`status`**：状態を 1 回表示。`--follow` でライブ監視（tmux ペインに置けば監視ダッシュボード）。
- **`gc`**：古い・完了済みの run をバスから削除（git バスでは git rm＋push）。対応する inbox 要求と
  claim も併せて消す（残すとデーモンが完了済み要求を拾い直して再実行してしまうため）。加えて、
  **run を伴わない「孤児 inbox 要求」も掃除する**——旧バージョンや外部ツールが run だけ消した／
  crash 等で取り残された要求は、デーモンの受理ゲート（`run_exists` のみ）から見ると「新規要求」に
  見え、**不要な run を再起動する**。`--older-than` より古く、かつ現在 claim されていない（lease 内で
  担当中でない）孤児だけを消す（フレッシュな未受理要求＝正規の受理待ちは保護。`--status` 指定時は
  run status で絞る意図なので触らない）。`--dry-run` で対象確認できる。
- **一時ファイルの自動掃除**：`daemon` が `cleanup_interval` ごとに、バス外に溜まる一時ファイルを掃除する
  — 未使用ロック（`$TMPDIR/agent-flow-locks/`）・`*.tmp.<pid>` 中間ファイル残骸・孤立した git クローン。
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

- 組み込み名 `agent` / `stub`
- **プラグイン名**（例 `gitlab`）— 検索ディレクトリの `executors/<name>.py` を解決
- **`.py` への明示パス** — そのファイルをプラグインとしてロード

プラグインは標準ライブラリのみで書ける単一ファイルで、次の関数を公開します（kiro-loop の
`event_hook` の `check()` に相当）。本体が `importlib` で動的ロードし（mtime キャッシュ付き）、
ワーカーが各タスクで呼び出します。

```python
def execute(kind, goal, dep_results, model=None, art_dir=None, dep_arts=None):
    ...  # (text, data) を返す
```

- **検索順**：`<スクリプトと同階層>/executors/` → リポジトリの `tools/agent-flow/executors/` →
  `~/.agent/agent-flow/executors/`（旧インストーラ配置・後方互換）→ 設定 `executor_dir`（`--executor-dir`）。
- **プラグイン設定**：同名のトップレベル設定ブロック（例 `gitlab:`）を JSON 化し、環境変数
  `AGENT_FLOW_EXECUTOR_CONFIG` でプラグインへ渡します。プラグインは個別の環境変数で上書きも可能。
- **インストール**：`install.sh` が同梱プラグインを **本体と同じフォルダ**（`<install-prefix>/executors/`、
  既定 `~/.local/bin/executors/`）へコピーするため、単一ファイル配布後も `--executor <name>` が検索順 #1
  「スクリプト同階層の `executors/`」で名前解決できます（kiro-loop と同じ「本体隣」の補助アセット配置）。

### gitlab ワーカーバス（同梱プラグイン）

`--executor gitlab` を選ぶと、各ワーカータスクを [gitlab-idd](../../.github/skills/gitlab-idd/) スキルの
`gl.py` で **GitLab イシュー** にして起票する。リモートの（別マシン・別人の）ワーカーがイシューを拾って
実装し、レビュアーが受け入れ条件を満たすと `status:approved` を付与する。agent-flow はイシューを
**ポーリング**し、`status:approved` に達したら**クリーンな関連 MR（コンフリクト無し・未解決レビュー
コメント無し・**ターゲットブランチがワークスペースの `target` と一致**）を自動マージしてイシューを
クローズ**する（自動承認・`auto_merge` 既定 on。gitlab-review-viewer の承認ボタンと同じ規則で、差分なし MR は
クローズ＋ブランチ削除、approved なのに未クリーン（または別ブランチ向け）なら `# 差し戻し` コメント＋
`status:needs-rework` でワーカーの修正ループへ戻す）。MR の宛先検証はワークスペースの `target`（無ければ
`base`）を基準にし、`--workspace` が無く target 不明のとき（`repo_url` フォールバック）はスキップする。
GitLab は作業履歴（Merged MR ＋ closed イシュー）の台帳として残る。人が先に全 MR をマージした場合も
従来どおり承認として決着する（`auto_merge: false` でその経路のみに戻せる）。
ローカルに kiro-cli が無くても、GitLab 越しに作業を委譲できる。

```
worker タスク ──▶ イシュー起票（status:open,assignee:any ＋ priority）
                     │
       （リモートのワーカーが拾って実装 → レビュアーが status:approved を付与）
                     │
   イシューをポーリング ──▶ approved ＋ MR クリーン ⇒ 自動マージ＆クローズ＝完了
                             approved だが未クリーン ⇒ # 差し戻し ＋ needs-rework で修正ループへ
```

- **opt-in**：既定の executor は `agent` のまま。`--executor gitlab`（または設定 `executor: gitlab`）で
  明示的に選んだときだけ有効になる。`gitlab-idd` スキル未導入や接続未設定なら起票時に明確に失敗する。
- **前提**：`.github/skills/gitlab-idd` が導入済みで、`connections.yaml` か `GITLAB_TOKEN` で接続設定済み。
- **再計画はローカル**：evaluator-optimizer の継続判断（再タスク生成）はオーケストレータ側で `agent` を使う。
  GitLab へ委譲するのは**ワーカータスクの実行**だけ。
- **設定**：ポーリング間隔・タイムアウト・付与ラベルは設定ファイルの `gitlab:` ブロックで調整する
  （[`agent-flow.yaml.example`](agent-flow.yaml.example) 参照）。`timeout` を `0` にすると無限待ち。
- **park & poll（承認待ちを worker から切り離す）**：daemon/run から起動したワーカーでは、承認待ちで
  ブロックし続けず、1 回だけ決着を確認して未決着ならノードを **park**（`waits/<node>.json` へ退避）し
  claim を解放する。承認待ちは監視主体が `watch_interval`（既定 90 秒）毎に **まとめて再確認** する
  （多数の承認待ちがあっても GitLab へのポーリングは監視 1 本のバッチに畳まれる）。決着判定・却下 data・
  外部クローズ推定・冪等再アタッチはブロック版と同じ関数を共有し、**確認する場所が worker か監視主体かの
  違いだけ**。監視主体の無い単発 `work` 実行では従来どおりブロック待機へフォールバックする（後方互換）。
- **同時イシュー上限（負荷の蛇口）**：`gitlab.max_open_issues`（0=無制限）で同時に開ける未決着イシュー数を
  絞れる。上限で**起票を一時停止**（バックプレッシャ、エラーにしない）し、人がレビューを捌いて枠が空けば
  自動で起票再開する。承認待ちが溜まっても PC/GitLab を溢れさせない。
- **冪等な起票（二重起票しない）**：イシュー本文にタスクごとの決定的トークン（`art_dir` ＝
  `runs/<run>/artifacts/<node>` 由来）を隠しマーカーとして埋め込む。起票前に同じトークンの **open
  イシュー**を検索し、見つかれば**再アタッチ**してポーリングを再開する。これにより、ワーカーが
  MR の決着待ちの最中に夜間停止などで殺され、`lease` 失効後にタスクが再 claim されても、同じタスクの
  イシューが二重に立たない（リモートの別ワーカーが拾い直すケースも含む）。
- **外部クローズの承認/却下判定**：MR の状態で決着がつかないまま**イシューが外部でクローズ**された
  場合は、`status:approved`/`status:done` ラベル → イシューコメントの内容（承認語/却下語）の順で
  承認・却下を推定し、タスクグラフへ反映する（承認なら `done` で下流へ、却下なら `[gitlab-reject]` で
  上位がやり直す）。判断材料が無いクローズは取り下げ＝却下扱い。
- **却下も機械可読な決着として残る**：却下時は failed result の `data` に承認と対称の構造化データ
  （`issue_iid` / `web_url` / `decision: rejected` / `reason` / `guidance`（人コメント）/ `merged_mrs`）
  が書かれる。status は failed のまま——done は「後続が成果に依存してよい」契約であり、成果の無い
  却下では満たせない。やり直しの判断とループは上位（agent-project が `guidance` を feedback に注入して
  再委譲）が担う。消費側は output の `[gitlab-reject]` 文字列マッチに頼らず data で却下を検知できる
  （旧 agent-flow の run には data が無いため、文字列マッチはフォールバックとして残る）。
- **イシュー削除への防御**：決着待ち中にイシューが**削除**された（404）場合も、一般エラーでなく
  **取り下げ＝却下**として決着させる（`decision: rejected`・reason に「削除された」。コメントは
  イシューごと消えているため guidance は空＝上位が自動判断でやり直す）。正規の却下はイシューの
  **クローズ**で伝えること（gitlab-review-viewer の却下もイシューを削除せず閉じる。関連 MR の
  クローズ＋ソースブランチ削除のみ行う）。
- **委譲先リポジトリ**：`gitlab:` ブロックの `repo_url` で委譲先の GitLab プロジェクト URL を明示できる。
  空の場合は `conn_label` の接続（`connections.yaml`）か、無ければ作業ディレクトリの `git remote origin`
  から解決する。手元とは別のリポジトリへ委譲したいときに指定する。
- **API のベース URL**：既定は repo_url / ワークスペース URL の scheme+host(:port) をそのまま使う
  （http の self-host・別ポートも可）。SSH 形（`git@host:...`）は `https://<host>` に既定するため、
  SSH 形しか無く API が http/別ポートの構成（local-gitlab-stack 等）は `gitlab.api_base` で明示する。
  なおエラーメッセージ中の `/projects/group%2Frepo/...` の `%2F` は GitLab API の正規エンコードで、
  接続可否とは無関係（接続不能の典型は scheme/ポートの取り違え・DNS・トークン）。

```bash
# 例: タスクを GitLab に委譲して承認まで待つ（要 gitlab-idd 接続設定）
agent-flow --bus /tmp/flowbus run "ログイン機能を実装" --executor gitlab
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
`map-reduce` は agent-flow が追加した 7 つ目）から組み合わせと並列数を選び、各ノードに **kind** を
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
  高精度な分解を行う（`.github/skills/flow-planner/` スキル）。`--planner agent` ならエージェント CLI
  （`agent_cli` の設定に従う）が1回の呼び出しで選ぶ。`--planner stub` は要求のキーワードで判定
  （「分類/振り分け」→classify、「tournament/最良」→tournament、「候補/フィルタ」→filter、
  「検証/レビュー」→adversarial、「繰り返し/通るまで」→loop、それ以外→fan-out）。
- **並列数**: 要求中の `xN` / `並列N` を拾う。無ければ並列タスク数から既定（2〜6）。
- **継続判断**: 静止（claim 可能・実行中タスクが無い）するたびに評価し、`classify` 結果でルーティング、
  `verify` が fail なら依存を作り直して再検証（`replaces` で後続の依存を付け替え）、失敗タスクは retry。
- **実行系プロンプト（worker/verify/evaluator）**: `executor: agent` のとき、`flow-worker` スキル
  （`.github/skills/flow-worker/`）が見つかれば、実行規律 —「三つの約束」（前提を書く・範囲を守る・
  検証してから渡す）、verify の再導出検証、evaluator の受け入れ評価 — と、git 操作を
  `git_worktree.py`（共有キャッシュ + worktree の provision/release/push CLI）に限定する
  git 利用規約を織り込んだプロンプトを使う（flow-planner と同じ作戦・同じ検索順）。
  スキルは決定的なプロンプトビルダーで LLM は呼ばない。未インストール・失敗時は
  組み込みプロンプトへフォールバックする。設定 `worker_skill: none` で常に組み込みを使う。
- **構造化成果（structured results）**: 各ノードの結果はテキスト `output` に加え、任意の **`data`（JSON）**
  を持てる。依存先へはテキスト＋構造化データの両方を渡す。`reduce` kind は依存の `data`（リスト等）を
  畳み込んで集約する集約ノード。agent executor は出力を寛容パースして `data` に格納する。
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

**ノードクローンの自己回復（git バス）**：各ノードのクローンは使い捨てのキャッシュで、
真実は常にリモート側にある。前プロセスの異常終了（SIGKILL・電源断・daemon の terminate）が
`.git/index.lock` 等のロック残骸や中断 rebase を残しても、再利用時に残骸を除去して回復し、
それでも使えなければクローンを作り直す。実行中にロックへ遭遇した場合も、新しいロック
（稼働中の他 git の可能性）は短く待ち、古いロック（残骸）は除去して再試行する。これが無いと
orchestrator の run 作成（`sync_push`）が恒久的に失敗し、daemon が同じ要求を毎 poll
再 claim し続ける無限ループに陥る。加えて daemon 側でも、orchestrator が run の meta を
一度も書けずに死んだ要求は failed run を新規作成して終端化する（`fail_request`）ため、
最悪ケースでも要求の再 claim ループは有限回で止まる。

**電源断で空になったオブジェクトへの耐性（durable write ＋ 自己修復）**：git は既定で
loose object を「一時ファイル→ rename」で書くが *中身の fsync をしない*。PC の定期
シャットダウンや電源断が書き込み途中に起きると、rename のメタデータだけがジャーナルで残り
中身が未フラッシュのまま——再起動後に **サイズ 0 のオブジェクトファイル**が残る（症状:
`error: object file .git/objects/xx/yy… is empty` → 以後 add/commit/push/checkout/pull が
全滅し「同期できない」状態になる）。agent-flow はこれを 2 段で防ぐ:

- **予防（durable write）**：管理するクローンと、リモートがローカルパスの共有リポジトリ本体
  （push を受ける `receive-pack` 側）に `core.fsync=all` / `core.fsyncMethod=batch` を設定し、
  rename 前にオブジェクト内容を fsync させる。`batch` により tiny JSON の書き込みでも安価。
  （URL リモートの本体側は agent-flow から触れないため、サーバ側で同設定を推奨——後述。）
- **自己修復**：それでも壊れたクローンは `git fsck` の軽量プローブで検知し、捨ててリモート
  （真実）から作り直す。クローン再利用時（起動時）に加え、`sync_push`/`sync_pull` の実行中に
  破損が露見した場合も同様に作り直して続行する。未 push の作業は孤児 reclaim が続きから
  再実行するため、捨てても情報は失われない。同じ耐性を状態鏡の `state_git` にも適用する。

**破損リポジトリの復旧（リモート本体が壊れた場合）**：クローンは使い捨てなので上記で自動回復
するが、*共有リポジトリ本体（リモート）* 自体のオブジェクトが電源断で壊れると clone/fetch が
失敗する（作り直しでは直らない）。agent-flow はこの場合「リモート破損の可能性」を明示した
エラーで中断する（無限の再クローンを避ける）。復旧手順:

1. どれか 1 台の **健全な PC のクローン**を特定する（`git -C <clone> fsck` がエラーを出さないもの）。
2. リモートの壊れたオブジェクトを、健全クローンの `.git/objects/` から補填するか、健全クローンを
   新しいリモートとして `git push --mirror` で作り直す。
3. 恒久対策として、リモート（ベアリポジトリ）側でも
   `git -C <remote> config core.fsync all && git -C <remote> config core.fsyncMethod batch`
   を設定しておく（ローカルパスのリモートなら agent-flow が自動設定するが、URL 越しのサーバは手動）。

## インストール

```bash
bash tools/agent-flow/install.sh          # ~/.local/bin/agent-flow にインストール
bash tools/agent-flow/install.sh --prefix /usr/local/bin   # 任意の場所へ
```

標準ライブラリのみで動作（pip 依存なし）。git は分散モードで必要、kiro-cli は実運用で必要
（無くても `--planner stub --executor stub` で動作確認できる）。

本体の実体は `agent_flow/` パッケージ（agent-project と同じ「断片の共有名前空間合成」）。
`install.sh` はこれを **zipapp 単一ファイル**にまとめて `~/.local/bin/agent-flow` へ置く
（CLI 呼び出し可能・配布は1ファイルのまま）。開発・テストはリポジトリ内の薄い shim:

```bash
python3 tools/agent-flow/agent-flow.py …   # → agent_flow パッケージを起動
```

以降の例は `agent-flow` コマンド前提（未インストールなら上記 shim で代用可）。

### 開発時の構成

```
tools/agent-flow/
  agent-flow.py          # 薄いエントリ（後方互換・テスト e2e）
  agent_flow/            # 実体（断片 *.py。__init__.py が共有名前空間へ exec）
  executors/            # executor プラグイン（install 時に prefix 隣へ）
  install.sh            # zipapp 化して ~/.local/bin/agent-flow へ
```

編集は `agent_flow/<断片>.py` を触る。配布後も `--help` / `run` / `daemon` は従来どおり。

## 設定ファイル

環境ごとに決まる値（バス、git リポジトリ、planner/executor、`max_workers`、`poll`、`lease` 等）は
設定ファイルに書ける。**優先順位は CLI 引数 > 設定ファイル > 組み込み既定**。

```bash
cp tools/agent-flow/agent-flow.yaml.example ~/.agent/agent-flow.yaml   # 編集して配置
```

検索順序（フォールバック、kiro-loop と同じ流儀）:

1. `--config <path>` で明示指定
2. カレントディレクトリの `.agent/agent-flow.{yaml,yml,json}`
3. `~/.agent/agent-flow.{yaml,yml,json}`

YAML を使うには PyYAML が必要（任意）。無い環境では **JSON**（`agent-flow.json`、同じキー）でよい。
設定できるキー: `bus` / `git` / `git_branch` / `git_subdir` / `planner` / `executor` / `executor_dir`
（プラグイン検索ディレクトリ）/ `worker_skill`（executor=agent の実行系プロンプト供給スキル。
既定 `flow-worker`・`none` で組み込み）/ `model` / `max_workers` / `max_runs`（daemon の同時実行 run＝
orchestrator プロセス上限。既定 8。全ノードが park（承認待ち）の run は数えない。0 以下で無制限）/
`workers` / `max_iterations` / `poll` / `lease` /
`kiro_timeout`（kiro-cli タイムアウト秒）/ `stub_sleep_max`（stub スリープ上限秒）/
`gitlab`（gitlab executor プラグイン用。`max_open_issues`・`watch_interval` を含む park & poll 設定もここ）/
`state_git[-branch/-subdir/-interval]`（状態の git 保存・共有）。例は
[`agent-flow.yaml.example`](agent-flow.yaml.example) を参照（実運用の組み方＝WSL 常駐＋gitlab executor 分散＋
viewer 監視＋GitLab バックアップは [`agent-flow.state-git.yaml.example`](agent-flow.state-git.yaml.example)）。

`kiro_timeout`（kiro-cli 1 呼び出しのタイムアウト秒。既定 600、`0` で無効化）。
心拍が lease を延長し続けるため、**ハングした kiro-cli はこのタイムアウトでしか止められない**
（超過時はそのタスクを失敗扱いにして再計画の retry に回し、run 全体の停止を防ぐ）。
設定ファイルに無い場合は環境変数 `AGENT_FLOW_KIRO_TIMEOUT` → 既定 600 にフォールバックする（後方互換）。

## 使い方

### デーモン（推奨・オンデマンド起動）

```bash
# 1) デーモンを常駐起動（このマシンのワーカー上限は --max-workers）
agent-flow --bus /tmp/flowbus daemon --max-workers 4 &
# サブコマンドを省略すると daemon として起動する（値は設定ファイル/既定から）
agent-flow &

# 2) 要求を投入（run-id が標準出力に返る）。デーモンが拾って自動実行する
#    submit の前に daemon を確保すること（daemon は冪等なので、そのまま起動コマンドを実行してよい）
RID=$(agent-flow --bus /tmp/flowbus submit "要件整理; API設計; テスト")
agent-flow --bus /tmp/flowbus --run-id "$RID" status --follow --until-done

# 分散: 各 PC で同じ --git を指すデーモンを起動するだけ。要求はどの PC から submit してもよい。
# 既存リポジトリ（GitHub 等）を間借りするなら専用ブランチ（例 agent-flow-bus）を使うと main を汚さない
agent-flow --git git@example.com:team/repo.git --git-branch agent-flow-bus daemon --max-workers 4 &   # PC ごとに
agent-flow --git git@example.com:team/repo.git --git-branch agent-flow-bus submit "<要求>"
```

### ワンショット（単発実行・既存 run-id なら自動で再開）

```bash
# kiro-cli 無しでプロトコルを確認（まずこれ）
agent-flow --bus /tmp/flowbus run \
  "要件を整理する; APIを設計する; テストを書く; READMEを書く" \
  --workers 3 --planner stub --executor stub --poll 0.5

# kiro-cli を使った実運用（既定）
agent-flow run "<要求>" --workers 3

# 中断した run を再開（要求は省略。状態を見て自動的に未完タスクから続行）
agent-flow --bus /tmp/flowbus --run-id <run-id> run

# 依存関係つきの分解（stub）: ';' は並列、'->' は逐次依存チェーン
agent-flow run "setup -> build -> test; write docs" --planner stub --executor stub

# 複数 PC 分散（共有 git リポジトリをバスにする）
agent-flow --git git@example.com:team/flow-bus.git run "<要求>" --workers 3
#   ローカルのベアリポジトリで動作確認:
#     git init --bare -b main /tmp/flowbus.git
#     agent-flow --git /tmp/flowbus.git run "A; B; C" --planner stub --executor stub

# 状態確認 / 最終結果 / ライブ監視 / 掃除
agent-flow --bus /tmp/flowbus --run-id <run-id> status            # 1 回だけ表示
agent-flow --bus /tmp/flowbus --run-id <run-id> status --follow   # ライブ監視
agent-flow --bus /tmp/flowbus result                              # 最終結果（run_id 省略で最新）
agent-flow --bus /tmp/flowbus --run-id <run-id> result --json     # 機械可読な最終結果
agent-flow --bus /tmp/flowbus gc --older-than 7 --keep 5 --status done --dry-run

# run を止める（承認待ちで park 中でも暴走中でも効く恒久停止。canceled で終端化）
agent-flow --bus /tmp/flowbus cancel <run-id>                     # イシューは残し追跡だけやめる
agent-flow --bus /tmp/flowbus cancel <run-id> --close-issues --reason "要件変更"  # 起票済みも後始末
```

### tmux で「実行 ＋ 監視」を一画面に

```bash
RID=run-XXXX
tmux new-session -d -s flow "agent-flow --run-id $RID run '<要求>' --workers 3"
tmux split-window -h "agent-flow --run-id $RID status --follow --until-done"
tmux attach -t flow
```

### 稼働診断（doctor）

```bash
agent-flow --bus /tmp/flowbus doctor          # 診断のみ（無害・既定）
agent-flow --bus /tmp/flowbus doctor --fix    # env/config を修正し program を gitlab-idd で起票
agent-flow --bus /tmp/flowbus doctor --json   # 連携呼び出し用の findings を JSON で出力
```

**収集と適用を決定的に・診断と分類は kiro-cli へ委譲** して稼働の問題を洗い出し、原因を分類する。

- **env**（ユーザー環境固有）… `kiro-cli`/`git` 不在・バスに書き込めない・worker/daemon 未起動 等。
- **config**（設定）… 有限停止の無効化（`max_iterations`/`max_retries` ≤0）・`lease`/`argv_limit` 不正・バス未作成 等。
- **program**（プログラム上の不具合）… 正しい環境・設定でも再現する failed・グラフ生成や claim/再計画のロジック欠陥。**コード修正が必要なものだけ**。

材料は決定的チェック（依存コマンド・バス・有限停止設定）＋稼働シグナル（直近 run の状態・滞留・失敗ノード・
kiro-cli エラー）。これを kiro-cli に渡して分類済みの所見を得る（kiro-cli 不在・解析不能なら**決定的チェックのみ**で続行）。

`--fix` のとき env/config は既知の修正（`ensure-bus`＝バス作成）を適用、判断が要るものは提案表示のみ。
**program は `gitlab-idd` スキルで GitLab イシューを起票**（スキルが無ければ出力のみ）。終了コード `0`=健康/`1`=所見あり/`2`=未解決の critical。

`--json` の `findings` は agent-project の `doctor` と同一スキーマ。**`agent-project doctor` が `--with-flow`（既定 on）で
この `agent-flow doctor --json` を同じバスに対して呼び、実行層の所見を統合する**（連携時は agent-flow 側が自分の env/config 修正と
program 起票を担い、二重作業を避ける）。

## 状態の git 保存・共有（state_git）— リモートの viewer に進捗/結果を見せる

ローカルバスのワーク内容（`<bus>/runs/`・`<bus>/inbox/`）を**共有 git リポジトリへ双方向同期**する。
リモートサーバで回している agent-flow の run の進捗・結果を、手元の
[agent-dashboard](../agent-dashboard/)（フロータブ）で眺め、viewer からの再投入
（inbox への要求ドロップ）をサーバへ届ける、を git だけで往復できる。
agent-project の同名機能（`state_git`）と対になる（同じ共有リポジトリの別 subdir を使える）。

```yaml
# .agent/agent-flow.yaml（サーバ側）
state_git: git@example.com:team/agent-state.git   # 共有リポジトリ（URL/パス）
state_git_subdir: agent-flow                      # リポジトリ内の保存先（名前空間）
state_git_interval: 300                          # fetch/push の最短間隔（秒）
```

- **`--git`（GitBus）とは別物**: GitBus は「バスそのものを git にして実行を分散する」。state_git は
  「実行はローカルのまま、**状態の鏡だけ**を共有する」——run の実行・終端は state_git に一切依存せず、
  同期失敗はログに残して続行する。`--git` 指定時はバス自体が共有 git なので state_git は無視される。
- **リモート負荷を抑える**: `state_git_subdir` だけの sparse・blob:none の管理クローン
  （`<bus>/.state-git`）を 1 本再利用し、fetch/push（バス走査も）は `state_git_interval`（既定 300 秒）で
  律速。push は共有すべきローカルコミットがあるときだけ（**run の終端時は間隔を待たず押し出す**）。
- **多重コミッタ前提**: 同一リポジトリには他プログラム（agent-project の state_git・viewer 側の
  [git-file-sync](../git-file-sync/) 等）もコミットする。ステージは自 subdir のみ、push 競合は
  `pull --rebase` → 再 push の指数バックオフで吸収し、force push はしない。
- **双方向・決定的裁定**: 前回同期スナップショット（manifest）基準の 3-way で発生源を判定し、
  同時変更のみ「`inbox/`（人の投入）はリモート優先・`runs/`（機械状態）はローカル優先」で裁定。
  gc / cleanup による run の掃除（削除）もリモートへ伝播する。書きかけの `*.tmp` は同期しない。

同期が走るのは `daemon` の poll ループ（間隔律速）・run 終端時（即時）・`run` の待機ループ。viewer 側の
組み方（clone または git-file-sync の pair + フロータブのバス発見）は agent-dashboard の README を参照。

### daemon の生存信号（status.json）— リモート viewer の稼働判定

daemon の稼働検知は本来ロックファイル（`$TMPDIR/agent-flow-locks/daemon-<sha1>.lock`。
pid のみ記録）で行うが、これは**同一ホスト限定**——state_git（鏡）越しにバスを見ているリモートの
viewer からは、daemon 自身の一時領域にあるこのファイルへ絶対に届かない。`<bus>/status.json`
（`host`/`pid`/`node_id`/`orchestrators`/`workers`/`updated_iso`/`fresh_after_sec`）を daemon が
書き、これも state_git で同期することで、viewer 側にロック不在時のフォールバック判定材料を渡す。

```json
{"host": "myserver", "pid": 4242, "node_id": "myserver-4242",
 "orchestrators": 1, "workers": 2,
 "updated_iso": "2026-07-05T03:34:24Z", "fresh_after_sec": 600}
```

- **`bus.root` 直下に置くだけで既存の state_git がそのまま同期する**: `StateGit._scan()` はバスの
  ツリー全体を走査するため、GitBus（`--git`）側のような sparse-checkout の追加設定は不要。
  GitBus モードでは書かない（sparse-checkout が `runs/`/`inbox/`（or `--git-subdir`）しか
  展開せず、対象外パスへの書き込みが `git add -A` を壊しかねないため。state_git と `--git` は
  元々ここでも相互排他）。
- **アイドル中の追加コミットは既定でゼロ**: 起動時に一度だけローカルへ書き、以降は実イベント
  （run 終端・「駆動中の run の生存リース」push）のタイミングで書き直し、その他ファイルの変更と
  **同じコミットに相乗り**する（単体では追加の push を生まない）。`--status-interval`
  （daemon サブコマンドの引数。既定 `0`＝無効）を指定しない限り、アイドル中は status.json に
  一切触れない。完全アイドルのままでも、起動直後に一度書いた内容が既存の `state_sync`（自身の
  `state_git_interval` で律速）に拾われるため、`state_git_interval` 以内には生存が可視化される。
- `fresh_after_sec` は daemon が自分の同期間隔（`state_git_interval`/`status_interval` の
  大きい方の 2 倍・下限 120 秒）から計算して埋め込むため、viewer 側は単純な経過時間比較だけで済む。

## 自動アップデート（既定 on）

スキルリポジトリ（このツールの配布元）の **main ブランチに更新が入ったら、daemon のアイドル時に自動で取り込む**。
**既定で有効**（6 時間ごと。前回チェック時刻は `~/.agent/agent-flow.update.json` に持続化され、
**再起動を跨いで間隔が尊重される**——前回から間隔ぶん経っていれば起動後の最初のアイドルで実施する）。
止めたいときは `update_enabled: false` か `update_check_interval: 0`。手順は doctor と同じ流儀で**決定的**——
知能は使わず、ファイル操作だけで完結する。

1. `git ls-remote` でスキルリポジトリ main の先頭コミットを確認する
2. 適用済み SHA（`~/.agent/agent-flow.update.json`）と違えば「更新候補」
3. **アイドル時（要求も子プロセスも無いとき）だけ**、temp 領域へ `tools/agent-flow/` だけを **sparse-checkout**（無関係ファイルは取得しない）
4. **取得した本体の内容ダイジェストが前回適用時と同一なら適用せず、ベースライン SHA だけ進める**
   （state_git 等で自分の push が更新元リポジトリの新コミットになる構成での自己増殖ループ防止）
5. その中の `install.sh` を実行して `~/.local/bin` の本体を更新する
6. **動いていたカレントディレクトリのまま** `os.execv` で新しい本体へ **graceful 再起動**する

**更新元 URL は通常は設定不要**。`install.py` がインストール時に生成する `skill-registry.json`
（`~/.kiro` / `~/.claude` / `~/.copilot` / `~/.codex` のいずれか）の `repositories.origin.url`
（無ければ `install_dir` のローカルクローン）から自動解決する。別リポジトリを使うときだけ `update_repo` を明示する。

```bash
agent-flow update --check    # 更新の有無だけ表示（取り込まない）
agent-flow update --now      # 更新があれば install.sh を実行して再起動
```

設定ファイル（`~/.agent/agent-flow.yaml`）で調整できる（すべて任意。**既定のままで有効**）。

```yaml
update_enabled: true            # 自動アップデートの ON/OFF（false で完全に止める。既定 on）
update_check_interval: 21600    # 更新チェック間隔（秒）。既定 6 時間。0 以下で自動チェック無効
update_repo: ""                 # 空なら skill-registry.json から自動解決。別 repo を使うときだけ指定
update_branch: main             # 追従するブランチ（空/既定なら registry の branch を採用）
update_subdir: tools/agent-flow  # リポジトリ内のこのツールのサブディレクトリ
update_installer: install.sh    # サブディレクトリ内で実行するインストーラ
```

> 初回チェックは「いま動いている本体が最新」とみなし、その時点の SHA をベースラインとして記録するだけ
> （更新はしない）。以降、main がそこから進んだときに更新を検出する。仕事中（worker 稼働中）は何もしない。

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
| `update` | スキルリポジトリ(main)の更新を確認。`--check` で有無のみ・`--now` で取り込み再起動（[自動アップデート](#自動アップデートopt-in)参照） |
| `orchestrate` / `work` | 計画役・ワーカー役の内部コマンド（`run`/`daemon` が起動する） |

### 主なオプション

| オプション | 既定 | 意味 |
|-----------|------|------|
| `--bus` | `./bus` | ローカルバスのルート（agent-project の既定 `<root>/bus` と同じ場所）/ git モードでは各ノードのクローン親 |
| `--git` | （なし） | 共有 git リポジトリ URL/パス。指定で複数 PC 分散モード |
| `--git-branch` | `main` | バスに使う git ブランチ |
| `--git-subdir` | （直下） | リポジトリ内でバスにするサブディレクトリ（sparse checkout 対象） |
| `--lease` | 1800 | claim のリース秒数（実行中はハートビートが延長） |
| `--workers` | 2 | 起動するワーカー数（`run`） |
| `--max-workers` | 4 | デーモンが同時に走らせる worker 上限（`daemon`） |
| `--planner` / `--executor` | `flow-planner` / `agent` | planner は `flow-planner`（3段パイプライン、既定）/ `agent`（エージェント CLI に1回問い合わせ）/ `stub`（オフライン検証）。executor は評価役にも使う |
| `--agent-cli` | `kiro` | LLM 実行に使うエージェント CLI（設定 `agent_cli`）。`kiro`=kiro-cli chat / `claude`=Claude Code ヘッドレス（`claude -p`・プロンプトは stdin 渡し）/ `copilot`=GitHub Copilot CLI（`copilot -p`・argv 渡しのため kiro と同じスピル退避が効く） / `codex`=OpenAI Codex CLI（`codex exec`・プロンプトは stdin 渡し・最終応答は `--output-last-message` 経由で取得）。planner / executor / verify 等の LLM 呼び出しすべてに効く。モデルは設定 `model` で指定 |
| `--max-iterations` | 3 | 再計画（evaluator-optimizer）の最大反復回数 |
| `--max-fanout` | 50 | データ駆動 fan-out（split→map）の最大展開数 |
| `--max-retries` | 3 | サーキットブレーカー：同一系統の作り直し（verify=fail 再生成・失敗 retry）の打ち切り回数。達成不可能な完了条件での無限再タスクを防ぐ |
| `--max-resumes` | 3 | 孤児 run（owning daemon 消失）の自動再開の上限（`daemon`）。「進捗なしの連続回数」で数え、進捗（新しい results）があれば数え直す。`0` 以下で無効（孤児は即 failed） |
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
- 実運用では `--planner agent` / `--executor agent` が要求するエージェント CLI
  （既定 `kiro-cli`。設定 `agent_cli` で `claude` / `copilot` / `codex` にも切替可）

## スキル

`.github/skills/agent-flow/` に、この CLI を呼び出すスキルを同梱。「ワークフローを実行して」「要求を投入して」
「デーモンを起動して」「run の状態を見て」などで発動し、`run`/`submit`/`daemon`/`status`/`gc` の使い分けや
要求の書き方（パターン/並列数/`--review`）を案内する。

## テスト

kiro-cli 不要（stub のみ）。プロトコル・障害注入・依存分解・再計画・end-to-end を検証する。

```bash
python3 tools/agent-flow/tests/test_agent_flow.py
# または: python3 -m unittest discover -s tools/agent-flow/tests
```

主なケース: 決定的タイブレーク、**lease 切れ claim の回収（死んだワーカー）**、
**同時 claim でも勝者は 1 人**、逐次依存の分解、失敗 → 再計画 → retry 成功（end-to-end）、
**要求 claim でデーモンが 1 台に決まる**・`run_claimable_count` の依存考慮、
**6 パターン検出・並列数抽出・fan-out/tournament のグラフ形・classify ルーティング・verify fail の作り直し**、
**構造化成果 + reduce 集約・データ駆動 fan-out（split→map→reduce）・統合前 gate（--review）・
グラフ健全性検査（未知依存/循環/自己ループ）・kind 正規化**、
**状態 git 同期（state_git: export/push・inbox 取り込み・3-way 裁定・多重コミッタ・間隔律速）**。

stub の擬似実行スリープは設定ファイルの `stub_sleep_max`（既定 1〜5 秒）で調整でき、テストは `0` で
高速に完走する（約 3 秒）。設定ファイルに無い場合は環境変数 `AGENT_FLOW_STUB_SLEEP_MAX` → 既定 5 に
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
- **M6**: `install.sh` で `agent-flow` コマンド化・サブコマンド整理
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
| **`agent-flow`** | **タスクグラフ** | **実行時に LLM が生成** |

`git-file-sync`（git をハブにした同期）と `gitlab-idd`（キューからの claim→実行→報告）の発想を、
タスクグラフの動的生成に組み合わせたもの。
