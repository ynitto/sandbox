# dagu-issue-loop

GitLab の issue を定期的に見に行き、変化があった issue をエージェント CLI（Claude Code / Codex / Kiro / Copilot など）に
1 件ずつ渡す。**同時に動くエージェント CLI は常に 1 つ**で、同じ変化を 2 回流さない。

issue の取得と変化の検知は [api-watch](../api-watch/README.md) が、起動は [Dagu](https://github.com/dagu-org/dagu)（v2）が受け持つ。
このフォルダの `issue_loop.py` は、api-watch から変化 1 件ごとに呼ばれ、issue を取り直してエージェント CLI を動かす。

```
 Dagu: api-watch-issue-agent（毎分起動）
   fetch     毎時 0 分の予定のあと、0〜15 分ずらして issue を全ページ取得（予定の時刻のあとに取得済みなら問い合わせない）
   evaluate  前回と比べて変化した issue を拾う
   work      変化した issue ごとに issue_loop.py handle  ← issue を取り直してエージェント CLI を実行し、既読にする
```

`issue_loop.py` は Python 3.10 以降の標準ライブラリだけで動く。Linux・macOS・Windows（ネイティブ）のどれでも動く。

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

3. api-watch を入れる。

   ```bash
   python3 tools/api-watch/install.py install
   ```

4. api-watch の設定を置く。絞り込み（`query` の `state`・`labels`・`assignee_username`）は `issue-loop.env` の
   `ISSUE_LOOP_ISSUE_STATE` などと同じにする。

   ```bash
   mkdir -p ~/.config/api-watch
   ```

   ```bash
   cp tools/dagu-issue-loop/api-watch.yaml.example ~/.config/api-watch/api-watch.yaml
   ```

5. 接続設定（例: `~/.github/connections.yaml`）に GitLab を書く。値は `issue-loop.env` から取るので、トークンを 2 か所に書かなくてよい。

   ```yaml
   gitlab:
     - label: default
       url: ${GITLAB_URL}
       token: ${GITLAB_TOKEN}
   ```

6. 環境変数を読み込んでから、Dagu のワークフローの雛形を書き出す。`api-watch-issue-agent.yaml`（取得から実行まで）と
   `api-watch-maintenance.yaml`（毎日の整理）ができる。作ったあとは自由に直してよい（既にあるファイルは上書きしない）。
   Dagu の設定（`config.yaml`）に足すものは無い。

   ```bash
   set -a; . ~/.config/dagu/issue-loop.env; set +a
   ```

   ```bash
   api-watch --plugin dagu --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml dags --out ~/.config/dagu/dags --dotenv ~/.config/dagu/issue-loop.env
   ```

7. 一度手で回して基準を取る（今ある issue を覚えるだけで、エージェントは起こさない）。そのあと Dagu のスケジューラを動かす
   （`dagu start-all` など）。以後は issue にコメント・ラベル変更・本文の更新などがあると、次の取得（1 時間に 1 回）でエージェントが起きる。

   ```bash
   api-watch --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml run --rule issue-agent --no-jitter
   ```

以前の版の `gitlab-issue-poll.yaml` / `gitlab-issue-agent.yaml` を置いていたら、Dagu の DAG フォルダから消す。

## GitLab への問い合わせの回数

- issue の一覧の取得は **1 時間に 1 回**。予定の時刻（毎時 0 分）から 0〜15 分のあいだでランダムにずらして問い合わせる。
  複数の PC やプロジェクトで動かしても、問い合わせが同じ時刻に重ならない。1 回の取得は 100 件ごとに 1 リクエスト
- ワークフローは毎分起動するが、予定の時刻のあとに取得し終えていれば一覧は取得しない。毎分の起動で進むのは、失敗したもののやり直しだけ
- エージェントを 1 回流すたびに 4 リクエスト（実行前の issue の取り直し・自分の名前・実行後の issue の取り直し・コメント）
- 取得が失敗したら 5 分（続けて失敗するほど倍、最大 1 時間）は問い合わせない

予定は api-watch の設定の取得元の `schedule`、ずらす幅は `jitter_sec` で変えられる。予定を変えるときは api-watch の設定だけを直せばよく、
ワークフローは書き出し直さなくてよい。問い合わせの予定も Dagu に任せたいなら、取得元の `schedule` を消し、ワークフローの
`schedule` に書く（`dags --schedule "0 * * * *"`）。

## 何を「変化」とみなすか

次のどれかが前回から変わったときだけ流す。`updated_at` だけが動いた更新（MR からの言及など）では起こさない。

- 新しい issue / タイトル / 状態（開いた・閉じた）/ ラベル / 担当 / マイルストーン / 期日 / コメント数 / 本文

見る項目は api-watch の設定のルールの `watch` に書いてある。対象は api-watch の設定の `query` と、`ISSUE_LOOP_ISSUE_STATE`・
`ISSUE_LOOP_ISSUE_LABELS`・`ISSUE_LOOP_ISSUE_ASSIGNEE` で絞る。実行を待つ間に絞り込みから外れた issue（閉じた・ラベルが外れた）は流さない。

## 重複させない仕組み

- 実行の直前に issue を取り直し、最新の内容でエージェントに渡す。プロンプトの差分は前回流したときからの累積
- 流し終えたら、その時点の issue を取り直して既読にする。**エージェント自身のコメントやラベル変更で
  もう一度起きることはない**（api-watch はその変化も拾うが、既読の版より新しくないので流さない）
- 実行を待つ間に同じ issue が何度か変わっても、先に動いた実行が最新の issue でまとめて流し、後ろは飛ばす
- 実行中に自分以外の人がコメントしたときだけ、次の取得でもう一度流す（自分はトークンの持ち主で判定する。
  `ISSUE_LOOP_SELF_USERNAME` で明示もできる）

## 1 セッションだけにする仕組み

- api-watch は変化を 1 件ずつ順に実行し、同じワークフローが重ねて起動されることもない
- `handle` はさらにロックファイルを取ってからエージェント CLI を起動する。取れなければ「別のセッションが実行中」と
  1 行出して終了コード 4 で終わり、api-watch があとで同じ変化を流し直す
- ロックは既定で**全プロジェクト共通の 1 つ**。プロジェクトごとにワークフローを並べても同時に動くのは 1 つだけ。
  プロジェクトごとに 1 つずつ動かしたいなら `ISSUE_LOOP_LOCK_FILE` をプロジェクトごとに変える
- `handle` が強制終了されてエージェント CLI だけが残った場合も、その CLI が終わるまで次は始めない
- Dagu からの停止・タイムアウト（SIGTERM）では CLI を子プロセスごと止め、失敗として終わる

## Windows で使うとき

置き場所は Linux と同じ形で、`~` はユーザーフォルダ（`C:\Users\<名前>`）になる。Dagu の設定フォルダも
`C:\Users\<名前>\.config\dagu\` で、DAG は `dags\` に置く。`issue-loop.env` では次の 2 つを Windows 向けにする。

```
ISSUE_LOOP_HOME=C:/Users/you/.local/share/dagu-issue-loop
ISSUE_LOOP_PYTHON=python
```

- api-watch は `python install.py install` で入れる（`dags` はその Python と `api-watch.pyz` のフルパスでワークフローを書く）
- Windows の Dagu は、止めるとき（中止・タイムアウト）にプロセスをツリーごと即座に終了させる。エージェント CLI も
  一緒に止まるので 2 つ目のセッションは始まらない。止められた変化は、api-watch の受け渡しの期限が切れたあと流し直される
- `ISSUE_LOOP_AGENT_TIMEOUT` で時間切れになったときは、CLI に Ctrl+Break を送り、15 秒待って子孫ごと強制終了する
- `claude.cmd` のようなバッチ形式の CLI は、プロンプトを標準入力で渡す（`ISSUE_LOOP_AGENT_PROMPT_VIA=stdin`、既定）。
  引数で渡すと、改行や記号を含むプロンプトが壊れることがある
- ログは UTF-8 で出す（Linux でも UTF-8 でないロケールなら UTF-8 に切り替える）

## 失敗したとき

エージェント CLI が 0 以外で終わる・`ISSUE_LOOP_AGENT_TIMEOUT` を超える・止められると失敗になり、api-watch が
受け渡し先の `retry_backoff_sec` 秒（1 回ごとに倍）待ってから同じ変化を流し直す。`max_attempts` 回失敗したら打ち切り、
その issue が次に変化したときにもう一度拾う。設定例では 3 回・300 秒。

| したいこと | コマンド |
|---|---|
| 失敗している・打ち切った変化を見る | `api-watch --config ~/.config/api-watch/api-watch.yaml events --rule issue-agent --status send_failed,dead` |
| 打ち切った変化をすぐ流し直す | `api-watch --config ~/.config/api-watch/api-watch.yaml retry <受け渡しの ID>` |
| 流し終えた issue をもう一度流させる | `python3 ~/.local/share/dagu-issue-loop/issue_loop.py forget 123`（次に変化を受け取ったとき流す） |
| 流し終えた版・実行中のセッションを見る | `python3 ~/.local/share/dagu-issue-loop/issue_loop.py status` |

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

`issue-loop.env` に書く。取得の予定・ずらし・再試行の回数と間隔は api-watch の設定（`api-watch.yaml.example`）に書く。

| 環境変数 | 既定 | 意味 |
|---|---|---|
| `GITLAB_URL` / `GITLAB_API_URL` | （必須） | GitLab の URL（API の URL を直接書くなら後者） |
| `GITLAB_PROJECT` | （必須） | `group/project` か数値 ID |
| `GITLAB_TOKEN`（または `GL_TOKEN`） | （必須） | アクセストークン |
| `ISSUE_LOOP_HOME` | （必須） | `issue_loop.py` を置いたフォルダ（絶対パス。api-watch の設定から呼ぶ） |
| `ISSUE_LOOP_PYTHON` | （必須） | `issue_loop.py` を動かす Python（`python3`。Windows では `python` か `py`） |
| `ISSUE_LOOP_ISSUE_STATE` | `opened` | `opened` / `closed` / `all` |
| `ISSUE_LOOP_ISSUE_LABELS` | — | カンマ区切り。すべて持つ issue だけ |
| `ISSUE_LOOP_ISSUE_ASSIGNEE` | — | この人に割り当てられた issue だけ |
| `ISSUE_LOOP_AGENT` / `ISSUE_LOOP_AGENT_CMD` | （どちらか必須） | エージェント CLI |
| `ISSUE_LOOP_AGENT_MODEL` | — | モデル名（定義に `{model}` があるときは必須） |
| `ISSUE_LOOP_AGENT_CWD` | — | エージェントを動かすフォルダ |
| `ISSUE_LOOP_AGENT_TIMEOUT` | `3600` | 1 回の上限秒数（api-watch の設定の `timeout_sec` はこれより長く） |
| `ISSUE_LOOP_PROMPT_TEMPLATE` | — | プロンプトのテンプレートファイル |
| `ISSUE_LOOP_SELF_USERNAME` | トークンの持ち主 | 自分のユーザー名（実行中の自分以外の動きを見分ける） |
| `ISSUE_LOOP_LOCK_FILE` | 全プロジェクト共通 | セッションの排他に使うファイル |
| `ISSUE_LOOP_STATE_DIR` | `~/.agents/dagu-issue-loop` | 既読・実行ログの置き場 |

実行ごとのプロンプトと CLI の出力は `ISSUE_LOOP_STATE_DIR/<プロジェクト>/runs/` に残る（Dagu のログにも同じ出力が出る）。

## 終了コード（`handle`）

| コード | 意味 | api-watch の扱い |
|---|---|---|
| 0 | 流して成功した・流し終えた変化だった・対象から外れていた | 完了 |
| 1 | エージェント CLI が失敗した・時間切れ・止められた | あとで流し直す |
| 2 | 設定の誤り | あとで流し直す（直すまで失敗が続く） |
| 3 | GitLab に届かない・認証に失敗した | あとで流し直す |
| 4 | 別のセッションが実行中だった | あとで流し直す |

## テスト

```bash
cd tools/dagu-issue-loop && python3 -m unittest discover -s test
```

設計: [`docs/designs/dagu-issue-loop-design.md`](../../docs/designs/dagu-issue-loop-design.md)
