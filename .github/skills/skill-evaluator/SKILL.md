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
| ✅ 昇格推奨 | ok ≥ 2 かつ `pending_refinement: false` かつ broken なし | git-skill-manager promote |
| ⚠️ 要改良後昇格 | `pending_refinement: true` または broken あり | git-skill-manager refine → 改良後に promote |
| 🔄 試用継続 | ok = 1、問題なし | 報告のみ（次回のフィードバックを待つ） |

### インストール済みスキル（ホーム領域）

| 評価 | 条件 | アクション |
|---|---|---|
| ⚠️ 要改良 | `pending_refinement: true` または未改良問題あり | git-skill-manager refine（必要なら push） |
| ✅ 正常 | 問題なし | 報告のみ |

## スキル品質評価の詳細基準

skill-creator が作成時の静的品質（構造・フロントマター・説明文）を検証するのに対し、skill-evaluator は**使用履歴に基づく動的評価**を行う。以下が skill-evaluator 固有の観点。

### 問題の深刻度分類

フィードバックの `verdict` を深刻度で区別して評価する:

| verdict | 深刻度 | 評価への影響 |
|---|---|---|
| `broken` | 高 | ok 数に関わらず即要改良 |
| `needs-improvement` | 中 | 問題ありとしてカウント |
| `ok` | - | 正常動作 |

`broken` が 1 件でもある場合は昇格条件を満たさない。

### 成熟度ステージ

総フィードバック数（ok + 問題）でスキルのデータ充足度を判定する:

| ステージ | 条件 | 評価方針 |
|---|---|---|
| 初期（データ不足） | 総フィードバック < 2 | 評価保留。試用継続を優先する |
| 評価可能 | 総フィードバック 2〜4 | 通常の評価基準を適用する |
| 十分な実績 | 総フィードバック ≥ 5 | 昇格後も継続的な改良サイクルを推奨する |

### フィードバックパターンからの構造問題推察

verdict の傾向から、スキルの構造的問題を推察して改良提案に含める:

| フィードバックパターン | 推察される原因 | 改良提案 |
|---|---|---|
| `needs-improvement` が連続 | SKILL.md の手順・説明が不明確 | 記述の整理・具体例の追加を提案 |
| `broken` が複数 | scripts/ の実装不備 | スクリプトのデバッグ・テストを提案 |
| ok が増えない（長期停滞） | スコープが広すぎる可能性 | スキルの分割を提案 |
| 改良後も同じ問題が続く | 根本的な設計問題 | description・構造の見直しを提案 |

### 改良効果の評価

`refined: true` のフィードバックを除外した上で新規フィードバックを分析し、改良が実際に効果をもたらしたかを判断する:

- 改良後に `ok` が増加 → 改良効果あり（昇格に向けて継続）
- 改良後も `needs-improvement` / `broken` が続く → 改良効果不十分（再改良を推奨）

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
