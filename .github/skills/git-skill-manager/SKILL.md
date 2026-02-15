---
name: git-skill-manager
description: Gitリポジトリを使ってエージェントスキルを管理するスキル。複数リポジトリの登録、スキルのpull（取得）とpush（共有）、スキルの有効化/無効化、プロファイル管理を行う。「スキルをpullして」「リポジトリからスキルを取ってきて」「スキルをpushして」「リポジトリを登録して」「スキル一覧」「スキルを無効化して」「プロファイルを切り替えて」など、スキルの取得・共有・リポジトリ管理・有効化管理に関するリクエストで使用する。GitHub/GitLab/Bitbucket/セルフホスト問わず動作する。Copilot + Windows環境で動作し、gitは設定済みの前提。
---

# Git Skill Manager

Gitリポジトリ経由でエージェントスキルの取得（pull）と共有（push）を行う管理システム。

## 利用者

| 呼び出し元 | 操作 | 例 |
|---|---|---|
| ユーザー直接 | repo add / pull / search / list / enable / disable / profile | 「スキルをpullして」「リポジトリを登録して」「スキルを無効化して」 |
| scrum-master サブエージェント | push | Phase 6 のスキル共有時にテンプレート経由で起動される |

- ユーザー直接呼び出しの場合、対話的に確認しながら進める
- サブエージェント経由の場合、プロンプトに必要な情報（対象スキル・リポジトリ名・操作）が含まれるため、確認なしで実行する

## 動作環境

- **Copilot on Windows** または **Claude Code**
- git はインストール・認証設定済み（SSH鍵 or credential manager）
- シェルは PowerShell または cmd を想定。bashコマンドは使わない

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

パス: `%USERPROFILE%\.copilot\skill-registry.json`

```json
{
  "version": 2,
  "repositories": [
    {
      "name": "team-skills",
      "url": "https://github.com/myorg/agent-skills.git",
      "branch": "main",
      "skill_root": "skills",
      "description": "チーム共有スキル集",
      "readonly": false,
      "priority": 1
    }
  ],
  "installed_skills": [
    {
      "name": "docx-converter",
      "source_repo": "team-skills",
      "source_path": "skills/docx-converter",
      "commit_hash": "a1b2c3d",
      "installed_at": "2026-02-14T12:00:00Z",
      "enabled": true,
      "pinned_commit": null,
      "usage_stats": {
        "total_count": 42,
        "last_used_at": "2026-02-15T10:00:00Z"
      }
    }
  ],
  "core_skills": ["scrum-master", "git-skill-manager", "skill-creator", "sprint-reviewer", "codebase-to-skill"],
  "remote_index": {
    "team-skills": {
      "updated_at": "2026-02-15T10:00:00Z",
      "skills": [
        {"name": "docx-converter", "description": "Word文書をPDFに変換する..."},
        {"name": "image-resizer", "description": "画像をリサイズする..."}
      ]
    }
  },
  "profiles": {
    "default": ["*"],
    "frontend": ["react-guide", "css-linter", "storybook"],
    "backend": ["api-guide", "db-migration", "auth"]
  },
  "active_profile": null
}
```

### フィールド説明

**repositories[].priority** (整数、デフォルト: 100):
- 値が小さいほど優先度が高い
- 同名スキルの競合時、サブエージェント経由（非対話）では優先度の高いリポジトリを自動採用する
- ユーザー直接呼び出しでは対話的に選択を求める

**installed_skills[].enabled** (真偽値、デフォルト: true):
- false のスキルは `discover_skills.py` によるメタデータ収集から除外される
- ディスク上にはスキルが残るため、再有効化は即座に完了する

**installed_skills[].pinned_commit** (文字列 or null、デフォルト: null):
- null の場合、pull 時に常に最新（HEAD）を取得する
- コミットハッシュが設定されている場合、pull 時にそのコミットを checkout して取得する
- `pin` 操作で現在の commit_hash に固定、`unpin` で解除
- `lock` で全スキルを一括 pin、`unlock` で全スキルを一括 unpin

**installed_skills[].usage_stats** (オブジェクト or null、デフォルト: null):
- `total_count`: 累計使用回数
- `last_used_at`: 最終使用日時（ISO 8601）
- `record_usage.py` によって自動更新される
- `discover_skills.py` がこの値を参照してスキルの返却順を決定する

**core_skills** (文字列リスト):
- 使用頻度に関わらず常に最優先でロードされるスキル名のリスト
- scrum-master、git-skill-manager、skill-creator など基盤スキルを登録する
- `usage_stats` による順位付けの対象外（常にトップ）
- `discover_skills.py` のソート時にこのリストのスキルを先頭に配置する

**remote_index** (オブジェクト):
- リポジトリ名 → スキル一覧のキャッシュ。`search` がこのインデックスを参照するため、ネットワーク不要で高速に検索できる
- `pull` 実行時に自動更新される
- `search --refresh` で明示的にリモートから更新できる
- `updated_at` で鮮度を確認可能

**profiles** (オブジェクト):
- プロファイル名 → スキル名のリスト。`"*"` は「全スキル」を意味する
- `active_profile` が null の場合、個別の enabled フラグに従う
- `active_profile` が設定されている場合、プロファイル内のスキルのみ enabled として扱う

レジストリが存在しなければ初回操作時に自動作成する。version: 1 のレジストリは自動マイグレーションする（新フィールドにデフォルト値を設定）。

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

## レジストリのマイグレーション

version: 1 のレジストリを読み込んだ場合、以下のマイグレーションを適用する。

→ 実装: `scripts/registry.py` — `migrate_registry(reg)`

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

## usage_stats と record_usage

スキルの使用回数を記録し、`discover_skills.py` のソート順に反映する仕組み。

### 記録方法

使用記録は `record_usage.py` スクリプトで行う:

```bash
python .github/skills/git-skill-manager/scripts/record_usage.py <skill-name>
```

### 記録タイミング

各 SKILL.md のフロントマター直後に記録の指示行が記載されている（skill-creator の `init_skill.py` が自動挿入する）。エージェントがスキルを読み込んで手順に従う前にこの行を実行することで使用回数が記録される。

```markdown
---
name: my-skill
description: ...
---
> **使用記録**: このスキルの手順に従う前に `python .github/skills/git-skill-manager/scripts/record_usage.py my-skill` を実行してください（スクリプトが存在しない場合はスキップ）。
```

git-skill-manager がインストールされていない環境ではスクリプトが存在しないため、エージェントはこの行をスキップする。

### record_usage.py

→ 実装: `scripts/record_usage.py`（既存スクリプト）

### discover_skills.py のソート順

`discover_skills.py` はスキル一覧を以下の優先度でソートして返す:

1. **コアスキル** (`core_skills` に含まれるスキル) → 常に先頭。コンテキストウィンドウに必ずロードされる
2. **使用頻度** (`usage_stats.total_count` 降順) → よく使うスキルほど上位
3. **最終使用日時** (`usage_stats.last_used_at` 降順) → 同頻度なら最近使ったものが上位
4. **名前順** → usage_stats がないスキルはアルファベット順

→ 実装: `scripts/manage.py` — `sort_key(skill, core_skills, registry)`

-----

## profile

プロファイルはスキルの有効・無効を一括で切り替えるショートカット。プロファイルをアクティブにすると、そのプロファイルに含まれるスキルのみがコンテキストにロードされる。

→ 実装: `scripts/manage.py` — `profile_create()`, `profile_use()`, `profile_list()`, `profile_delete()`

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

### 初回インストール

```
git clone https://github.com/myorg/agent-skills.git
python agent-skills/install.py
```

コアスキル（scrum-master, git-skill-manager, skill-creator, sprint-reviewer, codebase-to-skill）がユーザー領域にコピーされ、ソースリポジトリがレジストリに自動登録される。2回目以降の実行はスキルの上書き更新になる（レジストリの既存設定は保持）。

### 初回セットアップ

```
ユーザー: 「https://github.com/myorg/skills.git をスキルリポジトリに登録して」

Copilot:
  1. git ls-remote で接続確認
  2. レジストリ作成、リポジトリ追加（readonlyにするか確認、priorityを確認）
  3. 「登録しました。pullしますか？」
```

### readonlyリポジトリの登録

```
ユーザー: 「https://github.com/otherteam/skills.git を参照専用で登録して」

Copilot:
  1. git ls-remote で接続確認
  2. readonly: true でレジストリに追加
  3. 「readonlyで登録しました。pullのみ可能です」
```

### pull（キャッシュ活用）

```
ユーザー: 「スキルを全部同期して」

Copilot:
  1. 全リポジトリを cache からfetch（初回のみclone）
  2. 各リポジトリのスキルを走査
  3. 同名競合があればユーザーに確認
  4. %USERPROFILE%\.copilot\skills\ にコピー、レジストリ更新
  5. 結果レポート（有効/無効状態も表示）
```

### push

```
ユーザー: 「今作ったスキルを team-skills にpushして」

Copilot:
  1. レジストリから team-skills の情報を取得
  2. SKILL.md の存在確認
  3. clone → ブランチ作成 → コピー → commit → push
  4. コミットハッシュとブランチ名を報告
```

### スキルの無効化

```
ユーザー: 「legacy-tool スキルを無効化して」

Copilot:
  1. レジストリの enabled を false に変更
  2. 「legacy-tool を無効化しました。再有効化は 'スキルを有効化して' で可能です」
```

### 検索（オフライン）

```
ユーザー: 「converter で検索して」

Copilot:
  1. レジストリの remote_index から keyword=converter で検索（ネットワーク不要）
  2. 結果を表示（インデックス更新日も表示）
```

### 検索（最新を取得）

```
ユーザー: 「最新のスキルを検索して」

Copilot:
  1. 全リポジトリから fetch してインデックスを更新
  2. 更新後のインデックスから検索結果を表示
```

### スキルのバージョン固定

```
ユーザー: 「docx-converter を今のバージョンに固定して」

Copilot:
  1. 現在の commit_hash を pinned_commit に設定
  2. 「docx-converter を a1b2c3d に固定しました」
```

### 全スキルのロック

```
ユーザー: 「全スキルをロックして」

Copilot:
  1. 全 installed_skills の commit_hash を pinned_commit に設定
  2. ロックされたスキル一覧を表示
```

### スキルの昇格（promote）

```
ユーザー: 「ワークスペースのスキルを他のプロジェクトでも使えるようにして」

Copilot:
  1. $workspace/.github/skills/ をスキャン、候補をリストアップ
  2. ユーザーが昇格するスキルを選択
  3. ~/.copilot/skills/ にコピー、レジストリに登録
  4. push 先リポジトリをユーザーが選択
  5. 選択リポジトリに push（ブランチ作成）
```

### プロファイル切り替え

```
ユーザー: 「フロントエンド開発用のプロファイルに切り替えて」

Copilot:
  1. frontend プロファイルをアクティブに設定
  2. 「frontend プロファイルをアクティブにしました: react-guide, css-linter, storybook」
```
