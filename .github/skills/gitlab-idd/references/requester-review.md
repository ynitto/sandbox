# リクエスター — レビュー・クローズ / リオープン手順

## 目次

- [ステップ 1 — レビュー対象イシューを取得](#ステップ-1--レビュー対象イシューを取得)
- [ステップ 2 — 成果物の確認](#ステップ-2--成果物の確認)
- [ステップ 3 — 受け入れ条件の並列評価](#ステップ-3--受け入れ条件の並列評価)
- [ステップ 4a — 条件充足: クローズ & マージ](#ステップ-4a--条件充足-クローズ--マージ)
- [ステップ 4b — 条件不足: リオープン](#ステップ-4b--条件不足-リオープン)
- [判定基準](#判定基準)
- [注意事項](#注意事項)

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

ワーカーが作成したブランチの diff を取得する:

```
git fetch origin
git diff main...origin/feature/issue-{issue_id}-*
```

---

## ステップ 3 — 受け入れ条件の並列評価

**⚠️ 必ずサブエージェントに委譲すること。自分で評価してはならない。**

### ステップ 3-1: 環境スキルの確認と活用

評価の前に、現在の環境で利用可能なスキルを確認して **積極的に活用** する。

```
# 利用可能なスキルの確認方法:
# ~/.claude/skills/ や .github/skills/ 配下に定義されているスキルを参照する
# 例: /simplify, /code-reviewer, /security-reviewer など
```

利用可能なスキルの例と用途:

| スキル名 | 活用タイミング |
|---------|--------------|
| `simplify` | コード変更後のリファクタリング品質確認・重複排除 |
| `code-reviewer` | 機能要件・コードスタイルの検証 |
| `security-reviewer` | セキュリティ観点での脆弱性チェック |
| その他環境固有スキル | スキル定義の `description` を読んで判断 |

**スキルが利用可能な場合はサブエージェントを手動で立てる前にスキルを優先して使用する。**

### ステップ 3-2: 3 観点の並列評価

以下の 3 観点を **並列** サブエージェント（または対応スキル）で評価する:

| エージェント / スキル | 観点 |
|-------------------|------|
| 機能要件エージェント（または `code-reviewer` スキル） | 受け入れ条件チェックリストの全項目検証 |
| セキュリティエージェント（または `security-reviewer` スキル） | OWASP Top 10 視点でコード変更を確認 |
| アーキテクチャエージェント（または `simplify` スキル） | 設計の一貫性・依存方向・責任分割・重複排除を確認 |

各エージェント / スキルへの入力:
- イシュー本文（受け入れ条件含む）
- ブランチの diff（`git diff main..feature/issue-{id}*`）
- ワーカーのサマリーコメント

評価結果を統合し、全条件を満たしているか判定する。

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
