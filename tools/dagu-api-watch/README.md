# dagu-api-watch

任意の Web API を [Dagu](https://github.com/dagu-org/dagu)（v2）の定期実行からポーリングし、レコードの追加・変更・
条件の成立を検知して、設定した Dagu のワークフロー（DAG）へ渡す。外から受ける口は持たず、こちらから API を見に行く。
スケジュール・キュー・並列数・再試行・ログ・実行履歴は Dagu に任せ、このツールは常駐しない。

```
 Dagu: api-watch-<取得元>（取得元ごとの schedule）
   fetch     API を全ページ取得して保存        ← 1 つの取得元への問い合わせは、ルールがいくつあっても 1 回
   evaluate  保存した取得結果をルールごとに判定  ← API には問い合わせない
   dispatch  検知した変更を DAG のキューへ入れる  ← 実行 ID = 受け渡しの ID（渡し直しても 1 回だけ実行される）
                                 │
                                 ▼
 Dagu: 受け渡し先の DAG（キューで並列に実行。失敗は Dagu で再試行・復旧）── 終わったら ack
 Dagu: api-watch-dispatch（毎分）  未判定の取得結果の判定・渡せなかった変更の再送
 Dagu: api-watch-maintenance（毎日） 保持期間を過ぎたものの整理
```

- Python 3.10 以降。YAML の設定を使うなら PyYAML（`pip install pyyaml`）。JSON の設定なら標準ライブラリだけで動く
- Windows（ネイティブ）・WSL2・Linux で動く。状態は 1 つの SQLite ファイルに持つ
- 認証情報はスキル共通の `connections.yaml` に書く（このリポジトリの `.github/connections.yaml.example` と同じ形）

## 入れ方

1. フォルダごと置く（`api_watch.py` と `apiwatch/` が要る）。

   ```bash
   mkdir -p ~/.local/share
   ```

   ```bash
   cp -r tools/dagu-api-watch ~/.local/share/dagu-api-watch
   ```

2. 設定を書く。`api-watch.yaml.example` を `~/.config/dagu/api-watch.yaml` にコピーして取得元とルールを書く。

   ```bash
   cp tools/dagu-api-watch/api-watch.yaml.example ~/.config/dagu/api-watch.yaml
   ```

3. 認証情報を `connections.yaml` に書く。値は `${環境変数}` で参照し、環境変数は Dagu から読めるファイル
   （例 `~/.config/dagu/api-watch.env`。設定の `dagu.dotenv` に書くと生成する DAG が読み込む）に置く。

   ```yaml
   # ~/.github/connections.yaml など
   gitlab:
     - label: default
       url: https://gitlab.example.com
       token: ${GITLAB_TOKEN}
   ```

4. 確かめる。API には問い合わせず、設定と接続設定が読めるかだけを見る。

   ```bash
   python3 ~/.local/share/dagu-api-watch/api_watch.py check
   ```

5. 一度手で回す。初回は今あるレコードを基準値として覚えるだけで、DAG は起こさない（ルールの `initial: emit` なら起こす）。

   ```bash
   python3 ~/.local/share/dagu-api-watch/api_watch.py run
   ```

   ```bash
   python3 ~/.local/share/dagu-api-watch/api_watch.py status
   ```

6. Dagu の DAG を書き出す。取得元ごとの DAG・再送の DAG・整理の DAG ができる。DAG には今の Python と
   `api_watch.py`・設定ファイルの絶対パスが入る。

   ```bash
   python3 ~/.local/share/dagu-api-watch/api_watch.py dags --out ~/.config/dagu/dags
   ```

7. 受け渡し先の DAG を用意する（下の「受け渡し先の DAG」）。Dagu のスケジューラを動かせば（`dagu start-all` など）以後は自動で回る。

設定ファイルの場所は `--config`、なければ環境変数 `APIWATCH_CONFIG`、なければ `~/.config/dagu/api-watch.yaml`。

### Windows で使うとき

- 置き場所は同じ形で、`~` はユーザーフォルダ。`dags` は実行した Python（`python.exe`）の絶対パスで DAG を書くので、
  Dagu から使う Python で `dags` を実行する
- `dagu.executable` に `dagu.exe` の場所を書く（PATH にあれば `dagu` のままでよい）
- 状態のフォルダを Windows と WSL2 で共有しない（`/mnt/c/...` 越しの SQLite はロックが効かない）。どちらか片方で動かす

## 設定

### 取得元（sources）

1 つの取得元が 1 種類の取得（URL・条件・認証の組）。ルールはいくつでもこの取得結果を共有する。

| 項目 | 既定 | 意味 |
|---|---|---|
| `connection` | — | `connections.yaml` の `サービス` か `サービス:ラベル`。値は `{{ conn.項目 }}` で参照する |
| `request.method` / `url` / `headers` / `query` | `GET` | 要求。`{{ conn.* }}`・`{{ env.* }}`・`{{ position }}`（増分の位置）・`{{ now }}` / `{{ now_iso }}` を埋められる |
| `request.body` / `body_form` | — | JSON の本文（辞書・配列）か文字列 / フォームの本文 |
| `request.timeout_sec` | 30 | 1 回の要求の制限時間 |
| `records` | `$` | 応答の中のレコードの配列の場所（例 `data.items`）。オブジェクトなら 1 件として扱う |
| `key` | — | レコードを一意に指す項目。増分取得の取得元では書いておく（全体の姿を組み立て、基準値の取り直しに使う） |
| `pagination` | なし | 下の表 |
| `incremental` | なし | 下の表 |
| `schedule` | `*/5 * * * *` | 生成する DAG の起動間隔（cron） |
| `min_interval_sec` | 0 | 前回の成功からこの秒数たつまでは取得しない（手で叩いたときの重複を防ぐ。`fetch --force` で無視） |
| `rate_limit.min_interval_sec` / `per_minute` | 0 / 0 | 要求どうしの最小間隔・1 分あたりの上限。前回の実行の最後の要求からも数える |
| `rate_limit.max_wait_sec` | 120 | `Retry-After` やレート制限のリセットをこの秒数までは待つ。長ければ取得をやめ、その時刻までは問い合わせない |
| `retry.attempts` / `backoff_sec` / `max_backoff_sec` | 4 / 2 / 60 | 一時的な失敗（接続・タイムアウト・408/425/429/5xx）の再試行（指数バックオフ） |
| `conditional` | true | `ETag` / `Last-Modified` があれば条件付き要求にし、304 なら保存済みの本文を使う |
| `resume` / `resume_max_age_sec` | true / 3600 | 途中で止まった取得を、この秒数以内なら続きのページから再開する |
| `lease_sec` | 300 | 取得中の印の有効期限（ページごとに延ばす）。別の PC で動いていたプロセスが止まったときは、この秒数のあと引き継ぐ |

ページング（`pagination.type`）:

| type | 主な項目 | 終わりの判定 |
|---|---|---|
| `page` | `param`（`page`）・`start`（1）・`size_param`・`size`・`total_pages_path` | 件数が `size` 未満・0 件・総ページ数に達した |
| `offset` | `param`（`offset`）・`start`（0）・`size_param`（`limit`）・`size`（必須）・`total_path` | 件数が `size` 未満・0 件・総数に達した |
| `cursor` | `param`（`cursor`）・`cursor_path`（必須）・`has_more_path` | カーソルが無い・空・`has_more` が偽 |
| `next_url` | `next_url_path`（必須） | 次の URL が無い |
| `link_header` | — | `Link: <...>; rel="next"` が無い |

`in: body` でページ番号やカーソルを JSON の本文に入れる。次ページの URL が別のホストなら止める（認証情報を渡さないため。
意図したものなら `allow_cross_host: true`）。`max_pages`（1000）を超えたら、取りこぼさないよう取得を完了にしない。

増分取得（`incremental`）:

| type | 項目 | 動き |
|---|---|---|
| `max_value` | `field`・`param`・`initial` | 取得したレコードの `field` の最大値を覚え、次回は `param=<その値>` を付ける |
| `cursor` | `cursor_path`・`param` | 応答の `cursor_path`（同期トークンなど）を覚え、次回は `param=<その値>` を付ける |

位置は**全ページを取り終えたときだけ**進む。途中で失敗したら同じ位置から取り直す。`param` を書かずに
`{{ position }}` を URL や本文に直接埋めてもよい。境界のレコードが 2 回取れても、値が変わっていなければ発火しない。

### ルール（rules）

| 項目 | 既定 | 意味 |
|---|---|---|
| `source` | （必須） | 取得元の名前 |
| `id` | （必須） | 識別 ID の項目。配列で書くと複合 ID |
| `filter` | — | 対象のレコードの条件。外れたレコードは見ない（戻ってきたら `appeared`） |
| `watch` | レコード全体 | 監視する項目の配列。ここに無い項目の変化は無視する |
| `ignore` | — | 監視から外す項目（`watch` を書かずに全体を見るときに便利。`items[*].seen_at` のようにも書ける） |
| `array_order` | `respect` | `ignore` にすると配列の順序の違いを変化とみなさない |
| `unordered` | — | 順序を無視する配列だけを指定する（例 `labels`） |
| `missing_is_null` | false | true にすると「項目が無い」と `null` を同じとみなす（既定は区別する） |
| `condition` | — | 発火条件の式（下を参照） |
| `on` | `[appeared, changed]`（`condition` があれば `[entered]`） | 発火する種類（下を参照） |
| `initial` | `baseline` | 初回: `baseline` は基準値として覚えるだけ、`emit` は既存のレコードも新規として渡す |
| `on_rule_change` | `rebaseline` | 判定の仕方（`id`・`filter`・`watch`・`ignore`・順序・`condition`）を変えたとき、`rebaseline` は最新の取得結果を基準値にし直す（大量に発火しない）。`continue` は新しい比べ方で前回の値と比べる |
| `include_record` | true | イベントにレコード全体を入れる |
| `targets` | — | 受け渡し先（下の表）。無ければ検知を記録するだけ |

発火する種類（1 回の観測で複数つくことがあり、`on` のどれかに当たれば 1 件のイベントになる）:

| 種類 | いつ |
|---|---|
| `appeared` | 初めて見た ID（消えたあと・`filter` から外れたあとに戻ってきたときも） |
| `changed` | `watch` の値が前回の観測から変わった |
| `entered` | 条件が不一致（または初めて見た）→ 一致になった |
| `exited` | 条件が一致 → 不一致になった（全件取得で消えたときも） |
| `matching` | 条件に一致している（観測のたび。増分取得では取得されたときだけ） |
| `removed` | 全件取得で見当たらなくなった（増分取得の結果では判定しない） |

条件の式は `{path: 項目, op: 演算子, value: 値}`、組み合わせは `{all: [...]}`・`{any: [...]}`・`{not: {...}}`。
演算子は `eq`（`value` だけ書いたとき）・`ne`・`gt`・`ge`・`lt`・`le`・`in`・`not_in`・`contains`・`not_contains`・
`regex`・`startswith`・`endswith`・`exists`（`value` が無いとき）・`missing`・`null`・`not_null`・`empty`・`not_empty`・
`truthy`・`falsy`。大小は数どうし・文字列どうしだけ比べる（ISO 8601 の日時は文字列のまま比べられる）。

項目の書き方は `user.name`・`labels[0]`・`labels[*].name`（全要素の配列）・`["a.b"]`（ドットを含むキー）。
キーの順序の違いは比べるときに無視する。

### 受け渡し先（targets）

| 項目 | 既定 | 意味 |
|---|---|---|
| `dag` | （必須） | DAG の名前（`mode: cli` ならファイルのパスでもよい） |
| `name` | `dag` と同じ | 受け渡し先の名前（同じ DAG に 2 回渡すときに区別する） |
| `params` | — | 追加の引数。`{{ event.after.title }}`・`{{ event.record_id }}`・`{{ delivery.id }}`・`{{ env.X }}` を埋められる |
| `queue` | — | Dagu のキューを上書きする |
| `order` | `completion` | 同じレコードのイベントの順序。`completion` は前の後続処理が完了するまで次を渡さない、`enqueue` は前をキューに入れたら次を渡す、`none` は問わない |
| `max_attempts` | 10 | Dagu に渡せないときの試行回数。超えたら止めて手で再実行するのを待つ |
| `retry_backoff_sec` / `retry_max_backoff_sec` | 30 / 3600 | 渡し直しの間隔（倍々） |
| `inline_payload` | false | true ならイベントの JSON を引数 `APIWATCH_EVENT_JSON` でも渡す |

### Dagu への渡し方（dagu）

| 項目 | 既定 | 意味 |
|---|---|---|
| `mode` | `cli` | `cli`: `dagu enqueue --run-id=<ID> <DAG> -- KEY=VALUE ...` / `api`: Dagu の REST API / `command`: 任意のコマンド |
| `executable` / `extra_args` | `dagu` | `mode: cli` の dagu（argv の配列でもよい）と、`enqueue` の後ろに足す引数 |
| `url` / `connection` | — | `mode: api` の Dagu の URL、または `connections.yaml` の項目（`url` と `token`、または `user` と `password`） |
| `command` | — | `mode: command` の argv。`{{ run_id }}`・`{{ dag }}`・`{{ params_json }}` を埋められる |
| `dotenv` | — | 生成する DAG に読み込ませる環境変数のファイル |
| `dispatch_schedule` / `maintenance_schedule` | 毎分 / 毎日 3:17 | 再送の DAG・整理の DAG の起動間隔 |

### 保持期間（retention）

| 項目 | 既定 | 意味 |
|---|---|---|
| `fetches_keep` | 3 | 取得結果を最低何回分残すか |
| `fetch_days` | 7 | これより古い取得結果を消す。**どれかのルールがまだ判定していないものは消さない** |
| `events_days` | 30 | イベントを残す日数。**受け渡しがすべて完了（または飛ばした）したものだけ消す** |
| `auto_gc` | true | 取得のたびに整理する（整理の DAG と同じこと） |

状態の置き場所は `state_dir`（既定 `~/.local/share/dagu-api-watch`。環境変数 `APIWATCH_STATE_DIR` で上書き）。

### 接続設定の探し方

`connections_file` を書くか、環境変数 `APIWATCH_CONNECTIONS_FILE`（または `SKILL_CONNECTIONS_FILE`）を指定すると、そのファイルだけを見る。
指定が無ければ、設定ファイルのフォルダから上へ、続いて作業フォルダから上へ `.github/`・`.claude/`・`.copilot/`・`.kiro/` の
`connections.yaml` を探し、最後にホームの `~/.copilot/`・`~/.claude/`・`~/.github/`・`~/.kiro/` を見る。
同じサービス・ラベルは先に見つかったものを使う。参照している環境変数が無ければ、送らずに止める。

## 受け渡し先の DAG

`dags/api-watch-handler.example.yaml` が例。Dagu には次の引数が渡る（DAG の `params` に宣言しておく）。

| 引数 | 中身 |
|---|---|
| `APIWATCH_EVENT_ID` | イベント ID。1 つの観測の 1 つの変更につき 1 つ。複数の DAG に渡しても同じ |
| `APIWATCH_DELIVERY_ID` | 受け渡しの ID。Dagu の実行 ID（`DAG_RUN_ID`）と同じ。渡し直しても変わらない |
| `APIWATCH_SOURCE` / `APIWATCH_RULE` | 取得元・ルールの名前 |
| `APIWATCH_RECORD_ID` | レコードの ID（複合 ID なら JSON の配列） |
| `APIWATCH_KINDS` | 発火した種類（カンマ区切り） |
| `APIWATCH_EVENT_FILE` | イベントの JSON ファイル |

イベントの JSON:

```json
{
  "event_id": "ev_…", "source": "gitlab-issues", "rule": "issue-changed",
  "record_id": 42, "record_key": "42", "kinds": ["changed"], "fetch_id": 17, "observed_at": "2026-11-04T10:00:00",
  "before": {"state": "opened", "labels": ["ui"]},
  "after":  {"state": "opened", "labels": ["bug", "ui"]},
  "changes": [{"path": "labels[0]", "type": "changed", "before": "ui", "after": "bug"},
              {"path": "labels[1]", "type": "added", "after": "ui"}],
  "matched": null,
  "record": { "…取得したレコード全体…": "" }
}
```

`changes[].type` は `added`（項目が現れた）・`removed`（項目が無くなった）・`changed`。`watch` を書かないルールでは
`before` / `after` はレコード全体になる。

- **重複しない**: 実行 ID を受け渡しの ID にしているので、渡し直しで同じ実行が 2 つできることはない（Dagu が「既にある」と断り、
  このツールは渡せたものとして扱う）。後続処理の側でも `APIWATCH_EVENT_ID` で冪等にできる
- **完了の記録**: 例のように `handler_on` の `success` / `failure` から `api_watch.py ack ${DAG_RUN_ID}`（失敗なら `--failed`）を呼ぶ。
  `dagu.mode: api` なら、再送の DAG が `reconcile` で Dagu に状態を問い合わせて記録するので、`ack` は無くてもよい
- **後続処理の失敗**: 後続の DAG の `retry_policy` や、Dagu の画面の再実行で直す。`api_watch.py retry <ID>` も同じ実行 ID のまま
  Dagu に再実行させる
- **並列と順序**: 別のレコードの変更は Dagu のキューの並列数まで同時に動く。同じルール・受け渡し先・レコードの変更は、
  `order: completion` なら前の実行が完了してから次を渡す（前が失敗したら、再実行するか `skip` するまで後ろは待つ。他のレコードは止まらない）。
  完了を記録しない（`ack` も `mode: api` の `reconcile` も使わない）なら、`order: enqueue` にする（そうしないと 2 件目から先へ進まない）

## 運用

| コマンド | すること |
|---|---|
| `status` / `status --json` | 取得元ごとの最後の取得結果・エラー・途中の取得・待たされている時刻、ルールごとの判定位置・未判定の回数・受け渡しの内訳、失敗している受け渡し |
| `events [--rule R] [--status S] [--record ID]` | イベントと受け渡しの一覧 |
| `show <イベントか受け渡しの ID>` | イベントの中身と受け渡しの状態 |
| `records --source S [--fetch N]` | 保存済みの取得結果のレコード（ルールの書き方を確かめる） |
| `fetch` / `evaluate` / `dispatch` / `run` | 取得 / 判定 / 受け渡し / その 3 つを続けて。`--source`・`--rule` で絞れる |
| `retry [ID ...] [--rule R] [--target T] [--status S]` | 失敗した受け渡し（既定: 渡せなかった・上限に達した・後続処理が失敗した）を同じ ID のまま再実行 |
| `skip ID ...` | 受け渡しを飛ばす。同じレコードの後ろのイベントが進む |
| `rebaseline --rule R` / `--source S` | 最新の取得結果を基準値にし直す（その間の変化では発火しない）。`--refetch` で次の取得を全件に、`--now` でいま取り直す |
| `replay --rule R [--from-fetch N] [--dry-run]` | 保存済みの取得結果で判定をやり直す。API には問い合わせない。既にあるイベントは同じ ID なので積み直さない |
| `gc [--dry-run]` | 保持期間を過ぎたものを消す |
| `forget-rule R` | 設定から消したルールの判定状態を消す |
| `dags --out DIR` | Dagu の DAG を書き出す |

ログ・エラー・状態には認証情報を出さない（接続設定の値・認証のヘッダー・URL のトークンらしい引数は `***` に置き換える）。

終了コード: 0 成功（取得中・待機中で何もしなかったときも）/ 1 設定の誤り・再試行しても直らない失敗（認証・404 など）/
3 一時的な失敗（生成する DAG はこれだけ再試行する）/ 4 一部のルール・受け渡しが失敗した（他は進めた。失敗分は `status` に出る）。

## 止まったとき・失敗したとき

| 起きたこと | どうなるか |
|---|---|
| 取得の途中で失敗・プロセスや PC が停止 | 取ったページは保存済み。次の取得は続きのページから（`resume_max_age_sec` を過ぎたら最初から）。全ページそろうまで判定せず、増分の位置も進めない |
| 取得中にもう 1 つ取得が始まった | 何もせず終わる。取得中のプロセスが死んでいれば（同じ PC なら即座に、別の PC からは `lease_sec` 後に）引き継ぐ |
| 判定の途中で停止 | 判定は取得 1 回ごとに 1 トランザクション。次は判定し終えていない取得からやり直す |
| ルールの判定でエラー | そのルールだけ止まり、他のルールは進む。`status` にエラーが出る。直して `evaluate --rule R` |
| Dagu に渡せない | 時間をおいて同じ ID で渡し直す。`max_attempts` を超えたら止まり、`retry` を待つ。同じレコードの後ろのイベントだけが待つ |
| 渡した直後に停止（記録の前） | 次の `dispatch` で同じ ID で渡し直し、Dagu が「既にある」と言うので 1 回だけ実行される |
| 取得条件・認証を変えた | 前の条件で取ったデータ（取得結果・位置・ETag の控え）を使わず取り直し、ルールは基準値を取り直す |

## 限界

- ポーリングなので、取得の間隔の間に起きて元に戻った変化（A→B→A の B）は見えない。観測した値の列は順に比べるので、
  観測できた変化はすべて検知する。**すべての変化が要るなら、API が提供するイベント履歴・変更履歴を取得元にする**
  （履歴の各行をレコードとし、ID を履歴の ID にして `on: [appeared]`）
- ページ番号・オフセットのページングは、取得中にデータが増減するとページの境目がずれる。並び順を更新日時の昇順にするか、
  カーソル方式を使う
- 増分取得では「消えた」を判定できない（取得されないだけと区別できない）

## テスト

実 API・Dagu 本体なしで、偽の API（手元の HTTP サーバ）と偽の dagu で確かめる。

```bash
python3 -m unittest discover -s tools/dagu-api-watch/test
```

取得の集約・各ページング・増分取得・条件付き要求・一時的な失敗の再試行・途中からの再開・強制終了からの引き継ぎ・
変更検知の意味（キー順・配列の順序・欠落と null・A→B→A・一致の遷移・初回の扱い・ルールの変更）・保存済みの結果での判定のやり直し・
受け渡しの再送・同じ ID・順序・失敗の隔離・REST API での受け渡しと完了の確認・保持期間・認証情報の伏せ字を見る。
設計は `docs/designs/dagu-api-watch-design.md`。
