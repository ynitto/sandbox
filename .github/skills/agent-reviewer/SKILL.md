---
name: agent-reviewer
description: 指定された perspective でコード・ドキュメントをレビューし、LGTM / Request Changes を判定する。perspective が指定されない場合は対象を分析して適切な perspectives を選択し、サブエージェントを並列起動して集約する。「レビューして」「コードを確認して」「設計をレビューして」「ドキュメントをチェックして」「品質確認して」などの依頼で発動。sprint-reviewer は含まない。
metadata:
  version: 2.1.0
  tier: stable
  category: review
  tags:
    - review
    - single-perspective
    - orchestration
---

# agent-reviewer

**perspective が指定されている場合（サブエージェントとして呼ばれた場合）**:
指定された perspective のみを実行する。サブエージェントは起動しない。

**perspective が指定されていない場合（ユーザー直接呼び出し）**:
対象を分析して適切な perspectives を選択し、**perspective ごとに自身をサブエージェントとして並列起動**して結果を集約する。

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

### Step 1: perspective の確認

渡された `perspective` パラメータを確認する:

- **perspective が指定されている** → Step 2（単一 perspective 実行）へ
- **perspective が指定されていない** → Step 1a（perspective 選択と並列起動）へ

---

### Step 1a: perspectives の選択と並列起動（直接呼び出し時のみ）

> ### ⛔ STOP — `runSubagent` を今すぐ起動する
>
> **集約レビューはサブエージェントが行う。このインスタンス自身は直接レビューしない。**
> perspectives を決定したら即座に `runSubagent` を呼び出すこと。

対象ファイル・依頼内容から perspectives を決定し、**単一メッセージで並列起動**する:

| 対象・依頼内容 | 使用する perspectives |
|---|---|
| プロダクションコード（汎用） | `functional`, `ai-antipattern`, `architecture` |
| プロダクションコード + テストファイル混在 | `functional`, `ai-antipattern`, `architecture`, `test` |
| セキュリティ関連コード（認証・DB・API・入力処理） | `functional`, `ai-antipattern`, `architecture`, `security` |
| テストコードのみ | `test` |
| セキュリティ診断 | `security` |
| クラス・モジュール設計 | `design`, `architecture` |
| ドキュメント・仕様書・設計書 | `document` |
| 明示的な複数観点指定 | 指定された perspectives |

起動テンプレート（各 perspective ごとに1つ、単一メッセージに並べる）:

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

### Step 2: 単一 perspective のレビュー実行（perspective 指定時）

1. 指定された perspective に対応する参照ファイルを読み込む
2. 参照ファイルの手順に従ってレビューを実施する
3. 参照ファイルで定義された出力形式・判定スキーマで結果を返す

---

### Step 3: 集約（直接呼び出しで複数 perspective を実行した場合のみ）

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

## ガードレール

| 制限 | 値 |
|---|---|
| perspective 指定時のサブエージェント起動 | 禁止（直接レビューのみ） |
| perspective 未指定時の並列起動上限 | 最大4 perspectives |
| スキップ | 禁止（指定 / 選択された perspective は必ず実施） |
