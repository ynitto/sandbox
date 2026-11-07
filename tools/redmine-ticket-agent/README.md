# redmine-ticket-agent

Redmine のチケットを定期的に見に行き、**自分が担当する未完了のチケットのうち、決めた条件に当たるもの**（タイトルか説明に
決めた文字列を含むなど）に、決めた文をコメントしてステータスを完了にする。**条件とコメントの対は複数書ける**（「【重複】なら
『重複のため完了にしました。』」など）。コメントとステータスの変更は 1 回の更新で行い、同じチケットを 2 回処理しない。

チケットの取得・変化の検知・実行待ち・再試行は [change-watch](../change-watch/README.md) が、定期起動は
[Dagu](https://github.com/dagu-org/dagu) が受け持つ（Dagu を使わず cron などから動かしてもよい）。このフォルダには、Redmine に固有の部分を
足す `redmine_issue.py`（change-watch のプラグインで、チケットを完了にするコマンドも兼ねる）と、設定の例がある。
[gitlab-issue-agent](../gitlab-issue-agent/README.md) と同じ作りで、エージェント CLI の代わりに決まった処理を動かす。

```
 Dagu: change-watch-auto-close（毎分起動。Redmine への問い合わせは 15 分ごと）
   fetch      自分が担当する未完了のチケットを全ページ取得
   evaluate   条件（対ごとのルール）に当たるチケットを拾う（新しく当たったもの・内容が変わったもの）
   work       チケットを取り直し、まだ当たっていて担当が自分なら、その対のコメントをしてステータスを完了にする
```

## 入れ方

1. プラグインを置く。

   ```bash
   mkdir -p ~/.local/share/redmine-ticket-agent
   ```

   ```bash
   cp tools/redmine-ticket-agent/redmine_issue.py ~/.local/share/redmine-ticket-agent/
   ```

2. Redmine の URL と API キーを書く。`redmine-ticket-agent.env.example` を `~/.config/dagu/redmine-ticket-agent.env` にコピーして埋める。
   API キーは Redmine の「個人設定」の「API アクセスキー」（管理者が「REST による Web サービスを有効にする」をオンにしておく）。
   **このキーの持ち主が「自分」になる**（担当の絞り込みと、コメントの書き手）。

   ```bash
   cp tools/redmine-ticket-agent/redmine-ticket-agent.env.example ~/.config/dagu/redmine-ticket-agent.env
   ```

3. change-watch を入れる。

   ```bash
   python3 tools/change-watch/install.py install
   ```

4. change-watch の設定を置き、**条件とコメントの対・完了のステータス**を自分のものに直す（下の「決めること」）。
   例には対が 2 つある（「自動完了」と「【重複】」）。要らない対はルールごと消す。

   ```bash
   mkdir -p ~/.config/change-watch
   ```

   ```bash
   cp tools/redmine-ticket-agent/change-watch.yaml.example ~/.config/change-watch/change-watch.yaml
   ```

5. 接続設定（例: `~/.github/connections.yaml`）に Redmine を書く。値は `redmine-ticket-agent.env` から取るので、API キーを 2 か所に書かなくてよい。

   ```yaml
   redmine:
     - label: default
       url: ${REDMINE_URL}
       token: ${REDMINE_API_KEY}
   ```

6. 環境変数を読み込んで設定を確かめ、Dagu のワークフローを置く。`change-watch-auto-close.yaml.example`（取得から完了まで）を
   `~/.config/dagu/dags/change-watch-auto-close.yaml` にコピーして `/home/you` を自分の場所に直し、毎日の整理のワークフローは change-watch の例を
   そのまま置く。Dagu の設定（`config.yaml`）に足すものは無い。

   ```bash
   set -a; . ~/.config/dagu/redmine-ticket-agent.env; set +a
   ```

   ```bash
   change-watch --plugin ~/.local/share/redmine-ticket-agent/redmine_issue.py --config ~/.config/change-watch/change-watch.yaml --connections ~/.github/connections.yaml check
   ```

   ```bash
   cp tools/redmine-ticket-agent/change-watch-auto-close.yaml.example ~/.config/dagu/dags/change-watch-auto-close.yaml
   ```

   ```bash
   cp tools/change-watch/dags/change-watch-maintenance.example.yaml ~/.config/dagu/dags/change-watch-maintenance.yaml
   ```

7. Dagu のスケジューラを動かす（`dagu start-all` など）。設定の例は `initial: emit` なので、**最初の取得で、いま当たっているチケットも完了にする**。
   これから当たるものだけにしたいなら、動かす前に `initial: baseline` にする。

Dagu を使わないなら、6 のワークフローの代わりに cron などから次を定期的に呼ぶ（環境変数は先に読み込んでおく）。

```bash
change-watch --plugin ~/.local/share/redmine-ticket-agent/redmine_issue.py --config ~/.config/change-watch/change-watch.yaml --connections ~/.github/connections.yaml run --source redmine-my-issues
```

## 決めること

どれも `~/.config/change-watch/change-watch.yaml` に書く。

| 決めること | 書く場所 | 例 |
|---|---|---|
| 条件（対象の文字列など） | 対のルールの `filter` | `{ path: subject, op: contains, value: 【重複】 }` |
| コメントする文 | 対のルールの受け渡し先 `close` の `command` の `--note` の次 | `重複のため完了にしました。` |
| 完了にするステータス | `command` の `--status` の次（名前か ID。完了扱いのステータス） | `完了`・`5` |
| 対象のプロジェクト | 取得元の `query` に `project_id` を足す | `project_id: my-project` |
| 問い合わせの間隔 | 取得元の `schedule` | `*/15 * * * *` |

- 対は「ルール 1 本に 1 つ」で、ルールの名前は自由に付けてよい（`auto-close-duplicate` など）。対を足すときは例の
  `auto-close-duplicate` をまねて、`<<: *auto-close` の下に `filter`（条件）と `targets`（`<<: *close` とコメントの `command`）だけを書く。
  `filter` には、どの対にも `- { path: status.is_closed, op: falsy }` を入れる。ワークフローは直さなくてよい
- **1 つのチケットが複数の対に当たったら、上に書いた対のコメントで 1 回だけ完了にする**。下の対は、取り直したときにもう完了なので
  何もしない。そのため `--status` は完了扱いのステータス（Redmine の「チケットのステータス」で「終了したチケット」に印のあるもの）にする
- 文字列は大文字・小文字を区別して、そのまま含むかを見る。複数の文字列や大文字・小文字を区別しない比べ方は `op: regex` で書く
  （例: `value: "(?i)(自動完了|auto-close)"`）。タイトルだけを見るなら、`description` の行を消す
- 文字列を変えたとき・対を足したときは、新しい条件に当たる今あるチケットも、次の取得で完了にする
- 取得元の `schedule` を消すなら、代わりに `min_interval_sec` と `busy_wait_sec` を書く（対ごとにルールがあるので、change-watch が求める）
- コメントが長いときは、文をファイルに書いて `--note` の代わりに `--note-file <ファイルのパス>` にする
- `command` の `python3` と `redmine_issue.py` はフルパスで書く（`~` は展開されない）。Windows では `python` と `C:/Users/<名前>/...` にする
- ステータスの名前は Redmine の「管理」→「チケットのステータス」にあるものと同じにする。無い名前なら、あるものの一覧を出して失敗する

## 対象になるチケット

- 担当が自分（API キーの持ち主）のチケットだけ。グループに割り当てたチケットは対象にしない
- 未完了のステータスのものだけ。完了にしたチケットは次の取得から一覧に出ないので、もう一度処理することはない
- 次のどれかのとき処理する: 最初の取得で当たった（`initial: emit` のとき）/ 新しく担当になった・作られた / タイトルや説明を直して条件に当たるようになった
- 処理する直前にチケットを取り直し、**そのときにも当たっていて担当が自分のときだけ**完了にする。待つ間に文字列を消した・担当が替わった・
  人が先に完了にしたチケットはそのままにする

## 失敗したとき

ステータスを完了に変えられなかったときは失敗になり、5 分（1 回ごとに倍）たってからやり直す。3 回失敗したら打ち切り、そのチケットが次に
変化したときにもう一度拾う。複数の対に当たるチケットで上の対が失敗したときは、下の対のコメントで完了になることがある。

| 失敗の表示 | 直すこと |
|---|---|
| `「完了」に変えられません（ワークフローで選べるもの: …）` | Redmine のワークフローで、自分のロールがそのステータスに変えられるようにする。このときコメントも書かない |
| `ステータス「…」が Redmine にありません` | `--status` をあるステータスの名前か ID にする |
| `コメントしましたが、ステータスが「完了」になりませんでした` | 権限とワークフローを確かめる（古い Redmine では書く前に確かめられないので、コメントだけが残る。やり直しでは同じコメントを重ねない） |
| `401` で失敗 | API キーと、REST の Web サービスが有効かを確かめる |

| したいこと | コマンド |
|---|---|
| 失敗している・打ち切ったものを見る | `change-watch --config ~/.config/change-watch/change-watch.yaml events --status send_failed,dead` |
| 打ち切ったものをすぐやり直す | `change-watch --config ~/.config/change-watch/change-watch.yaml retry <受け渡しの ID>` |

## 設定の置き場所

| ファイル | 書くこと |
|---|---|
| `~/.config/dagu/redmine-ticket-agent.env` | Redmine の URL・API キー |
| `~/.github/connections.yaml` | Redmine の接続（値は上の環境変数から取る） |
| `~/.config/change-watch/change-watch.yaml` | 条件とコメントの対・ステータス・問い合わせの間隔・再試行 |

## テスト

```bash
cd tools/redmine-ticket-agent && python3 -m unittest discover -s test
```

設計: [`docs/designs/redmine-ticket-agent-design.md`](../../docs/designs/redmine-ticket-agent-design.md)
