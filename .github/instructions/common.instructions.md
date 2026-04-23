---
applyTo: "**"
---

# エージェントへの共通指示

## 前提条件

* 必ず日本語で回答すること

-----

## スキルパスの解決

このドキュメント中の `{skill_home}` は **`skill-registry.json` の `skill_home` フィールドの値** を指す。

`skill-registry.json` は `install.py` 実行時にエージェントのホームディレクトリ直下に生成される:

| エージェント | skill-registry.json の場所 |
|---|---|
| GitHub Copilot | `~/.copilot/skill-registry.json` |
| Claude Code    | `~/.claude/skill-registry.json`  |
| Codex          | `~/.codex/skill-registry.json`   |
| Kiro           | `~/.kiro/skill-registry.json`    |

セッション開始時にレジストリを読み、`skill_home` の値を `{skill_home}` として以降の手順で使用する。

-----

## セッション開始時の手順

セッション開始時に、以下を**順番に**実行する:

### 手順 1: スキル自動更新チェック

```
python {skill_home}/git-skill-manager/scripts/auto_update.py check
```

### 手順 2: 記憶の同期

```
python {skill_home}/ltm-use/scripts/auto_update.py check
```

### 手順 3: 過去の記憶のリコール

タスクを受け取ったら、作業を始める前に関連する記憶を全カテゴリを対象に検索する:

```bash
# 全カテゴリを対象に検索（エピソード・意味・手順すべて）
python {skill_home}/ltm-use/scripts/recall_memory.py "タスクのキーワード"
# 手順記憶に絞りたい場合のみ
python {skill_home}/ltm-use/scripts/recall_memory.py "キーワード procedural"
```

-----

## セッション中の手順

新しい手順を習得したら保存する:

```bash
python {skill_home}/ltm-use/scripts/save_memory.py \
  --non-interactive \
  --no-dedup --no-auto-tags \
  --scope home \
  --category [カテゴリ] \
  --title "[手順名]（procedural）" \
  --summary "[この手順が解決する問題と適用条件]" \
  --content "[具体的な手順・コマンド]" \
  --tags "procedural,[関連タグ]"
```

以下の場面では、作業中に随時記憶を保存する:

- 原因の特定が難しかったバグとその解決策
- ユーザーが明示的に「覚えておいて」「記憶して」と指示したとき
- 設計上の重要な決定やトレードオフの結論
- ユーザーが肯定・承認の反応をした直後（「ありがとう」「OK」「それでいい」「完璧」など、学習シグナルとして扱う）
- エラーが解決されたとき（難易度問わず、原因と対処法をセットで保存）
- 新しいコマンド・ツール・API・ライブラリを初めて正常に使ったとき
- 同じ問題が複数回登場したとき（繰り返しパターンは手続き記憶として保存）

```
python {skill_home}/ltm-use/scripts/save_memory.py --non-interactive --no-dedup --title "タイトル" --summary "要約" --tags tag1,tag2
```

scrum-master 経由のスプリント実行中は、スプリント完了後（Phase 6 の後）にまとめて保存する。

-----

## セッション終了時の手順

ユーザーが「終わり」「ありがとう」「以上」などセッション終了を示す発言をした際、
または長時間応答がなくなる前に、そのセッションで得た知識を振り返り記憶として保存する:

```
python {skill_home}/ltm-use/scripts/save_memory.py --non-interactive --no-dedup --title "タイトル" --summary "要約" --tags tag1,tag2
```

保存対象の例:
- 新たに判明したコードベースの構造・設計意図
- 採用した実装方針とその理由
- 解決したエラーと原因・対処法
- ユーザーの好みや繰り返し出てくる指示のパターン

保存後、エピソード記憶が3件以上蓄積されている場合は固定化を検討する:

```bash
# 固定化候補を確認（ドライラン）
python {skill_home}/ltm-use/scripts/consolidate_memory.py --dry-run
# 候補が多い場合はユーザーに確認してから実行
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

## ワークスペースドキュメントの優先

`docs/` ディレクトリにはプロジェクト固有のルール・設計・ガイドラインが含まれる。

タスクを受け取ったら、作業を始める前に `docs/` 以下のファイルをすべてコンテキストに読み込み、
その内容をスキルの指示より**優先して従うこと**。

- `docs/` の記述がスキルの指示と異なる場合、`docs/` を優先する
- `docs/` に該当する記述がない場合のみ、スキルのデフォルト動作に従う

-----

## スキルの優先順位

同名のスキルがユーザーホーム（`{skill_home}`）とワークスペース（`.github/skills/`）の
両方に存在する場合、**ワークスペース側を優先して使用する**。
ユーザーホーム側のバージョンは無視してよい。

-----

## コーディング行動規範（Karpathy Guidelines）

`{skill_home}/karpathy-guidelines/SKILL.md` を読み込み、すべてのコーディングタスクに常時適用する。
