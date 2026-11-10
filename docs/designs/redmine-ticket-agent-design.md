# redmine-ticket-agent 設計

Redmine の「自分が担当する未完了のチケットのうち、決めた条件に当たるもの」に、その条件と対にした文をコメントしてステータスを
完了にする（条件とコメントの対は複数）。gitlab-issue-agent と同じく、Dagu の定期実行と change-watch の上に組む。
実装は `tools/change-watch/agents/redmine-ticket-agent/`（設定と Dagu のワークフローの例）、使い方は同フォルダの README。
このエージェントはプログラムを持たない。

## 1. 位置づけと分担

| 受け持ち | どこ | 何を |
|---|---|---|
| Redmine への問い合わせ（offset のページング・予定・ずらし・失敗の待ち）と、前回 commit してからの変化・書き込みの送信 | change-watch | 取得元 `redmine-my-issues`、受け手 `auto-close`（`get --watch … --one`・`commit`）、`call PUT` |
| 起動・条件とコメントの対・再試行・ログと実行履歴 | Dagu | ワークフロー 1 本（`get` → 対ごとのステップ（`preconditions` と `call`）→ `commit`）。例は `change-watch-auto-close.yaml.example` |

gitlab-issue-agent との違い: 処理がエージェント CLI ではなく決まった 1 回の HTTP 更新なので、`harness.run` ではなく `change-watch call` で送る。
`call` は取得と同じ接続（API キー・要求の間隔・失敗の待ち）を使う。

## 2. 構成

```
Dagu: change-watch-auto-close（毎分。1 回の起動で 1 件を見る）
  get --consumer auto-close --one   schedule（*/15）の時刻のあとに未取得なら、jitter 0〜300 秒で
                                    GET /issues.json?assigned_to_id=me&status_id=open&sort=id を offset で全ページ取得し、
                                    watch（subject・description・status.id・assigned_to.id）が前回 commit から変わったチケットを 1 件出す（CHANGE）
  auto-close   （題名に「自動完了」を含む）  call PUT /issues/{id}.json  {"issue": {"status_id": 完了, "notes": 文}}
  duplicate    （題名に「【重複】」を含む）  同じ形で別の文
  commit CHANGE.key                 見終えたと記録する（条件に合わなかった・書き込めなかったものも）
```

### 2.1 対象の決め方

- 「自分が担当」は取得の `assigned_to_id=me` で絞る（API キーの持ち主）。未完了は `status_id=open` で絞る
- 「条件とコメントの対」は **ステップ 1 つに 1 つ**（2.3）。条件は Dagu の `preconditions`（`re:` の正規表現）で書く
- `--initial emit` を既定にする。「いま当たっているチケットも片付けたい」が主な使い方のため。GitLab 版の `baseline` と逆なので README で明示する
- 完了のステータスの ID は `.env` の `REDMINE_CLOSED_STATUS_ID` に書く（名前から引かない）

### 2.2 書き込みと重複

- `PUT /issues/{id}.json` に `{"issue": {"notes": N, "status_id": S}}`。コメントとステータスが 1 つのジャーナルになる
- 完了にしたチケットは次の取得から `status_id=open` で一覧に出ないので、同じチケットを 2 回処理しない。自分のコメントで出る
  `updated_on` の変化は `watch` に入れていないので、変化にもならない
- 書き込めなかったとき（選べないステータスなど）も `commit` する（`continue_on.failure`）。毎分やり直し続けず、失敗は Dagu の履歴に残る。
  一時的な失敗（終了コード 3）だけ `retry_policy` でやり直す。`call` は自分では書き込みを再試行しない
- Redmine は選べない `status_id` を黙って無視して 204 を返し、コメントだけが残る。ステータスを選べることは入れるときに確かめる（README）

### 2.3 条件とコメントの対

対ごとに Dagu のステップを 1 つ書く（条件は `preconditions`、コメントは `call` の `--json`）。ステップは `depends` で一列につなぎ、
`continue_on.skipped` で条件に合わなくても次へ進む。1 つのチケットが 2 つの対に当たると両方のコメントが付くので、条件は互いに
重ならないように書く（例のコメントに書いてある）。

### 2.4 認証情報の流れ

取得も書き込みも change-watch の接続（`connections.yaml` の `redmine`。値は `${REDMINE_URL}`・`${REDMINE_API_KEY}`）で送り、
`X-Redmine-API-Key` ヘッダーは change-watch がログで伏せ字にする。Dagu のワークフローの `dotenv` が環境変数を入れる。argv に API キーを書かない。

### 2.5 Redmine への負荷

| 呼び出し | 頻度 | リクエスト数 |
|---|---|---|
| 一覧の取得 | 15 分ごと（0〜5 分ずらす） | 担当の未完了チケット 100 件ごとに 1 |
| 1 件の処理 | 当たったチケットごとに 1 回 | `call` の PUT 1 |

取得と書き込みは取得元の `rate_limit`（`group: redmine`）と失敗の待ちを分け合う。

## 3. 試験

`tools/change-watch/agents/redmine-ticket-agent/test/test_redmine_issue.py`。Redmine は手元の HTTP サーバで偽装し（一覧の `assigned_to_id=me`・`status_id=open`・
offset、PUT）、例のワークフローを Dagu と同じ順で本物の change-watch で流す。

- 自分が担当し、題名に文字列を含むものだけが、その対のコメントで 1 回だけ完了になる。ほかの人の担当・当たらないものはそのまま
- 整理のワークフローが動く

## 4. 限界

- ポーリングなので、完了になるまで最大で `schedule` の間隔＋ずらし（例では約 20 分）かかる
- 待つ間の担当替え・文字列の削除は、取得のあとなら次の取得まで気づかない（0.7 までの、実行の直前の取り直しは持たない）
- 説明の文字列で当てるなら `${CHANGE.record.description}` を条件にする。Textile / Markdown の書式ごと比べる

## 5. 採らなかった案

- **change-watch のルールとプラグインで判定する（0.7 まで）**: 対ごとの change-watch のルール（`filter`）と、`command` の受け渡し先から呼ぶ
  `redmine_issue.py close`（ステータスの名前引き・`allowed_statuses` の確認・冪等の書き込み）、チェック `redmine.not_mine` を持っていた。
  条件が change-watch と Dagu に分かれ、Dagu の画面から追えないので、change-watch 0.8.0 でルールをなくし、条件は Dagu、書き込みは `call` にした
