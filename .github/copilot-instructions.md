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

## 長期記憶（ltm-use）

### 記憶の保存タイミング

以下の場面では記憶を保存して次のセッションに活かす:

- 原因の特定が難しかったバグとその解決策
- ユーザーが明示的に「覚えておいて」「記憶して」と指示したとき
- 設計上の重要な決定やトレードオフの結論

```bash
python ~/.copilot/skills/ltm-use/scripts/save_memory.py \
  --title "タイトル" --summary "要約" --tags tag1,tag2
```

scrum-master 経由のスプリント実行中は、スプリント完了後（Phase 6 の後）にまとめて保存する。

-----

## スキルの優先順位

同名のスキルが `~/.copilot/skills/`（ユーザーホーム）と `.github/skills/`（ワークスペース）の
両方に存在する場合、**ワークスペース側を優先して使用する**。
ユーザーホーム側のバージョンは無視してよい。
