# dagu-issue-loop 設計

GitLab の issue の変化を拾い、エージェント CLI を **同時に 1 セッションだけ**流す仕組みを、常駐デーモン（agent-loop）ではなく
Dagu の定期実行と api-watch の上に組む。実装は `tools/dagu-issue-loop/`（api-watch のプラグイン `gitlab_issue.py` と設定の例）、
使い方は同フォルダの README。

## 1. 位置づけと分担

| 受け持ち | どこ | 何を |
|---|---|---|
| 取得・変化の検知・実行待ち・再試行・処理し終えた状態・1 セッションの排他 | api-watch | 取得元（ページング・予定・ずらし・失敗の共有）、ルールの `watch` と `filter`、`external` の受け渡し先（`due` / `begin` / `render` / `finish`・`refresh`・`lock`） |
| 起動・1 件ずつの呼び出し・エージェント CLI の実行と停止・ログと実行履歴 | Dagu | 親のワークフロー（`dag.run` + `parallel`）、子のワークフロー（`harness.run`） |
| GitLab の issue に固有のこと | `gitlab_issue.py`（api-watch のプラグイン） | プロンプトの差分（フィルタ）、実行中に自分以外が動いたか（チェック） |

下敷きは agent-loop の gitlab-issue-hook（候補キー `iid`、版 `updated_at`、送信成功後にだけ既読を進める）。
エージェント CLI の起動方法は、当初の `agents/<name>.json` の代わりに Dagu の `harness.run` の組み込み（claude / codex / kiro / copilot など）を使う。

## 2. 構成

```
Dagu: api-watch-issue-agent（毎分）
  fetch --source gitlab-issues     取得元の schedule（毎時 0 分）の時刻のあとに未取得なら、jitter 0〜900 秒で取得
  evaluate --rule issue-agent      watch の項目が変わった issue をイベントにする
  due --rule issue-agent           実行を待つ受け渡しの ID の配列（DUE_RUN）
  run-run                          action: dag.run・parallel {items: DUE_RUN, max_concurrent: 1}（空なら preconditions で呼ばない）
Dagu: api-watch-issue-agent-run（子。DELIVERY に受け渡しの ID）
  begin DELIVERY                   run / skip / busy / wait / failed / gone（DECISION）
  render DELIVERY                  prompt.md を展開（INPUT。gitlab_issue_changes などのフィルタ）
  handle                           action: harness.run（provider: claude など、stdin: ${INPUT}）。DECISION が run のときだけ
  handler_on: success → finish / failure・abort → finish --failed
```

- ワークフローは api-watch の `dags` が雛形を書く。`handle` の中身は `--handle-step` で `issue-loop.handle.yaml` から差し込む
- 予定は api-watch の設定 1 か所（取得元の `schedule`）。ワークフローは毎分起動し、失敗したもののやり直しも毎分の起動で進む
- `issue_loop.py`（状態ファイル・プロセスの管理・CLI の起動）は無くなった（§7）

### 2.1 GitLab への負荷

| 呼び出し | 頻度 | リクエスト数 |
|---|---|---|
| issue の一覧（api-watch） | 予定の時刻（毎時 0 分）のあと 1 回。0〜900 秒ずらす | issue 100 件ごとに 1 |
| 毎分の起動（予定の時刻のあとに取得済み） | 毎分 | **0** |
| 1 件流した | 流すたび | 4（`begin` と `finish` の取り直し、`gitlab.others_acted` の `/user`・notes） |
| 処理し終えた状態と同じ変化（自分の書き込みなど） | 来るたび | **0**（イベントの値と処理し終えた状態のハッシュが同じなら、`begin` が取り直しもしない） |
| 取得の失敗のあと | — | `failure_backoff_sec`（300 秒、倍々で最大 1 時間）のあいだ 0。プラグインの問い合わせも同じ待ちに従う |

- ずらし幅は毎回ランダム（api-watch の `jitter_sec`）。PC やプロジェクトごとに固定のずれにすると、同じ設定を配った台数分が
  同じ時刻にそろいうる
- 同じ GitLab を見る別の取得元（MR など）とは `rate_limit.group` で、同時に取得せず要求の間隔と失敗の待ちを分け合う

## 3. 変化の検知と重複制御

### 3.1 api-watch 側（ルール）

ルールの `watch` は title / state / labels / `assignees[*].username` / `milestone.title` / due_date / user_notes_count / description。
`updated_at` だけが動いた更新ではイベントにならない。初回（`initial: baseline`）は今ある issue を基準値にするだけ。
`filter`（例: `state` が `opened`）は、取得したときと、実行の直前に取り直したときの両方に当てはめる。
`order: none`: 打ち切った（dead）イベントが `retry` / `skip` されるまで同じ issue の後ろを止めない（打ち切って次の変化で拾う）。

### 3.2 api-watch 側（begin / finish と refresh）

詳しくは api-watch の設計 §5.6・§5.7。1 件ごとに:

1. `begin`: イベントの値（`after`）が「処理し終えた状態」（受け渡し先 × issue ごと）と同じなら、取り直さずに飛ばす
2. `begin`: `lock`（`agent-session`）を取る。空いていなければ `busy`（試行に数えずに戻す）
3. `begin`: issue を取り直す。見つからない・`filter` から外れた・処理し終えた状態と同じなら飛ばす。実行するなら、取り直した issue と
   `refreshed.last_handled`（前回処理し終えたときの監視する値）を入れたイベントを書く
4. `render` がプロンプトを組み、Dagu の `harness.run` がエージェント CLI を動かす
5. `finish`: `handled_before_if` の `gitlab.others_acted` を呼ぶ。真なら実行前（3）の状態まで、偽なら取り直した実行のあとの状態を処理し終えた状態にする。
   失敗なら `retry_backoff_sec` のあとに同じ ID で流し直し、`max_attempts` で打ち切る。`lock` を返す

これで、初版の poll / run が状態ファイルで行っていたことを api-watch が持つ:

| 初版（poll / run） | 今 |
|---|---|
| 同じ版（`updated_at`）なら積まない・snapshot が同じなら版だけ進める | ルールの `watch`（監視する値が同じならイベントにならない） |
| 待ち行列で同じ issue の変化を 1 件にまとめる | 先の実行が最新の issue で流し、後ろは 1・3 の比較で飛ばす |
| 絞り込みから外れたら待ち行列から下ろす | 3 の `filter` |
| 実行後に取り直して既読にする（自分の書き込みを吸収） | 5 |
| 実行中に他人が動いたら積み直す | `gitlab.others_acted` が真 → 実行前の状態まで。次の取得で変化として出る |
| attempts / backoff / dead | 受け渡し先の `max_attempts` / `retry_backoff_sec` |

### 3.3 プラグイン: プロンプトの差分（gitlab_issue_changes）

`refreshed.last_handled` があれば前回処理し終えたときからの累積、無ければイベントの `before`（監視する項目だけ）からの差分。
どちらも今の issue に重ねて変化前とみなし、監視していない項目は今と同じとみなす（余計な行を出さない）。新しい issue（`appeared`）は「新しい issue」。
`gitlab_issue_kind` は同じ判定で新規 / 更新を返す。

### 3.4 プラグイン: 実行中に自分以外が動いたか（gitlab.others_acted）

issue の notes を新しい順に読み、`begin` の時刻（`ctx.delivery.started_at`）以降に**トークンの持ち主以外**の作者が居れば真
（システムノート＝ラベル変更等も作者で判定する）。他人の動きは監視項目を変えるので、次の取得で api-watch がイベントを出し、
処理し終えた状態（実行前）と違うので流れる。

- 自分が分からない（`/user` が引けない）ときは、自分の書き込みと区別できないので偽（同じ issue を流し続けない）
- GitLab に届かないときは例外を返し、api-watch が `handled_before_if` を真とみなす（取りこぼさない側）。この場合に限り、自分のコメントでもう一巡する
- 問い合わせは `ctx.get_json`（取得元の接続・要求の間隔・失敗の待ちを分け合う）。トークンは connections.yaml からだけ読む

## 4. 1 セッションの保証

| 層 | 仕組み | 守る場面 |
|---|---|---|
| Dagu | 同じ DAG が実行中なら次の定期実行を立てない。親の `parallel` は `max_concurrent: 1` | 毎分の起動が重なる・1 回の起動で複数の issue |
| api-watch の `lock` | `begin` で取り、`finish` で返す期限つきの排他（持ち主は受け渡しの ID。期限は `timeout_sec` + 120 秒） | 別プロジェクトのワークフロー・手で `begin` を叩いた |
| 期限 | `finish` が呼ばれないまま止まった受け渡しと `lock` は、期限が切れるまで誰にも渡さない | Dagu ごと落ちた |

- 当初の `issue_loop.py` は CLI の pid を見て、`run` が強制終了されて CLI だけが残った場合も次を始めなかった。今は CLI を Dagu が動かすので、
  Dagu の停止・タイムアウトでは Dagu が止める。Dagu ごと落ちて CLI が残った場合は、`lock` の期限（`timeout_sec` + 120 秒）までしか守れない
- `lock` の名前をプロジェクトごとに変えれば、プロジェクトごとに 1 つずつ動かせる

## 5. 失敗と再試行

- 失敗 = `harness.run` の失敗・時間切れ・中止（子のワークフローの `handler_on` が `finish --failed`）と、実行の前の取り直しの失敗
- api-watch が `retry_backoff_sec × 2^(回数-1)` のあとの起動で同じ受け渡しの ID のまま流し直す。`max_attempts` に達したら `dead`。issue が次に変われば新しいイベントになる
- `api-watch retry <ID>` で打ち切ったものを流し直す。`api-watch forget-handled --rule issue-agent IID` で処理し終えた状態を忘れ、次のイベントで必ず流させる
- Dagu のステップの `retry_policy` は付けない（再試行を api-watch と二重にしないため）

## 6. Windows（ネイティブ）

プロセスの管理（停止・生存確認・Ctrl+Break・コマンド行の分割）を Dagu に任せたので、dagu-issue-loop 側に OS ごとの切り替えは無い。
api-watch とプラグインは標準ライブラリだけで、Windows の CI で試験を回す。Windows の Dagu は停止時に Job Object ごと止めるので
エージェント CLI も一緒に止まり、`handler_on` の `abort` / `failure` が失敗として記録する。

## 7. 経緯と採らなかった案

- **初版の poll / run（2 本の DAG + 状態ファイルの待ち行列）**: 取得（毎時）と実行（毎分）を別 DAG に分け、state.json の
  `queue` / `inflight` / `dead` でつないでいた。api-watch が取得・検知・実行待ち・再試行・打ち切り・失敗の共有を汎用に持ったので移した
- **handle が既読の版（`seen`）を持つ形**: どこまで処理したかを handle と api-watch の 2 か所で持つことになるので、api-watch の `refresh` に移した
- **`issue_loop.py handle` がエージェント CLI を動かす形**: CLI の起動・時間切れ・子孫ごとの停止・Windows 対応を自前で持ち（約 820 行）、
  Dagu の画面からはエージェントの実行が 1 つのステップの中に隠れていた。Dagu の `harness.run` に移し、残った GitLab 固有の部分を
  api-watch のプラグイン（フィルタとチェック）にした。`issue_loop.py` は無くなり、プラグインは約 170 行
- **受け渡し先の DAG とキュー（api-watch の dagu アダプタで enqueue）**: 1 本の親のワークフローから子を呼ぶ形にした。取得から実行までが
  1 つの親の実行履歴に並び、Dagu 側の設定（キュー）も要らない
- **Dagu のキューだけで直列化する**: 別プロジェクトのワークフローをまたいだ排他を Dagu の設定に頼ることになる。api-watch の `lock` を本体にした
- **Webhook で受ける**: Dagu だけで完結させる要件と、GitLab から到達できない手元の PC でも動かすためにポーリングにした
