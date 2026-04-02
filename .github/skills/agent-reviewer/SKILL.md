---
name: agent-reviewer
description: 指定された観点でコード・ドキュメントをレビューし、LGTM / Request Changes を判定する。「レビューして」「コードを確認して」「設計をレビューして」「ドキュメントをチェックして」「品質確認して」などの依頼で発動。観点（perspective）が指定された場合はその観点のみ実行する。複数観点の並列レビューは呼び出し元（skill-mentor / scrum-master）が複数の agent-reviewer サブエージェントを起動して行う。sprint-reviewer は含まない。
metadata:
  version: 2.0.0
  tier: stable
  category: review
  tags:
    - review
    - single-perspective
---

# agent-reviewer

指定された **観点（perspective）** に従ってレビューを実施し、判定結果を返す。

**このスキル自身がサブエージェントを起動することはない。** レビューロジックはすべてこのスキルの参照ファイル内に内包されている。

複数観点での並列レビューは、呼び出し元（skill-mentor / scrum-master）が複数の agent-reviewer サブエージェントを同時起動することで実現する。

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR` とする。
各観点の手順は `${SKILL_DIR}/references/perspectives/` 以下の参照ファイルに定義されている。

---

## 観点（perspective）と参照ファイルの対応

| perspective | 参照ファイル | 主な対象 |
|---|---|---|
| `機能` | `${SKILL_DIR}/references/perspectives/機能.md` | プロダクションコードの正確性・堅牢性 |
| `AIアンチパターン` | `${SKILL_DIR}/references/perspectives/AIアンチパターン.md` | AI 生成コード特有の問題 |
| `アーキテクチャ` | `${SKILL_DIR}/references/perspectives/アーキテクチャ.md` | モジュール・レイヤー間の構造 |
| `セキュリティ` | `${SKILL_DIR}/references/perspectives/セキュリティ.md` | OWASP Top 10・脆弱性 |
| `設計` | `${SKILL_DIR}/references/perspectives/設計.md` | クラス・モジュール設計・SOLID |
| `テスト` | `${SKILL_DIR}/references/perspectives/テスト.md` | テストコードの品質・網羅性 |
| `ドキュメント` | `${SKILL_DIR}/references/perspectives/ドキュメント.md` | 要件定義書・設計書・仕様書 |

---

## 実行プロトコル

### Step 1: perspective を確認する

呼び出し元から渡された `perspective` パラメータを確認する。

**perspective が指定されている場合（サブエージェントとして呼ばれた場合）**:
→ 指定された perspective に対応する参照ファイルを読み込み、その手順に従ってレビューを実施する（Step 2 へ）

**perspective が指定されていない場合（ユーザー直接呼び出し）**:
→ 以下の自動選択ルールに従って perspective を決定し、順次レビューを実施する（Step 1a へ）

### Step 1a: perspective の自動選択（直接呼び出し時のみ）

対象ファイル・依頼内容から使用する perspective を決定する:

| 対象・依頼内容 | 使用する perspective |
|---|---|
| ドキュメント・仕様書・設計書 | ドキュメント |
| テストコードのみ | テスト |
| プロダクションコード（汎用品質確認） | 機能, AIアンチパターン, アーキテクチャ |
| セキュリティ診断 | セキュリティ, 機能 |
| クラス・モジュール設計 | 設計, アーキテクチャ |
| コード + テストコード混在 | 機能, テスト |
| 明示的な複数観点指定 | 指定された全 perspective |

決定した perspective を順次実行する（並列不可。直接呼び出し時の制約）。

### Step 2: レビューを実施する

1. 指定された perspective の参照ファイルを読み込む
2. 参照ファイルの手順に従ってレビューを実施する
3. 参照ファイルで定義された出力形式で結果を報告する（判定スキーマ含む）

複数の perspective を順次実行する場合は、各 perspective の結果を個別に出力した後、Step 3 で集約する。

### Step 3: 集約（複数 perspective を実行した場合のみ）

複数 perspective を実行した場合、全結果を集約して総合判定を報告する:

**集約判定ルール**:
- 全 perspective が LGTM / Approved / GOOD → **総合判定: LGTM ✅**
- いずれかが Request Changes / Needs Revision / NEEDS_IMPROVEMENT → **総合判定: Request Changes ❌**

**集約レポートフォーマット**:

```
## 総合レビュー結果: [LGTM ✅ | Request Changes ❌]

### 実施した観点

| perspective | 判定 | Critical | Warning | Suggestion |
|---|---|---|---|---|
| 機能 | LGTM ✅ / Request Changes ❌ | 0 | 0 | 0 |

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
  "perspectives_executed": ["機能", "AIアンチパターン", "アーキテクチャ"],
  "perspective_results": [
    {
      "perspective": "機能",
      "verdict": "LGTM | REQUEST_CHANGES",
      "blocking": false,
      "severity_summary": {"critical": 0, "warning": 0, "suggestion": 0}
    }
  ],
  "aggregated_blocking_issues": [
    {
      "from_perspective": "機能",
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
| サブエージェント起動 | 禁止（このスキル自身はサブエージェントを起動しない） |
| スキップ | 禁止（指定された perspective は必ず実施する） |
| 自動選択時の perspective 数上限 | 最大3件（優先度でフィルタする） |
