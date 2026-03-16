# GitLab API — Python スクリプトリファレンス

`scripts/gl.py` を Python で呼び出すコマンド集。`glab` CLI は不要。
Python 3.8+ と stdlib のみで動作し、Windows・macOS・Linux に対応する。

---

## セットアップ

```bash
# トークン設定（必須）
export GITLAB_TOKEN=glpat-xxxxxxxxxxxx   # Linux/macOS
$env:GITLAB_TOKEN = "glpat-xxxxxxxxxxxx" # Windows PowerShell

# 動作確認（git remote から ホスト・プロジェクトを自動取得）
python .github/skills/gitlab-issue-driven-dev/scripts/gl.py project-info

# ショートハンド定義（推奨）
GL="python .github/skills/gitlab-issue-driven-dev/scripts/gl.py"   # bash
$GL = "python .github/skills/gitlab-issue-driven-dev/scripts/gl.py" # PowerShell
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

## 認証・ユーザー

```bash
# 認証ユーザー情報を取得
$GL current-user

# ユーザー名だけ抽出
MY_USER=$($GL current-user | python -c "import sys,json; print(json.load(sys.stdin)['username'])")
```

---

## イシュー操作

### 一覧取得

```bash
# オープンイシューを全件取得
$GL list-issues --state opened

# ラベルでフィルタ（AND 条件）
$GL list-issues --label "status:open"
$GL list-issues --label "status:open,assignee:any"
$GL list-issues --label "status:open,priority:high"

# 自分に assign されたイシュー
$GL list-issues --assignee "$MY_USER" --state opened

# 自分が作成したイシュー
$GL list-issues --author "$MY_USER" --state opened

# review-ready のみ
$GL list-issues --label "status:review-ready"

# needs-rework で自分担当
$GL list-issues --label "status:needs-rework" --assignee "$MY_USER"
```

### イシュー詳細・コメント

```bash
# イシュー詳細（JSON）
$GL get-issue 42

# コメント一覧
$GL get-comments 42

# タイトルだけ抽出
TITLE=$($GL get-issue 42 | python -c "import sys,json; print(json.load(sys.stdin)['title'])")

# 作成者だけ抽出
AUTHOR=$($GL get-issue 42 | python -c "import sys,json; print(json.load(sys.stdin)['author']['username'])")
```

### イシュー作成

```bash
# 本文をヒアドキュメントで渡す
BODY=$(cat << 'EOF'
## 目的

{目的を 1〜3 文で記述}

## 実装スコープ

- {変更点 1}
- {変更点 2}

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

### イシュー更新（ラベル・アサイン・状態変更）

```bash
# ラベル追加・削除
$GL update-issue 42 \
  --add-labels "status:in-progress" \
  --remove-labels "status:open,assignee:any"

# 自分に assign
$GL update-issue 42 --assignee "$MY_USER"

# クローズ
$GL update-issue 42 --state-event close

# リオープン
$GL update-issue 42 --state-event reopen

# needs-rework にして差し戻し
$GL update-issue 42 \
  --add-labels "status:needs-rework" \
  --remove-labels "status:review-ready" \
  --state-event reopen
```

### コメント投稿

```bash
# 短いコメント
$GL add-comment 42 --body "作業開始しました"

# 複数行コメント（変数経由）
COMMENT=$(cat << 'EOF'
## ✅ 実装完了

受け入れ条件をすべて満たしました。

- [x] 条件 1 → 対応内容
- [x] 条件 2 → 対応内容
EOF
)
$GL add-comment 42 --body "$COMMENT"
```

---

## MR（マージリクエスト）操作

### 一覧取得

```bash
# オープン MR 一覧
$GL list-mrs --state opened

# ブランチ名で絞り込み
$GL list-mrs --source-branch "feature/issue-42-add-login"

# MR の web_url を取得
MR_URL=$($GL list-mrs --source-branch "$BRANCH" | python -c \
  "import sys,json; mrs=json.load(sys.stdin); print(mrs[0]['web_url'] if mrs else '')")
```

### MR 作成

```bash
$GL create-mr \
  --title "ログインフォームを実装する" \
  --source-branch "feature/issue-42-add-login-form" \
  --target-branch main \
  --description "Closes #42" \
  --draft
```

### MR マージ

```bash
# squash マージ（ソースブランチ削除）
MR_ID=$($GL list-mrs --source-branch "$BRANCH" | python -c \
  "import sys,json; print(json.load(sys.stdin)[0]['iid'])")

$GL merge-mr "$MR_ID" --squash --remove-source-branch
```

---

## self-defer チェック

自分が発行したイシューを猶予期間中にスキップする:

```bash
DEFER_MINUTES=${GITLAB_SELF_DEFER_MINUTES:-60}

DEFER=$($GL check-defer 42 --minutes "$DEFER_MINUTES")
SHOULD_DEFER=$(echo "$DEFER" | python -c "import sys,json; print(json.load(sys.stdin)['defer'])")

if [ "$SHOULD_DEFER" = "True" ]; then
  REMAINING=$(echo "$DEFER" | python -c "import sys,json; print(json.load(sys.stdin)['remaining_minutes'])")
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

## JSON 値の抽出パターン

```bash
# イシュー一覧から id と title を取得
$GL list-issues --label "status:open" | python -c "
import sys, json
for issue in json.load(sys.stdin):
    print(issue['iid'], issue['title'])
"

# 優先度順にソートして先頭 1 件の id を取得
$GL list-issues --label "status:open,assignee:any" | python -c "
import sys, json
PRIORITY = {'priority:high': 0, 'priority:normal': 1, 'priority:low': 2}
issues = json.load(sys.stdin)
def key(i):
    p = min((PRIORITY.get(l, 1) for l in i.get('labels', [])), default=1)
    return (p, i['created_at'])
issues.sort(key=key)
if issues:
    print(issues[0]['iid'])
"

# イシュータイトルからブランチ名スラグを生成
$GL get-issue 42 | python -c "
import sys, json, re
title = json.load(sys.stdin)['title'].lower()
slug = re.sub(r'[^a-z0-9]+', '-', title).strip('-')[:40]
print(slug)
"
```

---

## トラブルシューティング

| エラー | 原因 | 対処 |
|--------|------|------|
| `Set GITLAB_TOKEN...` | トークン未設定 | `export GITLAB_TOKEN=glpat-...` |
| `Cannot get git remote 'origin'` | git リポジトリ外 or remote なし | 正しいディレクトリで実行 |
| `HTTP 401 Unauthorized` | トークン無効・期限切れ | 新しいトークンを発行して再設定 |
| `HTTP 403 Forbidden` | 権限不足 | GitLab プロジェクトの Developer 以上の権限が必要 |
| `HTTP 404 Not Found` | remote の URL が間違っている | `git remote get-url origin` で確認 |
| `python: command not found` | Python 未インストール | `python3` または `py` コマンドを試す（OS による） |
| `MR マージ失敗` | CI パイプラインが失敗中 | GitLab の MR ページでパイプライン状態を確認 |
