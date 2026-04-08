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
| GitHub Copilot | `<AGENT_HOME>/skill-registry.json` |
| Claude Code    | `~/.claude/skill-registry.json`  |
| Codex          | `~/.codex/skill-registry.json`   |
| Kiro           | `~/.kiro/skill-registry.json`    |

セッション開始時にレジストリを読み、`skill_home` の値を `{skill_home}` として以降の手順で使用する。

-----

## セッション開始時の手順

セッション開始時に、以下を**順番に**実行する:

### 手順 0: 日次インターバルチェック

手順 2 は **1日1回のみ** 実行する（手順 1 の `auto_update.py check` は内部でインターバルチェックを行うため対象外）。以下の方法で判定すること:

タイムスタンプファイルの場所は `skill-registry.json` と同じディレクトリ内の `session_init_date`:

| エージェント | タイムスタンプファイル |
|---|---|
| GitHub Copilot | `<AGENT_HOME>/session_init_date` |
| Claude Code    | `~/.claude/session_init_date`  |
| Codex          | `~/.codex/session_init_date`   |
| Kiro           | `~/.kiro/session_init_date`    |

1. 上記のタイムスタンプファイルを読む（存在しない場合は空扱い）
2. ファイルの内容が今日の日付（`YYYY-MM-DD` 形式）と一致する場合は、**手順 2 をスキップ**して手順 3 へ進む
3. 一致しない場合は手順 2 を実行し、完了後に今日の日付をタイムスタンプファイルへ書き込む

### 手順 1: スキル自動更新チェック

```
{skill_home}/git-skill-manager/scripts/auto_update.py check
```

`--force` なしの場合、前回チェックから `interval_hours` 未満であればスクリプト側でスキップされる（ネットワーク負荷を抑制）。インターバルは `auto_update.py configure --interval <時間>` で変更可能（デフォルト: 24h）。

### 手順 2: 記憶の同期

#### 2-1. サーバから記憶を pull して home に取り込む

共有リポジトリの最新記憶を取得し、未取得のものを home スコープへインポートする:

```
{skill_home}/ltm-use/scripts/sync_memory.py --import-to-home
```

共有リポジトリが未設定の場合はスキップしてよい（エラーにならない）。

#### 2-2. 昇格対象の記憶を push する

home スコープの記憶のうち `share_score >= 85` のものを shared へ昇格し、git push まで一括実行する:

```
{skill_home}/ltm-use/scripts/promote_memory.py --scope home --target shared --auto --push
```

共有リポジトリが未設定、または昇格候補がない場合はスキップしてよい（エラーにならない）。

#### 2-3. Copilot Memory インポート（任意）

VSCode Copilot Memory が存在する場合は ltm-use へ自動インポートする:

```
{skill_home}/ltm-use/scripts/sync_copilot_memory.py
```

前回実行から72時間（3日）未満の場合はスクリプト内で自動スキップされる。
Copilot Memory が存在しない場合もスキップされる（エラーにならない）。

### 手順 3: 過去の記憶のリコール

タスクを受け取ったら、作業を始める前に関連する過去の記憶を検索する:

```
{skill_home}/ltm-use/scripts/recall_memory.py "キーワード"
```

-----

## セッション中の手順

以下の場面では、作業中に随時記憶を保存する:

- 原因の特定が難しかったバグとその解決策
- ユーザーが明示的に「覚えておいて」「記憶して」と指示したとき
- 設計上の重要な決定やトレードオフの結論

```
{skill_home}/ltm-use/scripts/save_memory.py --non-interactive --no-dedup --title "タイトル" --summary "要約" --tags tag1,tag2
```

scrum-master 経由のスプリント実行中は、スプリント完了後（Phase 6 の後）にまとめて保存する。

-----

## セッション終了時の手順

セッション終了前に、そのセッションで得た知識を振り返り記憶として保存する:

```
{skill_home}/ltm-use/scripts/save_memory.py --non-interactive --no-dedup --title "タイトル" --summary "要約" --tags tag1,tag2
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
