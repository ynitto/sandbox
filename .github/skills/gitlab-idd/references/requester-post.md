# リクエスター — イシュー投稿手順

タスクをイシューとして投稿し、ワーカーノードへの実行を委譲する。
すべての操作は `scripts/gl.py` を Python で実行する（`glab` CLI 不要）。

```bash
# GL ショートハンド（python コマンドは環境に合わせて python3 や py に読み替える）
GL="python scripts/gl.py"
```

---

## ステップ 1 — タスク整理

イシュー作成前に以下を確定させる:

```
タイトル    : 動詞で始まる短い命令形（例: "ログインフォームを実装する"）
目的        : なぜこのタスクが必要か（1〜3 文）
実装スコープ: 何を作るか・変更するか
受け入れ条件: 完了と判断する客観的基準（チェックリスト形式・必須）
技術制約    : 言語・フレームワーク・禁止事項（あれば）
優先度      : high / normal / low
```

曖昧な依頼はユーザーに確認してから作成する。

---

## ステップ 2 — イシュー本文テンプレート

本文末尾には **ワーカー用スキルデータ** を必ず付与する（スキル未インストールのエージェントへの手順ガイド）。

```markdown
## 目的

{目的を 1〜3 文で記述}

## 実装スコープ

- {変更点 1}
- {変更点 2}

## 受け入れ条件

- [ ] {条件 1}
- [ ] {条件 2}
- [ ] {条件 3}

## 技術制約

{制約があれば記載。なければ「特になし」}

## 参考情報

{関連イシュー・ドキュメント・スクリーンショットなど}

<!-- gitlab-idd: version=4.0.0 role=worker -->
<details>
<summary>🤖 Worker Instructions (gitlab-idd v4.0.0)</summary>

このイシューは `gitlab-idd` スキルのワーカーロールで処理します。
スキル未インストールの場合は以下の手順に従ってください。

**前提**: `GITLAB_TOKEN` 環境変数を設定し、`scripts/gl.py` が使用可能なこと。

```bash
GL="python scripts/gl.py"
ISSUE_ID={このイシューの番号}   # URL の末尾の数字
```

### Step 1 — 着手ロック

```bash
MY_USER=$($GL current-user --get username)
$GL update-issue $ISSUE_ID \
  --assignee "$MY_USER" \
  --add-labels "status:in-progress" \
  --remove-labels "status:open,assignee:any"

# 競合確認（別ノードが先取得した場合はスキップ）
ASSIGNED=$($GL get-issue $ISSUE_ID --get assignees.0.username)
[ "$ASSIGNED" = "$MY_USER" ] || { echo "競合: 別ノードが先取得"; exit 1; }
```

### Step 2 — ブランチ作成

```bash
BRANCH=$($GL make-branch-name $ISSUE_ID)
git fetch origin main
git checkout -b "$BRANCH" origin/main
$GL add-comment $ISSUE_ID --body "🚀 着手: ノード \`$(hostname)\` がブランチ \`${BRANCH}\` で作業開始"
```

### Step 3 — 実装

上記「受け入れ条件」をすべて満たす実装を行う。
機能・セキュリティ・アーキテクチャの 3 観点でレビューし、問題がなければコミット。

```bash
git add -A && git commit -m "feat: {概要} (issue #$ISSUE_ID)"
```

### Step 4 — 提出

```bash
git push -u origin "$BRANCH"

ISSUE_TITLE=$($GL get-issue $ISSUE_ID --get title)
$GL create-mr \
  --title "$ISSUE_TITLE" \
  --source-branch "$BRANCH" \
  --target-branch main \
  --description "Closes #$ISSUE_ID" \
  --draft

MR_URL=$($GL list-mrs --source-branch "$BRANCH" --get 0.web_url)
$GL add-comment $ISSUE_ID --body "## ✅ 実装完了 — レビュー依頼
**ブランチ**: \`${BRANCH}\`
**MR**: ${MR_URL}"

$GL update-issue $ISSUE_ID \
  --add-labels "status:review-ready" \
  --remove-labels "status:in-progress"
```

</details>
```

---

## ステップ 3 — イシュー作成

```bash
BODY=$(cat << 'EOF'
{上記テンプレートを埋めた内容}
EOF
)

$GL create-issue \
  --title "{タイトル}" \
  --body "$BODY" \
  --labels "status:open,assignee:any,priority:{high|normal|low}"
```

---

## ステップ 4 — 完了報告

作成されたイシューの URL をユーザーに報告して終了する。

```
✅ イシュー #{id} を作成しました。
URL: {issue_url}
ワーカーがプロンプトトリガーで拾って実行します。
レビュー時は「イシューをレビューして」と声をかけてください。
```
