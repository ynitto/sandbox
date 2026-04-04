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

> **注**: 環境によって `python` を `python3` や `py` に読み替える。

---

## 全体フロー

```
Phase 1  環境確認 ─── プロジェクト情報・認証確認
   │
Phase 2  イシュー取得 ─── オープンイシューをフィルタして 1 件選択
   │         ├── self-defer チェック: 自分発行イシューは猶予期間中はスキップ
   │         ├── 猶予期間経過後は自分発行イシューも実行可
   │         └── 依存チェック: 依存イシューが未完了ならスキップ
   │
Phase 3  イシュー着手 ─── assign + ラベル更新 + ブランチ作成 + 空ドラフト MR 作成
   │
Phase 4  タスク実行 ─── 並列評価ループ（最大 5 回）
   │         └── 実装 → 自己評価（self-checking、任意）→ 多角レビュー（機能・セキュリティ・アーキテクチャ 並列）→ 修正・コミット・push
   │
Phase 5  成果物提出 ─── push 確認 + MR 本文記入 + ドラフト解除 + イシューコメント + ラベル更新
```

---

## Phase 1 — 環境確認

```
python scripts/gl.py project-info
python scripts/gl.py current-user
```

`project-info` が失敗する場合: カレントディレクトリが git リポジトリ内にあるか確認する。
`current-user` が失敗する場合: GITLAB_TOKEN 環境変数が設定されているか確認して終了する。

---

## Phase 2 — イシュー取得

### ステップ 2-1: 候補イシューの一覧取得

自分のユーザー名を取得する:

```
python scripts/gl.py current-user --get username
```

次の順で候補イシューを取得する:

```
# 1. 自分に assign されたオープンイシューを優先取得
python scripts/gl.py list-issues --label "status:open" --assignee MY_USER

# 2. 誰でも引き受け可のイシューを取得
python scripts/gl.py list-issues --label "status:open,assignee:any"

# 3. 差し戻し済みで自分担当のものも対象
python scripts/gl.py list-issues --label "status:needs-rework" --assignee MY_USER
```

### ステップ 2-2: self-defer チェック（自分発行イシューの猶予）

他ノードに実行させるため自分が発行したイシューには猶予期間を設ける。
猶予期間は `GITLAB_SELF_DEFER_MINUTES` 環境変数（デフォルト 60 分）。

各候補イシューに対して以下を実行する:

```
python scripts/gl.py check-defer {issue_id} --get defer
# → True（スキップ）または False（取得可）

python scripts/gl.py check-defer {issue_id} --get remaining_minutes
# → スキップ時の残り猶予分数
```

`defer` が `True` の場合はそのイシューをスキップして次の候補へ進む。

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

### ステップ 2-4: 依存イシューチェック

選択したイシューの本文に `## 依存イシュー` セクションがある場合、記載されているイシューが **すべて完了済み**（`status:done` またはクローズ状態）であることを確認する。

```
# イシュー本文から依存イシュー番号を読み取り、各イシューのステータスを確認する
python scripts/gl.py get-issue {dep_issue_id} --get state
# → "closed" であれば完了。"opened" の場合はブロック

python scripts/gl.py get-issue {dep_issue_id} --get labels
# → "status:done" を含むか確認（クローズ済みでも labels で二重確認）
```

依存イシューが未完了（`state=opened` かつ `status:done` ラベルなし）の場合:

```
# 着手不可。次の候補イシューへ進む。
# 全候補が依存ブロック中の場合はコメントを投稿して終了:
python scripts/gl.py add-comment {issue_id} \
  --body "⏳ 依存イシュー #{dep_issue_id} が未完了のため着手を保留します。完了後に再実行してください。"
```

依存イシューが **すべて完了**している場合はそのまま Phase 3 へ進む。

### ステップ 2-5: イシュー詳細の読み込み

```
python scripts/gl.py get-issue {issue_id}
python scripts/gl.py get-comments {issue_id}
```

`## 受け入れ条件` セクションを必ず確認し、全項目を把握してから Phase 3 へ進む。

---

## Phase 3 — イシュー着手

### ステップ 3-1: 自分に assign してロック

MY_USER は `python scripts/gl.py current-user --get username` で取得した値を使う。

```
python scripts/gl.py update-issue {issue_id} \
  --assignee MY_USER \
  --add-labels "status:in-progress" \
  --remove-labels "status:open,assignee:any,status:needs-rework"
```

assign 直後に再取得して自分が assignee になっていることを確認する（競合防止）:

```
python scripts/gl.py get-issue {issue_id} --get assignees.0.username
# → MY_USER であることを確認。別のユーザーなら「競合: 別ノードが先に取得しました」として終了する。
```

### ステップ 3-2: 作業ブランチ作成

イシュー本文の `## ターゲットブランチ` セクションからターゲットブランチを読み取る。セクションがない場合は `main` を使用する（TARGET_BRANCH とする）。

```
python scripts/gl.py make-branch-name {issue_id}
# → "feature/issue-42-add-login-form" のようなブランチ名が出力される

git fetch origin TARGET_BRANCH
git checkout -b BRANCH origin/TARGET_BRANCH
```

### ステップ 3-3: 着手コメント投稿

```
python scripts/gl.py add-comment {issue_id} --body "🚀 **作業開始**: 着手しました。ブランチ: BRANCH"
```

### ステップ 3-4: 初回 push と空ドラフト MR 作成

ブランチを push してから空本文のドラフト MR を作成する:

```
git push -u origin BRANCH

python scripts/gl.py get-issue {issue_id} --get title

python scripts/gl.py create-mr \
  --title "ISSUE_TITLE" \
  --source-branch BRANCH \
  --target-branch TARGET_BRANCH \
  --description "" \
  --draft
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

### ステップ 4-2: 自己評価（self-checking スキルが利用可能な場合）

実装完了後・多角レビューの前に、自身で成果物を評価・改善する。

```bash
# self-checking スキルの存在確認
ls ${SKILLS_DIR}/self-checking/SKILL.md 2>/dev/null
```

- **ファイルが存在する** → `${SKILLS_DIR}/self-checking/SKILL.md` を読んで手順に従い、実装成果物を自己評価・改善する:
  - 種別: `code`（コード変更の場合）
  - 成果物: `git diff TARGET_BRANCH...BRANCH` で変更されたファイル
  - 完了基準: イシューの `## 受け入れ条件` の各項目
  - PASS になるまで改善を繰り返す（連続 2 回改善幅が小さい場合は収束とみなす）
- **ファイルが存在しない** → self-checking スキルなしで自律的に自己評価を実施する:
  - 受け入れ条件・実装内容をもとに、品質を自身で判断する
  - （観点: 受け入れ条件の充足・明らかな誤り・抜け漏れ・エッジケース）
  - 問題が見つかれば修正し、問題がなくなるまで繰り返す

### ステップ 4-3: 多角レビュー（並列サブエージェント）

実装・自己評価完了後、以下の 3 観点を **並列で** レビューする:

| エージェント | 観点 |
|------------|------|
| 機能レビュー | 受け入れ条件チェックリストの全項目検証・エッジケース |
| セキュリティレビュー | OWASP Top 10・認証・入力検証・機密情報漏洩 |
| アーキテクチャレビュー | SOLID・依存方向・既存設計との一貫性 |

各エージェントへの入力: イシュー本文（受け入れ条件含む）+ `git diff TARGET_BRANCH...BRANCH`

### ステップ 4-4: 指摘統合と修正判断

```
全レビュー結果を統合:
  - 指摘なし or 軽微のみ → Phase 5 へ進む
  - 修正必要な指摘あり   → 修正サブエージェントに委譲 → ステップ 4-3 に戻る（最大 5 回）
  - 5 回を超えた場合    → 現状の実装で Phase 5 へ進み、未解決指摘をコメントに記載
```

### ステップ 4-5: 変更のコミットと push

```
git add -A
git commit -m "feat: {受け入れ条件の要約} (issue #{issue_id})"
git push origin BRANCH
```

---

## Phase 5 — 成果物提出

### ステップ 5-1: 最終 push の確認

未 push のコミットがないことを確認する:

```
git log origin/BRANCH..HEAD
```

出力が空であれば push 済み。コミットが残っている場合は push する:

```
git push origin BRANCH
```

### ステップ 5-2: MR 本文を書いてドラフト解除

MR の iid を取得する:

```
python scripts/gl.py list-mrs --source-branch BRANCH --get 0.iid
```

MR 本文を `_mr_body.md` に書いてから更新・ドラフト解除する:

```
python scripts/gl.py update-mr MR_IID \
  --description-file _mr_body.md \
  --no-draft
```

`_mr_body.md` の内容:

```markdown
## 関連イシュー

Closes #{issue_id}

## 変更サマリー

{実装の概要を箇条書き}

## レビューポイント

{リクエスターに特に確認してほしい箇所}

## テスト結果

{実行したテストと結果。未解決の指摘があれば記載}
```

### ステップ 5-3: イシューにサマリーコメント投稿

MR の URL を取得する:

```
python scripts/gl.py list-mrs --source-branch BRANCH --get 0.web_url
```

コメントを `_comment.md` に書いて投稿する:

```
python scripts/gl.py add-comment {issue_id} --body-file _comment.md
```

`_comment.md` の内容:

```markdown
## ✅ 実装完了 — レビュー依頼

**ブランチ**: `BRANCH`
**MR**: MR_URL

### 受け入れ条件の対応状況

- [x] {条件 1} → {どう対応したか}
- [x] {条件 2} → {どう対応したか}

### リクエスターへの申し送り

{未解決の指摘・確認してほしい事項があれば記載}
```

### ステップ 5-4: ラベル更新

```
python scripts/gl.py update-issue {issue_id} \
  --add-labels "status:review-ready" \
  --remove-labels "status:in-progress"
```

### ステップ 5-5: 完了報告

```
✅ イシュー #{id} の実装が完了しました。
ブランチ: BRANCH
MR: MR_URL
レビュー待ち状態に更新しました。
```

---

## ワーカーの行動原則

1. **1 イシュー = 1 ブランチ = 1 MR**: 複数イシューの変更を 1 フィーチャーブランチに混在させない。ただし複数のフィーチャーブランチが同じ統合ブランチ（TARGET_BRANCH）を MR のターゲットにすることは許容される
2. **スコープ厳守**: イシューで定義された範囲外の変更を含めない
3. **受け入れ条件を読む**: 実装前に必ず受け入れ条件を確認し、全項目をカバーする
4. **レビューを通す**: 並列レビューを必ず実施する。自己判断でスキップしない
5. **コメントで証跡を残す**: リクエスターが判断できるよう、何をどう実装したかをコメントに記載する
6. **self-defer を守る**: 自分発行イシューは猶予期間中は取得しない。他ノードへの委譲を尊重する
7. **依存を守る**: `## 依存イシュー` に記載されたイシューがすべて完了するまで着手しない
