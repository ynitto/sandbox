# gitlab-issue-agent 設計

GitLab の issue の変化を拾い、エージェント CLI を **同時に 1 セッションだけ**流す仕組みを、常駐デーモン（agent-loop）ではなく
Dagu の定期実行と change-watch の上に組む。実装は `tools/change-watch/agents/gitlab-issue-agent/`（設定と Dagu のワークフローの例）、
使い方は同フォルダの README。このエージェントはプログラムを持たない。

## 1. 位置づけと分担

| 受け持ち | どこ | 何を |
|---|---|---|
| GitLab への問い合わせ（ページング・予定・ずらし・失敗の共有）と、前回 commit してからの変化 | change-watch | 取得元 `gitlab-issues`、受け手 `issue-agent`（`get --watch … --one`・`commit --refresh`） |
| 起動・条件と分岐・エージェント CLI の実行と停止・再試行・ログと実行履歴 | Dagu | ワークフロー 1 本（`get` → `new` / `update`（`preconditions` と `harness.run`）→ `commit`） |

下敷きは agent-loop の gitlab-issue-hook（候補キー `iid`、版 `updated_at`、処理し終えてからだけ既読を進める）。
エージェント CLI の起動方法は Dagu の `harness.run` の組み込み（claude / codex / kiro / copilot など）を使う。

## 2. 構成

```
Dagu: change-watch-issue-agent（毎分。1 回の起動で 1 件を処理する）
  get --consumer issue-agent --one     取得元の schedule（毎時 0 分）の時刻のあとに未取得なら、jitter 0〜900 秒で取得し、
                                       watch の項目が前回 commit から変わった issue を 1 件、JSON（key・kind・record・before・changes）で出す（CHANGE）
  new     （kind が appeared で開いている）  action: harness.run。stdin に CHANGE
  update  （kind が changed で開いている）   action: harness.run。stdin に CHANGE
  commit --refresh CHANGE.key          issue を取り直した姿を処理し終えた状態にする（条件に合わなかった・失敗したものも）
```

- ワークフローは例（`change-watch-issue-agent.yaml.example`・`examples/gitlab-idd/change-watch-idd.yaml.example`）を `install.py` が置き、
  `new` / `update` の中身（エージェント CLI）を直す。例は試験で、偽の GitLab と本物の change-watch を相手に Dagu と同じ順で流して確かめる
- 予定は change-watch の設定 1 か所（取得元の `schedule`）。ワークフローは毎分起動し、取得の予定が来ていなければ手元の状態だけで答える
- 変わった issue が複数あっても、1 回の起動では 1 件だけ。次の 1 件は次の起動（1 分後）が取る。同時に 1 つしか動かさないので、
  1 回で全部を流しても待ちの合計はほとんど変わらない
- 分け方（ラベルでプロンプトを変えるなど）はワークフローの `preconditions` を足して書く。change-watch の設定には書かない
- gitlab-issue-agent は状態ファイルもプロセスの管理も持たない。どこまで処理したかは change-watch の受け手の状態だけが知っている

### 2.1 GitLab への負荷

| 呼び出し | 頻度 | リクエスト数 |
|---|---|---|
| issue の一覧（change-watch） | 予定の時刻（毎時 0 分）のあと 1 回。0〜900 秒ずらす | issue 100 件ごとに 1 |
| 毎分の起動（予定の時刻のあとに取得済み） | 毎分 | **0** |
| 1 件流した | 流すたび | 1（`commit --refresh` の取り直し） |
| 取得の失敗のあと | — | `failure_backoff_sec`（300 秒、倍々で最大 1 時間）のあいだ 0 |

- ずらし幅は毎回ランダム（change-watch の `jitter_sec`）。PC やプロジェクトごとに固定のずれにすると、同じ設定を配った台数分が
  同じ時刻にそろいうる
- 同じ GitLab を見る別の取得元（MR など）とは `rate_limit.group` で、同時に取得せず要求の間隔と失敗の待ちを分け合う

## 3. 変化の検知と重複制御

### 3.1 何を変化にするか

`get` の `--watch` は title / state / labels / assignees / milestone / due_date / user_notes_count / description。
`updated_at` だけが動いた更新では変化にならない。受け手を初めて使うとき（`--initial baseline`、既定）は今ある issue を基準にするだけ。

### 3.2 自分の書き込みで次に起きない

`commit --refresh` は issue を取り直した姿（エージェントが書いた後）を処理し終えた状態にする。エージェント自身のコメント・ラベルの
変更は、次の `get` では変化にならない。エージェントの実行中に人が書き込んだ場合も同じ姿に吸収される（次の人の書き込みで起きる）。
実行中の他人の動きを確かめて実行前の姿で記録する仕組み（0.7 までのチェック `gitlab.others_acted`）は持たない。要るなら、
`commit --refresh` の前に、実行中に他人のノートがあれば `--refresh` を付けずに commit するステップをワークフローに足す。

### 3.3 プロンプトの材料

エージェント CLI には `get` の JSON をそのまま標準入力で渡す。`record` が今の issue、`before` が前回処理し終えたときの姿、`changes` が
変わった項目。差分の文章はエージェントに読ませる（プロンプトの組み立てをこちらで持たない）。

### 3.4 gitlab-idd スキルと繋ぐ（examples/gitlab-idd）

gitlab-idd はラベル（`status:*`）でイシューを回し、頼まれるたびに 1 回だけ動く（自分ではポーリングしない）。ワークフロー 1 本で、
ロールごとに受け手を分けて繋ぐ。

- ワーカー（受け手 `idd-worker`）は `status:open` / `status:needs-rework` / `status:needs-clarification`、レビュアー（受け手 `idd-reviewer`）は
  `status:review-ready` のイシューを、それぞれの `preconditions` で選ぶ。1 回の起動でロールごとに 1 件まで、エージェント CLI は 1 つずつ動く
- **ロールの受け渡し**: 受け手が別なので、ワーカーが `status:review-ready` にするとレビュアーの側にはそれが変化として届き、レビュアーが
  `status:needs-rework` に戻すとワーカーの側に届く。各ロールは自分が動いたイシューを `commit --refresh`（absorb）し直すので、ロールが
  変わらない自分の書き込み（質問のコメント、`status:needs-clarification` への変更）では起きない
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
- 接続設定は 1 つの connections.yaml を分け合う。gitlab-idd は `gitlab` の `default`（プロジェクトの URL）を、change-watch は
  `watch`（GitLab の URL）を使う。gitlab-idd には `SKILL_CONNECTIONS_FILE` で同じファイルを読ませる

## 4. 1 セッションの保証

Dagu が受け持つ。同じ DAG が実行中なら次の定期実行を立てず、1 回の起動で 1 件だけ処理する。別プロジェクトのワークフローとも
同時に動かさないなら、Dagu のキュー（`queue`）で同じキューに入れ、並列数を 1 にする。CLI は Dagu が動かすので、Dagu の停止・
タイムアウトでは Dagu が子孫ごと止める。

## 5. 失敗と再試行

- エージェント CLI の失敗・時間切れは、そのステップの `retry_policy`（2 回、300 秒あけて）でやり直し、尽きたら失敗として Dagu の履歴に残す
- 失敗しても `commit` まで進む（`continue_on.failure`）。同じ姿では二度流さず、issue が次に変われば流れる。流し直すなら Dagu の画面から再実行する
- change-watch の一時的な失敗（終了コード 3）は `get` / `commit` の `retry_policy` でやり直す
- ワークフローごと止めたときは `commit` まで進まないので、次の起動が同じ変化をもう一度受け取る

## 6. Windows（ネイティブ）

プロセスの管理（停止・生存確認・子孫ごとの停止・コマンド行の分割）は Dagu に任せるので、gitlab-issue-agent 側に OS ごとの切り替えは無い。
change-watch は標準ライブラリだけで、Windows の CI で試験を回す。Windows の Dagu は停止時に Job Object ごと止めるのでエージェント CLI も
一緒に止まる。

## 7. 採らなかった案

- **取得と実行を別の DAG に分け、状態ファイルの待ち行列でつなぐ**: 待ち行列・再試行・自分の書き込みの吸収を自前で持つことになる。
  change-watch の `get` / `commit` と Dagu の再試行に任せた
- **エージェント CLI をスクリプトが自分で起動する**: CLI の起動・時間切れ・子孫ごとの停止・Windows 対応を自前で持ち、Dagu の画面からは
  エージェントの実行が 1 つのステップの中に隠れる。Dagu の `harness.run` に任せた
- **処理済みの版をこちらでも持つ**: どこまで処理したかを 2 か所で持つとずれる。change-watch の受け手の状態に任せた
- **change-watch のルールとプラグインで判定する（0.7 まで）**: `claim` → `render` → `harness.run` → `finish` の形で、実行待ち・`lock`・
  再試行・プロンプトの差分（フィルタ `gitlab_issue_changes`）・実行中の他人の動き（チェック `gitlab.others_acted`）を change-watch の
  プラグイン `gitlab_issue.py` が持っていた。条件と再試行が change-watch と Dagu に分かれ、Dagu の画面から追えないので、change-watch 0.8.0 で
  ルールをなくし、条件と再試行は Dagu、変化の受け渡しは `get` / `commit` にした
- **Webhook で受ける**: Dagu だけで完結させる要件と、GitLab から到達できない手元の PC でも動かすためにポーリングにした
