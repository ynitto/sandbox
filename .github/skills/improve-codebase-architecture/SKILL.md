---
name: improve-codebase-architecture
description: コードベースから deepening（深化）の機会を発見し、shallow なモジュールを deep に作り変えてテスタビリティと AI ナビゲート性を高めるアーキテクチャ改善スキル。「アーキテクチャを改善して」「リファクタリングの機会を見つけて」「密結合なモジュールを統合して」「コードベースをテストしやすくして」などで発動する。HTML レポートで候補提示し grilling で設計を詰める。
metadata:
  version: 1.0.0
  tier: experimental
  category: maintenance
  tags:
    - architecture
    - refactoring
    - deep-modules
    - testability
    - seams
---

# improve-codebase-architecture

アーキテクチャ上の摩擦を可視化し、**deepening（深化）の機会** — shallow なモジュールを deep なモジュールへ作り変えるリファクタリング — を提案する。狙いは**テスタビリティ**と**AI ナビゲート性**の向上。

このスキルは「直接修正する」スキルではない。**候補の発見 → HTML レポート提示 → grilling（深掘り対話）→ インターフェース設計**という探索・合意形成のワークフローを駆動する。実際の修正適用は `code-simplifier` 等の実装スキルに委ねてよい。

---

## 用語集（すべての提案でこの語彙を厳密に使う）

語彙の一貫性こそが要点。「component」「service」「API」「boundary」へ流れない。完全な定義は [references/language.md](references/language.md)。

- **Module（モジュール）** — インターフェースと実装を持つもの全般（関数・クラス・パッケージ・層をまたぐスライス）。規模に依存しない。
- **Interface（インターフェース）** — 呼び出し側がそのモジュールを正しく使うために知らねばならないすべて。型シグネチャだけでなく、不変条件・順序制約・エラーモード・必要な設定・性能特性を含む。
- **Implementation（実装）** — モジュールの内側のコード。
- **Depth（深さ）** — インターフェースにおけるレバレッジ。小さいインターフェースの背後に大量の振る舞いがある＝**deep**。インターフェースが実装とほぼ同じ複雑さ＝**shallow**。
- **Seam（シーム）**（Michael Feathers）— その場を編集せずに振る舞いを差し替えられる場所。インターフェースが存在する*位置*。「boundary」とは言わない。
- **Adapter（アダプタ）** — シームでインターフェースを満たす具体物。
- **Leverage（レバレッジ）** — 深さから呼び出し側が得るもの。学ぶインターフェース量あたりの能力。
- **Locality（局所性）** — 深さから保守者が得るもの。変更・バグ・知識・検証が一箇所に集中する。

主要原則（全リストは [references/language.md](references/language.md)）:

- **削除テスト（deletion test）**: そのモジュールを削除すると想像する。複雑さが消えるなら pass-through（素通し）だった。複雑さが N 個の呼び出し側に再出現するなら、そのモジュールは価値を生んでいた。
- **インターフェースこそがテスト面（test surface）である。**
- **アダプタ1個 = 仮説的なシーム。アダプタ2個 = 本物のシーム。**

このスキルはプロジェクトのドメインモデルに*情報を与えられる*。ドメイン言語は良いシームに名前を与え、ADR は再蒸し返ししてはならない決定を記録する。

---

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR`、その親を `SKILLS_DIR` とする。関連スキルは名前で検索する: `${SKILLS_DIR}/[skill-name]/SKILL.md`。

---

## プロセス

### 1. 探索（Explore）

まず、プロジェクトのドメイン用語集（`CONTEXT.md` / ドメイングロッサリ。なければ `domain-modeler` の成果物）と、触れる領域の ADR を読む。

次に **Agent ツールを `subagent_type=Explore` で**使ってコードベースを歩く。硬直的なヒューリスティクスに従わず、有機的に探索し、**摩擦を感じる場所**を記録する:

- 1つの概念を理解するのに、多数の小さなモジュールを行き来する必要がある場所はどこか
- **shallow** なモジュール — インターフェースが実装とほぼ同じ複雑さ — はどこか
- テスタビリティのためだけに純粋関数を抽出したが、本当のバグは「どう呼ばれるか」に潜んでいる（**locality** がない）場所はどこか
- 密結合なモジュールがシームを越えて漏れている場所はどこか
- 現在のインターフェースを通してはテストできない・テストしにくい場所はどこか

shallow だと疑うものには**削除テスト**を適用する: 削除したら複雑さが集中するか、ただ移動するだけか。「集中する（yes）」が欲しいシグナル。

### 2. 候補を HTML レポートとして提示

リポジトリに何も残さないよう、OS の一時ディレクトリに自己完結型 HTML を書き出す。一時ディレクトリは `$TMPDIR`（なければ `/tmp`、Windows は `%TEMP%`）から解決し、`<tmpdir>/architecture-review-<timestamp>.html` に書く。OS に応じて開く（Linux: `xdg-open`、macOS: `open`、Windows: `start`）。絶対パスをユーザーに伝える。

レポートは **Tailwind（CDN）** でレイアウト、**Mermaid（CDN）** でグラフ的な図を描く。各候補に **before/after の視覚化**を付ける。視覚的に。

各候補はカードとして:

- **Files** — 関与するファイル/モジュール
- **Problem** — 現アーキテクチャがなぜ摩擦を生むか（1文）
- **Solution** — 何が変わるか（平易な英語/日本語、1文）
- **Benefits** — locality と leverage、テストがどう改善するかで説明
- **Before / After 図** — 並置。shallowness と deepening を描く
- **Recommendation strength** — `Strong` / `Worth exploring` / `Speculative` のいずれかをバッジで

レポート末尾に **Top recommendation** セクション: 最初に取り組むべき候補とその理由。

**ドメインには CONTEXT.md の語彙を、アーキテクチャには [references/language.md](references/language.md) の語彙を使う。** `CONTEXT.md` が "Order" を定義しているなら「Order intake module」と呼ぶ。「FooBarHandler」でも「Order service」でもない。

**ADR との衝突**: 候補が既存 ADR と矛盾する場合、摩擦が ADR 再検討に値するほど現実的なときだけ提示する。カード内に明示する（例: 警告コールアウト「ADR-0007 と矛盾 — だが…の理由で再オープンの価値あり」）。ADR が禁じる理論上のリファクタを片端から列挙しない。

完全な HTML スキャフォールド・図パターン・スタイル指針は [references/html-report.md](references/html-report.md)。

ここではまだインターフェースを提案しない。ファイル書き出し後、ユーザーに問う:「どれを深掘りしますか？」

### 3. Grilling ループ（深掘り対話）

ユーザーが候補を選んだら、grilling 対話に入る。設計ツリーを一緒に歩く — 制約・依存・深化後モジュールの形・シームの背後に何が座るか・どのテストが生き残るか。

決定が固まるにつれ、副作用がインラインで起きる:

- **深化後モジュールを `CONTEXT.md` にない概念で命名する?** → その用語を `CONTEXT.md` に追加する（ファイルがなければ遅延作成。ドメイン用語管理は `domain-modeler` / `doc-coauthoring` と同じ規律）。
- **対話中に曖昧な用語が鋭くなった?** → その場で `CONTEXT.md` を更新する。
- **ユーザーが根拠のある理由で候補を却下した?** → ADR を提案する:「将来のアーキテクチャレビューが同じ提案を再生産しないよう、ADR に記録しますか？」。将来の探索者が同じ再提案を避けるのに実際に必要な理由のときだけ提案する（「今はやる価値がない」等の一時的・自明な理由はスキップ）。ADR の作成は `doc-coauthoring` に委ねてよい。
- **深化後モジュールの代替インターフェースを探りたい?** → [references/interface-design.md](references/interface-design.md)。

依存カテゴリ別の安全な深化手順（in-process / local-substitutable / ports & adapters / mock）とテスト戦略は [references/deepening.md](references/deepening.md)。

---

## 参照ファイル

| 知りたいこと | 参照 |
|---|---|
| アーキテクチャ語彙の完全定義・原則 | [references/language.md](references/language.md) |
| 依存カテゴリ別の安全な深化手順・テスト戦略 | [references/deepening.md](references/deepening.md) |
| HTML レポートのスキャフォールド・図パターン・スタイル | [references/html-report.md](references/html-report.md) |
| 代替インターフェース設計（並列サブエージェント） | [references/interface-design.md](references/interface-design.md) |

---

## 他スキルとの境界

- **architecture-reviewer**: SOLID・依存方向・セキュリティ境界の観点で*レビュー*する。本スキルは shallow→deep の*深化機会*に特化し、HTML レポート + grilling で設計を詰める。
- **code-simplifier**: 変更された diff を*直接修正*する。本スキルで合意した深化を実際に適用する段で連携する。
- **legacy-modernizer**: 大規模・レガシーの*段階的移行戦略*を立てる。本スキルはモジュール単位の深さに焦点を当てる。
