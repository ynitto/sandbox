---
name: git-skill-manager
description: Gitリポジトリを使ってエージェントスキルを管理するスキル。スキルのpull/push・リポジトリ登録・有効化/無効化・プロファイル管理・フィードバック記録・改良・評価・発見・クロスリポジトリマージ・自動更新に対応。「スキルをpullして」「スキルをpushして」「リポジトリを登録して」「スキルを改良して」「スキルを評価して」などで発動。GitHub/GitLab/Bitbucket対応。
---

# Git Skill Manager

Gitリポジトリ経由でエージェントスキルの取得（pull）と共有（push）を行う管理システム。

## skill-recruiter との使い分け

**初めての外部URLからスキルを安全に取り込みたい場合は skill-recruiter を使ってください。**
skill-recruiter がライセンス・セキュリティ・ネットワーク通信を事前に検証し、
確認後にこのスキル（git-skill-manager）を自動で呼び出してインストールします。

このスキル（git-skill-manager）は以下の用途に特化しています:

| 操作 | 説明 |
|---|---|
| `repo add` / `pull` | 登録済みリポジトリからスキルを取得・更新 |
| `push` | ローカルスキルをチームリポジトリに共有 |
| `pin` / `lock` | バージョン固定・スナップショット |
| `enable` / `disable` / `profile` | スキルの有効化管理 |
| `feedback` / `promote` / `refine` | 評価・昇格・改良フロー |

## 利用者

| 呼び出し元 | 操作 | 例 |
|---|---|---|
| ユーザー直接 | repo add / pull / push / search / list / enable / disable / profile / promote / evaluate / refine / discover | 「スキルをpullして」「リポジトリを登録して」「スキルを無効化して」「スキルを昇格して」「スキルを評価して」「スキル候補を発見して」 |
| scrum-master サブエージェント | push / promote / evaluate / discover | Phase 6 のスキル共有・昇格・評価・発見時にテンプレート経由で起動される |

- ユーザー直接呼び出しの場合、対話的に確認しながら進める
- サブエージェント経由の場合、プロンプトに必要な情報（対象スキル・リポジトリ名・操作）が含まれるため、確認なしで実行する

## 動作環境

- **GitHub Copilot Chat**（Windows / macOS / Linux）および **Claude Code** で動作する
- git はインストール・認証設定済み（SSH鍵 or credential manager）
- シェルは実行環境に依存する（PowerShell、bash、zsh など）

-----

## アーキテクチャ

```
ローカル（Windows）
─────────────────────────────────────────
  %USERPROFILE%\.copilot\skills\          ← スキルインストール先
    ├── skill-a\SKILL.md
    ├── skill-b\SKILL.md  (enabled)
    └── skill-c\SKILL.md  (disabled → メタデータ非ロード)

  %USERPROFILE%\.copilot\skill-registry.json  ← レジストリ

  %USERPROFILE%\.copilot\cache\           ← リポジトリキャッシュ（永続）
    ├── team-skills\                      ← 初回clone、以降はfetch
    └── personal\
─────────────────────────────────────────
         │ pull              │ pull + push
         ▼                   ▼
  ┌────────────────┐  ┌────────────────┐
  │ repo: team     │  │ repo: personal │
  │ (readonly)     │  │ (read/write)   │
  │ priority: 1    │  │ priority: 2    │
  └────────────────┘  └────────────────┘
```

-----

## レジストリ

パス: `~/.copilot/skill-registry.json`（Windows: `%USERPROFILE%\.copilot\skill-registry.json`）

スキルの登録情報（リポジトリ・インストール済みスキル・プロファイル・フィードバック履歴）を管理するJSONファイル。初回操作時に自動作成する。

詳細なスキーマとフィールド説明 → [references/registry-schema.md](references/registry-schema.md)

-----

## 操作一覧

|操作             |トリガー例               |
|---------------|--------------------|
|**repo add**   |「リポジトリを登録して」        |
|**repo list**  |「登録リポジトリ一覧」         |
|**repo remove**|「リポジトリを削除して」        |
|**pull**       |「スキルをpullして」「スキルを取得」|
|**push**       |「スキルをpushして」「スキルを共有」|
|**list**       |「インストール済みスキル一覧」     |
|**search**     |「リポジトリにあるスキルを探して」   |
|**search --refresh**|「最新のスキルを検索して」  |
|**enable**     |「スキルを有効化して」         |
|**disable**    |「スキルを無効化して」         |
|**pin**        |「スキルを固定して」「バージョンをpinして」|
|**unpin**      |「スキルの固定を解除して」       |
|**lock**       |「全スキルをロックして」        |
|**unlock**     |「全スキルのロックを解除して」     |
|**promote**    |「このスキルを他でも使えるようにして」「スキルを昇格して」|
|**profile use**|「プロファイルを切り替えて」      |
|**profile create**|「プロファイルを作成して」    |
|**profile list**|「プロファイル一覧」         |
|**profile delete**|「プロファイルを削除して」    |
|**feedback**   |「フィードバックを記録して」「良かった/改善したい/うまくいかなかった」|
|**refine**     |「スキルを改良して」「フィードバックを反映して」「改善待ちを処理して」|
|**discover**   |「スキル候補を探して」「履歴からスキルを発見して」「新しいスキルを見つけて」|
|**evaluate**   |「スキルを評価して」「試用中スキルを確認して」「ワークスペーススキルを整理して」|
|**diff**       |「スキルの差分を見せて」「リポジトリ間の違いを確認して」|
|**sync**       |「マージしたスキルを全リポジトリに配信して」「スキルを同期して」|
|**merge**      |「スキルをマージして」「リポジトリ間のスキルを統合して配信して」|
|**auto-update**|「自動更新を有効化して」「更新チェックして」「自動更新の設定を見せて」|

-----

## パス定義

すべての操作で以下のパスを使う。

```powershell
$SKILL_HOME   = "$env:USERPROFILE\.copilot\skills"
$REGISTRY     = "$env:USERPROFILE\.copilot\skill-registry.json"
$CACHE_DIR    = "$env:USERPROFILE\.copilot\cache"
```

初回は `$SKILL_HOME` と `$CACHE_DIR` ディレクトリを作成する:

```powershell
if (-not (Test-Path $SKILL_HOME)) { New-Item -ItemType Directory -Path $SKILL_HOME -Force }
if (-not (Test-Path $CACHE_DIR))  { New-Item -ItemType Directory -Path $CACHE_DIR -Force }
```

-----

## repo add

```powershell
# 接続確認
git ls-remote $REPO_URL HEAD

# 成功したらレジストリに追加
```

→ 実装: `scripts/registry.py` — `load_registry()`, `save_registry(reg)`, `scripts/repo.py` — `add_repo()`

-----

## pull

### 処理フロー

→ 実装: `scripts/repo.py` — `clone_or_fetch(repo)`, `update_remote_index()`、`scripts/pull.py` — `pull_skills()`

主要なロジック:
- `clone_or_fetch`: キャッシュ有 → `git fetch + reset`（高速）、キャッシュ破損 → 削除して再clone
- `pull_skills`: 全リポジトリからスキル候補を収集 → 競合解決（対話 or priority自動） → pinned_commit 対応 → コピー → レジストリ更新

-----

## push

### 処理フロー

→ 実装: `scripts/push.py` — `push_skill(skill_path, repo_name, branch_strategy, commit_msg)`

一時ディレクトリにクローン → スキルフォルダをコピー → 不要ファイル除外 → commit & push。`branch_strategy="new_branch"` でブランチを切り PR/MR を作成するフローを推奨。

-----

## list

→ 実装: `scripts/manage.py` — `list_skills()`、`scripts/registry.py` — `is_skill_enabled()`

インストール済みスキルの一覧を表示。有効/無効、ソースリポジトリ、コミットハッシュ、pin状態を表示する。

-----

## search

デフォルトではレジストリ内の `remote_index` を検索する（ネットワーク不要、即座に結果を返す）。
`--refresh` 指定時はリモートから最新情報を取得してインデックスを更新してから検索する。
インデックスが空の場合（初回）は自動的に `--refresh` と同様の動作をする。

→ 実装: `scripts/manage.py` — `search_skills(repo_name, keyword, refresh)`

-----

## enable / disable

スキルの有効・無効を切り替える。無効化されたスキルはディスク上に残るが、`discover_skills.py` のメタデータ収集から除外される（コンテキストウィンドウを節約）。

→ 実装: `scripts/manage.py` — `enable_skill(skill_name)`, `disable_skill(skill_name)`

-----

## pin / unpin

スキルを特定のコミットハッシュに固定する。pin されたスキルは pull 時にそのコミットの内容を取得し、新しいバージョンには更新されない。

→ 実装: `scripts/manage.py` — `pin_skill(skill_name, commit)`, `unpin_skill(skill_name)`

-----

## lock / unlock

全インストール済みスキルのバージョンを一括で固定・解除する。チームで同じバージョンのスキルセットを共有するときに使う。

→ 実装: `scripts/manage.py` — `lock_all()`, `unlock_all()`

-----

## promote

ワークスペース内（`$workspace/.github/skills/`）のスキルをユーザー領域（`~/.copilot/skills/`）にコピーし、リポジトリにも push する。プロジェクト固有でないスキルを他のプロジェクトでも再利用可能にする。

### 処理フロー

→ 実装: `scripts/manage.py` — `promote_skills(workspace_skills_dir, interactive)`

1. ワークスペース内スキルをスキャン
2. ユーザーに候補を提示して選択させる
3. 選択されたスキルをユーザー領域にコピー + レジストリ登録
4. 書き込み可能なリポジトリがあれば push を提案

-----

## ワークスペーストライアルフロー

VSCode チャット経由で作成されたスキルは `.github/skills/` に置かれ、試用してから昇格する。

ライフサイクル・評価フロー詳細 → [references/workspace-trial.md](references/workspace-trial.md)

-----

## フィードバックループと record_feedback

スキル使用後にフィードバックを収集し、スキル品質の改良トリガーとスキル発見の起点にする仕組み。

しきい値・スクリプト呼び出し・ソート順の詳細 → [references/feedback-loop.md](references/feedback-loop.md)

-----

## refine

蓄積されたフィードバックをもとに、スキルの改良フローを開始する。ワークスペーススキルとインストール済みスキル（user-space / リポジトリ管理）の両方に対応する。

### 処理フロー

→ 実装: `scripts/manage.py` — `refine_skill(skill_name)`, `mark_refined(skill_name)`

1. `feedback_history` から未処理（`refined: false`）の `needs-improvement` / `broken` エントリを収集
2. フィードバック一覧とスキルパスをユーザーに提示
3. skill-creator サブエージェントを起動して改良を委譲（スキルパスを渡す）
4. 改良完了後、`mark_refined` で `pending_refinement` を false に更新
5. インストール済みスキルかつ source_repo がリポジトリの場合は push を提案

### スキルパスの違い

| スキル種別 | 編集対象パス |
|---|---|
| ワークスペーススキル | `.github/skills/<name>/` |
| インストール済みスキル | `~/.copilot/skills/<name>/` |

`refine_skill()` はスクリプト出力に `スキルパス: <path>` を含むため、エージェントはそれを参照して skill-creator に正しいパスを渡す。

```
ユーザー: 「docx-converter を改良して」

エージェント:
  1. python manage.py refine docx-converter
  2. フィードバック一覧とスキルパスを表示
  3. skill-creator に改良を委譲（表示されたパスを渡す）
  4. 改良後、リポジトリ管理スキルなら push を提案
```

-----

## diff / sync / merge

複数リポジトリに分岐した同名スキルを比較・統合・配信するクロスリポジトリ操作。

| 操作 | 用途 |
|---|---|
| `diff` | リポジトリ間の差分を表示（マージ前確認） |
| `sync` | マージ済みスキルを複数リポジトリへ一括 push |
| `merge` | diff → skill-creator → sync を一括実行 |

詳細な処理フローと出力例 → [references/cross-repo-ops.md](references/cross-repo-ops.md)

-----

## discover

`generating-skills-from-copilot-logs` を起動し、直近のチャット履歴から新しいスキル候補を発見する。

### 処理フロー

→ 実装: `scripts/manage.py` — `discover_skills_from_history(since, workspace)`

1. ユーザーに `--since` パラメータ（分析開始日時）を確認
2. ユーザーに同意を確認:
   ```
   「指定期間のチャット履歴を分析して新しいスキル候補を探します。
    続行しますか？」
   ```
   （ここで同意を取得済みのため、`generating-skills-from-copilot-logs` の Phase 1 同意確認はスキップしてよい）
3. `discover_skills_from_history()` を実行（コマンドを出力）
4. `generating-skills-from-copilot-logs` のフェーズ 2〜6 に従って分析・スキル生成（Phase 1 の同意確認は不要）

-----

## feedback

直前に実行したスキルの満足度をユーザーに確認し、レジストリに記録する。
スキル単体起動後に `copilot-instructions.md` の指示で自動的に呼ばれる。

→ 実装: `scripts/record_feedback.py`

1. 対象スキル名を確認（不明な場合はユーザーに確認）
2. ユーザーに確認:
   ```
   「[スキル名] の実行はいかがでしたか？
    1. 問題なかった (ok)
    2. 改善点がある (needs-improvement)
    3. うまくいかなかった (broken)」
   ```
3. `python record_feedback.py <name> --verdict <verdict> --note <note>` を実行
4. 出力に `EVAL_RECOMMEND: promote|refine` が含まれる場合は `evaluate` 操作へ進む

-----

## evaluate

ワークスペーススキル（`source_repo: "workspace"`）の昇格推奨度を評価する。`skill-evaluator` スキルを呼び出して実行する。

### トリガー

| トリガー | 説明 |
|---|---|
| `record_feedback.py` の `EVAL_RECOMMEND: promote\|refine` 出力 | フィードバック記録後にインラインで自動起動 |
| scrum-master Phase 6 | スプリント完了時のバッチ棚卸し |
| ユーザー直接 | 「スキルを評価して」など |

### 処理フロー

→ 実装: `.github/skills/skill-evaluator/scripts/evaluate.py`（skill-evaluator スキルが管理）

1. `skill-evaluator` サブエージェントを起動する:
   ```
   skill-evaluator スキルでワークスペーススキルを評価する。
   手順: まず .github/skills/skill-evaluator/SKILL.md を読んで手順に従ってください。
   ```
2. skill-evaluator が評価結果を提示し、promote / refine のアクションをユーザーに確認する
3. 「昇格する」→ `promote` 操作を実行する
4. 「改良する」→ `refine` 操作を実行する

-----

## profile

プロファイルはスキルの有効・無効を一括で切り替えるショートカット。プロファイルをアクティブにすると、そのプロファイルに含まれるスキルのみがコンテキストにロードされる。

→ 実装: `scripts/manage.py` — `profile_create()`, `profile_use()`, `profile_list()`, `profile_delete()`

-----

## auto-update

セッション開始時やユーザーの指示で、リポジトリの更新を自動チェックする機能。デフォルトは無効。
セッション開始時のトリガーは `.github/copilot-instructions.md` で定義されている。

→ 実装: `scripts/auto_update.py` — `run_auto_update()`, `check_updates()`, `configure_auto_update()`

動作モード・設定操作・チェック操作の詳細 → [references/auto-update.md](references/auto-update.md)

-----

## エラーハンドリング

|エラー               |対処                         |
|------------------|---------------------------|
|`git ls-remote` 失敗|URL・認証を確認するよう案内            |
|clone 失敗          |ブランチ名を `git ls-remote` で確認 |
|fetch 失敗（キャッシュ破損）|キャッシュを削除して再clone          |
|push to readonly  |readonlyリポジトリへのpush拒否を通知。別リポジトリを提案する|
|push rejected     |`git pull --rebase` 後に再push|
|SKILL.md なし       |スキルフォルダの構成確認を案内            |
|レジストリ破損           |削除して再作成するか、リポジトリから再pull    |
|ネットワークエラー         |ネットワーク接続を確認するよう案内          |

-----

## 使用例

操作ごとのエージェント対話例 → [references/examples.md](references/examples.md)
