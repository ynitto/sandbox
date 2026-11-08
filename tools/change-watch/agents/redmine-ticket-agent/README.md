# redmine-ticket-agent

Redmine のチケットを定期的に見に行き、**自分が担当する未完了のチケットのうち、決めた条件に当たるもの**（タイトルか説明に
決めた文字列を含むなど）に、決めた文をコメントしてステータスを完了にする。**条件とコメントの対は複数書ける**（「【重複】なら
『重複のため完了にしました。』」など）。コメントとステータスの変更は 1 回の更新で行い、同じチケットを 2 回処理しない。

起動と条件の分岐は [Dagu](https://github.com/dagu-org/dagu) のワークフローが、Redmine への問い合わせ（いつ取得するか・まとめる・失敗の待ち）と
「前回見てから新しく現れた・変わったチケット」を返すこと、チケットへの書き込みは [change-watch](../../README.md) が受け持つ
（change-watch の README の「条件をワークフローで判定する」）。条件とコメントの対は、ワークフローに 1 ステップずつ書いてある。

```
 Dagu: change-watch-auto-close（毎分起動。1 回の起動で 1 件。Redmine への問い合わせは 15 分ごと）
   get          自分が担当する未完了のチケットのうち、新しく現れた・題名などが変わったものを 1 件受け取る
   auto-close   題名に「自動完了」を含めば → コメントしてステータスを完了にする（change-watch call）   ← 条件（Dagu の preconditions）
   duplicate    題名に「【重複】」を含めば → 「重複のため完了にしました。」とコメントして完了にする
   commit       見終えたと記録する
```

## 入れ方

1. インストーラで入れる。change-watch と、このエージェントのプログラム（`redmine_issue.py`）・設定の雛形・Dagu のワークフローを、
   どのエージェントも同じ決まりの場所に置く（下の表。決まりは [change-watch の README](../../README.md) の「エージェントを入れる」）。
   ワークフローのパスは、この PC のものにして置く。既にある設定とワークフローは上書きしないので、入れ直すとプログラムだけが新しくなる。

   ```bash
   python3 tools/change-watch/install.py install --agent redmine-ticket-agent
   ```

   | 置かれるもの | 場所 |
   |---|---|
   | プログラム | `~/.local/share/change-watch/agents/redmine-ticket-agent/`（Windows は `%LOCALAPPDATA%\Programs\change-watch\agents\redmine-ticket-agent\`） |
   | 設定（`change-watch.yaml`・`redmine-ticket-agent.env`） | `~/.config/change-watch/redmine-ticket-agent/` |
   | Dagu のワークフロー（`change-watch-auto-close.yaml`・整理の `change-watch-redmine-ticket-agent-maintenance.yaml`） | `~/.config/dagu/dags/` |
   | 状態 | `~/.local/state/change-watch/redmine-ticket-agent/` |

2. Redmine の URL と API キーを `~/.config/change-watch/redmine-ticket-agent/redmine-ticket-agent.env` に書く。
   API キーは Redmine の「個人設定」の「API アクセスキー」（管理者が「REST による Web サービスを有効にする」をオンにしておく）。
   **このキーの持ち主が「自分」になる**（担当の絞り込みと、コメントの書き手）。

3. `~/.config/dagu/dags/change-watch-auto-close.yaml` の**条件とコメントの対**と、`redmine-ticket-agent.env` の**完了のステータスの ID**
   （`REDMINE_CLOSED_STATUS_ID`）を自分のものに直す（下の「決めること」）。例には対が 2 つある（「自動完了」と「【重複】」）。要らない対はステップごと消す
   （消したステップを `depends` に書いているステップは、その前のステップに付け替える）。

4. 接続設定（例: `~/.github/connections.yaml`）に Redmine を書く。値は `redmine-ticket-agent.env` から取るので、API キーを 2 か所に書かなくてよい。

   ```yaml
   redmine:
     - label: default
       url: ${REDMINE_URL}
       token: ${REDMINE_API_KEY}
   ```

5. 環境変数を読み込んで、設定を確かめる（Redmine には問い合わせない）。

   ```bash
   set -a; . ~/.config/change-watch/redmine-ticket-agent/redmine-ticket-agent.env; set +a
   ```

   ```bash
   change-watch --config ~/.config/change-watch/redmine-ticket-agent/change-watch.yaml --connections ~/.github/connections.yaml check
   ```

6. Dagu のスケジューラを動かす（`dagu start-all` など）。Dagu の設定（`config.yaml`）に足すものは無い。ワークフローの `get` は `--initial emit` なので、
   **最初の取得で、いま当たっているチケットも完了にする**（1 分に 1 件ずつ）。これから当たるものだけにしたいなら、動かす前に `--initial baseline` にする。

## 決めること

条件とコメントの対はワークフロー（`~/.config/dagu/dags/change-watch-auto-close.yaml`）に、それ以外は設定に書く。

| 決めること | 書く場所 | 例 |
|---|---|---|
| 条件（対象の文字列など） | 対のステップの `preconditions` | `condition: "${CHANGE.record.subject}"`・`expected: "re:【重複】"` |
| コメントする文 | 対のステップの `--json` の `notes` | `重複のため完了にしました。` |
| 完了にするステータス | `redmine-ticket-agent.env` の `REDMINE_CLOSED_STATUS_ID`（完了扱いのステータスの ID） | `5` |
| 対象のプロジェクト | `change-watch.yaml` の取得元の `query` に `project_id` を足す | `project_id: my-project` |
| 問い合わせの間隔 | `change-watch.yaml` の取得元の `schedule` | `*/15 * * * *` |

- 対は「ステップ 1 つに 1 つ」で、ステップの名前は自由に付けてよい。対を足すときは例の `duplicate` をまねて、`preconditions`（条件）と
  `--json` の `notes`（コメント）を変えたステップを足し、`depends` で前のステップのあとに、`commit` の `depends` をそのステップにする
- **1 つのチケットが 2 つの対に当たると、両方のコメントが付く**（2 回目の書き込みは完了のステータスへの変更で、コメントだけが増える）。
  条件は互いに重ならないように書く
- `expected` の `re:` は正規表現で、大文字・小文字を区別する。説明も見るなら `${CHANGE.record.description}` の条件を別のステップに書く
  （Dagu の `preconditions` はどれも満たすときだけ動くので、「題名か説明」は 2 つのステップになる）
- 文字列を変えたとき・対を足したときは、今あるチケットには当たらない（変化したものだけを受け取るため）。今あるものにも当てたいなら、
  `get` の `--consumer` の名前を変えて（`commit` も同じ名前に）、`--initial emit` で受け取り直す
- ステータスの ID は Redmine の「管理」→「チケットのステータス」で確かめる（完了扱いのもの）

## 対象になるチケット

- 担当が自分（API キーの持ち主）のチケットだけ。グループに割り当てたチケットは対象にしない
- 未完了のステータスのものだけ。完了にしたチケットは次の取得から一覧に出ないので、もう一度処理することはない
- 次のどれかのとき見る: 最初の取得で一覧にあった（`--initial emit` のとき）/ 新しく担当になった・作られた / 題名・説明・ステータス・担当が変わった
- 完了にする前にチケットを取り直すことはしない。15 分ごとの取得のあいだに、人が先に完了にした・担当が替わったチケットにも書き込むことがある

## 失敗したとき

Redmine に書き込めない一時的な失敗（終了コード 3）は、Dagu が 1 分おいて 3 回までやり直す。それでも書き込めないときや、権限が無い・
ステータスが無いなどの失敗は、Dagu の実行履歴に失敗として残り、`commit` はする（そのチケットが次に変化したときにもう一度見る）。
失敗の中身は、そのステップのログ（Redmine が返したエラー）で確かめる。

| ログ | 直すこと |
|---|---|
| `422` で失敗 | Redmine のワークフローで、自分のロールがそのステータスに変えられるようにする。`REDMINE_CLOSED_STATUS_ID` があるステータスの ID かも確かめる |
| `401` で失敗 | API キーと、REST の Web サービスが有効かを確かめる |

## ルールで判定する形（`examples/rules/`）

条件とコメントの対も change-watch の設定（ルール `rules`）に書き、`redmine_issue.py close` で完了にする形の例。
入れ方で、`~/.config/change-watch/redmine-ticket-agent/change-watch.yaml` を `examples/rules/change-watch.yaml.example` に、
`~/.config/dagu/dags/change-watch-auto-close.yaml` を `examples/rules/change-watch-auto-close.yaml.example` に替える（`/home/you` は自分の場所に直す）。
条件はルールの `filter`、コメントは受け渡し先 `close` の `command` の `--note`、完了のステータスは `--status`（名前か ID）に書く。既定の形との違い:

- 完了にする直前にチケットを取り直し、そのときにも当たっていて担当が自分のときだけ完了にする（チェック `redmine.not_mine`）
- 1 つのチケットが複数の対に当たっても、上に書いた対のコメントで 1 回だけ完了にする
- 条件や対を変えると、新しい条件に当たる今あるチケットも、次の取得で完了にする
- 失敗したものは change-watch が待ってから流し直し（`retry_backoff_sec`・`max_attempts`）、`events`・`retry` で見たりやり直したりできる
- Dagu を使わず、cron などから `change-watch ... --plugin ~/.local/share/change-watch/agents/redmine-ticket-agent/redmine_issue.py run --source redmine-my-issues` で動かせる

## 設定の置き場所

| ファイル | 書くこと |
|---|---|
| `~/.config/change-watch/redmine-ticket-agent/redmine-ticket-agent.env` | Redmine の URL・API キー・完了のステータスの ID |
| `~/.github/connections.yaml` | Redmine の接続（値は上の環境変数から取る） |
| `~/.config/change-watch/redmine-ticket-agent/change-watch.yaml` | 対象のチケットの絞り込み（`query`）・問い合わせの間隔 |
| `~/.config/dagu/dags/change-watch-auto-close.yaml` | 見る項目（`--watch`）・条件とコメントの対・再試行 |

## テスト

```bash
cd tools/change-watch/agents/redmine-ticket-agent && python3 -m unittest discover -s test
```

設計: [`docs/designs/redmine-ticket-agent-design.md`](../../../../docs/designs/redmine-ticket-agent-design.md)
