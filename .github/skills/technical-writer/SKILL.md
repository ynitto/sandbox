---
name: technical-writer
description: 技術ドキュメントを高品質に作成するスキル。README、デベロッパーガイド、チュートリアル、アーキテクチャ仕様書などの文書作成を支援する。「ドキュメントを書いて」「READMEを作って」「デベロッパーガイドを書いて」「APIの使い方をまとめて」「チュートリアルを作成して」「仕様書を書いて」などで発動する。APIの仕様定義（OpenAPI）は api-designer に委ねる。
---

# Technical Writer

読者中心のアプローチで、明確で実用的な技術ドキュメントを作成するスキル。

## 適用場面

- プロジェクトの README や Getting Started ガイドを作成する場合
- API の認証・利用手順・コード例をまとめたデベロッパーガイドを作成する場合
- インストール手順・セットアップガイドを整備する場合
- ステップバイステップのチュートリアルを作成する場合
- アーキテクチャ設計・技術仕様を記述する場合
- トラブルシューティングガイドを作成する場合

> **API 仕様（エンドポイント定義・スキーマ・OpenAPI ファイル）は api-designer に委ねる。**
> このスキルは「API をどう使うか」を開発者向けに説明するドキュメントを担当する。

## 5つのコア原則

### 1. 読者中心（User-Centered）

「どう動くか」よりも先に「なぜ使うべきか」に答える。

```
❌ このツールは --recursive フラグを受け付けます。
✅ 大量のファイルを一括処理する場合は --recursive フラグを使用します。
```

### 2. 明瞭さ優先（Clarity First）

- 1文は25語（日本語で50〜60字）以内
- 1段落に1つのアイデア
- 能動態を使う（受動態は最小限に）
- 専門用語を使う場合は初出時に説明する

### 3. 具体的なコード例（Show, Don't Tell）

動作確認済みのコードと期待される出力を必ず示す。

```python
# ファイルを再帰的に並べ替える
filesort . --recursive --output sorted/

# 出力例:
# ✓ 42 files processed
# ✓ Sorted into 8 directories
```

### 4. 段階的な開示（Progressive Disclosure）

- クイックスタートを最初に置く（5分で動かせる手順）
- 基本 → 応用 → エッジケースの順に構成する
- 詳細設定は別セクションに分離する

### 5. スキャンしやすい構造（Scannable Content）

- 説明的な見出しを使う（「設定」より「データベース接続を設定する」）
- コードブロック、箇条書き、テーブルを活用する
- 重要な UI 要素は **太字** で強調する
- コマンド・変数・ファイルパスは `コードフォーマット` を使う

---

## ワークフロー

各ステップを順に実行する。

### ステップ1: 対象読者とゴールを明確化する

執筆前に確認する:

- 読者は誰か（初心者 / 中級者 / 上級者）？
- 読者はこのドキュメントを読んで何ができるようになるべきか？
- 前提知識は何か（OS、プログラミング言語、フレームワーク等）？
- ドキュメントのスコープはどこまでか？

不明な点はユーザーに確認してから進む。

### ステップ2: ドキュメントの種類を選択する

下記「テンプレート」から最適な形式を選ぶ。複数の形式が必要な場合は分割して作成する。

### ステップ3: テンプレートをベースに執筆する

テンプレートに沿いながら以下を意識する:

- 冒頭で「これは何か・なぜ使うか」を1段落で説明する
- コード例には必ずコメントと期待される出力を付ける
- 環境別（Windows / macOS / Linux）の差異がある場合は全て記載する
- プレースホルダーには `YOUR_VALUE` 形式の大文字を使う

### ステップ4: セルフレビューをする

執筆後に確認する:

- [ ] クイックスタートで5分以内に動作確認できるか
- [ ] すべてのコード例が実行可能か
- [ ] Windows と Unix の両方のコマンドが記載されているか（対象の場合）
- [ ] 見出しは動詞から始まる説明的な文になっているか
- [ ] プレースホルダーはすべて `YOUR_VALUE` 形式か

---

## テンプレート集

### テンプレート1: プロジェクト README

```markdown
# プロジェクト名

[1〜2文でプロジェクトの説明と価値を述べる]

## 特徴

- 特徴1（ユーザーにとってのメリットを述べる）
- 特徴2
- 特徴3

## クイックスタート

### 前提条件

- Node.js 18 以上
- Git

### インストール

**Windows (PowerShell)**
\`\`\`powershell
git clone https://github.com/YOUR_ORG/YOUR_REPO.git
cd YOUR_REPO
npm install
\`\`\`

**macOS / Linux**
\`\`\`bash
git clone https://github.com/YOUR_ORG/YOUR_REPO.git
cd YOUR_REPO
npm install
\`\`\`

### 起動

\`\`\`bash
npm start
# → http://localhost:3000 で起動します
\`\`\`

## 使い方

[基本的な使い方のコード例と出力を記載]

## 設定

| 環境変数 | 説明 | デフォルト値 |
|---|---|---|
| `PORT` | サーバーポート番号 | `3000` |
| `DATABASE_URL` | データベース接続文字列 | — |

## トラブルシューティング

**`Error: Cannot find module` が表示される場合**
```bash
npm install  # 依存関係を再インストール
```

## ライセンス

[LICENSE_TYPE] — 詳細は [LICENSE](LICENSE) を参照。
```

---

### テンプレート2: デベロッパーガイド

> API の仕様定義は api-designer が生成する OpenAPI ファイルを参照すること。
> このテンプレートは「開発者がその API を使い始めるための手引き」を対象とする。

```markdown
# [API_NAME] デベロッパーガイド

[この API が何を解決するか・どんな開発者向けかを2〜3文で説明する]

## 前提条件

- アカウント登録と API キーの取得 → [ダッシュボードへのリンク]
- 対応言語・ランタイムバージョン（例: Python 3.9+, Node.js 18+）
- 認証方式の理解（Bearer トークン / API キー / OAuth 2.0 等）

## クイックスタート（5分）

### 1. 認証を設定する

**Windows (PowerShell)**
\`\`\`powershell
$env:YOUR_API_KEY = "YOUR_API_KEY_HERE"
\`\`\`

**macOS / Linux**
\`\`\`bash
export YOUR_API_KEY="YOUR_API_KEY_HERE"
\`\`\`

### 2. 最初のリクエストを送る

\`\`\`python
import httpx

client = httpx.Client(headers={"Authorization": f"Bearer {YOUR_API_KEY}"})
response = client.get("https://api.example.com/v1/resources")
print(response.json())
# → {"items": [...], "total": 42}
\`\`\`

\`\`\`typescript
const response = await fetch("https://api.example.com/v1/resources", {
  headers: { Authorization: `Bearer ${process.env.YOUR_API_KEY}` },
});
const data = await response.json();
\`\`\`

✓ `200 OK` が返れば接続成功です。

## 主要ユースケース

### ユースケース1: [USECASE_TITLE]

[何を達成するかを1文で説明]

\`\`\`python
# [操作の説明]
result = client.post("/v1/resources", json={"name": "my-item"})
print(result.json())  # → {"id": "res_abc123", "name": "my-item"}
\`\`\`

### ユースケース2: [USECASE_TITLE]

...

## エラーハンドリング

| ステータス | 意味 | 対処方法 |
|---|---|---|
| `400` | リクエスト不正 | レスポンスの `message` フィールドを確認する |
| `401` | 認証失敗 | API キーを再確認する |
| `429` | レート制限超過 | `Retry-After` ヘッダーの秒数だけ待機して再試行する |
| `5xx` | サーバーエラー | 指数バックオフで最大3回リトライする |

\`\`\`python
try:
    response = client.post("/v1/resources", json=payload)
    response.raise_for_status()
except httpx.HTTPStatusError as e:
    if e.response.status_code == 429:
        time.sleep(int(e.response.headers.get("Retry-After", 60)))
\`\`\`

## レート制限と制約

| 項目 | 制限値 |
|---|---|
| リクエスト数 | 1,000 req/分 |
| ペイロードサイズ | 最大 10 MB |
| 同時接続数 | 最大 20 |

## SDK とライブラリ

| 言語 | パッケージ | インストール |
|---|---|---|
| Python | `YOUR_SDK_PYTHON` | `pip install YOUR_SDK_PYTHON` |
| TypeScript | `YOUR_SDK_TS` | `npm install YOUR_SDK_TS` |

## OpenAPI 仕様書

エンドポイントの完全な仕様は [openapi.yaml](./openapi.yaml) を参照してください。

## サポート・フィードバック

- 公式ドキュメント: [URL]
- 変更履歴: [CHANGELOG へのリンク]
- Issue 報告: [GitHub Issues / サポートフォーム]
```

---

### テンプレート3: ステップバイステップ チュートリアル

```markdown
# チュートリアル: [TASK_NAME]

このチュートリアルでは [GOAL] の方法を説明します。

## 所要時間

約 [N] 分

## 前提条件

- [ ] [ツール名] がインストール済み → [インストールガイドへのリンク]
- [ ] [サービス名] のアカウントを持っている

## ステップ1: [STEP_TITLE]

[このステップで何をするか・なぜするかを1文で説明]

**Windows (PowerShell)**
\`\`\`powershell
New-Item -ItemType Directory -Path "my-project"
Set-Location my-project
\`\`\`

**macOS / Linux**
\`\`\`bash
mkdir my-project && cd my-project
\`\`\`

✓ この時点で `my-project/` ディレクトリが作成されています。

## ステップ2: [STEP_TITLE]

...（各ステップに確認チェックポイントを入れる）

## 完了

[達成したことを1〜2文でまとめる]

次のステップ:
- [発展的なトピック1](link)
- [発展的なトピック2](link)
```

---

## スタイルガイド

### 言語と口調

| 項目 | 推奨 | 非推奨 |
|---|---|---|
| 人称 | 「あなた」「ユーザー」 | 「自分」「君」 |
| 文体 | ですます調（丁寧体） | だ・である調 |
| 命令形 | 「〜してください」「〜します」 | 「〜せよ」 |
| 専門用語 | 初出時に括弧で英語を補記 | 説明なしの略語 |

### フォーマット規則

| 要素 | 形式 | 例 |
|---|---|---|
| UI ボタン・メニュー | **太字** | **「送信」ボタンをクリック** |
| コマンド・コード | `バッククオート` | `npm install` |
| ファイル・パス | `バッククオート` | `src/index.ts` |
| プレースホルダー | `大文字スネークケース` | `YOUR_API_KEY` |
| 強調・注意 | > ブロッククオート | > **注意:** ... |

### Windows 固有の考慮事項

- パス区切り文字: `\`（ドキュメントには `/` も併記するか、クロスプラットフォームツールを推奨）
- 環境変数: `$env:VARIABLE_NAME`（PowerShell）/ `%VARIABLE_NAME%`（cmd）
- シェルスクリプト: `.sh` の代替として `.ps1` の例を記載する
- 改行コード: Git の `autocrlf` 設定に注意が必要な場合は明記する

### GitHub Copilot / Claude Code との連携

ドキュメントを生成・改善する際は、以下のプロンプトパターンが有効:

```
「[ファイルパス] を読んで、technical-writer スキルに従い README を作成して」
「この API のデベロッパーガイドを日本語で作成して。openapi.yaml も参照して」
「このチュートリアルをレビューして、5つのコア原則に沿って改善点を提案して」
```

> **API 仕様の生成**: api-designer で OpenAPI ファイルを作成し、そのファイルを参照しながらデベロッパーガイドを technical-writer に書かせる、という連携フローが推奨。

---

## 評価メモ（取り込み時）

**参照元**: [awesome-llm-apps technical-writer](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/awesome_agent_skills/technical-writer) (MIT)

**評価結果**:

| 項目 | 評価 | コメント |
|---|---|---|
| コア原則の質 | ✅ 優秀 | 5原則は実践的で普遍的 |
| テンプレートの完成度 | ✅ 良好 | README・デベロッパーガイド・Tutorial の3種類をカバー |
| Windows 対応 | ⚠️ 欠如 → 追加済み | PowerShell/cmd 例を補完 |
| 日本語対応 | ⚠️ 欠如 → 追加済み | 全文日本語化・スタイルガイドを追補 |
| Copilot 連携 | ⚠️ 欠如 → 追加済み | プロンプトパターンを補完 |

**変更点のサマリー**:
- 全文を日本語に翻訳（コード例・プレースホルダーは英語のまま）
- Windows（PowerShell）向けコマンド例をすべてのテンプレートに追加
- 日本語スタイルガイドを新規追加（ですます調、人称、専門用語の扱い方）
- GitHub Copilot / Claude Code との連携プロンプトパターンを追加
- 5原則に「段階的な開示」の構造をより明確に記述
- API エンドポイント仕様テンプレート（テンプレート2）を削除 → api-designer に委任
- 代わりに「デベロッパーガイド」テンプレートを追加（認証・ユースケース・エラーハンドリング・SDK・OpenAPI 参照）
