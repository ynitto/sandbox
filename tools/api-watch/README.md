# api-watch

任意の Web API を [Dagu](https://github.com/dagu-org/dagu)（v2）の定期実行からポーリングし、レコードの追加・変更・
条件の成立を検知して、設定した Dagu のワークフロー（DAG）へ渡す。外から受ける口は持たず、こちらから API を見に行く。
起動・キュー・並列数・再試行・ログ・実行履歴は Dagu に任せ、このツールは常駐しない。
API にいつ問い合わせるかは、起動されたときに api-watch が取得元ごとに決める（取得元の `schedule`・`min_interval_sec` など。
書かなければ起動されるたびに取得する＝Dagu の起動間隔どおり）。

```
 Dagu: api-watch-<取得元>（毎分。取得するかは取得元の schedule を見て api-watch が決める）
   fetch     API を全ページ取得して保存        ← 1 つの取得元への問い合わせは、ルールがいくつあっても 1 回
   evaluate  保存した取得結果をルールごとに判定  ← API には問い合わせない
   dispatch  検知した変更を DAG のキューへ入れる  ← 実行 ID = 受け渡しの ID（渡し直しても 1 回だけ実行される）
                                 │
                                 ▼
 Dagu: 受け渡し先の DAG（キューで並列に実行。失敗は Dagu で再試行・復旧）── 終わったら ack
 Dagu: api-watch-dispatch（毎分）  未判定の取得結果の判定・渡せなかった変更の再送
 Dagu: api-watch-maintenance（毎日） 保持期間を過ぎたものの整理
```

- Python 3.10 以降。YAML の設定を使うなら PyYAML（`pip install pyyaml`）。JSON の設定なら標準ライブラリだけで動く
- Windows（ネイティブ）・WSL2・Linux で動く。状態は 1 つの SQLite ファイルに持つ
- 認証情報はスキル共通の `connections.yaml` に書き（このリポジトリの `.github/connections.yaml.example` と同じ形）、
  そのパスを実行のたびに `--connections` で渡す。環境変数や決まった場所からは探さない

### 本体と Dagu プラグインの分け方

| ファイル | 中身 | Dagu への依存 |
|---|---|---|
| `apiwatch/`（下の `plugins/dagu.py` を除く） | 本体: 取得・判定・状態・受け渡しの順序と再送・運用のコマンド。受け渡しは `delivery.adapter` で選ぶアダプタに任せる | なし |
| `apiwatch/adapters/command.py` | 任意のコマンドで受け渡すアダプタ（`adapter: command`） | なし |
| `apiwatch/plugins/__init__.py` | `--plugin` で指定されたプラグインの読み込み。同梱のプラグインを自分では import しない | なし |
| `apiwatch/plugins/dagu.py` | Dagu プラグイン: Dagu へ受け渡すアダプタ（`adapter: dagu`）と、Dagu の DAG を書き出す `dags` コマンド。**Dagu に依存するものはこの 1 ファイルだけ** | あり |

プラグインはコマンド行の `--plugin` で指定したときだけ読み込む。Dagu と使うときは毎回 `--plugin dagu` を付ける
（`dags` が書き出す DAG にも付く）。付けなければ `dags` コマンドも `adapter: dagu` も無く、`adapter: dagu` の設定で受け渡そうとすると
「`--plugin dagu` を付けて」と言って止まる（受け渡しの試行回数は減らない）。`status`・`events`・`ack` などアダプタを使わないコマンドは付けなくても動く。

1 本のワークフローの中で取得から処理まで済ませる形もある（「1 つのワークフローで取得から処理まで」）。

Dagu を使わないときは、起動を cron・タスク スケジューラなどから `run`（または `fetch` / `evaluate` / `dispatch`）で行い、
受け渡しは `adapter: command` で任意のコマンドに渡す。別の基盤へのプラグインも、同じ形の `.py` を `--plugin <パス>` で読み込める
（`register(api)` で `api.add_adapter(...)`・`api.add_command(...)` を呼ぶ。`apiwatch/plugins/__init__.py` を参照）。

## 入れ方

1. インストーラで入れる。本体と同梱のプラグインを 1 つの実行可能な zip（`.pyz`）にまとめ、コマンドとして置く。
   この Python に PyYAML があれば一緒にまとめるので、入れた先では PyYAML が要らない（`--no-yaml` で入れない）。

   ```bash
   python3 tools/api-watch/install.py install
   ```

   | OS | 置かれるもの |
   |---|---|
   | Linux・WSL2・macOS | `~/.local/bin/api-watch`（先頭に `#!/usr/bin/env python3` の付いた実行ファイル。`--python` で Python を指定できる） |
   | Windows | `%LOCALAPPDATA%\Programs\api-watch\api-watch.pyz` と、それを呼ぶ `api-watch.cmd`（インストーラを動かした Python で動く） |

   置き場所は `--dir`。zip だけ作るなら `install.py build --out <パス>`（`python3 <パス> ...` で動く）、消すなら `install.py uninstall`
   （状態と設定は残す）。ソースの木から直接 `python3 tools/api-watch/api_watch.py ...` で動かしてもよい。

2. 設定を書く。`api-watch.yaml.example` を `~/.config/api-watch/api-watch.yaml` にコピーして取得元とルールを書く。

   ```bash
   mkdir -p ~/.config/api-watch
   ```

   ```bash
   cp tools/api-watch/api-watch.yaml.example ~/.config/api-watch/api-watch.yaml
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
   | `--config <パス>` | 設定ファイル（省略すると `~/.config/api-watch/api-watch.yaml`） |
   | `--connections <パス>` | 接続設定（connections.yaml）。接続設定を使う取得元・受け渡し先があれば必須 |
   | `--state-dir <パス>` | 状態の置き場所。設定の `state_dir` より優先する |
   | `--plugin <名前か .py のパス>` | プラグイン。Dagu と使うなら `--plugin dagu`。繰り返し可 |

   ```bash
   api-watch --plugin dagu --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml check
   ```

5. 一度手で回す。初回は今あるレコードを基準値として覚えるだけで、受け渡し先は起こさない（ルールの `initial: emit` なら起こす）。

   ```bash
   api-watch --plugin dagu --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml run
   ```

   ```bash
   api-watch --config ~/.config/api-watch/api-watch.yaml status
   ```

6. Dagu の DAG の雛形を書き出す（`--plugin dagu` のときだけあるコマンド）。取得元ごとの DAG・再送の DAG・整理の DAG ができる。
   DAG には、今の Python・この `api-watch`（`.pyz`）・設定ファイル・`--connections` で渡したパスの絶対パスと `--plugin dagu` が入る。
   作ったあとは自由に直してよい（既にあるファイルは上書きしない。上書きするなら `--force`）。

   ```bash
   api-watch --plugin dagu --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml dags --out ~/.config/dagu/dags
   ```

   `dags` の追加の引数: `--schedule`（取得する DAG を Dagu が起動する間隔。既定は毎分で、取得するかは api-watch が決める。下の「いつ取得するか」）・
   `--force`（既にある DAG を上書きする）・`--dotenv <ファイル>`（DAG に読み込ませる環境変数のファイル。繰り返し可）・`--dispatch-schedule`（再送の DAG、既定は毎分）・
   `--maintenance-schedule`（整理の DAG、既定は毎日 3:17）・`--hist-retention-days`（14）・`--prefix`（`api-watch`）・`--python`・`--script`。

7. 受け渡し先の DAG を用意する（下の「受け渡し先の DAG」）。Dagu のスケジューラを動かせば（`dagu start-all` など）以後は自動で回る。

Dagu を使わないときは、6・7 の代わりに cron などから `api-watch ... run` を定期的に呼び、`delivery.adapter: command` で受け渡す（`--plugin` は要らない）。

### Windows で使うとき

- `install.py install` を、Dagu から使う Python で実行する。`dags` はその Python（`python.exe`）と `api-watch.pyz` の絶対パスで DAG を書く
- `delivery.executable` に `dagu.exe` の場所を書く（PATH にあれば `dagu` のままでよい）
- 状態のフォルダを Windows と WSL2 で共有しない（`/mnt/c/...` 越しの SQLite はロックが効かない）。どちらか片方で動かす

## Dagu での設定例

GitLab の issue を 10 分ごとに見て、新しい issue・状態やラベルの変化を `issue-handler` という DAG へ渡す例。
Linux・WSL2 のパスで書く（Windows は最後の「Windows のとき」）。Dagu の設定と DAG の置き場所は Dagu の既定
（`~/.config/dagu/config.yaml`・`~/.config/dagu/dags/`）にしている。

### 1. 接続設定（`~/.github/connections.yaml`）

```yaml
gitlab:
  - label: default
    url: https://gitlab.example.com
    token: glpat-xxxxxxxx
```

### 2. api-watch の設定（`~/.config/api-watch/api-watch.yaml`）

```yaml
version: 1
state_dir: ~/.local/state/api-watch

delivery:
  adapter: dagu              # 実行時に --plugin dagu を付ける
  mode: cli                  # dagu enqueue で渡す
  executable: dagu           # PATH に無ければ dagu のフルパス

sources:
  issues:
    connection: gitlab
    request:
      url: "{{ conn.url }}/api/v4/projects/123/issues"
      headers:
        PRIVATE-TOKEN: "{{ conn.token }}"
      query: { state: all, order_by: updated_at, sort: asc }
    key: iid
    pagination: { type: page, size_param: per_page, size: 100 }
    incremental: { type: max_value, field: updated_at, param: updated_after }
    schedule: "*/10 * * * *"   # 問い合わせの予定。DAG は毎分起動し、api-watch が 10 分ごとに取得する

rules:
  issue-changed:
    source: issues
    id: iid
    watch: [state, labels, title]
    unordered: [labels]
    on: [appeared, changed]
    targets:
      - workflow: issue-handler          # 受け渡し先の DAG の名前（= DAG のファイル名）
        params:
          ISSUE_TITLE: "{{ event.after.title | default: '' }}"
```

`check` で設定と接続設定を確かめ、一度手で回して基準値を取る（初回は受け渡さない）。

```bash
api-watch --plugin dagu --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml check
```

```bash
api-watch --plugin dagu --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml run
```

### 3. Dagu のキュー（`~/.config/dagu/config.yaml`）

受け渡し先の DAG をいくつ同時に動かすかは、Dagu のキューで決める。別の issue の変化は、ここで決めた数まで並んで動く
（同じ issue の変化は api-watch が順番に渡すので、ここでは気にしなくてよい）。

```yaml
queues:
  enabled: true
  config:
    - name: api-watch-handlers
      max_concurrency: 3
```

### 4. 取得・再送・整理の DAG を書き出す

```bash
api-watch --plugin dagu --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml dags --out ~/.config/dagu/dags
```

`~/.config/dagu/dags/` に 3 つできる。雛形なので、作ったあとは自由に直してよい（`dags` は既にあるファイルを上書きしない）。

| ファイル | 起動 | すること |
|---|---|---|
| `api-watch-issues.yaml` | 毎分（取得は取得元の `schedule` どおり 10 分ごと） | fetch → evaluate → dispatch。fetch は通信の失敗（終了コード 3）のときだけ Dagu が再試行する |
| `api-watch-dispatch.yaml` | 毎分 | 判定し残し・渡せなかった変化の再送・後続処理の完了の確認 |
| `api-watch-maintenance.yaml` | 毎日 3:17 | 保持期間を過ぎたものの整理 |

書き出される DAG の中身（取得の DAG の一部）:

```yaml
description: "issues を取得し、ルールで判定して受け渡す"
schedule: "* * * * *"
type: graph
steps:
  - name: fetch
    action: exec
    with:
      command: "/usr/bin/python3"
      args:
        - "/home/you/.local/bin/api-watch"
        - "--config"
        - "/home/you/.config/api-watch/api-watch.yaml"
        - "--plugin"
        - "dagu"
        - "--connections"
        - "/home/you/.github/connections.yaml"
        - "fetch"
        - "--source"
        - "issues"
    retry_policy:
      limit: 3
      interval_sec: 60
      backoff: true
      max_interval_sec: 600
      exit_code: [3]
  - name: evaluate
    depends: [fetch]
    # …（evaluate --source issues）
  - name: dispatch
    depends: [evaluate]
    # …（dispatch --source issues）
```

### 5. 受け渡し先の DAG（`~/.config/dagu/dags/issue-handler.yaml`）

ファイル名（拡張子を除く）がルールの `workflow` と同じになるように置く。変化の中身は `APIWATCH_EVENT_FILE` の JSON で受け取り、
終わったら `ack` で完了を知らせる（`ack` には `--connections` も `--plugin` も要らない）。

```yaml
params:
  - APIWATCH_EVENT_ID: ""
  - APIWATCH_DELIVERY_ID: ""
  - APIWATCH_RECORD_ID: ""
  - APIWATCH_KINDS: ""
  - APIWATCH_EVENT_FILE: ""
  - ISSUE_TITLE: ""                     # ルールの targets[].params で渡したもの
queue: api-watch-handlers               # 3 で作ったキュー
steps:
  - name: handle
    action: exec
    with:
      command: python3
      args: [/home/you/bin/handle_issue.py, "${params.APIWATCH_EVENT_FILE}"]
    retry_policy:                       # 後続処理の失敗は Dagu で再試行する
      limit: 3
      interval_sec: 60
handler_on:
  success:
    action: exec
    with:
      command: python3
      args: [/home/you/.local/bin/api-watch, --config, /home/you/.config/api-watch/api-watch.yaml,
             ack, "${DAG_RUN_ID}"]
  failure:
    action: exec
    with:
      command: python3
      args: [/home/you/.local/bin/api-watch, --config, /home/you/.config/api-watch/api-watch.yaml,
             ack, "${DAG_RUN_ID}", --failed]
```

`handle_issue.py` は渡された JSON を読むだけでよい（中身は「受け渡し先の DAG」を参照）:

```python
import json, sys
event = json.load(open(sys.argv[1], encoding="utf-8"))
print(event["record_id"], event["kinds"], [c["path"] for c in event["changes"]])
```

### 6. 動かす

Dagu のスケジューラ（画面も使うなら `start-all`）を動かす。以後は 10 分ごとに取得し、変化があれば `issue-handler` が動く。

```bash
dagu start-all
```

```bash
api-watch --config ~/.config/api-watch/api-watch.yaml status
```

Dagu の画面では、受け渡し先の DAG の実行 ID が api-watch の受け渡しの ID（`aw_…`）になっている。`api-watch ... show aw_…` で、その実行に
渡した変化の中身を見られる。

### REST API で渡すとき

Dagu を別のユーザー・別のマシンで動かしているなど、`dagu` コマンドを直接呼べないときは REST API で渡す。後続処理の完了も
再送の DAG（`reconcile`）が Dagu に問い合わせて記録するので、受け渡し先の DAG の `handler_on`（ack）は無くてよい。

```yaml
# api-watch.yaml
delivery:
  adapter: dagu
  mode: api
  connection: dagu
```

```yaml
# connections.yaml に足す（Dagu の API トークン。ユーザー名とパスワードなら user と password）
dagu:
  - label: default
    url: http://localhost:8080
    token: dagu-api-token
```

### Windows のとき

- api-watch は `python install.py install` で入れる（`%LOCALAPPDATA%\Programs\api-watch\` に `api-watch.pyz` と `api-watch.cmd`）。
  `dags` は、その Python と `api-watch.pyz` のフルパスで DAG を書く
- Dagu の設定と DAG の置き場所は `C:\Users\<名前>\.config\dagu\`（`config.yaml`・`dags\`）
- `delivery.executable` は `dagu.exe` のフルパス（PATH にあれば `dagu`）
- 受け渡し先の DAG の `command` は `python`、`args` の api-watch は `C:/Users/<名前>/AppData/Local/Programs/api-watch/api-watch.pyz` にする

## 1 つのワークフローで取得から処理まで

受け渡し先の DAG に渡さず、1 本のワークフローの中で取得 → 判定 → 処理まで済ませることもできる。受け渡し先に `workflow` の代わりに
`command` を書くと、`work` がその場で 1 件ずつ実行する。Dagu のキューも受け渡し先の DAG も要らず、1 回の実行履歴に全部が並ぶ。

```
 Dagu: api-watch-<ルール>（毎分。取得するかは取得元の schedule を見て api-watch が決める）
   fetch     取得元を取得する   ← 同じ取得元を使う別のワークフローとは、API への問い合わせを 1 回にまとめる
   evaluate  このルールで判定する
   work      変化ごとに command を実行する（終了コード 0 で完了。それ以外は次の起動でやり直す）
```

### 設定

同じ取得元 `issues` を 2 本のワークフロー（`triage` と `notify`）が使う例。

```yaml
version: 1
state_dir: ~/.local/state/api-watch

sources:
  issues:
    connection: gitlab
    request:
      url: "{{ conn.url }}/api/v4/projects/123/issues"
      headers:
        PRIVATE-TOKEN: "{{ conn.token }}"
      query: { state: opened, order_by: updated_at, sort: asc }
    key: iid
    pagination: { type: page, size_param: per_page, size: 100 }
    schedule: "0 * * * *"      # 問い合わせの予定。ワークフローが何本・何回起動しても、予定の時刻ごとに 1 回
    jitter_sec: 900            # 予定の時刻から 0〜15 分ずらして問い合わせる
    busy_wait_sec: 600         # 別のワークフローが取得中なら、終わるのを待ってその結果を使う
    rate_limit: { group: gitlab }

rules:
  triage:
    source: issues
    id: iid
    watch: [title, labels, user_notes_count]
    targets:
      - name: triage
        command: [python3, /home/you/bin/triage.py]
        timeout_sec: 1800
  notify:
    source: issues
    id: iid
    watch: [state]
    on: [changed]
    targets:
      - name: notify
        command: [python3, /home/you/bin/notify.py, "{{ event.record_key }}"]
        timeout_sec: 120
```

コマンドには「受け渡し先の DAG」と同じ `APIWATCH_*` が環境変数で渡る（変化の中身は `APIWATCH_EVENT_FILE` の JSON）。
`max_attempts`・`retry_backoff_sec`・`order` は受け渡し先の表のとおりで、失敗したものはワークフローの次の起動で同じ ID のまま実行し直す。

### Dagu のワークフロー

`dags` が、`command` の受け渡し先を持つルールごとに `api-watch-<ルール>.yaml` の雛形を書く。この例では `api-watch-triage.yaml`・
`api-watch-notify.yaml`・`api-watch-maintenance.yaml` の 3 つ（どのルールも自分のワークフローで取得するので、取得元の DAG と再送の DAG は書かない）。
Dagu の設定（`config.yaml`）に足すものは無い。

```bash
api-watch --plugin dagu --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml dags --out ~/.config/dagu/dags
```

書き出される `api-watch-triage.yaml`（引数は一部を省略）:

```yaml
description: "issues を取得し、ルール triage で判定して、その場で実行する"
schedule: "* * * * *"            # 毎分起動する。取得するかは取得元の schedule を見て api-watch が決める
type: graph
steps:
  - name: fetch
    action: exec
    with:
      command: "/usr/bin/python3"
      args: ["/home/you/.local/bin/api-watch", "--config", "…", "--plugin", "dagu", "--connections", "…",
             "fetch", "--source", "issues"]
    timeout_sec: 2220            # 取得の時間 + jitter_sec + busy_wait_sec
    retry_policy: { limit: 3, interval_sec: 60, backoff: true, max_interval_sec: 600, exit_code: [3] }
  - name: evaluate
    depends: [fetch]
    # …（evaluate --rule triage）
  - name: work
    depends: [evaluate]
    # …（work --rule triage）。1 件ごとの上限は timeout_sec なので、ステップには上限を付けない
```

雛形なので自由に直してよく、手で書いてもよい。必要なのは同じ 3 ステップ（`fetch --source S` → `evaluate --rule R` → `work --rule R`）か、
1 ステップの `run --rule R` だけ。毎分起動しても、取得の予定の時刻でなければ API には問い合わせず、`work` が失敗したもののやり直しだけを進める。

### いつ取得するか（api-watch の予定と Dagu の予定）

`schedule` は「Dagu がいつ起動するか」ではなく「API にいつ問い合わせるか」で、api-watch が起動されたときに見る。
Dagu の起動間隔は DAG の `schedule` に書き、`dags` は設定の `schedule` を書き写さない。どちらの予定でも動く:

| 書き方 | DAG の `schedule` | 取得元の設定 | 動き |
|---|---|---|---|
| api-watch の予定 | 毎分（`dags` の既定） | `schedule: "0 * * * *"` | 毎分起動し、直近の予定の時刻（毎時 0 分）のあとにまだ誰も取得し終えていなければ取得する |
| Dagu の予定 | `dags --schedule "0 * * * *"` か手で書く | `schedule` を書かない | 起動されるたびに取得する。`min_interval_sec` があれば、その秒数以内の取得はしない |

予定の時刻は、この PC の時刻（ローカル時刻）で見る。cron の 5 項目のほか `@hourly`・`@daily` なども使える。

`dags` を `--schedule` なしで使うと、どの DAG も毎分起動する。取得元に `schedule` も `min_interval_sec` も無いと毎分問い合わせることになるので、
そのときは `dags` が止めて、どちらかを書くか `--schedule` を付けるよう案内する。

### 同じ API を呼ぶワークフローが重なったとき

`triage` と `notify` が同じ時刻に起動しても、API への問い合わせは 1 回になる:

| 起きたこと | どうなるか |
|---|---|
| 片方が取得し終えたあとに、もう片方が `fetch` した | 予定の時刻のあとに取得済み（`schedule`）か、`min_interval_sec` の間なら、取得せず（ずらしもせず）その結果で判定する |
| 片方が取得している最中に、もう片方が `fetch` した | `busy_wait_sec` まで終わるのを待ち、その結果で判定する（待たないなら次の起動で判定する） |
| 両方がずらしている間に、片方が取得した | もう片方はずらし終えたあとで取得済みかを見直して、取得しない |
| 取得が失敗した | `failure_backoff_sec` のあいだ、どのワークフローも問い合わせず、前回までの取得結果で判定・実行を続ける |

取得元に `schedule` が無く、2 本以上のワークフローがその取得元を取得するときは（取得元の DAG とルールのワークフローの組み合わせも数える）、
`min_interval_sec` と `busy_wait_sec` が必須で、`jitter_sec` は `min_interval_sec` より短くする。満たさなければ設定の誤りとして止める。

### 別の取得元とも API への負荷を分け合う（`rate_limit.group`）

同じ API を、条件の違う複数の取得元（issue と MR など）で見るときは、`rate_limit.group` に同じ名前を書く。同じ group の取得元は:

- 同時には取得しない（片方の取得中は、もう片方は `busy_wait_sec` まで待つ）
- `rate_limit.min_interval_sec`・`per_minute` を group の全部の要求で数える（group の中では同じ値を書く。違えば設定の誤り）
- どれかの取得が失敗したら、group の全部が `failure_backoff_sec` のあいだ問い合わせない

## 設定

### 取得元（sources）

1 つの取得元が 1 種類の取得（URL・条件・認証の組）。ルールはいくつでもこの取得結果を共有する。

| 項目 | 既定 | 意味 |
|---|---|---|
| `connection` | — | `--connections` で渡したファイルの `サービス` か `サービス:ラベル`。値は `{{ conn.項目 }}` で参照する |
| `request.method` / `url` / `headers` / `query` | `GET` | 要求。`{{ conn.* }}`・`{{ env.* }}`・`{{ position }}`（増分の位置）・`{{ now }}` / `{{ now_iso }}` を埋められる |
| `request.body` / `body_form` | — | JSON の本文（辞書・配列）か文字列 / フォームの本文 |
| `request.timeout_sec` | 30 | 1 回の要求の制限時間 |
| `records` | `$` | 応答の中のレコードの配列の場所（例 `data.items`）。オブジェクトなら 1 件として扱う |
| `key` | — | レコードを一意に指す項目。増分取得の取得元では書いておく（全体の姿を組み立て、基準値の取り直しに使う） |
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
`{{ position }}` を URL や本文に直接埋めてもよい。境界のレコードが 2 回取れても、値が変わっていなければ発火しない。

### ルール（rules）

| 項目 | 既定 | 意味 |
|---|---|---|
| `source` | （必須） | 取得元の名前 |
| `id` | （必須） | 識別 ID の項目。配列で書くと複合 ID |
| `filter` | — | 対象のレコードの条件。外れたレコードは見ない（戻ってきたら `appeared`） |
| `watch` | レコード全体 | 監視する項目の配列。ここに無い項目の変化は無視する |
| `ignore` | — | 監視から外す項目（`watch` を書かずに全体を見るときに便利。`items[*].seen_at` のようにも書ける） |
| `array_order` | `respect` | `ignore` にすると配列の順序の違いを変化とみなさない |
| `unordered` | — | 順序を無視する配列だけを指定する（例 `labels`） |
| `missing_is_null` | false | true にすると「項目が無い」と `null` を同じとみなす（既定は区別する） |
| `condition` | — | 発火条件の式（下を参照） |
| `on` | `[appeared, changed]`（`condition` があれば `[entered]`） | 発火する種類（下を参照） |
| `initial` | `baseline` | 初回: `baseline` は基準値として覚えるだけ、`emit` は既存のレコードも新規として渡す |
| `on_rule_change` | `rebaseline` | 判定の仕方（`id`・`filter`・`watch`・`ignore`・順序・`condition`）を変えたとき、`rebaseline` は最新の取得結果を基準値にし直す（大量に発火しない）。`continue` は新しい比べ方で前回の値と比べる |
| `include_record` | true | イベントにレコード全体を入れる |
| `targets` | — | 受け渡し先（下の表。文字列だけ書けば `workflow`）。無ければ検知を記録するだけ |

発火する種類（1 回の観測で複数つくことがあり、`on` のどれかに当たれば 1 件のイベントになる）:

| 種類 | いつ |
|---|---|
| `appeared` | 初めて見た ID（消えたあと・`filter` から外れたあとに戻ってきたときも） |
| `changed` | `watch` の値が前回の観測から変わった |
| `entered` | 条件が不一致（または初めて見た）→ 一致になった |
| `exited` | 条件が一致 → 不一致になった（全件取得で消えたときも） |
| `matching` | 条件に一致している（観測のたび。増分取得では取得されたときだけ） |
| `removed` | 全件取得で見当たらなくなった（増分取得の結果では判定しない） |

条件の式は `{path: 項目, op: 演算子, value: 値}`、組み合わせは `{all: [...]}`・`{any: [...]}`・`{not: {...}}`。
演算子は `eq`（`value` だけ書いたとき）・`ne`・`gt`・`ge`・`lt`・`le`・`in`・`not_in`・`contains`・`not_contains`・
`regex`・`startswith`・`endswith`・`exists`（`value` が無いとき）・`missing`・`null`・`not_null`・`empty`・`not_empty`・
`truthy`・`falsy`。大小は数どうし・文字列どうしだけ比べる（ISO 8601 の日時は文字列のまま比べられる）。

項目の書き方は `user.name`・`labels[0]`・`labels[*].name`（全要素の配列）・`["a.b"]`（ドットを含むキー）。
キーの順序の違いは比べるときに無視する。

### 受け渡し先（targets）

| 項目 | 既定 | 意味 |
|---|---|---|
| `workflow` | （`command` が無ければ必須） | 受け渡し先のワークフロー。Dagu なら DAG の名前（`mode: cli` ならファイルのパスでもよい） |
| `command` | — | 別のワークフローへ渡さず、`work` がその場で実行する argv（下の「1 つのワークフローで取得から処理まで」）。書くときは `name` も書く。`workflow`・`queue` とはどちらか一方 |
| `timeout_sec` | 3600 | `command` の 1 回の実行の上限。過ぎたら止めて失敗として数える |
| `name` | `workflow` と同じ | 受け渡し先の名前（同じワークフローに 2 回渡すときに区別する） |
| `params` | — | 追加の引数。`{{ event.after.title }}`・`{{ event.record_id }}`・`{{ delivery.id }}`・`{{ env.X }}` を埋められる |
| `queue` | — | 受け渡し先のキューを上書きする（Dagu のキュー） |
| `order` | `completion` | 同じレコードのイベントの順序。`completion` は前の後続処理が完了するまで次を渡さない、`enqueue` は前をキューに入れたら次を渡す、`none` は問わない |
| `max_attempts` | 10 | 渡せないときの試行回数。超えたら止めて手で再実行するのを待つ |
| `retry_backoff_sec` / `retry_max_backoff_sec` | 30 / 3600 | 渡し直しの間隔（倍々） |
| `inline_payload` | false | true ならイベントの JSON を引数 `APIWATCH_EVENT_JSON` でも渡す |

### 受け渡しのアダプタ（delivery）

`adapter` でアダプタを選び、残りの項目はアダプタの設定。`workflow` の受け渡し先があれば必須（`command` の受け渡し先だけなら要らない）。

`adapter: dagu`（`--plugin dagu` を付けて実行する）:

| 項目 | 既定 | 意味 |
|---|---|---|
| `mode` | `cli` | `cli`: `dagu enqueue --run-id=<ID> <DAG> -- KEY=VALUE ...` / `api`: Dagu の REST API（`/api/v1`） |
| `executable` / `extra_args` | `dagu` | `mode: cli` の dagu（argv の配列でもよい）と、`enqueue` / `retry` の後ろに足す引数 |
| `url` / `connection` | — | `mode: api` の Dagu の URL、または接続設定の項目（`url` と `token`、または `user` と `password`） |
| `timeout_sec` | 60 | dagu・API の 1 回の呼び出しの制限時間 |

`adapter: command`（Dagu を使わないとき）:

| 項目 | 意味 |
|---|---|
| `command` | 受け渡しの argv。`{{ run_id }}`・`{{ workflow }}`・`{{ queue }}`・`{{ params_json }}`・`{{ params.NAME }}` を埋められる。引数は環境変数 `APIWATCH_*` でも渡る。終了コード 0 で成功、9 か出力に `already exists` で「同じ ID が既にある」 |
| `retry_command` | 省略可。`retry` で後続処理を同じ ID のまま再実行させる argv |
| `status_command` | 省略可。標準出力の 1 行目が `done` / `failed` なら、`reconcile` が後続処理の状態として記録する |
| `timeout_sec` | 1 回の実行の制限時間（60） |

アダプタは同じ ID を 2 回実行しないこと（2 回目は「既にある」と返すこと）を約束する。これで渡し直しても後続処理は 1 回だけ走る。

### 保持期間（retention）

| 項目 | 既定 | 意味 |
|---|---|---|
| `fetches_keep` | 3 | 取得結果を最低何回分残すか |
| `fetch_days` | 7 | これより古い取得結果を消す。**どれかのルールがまだ判定していないものは消さない** |
| `events_days` | 30 | イベントを残す日数。**受け渡しがすべて完了（または飛ばした）したものだけ消す** |
| `removed_days` | 30 | 消えた・`filter` から外れたレコードの前回の観測値を残す日数。過ぎたあとにまた現れたら `appeared` になる |
| `record_days` | なし（消さない） | 増分取得で組み立てた全体の姿（`key` を書いた取得元）から、この日数取得されていないレコードを消す。消したレコードは、ルールの基準値を取り直すときの対象から外れ、次に取得されたとき `appeared` になる |
| `auto_gc` | true | 取得のたびに整理する（整理の DAG と同じこと） |

整理のたびに、消した行の領域を DB ファイルから返し、WAL ファイルも切り詰めるので、ファイルは保持期間に見合った大きさで止まる。
`status` に DB・WAL・イベントのファイルの大きさが出る。このツールの古い版で作った DB は領域を自動で返さない形なので、
`status` に案内が出たら一度 `gc --vacuum`（DB を作り直す。大きいと時間がかかる）を流す。

状態の置き場所は `state_dir`（既定 `~/.local/state/api-watch`。コマンド行の `--state-dir` で上書き）。

### 接続設定

認証情報は `--connections <パス>` で渡した connections.yaml（または .json）からだけ読む。環境変数・設定ファイル・決まった場所からは探さない。
接続設定を使う取得元・受け渡し先があるのに `--connections` が無ければ、問い合わせる前に止める。生成する DAG にも同じパスが入る。

## 受け渡し先の DAG

`adapter: dagu` のときの例が `dags/api-watch-handler.example.yaml`。Dagu には次の引数が渡る（DAG の `params` に宣言しておく）。
`adapter: command` でも同じ名前の値が環境変数で渡る。

| 引数 | 中身 |
|---|---|
| `APIWATCH_EVENT_ID` | イベント ID。1 つの観測の 1 つの変更につき 1 つ。複数の DAG に渡しても同じ |
| `APIWATCH_DELIVERY_ID` | 受け渡しの ID。Dagu の実行 ID（`DAG_RUN_ID`）と同じ。渡し直しても変わらない |
| `APIWATCH_SOURCE` / `APIWATCH_RULE` | 取得元・ルールの名前 |
| `APIWATCH_RECORD_ID` | レコードの ID（複合 ID なら JSON の配列） |
| `APIWATCH_KINDS` | 発火した種類（カンマ区切り） |
| `APIWATCH_EVENT_FILE` | イベントの JSON ファイル |

イベントの JSON:

```json
{
  "event_id": "ev_…", "source": "gitlab-issues", "rule": "issue-changed",
  "record_id": 42, "record_key": "42", "kinds": ["changed"], "fetch_id": 17, "observed_at": "2026-11-04T10:00:00",
  "before": {"state": "opened", "labels": ["ui"]},
  "after":  {"state": "opened", "labels": ["bug", "ui"]},
  "changes": [{"path": "labels[0]", "type": "changed", "before": "ui", "after": "bug"},
              {"path": "labels[1]", "type": "added", "after": "ui"}],
  "matched": null,
  "record": { "…取得したレコード全体…": "" }
}
```

`changes[].type` は `added`（項目が現れた）・`removed`（項目が無くなった）・`changed`。`watch` を書かないルールでは
`before` / `after` はレコード全体になる。

- **重複しない**: 実行 ID を受け渡しの ID にしているので、渡し直しで同じ実行が 2 つできることはない（Dagu が「既にある」と断り、
  このツールは渡せたものとして扱う）。後続処理の側でも `APIWATCH_EVENT_ID` で冪等にできる
- **完了の記録**: 例のように `handler_on` の `success` / `failure` から `api-watch ack ${DAG_RUN_ID}`（失敗なら `--failed`）を呼ぶ。
  `mode: api` なら、再送の DAG が `reconcile` で Dagu に状態を問い合わせて記録するので、`ack` は無くてもよい
- **後続処理の失敗**: 後続の DAG の `retry_policy` や、Dagu の画面の再実行で直す。`api-watch retry <ID>` も同じ実行 ID のまま
  Dagu に再実行させる
- **並列と順序**: 別のレコードの変更は Dagu のキューの並列数まで同時に動く。同じルール・受け渡し先・レコードの変更は、
  `order: completion` なら前の実行が完了してから次を渡す（前が失敗したら、再実行するか `skip` するまで後ろは待つ。他のレコードは止まらない）。
  完了を記録しない（`ack` も `reconcile` も使わない）なら、`order: enqueue` にする（そうしないと 2 件目から先へ進まない）

## 運用

| コマンド | すること |
|---|---|
| `status` / `status --json` | 状態のファイルの大きさ、取得元ごとの最後の取得結果・エラー・途中の取得・待たされている時刻、ルールごとの判定位置・未判定の回数・受け渡しの内訳、失敗している受け渡し |
| `events [--rule R] [--status S] [--record ID]` | イベントと受け渡しの一覧 |
| `show <イベントか受け渡しの ID>` | イベントの中身と受け渡しの状態 |
| `records --source S [--fetch N]` | 保存済みの取得結果のレコード（ルールの書き方を確かめる） |
| `fetch` / `evaluate` / `dispatch` / `work` / `run` | 取得 / 判定 / 受け渡し / `command` の受け渡し先の実行 / その 4 つを続けて。`--source`・`--rule` で絞れる（`run --rule R` はそのルールの取得元を取得し、そのルールだけを判定・実行する）。`fetch` と `run` の `--no-jitter` はずらさない |
| `retry [ID ...] [--rule R] [--target T] [--status S]` | 失敗した受け渡し（既定: 渡せなかった・上限に達した・後続処理が失敗した）を同じ ID のまま再実行 |
| `skip ID ...` | 受け渡しを飛ばす。同じレコードの後ろのイベントが進む |
| `rebaseline --rule R` / `--source S` | 最新の取得結果を基準値にし直す（その間の変化では発火しない）。`--refetch` で次の取得を全件に、`--now` でいま取り直す |
| `replay --rule R [--from-fetch N] [--dry-run]` | 保存済みの取得結果で判定をやり直す。API には問い合わせない。既にあるイベントは同じ ID なので積み直さない |
| `gc [--dry-run] [--vacuum]` | 保持期間を過ぎたものを消し、ファイルを縮める。`--vacuum` は DB を作り直して詰める |
| `forget-rule R` | 設定から消したルールの判定状態を消す |
| `reconcile` | 受け渡し先に後続処理の状態を問い合わせて記録する（`adapter: dagu` の `mode: api`、`adapter: command` の `status_command`） |
| `dags --out DIR` | Dagu の DAG の雛形を書き出す（`--plugin dagu` のときだけ）。`command` の受け渡し先を持つルールごとに、取得から実行までのワークフローも書く。既にあるファイルは `--force` のときだけ上書きする。起動間隔は `--schedule`（既定は毎分） |

ログ・エラー・状態には認証情報を出さない（接続設定の値・認証のヘッダー・URL のトークンらしい引数は `***` に置き換える）。

終了コード: 0 成功（取得中・待機中で何もしなかったときも）/ 1 設定の誤り・再試行しても直らない失敗（認証・404 など）/
3 一時的な失敗（生成する DAG はこれだけ再試行する）/ 4 一部のルール・受け渡しが失敗した（他は進めた。失敗分は `status` に出る）。

## 止まったとき・失敗したとき

| 起きたこと | どうなるか |
|---|---|
| 取得の途中で失敗・プロセスや PC が停止 | 取ったページは保存済み。次の取得は続きのページから（`resume_max_age_sec` を過ぎたら最初から）。全ページそろうまで判定せず、増分の位置も進めない |
| 取得中にもう 1 つ取得が始まった | 何もせず終わる。取得中のプロセスが死んでいれば（同じ PC なら即座に、別の PC からは `lease_sec` 後に）引き継ぐ |
| 判定の途中で停止 | 判定は取得 1 回ごとに 1 トランザクション。次は判定し終えていない取得からやり直す |
| ルールの判定でエラー | そのルールだけ止まり、他のルールは進む。`status` にエラーが出る。直して `evaluate --rule R` |
| 受け渡し先に渡せない | 時間をおいて同じ ID で渡し直す。`max_attempts` を超えたら止まり、`retry` を待つ。同じレコードの後ろのイベントだけが待つ |
| 渡した直後に停止（記録の前） | 次の `dispatch` で同じ ID で渡し直し、受け渡し先が「既にある」と言うので 1 回だけ実行される |
| 取得条件・認証を変えた | 前の条件で取ったデータ（取得結果・位置・ETag の控え）を使わず取り直し、ルールは基準値を取り直す |

## 限界

- ポーリングなので、取得の間隔の間に起きて元に戻った変化（A→B→A の B）は見えない。観測した値の列は順に比べるので、
  観測できた変化はすべて検知する。**すべての変化が要るなら、API が提供するイベント履歴・変更履歴を取得元にする**
  （履歴の各行をレコードとし、ID を履歴の ID にして `on: [appeared]`）
- ページ番号・オフセットのページングは、取得中にデータが増減するとページの境目がずれる。並び順を更新日時の昇順にするか、
  カーソル方式を使う
- 増分取得では「消えた」を判定できない（取得されないだけと区別できない）

## テスト

実 API・Dagu 本体なしで、偽の API（手元の HTTP サーバ）と偽の dagu で確かめる。

```bash
python3 -m unittest discover -s tools/api-watch/test
```

取得の集約・各ページング・増分取得・条件付き要求・一時的な失敗の再試行・途中からの再開・強制終了からの引き継ぎ・
変更検知の意味（キー順・配列の順序・欠落と null・A→B→A・一致の遷移・初回の扱い・ルールの変更）・保存済みの結果での判定のやり直し・
受け渡しの再送・同じ ID・順序・失敗の隔離・REST API での受け渡しと完了の確認・保持期間・認証情報の伏せ字、
接続設定を `--connections` からだけ読むこと、Dagu プラグインを `--plugin` で指定したときだけ使うこと、本体が Dagu なしで動くこと（`adapter: command`）、
インストーラが作る 1 ファイル（`.pyz`）がそれだけで動くこと（同梱の PyYAML・DAG がその `.pyz` を呼ぶこと・インストールと削除）を見る。
設計は `docs/designs/api-watch-design.md`。
