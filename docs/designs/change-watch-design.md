# change-watch 設計

任意の Web API（またはフォルダ）へのアクセスを、Dagu のワークフローから受け持つ汎用の仕組み。取得をまとめ、ワークフロー（consumer）ごとに
前回処理し終えてから変わったレコードを返し、書き込みも同じ接続で通す。条件の判定と処理はワークフローに置く。
実装は `tools/change-watch/`、使い方は同フォルダの README。

## 1. 位置づけ

GitLab の issue の変化でエージェントを動かす [gitlab-issue-agent](./gitlab-issue-agent-design.md) のような用途から、「API の取得」と
「前回からの変化」を汎用に切り出したもの。**どれを処理するか（条件）と処理は Dagu のワークフローに置く**。

| 方針 | 決め方 |
|---|---|
| 常駐しない | ワークフローのステップが `get` / `commit` / `call` を呼ぶ。どれも状態を読んで進めて終わる |
| 条件と実行管理は Dagu | 起動・条件の分岐（`preconditions`）・再試行・ログ・履歴・並列数は Dagu |
| このツールが持つもの | Dagu では表せない状態だけ: 取得の途中経過と位置、取得元の今の姿、consumer ごとの「処理し終えた姿」、要求の間隔と失敗の待ち |
| 特定のサービス・言語に依らない | 要求・ページング・増分・抽出はすべて設定 |
| 本体は Dagu を知らない | Dagu を知っているのは `dags/` の例のワークフローだけ。cron から `get` / `commit` を呼んでも同じに動く |
| 1 ファイルで配る | インストーラ（`install.py`）が本体を zipapp（`.pyz`）にまとめ、コマンドとして置く（§2.0） |
| 認証情報の場所は実行時に渡す | connections.yaml のパスはコマンド行の `--connections` だけで受け取る（§6） |

## 2. 構成

```
            ┌─────────────── state.db（SQLite・WAL・synchronous=FULL）───────────────┐
 get ─────▶ │ sources（指紋・世代・位置・待ち時刻）  fetches / fetch_pages（ページ単位） │
 fetch      │ http_cache（ETag と本文）              source_records（key ごとの最新）   │
 commit ──▶ │ consumers（consumer × 取得元の世代）   consumer_records（処理し終えた姿）  │
 call       │ rate_groups（group で分け合う要求の記録） leases（取得の排他）             │
            └──────────────────────────────────────────────────────────────────────────┘
```

### 2.0 配布とプラグイン

| 置き場所 | 責務 | 依存 |
|---|---|---|
| `changewatch`（本体） | 取得・状態・`get` / `commit` / `call`・運用のコマンド | 標準ライブラリ（YAML の設定なら PyYAML） |
| `changewatch.plugins` / `changewatch.hooks` | `--plugin <.py>` で指定されたプラグインの読み込みと、プラグインが足すテンプレートのフィルタの登録先 | 本体だけ |
| `dags/*.example.yaml` | change-watch を使う Dagu のワークフローの例（§2.0.1）。利用者がコピーして直す | Dagu |

- プラグインは `register(api)` を持つ 1 つの `.py` で、`api.add_filter(name, fn)`・`api.add_command(name, fn, help)` を呼ぶ。
  本体はコマンド行の `--plugin` を先に読み（`parse_known_args`）、指定されたプラグインだけを読み込んでから残りの引数を解釈する。
  1 回の実行の始めにプラグインの登録を外すので、同じプロセスで続けて呼んでも前の指定は残らない
- 配布: `install.py build` が `changewatch/` の `.py`（テストと `__pycache__` は除く）と入口の `__main__.py`、それに PyYAML があれば
  その純 Python の部分とライセンス文を、`zipapp` で 1 つの `.pyz` にまとめる。C 拡張（`_yaml`）は zip から読み込めないので入れない（PyYAML は
  無ければ純 Python で動く）。`install` は Linux 系では `#!` 付きの実行ファイル `~/.local/bin/change-watch`、Windows では `.pyz` と `.cmd` を置く。
  Dagu のワークフローはこの 1 ファイルを呼ぶ（Linux 系は `~/.local/bin/change-watch` を `command` に、Windows は `python` の引数に `.pyz`）
- 問い合わせの予定（取得元の `schedule`）は基盤に依らない cron の文字列として本体の設定に置く（§3.6）。ワークフローだけに要る項目
  （起動の間隔・dotenv・履歴の日数）はワークフローに書く（本体の設定に Dagu の節を作らない）

### 2.0.1 Dagu のワークフローは例を置き、生成しない

ワークフローの形は「`get` → 条件で分かれた処理 → `commit`」と「整理（`gc`）」の 2 つで、設定から変わるのはパスと名前だけ。生成のコマンドは持たず、
`dags/*.example.yaml` を置く（以前は `dags` コマンドが設定から書き出していたが、生成のコードと「例が生成の出力と同じか」の試験を持ち続けるわりに
得るものが無かった）。ワークフローは `install.py install` が置いた 1 ファイルの `change-watch` を `command` にして呼ぶので、Python と入口のパスを
組み立てる必要がない。例が古くならないよう、試験（`test/dagrun.py`）が例の DAG を Dagu と同じ順（depends・`output:`・`preconditions`・
`retry_policy.exit_code`・`continue_on.failure`・`${<id>.exit_code}`）で本物の change-watch に流す。`agents/` の各エージェントの例のワークフロー
（整理を含む）も同じ実行役で流す。

### 2.0.2 エージェント（`agents/`）の置き場所とインストーラ

change-watch の上で動くエージェント（gitlab-issue-agent・gitlab-issue-hub-agent・gitlab-mr-review-agent・redmine-ticket-agent）は `tools/change-watch/agents/<名前>/` に置く。
どれも「設定の雛形（`change-watch.yaml`・`.env`）」「Dagu のワークフローの例」と、要るものだけ「エージェントが使う `.py`」（MR のレビューのコマンド・
ハブの issue の URL のフィルタ）でできていて、以前は README の `mkdir`・`cp` でエージェントごとに別の場所（`~/.local/share/<名前>/`・`~/.config/dagu/<名前>.env`）へ置き、
change-watch の設定は 3 つとも同じ `~/.config/change-watch/change-watch.yaml` にコピーしていた（1 台で 2 つ動かすと手でまとめる必要があった）。

`install.py install --agent <名前>` が、どのエージェントも同じ決まりで置く:

| 置くもの | 場所 | 入れ直したとき |
|---|---|---|
| `agents/<名前>/*.py` | `~/.local/share/change-watch/agents/<名前>/`（Windows は `.pyz` の隣の `agents\<名前>\`） | 上書き（更新） |
| `agents/<名前>/*.example`（ワークフロー以外） | `~/.config/change-watch/<名前>/`（`.example` を外す） | 無いときだけ |
| `agents/<名前>/change-watch-*.yaml.example` | `~/.config/dagu/dags/` | 無いときだけ |
| 状態（雛形の `state_dir`） | `~/.local/state/change-watch/<名前>/` | — |

- 設定と状態をエージェントごとに分ける。入れる・消す・`gc` がエージェント単位で済み、取得元や consumer の名前が重なっても互いに影響しない。
  そのためワークフローは `--config` を明示し、整理（`gc`）のワークフローもエージェントごとに 1 本ある（`change-watch-<名前>-maintenance`）
- 置き場所の決まりはファイル名だけで決める（`.py`・`change-watch-*.yaml.example`・それ以外の `.example`）。エージェントを足すときに
  インストーラを直さない（`change-watch.yaml.example` を持つフォルダを `agents/` に足すだけ）
- 例の `/home/you/...` は、change-watch・プログラム・設定・接続設定の場所に限って、この PC のパスに置き換える。Windows ではワークフローの
  `command` を Python に、`args` の先頭を `.pyz` にし、設定と `.env` のコマンドの `python3` を Python のフルパスにする。§2.0.1 の「生成しない」は
  設定から DAG を組み立てないという意味で、ここでは例の文字列の決まった箇所を置き換えるだけ。作業フォルダなど自分で決めるものは
  置き換えず、`/home/you` のまま残して直すところとして表示する
- 設定とワークフローを上書きしないのは、直したものを入れ直しで失わないため。前の版の置き場所や状態（`~/.local/state/change-watch/state.db`）
  が残っていれば、移し方を表示する（状態を移さないと `--initial emit` のエージェントが最初から数え直す）

### 2.1 状態を SQLite にした理由

- 取得 1 ページ・`commit` 1 件の遷移を、それぞれ 1 トランザクションで書ける。途中で停止しても「半分だけ取れた結果」を `get` に使わない
- Windows・WSL2・Linux の標準ライブラリにあり、追加の導入が要らない。`BEGIN IMMEDIATE` で複数のプロセス
  （同じ取得元を `get` する複数のワークフロー）の書き込みを直列にできる
- JSON のファイル 1 つ + flock は 1 プロセス 1 ファイルの読み書きには十分だが、consumer ごと・レコードごとの
  状態を持つと全体の書き戻しが重く、部分の遷移を原子的にできない
- 制約: SQLite のロックはネットワーク越し・`/mnt/c` 越しでは効かない。状態のフォルダは動かす OS のローカルに置く（README に明記）

## 3. 取得

### 3.1 取得元と指紋

取得元は「要求（URL・メソッド・ヘッダー・クエリ・本文）・抽出・ページング・増分・接続設定」の組で、ワークフローはこれを名前で参照して
共有する。`get` するワークフロー（consumer）が増えても API への問い合わせは取得元ごとに 1 回で済む。

取得元の**指紋** = 間隔やレート制限など取得条件に関係しない項目を除いた設定 + 接続設定の (サービス, ラベル) + 接続設定の値のハッシュ。
指紋が変わると世代（epoch）を進め、前の世代の取得結果・位置・ETag の控え・組み立てた全体の姿を使わない。consumer は世代が
変わったら基準を取り直す。これで「取得条件や認証・権限が違うデータを、意図せず同じものとして比べる」ことが起きない。
認証情報そのものは保存せず、ハッシュだけを指紋に入れる。

### 3.1.1 フォルダの取得元

取得元の `request` の代わりに `folder` を書くと、フォルダの一覧を 1 ページの全件取得として扱う（`changewatch/folder.py`）。
ファイル 1 つが 1 レコードで、`key` は相対パス（`rel`）に固定する。取得より後（`get` / `commit`）は API の取得元と同じ道を通るので、
フォルダ専用の監視の仕組み（常駐の監視プロセス・OS のファイル変更通知）は持たない。

| 決めたこと | 理由 |
|---|---|
| 常駐して OS の変更通知を受けず、起動のたびに一覧する | 「常駐しない」を崩さない。変更通知はネットワークのフォルダ・WSL2 の `/mnt/c` で届かないことがあり、取りこぼしの扱いが別に要る。一覧どうしを比べれば、止まっていた間の変化も拾える |
| 書き込み中のファイル（更新時刻から `settle_sec` 以内）は、`source_records` にある前回の姿を使い、無ければ一覧から外す | 一覧から外すだけだと、書き換え中の既存のファイルが `removed` → `appeared` に見える。前回の姿は `key` ごとの最新の姿として既に持っている |
| フォルダが無い・読めない下のフォルダがあるときは一時的な失敗にして完了にしない | 空の一覧で完了にすると、全件が `removed` になる（ネットワークのフォルダが切れただけで全部を返す） |
| `max_files` を超えたら完了にしない | `max_pages` と同じく、打ち切りを「全部取れた」と扱わない |
| 変化は大きさ・更新時刻で見て、中身のハッシュは `hash: true` のときだけ | 毎分全部のファイルを読むのは重い。更新時刻を保つコピーなどで取りこぼすときだけ中身を読む |
| `refresh`・接続・ページング・増分・条件付き要求は書けない | API の要求の項目で、フォルダには当てはまらない。書けると黙って無視されて誤解を生む |

指紋には解決したフォルダの絶対パスも入るので、`path` を変えると世代が変わり、consumer は基準を取り直す。

### 3.2 ページ単位の進行と再開

`fetches` の 1 行が 1 回の取得で、`page_state`（次のページの取り方: ページ番号・オフセット・カーソル・次の URL・
増分の途中の最大値・ループ検出用の既出カーソル）を持つ。1 ページ取るたびに、そのページのレコードと次の `page_state` を
同じトランザクションで書く。

- 失敗（一時的・恒久的とも）では `failed` にして `page_state` を残す。次の取得は `resume_max_age_sec` 以内なら続きから再開し、
  古ければ破棄して最初から取る（古い途中経過に新しいページをつなぐと、ページの境目のずれが大きくなるため）
- 最後のページを書いたあと「完了」にする前に止まったときは、`page_state.done` を見て取り直さずに完了処理だけ行う
- **完了するまで `get` に使わない・位置を進めない。** 全件取得の途中の結果を使うと、未取得のレコードが「消えた」に見える。
  増分の途中で位置を進めると、未取得の範囲を二度と取らない
- `max_pages` を超えたら完了にしない（打ち切りを「全部取れた」と扱わない）

### 3.3 増分取得

| 方式 | 位置 | 次の要求 |
|---|---|---|
| `max_value` | 取得したレコードの指定項目の最大値（数どうし・文字列どうしだけ比べる） | `param=<最大値>` |
| `cursor` | 応答の指定箇所の値（最後のページのもの） | `param=<値>` |

`>=` 系の API で境界のレコードが 2 回取れても、値が同じなので変化にしない。増分取得の結果は一部のレコードだけなので、
`key` を書いた取得元では完了のたびに `source_records`（key ごとの最新のレコード）を更新し、`get` と consumer の基準はこの全体の姿で
見る（直近の取得だけで基準を取ると、そこに無いレコードが次に取れたとき `appeared` で大量に返る）。

### 3.4 通信の失敗とレート制限

| 事象 | 扱い |
|---|---|
| 接続失敗・タイムアウト・408/425/429/5xx | 指数バックオフ（ジッタつき）で `retry.attempts` 回まで。`Retry-After`（秒・日時）があればそれ以上待つ |
| `Retry-After` やレート制限のリセットが `max_wait_sec` より長い | 取得をやめて `next_allowed_at` を記録し、終了コード 3。その時刻までの起動は問い合わせずに 0 で終わる |
| 200 系だが `X-RateLimit-Remaining: 0` | リセットまで待ってから次のページへ（長ければ上と同じ） |
| 上記以外の 4xx（認証・権限・404） | 再試行せず終了コード 1 |
| 取得元単位の間隔 | `min_interval_sec`・`per_minute`。最後の要求時刻は状態に残し、次の実行もそこから数える |

条件付き要求は要求の署名（メソッド + URL + 本文）ごとに ETag / Last-Modified と本文を控え、304 なら控えの本文を
そのページの応答として扱う。ページごとに控えるので、2 ページ目以降も 304 にできる。

### 3.5 排他

取得元ごとに期限つきの排他（`leases`、`fetch:<名前>`）を取る。期限はページごとに延ばす。持ち主が同じ PC で死んでいれば
（pid の生存確認、Windows は `OpenProcess`）即座に、別の PC なら期限切れで奪う。Dagu の DAG が重なっても、手で `fetch` を
叩いても、同じ取得元を同時に 2 回取らない。

### 3.6 いつ取得するか・取得の共有とずらし

**起動と取得を分ける**: いつ起動するかは Dagu（DAG の `schedule`）が、起動されたときに API へ問い合わせるかは change-watch が取得元ごとに決める。
初版は設定の `schedule` を DAG の生成（§2.0.1）が DAG に書き写していたが、起動の間隔が設定と DAG の 2 か所に分かれ、設定だけ直すとずれた。
今は書き写さない。取得元の `schedule`（cron。`changewatch/cron.py`。この PC の時刻）は「問い合わせの予定」で、`fetch` が起動されるたびに
「直近の予定の時刻より後に、この取得元を取得し終えているか」（`last_success_at` ≥ 直近の予定の時刻）を見て、済んでいれば `skipped` で終わる。
DAG は毎分起動にしておけば、予定は change-watch の設定 1 か所で決まる。取得元に `schedule` が無ければ起動されるたびに取得し、予定は
Dagu の起動間隔（ワークフローの `schedule`）に任せる。どちらでも動く。

例のワークフローは毎分起動する。取得元に `schedule` も `min_interval_sec` も無いと毎分問い合わせることになるので、
README でどちらかを書くか、ワークフローの `schedule` を問い合わせてよい間隔にするよう案内している。

1 つの取得元を複数のワークフローが `get` すると、同じ API を何度も呼びかねない。取得（`get` の前の取得と `fetch`）は次の順で判断して、
問い合わせを 1 回にまとめる。

0. `schedule` があり、直近の予定の時刻のあとに取得し終えていれば、待たずに `skipped` で終わる。`get` はその取得結果で答える
1. `min_interval_sec` の間に（同じ指紋で）取得し終えていれば、待たずに `skipped` で終わる
2. `jitter_sec` があれば 0〜その秒数だけランダムに待つ（定刻に複数の PC・ワークフローが API へ集中しない）。手で叩くときは
   `--no-jitter`、`--force` でも待たない。1 で終わるときは待たない（取得しないのにずらしても意味がない）
3. 排他（§3.5）が取れなければ、`busy_wait_sec` まで 1 秒ごとに取り直す。取れるまでの間に別のプロセスが取得し終えていれば
   （`last_success_at` が待ち始めた時刻以降）、その結果を使って `skipped` で終わる。待っても取れなければ従来どおり `busy`
4. 排他を取ったあと、改めて 0・1 と失敗の待ちを見る（ずらしている・待っている間に別のワークフローが取得した・失敗した場合）

DAG の get ステップの `timeout_sec` には、`jitter_sec` と `busy_wait_sec` を足す。

**何本のワークフローが取得するか**は、0.8.0 までは設定のルールから数え、2 本以上で `schedule` が無ければ `min_interval_sec`・`busy_wait_sec`
を必須にしていた。consumer は設定に現れない（ワークフローの argv にだけある）ので数えられない。必須の検査はやめ、README で「2 本以上が
同じ取得元を `get` するなら `schedule` か `min_interval_sec` と `busy_wait_sec` を書く」と案内する。

**失敗の共有**: 取得が失敗したら（一時的な失敗も、認証などの直らない失敗も）、取得元に `retry_not_before` = 今 + `failure_backoff_sec` ×
2^(連続の失敗回数)（上限 `failure_max_backoff_sec`。API の `Retry-After` が長ければそちら）を書く。この時刻までは、どのプロセスも
（失敗したワークフローの Dagu の再試行も）問い合わせず `deferred`（終了コード 0）で終わり、`get` は前回までの取得結果で答える。
ずらし・待ちのあとにも見直す。成功したら消す。`--force` は見ない。従来は `Retry-After` が無い失敗のあと、起動したワークフローがそれぞれ
すぐに取り直していた（ページごとに `retry.attempts` 回 × Dagu の再試行 × ワークフローの本数）。

**1 分あたりの上限**: `rate_limit.per_minute` は 1 プロセスの中で数えていたので、取得し終えた直後に別のワークフローが取得すると上限を超えた。
送った要求の時刻（直近 60 秒）を取得元の `recent_requests` に残し、次のプロセスはそれを数えてから始める。取得は排他（§3.5）で
同時には 1 つなので、これで取得元ごとの上限になる。

**同じ API の別の取得元（`rate_limit.group`）**: 同じ group の取得元は、排他（§3.5）を group で 1 つ（`fetch-group:<名前>`）にして同時には
取得しない。要求の時刻（`last_request_at`・直近 60 秒の `recent_requests`）と失敗の待ち（`retry_not_before`）を `rate_groups` の行に持ち、
`rate_limit.min_interval_sec`・`per_minute` は group の全部の要求で数え、どれかが失敗したら group の全部が待つ。group の中で間隔・上限の値が
違うと、どれが効くか分からないので設定の誤りにする。取得は同時に 1 つなので、要求の数え方はプロセスの中の記録と DB の記録の継ぎ足しで足りる。

## 4. ワークフローから使う口（`get` / `commit` / `call`）

条件の判定と処理はワークフロー（Dagu）に置き、change-watch は API へのアクセスだけを受け持つ（§9 の経緯）。口は 3 つ（`changewatch/access.py`・
`changewatch/cli.py`）。

| 口 | すること |
|---|---|
| `get --source S` | 取得して（するかは §3.6）、最後に取得し終えたレコードの一覧を返す |
| `get --source S --consumer C [--watch P]… [--initial baseline\|emit] [--one]` | C が前回 `commit` した姿（`consumer_records`）と今の姿（`source_records`）を key ごとに比べ、`appeared` / `changed` / `removed` を返す |
| `commit --source S --consumer C KEY… [--refresh]` | KEY の今の姿を C の「処理し終えた姿」にする。`--refresh` なら取得元の `item` で 1 件取り直した姿にし、取得元の姿もそれに替える |
| `call METHOD URL --source S` | 取得元の接続（認証）・要求の間隔・失敗の待ちを分け合って、API を 1 回呼ぶ。GET 以外は自分では再試行しない |

- **取得の失敗で止めない。** `get` の前の取得が失敗しても、警告を出して前回までの取得結果で答え、終了コード 0 にする。ワークフローの
  処理と `commit` が、API の一時的な不調で止まらない（設定の誤りは止める）
- **consumer ごとに独立。** 同じ取得元を `get` するワークフローが何本あっても、変化はそれぞれの「処理し終えた姿」から数える。
  ワークフローを足しても他のワークフローの受け取り方は変わらない
- **初めての consumer と世代の変化。** `consumers` に取得元の世代（§3.1）を持ち、初めて呼ばれたとき・世代が変わったときは、`baseline`（既定）
  なら今の姿を覚えるだけ、`emit` なら全部を `appeared` で返す
- **`commit` まで返し続ける。** `get` は状態を変えない。処理が失敗して `commit` されなければ、次の起動でも同じ変化を返す。
  例のワークフローは失敗しても `commit` し（`continue_on.failure`）、やり直しは Dagu の `retry_policy` に任せる
- **自分の書き込みを変化にしない。** 処理の中で API に書き込むと、次の取得でその書き込みが変化に見える。`commit --refresh` は書き込んだ後の
  姿を取り直して基準にする。取り直したレコードの key が違えば設定の誤りにする
- **比べ方。** `--watch` が無ければレコード全体、あればそのパスの値だけを、キー順に依らず比べる（true と 1 は別）。`changes` は辞書を再帰し、
  長さが同じ配列は添字ごとにたどる。前回の取得と比べるのではなく前回 `commit` した姿と比べるので、`commit` までに A→B→A と変われば変化なし

## 6. 運用

- 接続設定: connections.yaml のパスはコマンド行の `--connections` だけで受け取り、環境変数（`SKILL_CONNECTIONS_FILE` など）や
  決まった場所（`.github/connections.yaml` など）からは探さない。どのファイルの認証情報を使うかを、実行のたびに呼び出し側が決める。
  Dagu のワークフローはそのパスを argv に書く。ファイルの中の `${VAR}` は connections.yaml の書式として展開する
- 場所はすべてコマンド行で渡す: `--config`（設定）・`--connections`（接続設定）・`--state-dir`（状態。設定の `state_dir` より優先）。
  環境変数では上書きしない（呼び出し側の環境に左右されず、ワークフローの argv を見れば何を使うか分かる）

- `status`: 取得元ごとの最後の取得（ページ数・件数・要求回数・304 のページ数）・エラー・途中の取得・待たされている時刻、
  consumer ごとの処理し終えた件数と最後の `commit`
- 認証情報は出さない: 接続設定の値（URL・ユーザー名以外）と、名前が秘密らしいヘッダー・クエリの値を覚えて、ログ・保存するエラー文・
  状態の表示で `***` に置き換える。URL のクエリでトークンらしい名前の値も伏せる。別ホストへのリダイレクトでは認証のヘッダーを落とし、
  次ページの URL が別ホストなら止める
- 保持期間: 取得結果は「最新 N 回」「再開できる途中の取得」を残し、それ以外を `fetch_days` で消す。`get` は最新の取得結果
  （と組み立てた全体の姿）しか見ないので、古い取得結果を残す理由は再開と調べもの（`records --fetch`）だけ
- 肥大化の防止: 新しい DB は `auto_vacuum=INCREMENTAL` で作り、`gc` の最後に `incremental_vacuum` と `wal_checkpoint(TRUNCATE)` を流して、
  消した行の領域をファイルから返す（`incremental_vacuum` は 1 step で 1 ページ返すので、最後まで step する `executescript` で流す。
  `execute` では Python 3.11 などで 1 ページしか返らなかった）。`auto_vacuum` は表を作る前でないと効かないので、古い DB は
  `gc --vacuum`（VACUUM）で切り替える
- 行が増え続けるものの期限: 増分取得で組み立てた全体の姿は、最後に取得した時刻（`seen_at`）が `record_days` より古いものを消す。
  消すと consumer には `removed` で返り、次に取得されたとき `appeared` で返るので、既定は消さない
- 0.7 までの表（ルールの判定状態・イベント・受け渡し）とイベントのファイルは、`gc` が見つけたら消す（`store.LEGACY_TABLES`）

## 7. 範囲外

- Webhook の受信（外からの着信を要しないことが前提）
- 条件の判定・後続処理の実行・並列数の制御・再試行（Dagu に任せる）
- 1 つの取得元を複数の PC で分担すること。排他は同じ状態のフォルダを見るプロセスどうしに効く
- 応答が JSON 以外の API
- フォルダの中身の変化を即座に（1 分より短い間隔で）知ること。フォルダも起動のたびに一覧する（§3.1.1）

## 8. 検証

`tools/change-watch/test/`（標準ライブラリだけ。CI は Linux と Windows で回す）。

- `test_access.py`: `get`（consumer なし・あり）・`commit` まで返し続ける・consumer ごとに独立・`--initial emit`・`commit --refresh` で自分の書き込みを
  変化にしない・取得の失敗では前回までの結果で答える・`call` の書き込み（認証を分け合う・再試行しない）・フォルダの取得元・`key` の必須
- `test_shared.py`: 取得の共有（2 本のワークフローで問い合わせ 1 回・ずらし・取得中のプロセスを待ってその結果を使う・失敗の共有と倍々の待ち・
  `per_minute` を前のプロセスの要求も数える）、cron の解釈、予定の時刻での取得、`rate_limit.group`
- `test_flow.py`: 偽の API（手元の HTTP サーバ。5 種のページング・ETag・増分・失敗の注入・ページの途中で止める）で、各ページング、
  増分の位置が失敗で進まないこと、組み立てた全体の姿、304、再試行と長い Retry-After、認証エラーで再試行しない・
  トークンがログ・状態・DB・WAL に残らないこと、途中で失敗した取得の続きからの再開（途中の結果を `get` に使わない）、強制終了したプロセスからの
  引き継ぎ、取得元の認証の変更、保持期間・ファイルを縮めること・0.7 までの表を消すこと、YAML の設定、接続設定を `--connections` からだけ読むこと、
  `rules` / `delivery` が残った設定を案内して止めること、プラグインのフィルタ
- `test_folder.py`: フォルダの取得元（追加・更新・削除・書き込み中のファイル・フォルダが無いときに消えたとしない・`include` / `exclude` / 隠しファイル・下のフォルダ・中身とハッシュ・`max_files`・設定の検査・`path` を変えたときの基準の取り直し）
- `test_dag_examples.py`: `dags/` の例のワークフローの形（depends・取得の再試行は終了コード 3 だけ）と、`get` → 条件で分ける → `commit` の例と
  整理の例を `test/dagrun.py` で Dagu と同じ順に本物の change-watch へ流して動くこと
- `test_install.py`: `.pyz` を作り、別の場所へ動かしてもその 1 ファイルだけで `get` まで動くこと、
  site-packages を読まない（`python -S`）ときに同梱の PyYAML で YAML の設定を読めること、インストールと削除（Windows は `.cmd`）、
  `--agent` で 4 つのエージェントが同じ決まりの場所に置かれ、置いたワークフローのとおりの argv で `check` が通ること、入れ直しで
  プログラムは新しくなり直した設定は残ること、Windows の形（`command` が Python・`args` の先頭が `.pyz`）に置き換わること

## 9. 0.8.0 でルールと受け渡しをなくした経緯

0.7 までは、条件も change-watch の設定に書けた。ルール（`rules`）が取得結果を古い順に判定してイベントを作り（`evaluate`）、受け渡し先
（`targets`）へ渡す（Dagu の DAG へ `dispatch`、その場のコマンドを `work`、外のワークフローが `claim` → `render` → `finish`）。受け渡しの ID を
Dagu の実行 ID にして渡し直しを 1 回の実行に収め、レコードごとの順序・再送・完了の記録（`ack`・`reconcile`）・実行の前後の取り直し
（`refresh`）と、それを判定するプラグインのチェック（`skip_if`・`handled_before_if`）まで持っていた。

PR #1024（0.6.0）で、条件と分岐は Dagu の `preconditions` に、change-watch は `get` / `commit` / `call` に、という役割分担を決め、エージェントの
既定のワークフローもすべてその形にした。ルールの形は `examples/rules/` と `change-watch.rules.yaml.example` に並べて残し、例が固まったら
消すとしていた。0.8.0 でそれを消した。

| 残していた理由 | 消してよいと判断した理由 |
|---|---|
| 取得を古い順に全部比べるので A→B→A も 2 回の変化として拾える | ワークフローは「今の姿」で動くので、途中の B を処理したい用途がエージェントに無かった。全部の変化が要るなら、履歴の API を取得元にする形が README にある |
| 受け渡しの順序・再送・完了の記録 | Dagu の `retry_policy`・実行履歴・キューで足りる。change-watch と Dagu の両方に「やり直し」の状態があると、どちらを見ればよいか分からなかった |
| 実行の前後の取り直し（`refresh`）とチェック | `commit --refresh` と、エージェントのコマンド側の冪等（MR のレビューは差分の指紋でまとめを 1 回だけ書く）で足りた |

消したもの: `changewatch/detect.py`・`deliver.py`・`adapters/`・`plugins/dagu.py`（`--plugin dagu`）、設定の `rules`・`delivery`・`retention.events_days`・
`removed_days`、コマンドの `evaluate`・`dispatch`・`work`・`due`・`begin`・`claim`・`render`・`finish`・`run`・`ack`・`reconcile`・`retry`・`skip`・
`events`・`show`・`rebaseline`・`replay`・`forget-handled`・`forget-rule`、プラグインの `add_adapter`・`add_check`、条件式（`jpath.check`）、
エージェントのプラグイン（`gitlab_issue.py`・`redmine_issue.py` と、`gitlab_mr.py` のフィルタとチェック）、`dags/` のルール用の例 5 つと
`examples/rules/`。約 7,000 行が減り、本体は Dagu を知らなくなった（Dagu を知っているのは `dags/` の例だけ）。

古い設定に `rules` か `delivery` があると、黙って無視せずに案内して止める（無視すると、処理が止まったことに気づけない）。
