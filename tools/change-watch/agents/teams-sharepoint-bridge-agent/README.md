# teams-sharepoint-bridge-agent

Teams の投稿から Power Automate が SharePoint に作った依頼 JSON を、同期先のフォルダから受け取る。
このエージェントは依頼を検証して宛先を決め、Copilot アプリのハブの受け箱へ相関マーカー付きの依頼文を提出する。
ハブの自動実行は宛先のフォルダでセッションを起こして依頼を渡し、そのセッションが GitLab に結果を上げる。
GitLab の通知メールを受けた Power Automate が Teams の元の投稿へ返信する。

標準の形は、**ハブのフォルダを 1 つ**用意して Copilot アプリの自動実行をそこで 1 つだけ動かし、作業する**フォルダ（宛先）を
いくつでも**登録する形。宛先ごとに自動実行を作る必要はない。

```text
C:\work\hub      ← ハブ。受け箱（.copilot-jobs）があり、自動実行が 1 つだけ動く。作業はしない
C:\work\app      ← 宛先 app。ハブが振った依頼を、このフォルダのセッションが作業する
C:\work\docs     ← 宛先 docs
C:\work\ops      ← 宛先 ops
```

全体の構成（呼び出し元・呼び出すプログラム・設定ファイル・処理の概要）と流れは [ARCHITECTURE.md](ARCHITECTURE.md) にまとめた。

## 用意と動かし方

1. OneDrive / SharePoint の同期クライアントで、SharePoint の依頼フォルダを Dagu が動く PC に同期する。
   往路の依頼 JSON と復路の返信は [Power Automate のレシピ](power-automate/) を参照。
   Dagu の実行環境に合わせて、次のどちらかを選ぶ。

   - **直結方式**。Dagu がログインユーザーのセッションで動き、同期フォルダを直接読める場合に使う。
   - **フェッチャ方式**。Dagu を Windows サービスとして動かす場合に使う。ログインユーザーのセッションで同期し、フェッチャ（`session_fetcher.py`）が依頼ファイルをローカル受信フォルダへコピーする。ローカル受信フォルダは Dagu のサービスアカウントが読める場所に置く。[セッション側フェッチャの設計](../../../../docs/plans/2026-11-23-teams-bridge-session-fetcher-design.md)

2. リポジトリのルートからインストーラを動かす。change-watch とエージェントのプログラム、設定の雛形、Dagu のワークフローが入る。

   ```sh
   python3 tools/change-watch/install.py install --agent teams-sharepoint-bridge-agent
   ```

3. `~/.config/change-watch/teams-sharepoint-bridge-agent/` に置かれた `change-watch.yaml` の `folder.path` と `settle_sec` を使う方式に合わせる。フェッチャ方式ではローカル受信フォルダと `0`、直結方式では同期先と `10` を指定する。どちらも `hash: true` を使う。
4. 同じフォルダの `targets.json` に、ハブと宛先を書く（雛形は [targets.json.example](targets.json.example)）。

   ```json
   {
     "hub": "C:/work/hub",
     "default": "app",
     "targets": {
       "app":  {"dir": "C:/work/app",  "gitlab_project": "group/app",  "mode": "issue",
                "description": "業務アプリのコードと不具合", "keywords": ["バグ", "画面"]},
       "docs": {"dir": "C:/work/docs", "gitlab_project": "group/docs", "mode": "commit",
                "description": "手順書とマニュアル", "keywords": ["マニュアル", "手順書"]},
       "ops":  {"dir": "C:/work/ops",  "gitlab_project": "group/ops",  "mode": "issue",
                "description": "障害の対応と再発防止", "keywords": ["障害", "アラート"]}
     }
   }
   ```

   - `hub` はハブのフォルダの絶対パス。1 つだけ書く。
   - `targets` は宛先名ごとに、`dir`（作業するフォルダの絶対パス）・`gitlab_project`（`group/project` の形）・
     `mode`（issue を作るなら `issue`、コミットをプッシュするなら `commit`）を書く。宛先はいくつでも足せる。
   - `description` は、規則で宛先が決まらない依頼をハブのセッションが選ぶときの材料。`keywords`・`channels` は
     [投稿の中身で宛先を振る](#投稿の中身で宛先を振る) を参照。`default` は、どれにも決まらないときの宛先名。
5. ハブを用意する（[copilot-app-jobs](../../../copilot-app-jobs/README.md) の「ほかのフォルダのセッションに振る」）。
   1. ハブのフォルダと、各宛先のフォルダを、Copilot アプリでそれぞれ一度プロジェクトとして開く。
   2. 各宛先のフォルダを、ハブの振り先に登録する。`targets.json` の `dir` を 1 つずつ足す。

      ```powershell
      python C:\tools\copilot-app-jobs\copilot_app_job.py target add --dir C:\work\hub --to C:\work\app
      python C:\tools\copilot-app-jobs\copilot_app_job.py target add --dir C:\work\hub --to C:\work\docs
      python C:\tools\copilot-app-jobs\copilot_app_job.py target add --dir C:\work\hub --to C:\work\ops
      ```

   3. ハブのフォルダに、Copilot アプリの自動実行を 1 つ作る。貼るプロンプトは次のコマンドが出す（間隔は 1 時間）。

      ```powershell
      python C:\tools\copilot-app-jobs\copilot_app_job.py prompt --dir C:\work\hub --stay 50
      ```

   宛先を足すときは、`targets.json` に 1 つ書き足し、そのフォルダを `target add` で登録する。自動実行は増やさない。
6. Dagu を起動する環境の `TEAMS_BRIDGE_COPILOT_JOB` に `copilot_app_job.py` の絶対パスを設定する。
   リポジトリ内の `teams_bridge.py` を直接使うときは、未設定なら同じリポジトリのスクリプトを探す。
7. Dagu のスケジューラを動かす。インストーラが `~/.config/dagu/dags/` に置く `change-watch-teams-bridge.yaml` は毎分起動し、
   `get` で変化を 1 件受け取る。現れたファイルと、内容が変わったファイルを `check-file` に渡し、結果に応じて `submit` または
   `record-invalid` を動かしてから `commit` する。処理済みや見つからない依頼、削除も `commit` する。
   このソリューションに要る DAG 例は [dags/](dags/README.md) に一式そろえた（原本はこのフォルダの直下と各ツール側にあり、dags/ はコピー）。

`hub` を書かない形（宛先のフォルダごとに自動実行を作り、そのセッションが自分で作業する）も動く。違いは
[ARCHITECTURE.md の「ハブを使わない形」](ARCHITECTURE.md#6-ハブを使わない形) を参照。

### フェッチャ方式の設定

同期を行うログインユーザーで、リポジトリ内の `session_fetcher.py` があるフォルダから実行する。山括弧で囲んだ箇所は実際のパスに置き換える。

```sh
python3 session_fetcher.py fetch-once --source "<同期フォルダ>" --dest "<受信フォルダ>"
```

Windows のコマンドプロンプトで、同期を行うユーザーのタスクを毎分実行するように登録する例。Python とスクリプトには実際の絶対パスを指定する。

```bat
schtasks /Create /TN "teams-bridge-fetcher" /TR "\"C:\Python\python.exe\" \"C:\sandbox\tools\change-watch\agents\teams-sharepoint-bridge-agent\session_fetcher.py\" fetch-once --source \"C:\Users\you\Example Org\Requests\" --dest C:\TeamsBridge\inbox" /SC MINUTE /MO 1 /IT
```

このコマンドライン例だけではログオン時の開始と多重起動防止が設定されない。
[タスク定義の XML 例](session-fetcher-task.xml.example) を `session-fetcher-task.xml` にコピーし、
両方の `UserId` を同期ユーザーのアカウントに、Python・スクリプト・同期フォルダ・受信フォルダのパスを実際の値に置き換えてインポートする。
画面で設定する場合は、ログオン時に開始して 1 分間隔で無期限に繰り返し、既に実行中の場合は新しいインスタンスを開始しないようにする。

結果 JSON（標準出力）とエラーメッセージ（標準エラー）を確認する場合は、バッチファイルなどで `>> fetcher.log 2>&1` とリダイレクトしてログに残す（`pythonw.exe` では出力が捨てられる）。

登録後、タスクスケジューラの画面（またはタスク XML）で、同期を行うユーザーのログオン時に開始し、1 分間隔で繰り返すトリガーに設定する。「既に実行中の場合は新しいインスタンスを開始しない」設定も同じ画面または XML で指定する。

## 投稿の中身で宛先を振る

宛先は、次の順に、最初に決まったもので決める。どれで決めたかは台帳（`target`・`reason`）と、Copilot に渡す依頼文の
「宛先: 名前（理由）」に残り、issue の説明やコミットメッセージにも書かれる。

| 順 | 決め方 | 書くところ |
|---|---|---|
| 1 | 投稿の最初の行が `#名前` か `【名前】` で始まる | 投稿する人が書く。知らない名前なら無視して次へ |
| 2 | 依頼 JSON の `target`（往路のフローで固定した宛先） | Power Automate |
| 3 | 投稿したチャンネルを受け持つ宛先が 1 つだけ | `targets.json` の宛先ごとの `channels`（チャンネル ID の並び） |
| 4 | キーワードがいちばん多く当たった宛先が 1 つだけ | `targets.json` の宛先ごとの `keywords`（大文字小文字は区別しない） |
| 5 | ハブのセッションに選ばせる（ハブがあるときだけ） | `targets.json` の `hub` と、宛先ごとの `description` |
| 6 | 既定の宛先 | `targets.json` の `default` |

3 で複数の宛先が当たったときはその中から、4 で同点のときは同点の宛先の中から、5 の候補を選ぶ。

書き方の例は [用意と動かし方](#用意と動かし方) の手順 4。

### ハブ（`hub`）

`hub` を書くと、依頼はハブのフォルダの受け箱に置き、Copilot アプリの自動実行はハブで 1 つだけ動かす。
自動実行は宛先のフォルダで新しいセッションを起こして依頼を渡し、終わりを待たずに次へ進む
（[copilot-app-jobs の「ほかのフォルダのセッションに振る」](../../../copilot-app-jobs/README.md)）。同じ宛先への依頼は、
前の依頼が終わるまで受け箱で待つ。

- 1〜4 か 6 で決まった依頼は、その宛先に振る。
- 5 の依頼は、ハブの自動実行が候補（名前と `description`）から 1 つ選び、選んだ宛先に振る。issue かコミットかの指示と
  相関マーカーは、選んだあとに足す。
- 宛先のフォルダは、ハブで先に登録しておく（[用意と動かし方](#用意と動かし方) の手順 5）。

- 振られたセッションは、結果を GitLab に上げたあと `finish` を呼ぶ。
- `hub` を書かなければ、これまでどおり宛先のフォルダの受け箱に置く（5 は飛ばす）。

## コマンド

`change-watch --plugin teams_bridge <コマンド>` または `python3 teams_bridge.py <コマンド>` で呼ぶ。
ブリッジのコマンドは change-watch の設定を読まない。change-watch 本体の設定確認は `change-watch check` で行う。

| コマンド | 動作 |
|---|---|
| `check-file --path F --targets F [--ledger F]` | 未提出なら `submit`、提出済みなら `done`、様式違反なら `invalid`、読めない依頼なら `missing` を出す |
| `build-prompt --path F --targets F` | 検証して Copilot への依頼文を出す |
| `submit --path F --targets F [--ledger F] [--jobs-py PATH] [--python CMD] [--dry-run]` | 受け箱に提出し、結果を JSON で出す。提出済みなら `already` を返す。`--dry-run` では依頼文とコマンドを JSON で確認できる |
| `record-invalid --path F [--log F] [--targets F]` | 検証し直して、違反の理由を JSON Lines に追記する。宛先の検証も記録するときは `check-file` と同じ宛先表を渡す |

依頼は UTF-8 の JSON で、`request_id` とファイル名 `<request_id>.json` をそろえる。
`team_id`、`channel_id`、`message_id`、`author`、`posted_at`（ISO 8601）、`text`（2 万文字まで）は必須。
`target` は省略できる。返信先を引けるよう、Teams への返信が済むまで SharePoint の依頼ファイルを残す。

## 失敗したとき

- 様式違反はログに記録してから `commit` する。修正は投稿側で行う。
- 提出に失敗したら `commit` せず、翌分に同じ依頼を試す。提出済み台帳は `submitted.json` に保存する。台帳が壊れているときは処理を止める。
- 提出してから台帳を更新するまでに止まったときは、受け箱の `pending`・`running`・`done` に残る依頼文を相関マーカーで照合し、台帳を補う。
- 依頼本文と依頼者名に含まれる `teams-bridge:` は、ブリッジが全角コロンに置き換えて無効化する。
- 台帳に記録する前に受け箱のジョブファイルが消えると、再試行で二重提出が起こりうる。受け箱の `done/` にあるジョブファイルはすぐに削除しない。
- 既定の台帳は `~/.local/state/change-watch/teams-sharepoint-bridge-agent/submitted.json`。コマンドの `--ledger` または
  環境変数 `TEAMS_BRIDGE_LEDGER` で変えられる。既定の違反ログは既定の台帳と同じフォルダの `invalid.jsonl`。
  Dagu の例で保存先を変えるときは、両方の `--ledger` と `--log` をそろえて直す。

## セキュリティと検証状況

投稿本文はそのまま Copilot へのプロンプトに入る。Power Automate の往路フローで、対象チャンネルと投稿者を許可リストに絞る。
実 SharePoint・Teams・Power Automate・GitLab を使った一連の動作は未検証。
フェッチャはトークンを持たず、同期済みの依頼ファイルをローカル受信フォルダへ運ぶだけ。
フェッチャ方式は実 SharePoint・SSO・タスクスケジューラ・Windows サービスでは未検証。
