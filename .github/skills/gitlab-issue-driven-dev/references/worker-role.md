# ワーカーロール詳細手順

ワーカーノードはプロンプトトリガーで起動し、オープンイシューを取得して実行・報告する。ポーリングは行わない。

---

## 全体フロー

```
Phase 1  イシュー取得 ─── オープンイシューを取得して選択
   │
Phase 2  イシュー着手 ─── assign + ラベル更新 + ブランチ作成
   │
Phase 3  タスク実行 ─── scrum-master スタイルの並列評価ループ
   │         └── 実装 → 多角レビュー → 修正 → 最大 5 回
   │
Phase 4  成果物提出 ─── push + MR 作成 + イシューコメント + ラベル更新
```

---

## Phase 1 — イシュー取得

### ステップ 1-1: オープンイシューの一覧取得

```bash
# 自分に assign されたイシューを優先取得
glab issue list \
  --label "status:open" \
  --assignee "$(glab api user | jq -r '.username')" \
  --repo "$GITLAB_PROJECT" \
  --output json

# 次に "assignee:any"（誰でも引き受け可）のイシューを取得
glab issue list \
  --label "status:open,assignee:any" \
  --repo "$GITLAB_PROJECT" \
  --output json
```

### ステップ 1-2: イシュー選択

- オープンイシューが 0 件: 「実行可能なイシューはありません」と報告して終了
- 1 件: 即座に着手
- 複数件: `priority:high` → `priority:normal` → `priority:low` → 作成日の昇順で先頭 1 件を選択

### ステップ 1-3: イシュー詳細の読み込み

```bash
glab issue view {issue_id} --repo "$GITLAB_PROJECT"
```

受け入れ条件・技術制約・参考情報をすべて読み込む。

---

## Phase 2 — イシュー着手

### ステップ 2-1: 自分に assign してロック

```bash
MY_USER=$(glab api user | jq -r '.username')

glab issue update {issue_id} \
  --assignee "$MY_USER" \
  --label "status:in-progress" \
  --remove-label "status:open,assignee:any" \
  --repo "$GITLAB_PROJECT"
```

assign 後に他ノードが同じイシューを取得しても assign 済みと表示されるため競合を防ぐ。

### ステップ 2-2: 作業ブランチ作成

```bash
ISSUE_ID={issue_id}
SLUG=$(glab issue view "$ISSUE_ID" --repo "$GITLAB_PROJECT" --output json \
  | jq -r '.title' \
  | tr '[:upper:]' '[:lower:]' \
  | sed 's/[^a-z0-9]/-/g' \
  | sed 's/--*/-/g' \
  | cut -c1-40)

BRANCH="feature/issue-${ISSUE_ID}-${SLUG}"

git fetch origin main
git checkout -b "$BRANCH" origin/main
```

### ステップ 2-3: 着手コメント投稿

```bash
HOSTNAME=$(hostname)

glab issue note {issue_id} \
  --body "🚀 **作業開始**: ノード \`${HOSTNAME}\` が着手しました。ブランチ: \`${BRANCH}\`" \
  --repo "$GITLAB_PROJECT"
```

---

## Phase 3 — タスク実行（並列評価ループ）

### 概要

**⚠️ 実装・レビューは必ずサブエージェントに委譲すること。自分で実装してはならない。**

scrum-master の Phase 5〜6 に相当する並列評価ループを自律実行する。最大 5 回のレビュー→修正サイクルを回す。

### ステップ 3-1: タスク実装（サブエージェント委譲）

実装サブエージェントへの指示:

```
イシュー本文（受け入れ条件含む）と技術制約を渡し、以下を依頼:
- 実装スコープ内のコードを作成・変更する
- 受け入れ条件を全て満たすよう実装する
- 変更内容のサマリーを出力する
```

### ステップ 3-2: 多角レビュー（並列サブエージェント）

実装完了後、以下の 3 観点を **並列で** レビューする:

| エージェント | 観点 | 参照スキル |
|------------|------|----------|
| 機能レビュー | 受け入れ条件チェックリストの検証・エッジケース | code-reviewer |
| セキュリティレビュー | OWASP Top 10・認証・入力検証・機密情報漏洩 | security-reviewer |
| アーキテクチャレビュー | SOLID・依存方向・既存設計との一貫性 | architecture-reviewer |

各スキルのSKILL.mdパス: `${SKILLS_DIR}/{skill-name}/SKILL.md`

### ステップ 3-3: 指摘統合と修正判断

```
全レビュー結果を統合:
  - 指摘なし or 軽微のみ → Phase 4 へ進む
  - 修正必要な指摘あり   → 修正サブエージェントに委譲 → ステップ 3-2 に戻る
  - 5 回を超えた場合    → 現状の実装で Phase 4 へ進み、未解決指摘をコメントに記載
```

### ステップ 3-4: 変更のコミット

各修正後にコミットする:

```bash
git add -A
git commit -m "feat: {受け入れ条件の要約} (issue #{issue_id})"
```

---

## Phase 4 — 成果物提出

### ステップ 4-1: ブランチを push

```bash
git push -u origin "$BRANCH"
```

### ステップ 4-2: MR（ドラフト）作成

```bash
glab mr create \
  --title "Draft: {イシュータイトル}" \
  --description "$(cat << 'EOF'
## 関連イシュー

Closes #{issue_id}

## 変更サマリー

{実装の概要を箇条書き}

## レビューポイント

{SM に特に確認してほしい箇所}

## テスト結果

{実行したテストと結果}
EOF
)" \
  --source-branch "$BRANCH" \
  --target-branch main \
  --draft \
  --repo "$GITLAB_PROJECT"
```

### ステップ 4-3: イシューにサマリーコメント投稿

```bash
MR_URL=$(glab mr list \
  --source-branch "$BRANCH" \
  --repo "$GITLAB_PROJECT" \
  --output json | jq -r '.[0].web_url')

glab issue note {issue_id} \
  --body "$(cat << 'EOF'
## ✅ 実装完了 — レビュー依頼

**ブランチ**: \`{BRANCH}\`
**MR**: {MR_URL}

### 実装サマリー

{受け入れ条件ごとの対応内容}

- [x] {条件 1} → {どう対応したか}
- [x] {条件 2} → {どう対応したか}

### レビュー時の注意点

{SM に伝えたいこと・未解決事項があれば記載}
EOF
)" \
  --repo "$GITLAB_PROJECT"
```

### ステップ 4-4: ラベル更新

```bash
glab issue update {issue_id} \
  --label "status:review-ready" \
  --remove-label "status:in-progress" \
  --repo "$GITLAB_PROJECT"
```

### ステップ 4-5: 完了報告

```
✅ イシュー #{id} の実装が完了しました。
ブランチ: {BRANCH}
MR: {MR_URL}
スクラムマスターへのレビュー依頼を投稿しました。
```

---

## 競合防止メカニズム

### assign によるロック

イシューへの assign は GitLab のアトミック操作として扱う。assign 後に `glab issue view` で自分が assignee であることを確認する:

```bash
ASSIGNEE=$(glab issue view {issue_id} --repo "$GITLAB_PROJECT" --output json \
  | jq -r '.assignees[0].username')

if [ "$ASSIGNEE" != "$MY_USER" ]; then
  echo "別ノードが先に取得しました。次のイシューを探します。"
  # Phase 1 に戻って次のイシューを取得
fi
```

### `needs-rework` イシューの扱い

SM がリオープンしたイシューは `status:needs-rework` ラベルが付いている。差し戻しコメントを読んで修正内容を把握してから再作業する。

```bash
# needs-rework イシューも取得対象に含める
glab issue list \
  --label "status:needs-rework" \
  --assignee "$MY_USER" \
  --repo "$GITLAB_PROJECT" \
  --output json
```

再作業の場合は既存ブランチを使用し、追加コミットで対応する。

---

## ワーカーの行動原則

1. **1 イシュー = 1 ブランチ = 1 MR**: 複数イシューを 1 ブランチに混在させない
2. **スコープ厳守**: イシューで定義された範囲外の変更を含めない
3. **受け入れ条件を読む**: 実装前に必ず受け入れ条件を確認し、全項目をカバーする
4. **レビューを通す**: 自分の実装を過信せず並列レビューを必ず実施する
5. **コメントで証跡を残す**: SM が成果物を評価できるよう、何をどう実装したかをコメントに記載する
