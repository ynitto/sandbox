# change-watch

任意の Web API（またはフォルダ）へのアクセスを、[Dagu](https://github.com/dagu-org/dagu)（v2）のワークフローから受け持つ。
どのレコードをどう処理するか（条件）はワークフローに書き、ワークフローの各ステップが change-watch を呼んで、前回処理し終えてから変わった
レコードを受け取り（`get`）、処理し終えたら記録し（`commit`）、必要なら API へ書き込む（`call`）。フォルダを見るときは、ファイル 1 つが 1 件の
レコードになる（「フォルダを見る」）。外から受ける口は持たず、こちらから API を見に行く。
起動・条件の分岐・並列数・再試行・ログ・実行履歴は Dagu に任せ、このツールは常駐しない。change-watch が受け持つのは、API への問い合わせを
まとめること: いつ問い合わせるか（取得元の `schedule`・`min_interval_sec` など。書かなければ呼ばれるたびに取得する）、同じ取得元を呼ぶ
ワークフローがいくつあっても問い合わせを 1 回にまとめること、要求の間隔・失敗の待ち・取得結果の保存。

```
 Dagu: <ワークフロー>（毎分。1 回の起動で 1 件）
   get      change-watch get --source S --consumer C --one   ← 取得は取得元の予定ごとに 1 回。前回 commit してから変わったレコードを 1 件返す
   <処理>   preconditions で ${CHANGE.record.*} を見て分ける     ← 条件は Dagu に書く。API へ書き込むなら change-watch call
   commit   change-watch commit --source S --consumer C ${CHANGE.key}
 Dagu: change-watch-maintenance（毎日）  保持期間を過ぎたものの整理
```

- Python 3.10 以降。YAML の設定を使うなら PyYAML（`pip install pyyaml`）。JSON の設定なら標準ライブラリだけで動く
- Windows（ネイティブ）・WSL2・Linux で動く。状態は 1 つの SQLite ファイルに持つ
- 認証情報はスキル共通の `connections.yaml` に書き（このリポジトリの `.github/connections.yaml.example` と同じ形）、
  そのパスを実行のたびに `--connections` で渡す。環境変数や決まった場所からは探さない
- 本体は Dagu に依存しない（Dagu を知っているのは `dags/` の例のワークフローだけ）。cron などから `get` / `commit` を呼んでも同じに動く

| ファイル | 中身 |
|---|---|
| `changewatch/` | 本体: 取得・状態・`get` / `commit` / `call`・運用のコマンド |
| `dags/*.example.yaml` | change-watch を使う Dagu のワークフローの例。コピーして名前とパスを直して使う（下の「Dagu のワークフロー」） |
| `agents/<名前>/` | change-watch の上で動くエージェント（[gitlab-issue-agent](agents/gitlab-issue-agent/README.md)・[gitlab-issue-hub-agent](agents/gitlab-issue-hub-agent/README.md)・[gitlab-mr-review-agent](agents/gitlab-mr-review-agent/README.md)・[redmine-ticket-agent](agents/redmine-ticket-agent/README.md)）。`install.py install --agent <名前>` で入れる（下の「エージェントを入れる」） |

### プラグイン

`--plugin <.py のパス>` で読み込むプラグインは、`register(api)` を持つ 1 つの `.py` ファイル。設定のテンプレートで使うフィルタを足す
（gitlab-issue-hub-agent の `gitlab_issue_api` など）。本体の DB や取得の仕組みには直接触らない。

| 口 | 足すもの | 使うところ |
|---|---|---|
| `api.add_filter(name, fn)` | テンプレートのフィルタ。`fn(値, 引数)` が値を返す | 設定のどのテンプレートでも、`call` の URL でも `{{ 値 \| name: 引数 }}` |
| `api.add_command(name, fn, help)` | サブコマンド。`fn(cfg, store, args)` が終了コードを返す | コマンド行 |

名前は使う時点で探すので、`--plugin` を付け忘れるとその時点で案内して止まる。

## 入れ方

1. インストーラで入れる。本体を 1 つの実行可能な zip（`.pyz`）にまとめ、コマンドとして置く。
   この Python に PyYAML があれば一緒にまとめるので、入れた先では PyYAML が要らない（`--no-yaml` で入れない）。

   ```bash
   python3 tools/change-watch/install.py install
   ```

   | OS | 置かれるもの |
   |---|---|
   | Linux・WSL2・macOS | `~/.local/bin/change-watch`（先頭に `#!/usr/bin/env python3` の付いた実行ファイル。`--python` で Python を指定できる） |
   | Windows | `%LOCALAPPDATA%\Programs\change-watch\change-watch.pyz` と、それを呼ぶ `change-watch.cmd`（インストーラを動かした Python で動く） |

   置き場所は `--dir`。zip だけ作るなら `install.py build --out <パス>`（`python3 <パス> ...` で動く）、消すなら `install.py uninstall`
   （状態と設定は残す）。ソースの木から直接 `python3 tools/change-watch/change_watch.py ...` で動かしてもよい。

2. 設定を書く。`change-watch.yaml.example` を `~/.config/change-watch/change-watch.yaml` にコピーして取得元（見に行く API）を書く。
   条件はここに書かない（ワークフローに書く）。

   ```bash
   mkdir -p ~/.config/change-watch
   ```

   ```bash
   cp tools/change-watch/change-watch.yaml.example ~/.config/change-watch/change-watch.yaml
   ```

3. 認証情報を `connections.yaml` に書く（置き場所は自由。下の例は `~/.github/connections.yaml`）。

   ```yaml
   gitlab:
     - label: default
       url: https://gitlab.example.com
       token: glpat-xxxxxxxx
   ```

   値の `${VAR}` は connections.yaml の書式どおり展開するが、使わなくてよい。ファイルの権限は自分だけが読めるようにする。

4. 確かめる。API には問い合わせず、設定と接続設定が読めるかだけを見る。場所とプラグインはすべてコマンド行で渡し、環境変数は見ない:

   | 引数 | 意味 |
   |---|---|
   | `--config <パス>` | 設定ファイル（省略すると `~/.config/change-watch/change-watch.yaml`） |
   | `--connections <パス>` | 接続設定（connections.yaml）。接続設定を使う取得元があれば必須 |
   | `--state-dir <パス>` | 状態の置き場所。設定の `state_dir` より優先する |
   | `--plugin <.py のパス>` | プラグイン（テンプレートのフィルタを足すもの。エージェントが使う）。繰り返し可 |

   ```bash
   change-watch --config ~/.config/change-watch/change-watch.yaml --connections ~/.github/connections.yaml check
   ```

5. 一度手で呼んで、取得できるか確かめる。初めての consumer は今あるレコードを基準として覚えるだけで、何も返さない（`--initial emit` なら全部を返す）。

   ```bash
   change-watch --config ~/.config/change-watch/change-watch.yaml --connections ~/.github/connections.yaml get --source gitlab-issues
   ```

   ```bash
   change-watch --config ~/.config/change-watch/change-watch.yaml status
   ```

6. Dagu のワークフローを置く。`dags/change-watch-get.example.yaml` をコピーし、取得元・consumer の名前と、分かれた先の条件と処理を
   自分のものに直す（下の「ワークフローから使う」）。整理のワークフローも置く。

   ```bash
   cp tools/change-watch/dags/change-watch-get.example.yaml ~/.config/dagu/dags/change-watch-<名前>.yaml
   ```

   ```bash
   cp tools/change-watch/dags/change-watch-maintenance.example.yaml ~/.config/dagu/dags/change-watch-maintenance.yaml
   ```

7. Dagu のスケジューラを動かせば（`dagu start-all` など）以後は自動で回る。

### Windows で使うとき

| もの | 置き場所 |
|---|---|
| change-watch | `python install.py install` で `%LOCALAPPDATA%\Programs\change-watch\`（`change-watch.pyz` と `change-watch.cmd`） |
| change-watch の設定 | `%USERPROFILE%\.config\change-watch\change-watch.yaml` |
| 状態 | 設定の `state_dir`（例では `%LOCALAPPDATA%\change-watch\state`）。WSL2 と共有しない |
| Dagu の設定と DAG | `%USERPROFILE%\.config\dagu\`（`config.yaml`・`dags\`） |

```cmd
python tools\change-watch\install.py install
```

```cmd
mkdir %USERPROFILE%\.config\change-watch
```

```cmd
copy tools\change-watch\change-watch.windows.yaml.example %USERPROFILE%\.config\change-watch\change-watch.yaml
```

- `install.py install` を、Dagu から使う Python で実行する。Dagu のワークフローの `command` はその Python（`python.exe`）、`args` の先頭は `change-watch.pyz` の絶対パスにする
- 取得元の書き方の例は `change-watch.windows.yaml.example`（フォルダと GitLab の issue）。パスは `/` 区切りで書いてよい。`folder.path` は `%USERPROFILE%` などの環境変数を展開する
- 状態のフォルダを Windows と WSL2 で共有しない（`/mnt/c/...` 越しの SQLite はロックが効かない）。どちらか片方で動かす

## エージェントを入れる

`agents/` の下のエージェントは、インストーラに `--agent <名前>` を付けて入れる（繰り返し可）。change-watch もいっしょに入れ直すので、
エージェントと change-watch の版がずれない。入れられるものは `install.py agents` で出る。

```bash
python3 tools/change-watch/install.py install --agent redmine-ticket-agent
```

どのエージェントも、置き場所は同じ決まり:

| 置かれるもの | 場所 | 入れ直したとき |
|---|---|---|
| プログラム（エージェントが使うコマンドの `.py`。あるものだけ） | `~/.local/share/change-watch/agents/<名前>/`（Windows は `%LOCALAPPDATA%\Programs\change-watch\agents\<名前>\`） | 新しくする |
| 設定（`change-watch.yaml`・`<名前>.env`） | `~/.config/change-watch/<名前>/` | 既にあれば触らない |
| Dagu のワークフロー（処理と、整理の `change-watch-<名前>-maintenance.yaml`） | `~/.config/dagu/dags/`（`--dagu-dir` で変える。`--no-dags` で置かない） | 既にあれば触らない |
| 状態 | `~/.local/state/change-watch/<名前>/`（設定の `state_dir`） | — |

- 設定と状態はエージェントごとに分かれている。1 台で複数のエージェントを動かしても、設定をまとめる必要はない
- 置いたワークフローと設定のパス（change-watch・プログラム・設定・接続設定 `~/.github/connections.yaml`）は、この PC のものになっている。
  Windows では、ワークフローの `command` を Python に、`args` の先頭を `change-watch.pyz` にして置く。作業フォルダなど自分で決めるものは
  `/home/you` のまま残し、インストールの最後に「自分のものに直すところ」として出す
- 条件はワークフローの `preconditions` に書いてあり、直すならそこを直す
- 新しい版の雛形を見たいときは、置いた設定を別名にしてから入れ直すか、`agents/<名前>/` の `.example` と見比べる
- 消すときは `install.py uninstall --agent <名前>`（プログラムだけを消し、設定・状態・ワークフローは残す）

使い方は各エージェントの README の「入れ方」にある。

## ワークフローから使う（`get` / `commit` / `call`）

どのレコードを処理するかはワークフロー（Dagu）の条件で決め、change-watch には API へのアクセスだけを受け持たせる。
change-watch が受け持つのは、取得をまとめること（取得元の `schedule`・`min_interval_sec`・`busy_wait_sec`・失敗の待ち・`rate_limit`。同じ取得元を
呼ぶワークフローがいくつあっても、問い合わせはまとまる）と、「前回処理し終えてから変わったレコード」を返すこと、それに書き込みを同じ接続・
同じ要求の間隔で送ること。

```
 Dagu: <ワークフロー>（毎分。1 回の起動で 1 件）
   get      change-watch get --source S --consumer C --one   → output: CHANGE（前回 commit してから変わったレコード 1 件）
   <処理>   preconditions で ${CHANGE.record.*} を見て、合えば処理する   ← 条件は Dagu に書く
   commit   change-watch commit --source S --consumer C ${CHANGE.key}
```

| コマンド | すること | 標準出力 |
|---|---|---|
| `get --source S` | 取得して（するかは取得元の予定・間隔で決める）、レコードを出す。取得が失敗しても前回までの取得結果で答える | JSON の配列 |
| `get --source S --consumer C [--watch P]… [--initial baseline\|emit] [--one]` | C が前回 `commit` してから変わったレコードを出す。`commit` するまで同じものを出し続ける。`--watch` を書けば、その項目が変わったときだけ変化にする。初めて呼ばれたときは、`baseline`（既定）なら今ある姿を覚えるだけ、`emit` なら全部を出す | 変化の配列（`--one` なら 1 件のオブジェクトか、無ければ何も出さない） |
| `commit --source S --consumer C KEY… [--refresh]` | KEY を処理し終えたと記録する。`--refresh` なら取得元の `item` で 1 件取り直した姿で記録する（処理の中で自分が書き込んだ分を、次の変化にしない） | — |
| `call METHOD URL --source S [--json J \| --json-file F] [--query K=V]…` | 取得元の接続（認証）と要求の間隔の制限で API を 1 回呼ぶ。`/` で始まる URL は接続の `url` に足す。GET 以外は自分では再試行しない（再試行はワークフローの `retry_policy` で） | 応答の JSON |

変化は `{"key", "kind", "record", "before", "changes"}` の形。`kind` は `appeared`（新しく現れた）・`changed`（`--watch` の項目が変わった）・
`removed`（一覧から消えた）、`record` は今のレコード（`removed` なら最後の姿）、`before` は前回 `commit` したときの姿（`changed` のときだけ）、
`changes` は変わった項目。Dagu では `${CHANGE.record.state}` のように中の値を条件に使える。

- consumer はワークフローごとに名前を付ける。consumer ごとに別々に変化を受け取るので、同じ取得元を複数のワークフローで使っても互いに邪魔しない
- 取得元に `key` が要る。取得条件・認証を変えたら（取得元の指紋が変わったら）、consumer の基準も取り直す
- 失敗した処理も commit まで進める（分かれた先に `continue_on.failure`）。やり直しは Dagu の `retry_policy` に任せ、尽きたら Dagu の履歴に失敗として残る。
  commit しないでおくと、次の起動でも同じ変化が返り、毎分やり直し続ける
- 処理の中で自分が書き込む（コメントを付けるなど）なら `commit --refresh` にする。書き込んだ後の姿を基準にするので、自分の書き込みを次の変化にしない
- エージェントの例（インストーラが置くもの）も、どれもこの形で書いてある（[gitlab-issue-agent](agents/gitlab-issue-agent/README.md)・
  [gitlab-issue-hub-agent](agents/gitlab-issue-hub-agent/README.md)・[gitlab-mr-review-agent](agents/gitlab-mr-review-agent/README.md)・[redmine-ticket-agent](agents/redmine-ticket-agent/README.md)）

### 条件で分けるワークフローの例

`dags/change-watch-get.example.yaml`。取得元 `items` の `status` が変わったレコードを 1 件受け取り、`urgent` ならスクリプトを動かし、
`done` になったら `call` で API に書き込み、最後に commit する（抜粋）。

```yaml
schedule: "* * * * *"
type: graph
steps:
  - name: get
    action: exec
    with:
      command: "/home/you/.local/bin/change-watch"
      args: [--connections, /home/you/.github/connections.yaml, get, --source, items,
             --consumer, triage, --watch, status, --one]
    retry_policy: { limit: 3, interval_sec: 60, exit_code: [3] }   # 一時的な失敗（終了コード 3）だけ再試行する
    output: CHANGE
  - name: urgent
    depends: [get]
    preconditions:                       # 条件は Dagu に書く
      - condition: "${CHANGE.record.status}"
        expected: "urgent"
    continue_on: { skipped: true, failure: true }   # 合わなくても、失敗しても commit へ進む
    action: exec
    with: { command: python3, args: [/path/to/urgent.py, "${CHANGE}"] }
  - name: archive
    depends: [urgent]
    preconditions:
      - condition: "${CHANGE.kind}"
        expected: "changed"
      - condition: "${CHANGE.record.status}"
        expected: "done"
    continue_on: { skipped: true, failure: true }
    action: exec
    with:
      command: "/home/you/.local/bin/change-watch"
      args: [--connections, /home/you/.github/connections.yaml, call, POST, "/items/${CHANGE.key}/archive",
             --source, items, --json, '{"reason": "done"}']
  - name: commit
    depends: [archive]
    preconditions:                       # 受け取った変化があるときだけ
      - condition: "${CHANGE.key}"
        expected: "re:."
    action: exec
    with:
      command: "/home/you/.local/bin/change-watch"
      args: [--connections, /home/you/.github/connections.yaml, commit, --source, items, --consumer, triage, "${CHANGE.key}"]
```



### いつ取得するか（change-watch の予定と Dagu の予定）

`schedule` は「Dagu がいつ起動するか」ではなく「API にいつ問い合わせるか」で、change-watch が起動されたときに見る。
Dagu の起動間隔はワークフローの `schedule` に書く。どちらの予定でも動く:

| 書き方 | DAG の `schedule` | 取得元の設定 | 動き |
|---|---|---|---|
| change-watch の予定 | 毎分（例のワークフローのまま） | `schedule: "0 * * * *"` | 毎分起動し、直近の予定の時刻（毎時 0 分）のあとにまだ誰も取得し終えていなければ取得する（取得済みなら保存済みの結果で答える） |
| Dagu の予定 | `"0 * * * *"` などに書き換える | `schedule` を書かない | 起動されるたびに取得する。`min_interval_sec` があれば、その秒数以内の取得はしない |

予定の時刻は、この PC の時刻（ローカル時刻）で見る。cron の 5 項目のほか `@hourly`・`@daily` なども使える。

例のワークフローは毎分起動する。取得元に `schedule` も `min_interval_sec` も無ければ毎分問い合わせることになるので、
API の取得元ならどちらかを書くか、ワークフローの `schedule` を問い合わせてよい間隔にする（フォルダの取得元は毎分でよい）。

### 同じ API を呼ぶワークフローが重なったとき

同じ取得元を `get` するワークフロー（`triage` と `notify` など）が同じ時刻に起動しても、API への問い合わせは 1 回になる:

| 起きたこと | どうなるか |
|---|---|
| 片方が取得し終えたあとに、もう片方が `get` した | 予定の時刻のあとに取得済み（`schedule`）か、`min_interval_sec` の間なら、取得せず（ずらしもせず）その結果で答える |
| 片方が取得している最中に、もう片方が `get` した | `busy_wait_sec` まで終わるのを待ち、その結果で答える（待たないなら前回までの結果で答える） |
| 両方がずらしている間に、片方が取得した | もう片方はずらし終えたあとで取得済みかを見直して、取得しない |
| 取得が失敗した | `failure_backoff_sec` のあいだ、どのワークフローも問い合わせず、前回までの取得結果で答える |

2 本以上のワークフローが同じ取得元を `get` するなら、`schedule` か、`min_interval_sec` と `busy_wait_sec` を書く（書かなければ起動のたびに
問い合わせる）。`jitter_sec` は `min_interval_sec` より短くする。

### 別の取得元とも API への負荷を分け合う（`rate_limit.group`）

同じ API を、条件の違う複数の取得元（issue と MR など）で見るときは、`rate_limit.group` に同じ名前を書く。同じ group の取得元は:

- 同時には取得しない（片方の取得中は、もう片方は `busy_wait_sec` まで待つ）
- `rate_limit.min_interval_sec`・`per_minute` を group の全部の要求で数える（group の中では同じ値を書く。違えば設定の誤り）
- どれかの取得が失敗したら、group の全部が `failure_backoff_sec` のあいだ問い合わせない

## 設定

### 取得元（sources）

1 つの取得元が 1 種類の取得（URL・条件・認証の組）。いくつのワークフロー（consumer）からでも、この取得結果を共有する。

| 項目 | 既定 | 意味 |
|---|---|---|
| `connection` | — | `--connections` で渡したファイルの `サービス` か `サービス:ラベル`。値は `{{ conn.項目 }}` で参照する |
| `request.method` / `url` / `headers` / `query` | `GET` | 要求。`{{ conn.* }}`・`{{ env.* }}`・`{{ position }}`（増分の位置）・`{{ now }}` / `{{ now_iso }}` を埋められる |
| `request.body` / `body_form` | — | JSON の本文（辞書・配列）か文字列 / フォームの本文 |
| `request.timeout_sec` | 30 | 1 回の要求の制限時間 |
| `records` | `$` | 応答の中のレコードの配列の場所（例 `data.items`）。オブジェクトなら 1 件として扱う |
| `key` | — | レコードを一意に指す項目。増分取得の取得元と、`get --consumer` で変化を受け取る取得元では書く（全体の姿を組み立て、consumer ごとの変化を見分ける） |
| `item.url` / `headers` / `query` / `records` | — | レコードを 1 件取り直す要求（`commit --refresh`）。`{{ key }}`・`{{ conn.* }}`・`{{ env.* }}` が使える。`headers` が無ければ取得元のもの |
| `pagination` | なし | 下の表 |
| `incremental` | なし | 下の表 |
| `schedule` | なし | 問い合わせの予定（cron。この PC の時刻）。起動されても、直近の予定の時刻のあとに取得し終えていれば取得しない。無ければ起動されるたびに取得する（Dagu の起動間隔に任せる）。`fetch --force` で無視。上の「いつ取得するか」 |
| `min_interval_sec` | 0 | 前回の成功からこの秒数たつまでは取得せず、その取得結果を使う（手で叩いたとき・複数のワークフローから呼ばれたときの重複を防ぐ。`fetch --force` で無視） |
| `jitter_sec` | 0 | 取得の前に 0〜この秒数だけランダムに待つ（定刻に API へ集中させない）。`min_interval_sec` で取得しないときは待たない。`fetch --no-jitter`・`--force` でずらさない |
| `busy_wait_sec` | 0 | 別のプロセスが取得中なら、この秒数まで終わるのを待ち、その取得結果を使う。0 なら待たずに終わる |
| `failure_backoff_sec` / `failure_max_backoff_sec` | 60 / 3600 | 取得が失敗したら、どのプロセス（ワークフロー）もこの秒数は問い合わせず、前回までの取得結果を使う。続けて失敗するほど倍にし、上限で止める。成功したら戻す。`--force` では待たない |
| `rate_limit.min_interval_sec` / `per_minute` | 0 / 0 | 要求どうしの最小間隔・1 分あたりの上限。前の実行（別のワークフローを含む）の要求からも数える |
| `rate_limit.group` | なし | 同じ名前の取得元どうしで、同時に取得せず、要求の間隔・1 分あたりの上限・失敗の待ちを分け合う（上の「別の取得元とも API への負荷を分け合う」） |
| `rate_limit.max_wait_sec` | 120 | `Retry-After` やレート制限のリセットをこの秒数までは待つ。長ければ取得をやめ、その時刻までは問い合わせない |
| `retry.attempts` / `backoff_sec` / `max_backoff_sec` | 4 / 2 / 60 | 一時的な失敗（接続・タイムアウト・408/425/429/5xx）の再試行（指数バックオフ） |
| `conditional` | true | `ETag` / `Last-Modified` があれば条件付き要求にし、304 なら保存済みの本文を使う |
| `resume` / `resume_max_age_sec` | true / 3600 | 途中で止まった取得を、この秒数以内なら続きのページから再開する |
| `lease_sec` | 300 | 取得中の印の有効期限（ページごとに延ばす）。別の PC で動いていたプロセスが止まったときは、この秒数のあと引き継ぐ |

ページング（`pagination.type`）:

| type | 主な項目 | 終わりの判定 |
|---|---|---|
| `page` | `param`（`page`）・`start`（1）・`size_param`・`size`・`total_pages_path` | 件数が `size` 未満・0 件・総ページ数に達した |
| `offset` | `param`（`offset`）・`start`（0）・`size_param`（`limit`）・`size`（必須）・`total_path` | 件数が `size` 未満・0 件・総数に達した |
| `cursor` | `param`（`cursor`）・`cursor_path`（必須）・`has_more_path` | カーソルが無い・空・`has_more` が偽 |
| `next_url` | `next_url_path`（必須） | 次の URL が無い |
| `link_header` | — | `Link: <...>; rel="next"` が無い |

`in: body` でページ番号やカーソルを JSON の本文に入れる。次ページの URL が別のホストなら止める（認証情報を渡さないため。
意図したものなら `allow_cross_host: true`）。`max_pages`（1000）を超えたら、取りこぼさないよう取得を完了にしない。

増分取得（`incremental`）:

| type | 項目 | 動き |
|---|---|---|
| `max_value` | `field`・`param`・`initial` | 取得したレコードの `field` の最大値を覚え、次回は `param=<その値>` を付ける |
| `cursor` | `cursor_path`・`param` | 応答の `cursor_path`（同期トークンなど）を覚え、次回は `param=<その値>` を付ける |

位置は**全ページを取り終えたときだけ**進む。途中で失敗したら同じ位置から取り直す。`param` を書かずに
`{{ position }}` を URL や本文に直接埋めてもよい。境界のレコードが 2 回取れても、値が変わっていなければ変化にしない。

### フォルダを見る（`folder`）

取得元に `request` の代わりに `folder` を書くと、フォルダの中のファイルを 1 件ずつのレコードにする（`key` は `rel`）。`get --consumer` では、
ファイルが増えたら `appeared`、更新されたら `changed`、消えたら `removed` になる。Dagu からは毎分起動してよい（フォルダを見るだけなので軽い）。

```yaml
sources:
  inbox:
    folder:
      path: /data/inbox            # 相対パスは設定ファイルの場所から
      include: ["*.csv"]           # 下のフォルダも見るなら "**/*.csv"
```

ワークフローでは `get --source inbox --consumer import --one` で 1 件受け取り、`${CHANGE.record.path}` を処理に渡す
（新しいファイルだけなら、`${CHANGE.kind}` が `appeared` のときだけ処理する条件を書く）。

| 項目 | 既定 | 意味 |
|---|---|---|
| `folder.path` | （必須） | 見るフォルダ。`~` と環境変数（`$HOME`・`%USERPROFILE%`）を展開する |
| `folder.include` / `exclude` | `["*"]` / なし | 対象にするファイル・外すファイルの glob（フォルダからの相対パス、`/` 区切り）。`*` はフォルダをまたがず、`**` は何階層でもまたぐ。`include` が `/` か `**` を含むときだけ下のフォルダを見る |
| `folder.hidden` | false | 名前が `.` で始まるファイル・フォルダも見る |
| `folder.settle_sec` | 5 | 更新時刻からこの秒数たっていないファイルは、書き込みの途中とみなす。新しいファイルはまだ無いものとし、前からあるファイルは前回の姿のまま扱う |
| `folder.hash` | false | 中身の SHA-256 を `sha256` に入れる。大きさも更新時刻も変わらない書き換えも `changed` になる |
| `folder.content` | `none` | `text` か `json` で、中身を `content` に入れる（UTF-8）。読めなければ `content` を入れず `content_error`（`too_large`・`not_utf8`・`invalid_json`） |
| `folder.max_bytes` | 1048576 | `hash`・`content` で読む上限のバイト数。超えたファイルは読まない |
| `folder.max_files` | 10000 | ファイル数がこれを超えたら、取りこぼさないよう取得を完了にしない |

レコードの項目:

| 項目 | 例 | 意味 |
|---|---|---|
| `rel` | `sub/a.csv` | フォルダからの相対パス（`/` 区切り）。レコードの `key` |
| `path` | `/data/inbox/sub/a.csv` | この PC でのパス |
| `name` / `stem` / `ext` / `dir` | `a.csv` / `a` / `.csv` / `sub` | 名前・拡張子を除いた名前・拡張子・相対パスのフォルダ |
| `size` / `mtime` / `mtime_iso` | `120` / `1762300800.5` / `2025-11-05T00:00:00.500000Z` | 大きさ・更新時刻（秒と UTC の ISO 8601） |
| `sha256` / `content` / `content_error` | — | `hash`・`content` を書いたときだけ |

- フォルダが無い・読めないとき（ネットワークのフォルダが切れたときなど）は、一時的な失敗（終了コード 3）にする。全部のファイルが「消えた」にはならない
- `connection`・`records`・`key`・`pagination`・`incremental`・`conditional`・`rate_limit`・`retry`・`item` は書けない
- 処理したファイルを動かしたり消したりしなくてよい。同じファイルは、変わらない限り 2 回返さない。動かしたり消したりすると `removed` になる
- `path` を変えると、別のフォルダとして consumer の基準を取り直す（その時にあるファイルは返さない。`--initial emit` なら返す）

### 保持期間（retention）

| 項目 | 既定 | 意味 |
|---|---|---|
| `fetches_keep` | 3 | 取得結果を最低何回分残すか |
| `fetch_days` | 7 | これより古い取得結果を消す（最新の `fetches_keep` 回分は残す） |
| `record_days` | なし（消さない） | 増分取得で組み立てた全体の姿（`key` を書いた取得元）から、この日数取得されていないレコードを消す。consumer には `removed` として返り、次に取得されたとき `appeared` になる |
| `auto_gc` | true | 取得のたびに整理する（整理の DAG と同じこと） |

整理のたびに、消した行の領域を DB ファイルから返し、WAL ファイルも切り詰めるので、ファイルは保持期間に見合った大きさで止まる。
`status` に DB・WAL の大きさが出る。このツールの古い版で作った DB は領域を自動で返さない形なので、
`status` に案内が出たら一度 `gc --vacuum`（DB を作り直す。大きいと時間がかかる）を流す。

状態の置き場所は `state_dir`（既定 `~/.local/state/change-watch`。コマンド行の `--state-dir` で上書き）。

### 接続設定

認証情報は `--connections <パス>` で渡した connections.yaml（または .json）からだけ読む。環境変数・設定ファイル・決まった場所からは探さない。
接続設定を使う取得元があるのに `--connections` が無ければ、問い合わせる前に止める。

## Dagu のワークフロー

change-watch を使う Dagu のワークフローは、`dags/` の例をコピーして書く。`/home/you` は自分の場所に、`items`・`triage` などの名前は
自分の設定のものに直す。

| 例 | 置く名前 | ステップ |
|---|---|---|
| `change-watch-get.example.yaml` | `change-watch-<名前>.yaml` | get → 条件で分かれた処理 → commit |
| `change-watch-maintenance.example.yaml` | `change-watch-maintenance.yaml` | gc（いつも 1 本置く） |

- get は、一時的な失敗（終了コード 3）のときだけ Dagu に再試行させる（`retry_policy.exit_code: [3]`）。設定の誤りや認証の失敗は再試行しない
- get の `timeout_sec` は、取得元の `jitter_sec`・`busy_wait_sec` を書いたらその分を足す
- プラグイン（`gitlab_hub.py` など）を使うときは、どのステップにも同じ `--plugin` を足す
- 設定ファイルが既定の `~/.config/change-watch/change-watch.yaml` でなければ、どのステップの `args` の先頭にも `"--config"` と `<パス>` を足す
- 例は試験で、Dagu と同じ順に本物の change-watch へ流して確かめている（`test/test_dag_examples.py`）

## 運用

| コマンド | すること |
|---|---|
| `check` | 設定と接続設定が読めるか（API には問い合わせない） |
| `status` / `status --json` | 状態のファイルの大きさ、取得元ごとの最後の取得結果・エラー・途中の取得・待たされている時刻、consumer ごとの処理し終えた件数と最後の `commit` |
| `records --source S [--fetch N]` | 保存済みの取得結果のレコード（`--watch` やワークフローの条件を確かめる） |
| `fetch [--source S] [--force] [--no-jitter]` | 取得だけをする。`--force` は予定・間隔・失敗の待ちを無視する |
| `gc [--dry-run] [--vacuum]` | 保持期間を過ぎたものを消し、ファイルを縮める。`--vacuum` は DB を作り直して詰める |

ログ・エラー・状態には認証情報を出さない（接続設定の値・認証のヘッダー・URL のトークンらしい引数は `***` に置き換える）。

終了コード: 0 成功（取得中・待機中で何もしなかったときも。`get` は取得に失敗しても前回までの結果で答えて 0）/ 1 設定の誤り・再試行しても
直らない失敗（認証・404 など）/ 3 一時的な失敗（例のワークフローはこれだけ再試行する）。

## 止まったとき・失敗したとき

| 起きたこと | どうなるか |
|---|---|
| 取得の途中で失敗・プロセスや PC が停止 | 取ったページは保存済み。次の取得は続きのページから（`resume_max_age_sec` を過ぎたら最初から）。全ページそろうまで `get` には使わず、増分の位置も進めない |
| 取得中にもう 1 つ取得が始まった | `busy_wait_sec` まで待つか、待たずに前回までの結果で答える。取得中のプロセスが死んでいれば（同じ PC なら即座に、別の PC からは `lease_sec` 後に）引き継ぐ |
| 処理が失敗した | `commit` しなければ、次の起動でも同じ変化が返る。例のワークフローは失敗しても `commit` し、やり直しは Dagu の `retry_policy` に任せる |
| 取得条件・認証を変えた | 前の条件で取ったデータ（取得結果・位置・ETag の控え）を使わず取り直し、consumer の基準も取り直す |

## 0.7 からの移行

0.8.0 で、条件を change-watch の設定に書く形（ルール `rules` と受け渡し `delivery`、`evaluate`・`dispatch`・`work`・`claim`・`render`・
`finish`・`run`・`ack`・`retry`・`skip`・`events` などのコマンド、`--plugin dagu`）をなくした。条件は Dagu のワークフローに書き、
change-watch は `get` / `commit` / `call` で使う。

- 設定に `rules` か `delivery` が残っていると、どのコマンドも案内を出して止まる。`dags/change-watch-get.example.yaml` を元に、
  ルールの `filter`・`condition` をワークフローの `preconditions` に、受け渡し先を分かれた先のステップに移してから消す
- エージェントは `install.py install --agent <名前>` で入れ直すと、新しい形のワークフローが置かれる（置いた設定とワークフローが既にあれば
  触らないので、古いものは別名にしてから入れ直す）
- 状態の DB に残ったルールと受け渡しの表は、次の `gc` が消す。consumer はルールの判定状態を引き継がないので、初めての `get --consumer` は
  今ある姿を覚えるところから始まる（`--initial emit` なら今あるものを全部返す）

## 限界

- ポーリングなので、取得の間隔の間に起きて元に戻った変化（A→B→A の B）は見えない。`get --consumer` は前回 `commit` した姿と今の姿を
  比べるので、`commit` までの間に何度変わっても 1 回の変化として返す。**すべての変化が要るなら、API が提供するイベント履歴・変更履歴を
  取得元にする**（履歴の各行をレコードとし、`key` を履歴の ID にして、`appeared` だけを処理する）
- ページ番号・オフセットのページングは、取得中にデータが増減するとページの境目がずれる。並び順を更新日時の昇順にするか、
  カーソル方式を使う
- 増分取得では「消えた」を判定できない（取得されないだけと区別できない）
- フォルダの取得元も毎回フォルダ全体を一覧するので、ファイルが何十万もあるフォルダには向かない（`max_files` で止まる）。更新時刻を変えずに中身だけ書き換えるファイルは、`hash: true` のときだけ検知できる

## テスト

実 API・Dagu 本体なしで、偽の API（手元の HTTP サーバ）で確かめる。

```bash
python3 -m unittest discover -s tools/change-watch/test
```

consumer ごとの変化と `commit`・書き込み（`call`）、複数のワークフローでの取得の集約・予定・失敗の待ち・`rate_limit.group`、
各ページング・増分取得・条件付き要求・一時的な失敗の再試行・途中からの再開・強制終了からの引き継ぎ・保持期間・認証情報の伏せ字、
接続設定を `--connections` からだけ読むこと、プラグインのフィルタ、インストーラが作る 1 ファイル（`.pyz`）がそれだけで動くこと
（同梱の PyYAML・インストールと削除）、フォルダの取得元、`dags/` の例のワークフローを Dagu と同じ順で流して動くことを見る。
設計は `docs/designs/change-watch-design.md`。
