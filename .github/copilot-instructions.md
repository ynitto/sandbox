# エージェントへの共通指示

> **Windows の場合**: このドキュメント内の `~/.copilot/` は `%USERPROFILE%\.copilot\` に読み替えてください。

## セッション開始時のスキル自動更新チェック

セッション開始時に、git-skill-manager の自動更新チェックを実行する:

```bash
python ~/.copilot/skills/git-skill-manager/scripts/auto_update.py check
```

`--force` なしの場合、`interval_hours` 未満であればスキップされる（ネットワーク負荷を抑制）。
git-skill-manager がインストールされていない環境ではスキップしてよい。

-----

## スキル実行後のフィードバック収集

スキルを単体で実行完了したら、**scrum-master 経由の場合を除き**、
git-skill-manager の `feedback` 操作でフィードバックを収集する。

```
git-skill-manager で [スキル名] のフィードバックを記録して
```

scrum-master 経由の場合はスプリント終了時に一括収集されるためスキップする。
git-skill-manager がインストールされていない環境ではスキップしてよい。

-----

## 長期記憶（ltm-use）

### セッション開始時のリコール

タスクを受け取ったら、作業を始める前に関連する過去の記憶を検索する:

```bash
python ~/.copilot/skills/ltm-use/scripts/recall_memory.py "キーワード"
```

ltm-use がインストールされていない場合はスキップしてよい。

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
両方に存在する場合、**ユーザーホーム側を優先して使用する**。
ワークスペース側のバージョンは無視してよい。
