# gitlab-issue-hub-agent

決めた **1 つの GitLab の issue をハブ**にして、メールとやりとりする。

- **メールで知らせる**: ほかのワークフローやコマンド行から、ハブの issue に「【通知】…」のコメントを書く。GitLab がこの issue を
  見ている人へメールで知らせるので、そのメールで Power Automate のフローを動かせる
- **メールで頼まれる**: メールを受けた Power Automate が、ハブの issue に「【依頼】…」のコメントを書く。このエージェントがそれを見つけて
  決めたコマンドを動かし、その出力を「【返信】…」のコメントで返す（これもメールで届く）

起動と条件の分岐は [Dagu](https://github.com/dagu-org/dagu) のワークフローが、GitLab への問い合わせ（いつ取得するか・まとめる・失敗の待ち）と
「前回見てから新しく付いたコメント」を返すこと、コメントの書き込みは [change-watch](../../README.md) が受け持つ
（change-watch の README の「条件をワークフローで判定する」）。どのコメントを依頼として扱うかは、ワークフローに書いてある。

```
 Power Automate（メールを受けたら）── ハブの issue に「【依頼】件名⏎本文」をコメント
                                              │
 Dagu: change-watch-issue-hub（毎分起動。1 回の起動で 1 件。GitLab への問い合わせは 5 分ごと）
   get       ハブの issue に新しく付いたコメントを 1 件受け取る
   request   1 行目が【依頼】なら → 決めたコマンドを動かす                              ← 条件（Dagu の preconditions）
   reply     コマンドの出力を「【返信】件名⏎出力」でコメントする（change-watch call）
   commit    見終えたと記録する
                                              │
 GitLab ── この issue を見ている人へ、コメントをメールで知らせる ── Power Automate（メールを受けたら）…

 Dagu: change-watch-issue-hub-notify（呼ばれたとき）
   compose → post   「【通知】TITLE⏎BODY」をコメントする
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
   | Dagu のワークフロー（`change-watch-issue-hub.yaml`・`change-watch-issue-hub-notify.yaml`・整理の `change-watch-gitlab-issue-hub-agent-maintenance.yaml`） | `~/.config/dagu/dags/` |
   | 状態 | `~/.local/state/change-watch/gitlab-issue-hub-agent/` |

2. ハブにする issue を作る（例:「メール連携のハブ」）。GitLab の URL・プロジェクトの ID・issue の番号・トークンを
   `~/.config/change-watch/gitlab-issue-hub-agent/gitlab-issue-hub-agent.env` に書く。トークンには `api` が要る（コメントを書くため）。
   **トークンの持ち主が、返信・通知のコメントの書き手になる**。GitLab は自分の書いたコメントを自分にはメールしないので、
   メールを受けたい人（と Power Automate が見るメールボックスの持ち主）は、トークンの持ち主とは別の人にして、issue の「通知」をオンにする。

3. 依頼で動かすコマンドを置く。例の `examples/on_request.py` を `/home/you/work/hub/on_request.py` に置いて直すか、
   `~/.config/dagu/dags/change-watch-issue-hub.yaml` の `request` の最後の引数を自分のコマンドに替える（下の「依頼で動かすコマンド」）。

4. 接続設定（例: `~/.github/connections.yaml`）に GitLab を書く。値は `gitlab-issue-hub-agent.env` から取るので、トークンを 2 か所に書かなくてよい。

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

6. 通知を一度送ってみる。ハブの issue に「【通知】試し」のコメントが付き、通知をオンにした人にメールが届く。

   ```bash
   dagu start change-watch-issue-hub-notify -- TITLE="試し" BODY="通知の試しです"
   ```

## コメントの形

1 行目の印で、依頼・返信・通知を見分ける。Power Automate も、メールの本文にあるこの 1 行目で見分けられる。

| 印 | 書くもの | 1 行目 | 2 行目から後 |
|---|---|---|---|
| 【依頼】 | Power Automate（や人） | 【依頼】件名 | 本文 |
| 【返信】 | このエージェント（`request` のコマンドの出力） | 【返信】依頼の件名 | コマンドの出力 |
| 【通知】 | このエージェント（`change-watch-issue-hub-notify`） | 【通知】TITLE | BODY |

- 依頼として扱うのは、新しく付いた、1 行目が【依頼】のコメントだけ。書き直した・前からあるコメント、ラベルの変更などの記録は見ない
- 返信と通知は【依頼】で始まらないので、エージェントが自分の書いたものにもう一度返信することはない。Power Automate が
  エージェントと同じトークンで書いても、1 行目が【依頼】なら依頼になる
- 書き込む文の行頭の `/`（`/close` などのクイックアクション）は `\/` にして効かなくする。表示は `/` のまま

## 依頼で動かすコマンド

`~/.config/dagu/dags/change-watch-issue-hub.yaml` の `request` で、`gitlab_hub.py reply "${CHANGE}" --` のあとに書く。
依頼のコメントは次の形で届き、**標準出力に書いたものが返信の本文になる**。

| 受け取り方 | 中身 |
|---|---|
| 標準入力 | 受け取ったコメントの変化の JSON（`record` が GitLab のコメント。`record.body`・`record.author.username` など） |
| `HUB_SUBJECT` | 1 行目から【依頼】を除いたもの（件名） |
| `HUB_TEXT` | 2 行目から後（本文） |
| `HUB_AUTHOR` | 書いた人のユーザー名 |
| `HUB_NOTE_ID` | コメントの ID |

- 何も出力しなければ返信しない
- 0 以外で終わると「処理できませんでした（終了コード N）。」と返信し、Dagu の実行履歴に失敗として残る。やり直しはしない
  （同じ依頼をもう一度するなら、もう一度【依頼】のコメントを書く）。エラーの中身はコメントに書かず、そのステップのログに残す
- 件名で処理を分けるなら、コマンドの中で `HUB_SUBJECT` を見て分ける。コマンドごと分けたいなら、`request` と `reply` の組を
  ステップごとに足し、`preconditions` の `expected` を `re:^【依頼】売上` のように変える（`commit` の `depends` を最後のステップにする）

## Power Automate とつなぐ

**メール → ハブの issue（依頼）**。Power Automate のフローを「新しいメールが届いたとき」で始め、件名や差出人で絞って、次のどちらかで
ハブの issue にコメントを書く。

- HTTP のアクション（プレミアム）で `POST {GitLab の URL}/api/v4/projects/{プロジェクトの ID}/issues/{issue の番号}/notes` を送る。
  ヘッダーに `PRIVATE-TOKEN: <トークン>`、本文に `{"body": "【依頼】<件名>\n<本文>"}`
- GitLab で「メールで返信」が使えるなら、ハブの issue の通知メールの返信先（Reply-To）へ、本文の 1 行目を「【依頼】<件名>」にしたメールを送る

**ハブの issue → メール（返信・通知）**。GitLab がこの issue を見ている人へ、新しいコメントをメールで知らせる。Power Automate のフローを
そのメールボックスの「新しいメールが届いたとき」で始め、本文に「【返信】」「【通知】」とその件名を含むかで絞る。

## 決めること

| 決めること | 書く場所 | 例 |
|---|---|---|
| ハブの issue | `gitlab-issue-hub-agent.env` の `GITLAB_PROJECT`・`GITLAB_HUB_ISSUE` | `123`・`42` |
| 依頼で動かすコマンド | `change-watch-issue-hub.yaml` の `request` の `--` のあと | `python3`・`/home/you/work/hub/on_request.py` |
| どのコメントを依頼にするか | `change-watch-issue-hub.yaml` の `request` の `preconditions` | `expected: "re:^【依頼】"` |
| 問い合わせの間隔 | `change-watch.yaml` の取得元の `schedule` | `*/5 * * * *` |

`GITLAB_PROJECT` はプロジェクトの ID（「設定」→「一般」に出る数字）。パスで書くなら `/` を `%2F` にする（`group%2Fproject`）。

## 失敗したとき

GitLab に書き込めない一時的な失敗（終了コード 3）は、Dagu が 1 分おいて 3 回までやり直す。それでも書き込めないときや、権限が無いなどの
失敗は、Dagu の実行履歴に失敗として残り、`commit` はする（その依頼には返信しない）。中身はそのステップのログで確かめる。

| ログ | 直すこと |
|---|---|
| `401` で失敗 | トークンと、その期限を確かめる |
| `403` で失敗 | トークンに `api` があり、持ち主がハブのプロジェクトでコメントできるかを確かめる |
| `404` で失敗 | `GITLAB_PROJECT`（パスなら `%2F`）と `GITLAB_HUB_ISSUE` を確かめる |

## 設定の置き場所

| ファイル | 書くこと |
|---|---|
| `~/.config/change-watch/gitlab-issue-hub-agent/gitlab-issue-hub-agent.env` | GitLab の URL・プロジェクト・ハブの issue・トークン |
| `~/.github/connections.yaml` | GitLab の接続（値は上の環境変数から取る） |
| `~/.config/change-watch/gitlab-issue-hub-agent/change-watch.yaml` | 問い合わせの間隔 |
| `~/.config/dagu/dags/change-watch-issue-hub.yaml` | 依頼にするコメントの条件・依頼で動かすコマンド・再試行 |
| `~/.config/dagu/dags/change-watch-issue-hub-notify.yaml` | 通知の書き込み（ふつうは直さない） |

## テスト

```bash
cd tools/change-watch/agents/gitlab-issue-hub-agent && python3 -m unittest discover -s test
```

設計: [`docs/designs/gitlab-issue-hub-agent-design.md`](../../../../docs/designs/gitlab-issue-hub-agent-design.md)
