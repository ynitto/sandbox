# dagu-issue-loop 設計

GitLab の issue をポーリングして変化を拾い、エージェント CLI を **同時に 1 セッションだけ**流す仕組みを、
常駐デーモン（agent-loop）ではなく Dagu の定期実行の上に組む。実装は `tools/dagu-issue-loop/`、
使い方は同フォルダの README。

## 1. 位置づけと下敷き

| 借りたもの | 出どころ | 何を借りたか |
|---|---|---|
| 既読と ack の 2 相 | `tools/agent-loop/hooks/gitlab-issue-hook.py` + `_event_fallback.py` | 候補キー `project:issue:iid`、版 `updated_at`、初回 baseline、送信成功後にだけ既読を進める |
| pid つきのスロット | `tools/agent-loop/agent_loop/semaphore.py` | ロックファイルに実行中の pid を書き、生きていれば取らない |
| CLI の起動方法 | `agents/<name>.json` | `command` / `prompt_via` / `prompt_flag` / `model_flag` / `{model}` / `{output_file}` |

agent-loop は tmux ペインを常駐させ、フックの `check()` と `ack()` を同じプロセス内で呼ぶ。Dagu では
**ポーリングと実行が別プロセス・別 DAG** になるので、その間を状態ファイルの待ち行列でつなぐのが
本設計の差分である。

## 2. 構成

```
gitlab-issue-poll (0 * + 0〜15 分)  ──▶  issue_loop.py poll  ──┐
                                                    ▼
                                    <state_dir>/<project>-<hash>/state.json
                                      seen / queue / inflight / dead
                                                    ▲
gitlab-issue-agent (*)   ──▶  issue_loop.py run   ──┘── session.lock + session.json（全プロジェクト共通）
                                   └─▶ エージェント CLI（別プロセスグループ）
```

- **poll と run を別 DAG にした理由**: 1 本の DAG で poll → run とつなぐと、エージェントが 1 時間走る間
  ポーリングも止まる。別 DAG なら実行中も変化を積み続け、run が終わった次の分に次を取れる。
- state.json は `state.lock` の flock で排他した read-modify-write。poll と run が同時に触っても壊れない。
  書き戻しは tmp → rename。読めない state は `.broken-<時刻>` に退避して作り直す（止まるより進む）。

### 2.1 GitLab への負荷

| 呼び出し | 頻度 | リクエスト数 |
|---|---|---|
| poll | 毎時 0 分に起動し、`--jitter 900` で 0〜900 秒ランダムに待ってから取得 | issue 100 件ごとに 1（上限 `MAX_PAGES`） |
| run（待ち行列が空） | 毎分 | **0**。ロックと state.json だけを見て終わる |
| run（1 件流した） | 流すたび | 3（`/user`・issue の取り直し・notes）。`/user` は CLI の実行後に初めて引く |

- 定刻を Dagu の cron（毎時 0 分）に置き、ずらしはスクリプト側で行う。Dagu v2 のスケジュールにジッタの指定が無いため。
  ずらす幅は毎回ランダムにする。PC やプロジェクトごとに固定のずれにすると、同じ設定を配った台数分が
  同じ時刻にそろいうる。
- `--jitter` は CLI 引数 → `ISSUE_LOOP_POLL_JITTER` → 0 の順。手で `poll` を叩くときは待たない。
- DAG の `timeout_sec` はジッタの最大（900 秒）より長くする（1200 秒）。
- 当初は run の開始ごとに `/user` を引いており、待ち行列が空でも 1 時間に 60 リクエストあった。実行後へ遅らせた。

## 3. 変化の検知と重複制御

### 3.1 状態

| キー | 中身 |
|---|---|
| `seen[iid]` | 最後に「処理済み」とした `{version, snapshot}` |
| `queue[iid]` | 流す予定の 1 件。`version` / `snapshot` / `prev_snapshot` / `changes` / `attempts` / `next_attempt_at` / `enqueued_at` |
| `inflight` | 実行中の `{key, version, started_at, pid}`。run が SIGKILL されたときの痕跡 |
| `dead[iid]` | 打ち切った版 |

`snapshot` は title / state / labels / assignees / milestone / due_date / user_notes_count / 本文の sha。

### 3.2 poll の規則

1. 初回は全件を `seen` に入れて終わる（agent-loop と同じ baseline。導入直後に全 issue でエージェントを起こさない）
2. `seen` と同じ版 → 何もしない
3. 版は違うが snapshot が同じ → `seen` の版だけ進める（**updated_at だけの更新では起こさない**）
4. 待ち行列にある → 最新の版で差し替え（coalesce）。`enqueued_at` と `attempts` は引き継ぎ、差分は
   `prev_snapshot`（前回流した時点）からの累積で作り直す
5. `inflight` と同じ版 → 積まない
6. `dead` と同じ版 → 積まない。新しい版なら `dead` から外して積む
7. 取得が最後のページまで済んだときだけ、取得結果に無い（絞り込みから外れた）待ち行列の項目を下ろす。
   途中で打ち切った（`MAX_PAGES`）ときは「無い」と言い切れないので下ろさない

### 3.3 自分の書き込みで自分を起こさない

エージェントが issue にコメントすると `updated_at` も `user_notes_count` も動く。agent-loop の
gitlab-issue-hook はこれを区別しないため、コメントを返すワークフローでは自分のコメントで再発火しうる。
本設計では run の成功後に:

1. issue を取り直し、その時点の版と snapshot を `seen` にする（自分の変更を吸収）
2. issue の notes を新しい順に読み、`started_at` 以降に**トークンの持ち主以外**の作者が居れば
   「他人の動きあり」とする（システムノート＝ラベル変更等も作者で判定する）
3. 待ち行列にある同じ issue（実行中に poll が積み直したもの）は、他人の動きがあれば残して再実行、
   無ければ下ろす。poll がまだ拾っていなくても他人の動きがあれば積む

自分が分からない（`/user` が引けない）・取り直しに失敗したときは**取りこぼさない側**に倒す
（もう一度流す）。この場合に限り、自分のコメントでもう一巡する可能性が残る。

## 4. 1 セッションの保証

3 層で守る。どれか 1 つが破れても 2 つ目のセッションは始まらない。

| 層 | 仕組み | 守る場面 |
|---|---|---|
| Dagu | 同じ DAG が実行中なら次の定期実行を立てない（v2 のスケジューラの既定） | 毎分の起動が重なる |
| flock | `session.lock` を `LOCK_EX|LOCK_NB`。プロセスが死ねば OS が外す | 手で `run` を叩いた・別プロジェクトの DAG・Dagu の再起動 |
| pid | `session.json` に CLI の pid を書き、取得時に生きていれば見送る | run が SIGKILL されて CLI だけが残った |

- ロックの既定は `state_dir/session.lock` で**プロジェクトをまたいで 1 つ**。要件「必ず 1 セッション」を
  プロジェクトを足しても崩さないため。プロジェクトごとに 1 つずつにしたいときだけ `ISSUE_LOOP_LOCK_FILE` を分ける。
- 取れなかった run は終了コード 0。毎分の起動で失敗履歴を積まない。
- CLI は `start_new_session=True` で別のプロセスグループに置き、止めるときは `killpg`（SIGTERM → 15 秒 → SIGKILL）。
  Dagu の停止は SIGTERM で来るので、ハンドラで CLI を止めて失敗を記録してから終わる。DAG の
  `max_clean_up_time_sec: 60` はこの片付けが終わるまで Dagu に待たせるため。
- CLI 自身のタイムアウト（`ISSUE_LOOP_AGENT_TIMEOUT`）は DAG の `timeout_sec` より短くする。
  先にスクリプトが止めて記録し、Dagu のタイムアウトは後ろの安全網にする。
- run が SIGKILL されて `inflight` が残っていたら、次にロックを取った run が失敗 1 回として数え直す。

### 4.1 pid の再利用

`session.json` の pid が別のプロセスに再利用されると、そのプロセスが終わるまで見送り続ける。
agent-loop のスロットも同じ扱い（生存 pid は保持）で、誤って 2 セッション目を始めるより止まる方を選ぶ。
解消は `session.json` の削除。

## 5. 失敗と再試行

- 失敗 = 終了コード ≠ 0 / タイムアウト / 中止。`attempts += 1`、`next_attempt_at = now + backoff × 2^(attempts-1)`
- `MAX_ATTEMPTS` に達したら `dead` へ移し、その版を `seen` にする。次に issue が変われば 3.2-6 で蘇る
- `forget IID` で `seen` / `queue` / `dead` から外し、次の poll で新規として拾い直させる

## 6. Windows（ネイティブ）

POSIX 前提の箇所を OS ごとに切り替え、規則（§3〜§5）は変えない。

| 役目 | POSIX | Windows |
|---|---|---|
| state.lock / session.lock | `fcntl.flock` | `msvcrt.locking`（先頭 1 バイト、待つときは非ブロッキングで回す）。どちらもハンドルを閉じる・プロセスが死ぬと外れる |
| 生存確認 | `os.kill(pid, 0)` | `OpenProcess` + `GetExitCodeProcess == STILL_ACTIVE`。**Windows の `os.kill` は相手を終了させる**ので使わない |
| CLI の起動 | `start_new_session=True` | `CREATE_NEW_PROCESS_GROUP` |
| CLI を止める | `killpg` SIGTERM → 15 秒 → SIGKILL | Ctrl+Break → 15 秒 → `taskkill /T /F` |
| 停止の受け取り | SIGTERM / SIGINT / SIGHUP | SIGINT / SIGBREAK |
| 出力の転送 | — | 両方とも読み取りスレッド（パイプの `select` は Windows で使えないため、実装を 1 つにした） |
| コマンド行の分割 | `shlex.split` | `shlex.split(posix=False)` + 外側の引用符を外す（バックスラッシュのパスを壊さない） |
| 実行ファイル | — | 両方とも `shutil.which` で先頭を絶対パスにする（`.cmd` の CLI を起動できるように） |
| 標準出力の文字コード | UTF-8 でないロケールなら UTF-8 に切り替える | UTF-8 に固定（コンソールのコードページで日本語のログが落ちるのを防ぐ） |

Windows の Dagu は停止時にシグナルを送らず、Job Object（失敗すればプロセスツリー）ごと強制終了する
（`internal/cmn/cmdutil/lifecycle_windows.go`）。run とエージェント CLI は同じ Job に入るので一緒に消え、
**取り残しは起きない**。その代わり run はその場で失敗を記録できず、`inflight` が残る。次にロックを取った run が
§4 の「強制終了された run」と同じ経路で失敗 1 回として数え直す。

DAG の Python は `ISSUE_LOOP_PYTHON` で渡す（Windows の標準のインストーラは `python3` を置かない）。

## 6.5 api-watch で取得する構成（handle）

`poll` と待ち行列（state.json の queue / dead）の代わりに api-watch を使う構成。Dagu のワークフローは
api-watch の `dags` が書き出す `api-watch-issue-agent` の 1 本で、fetch → evaluate → work の 3 ステップ。スケジュールは api-watch の
設定にだけ置く（手書きの DAG を持つと、設定とワークフローの 2 か所にスケジュールができてずれる）。work は api-watch の「その場で実行する受け渡し先」
（`targets[].command`）として `issue_loop.py handle` を 1 件ずつ呼ぶ。役割の分け方:

| 受け持ち | `poll` / `run` | api-watch 構成 |
|---|---|---|
| 取得・ページング・再開 | `GitLab.list_issues` | api-watch の取得元（`gitlab-issues`） |
| 取得のずらし | `--jitter 900` | 取得元の `jitter_sec: 900` |
| 変化の判定 | `snapshot` の比較 | ルールの `watch`（`snapshot` と同じ項目を生のパスで書く） |
| 待ち行列 | state.json の queue | api-watch の deliveries（work が古い順に 1 件ずつ実行。`order: none`） |
| 再試行・打ち切り | `settle_failure`（attempts / backoff / dead） | 受け渡し先の `max_attempts` / `retry_backoff_sec`。dead は次の変化で新しいイベントになる |
| 1 セッション | `single_session` | 同じ（work が直列に実行するのに加えて二重にする。取れなければ 4 で終わり、次の起動で再試行） |
| 自分の書き込みを無視 | `settle_success` | `cmd_handle`（下） |

受け渡し先の DAG とキューに分けた初版（api-watch の dagu アダプタで `gitlab-issue-handle` に enqueue）から、1 本のワークフローにした。
取得から実行までが 1 つの実行履歴に並び、Dagu 側の設定（キュー）も要らない。代わりに、失敗の再試行は次の起動（1 時間後）になる。

`handle` が state.json に持つのは `seen`（最後に流した版）と、受け渡しの ID ごとの失敗回数（プロンプトの `{attempt}` 用、200 件で切る）だけ。
イベントのファイルと受け渡しの ID は環境変数 `APIWATCH_EVENT_FILE` / `APIWATCH_DELIVERY_ID` で受け取る。

- **飛ばす判定**: イベントのレコードの `updated_at` が `seen` の版以下なら、もう流した変化として 0 で終わる。
  エージェント自身のコメントで api-watch が出したイベントは、実行後に取り直した版を `seen` にしているのでここで落ちる。
  同じ issue のイベントが重なったときも、先の実行が最新の issue を取り直して流すので、後ろはここで落ちる（`poll` のまとめと同じ効き目）
- **取り直し**: 飛ばさないときは、セッションを取ってから issue を取り直し、その版で再度飛ばす判定をする。絞り込み（state / labels / assignee）から
  外れていたら流さない（`poll` が待ち行列から下ろすのに当たる）
- **差分**: `seen` があれば前回流したときからの累積（`poll` と同じ）。無ければイベントの `before`（監視する項目だけ）を今の issue に重ねて
  変化前とみなす。監視していない項目は今と同じとみなし、余計な行を出さない
- **実行中の他人の動き**: `others_acted` が真なら `seen` を実行前の版にとどめる。他人の動きは監視項目（コメント数など）を変えるので、
  次の取得で api-watch がイベントを出し、版が `seen` より新しいので流れる。`poll` 構成のようにその場で積み直しはしない
- **順序**: `order: completion` / `enqueue` だと、打ち切った（dead）イベントが `retry` / `skip` されるまで同じ issue の後ろが止まる。
  `poll` 構成の「打ち切って次の変化で拾う」に合わせて `none` にした。古いイベントが後から再試行されても、飛ばす判定で落ちる
- **同じ取得元の共有**: 通知など別のワークフローが同じ取得元を使うときは、取得元の `min_interval_sec`（取得し終えた結果を使い回す）と
  `busy_wait_sec`（取得中なら待ってその結果を使う）で GitLab への問い合わせを 1 回にまとめ、失敗は `failure_backoff_sec` で共有する
  （api-watch の設計 §3.6）

試験は本物の api-watch（`run --rule issue-agent` で `handle` をその場で実行）と偽の GitLab・偽のエージェントで回す（`ApiWatchHandleTest`）。

## 7. 採らなかった案

- **Dagu のキューだけで直列化する**: Dagu v2 のグローバルキュー（`queue:` + 設定の `max_concurrency: 1`）でも
  DAG 実行は直列にできるが、手で叩いた CLI・Dagu の再起動・取り残された子プロセスは守れない。
  スクリプト側の排他を本体にし、Dagu 側は重ねない程度に留めた。複数プロジェクトの agent DAG を同じグローバル
  キューに入れるのは併用してよい。
- **Webhook で受ける**: Dagu だけで完結させる要件と、GitLab から到達できない手元の PC でも動かすために
  ポーリングにした。Webhook が使える環境は agent-loop の webhook を使う。
- **agent-loop のスロット（`~/.agents/slots/`）を共有する**: agent-loop と並べて動かしたときに合計を 1 にできるが、
  agent-loop の内部形式に依存する。今回は独立に保ち、必要になったら `ISSUE_LOOP_LOCK_FILE` と別に足す。
