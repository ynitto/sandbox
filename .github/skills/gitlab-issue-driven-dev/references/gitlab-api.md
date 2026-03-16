# GitLab API リファレンス

`glab` CLI（GitLab 公式 CLI）を使ったコマンド集。すべてのコマンドは `$GITLAB_PROJECT`（`namespace/repo` 形式）を対象とする。

---

## セットアップ・認証確認

```bash
# 認証状態確認
glab auth status

# ログイン（初回）
glab auth login --hostname "$GITLAB_HOST"

# 現在のユーザー名を取得
glab api user | jq -r '.username'

# 環境変数の設定（セッション開始時に実行）
export GITLAB_PROJECT="namespace/repo"
export GITLAB_HOST="gitlab.com"          # セルフホスト時は変更
```

---

## イシュー操作

### 一覧取得

```bash
# オープンイシューを全件取得（JSON）
glab issue list \
  --label "status:open" \
  --repo "$GITLAB_PROJECT" \
  --output json

# ラベル複数指定（AND 条件）
glab issue list \
  --label "status:open,assignee:any" \
  --repo "$GITLAB_PROJECT" \
  --output json

# 自分に assign されたイシュー
glab issue list \
  --assignee "$(glab api user | jq -r '.username')" \
  --repo "$GITLAB_PROJECT" \
  --output json

# 優先度でフィルタ
glab issue list \
  --label "status:open,priority:high" \
  --repo "$GITLAB_PROJECT" \
  --output json

# review-ready のみ
glab issue list \
  --label "status:review-ready" \
  --repo "$GITLAB_PROJECT" \
  --output json
```

### イシュー詳細・コメント取得

```bash
# 詳細表示（コメント含む）
glab issue view {issue_id} --repo "$GITLAB_PROJECT"

# JSON 形式
glab issue view {issue_id} \
  --repo "$GITLAB_PROJECT" \
  --output json

# コメント一覧（REST API 経由）
glab api "projects/{encoded_project}/issues/{issue_id}/notes" \
  | jq '.[] | {id: .id, author: .author.username, body: .body, created_at}'
# ※ encoded_project は namespace%2Frepo 形式
```

### イシュー作成

```bash
# 基本作成
glab issue create \
  --title "タイトル" \
  --description "$(cat /tmp/issue_body.md)" \
  --label "status:open,assignee:any,priority:normal" \
  --repo "$GITLAB_PROJECT"

# マイルストーン指定（オプション）
glab issue create \
  --title "タイトル" \
  --description "説明" \
  --label "status:open" \
  --milestone "Sprint-1" \
  --repo "$GITLAB_PROJECT"
```

### イシュー更新（ラベル・アサイン）

```bash
# ラベル追加・削除
glab issue update {issue_id} \
  --label "status:in-progress" \
  --remove-label "status:open,assignee:any" \
  --repo "$GITLAB_PROJECT"

# assignee 設定
glab issue update {issue_id} \
  --assignee "username" \
  --repo "$GITLAB_PROJECT"

# assignee 削除（先着制に戻す）
glab issue update {issue_id} \
  --unassign \
  --repo "$GITLAB_PROJECT"
```

### コメント（ノート）投稿

```bash
# 短いコメント
glab issue note {issue_id} \
  --body "コメント本文" \
  --repo "$GITLAB_PROJECT"

# 複数行コメント（ヒアドキュメント）
glab issue note {issue_id} \
  --body "$(cat << 'EOF'
## セクション

本文をここに
EOF
)" \
  --repo "$GITLAB_PROJECT"
```

### イシュークローズ・リオープン

```bash
# クローズ
glab issue close {issue_id} --repo "$GITLAB_PROJECT"

# リオープン
glab issue reopen {issue_id} --repo "$GITLAB_PROJECT"
```

---

## MR（マージリクエスト）操作

### 一覧取得

```bash
# オープン MR 一覧
glab mr list --repo "$GITLAB_PROJECT" --output json

# ブランチ名で絞り込み
glab mr list \
  --source-branch "feature/issue-42*" \
  --repo "$GITLAB_PROJECT" \
  --output json

# MR の ID を取得
MR_ID=$(glab mr list \
  --source-branch "$BRANCH" \
  --repo "$GITLAB_PROJECT" \
  --output json | jq -r '.[0].iid')
```

### MR 作成

```bash
# ドラフト MR を作成
glab mr create \
  --title "Draft: タイトル" \
  --description "$(cat /tmp/mr_body.md)" \
  --source-branch "$BRANCH" \
  --target-branch main \
  --draft \
  --repo "$GITLAB_PROJECT"

# ドラフト解除（レビュー依頼時）
glab mr update "$MR_ID" \
  --ready \
  --repo "$GITLAB_PROJECT"
```

### MR マージ

```bash
# squash マージ（ソースブランチ削除）
glab mr merge "$MR_ID" \
  --squash \
  --remove-source-branch \
  --repo "$GITLAB_PROJECT"

# 通常マージ
glab mr merge "$MR_ID" \
  --repo "$GITLAB_PROJECT"
```

### MR の差分確認

```bash
# MR の diff を表示
glab mr diff "$MR_ID" --repo "$GITLAB_PROJECT"

# ローカルで確認
git fetch origin "$BRANCH"
git diff main..origin/"$BRANCH"
```

---

## ブランチ操作

```bash
# ブランチ作成
git fetch origin main
git checkout -b "feature/issue-{id}-{slug}" origin/main

# push
git push -u origin "feature/issue-{id}-{slug}"

# リモートブランチ一覧（issue 関連）
git branch -r | grep "feature/issue-"

# ブランチ削除（マージ後）
git push origin --delete "feature/issue-{id}-{slug}"
```

---

## REST API 直接呼び出し（`glab api`）

`glab api` は GitLab REST API に直接アクセスできる。プロジェクトパスのエンコードが必要:

```bash
# namespace/repo → namespace%2Frepo
ENCODED_PROJECT=$(echo "$GITLAB_PROJECT" | sed 's/\//%2F/g')

# イシュー一覧（ページネーション）
glab api "projects/${ENCODED_PROJECT}/issues?state=opened&labels=status:open&per_page=20"

# イシューの assignee を API で確認
glab api "projects/${ENCODED_PROJECT}/issues/{issue_id}" \
  | jq '.assignees[].username'

# MR の CI パイプライン状態確認
glab api "projects/${ENCODED_PROJECT}/merge_requests/{mr_id}/pipelines" \
  | jq '.[0] | {status: .status, web_url: .web_url}'
```

---

## よく使う jq パターン

```bash
# イシュー一覧から id と title だけ抽出
glab issue list --label "status:open" --repo "$GITLAB_PROJECT" --output json \
  | jq '.[] | {id: .iid, title: .title, priority: .labels}'

# 最優先イシューを 1 件選ぶ
glab issue list --label "status:open" --repo "$GITLAB_PROJECT" --output json \
  | jq 'sort_by(.created_at) | .[0]'

# MR の URL を取得
glab mr list --source-branch "$BRANCH" --repo "$GITLAB_PROJECT" --output json \
  | jq -r '.[0].web_url'
```

---

## トラブルシューティング

| エラー | 原因 | 対処 |
|--------|------|------|
| `ERRO 401 Unauthorized` | 認証切れ | `glab auth login` を再実行 |
| `ERRO 403 Forbidden` | 権限不足 | プロジェクトの Developer 以上の権限が必要 |
| `ERRO 404 Not Found` | プロジェクトパスが間違っている | `$GITLAB_PROJECT` の値を確認 |
| `glab: command not found` | `glab` 未インストール | <https://gitlab.com/gitlab-org/cli> を参照してインストール |
| MR マージ失敗（パイプライン） | CI が失敗している | `glab pipeline list --repo "$GITLAB_PROJECT"` で確認 |

---

## `glab` インストール

```bash
# macOS
brew install glab

# Linux (apt)
sudo apt install glab

# Linux (curl)
curl -s https://packagecloud.io/install/repositories/gitlab/cli/script.deb.sh | sudo bash
sudo apt install glab

# 公式ドキュメント
# https://gitlab.com/gitlab-org/cli
```
