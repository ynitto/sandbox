# agent-flow 設計書

> 対象：`tools/agent-flow/`
>
> 最終更新：2026-08-29
>
> 外部契約と設定値は[仕様書](../specs/agent-flow-spec.md)、導入と操作は
> `tools/agent-flow/README.md` を参照してください。
>
> 関連：[agent-project 設計書](./agent-project-design.md) / [複数 PC 運用ガイド](../guides/multi-pc-operations.md) /
> [git worktree キャッシュ](./git-worktree-cache-pattern.md)

## TL;DR

agent-flow は、1 件の要求をタスクグラフへ分解し、実行結果を見ながらグラフを更新するワークフローエンジンです。1 回の実行を run と呼びます。run は親プロセス、orchestrator 1 本、複数の worker からなるプロセス群です。

実行系は三つの規則に従います。

- run の共有状態はバス上のファイルに置く。node の状態は `result`、`claim`、`wait`、`task` の有無から導出する
- graph と task は orchestrator が更新し、worker は claim に勝った node だけを実行して result を残す
- 複数 PC へ配る単位は graph の node ではなく run とする。`participate` は受理だけを行い、周期駆動は `agent-project serve` が持つ

中央キューを置く案は、agent-flow とは別に可用性と運用を背負うため採用していません。LLM が実行用スクリプトを生成する方式も、任意コードの検査と再開時の再現性を両立できないため採用していません。実行計画は宣言的な graph として保存します。

この文書は、agent-flow の実行系やバス契約を変更する人向けです。設定キー、JSON の全フィールド、上限値を調べる場合は仕様書を使ってください。

## 1. 目的と範囲

### 1.1 解く問題

要求の分解を実行前に固定できない仕事があります。途中の調査結果で後続作業が増えたり、検証結果を受けて一部を作り直したり、人の承認を数日待ったりする仕事です。これを 1 プロセスのメモリだけで管理すると、停止時に進捗を失い、承認待ちのあいだ実行枠を占有します。

agent-flow は、計画と実行の途中状態をファイルへ確定し、プロセスを再起動しても続きから進められるようにします。共有 git をバスに使う場合も、同じファイル契約で動きます。

### 1.2 目標

- 要求から graph を作り、結果に応じて node の追加や置換ができる
- 完了済み node を残したまま、失敗箇所と未着手箇所だけを再開できる
- claim の競合が起きても、同じ node の実行者が 1 名に収束する
- 人や外部システムの応答待ちを worker から切り離せる
- workspace への変更、検証結果、公開先 revision を run の記録へ結び付けられる

### 1.3 非目標

- cron や常駐スケジューラの提供
- graph 内の node を複数 PC へ公平に割り振ること
- 成果物リポジトリの代替。バスには実行記録と参照先を置き、成果そのものは workspace に置く
- モデルや CLI の能力評価。候補と予算は管理面から受け取り、agent-flow は解決済みの条件を実行する
- verification plan による最終受入の決定。agent-flow は receipt を作り、不合格なら run 内で修正を試みるが、採否は依頼元が決める

## 2. システム構成

### 2.1 run のプロセス構成

通常の run は次のプロセスで動きます。

```text
run 親プロセス
├── orchestrator × 1
├── worker × N
└── 監視ループ
    ├── park 済み node の再確認
    ├── cancel と子プロセスの監視
    ├── state_git の同期
    └── transient failure の auto-heal
```

`run` は要求を子プロセスへ渡し、終了条件まで監視します。計画を作るのは orchestrator、node を実行するのは worker です。親プロセスは graph の内容を判断しません。

`participate` は別の入口です。inbox や委譲公示板を 1 巡し、受理した run-id を返します。そこで run を実行しないため、呼び出し元は返された id ごとに `run --from-inbox` を起動します。この周期処理は `agent-project serve` が担当します。

### 2.2 コンポーネント

| コンポーネント | 責務 | 主な実装 |
|---|---|---|
| CLI / launcher | 設定解決、子プロセス起動、run の監視 | `cli.py`, `run.py`, `daemon.py` |
| orchestrator | 初期計画、graph 更新、静止判定後の評価、最終化 | `orchestrate.py`, `patterns.py`, `continuation.py` |
| worker | node の選択、claim、executor 呼び出し、result の記録 | `work.py`, `agent.py`, `plugins.py` |
| bus | run のファイル配置、状態導出、claim と lease | `bus.py` |
| transport | ローカルバスまたは git バスの同期 | `gitbus.py`, `agentcore.transport` |
| wait service | human node と外部 executor の park、再確認 | `waits.py` |
| workspace | 作業用 clone、commit、push、復旧 ref | `workspace.py`, `gitcache.py` |
| verification | verification plan の実行と receipt 作成 | `verifyplan.py` |
| recovery | 世代交代、auto-heal、公開失敗の手動復旧 | `run.py`, `recovery.py`, `bus.py` |

`state_git` は実行バスではありません。ローカルバスの `runs/` と `inbox/` を dashboard 用のリポジトリへ写す補助経路です。同期に失敗しても run は継続します。

### 2.3 パッケージの組み立て

`agent_flow` は通常の Python モジュール群として import されません。`__init__.py` が `_FRAGMENTS` の順に各ファイルを同じ `globals()` へ `exec` し、単一の名前空間を作ります。旧単一ファイル版の monkey patch、global の再束縛、private symbol 参照を維持するための互換構造です。

このため、ファイル境界は実行時の名前空間境界ではありません。fragment の順序変更、同名 symbol の追加、通常 import への置換は広い変更になります。変更時は CLI の起動試験に加え、テストが `agent_flow.<symbol>` を直接差し替える経路も確認します。新しい設計上の依存は、可能なら `agentcore` の明示的なモジュールへ置き、共有名前空間への依存を増やしません。

## 3. 状態モデル

### 3.1 run、phase、node state を分ける

agent-flow には意味の異なる 3 種類の状態があります。

| 種類 | 例 | 正典 |
|---|---|---|
| run status | `running`, `done`, `failed`, `cancelled` | `meta.json` |
| run phase | `planning`, `executing`, `evaluating`, `verifying`, `finalizing` | `meta.json` |
| node state | `pending`, `claimed`, `waiting`, `done`, `failed` | ファイルから導出 |

phase は表示と診断のための進行段階です。終端判定には使いません。run status が終端でも、phase が古い値のまま残る可能性を読み手は許容します。

node に可変の `status` フィールドはありません。`Bus.node_state(id)` は次の優先順位で状態を返します。

1. `results/<id>.json` があれば、その `status`
2. 生存中の勝者 claim があれば `claimed`
3. 生存中の wait lease があれば `waiting`
4. `tasks/<id>.json` があれば `pending`
5. どれもなければ `unknown`

この順序により、結果が確定した node は古い claim や wait が残っても終端として読まれます。wait lease が失効した node は `pending` に戻り、監視主体の消失で行き止まりになりません。

### 3.2 バスの書き込み規律

run の主な配置は次のとおりです。全フィールドと書き手の表は[仕様書 §3.7](../specs/agent-flow-spec.md#37-バスのレイアウトと書き込み所有権)を正典とします。

```text
<bus>/
├── inbox/<run-id>.json
├── inbox/claims/<run-id>/<who>.json
└── runs/<run-id>/
    ├── meta.json
    ├── graph.json
    ├── tasks/<node-id>.json
    ├── claims/<node-id>/<who>.json
    ├── waits/<node-id>.json
    ├── interactions/<interaction-id>/...
    ├── results/<node-id>.json
    ├── artifacts/<node-id>/...
    ├── events/<who>.jsonl
    ├── final.json
    └── receipt.json
```

競合を減らす原則は、同じ論理状態に複数の書き手を置かないことです。orchestrator が graph と task を更新し、worker は自分名義の claim と event を書きます。result は claim の勝者、または park を決着させた監視主体が書きます。回復や cancel の管理操作だけが、定められた経路で lifecycle の記録を更新します。

event は履歴であり、現在状態の正典ではありません。`planned`、`evaluate`、`replan`、`inflight_amend` には理由と変更差分を残します。dashboard は最終 graph から計画変更の時系列を推測せず、event を読みます。

### 3.3 claim と lease

worker は `claims/<node-id>/<who>.json` へ自分の claim を書きます。生存 lease 内の claim を集め、`(ts, who)` が最小のものを勝者とします。同じ集合を見た実行者は同じ勝者を選ぶため、git の同期に遅延があっても結論は収束します。同一ホスト内では file lock を併用します。

lease は長い処理の timeout ではありません。実行中は heartbeat が claim を延長し、処理自体の打ち切りは agent timeout が担当します。両者を同じ値で扱うと、正常な長時間処理を横取りするか、停止した worker の回収が遅れます。

複数 PC で同じ run を能動的に分割実行する scheduler はありません。分散時の claim は、所有者交代や一時的な二重起動が起きても result が 1 つに収束するための安全策です。

## 4. run のライフサイクル

### 4.1 受理から起動まで

```text
submit
  │  inbox/<run-id>.json
  ▼
participate
  │  inbox claim に勝った PC が run-id を返す
  ▼
run --from-inbox
  ├── orchestrator
  ├── worker × N
  └── monitor
```

inbox は要求文だけでなく、workspace、参照先、user plan、verification plan、run 単位の計画指定を運びます。`run --from-inbox` はこれらを読み、CLI で明示された値があればそちらを優先します。

`participate` の受理直後から `run` が生存 lease を張るまでには短い窓があります。この間に呼び出し元が停止すると、inbox claim の lease が失効するまで別の PC は拾い直しません。claim lease を短くしてこの窓だけを詰めると、起動が遅い正常な run を二重受理するため、現行設計では許容しています。

### 4.2 初期計画

orchestrator は既存の `graph.json` があれば再計画せずに再開します。新規 run では入力を次の順で解決します。

1. user plan があれば、schema と graph の整合を検査して採用する
2. `pattern` が明示されていれば、標準 pattern から graph を作る
3. planner を使い、要求から strategy と task 列を作る

user plan と pattern の同時指定はエラーです。user plan が不正な場合も planner へ切り替えません。別の計画で動かすより、指定した計画を修正できる形で返すためです。

planner は `flow-planner`、`agent`、`stub` の経路を持ちます。自動縮退した場合は `strategy.reason` と event に理由を残します。planner の出力はそのまま実行せず、未知の依存、自己依存、循環を除去してから保存します。`split` の後続は実行時に展開するため、planner が静的に作った重複 successor も除きます。

workspace の target と作業 branch が異なる場合は、root の前に `base-sync` system node を入れます。plan gate が有効なら、その前に human node を挿入し、承認されるまで root を実行しません。

### 4.3 worker ループ

worker は graph を読み、次の条件を満たす node から候補を選びます。

- node state が `pending`
- すべての依存 node が `done`
- run status が終端ではない

候補の順序は worker 間の衝突を減らすためにランダム化します。claim の勝敗はランダムではなく、前節の規則で決まります。

claim に勝った worker は executor を呼び、結果を `results/<id>.json` に書きます。実際に使った CLI、model、tier、選択理由、成果物、エラー分類もこの時点で記録します。読み手が後から設定を再解決して「何で実行したか」を推測してはいけません。

依存結果は既定で digest を渡します。`verify`、`reduce`、`synthesize`、`filter`、`judge` など、前段の詳細を読む必要がある node は full input を使います。どちらを使ったかと削減量は result に残します。

### 4.4 静止、評価、再計画

orchestrator は次のいずれかが存在する間、graph を評価しません。

- 実行中の claim
- 生存中の wait
- 依存が解け、今すぐ claim できる pending node

これらがなくなった状態を静止と呼びます。依存先が failed のため実行できない pending node は静止を妨げません。評価役が置換や追加を判断できるようにするためです。

静止後、orchestrator は result を集めて `done`、`replan`、`failed` を決めます。`replan` では node を追加または置換し、graph の iteration を進めます。置換対象に依存していた後続 node の依存先も新 node へ付け替えます。iteration と同一系統の retry には上限があり、graph が無限に成長しないようにします。

user plan は既定で評価役による形の変更を受けません。`plan.evaluate: true` のときだけ通常の再計画へ載せます。ただし、`split` の出力から `map` と `reduce` を作る機械的な fan-out は user plan でも動きます。

### 4.5 park と再開

human node や deferring executor は、未決着を失敗 result として書かず park します。worker は wait record を書いた後に claim を解放し、次の node を処理できる状態へ戻ります。

親の監視ループが `service_waits` を一定間隔で呼び、外部の決着を確認します。決着すれば result を書き、未決着なら wait lease を延長します。監視主体が消えて lease が切れた場合、node state は `pending` に戻ります。再起動した監視主体や worker が再接続できます。

plan gate の差し戻しは一般の評価役へ渡さず、指摘を加えた要求で計画を作り直します。人が書いたコメントを LLM の評価結果に埋めて意味を変えないためです。

### 4.6 検証と最終化

graph 内の終端 `verify` node と、依頼元が渡す verification plan は役割が異なります。

| 検証 | 目的 | run への影響 |
|---|---|---|
| 終端 `verify` node | graph 自身の完了条件 | 1 件でも failed または判定不能なら run を failed にする |
| verification plan | 確定 revision に対する外部受入条件の証跡 | receipt を作る。fail は予算内で修正 node を追加し、inconclusive は上位へ返す |

終端 `verify` の判定は `_normalize_verify` に集約します。構造化された `data.ok` を優先し、本文から明示的な pass/fail を読めない場合は fail とします。orchestrator が別の文字列判定を持つことはできません。

verification plan は成果 revision が確定してから、同じ workspace で実行します。receipt は plan digest と result revision を持ち、同じ組み合わせを再実行しません。command の非ゼロ終了や criterion の不合格は `fail`、CLI 不在や実行不能は `inconclusive` です。証跡のない pass は認めません。

最終化では `final.json` を作り、run status を `done` または `failed` にします。workspace を持つ node は、commit だけでなく remote への push 成功までが delivery の完了条件です。

## 5. workspace と公開

### 5.1 書き込み先は run が受け取った集合（workset）

workspace を指定した run は、run 専用の作業 branch を使います。worker は同じ run の作業先を共有し、agent は編集だけを行います。commit と push は agent-flow が担当します。参照リポジトリは読み取り専用で、workspace と同じ扱いにしません。

書き込み先は 1 つに限りません。run は書込先の**順序付き集合（workset）**を受け取り、先頭を primary と呼びます。既定は 1 要素で、そのときは従来の単一 workspace と形も意味も変わりません。**どこへ書くかを決めるのは依頼側（agent-project / dashboard / 板）で、agent-flow の planner は集合を増減しません。** ノードは既定で repo を知りません（repo-blind）——worker が workset 全体を用意し、agent が実際に編集した repo だけを agent-flow が finalize します。集合の各要素には同じ規律を要素ごとに適用します（同名の作業 branch・commit と push・publication・復旧 ref・base-sync・CI の取り込み）。

要素の同一性は従来どおり (url, path, base) です。同じ url を持つ要素は base が等しくなければなりません——要素ごとの作業 branch は同名なので、同 url・別 base のままでは 1 本の branch の起点が矛盾するためです（明示 branch で分ける経路だけ許します）。同 url・同 base・別 path の要素は 1 つの clone を共有し、変更を許す範囲はその path の和集合になります。

複数の書込先を持つ run では、エージェントの作業ディレクトリは primary の clone のままにし、他の要素は指示ブロックへ絶対パスで列挙します。cwd を親ディレクトリへ移すと、cwd を「そのプロジェクトのルート」と解釈する CLI が壊れるためです。`path` を宣言した要素は finalize が範囲外の変更を機械的に弾きます——複数 repo を同時に開くと誤編集の余地が増えるので、指示だけに頼りません。

target が作業 branch より進んでいる場合、`base-sync` が先に統合します。競合がなければ機械的に merge し、競合したファイルの内容だけを executor に直させます。履歴操作を agent へ渡しません。target を含められなければ integration failure です。base-sync は**要素ごとに 1 ノード**（`base-sync@<name>`）で、root は全ての base-sync に依存します。1 つでも target を取り込まないまま作業を始めると、その repo だけ古い起点の上で検証することになるためです。

正典設計: [複数リポジトリ（workset）設計](../plans/2026-09-05-agent-flow-multi-workspace-design.md)。

### 5.2 commit と push

node 実行後に差分があれば、agent-flow は次を確認してから commit します。

- 未解決 conflict がない
- staged diff の品質検査が通る
- conflict marker が残っていない

commit が失敗した場合は node を失敗させます。古い HEAD を push して delivery 成功と記録することはありません。

push 前には `refs/agent-flow/recovery/<run-id>` をローカルの元 repository に作ります。push 成功後に削除し、失敗時は残します。remote が進んだことによる non-fast-forward だけを fetch、rebase、再 push の対象にします。認証、権限、ネットワークの失敗を rebase で覆い隠しません。

手動復旧は `force-complete` から行います。期待 commit が remote branch に含まれることを検証してから `published-manually` へ更新し、理由と remote tip を監査記録へ残します。検証なしで run を done に変える入口はありません。

### 5.3 半公開を隠さない

複数 remote への push は原子的にできません。要素 A が published、要素 B が failed になったノードは、B の失敗で打ち切らずに残りの要素も finalize してから failed にします。先に失敗した要素で止めると、他の要素が「commit も push もされていない」のか「されたが記録が無い」のか区別できなくなるためです。成功した要素の publication は published のまま残し、run 全体の完了条件は「全要素の publication が published（変更ゼロの要素は not-required）」とします。

再開（resume）では失敗ノードが pending へ戻り、A は差分ゼロで not-required、B だけが再 push されます。A の commit は既に remote にあり、作業ツリーは同じ作業 branch から作り直すためです。`force-complete` と復旧 ref の gc も要素ごとに回ります。

## 6. 失敗と回復

### 6.1 回復する層を固定する

同じ失敗を複数の層で無制限に再試行しないよう、回復場所を分けています。

| 層 | 対象 | 動作 | 消費する予算 |
|---|---|---|---|
| 呼び出し内 retry | 接続断、5xx、timeout などの transient | `run_agent` で指数 backoff | `transient_retries` |
| format repair | 空応答、JSON 契約違反 | 契約を言い直して同じ役割へ再要求 | `format_retries` |
| graph repair | 成果内容の不合格 | 評価して node を追加または置換 | `max_retries`, `max_iterations` |
| run auto-heal | 呼び出し内 retry 後も続く transient | cooldown 後、failed node だけ pending に戻す | `max_heals` |

認証切れ、CLI 不在、管理面の停止は待っても直る保証がないため、自動再計画しません。run を理由付きで failed にします。quota は明示設定がある場合だけ、長い cooldown 後の auto-heal 対象にできます。

回復後も `done` result は削除しません。修正対象と失敗箇所だけを動かし、同じ成果を再生成しないことが基本です。

### 6.2 orchestrator 消失と孤児 run

orchestrator は run の生存 lease を更新します。計画、評価、検証のような長い blocking 処理中は専用 heartbeat thread が lease を延長します。git バスでは heartbeat の更新も commit と push まで行い、dirty worktree のまま次の pull を妨げないようにします。

lease が切れた非終端 run は孤児候補です。`participate` の回収処理は inbox claim に勝ってから同じ run-id の orchestrator を再起動します。既存 graph と done result を読み、未完了部分から再開します。進捗のない再開には上限があります。

### 6.3 世代交代

依頼元が新しい要求や指摘でやり直す場合は `inherit_from` を使います。新 run へ引き継ぐものは、graph、task、artifact、`done` result です。failed result、claim、event は引き継ぎません。旧 run には後継 run-id を示す墓標を残します。

先行 run が実行中なら安全のため引き継ぎません。先行 run が終端または孤児であることを確認してから世代交代します。新 run の request と verification plan を正典とし、古い受入条件を持ち込みません。

auto-heal は世代交代ではありません。同じ run-id の中で failed node を pending に戻します。利用者の要求が変わっていないためです。

## 7. 計画とカスタマイズの境界

振る舞いを変える入力は、適用先の違いで分けます。

| 層 | 決めるもの | 例 |
|---|---|---|
| L1 graph の形 | どの工程をどう接続するか | user plan, pattern, planner output |
| L2 分け方 | run 全体をどう分解するか | granularity, split policy, review, plan gate |
| L3 指示文 | 選ばれた方針をどう伝えるか | method catalog, planner/worker skill |
| L4 実行資源 | どの CLI、model、tier、予算で動かすか | execution overrides, agent-control |

同じ設定は CLI、inbox、config で同じ名前を使います。解決順は `CLI > inbox > config > default` です。CLI の値はその場の明示指示、inbox は run 単位の指示、config は node の既定だからです。

計画にだけ効く引数は `run` と `orchestrate` に置き、`work` や `doctor` では受理しません。未知の値も既定へ丸めず起動前にエラーにします。指定が無視されたまま run が進む状態を許さないためです。

L3 では、選択は engine、文面は catalog が担当します。catalog が壊れている場合は組み込み文面へ戻ります。catalog の変更だけで `split_policy` の値や graph の意味を変えてはいけません。

## 8. 分散と外部 executor

### 8.1 Bus、GitBus、state_git

`Bus` はローカルディレクトリを正典とします。`GitBus` は同じ配置を node ごとの管理 clone に置き、pull と push で共有します。graph や result の意味は transport に依存しません。

GitBus の書き込みは path を書き手ごとに分け、push 競合を pull --rebase と再 push で吸収します。transport の clone、lock 残骸、rebase 中断、object 破損への対処は `agentcore.transport` が持ちます。agent-flow 側で別の git retry を増やしません。

`state_git` はローカル実行の観測用 mirror です。`runs/` はローカル優先、inbox は remote 優先の規則で同期します。実行可否や終端判定を state_git の成功に依存させません。

### 8.2 executor plugin の境界

executor plugin が担当するのは worker node の実行です。計画、graph の健全化、静止判定、評価、再計画、run の最終化は engine 内に残します。plugin の障害や外部サービスの都合で graph の意味が変わらないようにするためです。

未決着を返す plugin は `DeferDecision` 相当の signal で park できます。秘密を wait record に書かず、再確認に必要な token と公開可能な情報だけを残します。

同梱の GitLab executor は互換用途です。通常の承認は human node または plan gate を使います。GitLab の issue 状態を run の制御面として必須にしません。

## 9. 変更時に守る条件

### 9.1 不変条件

- node state は `Bus.node_state()` だけで導出する。表示側や executor に別の状態機械を作らない
- graph と task の更新は orchestrator の経路を通し、変更理由を event に残す
- result は実行時に解決した事実を持つ。読み手に設定の再解決をさせない
- `done` result と公開済み commit は retry、auto-heal、世代交代で失わない
- すべての retry、replan、resume、heal、fan-out に停止条件を持たせる
- cancel は `cancelled` で終端し、auto-heal や孤児回収で再開しない

### 9.2 変更箇所ごとの確認

| 変更 | 確認する範囲 |
|---|---|
| bus path / node state | `bus.py`, GitBus 同期、仕様書 §3.7、分散 claim test |
| planner / pattern / graph | `patterns.py`, `orchestrate.py`, graph sanitize、user plan、event 差分 |
| executor | `plugins.py`, `work.py`, park、failure class、result の実効値 |
| workspace / publication | commit failure、non-fast-forward、recovery ref、manual recovery、terminal status |
| verification | terminal verify と verification plan を混同していないか、receipt の再利用条件 |
| recovery | done 温存、retry 予算の二重消費、cancel と superseded の除外 |
| fragment 構成 | `_FRAGMENTS` 順、共有 global、CLI 起動、monkey patch を使う既存 test |

外部契約を変えた場合は、実装だけでなく[仕様書](../specs/agent-flow-spec.md)と `tools/agent-flow/tests/` の契約 test を同じ変更に含めます。

## 付録 A. ADR

### ADR-001 バス上のファイルを共有状態とし、node state を導出する

- 状態：採用
- 判断：プロセス間の情報はバス上のファイルで交換し、node state は result、claim、wait、task の存在から導出する
- 文脈：実行 node は同時に書き込み、git 同期には CAS も全順序もない
- 見送った案：中央 queue server、RPC、共有 status file、event replay による現在状態
- 代償：git 同期ぶんの遅延と、node 数に比例する filesystem access が発生する
- 見直し条件：実運用の node 数で stat または git 往復が支配的になり、file contract を保ったまま改善できない場合
- 確信度：高い

### ADR-002 claim は名義別ファイルと決定的 tie-break で決着する

- 状態：採用
- 判断：claim は `<node-id>/<who>.json` に分け、lease 内の `(ts, who)` 最小を勝者とする
- 文脈：別 clone からの同時書き込みでは、双方が一時的に自分を先着と観測できる
- 見送った案：push の non-fast-forward を lock に使う、ホスト間 file lock、単一 claim file の上書き
- 代償：勝敗確定まで同期を 1 往復待つ。同じ claim 集合を見るまでは一時的な見え方が異なる
- 見直し条件：transport が分散 CAS を正式な契約として提供し、その障害時動作も file bus より単純になる場合
- 確信度：高い

### ADR-003 agent-flow は常駐せず、run を分散単位にする

- 状態：採用
- 判断：`participate` は受理 1 巡、`run` は 1 run の完走に限定する。周期駆動は `agent-project serve` が担当する
- 文脈：各 engine が独自 daemon を持つと、生存監視、更新、掃除、実行枠が重複する
- 見送った案：agent-flow 専用 daemon の維持、run の待機ループへの全体 tick の同居、graph node の複数 PC scheduler
- 代償：呼び出し元が受理した run-id を起動し、実行中 id を次回の `participate` に渡す必要がある
- 見直し条件：agent-project 以外の正式な supervisor が必要になり、共通 lifecycle 契約を先に定義できた場合
- 確信度：高い

### ADR-004 失敗種別ごとに回復層を 1 つ決める

- 状態：採用
- 判断：transient は呼び出し内、format は契約修復、content は graph、継続する transient は run auto-heal で扱う
- 文脈：全失敗を再計画へ上げると、環境障害で node ごとの retry 予算と LLM 呼び出しを消費する
- 見送った案：各呼び出し元に独立 retry loop を置く、failed run を分類せず再開する、別 worker への即時付け替え
- 代償：failure class の誤判定が回復先を変える。各層の上限を合わせた最悪時間の確認が必要になる
- 見直し条件：実測で failure class の誤判定が回復率を下げる、または層をまたぐ予算管理が必要になった場合
- 確信度：高い

### ADR-005 カスタマイズを graph、分け方、文面、実行資源に分ける

- 状態：採用
- 判断：L1 から L4 の適用先を分け、同じ設定名と `CLI > inbox > config > default` の解決順を使う
- 文脈：形、分解、prompt、model を同じ override に入れると、どの段階で効く値かを各呼び出し元が再判定することになる
- 見送った案：全指定を `execution_overrides` に集約する、層ごとに別の命名規則と優先順位を設ける
- 代償：pattern のように L1 と L2 の両方へ影響する指定は完全には直交しない。CLI 明示値と default の区別も必要になる
- 見直し条件：既存 4 層のどこにも置けない run parameter が現れ、例外規則が増え始めた場合
- 確信度：中程度

## 付録 B. 採用していない実行形

- LLM が Python や shell の workflow script を生成し、そのまま実行する形は持たない
- graph node を PC 間へ公平に配る scheduler は持たない。run 単位で受理する
- CI の赤を自動で graph repair へ戻さない。CI は公開後の記録として取り込む
- terminal verify がない run に暗黙の verify node を追加しない。必要な gate は plan または planner が明示する
- state_git を実行バスや完了条件として使わない
