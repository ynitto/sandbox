# 非リクエスターレビュアー — レビュー手順

## 目次

- [概要](#概要)
- [ステップ 1 — レビュー対象イシューを取得](#ステップ-1--レビュー対象イシューを取得)
- [ステップ 2 — 成果物の確認](#ステップ-2--成果物の確認)
- [ステップ 3 — 受け入れ条件の並列評価](#ステップ-3--受け入れ条件の並列評価)
- [ステップ 4 — レビューコメント投稿](#ステップ-4--レビューコメント投稿)
- [注意事項](#注意事項)

## 概要

「イシューをレビューして」で発動したレビューフローの中で、`author.username != MY_USER`（他者発行）と判定されたイシューに適用される振る舞いを定義する。
レビュー評価はリクエスターと同じ基準で行うが、最終判断（マージ・クローズ・リオープン）はリクエスターが行うため、**マージリクエストのマージおよびイシューのクローズは行わない**。

> **エントリポイント**: [references/requester-review.md のステップ 1](requester-review.md#ステップ-1--レビュー対象イシューを取得分類) で非リクエスターレビューキューに分類されたイシューについて、このドキュメントのステップ 1 以降を実行する。

すべての操作は `scripts/gl.py` を Python で実行する（`glab` CLI 不要）。

> **注**: 環境によって `python` を `python3` や `py` に読み替える。

---

## ステップ 1 — スキップチェック

ノード ID を取得する:

```
python scripts/gl.py get-node-id --get node_id
```

非リクエスターレビューキュー内の各イシューについて次の 3 つのチェックを順に実施し、いずれかが `defer: true` ならそのイシューをスキップする。

### チェック 1: self-defer（ワーカーと同じ）

自分が作成したイシューは猶予期間中はスキップする:

```
python scripts/gl.py check-defer {issue_id}
```

| reason | defer | 対応 |
|--------|-------|------|
| `not_my_issue` | false | 他者が作成 → 次のチェックへ |
| `self_created_too_recent` | true | 自分作成・猶予中 → スキップ |
| `self_created_but_expired` | false | 自分作成・猶予切れ → 次のチェックへ |

### チェック 2: self-review ロック（リクエスターと同じ）

自分が実装したイシューはロック期間中はスキップする:

```
python scripts/gl.py check-review-defer {issue_id} --minutes 1440
```

| reason | defer | 対応 |
|--------|-------|------|
| `no_worker_node_id` | false | 実装者不明 → 次のチェックへ |
| `not_my_implementation` | false | 他者が実装 → 次のチェックへ |
| `self_implemented_locked` | true | 自分実装・ロック中 → スキップ |
| `self_implemented_lock_expired` | false | ロック切れ → 次のチェックへ |

### チェック 3: 今サイクル既レビューチェック

このノードが現在の作業サイクルですでにレビュー済みの場合はスキップする:

```
python scripts/gl.py check-non-requester-review-defer {issue_id}
```

| reason | defer | 対応 |
|--------|-------|------|
| `not_yet_reviewed` | false | 未レビュー → レビューへ進む |
| `already_reviewed_this_cycle` | true | 今サイクル済み → スキップ |

> **「今サイクル」の定義**: ワーカーが最後に着手コメント（`worker-node-id` タグ）を投稿した以降。
> ワーカーが差し戻し後に再着手すると新しい `worker-node-id` コメントが投稿され、このノードは再びレビュー可能になる。

レビュー対象が 0 件（または全件スキップ）の場合は終了。
複数件ある場合は優先度順（`priority:high` → `normal` → `low`）で並べ、先頭 **残り枠** 件のみ処理する。残り枠 = `GITLAB_MAX_REVIEW_PER_RUN`（デフォルト 1）− リクエスターキューで処理した件数。残り枠が 0 の場合はスキップして終了する。

---

## ステップ 2 — 成果物の確認

各イシューについて以下を確認する:

```
python scripts/gl.py get-issue {issue_id}
python scripts/gl.py get-comments {issue_id}

python scripts/gl.py list-mrs --source-branch "feature/issue-{issue_id}"
```

ワーカーが作成したブランチの diff を取得する。ターゲットブランチはイシュー本文の `## ターゲットブランチ` から読み取る（記載がなければ `python scripts/gl.py get-default-branch --get default_branch` で取得）:

```
git fetch origin
git diff {TARGET_BRANCH}...origin/feature/issue-{issue_id}-*
```

---

## ステップ 3 — 受け入れ条件の評価

### ステップ 3-1: 環境スキルの確認と活用

利用可能なスキルを自分で調べ、各スキルの `description` を読んでレビューに適したものを選択する。

### ステップ 3-2: agent-reviewer を直接起動

ブランチの変更内容と受け入れ条件をそのまま `agent-reviewer` に渡す。

agent-reviewer への入力:
- イシュー本文（受け入れ条件含む）
- ブランチの diff（`git diff {TARGET_BRANCH}..feature/issue-{id}*`）
- ワーカーのサマリーコメント

### ステップ 3-3: 結果集約

全観点の結果を受け取り判定する:
- 全観点が **LGTM** → ステップ 4 で LGTM コメントを投稿
- いずれかの観点が **Request Changes** → ステップ 4 で指摘コメントを投稿

> **注**: リクエスターと異なり、判定結果にかかわらずマージ・クローズ・リオープンは行わない。
> 最終判断はリクエスターに委ねる。

---

## ステップ 4 — レビューコメント投稿

ノード ID を取得する:

```
NODE_ID=$(python scripts/gl.py get-node-id --get node_id)
```

コメントを `_non_requester_review_comment.md` に書いて投稿する:

```
python scripts/gl.py add-comment {issue_id} --body-file _non_requester_review_comment.md
```

### LGTM の場合

`_non_requester_review_comment.md` の内容:

```markdown
## 🔍 非リクエスターレビュー — LGTM 👍

受け入れ条件をすべて確認しました。問題ありません。

### 確認した観点

- {観点 1}: ✅ 問題なし
- {観点 2}: ✅ 問題なし

<!-- gitlab-idd:non-requester-reviewed:{NODE_ID} -->
```

### Request Changes の場合

`_non_requester_review_comment.md` の内容:

```markdown
## 🔍 非リクエスターレビュー — 要確認 ⚠️

以下の点を確認してください。最終判断はリクエスターが行います。

### 指摘事項

- {指摘 1: 具体的な内容}
- {指摘 2}

### 詳細

{修正が望ましい箇所・理由を詳しく記述}

<!-- gitlab-idd:non-requester-reviewed:{NODE_ID} -->
```

> **重要**: コメントの末尾に必ず `<!-- gitlab-idd:non-requester-reviewed:{NODE_ID} -->` タグを含める。
> このタグが次回の `check-non-requester-review-defer` で「今サイクル済み」として検出される。

---

## 注意事項

- **マージ・クローズは行わない**: `merge-mr` および `update-issue --state-event close` は実行しない
- **ラベルは変更しない**: `status:review-ready` のままにしておく
- **助言コメントのみ**: レビュー結果は助言。リクエスターが最終判断を行う
- **self-defer 遵守**: 自分が作成したイシューは猶予期間中はレビューしない（ワーカーと同じ）
- **self-review ロック遵守**: 自分が実装したイシューはロック期間中はレビューしない（リクエスターと同じ）
- **再レビュー禁止**: 同一作業サイクルでは 1 回のみレビューする。ワーカーが差し戻し後に再着手すれば再びレビュー可能
