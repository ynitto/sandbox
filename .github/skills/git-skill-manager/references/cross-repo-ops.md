# クロスリポジトリ操作（diff / sync / merge / push-direct）

複数リポジトリに分岐した同名スキルを比較・統合・配信するための操作群。

## 目次

- [diff](#diff)
- [sync](#sync)
- [merge](#merge)
- [push-direct](#push-direct)

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

## push-direct

ブランチを切らずに main ブランチへ直接 push する。バージョン比較を行い、ローカルが新しいスキルのみを一括でプッシュする。

### 処理フロー

→ 実装: `scripts/push.py` — `push_all_skills(skill_names, repo_names, commit_msg)`、`scripts/manage.py` — `push_to_main(skill_names, repo_names, commit_msg)`

1. 書き込み可能なリポジトリを列挙する
2. 各リポジトリについて:
   1. リモートの最新を `git clone --depth 1` で取得する
   2. スキルごとにローカルとリモートの `metadata.version` を比較する
   3. `_version_tuple(local_ver) > _version_tuple(remote_ver)` または新規スキルのみをコピー対象とする
   4. 対象スキルのフォルダを一括コピーし、不要ファイルを除外する
   5. 変更をまとめて 1 コミットにして `repo["branch"]`（通常は main）へ直接 push する

### 引数

| 引数 | 省略時の挙動 |
|---|---|
| `skill_names` | インストール済みスキルを全て対象にする |
| `repo_names` | 書き込み可能な全リポジトリを対象にする |
| `commit_msg` | `"Update skills: <skill1>, <skill2>, ..."` を自動生成 |

```
ユーザー: 「全リポジトリにブランチを切らずに push して」

エージェント:
  python manage.py push-direct

  → 出力例:
    📦 リポジトリ: team-skills (https://github.com/myorg/agent-skills.git)
      🔄 リモートの最新を取得中...
      📋 バージョン比較結果 — プッシュ対象 (2 件):
         react-frontend-coder          (新規) → v1.0.0
         code-reviewer                 v1.2.0 → v1.3.0
      🚀 push 完了
         ブランチ: main (direct)
         コミット: a1b2c3d
         ✅ react-frontend-coder          (新規) → v1.0.0
         ✅ code-reviewer                 v1.2.0 → v1.3.0
```

特定スキルのみを push する場合:
```
python manage.py push-direct --skills code-reviewer
```

特定リポジトリのみを対象にする場合:
```
python manage.py push-direct --repos team-skills
```
