# presentator — スペック駆動プレゼンテーション作成スキル

スペックファイル（JSON）からPowerPointプレゼンテーションを生成するスキル。
ブリーフィング→アウトライン→アートディレクション→スライド構成→レビューという段階的ワークフローで、品質の高いプレゼンテーションを作成する。

## 前提環境

- Python 3.10+
- uv パッケージマネージャー
- スキルディレクトリで `uv sync` 実行済み

## 主要コマンド

```bash
uv run python3 scripts/pptx_builder.py {コマンド} [引数]
```

## 4つのワークフロー

**重要:** ワークフローファイルを読み込む前に、スライドの構成・内容・デザイン・レイアウトについて一切決定してはならない。必ずワークフローに従って段階的に進めること。

| ワークフロー | 用途 | 開始コマンド |
|---|---|---|
| A: 新規作成 | ゼロからスライドを作る | `workflows create-new-1-briefing` を読む |
| B: 既存編集 | 既存PPTXを修正する | `workflows edit-existing` を読む |
| C: 手動編集同期 | PowerPointで直接編集後に同期する | `workflows create-new-4-hand-edit-sync` を読む |
| D: スタイル作成 | 再利用可能なスタイルガイドを作る | `workflows create-style` を読む |

---

## ワークフロー A: 新規プレゼンテーション作成

### フェーズ1-1: ブリーフィング

**目的:** プレゼンテーションの「何を・誰に・なぜ」を合意する土台を作る。

ブリーフはアウトラインとは異なる。「何を作るか」に答えるものであり、スライド数・トピック・ストーリー構造は含まない。

**ヒアリングフェーズで確認すること:**
- プレゼンテーションのテーマ・題材
- 前提条件（聴衆の種類・知識レベル・場の設定・所要時間）
- コアメッセージ（主張ベースの1文で抽出）
- 具体的な期待アウトカム

**ライティングフェーズで作成するもの:**
- `specs/brief.md` — 目的・ペルソナ・主要メッセージ・文脈・聴衆に求める行動を散文で記述

**レビューフェーズの制約:**
- 口頭での合意は不十分。必ず書面（チャット上）での明示的な承認を得ること
- 承認なしに次のフェーズへ進んではならない
- 既存素材からメッセージを抽出すること。情報を捏造しないこと

承認後、フェーズ1-2（アウトライン設計）へ進む。

---

### フェーズ1-2: アウトライン設計

**目的:** ブリーフをスライド単位の構成に落とし込む。成果物: `specs/outline.md`

**`specs/outline.md` が承認されるまで他のワークフローを読んではならない。**

**ストーリーテリング語彙（アウトライン設計前に参照）:**

*プレゼンテーション構造の5つのマクロフレームワーク:*
- **結論先行型:** 時間制約がある聴衆向け。最初に結論を示し、根拠を展開する
- **説得シーケンス:** 問題の緊急性を積み上げ、解決策へ導く
- **コントラスト/ビジョン:** 現状と理想の未来を対比して可能性を示す
- **ナラティブアーク:** 変容ストーリー（Before→Conflict→After）
- **論理グルーピング:** 広範なコンテンツをカテゴリ別に整理する

*ナラティブテクニック:*
- Duarteフレームワーク / S.T.A.R.モーメント（Situation・Task・Action・Result）
- カイロス（タイミングの訴求）/ ソーシャルプルーフ

**2ステッププロセス:**

**ステップ1: 構成提案**
- ブリーフと聴衆に合ったフレームワークを提案
- 採用理由を説明し、代替案も提示
- ユーザーの確認を待つ

**ステップ2: アウトライン執筆**
承認後、各スライドのリストを作成する。各エントリに含めること:
- スライドラベル
- 「聴衆に何をどう変えるか」（例: 「[1: タイトル] 聴衆がトピックと発表者を認識する」）

同じビジュアルベースで構成するスライドには一貫したラベルプレフィックスを使う。

**アウトライン承認後、3つのオプションを提示する:**
- **(a)** 全スライドのトーキングポイントと証拠を詳細化する
- **(b)** 特定のスライドのみ詳細化する
- **(c)** アートディレクションにスキップする

ユーザーが明示的に選択してから進む。

---

### フェーズ1-3: アートディレクション

**目的:** 全スライドを通じた一貫したデザイン方向性を確立する。

このフェーズでの決定はフェーズ2の各スライドデザインのトップレベル制約となる。

**ステップ0: スタイル選択**
```bash
uv run python3 scripts/pptx_builder.py style-gallery
```
利用可能なスタイルを表示するか、事前定義スタイルなしで進む。

**ステップ1: テンプレート選択と分析**
```bash
uv run python3 scripts/pptx_builder.py list-templates
uv run python3 scripts/pptx_builder.py analyze-template {テンプレート名}
```
`presentation.json` にテンプレート名とフォントを更新する。

**ステップ2: デザインガイドを読む（提案前に必須）**
```bash
uv run python3 scripts/pptx_builder.py guides design-rules design-vocabulary
```

**デザインルール要約:**

*カラー:*
- テーマカラーを視覚的基盤とし、アクセントカラーで拡張する
- 使用色を増やすほど視線が分散する
- WCAGコントラスト比を維持: 通常テキスト4.5:1以上、大文字テキスト（18pt+）3:1以上
- スライドに絵文字を使用しない（プラットフォーム間でレンダリングが異なる）

*タイポグラフィ:*
- サイズ差でコンテンツ階層を確立する
- 最小サイズ: ボディテキスト14pt以上、見出し20pt以上（投影時）

*エフェクト:*
- シャドウは要素を浮かせる、グローは暗背景で映える、3D回転は奥行きを出す
- エフェクトが多いほど注目を集める → 使いすぎに注意

*レイアウトバランス:*
- コンテンツエリア内で要素を縦方向に分散させる
- 意図的でない限り上部集中は避ける

**デザイン語彙（art direction での参照用）:**

*レイアウト&構造:* comparison（並列）, hierarchy（ピラミッド/ツリー）, sequence（プロセスフロー/ファネル）, cycle, relationship（ハブ&スポーク/ネットワーク）, timeline/roadmap

*強調テクニック:* hero-title, big-number, KPI-tiles, gauge, spotlight, color-pop, gradient-overlay, frosted-glass

*データビジュアライゼーション:* deviation, correlation, ranking, distribution, change-over-time, magnitude, part-to-whole, spatial, flow

*ビジュアルトリートメント:* glassmorphism, neumorphism, double-exposure, gradient-map, isometric, parallax-depth

*タイポグラフィ:* knockout-text, gradient-text

*カラー方向性:* jewel-tones, cyberpunk, ocean, duotone, color-blocking

*スタイル方向性:* Art Deco, Bauhaus, Memphis, brutalist, industrial

**ステップ3: アートディレクション提案**
- スタイルがある場合: キーとなるトークンを提示し、そのまま使用するか確認
- スタイルがない場合: カラー・装飾・密度・視覚的印象を散文で記述した `specs/art-direction.md` を作成

**ステップ4: アウトライン適合性レビュー**
承認後、アウトラインが確定した方向性と合っているか確認し、必要に応じて構造変更を提案する。

**ユーザーがアートディレクションを明示的に承認するまで次のワークフローを読んではならない。**

---

### フェーズ2: スライド構成（コンポーズ）

**目的:** スライドJSONを1枚ずつ設計・構築する。

**開始前に実行する4つのコマンド:**
```bash
uv run python3 scripts/pptx_builder.py spec          # JSONスペック確認
uv run python3 scripts/pptx_builder.py grids         # グリッド確認
uv run python3 scripts/pptx_builder.py components    # コンポーネント確認
uv run python3 scripts/pptx_builder.py patterns      # パターン例確認
```

**スライド1枚ずつ必ず処理すること。バッチ生成はしない。**

**各スライドの処理:**

*設計フェーズ:*
1. スライドのメッセージを分析する
2. 関連するパターンを読む
3. 「このスライドは何を伝えるか。どのビジュアル構造がそれを最もよく支援するか」を問う
4. メッセージ → ビジュアル構造 → レイアウト → 要素の順で検証する

*構築フェーズ:*
1. ノート（スピーカーノート）を先に書く — 会話形式のプレゼンスクリプト
2. 要素を段階的に配置する
3. 実際のサイズを測定する
4. 測定結果に基づいて調整する

**テキストの実際のレンダリングサイズはその後のすべてに影響する** — 配置決定を順番に行うこと。

**測定コマンド:**
```bash
uv run python3 scripts/pptx_builder.py measure {output_json} -p {スライド番号}
```

**JSONスキーマの主要構造:**

```json
{
  "fonts": { "heading": "...", "body": "..." },
  "text_colors": { "primary": "#...", "secondary": "#..." },
  "slides": [
    {
      "notes": "スピーカーノート（会話形式で）",
      "background": "#色コード",
      "layout": "レイアウト名",
      "elements": [...]
    }
  ]
}
```

*サポートされる要素タイプ:*
- **Textbox:** テキスト、箇条書き、シンタックスハイライト（code-block）
- **Table:** CSSスタイルカスケードによるスタイリング
- **Chart:** bar / line / pie / donut
- **Image:** SVG/ラスター、マスク、クロップ、エフェクト、QRコード
- **Shape:** 40種以上（矩形、矢印、フローチャート記号、コールアウト等）
- **Line:** ストレート / エルボー / カーブコネクタ
- **Freeform:** ベジェ曲線によるカスタムパス
- **Video:** ポスターフレーム付き

*座標系:* 1920×1080ピクセルベース
*推奨描画エリア:* x=58–1862, y=173–950

**レイアウトの原則:**
- スタイル（見た目）× コンポーネント（使う部品）× パターン（構成の思考）を組み合わせる
- コンポーネントとパターンは参考であり制約ではない
- 非対称とコントラストを活かしたボールドなレイアウトがインパクトを生む
- 繰り返しは明確な意図がある場合のみ

---

### フェーズ3: 生成・レビュー・仕上げ

**ステップ1: 生成と測定**
```bash
uv run python3 scripts/pptx_builder.py build {output_json}
uv run python3 scripts/pptx_builder.py measure {output_json}
```
「Layout bias detected」警告は、意図的なデザインでない限り必ず修正すること。

**ステップ2: デザインレビュー**
```bash
uv run python3 scripts/pptx_builder.py preview {output_json}
```
生成されたPNGプレビューを確認し、以下を評価する:
- 明確さ（メッセージが伝わるか）
- レイアウト（要素の配置と間隔）
- テキスト（可読性、サイズ、コントラスト）
- デザインの一貫性

グリッドオーバーレイによる精密な位置確認:
```bash
uv run python3 scripts/pptx_builder.py preview {output_json} --grid
```

**プレビュー画像は要素位置の正式な参照として扱う。すべてのプレビューを確認してから報告すること。**

**ステップ3: 仕上げ**
レビュー結果に基づいてJSONを修正し、生成・レビューサイクルを繰り返す。

**ステップ4: 完了**
- 追加レビューが必要かユーザーに確認する
- スタイルを定義していない場合、デザイン決定を再利用可能なスタイルテンプレートとして保存するか提案する

---

## ワークフロー B: 既存PPTXの編集

**ステップ1: ガイド確認**
```bash
uv run python3 scripts/pptx_builder.py guides
```

**ステップ2: PPTXをJSONに変換**
```bash
uv run python3 scripts/pptx_to_json.py {input_pptx} -o {プロジェクトディレクトリ}
```
`slides.json` と画像フォルダが生成される。

**ステップ3: JSONを編集する**
スライドを**1枚ずつ**修正する。バッチ編集はしない。
- テキスト、要素、テーブル、スライドの順序を変更可能
- 元のスタイリング（色、フォント）を引き継がない。新テーマのデザインガイドラインを適用する

**ステップ4: 生成とレビュー**
フェーズ3の手順に従って生成・確認を行う。

*言語翻訳作業の場合は `translate-pptx` ワークフローを参照すること。*

---

## ワークフロー C: 手動編集の同期

**目的:** PowerPointで直接編集した内容をJSONに反映し、以降の再生成で失われないようにする。

**ステップ1-2は追加編集の前に必ず完了すること。手動編集は再生成時に失われる。**

**ステップ0: ガイド確認**
```bash
uv run python3 scripts/pptx_builder.py guides
```

**ステップ1: 差分確認**
```bash
uv run python3 scripts/pptx_builder.py diff {元のJSON} {編集済みPPTX}
```

**ステップ2: 手動編集をJSONに反映**
差分出力を確認し、`output_json` を手動で更新する:
- 変更された要素: 差分に基づいてプロパティを直接編集
- 新しいスライド/要素: `/tmp/sdpm/{プロジェクト}/edited/slides.json` のラウンドトリップJSONからデータをコピー
- 新しい画像: `/tmp/sdpm/{プロジェクト}/edited/images/` の `src` パスで参照
- スライドの並び替え: `output_json` のスライド配列の順序を調整

**差分出力を使って変更を特定すること。PPTXを再度全体抽出しないこと（ラウンドトリップJSONはビルダー固有のメタデータを失う）。**

**ステップ3: 追加編集と再生成**
手動編集の同期後、新たな変更を加えて再生成する。

---

## ワークフロー D: スタイル作成

**目的:** デザインの好みを1度設定し、プロジェクト横断で再利用できるスタイルガイドを構築する。

### フェーズ1: 好みの収集

参照素材（既存プレゼンテーション、画像、ブランドガイドライン）を提供してもらう。
参照素材は「存在するもの」であって「ユーザーが求めるもの」ではない。視覚的特徴を抽出して議論のきっかけとする。

```bash
# PPTXのカラー・フォント・レイアウトを分析
uv run python3 scripts/pptx_builder.py analyze-template {pptx_path}

# 画像からドミナントカラーを抽出
uv run python3 scripts/pptx_builder.py extract-colors {image_path}
```

### フェーズ2: 前提の特定

ビジュアル選択の背後にある単一の統一アイデアを特定する（シンプリシティ、ブランドアイデンティティ、聴衆文脈など）。

### フェーズ3: スタイルの設計

1. デザイントークンを定義する（カラー、タイポグラフィ、スペーシング）
2. 含めるスライドを計画する（通常5〜6枚）
3. HTMLテンプレートを作成する

**重要な技術ルール:**
- すべてのデザイン値は `:root` のCSS変数を使用し、ハードコードしない
- 座標系は1920×1080ピクセル、絶対位置指定
- フォントサイズは `pt` 単位のみ
- `:root` ブロックがスタイルの仕様そのものになる（エージェントが変数を読んで正確なパラメータを把握できるため）

出力ファイル: `references/examples/styles/{名前}.html`

このHTMLファイルはビジュアルショーケースとして機能すると同時に、プレゼンテーションスライドの機械読み取り可能な仕様書となる。

---

## よくある操作

```bash
# テンプレート一覧
uv run python3 scripts/pptx_builder.py list-templates

# テンプレート分析
uv run python3 scripts/pptx_builder.py analyze-template {テンプレート名}

# スタイルギャラリー表示
uv run python3 scripts/pptx_builder.py style-gallery

# ガイド参照
uv run python3 scripts/pptx_builder.py guides {ガイド名...}

# PPTX生成
uv run python3 scripts/pptx_builder.py build {output_json}

# テキスト測定
uv run python3 scripts/pptx_builder.py measure {output_json} [-p {スライド番号}]

# プレビュー生成
uv run python3 scripts/pptx_builder.py preview {output_json} [--grid]

# スタイル適用
uv run python3 scripts/pptx_builder.py apply-style {style_name} {output_json}
```

---

## プロジェクト構成

```
{プロジェクトルート}/
├── specs/
│   ├── brief.md          # ブリーフ（フェーズ1-1の成果物）
│   ├── outline.md        # アウトライン（フェーズ1-2の成果物）
│   └── art-direction.md  # アートディレクション（フェーズ1-3の成果物）
├── presentation.json     # プレゼンテーション設定
├── slides.json           # スライドJSONスペック（メイン成果物）
└── output/               # 生成されたPPTXとプレビュー
```

---

## 初回セットアップ

```bash
# スキルディレクトリで依存関係をインストール
cd {スキルディレクトリ}
uv sync

# 動作確認
uv run python3 scripts/pptx_builder.py list-templates

# アイコンのダウンロード（オプション）
uv run python3 scripts/download_aws_icons.py
uv run python3 scripts/download_material_icons.py
```

**トラブルシューティング:**
- `uv` が未インストールの場合: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- アイコンが見つからない場合: `download_aws_icons.py` / `download_material_icons.py` を実行する
