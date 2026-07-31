---
name: code-to-specs
description: 既存コードから仕様書を逆生成するスキル。「仕様書を作って」「スペックを生成して」「コードから仕様を起こして」「実装から仕様書を書いて」「リバースエンジニアリングして仕様書を作って」「既存システムの仕様をまとめて」などで発動する。レガシーコードや既存実装から、保守担当者・顧客・SME向けの信頼性の高い仕様書を生成する。
metadata:
  version: "1.1.0"
  tier: experimental
  category: documentation
  tags:
    - reverse-engineering
    - specification
    - documentation
    - code-analysis
---

# code-to-specs

既存のコードベースを解析して仕様書を逆生成するスキル。推測と確実性を明示し、トレーサビリティを確保した実務的な仕様書を生成する。

> **設計思想**: 「綺麗で完成度の高い仕様書よりも、正直で穴が見えている仕様書のほうが実務的価値が高い」

## 設計原則

- **正直さ優先**: 推測部分を `[ASSUMED]`・`[ASK SME]` で明示し、完璧さより誠実さを重視する
- **トレーサビリティ**: すべての記述にソースコード参照 `[REF: ファイル:行番号]` を付与する
- **段階的詳細化**: 各フェーズ完了後にユーザー確認を挟む
- **再開可能性**: `.specs-work/state.json` で進捗を管理し、中断・再開を可能にする

## エージェント互換性

Claude Code・GitHub Copilot・kiro-cli で動作する。サブエージェント並列起動（Phase 3）は Claude Code でのみ利用可。それ以外の環境では章ごとに逐次実行する。

---

## 6フェーズの概要

各フェーズの詳細手順は `references/` 以下を参照する。

| Phase | 名称 | 詳細手順 | 主な成果物 |
|---|---|---|---|
| 0 | セットアップとゴール定義 | [phase-0-setup.md](references/phase-0-setup.md) | `goal.json` |
| 1 | 偵察とテンプレート選定 | [phase-1-recon.md](references/phase-1-recon.md) | `recon-report.md` |
| 2 | 計画とWBS分割 | [phase-2-plan.md](references/phase-2-plan.md) | `inventory.json`, `wbs.json` |
| 3 | 並列調査（章ドラフト生成） | [phase-3-investigate.md](references/phase-3-investigate.md) | `drafts/*.md` |
| 4 | 検証 | [phase-4-verify.md](references/phase-4-verify.md) | `coverage-report.md` |
| 5 | 対話による精緻化 | [phase-5-refine.md](references/phase-5-refine.md) | 解消済み `questions.json` |
| 6 | 納品 | [phase-6-deliver.md](references/phase-6-deliver.md) | `final/` 配下の最終仕様書 |

各フェーズを開始する前に対応する参照ファイルを読む。

**補助リファレンス**（フェーズ参照ファイルから参照される）:
- [references/templates.md](references/templates.md) — テンプレート別章構成（Phase 1・2 で参照）
- [references/inventory-guide.md](references/inventory-guide.md) — 言語別インベントリ抽出コマンド（Phase 2 で参照）

---

## 不確実性マーカー（全フェーズ共通）

章ドラフトでは以下のマーカーを積極的に使用する:

| マーカー | 意味 |
|---|---|
| `[CONFIDENCE: HIGH]` | コードから明確に読み取れる |
| `[CONFIDENCE: MED]` | 文脈・命名から推測できる |
| `[CONFIDENCE: LOW]` | 推測に依存する（要確認） |
| `[ASSUMED: 内容; 根拠: 推論]` | 根拠付きの推測 |
| `[ASK SME]` | 専門家確認が必要 |
| `[BLOCKED: Q-XXX 参照]` | critical疑問で記述不可 |

すべての記述にコード参照を付与する: `[REF: src/app.py:42-58]`

---

## Question Bank（全フェーズ共通）

調査中に生まれた疑問を `.specs-work/questions.json` に追記する。

```json
{
  "id": "Q-001",
  "phase": "investigation",
  "category": "business_rule",
  "severity": "critical",
  "status": "open",
  "question": "タイムアウト値（30分）の根拠は何か？",
  "source_ref": "src/auth/middleware.py:87"
}
```

**severity**: `critical`（未解決なら章が空欄）/ `important`（推測で進められる）/ `nice-to-have`

**7カテゴリ**: `business_rule` / `architecture_decision` / `data_model_intent` / `external_integration` / `naming_history` / `operational_requirement` / `security_compliance`

---

## 作業ディレクトリ構造

```
.specs-work/
├── state.json          # 進捗管理（currentPhase, completedPhases）
├── goal.json           # ゴール定義（Phase 0）
├── recon-report.md     # 偵察レポート（Phase 1）
├── inventory.json      # 抽出インベントリ（Phase 2）
├── wbs.json            # 作業分解（Phase 2）
├── questions.json      # Question Bank（Phase 3〜5）
├── coverage-report.md  # 検証レポート（Phase 4）
├── drafts/             # 章ドラフト（Phase 3）
│   ├── 00-metadata.md
│   ├── 01-overview.md
│   └── ...
└── final/              # 最終成果物（Phase 6）
    ├── 00-metadata.md
    ├── 99-unresolved.md
    ├── traceability.md
    └── README.md
```

**章ファイル命名規約**: `NN-slug.md`（`NN`: 2桁ゼロ埋め、`slug`: ASCII小文字/数字/ハイフンのみ）

予約ファイル: `00-metadata.md`（メタデータ）/ `99-unresolved.md`（未確定事項）/ `traceability.md`（参照対応表）

---

## 再開プロトコル

「仕様書生成を再開して」と言われた場合:

1. `.specs-work/state.json` を読む
2. `currentPhase` から再開する
3. 「Phase X から再開します。前回の進捗: [概要]」とユーザーに伝える
