---
name: agent-reviewer
description: 入力された成果物を分析し、適切な perspectives を自律選択してサブエージェントを並列起動し、集約レビューを返す。「レビューして」「コードを確認して」「設計をレビューして」「ドキュメントをチェックして」「品質確認して」などの依頼で発動。sprint-reviewer は含まない。
metadata:
  version: 3.0.0
  tier: stable
  category: review
  tags:
    - review
    - orchestration
---

# agent-reviewer

入力された成果物を分析して適切な perspectives を選択し、**perspective ごとに自身をサブエージェントとして並列起動**して結果を集約する。

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR` とする。
各 perspective の手順は `${SKILL_DIR}/references/` 以下の参照ファイルに定義されている。

---

## perspectives と参照ファイルの対応

| perspective | 参照ファイル | 主な対象 |
|---|---|---|
| `functional` | `${SKILL_DIR}/references/functional.md` | プロダクションコードの正確性・堅牢性 |
| `ai-antipattern` | `${SKILL_DIR}/references/ai-antipattern.md` | AI 生成コード特有の問題 |
| `architecture` | `${SKILL_DIR}/references/architecture.md` | モジュール・レイヤー間の構造 |
| `security` | `${SKILL_DIR}/references/security.md` | OWASP Top 10・脆弱性 |
| `design` | `${SKILL_DIR}/references/design.md` | クラス・モジュール設計・SOLID |
| `test` | `${SKILL_DIR}/references/test.md` | テストコードの品質・網羅性 |
| `document` | `${SKILL_DIR}/references/document.md` | 要件定義書・設計書・仕様書 |

---

## 実行プロトコル

### Step 1: perspectives の選択と並列起動

> ### ⛔ STOP — `runSubagent` を今すぐ起動する
>
> **集約レビューはサブエージェントが行う。このインスタンス自身は直接レビューしない。**
> perspectives を決定したら即座に `runSubagent` を呼び出すこと。

対象ファイル・依頼内容から perspectives を決定し、**同一ターン内で全 perspective の `runSubagent` をまとめて起動**する:

| 対象・依頼内容 | 使用する perspectives |
|---|---|
| プロダクションコード（汎用） | `functional`, `ai-antipattern`, `architecture` |
| プロダクションコード + テストファイル混在 | `functional`, `ai-antipattern`, `architecture`, `test` |
| セキュリティ関連コード（認証・DB・API・入力処理） | `functional`, `ai-antipattern`, `architecture`, `security` |
| テストコードのみ | `test` |
| セキュリティ診断 | `security` |
| クラス・モジュール設計 | `design`, `architecture` |
| ドキュメント・仕様書・設計書 | `document` |

起動テンプレート（各 perspective ごとに `runSubagent` 1 件。全件を同一ターン内で開始する）:

```
agent-reviewer スキルで以下をレビューしてください。

手順: まず agent-reviewer スキルの SKILL.md（${SKILL_DIR}/SKILL.md）を読んで手順に従ってください。

perspective: [選択した perspective]

レビュー対象:
  変更ファイル: [対象ファイルの一覧]

コンテキスト（あれば）:
  [依頼内容・背景]

注意: ユーザーへの確認・対話は行わず、レビューのみ実施すること。
```

全サブエージェントの完了を待ち、Step 3（集約）へ進む。

---

### Step 2: 単一 perspective のレビュー実行（内部サブエージェント用）

1. 内部的に指定された perspective に対応する参照ファイルを読み込む
2. 参照ファイルの手順に従ってレビューを実施する
3. 参照ファイルで定義された出力形式・判定スキーマで結果を返す

---

### Step 3: 集約

各 perspective は **共通の判定スキーマ** を返す:

- `verdict`: `LGTM | REQUEST_CHANGES`
- `severity_summary`: `critical` / `warning` / `suggestion`
- `blocking_issues[*].severity`: `Critical | Warning`

**集約判定ルール**:
- 全 perspective が LGTM → **総合判定: LGTM ✅**
- いずれかが Request Changes → **総合判定: Request Changes ❌**（最も厳しい判定を採用）

**集約レポートフォーマット**:

```
## 総合レビュー結果: [LGTM ✅ | Request Changes ❌]

### 実施した perspectives

| perspective | 判定 | Critical | Warning | Suggestion |
|---|---|---|---|---|
| functional | LGTM ✅ / Request Changes ❌ | 0 | 0 | 0 |
| ai-antipattern | ... | | | |
| architecture | ... | | | |

### 重大な指摘（Critical / Warning）

#### [perspective] より
- [severity]: [summary] — [location]

（指摘なしの場合は「なし」）
```

集約スキーマ（`<!-- verdict-json -->` で囲んで出力）:
```json
{
  "skill": "agent-reviewer",
  "verdict": "LGTM | REQUEST_CHANGES",
  "blocking": false,
  "perspectives_executed": ["functional", "ai-antipattern", "architecture"],
  "perspective_results": [
    {
      "perspective": "functional",
      "verdict": "LGTM | REQUEST_CHANGES",
      "blocking": false,
      "severity_summary": {"critical": 0, "warning": 0, "suggestion": 0}
    }
  ],
  "aggregated_blocking_issues": [
    {
      "from_perspective": "functional",
      "severity": "Critical | Warning",
      "summary": "問題の要約（1行）",
      "location": "ファイル名:行番号"
    }
  ]
}
```

---

## スクリプト

`${SKILL_DIR}/scripts/` に以下のユーティリティスクリプトが含まれる。

| スクリプト | 用途 |
|---|---|
| `diff_analyzer.py` | `git diff` 出力を解析し、レビュー対象ファイルと変更行を構造化する |
| `pr_comment_formatter.py` | レビュー結果 JSON を GitHub PR コメント用 Markdown に変換する |

**diff_analyzer.py の使い方**:
```bash
# diff を解析してレビューコンテキストを出力
git diff HEAD~1 | python ${SKILL_DIR}/scripts/diff_analyzer.py

# サマリーのみ表示
git diff | python ${SKILL_DIR}/scripts/diff_analyzer.py --summary

# JSON 形式で出力
git diff HEAD~1 | python ${SKILL_DIR}/scripts/diff_analyzer.py --json
```

**pr_comment_formatter.py の使い方**:
```bash
# レビュー結果 JSON を PR コメント形式に変換
python ${SKILL_DIR}/scripts/pr_comment_formatter.py --file review_result.json

# stdin から読み込み
echo '{...}' | python ${SKILL_DIR}/scripts/pr_comment_formatter.py
```

---

## ガードレール

| 制限 | 値 |
|---|---|
| 外部呼び出し時の mode | 集約レビューのみ |
| 並列起動上限 | 最大4 perspectives |
| スキップ | 禁止（指定 / 選択された perspective は必ず実施） |
