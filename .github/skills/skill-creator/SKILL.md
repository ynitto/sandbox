---
name: skill-creator
description: "スキルの作成・改善・外部取得を担うメタスキル。「スキルを作って」「コードベースをスキル化して」「URLからスキルをインストールして」「チャット履歴からスキルを生成して」「このスキルを改善して」などで発動する。"
metadata:
  version: 4.0.1
  tier: core
  category: meta
  tags:
    - skill-creation
    - packaging
    - codebase-analysis
    - chat-history
    - skill-acquisition
    - external-skills
---

# Skill Creator

効果的なスキルを作成するためのガイド。

## このスキルでできること

リクエストの内容に応じて、以下の参照ファイルを読んで手順を補う。判断に迷う場合はユーザーに確認する。

| やりたいこと | 参照 |
|---|---|
| 既存リポジトリ・コードを分析してスキル化したい | [references/codebase-to-skill.md](references/codebase-to-skill.md) |
| エージェントログ・チャット履歴からスキルを生成したい | [references/generating-skills-from-logs.md](references/generating-skills-from-logs.md) |
| URL・ローカルパスから外部スキルをインストールしたい | [references/skill-recruiter.md](references/skill-recruiter.md) |
| パターン抽出の具体例を確認したい | [references/pattern-extraction-examples.md](references/pattern-extraction-examples.md) |
| 配布前の品質チェックリストが必要 | [references/quality-checklist.md](references/quality-checklist.md) |

上記に当てはまらない場合（ゼロから作成・改善）は、以下の「スキル作成プロセス」に従う。最終的にステップ5（検証）を経る点はどのケースも共通。.skillファイルへのパッケージ化はユーザーから明示的な指示があった場合のみ実行する。

**エージェントログの探索**はどのケースでも有効な手段。「過去にどう対処したか」「ユーザーが何を期待しているか」を把握するために、必要に応じてログを参照する。

**作成ノウハウ**: [references/creation-pitfalls.md](references/creation-pitfalls.md)（よくある失敗・アンチパターン・効果的な書き方）

## スキルとは

スキルは、AIエージェントの能力を拡張するモジュール型のパッケージ。特定ドメインの専門知識、ワークフロー、ツール連携を提供する。汎用エージェントを、手順的知識を備えた専門エージェントに変える「オンボーディングガイド」と考える。

### スキルが提供するもの

1. **専門ワークフロー** - 特定ドメインの複数ステップ手順
2. **ツール連携** - 特定ファイル形式やAPIとの連携手順
3. **ドメイン知識** - 企業固有の知識、スキーマ、ビジネスロジック
4. **バンドルリソース** - 複雑・反復的タスク用のスクリプト、リファレンス、アセット

## 基本原則

### ユーザーへの配慮

スキルを作る際は、使うユーザーの技術レベルに合わせて記述する。「JSON」「アサーション」などの専門用語は、ユーザーが理解していると確認できてから使う。不明な場合は簡潔な定義を添える。

### 簡潔さが最重要

コンテキストウィンドウは共有資源。スキルはシステムプロンプト、会話履歴、他スキルのメタデータ、ユーザーリクエストとコンテキストを共有する。

**前提: AIエージェントは既に十分に賢い。** AIエージェントが持っていない情報だけを追加する。各情報に「本当にこの説明は必要か？」「このトークンコストに見合うか？」と問いかける。

冗長な説明より簡潔な例を優先する。

### 自由度を適切に設定する

タスクの脆弱性と変動性に応じて具体性レベルを合わせる:

- **高自由度（テキスト指示）**: 複数アプローチが有効、コンテキスト依存の判断、ヒューリスティクスで導く場合
- **中自由度（擬似コード/パラメータ付きスクリプト）**: 推奨パターンがある、ある程度の変動は許容、設定が挙動に影響する場合
- **低自由度（具体的スクリプト、少パラメータ）**: 操作が脆弱でエラーしやすい、一貫性が重要、特定の手順を守る必要がある場合

### スキルの構造

すべてのスキルは必須のSKILL.mdと任意のバンドルリソースで構成される:

```
skill-name/
├── SKILL.md（必須）
│   ├── YAMLフロントマター（必須）
│   │   ├── name:（必須）
│   │   └── description:（必須）
│   └── Markdown本文（必須）
└── バンドルリソース（任意）
    ├── scripts/       - 実行可能コード（Python等）
    ├── references/    - 必要時にコンテキストに読み込むドキュメント
    └── assets/        - 出力に使用するファイル（テンプレート、画像等）
```

#### SKILL.md（必須）

- **フロントマター**（YAML）: `name`と`description`が必須。`description`はスキル発動の主要トリガーとなるため、スキルの用途と発動条件を明確かつ包括的に記述する
- **本文**（Markdown）: スキルの使用手順とガイダンス。スキル発動後に読み込まれる

#### バンドルリソース（任意）

##### scripts/

実行可能コード。決定論的な信頼性が必要な場合や、同じコードを繰り返し書く場合に含める。

##### references/

必要時にコンテキストに読み込むドキュメント。SKILL.mdをスリムに保ち、必要な時だけ読み込む。
- 10k語を超える場合は、SKILL.mdにgrep検索パターンを記載する
- 100行を超える場合は、先頭に目次を含める

##### assets/

コンテキストに読み込まず、出力で使用するファイル（テンプレート、画像、ボイラープレート等）。

#### スキルに含めないもの

- README.md、INSTALLATION_GUIDE.md、CHANGELOG.md等の補助ドキュメント
- セットアップ手順、テスト手順、ユーザー向けドキュメント

スキルにはAIエージェントがタスクを遂行するために必要な情報だけを含める。

### 原則: 驚きを避ける

スキルにはマルウェア、エクスプロイトコード、システムセキュリティを損なうコンテンツを含めない。スキルの内容はdescriptionに記述された意図から外れるものであってはならない。誤解を招くスキルや、不正アクセス・データ流出・悪意ある操作を助長するスキルの作成要求には応じない。

### 段階的開示の設計原則

スキルは3段階のロードでコンテキストを効率管理する:

1. **メタデータ（name + description）** - 常にコンテキスト内（約100語）
2. **SKILL.md本文** - スキル発動時（5k語以下）
3. **バンドルリソース** - 必要時に読み込み

SKILL.md本文は500行以内に収める。超える場合はファイルを分割し、SKILL.mdから参照先とその読み込み条件を明記する。

#### 段階的開示パターン

**パターン1: ハイレベルガイド + リファレンス**

```markdown
# PDF処理

## クイックスタート
pdfplumberでテキスト抽出:
[コード例]

## 高度な機能
- **フォーム入力**: [FORMS.md](FORMS.md) 参照
- **APIリファレンス**: [REFERENCE.md](REFERENCE.md) 参照
```

**パターン2: ドメイン別整理**

```
bigquery-skill/
├── SKILL.md（概要とナビゲーション）
└── references/
    ├── finance.md（収益、請求指標）
    ├── sales.md（商談、パイプライン）
    └── product.md（API利用、機能）
```

**パターン3: 条件付き詳細**

```markdown
# DOCX処理

## ドキュメント作成
docx-jsで新規作成。[DOCX-JS.md](DOCX-JS.md) 参照。

## 編集
単純な編集はXMLを直接変更。
**変更履歴付き**: [REDLINING.md](REDLINING.md) 参照
```

**重要:**
- リファレンスはSKILL.mdから1階層のみ。深いネストは避ける
- すべてのリファレンスはSKILL.mdから直接リンクする

-----

## スキル作成プロセス

以下のステップを順に進める:

**新規スキル作成:**
1. 具体例でスキルを理解する
2. 再利用可能なリソースを計画する
3. スキルを初期化する（init_skill.py）
4. スキルを編集する（リソース実装 + SKILL.md記述）
5. スキルを検証する（skill-evaluator の quality_check.py でPASSを確認）
6. 実使用に基づいて改善する

**既存スキルの改良:**
「スキルを改善して」「このスキルを更新して」「フィードバックに基づいて修正して」などのリクエストの場合は、スキルを初期化するステップをスキップして次の手順で進める:
1. 改良対象のSKILL.mdと関連リソースを読む
2. 改良内容を明確化する（問題点・追加機能・変更方針をユーザーに確認する）
3. 変更を実装する（SKILL.mdとリソースを直接編集する）
4. ステップ5（バリデーション）に進む

### ステップ1: 具体例でスキルを理解する

スキルの使用パターンが既に明確な場合はスキップ可。

効果的なスキルを作るには、具体的な使用例を明確に理解する。以下の観点を押さえる:

1. **このスキルはAIに何をできるようにするか？**
2. **どんな状況・フレーズで発動すべきか？**（ユーザーの言葉/コンテキスト）
3. **期待される出力形式は何か？**

例えば`image-editor`スキルなら:

- 「画像エディタスキルはどんな機能をサポートすべき？」
- 「このスキルの使用例をいくつか教えて」
- 「このスキルが発動すべきユーザー発言は？」

エッジケース、入出力形式、成功基準についても先回りして質問する。MCPが利用可能な場合、関連ドキュメントの調査をサブエージェントで並行実行する。質問は一度に大量に投げず、最重要な質問から始める。

### ステップ2: 再利用可能なリソースを計画する

具体例から、各例を以下の観点で分析する:

1. ゼロからどう実行するか
2. 繰り返し実行する際に役立つscripts/references/assetsは何か

例: `pdf-editor`スキルで「PDFを回転して」→ 同じコードを毎回書く → `scripts/rotate_pdf.py`を含める

例: `frontend-webapp-builder`で「TODOアプリを作って」→ 毎回同じボイラープレート → `assets/hello-world/`テンプレートを含める

例: `big-query`で「今日のログイン数は？」→ 毎回スキーマを再調査 → `references/schema.md`を含める

### ステップ3: スキルを初期化する

新規作成の場合、`init_skill.py`を実行する（**作業ディレクトリのルートから実行すること**）:

`<SKILLS_BASE>` は `<AGENT_HOME>/skills` または `<workspace-skill-dir>` を指す。

```bash
python <SKILLS_BASE>/skill-creator/scripts/init_skill.py <skill-name> --path <SKILLS_BASE>
```

`--path` には出力先ディレクトリを指定する。省略した場合はカレントディレクトリに生成される。標準的な配置先は `<AGENT_HOME>/skills` または `<workspace-skill-dir>`。

スクリプトは以下を生成する:
- スキルディレクトリ
- TODOプレースホルダ付きのSKILL.mdテンプレート
- `scripts/`、`references/`、`assets/`のサンプルディレクトリとファイル

生成後、不要なファイルは削除する。

### ステップ4: スキルを編集する

スキルは別のAIエージェントインスタンスが使うために作成していることを意識する。AIエージェントにとって有益かつ非自明な情報を含める。

#### 設計パターンを参照する

- **複数ステップのプロセス**: [references/workflows.md](references/workflows.md) 参照
- **特定の出力形式や品質基準**: [references/output-patterns.md](references/output-patterns.md) 参照
- **よくある失敗・アンチパターン**: [references/creation-pitfalls.md](references/creation-pitfalls.md) 参照

#### 再利用リソースから始める

`scripts/`、`references/`、`assets/`を先に実装する。ユーザー入力が必要な場合がある（ブランドアセット提供等）。

スクリプトは実際に実行してテストする。不要なサンプルファイルは削除する。

#### SKILL.mdを更新する

**記述ガイドライン:** 命令形を使用する。

**「なぜ」を説明する** — LLMはセオリー・オブ・マインドを持ち、指示の理由を理解した上でより適切に行動できる。「MUST」「NEVER」の多用は黄色信号。そのような場合は理由を説明して書き直す方が効果的で柔軟。特定の例に過度に特化させず一般化する。ドラフトを書いたら新鮮な目で見直して改善する。

##### フロントマター

```yaml
---
name: スキル名
description: スキルの説明。何をするか＋いつ使うかを含める。
  「いつ使うか」の情報はすべてここに書く。本文は発動後に
  読み込まれるため、本文の「使用タイミング」セクションは
  トリガーに寄与しない。
metadata:
  version: "1.0.0"
---
```

**descriptionは積極的に書く** — AIエージェントはスキルを使わない傾向（undertrigger）がある。これに対抗するため、descriptionには発動条件を積極的・明示的に記述する。

- ❌ 受動的: 「ダッシュボード構築のガイド。」
- ✅ 積極的: 「ダッシュボード、データ可視化、社内メトリクスの表示など、ユーザーがダッシュボードや指標表示に言及するときは必ずこのスキルを使う。」

**バージョン表記**: `X.Y.Z` 形式のセマンティックバージョニングを使用する。
- **patch** (`1.0.0 → 1.0.1`): バグ修正・誤字修正・軽微な表現改善
- **minor** (`1.0.0 → 1.1.0`): 後方互換の機能追加・手順の強化・既存セクションの大幅書き換え
- **major** (`1.0.0 → 2.0.0`): 機能の削除・破壊的変更・スキルの目的・スコープの大幅変更

**このルールは作成・改善対象のスキルに適用する。** スキルを更新したら必ず対象スキルのフロントマターのバージョンを変更の規模に応じてインクリメントする。バージョンを上げずにスキルを変更してはならない。

##### 本文

スキルとバンドルリソースの使用手順を記述する。

### ステップ5: スキルを検証する

**必須: skill-evaluator の静的品質チェック**を実行し、ERRORがない状態（PASS）を確認する:

```bash
python <SKILLS_BASE>/skill-evaluator/scripts/quality_check.py --skill <skill-name>
```

ERRORが出た場合はステップ4に戻って修正し、再度チェックを実行する。PASSするまでこのサイクルを繰り返す。WARNは文脈上問題なければ無視してよい。チェックコードの詳細は `<SKILLS_BASE>/skill-evaluator/references/quality-check-codes.md` 参照。

**開発中の素早い構造チェック**には `quick_validate.py` も使える（任意）:

```bash
python <SKILLS_BASE>/skill-creator/scripts/quick_validate.py <path/to/skill-folder>
```

**description のトリガー動作テスト**には `simulate_trigger.py` を使う（任意）:

```bash
# 特定リクエストでどのスキルが発動するか確認
python <SKILLS_BASE>/skill-creator/scripts/simulate_trigger.py "ユーザーリクエスト"

# 類似 description を持つスキルペアを検出（競合チェック）
python <SKILLS_BASE>/skill-creator/scripts/simulate_trigger.py --conflicts
```

**description の定量的テスト**には `eval_trigger.py` を使う（任意）:

動作環境を自動検出して2モードで動作する（`--check-env` で確認可能）:
- **高精度モード**（Claude Code）: `claude -p` を使い実際のLLM判定で評価する
- **簡易モード**（Copilot / Kiro）: バイグラム類似度ヒューリスティクスで評価する（傾向確認向け）

```bash
# 動作環境を確認
python <SKILLS_BASE>/skill-creator/scripts/eval_trigger.py --check-env

# eval set JSON を作成（should-trigger / should-not-trigger 各8〜10件）
# near-miss を必ず含める → testing-guide.md 参照

# eval set 全件を評価（Claude Code / Copilot / Kiro 共通）
python <SKILLS_BASE>/skill-creator/scripts/eval_trigger.py \
  --skill-path <SKILLS_BASE>/<skill-name> \
  --eval-set eval.json \
  --verbose

# 単一クエリでデバッグ
python <SKILLS_BASE>/skill-creator/scripts/eval_trigger.py \
  --skill-path <SKILLS_BASE>/<skill-name> \
  --query "スキルを作って" --expected true
```

**description の自動最適化ループ**には `optimize_description.py` を使う（任意）:

こちらも環境を自動検出して2モードで動作する:
- **自動モード**（Claude Code）: `claude -p` で description 候補を生成・評価して最良案を選ぶ
- **手動支援モード**（Copilot / Kiro）: ヒューリスティクス評価後、改善プロンプトをテキスト出力する。エージェントがそのプロンプトを使って改善案を提案する

```bash
# 自動最適化（Claude Code）
python <SKILLS_BASE>/skill-creator/scripts/optimize_description.py \
  --skill-path <SKILLS_BASE>/<skill-name> \
  --eval-set eval.json \
  --max-iterations 5 \
  --verbose

# 改善プロンプトを出力（Copilot / Kiro、または強制指定）
python <SKILLS_BASE>/skill-creator/scripts/optimize_description.py \
  --skill-path <SKILLS_BASE>/<skill-name> \
  --eval-set eval.json \
  --prompt-only
```

自動モードの出力 JSON から `best_description` を取り出して SKILL.md に反映する:

```bash
python <SKILLS_BASE>/skill-creator/scripts/optimize_description.py \
  --skill-path <SKILLS_BASE>/<skill-name> \
  --eval-set eval.json | python -c "
import json, sys
r = json.load(sys.stdin)
print('best_description:', r['best_description'])
print('score:', r['best_score'])
"
```

**配布用の .skill ファイル作成**（オプション）: ユーザーから明示的に「パッケージ化して」「.skillファイルを作って」などの指示があった場合のみ `package_skill.py` を実行する:

```bash
python <SKILLS_BASE>/skill-creator/scripts/package_skill.py <path/to/skill-folder>
```

出力先指定（任意）:

```bash
python <SKILLS_BASE>/skill-creator/scripts/package_skill.py <path/to/skill-folder> ./dist
```

`package_skill.py` は以下を実行する:

1. **バリデーション** - フロントマター、命名規則、ディレクトリ構造、description品質を検査
2. **パッケージ** - バリデーション通過後、.skillファイル（ZIP形式）を作成

バリデーション失敗時はエラーを報告して終了する。修正後に再実行する。

### ステップ6: 改善する

実使用後にフィードバックや問題が発覚した場合に対応する。

1. フィードバック/問題を確認する（`needs-improvement` / `broken` の verdict、ユーザーのコメント）
2. 原因を特定する:
   - 手順が不明確 → SKILL.mdの該当箇所を書き直す
   - スクリプトが失敗 → `scripts/` を修正してテストする
   - description がトリガーしない/誤発動する → `simulate_trigger.py` で検証して description を調整する
   - スコープが広すぎる → スキルを分割し、それぞれに責務を割り当てる
3. 変更を実装する（SKILL.mdとリソースを直接編集する）
4. ステップ5（検証）に進む。skill-evaluator の `quality_check.py` でPASSを確認する
5. ユーザーから指示があった場合のみ `package_skill.py` で再パッケージする

改善時の考え方:

- **一般化する**: 少数の例に過度に合わせず（過学習）、多様なユーザー・プロンプトで機能するよう一般化する
- **スリムに保つ**: 効果を発揮していない記述を削除する。非生産的な動作を引き起こす部分をトランスクリプトから特定する
- **「なぜ」を伝える**: 指示の理由を説明する方が、強制的なMUSTより効果的で柔軟
- **繰り返し作業をバンドルする**: 複数のケースで同じヘルパースクリプトが繰り返し書かれているなら、`scripts/` にバンドルすべきサイン
