# GitLab API — Python スクリプトリファレンス

## 目次

- [セットアップ](#セットアップ)
- [--get オプション](#--get-オプション)
- [認証・ユーザー](#認証ユーザー)
- [イシュー操作](#イシュー操作)
- [ブランチ名生成](#ブランチ名生成)
- [MR（マージリクエスト）操作](#mrマージリクエスト操作)
- [self-defer チェック](#self-defer-チェック)
- [トラブルシューティング](#トラブルシューティング)

`scripts/gl.py` を Python で呼び出すコマンド集。`glab` CLI は不要。
Python 3.8+ と stdlib のみで動作し、Windows・macOS・Linux に対応する。

---

## セットアップ

GITLAB_TOKEN 環境変数を設定する（必須）:

- **bash/zsh**: `export GITLAB_TOKEN=glpat-xxxxxxxxxxxx`
- **PowerShell**: `$env:GITLAB_TOKEN = "glpat-xxxxxxxxxxxx"`
- **cmd.exe**: `set GITLAB_TOKEN=glpat-xxxxxxxxxxxx`

動作確認（git remote からホスト・プロジェクトを自動取得）:

```
python scripts/gl.py project-info
```

> **注**: 環境によって `python` を `python3` や `py` に読み替える。

`project-info` の出力例:
```json
{
  "host": "gitlab.com",
  "project": "myteam/myapp",
  "project_encoded": "myteam%2Fmyapp",
  "base_url": "https://gitlab.com/myteam/myapp"
}
```

---

## --get オプション

すべての読み取りコマンドで `--get FIELD` を使うとフィールドだけを出力できる。
ドット記法で配列インデックスとネストに対応する。

```
python scripts/gl.py current-user --get username
python scripts/gl.py get-issue 42 --get title
python scripts/gl.py get-issue 42 --get author.username
python scripts/gl.py list-mrs --source-branch BRANCH --get 0.web_url
python scripts/gl.py list-mrs --source-branch BRANCH --get 0.iid
python scripts/gl.py check-defer 42 --get defer
python scripts/gl.py check-defer 42 --get remaining_minutes
```

---

## 認証・ユーザー

```
python scripts/gl.py current-user
python scripts/gl.py current-user --get username
```

---

## ノードID

ターミナル（ノード）を識別するIDを確認する。`GITLAB_NODE_ID` 環境変数で上書き可能。
同一 GitLab アカウントで複数ターミナルを独立ノードとして動かす場合に設定する。

```
python scripts/gl.py get-node-id
python scripts/gl.py --get node_id get-node-id
# → 12 文字のランダム文字列（~/.config/gitlab-idd/node-id に自動保存）

# ターミナルごとに上書きする場合:
export GITLAB_NODE_ID=my-terminal-1
python scripts/gl.py --get node_id get-node-id
# → "my-terminal-1"
```

> **注**: イシュー作成時（`create-issue`）に creator-node-id が description に自動埋め込まれる。
> ワーカー着手時には `worker-node-id` を着手コメントに含める（worker-role.md ステップ 3-3 参照）。
> これらを `check-defer` / `check-review-defer` が読み取り、ターミナル単位で自己判定を行う。

---

## イシュー操作

### 一覧取得

```
python scripts/gl.py list-issues --state opened
python scripts/gl.py list-issues --label "status:open"
python scripts/gl.py list-issues --label "status:open,assignee:any"
python scripts/gl.py list-issues --label "status:open,priority:high"
python scripts/gl.py list-issues --assignee MY_USER --state opened
python scripts/gl.py list-issues --author  MY_USER --state opened
python scripts/gl.py list-issues --label "status:review-ready" --author MY_USER
python scripts/gl.py list-issues --label "status:needs-rework" --assignee MY_USER
```

### イシュー詳細・コメント

```
python scripts/gl.py get-issue 42
python scripts/gl.py get-comments 42
python scripts/gl.py get-issue 42 --get title
python scripts/gl.py get-issue 42 --get author.username
```

### イシュー作成

複数行のイシュー本文は `--body-file` でファイルから渡す。
まず本文を Markdown ファイル（例: `_body.md`）に書いてからコマンドを実行する:

```
python scripts/gl.py create-issue \
  --title "ログインフォームを実装する" \
  --body-file _body.md \
  --labels "status:open,assignee:any,priority:normal"
```

`_body.md` の内容例:

```markdown
## 目的

{目的を 1〜3 文で記述}

## 実装スコープ

- {変更点 1}

## 受け入れ条件

- [ ] {条件 1}
- [ ] {条件 2}

## 技術制約

特になし
```

短い本文は `--body` に直接渡すことも可能:

```
python scripts/gl.py create-issue --title "タイトル" --body "短い説明" --labels "status:open,assignee:any,priority:normal"
```

### イシュー更新

ラベル・アサイニー・状態の更新:

```
python scripts/gl.py update-issue 42 --add-labels "status:in-progress" --remove-labels "status:open,assignee:any"
python scripts/gl.py update-issue 42 --assignee MY_USER
python scripts/gl.py update-issue 42 --state-event close
python scripts/gl.py update-issue 42 --state-event reopen
python scripts/gl.py update-issue 42 --add-labels "status:needs-rework" --remove-labels "status:review-ready" --state-event reopen
```

イシュー本文（description）の更新:

```
python scripts/gl.py update-issue 42 --body-file _clarified_body.md
python scripts/gl.py update-issue 42 --body "更新後の説明（短い場合）"
```

複数オプションの組み合わせも可能:

```
python scripts/gl.py update-issue 42 \
  --body-file _clarified_body.md \
  --add-labels "status:open,assignee:any" \
  --remove-labels "status:needs-clarification"
```

### コメント投稿

短いコメントは `--body` に直接渡す:

```
python scripts/gl.py add-comment 42 --body "作業開始しました"
```

複数行のコメントは `--body-file` でファイルから渡す:

```
python scripts/gl.py add-comment 42 --body-file _comment.md
```

`_comment.md` の内容例:

```markdown
## ✅ 実装完了

受け入れ条件をすべて満たしました。
```

---

## ブランチ名生成

```
python scripts/gl.py make-branch-name 42
# → "feature/issue-42-add-login-form"
```

---

## MR（マージリクエスト）操作

```
python scripts/gl.py list-mrs --state opened
python scripts/gl.py list-mrs --source-branch "feature/issue-42-add-login"
python scripts/gl.py list-mrs --source-branch BRANCH --get 0.web_url
python scripts/gl.py list-mrs --source-branch BRANCH --get 0.iid
```

MR 作成（本文をファイルから渡す）:

```
python scripts/gl.py create-mr \
  --title "ログインフォームを実装する" \
  --source-branch "feature/issue-42-add-login-form" \
  --target-branch main \
  --description-file _mr_body.md \
  --draft \
  --remove-source-branch
```

短い説明なら `--description` に直接渡すことも可能:

```
python scripts/gl.py create-mr \
  --title "ログインフォームを実装する" \
  --source-branch "feature/issue-42-add-login-form" \
  --target-branch main \
  --description "Closes #42" \
  --draft \
  --remove-source-branch
```

MR 更新（本文の書き換え・ドラフト解除）:

```
python scripts/gl.py update-mr MR_IID \
  --description-file _mr_body.md \
  --no-draft
```

`--description` に直接渡すことも可能:

```
python scripts/gl.py update-mr MR_IID --description "更新後の説明" --no-draft
```

```
python scripts/gl.py merge-mr MR_ID --squash --remove-source-branch
```

CI パイプラインの確認（マージ前に実行）:

```
python scripts/gl.py get-mr-pipeline MR_IID
python scripts/gl.py get-mr-pipeline MR_IID --get status
# → "success" / "running" / "pending" / "failed" / "canceled" / "skipped" / "none"
```

`"none"` は CI が未設定またはまだトリガーされていない場合に返る。

---

## self-defer チェック

猶予／ロック判定コマンドは 3 種類ある。いずれも `defer=true` ならスキップ、`false` なら着手可。

### check-defer（自分発行イシューの猶予）

自分のターミナル（ノード）が作成したイシューを猶予期間中はスキップするためのチェック。
「自分かどうか」はイシュー description に埋め込まれた `creator-node-id` で判定する（フォールバック: author.username）。

```
python scripts/gl.py check-defer 42 --get defer
# → True または False

python scripts/gl.py check-defer 42 --minutes 60 --get defer
python scripts/gl.py check-defer 42 --minutes 60 --get remaining_minutes
```

`check-defer` の判定結果:

| reason | defer | 意味 |
|--------|-------|------|
| `not_my_issue` | false | 他ノードが作成 → 即取得可 |
| `self_created_too_recent` | true | 自ノード作成・猶予中（デフォルト 60 分）→ スキップ |
| `self_created_but_expired` | false | 自ノード作成・猶予切れ → 取得可 |

### check-assigned-defer（他ノード着手済みイシューの疎境期間）

`worker-node-id` が別ノードのイシューが放置されていないか確認する。
ロック期間内は引き継ぎ禁止。期間切れなら取得可。

```
python scripts/gl.py check-assigned-defer 42 --get defer
# → True または False

python scripts/gl.py check-assigned-defer 42 --minutes 1440 --get defer
python scripts/gl.py check-assigned-defer 42 --get remaining_minutes
```

`check-assigned-defer` の判定結果:

| reason | defer | 意味 |
|--------|-------|------|
| `no_worker_node_id` | false | 着手記録なし → 引き受け可 |
| `my_assignment` | false | 自分が着手済み → 継続可 |
| `assigned_active_lock` | true | 別ノードが着手中（デフォルト 1440 分）→ スキップ |
| `assigned_lock_unknown` | true | 着手時刻不明 → スキップ |
| `assigned_lock_expired` | false | 着手から 24h 経過し放置 → 引き受け可 |

### check-review-defer（自分実装イシューのレビューロック）

イシューのコメントに埋め込まれた `worker-node-id` とロック時間で判定する。
`worker-node-id` がないイシューは誰でもレビュー可能（defer=false）。

```
python scripts/gl.py check-review-defer 42
python scripts/gl.py check-review-defer 42 --minutes 1440
python scripts/gl.py check-review-defer 42 --get defer
```

---

## トラブルシューティング

| エラー | 原因 | 対処 |
|--------|------|------|
| `Set GITLAB_TOKEN...` | トークン未設定 | GITLAB_TOKEN 環境変数を設定する |
| `Cannot get git remote 'origin'` | git リポジトリ外 or remote なし | 正しいディレクトリで実行 |
| `HTTP 401 Unauthorized` | トークン無効・期限切れ | 新しいトークンを発行して再設定 |
| `HTTP 403 Forbidden` | 権限不足 | GitLab プロジェクトの Developer 以上の権限が必要 |
| `HTTP 404 Not Found` | remote の URL が間違っている | `git remote get-url origin` で確認 |
| `python: command not found` | Python コマンド名が違う | `python3` または `py` を試す |
| MR マージ失敗 | CI パイプラインが失敗中 | GitLab の MR ページでパイプライン状態を確認 |
