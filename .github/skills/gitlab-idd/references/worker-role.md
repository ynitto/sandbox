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
  │         ├── 放置アサインチェック: 他ノード着手中はロック期間中スキップ
   │         ├── 猶予期間経過後は自分発行イシューも実行可
   │         └── 依存チェック: 依存イシューが未完了ならスキップ
   │
Phase 3  イシュー着手 ─── assign + ラベル更新 + テンポラリクローン作成 + 空ドラフト MR 作成
   │
Phase 4  タスク実行 ─── 実装ループ（最大 5 回）
  │         └── スキル選定（skill-selector）→ 実装 → supporting_skills をそのまま適用 → agent-reviewer でレビュー → 修正・コミット・push
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

# 4. 放置アサイン救済候補（クローズしておらず、status:open/status:done 以外）
python scripts/gl.py list-issues --state opened --exclude-labels "status:open,status:done"
```

### ステップ 2-2: self-defer チェック（自分発行イシューの猶予）

他ノードに実行させるため自分が発行したイシューには猶予期間を設ける。
猶予期間は `GITLAB_SELF_DEFER_MINUTES` 環境変数（デフォルト 60 分 = 1 時間）。

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

### ステップ 2-3: 放置アサインチェック（他ノード着手済みイシューの疎境期間）

`status:open` / `status:done` 以外の「クローズしていない」候補に対して実行する。
（ステップ 2-1 の 4 で取得した候補群）
疎境期間は `GITLAB_ASSIGNED_LOCK_MINUTES` 環境変数（デフォルト 1440 分 = 24 時間）。

```
python scripts/gl.py check-assigned-defer {issue_id} --get defer
# → True（スキップ）または False（引き受け可）

python scripts/gl.py check-assigned-defer {issue_id} --get remaining_minutes
# → スキップ時の残り疎境分数
```

`defer` が `True` の場合はそのイシューをスキップして次の候補へ進む。

`check-assigned-defer` の判定結果:

| reason | defer | 意味 |
|--------|-------|------|
| `no_worker_node_id` | false | 着手記録なし → 引き受け可 |
| `my_assignment` | false | 自分が着手済み → 引き受け可 |
| `assigned_active_lock` | true | 他ノードが着手中（ロック中） → スキップ |
| `assigned_lock_unknown` | true | 着手時刻不明 → スキップ |
| `assigned_lock_expired` | false | 着手から 24h 経過し放置 → 引き受け可 |

### ステップ 2-4: イシュー選択

優先順位:
1. `status:needs-rework` かつ自分 assign のもの（差し戻し再作業）
2. `status:open` かつ自分 assign のもの
3. `status:open,assignee:any` のもの
4. `status:open/status:done` 以外でクローズしていないもの（放置アサイン救済候補）

各候補に対して次を順に実行し、両方 `defer=false` の先頭 1 件を選択する。

1. `check-defer`（自分発行イシュー猶予）
2. `check-assigned-defer`（放置アサインロック）

全候補が `defer=true` の場合: 「残り {remaining_minutes} 分後に実行可能です」と報告して終了。
オープンイシューが 0 件の場合: 「実行可能なオープンイシューはありません」と報告して終了。

### ステップ 2-5: 依存イシューチェック

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

### ステップ 2-6: イシュー詳細の読み込み

```
python scripts/gl.py get-issue {issue_id}
python scripts/gl.py get-comments {issue_id}
```

`## 受け入れ条件` セクションを必ず確認し、全項目を把握してから ステップ 2-7 へ進む。

### ステップ 2-7: 説明の明確性チェック（着手前必須）

イシューの説明文と受け入れ条件を読み、**実装に着手できるだけの情報が揃っているか**を判断する。

以下のいずれかに該当する場合は **曖昧と判定する**:

| 判定基準 | 例 |
|---------|---|
| 受け入れ条件が存在しないまたは極端に少ない | 「ログイン機能を追加して」のみ |
| 技術的な詳細が不明で複数の解釈が生じうる | 「パフォーマンスを改善して」(何を・どう・どの程度かが不明) |
| 影響範囲が不明（ファイル・モジュール・APIが特定できない） | 「エラーハンドリングを直して」(どのエラー・どの箇所かが不明) |
| 受け入れ条件が検証不能（テスト・確認方法が示せない） | 「ユーザー体験を向上させて」|

**判定: 明確な場合** → そのまま Phase 3 へ進む。

**判定: 曖昧な場合** → 以下を実行して終了する（Phase 3 には進まない）:

1. イシューにコメントを投稿する:

```
python scripts/gl.py add-comment {issue_id} --body-file _clarification.md
```

`_clarification.md` の内容:

```markdown
## ⚠️ 説明が不明確なため着手を保留します

以下の点が不明確なため、実装に着手できません。リクエスターの方はイシュー説明を詳細化してください。

### 不明確な点

- {不明確な点 1: 具体的に何が分からないか}
- {不明確な点 2}

### 確認したい事項

- {リクエスターに確認したい具体的な質問 1}
- {具体的な質問 2}

<!-- gitlab-idd:clarification-requested -->
```

2. イシューのラベルを更新する:

```
python scripts/gl.py update-issue {issue_id} \
  --add-labels "status:needs-clarification" \
  --remove-labels "status:open,assignee:any,status:needs-rework"
```

3. ユーザーに報告して終了する:

```
⚠️ イシュー #{id} の説明が不明確なため着手を保留しました。
不明確な点をコメントに記載し、ラベルを「status:needs-clarification」に更新しました。
リクエスターが詳細化後、再度「イシューを拾って」で実行してください。
```

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

### ステップ 3-2: 作業用テンポラリクローンとブランチ作成

イシュー本文の `## ターゲットブランチ` セクションからターゲットブランチを読み取る。セクションがない場合は GitLab API からデフォルトブランチを取得して使用する（TARGET_BRANCH とする）:

```bash
TARGET_BRANCH=$(python scripts/gl.py get-default-branch --get default_branch)
```

feature ブランチは **テンポラリ領域にリポジトリをクローンして作成** する。複数エージェントが並行作業できるよう、クローン先はイシュー単位でユニークなパスとする。

```
python scripts/gl.py make-branch-name {issue_id}
# → "feature/issue-42-add-login-form" のようなブランチ名が出力される

# カレントディレクトリからリモート URL を取得
REMOTE_URL=$(git remote get-url origin)

# WSL・Linux 共通: /tmp 配下にイシュー単位でユニークなクローン先を作成
CLONE_DIR=$(mktemp -d /tmp/gitlab-idd-{issue_id}-XXXXXXXX)

# クローン
git clone --origin origin "$REMOTE_URL" "$CLONE_DIR"
cd "$CLONE_DIR"

# TARGET_BRANCH を基点に feature ブランチを作成
git fetch origin TARGET_BRANCH
git checkout -b BRANCH origin/TARGET_BRANCH
```

> WSL 環境では `/tmp` は Linux ファイルシステム側のテンポラリ領域を指す。Windows 側のパス（`/mnt/c/...`）は使用しない。
> `mktemp -d` により各エージェントのクローン先が競合しない。`CLONE_DIR` は Phase 5 末尾で削除する。

### ステップ 3-3: 着手コメント投稿

ノードIDを取得してからコメントに含める（`check-review-defer` がこのコメントのノードIDを使って実装者を識別する）:

```
NODE_ID=$(python scripts/gl.py --get node_id get-node-id)

python scripts/gl.py add-comment {issue_id} --body "🚀 **作業開始**: 着手しました。ブランチ: BRANCH
<!-- gitlab-idd:worker-node-id:${NODE_ID} -->"
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
  --draft \
  --remove-source-branch
```

---

## Phase 4 — タスク実行（実装ループ）

### 概要

実装 → 多角レビュー → 修正のサイクルを最大 5 回繰り返す。

### ステップ 4-0: スキル選定（skill-selector）

実装サブエージェントを起動する前に `skill-selector` を使って最適な実装スキルと補助スキルを選定する。

```bash
# skill-selector の SKILL.md を読んでインライン実行する
cat ${SKILLS_DIR}/skill-selector/SKILL.md
```

**skill-selector に渡す情報**:
- イシュータイトル・受け入れ条件（技術スタック・言語・フレームワーク）
- 差し戻しコメントがある場合はその内容

**skill-selector の推薦結果の扱い**:
1. **worker-role は推薦結果を再解釈しない**。どのプライマリスキルを使うか、どの補助スキルを先行適用するかは、すべて skill-selector の出力を正とする
2. **出力契約をそのまま保持する**。少なくとも `selection_status` / `primary_skills` / `supporting_skills` / `execution_plan` / `notes` を構造のまま扱う
3. **プライマリスキル**: `primary_skills[].name` をステップ 4-1 のサブエージェントとして使用する
4. **補助指示**: `supporting_skills.principle` / `supporting_skills.conditional` は `mode` / `timing` / `name` / `instruction` に従ってそのまま適用する。worker-role 側で個別の補助スキル判定や優先順位付けを追加しない
5. **レビュー**: レビューは skill-selector の返却値ではなく、worker-role が `agent-reviewer` を直接呼び出して実施する

適切な実装スキルが特定できない場合のみ、skill-selector の結論に従って汎用実装サブエージェントへフォールバックする。

### ステップ 4-1: タスク実装

ステップ 4-0 で選定したスキルに従ってタスクを実装する。

```
イシュー本文（受け入れ条件・技術制約を含む）と差し戻しコメント（あれば）、
使用スキル（${SKILLS_DIR}/[選定スキル名]/SKILL.md）を参照し:
- 受け入れ条件を全て満たすコードを作成・変更する
- 実装スコープ外の変更を含めない
- 変更内容のサマリーをまとめておく
```

### ステップ 4-2: 補助指示の適用（supporting_skills）

実装の前後で、skill-selector が返した `supporting_skills` をそのまま適用する。

```yaml
supporting_skills:
  principle:
    mode: skill | fallback | none
    name: skill-name | null
    instruction: string | null
    timing: before-primary | after-primary | null
  conditional:
    mode: skill | fallback | none
    name: skill-name | null
    instruction: string | null
    timing: before-primary | after-primary | null
```

適用ルール:
- `timing: before-primary` の項目はステップ 4-1 の前に適用する
- `mode: skill` の項目は `${SKILLS_DIR}/[name]/SKILL.md` を読んで補助スキルとして実行する
- `mode: fallback` の項目は `instruction` をそのまま実施する
- `mode: none` の項目はスキップする
- `timing: after-primary` の項目は実装成果物に対して適用する

`self-checking` が返ってきた場合も、worker-role は特別扱いせず上記ルールで処理する。スキル不在時の自然文フォールバックも `instruction` をそのまま使う。

### ステップ 4-3: agent-reviewer でレビュー

実装・補助指示適用完了後、成果物全体を `agent-reviewer` に渡してレビューする。perspective の決定と並列レビューは `agent-reviewer` 自身が行う。

agent-reviewer への入力:
- イシュー本文（受け入れ条件含む）
- ブランチの diff（`git diff {TARGET_BRANCH}..BRANCH`）
- ワーカーのサマリーコメント

### ステップ 4-4: 指摘統合と修正判断

```
全レビュー結果を統合:
  - 指摘なし or 軽微のみ → Phase 5 へ進む
  - 修正必要な指摘あり   → 修正してステップ 4-3 に戻る（最大 5 回）
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

> **GitLab Markdown ルール**: このフェーズで書くテンプレートの `{...}` プレースホルダーに流し込む文字列は **必ず GitLab Markdown 形式** で記述する。
> `##` 見出し・`**太字**`・`- 箇条書き`・` ``` コードブロック ``` `・`- [x] / - [ ] チェックボックス`・`` `インラインコード` `` を使う。プレーンテキストのまま埋めない。

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

> Draft 解除前に必ず最新コミットを push しておく。`--remove-source-branch` は MR 作成時に設定済みのため再指定は不要。

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

### ステップ 5-6: テンポラリクローンの削除

すべての push・コメント投稿が完了したら、テンポラリクローンを削除する:

```
cd /tmp
rm -rf "$CLONE_DIR"
```

---

## ワーカーの行動原則

1. **1 イシュー = 1 ブランチ = 1 MR**: 複数イシューの変更を 1 フィーチャーブランチに混在させない。ただし複数のフィーチャーブランチが同じ統合ブランチ（TARGET_BRANCH）を MR のターゲットにすることは許容される
2. **作業はテンポラリクローンで分離**: 元のリポジトリには触れず、実装はテンポラリ領域にクローンした issue 専用リポジトリで行う。完了後はテンポラリを削除する
3. **スコープ厳守**: イシューで定義された範囲外の変更を含めない
4. **受け入れ条件を読む**: 実装前に必ず受け入れ条件を確認し、全項目をカバーする
5. **レビューを通す**: agent-reviewer でレビューを必ず実施する。自己判断でスキップしない
6. **コメントで証跡を残す**: リクエスターが判断できるよう、何をどう実装したかをコメントに記載する
7. **self-defer を守る**: 自分発行イシューは猶予期間中は取得しない。他ノードへの委譲を尊重する
8. **依存を守る**: `## 依存イシュー` に記載されたイシューがすべて完了するまで着手しない
9. **曖昧なイシューには着手しない**: 説明・受け入れ条件が不明確な場合はコメントで指摘し、`status:needs-clarification` に更新してリクエスターの詳細化を待つ
