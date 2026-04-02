---
name: agent-reviewer
description: レビューオーケストレーター。依頼内容を分析し、code-reviewer・test-reviewer・security-reviewer・architecture-reviewer・design-reviewer・document-reviewer の中から適切なスキルを選択してサブエージェントで並列起動し、全結果を集約する。「レビューして」「コードを確認して」「設計をレビューして」「ドキュメントをチェックして」「品質確認して」などの依頼で発動。sprint-reviewer は含まない。skill-mentor・scrum-master の多角レビュー（機能/AIアンチパターン/アーキテクチャ）もこのスキルが担う。
metadata:
  version: 1.0.0
  tier: stable
  category: review
  tags:
    - orchestration
    - review
    - parallel
---

# agent-reviewer

レビュー依頼の内容を分析し、適切なレビュースキルを選択して **サブエージェントで並列起動**し、全結果を集約して統一レポートを返す。

**このスキルが直接レビューを行うことはない。** すべてのレビューはサブエージェント（専門レビュースキル）が担う。

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR`、その親ディレクトリを `SKILLS_DIR` とする。
他スキルは `${SKILLS_DIR}/[skill-name]/SKILL.md` で参照する。

---

## Step 1: レビューモードを判定する

受け取った情報から **モード** を決定する:

| モード | 判定条件 |
|--------|---------|
| **タスクレビュー** | コンテキストに「タスクゴール」「完了条件」「変更ファイル」が含まれている（skill-mentor / scrum-master から呼ばれる場合） |
| **直接レビュー** | ユーザーから直接レビュー依頼を受けた場合 |

---

## Step 2: 起動するレビュースキルを選択する

以下の選択ルールに従って起動するスキルを決定する。**過剰選択を避け、タスクに必要なスキルのみ選ぶ。**

### タスクレビューモード（skill-mentor / scrum-master 経由）

コード変更の有無に応じて起動するスキルを決定する:

**コード変更あり** → 以下の3観点を**同時に**並列起動する:

1. **機能レビュー** — 変更ファイルの種類に応じて選択:
   - プロダクションコードあり → **code-reviewer**
   - テストファイルあり（`*.test.*`・`*.spec.*`・`tests/`・`__tests__/` 等） → **test-reviewer**（プロダクションコードも含む場合は code-reviewer と並列）
   - ドキュメント/仕様書のみ → **document-reviewer**

2. **AIアンチパターンレビュー** — `${SKILLS_DIR}/agentic-code-evaluator/SKILL.md` を確認:
   - 見つかった場合 → **agentic-code-evaluator**
   - 見つからない場合 → **code-reviewer**（AI生成コード特有の臭い観点に絞って実施するよう指示）

3. **アーキテクチャレビュー** — **architecture-reviewer**

**コード変更なし（調査・ドキュメントのみ）** → 機能レビュー1件のみ（軽量レビュー）

### 直接レビューモード（ユーザーから直接依頼）

依頼内容と対象ファイルを分析して選択する:

| 対象・依頼内容 | 起動するスキル |
|---|---|
| ドキュメント・仕様書・設計書 | document-reviewer |
| テストコードのみ | test-reviewer |
| コード（汎用品質確認） | code-reviewer |
| コード + セキュリティ重視（認証・DB・API・入力処理を含む） | code-reviewer + security-reviewer |
| コード + テストコード混在 | code-reviewer + test-reviewer |
| セキュリティ診断 | security-reviewer |
| クラス・モジュール設計 | design-reviewer |
| クラス・モジュール設計 + 複数モジュール間の依存関係 | design-reviewer + architecture-reviewer |
| アーキテクチャ・システム設計 | architecture-reviewer |
| 複合依頼（明示的に複数観点の指定あり） | 指定された観点に対応するスキルを全て選択 |

---

## Step 3: 並列起動する

> ### ⛔ STOP — `runSubagent` を今すぐ起動する
>
> **レビューはサブエージェントが行う。agent-reviewer は直接レビューしない。**
> Step 2 で選定したスキルを **単一メッセージに並べて同時起動** すること。
> 理由や条件を考えてはならない。

Step 2 で選定した全スキルを **単一メッセージ** で並列起動する:

```
[runSubagent: skill-A, 以下の指示を渡す]
[runSubagent: skill-B, 以下の指示を渡す]
（以降、選定したスキル分だけ並べる）
```

### 各サブエージェントへの指示テンプレート

#### 機能レビュー / 直接レビュー用

```
[skill-name] スキルで以下をレビューしてください。

手順: まず [skill-name] スキルの SKILL.md（${SKILLS_DIR}/[skill-name]/SKILL.md）を読んで手順に従ってください。

レビュー対象:
  変更ファイル: [変更・作成したファイルの一覧。なければ「なし」]

コンテキスト（タスクレビューモードの場合のみ）:
  タスクゴール: [タスクの目的・実装内容]
  完了条件: [done_criteria]
  タスク結果サマリー: [タスク実行結果のサマリー]

注意: ユーザーへの確認・対話は行わず、レビューのみ実施すること。
レビュー結果を判定スキーマ（machine-readable）形式で出力すること。

結果を以下の形式で返してください:
レビュー観点: [機能 / テスト / ドキュメント / セキュリティ / 設計 / アーキテクチャ]
使用したレビュースキル: [skill-name]
レビュー結果: LGTM ✅ / Request Changes ❌
重大な指摘件数: [N件]
完了条件の充足: 満たす / 満たさない / 該当なし
主な指摘: [重大な指摘がある場合は要約。なければ「なし」]
```

#### AIアンチパターンレビュー用

```
以下のタスクの成果物を AI アンチパターン観点でレビューしてください。

手順: まず agentic-code-evaluator スキルの SKILL.md（${SKILLS_DIR}/agentic-code-evaluator/SKILL.md）を読んで手順に従ってください。

コンテキスト:
  タスクゴール: [タスクの目的・実装内容]
  完了条件: [done_criteria]
  タスク結果サマリー: [タスク実行結果のサマリー]
  変更ファイル: [変更・作成したファイルの一覧]

注意: ユーザーへの確認・対話は行わず、レビューのみ実施すること。

結果を以下の形式で返してください:
レビュー観点: AIアンチパターン
使用したレビュースキル: [スキル名 または「なし（直接レビュー）」]
レビュー結果: LGTM ✅ / Request Changes ❌
検出された問題:
- [種類]: [該当箇所] — [問題の説明] — [修正案]
（検出なしの場合は「なし」）
重大な指摘件数: [N件]
```

---

## Step 4: 結果を集約して報告する

全サブエージェントの完了を待ち、以下の手順で集約する。

### 集約判定ルール

- 全スキルが LGTM / Approved / GOOD → **総合判定: LGTM ✅**
- いずれかが Request Changes / Needs Revision / NEEDS_IMPROVEMENT → **総合判定: Request Changes ❌**（最も厳しい判定を採用）

### 報告フォーマット

```
## レビュー結果: [LGTM ✅ | Request Changes ❌]

### 実施したレビュー

| スキル | 判定 | Critical | Warning | Suggestion |
|--------|------|---------|---------|-----------|
| [skill-name] | LGTM ✅ / Request Changes ❌ | 0 | 0 | 0 |

### 重大な指摘（Critical / Warning）

#### [skill-name] より
- [severity]: [summary] — [location]

（指摘なしの場合は「なし」）

### 各スキルの詳細レポート

<details>
<summary>[skill-name] レポート</summary>

[各サブエージェントが出力した全文をそのまま展開する]

</details>

### サマリー
- 実施スキル数: N件
- 総合判定根拠: [なぜこの判定か]
```

### 統合判定スキーマ（machine-readable）

Step 4 の報告フォーマット末尾に `<!-- verdict-json -->` コメントで囲んで追加する。

```json
{
  "skill": "agent-reviewer",
  "verdict": "LGTM | REQUEST_CHANGES",
  "blocking": false,
  "review_results": [
    {
      "reviewer": "code-reviewer",
      "verdict": "LGTM | REQUEST_CHANGES",
      "blocking": false,
      "severity_summary": {"critical": 0, "warning": 0, "suggestion": 0}
    }
  ],
  "aggregated_blocking_issues": [
    {
      "from_reviewer": "code-reviewer",
      "severity": "Critical | Warning",
      "summary": "問題の要約（1行）",
      "location": "ファイル名:行番号"
    }
  ]
}
```

**`blocking`**: `aggregated_blocking_issues` に Critical が1件以上ある場合は `true`。
**`verdict`**: `aggregated_blocking_issues` が空の場合は `LGTM`、そうでない場合は `REQUEST_CHANGES`。

---

## ガードレール

| 制限 | 値 |
|---|---|
| 並列起動スキル数 | 最大5件（超過する場合は依頼観点の優先度でフィルタする） |
| 直接レビュー | 禁止（必ずサブエージェントに委譲） |
| タスクレビューモードでのスキップ | 禁止（変更ファイルの有無にかかわらずレビュー必須） |
