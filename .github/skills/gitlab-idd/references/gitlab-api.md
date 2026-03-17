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

```bash
# トークン設定（必須）
export GITLAB_TOKEN=glpat-xxxxxxxxxxxx

# GL ショートハンド定義（以降の例で使用）
# python コマンドは環境に合わせて python3 や py に読み替える
GL="python scripts/gl.py"

# 動作確認（git remote から ホスト・プロジェクトを自動取得）
$GL project-info
```

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

```bash
$GL current-user --get username          # → "alice"
$GL get-issue 42 --get title             # → "ログインフォームを実装する"
$GL get-issue 42 --get author.username   # → "bob"
$GL list-mrs --source-branch "$BRANCH" --get 0.web_url   # → "https://..."
$GL list-mrs --source-branch "$BRANCH" --get 0.iid       # → 7
$GL check-defer 42 --get defer           # → True or False
$GL check-defer 42 --get remaining_minutes  # → 47
```

---

## 認証・ユーザー

```bash
$GL current-user
MY_USER=$($GL current-user --get username)
```

---

## イシュー操作

### 一覧取得

```bash
$GL list-issues --state opened
$GL list-issues --label "status:open"
$GL list-issues --label "status:open,assignee:any"
$GL list-issues --label "status:open,priority:high"
$GL list-issues --assignee "$MY_USER" --state opened
$GL list-issues --author  "$MY_USER" --state opened
$GL list-issues --label "status:review-ready" --author "$MY_USER"
$GL list-issues --label "status:needs-rework" --assignee "$MY_USER"
```

### イシュー詳細・コメント

```bash
$GL get-issue 42
$GL get-comments 42
$GL get-issue 42 --get title
$GL get-issue 42 --get author.username
```

### イシュー作成

```bash
BODY=$(cat << 'EOF'
## 目的

{目的を 1〜3 文で記述}

## 実装スコープ

- {変更点 1}

## 受け入れ条件

- [ ] {条件 1}
- [ ] {条件 2}

## 技術制約

特になし
EOF
)

$GL create-issue \
  --title "ログインフォームを実装する" \
  --body "$BODY" \
  --labels "status:open,assignee:any,priority:normal"
```

### イシュー更新

```bash
$GL update-issue 42 \
  --add-labels "status:in-progress" \
  --remove-labels "status:open,assignee:any"

$GL update-issue 42 --assignee "$MY_USER"
$GL update-issue 42 --state-event close
$GL update-issue 42 --state-event reopen

$GL update-issue 42 \
  --add-labels "status:needs-rework" \
  --remove-labels "status:review-ready" \
  --state-event reopen
```

### コメント投稿

```bash
$GL add-comment 42 --body "作業開始しました"

COMMENT=$(cat << 'EOF'
## ✅ 実装完了

受け入れ条件をすべて満たしました。
EOF
)
$GL add-comment 42 --body "$COMMENT"
```

---

## ブランチ名生成

```bash
BRANCH=$($GL make-branch-name 42)
# → "feature/issue-42-add-login-form"
```

---

## MR（マージリクエスト）操作

```bash
$GL list-mrs --state opened
$GL list-mrs --source-branch "feature/issue-42-add-login"
MR_URL=$($GL list-mrs --source-branch "$BRANCH" --get 0.web_url)
MR_ID=$( $GL list-mrs --source-branch "$BRANCH" --get 0.iid)

$GL create-mr \
  --title "ログインフォームを実装する" \
  --source-branch "feature/issue-42-add-login-form" \
  --target-branch main \
  --description "Closes #42" \
  --draft

$GL merge-mr "$MR_ID" --squash --remove-source-branch
```

---

## self-defer チェック

```bash
DEFER_MINUTES=${GITLAB_SELF_DEFER_MINUTES:-60}

if [ "$($GL check-defer 42 --minutes "$DEFER_MINUTES" --get defer)" = "True" ]; then
  REMAINING=$($GL check-defer 42 --minutes "$DEFER_MINUTES" --get remaining_minutes)
  echo "スキップ: 残り ${REMAINING} 分後に実行可能"
fi
```

`check-defer` の出力パターン:

| reason | defer | 意味 |
|--------|-------|------|
| `not_my_issue` | false | 他者が作成 → 即取得可 |
| `self_created_too_recent` | true | 自分作成・猶予中 → スキップ |
| `self_created_but_expired` | false | 自分作成・猶予切れ → 取得可 |

---

## トラブルシューティング

| エラー | 原因 | 対処 |
|--------|------|------|
| `Set GITLAB_TOKEN...` | トークン未設定 | `export GITLAB_TOKEN=glpat-...` |
| `Cannot get git remote 'origin'` | git リポジトリ外 or remote なし | 正しいディレクトリで実行 |
| `HTTP 401 Unauthorized` | トークン無効・期限切れ | 新しいトークンを発行して再設定 |
| `HTTP 403 Forbidden` | 権限不足 | GitLab プロジェクトの Developer 以上の権限が必要 |
| `HTTP 404 Not Found` | remote の URL が間違っている | `git remote get-url origin` で確認 |
| `python: command not found` | Python コマンド名が違う | `python3` または `py` を試す |
| MR マージ失敗 | CI パイプラインが失敗中 | GitLab の MR ページでパイプライン状態を確認 |
