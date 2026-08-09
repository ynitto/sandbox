# agent-flow 設計書

> 最終更新: 2026-08-09（run phase と用途別 timeout、検証条件、資源効率、空応答の扱いを反映）
> 実装: `tools/agent-flow/`（本体 28 断片・約 10,000 行）、テスト `tools/agent-flow/tests/`（794 件）
> 関連: [agent-project 設計書](./agent-project-design.md) ／ [git worktree キャッシュ](./git-worktree-cache-pattern.md)
>
> 旧 `agent-flow-self-healing-retry-design.md`（自己回復リトライ）と
> `agent-flow-retry-inheritance-design.md`（リトライ引き継ぎ）は本書へ統合した。

## TL;DR

agent-flow は、自然言語の要求をタスクグラフへ分解して実行し、結果を評価して作り直しまで回す分散ワークフローエンジンです。**PC 間に配る単位は 1 つの要求（run）**で、落札した 1 台がその run のワーカーを自 PC 内に起こして完走します。agent-project から検証計画を受け取った run は、成果を作った worktree と revision のまま検証し、証跡付き receipt を返します（グラフ内の個々のステップが PC 間に散らないのは仕様。[運用ガイド §4.2](../guides/multi-pc-operations.md)）。

主要な決定は 3 つです。第一に、プロセス間の通信をバス上のファイルだけに限り、タスクの状態は専用フィールドではなくファイルの存在から導きます。第二に、二重実行を止めるロックは、書き込み先を名前で分けた claim と `(ts, who)` の決定的タイブレークで作ります。第三に、agent-flow 自身は常駐しません。受理と実行を別々の単発コマンドに割り、周期駆動は PC に 1 本の常駐体（`agent-project serve`）へ預けます。

却下した主要案は、中央のキューサーバ（分散の前提が崩れる）と、LLM に実行用スクリプトを書かせるコードハーネス（任意コード実行と分散の不整合）です。

読むべき人は、agent-flow を運用する人、executor プラグインを書く人、agent-project 側から呼び出す人です。GitLab 委譲の細部だけ知りたいなら `tools/agent-flow/README.md` の gitlab 節で足ります。

## 背景と課題

Anthropic の *Building Effective Agents* は、経路が固定された Workflow と、LLM が実行時に経路を決める Orchestrator-Workers を区別しています。agent-flow は後者を狙います。

問題は、後者を素直に実装すると 1 プロセスの中に閉じてしまうことでした。手元には常時稼働の PC が複数あり、GitLab 越しに人へ委譲したいタスクもあります。1 プロセスに閉じたオーケストレータは、PC をまたげず、夜間シャットダウンで作業が消え、人のレビュー待ちの間ワーカーを 1 枠占有し続けます。

解くべき問いは「実行時にタスク構造が変わるワークフローを、複数マシンにまたがって、途中で電源が落ちても失わずに回すには、何を共有すればよいか」です。答えが上の TL;DR、つまり共有するのはファイルだけ、という設計です。

### 目標

- 要求から実行時にタスクグラフを生成し、結果を見て再計画できる
- 同じ共有バスを見る N 台の PC が、同じタスクを二重に実行しない
- 駆動プロセスが消えても、確定済みの工程を捨てずに続きから再開できる
- 人の承認待ちが数日から数週間かかっても、その間ワーカー枠を占有しない

### 非目標

- 汎用ジョブスケジューラであること。単位は 1 要求 = 1 run で、cron 的な定期実行は持ちません
- 公平なタスク分配。負荷分散は起動位相のずらしとジッタだけの heuristic です
- 成果物そのものの保管。実体は成果物リポジトリに置き、バスには要約とリンクだけを残します
- 常駐管理。プロセスの起動・監視・再起動は agent-project の常駐体が担います
- タスクの受入基準と done の決定。agent-flow は渡された検証計画を緩めずに実行し、採否は agent-project が決めます

## 主要な設計判断

### 1. 通信はバス上のファイルだけにする

**判断**: プロセス間で交換する情報をすべて、バスのディレクトリ配下の JSON ファイルにする。RPC もソケットも共有メモリも使わない。

**文脈**: 分散の相手は同一 LAN 上の PC とは限らず、WSL とネイティブ Windows が混ざり、片方が落ちている時間帯もある。バスの実体はローカルディレクトリでも共有 git リポジトリでもよい必要があった。

**選択肢と却下理由**: 中央のキューサーバ（Redis / RabbitMQ）は、そのサーバ自身が単一障害点になり、家庭やチームの小規模構成には運用コストが見合わない。gRPC などの直接通信は、ノードが互いの生死とアドレスを知る必要があり、シャットダウン耐性の設計が別途要る。ファイルだけなら、転送層を差し替えるだけで単一マシンと git 分散が同じコードで動く。

**トレードオフ**: 得たのは転送層の差し替え可能性と、状態が全部目で読めることです。失ったのは即時性で、git バスでは pull の間隔ぶんだけ他ノードの結果が遅れて見えます。この判断を見直す引き金は、ノード数が 10 台を超えて git の往復が律速になったときです。

**確信度**: 高い。実運用で 3 台構成まで問題なく回っています。

### 2. 状態はファイルの存在から導く

**判断**: タスクに `status` フィールドを持たせて更新するのをやめ、`node_state(id)` が result / claim / wait / task の各ファイルの有無から状態を毎回導出する。

**文脈**: git バスでは複数ノードが同時に書く。1 つのファイルを皆で書き換えると、内容が競合してマージが必要になり、rebase が壊れる。

**選択肢と却下理由**: 状態フィールドを持って CAS 風に更新する案は、git に compare-and-swap がないため成立しない。イベントログを畳んで状態を作る案は、順序保証のないファイル同期の上では畳み込み結果がノードごとにずれる。ファイルの存在は同期しても曖昧にならず、名前を分ければ add/add コンフリクトも起きない。

**トレードオフ**: 状態を知るたびにディレクトリを読むので、ノード数に比例した stat が走ります。代わりに、書き込み競合が構造的に発生しません。

**確信度**: 高い。派生した規律（誰がどのパスを書くか）が付録 A の表に落ちています。

### 3. 分散ロックは名前空間付き claim と決定的タイブレークで作る

**判断**: claim を取りたいノードは `claims/<node-id>/<who>.json` という自分専用のファイルを書く。勝者は、リース内の全 claim のうち `(ts, who)` が最小の 1 件に決まる。実装は `agentcore.protocol` に置き、agent-amigos の役割 claim と委譲公示板の入札で共有する。

**文脈**: 「先に書いた者が勝ち」を素朴にやると、git バスでは両者が別々のクローンで同時に書けてしまい、pull 後に 2 人とも自分が勝者だと思う。

**選択肢と却下理由**: git の非 fast-forward push が失敗する性質を排他に使う案は、バス全体を 1 本の直列書き込みにしてしまい、ノードが増えるほど push 競合で遅くなる。ファイルロック（flock）は同一ホストでしか効かない。決定的タイブレークなら、同じ claim 集合を見た全ノードが必ず同じ勝者を選ぶので、同期が遅れても結論は 1 つに収束する。

**トレードオフ**: 勝敗が決まるまでに pull を 1 往復挟むので、claim には数百ミリ秒かかります。同一ホスト内の並行 claim は flock で直列化して、読みと判定のずれを潰しています。

**確信度**: 高い。ローカルのベアリポジトリを共有バスにした分散統合テストで、別クローンからの同時 claim でも勝者が 1 人になることを検証しています。

### 4. 常駐デーモンを持たず、受理と実行を別プロセスに割る

**判断**: `agent-flow daemon` を廃止し、参加 1 巡の `participate`（受理だけ・run は起こさない）と、run 1 本を完走させる `run --from-inbox` に分けた。周期駆動は PC に 1 本の `agent-project serve` が持つ。

**文脈**: 以前は agent-flow・agent-project・agent-amigos がそれぞれ常駐しており、同じ PC に 3 つのループが回っていた。設定も生存監視も自動更新も三重で、どれが動いているのか運用者が把握できなくなっていた。

**選択肢と却下理由**: デーモンを残したまま常駐体から起動する案は、三重ループの問題をそのまま残す。逆に、`run --watch` の待機ループに周期駆動を差し込む案は、計測で潰れた。1 パスの所要時間が二峰性で、中央値は 9 秒なのに 21% が 120 秒を超え、最大は 12,141 秒だった。run の生存リース窓は `max(poll×10, 120)` 秒なので、待機ループに tick を相乗りさせるとリースが切れて他ノードに run を奪われる。駆動と待機は同じ制御フローに置けない。

**トレードオフ**: プロセス起動のオーバーヘッドが tick ごとに乗ります。代わりに、agent-flow 側は状態を持たない単発 CLI になり、単体でも `agent-flow run "<要求>"` 1 本で完走できます（run 自身が生存リースを張り、park の監視もします）。`participate` を呼ぶ側は、自分がすでに走らせている run-id を `--running` で渡す必要があります。渡さないと、起動待ちの run を孤児と誤判定して再開回数を焼き潰します。

**確信度**: 高い。ただし移行のとき、旧デーモンのループにぶら下がっていた処理（生存信号の出力、自動アップデート、オンデマンド worker、定期掃除）が呼び出し元を失ったまま残りました。関数単体のテストは通り続けるので気づけません。後述の棚卸しで処置済みです。この形の設計を採るなら、ループを消す作業は「ループから呼ばれていた関数の呼び出し元を数える」作業とセットにする必要があります。

### 5. 人の承認待ちはノードを park してバッチ監視へ移す

**判断**: executor が「まだ決着していない」と判断したら `DeferDecision` を投げる。ワーカーは claim を解放し、`waits/<node>.json` に park 記録を書いて次のタスクへ回る。決着の確認は監視主体（`run` か `participate`）の `service_waits` が `watch_interval`（既定 90 秒）ごとにまとめて行う。

**文脈**: GitLab 委譲では、イシューを立ててから人がレビューして承認するまでに数日から数週間かかる。ワーカーがその間ブロックしていると、worker 枠が全部承認待ちで埋まり、新しい起票が止まる。同時に、N 個のワーカープロセスが各自 30 秒おきに GitLab を叩く多重ポーリングも起きていた。

**選択肢と却下理由**: 承認待ち専用のワーカープールを別に持つ案は、枠の割り当てを人が調整することになり、設定項目が増える。タイムアウトを短くして失敗させ、上位にリトライさせる案は、イシューを二重に起票する。park なら、待機はファイル 1 個になり、ワーカー枠も GitLab の負荷も同時に下がる。

**トレードオフ**: park 記録に生存リース（`wait_lease_until`）が要ります。監視主体が消えるとリースが切れ、`node_state` は `pending` へ縮退して通常のワーカーが同じトークンで再アタッチします。park を行き止まりにしない代わりに、状態が 1 つ増えました。

**確信度**: 高い。

### 6. 失敗は種別で分け、回復する層を種別ごとに固定する

**判断**: 失敗を transient（待てば直る）、内容（作り直しが要る）、環境（この実行場所では確かめられない）の 3 種に分け、種別ごとに回復する層を 1 つに決める。検証結果では `fail` が内容、`inconclusive` が環境に当たる。上の層で吸収した失敗は、下の層の予算（`max_retries` / `max_resumes`）を消費させない。

**文脈**: 以前はすべての失敗が最上位の再計画まで持ち上がり、同じ扱いを受けていた。実際に codex の利用上限で 26 ノードが 1 つずつリトライ予算を焼き尽くし、理由不明の全滅に見えた。接続断も、JSON の書き損じも、成果物の不合格も、全部が同じ「評価役を呼んでタスクを作り直す」経路を通っていた。一番安く直せる失敗に一番高い機構を使っていたことになる。

**選択肢と却下理由**: worker と orchestrator の呼び出し点それぞれにリトライループを置く案は、`run_agent` という単一チョークポイントが既にあるので冗長で、二重ループが予算の掛け算を生む。transient のときに claim を手放して別ワーカーへ任せる案は、他ワーカーも同じ API 障害で落ちるだけで claim の往復コストが増える。環境の不調はノードを替えても直らないので、run 単位で cooldown する方が正しい。failed run を無差別に自動再開する案は、内容の失敗と環境の失敗で LLM を無駄に焼く。

**トレードオフ**: 層が 4 つに増え、最悪時間の上界が伸びます。上界は 1 呼び出しあたり `(1 + transient_retries) × (1 + format_retries) × agent_timeout + Σbackoff` で有界にしてあります（既定で約 1 時間）。transient と `inconclusive` はレイヤ 3 に入らないので、成果修正の予算を減らしません。

**確信度**: 高い。層ごとにテストがあります（`TransientRetryTests` / `FormatRepairTests` / `TransientRunBreakTests` / `AutoHealTests`）。

## 実行の流れ

この節の抽象度は概要です。個々の関数には触れません。

```
    要求 + 任意の verification plan（agent-project / 板 / 人）
        │
        ▼
  inbox/<run-id>.json ──── participate が claim（分散時は 1 台に決まる）
        │                        │
        │                        └─▶ 実行すべき run-id を stdout に返す
        ▼
  run --from-inbox（1 run = 1 プロセス）
        │
        ├─▶ orchestrator … 戦略を選び graph.json を書く。以後は静止を待って評価と再計画
        ├─▶ worker × N  … claim → 実行 → results/<id>.json
        │                        │
        │                        └─ 承認待ちなら park して claim を解放
        └─▶ verifier     … 同じ worktree / revision で基準を調べ、receipt を返す
```

要求はまず `inbox/` に置かれます。`participate` は 1 巡のあいだに、キャンセル指示の受理、park 済みノードの再確認、駆動プロセスが消えた run の引き継ぎ判断、委譲公示板の巡回、そして新しい要求の受理を行い、実行すべき run-id を返します。実行はしません。

板の巡回で「この公示に入札してよいか」を決める規則は `agentcore.board.eligible` に置いてあり、agent-amigos の板参加と共有しています（以前は同じ仕様を両者が別々に実装しており、片方だけ育つと同じ公示が経路によって拾えたり拾えなかったりしました）。判定材料——担当リポジトリ・タグ・使える CLI・引き受けるエンジン・同時実行の枠——の正典は各 PC の `agent-project.host.yaml` で、agent-flow 設定の `board_repos` / `board_tags` / `board_agent_cli` はその明示上書きです。取り込み済みかどうかは**板の `status/<who>.json`** で判断します。自分のバスだけを見ると、同じ PC で 2 つのプロジェクトが同じ板を巡回したときに、片方が取り込んだ直後の公示をもう片方が取り込みます（同一ノードでの二重実行）。板に自分名義の有効な入札がある公示は、担当リポジトリ・タグの照合も枠の抑制も問わず取り込みます——それは人が dashboard から「この端末で引き受ける」を押した意思表示で、自動入札の自己抑制を人が上書きしたということだからです。板の上の名義の綴りは、書きも読みも claim プロトコルと同じサニタイザ（`protocol.safe_name`）で揃えます。かつて入札は claim 側の規則で書き、自分名義の入札の有無はバス側の別規則で読んでいました——正規化済みの node_id なら同値ですが、規則が割れたまま非正規形の名義が入ると、手動入札の受け皿が「押しても何も起きない」形で永久に効かなくなります。

判定の向きは項目によって逆です。公示が「要る」と言う条件（タグ・CLI・契約バージョン）は宣言の欠落を「無い」と読んで**入札しない**側に倒し（fail-close）、ノードが「これしかやらない」と言う条件（`workloads`）は宣言の欠落を「制限しない」と読みます（fail-open）。契約バージョンの照合は完全一致で、要求を載せていない公示は不問です。版を上げた回にだけ古いノードが外れる形にしておかないと、契約に項目を足しただけで板に残っている公示が一斉に入札不能になります。要求を無視すると拾えないノードが拾い、制限を強制すると宣言していないノードが全部止まる——安全な倒し方が逆だからです。枠（`budget.max_concurrent`）の自己抑制は、板の上の自分名義の非終端 `status/` を数えて判断します。プロセス内のカウンタでも自分のバスでもなく板を見るのは、同じ PC で 2 つのプロジェクトが同じ板を巡回する構成があるためで、`0` は板の契約どおり**無制限**です。1 巡の中で枠を数え直さないよう、件数は巡回の頭で 1 度だけ数え、落札するたびに減らします。終端の読みだけは寛容にします——板には語彙統一より前のノードが書いた旧綴り（`canceled`）が残りうるので、それも終端として数えます。読みは寛容・書きは正典のみ、という向きです。

呼び出し側（通常は `agent-project serve` のワーカープール）が、返ってきた run-id ごとに `run --from-inbox` を起こします。要求文・書込先ワークスペース・参照リポジトリ・引き継ぎ元は、呼び出し側が argv へ転記するのではなく、この `run` が inbox 要求から自分で読みます。転記させると項目が増えるたびに常駐体側を直す必要が出て、抜けたぶんだけ静かに機能が落ちるからです。プロジェクトを 1 つも持たない PC でも形は同じで、この 1 巡がプロジェクトではなくノードのスコープで回り、取り込み先が PC に 1 つのバス（`~/.agents/flow-node/bus`）になります。違うのは、板の所在と入札選別の宣言を常駐体が argv で明示的に渡す点だけです——その PC には agent-flow の設定ファイルもプロジェクト設定も無いので、宣言を持っている側から渡します。

`run` は orchestrator 1 本と worker を `workers` 個（既定 2）起こし、自分は待機ループに入ります。待機ループがやるのは、状態 git の同期、park の再確認、キャンセル指示の検知、そして run が終端に達したかの確認です。

run の終端 `status` とグラフ上の進捗とは別に、現在段階を `meta.json` の `phase`（`planning` / `executing` / `evaluating` / `verifying` / `finalizing`）と `phase_started_at` で持ち、遷移を event にも残します。作業ノードが全件終わった後の検証や結果確定を「100%・実行中」に潰さないためです。古い run のように phase が無い場合や未知値は、status とグラフから汎用表示へ縮退します。

orchestrator は最初にパターンと並列数を選んでタスクグラフを書き、あとは run が静止するのを待ちます。静止とは、実行中のノードも、park 中のノードも、いま claim できる pending もない状態です。静止したら評価役の LLM に「この結果で要求を満たすか」を尋ね、足りなければタスクを追加してもう一周します。反復は `max_iterations`（既定 3）で止まります。

計画には planner（LLM / stub / flow-planner）を通らない第 3 の経路があります——**ユーザー定義フロー**です。inbox 要求の `plan` フィールド（または `--plan-file`）にノード列（`{name?, evaluate?, nodes:[{id, goal, deps, kind, agent?, ...}]}`）が載っていれば、orchestrator は planner を呼ばず、検証（`plan_strategy_user`）だけでそのグラフを固定します。検証は planner 出力の防御（未知 kind の丸め・循環の切断）とは逆に**厳格に失敗させます**——丸めて実行すると「意図と違う形で走った」ことに気付けないからです。不正な plan は planner へフォールバックせず、`[user-plan]` タグ付きの failure_reason で失敗終端します。per-node の `agent`（`{agent_cli, model}`）はこの経路でだけ受けます（LLM planner の出力からは従来どおり剥がします——モデル選定はルーティングと実測格付けの仕事です）。goal 中の `{{request}}` は要求テキストへ置換され、保存済みフローを入力だけ変えて使い回す口になります。既定では評価役の再計画も無効です（形が意図そのもの。失敗ノードは正直に failed で返し、resume が失敗ノードを pending へ戻します）。`evaluate: true` で従来の継続判断に載ります。主な投入元は agent-dashboard のクイック実行（フロービルダー）です。

計画の履歴は最終形の `graph.json` から推測せず、イベントへ差分として残します。初期計画は
`planned.tasks`、以後の `replan` と実行中の人の指摘を反映する `inflight_amend` は、理由と
`changes`（`added` / `replaced` / `updated` / `removed`）を記録します。`graph.json` の `deps` は
実行上の依存関係、イベントの差分は計画が変わった時系列の境界で、同じ意味ではありません。
`added` / `updated` / `removed` はノード ID の列、`replaced` は `{old, next}` の列です。
依存のない追加工程へ再計画を理由に依存線を足さず、利用側はこの二つの事実を分けて表示します。

書込 workspace に作業ブランチと別の target がある場合は、planner の計画とは別に system node
`base-sync` を先頭へ置き、すべての root node をその完了後に開始します。target がすでに作業ブランチの
祖先なら no-op、進んでいれば agent-flow が通常 merge を行います。競合時だけ worker に競合ファイルの
編集を任せますが、履歴操作は渡しません。競合解消または祖先性の検査に失敗した run は通常の成果修正へ
再計画せず、integration failure として終端します。fetch 失敗は transient のままです。

worker は claim できるノードを 1 つ取り、kind に応じたプロンプトでエージェント CLI を呼び、結果を書きます。実行中は心拍が claim のリースを延ばし続けるので、長いタスクでも横取りされません。

`$AGENT_TUNING_DIR/tuning.json` に `methods` / `trials` があれば、基礎スキルで組み立てた最終
プロンプトへ role 別の追補指示を足します。資源段と agent CLI の相対コストを含む `when` は
決定的に評価し、同じ task の `-rN` 再試行は 2 variant を交互に割り当てます。適用した手法と
variant は graph strategy、node result、node-budget 台帳へ残し、効果判定は agent-audit に任せます。

**variant を名乗るのは、その variant の手法を実際に注入できたときだけ**です。variant が挙げた
id が `methods[]` へ複製されていない（カタログにあるだけ）、`when` が合わない、role が違う、
のいずれかで 1 つも効かなかった実行は trial として記録しません。宣言だけで名乗ると、何も
足していない実行がその variant の証拠として集計され、比較が「効かなかった」ではなく
「測っていない」を測ることになります。

verification plan を持つ run は、成果 revision が確定したあと専用 verifier を 1 セッション起動します。
固定検証コマンドは書き換えずに実行します。自然文の criterion は、verifier がコマンド、差分、
ファイル、ログを調べて `pass` / `fail` / `inconclusive` と証跡を返します。verifier は基準を
緩めず、成果物も変更しません（検証後に作業ツリーの変更を破棄する）。`fail` は同じ run の
修正ループへ戻します——不合格点を列挙した work ノード（`verify-fix-<n>`）を決定的に注入して
再度静止を待ち、`max_iterations` で有界。`inconclusive` は成果修正のリトライを消費せず
receipt のまま上位へ返します。コマンドの終了コード非 0 は fail、起動できない（実行場所が無い・
exit 127 = コマンド不在）は inconclusive で、成果物の欠陥と環境の欠落を混同しません。
結果は `runs/<run-id>/receipt.json` に書き、同じ plan digest × 同じ成果 revision の receipt が
既にあれば再実行しません（command 実行は一回だけ）。壊れた plan（digest 不一致・未知版）は
実行せず receipt も書きません——receipt 欠落を採用側が done にしない fail-close に倒します。
書込 workspace の plan は target も固定し、receipt には検証時の target revision と統合判定を載せます。
target revision が成果 revision の祖先でない限り integration は pass になりません。ブランチ単体の
検証が通っていても、最新 target を含まない成果を採用させないためです。この祖先性判定は
`agentcore.verifycontract` に置き、receipt が無い場合の agent-project local runner も同じ実装を使います。
検証経路が変わっても integration の有無や合否を変えません。
plan は `--verification-plan`（グローバル引数）または inbox 要求の `verification_plan` キーで
受け取ります。argv 未指定のときだけ inbox の値で充填する規則で、呼び出し側に argv への
転記をさせない設計です。両方指定されていれば CLI 引数が勝ちます（env 渡しは不安定として
却下・2026-07-31）。

自然文基準を判定するエージェントは、plan の `policy.agent`（`agent_cli` / `model` / `timeout_sec`）をタスク 1 件だけの明示指定として使い、ノード設定と agent-control より優先します。検証条件は plan digest の一部なので、条件を変えると古い receipt は再利用されません。実際に使った条件と所要時間は receipt の `verified_with` に残し、同じ条件で粘るか別条件へ移るかを上位が記録から決められるようにします。

実行場所は workspace 宣言のある run なら該当 repo の clone、無い run（ローカル実行・成果は
投入ノードの作業ツリーに直接出る）ならプロセスの cwd です。workspace 宣言があるのに clone を
用意できなかった run は cwd に倒さず inconclusive にします（成果の無い場所で誤判定しない）。
固定コマンドには差分基準の環境変数 `$AGENT_BASE_REV` を渡します——clone では成果 HEAD、cwd では
run 投入時に meta に固定した `base_rev`（act 前 HEAD）。plan の policy `confirm` が 1 より
大きければ同じコマンドを最大 confirm 回実行し、PASS/FAIL を跨いだら flaky を立てます（flaky な
pass は receipt の全体判定で fail に落ち、採用側が人へ隔離します）。コマンド実行のセマンティクスは
`agentcore.verifycontract.run_plan_command` の 1 実装で、agent-project の local runner
（receipt を返せない run の受け皿）と共有します。

## コンポーネント

この節の抽象度はコンポーネントです。責務と境界だけを書き、行数レベルの実装には踏み込みません。

### バスと転送層

`Bus` はローカルディレクトリ実装で、`sync_pull` / `sync_push` は何もしません。`GitBus` はこれを継承し、ノードごとの専用クローンで pull と push を行います。実際の git 転送（クローン、ロック残骸からの回復、中断した rebase の後始末、電源断で壊れたオブジェクトの検知と作り直し、durable-write 設定）は `agentcore.transport.GitTransport` が唯一の実装として持ち、agent-project の板リポジトリ操作と共有しています。

バスとは別に、状態の鏡だけを共有する `state_git` があります。実行はローカルのまま、`runs/` と `inbox/` を共有リポジトリの自分の subdir へ双方向同期し、リモートの agent-dashboard が進捗を読めるようにします。同期は前回スナップショット（manifest）基準の 3-way で、同時に変わったファイルだけを裁定します。人の投入口である `inbox/` はリモート優先、機械が書く `runs/` はローカル優先という決定的な規則です。run の実行と終端は state_git に一切依存せず、同期の失敗はログに残して続行します。

### claim と park

claim は §3 のとおりです。park は claim と同じリース意味論に相乗りしていて、`wait_lease_until` が切れれば `node_state` は `pending` に縮退します。park 記録の書き込みと claim の解放には順序があり、必ず記録を先に書きます。逆にすると、その隙間で死んだときに wait を失います。

### パターンカタログ

orchestrator は 7 つのパターンをカタログとして持ちます。最初の 6 つは Claude Dynamic Workflows の記事に載っていたもので、`map-reduce` は agent-flow が足した 7 つ目です。パターンは組み合わせられます。詳細は付録 D にまとめました。

計画役は 3 系統あります。既定の `flow-planner` はスキル側の 3 段パイプライン（分析 → 戦略選定 → グラフ構築）を呼びます。スキル名は設定 `planner_skill`（既定 `flow-planner`）で差し替えられます。スキルが見つからなければ `agent`（エージェント CLI に 1 回問い合わせる）へ、それも解釈できなければ `stub`（キーワード判定と正規表現）へ落ちます。落ちる先があるので、スキル未導入のノードが混ざっても run は成立します。

LLM planner が選んだ主パターンと並列数は、同じ入力に対する `stub` 系の決定的ルールと照合し、`strategy.decision_comparisons` に記録します。ルール側へ渡すのは要求文と**呼び出し時の granularity 引数**だけで、分析段が導出した粒度は渡しません——LLM の出力をルールの入力に混ぜると一致率が上振れし、「LLM に聞くのをやめてよい判断」の選定がその分だけ甘くなります。これは S16 の対象選定用計測であり、実行する戦略には介入しません。flow-bus 収集後に agent-audit が判断ごとの一致率を集計し、置換は後続段階で別途判断します。

縮退は静かに起きてはいけません。落ちた事実と理由はログと `strategy.reason` の両方に残します。以前はここが黙っていたため、planner の CLI を差し替えた環境でスキルが一度も起動していないのに「計画できた」ように見え、stub のキーワード判定が同じパターンを選び続けた run が 4 本続きました。スキルはエージェント CLI の argv を自前で組まず agentcore の定義（`agents/<name>.json`）へ委譲します——組み込み 4 種の白リストを持つと、定義を足しただけの CLI（`ollama` 等）で計画役だけが起動に失敗します。そのキーワード判定も、要求本体（先頭の段落）だけを見ます。agent-project 由来の要求には charter・対象リポジトリ一覧といった定型が付き、そこに含まれる語（「書込先候補」など）が要求の中身と無関係に同じパターンを選ばせるためです。

ノードの粒度は運任せにしません。`granularity` の既定は `auto` で、分析段の複雑さ判定から目標粒度を決定的に導出します（simple → coarse・work 系 1〜3 ノード / moderate → fine・3〜8 / complex → finest・6〜12、ハード上限 16）。`--granularity` の明示指定だけが導出に勝ちます。成果を生むノードの goal には、触ってよい範囲（`[scope]`）とやらないこと（`[out_of_scope]`）を先頭の構造化ブロックで書かせるスコープ契約を課し、生成後は LLM を使わない決定的ゲートで検査します——work 系ノード数が目標レンジ外、goal にスコープ相当が無い、goal 同士が似すぎている、のいずれかに当たれば指示を強めて 1 回だけ再生成します。検証・統合・ルーティング役（verify / synthesize / reduce / filter / judge / classify / split）はスコープ上限の対象外です。

この形に落ち着いた経緯を書いておきます。戦略選定とグラフ構造には満足できていて、粒度のばらつきの主因は「通常の約 N 倍に細分化」という相対指示と、既定が常に finest だったことにありました。だから直すのは指示の側です。複雑度から目標レンジを決定的に導出し、絶対レンジとスコープ契約をプロンプトに埋め、結果を LLM なしのゲートで検査する。別 LLM に分解を批評させる案はコストと出力の揺れで、初回は粗く作って失敗したら細分化する案は初手の失敗が増えるので、どちらも却下しました（いずれもこのゲートの後段に足せる形は残しています）。ゲートが completion verifier の有無を検査しないのは意図的で、内側ノードの偽完了は flow-worker の規律と agent-project から渡された verification plan で検出します。work ノードの手戻りやスコープ逸脱が減らないようなら、分解批評か内側 verify 契約を再検討します。

### executor プラグイン

executor はタスクを実際に実行するバックエンドです。組み込みは `agent`（エージェント CLI に委譲）と `stub`（LLM なしの擬似実行）で、それ以外は `executors/<name>.py` を動的ロードします。プラグインは `execute(kind, goal, dep_results, model, art_dir, dep_arts)` を公開し `(text, data)` を返します。追加の引数（`workspace` / `references` / `request` / `instructions` / `repo_instruction`）は、シグネチャを調べて受け取れるプラグインにだけ渡します。受け取れない古いプラグインには、指示を goal の先頭へ結合する後方互換の経路が残っています。

同梱の `gitlab` プラグインは、各タスクを GitLab イシューにして委譲し、`status:approved` が付いてクリーンな MR があれば自動でマージしてイシューを閉じます。プラグイン固有の設定は同名の設定ブロック（`gitlab:`）を JSON 化し、環境変数 `AGENT_FLOW_EXECUTOR_CONFIG` で渡します。

再計画の判断だけは executor に委譲しません。`stub` のときだけ stub の継続ルールを使い、それ以外はローカルのエージェント CLI で判断します。プラグインに委ねるのはワーカータスクの実行だけで、メタ評価は手元に残す、という線引きです。

### ワークスペース

1 つの run が書き込んでよいリポジトリはちょうど 1 つです。worker は temp 領域に作業ツリーを用意し、作業ブランチ（既定 `af/<run-id>`、spec で `branch` を明示すればそちら）を base から作ってエージェントに渡します。エージェントは編集だけを行い、commit と push は agent-flow が行います。変更がなければ何も push しません。調査だけのグラフでブランチを作らないためです。

`base-sync` の競合解消も同じ所有境界に従います。worker が編集を終えた後、制御層が未解決 index、
競合マーカー、target の祖先性を検査し、成功時だけ merge commit と push を行います。ここで見るのは
競合マーカーであり、target から入った既存の末尾空白は競合として扱いません。通常 work node では
従来どおり staged diff の空白エラーも拒否し、新しく持ち込む差分の品質を落としません。通常の work / generate node も、
最終出力の構造化 envelope が `{"ok": false}` なら `done` にせず失敗として扱います。

作業ツリーは、URL 単位のホスト共有 bare ミラーから detached worktree を生やして用意します。フルクローンを初回 1 回と増分 fetch に圧縮し、GitLab 側の pack 生成負荷を抑えるためです。手元に同じリポジトリのクローンがあればそこから worktree を切り、ネットワークすら使いません。どちらも失敗したら従来の direct clone に落ちます。「手元のクローン」の宣言は各 PC の `agent-project.host.yaml` の `repos[]`（URL とローカルパスの対）が正典です。共有レジストリ repos.json にホスト固有の絶対パスを書くと状態同期で全 PC へ配られてしまうため、そこには書けません（残っていれば警告して無視）。URL の同一性判定は `agentcore.repolocal` の 1 実装に揃えてあり、板経由で請け負った仕事も submit 前に自ノードの宣言をマージするので、同じ最適化が効きます。

書込先を決めるのは agent-flow ではなく agent-project です。agent-flow は渡されたワークスペースを厳格に守る側に徹します。読むだけのリポジトリは `--reference` で渡し、clone せずプロンプトとイシュー本文に参照節として描画します。

タスク完了の検証もこの worktree で行います。push 後に別 checkout で再実行すると、cwd、依存物、
成果 revision のいずれかがずれます。receipt には plan digest と result revision を必ず書き、
agent-project が採用対象と照合できるようにします。

### 失敗の 4 層と、それぞれが拾うもの

| 層 | 実体 | 拾う失敗 | 予算 |
|---|---|---|---|
| 1. in-place 再試行 | `run_agent` の中 | transient（接続断・5xx・overloaded・タイムアウト） | `transient_retries`（既定 2・指数バックオフ） |
| 2. 形式修復 | `_repair_json_output` / `run_agent` の空応答再要求 | 出力契約違反（split の配列・decision JSON の崩れ・JSON 契約の役割の空応答） | `format_retries`（既定 1） |
| 3. 再計画 | 評価役の継続判断 | 内容の失敗（criterion=`fail`、成果物が要求を満たさない） | `max_retries`（既定 3・系統ごと） |
| 4. auto-heal | `run` の待機ループと `participate` | transient 起因で failed 終端した run | `max_heals`（既定 2・進捗で数え直し） |

層が分かれているのは、直し方が違うからです。接続が一瞬切れただけならその場でもう一度呼べば済み、グラフを触る必要はありません。LLM が JSON を書き損じたなら、契約違反を指摘して同じ役割で呼び直せばほぼ直ります。成果物が要求を満たさないなら、同じ入力の再実行では直らないので作り直しと付け替えが要ります。

レイヤ 3 の最初の内容再試行では、`agents.<purpose>.fallbacks` に宣言された候補のうち、現在より `relative_cost` が厳密に大きい最初の 1 件へだけ昇格できます。再試行済みノードは同じ先を引き継ぎ、環境要因や形式修復には適用しません。昇格元・先と係数は result と budget ledger に残すため、最初から上位へ流した場合との実効単価を後から比較できます。**ledger 側は消費ではなく観測行（`event: model_escalation`・秒もトークンも 0）として書き、集計はこれを消費にも実行回数にも数えません**——数えると、実効単価を測るために書いた行が実効単価を下げてしまいます。

worker の入力も契約で絞ります。task の `read_allocation` は最初に読む path/range を指定し、worker の自己報告から的中率と割付外読込を result に残します。依存成果は要約・成果物参照・省略量だけの digest に畳みます。ただし**依存成果そのものが判断の対象である役割（verify / reduce / synthesize / judge / filter）は既定で全文**です——要約を渡すと判断の対象が消えるためで、とりわけ verify が 600 字の要約で pass/fail を決める形は品質ゲートとして成立しません。work / generate でも完全な構造化データが不可欠なノードは `dependency_input: full` を宣言できます。逆に、要約で足りると分かっている判断役は `dependency_input: digest` で既定を下ろせます（明示宣言は kind による既定より強い）。省略量は `dependency_context.saved_chars` として台帳へ収集します。

レイヤ 2 が拾う契約違反には**空応答**も入ります。ローカルモデルやツールループ型の CLI は、本文の代わりに制御語だけを返して終わることがあります（`agent-ollama` が `TASK_COMPLETE` だけを出す、など）。これはパースの手前で落ちるので `_repair_json_output` には届かず、以前は 1 発で内容の失敗として扱われ、再計画の予算だけが焼けていました。JSON 契約の役割（`JSON_CONTRACT_ROLES`）に限り、`run_agent` が空応答を形式違反として拾い、契約を言い直して同じ予算枠で呼び直します。それでも空のときと、自由記述が成果の役割（`work` など）の空応答は、既知の認証・quota 等の分類が無ければ transient として run 単位で打ち切り、done を温存する auto-heal へ渡します。同じプロンプトの即時再試行は、遅いローカル LLM の壁時計だけを焼くため行いません。

不変条件は 2 つです。上の層で吸収した失敗は下の層の予算を消費しません。逆に、レイヤ 1 で回収し切れなかった transient はノード単位で粘らず、run 単位で打ち切ってレイヤ 4 へ渡します。環境がまだ不調なら他のノードも同じ理由で落ちるからです。

環境要因（認証切れ、利用上限、CLI 不在、管理面による停止）はどの層でも再試行しません。1 ノードでもそのタグが立った時点で run を failed で終端し、人が環境を直してから再開します。quota は node-budget 台帳へ観測として残し、時限の `rate_limit` は復帰時刻まで、累積枠の `exhausted` は次周期まで管理面の候補から外します。利用上限だけは時間が直すので、`heal_quota` を立てれば長い cooldown（既定 1 時間）でレイヤ 4 が拾います。

auto-heal の簿記（`heal_count` / `heal_progress` / `heal_next_at` / `heal_exhausted`）は run の meta に閉じています。人の明示 retry はこれを白紙に戻し、auto-heal は戻さずに heal 横断で「進捗なしの連続回数」を数えます。前回の heal 以降に done ノードが増えていれば 1 から数え直すので、前進している run は何度でも回収され、進捗ゼロのまま失敗し続ける run だけが上限に達します。

消費者（agent-project）は failed run を見ると新世代の run を作るので、auto-heal と二重に回復しうる関係にあります。裁定は既存の機構が持ちます。次世代に引き継がれた旧 run は `superseded` が立ち、auto-heal はそれを対象から外します。逆に heal が先に動いて run が running に戻れば、消費者側の引き継ぎは「実行中でリースが有効な run は触らない」安全条件で何もしません。どちらの経路も done を温存する冪等な操作なので、最悪でも重複実行であって破壊は起きません。

### リトライの世代交代（inherit_from）

criterion の `fail` はまず同じ run の再計画で直します。計画を変えない失敗やタイムアウトを別の
run-id でやり直す場合は、先行 run の確定済み成果を引き継げます。一方、人が基準や指示を変更した
新 run は done node を引き継ぎません。古い前提で完了した工程を新しい計画へ混ぜないためです。

計画を変えない再試行でも、素朴に新 run を作ると、
先行 run が確定させたノード結果も、計画も、中間成果物も、作業ブランチの commit も全部捨てる
ことになります。GitLab 委譲のような長時間の run では、これはトークンと人手の空費です。

`Bus.inherit_from(old_run_id)` が「引き継いでから掃除する」1 プリミティブになっています。引き継ぐのは計画（`graph.json`）、ノード仕様（`tasks/`）、中間成果物（`artifacts/`、node-id で決定的にアドレスされるので新 run でも同じパスで見つかる）、そして status が done のノードの結果だけです。failed はやり直させます。claim と events は引き継ぎません。wall-clock のリースや孤児判定を汚染するからです。

作業ブランチは連鎖させます。新 run の spec の `base` を旧ブランチ `af/<old-run-id>` に差し替えるので、確定済みノードの commit を失いません。旧ブランチが無ければ clone 側が既定へフォールバックします。

要求文は新世代のものを正にします。以前は旧 run の request をそのままコピーしていたため、リトライの引き金になった差し戻しの指摘が再実行ノードに届いていませんでした。worker は meta.request を全体文脈として読むので、ここが古いと同じ失敗を繰り返します。

書込先と検証計画も再投入のたびに正へ寄せます。worker と検証 runner は meta しか読まないため、作成時に workspace ルーティングが決まらないまま run が生まれると、以後の再開で `--workspace` を渡し続けても永久に read-only（成果ブランチが push されない）でした。`ensure_run` は既存 meta に対して、workspace が無ければ今回の投入値で補い（既存 spec の差し替えはしません。inherit が旧ブランチへ差した base を壊さないため）、`verification_plan` は最新の投入正本へ更新します。settle は常に今の正本と検算するので、古い plan のままだと receipt が fail-close で捨てられ続けます。

安全条件は、先行 run が終端しているか孤児（生存リース切れ）のときだけ触る、です。実行中でリースが有効な run には seed も削除もしません。人が cancel した run も触りません。停止の意思を尊重しないと、cancel 後のリトライが cancelled 行を蘇らせます。先行 run が完全に done（全ノード確定後も criterion が fail）なら状態は引き継がず掃除だけ行います。同じ出力で即 done になり、また fail になる無限ループを避けるためです。

削除の前に墓標（`inherited/<旧 run-id>.json`）を残します。meta、計画、final、全ノードの結果（出力は抜粋）、成果物のファイル名を書き出し、前の世代が持っていた墓標も引き継ぎます。これが無いと、完走後の criterion fail でリトライされた run の記録がバスから即座に消え、viewer がその瞬間にポーリングしていない限り二度と見られません。

auto-heal はこの世代交代を使いません。heal は同一 run の再開なので、要求文は投入時のまま変わりません。これは仕様です。知識の更新を伴わないやり直し（transient の回復）が heal、知識の更新を伴うやり直し（差し戻しや criterion fail）が世代交代、という分担です。

## プロセスと責務の対応

この節の抽象度は実装です。コマンドと関数の対応だけを載せます。

| コマンド | 入口 | 責務 |
|---|---|---|
| `run` | `cmd_run` | orchestrator と worker を起こし、生存リース、park 監視、キャンセル検知、auto-heal を回す |
| `orchestrate` | `cmd_orchestrate` | 計画、静止待ち、評価と再計画、成果確定後の verifier、`final.json` の書き出し |
| `work` | `cmd_work` | claim、実行、park、結果の書き込み |
| `participate` | `cmd_participate` | 受理と回収の 1 巡。実行すべき run-id を返す |
| `cancel` | `cmd_cancel` | キャンセルマーカーの投函と即時終端化 |
| `status` / `result` | `cmd_status` / `cmd_result` | 進捗の表示 / 最終成果の全文 |
| `gc` / `cleanup` | `cmd_gc` / `cmd_cleanup` | 古い run の削除 / バス外の一時ファイルの掃除 |
| `doctor` / `update` | `cmd_doctor` / `cmd_update` | 稼働診断 / 自己更新 |

生存リースは orchestrator 自身が張ります。`heartbeat()` がリース窓の 1/3 ごとに `meta.json` を書いて push します。通常の監視ループだけでなく、計画、静止後の LLM 評価、verification plan の固定コマンドと自然文基準判定のようにメインスレッドが長く塞がる区間も、同じ間隔の別スレッドが更新します。計画だけを守ると、既定 120 秒のリースより長い評価・検証を孤児と誤認するためです。区間を抜けるときは実行中の heartbeat が終わるまで待ち、古い更新を次の状態へ持ち越しません。git バスでは、書き換えたまま未コミットで残すと `pull --rebase` が dirty な作業ツリーで失敗し続け、他ノードの結果を永久に取り込めなくなります。心拍が push まで含めて 1 単位なのはそのためです。cancel マーカーは外部の適用側では消さず、実行所有者が停止を確認してから消します。heartbeat と cancel が同じ古い `meta.json` から競合しても、残った停止意図を再適用して `cancelled` へ収束させるためです。

## 常駐デーモン廃止で拾い直したもの（2026-07-26）

`agent-flow daemon` を消したとき、旧 daemon ループにぶら下がっていた処理が呼び出し元を失ったまま残っていました。関数単体のテストは通っていたので、経路が死んでいることに気づけていません。以下は棚卸しと処置です。

**自動アップデートは `participate` のアイドル巡回へ移した。** 確認（`git ls-remote`）だけを巡回の中で同期に行い、取り込み（clone と install.sh）は切り離した子プロセス（`agent-flow update --now`）へ渡します。参加巡回は呼び出し側が 120 秒で kill するので、その中で installer を回すと本体が半分だけ入れ替わりかねません。アイドルの定義は「受理する要求も引き継ぐ孤児も無かった巡回」です。更新後の `os.execv` による自己再起動は削除しました。更新を跨いで生き続けるプロセスがもう無く、次の起動が新しい本体を使うからです。

**生存信号 `<bus>/status.json` は実装ごと削除した。** 稼働判定は agent-project の常駐体が書く `engine/status.json` に一本化されており、agent-dashboard もそちらだけを読みます。設定 `status_interval` も併せて削除しました。run が生きているかは、鏡写しされた `meta.json` の生存リースで判定できます。

**オンデマンド worker（`_spawn_worker`）と `max_workers` は削除した。** worker は `run` が run ごとに `--workers` 個だけ起こします。executor 設定を子へ届ける仕組み（`resolve_executor_config_json`）は `make_executor` 側に残っています。

**定期掃除の設定 `cleanup_interval` は削除した。** 掃除は `agent-flow cleanup` の単発で、周期は agent-project の gc tick が持ちます。

**park 中のノードを `status` に出るようにした。** 表示のグリフと集計順に `waiting` が無く、全ノードが承認待ちの run が「進捗 0/N・実行中ゼロ」としか見えていませんでした。止まっているのか待っているのかを画面から区別できないのは、park & poll を主要機能として売っている以上まずい欠落です。

**更新状態ファイルの置き場を共通ホームに揃えた。** ここだけ旧ホーム `~/.agent` を直書きしており、新ホームしかない環境で `~/.agent/` を新しく作って書いていました。現在は `~/.agents/agent-flow.update.json` だけを読み書きします。

**auto-heal の再起動が検証 gate 設定を引き継ぐようにした。** 初回起動は `--review` / `--no-review` を子へ渡すのに、heal で起こし直す側が渡していませんでした。

残した既知の窓が 1 つあります。`participate` が要求を受理した直後は run がまだ作られておらず、生存リースを張れません。この間に呼び出し側が落ちると、その要求は inbox claim のリース（`--lease`・既定 1800 秒）が失効するまで誰も拾い直しません。リースを短くするとワーカープールで起動待ちの run を別ノードが二重に拾うので、この窓は意図的に縮めていません。

## 付録

### A. バスのファイルレイアウトと書き込み所有権

```
<bus>/
  inbox/<run-id>.json               要求（request / workspace / references / inherit_from / delegation / verification_plan / plan）
  inbox/claims/<run-id>/<who>.json  受理の claim
  inbox/cancels/<run-id>.json       キャンセルマーカー
  runs/<run-id>/
    meta.json          request・status・workspace・references・instructions・リース簿記
    graph.json         strategy + nodes{id: {goal, deps, kind, retries}} + iteration
    tasks/<id>.json    ノード仕様
    claims/<id>/<who>.json
    waits/<id>.json    park 記録（承認待ち。秘密は載せない）
    results/<id>.json  成果（output / data / artifacts / who / node / status。
                       agent executor では実行に使った agent_cli / model も）
    artifacts/<id>/    中間成果物のファイル
    events/<who>.jsonl 追記専用ログ
    final.json         全結果のサマリ
    receipt.json       統一 verify の receipt（verification-receipt.schema.json）
    inherited/<旧 run-id>.json  リトライで消した先行 run の墓標
```

| パス | 書く人 |
|---|---|
| `meta.json` / `graph.json` / `tasks/*` | orchestrator のみ |
| `claims/<id>/<who>.json` | claim を試みる各ワーカー（ファイル名が衝突しない） |
| `results/<id>.json` | claim に勝ったワーカー、または park を決着させた `service_waits` |
| `receipt.json` | orchestrator（成果確定後の専用 verifier セッション）のみ |
| `waits/<id>.json` | park したワーカーと、それを再確認する監視主体 |
| `events/<who>.jsonl` | 各ノードが自分のファイルにだけ追記 |

`node_state` はこの表から導出されます。優先順位は result（終端）、生存リース内の claim、生存リース内の wait、`tasks/` があれば pending、なければ unknown です。

**`<who>` には PC 名が入ります**（`<node_id>-w<i>`、auto-heal の世代は `<node_id>-h<n>w<i>`。綴りは `agentcore.protocol.safe_name` の規則）。2026-07-27 までは `worker-<i>` 固定で、共有バスに 2 台が参加すると両者が `claims/<id>/worker-1.json` と `events/worker-1.jsonl` という同一パスへ書きました——この表の「ファイル名が衝突しない」という不変条件そのものの破れです。あわせて **`results/<id>.json` には実行した PC を `node` として書きます**（`agent-flow status` の `by pc` 行・doctor の signals・dashboard の run 詳細はこのフィールドを読む。読み手が `who` の綴りを割って PC を当てにいくと、名義の作り方の 2 実装目になるため）。同じ理由で、agent executor は**実行に使ったエージェントを `agent_cli` / `model` として claimed イベントと result に書きます**——実効解決（`_agent_for`: control 上書き・縮退込み）は実行時にしか分からず、読み手（dashboard のノード詳細）に設定からの再解決をさせないためです。

### B. サブコマンドと主なオプション

| コマンド | 用途 |
|---|---|
| `run [要求]` | 単発実行。既存 run-id なら再開、なければ新規。`--from-inbox` で要求を inbox から読む |
| `participate` | 受理と回収の 1 巡。`--running` に自分が走らせている run-id を必ず渡す |
| `cancel <run-id>` | 恒久停止。`--close-issues` で起票済みイシューも後始末 |
| `status` / `result` | 進捗ダッシュボード（`--follow`） / 最終成果（`--json`） |
| `gc` / `cleanup` | 古い run と孤児 inbox の削除 / バス外の一時ファイルの掃除 |
| `doctor` | 稼働診断。所見を env / config / program に分類（`--fix` / `--json`） |
| `update` | スキルリポジトリからの自己更新（`--check` / `--now`） |
| `orchestrate` / `work` | 内部コマンド。`run` が起こす |

グローバル引数は `--bus`、`--git` / `--git-branch` / `--git-subdir`、`--state-git` 系、`--board`、`--workspace`、`--reference`、`--agent-cli`、`--granularity`、`--lease`、`--config` です。サブコマンドを省略すると案内を出して終了します。裸起動を黙って常駐にすると、常駐体と二重に回って inbox の要求を奪い合うためです。

### C. 設定ファイル

探索順は `--config` の明示指定、カレントディレクトリ直下、`./.agents/`、`./.agent/`、`~/.agents/` の順で、ファイル名は `agent-flow.{yaml,yml,json}` です。カレント直下を最優先にするのは、1 root = 1 プロジェクト構成でこのファイルがプロジェクトの発見マーカーを兼ねるためです。優先順位は CLI 引数、設定ファイル、組み込み既定の順。PyYAML がなければ JSON で同じキーが使えます。

キーの一覧と既定値は `CONFIG_DEFAULTS`（`agent_flow/config.py`）が正典で、注釈つきの実例は `tools/agent-flow/agent-flow.yaml.example` にあります。役割ごとにエージェント CLI とモデルを差し替える `agents:` は yaml 専用で、キーは `planner` / `evaluator` / `worker`（全 kind の既定）と個別の kind です。

### D. パターンと kind

| パターン | 形 | 使いどころ |
|---|---|---|
| classify-and-act | `classify` → 結果に応じた `work` を追加 | 種別を判定して専門処理へ振り分ける |
| fan-out-and-synthesize | 並列 `work`/`generate` × N → `synthesize` | 分割して並列処理し統合する |
| adversarial-verification | `generate` → `verify`（fail なら作り直し） | 成果を批判的に検証する |
| generate-and-filter | `generate` × N → `filter` | 候補を多数出して絞り込む |
| tournament | `generate` × N → `judge` | 複数案から最良を選ぶ |
| loop-until-done | `work` → `verify` を条件達成まで反復 | テスト通過や品質達成まで繰り返す |
| map-reduce | `split` → 実行時に `map` × N を展開 → `reduce` | 件数を事前に固定せずデータ駆動で並列処理する |

kind は `work` / `generate` / `classify` / `synthesize` / `verify` / `filter` / `judge` / `reduce` / `split` / `map` の 10 種です。planner が未知の kind を出したら `work` に丸めます。`kind: verify` は run 内の候補比較や反復を制御する工程で、agent-project の verification plan を判定する専用 verifier とは別です。前者が task の done を主張することはできません。構造化データ（`data`）を成果として期待するのは `split` / `map` / `reduce` / `filter` / `judge` / `verify` だけで、自由記述の kind では本文中の JSON 風断片を data に昇格させません。ただし `work` / `generate` は末尾の `{"ok": ...}` だけを完了可否の envelope として読みます。散文に紛れた `"issues": []` を空リストとして拾い、下流を汚した事故があったためです。

`map-reduce` はカタログ上ほかの 6 つと同格の選択可能パターンです。`split` 完了後に `map` と `reduce` を実行時生成する `_expand_splits` は、パターンではなく継続メカニズムで、classify のルーティングや verify の作り直しと同じ層にあります。

`exemplar_first` を立てると、fan-out を見本先行に変えます。先頭 1 件と検証ゲートだけを先に出し、ゲートを通ってから残りを展開します。同じ手順を繰り返す作業を、1 件で手順を固めてから流したいときに使います。

### E. 決着済みの判断

**ハングは lease ではなく task timeout で守る**（2026-06-14 採用）。lease と心拍はプロセスの生存を伝える信号で、タスクの進捗を伝える信号ではありません。心拍は別スレッドで鳴るので、メインスレッドが `subprocess.run` でブロックしていても lease は延び続け、孤児回収は永久に発動しません。lease に上限を設ける案は正当に長いタスクを誤って横取りし、ハングしたプロセスを kill もしません。採ったのは `run_agent` の subprocess タイムアウトです。解決順は、呼び出し 1 回の明示指定（verification plan の `policy.agent.timeout_sec`）→ agent-control の用途別値（kind は worker 値も継承）→ flow 共通値 → CLI 定義の timeout → `agent_timeout` → 環境変数・既定 600 秒。control は呼び出しごとに読み直し、すでに動いている subprocess の期限は変えません。`agent_timeout: 0` だけが無効化の口で、固定検証コマンド、GitLab 待機、lease、poll の timer はこの設定の対象外です。超過したタスクは transient タグ付きで失敗させ、レイヤ 1 の再試行に載せます。stdout のバイト流量で心拍をゲートする案（真の進捗連動）は筋が良いものの、タスクが元々 LLM 1 コールで有界な現状では複雑さに見合わないと判断しました。長尺タスクを扱いたくなった時点で再検討します。

**run 内のステップは PC 間に分散させない**（2026-07-27 既定）。配る単位は run のままで、グラフの中のステップを他 PC が拾いに来る経路は持ちません。下地（ノード単位の claim と決定的タイブレーク）は実装済みで別クローンからの競合テストもありますが、それを駆動する主体を置かない、という判断です。理由は 4 つあります。実行はローカル・共有するのは状態の鏡だけ、という公理と正面から衝突すること。バスの claim には板の入札選別（契約バージョンの fail-close・`workloads`・枠の自己抑制）が一つも無いので、板を唯一の PC 間分配経路とする契約を迂回する第 2 の分配機構になること。公平なタスク分配がそもそも非目標であること。そして既定のエージェント CLI の月間上限は 1 台が踏んだ時点で run 全体を失敗終端させるので、同じアカウントを使う限り PC を増やしても実行総量が増えないことです。入れるなら形は 2 つで、ステップ（またはサブグラフ）を板の公示として出して入札選別を通す形なら契約は 1 本のまま保てます（agent-project の検証委譲がこの型の実例）。共有バスに版と枠の照合を足す「信頼フリート」モードは原則そのものの例外になるので、採るならコンセプト正典の改訂とセットです。契機は「1 台の `workers` × 枠では実際に足りない」実測——長い run が 1 台を占有して他 PC が遊ぶ状態の観測で、実行した PC を結果に書くようになったので、この実測自体は取れます。

**ワークフロースクリプトの動的生成は採用しない**（2026-06-13 採用）。公式の Dynamic Workflows は、LLM がタスク専用のコードハーネスを生成して実行します。agent-flow は宣言的なタスクグラフと継続ルールとデータ駆動 fan-out で同等の動的性を、コードを実行せずに表現しています。採用しない理由は 3 つで、LLM 生成コードの実行は任意コード実行そのものであること、プロセス内 spawn 型のハーネスは claim とリースと複数 PC に自然に乗らないこと、走るスクリプトは中断再開と監査ができないことです。表現力が足りなくなったら、まず宣言的語彙の拡張（条件付きエッジ、新しい kind）で対応します。どうしても必要になったら、分散バスから切り離した単一ノードのローカルハーネスモードをオプトインで用意し、サンドボックスを必須にします。

### F. テスト

`tools/agent-flow/tests/` に 794 件（機能別に分割済み）。共有の前置きは `_shared.py` にあり、エージェント CLI なしで全件が通ります。

```bash
AGENT_FLOW_STUB_SLEEP_MAX=0 python3 -m pytest tools/agent-flow/tests -q
```

分散の検証は `GitDistributedTests` が担い、ローカルのベアリポジトリを共有バスにしてノードごとの独立クローン（別 PC 相当）から push/pull させます。別クローンからの同一タスク claim で勝者が 1 人になること、2 ノードが同じ要求を受理しても orchestrate 担当が 1 台に決まること、`--git-subdir` と sparse checkout で無関係なディレクトリを作業ツリーに展開しないことを確認します。
