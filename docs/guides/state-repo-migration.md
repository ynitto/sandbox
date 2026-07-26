# 状態専用リポジトリへの移行手順

> 参照設計: [`docs/plans/2026-07-21-agent-dashboard-production-hardening-plan.md`](../plans/2026-07-21-agent-dashboard-production-hardening-plan.md) 案1 /
> [`docs/plans/2026-07-26-s1-config-two-layer-detailed-design.md`](../plans/2026-07-26-s1-config-two-layer-detailed-design.md)（S1）。
>
> **状態専用リポジトリは唯一の方式になった（S1）。** 旧 worktree 方式のキーが残っていると
> 起動時に本手順を案内して停止する。適用は監視者がプロジェクトのアイドル時に行い、旧構成の
> 削除は安定を確認してから手動で行う（自動削除はしない）。

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

### 1. 状態を専用リポジトリへ移す（状態だけ・ルート直下）

移行スクリプトは、**状態エントリ**（backlog / needs / decisions / charter / charters /
project.json / journal など）だけを専用リポジトリの**ルート直下**へ 1 コミットで置く。
成果物ファイルは混ぜない。元の状態フォルダや worktree は消さない（安定確認後に手動削除）。

`--state-dir` には「`backlog/` や `project.json` がある**実際の状態フォルダ**」を渡す:

- worktree 運用なら通常 `<repo>-agent-state`（sparse なら `<repo>-agent-state/.agent-project`）。
- 本体同居なら `<repo>/.agent-project` か `<repo>` 直下。
- 迷ったら `ls` して `backlog/` が直下にあるフォルダを指定する。

```bash
# まず dry-run で「何を移すか」を確認
bash tools/agent-project/migrate-state-repo.sh \
  --state-dir /path/to/app-agent-state \
  --state-repo https://gitea.example/you/app-state.git \
  --dry-run

# 問題なければ本実行
bash tools/agent-project/migrate-state-repo.sh \
  --state-dir /path/to/app-agent-state \
  --state-repo https://gitea.example/you/app-state.git
```

既定の移行先ブランチは `main`（`--dest-branch` で変更可）。**空リポジトリの既定ブランチ**が
`main` でないと、普通の `git clone` が空チェックアウトになる（エンジンは `--branch` 付き clone
なので影響しない）。ローカルの bare ならスクリプトが自動で直し、リモート（Gitea/GitLab）は
「既定ブランチを `main` に設定してください」と促す。

> **なぜ「状態だけ・ルート直下」か**: 旧 `agent-state` ブランチをそのまま push すると、
> 成果物リポジトリの全ファイルが混ざり、さらに状態が `<rel>` サブディレクトリに入って、
> エンジン（clone のルートを状態ルートとして読む）と場所が食い違う。結果、**バージョン情報
> などが引き継がれない**。状態エントリだけをルート直下に並べれば確実に読める。

### 2. host.yaml に宣言する（設定の 2 層・S1）

状態リポジトリの URL と **このノードでの clone 先** は、各 PC の
`~/.agents/agent-project.host.yaml` が唯一の置き場。`state_repo:` は状態 clone を作る**前**に
読める必要があるので、状態リポジトリの中に書いても意味を持てない。

```yaml
# ~/.agents/agent-project.host.yaml（この PC の宣言。共有しない）
schema_version: 1
node_id: pc-a
projects:
  - name: app
    state_repo: https://gitea.example/you/app-state.git   # --dest-branch と一致させる
    branch: main
    root: /home/me/agents/app-state    # このノードでの clone 先（絶対パス・無ければ自動 clone）
```

運用設定（`planner` / `level` / `plan_review` / 予算など「プロジェクトとしてどう進めるか」）は
**状態リポジトリの clone 直下** `agent-project.yaml` に移す。ここに置いたものが全 PC で共有される。

```yaml
# <状態 clone>/agent-project.yaml（全 PC 共有）
workdir: work
watch: true
plan_review: true
```

`agent-project serve`（または `agent-project run --project app`）を起動すると、宣言した
`root` に状態リポジトリが無ければ自動 clone され、あれば `origin` の一致を検査してから使う。

> **移行の取り違えはすべて起動時に止まる。** 旧 root（成果物リポジトリ）をそのまま宣言すると
> 「状態ルートに見えません」で停止し、そこに残った `state_repo:` 入り yaml を見つけたら
> その URL を案内に含める。origin が食い違うディレクトリを指した場合も停止する
> （旧実装はここで黙って worktree 方式へ倒れ、移行が効いていないことに気付けなかった）。

**書いてはいけない場所**（起動時にエラーで止まる）:

- 共有 `agent-project.yaml` に `node_id` / `projects` / `repos` / `availability` / `budget` /
  `board_workdir` / `update_*` などノード固有のキー — state repo 経由で全 PC へ配られて壊れる
- host.yaml の `defaults:` / `projects[].overrides:` に計画・予算・収束・検証系のキー —
  ノードごとに食い違うと実行が非決定になる

両方に書けるのは `agent_cli` / `model` / `act_timeout` / `verify_timeout` / `location` /
`concurrency` / `agent_timeout` / `actor` / `notify_cmd` / `ltm_home` / `flow_config` /
`verify_cwd` だけ（優先順位: CLI > `overrides` > `defaults` > プロジェクト yaml > 既定）。

### 3. 各 PC を切り替える（clone は agent-project・dashboard はパス解決のみ）

- **エンジンを動かす PC**: 上記の host.yaml 宣言＋再起動だけでよい。エンジン（agent-project）が
  `projects[].root` へ**自動 clone** する（手動 clone 不要）。
- **dashboard の登録**: 状態 clone（`projects[].root`）を登録する。**状態リポジトリの git clone
  自体は dashboard では行わず agent-project に任せる。**
- **閲覧のみ（viewer）の PC**: `git clone <state_repo> …` で状態 clone を置き、それを登録する
  （viewer にはエンジンが居ないので手動 clone が必要）。WSL/CLI 設定は不要
  （⚙ 設定の役割を viewer にすると本体起動ボタンも隠れる）。

> 成果物の diff（検収）は成果物リポジトリの `origin/<branch>` を fetch して見る。手元にクローンが
> あるなら host.yaml の `repos[]` に宣言しておくと、ミラーを取り直さずそれを使う（S3）:
> ```yaml
> repos:
>   - url: https://gitea.example/you/app.git
>     local: /home/me/mirrors/app
> ```

### 4. 安定を確認してから旧構成を削除（手動）

数日〜1週間ほど通常運用し、状態が専用リポジトリで正しく同期・検収できることを確認してから、
以下を**人が手動で**削除する（自動削除はしない）:

- 成果物リポジトリの `agent-state` ブランチ
- 旧 `<repo>-agent-state` worktree フォルダ（`git worktree remove` → フォルダ削除）
- 成果物リポジトリ直下の旧ブートストラップ `agent-project.yaml`（もう読まれない）

### 廃止したキーと移行先

| 旧キー / フラグ | 挙動 | 移行先 |
|---|---|---|
| `state_worktree_dir` / `state_branch` / `state_commit` / `state_push` / `state_backup_branch` | **起動を止める** | 状態専用リポジトリ方式へ移行（本手順） |
| `state_repo` / `state_repo_branch` | 警告して無視 | host.yaml `projects[].state_repo` / `.branch` |
| `state_repo_dir` / `--state-repo-dir` | 警告して無視 / **止める** | host.yaml `projects[].root` |
| `root:`（プロジェクト yaml） | 警告して無視 | ファイルの置き場所そのものが root |
| `state_git` | 警告して無視 | 状態 clone の origin（設定不要） |
| `state_commit_interval` | 警告して無視 | `state_git_interval` に一本化 |
| `--profile` / `~/.agents/agent-project/profiles/` | **止める** | host.yaml（`root`→`projects[].root` / `node`→`node_id` / `availability`→`availability` / `project_config`→廃止） |
| `.agents/` `.agent/` `~/.agents/` の設定探索 | 読まずに警告 | 状態ルート直下のみ（`--config` 明示は可） |

## よくある質問（移行でつまずいた点）

- **状態専用リポジトリに成果物ファイルが全部入る** → 旧スクリプトの挙動。現行スクリプトは
  状態エントリだけをルート直下に置くので混ざらない。移行し直すには、空の専用リポジトリに
  対して現行スクリプトを再実行する。
- **再起動しても専用リポジトリが使われない/バージョン情報が引き継がれない** → 旧既定の
  clone 先 `<repo>-agent-state` が旧 worktree と同名で、旧 worktree を掴んでいた。現行は
  `<repo>-state` に clone する。旧構成が `app-agent-state` に残っていても衝突しない。
- **dashboard にどのフォルダを登録する？** → 状態 clone（host.yaml の `projects[].root`）を
  登録する。dashboard は clone しない。
- **手動 clone は必要？** → エンジン PC は不要（agent-project が `projects[].root` へ自動 clone）。
  閲覧専用 PC は `git clone` 1 回。
- **設定を書いたのに効かない** → 置き場所を確認する。プロジェクトの合意は**状態 clone 直下**の
  `agent-project.yaml`、ノード固有の宣言は `~/.agents/agent-project.host.yaml`。旧探索先
  （`./.agents/` `./.agent/` `~/.agents/agent-project.yaml`）は読まれず、起動時に名指しで警告する。
- **起動が「状態ルートに見えません」で止まる** → 成果物リポジトリを root に宣言している。
  `projects[].root` を状態 clone のパス（例 `<repo>-state`）へ直す。

## 履歴のリセット（任意・年数回の運用）

状態専用リポジトリの履歴が肥大したら、現在のツリーだけを残して履歴を積み直す:

```bash
cd app-state
git checkout --orphan fresh
git add -A && git commit -m "状態履歴リセット（現時点のスナップショット）"
git branch -D main && git branch -m main
git push -f origin main
```

状態は「現在の状態」だけが意味を持つので、履歴リセットで成果物に影響はない
（成果物リポジトリとは分離されている）。実施はプロジェクトのアイドル時に監視者が行う。

## ロールバック

worktree 方式へは戻せない（S1 で廃止した）。移行中に問題が出たときは、**旧 `agent-state`
ブランチと `<repo>-agent-state` を消さずに残しておき**、状態リポジトリを作り直して手順 1 から
やり直す（旧構成には状態のスナップショットがそのまま残っているので、そこから再度移行できる）。
これが「安定を確認してから削除」を推奨する理由。
