---
name: skill-evaluator
description: ワークスペーススキルとインストール済みスキルを評価し、昇格・改良・試用継続を判断するスキル。「スキルを評価して」「試用中スキルを確認して」「どのスキルを昇格すべき？」などで発動する。git-skill-manager の evaluate 操作・scrum-master Phase 6・EVAL_RECOMMEND 出力時にも自動起動される。
metadata:
  version: 1.3.0
  tier: core
  category: meta
  tags:
    - skill-evaluation
    - quality-check
---

# Skill Evaluator

スキルの**静的品質チェック**と**動的評価（フィードバック + メトリクス）**を組み合わせ、
昇格・改良・試用継続の推奨アクションと定量品質スコアを算出するスキル。

`<SKILLS_BASE>` は `<AGENT_HOME>/skills` または `<workspace-skill-dir>` を指す。

## ワークフロー

### Step 0. 静的品質チェック

```bash
python <SKILLS_BASE>/skill-evaluator/scripts/quality_check.py [--skill <name>]
```

ERROR がある場合は修正してから次のステップへ進む。WARN は文脈上問題なければ無視してよい。
チェックコードの一覧と解釈は `references/quality-check-codes.md` を参照。

静的チェックのみでは不十分な場合がある。手動テスト（トリガーテスト・動作テスト）との組み合わせについては `references/testing-guide.md` を参照。

### Step 1. トリガー評価スクリプトを実行する

descriptionが意図通りに発動するかを定量的に評価する。

```bash
# 環境確認（Claude Code か Copilot/Kiro かを判定）
python <SKILLS_BASE>/skill-evaluator/scripts/eval_trigger.py --check-env

# 単一クエリでテスト
python <SKILLS_BASE>/skill-evaluator/scripts/eval_trigger.py \
    --skill-path <SKILLS_BASE>/<skill-name> \
    --query "スキルを評価して" --expected true

# eval set JSON に対して一括テスト
python <SKILLS_BASE>/skill-evaluator/scripts/eval_trigger.py \
    --skill-path <SKILLS_BASE>/<skill-name> \
    --eval-set eval.json --verbose

# トリガーのシミュレーション・競合確認
python <SKILLS_BASE>/skill-evaluator/scripts/simulate_trigger.py "<ユーザーリクエスト>"
python <SKILLS_BASE>/skill-evaluator/scripts/simulate_trigger.py --conflicts
```

評価モード:
- **高精度モード**（Claude Code）: `claude -p` を使って実際のLLM判定を行う
- **簡易モード**（Copilot/Kiro）: バイグラム類似度でヒューリスティクス判定する

eval set JSON 形式:
```json
[
  {"query": "スキルを評価して", "should_trigger": true},
  {"query": "スキルを作って", "should_trigger": false}
]
```

トリガーテストの詳細手順は `references/testing-guide.md` を参照。

### Step 2. 動的評価スクリプトを実行する

```bash
# 全スキル（ワークスペース + インストール済み）を評価
python <SKILLS_BASE>/skill-evaluator/scripts/evaluate.py

# メトリクスを最新化してから評価（推奨）
python <SKILLS_BASE>/skill-evaluator/scripts/evaluate.py --auto-collect

# フィルタ
python <SKILLS_BASE>/skill-evaluator/scripts/evaluate.py --type workspace   # ワークスペースのみ
python <SKILLS_BASE>/skill-evaluator/scripts/evaluate.py --type installed   # インストール済みのみ
python <SKILLS_BASE>/skill-evaluator/scripts/evaluate.py --skill <name>     # 特定スキルのみ
```

`--auto-collect` を付けると評価前に `git-skill-manager` の `metrics_collector.py` を実行し、
`metrics-log.jsonl` から使用回数・Pass率・リトライ回数を集計してレジストリに反映する。

出力例:

```
📊 メトリクスを自動集計しました
   📊 3 スキルを集計、3 スキルのレジストリを更新しました

📋 ワークスペーススキル（試用中）:

  my-skill                        ok:2 問題:0  📊 評価可  スコア:78/100(B)  → ✅ 昇格推奨
                                    [実行:8回 / Pass:88% / retry:1.5 / 7d:3回]
  other-skill                     ok:1 問題:1  📊 初期    スコア:35/100(D)  → ⚠️  要改良後昇格
                                    [実行:2回 / Pass:50% / retry:3.0]
  new-skill                       ok:1 問題:0  📊 初期    スコア:-          → 🔄 試用継続

昇格推奨: my-skill
要改良:   other-skill

📋 インストール済みスキル（ホーム領域）:

  docx-converter                  ok:3 問題:2  📊 評価可  スコア:52/100(C)  → ⚠️  要改良  [team-skills]
                                    [実行:12回 / Pass:60% / retry:2.0 / 7d:4回]
  image-resizer                   ok:5 問題:0  📊 実績十分 スコア:91/100(A) → ✅ 正常    [local]
                                    [実行:25回 / Pass:95% / retry:0.5 / 7d:8回]

要改良: docx-converter
正常:   image-resizer
```

### Step 3. ユーザーに確認してアクションを実行する

**昇格推奨がある場合:**

```
「my-skill を昇格しますか？
 昇格すると <AGENT_HOME>/skills/ にコピーされ、他のプロジェクトでも使えるようになります。
 1. 昇格する（git-skill-manager promote）
 2. もう少し試用する
 3. スキップ」
```

**要改良がある場合（ワークスペース・インストール済み共通）:**

```
「[skill-name] に改善待ちのフィードバックがあります。
 1. 今すぐ改良する（git-skill-manager refine）
 2. 後で改良する
 3. スキップ」
```

各アクションの実行方法は `<SKILLS_BASE>/git-skill-manager/SKILL.md` の該当操作（`promote` / `refine`）を参照する。
インストール済みスキルかつ `source_repo` がリポジトリ名の場合、改良後に `push` 操作を提案する。

---

## 評価基準

### ワークスペーススキル（試用中）

| 評価 | 条件 | アクション |
|---|---|---|
| ✅ 昇格推奨 | ok ≥ 2 かつ `pending_refinement: false` かつ broken なし かつ成熟度 ≠ 初期 | git-skill-manager promote |
| ⚠️ 要改良後昇格 | `pending_refinement: true` または broken あり | git-skill-manager refine → 改良後に promote |
| 🔄 試用継続 | ok = 1 かつ問題なし（またはデータ不足） | 報告のみ |

### インストール済みスキル（ホーム領域）

| 評価 | 条件 | アクション |
|---|---|---|
| ⚠️ 要改良 | `pending_refinement: true` または未改良問題あり | git-skill-manager refine（必要なら push） |
| ✅ 正常 | 問題なし | 報告のみ |

### verdict の深刻度

| verdict | 深刻度 | 影響 |
|---|---|---|
| `broken` | 高 | ok 数に関わらず即要改良。昇格条件を満たさない |
| `needs-improvement` | 中 | 問題ありとしてカウント |
| `ok` | — | 正常動作 |

### 成熟度ステージ

| ステージ | 条件 | 方針 |
|---|---|---|
| 初期（データ不足） | 総フィードバック < 2 | 評価保留。試用継続を優先 |
| 評価可能 | 総フィードバック 2〜4 | 通常の評価基準を適用 |
| 十分な実績 | 総フィードバック ≥ 5 | 昇格後も継続的な改良サイクルを推奨 |

---

## 定量品質スコア（0〜100）

`evaluate.py` が `git-skill-manager` の `metrics` フィールドから自動算出する。
スコアは推奨アクション（promote / refine）の決定には使用しない。改良優先度の参考として使う。

### スコア構成

| 指標 | 最大点 | 算出方法 |
|---|---|---|
| Pass率（ok_rate） | 70 点 | `ok_rate × 70` |
| 実績（使用回数） | 20 点 | `min(total_executions / 10, 1.0) × 20`（10 回以上で満点） |
| リトライ少なさ | 10 点 | `max(0, 10 - avg_subagent_calls × 2)`（0 回で 10 点、5 回以上で 0 点） |

### グレード

| グレード | スコア |
|---|---|
| A | 80〜100 |
| B | 60〜79 |
| C | 40〜59 |
| D | 0〜39 |

`metrics` が未集計（`--auto-collect` 未実行またはログなし）の場合はスコア `-` で表示される。

---

## 改良ガイドライン

詳細なテスト手順・回帰テスト・よくある評価ミスは [references/testing-guide.md](references/testing-guide.md) を参照。

### フィードバックパターンから問題を推察する

| パターン | 推察される原因 | 改良提案 |
|---|---|---|
| `needs-improvement` が連続 | SKILL.md の手順・説明が不明確 | 記述の整理・具体例の追加 |
| `broken` が複数 | scripts/ の実装不備 | スクリプトのデバッグ・テスト |
| ok が増えない（長期停滞） | スコープが広すぎる | スキルの分割 |
| 改良後も同じ問題が続く | 根本的な設計問題 | description・構造の見直し |

### 改良効果の評価

`refined: true` のフィードバックを除外した上で新規フィードバックを分析し、改良が効果をもたらしたかを判断する:

- 改良後に `ok` が増加 → 効果あり（昇格に向けて継続）
- 改良後も `needs-improvement` / `broken` が続く → 再改良を推奨

### refine 実施時の指針

- **一般化する**: 少数の使用例に過度に合わせず、多様なプロンプトで機能するよう一般化する
- **スリムに保つ**: 効果を発揮していない記述を削除する
- **「なぜ」を説明する**: MUST / NEVER の多用より、理由の説明のほうが効果的
- **繰り返し作業をバンドルする**: 同じ処理が複数ケースで書かれていたら `scripts/` へ
- **description を見直す**: undertrigger が疑われる場合は、発動条件を積極的・明示的に記述する

---

## 起動元別の動作

| 起動元 | 対象 | モード |
|---|---|---|
| ユーザー直接 / git-skill-manager evaluate | 全スキル（`--type all`） | 通常モード（対話的に進める） |
| scrum-master Phase 6 | 全スキル（`--type all`） | レポートのみモード |
| record_feedback.py の EVAL_RECOMMEND 出力 | 対象スキル 1 件（`--skill <name>`） | 通常モード |

### レポートのみモード（scrum-master Phase 6 から起動された場合）

VSCode Copilot ではサブエージェントがユーザーと対話できないため、Step 2（確認・実行）は行わない。
代わりに以下の形式でアクション一覧を返し、scrum-master が判断・実行する:

```
評価結果（ワークスペース）:
- [skill-name]: [昇格推奨 / 要改良後昇格 / 試用継続] — スコア:[N]/100([グレード]) — [理由1文]

評価結果（インストール済み）:
- [skill-name]: [要改良 / 正常] — スコア:[N]/100([グレード]) — [理由1文] — source_repo: [repo-name または local]
```

インストール済みスキルで「要改良」かつ `source_repo` がリポジトリ名の場合、scrum-master が改良後に push も提案する。

`EVAL_RECOMMEND: promote` または `EVAL_RECOMMEND: refine` が `record_feedback.py` から出力された場合、
そのスキルだけを対象に `--skill <name>` で評価スクリプトを実行する。
