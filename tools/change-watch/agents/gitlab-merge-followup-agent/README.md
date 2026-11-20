# gitlab-merge-followup-agent（仮置き）

人が GitLab MR をマージした後に、関連 Issue のクローズ確認コメントと CHANGELOG の追記案を下書きにする。
マージ自体、Issue のクローズ、CHANGELOG の実編集は自動化しない。関連 Issue へのコメントも下書きに含め、投稿先は設定したハブ Issue に限る。

## 設定と実行

1. `tools/change-watch` で `python3 install.py install --agent gitlab-merge-followup-agent` を実行する。
2. `~/.config/change-watch/gitlab-merge-followup-agent/env` の GitLab URL、対象プロジェクト（`group/project` または数値 ID）、トークン、ハブ Issue URL を設定する。マージ済み MR と関連 Issue の参照権限が必要。投稿を有効にする場合は、ハブにコメントできる `api` スコープのトークンを使う。ハブには同じ GitLab 上のブラウザ URL または Issue の API URL を指定する。
3. 配置された `change-watch.yaml` の `incremental.initial` を運用開始時刻に変更する。Dagu 例は初回も、その時刻以降に更新されたマージ済み MR を処理する。以前にマージされた MR も、開始時刻以降に更新されると対象に入る。
4. Dagu 例を起動する。取得には `~/.github/connections.yaml` の `gitlab` 接続を使う。`url` と `token` からそれぞれ `GITLAB_URL` と `GITLAB_TOKEN` を参照する設定は、[既存の接続設定例](../gitlab-issue-hub-agent/README.md)を参照する。

`get` が 1 件の変化を渡し、`draft` が MR の詳細、関連 Issue、コミット件名を取得して `drafts/mr-<IID>.md` と `drafts/mr-<IID>.json` を作る。関連 Issue は GitLab の `closes_issues` を優先し、未提供の場合は MR の説明文で `Closes`、`Fixes`、`Resolves`（活用形を含む）に続く `#n` または同じ GitLab 上の Issue URL を使う。JSON には `mr_iid`、`related_issues`（プロジェクト ID・IID・URL・現在の状態）、`draft_types` が入る。

`post` は既定で dry-run になり、Markdown の場所を表示する。実投稿するには `env` に `MERGE_FOLLOWUP_DRY_RUN=0` を設定する。最後の `commit` は change-watch に処理済みと記録する。dry-run で処理済みになった下書きを投稿する場合は、設定変更後に保存済みファイルを指定して `post` を実行する。下書きの先頭の印で MR を識別できる。投稿の成否が不明な場合は、再実行前にハブで印を確認する。

環境変数を設定したシェルでは個別に実行できる。`change.json` には `change-watch get --one` の出力を保存しておく。

```sh
change-watch --plugin gitlab_merge_followup draft - --out mr-12.md --json-out mr-12.json < change.json
change-watch --plugin gitlab_merge_followup post mr-12.md
```

## 下書きの例

```markdown
【下書き】マージ後処理（MR !12）

対象: [MR !12](<https://gitlab.example.com/group/project/-/merge_requests/12>)

## クローズ確認コメントの下書き

### [Issue #7](<https://gitlab.example.com/group/project/-/issues/7>)（未クローズ）

[MR !12](<https://gitlab.example.com/group/project/-/merge_requests/12>) がマージされました。取り込まれた変更: change-watch: 取得時の再試行を追加 / 一時的な失敗から復帰できるようにする。 / 取得エラー時の再試行を実装
未クローズの場合はこの Issue を閉じてよいか、クローズ済みの場合はそのままでよいか確認してください。

## CHANGELOG 追記案（[Unreleased]）

### change-watch: 取得時の再試行を追加

- 一時的な失敗から復帰できるようにする。
- 取得エラー時の再試行を実装
```

MR のタイトルが `ツール名: 要約` なら、その形で見出しを作る。それ以外は対象プロジェクトの設定値の末尾と MR タイトルを使う。
