# ワーカーロール詳細手順

ワーカーノードはプロンプトトリガーで起動し、オープンイシューを取得して実行・報告する。ポーリングは行わない。

すべての GitLab API 操作は `scripts/gl.py` を Python で実行する（`glab` CLI 不要）。

```
# スクリプトのショートハンド（以降の手順でも同じパスを使う）
GL="python .github/skills/gitlab-issue-driven-dev/scripts/gl.py"
```

---

## 全体フロー

```
Phase 1  環境確認 ─── プロジェクト情報・認証確認
   │
Phase 2  イシュー取得 ─── オープンイシューをフィルタして 1 件選択
   │         ├── self-defer チェック: 自分発行イシューは猶予期間中はスキップ
   │         └── 猶予期間経過後は自分発行イシューも実行可
   │
Phase 3  イシュー着手 ─── assign + ラベル更新 + ブランチ作成
   │
Phase 4  タスク実行 ─── 並列評価ループ（最大 5 回）
   │         └── 実装 → 多角レビュー（機能・セキュリティ・アーキテクチャ 並列）→ 修正
   │
Phase 5  成果物提出 ─── push + MR 作成 + イシューコメント + ラベル更新
```

---

## Phase 1 — 環境確認

```bash
GL="python .github/skills/gitlab-issue-driven-dev/scripts/gl.py"

# プロジェクト情報（ホスト名・プロジェクトパスを git remote から自動取得）
$GL project-info

# 認証・ユーザー確認
$GL current-user
```

`project-info` が失敗する場合:
- カレントディレクトリが git リポジトリ内か確認
- `git remote get-url origin` の URL が GitLab を指しているか確認

`current-user` が失敗する場合:
- `export GITLAB_TOKEN=glpat-xxxxxxxxxxxx` を案内して終了

---

## Phase 2 — イシュー取得

### ステップ 2-1: 候補イシューの一覧取得

```bash
GL="python .github/skills/gitlab-issue-driven-dev/scripts/gl.py"

# 1. 自分に assign されたオープンイシューを優先取得
MY_USER=$($GL current-user | python -c "import sys,json; print(json.load(sys.stdin)['username'])")

$GL list-issues --label "status:open" --assignee "$MY_USER" --state opened

# 2. 誰でも引き受け可のイシューを取得（assignee:any ラベル付き）
$GL list-issues --label "status:open,assignee:any" --state opened

# 3. needs-rework（差し戻し）のうち自分担当のものも対象
$GL list-issues --label "status:needs-rework" --assignee "$MY_USER" --state opened
```

### ステップ 2-2: self-defer チェック（自分発行イシューの猶予）

他ノードに実行させるため自分が発行したイシューには猶予期間を設ける。
猶予期間は `GITLAB_SELF_DEFER_MINUTES`（デフォルト 60 分）。

各候補イシューについて以下を実行する:

```bash
DEFER_MINUTES=${GITLAB_SELF_DEFER_MINUTES:-60}

# check-defer: defer=true なら猶予中 → スキップ、false なら取得可
$GL check-defer {issue_id} --minutes "$DEFER_MINUTES"
```

返却 JSON の例:
```json
// 猶予中 → スキップ
{"defer": true, "reason": "self_created_too_recent",
 "age_minutes": 12.3, "defer_minutes": 60, "remaining_minutes": 47}

// 猶予切れ or 他者発行 → 取得可
{"defer": false, "reason": "not_my_issue", "author": "alice", "me": "bob"}
{"defer": false, "reason": "self_created_but_expired",
 "age_minutes": 75.1, "defer_minutes": 60}
```

### ステップ 2-3: イシュー選択

優先順位:
1. `status:needs-rework` かつ自分 assign のもの（差し戻し再作業）
2. `status:open` かつ自分 assign のもの
3. `status:open,assignee:any` のもの

各候補に対して self-defer チェックを行い、`defer=false` の先頭 1 件を選択する。

全候補が `defer=true` の場合:
```
「実行可能なイシューはありません。
自分が発行したイシュー #{id} は残り {remaining_minutes} 分後に実行可能になります。」
と報告して終了する。
```

オープンイシューが 0 件の場合:
```
「実行可能なオープンイシューはありません。」と報告して終了する。
```

### ステップ 2-4: イシュー詳細の読み込み

```bash
$GL get-issue {issue_id}
$GL get-comments {issue_id}    # 差し戻し時は特に重要
```

`## 受け入れ条件` セクションを必ず確認し、全項目を把握してから Phase 3 へ進む。

---

## Phase 3 — イシュー着手

### ステップ 3-1: 自分に assign してロック

```bash
# assign + ラベル更新（in-progress にして競合を防ぐ）
$GL update-issue {issue_id} \
  --assignee "$MY_USER" \
  --add-labels "status:in-progress" \
  --remove-labels "status:open,assignee:any,status:needs-rework"
```

assign 直後に再取得して自分が assignee になっていることを確認する:

```bash
ASSIGNED=$($GL get-issue {issue_id} | python -c \
  "import sys,json; d=json.load(sys.stdin); print(d['assignees'][0]['username'] if d.get('assignees') else '')")

if [ "$ASSIGNED" != "$MY_USER" ]; then
  echo "別ノードが先に取得しました。次のイシューを探します。"
  # Phase 2 に戻る
fi
```

### ステップ 3-2: 作業ブランチ作成

```bash
# イシュータイトルからスラグを生成（Python で行う）
SLUG=$($GL get-issue {issue_id} | python -c "
import sys, json, re
title = json.load(sys.stdin)['title'].lower()
slug = re.sub(r'[^a-z0-9]+', '-', title).strip('-')[:40]
print(slug)
")

BRANCH="feature/issue-{issue_id}-${SLUG}"

git fetch origin main
git checkout -b "$BRANCH" origin/main
```

### ステップ 3-3: 着手コメント投稿

```bash
HOSTNAME=$(hostname)

$GL add-comment {issue_id} \
  --body "🚀 **作業開始**: ノード \`${HOSTNAME}\` が着手しました。ブランチ: \`${BRANCH}\`"
```

---

## Phase 4 — タスク実行（並列評価ループ）

### 概要

**⚠️ 実装・レビューは必ずサブエージェントに委譲すること。自分で実装してはならない。**

実装 → 多角レビュー → 修正のサイクルを最大 5 回繰り返す。

### ステップ 4-1: タスク実装（サブエージェント委譲）

実装サブエージェントへの指示:

```
以下を渡して実装を依頼する:
- イシュー本文（受け入れ条件・技術制約を含む）
- 差し戻しコメント（needs-rework の場合）
- 対象ブランチ名

依頼内容:
- 受け入れ条件を全て満たすコードを作成・変更する
- 実装スコープ外の変更を含めない
- 変更内容のサマリーを出力する
```

### ステップ 4-2: 多角レビュー（並列サブエージェント）

実装完了後、以下の 3 観点を **並列で** レビューする:

| エージェント | 観点 |
|------------|------|
| 機能レビュー | 受け入れ条件チェックリストの全項目検証・エッジケース |
| セキュリティレビュー | OWASP Top 10・認証・入力検証・機密情報漏洩 |
| アーキテクチャレビュー | SOLID・依存方向・既存設計との一貫性 |

各エージェントへの入力:
- イシュー本文（受け入れ条件含む）
- ブランチの diff（`git diff main..{BRANCH}`）

### ステップ 4-3: 指摘統合と修正判断

```
全レビュー結果を統合:
  - 指摘なし or 軽微のみ → Phase 5 へ進む
  - 修正必要な指摘あり   → 修正サブエージェントに委譲 → ステップ 4-2 に戻る（最大 5 回）
  - 5 回を超えた場合    → 現状の実装で Phase 5 へ進み、未解決指摘をコメントに記載
```

### ステップ 4-4: 変更のコミット

```bash
git add -A
git commit -m "feat: {受け入れ条件の要約} (issue #{issue_id})"
```

---

## Phase 5 — 成果物提出

### ステップ 5-1: ブランチを push

```bash
git push -u origin "$BRANCH"
```

### ステップ 5-2: MR（ドラフト）作成

```bash
ISSUE_TITLE=$($GL get-issue {issue_id} | python -c \
  "import sys,json; print(json.load(sys.stdin)['title'])")

MR_BODY=$(cat << 'MRBODY'
## 関連イシュー

Closes #{issue_id}

## 変更サマリー

{実装の概要を箇条書き}

## レビューポイント

{レビュアーに特に確認してほしい箇所}

## テスト結果

{実行したテストと結果。未解決の指摘があれば記載}
MRBODY
)

$GL create-mr \
  --title "$ISSUE_TITLE" \
  --source-branch "$BRANCH" \
  --target-branch main \
  --description "$MR_BODY" \
  --draft
```

### ステップ 5-3: イシューにサマリーコメント投稿

```bash
MR_URL=$($GL list-mrs --source-branch "$BRANCH" | python -c \
  "import sys,json; mrs=json.load(sys.stdin); print(mrs[0]['web_url'] if mrs else 'N/A')")

$GL add-comment {issue_id} --body "$(cat << 'COMMENT'
## ✅ 実装完了 — レビュー依頼

**ブランチ**: \`{BRANCH}\`
**MR**: {MR_URL}

### 受け入れ条件の対応状況

- [x] {条件 1} → {どう対応したか}
- [x] {条件 2} → {どう対応したか}

### 注意事項

{未解決の指摘・レビュアーへの申し送りがあれば記載}
COMMENT
)"
```

### ステップ 5-4: ラベル更新

```bash
$GL update-issue {issue_id} \
  --add-labels "status:review-ready" \
  --remove-labels "status:in-progress"
```

### ステップ 5-5: 完了報告

```
✅ イシュー #{id} の実装が完了しました。
ブランチ: {BRANCH}
MR: {MR_URL}
レビュー待ち状態に更新しました。
```

---

## 競合防止（assign ロック）

`update-issue --assignee` の後、すぐに `get-issue` で自分が assignees に入っているか確認する。
GitLab はアトミックな排他ロックを提供しないため、このダブルチェックで競合を検知する。

```bash
ASSIGNED=$($GL get-issue {issue_id} | python -c \
  "import sys,json
d=json.load(sys.stdin)
assignees=[a['username'] for a in d.get('assignees',[])]
print(assignees[0] if assignees else '')")

[ "$ASSIGNED" = "$MY_USER" ] || { echo "競合: 別ノードが先に取得しました"; exit 1; }
```

---

## ワーカーの行動原則

1. **1 イシュー = 1 ブランチ = 1 MR**: 複数イシューを 1 ブランチに混在させない
2. **スコープ厳守**: イシューで定義された範囲外の変更を含めない
3. **受け入れ条件を読む**: 実装前に必ず受け入れ条件を確認し、全項目をカバーする
4. **レビューを通す**: 並列レビューを必ず実施する。自己判断でスキップしない
5. **コメントで証跡を残す**: レビュアーが判断できるよう、何をどう実装したかをコメントに記載する
6. **self-defer を守る**: 自分発行イシューは猶予期間中は取得しない。他ノードへの委譲を尊重する
