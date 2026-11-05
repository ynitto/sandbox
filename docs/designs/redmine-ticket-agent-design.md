# redmine-ticket-agent 設計

Redmine の「自分が担当する未完了のチケットのうち、タイトルか説明に決めた文字列を含むもの」に、決めた文をコメントしてステータスを
完了にする。gitlab-issue-agent と同じく、Dagu の定期実行と api-watch の上に組み、ここには Redmine に固有の部分だけを置く。
実装は `tools/redmine-ticket-agent/`（`redmine_issue.py` と設定の例）、使い方は同フォルダの README。

## 1. 位置づけと分担

| 受け持ち | どこ | 何を |
|---|---|---|
| 取得・変化の検知・実行待ち・再試行・処理し終えた状態 | api-watch | 取得元（offset のページング・予定・ずらし・失敗の待ち）、ルールの `filter` と `watch`、`command` の受け渡し先（`work`・`refresh`・`skip_if`） |
| 起動・ログと実行履歴 | Dagu | ワークフロー 1 本（`fetch` → `evaluate` → `work`）。例（`api-watch-auto-close.yaml.example`）をコピーして置く |
| Redmine に固有のこと | `redmine_issue.py` | チェック `redmine.not_mine`（プラグイン）と、コメントとステータスを変える `close`（`command` から呼ぶ CLI） |

gitlab-issue-agent との違い: 処理がエージェント CLI ではなく決まった 1 回の HTTP 更新なので、`external`（Dagu の `harness.run` に処理を任せる形）
ではなく `command` の受け渡し先にし、`work` がその場で実行する。`lock`・`input_template`・`handled_before_if` は要らない。

## 2. 構成

```
Dagu: api-watch-auto-close（毎分）
  fetch --source redmine-my-issues   schedule（*/15）の時刻のあとに未取得なら、jitter 0〜300 秒で
                                     GET /issues.json?assigned_to_id=me&status_id=open&sort=id を offset で全ページ
  evaluate --rule auto-close         filter（subject か description が文字列を含み、status.is_closed が偽）に当たり、
                                     watch（subject・description・status.id・assigned_to.id）が変わったものをイベントにする
  work --rule auto-close             受け渡しごとに:
                                       begin   refresh（GET /issues/{id}.json の issue）で取り直し、filter を当て直す。
                                               skip_if の redmine.not_mine が真なら飛ばす
                                       command python3 redmine_issue.py close --status S --note N（APIWATCH_EVENT_FILE）
                                       finish  終了コード 0 なら取り直した状態を処理し終えた状態にする。それ以外は retry_backoff_sec 後に再試行
```

### 2.1 対象の決め方

- 「自分が担当」は取得の `assigned_to_id=me` で絞る（API キーの持ち主）。待つ間の担当替えは、`refresh` の取り直しと `redmine.not_mine` で止める。
  担当がグループのチケットは `assigned_to_id=me` に含まれる版もあるが、`redmine.not_mine` は自分の ID と一致しないので飛ばす（意図した担当に限る）
- 「文字列を含む」はルールの `filter`（`contains` / `regex`）で書く。プラグインに持たせず設定に置くのは、取り直したレコードに api-watch が
  同じ条件を当て直してくれるため（プラグインで判定すると、取り直し後の確認を別に書くことになる）
- 未完了の確認は取得の `status_id=open` と `filter` の `status.is_closed`（Redmine 5.1 以降。無い版では `falsy` が欠落を真とみなし、
  `close` が「既に目的のステータス」を見て何もしない）
- `initial: emit` を既定にする。「いま当たっているチケットも片付けたい」が主な使い方のため。GitLab 版の `baseline` と逆なので README で明示する
- `on_rule_change: continue` にする。既定の `rebaseline` では、文字列（`filter`）を変えたとき今ある当たりを基準に取り込むだけで処理しない。
  `continue` なら新しく当たったチケットは前回の観測が無いので `appeared` になり、完了にする（`watch` を変えたときは当たっているもの全部が
  `changed` になるが、どれも完了にすべきものなので差し支えない）

### 2.2 close の手順と冪等性

1. `--status` が数でなければ `GET /issue_statuses.json` で名前から ID を引く（無ければ一覧を添えて失敗）
2. イベントのレコード、次に `GET /issues/{id}.json?include=allowed_statuses,journals` の現在値が既に目的のステータスなら、何もせず 0 で終わる
   （PUT の応答だけを失った再試行で、コメントを 2 回書かない）
3. `allowed_statuses`（Redmine 5.0 以降）に目的のステータスが無ければ、書かずに失敗する。Redmine は選べない `status_id` を
   黙って無視して 204 を返し、コメントだけが残るため
4. `PUT /issues/{id}.json` に `{"issue": {"notes": N, "status_id": S}}`。コメントとステータスが 1 つのジャーナルになる
   ただし同じ文のコメントがジャーナルに既にあれば（前の試行でステータスだけが無視された）、`notes` を送らずステータスだけを変える
5. `GET /issues/{id}.json` でステータスが変わったかを確かめ、変わっていなければ失敗（`allowed_statuses` を返さない古い版の保険）

成功のあとは `finish` の取り直しで完了のステータスが処理し終えた状態になり、次の取得からは `status_id=open` で一覧に出ないので、
同じチケットを 2 回処理しない。自分のコメントで出る `updated_on` の変化は `watch` に入れていないので、イベントにもならない。

### 2.3 認証情報の流れ

- 取得・取り直し・チェックは api-watch の接続（`connections.yaml` の `redmine`。値は `${REDMINE_URL}`・`${REDMINE_API_KEY}`）で送り、
  `X-Redmine-API-Key` ヘッダーは api-watch がログで伏せ字にする
- `close` は別プロセスなので接続設定を読まず、環境変数 `REDMINE_URL`・`REDMINE_API_KEY` を読む。Dagu のワークフローの `dotenv` が
  ワークフローの環境に入れ、`work` が子プロセスに引き継ぐ。argv に API キーを書かない（`ps` やログに出ないように）

### 2.4 Redmine への負荷

| 呼び出し | 頻度 | リクエスト数 |
|---|---|---|
| 一覧の取得 | 15 分ごと（0〜5 分ずらす） | 担当の未完了チケット 100 件ごとに 1 |
| 1 件の処理 | 当たったチケットごとに 1 回 | 取り直し 2（前後）＋ `/users/current.json` 1 ＋ `close` の 3〜4（ステータス一覧・現在値・PUT・確認） |

取得・取り直し・チェックは取得元の `rate_limit`（`group: redmine`）と失敗の待ちを分け合う。`close` の要求はそれに数えない（件数が少なく、1 件ごとに数回のため）。

## 3. 試験

`tools/redmine-ticket-agent/test/test_redmine_issue.py`。Redmine は手元の HTTP サーバで偽装し（一覧の `assigned_to_id=me`・`status_id=open`・
offset、`include=allowed_statuses`、選べない `status_id` を黙って無視する PUT）、設定の例をそのまま本物の api-watch で回す。

- 自分が担当し、タイトルか説明に文字列を含むものだけが 1 回だけ完了になる。ほかの人の担当・当たらないものはそのまま
- あとから文字列を足したチケットも完了になる
- 待つ間に担当が替わった・文字列を消したチケットは完了にしない
- 選べないステータスのときは何も書かずに失敗し、直したあとの再試行で完了になる
- 文字列を変えると、変えたあとの文字列を含む今あるチケットも完了になる
- 前の試行でコメントだけが残っていたら、やり直しでコメントを重ねない
- 例のワークフローを Dagu と同じ順で流すと、当たったチケットが完了になる

## 4. 限界

- ポーリングなので、完了になるまで最大で `schedule` の間隔＋ずらし（例では約 20 分）かかる
- Redmine 5.0 より前は `allowed_statuses` が無く、選べないステータスのときコメントだけが残る（2.2 の 5 で失敗として気づける）
- 説明の文字列は Textile / Markdown の書式ごと比べる（`*自動完了*` も含むとみなす）
