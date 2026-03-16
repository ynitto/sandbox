---
name: gitlab-issue-driven-dev
description: GitLab イシューを非同期キューとして使うイシュー駆動開発スキル。リクエスターノードがイシューを投稿し、ワーカーノードがプロンプトトリガーで拾って並列評価ループで実装・報告する。ポーリング不要。REST API（Python スクリプト）で動作し glab 不要。「イシューを立てて」「イシューを拾って実行して」「イシューをレビューして」などで発動。
metadata:
  version: 2.0.0
  tier: experimental
  category: collaboration
  tags:
    - gitlab
    - issue-driven
    - multi-agent
    - async
---

# gitlab-issue-driven-dev — GitLab イシュードリブン開発

GitLab イシューを **非同期タスクキュー** として使い、複数ノード間でタスクを分散実行するスキル。
ポーリングは行わず、**プロンプトトリガーで起動**する。

---

## ロール選択ガイド

| 役割 | 発動フレーズ | やること |
|------|------------|---------|
| **リクエスター** | 「イシューを立てて」「タスクを依頼して」 | イシュー作成・受け入れ条件定義 |
| **リクエスター — レビュー** | 「イシューをレビューして」「受け入れ条件を確認して」「マージして」「リオープンして」 | 受け入れ評価 → クローズ/マージ or リオープン |
| **ワーカー** | 「イシューを拾って」「担当タスクを実行して」「オープンイシューをこなして」 | イシュー取得 → 実行 → ブランチ＋コメントで報告 |

---

## 前提条件

- Python 3.8+ がインストール済み（stdlib のみ使用・追加パッケージ不要）
- `GITLAB_TOKEN` または `GL_TOKEN` 環境変数にパーソナルアクセストークンを設定済み
- カレントディレクトリが対象 GitLab リポジトリの git ワークツリー内にある
- GitLab ホスト名とプロジェクトパスは `git remote get-url origin` から自動取得（設定不要）

```bash
# 必須: トークン設定
export GITLAB_TOKEN=glpat-xxxxxxxxxxxx

# 任意: ワーカーが自分発行イシューを引き受けるまでの猶予（分、デフォルト 60）
export GITLAB_SELF_DEFER_MINUTES=60
```

---

## リクエスター — イシュー投稿

タスクを GitLab イシューとして投稿し、ワーカーへの実行を委譲する。

**詳細手順** → [references/requester-role.md](references/requester-role.md)

### フロー概要

```
1. タスク要件を整理（受け入れ条件を必ず定義）
2. python scripts/gl.py create-issue でイシュー作成
3. ラベル "status:open" + "assignee:any" を付与
4. イシュー URL をユーザーに報告して終了（ポーリング不要）
```

---

## リクエスター — レビュー・クローズ / リオープン

ワーカーが報告済みのイシューを確認し、受け入れ条件を評価する。

**詳細手順** → [references/requester-role.md](references/requester-role.md)

### フロー概要

```
1. list-issues --label "status:review-ready" でレビュー対象を取得
2. イシューコメントとブランチの成果物を確認
3. 受け入れ条件を並列サブエージェントで評価（機能・セキュリティ・アーキテクチャ）
4a. 条件充足 → merge-mr + update-issue --state-event close
4b. 条件不足 → add-comment（差し戻し理由）+ update-issue --state-event reopen
```

---

## ワーカー — イシュー取得・実行・報告

オープンイシューを取得して実装し、結果をブランチとコメントで返す。

**詳細手順** → [references/worker-role.md](references/worker-role.md)

### フロー概要

```
1. list-issues でオープンイシューを取得
2. self-defer チェック: 自分発行イシューは DEFER_MINUTES 経過後まで skip
3. イシューを自分に assign してロック（競合防止）
4. feature/issue-{id} ブランチを作成
5. 並列評価ループでタスク実行（最大 5 回）
   └── 実装 → 多角レビュー（機能・セキュリティ・アーキテクチャ）→ 修正
6. ブランチを push + MR（draft）作成
7. イシューにサマリーコメント投稿 + ラベル "status:review-ready" に更新
```

---

## イシューラベル規約

| ラベル | 意味 |
|--------|------|
| `status:open` | ワーカー未着手 |
| `status:in-progress` | ワーカー実行中 |
| `status:review-ready` | 実装完了・レビュー待ち |
| `status:needs-rework` | リオープン済み・再作業必要 |
| `status:done` | クローズ済み |
| `priority:high` / `priority:normal` / `priority:low` | 優先度 |
| `assignee:any` | 誰でも引き受け可（先着） |

---

## ブランチ命名規則

```
feature/issue-{issue-id}-{slug}
例: feature/issue-42-add-login-form
```

---

## Python スクリプト

`scripts/gl.py` を使ってすべての GitLab API 操作を行う。`glab` CLI は不要。

→ コマンドリファレンス: [references/gitlab-api.md](references/gitlab-api.md)

```bash
# 動作確認（git remote からホスト・プロジェクトを自動取得）
python .github/skills/gitlab-issue-driven-dev/scripts/gl.py project-info
```

---

## エラーハンドリング

| 状況 | 対応 |
|------|------|
| `GITLAB_TOKEN` 未設定 | トークン設定を案内して終了 |
| `git remote origin` が存在しない | エラーメッセージを出して終了 |
| イシューが競合取得された | assign 後に自分が assignee か確認 → 違えば次のイシューへ |
| ブランチ競合 | `feature/issue-{id}-{hostname}-{slug}` にサフィックス追加 |
| 取得可能なイシューが 0 件 | 「実行可能なイシューはありません」と報告して終了 |
| 自分発行イシューのみ・猶予期間中 | 猶予期間終了時刻を報告して終了 |
| レビュー対象が 0 件 | 「レビュー待ちイシューはありません」と報告して終了 |

---

## 行動指針

1. **ポーリング禁止**: `sleep` ループや定期確認は実装しない。プロンプトで起動するたびに一回だけ実行する
2. **一気通貫**: 取得 → 実行 → 報告 → push を途中で止めない（破壊的操作を除く）
3. **受け入れ条件最優先**: イシュー作成時に `## 受け入れ条件` セクションを必ず含める
4. **最小往復**: 1 回のワーカー実行でレビュアーがマージ判断できる成果物を揃える
5. **並列評価**: ワーカーのレビューは機能・セキュリティ・アーキテクチャを並列サブエージェントで実施
6. **self-defer 遵守**: ワーカーは自分が発行したイシューを猶予期間中は取得しない

## Permissions

- **Allowed**: `scripts/gl.py` の実行（Python）、ブランチの作成・push、イシューコメント投稿・ラベル更新、MR の作成・マージ（リクエスターのみ）
- **Denied**: イシューの削除、force push、ユーザー確認なき既存 MR のクローズ、ポーリングループの実装
