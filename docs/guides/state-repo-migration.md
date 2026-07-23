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

### 2. エンジン設定を切り替える

`agent-project.yaml`（全 PC 共有）に次を追加する:

```yaml
state_repo: https://gitea.example/you/app-state.git   # 専用リポジトリ URL（全 PC 共有）
state_repo_branch: main                                # --dest-branch と一致させる
```

CLI/環境での個別上書きも可: `--state-repo` / `--state-repo-branch` / `--state-repo-dir`。
`--state-repo-dir` は**成果物top の親を基準**に解決する（相対なら親配下、絶対ならそのまま）。
例: 成果物が `/home/me/src/app` で `state_repo_dir: mystate` → `/home/me/src/mystate`。既定は
`<repo>-state`（この例では `/home/me/src/app-state`）。

エンジンを再起動すると、状態は専用リポジトリの通常 clone に置かれ、`backup_state`（成果物 main
へのミラー）は自動的に無効になる。**clone 先は既定で `<repo>-state`**（例: `app` → `app-state`）。
これは**旧 worktree `<repo>-agent-state` とは別フォルダ**で、衝突しない（同名だと旧 worktree を
掴んで移行が効かなかった不具合を修正済み）。既存フォルダがあっても、その `origin` が `state_repo`
と一致しなければ再利用せず worktree 方式へフォールバックし、理由を表示する（黙って旧構成を使わない）。

### 3. 各 PC を切り替える（clone は agent-project・dashboard はパス解決のみ）

- **エンジンを動かす PC**: 上記の設定＋再起動だけでよい。エンジン（agent-project）が専用
  リポジトリを `<repo>-state` へ**自動 clone** する（手動 clone 不要）。
- **dashboard の登録**: 成果物リポジトリを登録すればよい。dashboard は
  `.agents/agent-project.yaml`（または直下の `agent-project.yaml`）の `state_repo` /
  `state_repo_dir` から状態 clone パスを解決し、そこをプロジェクトルートとして開く。
  **状態リポジトリの git clone 自体は dashboard では行わず agent-project に任せる。**
  エンジンと同じ PC なら、作られた `<repo>-state` を自動で見つける。状態 clone を直接
  登録する従来のやり方もそのまま使える。
- **閲覧のみ（viewer）の PC**: 成果物リポジトリを clone して同じ `agent-project.yaml`
  （`state_repo` / `state_repo_dir`）を置き、隣に `git clone <state_repo> …` で状態
  clone を置く（viewer にはエンジンが居ないので手動 clone が必要）。または状態 clone
  だけを登録してもよい。WSL/CLI 設定は不要（⚙ 設定の役割を viewer にすると本体起動
  ボタンも隠れる）。

> 成果物の diff（検収）は従来どおり成果物リポジトリの `origin/<branch>` を fetch して見るため、
> 検収 diff 用に成果物リポジトリの clone を併存させてもよい（必須ではない）。

### 4. 安定を確認してから旧構成を削除（手動）

数日〜1週間ほど通常運用し、状態が専用リポジトリで正しく同期・検収できることを確認してから、
以下を**人が手動で**削除する（自動削除はしない）:

- 成果物リポジトリの `agent-state` ブランチ
- 旧 `<repo>-agent-state` worktree フォルダ（`git worktree remove` → フォルダ削除）

新 clone は `<repo>-state`、旧 worktree は `<repo>-agent-state` と**名前が別**なので、旧構成を
残したまま新構成へ切り替えられる（ロールバックの保険になる）。

## デーモン起動時のカレントパス（cwd）

エンジンは起動時に **cwd → `cwd/.agent/` → `~/.agent/`** の順で `agent-project.yaml` を探す
（`--config` 明示が最優先）。`state_repo:` はこの設定から読むので、**状態リポジトリを clone する
前に読める場所**に無いといけない（状態 clone の中だけに置くと、clone する前に読めず起動できない）。

したがって:

- **cwd = 成果物（deliverable）リポジトリ**にして起動するのが基本。そこに最小の
  `agent-project.yaml` を置く:

  ```yaml
  # <成果物repo>/agent-project.yaml（起動のブートストラップ。state_repo はここで読む）
  root: .                       # cwd（成果物repo）を root に。source_root として state_top に使う
  state_repo: https://gitea.example/you/app-state.git
  state_repo_branch: main
  # 他の運用設定（planner / level / gitlab 等）もここに書いてよい
  ```

  ```bash
  cd /path/to/app            # 成果物リポジトリ
  agent-project start        # ここを cwd に。状態は自動で app-state に clone される
  ```

- **cwd に依存させたくないデーモン**（systemd / Windows Task Scheduler / wsl-launcher 等）では、
  cwd を当てにせず**絶対パスで明示**する:

  ```bash
  agent-project start \
    --root /home/me/src/app \
    --state-repo https://gitea.example/you/app-state.git \
    --state-repo-branch main
  # あるいは --config /home/me/src/app/agent-project.yaml を渡す
  ```

  `--state-repo` を CLI で渡せば設定ファイルの発見に一切依存しない。`--root` は成果物
  リポジトリの絶対パス（`state_top` に使われ、検収 diff の解決に必要）。

> 補足: `charter.md` / `repos.json` / `backlog/` などの状態本体は状態リポジトリ（`app-state`）側に
> あり、clone 後にそこから読まれる。cwd の `agent-project.yaml` は「どの状態リポジトリを使うか」を
> 伝えるブートストラップとして必要（状態 clone の中の設定は起動時には読まれない）。

### `agent-project.yaml` は両方に置くのか？ → いいえ

エンジンが実際に読む `agent-project.yaml` は **cwd（成果物リポジトリ or `~/.agent`）の 1 つだけ**。
これを正として編集する。状態リポジトリ側に同名ファイルを置いても**起動時には読まれない**
（設定は状態 clone より前に読むため）。したがって:

- 設定は cwd 側（成果物repo の `agent-project.yaml`、または `~/.agent/agent-project.yaml`）に置く。
- 移行スクリプトは `agent-project.yaml` / `agent-flow.yaml` を状態リポジトリへ**コピーしない**
  （混乱を避けるため）。`state_repo:` を含むこのブートストラップ設定は、移行時に人が cwd 側へ
  用意する（旧設定に `state_repo:` を足すだけ）。
- `agent-flow.yaml`（agent-flow デーモンの設定）も同様に cwd 側／`--config` で渡す。

## よくある質問（移行でつまずいた点）

- **状態専用リポジトリに成果物ファイルが全部入る** → 旧スクリプトの挙動。現行スクリプトは
  状態エントリだけをルート直下に置くので混ざらない。移行し直すには、空の専用リポジトリに
  対して現行スクリプトを再実行する。
- **再起動しても専用リポジトリが使われない/バージョン情報が引き継がれない** → 旧既定の
  clone 先 `<repo>-agent-state` が旧 worktree と同名で、旧 worktree を掴んでいた。現行は
  `<repo>-state` に clone する。旧構成が `app-agent-state` に残っていても衝突しない。
- **dashboard にどのフォルダを登録する？** → 成果物リポジトリ（`state_repo` 付き yaml がある方）
  でよい。dashboard が状態 clone（`<repo>-state`）をルートとして解決する。状態 clone の
  直接登録も可。
- **手動 clone は必要？** → エンジン PC は不要（agent-project が自動 clone。dashboard は
  clone しない）。閲覧専用 PC は `git clone` 1 回。

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

`agent-project.yaml` から `state_repo` を消してエンジンを再起動すれば、従来の worktree 方式に
戻る（旧 `agent-state` ブランチと `<repo>-agent-state` を削除していなければそのまま復帰できる。
これが「安定を確認してから削除」を推奨する理由）。
