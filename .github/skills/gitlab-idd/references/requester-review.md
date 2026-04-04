# リクエスター — レビュー・クローズ / リオープン手順

## 目次

- [ステップ 1 — レビュー対象イシューを取得](#ステップ-1--レビュー対象イシューを取得)
- [ステップ 2 — 成果物の確認](#ステップ-2--成果物の確認)
- [ステップ 3 — 受け入れ条件の並列評価](#ステップ-3--受け入れ条件の並列評価)
- [ステップ 4a — 条件充足: クローズ & マージ](#ステップ-4a--条件充足-クローズ--マージ)
- [ステップ 4b — 条件不足: リオープン](#ステップ-4b--条件不足-リオープン)
- [判定基準](#判定基準)
- [注意事項](#注意事項)
- [統合ブランチの最終マージ MR 作成](#統合ブランチの最終マージ-mr-作成)

**自分が発行した** `status:review-ready` イシューを評価し、マージまたはリオープンする。
他ノードが発行したイシューはレビューしない。
すべての操作は `scripts/gl.py` を Python で実行する（`glab` CLI 不要）。

> **注**: 環境によって `python` を `python3` や `py` に読み替える。

---

## ステップ 1 — レビュー対象イシューを取得

自分のユーザー名を取得する:

```
python scripts/gl.py current-user --get username
```

自分が発行した `status:review-ready` イシューを取得する:

```
python scripts/gl.py list-issues --label "status:review-ready" --author MY_USER
```

レビュー対象が 0 件の場合は「自分が発行したレビュー待ちイシューはありません」と報告して終了。
複数件ある場合は優先度順（`priority:high` → `normal` → `low`）に処理する。

---

## ステップ 2 — 成果物の確認

各イシューについて以下を確認する:

```
python scripts/gl.py get-issue {issue_id}
python scripts/gl.py get-comments {issue_id}

python scripts/gl.py list-mrs --source-branch "feature/issue-{issue_id}"
```

ワーカーが作成したブランチの diff を取得する。ターゲットブランチはイシュー本文の `## ターゲットブランチ` から読み取る（記載がなければ `main`）:

```
git fetch origin
git diff {TARGET_BRANCH}...origin/feature/issue-{issue_id}-*
```

---

## ステップ 3 — 受け入れ条件の並列評価

> ### ⛔ STOP — サブエージェントを今すぐ起動する
>
> **成果物のレビューはサブエージェントが行う。リクエスター自身は直接レビューしない。**
> 読み終えたら即座にサブエージェントを呼び出すこと。

**⚠️ 必ずサブエージェントに委譲すること。自分で評価してはならない。**

### ステップ 3-1: 環境スキルの確認と活用

評価の前に、現在の環境で利用可能なスキルを確認して **積極的に活用** する。
利用可能なスキルを自分で調べ、各スキルの `description` を読んでレビューに適したものを選択する。

レビューに役立つスキルが見つかった場合は、手動でサブエージェントを立てる前にそのスキルを優先して使用する。

### ステップ 3-2: perspectives の決定と並列起動

ブランチの変更内容を確認し、以下の表に従って **perspectives（レビュー観点）** を決定する。
その後、perspectives ごとに **agent-reviewer サブエージェントを単一メッセージで並列起動** する。

| 変更内容 | 使用する perspectives |
|---------|---------------------|
| プロダクションコードあり | `functional` + `ai-antipattern` + `architecture` |
| テストファイルあり（`*.test.*` 等） | 上記に加えて `test` を追加 |
| セキュリティ関連コード（認証・DB・API） | 上記に加えて `security` を追加 |
| ドキュメント・仕様書のみ | `document` のみ |

各 perspective は独立したサブエージェントとして同時起動する（単一メッセージに並べる）。

各サブエージェントへの入力:
- イシュー本文（受け入れ条件含む）
- ブランチの diff（`git diff {TARGET_BRANCH}..feature/issue-{id}*`）
- ワーカーのサマリーコメント
- 使用する perspective 名

### ステップ 3-3: 結果集約・修正リトライ（最大 5 回）

全観点の結果を受け取り以下の手順で判定する:

- 全観点が **LGTM** → ステップ 4a（クローズ）へ進む
- いずれかの観点が **Request Changes** → 全指摘を統合してワーカーに差し戻す（ステップ 4b）

差し戻し後にワーカーが再提出した場合は、再度ステップ 3-2 からレビューを繰り返す。
**繰り返し上限は 5 回**。5 回目でも Request Changes の場合は現状をユーザーに報告して判断を委ねる。

---

## ステップ 4a — 条件充足: クローズ & マージ

MR の IID を取得する:

```
python scripts/gl.py list-mrs --source-branch "feature/issue-{issue_id}" --get 0.iid
```

マージしてイシューをクローズする:

```
python scripts/gl.py merge-mr MR_ID --squash --remove-source-branch

python scripts/gl.py update-issue {issue_id} \
  --add-labels "status:done" \
  --remove-labels "status:review-ready" \
  --state-event close

python scripts/gl.py add-comment {issue_id} \
  --body "✅ 受け入れ条件をすべて満たしています。マージしてクローズしました。"
```

**完了報告**:
```
✅ イシュー #{id} をクローズしました。
MR #{mr_id} をマージ済みです。
```

---

## ステップ 4b — 条件不足: リオープン

差し戻しコメントを `_rework_comment.md` に書く:

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
- **自分が発行したイシューのみレビューする**。他ノードが発行したイシューは対象外
- MR が存在しない場合はワーカーに確認コメントを投稿してから評価を保留
- 複数 MR が存在する場合は最新のものを使用

---

## 統合ブランチの最終マージ MR 作成

「統合ブランチの最終 MR を作成して」などのフレーズで発動する。

複数のワーカー MR を統合ブランチ（`feature/{機能名}`）にマージし終えた後、
`feature/{機能名} → main` の最終 MR を作成する。**マージは行わず、人がレビュー・マージする。**

### ステップ F-1: 対象統合ブランチの確認

ユーザーから統合ブランチ名を確認する（明示されている場合はそのまま使用）。

全イシューが完了済みであることを確認する:

```
python scripts/gl.py list-issues --label "status:open,status:in-progress,status:review-ready,status:blocked"
```

未完了イシューが残っている場合は「未完了のイシューがあります」と報告して確認を取る。

### ステップ F-2: 統合ブランチの全体レビュー（サブエージェント）

`main` との差分を取得する:

```
git fetch origin
git diff main...origin/feature/{機能名}
```

差分の内容に応じて agent-reviewer サブエージェントを並列起動する（判断基準はステップ 3-2 と同じ）。

各サブエージェントへの入力:
- 統合ブランチ名・対象イシューの一覧
- `git diff main...origin/feature/{機能名}`
- 使用する perspective 名

> レビュー結果が **Request Changes** の場合: 指摘内容をユーザーに報告して判断を委ねる（自動差し戻しはしない）。
> レビュー結果が **LGTM** の場合: ステップ F-3 へ進む。

### ステップ F-3: 最終マージ MR の作成

MR 本文を `_final_mr_body.md` に書く:

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

```
python scripts/gl.py create-mr \
  --title "feat: {機能名}" \
  --source-branch feature/{機能名} \
  --target-branch main \
  --description-file _final_mr_body.md
```

**完了報告**:

```
✅ 最終マージ MR を作成しました。
MR: {MR_URL}

内容をご確認の上、マージをお願いします。
```

> **注意**: この MR のマージはリクエスターまたはプロジェクトオーナーが手動で行う。自動マージは行わない。
