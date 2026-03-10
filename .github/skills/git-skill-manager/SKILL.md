---
name: git-skill-manager
description: Gitリポジトリを使ってエージェントスキルを管理するスキル。スキルのpull/push・リポジトリ登録・有効化/無効化・プロファイル管理・フィードバック記録・改良・評価・発見・クロスリポジトリマージ・自動更新に対応。「スキルをpullして」「スキルをpushして」「リポジトリを登録して」「スキルを改良して」「スキルを評価して」「スキルをマージして」「スキルを自動更新して」などで発動。
metadata:
  version: 1.0.1
  tier: core
  category: meta
  tags:
    - skill-management
    - git
    - versioning
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
|**changelog**  |「スキルの変更履歴を生成して」「CHANGELOGを作って」|
|**bump**       |「バージョンを上げて」「パッチバージョンアップして」「メジャーバージョンアップして」|
|**auto-update**|「自動更新を有効化して」「更新チェックして」「自動更新の設定を見せて」|
|**snapshot**   |「スナップショットを保存して」「一覧を見せて」|
|**rollback**   |「元に戻して」「前の状態に戻して」「pullを取り消して」|
|**metrics**    |「メトリクスを見せて」「スキルの実行統計を確認」|
|**metrics-detail**|「○○のメトリクスを詳しく」「スキルの実行時間の推移を見たい」|
|**metrics-co** |「どのスキルが一緒に使われてる？」「共起分析して」|
|**metrics-collect**|「メトリクスを集計して」「ログを再集計して」|

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

### 競合解決

同名スキルが複数のリポジトリに存在する場合、以下のロジックで解決する。

**ユーザー直接呼び出し（`interactive=True`）**: 番号選択で競合リポジトリを選択（Enter のみでデフォルト 1 を選択）。無効な入力や非対話環境では `priority` の高いリポジトリを自動採用しフォールバック。

**サブエージェント経由（`interactive=False`）**: `priority` の低い値（高優先度）のリポジトリを自動採用し、採用したリポジトリ名をログ出力。

-----

## push

### 処理フロー

→ 実装: `scripts/push.py` — `push_skill(skill_path, repo_name, branch_strategy, commit_msg)`

一時ディレクトリにクローン → スキルフォルダをコピー → 不要ファイル除外 → commit & push。`branch_strategy="new_branch"` でブランチを切り PR/MR を作成するフローを推奨。

-----

## list

→ 実装: `scripts/manage.py` — `list_skills()`、`scripts/registry.py` — `is_skill_enabled()`

インストール済みスキルの一覧を表示。有効/無効、ソースリポジトリ、コミットハッシュ、pin状態、バージョンを表示する。

- `v1.2.3` — ローカルのバージョン
- `v1.2.3 ⬆️` — ローカルが中央より新しい（version_ahead）
- `v1.2.3 (central: v1.2.4)` — 中央に新しいバージョンがある（pull 推奨）

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

ワークスペースのスキルディレクトリ（`<workspace-skill-dir>`）のスキルをユーザー領域（`~/.copilot/skills/`）にコピーし、リポジトリにも push する。プロジェクト固有でないスキルを他のプロジェクトでも再利用可能にする。

### 処理フロー

→ 実装: `scripts/manage.py` — `promote_skills(workspace_skills_dir, interactive)`

1. ワークスペース内スキルをスキャン
2. ユーザーに候補を提示して選択させる
3. 選択されたスキルをユーザー領域にコピー + レジストリ登録
4. 書き込み可能なリポジトリがあれば push を提案

-----

## ワークスペーストライアルフロー

VSCode チャット経由で作成されたスキルはワークスペースのスキルディレクトリ（`<workspace-skill-dir>`）に置かれ、試用してから昇格する。

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
4. 改良完了後、スクリプト出力の `REFINE_COMPLETE_CMD:` 行に示されたコマンドを**必ず実行する**（`pending_refinement` フラグの解除と `refined` フラグの更新が行われる）
5. インストール済みスキルかつ source_repo がリポジトリの場合は push を提案

### スキルパスの違い

| スキル種別 | 編集対象パス |
|---|---|
| ワークスペーススキル | `<workspace-skill-dir>/<name>/` |
| インストール済みスキル | `~/.copilot/skills/<name>/` |

`refine_skill()` はスクリプト出力に `スキルパス: <path>` および `REFINE_COMPLETE_CMD: python manage.py mark-refined <name>` を含む。エージェントはスキルパスを skill-creator に渡し、改良完了後に `REFINE_COMPLETE_CMD:` のコマンドを実行する。

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

## changelog

スキルのコミット履歴とフロントマターのバージョン変更から `CHANGELOG.md` を自動生成する。

→ 実装: `scripts/changelog.py` — `generate_changelog()`、`scripts/manage.py` — `changelog_skill()`

コマンド例・詳細 → [references/version-management.md](references/version-management.md)

-----

## bump

SKILL.md の `metadata.version` をセマンティックバージョニングに従ってインクリメントする（`X.Y.Z` 形式）。

→ 実装: `scripts/manage.py` — `bump_version()`、`scripts/registry.py` — `_update_frontmatter_version()`

バージョン指針・処理フロー・タイミング → [references/version-management.md](references/version-management.md)

-----

## discover

`generating-skills-from-copilot-logs` を起動し、直近のチャット履歴から新しいスキル候補を発見する。

処理フロー詳細 → [references/version-management.md](references/version-management.md)

-----

## feedback

直前に実行したスキルの満足度をユーザーに確認し、レジストリに記録する。
スキル単体起動後に `copilot-instructions.md` の指示で自動的に呼ばれる。

→ 実装: `scripts/record_feedback.py`

1. 対象スキル名を確認（不明な場合はユーザーに確認）
2. 次の選択肢をユーザーに提示し、**ユーザーの回答を受け取るまで待機する**（ターンを終えてユーザー入力を待つこと。回答前に次のステップへ進んではいけない）:
   ```
   「[スキル名] の実行はいかがでしたか？
    1. 問題なかった (ok)
    2. 改善点がある (needs-improvement)
    3. うまくいかなかった (broken)」
   ```
3. ユーザーの選択を `<verdict>` に使って `python record_feedback.py <name> --verdict <verdict> --note <note>` を実行。実行時間が分かる場合は `--duration <秒>` を追加する
4. 出力に応じて次のアクションを取る:
   - `EVAL_RECOMMEND: promote` または `EVAL_RECOMMEND: refine` → `evaluate` 操作へ進む（ワークスペース・インストール済み両方に対応）
   - `EVAL_RECOMMEND: continue` → 「試用継続中です（あと N 回の ok フィードバックで昇格候補になります）」とユーザーに伝えて終了

-----

## evaluate

ワークスペーススキル（試用中）とインストール済みスキル（ホーム領域）の両方の推奨アクションを評価する。`skill-evaluator` スキルを呼び出して実行する。

### トリガー

| トリガー | 説明 |
|---|---|
| `record_feedback.py` の `EVAL_RECOMMEND: promote\|refine` 出力 | フィードバック記録後にインラインで自動起動 |
| scrum-master Phase 6 | スプリント完了時のバッチ棚卸し |
| ユーザー直接 | 「スキルを評価して」など |

### 処理フロー

→ 実装: `<SKILLS_BASE>/skill-evaluator/scripts/evaluate.py`（skill-evaluator スキルが管理、`<SKILLS_BASE>` は `~/.copilot/skills` または `<workspace-skill-dir>`）

1. `skill-evaluator` サブエージェントを起動する:
   ```
   skill-evaluator スキルでスキルを評価する。
   手順: まず <SKILLS_BASE>/skill-evaluator/SKILL.md を読んで手順に従ってください。
   ```
2. skill-evaluator が評価結果を提示し、promote / refine のアクションをユーザーに確認する
3. 「昇格する」→ `promote` 操作を実行する
4. 「改良する」→ `refine` 操作を実行する

-----

## metrics

スキルの実行メトリクス（実行時間・成功率推移・サブエージェント回数・共起分析）を表示する。

→ 実装: `scripts/metrics_report.py`, `scripts/metrics_collector.py`

### サブ操作

| サブ操作 | コマンド | 説明 |
|---|---|---|
| **metrics** | `python metrics_report.py` | 全スキルのサマリテーブル |
| **metrics-detail** | `python metrics_report.py --skill <name> --detail` | 特定スキルの詳細（週次チャート付き） |
| **metrics-co** | `python metrics_report.py --co-occurrence` | スキル共起マトリクス（上位10ペア） |
| **metrics-collect** | `python metrics_collector.py` | JSONL ログを再集計してレジストリ更新 |
| **metrics-collect（期間指定）** | `python metrics_collector.py --days 30` | 直近 N 日のみ集計 |
| **metrics-collect（ローテーション）** | `python metrics_collector.py --rotate` | 90 日超の古いログを `.bak` へアーカイブ |
| **metrics（Markdown出力）** | `python metrics_report.py --output metrics-report.md` | Markdown ファイルへ出力 |

### データフロー

```
record_feedback.py --duration --subagent-calls --co-skills
        │
        ▼
~/.copilot/metrics-log.jsonl  ← 生イベント（JSONL 追記）
        │
        ▼  metrics_collector.py
registry.json .metrics        ← サマリ（インクリメンタル + バッチ更新）
        │
        ▼  metrics_report.py
ターミナル / Markdown          ← 可視化レポート
```

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

## snapshot / rollback

pull 実行時に自動でスナップショットを保存し、問題が発生した場合に元の状態へ復元する。

### pull 時の自動保存

`pull_skills()` の先頭で `snapshot.py save` を自動呼び出し。保存後は上限（デフォルト: 10件）を超えた古いスナップショットを自動削除する。pull 完了後に復元コマンドをヒント表示する:

```
💡 問題があれば元に戻せます:
   python snapshot.py restore --latest
```

手動コマンド・上限管理・保存内容の詳細 → [references/snapshot-rollback.md](references/snapshot-rollback.md)

### エージェントの動作

「元に戻して」「pullを取り消して」「前の状態に戻したい」などのユーザー発言で発動:

1. `python snapshot.py list` でスナップショット一覧を表示
2. ユーザーに復元先を確認（直近1件なら `--latest` でよいか確認）
3. `python snapshot.py restore --latest` または指定IDで復元
4. 復元完了を報告

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

## 使用例

操作ごとのエージェント対話例 → [references/examples.md](references/examples.md)
