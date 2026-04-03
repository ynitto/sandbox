---
name: gitlab-idd
description: GitLab イシューを非同期キューとして使うイシュー駆動開発スキル。リクエスターノードがイシューを投稿し、ワーカーノードがプロンプトトリガーで拾って並列評価ループで実装・報告する。ポーリング不要。REST API（Python スクリプト）で動作し glab 不要。「イシューを立てて」「イシューを拾って実行して」「イシューをレビューして」「ポーリングデーモンをインストールして」などで発動。
metadata:
  version: 4.0.0
  tier: stable
  category: collaboration
  tags:
    - gitlab
    - issue-driven
    - multi-agent
    - async
    - polling-daemon
---

# gitlab-idd — GitLab イシュードリブン開発

GitLab イシューを **非同期タスクキュー** として使い、複数ノード間でタスクを分散実行するスキル。
ポーリングは行わず、**プロンプトトリガーで起動**する。

---

## ロール選択ガイド

| 役割 | 発動フレーズ | やること |
|------|------------|---------|
| **リクエスター — 投稿** | 「イシューを立てて」「タスクを依頼して」 | イシュー作成・受け入れ条件定義 |
| **リクエスター — レビュー** | 「イシューをレビューして」「受け入れ条件を確認して」「マージして」「リオープンして」 | 受け入れ評価 → クローズ/マージ or リオープン |
| **ワーカー** | 「イシューを拾って」「担当タスクを実行して」「オープンイシューをこなして」 | イシュー取得 → 実行 → ブランチ＋コメントで報告 |
| **ポーリングデーモン管理** | 「ポーリングデーモンをインストールして」「デーモンを設定して」 | 常駐デーモンのインストール・リポジトリ追加 |

---

## 前提条件

- Python 3.11+ がインストール済み（stdlib のみ使用・追加パッケージ不要）
- `GITLAB_TOKEN` または `GL_TOKEN` 環境変数にパーソナルアクセストークンを設定済み
- カレントディレクトリが対象 GitLab リポジトリの git ワークツリー内にある
- GitLab ホスト名とプロジェクトパスは `git remote get-url origin` から自動取得（設定不要）
- ポーリングデーモン使用時はエージェント CLI が 1 つ以上インストール済み
  （claude / codex / kiro-cli / q のいずれか）

```bash
# 必須: トークン設定
export GITLAB_TOKEN=glpat-xxxxxxxxxxxx

# 任意: ワーカーが自分発行イシューを引き受けるまでの猶予（分、デフォルト 60）
export GITLAB_SELF_DEFER_MINUTES=60
```

---

## リクエスター — イシュー投稿

タスクを GitLab イシューとして投稿し、ワーカーへの実行を委譲する。

**詳細手順** → [references/requester-post.md](references/requester-post.md)

### フロー概要

```
0. ユーザーへの要件ヒアリング（必須）— 目的・スコープ・受け入れ条件・依存関係を確認
1. タスクを適切な粒度に分割し、依存関係テーブルをユーザーに提示・承認を得る
2. 依存なし or 依存完了済み → "status:open,assignee:any" でイシュー作成
   依存あり（未完了）       → "status:blocked" でイシュー作成
3. イシュー本文に "## 依存イシュー" セクションを必ず含める
4. 作成したイシュー一覧（番号・タイトル・ステータス・依存）をユーザーに報告して終了
```

---

## リクエスター — レビュー・クローズ / リオープン

自分が発行したイシューの成果物を確認し、受け入れ条件を評価する。

**詳細手順** → [references/requester-review.md](references/requester-review.md)

### フロー概要

```
0. MY_USER=$(python scripts/gl.py current-user --get username)
1. list-issues --label "status:review-ready" --author "$MY_USER" でレビュー対象を取得
2. イシューコメントとブランチの成果物を確認
3. 環境で利用可能なスキル（simplify / code-reviewer / security-reviewer 等）を確認・活用
4. 受け入れ条件を並列サブエージェント（またはスキル）で評価（機能・セキュリティ・アーキテクチャ）
5a. 条件充足 → merge-mr + update-issue --state-event close
5b. 条件不足 → add-comment（差し戻し理由）+ update-issue --state-event reopen
```

---

## ワーカー — イシュー取得・実行・報告

オープンイシューを取得して実装し、結果をブランチとコメントで返す。

**詳細手順** → [references/worker-role.md](references/worker-role.md)

### フロー概要

```
1. list-issues でオープンイシューを取得
2. self-defer チェック: 自分発行イシューは DEFER_MINUTES 経過後まで skip
3. 依存チェック: "## 依存イシュー" に記載のイシューがすべて done/closed か確認
   → 未完了の依存あり → スキップまたはコメントして終了
4. イシューを自分に assign してロック（競合防止）
5. feature/issue-{id} ブランチを作成
6. 並列評価ループでタスク実行（最大 5 回）
   └── 実装 → 多角レビュー（機能・セキュリティ・アーキテクチャ）→ 修正
7. ブランチを push + MR（draft）作成
8. イシューにサマリーコメント投稿 + ラベル "status:review-ready" に更新
```

---

## イシューラベル規約

| ラベル | 意味 |
|--------|------|
| `status:open` | ワーカー未着手（着手可） |
| `status:blocked` | 依存イシューが未完了のため着手不可 |
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
# python コマンドは環境に合わせて python3 や py に読み替える
python scripts/gl.py project-info
```

---

## イシューポーリングデーモン

`scripts/gl_poll_daemon.py` と `scripts/gl_poll_setup.py` が OS 常駐ポーリング機能を提供する。
LLM によって発動されることはなく、バックグラウンドサービスとして定期動作する。

**詳細手順** → [references/polling-daemon.md](references/polling-daemon.md)

### 概要

```
新規 status:open + assignee:any イシューを検出
       ↓
エージェント CLI（claude/codex/kiro/amazonq を自動選択）を非同期起動
       ↓
ワーカーフローを実行（担当→実装→ブランチ push→コメント報告）
```

### スキル発動時のインストールフロー

```
1. 利用可能なエージェント CLI を確認（claude/codex/kiro/amazonq のいずれか必須）
2. ユーザーへの確認（必須）— インストール内容を提示して明示的な同意を得る
3. python scripts/gl_poll_setup.py --install  を実行
```

セッション開始のたびに `SessionStart` フックがカレントリポジトリをポーリング対象に自動追加する。

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
| 依存イシューが未完了 | イシューにコメントを投稿して次の候補へ（全候補ブロック中なら終了） |
| レビュー対象が 0 件 | 「自分が発行したレビュー待ちイシューはありません」と報告して終了 |

---

## 行動指針

1. **LLM ポーリング禁止**: LLM セッション内での `sleep` ループや定期確認は実装しない。プロンプトで起動するたびに一回だけ実行する
2. **一気通貫**: 取得 → 実行 → 報告 → push を途中で止めない（破壊的操作を除く）
3. **受け入れ条件最優先**: イシュー作成時に `## 受け入れ条件` セクションを必ず含める
4. **最小往復**: 1 回のワーカー実行でリクエスターがマージ判断できる成果物を揃える
5. **並列評価**: ワーカーのレビューは機能・セキュリティ・アーキテクチャを並列サブエージェントで実施
6. **self-defer 遵守**: ワーカーは自分が発行したイシューを猶予期間中は取得しない
7. **依存遵守**: ワーカーは `## 依存イシュー` に記載されたイシューが完了するまで着手しない
8. **要件ヒアリング必須**: リクエスターはイシュー作成前に必ずユーザーに要件を確認し、タスクを適切な粒度に分割する
9. **スキル優先活用**: レビュー時は環境で利用可能なスキルを自ら調べ、description を読んで適切なものを積極的に活用する
10. **デーモンインストール前にユーザー確認必須**: `gl_poll_setup.py --install` を実行する前に、必ずユーザーに内容を提示して同意を得る
11. **エージェント CLI 確認必須**: デーモンインストール前に claude/codex/kiro/amazonq のいずれかが利用可能か確認し、見つからない場合はインストールを中止する
12. **セッション開始時の自動設定**: セッション開始のたびにカレントリポジトリをポーリング設定に追加する（SessionStart フック経由）

## Permissions

- **Allowed**: `scripts/gl.py` の実行（Python）、ブランチの作成・push、イシューコメント投稿・ラベル更新（`status:blocked` → `status:open` の解除を含む）、MR の作成・マージ（リクエスターのみ）、ユーザー確認後の `gl_poll_setup.py --install` 実行、`gl_poll_setup.py --add-repo` / `--status` / `--session-start` の実行
- **Denied**: イシューの削除、force push、ユーザー確認なき既存 MR のクローズ、LLM セッション内でのポーリングループ実装、ユーザー確認なき `gl_poll_setup.py --install` の実行
