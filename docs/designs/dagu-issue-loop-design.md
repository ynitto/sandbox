# dagu-issue-loop 設計

GitLab の issue の変化を拾い、エージェント CLI を **同時に 1 セッションだけ**流す仕組みを、
常駐デーモン（agent-loop）ではなく Dagu の定期実行と api-watch の上に組む。実装は `tools/dagu-issue-loop/`、
使い方は同フォルダの README。

## 1. 位置づけと下敷き

| 借りたもの | 出どころ | 何を借りたか |
|---|---|---|
| 取得・変化の検知・実行待ち・再試行 | `tools/api-watch/` | 取得元（ページング・予定・ずらし・失敗の共有）、ルールの `watch`、その場で実行する受け渡し先（`command` と `work`） |
| 既読の版 | `tools/agent-loop/hooks/gitlab-issue-hook.py` | 候補キー `iid`、版 `updated_at`、送信成功後にだけ既読を進める |
| pid つきのスロット | `tools/agent-loop/agent_loop/semaphore.py` | ロックファイルに実行中の pid を書き、生きていれば取らない |
| CLI の起動方法 | `agents/<name>.json` | `command` / `prompt_via` / `prompt_flag` / `model_flag` / `{model}` / `{output_file}` |

api-watch は汎用の「取得・検知・受け渡し」に留め、GitLab の issue とエージェントに固有のこと（実行直前の取り直し・
自分の書き込みの見分け・プロンプト・1 セッションの排他・CLI の起動と停止）を `issue_loop.py handle` が持つ。

## 2. 構成

```
Dagu: api-watch-issue-agent（毎分）
  api-watch fetch --source gitlab-issues   取得元の schedule（毎時 0 分）の時刻のあとに未取得なら、jitter 0〜900 秒で取得
  api-watch evaluate --rule issue-agent    watch の項目が変わった issue をイベントにする
  api-watch work --rule issue-agent        イベントごとに issue_loop.py handle（APIWATCH_EVENT_FILE / APIWATCH_DELIVERY_ID）
                                              └─▶ <state_dir>/<project>-<hash>/state.json（seen / handle_attempts）
                                              └─▶ session.lock + session.json（全プロジェクト共通）
                                              └─▶ エージェント CLI（別プロセスグループ）
```

- Dagu のワークフローは api-watch の `dags` が雛形を作る 1 本。毎分起動し、GitLab に問い合わせるかは api-watch が取得元の
  `schedule` を見て決める（予定は api-watch の設定 1 か所）。毎分の起動では、失敗したもののやり直し（`retry_backoff_sec` を過ぎたもの）が進む
- state.json は `state.lock` の flock で排他した read-modify-write。書き戻しは tmp → rename。読めない state は `.broken-<時刻>` に退避して
  作り直す（止まるより進む）

### 2.1 GitLab への負荷

| 呼び出し | 頻度 | リクエスト数 |
|---|---|---|
| issue の一覧（api-watch） | 予定の時刻（毎時 0 分）のあと 1 回。0〜900 秒ずらす | issue 100 件ごとに 1 |
| 毎分の起動（予定の時刻のあとに取得済み） | 毎分 | **0** |
| `handle`（1 件流した） | 流すたび | 4（実行前の issue の取り直し・`/user`・実行後の issue の取り直し・notes）。`/user` は CLI の実行後に初めて引く |
| 取得の失敗のあと | — | `failure_backoff_sec`（300 秒、倍々で最大 1 時間）のあいだ 0 |

- ずらし幅は毎回ランダム（api-watch の `jitter_sec`）。PC やプロジェクトごとに固定のずれにすると、同じ設定を配った台数分が
  同じ時刻にそろいうる
- 同じ GitLab を見る別の取得元（MR など）とは `rate_limit.group` で、同時に取得せず要求の間隔と失敗の待ちを分け合う

## 3. 変化の検知と重複制御

### 3.1 api-watch 側

ルールの `watch` は title / state / labels / `assignees[*].username` / `milestone.title` / due_date / user_notes_count / description。
`updated_at` だけが動いた更新ではイベントにならない。初回（`initial: baseline`）は今ある issue を基準値にするだけ。
`order: none`: 打ち切った（dead）イベントが `retry` / `skip` されるまで同じ issue の後ろを止めない（打ち切って次の変化で拾う）。

### 3.2 handle の状態

| キー | 中身 |
|---|---|
| `seen[iid]` | 最後に流し終えた `{version, snapshot}`。`version` は `updated_at` |
| `handle_attempts[受け渡しの ID]` | 失敗した回数（プロンプトの `{attempt}` 用。200 件で古いものから切る） |

`snapshot` は title / state / labels / assignees / milestone / due_date / user_notes_count / 本文の sha。
以前の版（poll / run）の state.json は `seen` だけを引き継ぎ、待ち行列（queue / dead / inflight）は捨てる。

### 3.3 handle の規則

1. イベントのレコードの `updated_at` が `seen` の版以下なら、流し終えた変化として 0 で終わる（GitLab に問い合わせない）
2. セッション（§4）を取ってから issue を取り直す。取り直した版でもう一度 1 を見る
3. 絞り込み（`ISSUE_LOOP_ISSUE_STATE` / `LABELS` / `ASSIGNEE`）から外れていたら流さない
4. 差分は `seen` があれば前回流したときからの累積。無ければイベントの `before`（監視する項目だけ）を今の issue に重ねて
   変化前とみなす。監視していない項目は今と同じとみなし、余計な行を出さない
5. 流し終えたら §3.4。失敗したら `handle_attempts` を数えて 1 で終わる（再試行は api-watch）

実行を待つ間に同じ issue のイベントが重なっても、先の実行が最新の issue を取り直して流すので、後ろは 1 で落ちる。

### 3.4 自分の書き込みで自分を起こさない

エージェントが issue にコメントすると `updated_at` も `user_notes_count` も動き、api-watch はそれもイベントにする。
`handle` の成功後に:

1. issue を取り直し、その時点の版と snapshot を `seen` にする（自分の変更を吸収。次のイベントは §3.3-1 で落ちる）
2. issue の notes を新しい順に読み、実行を始めた時刻以降に**トークンの持ち主以外**の作者が居れば「他人の動きあり」とする
   （システムノート＝ラベル変更等も作者で判定する）
3. 他人の動きがあれば `seen` を実行前の版にとどめる。他人の動きは監視項目を変えるので、次の取得で api-watch がイベントを出し、
   版が `seen` より新しいので流れる

自分が分からない（`/user` が引けない）ときは、自分の書き込みと区別できないので「他人の動きなし」とする（同じ issue を流し続けない）。
実行後の取り直しに失敗したときは `seen` を実行前の版にとどめ、取りこぼさない側に倒す。この場合に限り、自分のコメントでもう一巡する。

## 4. 1 セッションの保証

3 層で守る。どれか 1 つが破れても 2 つ目のセッションは始まらない。

| 層 | 仕組み | 守る場面 |
|---|---|---|
| Dagu + api-watch | 同じ DAG が実行中なら次の定期実行を立てない。`work` はイベントを 1 件ずつ順に実行する | 毎分の起動が重なる |
| flock | `session.lock` を `LOCK_EX|LOCK_NB`。プロセスが死ねば OS が外す | 手で叩いた・別プロジェクトのワークフロー・Dagu の再起動 |
| pid | `session.json` に CLI の pid を書き、取得時に生きていれば見送る | handle が SIGKILL されて CLI だけが残った |

- ロックの既定は `state_dir/session.lock` で**プロジェクトをまたいで 1 つ**。要件「必ず 1 セッション」を
  プロジェクトを足しても崩さないため。プロジェクトごとに 1 つずつにしたいときだけ `ISSUE_LOOP_LOCK_FILE` を分ける。
- 取れなかった `handle` は終了コード 4。api-watch が `retry_backoff_sec` のあとに同じ受け渡しの ID で流し直す
  （以前の `run` は 0 で終えて待ち行列に残していた。今は待ち行列が api-watch にあるので、失敗として返して残させる）。
- CLI は `start_new_session=True` で別のプロセスグループに置き、止めるときは `killpg`（SIGTERM → 15 秒 → SIGKILL）。
  Dagu の停止は SIGTERM で来る（api-watch の `work` も実行中のコマンドへ同じシグナルを伝える）ので、ハンドラで CLI を止めて 1 で終わる。
- CLI 自身のタイムアウト（`ISSUE_LOOP_AGENT_TIMEOUT`）は api-watch の受け渡し先の `timeout_sec` より短くする。
  先にスクリプトが止めて記録し、api-watch のタイムアウトは後ろの安全網にする。

### 4.1 pid の再利用

`session.json` の pid が別のプロセスに再利用されると、そのプロセスが終わるまで見送り続ける。
agent-loop のスロットも同じ扱い（生存 pid は保持）で、誤って 2 セッション目を始めるより止まる方を選ぶ。
解消は `session.json` の削除。

## 5. 失敗と再試行

- 失敗 = 終了コード ≠ 0（1 / 2 / 3 / 4）。api-watch が受け渡しを `send_failed` にし、`retry_backoff_sec × 2^(回数-1)` のあとの `work` で
  同じ受け渡しの ID のまま流し直す。`max_attempts` に達したら `dead`。issue が次に変われば新しいイベントになる
- `api-watch retry <ID>` で打ち切ったものを流し直す。`issue_loop.py forget IID` で `seen` から外し、次のイベントで必ず流させる

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
（`internal/cmn/cmdutil/lifecycle_windows.go`）。api-watch・handle・エージェント CLI は同じ Job に入るので一緒に消え、
**取り残しは起きない**。受け渡しは `sending` のまま残り、期限（`timeout_sec` + 120 秒）が切れたあとの `work` が同じ ID で流し直す。

`issue_loop.py` を動かす Python は `ISSUE_LOOP_PYTHON` で渡す（Windows の標準のインストーラは `python3` を置かない）。

## 7. 経緯と採らなかった案

- **初版の poll / run（2 本の DAG + 状態ファイルの待ち行列）**: 取得（毎時）と実行（毎分）を別 DAG に分け、state.json の
  `queue` / `inflight` / `dead` でつないでいた。同じ issue の変化を 1 件にまとめ（coalesce）、`inflight` の痕跡から強制終了された
  run を数え直していた。api-watch が取得・検知・実行待ち・再試行・打ち切り・失敗の共有・同じ API の問い合わせのまとめを
  汎用に持ったので、取得と待ち行列をそちらに移し、`issue_loop.py` は `handle` だけにした（約 1250 行 → 約 940 行）。
  まとめは「流し終えた版より古いイベントを飛ばす」（§3.3）で、強制終了の数え直しは api-watch の受け渡しの期限で代える
- **受け渡し先の DAG とキュー（api-watch の dagu アダプタで enqueue）**: 1 本のワークフローにした。取得から実行までが
  1 つの実行履歴に並び、Dagu 側の設定（キュー）も要らない
- **Dagu のキューだけで直列化する**: 手で叩いた CLI・Dagu の再起動・取り残された子プロセスは守れない。
  スクリプト側の排他を本体にし、Dagu・api-watch 側は重ねない程度に留めた
- **Webhook で受ける**: Dagu だけで完結させる要件と、GitLab から到達できない手元の PC でも動かすために
  ポーリングにした。Webhook が使える環境は agent-loop の webhook を使う
- **agent-loop のスロット（`~/.agents/slots/`）を共有する**: agent-loop と並べて動かしたときに合計を 1 にできるが、
  agent-loop の内部形式に依存する。今回は独立に保ち、必要になったら `ISSUE_LOOP_LOCK_FILE` と別に足す
