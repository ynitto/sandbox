# api-watch 設計

任意の Web API をローカルからポーリングし、レコードの変化・条件の成立を検知して Dagu の DAG へ渡す汎用の仕組み。
実装は `tools/api-watch/`、使い方は同フォルダの README。

## 1. 位置づけ

GitLab の issue の変化でエージェントを動かす [gitlab-issue-agent](./gitlab-issue-agent-design.md) のような用途から、「API の取得」「変更の検知」
「受け渡し」を汎用に切り出したもの。**実行（後続処理）は Dagu のワークフローかその場のコマンドに渡す**。gitlab-issue-agent は
取得・検知・実行待ち・1 セッションの排他を本設計に、エージェント CLI の実行を Dagu の `harness.run` に任せ、GitLab 固有の部分を
本設計のプラグイン（`gitlab_issue.py`）として持つ。

| 方針 | 決め方 |
|---|---|
| 常駐しない | 3 つの段（fetch / evaluate / dispatch）を Dagu のステップとして起動する。各段は状態を読んで進めて終わる |
| 実行管理は Dagu | 定期実行・再試行・ログ・履歴は Dagu の DAG、後続処理の並列数はキュー、後続処理の再試行・復旧は後続の DAG の `retry_policy` と `dagu retry` |
| このツールが持つもの | Dagu では表せない状態だけ: 取得の途中経過と位置、ルールごとの前回の観測値、イベントと受け渡しの状態 |
| 特定のサービス・言語に依らない | 要求・ページング・増分・抽出・判定はすべて設定。後続処理は任意の DAG（言語は問わない） |
| Dagu 連携は本体の外 | 本体（`apiwatch/`）は受け渡しをアダプタの口（§5.3）越しにだけ行い、Dagu を知らない。Dagu へのアダプタと DAG の生成は 1 ファイルのプラグイン `apiwatch/plugins/dagu.py` で、`--plugin dagu` のときだけ読み込む |
| 1 ファイルで配る | インストーラ（`install.py`）が本体と同梱のプラグインを zipapp（`.pyz`）にまとめ、コマンドとして置く（§2.0） |
| 認証情報の場所は実行時に渡す | connections.yaml のパスはコマンド行の `--connections` だけで受け取る（§6） |

## 2. 構成

```
            ┌─────────────── state.db（SQLite・WAL・synchronous=FULL）───────────────┐
 fetch ───▶ │ sources（指紋・世代・位置・待ち時刻）  fetches / fetch_pages（ページ単位） │
            │ http_cache（ETag と本文）              source_records（key ごとの最新）   │
 evaluate ─▶│ rules（判定位置・指紋）                rule_records（前回の観測値）       │
            │ events（変更 1 件 = ルール×レコード×取得）                                │
 dispatch ─▶│ deliveries（イベント×受け渡し先: pending→sending→dispatched→done/failed）│
 ack ─────▶ │ leases（取得・判定の排他）                                                 │
            └──────────────────────────────────────────────────────────────────────────┘
```

### 2.0 本体と Dagu プラグインの分離・配布

| 置き場所 | 責務 | 依存 |
|---|---|---|
| `apiwatch`（本体） | 取得・判定・状態・受け渡しの行の管理（順序・再送・ack・retry・skip）・運用のコマンド | 標準ライブラリ（YAML の設定なら PyYAML） |
| `apiwatch.adapters` | アダプタの口（`parse` / `create`、`enqueue` / `retry` / `status`）と、任意のコマンドで渡す `command` | 本体だけ |
| `apiwatch.plugins` | `--plugin` で指定されたプラグインの読み込み（同梱の `apiwatch/plugins/<名前>.py`、または任意の `.py` のパス）。パッケージ自体は同梱のプラグインを import しない | 本体だけ |
| `apiwatch/plugins/dagu.py` | `dagu` アダプタ（CLI / REST API）と `dags` コマンド（DAG の生成）。Dagu に依存するものはこの 1 ファイルだけ | 本体の公開部品 + Dagu |

- プラグインは `register(api)` を持つ 1 つの `.py` で、`api.add_adapter(name, parse, create)`・`api.add_command(name, fn, help)` を呼ぶ。
  本体はコマンド行の `--plugin` を先に読み（`parse_known_args`）、指定されたプラグインだけを読み込んでから残りの引数を解釈する。
  サブコマンド（`dags`）はプラグインが足すので、指定しなければ存在しない
- 自動では読み込まない。どのプラグインを使うかを実行のたびに呼び出し側が決め、`dags` は自分を読み込んだ `--plugin` を生成する DAG にも入れる。
  1 回の実行の始めにプラグインの登録を外すので、同じプロセスで続けて呼んでも前の指定は残らない
- 設定の `delivery` は名前と設定を持つだけで、アダプタは使う時点（dispatch・reconcile・retry・check）で解決する。だから `status`・`events`・`ack`
  はプラグインなしで動き、`--plugin` を付け忘れた dispatch は設定の誤り（終了コード 1）で止まって、受け渡しの試行回数を減らさない
- テストで、本体の主要モジュールを読み込んでも `apiwatch.plugins.dagu` が読み込まれないこと、`--plugin` なしでは `dags` も `dagu` アダプタも無いこと、
  `.py` のパスでも読み込めることを確かめている
- 配布: `install.py build` が `apiwatch/`（同梱のプラグインを含む）の `.py`（テストと `__pycache__` は除く）と入口の `__main__.py`、それに PyYAML があれば
  その純 Python の部分とライセンス文を、`zipapp` で 1 つの `.pyz` にまとめる。C 拡張（`_yaml`）は zip から読み込めないので入れない（PyYAML は
  無ければ純 Python で動く）。`install` は Linux 系では `#!` 付きの実行ファイル `~/.local/bin/api-watch`、Windows では `.pyz` と `.cmd` を置く。
  `.pyz` から動いているとき、`dags` は DAG から呼ぶ入口をその `.pyz` にする（`apiwatch` の親が zip のファイルかで見分ける）
- アダプタが約束するのは「同じ run_id を 2 回実行しない（2 回目は duplicate を返す）」こと。本体の再送の正しさはこれだけに依る
- 本体の語彙は「ワークフロー（`targets[].workflow`）」「run_id」。Dagu の DAG 名・実行 ID への読み替えはアダプタが持つ
- 起動の間隔（取得元の `schedule`）は基盤に依らない cron の文字列として本体の設定に置き、`dags` がそれを DAG の `schedule` に写す。
  DAG だけに要る項目（dotenv・再送 / 整理の DAG の間隔・履歴の日数）は設定に置かず `dags` の引数にした（本体の設定に Dagu の節を作らない）

段を分けたのは、Dagu の再試行がその段だけをやり直すようにするため。判定や受け渡しを再試行しても、
成功済みの取得は繰り返さない（生成する DAG は fetch → evaluate → dispatch の 3 ステップで、fetch だけが
終了コード 3 で再試行する）。

### 2.1 状態を SQLite にした理由

- 取得 1 ページ・判定 1 回・受け渡し 1 件の遷移を、それぞれ 1 トランザクションで書ける。途中で停止しても
  「観測値は進んだがイベントが無い」「イベントはあるが受け渡しが無い」が起きない
- Windows・WSL2・Linux の標準ライブラリにあり、追加の導入が要らない。`BEGIN IMMEDIATE` で複数のプロセス
  （取得元ごとの DAG・再送の DAG・後続の DAG からの ack）の書き込みを直列にできる
- JSON のファイル 1 つ + flock は 1 プロセス 1 ファイルの読み書きには十分だが、受け渡し先ごと・レコードごとの
  状態を持つと全体の書き戻しが重く、部分の遷移を原子的にできない
- 制約: SQLite のロックはネットワーク越し・`/mnt/c` 越しでは効かない。状態のフォルダは動かす OS のローカルに置く（README に明記）

## 3. 取得

### 3.1 取得元と指紋

取得元は「要求（URL・メソッド・ヘッダー・クエリ・本文）・抽出・ページング・増分・接続設定」の組で、ルールはこれを名前で参照して
共有する。ルールや受け渡し先が増えても API への問い合わせは取得元ごとに 1 回で済む。

取得元の**指紋** = 間隔やレート制限など取得条件に関係しない項目を除いた設定 + 接続設定の (サービス, ラベル) + 接続設定の値のハッシュ。
指紋が変わると世代（epoch）を進め、前の世代の取得結果・位置・ETag の控え・組み立てた全体の姿を使わない。ルールは世代が
変わったら基準値を取り直す。これで「取得条件や認証・権限が違うデータを、意図せず同じものとして比べる」ことが起きない。
認証情報そのものは保存せず、ハッシュだけを指紋に入れる。

### 3.2 ページ単位の進行と再開

`fetches` の 1 行が 1 回の取得で、`page_state`（次のページの取り方: ページ番号・オフセット・カーソル・次の URL・
増分の途中の最大値・ループ検出用の既出カーソル）を持つ。1 ページ取るたびに、そのページのレコードと次の `page_state` を
同じトランザクションで書く。

- 失敗（一時的・恒久的とも）では `failed` にして `page_state` を残す。次の取得は `resume_max_age_sec` 以内なら続きから再開し、
  古ければ破棄して最初から取る（古い途中経過に新しいページをつなぐと、ページの境目のずれが大きくなるため）
- 最後のページを書いたあと「完了」にする前に止まったときは、`page_state.done` を見て取り直さずに完了処理だけ行う
- **完了するまで判定しない・位置を進めない。** 全件取得の途中の結果を判定すると、未取得のレコードが「消えた」に見える。
  増分の途中で位置を進めると、未取得の範囲を二度と取らない
- `max_pages` を超えたら完了にしない（打ち切りを「全部取れた」と扱わない）

### 3.3 増分取得

| 方式 | 位置 | 次の要求 |
|---|---|---|
| `max_value` | 取得したレコードの指定項目の最大値（数どうし・文字列どうしだけ比べる） | `param=<最大値>` |
| `cursor` | 応答の指定箇所の値（最後のページのもの） | `param=<値>` |

`>=` 系の API で境界のレコードが 2 回取れても、観測値が同じなので発火しない。増分取得の結果は一部のレコードだけなので、
`key` を書いた取得元では完了のたびに `source_records`（key ごとの最新のレコード）を更新し、ルールの基準値の取り直しは
この全体の姿で行う（直近の取得だけで基準値を取ると、そこに無いレコードが次に取れたとき `appeared` で大量に発火する）。

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
叩いても、同じ取得元を同時に 2 回取らない。判定も取得元ごとに排他する（`eval:<名前>`）。

### 3.6 いつ取得するか・取得の共有とずらし

**起動と取得を分ける**: いつ起動するかは Dagu（DAG の `schedule`）が、起動されたときに API へ問い合わせるかは api-watch が取得元ごとに決める。
初版は設定の `schedule` を `dags` が DAG に書き写していたが、起動の間隔が設定と DAG の 2 か所に分かれ、設定だけ直すとずれた。
今は書き写さない。取得元の `schedule`（cron。`apiwatch/cron.py`。この PC の時刻）は「問い合わせの予定」で、`fetch` が起動されるたびに
「直近の予定の時刻より後に、この取得元を取得し終えているか」（`last_success_at` ≥ 直近の予定の時刻）を見て、済んでいれば `skipped` で終わる。
DAG は毎分起動にしておけば、予定は api-watch の設定 1 か所で決まる。取得元に `schedule` が無ければ起動されるたびに取得し、予定は
Dagu の起動間隔に任せる（`dags --schedule` か手で書く）。どちらでも動く。

`dags` は `--schedule` が無ければ毎分の DAG を書く。取得元に `schedule` も `min_interval_sec` も無いと毎分問い合わせることになるので、
そのときは止めて `--schedule` か設定を求める。`dags` は雛形を作るだけで、既にあるファイルは `--force` のときだけ上書きする
（DAG の `schedule` は利用者が直す場所なので、作り直しで消さない）。

1 つの取得元を、それぞれ自分のワークフロー（§5.5）で取得から処理まで行う複数のルールが使うと、同じ API を何度も呼びかねない。
`fetch` は次の順で判断して、問い合わせを 1 回にまとめる。

0. `schedule` があり、直近の予定の時刻のあとに取得し終えていれば、待たずに `skipped` で終わる。続く `evaluate` はその取得結果を使う
1. `min_interval_sec` の間に（同じ指紋で）取得し終えていれば、待たずに `skipped` で終わる
2. `jitter_sec` があれば 0〜その秒数だけランダムに待つ（定刻に複数の PC・ワークフローが API へ集中しない）。手で叩くときは
   `--no-jitter`、`--force` でも待たない。1 で終わるときは待たない（取得しないのにずらしても意味がない）
3. 排他（§3.5）が取れなければ、`busy_wait_sec` まで 1 秒ごとに取り直す。取れるまでの間に別のプロセスが取得し終えていれば
   （`last_success_at` が待ち始めた時刻以降）、その結果を使って `skipped` で終わる。待っても取れなければ従来どおり `busy`
4. 排他を取ったあと、改めて 0・1 と失敗の待ちを見る（ずらしている・待っている間に別のワークフローが取得した・失敗した場合）

DAG の fetch ステップの `timeout_sec` には、`jitter_sec` と `busy_wait_sec` を足す。

**何本のワークフローが取得するか**（`Config.fetching_workflows`）: command の受け渡し先を持つルールは自分のワークフローで、それ以外のルール
（とルールの無い取得元）は取得元のワークフローで取得する。2 本以上で、取得元に `schedule` が無いなら（`schedule` があれば 0 だけで
予定の時刻ごとに 1 回にまとまる）、設定の読み込みで次を必須にする（注意ではなく誤りにする。抜けると、起動したワークフローの数だけ毎回問い合わせる）:

- `min_interval_sec` > 0（成功した取得は、ワークフローの本数によらず `min_interval_sec` に 1 回まで）
- `busy_wait_sec` > 0（待たずに終わると、続く `evaluate` が前回までの結果しか見ず、次の起動まで処理が遅れる）
- `jitter_sec` < `min_interval_sec`（ずらしが長いと、先に取得したワークフローの結果が `min_interval_sec` を過ぎてから起きるワークフローがいて、
  もう一度問い合わせる）

**失敗の共有**: 取得が失敗したら（一時的な失敗も、認証などの直らない失敗も）、取得元に `retry_not_before` = 今 + `failure_backoff_sec` ×
2^(連続の失敗回数)（上限 `failure_max_backoff_sec`。API の `Retry-After` が長ければそちら）を書く。この時刻までは、どのプロセスも
（失敗したワークフローの Dagu の再試行も）問い合わせず `deferred`（終了コード 0）で終わり、前回までの取得結果で判定を続ける。
ずらし・待ちのあとにも見直す。成功したら消す。`--force` は見ない。従来は `Retry-After` が無い失敗のあと、起動したワークフローがそれぞれ
すぐに取り直していた（ページごとに `retry.attempts` 回 × Dagu の再試行 × ワークフローの本数）。

**1 分あたりの上限**: `rate_limit.per_minute` は 1 プロセスの中で数えていたので、取得し終えた直後に別のワークフローが取得すると上限を超えた。
送った要求の時刻（直近 60 秒）を取得元の `recent_requests` に残し、次のプロセスはそれを数えてから始める。取得は排他（§3.5）で
同時には 1 つなので、これで取得元ごとの上限になる。

**同じ API の別の取得元（`rate_limit.group`）**: 同じ group の取得元は、排他（§3.5）を group で 1 つ（`fetch-group:<名前>`）にして同時には
取得しない。要求の時刻（`last_request_at`・直近 60 秒の `recent_requests`）と失敗の待ち（`retry_not_before`）を `rate_groups` の行に持ち、
`rate_limit.min_interval_sec`・`per_minute` は group の全部の要求で数え、どれかが失敗したら group の全部が待つ。group の中で間隔・上限の値が
違うと、どれが効くか分からないので設定の誤りにする。取得は同時に 1 つなので、要求の数え方はプロセスの中の記録と DB の記録の継ぎ足しで足りる。

## 4. 判定

### 4.1 観測の列を順に比べる

ルールは取得元の完了した取得を古い順に 1 回ずつ判定し、`rules.last_fetch_id` を進める。1 回の判定（観測値の更新・
イベントと受け渡しの追加・位置の前進）は 1 トランザクション。取得が判定より先に何回か進んでも、間の観測を飛ばさないので
A→B→A は 2 回の変化として検知する（取得の間隔の中で起きて戻った変化は原理的に見えない。README の「限界」）。

### 4.2 正規化

1. `ignore` のパスを取り除く（ワイルドカード可）
2. 配列: `array_order: ignore` なら全配列を、`unordered` のパスの配列だけを、要素の正規化 JSON の順に並べる
3. `watch` のパスの値を取り出す。値が無いパスは辞書に入れない（null とは別。`missing_is_null` で同一視）
4. キー順に依らない JSON（`sort_keys`）のハッシュで前回と比べる。true と 1 は別の値として扱う

差分（`changes`）は辞書を再帰し、長さが同じ配列は添字ごと、それ以外は丸ごと `changed` とする。

### 4.3 種類と条件

`filter` は対象の範囲、`condition` は発火条件で、役割を分けた。`filter` から外れたレコードは追跡をやめ（戻ってきたら
`appeared`）、全件取得で消えても `removed` にしない。`condition` は一致の状態を観測値と一緒に持ち、`entered` / `exited` /
`matching` を出す。初めて見たレコードの「前の状態」は不一致とみなす（初回の基準値の取得では発火しない）。

イベント ID = ハッシュ(ルール名, ルールの判定の指紋, レコードのキー, 取得の ID)。判定のやり直し（`replay`）で同じ取得を
同じルールで判定すれば同じ ID になり、既にあるイベントは積まない（`INSERT OR IGNORE`）。ルールの判定の仕方を変えれば
別の ID になる。

### 4.4 基準値

| 場面 | 振る舞い |
|---|---|
| 初回 | 最新の取得結果（増分 + `key` なら全体の姿）を基準値にする。`initial: emit` なら同時に既存のレコードを `appeared` で出す |
| 判定の仕方の変更（指紋が変わった） | 既定（`rebaseline`）は最新の取得結果で基準値を取り直す。`continue` は新しい比べ方で前回の値と比べ続ける |
| 取得元の世代が変わった | 必ず基準値を取り直す（`initial` は効かない） |
| `rebaseline` コマンド | 次の判定（`--now` ならいま）で取り直す。`--refetch` で次の取得を全件にする |

`on`・`targets`・`initial` の変更は判定の仕方ではないので基準値を取り直さない。

## 5. 受け渡し

### 5.1 1 回だけ実行される受け渡し

受け渡しの ID（`aw_` + ハッシュ(イベント ID, 受け渡し先)、Dagu の実行 ID の制約 `^[-a-zA-Z0-9_]+$`・64 文字以内に収まる）を
そのまま Dagu の実行 ID にする。Dagu は同じ DAG・同じ実行 ID の enqueue を断る（CLI は `already exists`、API は 409）ので、
このツールが「渡したかどうか分からない」状態から渡し直しても、後続処理は 1 回しか走らない。断られたら渡せたものとして記録する。

| 状態 | 意味 | 次 |
|---|---|---|
| `pending` | 未受け渡し | dispatch |
| `sending` | 渡している最中（期限つき）。期限切れは渡せたか分からない | 同じ ID で渡し直す |
| `send_failed` | 渡せなかった | バックオフ後に同じ ID で渡し直す |
| `dead` | `max_attempts` に達した | 手で `retry`（`pending` に戻す） |
| `dispatched` | Dagu のキューに入った（= 受け渡し済み） | ack / reconcile |
| `done` / `failed` | 後続処理が成功 / 失敗 | `failed` は `retry` で Dagu に同じ実行 ID のまま再実行させる |
| `skipped` | 手で飛ばした | — |

「取得・観測済み」は fetches / rule_records、「Dagu への受け渡し済み」は `dispatched`、「後続処理の完了」は `done` で区別する。
受け渡しはイベント × 受け渡し先ごとの行なので、1 つの受け渡し先の成功が他を完了にすることはない。

`sending` → `dispatched` の更新は「まだ `sending` で、自分が持ち主のとき」だけ行う。後続処理が速く終わって先に `ack` しても、
完了を上書きしない。

### 5.2 順序と並列

受け渡し先の `order` で、同じ (ルール, 受け渡し先, レコード) の受け渡しの間だけ順序を守る。dispatch はイベントの発生順（`seq`）に
なめ、前に未完了のものがあれば飛ばす（`completion` は `done` / `skipped` 以外すべて、`enqueue` は Dagu に入る前の状態だけを数える）。
別のレコード・別の受け渡し先は制約を受けないので、Dagu のキューの並列数まで並ぶ。失敗した受け渡しが止めるのは、同じレコードの
後ろのイベントだけ。`skip` で先へ進められる。

### 5.3 アダプタの口と Dagu アダプタ

本体は `enqueue(run_id, workflow, params, queue) -> ok | duplicate`・`retry(run_id, workflow)`・`status(run_id, workflow) -> done | failed | 不明`
だけを呼ぶ（`apiwatch/adapters/__init__.py`）。`command` アダプタは argv のテンプレートで同じことをする（終了コード 9 か
`already exists` を duplicate とみなす）。以下は `apiwatch/plugins/dagu.py` の `dagu` アダプタ。

| mode | enqueue | 再実行 | 状態の問い合わせ |
|---|---|---|---|
| `cli` | `dagu enqueue --run-id=<ID> [--queue=Q] <DAG> -- K=V ...`（argv で渡し、シェルを通さない） | `dagu retry --run-id=<ID> <DAG>` | しない（出力は人向け）。後続の DAG から `ack` |
| `api` | `POST /api/v1/dags/{name}/enqueue`（`dagRunId`・`params`（JSON 文字列）・`queue`） | `POST /api/v1/dag-runs/{name}/{id}/retry` | `GET /api/v1/dag-runs/{name}/{id}` の `statusLabel`（`succeeded` / `partially_succeeded` → done、`failed` / `aborted` / `rejected` → failed） |

Dagu v2.25.2 のソース（`internal/cmd/enqueue.go`・`api/v1/api.yaml`・`internal/ir/run_id.go`）で、実行 ID の検査と重複時の応答を確かめた。

イベントの中身は引数に入れず、`<state_dir>/events/<イベント ID>.json` に書いてパスを渡す（大きなレコードや改行・記号を
コマンド行・Windows の引用規則に通さないため）。小さければ `inline_payload` で引数にも入れられる。

### 5.4 完了の記録

後続の DAG の `handler_on.success` / `failure` から `ack ${DAG_RUN_ID}` を呼ぶのが基本。`mode: api` なら再送の DAG の
`reconcile` が Dagu に問い合わせて記録するので、後続の DAG を変えずに済む（`command` アダプタでは `status_command`）。どちらも無い場合、`order: completion` の受け渡し先では
同じレコードの次のイベントが渡らない（README に明記）。

### 5.5 その場で実行する受け渡し先（command）と、1 本のワークフロー

受け渡し先に `workflow` の代わりに `command`（argv）を書くと、アダプタで別のワークフローへ渡さず、`work` がその場で実行する。
取得 → 判定 → 処理を 1 本のワークフロー（`fetch --source S` → `evaluate --rule R` → `work --rule R`）で行い、Dagu 側に
受け渡し先の DAG もキューも要らない。`run --rule R` は同じことを 1 つのコマンドで行う。

- 受け渡しの行（deliveries）・ID・順序（`order`）・試行回数と間隔（`max_attempts` / `retry_backoff_sec`）・`retry` / `skip` は
  `workflow` の受け渡し先と同じものを使う。状態は `pending` → `sending`（実行中。期限は `timeout_sec` + 120 秒）→ `done`、
  失敗は `send_failed`（間隔をおいて次の `work` で同じ ID のまま再実行）→ 上限で `dead`。`dispatched` と `failed` は通らない
- 1 回の `work` では同じ受け渡しを 2 回試さない（`retry_backoff_sec: 0` でも、失敗を同じ実行の中で上限まで繰り返さない）
- コマンドには `params_for` と同じ値を環境変数で渡し（`APIWATCH_EVENT_FILE` など）、argv にも `{{ event.* }}`・`{{ delivery.* }}`・
  `{{ env.* }}` を埋められる。標準出力・標準エラーはそのまま流す（Dagu のステップのログに出る）
- `timeout_sec` を過ぎたら終了を頼み、15 秒で強制終了して失敗として数える。`work` が止められた（SIGTERM など）ときは実行中のコマンドへ
  同じシグナルを伝え、終わるのを待って失敗として記録し、終了コード 1 で終わる
- 同じ `work` を 2 つ動かしても、`sending` への書き換えを条件付きの UPDATE で取り合うので、1 件を 2 回実行しない。プロセスが死んだら、
  期限が過ぎたあとの `work` が同じ ID で実行し直す（少なくとも 1 回。冪等にするのはコマンドの側）
- `dispatch` はこの受け渡し先を飛ばす。`workflow` の受け渡し先が 1 つも無ければ `delivery`（アダプタ）は要らない
- `dags` は、command の受け渡し先を持つルールごとに `<prefix>-<ルール名>` のワークフロー（fetch → evaluate → [dispatch] → work）の雛形を書く。
  起動間隔は `--schedule`（既定は毎分。§3.6）。毎分起動なら、予定の時刻以外は問い合わせず、`work` が失敗したもののやり直しだけを進める。
  work のステップには上限を付けない（1 件ごとの上限は `timeout_sec`）。
  どのルールも自分のワークフローを持つ取得元には取得元の DAG を、`workflow` の受け渡し先が無ければ再送の DAG を書かない

### 5.6 実行の前後の取り直し（`refresh`）と「処理し終えた状態」

ポーリングで拾った変化を実行するまでには間があり、実行するコマンド自身がレコードを書き換えることもある。どちらも受け渡し先ごとの事情なので、
ルールの観測値（`rule_records`。ほかの受け渡し先とも共有）は動かさず、`refresh` のある受け渡し先 × レコードごとに「最後に処理し終えた
状態」（`handled_records`。監視する値とそのハッシュ）を持つ。`work` の 1 件は:

1. イベントの `after`（監視する値）のハッシュが処理し終えた状態と同じなら、取り直さずに `done`（`last_error` に理由）。
   コマンド自身の書き込みで出たイベント・まとめて処理し終えた古いイベントはここで落ち、API への問い合わせは 0
2. `refresh` の要求で 1 件取り直す。404 → `done`（見つからない）。ルールの `filter` から外れた → `done`。監視する値が処理し終えた状態と同じ → `done`
3. 取り直したレコードを入れたイベントの JSON（`<イベント ID>--<受け渡し先>.json`。ほかの受け渡し先のファイルは書き換えない）と、
   `APIWATCH_ATTEMPT`・`APIWATCH_RESULT_FILE` を渡してコマンドを実行する
4. 成功したら、結果ファイルが `{"handled": "before"}` でなければもう一度取り直し、その監視する値を処理し終えた状態にする。
   `before` なら 2 の値（実行前）にする。取り直しに失敗したら 2 の値にとどめる（取りこぼさない側）

取り直しは排他（§3.5）を取らない。1 件だけで、取得の途中に挟まっても取得の結果を壊さないため。要求の間隔・1 分あたりの上限・失敗の待ちは
取得元（と `rate_limit.group`）と分け合い、取り直しの失敗も取得元を失敗の待ちに入れる。待ちの中なら、試行に数えずにあとへ回す。
処理し終えた状態は、ルールの観測値が消えたレコードのものを `gc` で消し、`forget-rule` でルールごと消す。`forget-handled` で個別に忘れさせる。

### 5.7 外のワークフローで処理する（`external`）と、プラグインの口

処理（エージェントの CLI など）を api-watch のプロセスの中で動かすと、止める・時間切れ・ログ・実行履歴を api-watch が持つことになり、
ワークフロー基盤の画面から見えない。`external: true` の受け渡し先は api-watch が実行せず、§5.5 の `work` の 1 件を、基盤のステップから呼べる
コマンドに分ける。

| コマンド | `work` の中のどこか | 状態 |
|---|---|---|
| `due` | 実行を待っている受け渡しを探す（`_blocked` で同じレコードの前を待つ） | 何も変えない |
| `begin` | 受け渡しを取る（`sending`・期限 `timeout_sec` + 120 秒）→ 処理し終えた状態との比較 → `lock` → `refresh` → `filter` → `skip_if` | 実行するなら `delivery_runs` に開始の時刻・実行前の監視する値・`lock` を書く |
| `render` | `begin` が書いたイベントの JSON（`<イベント ID>--<受け渡し先>.json`）でテンプレートを展開する | 何も変えない |
| `finish` | `handled_before_if` → 実行のあとの `refresh` → 処理し終えた状態の記録 → 完了 / 失敗 → `lock` を返す | `delivery_runs` を消す |

`begin` と `finish` は別のプロセスなので、`deliveries.owner`（プロセスごと）ではなく `delivery_runs` の行と `sending` の状態でつなぐ。
`lock` も同じ理由で、pid で死活を見る取得の排他（§3.5）とは別に、持ち主を受け渡しの ID にした期限つきの排他（`Store.hold`）にした。
`finish` が呼ばれないまま基盤ごと落ちても、受け渡しの期限と `lock` の期限が切れれば次の `begin` が取り直す（少なくとも 1 回）。
`work`（`command` の受け渡し先）も同じ `begin` / `finish` を 1 つのプロセスで呼ぶだけにして、振る舞いを 1 つにした。

ログは標準エラーに出す。標準出力を結果だけ（`due` の配列・`begin` の 1 語・`render` の文章）にして、Dagu のステップの `output:` で受けるため。

`claim` は `due` の古い順に `begin` を呼び、最初に `run` になった 1 件の ID を出す（`skip` などはそのまま次へ、`busy` / `wait` ならやめる）。
`finish --exit-code N` は処理のステップの終了コードで成否を決める（数でなければ失敗。ID が空なら何もしない）。

`dags` は `external` の受け渡し先を、ルールのワークフロー 1 本の中に `claim`（`output: DELIVERY`）→ `render`（`output: INPUT`）→
`handle`（`id: handle`・`continue_on: failure`）→ `finish ${DELIVERY} --exit-code ${handle.exit_code}` として書く。`render` 以降は
`preconditions` の `re:^aw_`（取れた ID があるとき）で動く。1 回の起動で 1 件だけ処理する。確かめたのは Dagu v2.25.2 のスキーマと実装
（`${<ステップ>.exit_code}` で前のステップの終了コードを参照できる、`harness.run` の `with.stdin` は文字列で、組み込みの claude などは
標準入力に流す）。書き出した DAG はそのスキーマで検証した。

当初は親（`due` の配列を `action: dag.run` の `parallel`（`max_concurrent: 1`）に渡す）と子（`begin` → `render` → `handle`、`handler_on` で
`finish`）の 2 本にしていた。1 回の起動で全部を流せ、子の `handler_on.abort` で中止も記録できたが、ワークフローが 2 本になる。1 本にするため、
1 回の起動で 1 件だけにし（同時に 1 つしか動かさないので待ちはほとんど変わらない）、中止は受け渡しの期限切れで取り直すことにした。
`handle` は処理ごとに違うので雛形にし、`--handle-step` で中身を差し込めるようにした。1 つのルールに `external` の受け渡し先は 1 つまで。

**プラグインの口**（`apiwatch/plugins/__init__.py`・`apiwatch/hooks.py`）: アダプタ・コマンドに加えて、フィルタ（テンプレートの関数）と
チェック（実行の前後の真偽）を名前で登録できる。プラグインは本体の内部に触らず、チェックの HTTP も `CheckContext.get_json` を通す。
取得元の接続・要求の間隔・1 分あたりの上限・失敗の待ちを分け合わせ、プラグインが API への負荷の約束を破れないようにするため。
チェックの例外は取りこぼさない側に倒す（`skip_if` は偽、`handled_before_if` は真）。

## 6. 運用

- 接続設定: connections.yaml のパスはコマンド行の `--connections` だけで受け取り、環境変数（`SKILL_CONNECTIONS_FILE` など）や
  決まった場所（`.github/connections.yaml` など）からは探さない。どのファイルの認証情報を使うかを、実行のたびに呼び出し側が決める。
  `dags` は渡されたパスを生成する DAG の argv に入れる。ファイルの中の `${VAR}` は connections.yaml の書式として展開する
- 場所はすべてコマンド行で渡す: `--config`（設定）・`--connections`（接続設定）・`--state-dir`（状態。設定の `state_dir` より優先）。
  環境変数では上書きしない（呼び出し側の環境に左右されず、生成した DAG の argv を見れば何を使うか分かる）

- `status`: 取得元ごとの最後の取得（ページ数・件数・要求回数・304 のページ数）・エラー・途中の取得・待たされている時刻、
  ルールごとの判定位置と未判定の回数・判定のエラー・受け渡しの内訳、失敗している受け渡しの一覧
- 認証情報は出さない: 接続設定の値（URL・ユーザー名以外）と、名前が秘密らしいヘッダー・クエリの値を覚えて、ログ・保存するエラー文・
  状態の表示で `***` に置き換える。URL のクエリでトークンらしい名前の値も伏せる。別ホストへのリダイレクトでは認証のヘッダーを落とし、
  次ページの URL が別ホストなら止める
- 保持期間: 取得結果は「最新 N 回」「どれかのルールが未判定」「再開できる途中の取得」を残し、それ以外を `fetch_days` で消す。
  イベントは受け渡しがすべて `done` / `skipped` のものだけ `events_days` で消す。設定から消したルールは保持の判断に入れない
  （`status` に出し、`forget-rule` で消す）
- 肥大化の防止: 新しい DB は `auto_vacuum=INCREMENTAL` で作り、`gc` の最後に `incremental_vacuum` と `wal_checkpoint(TRUNCATE)` を流して、
  消した行の領域をファイルから返す（`incremental_vacuum` は 1 step で 1 ページ返すので、最後まで step する `executescript` で流す。
  `execute` では Python 3.11 などで 1 ページしか返らなかった）。`auto_vacuum` は表を作る前でないと効かないので、古い DB は
  `gc --vacuum`（VACUUM）で切り替える
- 行が増え続けるものの期限: ルールの観測値のうち消えた・対象外になったもの（`present=0`）は `removed_days` で消す（また現れたら
  `appeared`）。増分取得で組み立てた全体の姿は、最後に取得した時刻（`seen_at`）が `record_days` より古いものを消す。消すと基準値の
  取り直しの対象から外れ、次に取得されたとき `appeared` で発火するので、既定は消さない

## 7. 範囲外

- Webhook の受信（外からの着信を要しないことが前提）
- 後続処理の実行・並列数の制御・再試行（Dagu に任せる）
- 1 つの取得元を複数の PC で分担すること。排他は同じ状態のフォルダを見るプロセスどうしに効く
- 応答が JSON 以外の API

## 8. 検証

`tools/api-watch/test/`（標準ライブラリだけ。CI は Linux と Windows で回す）。

- `test_shared.py`: command の受け渡し先（`run --rule` で取得から実行まで・失敗の再試行と打ち切り・`retry`・`dispatch` が触らないこと・
  時間切れ・設定の検査）、取得の共有（2 本のワークフローで問い合わせ 1 回・共有の設定が無ければ誤り・ずらし・取得中のプロセスを待ってその結果を使う・
  失敗の共有と倍々の待ち・`per_minute` を前のプロセスの要求も数える）、`dags` が書くルールごとのワークフロー・`--schedule`・既にある DAG を上書きしないこと、cron の解釈、予定の時刻での取得、`rate_limit.group`、
  `refresh`（自分の書き込みで再実行しない・実行中のほかの人の変化で再実行する・たまったイベントを最新で 1 回・filter から外れた／消えたら飛ばす・
  取り直しの失敗・`forget-handled`）、`external`（`due` → `begin` → `render` → `finish`・失敗の再試行と打ち切り・ルールをまたいだ `lock`・
  プラグインのフィルタとチェック・チェックの例外・`--plugin` の付け忘れ）、`claim` と `finish --exit-code`、`dags` が書く 1 本のワークフローと `--handle-step`

- `test_detect.py`: パス・条件式・テンプレート、正規化（キー順・配列の順序・`unordered`・`ignore`・欠落と null）、
  種類（A→B→A・`entered` / `exited` / `matching`・`filter` と `removed`・増分では消えたとしない）、複合 ID、イベント ID の安定性、設定の検査
- `test_flow.py`: 偽の API（手元の HTTP サーバ。5 種のページング・ETag・増分・失敗の注入・ページの途中で止める）と偽の dagu
  （本物と同じく同じ実行 ID を断る）で、取得の集約（ルール 2 本・受け渡し先 3 つで問い合わせ 1 回分）、各ページング、
  増分の位置が失敗で進まないこと、組み立てた全体の姿での基準値の取り直し、304、再試行と長い Retry-After、認証エラーで再試行しない・
  トークンがログ・状態・DB・WAL に残らないこと、途中で失敗した取得の続きからの再開、強制終了したプロセスからの引き継ぎ、ルールの変更、
  取得元の認証の変更、API なしでの判定のやり直し、判定のエラーの隔離、順序（completion / enqueue）、同じ ID での再送と受け渡し先ごとの隔離、
  受け渡し直後の停止からの復旧、ack / retry、REST API での受け渡し・完了の確認・409、保持期間が未処理を消さないこと、DAG の生成、
  YAML の設定、接続設定を `--connections` からだけ読むこと（決まった場所に置いても読まない・無ければ問い合わせ前に止まる）、
  `adapter: command` での受け渡しと重複の検出、本体が `apiwatch.plugins.dagu` を import しないこと、`--plugin` を指定したときだけ Dagu プラグインを使うこと
- `test_install.py`: `.pyz` を作り、別の場所へ動かしてもその 1 ファイルだけで取得から受け渡しまで動くこと、DAG がその `.pyz` を呼ぶこと、
  site-packages を読まない（`python -S`）ときに同梱の PyYAML で YAML の設定を読めること、インストールと削除（Windows は `.cmd`）
- 生成した DAG と `dags/api-watch-handler.example.yaml` を Dagu v2.25.2 の `internal/cmn/schema/dag.schema.json` で検証し、
  エラー 0 件を確認した（2026-11-04、手元）。Dagu 本体での実行はまだ確かめていない
