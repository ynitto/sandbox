# 状態専用リポジトリへの移行手順（案1）

> 参照設計: [`docs/plans/2026-07-21-agent-dashboard-production-hardening-plan.md`](../plans/2026-07-21-agent-dashboard-production-hardening-plan.md) 案1。
> **本手順は「コードと手順は用意・適用は保留」の段階。** 既存プロジェクトへの適用は
> 監視者がプロジェクトのアイドル時に、下記に従って明示的に実施する。旧構成の削除は
> 安定を確認してから手動で行う（自動削除はしない）。

## 何を変えるのか

これまで状態（`.agent-project` 一式: backlog / needs / decisions / journal など）は、
成果物リポジトリの `agent-state` ブランチを **worktree**（`<repo>-agent-state`）に逃がして
管理していた。この方式は次の問題を抱えていた（本番運用ハードニング計画 P1・P5）:

- worktree の生成・パス解決が Python(エンジン)と JS(dashboard)の**二重実装**で、
  Windows/WSL のパス差により壊れやすい。
- `backup_state` が状態を成果物 `main` へミラーし続け、**ドリフト**と履歴肥大を生む。

**状態専用リポジトリ方式**は、状態を成果物とは別の**専用リポジトリの通常 clone**に置く。
worktree も sparse-checkout も main へのバックアップも要らなくなる。専用リポジトリの clone は
普通の git リポジトリなので、既存の direct 同期（`DirectStateGit`）がそのまま使える。

## 前提

- 状態専用リポジトリを1つ用意する（Gitea/GitLab の**空リポジトリ**で可）。
  プロジェクトごとに1つ（例: 成果物が `app` なら `app-state`）。
- 成果物リポジトリに既存の `agent-state` ブランチがある（これまで運用してきた状態）。

## 手順

### 1. 状態を専用リポジトリへ移す

移行スクリプトで、既存 `agent-state` ブランチの内容（履歴ごと）を専用リポジトリへ push する。
これはコピーで、成果物側の `agent-state` ブランチや `<repo>-agent-state` フォルダは消さない。

```bash
# まず dry-run で実行内容を確認
bash tools/agent-project/migrate-state-repo.sh \
  --source /path/to/app \
  --state-repo https://gitea.example/you/app-state.git \
  --dry-run

# 問題なければ本実行
bash tools/agent-project/migrate-state-repo.sh \
  --source /path/to/app \
  --state-repo https://gitea.example/you/app-state.git
```

既定では成果物側 `agent-state` を専用リポジトリの `main` へ移す。ブランチ名を変えたいときは
`--source-branch` / `--dest-branch` を使う。専用リポジトリに既存内容があると非 fast-forward で
止まる（意図せぬ上書き防止）。

### 2. エンジン設定を切り替える

`agent-project.yaml`（成果物リポジトリ側の設定。全 PC 共有）に次を追加する:

```yaml
state_repo: https://gitea.example/you/app-state.git   # 専用リポジトリ URL（全 PC 共有）
state_repo_branch: main                                # --dest-branch と一致させる
```

CLI や環境で個別に上書きもできる（PC 毎に clone 先を変えたいとき）:

- `--state-repo <URL>` / `--state-repo-branch <branch>`
- `--state-repo-dir <path>`（clone 先。既定は `<成果物repo>-agent-state` の隣）

エンジンを再起動すると、状態は専用リポジトリの通常 clone（既定 `<repo>-agent-state`）に置かれ、
`backup_state`（成果物 main へのミラー）は自動的に無効になる。**専用リポジトリの clone に失敗した
場合は、従来の worktree 方式へ自動フォールバックする**（状態を本体 dirty にしない）。

### 3. 各 PC を切り替える

各 PC は専用リポジトリを clone し直して dashboard に登録する（成果物リポジトリの clone は
検収 diff 用に併存してよい）。閲覧のみの PC は「専用リポジトリを clone + dashboard に登録」で
完結する。

### 4. 安定を確認してから旧構成を削除（手動）

数日〜1週間ほど通常運用し、状態が専用リポジトリで正しく同期・検収できることを確認してから、
以下を**人が手動で**削除する（自動削除はしない）:

- 成果物リポジトリの `agent-state` ブランチ
- `<repo>-agent-state` worktree フォルダ（`git worktree remove` → フォルダ削除）

## 履歴のリセット（任意・年数回の運用）

状態専用リポジトリの履歴が肥大したら、現在のツリーだけを残して履歴を積み直す:

```bash
cd app-agent-state
git checkout --orphan fresh
git add -A && git commit -m "状態履歴リセット（現時点のスナップショット）"
git branch -D main && git branch -m main
git push -f origin main
```

状態は「現在の状態」だけが意味を持つので、履歴リセットで成果物に影響はない
（成果物リポジトリとは分離されている）。実施はプロジェクトのアイドル時に監視者が行う。

## ロールバック

`agent-project.yaml` から `state_repo` を消してエンジンを再起動すれば、従来の worktree 方式に
戻る（旧 `agent-state` ブランチと `<repo>-agent-state` を削除していなければそのまま復帰できる。
これが「安定を確認してから削除」を推奨する理由）。
