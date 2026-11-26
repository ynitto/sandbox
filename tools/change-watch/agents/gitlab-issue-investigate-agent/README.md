# gitlab-issue-investigate-agent

1. change-watch が開いている GitLab Issue を 5 分ごとに取得します。Dagu は毎分、consumer `issue-investigate` に変化を 1 件ずつ渡します。
2. `issue_investigate.py` は新規（`appeared`）かつ開いていて、タイトルに設定文字列を含む Issue を選びます。調査済みマーカーがなければ goalie の受け箱に依頼を出します。
3. Copilot アプリが受け箱から依頼を拾い、作業フォルダのコードと履歴をステートマシンで調べ、コメント原案を返します。
4. `issue_investigate.py` が同じ Issue にマーカー付きの結果を投稿します。Dagu は `commit --refresh` で投稿後の姿を記録します。

## 調査とコメント原案

調査手順と起票（コメント原案の作成）は、statemachine-use の [ステートマシン定義](machine/workflow.yaml) にあります。

| ステート | 出力と遷移 |
| --- | --- |
| `read_issue` | Issue の情報から調査観点を整理。 `OK` → `investigate`、`FAILED` → `failed` |
| `investigate` | コードや履歴を読み、根拠を整理。 `FOUND` → `draft_comment`、`NOT_FOUND` → `draft_inconclusive` |
| `draft_comment` | 見立て、根拠、再現・確認方法、次のアクション案をコメント原案として返す |
| `draft_inconclusive` | 原因を特定できなかった旨と追加で必要な情報をコメント原案として返す |
| `failed` | 調査を進められない理由を返す |

仕様は [.github/skills/statemachine-use/SKILL.md](../../../../.github/skills/statemachine-use/SKILL.md) と [references/schema.md](../../../../.github/skills/statemachine-use/references/schema.md) を参照してください。

実際に GitLab へ投稿するのは `issue_investigate.py` です。GitLab トークンを Copilot アプリ側に渡さないよう、goalie の子プロセス環境から `GITLAB_TOKEN` と `GL_TOKEN` を外します。アプリからはステートマシンの最終出力（コメント原案）を受け取ります。

## 入れ方

リポジトリのルートで実行します。

```sh
python3 tools/change-watch/install.py install --agent gitlab-issue-investigate-agent
```

1. `~/.config/change-watch/gitlab-issue-investigate-agent/gitlab-issue-investigate-agent.env` を編集します。対象の GitLab URL とプロジェクトを設定し、Issue の読み取りとコメント投稿ができる `api` スコープのトークンを入れます。タイトル一致文字列は `ISSUE_INVESTIGATE_TITLE_CONTAINS` で変えられます。
2. Copilot アプリで `ISSUE_INVESTIGATE_WORK_DIR` の作業フォルダを開きます。アプリから statemachine-use スキルとインストール済みの定義を読めるようにします。`goalie.py job prompt --dir <作業フォルダ>` が出すプロンプトを使い、そのフォルダで受け箱を見る自動実行を設定します。インストールした CLI から goalie.py の相対パスが解決できない場合は、`ISSUE_INVESTIGATE_GOALIE_PY` に goalie.py の絶対パスを設定します。 [goalie の受け箱設定](../../../goalie/README.md)
3. `~/.github/connections.yaml` に以下の接続を用意します。既存の `gitlab` がある場合は、使用する `default` の接続を確認します。

   ```yaml
   gitlab:
     - label: default
       url: ${GITLAB_URL}
       token: ${GITLAB_TOKEN}
   ```

4. Dagu の DAG ディレクトリに `change-watch-issue-investigate.yaml` と `change-watch-gitlab-issue-investigate-agent-maintenance.yaml` があることを確認します。インストーラに Dagu のディレクトリを指定する場合は `--dagu-dir` を使います。手で登録する場合は各 `.yaml.example` をコピーし、`/home/you` で始まるパスを直してください。前者は毎分 1 件を処理し、後者は日次で取得結果を整理します。

## 試運転と終了コード

`ISSUE_INVESTIGATE_DRY_RUN=1` で Dagu のログに出る依頼内容と投稿先を確認し、実際に投稿するときは `0` にします。CLI を手で動かす場合は `run --dry-run` でも試せます。試運転中も DAG は処理した変化を記録するので、切り替え後の確認には新しい Issue を使ってください。

| goalie の終了コード | エージェントの動き |
| --- | --- |
| 0 | コメント原案を投稿する |
| 1 | 調査が失敗した旨と、あれば報告本文を投稿する |
| 2 | 設定や使い方のエラーとして終了する。 |
| 3 | 依頼は拾われず取り下げ済み。Dagu が 300 秒間隔で最大 3 回再試行する |
| 4 | 着手済みの依頼が残る。再投入せず、タイムアウトと手動確認の案内を投稿する |

投稿対象の終了コードは結果をローカルに控えてから投稿します。投稿に失敗して同じ Issue を再処理したときは、控えた結果を使います。

## 設定項目

| 環境変数 | 値 |
| --- | --- |
| `GITLAB_URL` | 必須。GitLab のベース URL |
| `GITLAB_PROJECT` | 必須。プロジェクトのパス（`group/project`） |
| `GITLAB_TOKEN` | 必須。コメント投稿用の `api` スコープのトークン |
| `ISSUE_INVESTIGATE_TITLE_CONTAINS` | 必須。タイトルに含まれる文字列（例 `障害調査`） |
| `ISSUE_INVESTIGATE_WORK_DIR` | 必須。Copilot アプリで開く作業フォルダ |
| `ISSUE_INVESTIGATE_GOALIE_PY` | 任意。既定は CLI から見た `../../../goalie/goalie.py` |
| `ISSUE_INVESTIGATE_TIMEOUT_SEC` | 任意。goalie の待ち時間。既定は 3600 秒。変更する場合は DAG の `timeout_sec` にも余裕を持たせる |
| `ISSUE_INVESTIGATE_STATE_DIR` | 任意。依頼ファイルと投稿待ち結果の置き場所。既定は `~/.local/state/change-watch/gitlab-issue-investigate-agent` |
| `ISSUE_INVESTIGATE_DRY_RUN` | 任意。`1`、`true`、`yes`、`on` で試運転。env の雛形は `1` |

## 検証範囲

GitLab と goalie を差し替えた unittest と、ステートマシン定義の dry-run で検証します。実 GitLab、実 Copilot アプリ、実 Dagu での動作は未検証です。
