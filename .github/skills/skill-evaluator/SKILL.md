---
name: skill-evaluator
description: ワークスペーススキル（.github/skills/ に置かれた試用中スキル）を評価し、昇格・改良・試用継続を判断するスキル。「スキルを評価して」「試用中スキルを確認して」「どのスキルを昇格すべき？」「ワークスペーススキルを整理して」などで発動する。git-skill-manager の evaluate 操作、scrum-master Phase 6、スキル使用後に EVAL_RECOMMEND 出力があった場合に自動的に起動される。
---

# Skill Evaluator

ワークスペーススキルのフィードバック履歴を読み取り、昇格・改良・試用継続を判断して実行するスキル。

## 評価基準

| 評価 | 条件 | アクション |
|---|---|---|
| ✅ 昇格推奨 | ok ≥ 2 かつ `pending_refinement: false` | git-skill-manager promote |
| ⚠️ 要改良後昇格 | `pending_refinement: true` または broken あり | git-skill-manager refine → 改良後に promote |
| 🔄 試用継続 | ok = 1、問題なし | 報告のみ（次回のフィードバックを待つ） |

## ワークフロー

### 1. 評価スクリプトを実行する

```bash
python .github/skills/skill-evaluator/scripts/evaluate.py
```

特定スキルのみ評価する場合:

```bash
python .github/skills/skill-evaluator/scripts/evaluate.py --skill <skill-name>
```

### 2. 結果をユーザーに提示する

スクリプトの出力をそのままユーザーに見せる。出力例:

```
📋 ワークスペーススキルの評価:

  my-skill      ok:2 問題:0  → ✅ 昇格推奨
  other-skill   ok:1 問題:1  → ⚠️  要改良後昇格
  new-skill     ok:1 問題:0  → 🔄 試用継続

昇格推奨: my-skill
要改良:   other-skill
```

### 3. ユーザーのアクションを確認して実行する

昇格推奨スキルがある場合:
```
「my-skill を昇格しますか？
 昇格すると ~/.copilot/skills/ にコピーされ、他のプロジェクトでも使えるようになります。
 1. 昇格する（git-skill-manager promote）
 2. もう少し試用する
 3. スキップ」
```

要改良スキルがある場合:
```
「other-skill に改善待ちのフィードバックがあります。
 1. 今すぐ改良する（git-skill-manager refine）
 2. 後で改良する
 3. スキップ」
```

### 4. 各アクションを実行する

- **昇格**: `.github/skills/git-skill-manager/SKILL.md` を読んで `promote` 操作の手順に従う
- **改良**: `.github/skills/git-skill-manager/SKILL.md` を読んで `refine` 操作の手順に従う
- **試用継続・スキップ**: 報告のみで次へ進む

## 起動元別の動作

| 起動元 | 対象 | 確認の省略 |
|---|---|---|
| ユーザー直接 / git-skill-manager evaluate | 全ワークスペーススキル | なし（対話的に進める） |
| scrum-master Phase 6 | 全ワークスペーススキル | なし |
| record_feedback.py の EVAL_RECOMMEND 出力 | フィードバック対象のスキル1件 | なし |

`EVAL_RECOMMEND: promote` または `EVAL_RECOMMEND: refine` が record_feedback.py から出力された場合、
そのスキルだけを対象に `--skill <name>` で評価スクリプトを実行する。
