# gitlab-issue-hub-agent 設計

URL で決めた 1 つの GitLab の issue を、メールと Power Automate をつなぐハブにする。頼まれる側では、issue の変化（新しく付いたコメントと
その中身）を返し、ワークフローが「【依頼】」のコメントで決めたコマンドを動かして「【返信】」でコメントする。頼む側では、ほかのワークフローから
issue を変える（コメントを足す・ラベルや説明や状態を更新する）だけで、変化を受けて何をするかは Power Automate が決める。メールの送受信は GitLab の通知メールと Power Automate に任せ、
このエージェントはメールに触らない。gitlab-issue-agent・redmine-ticket-agent と同じく Dagu と change-watch の上に組み、
change-watch には API へのアクセス（`get`・`commit`・`call`）だけを、条件は Dagu の `preconditions` に持たせる（change-watch の 0.6.0 の分担）。
実装は `tools/change-watch/agents/gitlab-issue-hub-agent/`、使い方は同フォルダの README。

## 1. 決めたこと

- ハブは issue の URL 1 つで決める（`GITLAB_HUB_ISSUE_URL`）。プラグインのフィルタ `gitlab_issue_api` が URL を API の URL にし、
  取得元の `request.url` とワークフローの `call` の URL（change-watch がテンプレートとして展開する）に書く。プロジェクトの ID や番号を別に持たない
- 頼む側は「issue を変えられる」ことだけを受け持つ。印（【通知】など）も付けず、渡されたコメントと更新をそのまま送る（2026-11-09、利用者の指示）

依頼の向き:

依頼は「メールを受けた Power Automate が issue にコメントを書き、エージェントがそれを読む」向きとした。逆向き（エージェントがメールを送る）は
GitLab の通知メールで足りる（issue を見ている人へ、コメントごとにメールが届く）。こうすると:

- エージェントは SMTP・Microsoft 365 の認証を持たない。GitLab のトークン 1 つで済み、change-watch の接続・要求の間隔の中に収まる
- Power Automate 側は「メールが届いたとき」の起動と、HTTP（または GitLab の「メールで返信」）の 1 アクションで組める
- やりとりが issue に残り、人も同じ場所で見られる

## 2. 位置づけと分担

| 受け持ち | どこ | 何を |
|---|---|---|
| ハブの issue のコメントの取得・新しく付いたものを返す・書き込み | change-watch | 取得元 `gitlab-hub-notes`（`GET /projects/:id/issues/:iid/notes`、page のページング、5 分ごと）、`get --consumer hub --watch body --one`、`commit`、`call POST .../notes` |
| 起動・条件の分岐・再試行・履歴 | Dagu | `change-watch-issue-hub`（毎分。get → request → reply → commit）、`change-watch-issue-hub-update`（params の COMMENT・UPDATE で呼ばれる。compose → comment → update）、整理 |
| GitLab に固有のこと | `gitlab_hub.py` | フィルタ `gitlab_issue_api`（プラグイン）、`note`（コメントの本文の JSON）・`reply`（依頼を渡してコマンドを動かし、出力を【返信】の本文の JSON にする）。HTTP は送らない |

`gitlab_hub.py` は API に触らず、`change-watch call --json` に渡す本文を作るだけにした。Dagu の引数に任意の文（改行・引用符を含む）を
JSON として埋め込むと壊れるため、JSON を作る小さなコマンドが要る。認証と要求の間隔は change-watch の `call` に一本化する。

## 3. 構成

```
Dagu: change-watch-issue-hub（毎分）
  （change-watch のステップはどれも --plugin gitlab_hub.py。URL は {{ env.GITLAB_HUB_ISSUE_URL | gitlab_issue_api }}）
  get      change-watch get --source gitlab-hub-notes --consumer hub --watch body --one   → CHANGE
  request  preconditions: CHANGE.kind = appeared・CHANGE.record.system = false・CHANGE.record.body が ^【依頼】
           python3 gitlab_hub.py reply "${CHANGE}" -- <依頼で動かすコマンド>                → NOTE（{"body": "【返信】…"}）
           continue_on: skipped・failure
  reply    preconditions: NOTE が ^{"body"
           change-watch call POST "<issue の API>/notes" --json "${NOTE}"
           retry_policy: 終了コード 3 だけ。continue_on: skipped・failure
  commit   preconditions: CHANGE.key があるとき。change-watch commit --consumer hub ${CHANGE.key}

Dagu: change-watch-issue-hub-update（予定なし。dagu start … -- COMMENT=… UPDATE='{…}'）
  compose  preconditions: COMMENT が空でない。python3 gitlab_hub.py note --text=${params.COMMENT}   → NOTE
  comment  preconditions: NOTE が ^{"body"。change-watch call POST "<issue の API>/notes" --json "${NOTE}"
  update   preconditions: UPDATE が { で始まる。change-watch call PUT "<issue の API>" --json "${params.UPDATE}"
```

### 3.1 自分の書き込みでループしない

返信と通知も同じ issue のコメントなので、次の取得で `appeared` として返る。書き手（トークンの持ち主）では見分けない。Power Automate が
同じトークンで依頼を書くことがあるため。代わりに 1 行目の印で見分け、`request` の条件を「1 行目が【依頼】」にする。返信は必ず【返信】で
始まり、`change-watch-issue-hub-update` で足すコメントも【依頼】で始めない限り、条件に当たらず `commit` だけされる。`commit --refresh` は要らない（コメントは 1 件ずつのレコードで、
自分の書き込みが既存のレコードを変えない）。

### 3.2 条件が飛ばされたステップの出力に頼らない

`reply` は `request` が動いたかを `${request.exit_code}` や `${NOTE}` が空かどうかでは見ず、`${NOTE}` が `{"body"` で始まるかで見る。
飛ばされたステップの `output` が定義されないとき、Dagu が `${NOTE}` を空にするか文字のまま残すかに依らず、どちらも当たらない。

### 3.3 失敗の扱い

- 依頼のコマンドが失敗したら、`gitlab_hub.py reply` は「処理できませんでした（終了コード N）。」の本文を出し、コマンドの終了コードで終わる。
  `continue_on.failure` で `reply` に進み、依頼した人にメールで失敗が届く。Dagu の履歴にも失敗として残る。標準エラーはログにだけ残し、
  コメント（メール）には書かない（秘密が混ざることがあるため）
- 依頼のコマンドは自動ではやり直さない（`request` に `retry_policy` を付けない）。副作用のあるコマンドを二重に動かさないため。
  やり直しはもう一度【依頼】を書く
- `call` は書き込みを自分で再試行しないので、`reply`・`post` に `retry_policy`（終了コード 3）を付ける

### 3.4 クイックアクション

GitLab はコメントの行頭の `/close` などを操作として実行する。メールの本文やコマンドの出力にそれが混ざると、トークンの持ち主の権限で
issue が閉じられる。`gitlab_hub.py` は書く本文の行頭の `/` を `\/` にする（Markdown の表示は `/` のまま）。Power Automate が書く依頼側は
Power Automate の責任とし、README に書く。

### 3.5 取得の量

ハブの issue のコメントは増え続けるが、notes の API には更新日時での絞り込みが無いので、毎回全ページを取る（100 件で 1 要求、5 分ごと）。
数千件までは問題にならない。超えたら新しいハブの issue に替える（`GITLAB_HUB_ISSUE_URL` を変えると取得元の指紋が変わり、基準を取り直す）。

### 3.6 issue の URL から API の URL を作る

`https://host/<プロジェクトのパス>(/-)/(issues|work_items)/<番号>` を `https://host/api/v4/projects/<パスを %2F でまとめたもの>/issues/<番号>` にする。
ほかの形は止める（`call`・`get` が設定の誤りとして終わる）。GitLab を `https://host/gitlab/` の下に置いていると、`gitlab` がグループか
置き場所かを URL から見分けられないので、`/api/v4/` を含む URL はそのまま使い、README で API の URL を書くよう案内する。
問い合わせ先は URL から決まるので、接続設定の `url` は使わない（トークンだけ。ほかの GitLab のエージェントと同じ接続でよい）。
URL を書き換えると取得元の指紋が変わり、consumer の基準を取り直す（前のハブのコメントを依頼として扱わない）。

## 4. インストーラ

ほかのエージェントと同じ決まりで置く。ワークフローの中で `gitlab_hub.py` を `command: "python3"` で呼ぶので、Windows では
`install.py` の `render` がワークフローの `command: "python3"`（と引数の `- "python3"`）をインストーラを動かした Python に置き換える。

## 5. 試験

`tools/change-watch/agents/gitlab-issue-hub-agent/test/test_gitlab_hub.py`。GitLab は手元の HTTP サーバで偽装し（notes の GET・POST）、
設定の例とワークフローの例をそのまま本物の change-watch と `test/dagrun.py` で回す。

- `gitlab_issue_api`: issue の URL（下のグループ・`#note_…`・旧形式の `/issues/`・`work_items`）と API の URL、ほかの URL は止める
- `note`: 本文をそのまま、クイックアクションを効かなくする、`-` で始まる本文・ファイルの本文
- `reply`: 依頼の中身が標準入力と `HUB_*` で届き、出力が【返信】の本文になる。失敗は詳細を書かずに知らせ、終了コードを返す。
  コマンドが無い・何も出力しないとき
- 例のワークフロー: 初回は今あるコメントを覚えるだけ。新しく付いた【依頼】にだけ 1 回返信し、ふつうのコメント・記録・自分の返信には
  返信しない。同じトークンで書かれた依頼にも返信する。失敗した依頼は失敗を返信し、やり直さない。更新のワークフローがコメントを足す・issue を更新する
  （その組み合わせ）、足したコメントは依頼にならない。整理が動く。偽の GitLab のプロジェクトは `group/hub`（`%2F` になること）
- `test_install.py`: 4 つのエージェントが同じ決まりで置かれる。Windows でワークフローの Python が置き換わる
- 3 つのワークフローの例を Dagu v2.25.2 の `internal/cmn/schema/dag.schema.json` で検証し、エラー 0 件を確認した（2026-11-09、手元）

## 6. 限界

- ポーリングなので、依頼から返信まで最大で `schedule` の間隔＋ずらし（例では約 6 分）と、GitLab の通知メールの遅れがかかる
- 依頼は 1 回の起動で 1 件ずつなので、一度にたくさん届くと 1 分に 1 件ずつ返信する
- メールの書式（件名・本文の取り出し）は Power Automate 側で決める。このエージェントは 1 行目と 2 行目から後に分けるだけ
