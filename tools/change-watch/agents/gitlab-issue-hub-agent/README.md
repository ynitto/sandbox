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

動かすコマンドは `gitlab_hub.py note --` のあとに書く。引数に `${CHANGE}`（受け取った変化の JSON）を渡し、**標準出力に書いたものが
コメントの本文になる**。例の `examples/on_request.py` は「【返信】件名」で始まる本文を出す。

- 何も出力しなければコメントしない
- 0 以外で終わるとコメントせず、Dagu の実行履歴に失敗として残る。やり直しはしない（`commit` はする）
- コマンドの出力が条件に当たると、次の起動で自分のコメントにもう一度反応する。例では、出力を【依頼】で始めなければ当たらない
- 書き込む文の行頭の `/`（`/close` などのクイックアクション）は `\/` にして効かなくする。表示は `/` のまま

## 頼む: issue を変える

例の `change-watch-issue-hub-update` は、渡した `COMMENT` をコメントとして足す。ほかのワークフローからは、そのステップでこのコマンドを動かす。

```bash
dagu start change-watch-issue-hub-update -- COMMENT="夜間バッチが終わりました"
```

ラベル・題・説明・状態などを変えるなら、ワークフローに `gitlab_hub.py fields`（更新の JSON を `名前=値` から作る）と
`change-watch call PUT` のステップを書く（項目は GitLab の issue の編集のもの: `title`・`add_labels`・`remove_labels`・`description`・`state_event` など）。
何を書くかはワークフローで決める。

## CLI で get と post をする例（`examples/change-watch-issue-hub-get-post.yaml.example`）

Dagu のワークフローから change-watch の CLI を呼んで、ハブの issue を get し、post（コメントを足す・題を変える）する一通りの例。
インストーラは置かないので、`~/.config/dagu/dags/` にコピーして `/home/you` を自分の場所に直す。

| ステップ | 呼ぶもの | すること |
|---|---|---|
| `get` | `change-watch get --source gitlab-hub-issue --consumer get-post --watch title --watch labels --watch description --one` | issue の変化と中身を 1 件受け取る（`${CHANGE.record.*}`・`${CHANGE.before.*}`） |
| `compose` → `comment` | `gitlab_hub.py note --text=…` → `change-watch call POST "<issue の API>/notes" --json "${NOTE}"` | 条件（例: ラベル「完了」が付き、題がまだ【完了】で始まらない）に合えばコメントを足す |
| `fields` → `retitle` | `gitlab_hub.py fields "title=【完了】${CHANGE.record.title}"` → `change-watch call PUT "<issue の API>" --json "${UPDATE}"` | 同じ条件で題を変える |
| `commit` | `change-watch commit --source gitlab-hub-issue --consumer get-post --refresh ${CHANGE.key}` | 見終えたと記録する。自分が変えたあとの姿を基準にするので、自分の変更では次に起きない |

change-watch のステップには、どれも `--plugin <gitlab_hub.py>` を付ける（`<issue の API>` は `{{ env.GITLAB_HUB_ISSUE_URL | gitlab_issue_api }}` と書き、
change-watch が issue の URL から作る）。コメントを見るなら `get` を `--source gitlab-hub-notes` にする。

変わったことは GitLab がメールで知らせるので、それを受けて何をするかは Power Automate のフローで決める。

## Power Automate とつなぐ

**メール → ハブの issue（頼まれる）**。Power Automate のフローを「新しいメールが届いたとき」で始め、件名や差出人で絞って、次のどちらかで
ハブの issue にコメントを書く（本文の形は、ワークフローの条件と合わせて決める）。

- HTTP のアクション（プレミアム）で `POST {GitLab の URL}/api/v4/projects/{プロジェクトの ID}/issues/{issue の番号}/notes` を送る。
  ヘッダーに `PRIVATE-TOKEN: <トークン>`、本文に `{"body": "【依頼】<件名>\n<本文>"}`（例のワークフローの条件に合わせた形）
- GitLab で「メールで返信」が使えるなら、ハブの issue の通知メールの返信先（Reply-To）へメールを送る（本文がコメントになる）

**ハブの issue → メール（頼む）**。GitLab がこの issue を見ている人へ、コメント・ラベル・状態などの変化をメールで知らせる。
Power Automate のフローをそのメールボックスの「新しいメールが届いたとき」で始め、件名・本文で何をするかを分ける。

## 決めること

| 決めること | 書く場所 | 例 |
|---|---|---|
| ハブの issue | `gitlab-issue-hub-agent.env` の `GITLAB_HUB_ISSUE_URL` | `https://gitlab.example.com/group/project/-/issues/42` |
| どの変化に反応するか | `change-watch-issue-hub.yaml` の `request` の `preconditions` | `expected: "re:^【依頼】"` |
| 反応して動かすコマンド | `change-watch-issue-hub.yaml` の `request` の `--` のあと | `python3`・`/home/you/work/hub/on_request.py`・`${CHANGE}` |
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
