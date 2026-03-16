# スクラムマスターロール詳細手順

スクラムマスター（SM）ノードはタスクをイシューとして投稿し、ワーカーの成果物を評価してマージまたはリオープンする。

---

## 操作 A: イシュー投稿（タスク委譲）

### 手順

#### ステップ 1 — タスク整理

イシュー作成前に以下を確定させる:

```
タイトル    : 動詞で始まる短い命令形（例: "ログインフォームを実装する"）
目的        : なぜこのタスクが必要か（1〜3文）
実装スコープ: 何を作るか・変更するか
受け入れ条件: 完了と判断する客観的基準（チェックリスト形式・必須）
技術制約    : 言語・フレームワーク・禁止事項（あれば）
優先度      : high / normal / low
```

曖昧な依頼の場合はユーザーに確認してから作成する。

#### ステップ 2 — イシュー本文テンプレート

```markdown
## 目的

{目的を 1〜3 文で記述}

## 実装スコープ

{何を作るか・変更するか箇条書き}

## 受け入れ条件

- [ ] {条件 1}
- [ ] {条件 2}
- [ ] {条件 3}

## 技術制約

{制約があれば記載。なければ「特になし」}

## 参考情報

{関連イシュー・ドキュメント・スクリーンショットなど}
```

#### ステップ 3 — イシュー作成コマンド

```bash
# 本文をファイルに書いてから作成（マルチライン対応）
cat > /tmp/issue_body.md << 'EOF'
{上記テンプレートを埋めた内容}
EOF

glab issue create \
  --title "{タイトル}" \
  --description "$(cat /tmp/issue_body.md)" \
  --label "status:open,assignee:any,priority:{high|normal|low}" \
  --repo "$GITLAB_PROJECT"
```

#### ステップ 4 — 完了報告

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
glab issue list \
  --label "status:review-ready" \
  --repo "$GITLAB_PROJECT" \
  --output json
```

レビュー対象が 0 件の場合は「レビュー待ちイシューはありません」と報告して終了。

複数件ある場合は優先度順（`priority:high` → `normal` → `low`）に処理する。

### ステップ 2 — 成果物の確認

各イシューについて以下を確認する:

```bash
# イシュー詳細（最新コメント含む）
glab issue view {issue_id} --repo "$GITLAB_PROJECT"

# ワーカーが作成したブランチの確認
git fetch origin feature/issue-{issue_id}-*
git log --oneline origin/feature/issue-{issue_id}-* | head -20

# MR の確認
glab mr list \
  --source-branch "feature/issue-{issue_id}*" \
  --repo "$GITLAB_PROJECT" \
  --output json
```

### ステップ 3 — 受け入れ条件の並列評価

**⚠️ 必ずサブエージェントに委譲すること。自分で評価してはならない。**

受け入れ条件の各項目を並列サブエージェントに評価させる。各サブエージェントには以下を渡す:

```
評価観点:
  - 機能要件エージェント  : 受け入れ条件チェックリストを全項目検証
  - セキュリティエージェント: OWASP Top 10 視点でコード変更を確認
  - アーキテクチャエージェント: 設計の一貫性・依存方向・責任分割を確認

各エージェントへの入力:
  - イシュー本文（受け入れ条件含む）
  - ブランチの diff（`git diff main..feature/issue-{id}*`）
  - ワーカーのサマリーコメント
```

評価結果を統合し、全条件を満たしているか判定する。

### ステップ 4a — 条件充足: クローズ & マージ

```bash
MR_ID=$(glab mr list \
  --source-branch "feature/issue-{issue_id}*" \
  --repo "$GITLAB_PROJECT" \
  --output json | jq -r '.[0].iid')

# MR をマージ
glab mr merge "$MR_ID" \
  --squash \
  --remove-source-branch \
  --repo "$GITLAB_PROJECT"

# イシューをクローズしてラベル更新
glab issue close {issue_id} --repo "$GITLAB_PROJECT"
glab issue update {issue_id} \
  --label "status:done" \
  --remove-label "status:review-ready" \
  --repo "$GITLAB_PROJECT"

# 承認コメント投稿
glab issue note {issue_id} \
  --body "✅ 受け入れ条件をすべて満たしています。マージしてクローズしました。" \
  --repo "$GITLAB_PROJECT"
```

**完了報告**:
```
✅ イシュー #{id} をクローズしました。
MR #{mr_id} をマージ済みです。
```

### ステップ 4b — 条件不足: リオープン

```bash
# 差し戻し理由をコメント投稿
glab issue note {issue_id} \
  --body "$(cat << 'EOF'
## ❌ 差し戻し

以下の受け入れ条件が未充足です。修正後に再度「status:review-ready」に更新してください。

### 未充足項目

{未充足の条件を箇条書き}

### 具体的な指摘

{修正が必要な箇所・理由を詳しく記述}
EOF
)" \
  --repo "$GITLAB_PROJECT"

# イシューをリオープンしてラベル更新
glab issue reopen {issue_id} --repo "$GITLAB_PROJECT"
glab issue update {issue_id} \
  --label "status:needs-rework" \
  --remove-label "status:review-ready" \
  --repo "$GITLAB_PROJECT"
```

**完了報告**:
```
🔁 イシュー #{id} をリオープンしました。
差し戻し理由をコメントに記載済みです。
ワーカーが再作業後に「status:review-ready」に更新します。
```

---

## SM の判定基準

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

- SM は実装を行わない。評価と判定のみ
- MR が存在しない場合はワーカーに確認コメントを投稿してから評価を保留
- 複数 MR が存在する場合は最新のものを使用
