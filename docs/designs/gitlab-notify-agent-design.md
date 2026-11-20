# gitlab-notify-agent 設計

GitLab の issue の変化を **change-watch で 1 回だけ検出し**、通知先（tmux の表示・エージェント CLI へのプロンプト投入・ログ・ハブの issue）
だけを用途で分ける仕組み。いまは issue-mailbox（自前のポーリングと状態ファイル）とハブ系（change-watch と Dagu）の 2 系統が別々に
GitLab を見ている。検出を change-watch に寄せて監視の保守箇所を 1 つにし、「届かなかった」ときに検出・整形・配信のどこで
止まったかをすぐ切り分けられるようにする。

実装は `tools/change-watch/agents/gitlab-notify-agent/`（プログラムは `gitlab_notify.py`。change-watch のプラグインで、
`change-watch --plugin gitlab_notify <コマンド>` で呼ぶ）にある。issue-mailbox は引き続き利用できる。

## 1. 位置づけと分担

| 受け持ち | どこ | 何を |
|---|---|---|
| GitLab への問い合わせ（ページング・予定・失敗の待ち）と、前回 commit してからの変化 | change-watch | 取得元 `gitlab-issues`（gitlab-issue-agent と同じ定義）、受け手 `notify`（`get --watch … --one`・`commit`） |
| 起動・ステップの順序・一時的な失敗の再試行・実行履歴 | Dagu | ワークフロー 1 本（`get` → `retry` → `notify` → `commit`）。リポジトリごとに 1 本 |
| 変化 → 文言の整形、チャネルへの振り分け、配信の台帳（未送信の記録と再送） | `gitlab_notify.py` | コマンド `notify`・`retry`・`status` |

ほかの仕組みとの分担:

| 仕組み | 検出 | 出す先 | この設計との関係 |
|---|---|---|---|
| issue-mailbox | 自前（5 分ごとのポーリングと `~/.issue-mailbox/state`） | tmux・ログ | 残す。移行していないリポジトリを受け持つ。同じリポジトリを両方で見ない（5 章）。`view` は移行後もそのまま使える（ログの行の形が同じ） |
| gitlab-issue-agent | change-watch（受け手 `issue-agent`、毎時） | エージェント CLI を Dagu の `harness.run` で**起動**して 1 件を処理させる | 別物として併存する。こちらは**知らせるだけ**で、エージェント CLI を起動しない。prompt チャネルは、既に動いているペインへ文字を送る |
| gitlab-issue-hub-agent | change-watch（ハブの issue のコメント） | ハブへの頼みごとに反応 | hub チャネルの書き込み先。こちらのコメントは `【通知】` で始め、ハブ側の条件（`【依頼】` など）に合わせない |

状態（`state_dir`）と受け手は gitlab-issue-agent と分ける。同じ取得元の定義を使うが、取得の予定（5 分ごと）が違い、
片方の設定変更や状態の作り直しがもう片方の基準を動かさないようにするため。

### 1.1 流れ

```
Dagu: change-watch-notify（毎分起動。1 回の起動で 1 件）
                                                             ┌──────────── 配信の台帳（SQLite）───────────┐
  get     change-watch get --consumer notify --one           │ 1 行 = 1 つの文言 × 1 つのチャネル            │
          │  取得は */5（取得元の schedule）。CHANGE を出す     │ 状態: pending → sent / dead                  │
          ▼                                                  └──────────────────────────────────────────────┘
  retry   台帳の pending を古い順に送り直す ──────────────────────▶ 読む・sent にする
          │  （失敗が残っても次へ進む）
          ▼
  notify  ① 整形   CHANGE → 文言 0〜4 個（issue-mailbox と同じ形）
          ② 振り分け  文言ごとに、条件に合う有効なチャネルを選ぶ
          ③ 記録   文言 × チャネルを pending で台帳へ（1 トランザクション）─▶ 書く   ← 書けなければ終了コード 1（commit しない）
          ④ 配信   tmux / prompt / log / hub へ送り、送れた行を sent に ──▶ sent にする（送れなければ pending のまま）
          ▼
  commit  change-watch commit --consumer notify CHANGE.key   notify が 0 で終わったときだけ
```

「処理し終えた」の意味は **「全チャネルに届いた」ではなく「届けるべきものを台帳に書き終えた」**。届け終える責任は台帳と `retry` が持つ
（4 章）。これで、1 つのチャネルの不調がほかのチャネルと次の変化を止めない。

## 2. 変化 → 文言

### 2.1 見る項目

`get` の `--watch` は `title`・`state`・`labels`・`assignee.username` の 4 つ（issue-mailbox のスナップショットと同じ項目）。
`updated_at`・コメント数・本文は見ないので、hub チャネルがハブの issue に書いたコメントで自分が起きることはない。
`commit` に `--refresh` は付けない（このエージェントは見ている issue に書き込まない。取り直しの 1 リクエストも要らない）。

文言は `before` と `record` を次の形に正規化して**比べて**作る。`changes`（変わった項目のパス）は判断に使わない。ラベルの並びだけが
変わったときに `labels[0]` などが入り、issue-mailbox（並べ替えてから比べる）と結果がずれるため。ログには `changes` をそのまま残す。

| 正規化した項目 | 取り出し方 |
|---|---|
| `iid` | `record.iid` |
| `title` | `title` |
| `state` | `state` |
| `labels` | `labels`（無ければ空）を並べ替えたもの |
| `assignee` | `assignee.username`（`assignee` が無ければ空文字） |

### 2.2 文言の一覧（issue-mailbox と同じ形）

どの行も先頭に `[<リポジトリ名>] ` が付く。`旧` は `before`、`新` は `record` を正規化した値。

| `kind` | 条件（正規化した値で比べる） | 対応する `changes` | 出すかを決める設定 | 文言 | 見る場所 |
|---|---|---|---|---|---|
| `appeared` | — | （空） | `new_issues`（既定 true） | `[NEW] #<iid> <title>` + ラベルがあれば ` [a, b]` + 担当がいれば ` @<assignee>` | `record` |
| `changed` | `旧.state != 新.state` | `state` | `state_changes`（true） | `[UPD] #<iid> state:<旧>→<新>  <title>` | `before.state`・`record.state`・`record.title` |
| `changed` | `旧.title != 新.title` | `title` | `title_changes`（false） | `[UPD] #<iid> title changed  <title>` | `record.title` |
| `changed` | `旧.labels != 新.labels` | `labels`・`labels[n]` | `label_changes`（false） | `[UPD] #<iid> labels:+<増えた分> -<減った分>  <title>` | `before.labels`・`record.labels` |
| `changed` | `旧.assignee != 新.assignee` | `["assignee.username"]` | `assignee_changes`（true） | `[UPD] #<iid> assignee:<旧>→<新>  <title>` | `before.assignee`・`record.assignee` |
| `removed` | — | （空） | `removed_as_closed`（true）かつ `state_changes` | `[CLOSED] #<iid> <title>` | `record`（`removed` の `record` は前回 commit した姿） |

細かい決まり（どれも issue-mailbox の `detect_changes` と同じ）:

- 1 つの `changed` から複数の文言が出る。順は state → title → labels → assignee
- `[UPD]` は項目と `<title>` のあいだが空白 2 つ。`[NEW]` のラベルは `, ` 区切り、`[UPD]` の `labels:` は `,` 区切りで、増えた分と減った分の
  あいだは空白 1 つ。片方しか無ければその片方だけ
- 担当が空のときは `(未割り当て)` と書く
- `removed` を `[CLOSED]` にするのは、取得元が開いている issue だけを取っているとき（issue-mailbox の `state: opened`）。全部の状態を
  取っているリポジトリでは `removed_as_closed` を false にし、閉じたことは `state:opened→closed` で知らせる
- 文言が 0 個の変化（見ない設定の項目だけが変わった・`removed` を出さない設定）は、何も送らずに `commit` する

ログ・tmux に出す 1 行は `<時刻>  <文言>`（時刻は PC の時刻で `YYYY-MM-DD HH:MM:SS`、あいだは空白 2 つ）。時刻は `notify` が
文言を作ったときのもので、再送でも変えない。

### 2.3 prompt チャネルの文面

テンプレートの `{名前}` を値に置き換えて 1 行にする。既定のテンプレートは `{line}` で、issue-mailbox の `send_enter: true` と
同じものがペインに入る。

| 名前 | 値 | 例 |
|---|---|---|
| `{line}` | 時刻つきの 1 行 | `2026-11-20 09:05:12  [my-api] [NEW] #12 ログインできない [bug] @sato` |
| `{message}` | 時刻なしの文言 | `[my-api] [NEW] #12 ログインできない [bug] @sato` |
| `{repo}` | リポジトリ名 | `my-api` |
| `{event}` | `new`・`state`・`title`・`labels`・`assignee`・`closed` | `new` |
| `{iid}`・`{title}`・`{url}` | `record` の `iid`・`title`・`web_url` | `12` |
| `{state}`・`{labels}`・`{assignee}` | 正規化した今の値（ラベルは `, ` 区切り） | `opened` |
| `{old}`・`{new}` | 変わった項目の前と後（`new`・`closed` では空） | `opened`・`closed` |

- `templates` に `event` ごとの文面を書ける。無い `event` は `template` を使う。例:
  `"new": "GitLab の {repo} に issue #{iid}「{title}」が作られました。{url} を読んで着手してください。"`
- 置き換えは 1 回だけ（値の中の `{…}` は置き換えない）。一覧に無い名前がテンプレートにあれば、設定の誤りとして読み込み時に止める
- 値の中の改行・制御文字は空白 1 つにする（1 つの通知が 2 回の Enter にならないように）

## 3. チャネル

### 3.1 共通の形

どのチャネルも「台帳の 1 行（1 つの文言）を受け取って送る」だけを持つ。台帳・再送・順序は共通の側が持ち、チャネルは持たない。

| チャネルが持つもの | 中身 |
|---|---|
| 設定の検査 | 読み込み時に、必須の項目・テンプレートの名前を確かめる。誤りは終了コード 1 で、どのチャネルの何が悪いかを出す |
| 送る | 1 行を送る。送れたら何も返さず、送れなければ理由つきの失敗を上げる。**自分では再試行しない**（待ちは台帳が決める） |

どのチャネルにも書ける項目: `id`（台帳のキーに入る名前。設定の中で重複させない。変えると別のチャネルとして扱う）、`type`、
`enabled`（既定 true）、`repos`（流すリポジトリ名。省略で全部）、`events`（流す `event`。省略で全部）、
`max_age_minutes`（未送信をあきらめるまで。既定は下の表）。

| `type` | 送り方 | 固有の項目 | あきらめるまで（既定） |
|---|---|---|---|
| `tmux` | `{line}` をペインに送る（Enter は送らない） | `target` | 1 日 |
| `prompt` | テンプレートで作った 1 行をペインに送り、続けて Enter | `target`・`template`・`templates` | 1 時間（古い指示をあとから投入しない） |
| `log` | `{line}` と改行をファイルに追記する（親フォルダが無ければ作る。UTF-8） | `path` | 1 日 |
| `hub` | ハブの issue にコメントを 1 件書く | `issue_url`（省略で環境変数 `GITLAB_HUB_ISSUE_URL`） | 1 日 |

- **tmux・prompt**: `tmux send-keys -t <target>` を 1 回の起動で呼ぶ（issue-mailbox と同じ）。文字はキー名として解釈させない形で
  渡し、末尾が `;` の文言が tmux のコマンドの区切りにならないようにする。`tmux` が無い・ペインが無い・0 以外で終わったら失敗。
  待ちは 10 秒まで
- **hub**: gitlab-merge-followup-agent と同じ流儀。`GITLAB_URL`・`GITLAB_TOKEN`（`api`）を環境変数から取り、同じ GitLab の issue に
  限り、行頭の `/` を `\/` にしてクイックアクションを無効にする。本文は `【通知】` に続けて文言をコードの書式で囲み
  （`@担当` が人への通知に、`#12` がハブ側のプロジェクトの issue へのリンクにならないように）、次の行に issue の URL を書く。
  待ちは 30 秒まで

### 3.2 失敗の扱い

- 失敗は、一時的か恒久的かを分けず、どれも「未送信」として台帳に残す（トークンやペインの指定を直せば、次の再送で流れる）
- 1 つのチャネルの失敗は、ほかのチャネルの配信を止めない
- 同じチャネルの中では古い順に送る。先頭が送れなければ、そのチャネルの後ろは送らずに待たせる（順序を保ち、不調の相手を叩き続けない）
- `notify`・`retry` は、送れなかった行ごとに `チャネル・key・理由` を標準エラーに 1 行出す。トークンは伏せる

### 3.3 設定の置き場所

**JSON の設定ファイル 1 つ**（`~/.config/change-watch/gitlab-notify-agent/gitlab-notify.json`。場所は env の `GITLAB_NOTIFY_CONFIG`）に
リポジトリごとの出し分けとチャネルを書く。認証情報（`GITLAB_URL`・`GITLAB_TOKEN`・`GITLAB_HUB_ISSUE_URL`）は、ほかのエージェントと
同じく env（`gitlab-notify-agent.env`）にだけ書く。

JSON にした理由:

- チャネルは「同じ種類を複数（tmux の先が 2 つ）」「チャネルごとに流すリポジトリと `event`」と入れ子の並びになる。env の
  `名前=値` では番号つきの変数名か区切り文字の約束が要り、読み違えやすい
- 標準ライブラリだけで読める（YAML は読めない）。ci-triage の `ci-triage-patterns.json`・ハブの `hub-request-kinds.json` と同じ流儀で、
  インストーラの「`*.example` を設定の置き場所へ置く」決まりにもそのまま乗る
- トークンを入れないので、設定ファイルを人に見せたり例として配ったりできる

```json
{
  "outbox": "~/.local/state/change-watch/gitlab-notify-agent/outbox.db",
  "repos": {
    "my-api": {
      "watch": { "new_issues": true, "state_changes": true, "title_changes": false,
                 "label_changes": false, "assignee_changes": true },
      "removed_as_closed": true
    }
  },
  "channels": [
    { "id": "log",  "type": "log",    "path": "~/.issue-mailbox/notifications.log" },
    { "id": "pane", "type": "tmux",   "target": "main:mailbox" },
    { "id": "kiro", "type": "prompt", "target": "main:kiro.0", "template": "{line}",
      "repos": ["my-api"], "events": ["new", "state"], "enabled": false },
    { "id": "hub",  "type": "hub",    "enabled": false }
  ]
}
```

`repos` のキーは、ワークフローが `notify --repo` に渡す名前で、文言の `[<リポジトリ名>]` になる。`repos` に無い名前で呼ばれたら
設定の誤りとして止める（commit しない）。

## 4. commit と通知の失敗

### 4.1 規則

1. `commit` してよいのは、その変化の**全部の文言 × 全部の宛先チャネルが、台帳に `sent` か `pending` で載っているとき**だけ
2. 台帳に書けなかった（ディスクが一杯・DB を開けない・権限が無い）、変化の JSON を読めなかった、設定が誤っていたときは、`notify` は
   **終了コード 1** で終わる。`commit` は動かず、次の起動の `get` が同じ変化をもう一度返す
3. 台帳に書けて送れなかっただけなら、`notify` は **0** で終わり、`commit` する。届けるのは `retry` の仕事になる
4. 記録は送る**前**に書く（先に送ると、送ったあとで落ちたときに「どこへ送ったか」が残らない）

### 4.2 台帳

場所は設定の `outbox`（既定 `~/.local/state/change-watch/gitlab-notify-agent/outbox.db`）。SQLite（標準ライブラリ）にする。
複数のリポジトリのワークフローが同時に書いても壊れず、「文言 × チャネルをまとめて記録する」を 1 トランザクションでできるため。
change-watch 本体の DB とは別のファイルにし、本体の表の形に寄りかからない。1 行は「1 つの文言 × 1 つのチャネル」。

| 列 | 中身 |
|---|---|
| `id` | 配信の指紋（下）。主キー |
| `seq` | 載せた順の番号（送る順） |
| `repo`・`rkey`・`kind`・`event` | リポジトリ名・`get` の `key`・`kind`・`event` |
| `channel` | チャネルの `id` |
| `ts`・`message` | 文言を作った時刻・時刻なしの文言 |
| `fields` | テンプレート用の値（`iid`・`title`・`url`・`old`・`new` など）の JSON。再送のときに変化の JSON が無くても文面を作れる |
| `status` | `pending`（未送信）・`sent`（送った）・`dead`（あきらめた） |
| `attempts`・`last_error`・`next_attempt_at` | 試した回数・最後の失敗の理由・次に試してよい時刻 |
| `created_at`・`sent_at` | 載せた時刻・送れた時刻 |

**配信の指紋**（`id`）は、次を順につないだものの SHA-256 の先頭 32 桁。

| 入れるもの | 中身 | 入れる理由 |
|---|---|---|
| リポジトリ名・`key`・`kind` | — | どの issue のどの種類の変化か |
| 基準 | `changed` は `before` 全体の指紋、`appeared`・`removed` は `record.updated_at` | commit で基準が進むと値が変わる。同じ文言が後日もう一度出たとき（閉じる → 開き直す → また閉じる）に別物として扱う |
| 時刻なしの文言 | — | commit できないまま issue がさらに変わったとき、既に載せた文言（`state:opened→closed`）は同じ指紋になり、増えた文言だけが新しく載る |
| チャネルの `id` | — | チャネルごとに 1 行 |

送る先（`target`・`path`・`issue_url`）とテンプレートは台帳に入れず、送るときの設定から取る。指定の誤りを直せば、たまった分が流れる。

### 4.3 二重に流さない

- `notify` の記録は「同じ `id` が既にあれば何もしない」。`commit` に失敗して同じ変化がもう一度来ても、行は増えない
- 送るのは `pending` の行だけ。`sent` の行はどの経路（`notify` のやり直し・`retry`・手での再実行）でも送らない。A に送れて B に
  送れなかった変化は、B の行だけが `pending` で残り、再送は B にだけ流れる
- 配信と期限切れの整理は、台帳の隣にある `outbox.db.lock`（台帳パス + `.lock`）を `flock` で排他する。ロックを取れない回は
  配信を見送り、行は `pending` に残す。送信中は SQLite のトランザクションを閉じておき、結果だけを短いトランザクションで記録する
- 送れたら、その行をすぐ `sent` にする（1 行ずつ。まとめて最後に書かない）
- 残る穴は 1 つ: **送れた直後、`sent` と書く前に落ちた**とき、その 1 行がもう一度送られる。tmux とファイルへの追記は相手に
  「同じものか」を聞けないので、ここは受け入れる（取りこぼすより重複を選ぶ）

### 4.4 再送（`retry`）

- `pending` のうち `next_attempt_at` を過ぎた行を、チャネルごとに `seq` の順で送る。先頭が失敗したら、そのチャネルはそこで止める
- 失敗したら `attempts` を増やし、次に試す時刻を 1 分・2 分・4 分…（最大 1 時間）先にする
- 載せてから `max_age_minutes` を過ぎた `pending` は `dead` にして、理由を標準エラーに出す。`dead` は後ろの行を待たせない
- 設定から消えた `id` の行、`enabled: false` にしたチャネルの行は送らず、期限が来たら `dead` にする
- `sent` は 7 日、`dead` は 30 日たったら消す
- 終了コード: この回に試した行が全部送れた・試す行が無かったら 0、1 行でも送れなかったら 1（Dagu の履歴に赤く残る。待ちの
  あいだは試さないので、毎分は赤くならない）
- `notify` の配信も同じ決まりで動く。そのチャネルに古い `pending` が残っていれば、新しい行はその後ろに並べて待たせる

`status` は、リポジトリごと・チャネルごとの `pending`・`dead` の件数と、いちばん古い未送信の時刻・最後の失敗の理由を出す
（`--json` で機械向け）。「届かない」ときの切り分けは次の順になる。

| 確かめること | 見るもの | 分かること |
|---|---|---|
| GitLab から取れているか | `change-watch status` の取得元の最後の取得 | 取得の失敗・待ち |
| 変化として拾ったか | Dagu の `get` の出力（`CHANGE`）と `notify` の出力（作った文言の数） | 見る項目・出し分けの設定で落ちていないか |
| 送れたか | `gitlab_notify status` と `retry` のログ | どのチャネルが、何の理由で止まっているか |

### 4.5 ワークフロー

リポジトリごとに 1 本（`change-watch-notify.yaml`）。毎分起動し、1 回の起動で 1 件。

```yaml
schedule: "* * * * *"
type: graph
dotenv:
  - "/home/you/.config/change-watch/gitlab-notify-agent/gitlab-notify-agent.env"
steps:
  - name: get                       # 変わった issue を 1 件（取得するかは取得元の schedule で change-watch が決める）
    action: exec
    with:
      command: "/home/you/.local/bin/change-watch"
      args: ["--config", "…/gitlab-notify-agent/change-watch.yaml", "--connections", "…/connections.yaml",
             "get", "--source", "gitlab-issues", "--consumer", "notify",
             "--watch", "title", "--watch", "state", "--watch", "labels", "--watch", "assignee.username", "--one"]
    retry_policy: { limit: 3, interval_sec: 60, exit_code: [3] }
    output: CHANGE
  - name: retry                     # たまった未送信を先に送る（古いものから届く）
    depends: [get]
    continue_on: { failure: true }  # 送れないものが残っても、新しい変化の記録と commit は止めない
    action: exec
    with:
      command: "/home/you/.local/bin/change-watch"
      args: ["--plugin", "gitlab_notify", "retry"]
    timeout_sec: 300
  - name: notify                    # 整形 → 振り分け → 台帳に記録 → 配信
    depends: [retry]
    preconditions:
      - condition: "${CHANGE.key}"
        expected: "re:."
    continue_on: { skipped: true }  # 変化が無い回。failure は付けない（記録できなければ commit しない）
    action: exec
    with:
      command: "/home/you/.local/bin/change-watch"
      args: ["--plugin", "gitlab_notify", "notify", "--repo", "my-api", "${CHANGE}"]
    timeout_sec: 300
  - name: commit                    # notify が 0 で終わったときだけ。removed も同じステップ（取り直さないので分けない）
    depends: [notify]
    preconditions:
      - condition: "${CHANGE.key}"
        expected: "re:."
    action: exec
    with:
      command: "/home/you/.local/bin/change-watch"
      args: ["--config", "…", "--connections", "…",
             "commit", "--source", "gitlab-issues", "--consumer", "notify", "${CHANGE.key}"]
    retry_policy: { limit: 3, interval_sec: 60, exit_code: [3] }
```

| ステップ | 失敗したら | 理由 |
|---|---|---|
| `get` | そこで止める（`continue_on` なし） | 設定の誤りのときだけ失敗する（GitLab に届かないときは、前回までの取得結果で答えて 0）。直すまで何もしない |
| `retry` | 次へ進む（`continue_on.failure`） | 1 つのチャネルの不調で、新しい変化の記録を止めない。未送信は台帳に残っている |
| `notify` | `commit` しない（`continue_on.failure` を**付けない**） | 失敗は「記録できなかった」だけ。次の起動で同じ変化をやり直す |
| `commit` | 失敗として残る | 次の起動で同じ変化が来るが、指紋が同じなので行は増えず、送り直しもしない |

- `retry` を `notify` の前に置くのは、同じチャネルで古い通知を先に届けるため。変化が無い回も `retry` は動く（`get` の出力が空でも
  `retry` には条件を付けない）
- `notify` が失敗し続ける変化があると、`--one` は毎回その 1 件を返すので後ろが進まない。これは取りこぼさないための意図した
  止まり方で、Dagu の履歴に毎分の失敗として出る。直せない変化を飛ばすには、手で `commit` する

## 5. 二重検出の防止と移行

決まりは 1 つ: **1 つのリポジトリを、同じ宛先に向けて両方で見ない**。切り替えはリポジトリ単位で行う。

### 5.1 移行の手順（リポジトリ R）

1. **入れる**: インストーラで入れ、`change-watch.yaml` に R の取得元（絞り込みは 6 章の対応表のとおり）、`gitlab-notify.json` に R の
   出し分けを書く。チャネルは **log だけ**にし、`path` は issue-mailbox とは別のファイル（例: `~/.issue-mailbox/notifications-shadow.log`）にする
2. **基準を取る**: `get --source … --consumer notify --no-jitter` を手で 1 回。今ある issue を覚えるだけで何も出さない
   （issue-mailbox の「初回は通知しない」と同じ。`--no-silent-first` に当たるのは `--initial emit`）
3. **並べて確かめる**: ワークフローを動かし、1 日ほど、別ファイルのログと issue-mailbox のログの R の行を、時刻を除いて見比べる。
   この間、人やエージェントに届くのは issue-mailbox の分だけなので、二重にはならない
4. **切り替える直前**: `gitlab_notify status` で未送信が 0、Dagu の直近の `get` が空なのを確かめる。`issue-mailbox.py status` で R の
   最終ポーリングの時刻を見て、**ポーリングの直後**（次のポーリングまでの 5 分のあいだ）に 5 を終える
5. **切り替える**（この順で）:
   1. R のワークフローを Dagu で止める
   2. `issue-mailbox.yaml` の R を `enabled: false` にして、issue-mailbox を **`--no-silent-first` を付けて**起動し直す
   3. `gitlab-notify.json` のチャネルを本番の宛先（issue-mailbox と同じログ・ペイン）に替える
   4. R のワークフローを動かす
6. 次のリポジトリは 1 から繰り返す（2 本目からは、取得元とワークフローを足すだけ）

この順の性質:

- **取りこぼさない**。ワークフローを止めてから動かすまでの変化は、動かしたあとの最初の `get` が拾う（受け手の基準は止める前のまま）
- **二重になりうるのは、5-1 から 5-2 までのあいだに issue-mailbox がポーリングして知らせた変化だけ**。4 のとおりポーリングの直後に
  行えば、その窓にポーリングは入らない。入ってしまったら、その変化は 1 回ずつ両方から届く（別ファイルのログと見比べれば分かる）
- 5-2 で `--no-silent-first` を付けるのは、issue-mailbox が起動し直すたびに**最初のポーリングを知らせずに状態だけ進める**ため。
  付けないと、残りのリポジトリの「最後のポーリングから起動し直しまで」の変化が消える。状態ファイルがあるリポジトリは、付けても
  前回との差分だけが出る（全部が `[NEW]` になるのは、状態ファイルが無いリポジトリだけ）

### 5.2 戻し方

issue-mailbox の状態ファイル（`~/.issue-mailbox/state/<R>.json`）は**無傷で残る**。新しい方式は `~/.issue-mailbox/` の下では、設定で
指定したログファイルへの追記しか行わず、`enabled: false` のリポジトリを issue-mailbox はポーリングしないので書き換えない。
ただし中身は切り替えた時点の姿のまま古くなっている。

1. R のワークフローを Dagu で止める（台帳に残った R の未送信は、ほかのリポジトリのワークフローの `retry` が送り切るか、期限で
   `dead` になる）
2. R の状態ファイルを今の姿に進める。R だけを書いた一時の設定（同じ `state_dir`、`tmux` は書かない、`log_file` は捨ててよい場所）で
   issue-mailbox を `--no-silent-first` **なし**で起動し、最初のポーリングが済んだら止める。状態だけが進み、何も知らされない
3. `issue-mailbox.yaml` の R を `enabled: true` に戻し、issue-mailbox を `--no-silent-first` を付けて起動し直す

**追いつきの一斉通知に注意**。2 を飛ばして 3 を行うと、切り替えてから今までの変化が、最初のポーリングで一度に出る（開いた issue の
数だけ `[NEW]`、閉じた数だけ `[CLOSED]`）。新しい方式が既に知らせた内容で、`send_enter: true` のペインにはその数のプロンプトが
続けて入る。2 を行えば出ない。`--no-silent-first` なしで 3 だけを行っても R の分は出ないが、ほかのリポジトリの直近の変化が消える。

戻したあとでもう一度新しい方式へ切り替えるときは、change-watch の受け手の基準が古いので、同じことが新しい方式の側で起きる。
チャネルを別ファイルの log だけにしてワークフローを動かし、`get` が空になってから 5.1 の 4 へ進む（追いつきの分は別ファイルに落ちる）。

### 5.3 issue-mailbox との違い（移行で見え方が変わるところ）

| こと | issue-mailbox | 新しい方式 |
|---|---|---|
| 取得が途中で失敗した | 取れたページまでで比べるので、残りが `[CLOSED]` として出うる | 取得し終えた結果だけで比べる。失敗した回は前回の結果のまま |
| 起動し直し | 最初のポーリングは知らせない（その間の変化が消える） | 常駐しない。受け手の基準から続きを拾う |
| 絞り込みを変えた | 外れた issue が `[CLOSED]`、入った issue が `[NEW]` で出る | 取得条件が変わると基準を取り直し、何も出さない |
| 一度に多く変わった | 1 回のポーリングで全部出る | 1 分に 1 件ずつ出る（8 章） |
| 届かなかった通知 | tmux は警告だけで失われる | 台帳に残り、送り直す |

## 6. 設定の対応表

| issue-mailbox.yaml | 新しい方式の場所 | 書き方 |
|---|---|---|
| `repositories[].name` | `gitlab-notify.json` の `repos` のキーと、ワークフローの `notify --repo` | 文言の `[名前]` になる。同じ名前にすればログの形が変わらない |
| `repositories[].gitlab_url` | env の `GITLAB_URL`（接続設定 `connections.yaml` の `gitlab` が `${GITLAB_URL}` で取る） | 別の GitLab のリポジトリは、接続設定にラベルを足して取得元の `connection` で選ぶ |
| `repositories[].project_id` | env の `GITLAB_PROJECT`（数値の ID でも `group/project` でもよい） | 2 本目からは、`change-watch.yaml` に足した取得元の `url` に直接書く |
| `repositories[].private_token` | env の `GITLAB_TOKEN`（接続設定が `${GITLAB_TOKEN}` で取る） | `read_api`。hub チャネルを使うなら `api` |
| `repositories[].state` | `change-watch.yaml` の取得元の `query.state` | `opened` 以外にするなら、`gitlab-notify.json` の `removed_as_closed` を false にする |
| `repositories[].labels_filter` | 取得元の `query.labels` | カンマ区切りの 1 つの文字列（`"ai-task,backend"`） |
| `repositories[].assignee_username` | 取得元の `query.assignee_username` | — |
| `repositories[].poll_interval_minutes` | 取得元の `schedule`（cron） | 5 → `*/5 * * * *`、10 → `*/10 * * * *`。ワークフローの起動（毎分）は変えない |
| `repositories[].watch.new_issues` | `repos.<名前>.watch.new_issues` | 同じ名前・同じ既定値（true） |
| `repositories[].watch.state_changes` | `repos.<名前>.watch.state_changes` | 同上（true）。`[CLOSED]` もこれで止まる |
| `repositories[].watch.title_changes` | `repos.<名前>.watch.title_changes` | 同上（false） |
| `repositories[].watch.label_changes` | `repos.<名前>.watch.label_changes` | 同上（false） |
| `repositories[].watch.assignee_changes` | `repos.<名前>.watch.assignee_changes` | 同上（true） |
| `repositories[].enabled` | そのリポジトリのワークフローを Dagu で止める／動かす | 止めているあいだの変化は、動かしたときにまとめて出る。捨てたいときは 5.2 の末尾の手順 |
| `tmux.target` | `channels[]` の `type: tmux` または `type: prompt` の `target` | 複数書ける |
| `tmux.send_enter` | チャネルの `type` | false → `tmux`、true → `prompt`（`template` は既定の `{line}` で同じ文面） |
| `log_file` | `channels[]` の `type: log` の `path` | 同じファイルを指せば `issue-mailbox.py view` がそのまま使える |
| `state_dir` | `change-watch.yaml` の `state_dir`（変化の基準）と、`gitlab-notify.json` の `outbox`（配信の台帳） | `~/.local/state/change-watch/gitlab-notify-agent/` の下。`~/.issue-mailbox/state` は使わない |
| （コマンド行）`--no-silent-first` | 基準を取るときの `get --initial emit` | 既定は基準を取るだけ |
| （コマンド）`status` | `change-watch status` と `gitlab_notify status` | — |

`watch.*` を false にした項目も、`get` の `--watch` からは外さない（ワークフローをリポジトリごとに書き分けずに済ませ、あとで true に
したときに基準がずれないように）。出さない項目だけが変わった変化は、文言 0 個で `commit` される。

## 7. ファイルとテスト

### 7.1 新しく作るファイル

`tools/change-watch/agents/gitlab-notify-agent/` の下。

| ファイル | 役割 |
|---|---|
| `gitlab_notify.py` | プラグイン本体。`register(api)` で `notify`・`retry`・`status` を足す（設定は自分で読むので、change-watch の `--config` は要らない）。整形・振り分け・台帳・4 つのチャネル |
| `README.md` | 入れ方・チャネルの書き方・issue-mailbox からの移行と戻し方・届かないときの確かめ方 |
| `change-watch.yaml.example` | 取得元 `gitlab-issues`（gitlab-issue-agent と同じ定義で、`state_dir` は自分のもの、`schedule` は `*/5 * * * *`、`jitter_sec` は 60。`item` は書かない） |
| `change-watch-notify.yaml.example` | 4.5 のワークフロー |
| `change-watch-gitlab-notify-agent-maintenance.yaml.example` | 取得結果の整理（`gc`）。gitlab-issue-agent のものと同じ形 |
| `gitlab-notify-agent.env.example` | `GITLAB_URL`・`GITLAB_PROJECT`・`GITLAB_TOKEN`・`GITLAB_HUB_ISSUE_URL`・`GITLAB_NOTIFY_CONFIG` |
| `gitlab-notify.json.example` | 3.3 の設定の例（log と tmux が有効、prompt と hub は `enabled: false`） |
| `test/test_gitlab_notify.py` | 7.2 のテスト |

あわせて直すもの: `tools/change-watch/README.md` のエージェントの一覧、`CHANGELOG.md`、`docs/designs/README.md` の索引。
インストーラは `change-watch.yaml.example` を持つフォルダを自分で見つけるので、直さなくてよい見込み（8 章）。

### 7.2 テスト計画

標準ライブラリだけ（`python3 -m unittest discover -s test`）。`get` の出力の形をした JSON を手で作って渡し、チャネルは
「送られたものを覚える・指定した回だけ失敗する」偽物に差し替える。台帳は一時フォルダの本物の SQLite を使う。

| 確かめること | やり方 |
|---|---|
| 文言が 2.2 の表のとおりか | `appeared`・`changed`（項目ごと・複数同時）・`removed` の JSON から、期待の文字列（issue-mailbox のコードから書き写した固定値）と一字一句同じになる。空白 2 つ・`(未割り当て)`・ラベルの区切り・順序を含む |
| 出し分け | `watch.*` を false にした項目・`removed_as_closed: false` で文言が出ない。ラベルの並びだけが違う `changed` は 0 個 |
| ログの 1 行 | 時刻の形と空白 2 つ。再送で時刻が変わらない |
| テンプレート | 既定で `{line}` と同じ。`event` ごとの文面。値の中の `{…}` と改行。知らない名前は読み込みで止まる |
| 振り分け | `repos`・`events`・`enabled` で、流れるチャネルが決まる |
| 1 つのチャネルの失敗 | B だけ失敗させる → A には届き、B の行が `pending`、`notify` は 0 |
| 再送で二重にならない | 続けて `retry` → B にだけ 1 回届き、A には増えない。もう一度 `retry` しても増えない |
| 同じ変化がもう一度来た | 同じ JSON で `notify` を 2 回 → 行もチャネルへの送信も増えない。`before` が同じで `record` が進んだ JSON → 増えた文言だけが載る |
| 記録できない | 台帳を書けない場所にする → `notify` が 1、どのチャネルにも送っていない |
| 順序と待ち | 先頭が失敗したチャネルは後ろを送らない。待ちの時刻までは試さない。期限で `dead` になり、後ろが流れる |
| hub の本文 | `【通知】` で始まる。`@`・`#`・行頭の `/` が効かない形。別の GitLab の URL は断る。トークンがログに出ない |
| tmux の呼び方 | `tmux` を偽のコマンドに差し替え、引数（`-t`・文字・Enter の有無・末尾 `;` の文言）を確かめる |
| ワークフローの順 | gitlab-issue-agent のテストと同じく、偽の GitLab と本物の change-watch で `get` → `retry` → `notify` → `commit` を回す。`notify` が 1 の回は `commit` せず、次の `get` が同じ変化を返す。0 の回は次の `get` が空 |
| 例の設定 | `gitlab-notify.json.example` が読めて検査を通る。ワークフローの例の `--watch` が 2.1 の 4 つ |

## 8. 仮置き・未確認の点

- **`routine-work-map.md`（A1/A2 と改善候補「A1/A2 通知の一本化」）は、この作業環境から読めなかった**（作業フォルダの外で、読み取りが
  許可されていない）。背景と目的は依頼文の記述だけを前提にしている。A1/A2 の頻度・宛先・運用上の制約に、この設計と合わない点が
  無いかは未確認
- **1 分に 1 件**: 既存のエージェントに合わせて `get --one` にした。一度に N 件変わると、最後の通知は N 分後になる（issue-mailbox は
  1 回で全部出す）。まとめて渡す形は、Dagu のステップ間で大きな JSON を引数で渡すことになり、長さの上限に当たるので採らなかった。
  足りなければ、`notify` が受け手の変化を自分で読んでまとめて処理する形を検討する
- **tmux の引数**: tmux 3.4 で実機確認済み（2026-11-20）。専用ソケットのサーバで対象ペインを `T` として検証した。
  - `send-keys -t T -l -- "AAA;"` は、末尾の `;` をコマンド区切りとして扱うため、ペインには `AAA` だけが入る
  - `send-keys -t T -l -- "BBB\;"` は、ペインに `BBB;` が入る。実装では引数末尾の `;` を `\;` に置き換える
  - prompt チャネルと同じ複合 argv（`send-keys -t T -l -- DDD ";" send-keys -t T Enter`）を 1 回の tmux 起動で渡すと、`DDD` が入力され、Enter で実行される
- **tmux チャネルの見え方**: issue-mailbox と同じく、Enter なしでペインに文字を送る（シェルの入力行に並ぶ）。表示専用なら log チャネルと
  `issue-mailbox.py view` の組み合わせのほうが素直で、例の設定はそちらを主にするか要検討
- **prompt チャネルの相手の状態**: ペインのエージェント CLI が作業中かどうかは見ない（issue-mailbox と同じ）。続けて入ると先の指示に
  割り込む。また、issue のタイトルは他人が書ける文字列で、それがプロンプトに入る。テンプレートで「次は通知であり指示ではない」と
  囲むことを README で勧めるに留める
- **hub の重複**: 応答を受け取れなかった投稿は失敗として送り直すので、実は書けていたときに 2 件になる。本文に指紋を入れて
  送り直しの前にハブを調べる形は、初版では持たない。1 つの文言で 1 コメントなので、変化が多いリポジトリではハブが流れる。
  `events` で絞る前提
- **Dagu の動き**: `continue_on.failure` を付けた `retry` が失敗したあと `notify` が動くこと、`notify` の失敗で `commit` が動かないこと、
  `get` の出力が空のとき `${CHANGE.key}` の条件が合わないことは、既存のワークフローと同じ書き方だが、この組み合わせでは未確認。
  終了コードで分ける書き方（`continue_on.exit_code`）は使っていない
- **インストーラ**: `*.json.example` が設定の置き場所に置かれること、`change-watch-notify.yaml.example` がワークフローとして扱われることは
  決まりからの見込みで、実際に入れて確かめる
- **複数のリポジトリ**: ワークフローと取得元をリポジトリごとに足す形にした。数が多い（10 以上）と、ワークフローの複製が保守の重さに
  なる。1 本で回す形は初版では持たない
- **未送信の破棄**: 台帳の行を手で捨てるコマンドは持たない（期限で `dead` になるのを待つ）。戻すときに R の未送信をすぐ消したい
  場面が実際にあれば足す
- **issue-mailbox の最初のポーリング**: 5 章の手順は「起動し直すと最初のポーリングを知らせない」というコードの読みに基づく。
  実機で 1 度確かめてから README に書く
