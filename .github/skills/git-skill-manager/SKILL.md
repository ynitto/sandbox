---
name: git-skill-manager
description: Gitリポジトリを使ってエージェントスキルを管理するスキル。複数リポジトリの登録、スキルのpull（取得）とpush（共有）、スキルの有効化/無効化、プロファイル管理を行う。「スキルをpullして」「リポジトリからスキルを取ってきて」「スキルをpushして」「リポジトリを登録して」「スキル一覧」「スキルを無効化して」「プロファイルを切り替えて」など、スキルの取得・共有・リポジトリ管理・有効化管理に関するリクエストで使用する。また「スキルを改良して」「フィードバックを反映して」「新しいスキル候補を探して」「履歴からスキルを発見して」「スキルを評価して」「試用中スキルを確認して」のようなスキル改良・発見・評価のリクエストでも使用する。「スキルをマージして」「リポジトリ間のスキルを統合して配信して」のようなクロスリポジトリマージリクエストでも使用する。GitHub/GitLab/Bitbucket/セルフホスト問わず動作する。Copilot + Windows環境で動作し、gitは設定済みの前提。
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

パス: `%USERPROFILE%\.copilot\skill-registry.json`

```json
{
  "version": 3,
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
      "feedback_history": [
        {
          "timestamp": "2026-02-15T10:00:00Z",
          "verdict": "needs-improvement",
          "note": "PDF変換時に文字化けが発生した",
          "refined": false
        }
      ],
      "pending_refinement": true
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

**installed_skills[].source_repo** (文字列):
- `"workspace"`: `.github/skills/` に置かれた試用中スキル（チャット経由で作成）
- `"local"`: `promote` 操作でユーザー領域に昇格済みのスキル
- その他: リポジトリ名（`pull` でインストールしたスキル）
- `"workspace"` のスキルは `skill-evaluator` の評価対象になる

**installed_skills[].feedback_history** (配列、デフォルト: []):
- スキル使用後にユーザーが提供したフィードバックの履歴
- 各エントリ: `timestamp`（ISO 8601）、`verdict`（ok/needs-improvement/broken）、`note`（コメント）、`refined`（改良済みフラグ）
- `record_feedback.py` で記録し、`refine` 操作の入力として使われる

**installed_skills[].pending_refinement** (真偽値、デフォルト: false):
- `needs-improvement` または `broken` の未対応フィードバックが存在する場合 true
- `discover_skills.py` がこの値を参照してスキルのソート順を決定する（改良待ちは後ろへ）
- `refine` 操作完了後に false に戻る

**core_skills** (文字列リスト):
- 使用頻度に関わらず常に最優先でロードされるスキル名のリスト
- scrum-master、git-skill-manager、skill-creator など基盤スキルを登録する
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
|**refine**     |「スキルを改良して」「フィードバックを反映して」「改善待ちを処理して」|
|**discover**   |「スキル候補を探して」「履歴からスキルを発見して」「新しいスキルを見つけて」|
|**evaluate**   |「スキルを評価して」「試用中スキルを確認して」「ワークスペーススキルを整理して」|
|**diff**       |「スキルの差分を見せて」「リポジトリ間の違いを確認して」|
|**sync**       |「マージしたスキルを全リポジトリに配信して」「スキルを同期して」|
|**merge**      |「スキルをマージして」「リポジトリ間のスキルを統合して配信して」|

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

## ワークスペーストライアルフロー

VSCode チャット経由で作成されたスキルは `.github/skills/` に置かれる（ワークスペース領域）。
ユーザーホームの `~/.copilot/skills/` とは別の場所なので、まず試用してから昇格する。

### スキルのライフサイクル

```
【作成】 skill-creator → .github/skills/<name>/   (source_repo: "workspace")
   ↓ 使用するたびにフィードバック収集
【評価】 record_feedback.py が自動評価
   ├── ok × 2回以上、問題なし  → ✅ 昇格推奨
   ├── 問題あり (needs-improvement/broken)  → ⚠️ 要改良後昇格
   └── ok × 1回  → 🔄 試用継続
   ↓ 昇格推奨 or ユーザーが判断
【昇格】 promote → ~/.copilot/skills/<name>/   (source_repo: "local")
   ↓ 必要なら
【共有】 push → チームリポジトリ
```

### 評価基準

評価基準の詳細は [skill-evaluator/SKILL.md](../skill-evaluator/SKILL.md) を参照してください。

### 評価の実行

**インライン（フィードバック記録時に自動トリガー）**

`record_feedback.py` がワークスペーススキルを検出すると `EVAL_RECOMMEND:` シグナルを出力する:
```
✅ my-skill: フィードバックを記録しました (ok)
EVAL_RECOMMEND: promote
```

エージェントはこのシグナルを受けて `skill-evaluator` サブエージェントを起動する（`promote` または `refine` の場合のみ）。

**バッチ（スプリント完了時）**

scrum-master の Phase 6 が `skill-evaluator` サブエージェントを起動して全ワークスペーススキルを一覧評価する:
```bash
python .github/skills/skill-evaluator/scripts/evaluate.py
```

-----

## フィードバックループと record_feedback

スキル使用後にフィードバックを収集し、スキル品質の改良トリガーとスキル発見の起点にする仕組み。

### pending_refinement トリガーのしきい値

スキルの種別ごとに `pending_refinement` が立つまでの未改良問題数が異なる。頻度を下げることで、安定稼働しているインストール済みスキルへの過剰な改良提案を防ぐ。

| スキル種別 | source_repo | デフォルトしきい値 |
|---|---|---|
| ワークスペーススキル | `workspace` | **1件**（即トリガー）|
| インストール済みスキル | `local` / リポジトリ名 | **3件**（蓄積してトリガー）|

`mark_refined()` 実行後は未改良カウントがリセットされるため、1サイクルの改良後に再び N 件蓄積するまで提案されない。
スキルエントリに `refine_threshold` フィールドを設定すれば個別に上書き可能。

### フィードバックの記録

使用後フィードバックは `record_feedback.py` スクリプトで行う:

```bash
# 問題なく動作した
python .github/skills/git-skill-manager/scripts/record_feedback.py <skill-name> --verdict ok

# 改善余地あり
python .github/skills/git-skill-manager/scripts/record_feedback.py <skill-name> --verdict needs-improvement --note "改善点の説明"

# 動作しなかった
python .github/skills/git-skill-manager/scripts/record_feedback.py <skill-name> --verdict broken --note "壊れている箇所"
```

### 記録タイミング（SKILL.md の実行後フィードバック節）

`skill-creator` の `init_skill.py` が新規スキル作成時に以下の節を自動挿入する。エージェントはスキルの手順を全て完了した後にこの節に従って動作する。

```markdown
## 実行後フィードバック（必須）

> **scrum-master 経由の場合**: このセクションをスキップしてください。フィードバックはスプリント終了時に一括で収集されます。

スキルの手順を全て完了したら、ユーザーに確認する:

「[skill-name] の実行はいかがでしたか？
 1. 問題なかった (ok)
 2. 改善点がある (needs-improvement)
 3. うまくいかなかった (broken)」

回答に応じて以下を実行する（git-skill-manager がない環境ではスキップ）:
python -c "import os,sys,subprocess; s=os.path.join(os.path.expanduser('~'),'.copilot','skills','git-skill-manager','scripts','record_feedback.py'); subprocess.run([sys.executable,s,'<skill-name>','--verdict','<verdict>','--note','<note>']) if os.path.isfile(s) else None"

スクリプトの出力に「EVAL_RECOMMEND: promote」または「EVAL_RECOMMEND: refine」が含まれる場合は、
skill-evaluator サブエージェントを起動して評価・昇格フローを進める。
```

- **直接呼び出し時**: スキル完了後にユーザーへフィードバックを確認し、record_feedback.py で記録する
- **scrum-master 経由時**: フィードバック節をスキップする。スプリント終了時（Phase 6）にまとめて収集される
- git-skill-manager がインストールされていない環境では record_feedback.py が存在しないため、フィードバック質問のみ行いスクリプト実行はスキップする

### discover_skills.py のソート順

`discover_skills.py` はスキル一覧を以下の優先度でソートして返す:

1. **コアスキル** (`core_skills` に含まれるスキル) → 常に先頭
2. **改良待ちなし + 直近 ok** (`pending_refinement=false` かつ最新 verdict が ok) → 信頼済み
3. **改良待ちあり** (`pending_refinement=true`) → 後ろに配置
4. **フィードバックなし** → アルファベット順

→ 実装: `scripts/manage.py` — `sort_key(skill, core_skills, registry)`

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

## diff

複数リポジトリに存在する同名スキルの実装を比較し、どのファイルがどう異なるかを表示する。
マージ前の差分確認に使う。

### 処理フロー

→ 実装: `scripts/manage.py` — `diff_skill(skill_name, repo_names)`

1. 登録リポジトリのキャッシュ（`~/.copilot/cache/`）から `skill_name` を検索
2. 見つかったリポジトリ同士をペアワイズで `git diff --no-index` にかける
3. `--stat`（変更ファイル概要）と詳細差分を表示（120行超は省略）

キャッシュが古い場合は先に `pull` または `search --refresh` を実行すること。

```
ユーザー: 「docx-converter の差分を見せて」

エージェント:
  python manage.py diff docx-converter

  → 出力例:
    🔍 スキル 'docx-converter' の差分 (2 リポジトリ)

      [team-skills]   commit: a1b2c3d  (2026-01-10)
      [personal]      commit: f4e5d6c  (2026-02-01)

    ────────────────────────────────────────────────────────────
      team-skills (a1b2c3d)  vs  personal (f4e5d6c)
    ────────────────────────────────────────────────────────────
     scripts/convert.py | 12 ++++---
     SKILL.md           |  3 +-
     2 files changed, 11 insertions(+), 4 deletions(-)

    @@ -45,7 +45,7 @@
    -    output_format = "pdf"
    +    output_format = kwargs.get("format", "pdf")
    ...
```

-----

## sync

マージ済みスキルをインストール済みの実体（`~/.copilot/skills/<name>/`）から、複数リポジトリへ一括 push する。
`diff` で差分を確認し、skill-creator でマージした後に実行する。

### 処理フロー

→ 実装: `scripts/manage.py` — `sync_skill(skill_name, repo_names)`、`scripts/push.py` — `push_skill()`

1. `skill_home/<skill_name>/` の存在確認（マージ済み実装がここにある前提）
2. 書き込み可能なリポジトリに対して `push_skill()` をループ実行
3. 各リポジトリで `new_branch` 戦略でブランチを切り、PR/MR 作成を促す

`repo_names` を指定した場合はその名前のリポジトリのみに push する。

```
ユーザー: 「マージした docx-converter を team-skills と personal に配信して」

エージェント:
  python manage.py sync docx-converter --repos team-skills,personal

  → 出力例:
    🔄 'docx-converter' を 2 リポジトリへ同期します

      → team-skills  (https://github.com/myorg/agent-skills.git)
      → personal     (https://github.com/me/my-skills.git)

    ⬆️  push 中: team-skills ...
      🚀 push 完了  ブランチ: add-skill/docx-converter
    ⬆️  push 中: personal ...
      🚀 push 完了  ブランチ: add-skill/docx-converter

    📋 sync 結果: docx-converter
      ✅ team-skills
      ✅ personal

    💡 各リポジトリで PR/MR を作成してマージしてください
```

-----

## merge

複数リポジトリに分岐した同名スキルを統合して全リポジトリへ配信する。
`diff` → `skill-creator` → `sync` の3ステップを1つのリクエストで処理する。

### 処理フロー

→ 実装: `scripts/manage.py` — `merge_skill(skill_name, repo_names)`

1. `merge_skill()` を実行して差分を表示する（内部で `diff_skill()` を呼び出す）
2. 出力の `MERGE_GUIDANCE:` ブロックを読み、skill-creator サブエージェントを起動する
   - ユーザーに統合方針を確認しながらマージ実装を生成させる
   - 編集先は `~/.copilot/skills/<skill_name>/`（インストール済みスキルを上書き）
3. skill-creator 完了後に `sync_skill()` を実行して全リポジトリへ配信する

```
ユーザー: 「docx-converter を team-skills と personal でマージして配信して」

エージェント:
  1. python manage.py merge docx-converter --repos team-skills,personal
       → 差分を表示 + MERGE_GUIDANCE: を出力
  2. skill-creator サブエージェントを起動してマージ実装を生成
  3. python manage.py sync docx-converter --repos team-skills,personal
       → 各リポジトリに PR ブランチを作成
```

全リポジトリを対象にする場合は `--repos` を省略する。

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
   手順: .github/skills/skill-evaluator/SKILL.md を読んで手順に従ってください。
   ```
2. skill-evaluator が評価結果を提示し、promote / refine のアクションをユーザーに確認する
3. 「昇格する」→ `promote` 操作を実行する
4. 「改良する」→ `refine` 操作を実行する

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
