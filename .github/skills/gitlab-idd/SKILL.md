---
name: gitlab-idd
description: GitLab イシューを非同期タスクキューとして使うイシュー駆動開発スキル。glab 不要。「GitLab イシューを立てて」「イシューを拾って実行して」「イシューをレビューして」「GitLab MR をマージして」などで発動。GitLab イシュー・MR の操作が含まれる場合に優先して選択する。
metadata:
  version: 4.3.0
  tier: stable
  category: collaboration
  tags:
    - gitlab
    - issue-driven
    - multi-agent
    - async
---

# gitlab-idd — GitLab イシュードリブン開発

GitLab イシューを **非同期タスクキュー** として使い、複数ノード間でタスクを分散実行するスキル。
ポーリングは行わず、**プロンプトトリガーで起動**する。

---

## ロール選択ガイド

| 役割 | 発動フレーズ | やること |
|------|------------|---------|
| **リクエスター — 投稿** | 「イシューを立てて」「タスクを依頼して」 | イシュー作成・受け入れ条件定義 |
| **レビュアー** | 「イシューをレビューして」「受け入れ条件を確認して」「マージして」「リオープンして」「統合ブランチの最終 MR を作成して」「曖昧なイシューを詳細化して」「イシューを明確化して」 | 自分発行イシュー → 受け入れ評価・マージ依頼 or リオープン；他者発行イシュー → 助言コメントのみ（マージ・クローズ不可）。どちらもある場合は自分発行を優先 |
| **ワーカー** | 「イシューを拾って」「担当タスクを実行して」「オープンイシューをこなして」 | イシュー取得 → 説明の明確性確認 → 実行 → ブランチ＋コメントで報告 |
## 前提条件

- Python 3.8+ がインストール済み（stdlib のみ使用・追加パッケージ不要）
- `GITLAB_TOKEN` または `GL_TOKEN` 環境変数にパーソナルアクセストークンを設定済み
- カレントディレクトリが対象 GitLab リポジトリの git ワークツリー内にある
- GitLab ホスト名とプロジェクトパスは `git remote get-url origin` から自動取得（設定不要）

```bash
# 必須: トークン設定（~/.bashrc / ~/.profile に記載がある場合は自動読み込み）
export GITLAB_TOKEN=glpat-xxxxxxxxxxxx

# 任意: ターミナル（ノード）を識別するID（未設定時は skill-registry.json の skill_configs.gitlab-idd.node_id に自動生成）
# 同一 GitLab アカウントで複数ターミナルを独立ノードとして動かす場合に設定する
export GITLAB_NODE_ID=my-terminal-1

# 任意: 各種閾値を変更する場合は <agent-home>/skill-registry.json の skill_configs.gitlab-idd を設定する
# 例:
# {
#   "skill_configs": {
#     "gitlab-idd": {
#       "max_review_per_run": 3,          # 1回のレビュー実行で処理するイシューの最大件数（デフォルト 1）
#       "self_defer_minutes": 60,         # ワーカーの self-defer 猶予（分、デフォルト 60 = 1時間）
#       "self_review_lock_minutes": 1440, # レビュアーの self-review ロック（分、デフォルト 1440 = 24時間）
#       "assigned_lock_minutes": 1440     # 放置アサイン済イシューの引き受けロック（分、デフォルト 1440 = 24時間）
#     }
#   }
# }
```

---

## リクエスター — イシュー投稿

タスクを GitLab イシューとして投稿し、ワーカーへの実行を委譲する。
既存イシューへの参照がある場合は **派生イシュー投稿モード** に自動切り替えする。

**詳細手順** → [references/requester-post.md](references/requester-post.md)

### フロー概要

```
依頼に既存イシュー番号の参照がある
  → 派生イシュー投稿モード:
      1. 派生元イシューのターゲットブランチを取得
      2. タスクを整理・分割してユーザーに確認
      3. 派生元と同じターゲットブランチでイシュー作成
      4. 完了報告
依頼に既存イシュー番号の参照がない（通常投稿）:
      1. リポジトリのコンテキストを探索し、依頼の影響範囲を把握する
      2. できるだけ細かくタスクを自動分割する
         → 各タスクの修正量（変更ファイル数・行数）を見積もり、L/XL サイズは再分割する
         → 依存関係テーブル（# / タイトル / サイズ / 優先度 / 依存）をユーザーに提示・承認を得る
         → タスク 1 件: ターゲットブランチはデフォルトブランチ / タスク 2 件以上: 依頼内容から統合ブランチ名（feature/{スラグ}）を自動生成してユーザーに提示
      3. 各イシューの内容（目的・受け入れ条件・修正量見積もり・依存など）を整理してユーザーに一括確認
      4. タスク 2 件以上の場合: 統合ブランチをデフォルトブランチから派生して push（必須）
         依存なし or 依存完了済み → "status:open,assignee:any" でイシュー作成
         依存あり（未完了）       → "status:blocked" でイシュー作成
      5. 作成したイシュー一覧（番号・タイトル・ステータス・依存）をユーザーに報告して終了
```

---

## レビュアー — レビュー・クローズ / リオープン

`status:review-ready` イシューを全件取得し、**イシューの作成者が自分かどうかでスキル内部が自動的に振る舞いを切り替える**。

- **自分発行イシュー（リクエスターとして振る舞う）**: 受け入れ条件を評価し、マージ依頼 または リオープンする
- **他者発行イシュー（非リクエスターとして振る舞う）**: 助言コメントのみ投稿し、マージ・クローズは行わない
- **どちらもある場合**: 自分発行イシューを優先して先に処理する

**詳細手順** →
- 自分発行: [references/requester-review.md](references/requester-review.md)
- 他者発行: [references/non-requester-review.md](references/non-requester-review.md)

### フロー概要

```
0. MY_USER=$(python scripts/gl.py current-user --get username)
0a. 【クリーンアップ】自分発行のオープンイシューで MR がマージ/クローズ済みのものを検出してクローズする
1. (任意) status:needs-clarification のイシューがあれば詳細化して status:open,assignee:any に戻す
2. list-issues --label "status:review-ready" で全件取得（--author フィルタなし）
3. 取得したイシューを author.username で分類:
   - author.username == MY_USER → リクエスターレビューキュー（優先処理）
   - author.username != MY_USER → 非リクエスターレビューキュー
4. MAX=$(python scripts/gl.py get-max-review-per-run --get max_review_per_run)  # デフォルト 1
5. リクエスターレビューキューを処理（リクエスターとして振る舞う）:
   - 優先度順（priority:high → normal → low）で先頭 MAX 件を選択
   - check-review-defer で self-defer チェック
   - <!-- gitlab-idd:requester-approved:{NODE_ID} --> マーカーがあればスキップ（承認済み・人間のマージ待ち）
   - イシューコメントとブランチの成果物を確認
   - agent-reviewer で受け入れ条件を並列評価
   - 全観点 LGTM → add-comment（**@{作成者username} さん、マージをお願いします 🙏** + <!-- gitlab-idd:requester-approved:{NODE_ID} --> マーカー付与）+ update-issue（status:approved ラベルに変更）（マージは人間が行う）
   - Request Changes → add-comment（差し戻しコメント）+ reopen → 再提出後に再レビュー（最大 5 回）
   - (任意) スコープ外タスクを発見した場合は派生/新規イシューとして起票
6. 非リクエスターレビューキューを処理（非リクエスターとして振る舞う）:
   - 残り枠（MAX - リクエスターキューで処理した件数）を上限として選択
   - 残り枠が 0 の場合はスキップして終了
   - 3 段階スキップチェック: check-defer / check-review-defer / check-non-requester-review-defer
   - イシューコメントとブランチの成果物を確認
   - agent-reviewer で受け入れ条件を並列評価
   - 助言コメントを投稿（末尾に <!-- gitlab-idd:non-requester-reviewed:{NODE_ID} --> 付与）
   - merge-mr・close・ラベル変更は行わない
```

---

## ワーカー — イシュー取得・実行・報告

オープンイシューを取得して実装し、結果をブランチとコメントで返す。

**詳細手順** → [references/worker-role.md](references/worker-role.md)

### フロー概要

```
1. list-issues でオープンイシューを取得
2. self-defer チェック: 自分発行イシューは DEFER_MINUTES（デフォルト 60 分）経過後まで skip
3. 依存チェック: "## 依存イシュー" に記載のイシューがすべて done/closed か確認
   → 未完了の依存あり → スキップまたはコメントして終了
4. 説明の明確性チェック: 受け入れ条件・影響範囲が実装に十分か判断
   → 曖昧な場合 → 不明点をコメントし "status:needs-clarification" に更新して終了（着手しない）
5. イシューを自分に assign してロック（競合防止）
6. テンポラリ領域にリポジトリをクローンし feature/issue-{id} ブランチを作成
7. feature ブランチを push して空の Draft MR を作成（マージ後ブランチ削除設定つき）
8. 実装ループ（最大 5 回）
   └── skill-selector で選定 → 実装 → supporting_skills を適用 → agent-reviewer でレビュー → 修正
9. コミットを push し、MR 本文を更新して Draft を解除
10. イシューにサマリーコメント投稿 + ラベル "status:review-ready" に更新
```

---

## イシューラベル規約

| ラベル | 意味 |
|--------|------|
| `status:open` | ワーカー未着手（着手可） |
| `status:blocked` | 依存イシューが未完了のため着手不可 |
| `status:in-progress` | ワーカー実行中 |
| `status:review-ready` | 実装完了・レビュー待ち |
| `status:approved` | 受け入れ条件充足・マージ待ち（人間がマージするまでの状態） |
| `status:needs-rework` | リオープン済み・再作業必要 |
| `status:needs-clarification` | ワーカーが説明不明確と判断・リクエスターによる詳細化待ち |
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

## エラーハンドリング

| 状況 | 対応 |
|------|------|
| `GITLAB_TOKEN` 未設定 | `~/.bashrc` / `~/.profile` / `~/.bash_profile` / `~/.zshrc` から自動読み込みを試みる。見つからなければ設定を案内して終了 |
| `git remote origin` が存在しない | エラーメッセージを出して終了 |
| イシューが競合取得された | assign 後に自分が assignee か確認 → 違えば次のイシューへ |
| ブランチ競合 | `feature/issue-{id}-{hostname}-{slug}` にサフィックス追加 |
| 取得可能なイシューが 0 件 | 「実行可能なイシューはありません」と報告して終了 |
| イシューの説明・受け入れ条件が曖昧 | 不明点をコメントし `status:needs-clarification` に更新して終了（着手しない） |
| 自分発行イシューのみ・猶予期間中 | 猶予期間終了時刻を報告して終了 |
| 依存イシューが未完了 | イシューにコメントを投稿して次の候補へ（全候補ブロック中なら終了） |
| worker-node-id がないレビュー待ちイシュー | 誰でもレビュー可（self-defer しない） |
| レビュー対象が 0 件（または全件スキップ） | 「レビュー待ちイシューはありません」と報告して終了 |

---

## 行動指針

1. **LLM ポーリング禁止**: LLM セッション内での `sleep` ループや定期確認は実装しない。プロンプトで起動するたびに一回だけ実行する
2. **一気通貫**: 取得 → 実行 → 報告 → push を途中で止めない（破壊的操作を除く）
3. **受け入れ条件最優先**: イシュー作成時に `## 受け入れ条件` セクションを必ず含める
4. **最小往復**: 1 回のワーカー実行でリクエスターがマージ判断できる成果物を揃える
5. **レビュー実施**: ワーカーは agent-reviewer でレビューを行う。リクエスターは agent-reviewer で受け入れ条件を評価する
6. **self-defer 遵守**: ワーカーは自分が発行したイシューを猶予期間中は取得しない
7. **self-review ロック遵守**: リクエスター/レビュアーは自分実装のイシューをロック期間（デフォルト 24 時間）中はレビューしない
8. **nodeID なしレビュー許可**: `worker-node-id` が記録されていないイシューは誰でもレビューしてよい
9. **放置アサイン遵守**: `worker-node-id` が別ノードのものでロック期間内（デフォルト 24 時間）はそのイシューを引き受けない
10. **依存遵守**: ワーカーは `## 依存イシュー` に記載されたイシューが完了するまで着手しない
11. **自動タスク分解**: リクエスターはユーザーへの質問なしにリポジトリを探索してタスクを自動整理し、できるだけ細かく分割する。各タスクの修正量（変更ファイル数・行数）を見積もり、L（6〜10 ファイル / 201〜500 行）以上のタスクは必ず再分割する
12. **スキル優先活用**: レビュー時は環境で利用可能なスキルを自ら調べ、description を読んで適切なものを積極的に活用する
13. **GitLab Markdown 統一**: イシューコメント・MR 本文のテンプレートに流し込む文字列は必ず GitLab Markdown 形式で記述する。`##` 見出し・`**太字**`・`- 箇条書き`・` ``` コードブロック ``` `・`- [x] チェックボックス`・`` `インラインコード` `` を用い、プレーンテキストのまま流し込まない
14. **着手前明確性確認**: ワーカーはイシュー説明が実装に足りる詳細さかを必ず確認する。曖昧な場合は着手せずコメントで指摘し、リクエスターの詳細化を待つ
15. **曖昧イシュー詳細化**: リクエスターは `status:needs-clarification` イシューを発見または指示された場合、ワーカーの指摘に答えてイシュー本文・受け入れ条件を詳細化し `status:open,assignee:any` に戻す
16. **レビュー時の自動ロール判定**: `status:review-ready` イシューは全件取得し、`author.username == MY_USER` なら自分発行（リクエスターとして振る舞い）、そうでなければ他者発行（非リクエスターとして振る舞い）として処理する。自分発行を優先して先に処理する
17. **他者発行イシューへの非リクエスター振る舞い**: 他者が作成したイシューは 3 段階チェック（`check-defer` / `check-review-defer` / `check-non-requester-review-defer`）をパスしたもののみ助言コメントを投稿する。マージリクエストのマージおよびイシューのクローズ・ラベル変更は行わない
18. **non-requester-reviewed タグ必須**: 他者発行イシューをレビューした場合、コメント末尾に必ず `<!-- gitlab-idd:non-requester-reviewed:{NODE_ID} -->` を付与する
19. **マージは必ず人間が行う**: レビュー完了時に `merge-mr` は使用しない。代わりにイシュー作成者へ `@username` メンション付きで「**@{作成者username} さん、マージをお願いします 🙏**」とコメントし `<!-- gitlab-idd:requester-approved:{NODE_ID} -->` マーカーを付与する。ラベルを `status:approved` に変更して承認済み状態を可視化する。マージ後のイシュークローズはステップ 0 クリーンアップで自動処理する
20. **MR マージ済みイシューのクリーンアップ**: レビュー実行時に自分発行のオープンイシューを確認し、関連 MR が `merged` または `closed` 状態であればイシューをクローズする

## Permissions

- **Allowed**: `scripts/gl.py` の実行（Python）、ブランチの作成・push、イシューコメント投稿・ラベル更新（`status:blocked` → `status:open` の解除を含む）、MR の作成（リクエスターのみ）、助言コメント投稿（非リクエスターレビュアー）
- **Denied**: イシューの削除、force push、ユーザー確認なき既存 MR のクローズ、LLM セッション内でのポーリングループ実装、**merge-mr（マージは必ず人間が行う・全ロール禁止）**、非リクエスターレビュアーによる update-issue --state-event close
