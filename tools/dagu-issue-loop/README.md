# dagu-issue-loop

GitLab の issue を [Dagu](https://github.com/dagu-org/dagu)（v2）で定期的に見に行き、変化があった issue を
エージェント CLI（Claude Code / Codex / Kiro / Copilot など）に 1 件ずつ渡す。
**同時に動くエージェント CLI は常に 1 つ**で、同じ変化を 2 回流さない。

```
 Dagu: gitlab-issue-poll（1 時間ごと）        Dagu: gitlab-issue-agent（毎分）
   issue_loop.py poll                            issue_loop.py run
     0〜15 分ずらして issue を取得                    実行中のセッションがあれば何もせず終わる
     前回との差分を見て待ち行列へ積む  ──待ち行列──▶  1 件取り出してエージェント CLI を実行
     （同じ issue の変化は 1 件にまとめる）          終わったら既読にする（失敗なら後で再試行）
```

Python 3.10 以降の標準ライブラリだけで動く。追加のパッケージは要らない。Linux・macOS・Windows（ネイティブ）のどれでも動く。

## 入れ方

1. スクリプトを置く。

   ```bash
   mkdir -p ~/.local/share/dagu-issue-loop
   ```

   ```bash
   cp tools/dagu-issue-loop/issue_loop.py ~/.local/share/dagu-issue-loop/
   ```

2. 設定を書く。`issue-loop.env.example` を `~/.config/dagu/issue-loop.env` にコピーし、`ISSUE_LOOP_HOME`（1 で置いたフォルダの絶対パス）・
   `ISSUE_LOOP_PYTHON`・GitLab の URL・プロジェクト・トークンと、使うエージェント CLI を埋める。トークンには `read_api` が要る
   （エージェントが issue にコメントするなら、そのエージェント側の権限も別に要る）。

   ```bash
   cp tools/dagu-issue-loop/issue-loop.env.example ~/.config/dagu/issue-loop.env
   ```

3. DAG を置く。Dagu の DAG フォルダ（既定 `~/.config/dagu/dags/`）へ 2 本ともコピーする。

   ```bash
   cp tools/dagu-issue-loop/dags/*.yaml ~/.config/dagu/dags/
   ```

4. 手で一度ずつ動かして確かめる。最初の `poll` は今ある issue を既読にするだけで、エージェントは起こさない。

   ```bash
   set -a; . ~/.config/dagu/issue-loop.env; set +a
   ```

   ```bash
   python3 ~/.local/share/dagu-issue-loop/issue_loop.py poll
   ```

   ```bash
   python3 ~/.local/share/dagu-issue-loop/issue_loop.py status
   ```

5. Dagu のスケジューラを動かす（`dagu start-all` など）。以後は issue にコメント・ラベル変更・本文の更新などが
   あると、次の取得（1 時間に 1 回）でエージェントが起きる。

## GitLab への問い合わせの回数

- issue の取得は **1 時間に 1 回**。毎時 0 分に起動し、0〜15 分のあいだでランダムにずらしてから問い合わせる。
  複数の PC やプロジェクトで動かしても、問い合わせが同じ時刻に重ならない。1 回の取得は 100 件ごとに 1 リクエスト
- エージェントの DAG は毎分起動するが、待ち行列が空なら GitLab には**問い合わせない**
- エージェントを 1 回流すたびに、終わったあとの確認で 3 リクエスト（自分の名前・issue の取り直し・コメント）

間隔は `gitlab-issue-poll.yaml` の `schedule`、ずらす幅は同じファイルの `--jitter`（秒）で変えられる。手で `poll` を
動かすときはずらさない（`--jitter` を付けたときと、`ISSUE_LOOP_POLL_JITTER` を設定したときだけずらす）。

## 何を「変化」とみなすか

次のどれかが前回から変わったときだけ積む。`updated_at` だけが動いた更新（MR からの言及など）では起こさない。

- 新しい issue / タイトル / 状態（開いた・閉じた）/ ラベル / 担当 / マイルストーン / 期日 / コメント数 / 本文

対象は `ISSUE_LOOP_ISSUE_STATE`・`ISSUE_LOOP_ISSUE_LABELS`・`ISSUE_LOOP_ISSUE_ASSIGNEE` で絞れる。
絞り込みから外れた issue（閉じた・ラベルが外れた）は、まだ流していなければ待ち行列から下ろす。

## 重複させない仕組み

- 待ち行列は issue ごとに 1 件。実行を待つ間に同じ issue がさらに変わったら、最新の内容に差し替える
  （順番は最初に積んだときのまま。プロンプトの差分は前回流したときからの累積）
- 流し終えたら、その時点の issue を取り直して既読にする。**エージェント自身のコメントやラベル変更で
  もう一度起きることはない**
- 実行中に自分以外の人がコメントしたときだけ、もう一度流す（自分はトークンの持ち主で判定する。
  `ISSUE_LOOP_SELF_USERNAME` で明示もできる）

## 1 セッションだけにする仕組み

- `run` はロックファイルを取ってからエージェント CLI を起動する。取れなければ「別のセッションが実行中」と
  1 行出して正常終了する（Dagu の履歴は失敗にならない）
- ロックは既定で**全プロジェクト共通の 1 つ**。プロジェクトごとに DAG を並べても同時に動くのは 1 つだけ。
  プロジェクトごとに 1 つずつ動かしたいなら `ISSUE_LOOP_LOCK_FILE` をプロジェクトごとに変える
- `run` が強制終了されてエージェント CLI だけが残った場合も、その CLI が終わるまで次は始めない
- Dagu からの停止・タイムアウト（SIGTERM）では CLI を子プロセスごと止め、失敗として記録してから終わる

## Windows で使うとき

置き場所は Linux と同じ形で、`~` はユーザーフォルダ（`C:\Users\<名前>`）になる。Dagu の設定フォルダも
`C:\Users\<名前>\.config\dagu\` で、DAG は `dags\` に置く。`issue-loop.env` では次の 2 つを Windows 向けにする。

```
ISSUE_LOOP_HOME=C:/Users/you/.local/share/dagu-issue-loop
ISSUE_LOOP_PYTHON=python
```

- Windows の Dagu は、止めるとき（中止・タイムアウト）にプロセスをツリーごと即座に終了させる。エージェント CLI も
  一緒に止まるので 2 つ目のセッションは始まらないが、その場では失敗として記録できない。次の `run` が
  「途中で止まった」として失敗 1 回に数え直す
- `ISSUE_LOOP_AGENT_TIMEOUT` で時間切れになったときは、CLI に Ctrl+Break を送り、15 秒待って子孫ごと強制終了する
- `claude.cmd` のようなバッチ形式の CLI は、プロンプトを標準入力で渡す（`ISSUE_LOOP_AGENT_PROMPT_VIA=stdin`、既定）。
  引数で渡すと、改行や記号を含むプロンプトが壊れることがある
- ログは UTF-8 で出す（Linux でも UTF-8 でないロケールなら UTF-8 に切り替える）

## 失敗したとき

エージェント CLI が 0 以外で終わる・`ISSUE_LOOP_AGENT_TIMEOUT` を超える・止められると失敗として数え、
`ISSUE_LOOP_RETRY_BACKOFF` 秒（1 回ごとに倍）待ってから再試行する。`ISSUE_LOOP_MAX_ATTEMPTS` 回失敗したら打ち切り、
その issue が次に変化したときにもう一度拾う。打ち切ったものをすぐ拾い直させるには:

```bash
python3 ~/.local/share/dagu-issue-loop/issue_loop.py forget 123
```

## エージェント CLI の指定

- `ISSUE_LOOP_AGENT=claude` のように名前で指定すると、このリポジトリの `agents/<名前>.json`
  （または `~/.agents/agents/`）の起動方法を使う。プロンプトの渡し方も定義に従う
- 直接書くなら `ISSUE_LOOP_AGENT_CMD`（こちらが優先）。プロンプトは標準入力で渡す。
  `ISSUE_LOOP_AGENT_PROMPT_VIA=arg` で末尾の引数、`file` でファイルのパスを渡す
- 権限の確認で止まらないよう、無人で動かせるフラグを付けること（例: `claude -p --dangerously-skip-permissions`）
- 作業フォルダは `ISSUE_LOOP_AGENT_CWD`。CLI には `ISSUE_LOOP_IID`・`ISSUE_LOOP_PROJECT`・`ISSUE_LOOP_PROMPT_FILE` も渡す

プロンプトは `ISSUE_LOOP_PROMPT_TEMPLATE` のファイルで差し替えられる（`prompt.md.example` を参照）。
使える差し込みは `{project}` `{iid}` `{title}` `{web_url}` `{labels}` `{change_kind}` `{changes}` `{issue_json}` `{attempt}`。

## 設定一覧

| 環境変数 | 既定 | 意味 |
|---|---|---|
| `GITLAB_URL` / `GITLAB_API_URL` | （必須） | GitLab の URL（API の URL を直接書くなら後者） |
| `GITLAB_PROJECT` | （必須） | `group/project` か数値 ID |
| `GITLAB_TOKEN`（または `GL_TOKEN`） | （必須） | アクセストークン |
| `ISSUE_LOOP_HOME` | （必須） | DAG が `issue_loop.py` を探すフォルダ（絶対パス） |
| `ISSUE_LOOP_PYTHON` | （必須） | DAG が使う Python（`python3`。Windows では `python` か `py`） |
| `ISSUE_LOOP_ISSUE_STATE` | `opened` | `opened` / `closed` / `all` |
| `ISSUE_LOOP_ISSUE_LABELS` | — | カンマ区切り。すべて持つ issue だけ |
| `ISSUE_LOOP_ISSUE_ASSIGNEE` | — | この人に割り当てられた issue だけ |
| `ISSUE_LOOP_AGENT` / `ISSUE_LOOP_AGENT_CMD` | （どちらか必須） | エージェント CLI |
| `ISSUE_LOOP_AGENT_MODEL` | — | モデル名（定義に `{model}` があるときは必須） |
| `ISSUE_LOOP_AGENT_CWD` | — | エージェントを動かすフォルダ |
| `ISSUE_LOOP_AGENT_TIMEOUT` | `3600` | 1 回の上限秒数（DAG の `timeout_sec` はこれより長く） |
| `ISSUE_LOOP_PROMPT_TEMPLATE` | — | プロンプトのテンプレートファイル |
| `ISSUE_LOOP_MAX_ATTEMPTS` | `3` | 打ち切るまでの失敗回数 |
| `ISSUE_LOOP_RETRY_BACKOFF` | `300` | 再試行までの秒数（1 回ごとに倍） |
| `ISSUE_LOOP_POLL_JITTER` | `0` | 取得の前にランダムに待つ最大秒数（DAG では `--jitter 900` を渡す） |
| `ISSUE_LOOP_DRAIN` | `1` | 1 回の `run` で続けて流す最大件数 |
| `ISSUE_LOOP_LOCK_FILE` | 全プロジェクト共通 | セッションの排他に使うファイル |
| `ISSUE_LOOP_STATE_DIR` | `~/.agents/dagu-issue-loop` | 待ち行列・既読・実行ログの置き場 |
| `ISSUE_LOOP_MAX_PAGES` | `10` | 1 回に読む issue の上限（100 件 × ページ数） |

実行ごとのプロンプトと CLI の出力は `ISSUE_LOOP_STATE_DIR/<プロジェクト>/runs/` に残る（Dagu のログにも同じ出力が出る）。

## 終了コード

| コード | 意味 |
|---|---|
| 0 | 正常（積んだ・流して成功した・待ち行列が空・別のセッションが実行中で見送った） |
| 1 | エージェント CLI が失敗した・時間切れ・止められた |
| 2 | 設定の誤り |
| 3 | GitLab に届かない・認証に失敗した（poll の DAG はこのときだけ再試行する） |
| 4 | 別のセッションが実行中だった（`handle` だけ） |

## api-watch で動かす

issue の取得と変化の検知を [api-watch](../api-watch/README.md) に任せる構成もある。Dagu のワークフロー 1 本（`api-watch-issue-agent`）の中で、
api-watch が issue を取得して変化を判定し、変化した issue ごとに `issue_loop.py handle` を 1 件ずつ実行する。
エージェント CLI の起動・プロンプト・1 セッションの排他・自分の書き込みで起きない仕組みは、これまでと同じ `issue_loop.py` が受け持つ。

```
 Dagu: api-watch-issue-agent（1 時間ごと）
   fetch     0〜15 分ずらして issue を全ページ取得    ← 同じ取得元を使う別のワークフローとは 1 回にまとめる
   evaluate  前回と比べて変化した issue を拾う
   work      変化した issue ごとに issue_loop.py handle  ← issue を取り直してエージェント CLI を実行し、既読にする
```

スケジュールは api-watch の設定にだけ書く。Dagu のワークフローは手で書かず、api-watch の `dags` で書き出す（設定の `schedule` が
ワークフローの `schedule` になる）。設定を変えたら書き出し直す。書き出し直していなければ、実行のたびに api-watch がログで知らせる。

### 入れ方

1. 上の「入れ方」の 1・2 と同じく、スクリプトを置いて `issue-loop.env` を書く。
2. api-watch を入れる（`python3 tools/api-watch/install.py install`）。
3. api-watch の設定を置く。絞り込み（`query` の `state`・`labels`・`assignee_username`）は `issue-loop.env` の
   `ISSUE_LOOP_ISSUE_STATE` などと同じにする。

   ```bash
   mkdir -p ~/.config/api-watch
   ```

   ```bash
   cp tools/dagu-issue-loop/api-watch/api-watch.yaml.example ~/.config/api-watch/api-watch.yaml
   ```

4. 接続設定（例: `~/.github/connections.yaml`）に GitLab を書く。値は `issue-loop.env` から取るので、トークンを 2 か所に書かなくてよい。

   ```yaml
   gitlab:
     - label: default
       url: ${GITLAB_URL}
       token: ${GITLAB_TOKEN}
   ```

5. 環境変数を読み込んでから、Dagu のワークフローを書き出す。`api-watch-issue-agent.yaml`（取得から実行まで）と
   `api-watch-maintenance.yaml`（毎日の整理）ができる。`gitlab-issue-poll.yaml` と `gitlab-issue-agent.yaml` は置かない
   （置いてあれば消す）。Dagu の設定（`config.yaml`）に足すものは無い。

   ```bash
   set -a; . ~/.config/dagu/issue-loop.env; set +a
   ```

   ```bash
   api-watch --plugin dagu --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml dags --out ~/.config/dagu/dags --dotenv ~/.config/dagu/issue-loop.env
   ```

6. 一度手で回して基準を取る（今ある issue を覚えるだけで、エージェントは起こさない）。そのあと Dagu のスケジューラを動かす。

   ```bash
   api-watch --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml run --rule issue-agent --no-jitter
   ```

### 同じ GitLab を別のワークフローからも見るとき

issue の変化で別のことをしたい（通知など）ときは、api-watch の設定に同じ取得元 `gitlab-issues` を使うルールを足して、`dags` で
書き出し直す（設定例の `issue-notify`。`api-watch-issue-notify` ができる）。ワークフローが同じ時刻に起動しても、GitLab への問い合わせは 1 回にまとまる:

- 先に取得したワークフローの結果を、`min_interval_sec`（例は 30 分）のあいだ他のワークフローも使う
- 別のワークフローが取得している最中なら、`busy_wait_sec`（例は 10 分）まで終わるのを待って、その結果を使う
- 取得が失敗したら、`failure_backoff_sec`（例は 5 分。続けて失敗するほど倍、最大 1 時間）のあいだ、どのワークフローも問い合わせない

2 本以上のワークフローが同じ取得元を取得するのに `min_interval_sec`・`busy_wait_sec` が無い、または `jitter_sec` が
`min_interval_sec` 以上のときは、api-watch が設定の誤りとして止める。

### これまでの構成との違い

| | `poll` と `run` | api-watch で動かす |
|---|---|---|
| ワークフロー | 取得（1 時間ごと）と実行（毎分）の 2 本 | 取得から実行までの 1 本（api-watch が書き出す） |
| 取得の間隔 | 毎時 0 分から 0〜15 分ずらす | 同じ（`schedule` と `jitter_sec`）。取得の失敗は `failure_backoff_sec` で間を空ける |
| 変化の判定 | 上の「何を変化とみなすか」 | 同じ項目を api-watch の `watch` に書く |
| 待っている変化 | issue ごとに 1 件へまとめる | 変化ごとに 1 件。先に動いた実行が最新の issue で流し、後ろの同じ issue は流し終えた分として飛ばす |
| 失敗と再試行 | `ISSUE_LOOP_MAX_ATTEMPTS`・`ISSUE_LOOP_RETRY_BACKOFF`（毎分の `run` で再試行） | 設定の `max_attempts`・`retry_backoff_sec`。再試行はワークフローの次の起動で行う |
| 1 セッション | ロックファイル | ワークフローの中で 1 件ずつ実行し、ロックファイルでも排他する |
| 取得の履歴・やり直し | なし | api-watch の `status`・`events`・`retry` で見られる |

`handle` は、別のセッションが実行中なら 4、GitLab に届かなければ 3、エージェント CLI が失敗したら 1 で終わり、どれも次の起動で再試行される。
対象から外れた issue（実行を待つ間に閉じた・ラベルが外れた）は流さずに 0 で終わる。

## テスト

```bash
cd tools/dagu-issue-loop && python3 -m unittest discover -s test
```

設計: [`docs/designs/dagu-issue-loop-design.md`](../../docs/designs/dagu-issue-loop-design.md)
