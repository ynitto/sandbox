# スキルコンビネーションパターン集

よく使われるスキルの組み合わせパターン。ユーザーの複合タスクに対して推薦する際の参考にする。

---

## 補助スキルの付加ガイド

プライマリスキルに対して補助スキルを1つ以上付け加えることで品質を向上させる。詳細な付加基準は `skill-selector/SKILL.md` の Step 4.5 を参照。

複数の補助スキルを同時に付加してよい。詳細な付加基準（TDD が有効なケース含む）は `skill-selector/SKILL.md` の Step 4.5 を参照。

| 補助スキル | 主な付加対象 | 効果 |
|---|---|---|
| `self-checking` | 実装・作成系プライマリスキル全般（コード・ドキュメント） | 多角レビュー前の自己評価・改善で指摘件数を削減 |
| `tdd-executing` | 新規コード実装プライマリスキル（ドメインロジック・API・モジュール等） | Red-Green-Refactor サイクルで品質とカバレッジを保証 |
| `agent-reviewer` | 成果物レビューが必要な場合 | 機能・セキュリティ・アーキテクチャの多角レビュー |

---

## 新機能開発（フルサイクル）

```
brainstorming
  → requirements-definer
  → (domain-modeler | api-designer | ui-designer)  ※対象次第
  → scrum-master  [実装フェーズのオーケストレーター]
    → (tdd-executing)  ※補助スキル: 品質・カバレッジ重視の場合
    → react-frontend-coder + self-checking  ※補助スキル: 実装成果物の自己評価
    → code-reviewer → code-simplifier
  → technical-writer + self-checking  ※補助スキル: ドキュメント成果物の自己評価
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
tdd-executing             ※補助スキルとしても機能（実装スキルに先行して付加）
  → (react-frontend-coder | 言語固有テストスキル) + self-checking
  → code-reviewer
  → test-reviewer
```

**補助スキル**: tdd-executing 自体が品質保証スキルだが、実装後に self-checking を付加することでさらなる品質向上が可能  
**用途**: TDD サイクルでの実装  
**特徴**: tdd-executing がオーケストレーター、言語実装は専門スキルに委譲

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
| 「何かを作りたい」 | brainstorming | requirements-definer → scrum-master |
| 「コードを直したい」 | systematic-debugging | code-reviewer |
| 「コードを整理したい」 | code-simplifier / code-reviewer | — |
| 「設計を確認したい」 | architecture-reviewer / design-reviewer | security-reviewer |
| 「テストを書きたい」 | tdd-executing | 言語固有テストスキル |
| 「ドキュメントを書きたい」 | technical-writer / doc-coauthoring | — |
| 「スキルを作りたい」 | skill-creator | git-skill-manager |
| 「調査したい」 | deep-research | brainstorming / doc-coauthoring |

**注意**: この表は既知パターンの例示であり網羅的ではない。`description` を読んで最適なスキルを動的に判断し、新しいスキルが追加された場合も `discover_skills.py` の出力を正とする。
