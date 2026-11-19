# gitlab-issue-hub-agent 設計

URL で決めた 1 つの GitLab の issue を、メールと Power Automate をつなぐハブにする。エージェントが受け持つのは **issue の変化を返すこと**
（新しく付いたコメントとその中身、issue そのものの変化）と、**issue を変えられること**（コメントを足す・ラベルや説明や状態を更新する）だけ。
どの変化にどう反応するか・何を書くかは Dagu のワークフローに、変化を受けて何をするかは Power Automate に置く。メールの送受信は
GitLab の通知メールと Power Automate に任せ、エージェントはメールに触らない。gitlab-issue-agent・redmine-ticket-agent と同じく Dagu と
change-watch の上に組み、change-watch には API へのアクセス（`get`・`commit`・`call`）だけを、条件は Dagu の `preconditions` に持たせる
（change-watch の 0.6.0 の分担）。実装は `tools/change-watch/agents/gitlab-issue-hub-agent/`、使い方は同フォルダの README。

## 1. 決めたこと（2026-11-09、利用者と確かめた）

- ハブは issue の URL 1 つで決める（`GITLAB_HUB_ISSUE_URL`）。プラグインのフィルタ `gitlab_issue_api` が URL を API の URL にし、
  取得元の `request.url` とワークフローの `call` の URL（change-watch がテンプレートとして展開する）に書く。プロジェクトの ID や番号を別に持たない
- 頼まれる側: エージェントは変化の検知に注力する。ワークフローの先頭の `get` が変化を受け取り、GitLab に問い合わせるか前回の取得結果で
  答えるかは change-watch が決める。どの変化に反応するか（例の「1 行目が【依頼】」）はワークフローの `preconditions` に書き、エージェントの
  プログラムには持たない
- 頼む側: エージェントは issue を変えられればよい。何を書くかはワークフローが決め、変化を受けて何をするかは Power Automate が決める。
  エージェントは印（【通知】など）も条件も持たない
- 依頼の向き: Power Automate が issue に書き、エージェントが読む。逆向き（エージェントからメール）は GitLab の通知メールで足りる。
  エージェントは SMTP・Microsoft 365 の認証を持たず、GitLab のトークン 1 つで済み、やりとりが issue に残る

## 2. 位置づけと分担

| 受け持ち | どこ | 何を |
|---|---|---|
| ハブの issue の取得・変化を返す・書き込み | change-watch | 取得元 `gitlab-hub-notes`（`GET <issue の API>/notes`、page のページング）と `gitlab-hub-issue`（`GET <issue の API>`、1 件）、どちらも 5 分ごと。`get --consumer`・`commit`・`call`（POST notes / PUT issue） |
| 起動・条件の分岐・何を書くか・再試行・履歴 | Dagu | 例の `change-watch-issue-hub`（毎分。get → request → reply → commit）、例の `change-watch-issue-hub-update`（params の COMMENT で呼ばれる。compose → comment）、整理 |
| GitLab に固有のこと | `gitlab_hub.py`（change-watch のプラグイン。コマンドも `change-watch --plugin gitlab_hub <コマンド>` で呼び、設定は読まない） | フィルタ `gitlab_issue_api` と、コマンド `note`（コメントの本文の JSON を、文・ファイル・コマンドの出力から作る）・`fields`（issue の更新の JSON を `名前=値` から作る）・`description`（説明の JSON を、`note` と同じ渡し方で作る）・`api-url`（issue の URL から API の URL を出す。無ければハブ）。条件も本文の形も持たない。HTTP は送らない |

`note` が要るのは、Dagu の引数に任意の文（改行・引用符を含む）を JSON として埋め込むと壊れるため。認証と要求の間隔は change-watch の
`call` に一本化する。

## 3. 構成

```
Dagu: change-watch-issue-hub（毎分。例）
  （change-watch のステップはどれも --plugin gitlab_hub。URL は {{ env.GITLAB_HUB_ISSUE_URL | gitlab_issue_api }}）
  get      change-watch get --source gitlab-hub-notes --consumer hub --watch body --one              → CHANGE
  request  preconditions（例）: CHANGE.kind = appeared・CHANGE.record.system = false・CHANGE.record.body が ^【依頼】
           change-watch note -- python3 on_request.py "${CHANGE}"                          → NOTE（{"body": …}）
           continue_on: skipped・failure
  reply    preconditions: NOTE が ^{"body"
           change-watch call POST "<issue の API>/notes" --json "${NOTE}"
           retry_policy: 終了コード 3 だけ。continue_on: skipped・failure
  commit   preconditions: CHANGE.key があるとき。change-watch commit --consumer hub ${CHANGE.key}

Dagu: change-watch-issue-hub-update（予定なし。dagu start … -- COMMENT=…。例）
  compose  change-watch note --text=${params.COMMENT}                                       → NOTE
  comment  preconditions: NOTE が ^{"body"。change-watch call POST "<issue の API>/notes" --json "${NOTE}"
  （issue の更新は、同じ形で change-watch fields title=… → call PUT "<issue の API>" --json "${UPDATE}" のステップを書く）

examples/change-watch-issue-hub-get-post（インストーラは置かない。get と post の一通りの例）
  get      change-watch get --source gitlab-hub-issue --consumer get-post --watch title --watch labels --watch description --one
  compose → comment   条件（例: ラベル「完了」・題がまだ【完了】で始まらない）で note → call POST …/notes
  fields → retitle    同じ条件で fields title=【完了】… → call PUT <issue の API>
  commit   commit --refresh（取得元 gitlab-hub-issue の item で取り直し、自分の変更を次の変化にしない）

examples/change-watch-issue-hub-description（インストーラは置かない。予定なし。dagu start … -- ISSUE_URL=… DESCRIPTION=…）
  compose  change-watch description --text=${params.DESCRIPTION}                            → UPDATE（{"description": …}）
  target   preconditions: UPDATE が ^{"description"。change-watch api-url --url=${params.ISSUE_URL}   → ISSUE_API
  update   change-watch call PUT "${ISSUE_API}" --json "${UPDATE}"
```

ハブでない issue を引数で指すとき、`call` の URL の `{{ }}` は環境変数しか受けられない（Dagu の引数をフィルタに通せない）ので、
`api-url` が API の URL を先に作って渡す。接続と要求の間隔は取得元 `gitlab-hub-issue` のものを借りる。

### 3.1 自分の書き込みでループしない

返信も同じ issue のコメントなので、次の取得で `appeared` として返る。これを避けるのはワークフローの条件の受け持ちで、例では
「1 行目が【依頼】」に絞り、例のコマンドは【返信】で始まる本文を出す。書き手（トークンの持ち主）では見分けない（Power Automate が同じ
トークンで書くことがあるため）。`commit --refresh` は要らない（コメントは 1 件ずつのレコードで、自分の書き込みが既存のレコードを変えない）。

### 3.2 条件が飛ばされたステップの出力に頼らない

`reply`・`comment` は、前のステップが動いたかを `${request.exit_code}` や `${NOTE}` が空かどうかでは見ず、`${NOTE}` が `{"body"` で始まるかで見る。
飛ばされたステップの `output` が定義されないとき、Dagu が `${NOTE}` を空にするか文字のまま残すかに依らず、どちらも当たらない。
`note` は本文が空なら何も出さないので、空の書き込みも同じ条件で飛ばす。

### 3.3 失敗の扱い

- `note -- <コマンド>` は、コマンドが失敗したら何も出さず、その終了コードで終わる。`continue_on.failure` で進むが、`reply` は飛ばされ、
  Dagu の履歴に失敗として残る。失敗を知らせるコメントを書くかはワークフロー（とコマンド）が決める。標準エラーはログにだけ残す
- 反応したコマンドは自動ではやり直さない（`request` に `retry_policy` を付けない）。副作用のあるコマンドを二重に動かさないため
- `call` は書き込みを自分で再試行しないので、`reply`・`comment` に `retry_policy`（終了コード 3）を付ける

### 3.4 クイックアクション

GitLab はコメントの行頭の `/close` などを操作として実行する。メールの本文やコマンドの出力にそれが混ざると、トークンの持ち主の権限で
issue が閉じられる。`note` は本文の行頭の `/` を `\/` にする（Markdown の表示は `/` のまま）。Power Automate が書く側は Power Automate の責任とする。

### 3.5 取得の量

ハブの issue のコメントは増え続けるが、notes の API には更新日時での絞り込みが無いので、毎回全ページを取る（100 件で 1 要求、5 分ごと）。
数千件までは問題にならない。超えたら新しいハブの issue に替える（`GITLAB_HUB_ISSUE_URL` を変えると取得元の指紋が変わり、基準を取り直す）。
`gitlab-hub-issue` は 1 要求で、`get` を呼ぶワークフローが無ければ問い合わせない。

### 3.6 issue の URL から API の URL を作る

`https://host/<プロジェクトのパス>(/-)/(issues|work_items)/<番号>` を `https://host/api/v4/projects/<パスを %2F でまとめたもの>/issues/<番号>` にする。
ほかの形は止める（`call`・`get` が設定の誤りとして終わる）。GitLab を `https://host/gitlab/` の下に置いていると、`gitlab` がグループか
置き場所かを URL から見分けられないので、`/api/v4/` を含む URL はそのまま使い、README で API の URL を書くよう案内する。
問い合わせ先は URL から決まるので、接続設定の `url` は使わない（トークンだけ。ほかの GitLab のエージェントと同じ接続でよい）。

## 4. インストーラ

ほかのエージェントと同じ決まりで置く。ワークフローは `gitlab_hub.py` のコマンドも change-watch から呼ぶので、Windows での置き換えは
ほかのステップと同じ（`command` を Python に、`args` の先頭を `.pyz` に）。`request` が動かすコマンドの `- "python3"` は、
`install.py` の `render` がインストーラを動かした Python に置き換える。`python3 gitlab_hub.py <コマンド>` と直接呼ぶ口も残す
（既に置いたワークフローは上書きしないため）。

## 5. 試験

`tools/change-watch/agents/gitlab-issue-hub-agent/test/test_gitlab_hub.py`。GitLab は手元の HTTP サーバで偽装し（notes の GET・POST、issue の
GET・PUT。プロジェクトは `group/hub` で `%2F` になること）、設定の例とワークフローの例をそのまま本物の change-watch と `test/dagrun.py` で回す。

- `gitlab_issue_api`: issue の URL（下のグループ・`#note_…`・旧形式の `/issues/`・`work_items`）と API の URL、ほかの URL は止める
- `description`・`api-url`: 説明の JSON（`note` と同じ渡し方・空なら出さない）、指した issue かハブの API の URL、issue でない URL は止める
- `fields`: `名前=値` を JSON にする（引用符を含む題・`=` を含む値、`description` のクイックアクション）、形の誤りは止める
- `note`: 本文をそのまま、クイックアクションを効かなくする、`-` で始まる本文・ファイル・コマンドの出力。空なら何も出さない。
  コマンドの失敗は何も出さずに終了コードを返す。本文の渡し方は 1 つだけ
- 例のワークフロー: 初回は今あるコメントを覚えるだけ。条件に合う新しいコメントにだけ 1 回コメントし、ふつうのコメント・記録・自分の
  コメントには反応しない。同じトークンで書かれたものも条件だけで決まる。コマンドが失敗したらコメントせず、やり直さない。
  書き込みのワークフローがコメントを足し、空なら書かない。issue そのものの変化（ラベル）を `gitlab-hub-issue` で受け取り、`call PUT` で変えられる。get と post の例が、条件に合う変化でコメントを足して題を変え、自分の変更では起きず、二度は書かない。整理が動く
- `test_install.py`: 4 つのエージェントが同じ決まりで置かれる。Windows でワークフローの Python が置き換わる
- 4 つのワークフローの例を Dagu v2.25.2 の `internal/cmn/schema/dag.schema.json` で検証し、エラー 0 件を確認した（2026-11-09、手元）

## 6. 限界

- ポーリングなので、変化から反応まで最大で `schedule` の間隔＋ずらし（例では約 6 分）と、GitLab の通知メールの遅れがかかる
- 1 回の起動で 1 件ずつなので、一度にたくさん届くと 1 分に 1 件ずつ処理する

## 7. 依頼の書式と種別で振り分ける例

### 7.1 依頼と返信を対応づける

1 件の依頼を 1 コメントにする。1 行目は `【依頼】[種別] 件名`。タグは ASCII の小文字で始まり、小文字・数字・ハイフンで 32 文字まで。
コメントの先頭に合図を置く。BOM と改行の違い、前後の空白は書式を調べるときにそろえる。

2 行目から最初の空行までは `項目名: 値`。全角のコロンと箇条書きの行頭にも対応する。同名の項目があれば最初の値を残して違反にし、
未知の項目は独自の項目として受け取る。空行のあとは自由な文。種別ごとの必須項目は次のとおり。

| 種別 | 必須の項目 | 自由な文 |
|---|---|---|
| `report` | 対象・期間 | 任意 |
| `inquiry` | 差出人・受信日時 | 必須 |
| `account` | 対象者・操作（追加・変更・削除）・期日（年月日を `YYYY-MM-DD` の形で） | 任意 |

返信と差し戻しには、読めた種別と件名、元のコメントの `note_<ID>` と書いた人、受け取った受付番号があればその値を載せる。
`note_<ID>` は GitLab のコメントへのリンクの末尾や、コメント作成時の応答の `id` と突き合わせられる。
元の依頼を編集すると `changed` になるので、依頼を直すときは新しいコメントを書く。

### 7.2 書式を調べた結果で分ける

`check-request` は設定を読まず、書式を調べた結果を JSON で 1 行返す。Dagu の分岐は `route` だけで決める。
規約どおりなら種別名、違反があれば `invalid`、依頼の合図が無ければ `none`。種別の無い旧形式は、振り分け例では `invalid` として差し戻す。
書式違反も終了コード 0 で返し、コマンドを動かせない場合や入力の誤りは終了コードで区別する。
自由な文は開始行番号だけを出し、本文は分岐先がコメントの変化から読む。

`--kinds-file` は組み込みの種別を追加・置換する。判定結果との衝突を避けるため、`invalid` と `none` は予約する。
追加した種別を受け付けるには、ワークフローにも分岐を足す。

### 7.3 分岐ごとに返信を持つ

入口の条件は、新しく付いた（`appeared`）、人が書いた（`system=false`）コメントの 2 つ。本文の書式は `check-request` が表記をそろえて判定する。
分岐の条件は種別名と `invalid` の完全一致で書く。`none` なら返信せず、`commit` で見終えたと記録する。
Dagu で複数の分岐が同じ出力名を共有できるかは確認できていないため、分岐ごとに出力名と返信ステップを分ける。
返信ステップはその分岐の出力だけを調べる。最後に `commit` し、条件に合わなかったコメントや処理に失敗したコメントも見終えたと記録する。

振り分け例は `examples/` に置き、既存のワークフローから切り替えて使う。別の consumer `hub-route` を使うので、初回は今あるコメントを覚えるだけ。
