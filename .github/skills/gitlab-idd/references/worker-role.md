# ワーカーロール詳細手順

## 目次

- [全体フロー](#全体フロー)
- [Phase 1 — 環境確認](#phase-1--環境確認)
- [Phase 2 — イシュー取得](#phase-2--イシュー取得)
- [Phase 3 — イシュー着手](#phase-3--イシュー着手)
- [Phase 4 — タスク実行（並列評価ループ）](#phase-4--タスク実行並列評価ループ)
- [Phase 5 — 成果物提出](#phase-5--成果物提出)
- [ワーカーの行動原則](#ワーカーの行動原則)

ワーカーノードはプロンプトトリガーで起動し、オープンイシューを取得して実行・報告する。ポーリングは行わない。

すべての GitLab API 操作は `scripts/gl.py` を Python で実行する（`glab` CLI 不要）。

```bash
# GL ショートハンド（python コマンドは環境に合わせて python3 や py に読み替える）
GL="python scripts/gl.py"
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
$GL project-info    # ホスト名・プロジェクトパスを git remote から自動取得
$GL current-user    # 認証・ユーザー確認
```

`project-info` が失敗する場合: カレントディレクトリが git リポジトリ内にあるか確認する。
`current-user` が失敗する場合: `export GITLAB_TOKEN=glpat-...` を案内して終了する。

---

## Phase 2 — イシュー取得

### ステップ 2-1: 候補イシューの一覧取得

```bash
MY_USER=$($GL current-user --get username)

# 1. 自分に assign されたオープンイシューを優先取得
$GL list-issues --label "status:open" --assignee "$MY_USER"

# 2. 誰でも引き受け可のイシューを取得
$GL list-issues --label "status:open,assignee:any"

# 3. 差し戻し済みで自分担当のものも対象
$GL list-issues --label "status:needs-rework" --assignee "$MY_USER"
```

### ステップ 2-2: self-defer チェック（自分発行イシューの猶予）

他ノードに実行させるため自分が発行したイシューには猶予期間を設ける。
猶予期間は `GITLAB_SELF_DEFER_MINUTES`（デフォルト 60 分）。

```bash
DEFER_MINUTES=${GITLAB_SELF_DEFER_MINUTES:-60}

if [ "$($GL check-defer {issue_id} --minutes "$DEFER_MINUTES" --get defer)" = "True" ]; then
  REMAINING=$($GL check-defer {issue_id} --minutes "$DEFER_MINUTES" --get remaining_minutes)
  echo "スキップ: 残り ${REMAINING} 分後に実行可能"
  # 次の候補イシューへ進む
fi
```

`check-defer` の判定結果:

| reason | defer | 意味 |
|--------|-------|------|
| `not_my_issue` | false | 他者が作成 → 即取得可 |
| `self_created_too_recent` | true | 自分作成・猶予中 → スキップ |
| `self_created_but_expired` | false | 自分作成・猶予切れ → 取得可 |

### ステップ 2-3: イシュー選択

優先順位:
1. `status:needs-rework` かつ自分 assign のもの（差し戻し再作業）
2. `status:open` かつ自分 assign のもの
3. `status:open,assignee:any` のもの

各候補に対して self-defer チェックを行い、`defer=false` の先頭 1 件を選択する。

全候補が `defer=true` の場合: 「残り {remaining_minutes} 分後に実行可能です」と報告して終了。
オープンイシューが 0 件の場合: 「実行可能なオープンイシューはありません」と報告して終了。

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
$GL update-issue {issue_id} \
  --assignee "$MY_USER" \
  --add-labels "status:in-progress" \
  --remove-labels "status:open,assignee:any,status:needs-rework"
```

assign 直後に再取得して自分が assignee になっていることを確認する（競合防止）:

```bash
ASSIGNED=$($GL get-issue {issue_id} --get assignees.0.username)
[ "$ASSIGNED" = "$MY_USER" ] || { echo "競合: 別ノードが先に取得しました"; exit 1; }
```

### ステップ 3-2: 作業ブランチ作成

```bash
BRANCH=$($GL make-branch-name {issue_id})   # → "feature/issue-42-add-login-form"

git fetch origin main
git checkout -b "$BRANCH" origin/main
```

### ステップ 3-3: 着手コメント投稿

```bash
$GL add-comment {issue_id} \
  --body "🚀 **作業開始**: ノード \`$(hostname)\` が着手しました。ブランチ: \`${BRANCH}\`"
```

---

## Phase 4 — タスク実行（並列評価ループ）

### 概要

**⚠️ 実装・レビューは必ずサブエージェントに委譲すること。自分で実装してはならない。**

実装 → 多角レビュー → 修正のサイクルを最大 5 回繰り返す。

### ステップ 4-1: タスク実装（サブエージェント委譲）

```
イシュー本文（受け入れ条件・技術制約を含む）と差し戻しコメント（あれば）を渡し、以下を依頼:
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

各エージェントへの入力: イシュー本文（受け入れ条件含む）+ `git diff main...{BRANCH}`

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
ISSUE_TITLE=$($GL get-issue {issue_id} --get title)

MR_BODY=$(cat << 'MRBODY'
## 関連イシュー

Closes #{issue_id}

## 変更サマリー

{実装の概要を箇条書き}

## レビューポイント

{リクエスターに特に確認してほしい箇所}

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
MR_URL=$($GL list-mrs --source-branch "$BRANCH" --get 0.web_url)

COMMENT=$(cat << 'COMMENT'
## ✅ 実装完了 — レビュー依頼

**ブランチ**: `{BRANCH}`
**MR**: {MR_URL}

### 受け入れ条件の対応状況

- [x] {条件 1} → {どう対応したか}
- [x] {条件 2} → {どう対応したか}

### リクエスターへの申し送り

{未解決の指摘・確認してほしい事項があれば記載}
COMMENT
)

$GL add-comment {issue_id} --body "$COMMENT"
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

## ワーカーの行動原則

1. **1 イシュー = 1 ブランチ = 1 MR**: 複数イシューを 1 ブランチに混在させない
2. **スコープ厳守**: イシューで定義された範囲外の変更を含めない
3. **受け入れ条件を読む**: 実装前に必ず受け入れ条件を確認し、全項目をカバーする
4. **レビューを通す**: 並列レビューを必ず実施する。自己判断でスキップしない
5. **コメントで証跡を残す**: リクエスターが判断できるよう、何をどう実装したかをコメントに記載する
6. **self-defer を守る**: 自分発行イシューは猶予期間中は取得しない。他ノードへの委譲を尊重する
