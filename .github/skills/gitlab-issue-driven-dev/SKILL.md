---
name: gitlab-issue-driven-dev
description: GitLab イシューを非同期メッセージキューとして使うイシュー駆動開発スキル。スクラムマスターノードがイシューを投稿し、ワーカーノードがプロンプトトリガーで拾って実行・報告する。ポーリング不要。「イシューを立てて」「イシューを拾って実行して」「イシューをレビューして」「受け入れ条件を確認して」「マージして」「リオープンして」などで発動。
metadata:
  version: 1.0.0
  tier: experimental
  category: collaboration
  tags:
    - gitlab
    - issue-driven
    - multi-agent
    - async
---

# gitlab-issue-driven-dev — GitLab イシュー駆動開発

GitLab イシューを **非同期メッセージキュー** として使い、複数ノード間でタスクを分散実行するスキル。ポーリングは行わず、**プロンプトトリガーで起動**する。

---

## ロール選択ガイド

| 役割 | 発動フレーズ | やること |
|------|------------|---------|
| **スクラムマスター（SM）** | 「イシューを立てて」「タスクを依頼して」 | イシュー作成・受け入れ条件定義 |
| **SM — レビュー** | 「イシューをレビューして」「受け入れ条件を確認して」「マージして」「リオープンして」 | 受け入れ評価 → クローズ/マージ or リオープン |
| **ワーカー** | 「イシューを拾って」「担当タスクを実行して」「オープンイシューをこなして」 | イシュー取得 → 実行 → ブランチ＋コメントで報告 |

---

## 前提条件

- `glab` CLI インストール済み・認証済み（`glab auth status` で確認）
- 対象リポジトリの URL または `CI_PROJECT_PATH`（`namespace/repo`）が既知
- GitLab のパーソナルアクセストークンまたは OAuth 設定済み
- ブランチ操作権限あり（ワーカーのみ）

環境変数の設定:
```bash
export GITLAB_PROJECT="namespace/repo"   # 例: myteam/myapp
export GITLAB_HOST="gitlab.com"          # セルフホスト時は変更
```

---

## スクラムマスター — イシュー投稿

タスクを GitLab イシューとして投稿し、他ノードへの実行を委譲する。

**詳細手順** → [references/scrum-master-role.md](references/scrum-master-role.md)

### フロー概要

```
1. タスク要件を整理（受け入れ条件を必ず定義）
2. glab issue create でイシュー作成
3. ラベル "status:open" + "assignee:any" を付与
4. イシュー URL をユーザーに報告して終了（ポーリング不要）
```

---

## スクラムマスター — レビュー・クローズ/リオープン

ワーカーが報告済みのイシューを確認し、受け入れ条件を評価する。

**詳細手順** → [references/scrum-master-role.md](references/scrum-master-role.md)

### フロー概要

```
1. glab issue list でレビュー対象（status:review-ready）を取得
2. イシューコメントとブランチの成果物を確認
3. 受け入れ条件を並列サブエージェントで評価
4a. 条件充足 → glab mr merge + glab issue close
4b. 条件不足 → glab issue reopen + 差し戻しコメント投稿
```

---

## ワーカー — イシュー取得・実行・報告

オープンイシューを取得して実装し、結果をブランチとコメントで返す。

**詳細手順** → [references/worker-role.md](references/worker-role.md)

### フロー概要

```
1. glab issue list でオープンイシューを取得（assigned:any or 自分）
2. イシューを "自分" に assign してロック
3. feature/issue-{id} ブランチを作成
4. scrum-master スタイルの並列評価ループでタスク実行
   └── 実装 → 多角レビュー（機能・セキュリティ・アーキテクチャ）→ 修正 → 最大5回
5. ブランチを push + MR（draft）作成
6. イシューにサマリーコメント投稿 + ラベル "status:review-ready" に更新
```

---

## イシューラベル規約

| ラベル | 意味 |
|--------|------|
| `status:open` | ワーカー未着手 |
| `status:in-progress` | ワーカー実行中 |
| `status:review-ready` | 実装完了・SM レビュー待ち |
| `status:needs-rework` | SM がリオープン済み・再作業必要 |
| `status:done` | SM がクローズ済み |
| `priority:high` / `priority:normal` / `priority:low` | 優先度 |
| `assignee:any` | 誰でも引き受け可（先着） |

---

## ブランチ命名規則

```
feature/issue-{issue-id}-{slug}
例: feature/issue-42-add-login-form
```

---

## GitLab API リファレンス

→ [references/gitlab-api.md](references/gitlab-api.md)

---

## エラーハンドリング

| 状況 | 対応 |
|------|------|
| `glab` 未認証 | `glab auth login` を案内して終了 |
| イシューが競合取得された | assign 失敗を検知 → 次のイシューへ |
| ブランチ競合 | `feature/issue-{id}-{node-id}` にサフィックス追加 |
| MR 作成失敗 | ブランチ名とコメントのみで報告し SM に確認を促す |
| レビュー対象が 0 件 | 「レビュー待ちイシューはありません」と報告して終了 |

---

## 行動指針

1. **ポーリング禁止**: `sleep` ループや定期確認は実装しない。プロンプトで起動するたびに一回だけ実行する
2. **一気通貫**: 取得 → 実行 → 報告 → push を途中で止めない（破壊的操作を除く）
3. **受け入れ条件最優先**: イシュー作成時に `## 受け入れ条件` セクションを必ず含める
4. **最小往復**: 1 回のワーカー実行で SM がマージ判断できる成果物を揃える
5. **並列評価**: ワーカーのレビューは機能・セキュリティ・アーキテクチャを並列サブエージェントで実施

## Permissions

- **Allowed**: `glab` コマンドの実行、ブランチの作成・push、イシューコメントの投稿・ラベル更新、MR の作成・マージ（SM のみ）
- **Denied**: イシューの削除、force push、ユーザー確認なき既存 MR のクローズ、ポーリングループの実装
