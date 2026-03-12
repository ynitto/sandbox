# エージェントへの共通指示

> **Windows の場合**: このドキュメント内の `~/.copilot/` は `%USERPROFILE%\.copilot\` に読み替えてください。

## 前提条件

* 必ず日本語で回答すること

## セッション開始時の手順

セッション開始時に、以下を**順番に**実行する:

### 手順 1: スキル自動更新チェック

```bash
python ~/.copilot/skills/git-skill-manager/scripts/auto_update.py check
```

`--force` なしの場合、`interval_hours` 未満であればスクリプト側でスキップされる（ネットワーク負荷を抑制）。

### 手順 2: Copilot Memory 同期

VSCode Copilot Memory の内容を ltm-use へ自動インポートする:

```bash
python ~/.copilot/skills/ltm-use/scripts/sync_copilot_memory.py
```

### 手順 3: 過去の記憶のリコール

タスクを受け取ったら、作業を始める前に関連する過去の記憶を検索する:

```bash
python ~/.copilot/skills/ltm-use/scripts/recall_memory.py "キーワード"
```

-----

## セッション中の手順

以下の場面では、作業中に随時記憶を保存する:

- 原因の特定が難しかったバグとその解決策
- ユーザーが明示的に「覚えておいて」「記憶して」と指示したとき
- 設計上の重要な決定やトレードオフの結論

```bash
python ~/.copilot/skills/ltm-use/scripts/save_memory.py \
  --title "タイトル" --summary "要約" --tags tag1,tag2
```

scrum-master 経由のスプリント実行中は、スプリント完了後（Phase 6 の後）にまとめて保存する。

-----

## セッション終了時の手順

セッション終了前に、そのセッションで得た知識を振り返り記憶として保存する:

```bash
python ~/.copilot/skills/ltm-use/scripts/save_memory.py \
  --title "タイトル" --summary "要約" --tags tag1,tag2
```

保存対象の例:
- 新たに判明したコードベースの構造・設計意図
- 採用した実装方針とその理由
- 解決したエラーと原因・対処法
- ユーザーの好みや繰り返し出てくる指示のパターン

-----

## スキル実行後のフィードバック収集

スキルを単体で実行完了したら、**scrum-master 経由の場合を除き**、
git-skill-manager の `feedback` 操作でフィードバックを収集する。

```
git-skill-manager で [スキル名] のフィードバックを記録して
```

実行時間が概算で分かる場合は、フィードバック記録時に `--duration <秒>` オプションを付けて記録する。
正確でなくてよい（「約30秒」→ `--duration 30`）。不明な場合は省略してよい。

scrum-master 経由の場合はスプリント終了時に一括収集されるためスキップする。

-----

## 言語・用途別コーディング指示の適用

`~/.copilot/instructions/` にはコーディングの言語・用途別指示 MD が格納されている。

タスクを受け取ったら、作業を始める前に以下を読み込み、コーディング規範として適用する:

- **常時**: `~/.copilot/instructions/common.md`（共通規範）
- **Python コード**: `~/.copilot/instructions/python.md`
- **TypeScript / JavaScript コード**: `~/.copilot/instructions/typescript.md`
- **Go コード**: `~/.copilot/instructions/go.md`
- **React / Next.js**: `~/.copilot/instructions/react.md`

ファイルが存在しない場合はスキップしてよい。
instructions の手動同期が必要な場合:

```bash
python ~/.copilot/skills/git-skill-manager/scripts/sync_instructions.py
```

-----

## ワークスペースドキュメントの優先

`docs/` ディレクトリにはプロジェクト固有のルール・設計・ガイドラインが含まれる。

タスクを受け取ったら、作業を始める前に `docs/` 以下のファイルをすべてコンテキストに読み込み、
その内容をスキルの指示より**優先して従うこと**。

- `docs/` の記述がスキルの指示と異なる場合、`docs/` を優先する
- `docs/` に該当する記述がない場合のみ、スキルのデフォルト動作に従う

-----

## スキルの優先順位

同名のスキルが `~/.copilot/skills/`（ユーザーホーム）と `.github/skills/`（ワークスペース）の
両方に存在する場合、**ワークスペース側を優先して使用する**。
ユーザーホーム側のバージョンは無視してよい。

-----

## コーディング行動規範（Karpathy Guidelines）

`~/.copilot/skills/karpathy-guidelines/SKILL.md` を読み込み、すべてのコーディングタスクに常時適用する。
