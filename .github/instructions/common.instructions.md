---
applyTo: "**"
---

# エージェントへの共通指示

## 前提条件

* 必ず日本語で回答すること

-----

## スキルパスの解決

スキルのインストール先は **`skill-registry.json`** を読んで解決する。
レジストリには `agent_type`・`user_home`・`install_dir`・`skill_home` が記録されている。

### レジストリの場所

以下の順で検索し、最初に見つかったファイルを使用する:

| エージェント | レジストリパス |
|---|---|
| GitHub Copilot | `~/.copilot/skill-registry.json` |
| Claude Code    | `~/.claude/skill-registry.json`  |
| Codex          | `~/.codex/skill-registry.json`   |
| Kiro           | `~/.kiro/skill-registry.json`    |

> **Windows の場合**: `~` は `%USERPROFILE%` に読み替えてください。

### SKILL_HOME の取得

```python
import json, os, sys

_AGENT_DIRS = [".copilot", ".claude", ".codex", ".kiro"]
_home = os.environ.get("USERPROFILE", os.path.expanduser("~"))

for _d in _AGENT_DIRS:
    _reg_path = os.path.join(_home, _d, "skill-registry.json")
    if os.path.isfile(_reg_path):
        with open(_reg_path, encoding="utf-8") as _f:
            _reg = json.load(_f)
        SKILL_HOME = _reg.get("skill_home", os.path.join(_home, _d, "skills"))
        break
else:
    SKILL_HOME = os.path.join(_home, ".copilot", "skills")  # フォールバック
```

以降のコードブロックでは、この `SKILL_HOME` 変数を使ってスクリプトパスを組み立てる。

-----

## セッション開始時の手順

セッション開始時に、以下を**順番に**実行する:

### 手順 1: スキル自動更新チェック

```bash
python "$SKILL_HOME/git-skill-manager/scripts/auto_update.py" check
```

`--force` なしの場合、`interval_hours` 未満であればスクリプト側でスキップされる（ネットワーク負荷を抑制）。

### 手順 2: Copilot Memory 同期

VSCode Copilot Memory の内容を ltm-use へ自動インポートする:

```bash
python "$SKILL_HOME/ltm-use/scripts/sync_copilot_memory.py"
```

### 手順 3: 過去の記憶のリコール

タスクを受け取ったら、作業を始める前に関連する過去の記憶を検索する:

```bash
python "$SKILL_HOME/ltm-use/scripts/recall_memory.py" "キーワード"
```

-----

## セッション中の手順

以下の場面では、作業中に随時記憶を保存する:

- 原因の特定が難しかったバグとその解決策
- ユーザーが明示的に「覚えておいて」「記憶して」と指示したとき
- 設計上の重要な決定やトレードオフの結論

```bash
python "$SKILL_HOME/ltm-use/scripts/save_memory.py" \
  --title "タイトル" --summary "要約" --tags tag1,tag2
```

scrum-master 経由のスプリント実行中は、スプリント完了後（Phase 6 の後）にまとめて保存する。

-----

## セッション終了時の手順

セッション終了前に、そのセッションで得た知識を振り返り記憶として保存する:

```bash
python "$SKILL_HOME/ltm-use/scripts/save_memory.py" \
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

## ワークスペースドキュメントの優先

`docs/` ディレクトリにはプロジェクト固有のルール・設計・ガイドラインが含まれる。

タスクを受け取ったら、作業を始める前に `docs/` 以下のファイルをすべてコンテキストに読み込み、
その内容をスキルの指示より**優先して従うこと**。

- `docs/` の記述がスキルの指示と異なる場合、`docs/` を優先する
- `docs/` に該当する記述がない場合のみ、スキルのデフォルト動作に従う

-----

## スキルの優先順位

同名のスキルがユーザーホーム（`skill_home`）とワークスペース（`.github/skills/`）の
両方に存在する場合、**ワークスペース側を優先して使用する**。
ユーザーホーム側のバージョンは無視してよい。

-----

## コーディング行動規範（Karpathy Guidelines）

`$SKILL_HOME/karpathy-guidelines/SKILL.md` を読み込み、すべてのコーディングタスクに常時適用する。
