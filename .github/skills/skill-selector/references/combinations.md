# スキルコンビネーションパターン集

単一タスクに対してよく使われるプライマリスキルと補助スキルの組み合わせパターン。ユーザーの1つのタスクに対して推薦する際の参考にする。

> **注意**: `category: orchestration` のスキル（scrum-master・skill-mentor・gitlab-idd 等）はこのパターン集の推薦対象外。

---

## 目次

- [補助スキルの付加ガイド](#補助スキルの付加ガイド)
- [コードレビュー・品質改善](#コードレビュー品質改善)
- [設計レビュー](#設計レビュー)
- [デバッグ・バグ修正](#デバッグバグ修正)
- [ドキュメント整備](#ドキュメント整備)
- [スキル管理](#スキル管理)
- [リサーチ・意思決定](#リサーチ意思決定)
- [テスト駆動開発](#テスト駆動開発)
- [セキュリティ監査](#セキュリティ監査)
- [選択指針](#選択指針)

---

## 補助スキルの付加ガイド

プライマリスキルに対する補助スキルは、`self-checking` 系を 0 または 1件、条件付き補助を 0 または 1件の **最大2件** に固定する。詳細な付加基準は `skill-selector/SKILL.md` の Step 4.5 を参照。

| 種類 | 補助スキル | 主な付加対象 | 効果 |
|---|---|---|---|
| 原則付加 | `self-checking` | 実装・作成系プライマリスキル全般（コード・ドキュメント） | 多角レビュー前の自己評価・改善で指摘件数を削減 |
| 条件付きで1つ選択 | `test-driven-development` | 新規コード実装プライマリスキル（ドメインロジック・API・モジュール等） | Red-Green-Refactor サイクルで品質とカバレッジを保証 |
| 条件付きで1つ選択 | `contract-driven-development` | API・モジュール境界・外部連携を持つ実装系プライマリスキル | 入出力契約、境界条件、互換性ルールを先に固定 |
| 条件付きで1つ選択 | `risk-driven-development` | UI・設定・CI・段階導入・軽量リファクタなどの変更系プライマリスキル | リスク棚卸し、最小検証、段階適用、停止条件の明確化 |
| 条件付きで1つ選択 | `failure-driven-development` | 外部依存・非同期・可用性要件を持つ実装系プライマリスキル | 失敗モード、検知、回復手段、劣化運転を先に固定 |

`agent-reviewer` は skill-selector の推薦対象ではない。レビューは orchestrator が直接 `agent-reviewer` を起動して実施する。

---

## コードレビュー・品質改善

```
code-reviewer
  → (security-reviewer)  ※セキュリティ指摘があった場合
  → (code-simplifier)    ※リファクタ対象がある場合
  → (test-reviewer)      ※テストコードも対象の場合
```

**補助スキル**: レビュー系は成果物を生まないため self-checking の付加は不要  
**用途**: PR レビュー、品質チェック  
**特徴**: code-reviewer が起点、指摘内容に応じて専門スキルを追加

---

## 設計レビュー

```
architecture-reviewer
  → design-reviewer
  → (security-reviewer)
  → (document-reviewer)  ※設計ドキュメントがある場合
```

**補助スキル**: 設計ドキュメントを更新する場合は technical-writer に self-checking を付加  
**用途**: アーキテクチャ・クラス設計の評価  
**特徴**: 広域（architecture）→ 詳細（design）の順で掘り下げる

---

## デバッグ・バグ修正

```
systematic-debugging
  → (code-reviewer)      ※修正後のレビュー
  + self-checking         ※補助スキル: 修正コードの自己評価（修正フェーズ後に適用）
```

**補助スキル**: コード修正が発生するため self-checking をデフォルト付加  
**用途**: バグ原因の特定と修正  
**特徴**: 体系的分析 → ランタイム計装 → 修正の流れ。修正前に根本原因を必ず特定

---

## ドキュメント整備

```
(document-reviewer)      ※既存ドキュメントのレビュー
  → technical-writer + self-checking  ※補助スキル: ドキュメント成果物の自己評価
  → (doc-coauthoring)    ※仕様書・設計書の共同執筆
```

**補助スキル**: ドキュメント作成系スキルに self-checking をデフォルト付加  
**用途**: ドキュメント作成・更新  
**特徴**: 対象読者（外向け vs 内向け）で使用スキルが変わる

---

## スキル管理

```
skill-creator（モードA〜D）+ self-checking  ※補助スキル: 作成スキルファイルの自己評価
  → git-skill-manager    [push/pull]
  → skill-evaluator      ※試用後の評価
```

**補助スキル**: skill-creator（スキル作成系）に self-checking をデフォルト付加  
**用途**: 新しいスキルの作成・管理・上申  
**特徴**: skill-creator がモードに応じてゼロから作成（A）、コードベースから生成（B）、履歴から生成（C）、外部取得（D）を切り替える

---

## リサーチ・意思決定

```
deep-research
  → (domain-modeler)     ※ドメイン知識の整理
  → (brainstorming)      ※調査結果をもとに設計へ
  → (doc-coauthoring) + self-checking  ※補助スキル: 調査結果ドキュメントの自己評価
```

**補助スキル**: ドキュメント化フェーズのみ self-checking を付加（調査フェーズは不要）  
**用途**: 技術選定、仕様調査、競合分析  
**特徴**: deep-research が情報収集、後続スキルで成果物化

---

## テスト駆動開発

```
(react-frontend-coder | 言語固有実装スキル)
  + test-driven-development（補助）  ※プライマリスキルに先行して設定
  + self-checking（原則付加）  ※実装後の自己評価
  → code-reviewer
  → test-reviewer
```

**補助スキル**: test-driven-development はプライマリスキル（実装スキル）に付加する補助スキル。Red-Green-Refactor サイクルを管理し、実装をその中で行う  
**用途**: TDD サイクルでの実装  
**特徴**: test-driven-development が TDD ライフサイクルを管理し、言語実装はプライマリスキルに委譲

---

## リスク駆動の変更実装

```
(react-frontend-coder | ci-cd-configurator | code-simplifier | その他変更系スキル)
  + risk-driven-development（補助）  ※プライマリスキルに先行して設定
  + self-checking（原則付加）        ※変更後の自己評価
```

**補助スキル**: risk-driven-development はプライマリスキルの前段で、最大リスク・最小検証・停止条件を定義する補助スキル。  
**用途**: TDD を厳密に回しにくい変更を安全に進める場合  
**特徴**: 実装そのものはプライマリスキルが担い、risk-driven-development は順序と安全ゲートを管理する

---

## 契約駆動の境界実装

```
(api-designer | react-frontend-coder | その他 API / 連携系実装スキル)
  + contract-driven-development（補助）  ※プライマリスキルに先行して設定
  + self-checking（原則付加）           ※実装後の自己評価
```

**補助スキル**: contract-driven-development はプライマリスキルの前段で、I/O 契約、境界条件、互換性ルールを固定する補助スキル。  
**用途**: API やモジュール境界を先に固めてから実装したい場合  
**特徴**: 実装そのものはプライマリスキルが担い、contract-driven-development は境界の固定と breaking change 防止を管理する

---

## 失敗駆動の回復設計実装

```
(api-designer | react-frontend-coder | ci-cd-configurator | その他可用性重視の実装スキル)
  + failure-driven-development（補助）  ※プライマリスキルに先行して設定
  + self-checking（原則付加）           ※実装後の自己評価
```

**補助スキル**: failure-driven-development はプライマリスキルの前段で、失敗モード、検知方法、回復手段、許容劣化を固定する補助スキル。  
**用途**: 障害時の振る舞いを先に決めてから実装したい場合  
**特徴**: 実装そのものはプライマリスキルが担い、failure-driven-development は異常系設計と回復戦略を管理する

---

## セキュリティ監査

```
security-reviewer
  → (code-reviewer)      ※セキュリティ以外の品質も見る場合
  → (architecture-reviewer)  ※アーキテクチャレベルの脆弱性
```

**補助スキル**: レビュー系は成果物を生まないため self-checking の付加は不要  
**用途**: セキュリティ診断、OWASP チェック  
**特徴**: security-reviewer が主体、必要に応じてスコープ拡大

---

## 選択指針

以下はよくある意図とスキル組み合わせの**代表例**。実際には `discover_skills.py --group-by-category` で最新スキル一覧を確認してから選択すること。

| ユーザーの意図 | 最初に使うスキル | 後続の候補 |
|---|---|---|
| 「何かを作りたい（構想段階）」 | brainstorming | requirements-definer |
| 「何かを実装したい」 | react-frontend-coder 等の実装スキル | self-checking（原則付加） + test-driven-development / contract-driven-development / risk-driven-development / failure-driven-development のいずれか1つ |
| 「コードを直したい」 | systematic-debugging | code-reviewer |
| 「コードを整理したい」 | code-simplifier / code-reviewer | — |
| 「設計を確認したい」 | architecture-reviewer / design-reviewer | security-reviewer |
| 「テストを書きたい」 | test-driven-development | 言語固有テストスキル |
| 「ドキュメントを書きたい」 | technical-writer / doc-coauthoring | — |
| 「スキルを作りたい」 | skill-creator | git-skill-manager |
| 「調査したい」 | deep-research | brainstorming / doc-coauthoring |

**注意**: この表は既知パターンの例示であり網羅的ではない。`description` を読んで最適なスキルを動的に判断し、新しいスキルが追加された場合も `discover_skills.py` の出力を正とする。
