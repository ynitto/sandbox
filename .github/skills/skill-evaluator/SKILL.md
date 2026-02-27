---
name: skill-evaluator
description: ワークスペーススキル（.github/skills/）とインストール済みスキル（~/.copilot/skills/）の両方を評価し、昇格・改良・試用継続を判断するスキル。「スキルを評価して」「試用中スキルを確認して」「どのスキルを昇格すべき？」「インストール済みスキルの状態を確認して」などで発動する。git-skill-manager の evaluate 操作、scrum-master Phase 6、スキル使用後に EVAL_RECOMMEND 出力があった場合に自動的に起動される。
---

# Skill Evaluator

ワークスペーススキルとインストール済みスキルのフィードバック履歴を読み取り、推奨アクションを判断して実行するスキル。

## 評価基準

### ワークスペーススキル（試用中）

| 評価 | 条件 | アクション |
|---|---|---|
| ✅ 昇格推奨 | ok ≥ 2 かつ `pending_refinement: false` | git-skill-manager promote |
| ⚠️ 要改良後昇格 | `pending_refinement: true` または broken あり | git-skill-manager refine → 改良後に promote |
| 🔄 試用継続 | ok = 1、問題なし | 報告のみ（次回のフィードバックを待つ） |

### インストール済みスキル（ホーム領域）

| 評価 | 条件 | アクション |
|---|---|---|
| ⚠️ 要改良 | `pending_refinement: true` または未改良問題あり | git-skill-manager refine（必要なら push） |
| ✅ 正常 | 問題なし | 報告のみ |

## ワークフロー

### 1. 評価スクリプトを実行する

```bash
# 全スキル（ワークスペース + インストール済み）を評価
python .github/skills/skill-evaluator/scripts/evaluate.py

# ワークスペーススキルのみ
python .github/skills/skill-evaluator/scripts/evaluate.py --type workspace

# インストール済みスキルのみ
python .github/skills/skill-evaluator/scripts/evaluate.py --type installed

# 特定スキルのみ（種別を問わず検索）
python .github/skills/skill-evaluator/scripts/evaluate.py --skill <skill-name>
```

### 2. 結果をユーザーに提示する

スクリプトの出力をそのままユーザーに見せる。出力例:

```
📋 ワークスペーススキル（試用中）:

  my-skill                        ok:2 問題:0  → ✅ 昇格推奨
  other-skill                     ok:1 問題:1  → ⚠️  要改良後昇格
  new-skill                       ok:1 問題:0  → 🔄 試用継続

昇格推奨: my-skill
要改良:   other-skill

📋 インストール済みスキル（ホーム領域）:

  docx-converter                  ok:3 問題:2  → ⚠️  要改良  [team-skills]
  image-resizer                   ok:5 問題:0  → ✅ 正常    [local]

要改良: docx-converter
正常:   image-resizer
```

### 3. ユーザーのアクションを確認して実行する

**ワークスペーススキルに昇格推奨がある場合:**
```
「my-skill を昇格しますか？
 昇格すると ~/.copilot/skills/ にコピーされ、他のプロジェクトでも使えるようになります。
 1. 昇格する（git-skill-manager promote）
 2. もう少し試用する
 3. スキップ」
```

**要改良スキルがある場合（ワークスペース・インストール済み共通）:**
```
「[skill-name] に改善待ちのフィードバックがあります。
 1. 今すぐ改良する（git-skill-manager refine）
 2. 後で改良する
 3. スキップ」
```

### 4. 各アクションを実行する

- **昇格**: `.github/skills/git-skill-manager/SKILL.md` を読んで `promote` 操作の手順に従う
- **改良**: `.github/skills/git-skill-manager/SKILL.md` を読んで `refine` 操作の手順に従う
  - インストール済みスキルかつ `source_repo` がリポジトリ名の場合: 改良後に `push` 操作を提案する
- **試用継続・スキップ**: 報告のみで次へ進む

## 起動元別の動作

| 起動元 | 対象 | 確認の省略 |
|---|---|---|
| ユーザー直接 / git-skill-manager evaluate | 全スキル（`--type all`） | なし（対話的に進める） |
| scrum-master Phase 6 | 全ワークスペーススキル（`--type workspace`） | なし |
| record_feedback.py の EVAL_RECOMMEND 出力 | フィードバック対象のスキル1件（`--skill <name>`） | なし |

`EVAL_RECOMMEND: promote` または `EVAL_RECOMMEND: refine` が record_feedback.py から出力された場合、
そのスキルだけを対象に `--skill <name>` で評価スクリプトを実行する。
スキルの種別（ワークスペース/インストール済み）は evaluate.py が自動判別する。
