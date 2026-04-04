# スキルコンビネーションパターン集

よく使われるスキルの組み合わせパターン。ユーザーの複合タスクに対して推薦する際の参考にする。

---

## 新機能開発（フルサイクル）

```
brainstorming
  → requirements-definer
  → (domain-modeler | api-designer | ui-designer)  ※対象次第
  → scrum-master  [実装フェーズのオーケストレーター]
    → react-frontend-coder
    → (tdd-executing)  ※厳密なTDDが必要な場合のみ
    → code-reviewer → code-simplifier
  → technical-writer
  → sprint-reviewer
```

**用途**: ゼロから機能・サービスを作る  
**特徴**: brainstorming が必ず先行、scrum-master がウェーブ実行を管理

---

## コードレビュー・品質改善

```
code-reviewer
  → (security-reviewer)  ※セキュリティ指摘があった場合
  → (code-simplifier)    ※リファクタ対象がある場合
  → (test-reviewer)      ※テストコードも対象の場合
```

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

**用途**: アーキテクチャ・クラス設計の評価  
**特徴**: 広域（architecture）→ 詳細（design）の順で掘り下げる

---

## デバッグ・バグ修正

```
systematic-debugging
  → (code-reviewer)      ※修正後のレビュー
```

**用途**: バグ原因の特定と修正
**特徴**: 体系的分析 → ランタイム計装 → 修正の流れ。修正前に根本原因を必ず特定

---

## ドキュメント整備

```
(document-reviewer)      ※既存ドキュメントのレビュー
  → technical-writer     ※README・ガイド
  → (doc-coauthoring)    ※仕様書・設計書の共同執筆
```

**用途**: ドキュメント作成・更新  
**特徴**: 対象読者（外向け vs 内向け）で使用スキルが変わる

---

## スキル管理

```
skill-creator（モードA〜D）
  → git-skill-manager    [push/pull]
  → skill-evaluator      ※試用後の評価
```

**用途**: 新しいスキルの作成・管理・上申
**特徴**: skill-creator がモードに応じてゼロから作成（A）、コードベースから生成（B）、履歴から生成（C）、外部取得（D）を切り替える

---

## リサーチ・意思決定

```
deep-research
  → (domain-modeler)     ※ドメイン知識の整理
  → (brainstorming)      ※調査結果をもとに設計へ
  → (doc-coauthoring)    ※調査結果のドキュメント化
```

**用途**: 技術選定、仕様調査、競合分析  
**特徴**: deep-research が情報収集、後続スキルで成果物化

---

## テスト駆動開発

```
tdd-executing
  → (react-frontend-coder | 言語固有テストスキル)
  → code-reviewer
  → test-reviewer
```

**用途**: TDD サイクルでの実装  
**特徴**: tdd-executing がオーケストレーター、言語実装は専門スキルに委譲

---

## セキュリティ監査

```
security-reviewer
  → (code-reviewer)      ※セキュリティ以外の品質も見る場合
  → (architecture-reviewer)  ※アーキテクチャレベルの脆弱性
```

**用途**: セキュリティ診断、OWASP チェック  
**特徴**: security-reviewer が主体、必要に応じてスコープ拡大

---

## 選択指針

以下はよくある意図とスキル組み合わせの**代表例**。実際には `discover_skills.py --group-by-category` で最新スキル一覧を確認してから選択すること。

| ユーザーの意図 | 最初に使うスキル | 後続の候補 |
|---|---|---|
| 「何かを作りたい」 | brainstorming | requirements-definer → scrum-master |
| 「コードを直したい」 | systematic-debugging | code-reviewer |
| 「コードを整理したい」 | code-simplifier / code-reviewer | — |
| 「設計を確認したい」 | architecture-reviewer / design-reviewer | security-reviewer |
| 「テストを書きたい」 | tdd-executing | 言語固有テストスキル |
| 「ドキュメントを書きたい」 | technical-writer / doc-coauthoring | — |
| 「スキルを作りたい」 | skill-creator | git-skill-manager |
| 「調査したい」 | deep-research | brainstorming / doc-coauthoring |

**注意**: この表は既知パターンの例示であり網羅的ではない。`description` を読んで最適なスキルを動的に判断し、新しいスキルが追加された場合も `discover_skills.py` の出力を正とする。
