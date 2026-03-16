# リクエスターロール詳細手順

リクエスターノードはタスクをイシューとして投稿し、ワーカーの成果物を評価してマージまたはリオープンする。
外部スキルへの依存はない。すべての操作は `scripts/gl.py` を Python で実行する。

```bash
# ショートハンド定義
GL="python .github/skills/gitlab-issue-driven-dev/scripts/gl.py"
```

---

## 操作 A: イシュー投稿（タスク委譲）

### ステップ 1 — タスク整理

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

### ステップ 2 — イシュー本文テンプレート

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
```

### ステップ 3 — イシュー作成

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

### ステップ 4 — 完了報告

作成されたイシューの URL をユーザーに報告して終了する。

```
✅ イシュー #{id} を作成しました。
URL: {issue_url}
ワーカーがプロンプトトリガーで拾って実行します。
レビュー時は「イシューをレビューして」と声をかけてください。
```

---

## 操作 B: レビュー・クローズ / リオープン

ワーカーが `status:review-ready` に更新したイシューを評価する。

### ステップ 1 — レビュー対象イシューを取得

```bash
$GL list-issues --label "status:review-ready"
```

レビュー対象が 0 件の場合は「レビュー待ちイシューはありません」と報告して終了。
複数件ある場合は優先度順（`priority:high` → `normal` → `low`）に処理する。

### ステップ 2 — 成果物の確認

各イシューについて以下を確認する:

```bash
# イシュー詳細
$GL get-issue {issue_id}

# ワーカーのサマリーコメントを確認
$GL get-comments {issue_id}

# ワーカーが作成したブランチの diff
git fetch origin
git diff main...origin/feature/issue-{issue_id}-*

# MR 情報確認
$GL list-mrs --source-branch "feature/issue-{issue_id}"
```

### ステップ 3 — 受け入れ条件の並列評価

**⚠️ 必ずサブエージェントに委譲すること。自分で評価してはならない。**

以下の 3 観点を **並列** サブエージェントで評価する:

| エージェント | 観点 |
|------------|------|
| 機能要件エージェント | 受け入れ条件チェックリストの全項目検証 |
| セキュリティエージェント | OWASP Top 10 視点でコード変更を確認 |
| アーキテクチャエージェント | 設計の一貫性・依存方向・責任分割を確認 |

各エージェントへの入力:
- イシュー本文（受け入れ条件含む）
- ブランチの diff（`git diff main..feature/issue-{id}*`）
- ワーカーのサマリーコメント

評価結果を統合し、全条件を満たしているか判定する。

### ステップ 4a — 条件充足: クローズ & マージ

```bash
# MR ID を取得
MR_ID=$($GL list-mrs --source-branch "feature/issue-{issue_id}" | python -c \
  "import sys,json; print(json.load(sys.stdin)[0]['iid'])")

# MR を squash マージ
$GL merge-mr "$MR_ID" --squash --remove-source-branch

# イシューをクローズしてラベル更新
$GL update-issue {issue_id} \
  --add-labels "status:done" \
  --remove-labels "status:review-ready" \
  --state-event close

# 承認コメント投稿
$GL add-comment {issue_id} \
  --body "✅ 受け入れ条件をすべて満たしています。マージしてクローズしました。"
```

**完了報告**:
```
✅ イシュー #{id} をクローズしました。
MR #{mr_id} をマージ済みです。
```

### ステップ 4b — 条件不足: リオープン

```bash
# 差し戻しコメントを投稿
COMMENT=$(cat << 'EOF'
## ❌ 差し戻し

以下の受け入れ条件が未充足です。修正後に再度 `status:review-ready` に更新してください。

### 未充足項目

- {未充足の条件 1}
- {未充足の条件 2}

### 具体的な指摘

{修正が必要な箇所・理由を詳しく記述}
EOF
)
$GL add-comment {issue_id} --body "$COMMENT"

# リオープンしてラベル更新
$GL update-issue {issue_id} \
  --add-labels "status:needs-rework" \
  --remove-labels "status:review-ready" \
  --state-event reopen
```

**完了報告**:
```
🔁 イシュー #{id} をリオープンしました。
差し戻し理由をコメントに記載済みです。
ワーカーが再作業後に「status:review-ready」に更新します。
```

---

## リクエスターの判定基準

| 評価項目 | 合格条件 |
|---------|---------|
| 機能要件 | 受け入れ条件チェックリスト全項目にチェックが入る |
| テスト | ユニットテストが存在し、CI が通過している（CI 設定がある場合） |
| セキュリティ | 重大度 High 以上の脆弱性がない |
| アーキテクチャ | 既存の設計方針と一貫している |
| 実装スコープ | イシューで定義した範囲外の変更を含まない |

1 項目でも不合格の場合はリオープン。

---

## 注意事項

- リクエスターは実装を行わない。タスク定義と評価・判定のみ
- MR が存在しない場合はワーカーに確認コメントを投稿してから評価を保留
- 複数 MR が存在する場合は最新のものを使用
