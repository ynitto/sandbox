# gitlab-issue-hub-agent

**issue の URL を 1 つ決めて、その issue をハブ**にし、メール・Power Automate とやりとりする。このエージェントが受け持つのは、
ハブの issue の**変化を返すこと**と、**issue を変えられること**だけ。どの変化をどう扱うか・何を書くかは Dagu のワークフローに書く。

- **頼まれる**: メールを受けた Power Automate が、ハブの issue にコメントを書く（や、issue を変える）。ワークフローの最初の `get` が、
  前回見てから変わったもの（新しく付いたコメントとその中身、または issue そのものの変化）を受け取る。GitLab に問い合わせるか、
  前回の取得結果で答えるかは change-watch が決める。受け取ったものに何をするかはワークフローの条件で決める
- **頼む**: ワークフローから、ハブの issue にコメントを足す・issue を更新する（ラベル・説明・状態など）。GitLab がこの issue を
  見ている人へ変わったことをメールで知らせ、それを受けて何をするかは Power Automate が決める

起動と条件の分岐は [Dagu](https://github.com/dagu-org/dagu) のワークフローが、GitLab への問い合わせ（いつ取得するか・まとめる・失敗の待ち）と
変化を返すこと、issue への書き込みは [change-watch](../../README.md) が受け持つ（change-watch の README の「条件をワークフローで判定する」）。

```
 Power Automate（メールを受けたら）── ハブの issue にコメント
                                              │
 Dagu: change-watch-issue-hub（毎分起動。1 回の起動で 1 件。GitLab への問い合わせは 5 分ごと）   ← 例。条件と処理は自分のものに直す
   get       ハブの issue に新しく付いたコメントを 1 件受け取る
   request   条件（例: 1 行目が【依頼】）に合えば → 決めたコマンドを動かす               ← 条件（Dagu の preconditions）
   reply     コマンドの出力をコメントする（change-watch call）
   commit    見終えたと記録する
                                              │
 GitLab ── この issue を見ている人へ、変化をメールで知らせる ── Power Automate（メールを受けたら）…

 Dagu: change-watch-issue-hub-update（呼ばれたとき）   ← 例。何を書くかはワークフローで決める
   compose → comment   COMMENT をコメントとして足す
```

## 入れ方

1. インストーラで入れる。change-watch と、このエージェントのプログラム（`gitlab_hub.py`）・設定の雛形・Dagu のワークフローを、
   どのエージェントも同じ決まりの場所に置く（下の表。決まりは [change-watch の README](../../README.md) の「エージェントを入れる」）。
   ワークフローのパスは、この PC のものにして置く。既にある設定とワークフローは上書きしないので、入れ直すとプログラムだけが新しくなる。

   ```bash
   python3 tools/change-watch/install.py install --agent gitlab-issue-hub-agent
   ```

   | 置かれるもの | 場所 |
   |---|---|
   | プログラム | `~/.local/share/change-watch/agents/gitlab-issue-hub-agent/`（Windows は `%LOCALAPPDATA%\Programs\change-watch\agents\gitlab-issue-hub-agent\`） |
   | 設定（`change-watch.yaml`・`gitlab-issue-hub-agent.env`） | `~/.config/change-watch/gitlab-issue-hub-agent/` |
   | Dagu のワークフロー（`change-watch-issue-hub.yaml`・`change-watch-issue-hub-update.yaml`・整理の `change-watch-gitlab-issue-hub-agent-maintenance.yaml`） | `~/.config/dagu/dags/` |
   | 状態 | `~/.local/state/change-watch/gitlab-issue-hub-agent/` |

2. ハブにする issue を作る（例:「メール連携のハブ」）。その issue の URL（ブラウザで開いたときの
   `https://gitlab.example.com/group/project/-/issues/42`）とトークンを
   `~/.config/change-watch/gitlab-issue-hub-agent/gitlab-issue-hub-agent.env` に書く。ハブを替えるときは URL だけを書き換える。
   トークンには `api` が要る（issue を変えるため）。**トークンの持ち主が、コメントと issue の変更の書き手になる**。GitLab は自分の書いたコメントを自分にはメールしないので、
   メールを受けたい人（と Power Automate が見るメールボックスの持ち主）は、トークンの持ち主とは別の人にして、issue の「通知」をオンにする。

3. `~/.config/dagu/dags/change-watch-issue-hub.yaml` の `request` の条件（`preconditions`）とコマンドを自分のものにする。
   例のままなら `examples/on_request.py` を `/home/you/work/hub/on_request.py` に置いて直す（下の「頼まれる: 変化を受け取る」）。

4. 接続設定（例: `~/.github/connections.yaml`）に GitLab を書く。値は `gitlab-issue-hub-agent.env` から取るので、トークンを 2 か所に書かなくてよい。
   ほかの GitLab のエージェントと同じものでよい（問い合わせ先は issue の URL から決めるので、`url` は使わない）。

   ```yaml
   gitlab:
     - label: default
       url: ${GITLAB_URL}
       token: ${GITLAB_TOKEN}
   ```

5. 環境変数を読み込んで、一度手で取得して基準を取る（今あるコメントを覚えるだけで、何もしない）。そのあと Dagu のスケジューラを動かす
   （`dagu start-all` など）。Dagu の設定（`config.yaml`）に足すものは無い。

   ```bash
   set -a; . ~/.config/change-watch/gitlab-issue-hub-agent/gitlab-issue-hub-agent.env; set +a
   ```

   ```bash
   change-watch --config ~/.config/change-watch/gitlab-issue-hub-agent/change-watch.yaml --connections ~/.github/connections.yaml get --source gitlab-hub-notes --consumer hub --no-jitter
   ```

6. issue を一度変えてみる。ハブの issue にコメントが付き、通知をオンにした人にメールが届く。

   ```bash
   dagu start change-watch-issue-hub-update -- COMMENT="試しのコメントです"
   ```

## 頼まれる: 変化を受け取る

ワークフローの `get` は、取得元を選んで変化を 1 件受け取る（`output: CHANGE`）。`${CHANGE.kind}` は `appeared`（新しく現れた）・`changed`
（`--watch` の項目が変わった）・`removed`、`${CHANGE.record.*}` は今の中身、`${CHANGE.before.*}` は前回 commit したときの中身。

| 取得元 | 1 件のレコード | 例の `get` |
|---|---|---|
| `gitlab-hub-notes` | issue に付いたコメント（`body`・`author.username`・`system` など） | `--watch body`（書き直しも変化にする） |
| `gitlab-hub-issue` | issue そのもの（`title`・`description`・`labels`・`state` など） | `--watch labels --watch description --watch state` |

例のワークフロー（`change-watch-issue-hub.yaml`）はコメントを受け取り、`request` の `preconditions` で「新しく付いた、人が書いた
（`system` でない）、1 行目が【依頼】のコメント」に絞って、`/home/you/work/hub/on_request.py` を動かす。条件もコマンドも例なので、
自分のものに直す。issue そのものの変化を見るなら、`get` を `--source gitlab-hub-issue` にしたワークフローを別に置く
（consumer の名前は `commit` と同じにし、ほかのワークフローと分ける）。

動かすコマンドは、`request` の `note` の `--` のあとに書く。引数に `${CHANGE}`（受け取った変化の JSON）を渡し、**標準出力に書いたものが
コメントの本文になる**。例の `examples/on_request.py` は「【返信】件名」で始まる本文を出す。

- 何も出力しなければコメントしない
- 0 以外で終わるとコメントせず、Dagu の実行履歴に失敗として残る。やり直しはしない（`commit` はする）
- コマンドの出力が条件に当たると、次の起動で自分のコメントにもう一度反応する。例では、出力を【依頼】で始めなければ当たらない
- 書き込む文の行頭の `/`（`/close` などのクイックアクション）は `\/` にして効かなくする。表示は `/` のまま

## 依頼の書き方

種別で振り分けるときは、1 つのコメントに 1 つの依頼を書く。

```text
【依頼】[report] 10 月の売上の集計
対象: 東日本の売上
期間: 2026-10
期限: 2026-11-30

先月と同じ形でお願いします。
```

- 1 行目は `【依頼】[種別] 件名`。先頭に空行・空白・挨拶は置かない。種別タグは `【依頼】` の直後に 1 つ書く。
  合図とタグの間には半角・全角の空白を置ける。種別は半角の小文字で始め、小文字・数字・ハイフンで 32 文字まで。件名は必ず書く。
- 2 行目からは `項目名: 値` を 1 行に 1 つずつ書く。区切りは `:` でも `：` でもよく、行頭に `- ` や `* ` を付けてもよい。
  同じ項目は 1 回だけ書く。必須の項目には値を入れる。表に無い独自の項目も書ける。
- 項目のあとに空行を 1 つ入れると、そのあとを自由な文として読める。項目が無ければ 2 行目を空ける。

| 種別 | 用途 | 必須の項目 | 任意の項目 | 自由な文 |
|---|---|---|---|---|
| `report` | 集計・報告の作成 | 対象・期間 | 期限（`2026-11-30` の形）・提出先 | 任意 |
| `inquiry` | 問い合わせへの回答案 | 差出人・受信日時 | 期限（`2026-11-30` の形） | 必須 |
| `account` | アカウント・権限の申請 | 対象者・操作（追加・変更・削除のどれか）・期日（`2026-12-01` の形） | 権限 | 任意 |

差出人・受信日時・受付番号はどの種別にも書ける。受付番号は返信にも写る。

```text
【返信】[report] 10 月の売上の集計
元の依頼: note_1003（alice さん）
受付番号: M20261119093000

集計・報告の依頼を受け付けました。
```

書式が違う依頼には `【差し戻し】[種別] 件名` で始まるコメントが付き、空行のあとに直すところが並ぶ。種別が読めなければタグは付かない。
種別タグの無い `【依頼】件名` も、振り分け例では差し戻す。既存の `change-watch-issue-hub.yaml` では今までどおり受け付ける。

- 依頼を直すときは、新しいコメントに書き直す。元のコメントを編集しても新しい依頼にはならない。
- GitLab に直接書くときは行頭に `/` を書かない。`/close` などが操作として効く。メールを写すフローでは行頭の `/` を `\/` にしてから送る。
- メールを写すときは、GitLab の通知を避けるため、本文に `@名前` を書かない。
- 返信など、依頼でないコメントを `【依頼】` で始めない。

## 種別で振り分ける例（`examples/change-watch-issue-hub-route.yaml.example`）

`change-watch-issue-hub.yaml` の代わりに使う。二重に返信しないよう、切り替えるときは元のワークフローを止める。
`examples/change-watch-issue-hub-route.yaml.example` を `~/.config/dagu/dags/change-watch-issue-hub-route.yaml` にコピーし、`/home/you` を自分の場所に直す。
`examples/` の `hub_reply.py`・`on_report.py`・`on_inquiry.py`・`on_account.py`・`on_invalid.py` はすべて同じ作業フォルダに置く。
これらの例と Power Automate のテンプレートは手でコピーする。

| ステップ | 呼ぶもの | すること |
|---|---|---|
| `get` | `change-watch get --source gitlab-hub-notes --consumer hub-route --watch body --one` | コメントの変化を 1 件受け取る。初回は今あるコメントを覚える |
| `check` | `change-watch check-request --change=${CHANGE}` | 新しく付いた（`appeared`）、人が書いた（`system=false`）コメントを調べ、`route` に振り分け先を出す |
| `report` → `reply_report` | `note` と `call POST` | 集計・報告の依頼への返信を作って書く |
| `inquiry` → `reply_inquiry` | `note` と `call POST` | 問い合わせへの返信を作って書く |
| `account` → `reply_account` | `note` と `call POST` | アカウント・権限の申請への返信を作って書く |
| `invalid` → `reply_invalid` | `note` と `call POST` | 書式が違う依頼への差し戻しを作って書く |
| `commit` | `change-watch commit --source gitlab-hub-notes --consumer hub-route ${CHANGE.key}` | 見終えたと記録する |

各分岐の条件は `${CHECK.route}` と行き先の完全一致。分岐のスクリプトは同じ種別の依頼を受け付けたと返す例なので、自分の処理に替えて使う。
何も出さなければコメントせず、スクリプトが失敗したときは Dagu の履歴に残して見終えたと記録する。

種別を足すときは、`examples/hub-request-kinds.json.example` をコピーして直し、`check` に `--kinds-file=<ファイル>` を渡す。
同じ名前の種別は定義ごと置き換わる。`invalid` と `none` は判定結果のために予約している。
種別を足したら、条件とコマンドを書くステップと、その出力をコメントするステップの 2 つをワークフローにも足す。
分岐を足すまでは、追加した種別の依頼が書式チェックを通っても返信せずに見終えたと記録する。


## 頼む: issue を変える

例の `change-watch-issue-hub-update` は、渡した `COMMENT` をコメントとして足す。ほかのワークフローからは、そのステップでこのコマンドを動かす。

```bash
dagu start change-watch-issue-hub-update -- COMMENT="夜間バッチが終わりました"
```

長いコメントは、ファイル（UTF-8）に書いて `COMMENT_FILE` で渡す。コマンド行には長さの上限があり（Windows は 8191 文字）、
長い文を `COMMENT` に入れると「command line is too long」で止まる。

```bash
dagu start change-watch-issue-hub-update -- COMMENT_FILE="/home/you/work/hub/comment.txt"
```

例のワークフローは、本文を `~/.config/change-watch/gitlab-issue-hub-agent/` のファイルに書いてから送る（本文をコマンド行に載せない）。
自分でワークフローを書くときも、`note`・`description`・`fields` に `--out=<ファイル>` を付け、`call` に `--json-file=<ファイル>` で渡す。

ラベル・題・説明・状態などを変えるなら、ワークフローに `change-watch --plugin gitlab_hub fields`（更新の JSON を `名前=値` から作る）と
`change-watch call PUT` のステップを書く（項目は GitLab の issue の編集のもの: `title`・`add_labels`・`remove_labels`・`description`・`state_event` など）。
何を書くかはワークフローで決める。

## CLI で get と post をする例（`examples/change-watch-issue-hub-get-post.yaml.example`）

Dagu のワークフローから change-watch の CLI を呼んで、ハブの issue を get し、post（コメントを足す・題を変える）する一通りの例。
インストーラは置かないので、`~/.config/dagu/dags/` にコピーして `/home/you` を自分の場所に直す。

| ステップ | 呼ぶもの | すること |
|---|---|---|
| `get` | `change-watch get --source gitlab-hub-issue --consumer get-post --watch title --watch labels --watch description --one` | issue の変化と中身を 1 件受け取る（`${CHANGE.record.*}`・`${CHANGE.before.*}`） |
| `compose` → `comment` | `change-watch note --text=…` → `change-watch call POST "<issue の API>/notes" --json "${NOTE}"` | 条件（例: ラベル「完了」が付き、題がまだ【完了】で始まらない）に合えばコメントを足す |
| `fields` → `retitle` | `change-watch fields "title=【完了】${CHANGE.record.title}"` → `change-watch call PUT "<issue の API>" --json "${UPDATE}"` | 同じ条件で題を変える |
| `commit` | `change-watch commit --source gitlab-hub-issue --consumer get-post --refresh ${CHANGE.key}` | 見終えたと記録する。自分が変えたあとの姿を基準にするので、自分の変更では次に起きない |

どのステップも change-watch を `--plugin gitlab_hub` を付けて呼ぶ（下の「このエージェントが足すコマンド」。`<issue の API>` は `{{ env.GITLAB_HUB_ISSUE_URL | gitlab_issue_api }}` と書き、
change-watch が issue の URL から作る）。コメントを見るなら `get` を `--source gitlab-hub-notes` にする。

コメントを足すと GitLab がメールで知らせるので、それを受けて何をするかは Power Automate のフローで決める（題や説明を変えただけでは届かない）。

## 決めた issue の説明を書き換える例（`examples/change-watch-issue-hub-description.yaml.example`）

issue の説明を、渡した文に丸ごと入れ替える。インストーラは置かないので、`~/.config/dagu/dags/` にコピーして `/home/you` を自分の場所に直す。
`ISSUE_URL` を渡さなければハブの issue、渡せばその issue（同じ GitLab の、トークンで変えられるもの）を書き換える。`DESCRIPTION` が空なら何もしない。

```bash
dagu start change-watch-issue-hub-description -- DESCRIPTION="夜間バッチが終わりました"
```

```bash
dagu start change-watch-issue-hub-description -- ISSUE_URL="https://gitlab.example.com/group/project/-/issues/7" DESCRIPTION="夜間バッチが終わりました"
```

| ステップ | 呼ぶもの | すること |
|---|---|---|
| `compose` | `change-watch description --text=…` | 説明を `call` に渡す形にする。何行もある説明は `--text-file=<ファイル>` か `-- <コマンド> [引数...]`（標準出力が説明になる）で渡す |
| `target` | `change-watch api-url --url=…` | 書き換える issue を決める（URL が空ならハブの issue） |
| `update` | `change-watch call PUT "${ISSUE_API}" --json "${UPDATE}"` | issue を更新する |
| `note` → `comment` | `change-watch note --text=…` → `change-watch call POST "${ISSUE_API}/notes" --json "${NOTE}"` | `COMMENT` を渡したときだけ、同じ issue にコメントを足す |

説明だけを書き換えても、GitLab はメールで知らせない。知らせたいときは `COMMENT` も渡す。

```bash
dagu start change-watch-issue-hub-description -- DESCRIPTION="夜間バッチが終わりました" COMMENT="説明を更新しました"
```

長い説明は、引数ではなくワークフローの `compose` を `--text-file=<ファイル>` にして渡す（コマンド行には長さの上限がある）。
前の説明に足したいときは、前の説明を含めた全文を渡す（ハブの issue なら、`get --source gitlab-hub-issue` で受け取った `${CHANGE.record.description}` が今の説明）。
書き込む文の行頭の `/` は、コメントと同じく `\/` にして効かなくする。

## 知らせを新しい issue にして届ける例（`examples/change-watch-issue-hub-notice.yaml.example`）

ハブの issue にコメントを足すと、通知メールに前のコメントまで入ることがあり、そのまま Teams に流せない。
この例は知らせを 1 件ずつ新しい issue にする。新しい issue の通知メールには、その issue の題と説明しか入らない。
インストーラは置かないので、`~/.config/dagu/dags/` にコピーして `/home/you` を自分の場所に直す。

```bash
dagu start change-watch-issue-hub-notice -- TITLE="夜間バッチ" TEXT="終わりました"
dagu start change-watch-issue-hub-notice -- TITLE="夜間バッチ" TEXT_FILE="C:/work/hub/notice.txt"
```

1. 知らせ専用のプロジェクトを作り、その URL を `.env` の `GITLAB_NOTICE_PROJECT_URL` に書く（例 `https://gitlab.example.com/group/notices`）。
   トークンの持ち主は、そのプロジェクトで issue を立てられる役割（Reporter 以上）にする。
2. メールを受ける人（Power Automate が見るメールボックスの持ち主）は、そのプロジェクトの通知を「カスタム」にし、「新しい issue」だけを選ぶ。
   ハブの issue と同じく、トークンの持ち主とは別の人にする。
3. Power Automate では、このプロジェクトからの「新しい issue」のメールを Teams に流す。件名に題が、本文に説明が入る。

| ステップ | 呼ぶもの | すること |
|---|---|---|
| `compose` | `change-watch issue --title=… --labels=… --text=… --text-file=… --out=<ファイル>` | 題・本文・ラベルを JSON にしてファイルに書く。題が空なら本文の 1 行目にする |
| `create` | `change-watch call POST "<プロジェクトの API>/issues" --json-file=<ファイル>` | issue を立てる。GitLab が「新しい issue」のメールを送る |
| `close` | `change-watch call PUT "<プロジェクトの API>/issues/<番号>"` | 立てた issue をすぐ閉じる（`CLOSE=no` なら開いたまま） |

本文は `TEXT` か、長ければファイルにして `TEXT_FILE` で渡す（Shift_JIS・UTF-16 のファイルも読める）。どちらも空なら何もしない。
閉じた issue は一覧に出ず、通知を「新しい issue」だけにしていれば、閉じたときのメールも届かない。

## 知らせの issue を残し、決めた日数で閉じる例（`examples/change-watch-issue-hub-notice-expire.yaml.example`）

上の例は立てた issue をすぐ閉じる。こちらは開いたまま残し、`KEEP_DAYS` 日（既定 7）たったものを閉じる（`MODE=delete` なら消す）。
知らせを届けるのと、古い issue を片付けるのを 1 本のワークフローで行う。

```bash
dagu start change-watch-issue-hub-notice-expire -- TITLE="夜間バッチ" TEXT="終わりました"
```

1. 上の例の 1〜3 と同じく、知らせ専用のプロジェクトを作って `.env` の `GITLAB_NOTICE_PROJECT_URL` に書き、受け手の通知を「新しい issue」だけにする。
   このプロジェクトの開いている issue はすべて片付けの対象になるので、ほかの issue を立てない。
2. `examples/change-watch-notice.yaml.example` を `~/.config/change-watch/gitlab-issue-hub-agent/change-watch-notice.yaml` にコピーする
   （知らせ用プロジェクトの issue を見る設定。ハブの設定とは別のファイルにする）。
3. ワークフローを `~/.config/dagu/dags/` にコピーして `/home/you` を自分の場所に直す。

5 分ごとにも動き、本文が無ければ issue を立てずに片付けだけをする。片付けは 1 回の起動で、開いている issue を古い順に 1 件見る。
初回は今ある issue も順に見る。

| ステップ | 呼ぶもの | すること |
|---|---|---|
| `compose` → `create` | `change-watch issue … --out=<ファイル>` → `change-watch call POST "<プロジェクトの API>/issues" --json-file=<ファイル>` | 本文があれば issue を立てる |
| `get` | `change-watch get --source gitlab-notice-issues --consumer notice-expire --initial emit --watch state --one` | 開いている issue を古い順に 1 件受け取る |
| `check` | `change-watch check-old-issue --kind=${CHANGE.kind} --state=${CHANGE.record.state} --created-at=${CHANGE.record.created_at} --days=…` | 期限が来た（`expire`）・まだ待つ（`wait`）・何もしない（`keep`）を決める |
| `close` / `delete` | `change-watch call PUT …/issues/<番号>`（閉じる）・`change-watch call DELETE …/issues/<番号>`（消す） | `expire` のとき、`MODE` に従って閉じるか消す |
| `commit` | `change-watch commit --source gitlab-notice-issues --consumer notice-expire <番号>` | 片付けたか何もしないときに見終えたと記録する。`wait` なら記録せず、次の起動でもう一度見る |

閉じた issue は一覧に出ず、通知を「新しい issue」だけにしていれば閉じたときのメールも届かない。
消すにはトークンの持ち主がそのプロジェクトの Owner である必要がある。消した issue は戻せない。
`KEEP_DAYS` は 1 以上にする（立ててすぐ閉じると、メールが送られる前に閉じてしまうことがある）。

## 溜まった古いコメントを消す例（`examples/change-watch-issue-hub-prune.yaml.example`）

メールで知らせるためにコメントを足し続けると、issue にコメントが溜まる。メールは送り終えているので、古いコメントは消してよい。
インストーラは置かないので、`~/.config/dagu/dags/` にコピーして `/home/you` を自分の場所に、`AUTHOR` をトークンの持ち主のユーザー名に直す。

消すのは、`AUTHOR` が書いた、`KEEP_DAYS` 日（既定 7）より古いコメントだけ。ほかの人のコメントと、ラベルの変更などの記録は消さない。
付けてすぐ消すと GitLab がメールを送る前に無くなることがあるので、`KEEP_DAYS` は 1 以上にする。
5 分ごとに起動し、1 回の起動でコメントを古い順に 1 件見る（初回は今あるコメントも順に見る）。

| ステップ | 呼ぶもの | すること |
|---|---|---|
| `get` | `change-watch get --source gitlab-hub-notes --consumer hub-prune --initial emit --watch body --one` | まだ見終えていないコメントを、古い順に 1 件受け取る |
| `check` | `change-watch check-old-note --kind=${CHANGE.kind} --system=… --writer=… --created-at=… --author=… --days=…` | 消す（`delete`）・まだ待つ（`wait`）・消さない（`keep`）を決める |
| `delete` | `change-watch call DELETE "<issue の API>/notes/${CHANGE.key}"` | `delete` のときだけ、そのコメントを消す |
| `commit` | `change-watch commit --source gitlab-hub-notes --consumer hub-prune ${CHANGE.key}` | `delete`・`keep` なら見終えたと記録する。`wait` なら記録せず、次の起動でもう一度見る |

トークンの持ち主が自分のコメントを消すので、トークンに追加の権限は要らない。消せなかったときは記録せず、次の起動でやり直す。
説明を書き換えたときに GitLab が残す「説明を変更しました」の記録は、API では消せない。

## Power Automate とつなぐ

**メール → ハブの issue（頼まれる）**。Power Automate のフローを「新しいメールが届いたとき」で始め、件名や差出人で絞って、次のどちらかで
ハブの issue にコメントを書く（本文の形は、ワークフローの条件と合わせて決める）。

- HTTP のアクション（プレミアム）で `POST {GitLab の URL}/api/v4/projects/{プロジェクトの ID}/issues/{issue の番号}/notes` を送る。
  ヘッダーに `PRIVATE-TOKEN: <トークン>`、本文に `{"body": "【依頼】<件名>\n<本文>"}`（例のワークフローの条件に合わせた形）
- GitLab で「メールで返信」が使えるなら、ハブの issue の通知メールの返信先（Reply-To）へメールを送る（本文がコメントになる）

種別で振り分けるときは「依頼の書き方」の形にし、[Power Automate の組み方](power-automate/README.md) に沿って本文を作る。

**ハブの issue → メール（頼む）**。GitLab がこの issue を見ている人へ、変化をメールで知らせる。届くかどうかは変化の種類で決まる。

| 変化 | メール |
|---|---|
| コメントが付く | 届く |
| 閉じる・開き直す | 届く |
| 担当者・マイルストーン・期日が変わる | 届く |
| ラベルが付く | そのラベルを購読している人にだけ届く |
| 題・説明が変わる | 届かない（説明に新しく `@名前` を書かれた人にだけ届く） |

確実に知らせたいときは、変えたあとにコメントを足す。メールに載るのはそのコメントの本文だけなので、Power Automate に渡したい内容はコメントに書く。
溜まったコメントは「溜まった古いコメントを消す例」で消せる。
Power Automate のフローをそのメールボックスの「新しいメールが届いたとき」で始め、件名・本文で何をするかを分ける。

## このエージェントが足すコマンド

書くものを作るコマンドも、取得・書き込みと同じ change-watch から呼ぶ（`--plugin gitlab_hub`。インストーラが置いたプログラムを
change-watch が探すので、パスは書かなくてよい）。設定は読まないので、`--config`・`--connections` は付けなくてよい。

```bash
change-watch --plugin gitlab_hub note --text="夜間バッチが終わりました"
```

| コマンド | 出すもの | 渡す先 |
|---|---|---|
| `note --text=<文>`・`note --text-file=<ファイル>`・`note -- <コマンド> [引数...]` | コメントの本文 | `call POST "<issue の API>/notes" --json` |
| `description`（渡し方は `note` と同じ） | issue の説明 | `call PUT "<issue の API>" --json` |
| `fields <名前>=<値> ...` | issue の更新（`title`・`add_labels`・`remove_labels`・`description`・`state_event` など） | `call PUT "<issue の API>" --json` |
| `check-request`（`note` と同じ渡し方か `--change=<get の JSON>`） | 書式を調べた結果 | ワークフローの `preconditions` |
| `issue --title=<題> [--labels=<ラベル>]`（本文の渡し方は `note` と同じ） | 新しい issue（題が空なら本文の 1 行目） | `call POST "<プロジェクトの API>/issues" --json-file` |
| `check-old-issue --kind=… --state=… --created-at=… [--days=7]` | その issue の期限が来たか（`expire`・`wait`・`keep`） | ワークフローの `preconditions` |
| `api-url [--url=<issue の URL>]` | その issue の API の URL（無ければハブの issue） | `call` の URL |
| `check-old-note --kind=… --system=… --writer=… --created-at=… --author=<ユーザー名> [--days=7]` | そのコメントを消してよいか（`delete`・`wait`・`keep`） | ワークフローの `preconditions` |

`note`・`description`・`fields` に `--out=<ファイル>` を付けると、JSON をそのファイルに書き、ファイルのパスだけを出す（`call` の `--json-file` に渡す）。
長い文は、こうしてコマンド行に載せずに渡す。

本文のファイルとコマンドの出力は UTF-8 で読み、読めなければ UTF-16（BOM つき）・Shift_JIS で読む（PowerShell の `Out-File` や
Windows のコマンドの出力もそのまま渡せる）。文字化けした文字（`\ufffd`）を含む本文は書かずに止まる。前のステップの出力を
`--text=${…}` で渡すと、Dagu が UTF-8 でない文字を渡す前に置き換えて失うので、ファイルか `-- <コマンド>` で渡す。

`check-old-issue`・`check-old-note` には、受け取った変化の項目を 1 つずつ渡す（`--kind=${CHANGE.kind}` など）。
`--change=${CHANGE}` でも渡せるが、受け取った JSON には issue の説明やコメントの本文が丸ごと入るので、長いと「command line is too long」で止まる。

`note`・`description` は、本文が空なら何も出さず、`--` のあとのコマンドが失敗したらその終了コードで終わる。

`check-request` は、調べられれば違反があっても JSON を 1 行出して 0 で終わる。渡し方やファイルの誤りは 2、本文を作るコマンドの失敗はその終了コードで終わる。

## 決めること

| 決めること | 書く場所 | 例 |
|---|---|---|
| ハブの issue | `gitlab-issue-hub-agent.env` の `GITLAB_HUB_ISSUE_URL` | `https://gitlab.example.com/group/project/-/issues/42` |
| どの変化に反応するか | `change-watch-issue-hub.yaml` の `request` の `preconditions` | `expected: "re:^【依頼】"` |
| 反応して動かすコマンド | `change-watch-issue-hub.yaml` の `request` の `--` のあと | `python3`・`/home/you/work/hub/on_request.py`・`${CHANGE}` |
| どの種別をどのコマンドに渡すか | 振り分け例の分岐 | `report` なら `on_report.py` |
| 種別を足す | 振り分け例の `check` と追加する分岐 | `--kinds-file=kinds.json` |
| 何を書くか | 書き込むワークフロー（`change-watch-issue-hub-update.yaml` など）の `call` | `COMMENT`・`--json '{"add_labels": "…"}'` |
| 問い合わせの間隔 | `change-watch.yaml` の取得元の `schedule` | `*/5 * * * *` |

GitLab を `https://host/gitlab/` のような下の場所に置いているときは、issue の URL からは API の場所を見分けられないので、
`GITLAB_HUB_ISSUE_URL` に API の URL（`https://host/gitlab/api/v4/projects/<ID>/issues/42`）を書く。

## 失敗したとき

GitLab に書き込めない一時的な失敗（終了コード 3）は、Dagu が 1 分おいて 3 回までやり直す。それでも書き込めないときや、権限が無いなどの
失敗は、Dagu の実行履歴に失敗として残る。`change-watch-issue-hub` は `commit` はする（そのコメントにはもう反応しない）。中身はそのステップのログで確かめる。

| ログ | 直すこと |
|---|---|
| `401` で失敗 | トークンと、その期限を確かめる |
| `403` で失敗 | トークンに `api` があり、持ち主がハブのプロジェクトでコメントできるかを確かめる |
| `404` で失敗 | `GITLAB_HUB_ISSUE_URL` が開ける issue の URL かを確かめる |

## 設定の置き場所

| ファイル | 書くこと |
|---|---|
| `~/.config/change-watch/gitlab-issue-hub-agent/gitlab-issue-hub-agent.env` | ハブの issue の URL・トークン |
| `~/.github/connections.yaml` | GitLab の接続（値は上の環境変数から取る） |
| `~/.config/change-watch/gitlab-issue-hub-agent/change-watch.yaml` | 問い合わせの間隔 |
| `~/.config/dagu/dags/change-watch-issue-hub.yaml` | 反応する変化の条件・動かすコマンド・再試行 |
| `~/.config/dagu/dags/change-watch-issue-hub-update.yaml` | issue に何を書くか |

## テスト

```bash
cd tools/change-watch/agents/gitlab-issue-hub-agent && python3 -m unittest discover -s test
```

設計: [`docs/designs/gitlab-issue-hub-agent-design.md`](../../../../docs/designs/gitlab-issue-hub-agent-design.md)
