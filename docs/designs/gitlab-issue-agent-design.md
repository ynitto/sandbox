# gitlab-issue-agent 設計

GitLab の issue の変化を拾い、エージェント CLI を **同時に 1 セッションだけ**流す仕組みを、常駐デーモン（agent-loop）ではなく
Dagu の定期実行と api-watch の上に組む。実装は `tools/gitlab-issue-agent/`（api-watch のプラグイン `gitlab_issue.py` と設定の例）、
使い方は同フォルダの README。

## 1. 位置づけと分担

| 受け持ち | どこ | 何を |
|---|---|---|
| 取得・変化の検知・実行待ち・再試行・処理し終えた状態・1 セッションの排他 | api-watch | 取得元（ページング・予定・ずらし・失敗の共有）、ルールの `watch` と `filter`、`external` の受け渡し先（`due` / `begin` / `render` / `finish`・`refresh`・`lock`） |
| 起動・エージェント CLI の実行と停止・ログと実行履歴 | Dagu | ワークフロー 1 本（`claim` → `render` → `harness.run` → `finish`） |
| GitLab の issue に固有のこと | `gitlab_issue.py`（api-watch のプラグイン） | プロンプトの差分（フィルタ）、実行中に自分以外が動いたか（チェック） |

下敷きは agent-loop の gitlab-issue-hook（候補キー `iid`、版 `updated_at`、送信成功後にだけ既読を進める）。
エージェント CLI の起動方法は Dagu の `harness.run` の組み込み（claude / codex / kiro / copilot など）を使う。

## 2. 構成

```
Dagu: api-watch-issue-agent（毎分。1 回の起動で 1 件を処理する）
  fetch --source gitlab-issues     取得元の schedule（毎時 0 分）の時刻のあとに未取得なら、jitter 0〜900 秒で取得
  evaluate --rule issue-agent      watch の項目が変わった issue をイベントにする
  claim --rule issue-agent         実行を待つものを古い順に begin し、最初に run になった 1 件の ID（DELIVERY）。無ければ空
  render DELIVERY                  prompt.md を展開（INPUT。gitlab_issue_changes などのフィルタ）
  handle (id: handle)              action: harness.run（provider: claude など、stdin: ${INPUT}）。continue_on: failure
  finish DELIVERY --exit-code ${handle.exit_code}
  （render 以降は DELIVERY が aw_ で始まるときだけ動く: preconditions の re:^aw_）
```

- ワークフローは api-watch の `dags` が雛形を書く。`handle` の中身は `--handle-step` で `gitlab-issue-agent.handle.yaml` から差し込む
- 予定は api-watch の設定 1 か所（取得元の `schedule`）。ワークフローは毎分起動し、失敗したもののやり直しも毎分の起動で進む
- 実行を待つ issue が複数あっても、1 回の起動では 1 件だけ。次の 1 件は次の起動（1 分後）が取る。同時に 1 つしか動かさないので、
  1 回で全部を流しても待ちの合計はほとんど変わらない
- gitlab-issue-agent は状態ファイルもプロセスの管理も持たない。どこまで処理したかは api-watch だけが知っている

### 2.1 GitLab への負荷

| 呼び出し | 頻度 | リクエスト数 |
|---|---|---|
| issue の一覧（api-watch） | 予定の時刻（毎時 0 分）のあと 1 回。0〜900 秒ずらす | issue 100 件ごとに 1 |
| 毎分の起動（予定の時刻のあとに取得済み） | 毎分 | **0** |
| 1 件流した | 流すたび | 4（`claim` と `finish` の取り直し、`gitlab.others_acted` の `/user`・notes） |
| 処理し終えた状態と同じ変化（自分の書き込みなど） | 来るたび | **0**（イベントの値と処理し終えた状態のハッシュが同じなら、`claim` が取り直しもしない） |
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

### 3.2 api-watch 側（claim / finish と refresh）

詳しくは api-watch の設計 §5.6・§5.7。`claim` は実行を待つものに古い順に `begin` を呼び、1 件ごとに:

1. `begin`: イベントの値（`after`）が「処理し終えた状態」（受け渡し先 × issue ごと）と同じなら、取り直さずに飛ばす
2. `begin`: `lock`（`agent-session`）を取る。空いていなければ `busy`（試行に数えずに戻す）
3. `begin`: issue を取り直す。見つからない・`filter` から外れた・処理し終えた状態と同じなら飛ばす。実行するなら、取り直した issue と
   `refreshed.last_handled`（前回処理し終えたときの監視する値）を入れたイベントを書く
4. `render` がプロンプトを組み、Dagu の `harness.run` がエージェント CLI を動かす
5. `finish`: `handled_before_if` の `gitlab.others_acted` を呼ぶ。真なら実行前（3）の状態まで、偽なら取り直した実行のあとの状態を処理し終えた状態にする。
   失敗なら `retry_backoff_sec` のあとに同じ ID で流し直し、`max_attempts` で打ち切る。`lock` を返す

同じ issue の変化が実行を待つ間に重なっても、先の実行が最新の issue で流し、後ろは 1・3 の比較で飛ばす（1 件にまとめるのと同じ効き目）。

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

### 3.5 gitlab-idd スキルと繋ぐ（examples/gitlab-idd）

gitlab-idd はラベル（`status:*`）でイシューを回し、頼まれるたびに 1 回だけ動く（自分ではポーリングしない）。これを 1 つのルール `idd` で繋ぐ。

- `filter` で gitlab-idd が扱うラベル（`status:open` / `status:needs-rework` / `status:needs-clarification` / `status:review-ready`）の
  イシューだけにし、ロールはプロンプトの `{{ event.record | gitlab_idd_role }}` がラベルから選ぶ。ロールごとにルールを分けないので、
  ワークフローも 1 本で済み、`lock` が 1 つなのでワーカーとレビュアーが同時に動くこともない
- **ロールの受け渡し**: ワーカーが `status:review-ready` にするのも、レビュアーが `status:needs-rework` に戻すのもエージェント自身の書き込みで、
  そのままでは §3.2 の 5 が「処理し終えた状態」として吸収してしまう。チェック `gitlab_idd.role_changed`（`handled_before_if`）が、
  実行の前と後でロールが変わったら真を返し、実行前の状態までを処理済みにする。次の取得でラベルの変化がイベントになり、次のロールで流れる。
  ロールが変わらない書き込み（質問のコメント、`status:needs-clarification` への変更）は吸収したまま
- プロンプトで「このイシューだけを処理して」と頼む。gitlab-idd のワーカーは本来、候補の一覧から自分で 1 件選ぶので、別のイシューに
  手を出さないように止める（ほかのイシューは、それぞれの変化で別に呼ばれる）
- gitlab-idd の猶予（自分が出したイシューの self-defer 60 分・自分が実装したイシューの self-review ロック 24 時間、どちらもノード単位）は、
  1 つのエージェントがワーカーとレビュアーを兼ねると「何もせずに終わる」になり、変化が無いので二度と流れない。設定の例では
  `skill-registry.json` で 0 にする。gitlab-idd の 24 時間の自動着手（質問に答えがないとき）も、変化が無いので起きない（README に明記）
- **作業用フォルダ**: gitlab-idd のワーカーは、イシューごとに `gl.py workspace-create` で作業用フォルダを作り、終わったら `workspace-remove` で消す。
  `GITLAB_IDD_LOCAL_REPO`（既にある作業コピーでよい）があれば、クローンせずにそのリポジトリへ `git worktree add -B <ブランチ>` を足す
  （`origin` から対象ブランチだけを `refs/gitlab-idd/heads/` に fetch する。作業コピーの作業ツリー・HEAD・書きかけの変更には触らない）。
  無ければ従来どおり、一時フォルダへイシューごとにクローンする。どちらも一時フォルダは `tempfile`（Linux・WSL は `/tmp`、Windows は `%TEMP%`）で決め、
  `mktemp`・`rm -rf` を手順から外したので、Windows のシェルでも同じ手順で動く。`workspace-remove` は worktree なら作業ツリーと手元のブランチ（push 済み）を外し、
  クローンなら読み取り専用のファイルも消す。名前が `gitlab-idd-` で始まらないフォルダは消さない
- 接続設定は 1 つの connections.yaml を分け合う。gitlab-idd は `gitlab` の `default`（プロジェクトの URL）を、api-watch と
  プラグインは `watch`（GitLab の URL）を使う。gitlab-idd には `SKILL_CONNECTIONS_FILE` で同じファイルを読ませる

## 4. 1 セッションの保証

| 層 | 仕組み | 守る場面 |
|---|---|---|
| Dagu | 同じ DAG が実行中なら次の定期実行を立てない。1 回の起動で 1 件だけ | 毎分の起動が重なる |
| api-watch の `lock` | `claim`（の `begin`）で取り、`finish` で返す期限つきの排他（持ち主は受け渡しの ID。期限は `timeout_sec` + 120 秒） | 別プロジェクトのワークフロー・手で叩いた |
| 期限 | `finish` が呼ばれないまま止まった受け渡しと `lock` は、期限が切れるまで誰にも渡さない | ワークフローを止めた・Dagu ごと落ちた |

- CLI は Dagu が動かすので、Dagu の停止・タイムアウトでは Dagu が止める。Dagu ごと落ちて CLI だけが残った場合は、`lock` の期限
  （`timeout_sec` + 120 秒）までしか守れない（CLI の pid は見ない）
- `lock` の名前をプロジェクトごとに変えれば、プロジェクトごとに 1 つずつ動かせる

## 5. 失敗と再試行

- 失敗 = `harness.run` の失敗・時間切れ（`continue_on: failure` で `finish` まで進み、`--exit-code ${handle.exit_code}` が 0 以外）と、実行の前の取り直しの失敗
- ワークフローごと止めたときは `finish` まで進まない。その受け渡しは期限（`timeout_sec` + 120 秒）が切れてから、次の起動がもう一度取る（失敗には数えない）
- api-watch が `retry_backoff_sec × 2^(回数-1)` のあとの起動で同じ受け渡しの ID のまま流し直す。`max_attempts` に達したら `dead`。issue が次に変われば新しいイベントになる
- `api-watch retry <ID>` で打ち切ったものを流し直す。`api-watch forget-handled --rule issue-agent IID` で処理し終えた状態を忘れ、次のイベントで必ず流させる
- Dagu のステップの `retry_policy` は付けない（再試行を api-watch と二重にしないため）

## 6. Windows（ネイティブ）

プロセスの管理（停止・生存確認・子孫ごとの停止・コマンド行の分割）は Dagu に任せるので、gitlab-issue-agent 側に OS ごとの切り替えは無い。
api-watch とプラグインは標準ライブラリだけで、Windows の CI で試験を回す。Windows の Dagu は停止時に Job Object ごと止めるので
エージェント CLI も一緒に止まる。その受け渡しは期限が切れてから次の起動がもう一度取る。

## 7. 採らなかった案

- **取得と実行を別の DAG に分け、状態ファイルの待ち行列でつなぐ**: 待ち行列・再試行・打ち切り・自分の書き込みの吸収を自前で持つことになる。
  api-watch が取得・検知・実行待ち・再試行・処理し終えた状態を汎用に持つので、そちらに任せた
- **エージェント CLI をスクリプトが自分で起動する**: CLI の起動・時間切れ・子孫ごとの停止・Windows 対応を自前で持ち、Dagu の画面からは
  エージェントの実行が 1 つのステップの中に隠れる。Dagu の `harness.run` に任せ、GitLab に固有の部分だけを api-watch のプラグインにした
- **処理済みの版をこちらでも持つ**: どこまで処理したかを 2 か所で持つとずれる。api-watch の `refresh` と処理し終えた状態に任せた
- **受け渡し先の DAG とキュー（api-watch の dagu アダプタで enqueue）**: Dagu 側にキューの設定が要り、取得と実行が別のワークフローに分かれる
- **親と子の 2 本のワークフロー（親が `dag.run` + `parallel` で子を 1 件ずつ呼ぶ）**: 1 回の起動で待っている issue を全部流せ、
  子の `handler_on`（abort）で中止も失敗として記録できるが、ワークフローが 2 本になる。1 本にするため、1 回の起動で 1 件だけにし
  （同時に 1 つしか動かさないので待ちはほとんど変わらない）、中止は期限切れで取り直す
- **Dagu のキューだけで直列化する**: 別プロジェクトのワークフローをまたいだ排他を Dagu の設定に頼ることになる。api-watch の `lock` を本体にした
- **Webhook で受ける**: Dagu だけで完結させる要件と、GitLab から到達できない手元の PC でも動かすためにポーリングにした
