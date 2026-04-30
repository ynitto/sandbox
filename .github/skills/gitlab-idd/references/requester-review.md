# リクエスター — レビュー・クローズ / リオープン手順

## 目次

- [曖昧イシューの詳細化](#曖昧イシューの詳細化)
- [ステップ 0 — MR マージ/クローズ済みイシューのクローズ（クリーンアップ）](#ステップ-0--mr-マージクローズ済みイシューのクローズクリーンアップ)
- [ステップ 1 — レビュー対象イシューを取得・分類](#ステップ-1--レビュー対象イシューを取得分類)
- [ステップ 2 — 成果物の確認](#ステップ-2--成果物の確認)
- [ステップ 3 — 受け入れ条件の評価](#ステップ-3--受け入れ条件の評価)
- [ステップ 4a — 条件充足: マージ依頼](#ステップ-4a--条件充足-マージ依頼)
- [ステップ 4b — 条件不足: リオープン](#ステップ-4b--条件不足-リオープン)
- [判定基準](#判定基準)
- [注意事項](#注意事項)
- [スコープ外タスクのイシュー起票](#スコープ外タスクのイシュー起票)
- [統合ブランチの最終マージ MR 作成](#統合ブランチの最終マージ-mr-作成)

## 曖昧イシューの詳細化

「曖昧なイシューを詳細化して」「イシューを明確化して」などのフレーズで発動。
または「イシューをレビューして」の流れで `status:needs-clarification` イシューが見つかった場合に自動的に処理する。

ワーカーが「説明が不明確」と指摘したイシュー（`status:needs-clarification` ラベル付き）に対して、リクエスターが内容を詳細化してワーカーへ返答する。

### ステップ C-1: 対象イシューの取得

```
python scripts/gl.py list-issues --label "status:needs-clarification"
```

対象が 0 件の場合は「詳細化が必要なイシューはありません」と報告して終了。

### ステップ C-2: ワーカーの指摘内容を確認

各イシューについてコメントを取得し、ワーカーが指摘した不明確な点を把握する:

```
python scripts/gl.py get-issue {issue_id}
python scripts/gl.py get-comments {issue_id}
```

`<!-- gitlab-idd:clarification-requested -->` を含むコメントが対象。
そのコメントの「不明確な点」「確認したい事項」を全て把握する。

### ステップ C-3: イシュー説明を詳細化して更新

ワーカーの指摘事項に答える形で、イシュー本文を詳細化する。
以下の内容が含まれるよう **イシュー本文を更新** する:

- 曖昧だった箇所を具体的に記述
- `## 受け入れ条件` を明確な検証可能な項目として列挙
- 影響範囲（対象ファイル・モジュール・APIなど）を明示（分かる範囲で）
- 技術的な制約・方針があれば記載

```
python scripts/gl.py update-issue {issue_id} --body-file _clarified_body.md
```

`_clarified_body.md` は元のイシュー本文をベースに、不明確だった箇所を補完した内容とする。

### ステップ C-4: 詳細化完了コメントを投稿

```
python scripts/gl.py add-comment {issue_id} --body-file _clarified_comment.md
```

`_clarified_comment.md` の内容:

```markdown
## ✏️ イシューを詳細化しました

ご指摘いただいた点を説明に追記しました。

### 更新した内容

- {更新点 1: 何を明確化したか}
- {更新点 2}

### ワーカーへの補足

{指摘事項への具体的な回答や追加情報}

以上を踏まえて実装をお願いします。
```

### ステップ C-5: ラベルを元に戻してワーカーへ返却

```
python scripts/gl.py update-issue {issue_id} \
  --add-labels "status:open,assignee:any" \
  --remove-labels "status:needs-clarification"
```

**完了報告**:

```
✅ イシュー #{id} の説明を詳細化しました。
不明確な点を補完し、「status:open,assignee:any」に更新しました。
ワーカーが再度「イシューを拾って」で着手できます。
```

---

## レビュー・クローズ / リオープン

`status:review-ready` イシューを評価し、マージ依頼またはリオープンする。
自分が実装したイシューは self-review ロック期間（デフォルト 24 時間）中は self-defer し、経過後は自分でレビューしてよい。
すべての操作は `scripts/gl.py` を Python で実行する（`glab` CLI 不要）。

> **注**: 環境によって `python` を `python3` や `py` に読み替える。
> **マージは必ず人間が行う**: レビュー完了後に自動マージはしない。イシュー作成者にマージを依頼するコメントを投稿する。

---

## ステップ 0 — MR マージ/クローズ済みイシューのクローズ（クリーンアップ）

レビューフロー開始前に、自分が作成したオープンイシューのうち関連 MR がすでにマージまたはクローズされているものをクローズする。
これにより、人間がマージした後もオープンのままになっているイシューを解消する。

自分が作成したすべてのオープンイシューを取得する:

```
MY_USER=$(python scripts/gl.py current-user --get username)
python scripts/gl.py list-issues --author MY_USER --state opened
```

各イシューについて、関連する MR の状態を確認する:

```
python scripts/gl.py list-mrs --source-branch-prefix "feature/issue-{issue_id}" --state merged
python scripts/gl.py list-mrs --source-branch-prefix "feature/issue-{issue_id}" --state closed
```

`merged` または `closed` の MR が存在する場合はイシューをクローズする:

```
python scripts/gl.py add-comment {issue_id} \
  --body "関連するマージリクエストがマージ/クローズされたため、このイシューをクローズします。"

python scripts/gl.py update-issue {issue_id} \
  --add-labels "status:done" \
  --remove-labels "status:open,status:in-progress,status:review-ready,status:approved,status:needs-rework,status:blocked,status:needs-clarification,assignee:any" \
  --state-event close
```

クローズしたイシューは後続のステップで重複処理しないよう記録しておく。

---

## ステップ 1 — レビュー対象イシューを取得・分類

自分のユーザー名を取得する:

```
python scripts/gl.py current-user --get username
```

`status:review-ready` イシューを**全件**取得する（作成者フィルタなし）:

```
python scripts/gl.py list-issues --label "status:review-ready"
```

取得したイシューを `author.username` で分類する:

- `author.username == MY_USER` → **リクエスターレビューキュー**（自分発行）
- `author.username != MY_USER` → **非リクエスターレビューキュー**（他者発行、[references/non-requester-review.md](non-requester-review.md) の手順で処理）

**優先順位**: リクエスターレビューキューを先に処理し、完了後に非リクエスターレビューキューを処理する。

### リクエスターレビューキューの処理

各イシューについて、自分が実装者（アサイニー）であれば self-defer してスキップする。ただし `priority:high` ラベルが付いている場合はこのチェックをスキップして処理を続行する:

```
python scripts/gl.py check-review-defer {issue_id} --minutes 1440
```

`defer: true` の場合はそのイシューをスキップする。
`defer: false` かつ `reason=self_implemented_lock_expired` の場合は、ロック期間が切れているため自分でレビューしてよい。
`defer: false` かつ `reason=no_worker_node_id` の場合は、実装者特定情報がないため誰でもレビューしてよい。

承認済みイシュー（`status:approved` ラベル付き）は `list-issues --label "status:review-ready"` には出現しないため自動的にスキップされる。
念のためコメントに `<!-- gitlab-idd:requester-approved:{NODE_ID} -->` が含まれるイシューも手動でスキップする:

```
NODE_ID=$(python scripts/gl.py get-node-id --get node_id)
python scripts/gl.py get-comments {issue_id}
```

リクエスターレビューキューが 0 件の場合は非リクエスターレビューキューの処理へ進む。
最大件数を取得する:

```
MAX=$(python scripts/gl.py get-max-review-per-run --get max_review_per_run)
```

複数件ある場合は優先度順（`priority:high` → `normal` → `low`）で並べ、先頭 `MAX` 件のみ処理する。処理が終わったら残りは次回実行時に処理する。

全キューが 0 件（または全件スキップ）の場合は「レビュー待ちイシューはありません」と報告して終了。

---

## ステップ 2 — 成果物の確認

各イシューについて以下を確認する:

```
python scripts/gl.py get-issue {issue_id}
python scripts/gl.py get-comments {issue_id}

python scripts/gl.py list-mrs --source-branch-prefix "feature/issue-{issue_id}"
```

ワーカーが作成したブランチの diff を取得する。ターゲットブランチはイシュー本文の `## ターゲットブランチ` から読み取る（記載がなければ `python scripts/gl.py get-default-branch --get default_branch` で取得）:

```
git fetch origin
git diff {TARGET_BRANCH}...origin/feature/issue-{issue_id}-*
```

---

## ステップ 3 — 受け入れ条件の評価

### ステップ 3-1: 環境スキルの確認と活用

評価の前に、現在の環境で利用可能なスキルを確認して **積極的に活用** する。
利用可能なスキルを自分で調べ、各スキルの `description` を読んでレビューに適したものを選択する。

### ステップ 3-2: agent-reviewer を直接起動

ブランチの変更内容と受け入れ条件をそのまま `agent-reviewer` に渡す。perspective の決定と並列レビューは `agent-reviewer` 自身が行う。

agent-reviewer への入力:
- イシュー本文（受け入れ条件含む）
- ブランチの diff（`git diff {TARGET_BRANCH}..feature/issue-{id}*`）
- ワーカーのサマリーコメント

### ステップ 3-3: 結果集約・修正リトライ（最大 5 回）

全観点の結果を受け取り以下の手順で判定する:

- 全観点が **LGTM** → ステップ 4a（マージ依頼）へ進む
- いずれかの観点が **Request Changes** → 全指摘を統合してワーカーに差し戻す（ステップ 4b）

差し戻し後にワーカーが再提出した場合は、再度ステップ 3-2 からレビューを繰り返す。
**繰り返し上限は 5 回**。5 回目でも Request Changes の場合は現状をユーザーに報告して判断を委ねる。

---

## ステップ 4a — 条件充足: マージ依頼

MR の IID を取得する:

```
python scripts/gl.py list-mrs --source-branch-prefix "feature/issue-{issue_id}" --get 0.iid
```

### CI パイプラインの確認

マージ依頼前に CI パイプラインの状態を確認する:

```
python scripts/gl.py get-mr-pipeline MR_IID --get status
```

| status | 対応 |
|--------|------|
| `success` | そのままマージ依頼へ進む |
| `none` | CI 未設定（スキップしてマージ依頼へ進む） |
| `skipped` | CI スキップ設定済み（スキップしてマージ依頼へ進む） |
| `running` / `pending` | 「パイプライン実行中のため待機中です」と報告して終了。完了後に再度「イシューをレビューして」で再実行する |
| `failed` / `canceled` | MR の URL をユーザーに提示し、「CI が失敗しています。MR を確認してください」と報告して終了 |

イシュー作成者のユーザー名とノード ID を取得する:

```
AUTHOR_USERNAME=$(python scripts/gl.py get-issue {issue_id} --get author.username)
NODE_ID=$(python scripts/gl.py get-node-id --get node_id)
```

レビュー完了コメントのファイルを作成する（`_approve_comment.md`）:

```markdown
## ✅ レビュー承認

**@{AUTHOR_USERNAME} さん、マージをお願いします 🙏**

<!-- gitlab-idd:requester-approved:{NODE_ID} -->
```

コメントを投稿してラベルを更新する:

```
python scripts/gl.py add-comment {issue_id} --body-file _approve_comment.md

python scripts/gl.py update-issue {issue_id} \
  --add-labels "status:approved" \
  --remove-labels "status:review-ready"
```

> **注意**: マージは必ず人間（イシュー作成者）が行う。自動マージはしない。
> MR がマージされた後、次回レビュー実行時のステップ 0 でイシューは自動クローズされる。

**完了報告**:
```
✅ イシュー #{id} のレビューが完了しました。
@{AUTHOR_USERNAME} さんにマージを依頼するコメントを投稿し、ラベルを status:approved に更新しました。
MR がマージされた後、次回レビュー実行時にイシューは自動クローズされます。
```

---

## ステップ 4b — 条件不足: リオープン

差し戻しコメントを `_rework_comment.md` に書く。`{...}` プレースホルダーに流し込む文字列は **GitLab Markdown 形式**（`##` 見出し・`**太字**`・`- 箇条書き`・`` `インラインコード` ``）で記述する:

```markdown
## ❌ 差し戻し

以下の受け入れ条件が未充足です。修正後に再度 `status:review-ready` に更新してください。

### 未充足項目

- {未充足の条件 1}
- {未充足の条件 2}

### 具体的な指摘

{修正が必要な箇所・理由を詳しく記述}
```

コメントを投稿してリオープンする:

```
python scripts/gl.py add-comment {issue_id} --body-file _rework_comment.md

python scripts/gl.py update-issue {issue_id} \
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

## 判定基準

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

- リクエスターは実装を行わない。評価と判定のみ
- **マージは必ず人間が行う**: レビュー完了時に自動マージはしない。`merge-mr` コマンドは使用しない
- 自分が実装したイシューはロック期間（デフォルト 24 時間）中はレビューしない。ロック経過後はレビュー可能
- `worker-node-id` が付いていないイシューは誰でもレビュー可能
- MR が存在しない場合はワーカーに確認コメントを投稿してから評価を保留
- 複数 MR が存在する場合は最新のものを使用
- `status:approved` ラベルのイシューは承認済み（人間のマージ待ち）。`<!-- gitlab-idd:requester-approved:{NODE_ID} -->` マーカーで承認したノードを記録する

---

## スコープ外タスクのイシュー起票

レビュー中に **イシューのスコープ外だが実施すべきタスク** を発見した場合、イシューとして起票する。
スコープの差異に応じて **派生イシュー** または **新規イシュー** を判断する。

「レビュー中に気づいた追加タスクをイシューに上げて」などのフレーズでも発動する。

### 判断基準

| タスクの性質 | 起票種別 | ターゲットブランチ |
|------------|---------|--------------------|
| 同一機能・同じ統合ブランチで解決できる（例: 同じ `feature/{機能名}` に含めるべき修正・改善） | **派生イシュー** | レビュー中イシューのターゲットブランチを引き継ぐ |
| 別機能・別関心事・独立して進めるべき作業 | **新規イシュー** | 新規統合ブランチ（`feature/{task-name}`、`main` から作成） |

### ステップ O-1: スコープ外タスクの整理と報告

発見したスコープ外タスクを以下の形式でユーザーに報告する:

```
レビュー中に以下のスコープ外タスクを発見しました。イシューとして起票します。

| # | タスク概要 | 起票種別 | ターゲットブランチ | 判断理由 |
|---|-----------|---------|-----------------|-------|
| 1 | {タスク概要} | 派生イシュー | {BRANCH}（#{source_id} から引き継ぎ） | {理由} |
| 2 | {タスク概要} | 新規イシュー | 新規統合ブランチ（作成後に確定） | {理由} |
```

**ユーザーに報告**: 「上記のスコープ外タスクをイシューとして起票します。」

そのままステップ O-2 以降を実行する。

### ステップ O-2: ターゲットブランチの確認（派生イシューの場合）

派生イシューを作成する場合は、レビュー中イシューのターゲットブランチを確認する:

```
python scripts/gl.py get-issue {source_issue_id}
```

イシュー本文の `## ターゲットブランチ` セクションから読み取る（記載がなければ `main`）。
このブランチ名を `SOURCE_TARGET_BRANCH` として使用する。

### ステップ O-3: イシューの起票

各タスクについてイシュー本文（`_out_of_scope_N_body.md`）を書き、以下のコマンドで作成する:

**派生イシューの場合** — `## ターゲットブランチ` に `SOURCE_TARGET_BRANCH` を記載し、`## 参考情報` に派生元を明記:

```
python scripts/gl.py create-issue \
  --title "{タイトル}" \
  --body-file _out_of_scope_N_body.md \
  --labels "status:open,assignee:any,priority:{high|normal|low}"
```

本文の `## ターゲットブランチ` および `## 参考情報` に以下を含める:

```markdown
## ターゲットブランチ

{SOURCE_TARGET_BRANCH}

## 参考情報

派生元: #{source_issue_id} {タイトル} のレビュー中に発見
```

**新規イシューの場合** — **[requester-post のフェーズ 1・3〜5](requester-post.md)** と同じフローを実行する：

1. **新規統合ブランチを作成**（requester-post フェーズ 4 の統合ブランチ作成と同じ手順）:

   ```bash
   DEFAULT_BRANCH=$(python scripts/gl.py get-default-branch --get default_branch)
   git fetch origin "$DEFAULT_BRANCH"
   git checkout -b feature/{task-name} "origin/$DEFAULT_BRANCH"
   git push -u origin feature/{task-name}
   ```

   > ブランチ名はタスク内容を簡潔に表す名詞を使う（例: `feature/improve-error-logging`）。

2. **requester-post フェーズ 1・3〜5 を実行**（ターゲットブランチは上記 `feature/{task-name}` に固定）:
   - フェーズ 1: モード判定（新規タスクなので通常モード・ステップ 1-1 へ）→ タスク分割・依存関係整理
   - フェーズ 3: イシュー内容の整理とユーザー確認
   - フェーズ 4: イシュー作成（`## ターゲットブランチ: feature/{task-name}` で固定）
   - フェーズ 5: 完了報告

### ステップ O-4: 完了報告

```
✅ スコープ外タスクをイシューとして起票しました。

| # | イシュー番号 | タイトル | 起票種別 | ターゲットブランチ |
|---|-----------|--------|---------|-----------------|
| 1 | #{id} | {タイトル} | 派生イシュー | {SOURCE_TARGET_BRANCH} |
| 2 | #{id} | {タイトル} | 新規イシュー | feature/{task-name} |
```

---

## 統合ブランチの最終マージ MR 作成

「統合ブランチの最終 MR を作成して」などのフレーズで発動する。

複数のワーカー MR を統合ブランチ（`feature/{機能名}`）にマージし終えた後、
`feature/{機能名} → {DEFAULT_BRANCH}` の最終 MR を作成する。**マージは行わず、人がレビュー・マージする。**

### ステップ F-1: 対象統合ブランチとデフォルトブランチの確認

ユーザーから統合ブランチ名を確認する（明示されている場合はそのまま使用）。

プロジェクトのデフォルトブランチを取得する:

```bash
DEFAULT_BRANCH=$(python scripts/gl.py get-default-branch --get default_branch)
```

全イシューが完了済みであることを確認する:

```
python scripts/gl.py list-issues --label "status:open,status:in-progress,status:review-ready,status:blocked,status:needs-clarification"
```

未完了イシューが残っている場合は「未完了のイシューがあります。完了後に再度実行してください」と報告して終了する。

### ステップ F-2: 統合ブランチの全体レビュー（サブエージェント）

`main` との差分を取得する:

```bash
git fetch origin
git diff "$DEFAULT_BRANCH"...origin/feature/{機能名}
```

差分全体を agent-reviewer に渡して統合レビューを実施する。

agent-reviewer への入力:
- 統合ブランチ名・対象イシューの一覧
- `git diff {DEFAULT_BRANCH}...origin/feature/{機能名}`

> レビュー結果が **Request Changes** の場合: 指摘内容をユーザーに報告して判断を委ねる（自動差し戻しはしない）。
> レビュー結果が **LGTM** の場合: ステップ F-3 へ進む。

### ステップ F-3: 最終マージ MR の作成

MR 本文を `_final_mr_body.md` に書く。`{...}` プレースホルダーに流し込む文字列は **GitLab Markdown 形式**（`##` 見出し・`- 箇条書き`・`- [x] チェックボックス`）で記述する:

```markdown
## 統合内容

`feature/{機能名}` を `main` にマージします。

## 含まれるイシュー

- Closes #{id1} {タイトル1}
- Closes #{id2} {タイトル2}
（統合ブランチ内でマージ済みのイシューをすべて列挙）

## 変更サマリー

{統合ブランチ全体の変更概要を箇条書き}

## レビューポイント

{確認してほしい箇所・注意点}
```

MR を作成する（**draft にしない**）:

```bash
python scripts/gl.py create-mr \
  --title "feat: {機能名}" \
  --source-branch feature/{機能名} \
  --target-branch "$DEFAULT_BRANCH" \
  --description-file _final_mr_body.md
```

**完了報告**:

```
✅ 最終マージ MR を作成しました。
MR: {MR_URL}

内容をご確認の上、マージをお願いします。
```

> **注意**: この MR のマージはリクエスターまたはプロジェクトオーナーが手動で行う。自動マージは行わない。
