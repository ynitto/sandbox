# teams-sharepoint-bridge-agent

Teams の投稿から Power Automate が SharePoint に作った依頼 JSON を、同期先のフォルダから受け取る。
このエージェントは依頼を検証し、宛先の Copilot アプリの受け箱へ相関マーカー付きの依頼文を提出する。
Copilot が GitLab に結果を上げ、GitLab の通知メールを受けた Power Automate が Teams の元の投稿へ返信する。

## 用意と動かし方

1. OneDrive / SharePoint の同期クライアントで、SharePoint の依頼フォルダを Dagu が動く PC に同期する。
   往路の依頼 JSON と復路の返信は [Power Automate のレシピ](power-automate/) を参照。
2. リポジトリのルートからインストーラを動かす。change-watch とエージェントのプログラム、設定の雛形、Dagu のワークフローが入る。

   ```sh
   python3 tools/change-watch/install.py install --agent teams-sharepoint-bridge-agent
   ```

3. `~/.config/change-watch/teams-sharepoint-bridge-agent/` に置かれた `change-watch.yaml` の `folder.path` を同期先に直す。
   `targets.json` の `targets` に宛先名ごとの設定を書く。 `dir` は Copilot アプリで開く作業フォルダの絶対パス、
   `gitlab_project` は `group/project` の形、`mode` は issue を作るなら `issue`、コミットをプッシュするなら `commit`。
   依頼 JSON の `target` が省略されたときは `default` の宛先名を使う。
4. 各作業フォルダで [copilot-app-jobs](../../../copilot-app-jobs/README.md) の手順に従い、受け箱を拾う Copilot アプリの自動実行を用意する。
   Dagu を起動する環境の `TEAMS_BRIDGE_COPILOT_JOB` に `copilot_app_job.py` の絶対パスを設定する。
   リポジトリ内の `teams_bridge.py` を直接使うときは、未設定なら同じリポジトリのスクリプトを探す。
5. Dagu のスケジューラを動かす。インストーラが `~/.config/dagu/dags/` に置く `change-watch-teams-bridge.yaml` は毎分起動し、
   `get` で変化を 1 件受け取る。新しく現れたファイルだけを `check-file` に渡し、結果に応じて `submit` または
   `record-invalid` を動かしてから `commit` する。処理済みや見つからない依頼、追加以外の変化も `commit` する。
   このソリューションに要る DAG 例は [dags/](dags/README.md) に一式そろえた（原本はこのフォルダの直下と各ツール側にあり、dags/ はコピー）。

## コマンド

`change-watch --plugin teams_bridge <コマンド>` または `python3 teams_bridge.py <コマンド>` で呼ぶ。
ブリッジのコマンドは change-watch の設定を読まない。change-watch 本体の設定確認は `change-watch check` で行う。

| コマンド | 動作 |
|---|---|
| `check-file --path F [--targets F] [--ledger F]` | 未提出なら `submit`、提出済みなら `done`、様式違反なら `invalid`、読めない依頼なら `missing` を出す |
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
- 既定の台帳は `~/.local/state/change-watch/teams-sharepoint-bridge-agent/submitted.json`。コマンドの `--ledger` または
  環境変数 `TEAMS_BRIDGE_LEDGER` で変えられる。既定の違反ログは既定の台帳と同じフォルダの `invalid.jsonl`。
  Dagu の例で保存先を変えるときは、両方の `--ledger` と `--log` をそろえて直す。

## セキュリティと検証状況

投稿本文はそのまま Copilot へのプロンプトに入る。Power Automate の往路フローで、対象チャンネルと投稿者を許可リストに絞る。
実 SharePoint・Teams・Power Automate・GitLab を使った一連の動作は未検証。
