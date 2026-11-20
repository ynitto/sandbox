# gitlab-ci-triage-agent 設計

決めた GitLab のプロジェクトで**失敗したパイプライン**を定期的に拾い、失敗したジョブのログを**決まった規則だけ**で分類して、
診断のコメント（分類・根拠のログ抜粋・次のアクション・既知のパターンなら修正案）を 1 回だけ書く。gitlab-mr-review-agent と同じく
Dagu の定期実行と change-watch の上に組み、CI に固有の部分だけを足す。実装は `tools/change-watch/agents/gitlab-ci-triage-agent/`
（`gitlab_ci.py` と設定の例）にあり、本 PR で実装済み。使い方は同フォルダの README に記載している。

要件:

1. 同じパイプラインに重ねて書かない
2. 分類は決定的にする（同じログ・同じパターンなら、いつ誰が動かしても同じ診断。v1 は LLM を使わない）
3. ログの抜粋から秘匿情報らしい文字列を消してから書く
4. 書かずに確かめられる（dry-run）

## 1. 目的と全体像

CI が落ちたとき、人はまずログを開いて「テストが落ちたのか・依存が取れなかったのか・ランナーや枠の問題か・設定の誤りか」を見分ける。
この最初の切り分けを機械に任せ、落ちた場所の抜粋と次の一手を、その変更を見ている人のところ（MR・コミット・ハブの issue）に届ける。
直すこと・再実行すること・MR を止めることはしない（§13）。

```
 Dagu: change-watch-ci-triage（毎分起動。1 回の起動で 1 件。GitLab への問い合わせは 5 分ごと）
   get       change-watch get --source gitlab-pipelines --consumer ci-triage --watch updated_at --one
               失敗したパイプラインのうち、前回 commit してから現れた・変わったものを 1 件受け取る（output: CHANGE）
   collect   現れたか変わった失敗なら → change-watch --plugin gitlab_ci ci collect ${CHANGE.key}     ← 条件（Dagu の preconditions）
               パイプライン・失敗したジョブ・ログの末尾を読み、秘匿情報を消して作業フォルダに置く（GitLab から読むだけ）
   diagnose  change-watch --plugin gitlab_ci ci diagnose ${CHANGE.key}
               パターンで分類し、コメントの本文を作る（通信しない。同じ入力なら同じ出力）
   post      change-watch --plugin gitlab_ci ci post ${CHANGE.key}
               投稿先（MR → コミット → ハブの issue）を決め、印が無ければ 1 回だけ書く。dry-run なら書かずに標準出力へ出す
   commit    change-watch commit --source gitlab-pipelines --consumer ci-triage ${CHANGE.key}
 Dagu: change-watch-gitlab-ci-triage-agent-maintenance（毎日）  保持期間を過ぎた取得結果の整理（gc）
```

| 受け持ち | どこ | 何を |
|---|---|---|
| GitLab への問い合わせ（いつ取得するか・まとめる・失敗の待ち）と、前回 commit してからの変化 | change-watch | 取得元 `gitlab-pipelines`、受け手 `ci-triage`（`get --watch updated_at --one`・`commit`） |
| 起動・条件・再試行・ログと実行履歴 | Dagu | ワークフロー `change-watch-ci-triage`（`get` → `collect` → `diagnose` → `post` → `commit`） |
| ログの取得・分類・本文・投稿先と書き込み | `gitlab_ci.py`（change-watch のプラグインが足すコマンド `ci`） | `collect` / `diagnose` / `post` と、手で使う `triage`・`patterns`。change-watch の設定は読まない（`needs_config=False`）。`python3 gitlab_ci.py <コマンド>` と直接呼ぶ口も残す |
| 分類の規則 | `gitlab_ci.py` の組み込みのパターンと、現場の `ci-triage-patterns.json` | §5 |

## 2. 配置の判断

| 観点 | A: change-watch のエージェント（`tools/change-watch/agents/gitlab-ci-triage-agent/`） | B: 独立したツール（`tools/gitlab-ci-triage/`） |
|---|---|---|
| 「まだ診断していない失敗」の管理 | change-watch の受け手（`get` / `commit`）がそのまま持つ | 自前の状態（DB かファイル）と、その整理・移行を書く |
| 取得の予定・ずらし・失敗の待ち・レート制限 | 取得元の設定で済む。同じ GitLab を見るほかのエージェントと `rate_limit.group` で分け合う | 自前で実装し、ほかのエージェントとは分け合えない |
| 起動・再試行・履歴 | Dagu（ほかのエージェントと同じ見え方） | cron か常駐を自前で持つ。履歴の置き場も別になる |
| 入れ方・置き場所・接続設定 | `install.py install --agent gitlab-ci-triage-agent`。既にある決まり（プログラム・設定・ワークフロー・状態）に乗る | インストーラ・設定の読み方・トークンの置き方を新しく決める |
| 条件の置き場所 | Dagu の `preconditions`（ほかのエージェントと同じ） | ツールの設定に持つことになり、流儀が割れる |
| 単体で配れるか | change-watch と Dagu が要る | 単体で動く |
| 新しく書く量 | プラグイン 1 ファイル・例 5 つ・試験 | 上に加えて、取得・状態・スケジュール・インストール |

**結論: A。** 新しく要るのは「ログを読んで分類し、決まった場所に 1 回書く」ことだけで、残り（変化の検知・予定・再試行・置き場所）は
change-watch と Dagu が既に受け持っている。B の利点は単体で配れることだけで、このリポジトリの利用者は既に change-watch と Dagu を
入れている。分類と本文の組み立ては通信をしない純粋な関数にしておくので、あとで B が要ることになっても `gitlab_ci.py` から切り出せる。

インストーラ（`install.py`）は変えない。エージェントのフォルダの直下の `*.py` をプログラムとして、`change-watch-*.yaml.example` を
ワークフローとして、ほかの `*.example` を設定の雛形として置く決まりに、そのまま乗る（§9 の表）。

## 3. 取得元（source）

### 3.1 要求

GitLab の「パイプラインの一覧」を、失敗したものに絞って取る。

| 項目 | 値 | 理由 |
|---|---|---|
| `request.url` | `{{ conn.url }}/api/v4/projects/{{ env.GITLAB_PROJECT \| urlencode }}/pipelines` | gitlab-mr-review-agent と同じ書き方 |
| `request.headers` | `PRIVATE-TOKEN: "{{ conn.token }}"` | 同上 |
| `request.query` | `{ status: failed, order_by: updated_at, sort: asc }` | 終わって失敗になったものだけ（実行中に 1 つ落ちていても、パイプラインはまだ `running`）。絞り込み（`ref: main`・`source: push` など）を足すならここ |
| `key` | `id`（パイプラインの ID） | プロジェクトをまたいで一意。`get --one` は小さい順（古い順）に返す |
| `pagination` | `{ type: page, size_param: per_page, size: 100 }` | |
| `incremental` | `{ type: max_value, field: updated_at, param: updated_after, initial: "<入れる日>" }` | 失敗の一覧は増える一方なので、毎回全部は取らない。`initial` が無いと最初の取得で過去の失敗を全ページ取るので、例には日付を書き、入れるときに直してもらう |
| `schedule` | `"*/5 * * * *"` | 落ちてから診断が付くまで最大 5 分＋ずらし＋順番待ち。レビュー（15 分）より短く、ハブ（5 分）と同じ |
| `jitter_sec` / `busy_wait_sec` / `failure_backoff_sec` | 60 / 300 / 300 | ハブ・MR の例に合わせる |
| `rate_limit` | `{ min_interval_sec: 1, group: gitlab }` | 同じ GitLab を見るほかの取得元と分け合う |
| `retention.record_days` | 30 | 増分取得で組み立てた姿から、30 日取得されていない失敗を消す（下の「一覧から外れた」） |

一覧のレコードで使う項目: `id`・`iid`・`project_id`・`sha`・`ref`・`status`・`source`・`updated_at`・`web_url`。ジョブとログは一覧に無いので
`collect` が取りに行く。

### 3.2 受け手と二重投稿の防止

三段で守る（gitlab-mr-review-agent の §2 と同じ考え方）。

| 段 | 仕組み | 防ぐもの |
|---|---|---|
| 検知 | `get --watch updated_at`。失敗の一覧に現れたとき（`appeared`）と、再実行でもう一度失敗になったとき（`changed`）だけを変化にする | 一覧の取り直しでの起動 |
| 処理し終えた状態 | `commit` が受け手 `ci-triage` × パイプラインごとに、受け取ったときの姿を覚える。次の `get` はその姿からの変化だけを返す | 同じ失敗での再実行（ふだんはここで止まる） |
| 書き込みの印 | 本文の 1 行目に `<!-- ci-triage-agent:diagnosis pipeline=<ID> fp=<診断の指紋> -->`。`post` は投稿先の既存のコメントを読み、同じ `pipeline` と `fp` の印があれば書かない（§7.3） | `post` のあと `commit` の前に止まった・書き込みの応答だけ失われた Dagu の再試行・状態を消したあと・取得条件を変えて基準を取り直したあと |

- `commit` は `--refresh` を付けない。書き込むのはコメントで、パイプラインのレコードは変わらない
- 初めての `get` は `--initial baseline`（既定）。入れた時点で一覧にある失敗は覚えるだけで、診断しない。`incremental.initial` との組で、
  過去の失敗に一斉に書き込むことはない
- 条件に合わない変化（`removed` など）も `commit` する（`continue_on.skipped`）
- `collect`・`diagnose`・`post` が失敗しても `commit` する（`continue_on.failure`。change-watch の README の決まり）。やり直しは
  Dagu の `retry_policy`、尽きたら Dagu の履歴に失敗として残り、その失敗は次に変わるまで拾わない

### 3.3 再実行（retry）されたパイプライン

GitLab の再実行は、同じパイプライン ID のまま失敗したジョブを新しいジョブ（新しいジョブ ID）で流し直す。

| 起きたこと | 一覧（`status=failed`）での見え方 | 扱い |
|---|---|---|
| 再実行中 | 一覧に出ない（`running`） | 何も起きない |
| 再実行で成功した | 一覧に出なくなる。増分取得なので手元の姿（失敗のまま）は残り、変化にならない | 何もしない。書いた診断は消さず、書き換えない（「直りました」の追記は v1 ではしない。§12） |
| 再実行でまた失敗した | `updated_at` が進んで取り直され、`changed` で返る | もう一度 `collect` → `diagnose`。**診断の指紋（§5.5）が前と同じなら書かない**（同じ原因で同じジョブが落ちた）。違えば、見出しに「（再実行後）」を付けた新しいコメントを足す |
| 一覧から外れた（30 日取得されていない） | `removed` | 条件に合わないので何もせず `commit` |
| 新しいコミットで新しいパイプラインが失敗した | 別の ID で `appeared` | 別のパイプラインなので、同じ MR でも新しく書く |

ジョブ単位の自動リトライ（`.gitlab-ci.yml` の `retry:`）はパイプラインが終わる前に済むので、ここには現れない。ジョブの一覧は既定で
最新の試行だけを返す（`include_retried` を付けない）ので、診断するのはいつも最後の試行。

印の `fp` は、その投稿先にある**同じパイプラインのすべての印**と比べる。A → B → A と行き来しても、A は 1 回しか書かない。

## 4. プラグイン `gitlab_ci.py` のコマンド

`change-watch --plugin gitlab_ci ci <コマンド> ...` で呼ぶ（`register(api)` が `api.add_command("ci", …, needs_config=False)` で足す。
`--config`・`--connections` は要らない）。GitLab の URL・トークン・プロジェクトは環境変数 `GITLAB_URL`・`GITLAB_TOKEN`・`GITLAB_PROJECT`
から読む（Dagu が `.env` から渡す。gitlab-mr-review-agent と同じ）。HTTP は `gitlab_mr.py` の `GitLab` クラスと同じ作り（標準ライブラリ・
別の接続先へのリダイレクトを拒む）を、このファイルの中に持つ。プラグインは 1 ファイルで完結させ、ほかのエージェントのファイルを import しない。

### 4.1 分け方

通信の向きで 3 つに分ける。Dagu のステップもこの単位。

| コマンド | 通信 | 分けた理由 |
|---|---|---|
| `collect` | GitLab から**読むだけ** | 一時的な失敗（終了コード 3）を Dagu に再試行させてよい。何度やり直しても害が無い |
| `diagnose` | **しない** | 分類と本文の組み立てを、通信と切り離して決定的にする。フィクスチャだけで試験でき、現場はログのファイルだけでパターンを試せる。再試行しない（失敗は入力かパターンの誤り） |
| `post` | 投稿先を決めるための読みと、**1 回の書き込み** | 書き込みを 1 か所に閉じ、dry-run と印の確認をここだけに置く |

受け渡しは作業フォルダのファイルで行う（Dagu の変数にログや本文を載せない。大きさの上限と、Windows の引数の長さに当たるため）。
ステップの標準出力は、条件に使う小さな JSON を 1 行だけ（ASCII。`json.dumps` の既定）。

作業フォルダは `<CI_TRIAGE_WORK_DIR>/<パイプライン ID>/`（既定は `~/.local/state/change-watch/gitlab-ci-triage-agent/work`。
Windows は `%LOCALAPPDATA%\change-watch\gitlab-ci-triage-agent\work`）。どのコマンドもパイプライン ID からこの場所を求めるので、
ステップの間でパスを渡さない。

| ファイル | 書くコマンド | 中身 |
|---|---|---|
| `pipeline.json` | `collect` | パイプライン 1 件の応答（`id`・`sha`・`ref`・`source`・`tag`・`yaml_errors`・`web_url` など） |
| `jobs.json` | `collect` | 診断するジョブの配列（`id`・`name`・`stage`・`failure_reason`・`web_url`・`trace`（ログのファイル名。無ければ `null`）・`skipped_trace`（上限で読まなかったら `true`）） |
| `trace-<ジョブ ID>.log` | `collect` | ログの末尾（整えて、秘匿情報を消したもの。§6.2・§6.3）。**消す前のログはディスクに置かない** |
| `diagnosis.json` | `diagnose` | 診断（§5.6） |
| `comment.md` | `diagnose` | コメントの本文（§6.1。1 行目が印） |

### 4.2 コマンド

共通: `<パイプライン>` はパイプラインの ID（`#123` の `#` は付けても付けなくてもよい）。`--project` の既定は `GITLAB_PROJECT`。
`--dir <フォルダ>` を付けると、作業フォルダをその場所にする（試験・手で試すとき）。`collect` と `triage` では、
解決したフォルダが作業ルート配下にある場合だけ取得する。作業ルート自体や配下の外を指定すると終了コード 1。

| コマンド | 引数 | すること | 標準出力 |
|---|---|---|---|
| `collect <パイプライン>` | `[--dir D]` | 作業フォルダを作り直し（そのパイプラインの前回のものは消す）、パイプライン・失敗したジョブ・ログを読んで置く（§4.3）。7 日より古いほかのパイプラインの作業フォルダを消す | `{"pipeline": 123, "failed_jobs": 2, "yaml_errors": false}` |
| `diagnose <パイプライン>` | `[--dir D] [--patterns F]` | 作業フォルダを読み、パターンで分類して `diagnosis.json` と `comment.md` を書く。`--patterns` の既定は `CI_TRIAGE_PATTERNS`（空なら組み込みだけ） | `{"pipeline": 123, "category": "dependency", "known": true, "jobs": 2, "fp": "1a2b3c4d5e6f"}` |
| `diagnose --trace-file F` | `[--job-name N] [--stage S] [--failure-reason R] [--patterns F]` | ログのファイル 1 つを 1 つのジョブとして分類し、当たったパターン・抜粋・本文を人が読む形で出す（パターンを足すときの確かめ。作業フォルダは使わない） | 人が読む文 |
| `post <パイプライン>` | `[--dir D] [--dry-run]` | `comment.md` と `diagnosis.json` を読み、投稿先を決め（§7）、印を確かめて 1 回書く。`--dry-run` か `CI_TRIAGE_DRY_RUN` が真なら書かずに出す（§8） | `{"posted": true, "reason": "posted", "target": "mr", "ref": "!12", "url": "…"}`。dry-run のときは §8 の形 |
| `triage <パイプライン>` | `[--dir D] [--patterns F] [--dry-run]` | `collect` → `diagnose` → `post` を続けて行う（手で 1 件を確かめる口。中身は同じ関数）。途中で失敗したらそこで止まり、その終了コードで終わる | `post` と同じ |
| `patterns` | `[--patterns F] [--check]` | 組み込みと現場のパターンを重ねた結果（いま効いているもの）を JSON で出す。`--check` は形だけを確かめ、誤りを 1 行ずつ出す | パターンの JSON／`--check` は何も出さないか誤りの一覧（標準エラー） |

`post` の `reason`: `posted`（書いた）・`duplicate`（同じ印があったので書かなかった）・`dry_run`・`no_target`（投稿先が決まらなかった）。

### 4.3 `collect` が読むもの

| 順 | API | 使うところ |
|---|---|---|
| 1 | `GET /projects/:id/pipelines/:pipeline_id` | `pipeline.json`。`status` が `failed` でなければ（読みに行くまでに再実行された）、ジョブを読まずに `failed_jobs: 0`・`"status": "<今の状態>"` を出して 0 で終わる |
| 2 | `GET /projects/:id/pipelines/:pipeline_id/jobs?scope[]=failed&per_page=100`（全ページ） | `allow_failure` が真のジョブを除く（パイプラインを落としていない）。ジョブ ID の小さい順に並べ、先頭 20 件（`MAX_JOBS`）だけログを読む。残りは `skipped_trace: true` で名前だけ持つ |
| 3 | ジョブごとに `GET /projects/:id/jobs/:job_id/trace`（`text/plain`） | 流しながら読み、末尾 1 MiB（`TRACE_TAIL_BYTES`）だけを手元に残す。404・空なら `trace: null`（ログが無い。消された・始まる前に落ちた） |

`yaml_errors` があるパイプライン（`.gitlab-ci.yml` の誤りでジョブが 1 つも作られない）は、1 だけで診断の材料がそろう。
下流のパイプラインを起こすジョブ（bridge）の失敗は、v1 では読まない（§12）。

### 4.4 終了コード

change-watch に合わせる（0 / 1 / 2 / 3）。Dagu の `retry_policy.exit_code: [3]` で一時的な失敗だけをやり直せる。

| コード | 意味 | 例 |
|---|---|---|
| 0 | 成功。書かなかった理由が正常なもの（`duplicate`・`dry_run`・失敗でなくなっていた）も 0 | |
| 1 | 再試行しても直らない失敗 | 401 / 403 / 404（パイプラインそのもの）、作業フォルダに前のステップの出力が無い、パターンのファイルが読めない・形が違う、投稿先が決まらない（`no_target`） |
| 2 | 使い方の誤り | 環境変数 `GITLAB_URL`・`GITLAB_TOKEN`・`GITLAB_PROJECT` が無い、引数の誤り |
| 3 | 一時的な失敗 | 接続の失敗・タイムアウト・408 / 425 / 429 / 5xx |

エラーは標準エラーに 1 行で出す。トークンは出さない。

## 5. 分類エンジン

### 5.1 分類

| ID | 画面・コメントでの名前 | 何を指すか | 見る人の次の一手 |
|---|---|---|---|
| `test_failure` | テスト失敗 | コードは組めて、テスト（単体・結合・E2E）の検証が落ちた | 落ちたテストを直す・テストを直す |
| `dependency` | 依存関係 | パッケージ・モジュール・イメージ・ロックファイルが取れない・合わない | 依存の指定・ロックファイル・レジストリを直す |
| `infrastructure` | インフラ・クォータ | ランナー・ディスク・メモリ・ネットワーク・レート制限・実行時間や枠の超過。変更の中身と関係なく落ちうる | 再実行する・ランナーや枠を見る |
| `configuration` | 設定ミス | `.gitlab-ci.yml`・CI の変数・認証情報・コマンドや権限の誤り | CI の設定・変数を直す |
| `unknown` | 不明 | どのパターンにも当たらなかった | ログを読む。繰り返すならパターンを足す（§5.7） |

ID は ASCII にし、Dagu の条件（`${DIAG.category}`）と印に使う。静的検査（lint・型検査）の失敗は、v1 の組み込みには入れない
（当たらなければ `unknown`。現場のパターンで `test_failure` に足せる。§12）。

### 5.2 パターンの置き場所と形

| 層 | 置き場所 | 直す人 |
|---|---|---|
| 組み込み | `gitlab_ci.py` の定数 `BUILTIN_PATTERNS`（下の JSON と同じ形の辞書）。入れ直すと新しくなる | このリポジトリ（フィクスチャと試験を一緒に足す） |
| 現場 | `ci-triage-patterns.json`（雛形は `ci-triage-patterns.json.example`。インストーラが `~/.config/change-watch/gitlab-ci-triage-agent/` に置き、既にあれば触らない）。場所は `.env` の `CI_TRIAGE_PATTERNS` | その PC の運用者 |

現場のファイルは組み込みに重ねる。**同じ `id` は定義ごと置き換わり**、新しい `id` は足される（gitlab-issue-hub-agent の種別のファイルと
同じ重ね方）。組み込みを止めたいときは、同じ `id` で `"enabled": false` を書く。実行のたびに読むので、直したら次の起動から効く。

```json
{
  "version": 1,
  "mask": ["INTERNAL-[0-9A-Z]{12}"],
  "patterns": [
    {
      "id": "pip-no-matching-distribution",
      "category": "dependency",
      "title": "pip が指定の版を見つけられない",
      "priority": 50,
      "match": {
        "regex": [
          "ERROR: Could not find a version that satisfies the requirement (?P<package>[^\\s(]+)",
          "ERROR: No matching distribution found for (?P<package>\\S+)"
        ]
      },
      "context": { "before": 3, "after": 2 },
      "next_action": "requirements の版の指定と、パッケージの取得元（index）に届くかを確かめてください。",
      "fix": "{package} の版の指定を、取得元にある版に直してください。"
    }
  ]
}
```

| 項目 | 必須 | 型・既定 | 意味 |
|---|---|---|---|
| `version` | ○ | `1` | 形の版。ほかの値は誤り |
| `mask` | | 文字列の配列・`[]` | 現場に固有の秘匿情報の正規表現（§6.3 の規則に足す） |
| `patterns[].id` | ○ | `[a-z][a-z0-9-]{0,63}` | 一意の名前。印とコメントの末尾に出る |
| `patterns[].category` | ○ | `test_failure`・`dependency`・`infrastructure`・`configuration` | `unknown` は書けない（当たらなかった結果のため） |
| `patterns[].title` | ○ | 1 行の文字列（80 字まで） | 何が起きたか。コメントのジョブの見出しに出る |
| `patterns[].priority` | | 0〜100 の整数・50 | 1 つのジョブに複数当たったときの順（§5.4） |
| `patterns[].match.regex` | △ | 文字列の配列 | ログの 1 行に対する正規表現（Python の `re.search`）。**どれか 1 つ**が当たればよい。名前付きグループは `fix`・`next_action` に埋められる |
| `patterns[].match.failure_reason` | △ | 文字列の配列 | ジョブの `failure_reason`（GitLab が付ける理由）が**どれか**に等しい |
| `patterns[].match.job_name` / `stage` | | 正規表現 | ジョブ名・ステージ名で絞る（`re.search`） |
| `patterns[].match.ignore_case` | | 真偽・`false` | `regex` の大文字小文字を区別しない |
| `patterns[].context.before` / `after` | | 0〜20 の整数・5 / 3 | 抜粋に入れる、当たった行の前後の行数 |
| `patterns[].next_action` | ○ | 文字列（300 字まで） | 次のアクション |
| `patterns[].fix` | | 文字列（600 字まで） | 修正案。**これがあるパターンを「既知のパターン」と呼ぶ**。無ければコメントに修正案の行を出さない |
| `patterns[].enabled` | | 真偽・`true` | 偽なら使わない |

- △: `match.regex` と `match.failure_reason` のどちらかは必須。`match` に書いた条件は**すべて**満たすこと（AND）。配列の中はどれか 1 つ（OR）
- 知らない項目・型の違い・重なった `id`・正規表現として読めない `regex`・`fix` や `next_action` の `{名前}` に対応する名前付きグループが
  どの `regex` にも無い、は誤り。`diagnose` は終了コード 1 で止まる（黙って組み込みだけで分類しない）
- `fix`・`next_action` の `{名前}` には、当たった行で取れた値を埋める。値はログ由来なので、改行とバッククォートを除き 80 字で切って
  `` ` `` で囲む。その `regex` に無いグループ・取れなかったグループは `（不明）` にする。`{` `}` そのものを書くときは `{{` `}}`

### 5.3 1 つのジョブの分類

1. ログを行に分ける（§6.2 の整え方のあと）。調べるのは末尾 5000 行（`SCAN_LINES`）。1 行は 2000 字までを見る（長い行での正規表現の暴走を抑える）
2. 有効なパターンを 1 つずつ当てる。`failure_reason`・`job_name`・`stage` の条件を見て、`regex` があれば**最初に当たった行**を
   そのパターンの「当たった行」にする（原因は最初のエラーにあり、あとの行は巻き添えが多い）。`regex` の無いパターンの当たった行は「なし」
3. 当たったパターンが無ければ `unknown`
4. 複数当たったら §5.4 の順で 1 つを選ぶ。選ばれなかったものは `diagnosis.json` の `also_matched` に `id` だけ残す（コメントには出さない）

ログが無いジョブ（`trace: null`）は `failure_reason` だけで当てる。`yaml_errors` のあるパイプラインは、ジョブの代わりに
パイプラインそのものを 1 件として、組み込みの `gitlab-ci-yaml-invalid`（`configuration`・priority 95）に当てる。抜粋は `yaml_errors` の文。

### 5.4 優先順位

1 つのジョブに複数のパターンが当たったときは、次の順で決める（上から比べ、同じなら次へ）。

1. `priority` の大きいもの
2. 分類の順: `configuration` → `infrastructure` → `dependency` → `test_failure`
3. 当たった行が先（上）のもの。当たった行が「なし」のものは最後
4. `id` の辞書順

組み込みの `priority` は次の帯で付ける。現場のパターンも同じ物差しで書く。

| 帯 | 何に付けるか | 例 |
|---|---|---|
| 90〜95 | GitLab が理由を言い切っているもの（`failure_reason`・`yaml_errors`） | 枠の超過・ランナーの障害・YAML の誤り |
| 70 | ログに出るインフラの形 | ディスクが無い・メモリ不足・名前解決の失敗・レート制限 |
| 60 | 設定の形 | コマンドが無い・変数が無い・認証の失敗 |
| 50 | 依存の形 | パッケージが無い・解決できない・ロックファイルのずれ |
| 40 | テストの形 | テストランナーの失敗の集計 |
| 20 以下 | 広く当たる保険 | `No such file or directory` |

帯の順は「上流の原因ほど強い」で付けてある。たとえば依存の取得がネットワークで落ちたログは依存の形（50）とインフラの形（70）の
両方に当たり、インフラになる。テストの途中でメモリ不足で殺されたログは、テストの形（40）よりインフラ（70）が勝つ。

### 5.5 複数のジョブにまたがるとき

- 分類はジョブごとに行い、コメントにはジョブごとの分類を出す
- **代表の分類**（見出しと `${DIAG.category}`）は、件数ではなく順で決める: `configuration` → `infrastructure` → `dependency` →
  `test_failure` → `unknown` のうち、1 件でも当たったいちばん前のもの。上流の原因（設定・基盤）が 1 つあれば、ほかのジョブの失敗は
  その巻き添えでありうるため。2 種類以上あれば `mixed: true` にし、見出しの下の 1 行に内訳（「依存関係 2・テスト失敗 1」）を出す
- **同じパターンに当たったジョブは 1 つにまとめる**。抜粋を出すのはジョブ ID のいちばん小さいもので、残りは「同じ原因のジョブ」に名前を並べる。
  `unknown` はまとめない（原因が同じとは限らない）
- まとまりの並びは、代表の分類の順 → `priority` の大きい順 → ジョブ ID の小さい順。コメントに抜粋つきで出すのは先頭 5 つ（`MAX_GROUPS`）、
  残りは「ほかに失敗したジョブ」に名前と分類だけを並べる
- ログを読まなかったジョブ（21 件目から）は「ほかに失敗したジョブ」に名前だけを出し、分類に数えない
- 失敗したジョブが 1 つも無く `yaml_errors` も無い（下流のパイプラインの失敗など）ときは、`unknown` で「失敗したジョブが見つかりません。
  パイプラインの画面で下流のパイプラインを確かめてください」と出す
- **`known`**（既知のパターンか）は、代表の分類のまとまりのどれかに `fix` があれば真
- **診断の指紋 `fp`**: まとまりにしたすべてのジョブ（ログを読まなかったものを除く）について `ジョブ名<TAB>分類<TAB>パターンの id`
  （`unknown` は `-`、`yaml_errors` は `(pipeline)<TAB>configuration<TAB>gitlab-ci-yaml-invalid`）を作り、並べ替えて改行でつないだものの
  SHA-256 の先頭 12 桁。ジョブ ID・行の中身・時刻は混ぜないので、同じ原因の再実行では変わらず、落ちるジョブか原因が変われば変わる

### 5.6 `diagnosis.json`

```json
{
  "version": 1,
  "pipeline": {"id": 123456, "sha": "1a2b3c4d…", "ref": "feature/x", "source": "push", "web_url": "…"},
  "category": "dependency", "mixed": true, "known": true, "fp": "1a2b3c4d5e6f",
  "counts": {"dependency": 2, "test_failure": 1},
  "groups": [
    {
      "category": "dependency", "pattern": "pip-no-matching-distribution", "title": "pip が指定の版を見つけられない",
      "job": {"id": 9001, "name": "build", "stage": "build", "web_url": "…", "failure_reason": "script_failure"},
      "same_cause": ["build-arm64"],
      "line": 412, "excerpt": "…", "excerpt_truncated": false,
      "next_action": "…", "fix": "…", "also_matched": ["pytest-failed"]
    }
  ],
  "others": [{"name": "deploy", "category": "unknown", "skipped_trace": true}]
}
```

`excerpt` は §6.2 の切り出しのあとの文（秘匿情報は `collect` で消してある）。`comment.md` はこの JSON だけから作る
（本文の組み立てを、分類と別に試験できる）。

### 5.7 代表的なログの形（組み込みのパターン）

実装では、下の表を `BUILTIN_PATTERNS` にし、1 行につきフィクスチャを 1 つ持つ（§11）。正規表現は要点だけを書いてある。

**テスト失敗（priority 40）**

| id | 当てる形 |
|---|---|
| `pytest-failed` | `^=+ FAILURES =+$`・`^FAILED \S+::\S+`・`^=+ .*\b\d+ failed\b.*=+$` |
| `python-unittest-failed` | `^FAILED \((failures\|errors)=\d+` |
| `jest-failed` | `^Tests:\s+\d+ failed`・`^\s*● .+ › .+` |
| `go-test-failed` | `^--- FAIL: (?P<test>\S+)`・`^FAIL\s+\S+\s+[\d.]+s$` |
| `junit-failed` | `Tests run: \d+, Failures: [1-9]`・`Tests run: \d+, Failures: \d+, Errors: [1-9]`・`There are test failures` |
| `rspec-failed` | `^\d+ examples?, [1-9]\d* failures?` |

**依存関係（priority 50）**

| id | 当てる形 | 修正案（`fix`） |
|---|---|---|
| `npm-package-not-found` | `npm (ERR!\|error) code (E404\|ETARGET)`・`npm (ERR!\|error) 404\s+'(?P<package>[^']+)'` | あり |
| `npm-resolve-conflict` | `npm (ERR!\|error) code ERESOLVE` | あり |
| `npm-lockfile-out-of-sync` | `` `npm ci` can only install packages when your package\.json and package-lock\.json ``・`Your lockfile needs to be updated` | あり（ロックファイルを作り直してコミットする） |
| `pip-no-matching-distribution` | 上の例 | あり |
| `pip-resolution-impossible` | `ResolutionImpossible`・`conflicting dependencies` | |
| `python-module-not-found` | `ModuleNotFoundError: No module named '(?P<module>[^']+)'` | あり（依存の一覧に足す） |
| `maven-artifact-not-found` | `Could not resolve dependencies for project`・`Could not find artifact (?P<artifact>\S+)` | |
| `gradle-unresolved` | `Could not resolve all (files\|dependencies) for configuration` | |
| `go-module-missing` | `missing go\.sum entry`・`go: .*: unknown revision` | あり（`go mod tidy`） |
| `bundler-gem-not-found` | `Could not find gem '(?P<gem>[^']+)'` | |
| `image-not-found` | `manifest for (?P<image>\S+) not found`・`pull access denied for (?P<image>\S+)` | |

**インフラ・クォータ**

| id | priority | 当てる形 |
|---|---|---|
| `gitlab-quota-exceeded` | 90 | `failure_reason`: `ci_quota_exceeded` |
| `gitlab-runner-failure` | 90 | `failure_reason`: `runner_system_failure`・`scheduler_failure`・`api_failure`・`data_integrity_failure`・`runner_unsupported`・`no_matching_runner` |
| `gitlab-job-timeout` | 90 | `failure_reason`: `stuck_or_timeout_failure`・`job_execution_timeout`・`trace_size_exceeded` |
| `job-timeout-log` | 70 | `ERROR: Job failed: execution took longer than` |
| `runner-system-failure-log` | 70 | `ERROR: Job failed \(system failure\)`・`ERROR: Preparation failed`・`Cannot connect to the Docker daemon` |
| `disk-full` | 70 | `No space left on device`・`ENOSPC` |
| `out-of-memory` | 70 | `OOMKilled`・`Out of memory`・`JavaScript heap out of memory`・`java\.lang\.OutOfMemoryError`・`ERROR: Job failed: exit code 137` |
| `network-unreachable` | 70 | `Could not resolve host`・`Temporary failure in name resolution`・`TLS handshake timeout`・`[Cc]onnection (timed out\|reset by peer)`・`\b(ETIMEDOUT\|ECONNRESET\|EAI_AGAIN)\b`・`50[234] (Bad Gateway\|Service Unavailable\|Gateway Time-?out)` |
| `rate-limited` | 70 | `toomanyrequests`・`429 Too Many Requests`・`pull rate limit` |
| `resource-quota-exceeded` | 70 | `exceeded quota`・`Quota exceeded` |

**設定ミス**

| id | priority | 当てる形 |
|---|---|---|
| `gitlab-ci-yaml-invalid` | 95 | パイプラインの `yaml_errors`（§5.3） |
| `gitlab-job-prerequisite` | 90 | `failure_reason`: `missing_dependency_failure`・`unmet_prerequisites`・`environment_creation_failure`・`deployment_rejected`・`protected_environment_failure`・`secrets_provider_not_found`・`invalid_bridge_trigger`・`insufficient_bridge_permissions`・`downstream_bridge_project_not_found` |
| `command-not-found` | 60 | `(?P<command>[\w./+-]+): (command )?not found$`・`ERROR: Job failed: exit code 127` |
| `variable-not-set` | 60 | `(?P<variable>[A-Za-z_][A-Za-z0-9_]*): unbound variable`・`(?P<variable>[A-Za-z_][A-Za-z0-9_]*): parameter (null or )?not set` |
| `auth-failed` | 60 | `\b401 Unauthorized\b`・`\b403 Forbidden\b`・`HTTP Basic: Access denied`・`Permission denied \(publickey\)`・`unauthorized: authentication required`・`denied: access forbidden` |
| `script-not-executable` | 60 | `(ba)?sh: .*: Permission denied$` |
| `git-ref-not-found` | 60 | `fatal: couldn't find remote ref`・`pathspec '.*' did not match` |
| `file-not-found` | 20 | `No such file or directory` |

`failure_reason` の値は GitLab の版で増減する。知らない値は当たらないだけで、誤りにはしない。`script_failure`・`unknown_failure` は
どのパターンにも書かない（ログの形で決める）。

### 5.8 現場でパターンを足す手順

README の「パターンを足す」に、次の形で書く。

1. `不明` になったパイプラインのログを手元に取る: `change-watch --plugin gitlab_ci ci collect <パイプライン>`
   （作業フォルダの `trace-<ジョブ ID>.log`。秘匿情報は消してある）
2. 原因を言い切れる 1 行（ツールが出すエラーの決まり文句）を選び、`ci-triage-patterns.json` の `patterns` に足す。
   ジョブやリポジトリに固有の値（パッケージ名・パス・時刻）は正規表現に書かず、要るなら名前付きグループにする
3. 形を確かめる: `change-watch --plugin gitlab_ci ci patterns --check`
4. 当たり方を確かめる: `change-watch --plugin gitlab_ci ci diagnose --trace-file <ログ>`（当たったパターン・抜粋・本文が出る）。
   前に正しく分類できていたログでも試し、横取りしていないかを見る（していれば `priority` を下げるか、`job_name`・`stage` で絞る）
5. 書かずに通しで見る: `change-watch --plugin gitlab_ci ci triage <パイプライン> --dry-run`
6. 何もしなくても次の起動から効く。どの現場でも起きる形なら、このリポジトリの `BUILTIN_PATTERNS` に、フィクスチャと一緒に足す

## 6. 診断コメント

### 6.1 雛形

````markdown
<!-- ci-triage-agent:diagnosis pipeline=123456 fp=1a2b3c4d5e6f -->
### CI の失敗の診断: 依存関係

パイプライン [#123456](https://gitlab.example.com/g/p/-/pipelines/123456)（`feature/x` @ `1a2b3c4d`）で 4 件のジョブが失敗しました（依存関係 2・テスト失敗 1・不明 1）。

#### [`build`](https://gitlab.example.com/g/p/-/jobs/9001)（build）— 依存関係: pip が指定の版を見つけられない

```text
Collecting requests==99.0
ERROR: Could not find a version that satisfies the requirement requests==99.0 (from versions: 2.31.0, 2.32.3)
ERROR: No matching distribution found for requests==99.0
```

- **次のアクション**: requirements の版の指定と、パッケージの取得元（index）に届くかを確かめてください。
- **修正案**: `requests==99.0` の版の指定を、取得元にある版に直してください。
- 同じ原因のジョブ: `build-arm64`

#### [`unit`](https://gitlab.example.com/g/p/-/jobs/9003)（test）— テスト失敗: pytest のテストが失敗

（抜粋・次のアクション）

ほかに失敗したジョブ: `deploy`（不明）

<sub>ログの決まった形だけで分類しています（`pip-no-matching-distribution`・`pytest-failed`）。秘匿情報らしい文字列は `***` に置き換えています。</sub>
````

| 部分 | 規則 |
|---|---|
| 1 行目 | 印（§7.3）。本文の先頭に置く |
| 見出し | `### CI の失敗の診断: <代表の分類>`。再実行後の 2 回目からは `### CI の失敗の診断（再実行後）: <代表の分類>` |
| 概要の 1 行 | パイプラインへのリンク・`ref`・コミットの先頭 8 桁・失敗したジョブの数。分類が 2 種類以上なら内訳。MR の今のコミットとパイプラインのコミットが違えば、次の行に「この MR には、このパイプラインより新しいコミットがあります。」 |
| ジョブのまとまり | `#### [ジョブ名](ジョブの URL)（ステージ）— <分類>: <パターンの title>`。`unknown` は「不明: どのパターンにも当たりませんでした」。`yaml_errors` は「`.gitlab-ci.yml`」を名前にする |
| 抜粋 | `text` のコードブロック。ログが無ければ「（ログはありません。GitLab が付けた理由: `<failure_reason>`）」 |
| 次のアクション | 必ず出す。`unknown` は「ジョブのログを確かめてください。同じ形が繰り返すなら、パターンを足すと次から分類できます。」 |
| 修正案 | 既知のパターン（`fix` がある）のときだけ出す |
| 末尾の 1 行 | 当たったパターンの `id` と、置き換えの断り |

- コードブロックの外に出すログ由来の文字（ジョブ名・ステージ名・`ref`・`{名前}` の値）は、改行とバッククォートを除いて `` ` `` で囲む。
  メンション（`@名前`）・issue への参照・クイックアクション（行頭の `/`）として効かせない。雛形の行はどれも `/` で始まらない
- コードブロックの囲みは、抜粋の中のいちばん長いバッククォートの並びより 1 つ長くする（3 つ以上）。ログの中の ```` ``` ```` で囲みを抜けさせない
- 本文の全体は 12000 字まで（`BODY_MAX_CHARS`）。超えるときは、後ろのまとまりから抜粋を外して名前と分類だけにする
- 人への呼びかけ（メンション）・ラベル・担当の変更はしない

### 6.2 ログの整え方と抜粋の切り出し

**整え方**（`collect` が、置く前に行う）:

1. UTF-8 で読む（読めないバイトは置き換える）
2. `\n` で行に分け、各行は最後の `\r` より後ろだけを残す（進み具合の表示の上書きを捨てる）
3. ANSI のエスケープ（`\x1b\[[0-9;?]*[ -/]*[@-~]`）と、GitLab の節の印（`section_(start|end):\d+:[\w.-]+`）を消す
4. 秘匿情報を消す（§6.3）
5. 末尾 1 MiB の先頭の欠けた行は捨てる

**失敗した場所**:

| 場合 | 中心にする行 |
|---|---|
| パターンが当たった | そのパターンの当たった行（最初に当たった行。§5.3） |
| 当たったのが `failure_reason` だけ／`unknown` | `ERROR: Job failed` の行より前で、`(?i)\b(error\|fatal\|failed\|exception\|traceback)\b` に当たる**最後の**行。無ければ末尾 |

**切り出し**:

- 中心の行の前 `context.before` 行・後ろ `context.after` 行（既定 5 / 3。`unknown` と `failure_reason` だけのときは前 15・後ろ 5。
  中心が末尾のときは末尾 20 行）
- 抜粋は 30 行・2000 字まで（`EXCERPT_MAX_LINES`・`EXCERPT_MAX_CHARS`）。超えたら中心の行を残して外側から削り、削った側に `…（省略）` の行を置く
- 1 行は 300 字まで（`EXCERPT_LINE_CHARS`）。超えた分は `…` にする
- 空行の続きは 1 行にまとめる。前後の空行は落とす

### 6.3 秘匿情報のマスク

`collect` がログを置く前に、行ごとに上から順に当て、当たった部分を `***` にする（change-watch のログと同じ表記）。分類は消したあとの
ログで行う。GitLab が自分で伏せた `[MASKED]` はそのまま残す。

| 順 | 消すもの | 規則（要点） | 置き換え |
|---|---|---|---|
| 1 | このプロセスが持つ秘密そのもの | 環境変数 `GITLAB_TOKEN` の値（8 字以上のとき）と、その URL パーセントエンコード形 | `***` |
| 2 | 秘密鍵のブロック | `-----BEGIN [A-Z ]*PRIVATE KEY-----` の行から `-----END [A-Z ]*PRIVATE KEY-----` の行まで | 1 行の `***` |
| 3 | 決まった接頭辞のトークン | GitLab `\bgl(pat\|dt\|rt\|cbt\|ptt\|ft\|imt\|agent\|oas\|soat)-[0-9A-Za-z_-]{20,}`、GitHub `\bgh[pousr]_[A-Za-z0-9]{36,}`・`\bgithub_pat_[A-Za-z0-9_]{22,}`、AWS `\b(AKIA\|ASIA)[0-9A-Z]{16}\b`、Slack `\bxox[abprs]-[A-Za-z0-9-]{10,}`、JWT `\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}` | `***` |
| 4 | 認証のヘッダー | `(?i)\b(authorization\|proxy-authorization\|private-token\|job-token\|deploy-token\|x-api-key\|x-auth-token)\b\s*[:=]\s*` のあと行末まで | 名前は残し、値を `***` |
| 5 | URL の中の認証情報 | `://[^/\s:@]+:[^/\s@]+@` | `://***:***@` |
| 6 | 名前が秘密らしい代入 | `password`・`passwd`・`pwd`・`secret`・`token`・`api_key`・`access_key`・`private_key`・`credential` などを含む名前への代入。キーと値の一重・二重引用符に対応 | 名前は残し、値を `***` |
| 7 | トークンらしい長い文字列 | `[A-Za-z0-9+_-]{40,}={0,2}` のうち、大文字・小文字・数字を**すべて**含むもの | `***` |
| 8 | 現場の規則 | パターンのファイルの `mask` の正規表現 | `***` |

- 7 は 16 進だけの文字列（コミットの SHA・イメージのダイジェスト）を消さない（小文字と数字だけなので当たらない）。`/` を含めないので、
  長いパスは区切りで切れて当たりにくい。長い識別子を消しすぎることはあるが、漏らすより消すほうに倒す
- 消したことで分類が変わりうるのは、秘密の値そのものに当てるパターンだけ（そういうパターンは書かない）
- 規則で拾えない秘密（短いパスワードをそのまま `echo` したなど）は残りうる。README に「CI の変数は GitLab のマスクを有効にしておく」と書く

## 7. 投稿先

### 7.1 優先順位

上から順に見て、最初に決まったところへ書く。`post` が決める（`diagnosis.json` の `pipeline` と API）。

| 順 | 投稿先 | 決まる条件 | 決めるための読み | 書き込み |
|---|---|---|---|---|
| 1 | MR | (a) `ref` が `refs/merge-requests/<iid>/(head\|merge\|train)`（MR のパイプライン）→ その MR（状態は問わない）。(b) タグでないブランチのパイプラインで、`GET /projects/:id/merge_requests?state=opened&source_branch=<ref>` に、このプロジェクトから出た（`source_project_id` が同じ）MR がある → その MR（複数なら `iid` のいちばん大きいもの） | (a) `GET /projects/:id/merge_requests/:iid`、(b) 左の一覧 | `POST /projects/:id/merge_requests/:iid/notes`（`{"body": …}`） |
| 2 | コミット | 1 で決まらず、パイプラインの `source` が `CI_TRIAGE_COMMIT_SOURCES`（既定 `push`）に含まれ、コミットが読める | `GET /projects/:id/repository/commits/:sha`（404 なら 3 へ） | `POST /projects/:id/repository/commits/:sha/comments`（`{"note": …}`） |
| 3 | ハブの issue | 1 でも 2 でも決まらない（定期実行・API・トリガー・画面からの実行のパイプライン、消えたコミット） | なし（`GITLAB_HUB_ISSUE_URL` から API の URL を作る） | `POST <issue の API>/notes`（`{"body": …}`） |

- マージ済みの MR は 1(b) に当たらない（開いている MR だけ）。`main` へのマージ後のパイプラインは 2（マージコミットへのコメント）になる
- 2 を `source` で絞るのは、定期実行の失敗を「たまたま先頭だったコミット」に書いても、見るべき人に届かないため。
  `push` はそのコミットを積んだ人の操作なので、コミットに書く。どの `source` をコミットに書くかは現場で変えられる（空にすれば 2 を使わない）
- 3 でハブの issue の URL（`GITLAB_HUB_ISSUE_URL`）が空なら、書かずに `no_target`（終了コード 1）。Dagu の履歴に失敗として残る
- ハブの issue の URL から API の URL を作る規則は gitlab-issue-hub-agent の `gitlab_issue_api` と同じ
  （`https://host/group/project/-/issues/42` → `https://host/api/v4/projects/group%2Fproject/issues/42`。`/api/v4/` を含む URL はそのまま）。
  同じ関数を `gitlab_ci.py` の中に持つ。ハブは別のプロジェクトでもよいが、**ホストが `GITLAB_URL` と違えば断る**（トークンを別のホストへ送らない）
- ハブへのコメントは、概要の 1 行にプロジェクト（`GITLAB_PROJECT`）も出す（複数のプロジェクトが同じハブに書いても見分けられる）
- 書き込みは 1 回の POST。自分では再試行しない。応答が 408 / 425 / 429 / 5xx・届かないなら終了コード 3（Dagu がやり直し、やり直しでは印を先に確かめる）

### 7.2 投稿先ごとの API のまとめ

| 投稿先 | 既存のコメントを読む | 本文の項目 | 書いたあとの URL |
|---|---|---|---|
| MR | `GET /projects/:id/merge_requests/:iid/notes?sort=desc&order_by=created_at` | `body` | `<MR の web_url>#note_<id>` |
| コミット | `GET /projects/:id/repository/commits/:sha/comments` | `note` | `<プロジェクトの URL>/-/commit/<sha>` |
| ハブの issue | `GET <issue の API>/notes?sort=desc&order_by=created_at` | `body` | `<issue の URL>#note_<id>` |

### 7.3 見えない印

本文の 1 行目に、HTML のコメントで印を付ける（GitLab の画面には出ない）。

```
<!-- ci-triage-agent:diagnosis pipeline=<パイプライン ID> fp=<診断の指紋 12 桁> -->
```

- `post` は書く前に、投稿先の既存コメントを新しい順に最大 300 件（`DEDUP_SCAN_NOTES`）調べる。MR とハブの issue は API の降順を使い、
  コミットは全ページを取得して `created_at` の降順に並べ替えた後で 300 件に絞る。どの投稿先も 1 リクエストにつき最大 100 件を取得する。
  **本文の先頭**が `^<!--\s*ci-triage-agent:diagnosis\s+pipeline=(\d+)\s+fp=([0-9a-f]{12})\s*-->` に当たるものを印として集める
- 同じ `pipeline` で同じ `fp` の印があれば書かない（`duplicate`）。同じ `pipeline` で違う `fp` の印だけがあれば、「（再実行後）」の見出しで書く
- 本文の先頭だけを見るので、ログの抜粋や人の引用に印の文字が混ざっても、印として数えない
- 300 件より古いところに印があると見落とすが、ふだんの二重投稿は change-watch の `commit` が止めている（§3.2）。印が受け持つのは
  「書いた直後のやり直し」で、そのときの印はいちばん新しいところにある
- 書き手は確かめない（トークンを替えても、前のトークンで書いた診断を見分けられる）
- 投稿先はパイプラインの中身から決まるので、やり直しでも同じ場所を読む。開いていた MR が閉じられたあとのやり直しは投稿先が変わりうるが、
  その場合の重複は 1 件に限られるので許す

## 8. dry-run

| 項目 | 仕様 |
|---|---|
| 入り口 | `post --dry-run`・`triage --dry-run`、または環境変数 `CI_TRIAGE_DRY_RUN` が `1` / `true` / `yes` / `on`（大文字小文字を問わない）。どちらかが真なら dry-run。ほかの値と空は偽 |
| すること | `collect`・`diagnose` はふだんどおり（読むだけ・通信しないので、dry-run でも同じ）。`post` は投稿先を決め、既存の印も読む（**GET だけ**）。POST はしない |
| 標準出力 | 下の形。投稿先が決まれば本番と同じ `render_comment` で本文を組み立てる。印が別の指紋だけなら「（再実行後）」を見出しに付け、ハブの issue ではプロジェクト名を入れる。MR に新しいコミットがあればその旨も入れる。投稿先が決まらない場合は `comment.md` を表示する |
| 終了コード | 書けたはずなら 0。投稿先が決まらなければ 1（ふだんと同じ。dry-run で設定の不足に気づける） |
| ワークフローでの `commit` | する。しないと同じパイプラインを毎分受け取り続ける。**dry-run のあいだに受け取った失敗は、あとで dry-run を外しても書かれない** |
| 既定 | `.env` の例は `CI_TRIAGE_DRY_RUN=1`。入れた直後は書かず、Dagu のログで本文を確かめてから `0` にしてもらう |

```
[dry-run] 投稿先: MR !12（https://gitlab.example.com/g/p/-/merge_requests/12）
[dry-run] 既存の診断: なし（dry-run でなければ投稿します）
[dry-run] 本文（1834 字）:
<!-- ci-triage-agent:diagnosis pipeline=123456 fp=1a2b3c4d5e6f -->
### CI の失敗の診断: 依存関係
…
```

2 行目は、同じ印があれば `既存の診断: あり（dry-run でなければ投稿しません）`、違う `fp` の印だけなら
`既存の診断: 別の内容であり（dry-run でなければ再実行後として投稿します）`。投稿先が決まらなければ 1 行目を
`[dry-run] 投稿先: なし（<理由>）` にして本文を出し、終了コード 1。dry-run の出力は人が読むもので、ワークフローは変数に受けない。

## 9. 設定

フォルダの中身（インストーラの決まりに合わせた名前）:

| ファイル | 置かれる場所 | 中身 |
|---|---|---|
| `gitlab_ci.py` | `~/.local/share/change-watch/agents/gitlab-ci-triage-agent/` | プラグイン（コマンド `ci`） |
| `change-watch.yaml.example` | `~/.config/change-watch/gitlab-ci-triage-agent/change-watch.yaml` | 取得元 `gitlab-pipelines` |
| `gitlab-ci-triage-agent.env.example` | `~/.config/change-watch/gitlab-ci-triage-agent/gitlab-ci-triage-agent.env` | 下の表 |
| `ci-triage-patterns.json.example` | `~/.config/change-watch/gitlab-ci-triage-agent/ci-triage-patterns.json` | 現場のパターン（`{"version": 1, "mask": [], "patterns": [<"enabled": false の見本 1 件>]}`） |
| `change-watch-ci-triage.yaml.example` | `~/.config/dagu/dags/change-watch-ci-triage.yaml` | 診断のワークフロー（§10） |
| `change-watch-gitlab-ci-triage-agent-maintenance.yaml.example` | `~/.config/dagu/dags/` | 整理（gc）。ほかのエージェントと同じ形で、時刻だけずらす（`"27 3 * * *"`） |
| `README.md`・`test/` | 置かれない | 使い方・試験 |

### 9.1 `change-watch.yaml.example`

```yaml
version: 1
state_dir: ~/.local/state/change-watch/gitlab-ci-triage-agent   # エージェントごとに分ける
retention:
  record_days: 30            # 30 日取得されていない失敗は、手元の姿から消す

sources:
  gitlab-pipelines:
    connection: gitlab
    request:
      url: "{{ conn.url }}/api/v4/projects/{{ env.GITLAB_PROJECT | urlencode }}/pipelines"
      headers:
        PRIVATE-TOKEN: "{{ conn.token }}"
      query: { status: failed, order_by: updated_at, sort: asc }   # 絞るなら ref: main・source: push などを足す
    key: id                    # get --consumer が変化を見分ける key（パイプラインの ID）
    pagination: { type: page, size_param: per_page, size: 100 }
    incremental:
      type: max_value
      field: updated_at
      param: updated_after
      initial: "2026-01-01T00:00:00Z"   # 入れる日に直す（これより前の失敗は取らない）
    schedule: "*/5 * * * *"
    jitter_sec: 60
    busy_wait_sec: 300
    failure_backoff_sec: 300
    rate_limit: { min_interval_sec: 1, group: gitlab }
```

先頭のコメントは、ほかのエージェントの例と同じ調子で書く（置かれる場所・条件は Dagu に書くこと・change-watch が受け持つこと）。
接続設定（`connections.yaml`）は、ほかの GitLab のエージェントと同じ `gitlab` の `default`（`url: ${GITLAB_URL}`・`token: ${GITLAB_TOKEN}`）。
トークンは `.env` にだけ書く。

### 9.2 `gitlab-ci-triage-agent.env.example`

| 変数 | 例の値 | 必須 | 意味 |
|---|---|---|---|
| `GITLAB_URL` | `https://gitlab.example.com` | ○ | GitLab の URL |
| `GITLAB_PROJECT` | `group/project` | ○ | 見るプロジェクト（パスか ID）。1 つの設定で 1 プロジェクト |
| `GITLAB_TOKEN` | `glpat-xxxxxxxxxxxxxxxx` | ○ | `api` スコープのトークン。**このトークンの持ち主が診断の書き手**になる。ジョブのログを読めて、MR・コミット・ハブの issue にコメントできる権限が要る（§12） |
| `GITLAB_HUB_ISSUE_URL` | `https://gitlab.example.com/group/project/-/issues/42` | | MR にもコミットにも書けない失敗の投稿先（ブラウザで開いたときの URL）。gitlab-issue-hub-agent と同じ名前・同じ形。空なら、そういう失敗は書かずに失敗として残す |
| `CI_TRIAGE_DRY_RUN` | `1` | | 真なら書かずに標準出力へ出す。確かめたら `0` にする |
| `CI_TRIAGE_PATTERNS` | `/home/you/.config/change-watch/gitlab-ci-triage-agent/ci-triage-patterns.json` | | 現場のパターン。空なら組み込みだけ（インストーラがこの PC のパスに直す） |
| `CI_TRIAGE_COMMIT_SOURCES` | （コメントアウト。既定 `push`） | | コミットに書くパイプラインの `source`（カンマ区切り）。空にするとコミットには書かない |
| `CI_TRIAGE_WORK_DIR` | （コメントアウト） | | 作業フォルダ。既定は状態の置き場所の `work` |

上限（ログを読むジョブの数・末尾の大きさ・抜粋の行数など）は環境変数にしない。`gitlab_ci.py` の先頭の定数に、理由のコメントと一緒に置く
（`MAX_JOBS = 20`・`TRACE_TAIL_BYTES = 1 << 20`・`SCAN_LINES = 5000`・`SCAN_LINE_CHARS = 2000`・`EXCERPT_MAX_LINES = 30`・
`EXCERPT_MAX_CHARS = 2000`・`EXCERPT_LINE_CHARS = 300`・`MAX_GROUPS = 5`・`BODY_MAX_CHARS = 12000`・`DEDUP_SCAN_NOTES = 300`・
`WORK_KEEP_DAYS = 7`・`HTTP_TIMEOUT = 60`・`TRACE_TIMEOUT = 120`）。

## 10. Dagu のワークフロー（`change-watch-ci-triage.yaml.example`）

`schedule: "* * * * *"`・`type: graph`・`hist_retention_days: 14`・`dotenv` に `.env`。どのステップも `action: exec` で、`command` は
`/home/you/.local/bin/change-watch`（インストーラがこの PC のものに直す）。プラグインを使うので、**どのステップにも `--plugin gitlab_ci` を足す**
（change-watch の README の決まり）。

| ステップ | 呼ぶもの | `preconditions` | `continue_on` | `retry_policy` / `timeout_sec` | `output` |
|---|---|---|---|---|---|
| `get` | `--config … --connections … get --source gitlab-pipelines --consumer ci-triage --watch updated_at --one` | — | — | `limit: 3`・`interval_sec: 60`・`exit_code: [3]` / 480（`jitter_sec` + `busy_wait_sec` + 余裕） | `CHANGE` |
| `collect` | `ci collect ${CHANGE.key}` | `${CHANGE.kind}` が `re:^(appeared\|changed)$`、`${CHANGE.record.status}` が `failed` | `skipped`・`failure` | `limit: 3`・`interval_sec: 60`・`exit_code: [3]` / 600 | `COLLECT` |
| `diagnose` | `ci diagnose ${CHANGE.key}` | `${COLLECT.pipeline}` が `re:.`（`collect` が動いた）、`${COLLECT.status}` が空（読みに行ったとき、まだ失敗だった） | `skipped`・`failure` | なし / 120 | `DIAG` |
| `post` | `ci post ${CHANGE.key}` | `${DIAG.category}` が `re:.`（診断ができた） | `skipped`・`failure` | `limit: 3`・`interval_sec: 60`・`exit_code: [3]` / 180 | — |
| `commit` | `--config … --connections … commit --source gitlab-pipelines --consumer ci-triage ${CHANGE.key}` | `${CHANGE.key}` が `re:.` | — | — / 600 | — |

`depends` は一本道（`get` → `collect` → `diagnose` → `post` → `commit`）。

`preconditions` の方針:

- **どのパイプラインを診断するか・どの診断を書くかは、ここ（Dagu）に書く**。`gitlab_ci.py` は条件を持たない
- 例に入れるのは上の表の条件だけ。絞り込みの例は、コメントアウトした行として添える:
  - ブランチで絞る: `collect` に `condition: "${CHANGE.record.ref}"`・`expected: "re:^(main|release/.*)$"`
  - 不明は書かない: `post` に `condition: "${DIAG.category}"`・`expected: "re:^(test_failure|dependency|infrastructure|configuration)$"`
  - 既知のパターンだけ書く: `post` に `condition: "${DIAG.known}"`・`expected: "true"`
- 条件に合わなくても、途中で失敗しても `commit` まで進める（同じ失敗を毎分やり直し続けない）
- 分類ごとに別のことをしたいとき（インフラなら別の人に知らせる、など）は、`post` の後ろに `${DIAG.category}` を条件にしたステップを足す。
  このエージェントの例には入れない

先頭のコメントには、ほかの例と同じく、置かれる場所・ステップの流れ・「入れた直後は dry-run」であること・Windows での `command` を書く。

## 11. テスト

`tools/change-watch/agents/gitlab-ci-triage-agent/test/test_gitlab_ci.py`（標準ライブラリと PyYAML。
`cd tools/change-watch/agents/gitlab-ci-triage-agent && python3 -m unittest discover -s test`）。gitlab-mr-review-agent の試験と同じ流儀にする。

### 11.1 流儀

| もの | 合わせ方 |
|---|---|
| プラグインの読み込み | `importlib.util.spec_from_file_location("gitlab_ci", ROOT / "gitlab_ci.py")` |
| GitLab | 試験ファイルの中の `FakeGitLab`（`ThreadingHTTPServer`。`PRIVATE-TOKEN` を確かめ、`writes`・`requests` に記録する）。持つもの: パイプラインの一覧（`status`・`updated_after` で絞る）と 1 件、パイプラインのジョブ（`scope[]`）、ジョブのログ（`text/plain`。404 にもできる）、MR の一覧（`state`・`source_branch`）と 1 件、MR・コミット・issue のコメントの読み書き。`fail_next = <状態コード>` で次の 1 回を失敗させる（終了コード 3 と再試行の確かめ） |
| 状態を変える口 | `put_pipeline(id, **項目)`・`put_job(pipeline, id, name, trace=…, failure_reason=…)`・`retry(pipeline, …)`（`updated_at` を進め、ジョブを入れ替える） |
| コマンドの呼び方 | `_GitLabBase.cli(*args)`: 環境変数を `mock.patch.dict` で渡し、`gitlab_ci.main([...])` を呼んで（終了コード, 標準出力）を返す。1 つだけ、本物の change-watch 越し（`change_watch.py --plugin <パス> ci …`）でも通す |
| ワークフロー | `tools/change-watch/test/dagrun.py` で、**例のファイルそのまま**（`change-watch.yaml.example`・`change-watch-ci-triage.yaml.example`・整理）を本物の change-watch に流す。設定は例を読み、`state_dir` を一時フォルダにし、`schedule`・`jitter_sec`・`busy_wait_sec` を外して使う（MR の `FlowTest` と同じ）。`dagrun.check_shape` も通す |
| 作業フォルダ | 試験ごとの一時フォルダ（`CI_TRIAGE_WORK_DIR`） |

### 11.2 フィクスチャ

```
test/fixtures/
  traces/
    test_failure/pytest-failed.log        ← 組み込みのパターン 1 つにつき 1 つ。名前はパターンの id
    dependency/npm-package-not-found.log
    infrastructure/disk-full.log
    configuration/command-not-found.log
    unknown/plain-exit-1.log              ← どれにも当たらないもの
    mixed/oom-during-pytest.log           ← 2 つ以上に当たり、優先順位で決まるもの
  expected.json                           ← ファイルごとの期待: {"traces/dependency/npm-package-not-found.log":
                                             {"category": "dependency", "pattern": "npm-package-not-found",
                                              "excerpt_contains": ["npm ERR! code E404"], "fix": true}, …}
  secrets.log / secrets.expected.log      ← マスクの前と後（§6.3 の規則ごとに 1 行以上。消してはいけない行も入れる）
  comments/single.md・multi.md・yaml-errors.md・rerun.md   ← 本文の期待（diagnosis.json から作った結果と丸ごと比べる）
```

- ログは実物の形を写した 20〜80 行の抜粋（ANSI のエスケープ・節の印・`\r` を残したもの）。本物の秘密・社内のホスト名は入れない
  （トークンは形だけ合う作り物）
- `failure_reason` だけで当たるパターンは、ログを持たず `expected.json` に `{"failure_reason": "ci_quota_exceeded", …}` で書く
- **組み込みのパターンの `id` と、フィクスチャの名前が 1 対 1 であること**を試験で確かめる（パターンを足してフィクスチャを足し忘れると落ちる）

### 11.3 観点

| まとまり | 観点 |
|---|---|
| 分類（`ClassifyTest`） | フィクスチャごとに期待の分類・パターン・抜粋になる／同じ入力を 2 回流して `diagnosis.json` と `comment.md` がバイト単位で同じ（決定的）／優先順位の 4 段（`priority` → 分類の順 → 行 → `id`）それぞれ／`match` の AND と配列の OR／`job_name`・`stage`・`ignore_case`／調べる行数と 1 行の長さの上限／ログの無いジョブは `failure_reason` だけで当たる／`yaml_errors` |
| 集約（`AggregateTest`） | 代表の分類が順で決まる／`mixed` と内訳／同じパターンのジョブがまとまる・`unknown` はまとまらない／6 つ目のまとまりと 21 件目のジョブが「ほかに失敗したジョブ」になる／`allow_failure` のジョブを除く／ジョブが 0 件のとき／`fp` がジョブ ID・行の中身で変わらず、落ちるジョブか原因で変わる |
| パターンのファイル（`PatternsFileTest`） | 新しい `id` が足される・同じ `id` が置き換わる・`enabled: false` で止まる／誤り（知らない項目・型・重なった `id`・読めない正規表現・`{名前}` の不足・`unknown` の指定・`version` 違い）が終了コード 1 で、どこが誤りか 1 行で出る／`patterns --check`／例のファイル（`ci-triage-patterns.json.example`）が形に合う／`{名前}` の値の無害化 |
| 整え方・抜粋・マスク（`TraceTest`） | ANSI・節の印・`\r` が消える／末尾 1 MiB だけ残り、欠けた先頭の行を捨てる／抜粋の行数・字数・1 行の上限と `…（省略）`／中心の行の決め方（当たった行・最後のエラー行・末尾）／`secrets.log` が `secrets.expected.log` になる／SHA とダイジェストは消えない／`GITLAB_TOKEN` の値そのものが消える／**作業フォルダのどのファイルにも、仕込んだ秘密が残っていない** |
| 本文（`RenderTest`） | `comments/*.md` と一致／印が 1 行目／バッククォートを含む抜粋で囲みが伸びる／ジョブ名・`ref` のバッククォートと改行が除かれる／12000 字を超えると後ろの抜粋が外れる／修正案は `fix` があるときだけ |
| コマンド（`CommandTest`） | `collect` が読む API と回数（`requests`）・ログの 404・失敗でなくなっていたパイプライン／`post` の投稿先: MR のパイプライン（`head`・`merge`・`train`）・ブランチのパイプラインと開いている MR・マージ済みの MR しか無い → コミット・`source` が `schedule` → ハブ・コミットが 404 → ハブ・ハブの URL が空 → `no_target`（1）・ハブが別ホスト → 断る／同じ印で `duplicate`・違う `fp` で「（再実行後）」・本文の途中の印は数えない／一時的な失敗が 3、401・404 が 1、環境変数の不足が 2／`triage` が 3 つを続けて行う／本物の change-watch 越しでも動く |
| dry-run（`DryRunTest`） | `--dry-run` と `CI_TRIAGE_DRY_RUN`（`1`・`true`・`0`・空）／`writes` が空のまま／標準出力の 3 つの形（なし・あり・別の内容であり）と本文／投稿先が決まらないと 1 |
| 流れ（`FlowTest`） | 例のワークフローで: 入れた時点の失敗は書かない（`baseline`）／新しい失敗で `get` → `collect` → `diagnose` → `post` → `commit` が 0 で、MR にコメントが 1 件／次の起動では何も受け取らない／再実行で同じ原因 → 流れるが書かない／再実行で別の原因 → 「（再実行後）」が 1 件／`post` のあと `commit` を飛ばしてやり直しても 1 件のまま（印）／`collect` が失敗しても `commit` する／例の `.env` のまま（dry-run）では書かない／整理のワークフローが動く |

change-watch の本体の試験（`tools/change-watch/test/`）に、エージェントの一覧を数えている箇所があれば、このエージェントを足す
（`test_install.py`）。

## 12. 未確定事項・仮置き

この設計は、**実物の GitLab が無い環境で、GitLab の API の文書だけを頼りに書いている**。試験は偽の GitLab で通すので、
下の前提は実物につないだときに最初に確かめる。違っていたら、偽の GitLab とフィクスチャを先に実物の形に直してから実装を直す。

| # | 仮に置いたこと | 違っていたときの影響・直すところ |
|---|---|---|
| 1 | パイプラインの一覧が `status=failed`・`updated_after`・`order_by=updated_at` を受け付け、レコードに `source` と `updated_at` がある | 受け付けなければ増分取得をやめ、`ref` などで絞った全件の取得にする（§3.1）。`source` が一覧に無くても、`post` は `collect` の 1 件の応答を使うので動く |
| 2 | 再実行でまた失敗すると、パイプラインの `updated_at` が進む | 進まなければ再実行後の失敗を拾えない。`--watch` に足せる項目が無ければ、「再実行後は診断しない」を README に書く |
| 3 | ジョブの応答に `failure_reason` と `allow_failure` があり、§5.7 の値が返る | `failure_reason` が無ければ、それだけで当てるパターン（priority 90）が当たらず、ログの形（70 以下）で当たる。分類は弱くなるが壊れない |
| 4 | MR のパイプラインの `ref` が `refs/merge-requests/<iid>/head`（`merge`・`train`）の形 | 違えば 1(a) が当たらず、1(b)（ブランチ名で探す）か 2 になる。1(a) の正規表現を直す |
| 5 | ログの取得が `text/plain` で全体を返し、末尾だけを取る口（Range）は当てにできない | 大きなログ（数十 MiB）では読むのに時間がかかる。Range が使えると分かったら `collect` を末尾だけの取得に替える |
| 6 | トークンの権限: ジョブのログを読み、MR・コミット・issue にコメントするのに Reporter 以上（ログの公開を絞っているプロジェクトでは Developer 以上）で足りる | 足りなければ `collect` か `post` が 403（終了コード 1）。README の権限の説明を直す |
| 7 | コミットのコメントの一覧・投稿が `/repository/commits/:sha/comments`（項目 `note`）で、本文の HTML コメントが画面に出ない | 画面に印が見えるなら、コミットだけ印を本文の末尾の目立たない形に変える |
| 8 | `source` が `push` のパイプラインだけをコミットに書き、ほかはハブへ（§7.1 の 2） | 依頼の「MR → コミット → ハブ」を、定期実行の失敗をハブに集める形に読んだもの。「コミットがある限りコミットへ」が意図なら、`CI_TRIAGE_COMMIT_SOURCES` の既定をすべての `source` にする（コードは変わらない） |
| 9 | Dagu の変数で `${COLLECT.status}` のように、JSON に無い項目は空になる | 空にならなければ、`collect` がいつも `"status": ""` を出すようにする（`dagrun.py` は空にする） |

v1 でやらないと決めたこと（要るなら次の版で）:

- **下流のパイプライン（bridge）・子パイプラインの失敗を追う**。親には「失敗したジョブが見つかりません」と書くだけ。子のパイプラインが
  同じプロジェクトにあれば、子は子として一覧に出て診断される
- **直ったことの追記・古い診断の書き換え**。再実行で成功しても、新しいコミットで直っても、前の診断はそのまま残る
- **同じ MR で同じ診断が続いたときに畳む**。コミットを積むたびに同じ原因で落ちれば、そのたびに 1 件書く（パイプラインが別なので）
- **静的検査（lint・型検査）の組み込みのパターン**。道具ごとに形が違いすぎるので、現場のパターンに任せる
- **複数のプロジェクトを 1 つの設定で見る**。1 つの設定で 1 プロジェクト。増やすなら、取得元とワークフローをプロジェクトごとに置く
  （`ci` のコマンドは `--project` を受けるので、プラグインは 1 つでよい）
- **フォークからの MR のパイプライン**（フォーク側のプロジェクトで走るもの）
- **LLM による分類・要約**。入れるとしても `unknown` のときの補助に限り、決定的な分類の結果を上書きさせない（別の設計にする）

実装のときに併せて直すもの（本書の範囲の外。実装の変更に含める）: `tools/change-watch/README.md` のエージェントの一覧と
「エージェントの例」の箇条書き、`docs/designs/README.md` の索引（件数と「実装・運用設計」の表）、CHANGELOG。

## 13. 採らなかった案

- **LLM に分類させる**: 同じログで結果が変わりうる。試験をフィクスチャとの完全一致で書けず、ログを外へ送ることにもなる。v1 は
  決まった形だけを当て、当たらなければ正直に「不明」と書く
- **すべてのパイプラインを取り、失敗かどうかを Dagu の条件で見る**（MR のエージェントは開いている MR を全部取り、ドラフトを条件で外す）:
  パイプラインは作成 → 実行中 → 終了と何度も変わり、そのたびに 1 回の起動（1 分）を使って `commit` するだけになる。活発なプロジェクトでは
  失敗に届くまでの順番待ちが延びる。失敗だけを取り、「どの失敗を診断するか」（ブランチなど）を Dagu の条件に残した
- **ジョブを取得元にする（1 件の変化 = 失敗したジョブ 1 つ）**: 1 つのパイプラインで 10 個落ちると 10 件のコメントになる。
  診断はパイプラインで 1 つにまとめる
- **パイプラインの再実行のたびに必ず書く／再実行後は一切書かない**: 前者は同じ原因での再実行で同じ文が並び、後者は再実行で
  別の原因が出たときに黙る。診断の指紋で分けた
- **前の診断を書き換える（1 パイプライン 1 コメントを保つ）**: コミットのコメントには書き換える API が無く、投稿先で動きが割れる。
  書き換えは通知も出ない。別のコメントを足すことにした
- **change-watch の `call` だけで読み書きし、プラグインは本文を作るだけにする**（gitlab-issue-hub-agent の形）: ログは JSON でなく大きい。
  Dagu の変数に載せられず、投稿先を決めるには条件つきで何度か読む必要がある。gitlab-mr-review-agent と同じく、プラグインが環境変数の
  トークンで直接読み書きする形にした（change-watch の要求の間隔は分け合わないが、1 回の起動で 1 件なので集中しない）
- **1 つのコマンド（`triage`）だけをワークフローから呼ぶ**: 再試行してよいところ（読む）と、してはいけないところ（決定的な分類の失敗）が
  1 つのステップに混ざり、Dagu の履歴でどこで落ちたかが見えない。分類の結果を条件に使うこともできない。`triage` は手で確かめる口に残した
- **組み込みのパターンも JSON のファイルで配る**: インストーラはエージェントの `*.py` だけをプログラムとして置く。`*.json` も置くように
  変えると、ほかのエージェントにも効く決まりの変更になる。組み込みはプログラムの中に持ち（入れ直せば新しくなる）、現場の分だけを
  設定のフォルダの JSON にした。いま効いている全部は `ci patterns` で JSON として見られる
- **分類を信頼度（点数）で出す**: 点数の付け方に根拠が要り、人は結局ログを見る。当たったパターンの `id` と抜粋を見せれば、
  外れているかは読めば分かる
- **消す前のログを作業フォルダに残す（調べ直せるように）**: 秘密がディスクに残る。元のログは GitLab にあるので、要るときに取り直す
- **失敗したら MR にラベルを付ける・担当を変える・再実行する**: 頼まれていない。インフラの失敗の自動再実行は、枠を使い切る方向に
  働きうる。分類ごとの次の動きは、`${DIAG.category}` を条件にしたステップとして現場が足せる
