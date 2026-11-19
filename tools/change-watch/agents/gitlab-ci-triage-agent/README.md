# gitlab-ci-triage-agent

決めた GitLab のプロジェクトを定期的に見に行き、**失敗したパイプライン**を 1 件ずつ診断する。失敗したジョブのログを取り、
テスト失敗・依存関係・インフラやクォータ・設定ミス・不明に分類し、根拠の抜粋と次のアクションを MR・コミット・ハブの issue に届ける。
パターンに修正案があれば一緒に書く。**同じパイプラインで診断の指紋が同じなら、重ねて書かない**。

起動・条件・再試行と履歴は [Dagu](https://github.com/dagu-org/dagu)、失敗したパイプラインの取得と前回からの変化は
[change-watch](../../README.md) が受け持つ。このフォルダには、ログの取得・分類・投稿を行う `gitlab_ci.py` と設定の例がある。
分類には決まったパターンを使い、ログは秘匿情報らしい文字列を伏せてから保存する。

```
 Dagu: change-watch-ci-triage（毎分起動。1 回に 1 件。GitLab の失敗一覧は 5 分ごとに取得）
   get       前回 commit してから現れた・変わった失敗を 1 件受け取る
   collect   条件に合えば、失敗したジョブのログを読んでマスクする
   diagnose  パターンで分類し、コメントの本文を作る
   post      投稿先を選び、同じ診断が無ければ書く（初期設定は dry-run）
   commit    処理し終えたと記録する
 Dagu: change-watch-gitlab-ci-triage-agent-maintenance（毎日、保持期間を過ぎた取得結果を整理）
```

## 入れ方

1. インストーラで change-watch・プラグイン・設定の雛形・Dagu のワークフローを入れる。パスはこの PC のものにして置く。
   設定とワークフローは無いときだけ置くので、入れ直すとプログラムが新しくなる。

   ```bash
   python3 tools/change-watch/install.py install --agent gitlab-ci-triage-agent
   ```

2. `~/.config/change-watch/gitlab-ci-triage-agent/gitlab-ci-triage-agent.env` に GitLab の URL・プロジェクト・トークンを書く。
   トークンには `api` スコープと、ログを読んで MR・コミット・ハブの issue にコメントできる権限が要る。
   **このトークンの持ち主が診断の書き手**になる。Reporter 以上が目安で、ログの公開範囲によっては Developer 以上が要る。
3. 接続設定（例は `~/.github/connections.yaml`）に GitLab を書く。値はワークフローの `.env` から取る。

   ```yaml
   gitlab:
     - label: default
       url: ${GITLAB_URL}
       token: ${GITLAB_TOKEN}
   ```

4. `change-watch.yaml` の `incremental.initial` を入れる日の日時に直す。最初の取得では、一覧にある失敗を基準として覚える。
5. Dagu のスケジューラを動かす（`dagu start-all` など）。初期設定は **dry-run**。ログに出た投稿先・既存の診断・本文を
   確かめたら、`CI_TRIAGE_DRY_RUN=0` にする。dry-run の間も `commit` するので、その間に処理した失敗は設定を切り替えただけでは投稿されない。

## 設定

| 変数 | 値 |
|---|---|
| `GITLAB_URL`・`GITLAB_PROJECT`・`GITLAB_TOKEN` | GitLab の URL・プロジェクトのパスまたは ID・トークン。1 つの設定で 1 プロジェクトを見る |
| `GITLAB_HUB_ISSUE_URL` | MR とコミットのどちらにも書けない場合に使う issue の URL。同じ GitLab なら別のプロジェクトでもよい |
| `CI_TRIAGE_DRY_RUN` | `1`・`true`・`yes`・`on` なら書かずに標準出力に出す（大文字小文字は問わない） |
| `CI_TRIAGE_PATTERNS` | 現場のパターンファイルのパス。空なら組み込みだけ使う |
| `CI_TRIAGE_COMMIT_SOURCES` | コミットに書くパイプラインの `source` をカンマ区切りで書く。既定は `push`。空ならコミットへの投稿を省く |
| `CI_TRIAGE_WORK_DIR` | 作業フォルダの親。既定は状態の置き場所の下の `work` |

CI の変数は GitLab のマスクを有効にしておく。トークンの形や秘密らしい名前の代入は伏せるが、短い秘密をそのまま出力すると拾えないことがある。

## 使い方

手で試すときは、先に `.env` の値を環境変数として読み込む。

```bash
set -a
. ~/.config/change-watch/gitlab-ci-triage-agent/gitlab-ci-triage-agent.env
set +a
change-watch --plugin gitlab_ci ci triage 123 --dry-run
```

| コマンド（`change-watch --plugin gitlab_ci ci …`） | すること |
|---|---|
| `collect 123` | パイプライン・失敗したジョブ・マスク済みログを作業フォルダに置く |
| `diagnose 123` | 作業フォルダのログを分類し、`diagnosis.json` と `comment.md` を作る |
| `post 123 [--dry-run]` | 投稿先と既存の印を調べ、コメントを 1 回投稿する。dry-run では投稿先と `comment.md` を出す |
| `triage 123 [--dry-run]` | `collect` → `diagnose` → `post` を続けて行う |
| `patterns [--check]` | いま効いているパターンを JSON で出す。`--check` は設定の形を確かめる |
| `diagnose --trace-file F` | ログ 1 つを分類して本文を出す。`--job-name`・`--stage`・`--failure-reason` を添えられる |

`python3 gitlab_ci.py <コマンド>` でも呼べる。ID は `123` または `'#123'`。`--project` でプロジェクト、`--dir` で
パイプラインの作業フォルダを指定できる。取得時に中身を作り直すので、`collect` と `triage` の `--dir` には
作業ルート（`CI_TRIAGE_WORK_DIR` または既定の `work`）配下の取得結果専用フォルダを使う。

## いつ診断するか

| 変化 | 扱い |
|---|---|
| 一覧に失敗が現れた | 診断する |
| 同じパイプラインが再実行でまた失敗した | 診断の指紋が変われば「（再実行後）」のコメントを足す |
| 取得時にはまだ失敗だったが、ログを読む時点では失敗でなくなっていた | 今の状態を記録して処理を終える |
| 30 日取得されず一覧から外れた | `commit` して処理を終える |

失敗ジョブのうち ID の小さい順に 20 件のログを読み、末尾 1 MiB を使う。同じ原因のジョブは本文でまとめる。
投稿先は MR が先で、ブランチのパイプラインなら開いている同じプロジェクトの MR を探す。MR が無ければ既定では push 起点だけコミットに書き、
それ以外はハブの issue へ送る。投稿先の最近のコメント 300 件を調べ、本文先頭の印が同じなら投稿を省く。

## 条件で分ける

例の `collect` は `${CHANGE.kind}` が `appeared` か `changed` で、`${CHANGE.record.status}` が `failed` のときに動く。
対象ブランチで絞るなら `collect` に `${CHANGE.record.ref}` の条件を足す。既知の原因だけ書くなら `post` に
`${DIAG.known}` が `true` という条件を足す。不明を外すには `${DIAG.category}` を使う。例のコメントに条件の書き方がある。
条件から外れた変化も `commit` する。

## パターンを足す

1. `ci collect <パイプライン>` でマスク済みの `trace-<ジョブ ID>.log` を取る。
2. 原因を言い切れるエラー行を選び、`ci-triage-patterns.json` に足す。同じ `id` は定義ごと置き換わる。
   組み込みを止めるには同じ `id` で `enabled: false` を書く。ログに固有の名前は名前付きグループにできる。
3. `ci patterns --check` で形を確かめる。
4. `ci diagnose --trace-file <ログ>` で当たり方を確かめる。前にうまく分類できたログでも試し、必要なら `priority` を下げるか `job_name`・`stage` で絞る。
5. `ci triage <パイプライン> --dry-run` で、投稿先まで通して確かめる。
6. 次の起動から効く。どの現場でも使える形は、ログのフィクスチャと一緒に組み込みに足す。

## 失敗したとき

接続エラーや一時的な HTTP エラーは終了コード 3 で、Dagu が再試行する。設定や権限の誤りは履歴で確かめる。
`collect`・`diagnose`・`post` が失敗しても `commit` する。手でやり直すときは `triage` を使う。
失敗したジョブが無ければ、パイプラインの画面で下流のパイプラインを確かめる。

## Windows で使うとき

インストーラが Dagu の `command` を Python に、`args` の先頭を `change-watch.pyz` のフルパスにする。
プラグインは `%LOCALAPPDATA%\Programs\change-watch\agents\gitlab-ci-triage-agent\`、既定の作業フォルダは
`%LOCALAPPDATA%\change-watch\gitlab-ci-triage-agent\work\` に置く。

## 設定の置き場所

| 置かれるもの | 場所 |
|---|---|
| プラグイン `gitlab_ci.py` | `~/.local/share/change-watch/agents/gitlab-ci-triage-agent/` |
| `change-watch.yaml`・`gitlab-ci-triage-agent.env`・`ci-triage-patterns.json` | `~/.config/change-watch/gitlab-ci-triage-agent/` |
| 診断・整理のワークフロー | `~/.config/dagu/dags/` |
| 状態・マスク済みログと診断 | `~/.local/state/change-watch/gitlab-ci-triage-agent/`。取得時に 7 日を過ぎた作業フォルダを整理する |

## テスト

```bash
cd tools/change-watch/agents/gitlab-ci-triage-agent
python3 -m unittest discover -s test
```

分類・マスク・本文はフィクスチャで確かめる。接続を使う試験は手元の偽 GitLab と PyYAML を使い、
例のワークフローは本物の change-watch で動かす。

設計は [gitlab-ci-triage-agent 設計](../../../../docs/designs/gitlab-ci-triage-agent-design.md)。
