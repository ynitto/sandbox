# wiki-use ページ規約

## 目次

- [ページフォーマット](#ページフォーマット)
- [カテゴリ別フォーマット](#カテゴリ別フォーマット)
  - [atoms](#atoms個別トピックページ)
  - [topics](#topicsテーマ別まとめページ)
- [ファイル命名規則](#ファイル命名規則)
- [ウィキリンク形式](#ウィキリンク形式)
- [index.md フォーマット](#indexmd-フォーマット)
- [log.md フォーマット](#logmd-フォーマット)
- [hot.md フォーマット](#hotmd-フォーマット)
- [SCHEMA.md フォーマット](#schemamd-フォーマット)

---

## ページフォーマット

すべての Wiki ページは以下の YAML フロントマターを持つ:

```yaml
---
title: "<ページタイトル>"
type: concept | term | person | organization | product | topic
tags: [タグ1, タグ2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources:
  - sources/<YYYY-MM-DD>-<slug>.<ext>
summary: "<index.md 用の1文説明（80文字以内）>"
---
```

- `type` は atoms では必須。topics では `topic` を指定する。
- `summary` はindex.mdの1行説明として使われる。省略するとスクリプトが本文から自動生成する。

---

## 発行日の記載ルール

発行日はページのフロントマターには付けず、**情報ブロックごとに本文内で注記**する。
発行日が不明な場合は付けなくてよい。

**内容が単一ソースの場合**: セクション末尾に一行追記する。
```markdown
## 詳細

[説明テキスト]

*発行: 2024-03-01 / [[source-slug]]*
```

**内容が複数ソースから追記された場合**: 各追記ブロックの末尾に個別に付ける。
```markdown
## 詳細

[ソースAの情報]

*発行: 2024-03-01 / [[source-a]]*

[ソースBの情報]

*発行: 2023-11-10 / [[source-b]]*
```

---

## カテゴリ別フォーマット

### atoms（個別トピックページ）

概念・用語・人物・製品・組織など、1つのトピックを扱うページ。
`type` で細分類する（concept / term / person / organization / product）。

```markdown
---
title: "トピック名"
type: concept
tags: [...]
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources:
  - sources/2026-01-01-example.pdf
summary: "1文の簡潔な説明（80文字以内）"
---

# トピック名

## 概要

[1〜3文で明確に説明する。定義・役割・概要など]

*発行: 2024-03-01 / [[source-slug]]*

## 詳細

[詳しい情報。背景・仕組み・意義など。通算400語以内を目安にする]

## 関連

- [[関連トピック1]]
- [[関連トピック2]]

## 出典

- [ソースタイトル](sources/2026-01-01-example.pdf)
```

`type` 別の書き方の違いは最小限に留める:
- `concept` / `term`: 概要は「定義」として書く
- `person`: 概要は「人物紹介」として書く（所属・業績など）
- `organization` / `product`: 概要は「設立・目的・概要」として書く

---

### topics（テーマ別まとめページ）

複数のatomsを横断するまとめ・比較・分析ページ。

```markdown
---
title: "テーマ名"
type: topic
tags: [...]
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources:
  - sources/2026-01-01-example.pdf
summary: "1文の簡潔な説明（80文字以内）"
---

# テーマ名

## 概要

[テーマの全体像を1〜3文で説明する]

## 主要なトピック

- [[atom-slug-1]]: 簡潔な説明
- [[atom-slug-2]]: 簡潔な説明

## 詳細

[テーマの詳しい内容・分析・比較]

*発行: 2024-03-01 / [[source-slug]]*

## 関連テーマ

- [[関連トピック1]]

## 出典

- [ソースタイトル](sources/2026-01-01-example.pdf)
```

---

## ファイル命名規則

- **スペース → ハイフン**: `attention-mechanism.md`
- **英数字・ハイフンのみ**: 日本語タイトルはローマ字またはキーワード英語化
- **小文字**: `transformer-architecture.md`（大文字不使用）

例:
| タイトル | ファイル名 |
|---------|-----------|
| Attention Mechanism | `wiki/atoms/attention-mechanism.md` |
| GPT-4 | `wiki/atoms/gpt-4.md` |
| Andrej Karpathy | `wiki/atoms/andrej-karpathy.md` |
| トランスフォーマー入門 | `wiki/topics/transformer-introduction.md` |

---

## ウィキリンク形式

内部リンクは Obsidian ウィキリンク形式 `[[ファイル名（拡張子なし）]]` を使用:

```markdown
[[attention-mechanism]]
[[gpt-4]]
```

ページタイトルとファイル名が異なる場合は表示名付き:
```markdown
[[attention-mechanism|Attention Mechanism]]
```

---

## index.md フォーマット

1ページ1行の箇条書き。LLMがqueryで最初に読む羅針盤として使う。
詳細はページ本体が持つので、ここでは最小限の情報に留める。

```markdown
# Wiki インデックス

最終更新: YYYY-MM-DD

## atoms

- [[attention-mechanism]] — スケールドドット積アテンション機構（concept, 2 sources）
- [[gpt-4]] — OpenAIのGPT-4言語モデル（product, 1 source）
- [[andrej-karpathy]] — AI研究者（person, 3 sources）

## topics

- [[transformer-introduction]] — トランスフォーマーアーキテクチャの全体まとめ（1 source）
```

- `summary` はフロントマターの `summary:` フィールドから自動取得される
- `type` と `N sources` はフロントマターから自動取得される
- `update-index` スクリプトが自動でこの形式で追記する

---

## log.md フォーマット

追記専用。新しいエントリを先頭に追加する:

```markdown
# Wiki 操作ログ

## 2026-01-15 14:30 — ingest

- ソース: `sources/2026-01-15-attention-paper.pdf`
- 作成: 8ページ（concepts: 5, entities: 2, topics: 1）
- 更新: 3ページ
- 主な追加概念: attention-mechanism, self-attention, multi-head-attention

---

## 2026-01-10 10:00 — init

- Wiki を初期化しました
- wiki_root: ~/Documents/wiki
```

---

## hot.md フォーマット

直近 20 件の取り込みページを維持するコンテキストキャッシュ:

```markdown
# Hot Pages（最近のコンテキスト）

最終更新: YYYY-MM-DD

<!-- 新しい取り込みで上書きされる。最大20件 -->

- [[attention-mechanism]] — 2026-01-15 更新
- [[self-attention]] — 2026-01-15 作成
- [[multi-head-attention]] — 2026-01-15 作成
```

---

## SCHEMA.md フォーマット

`wiki_init.py` が初期生成するWiki構造の定義書。
**LLMとユーザーが協力して育てる**ライブドキュメント。  
lintの結果、運用で気づいたルール、ドメイン固有の規約などを随時追記する。

```markdown
# Wiki スキーマ

このファイルは Wiki の構造・規約を定義します。LLM はこのファイルを参照して
一貫したページを作成・更新してください。このファイル自体を LLM とユーザーが
協力して育てることで、ドメインに最適化された Wiki 運用ルールを確立していく。

## ディレクトリ構造

- `wiki/atoms/`  — 個別トピックのページ（概念・用語・人物・製品・組織など）
- `wiki/topics/` — 複数 atom を横断するまとめ・比較・分析ページ
- `wiki/meta/`   — hot.md（最近のコンテキスト）
- `sources/`     — 取り込み元の原文（変更しない）

## ページ規約

- フロントマター必須（title, type, tags, created, updated, sources, summary）
- `type` フィールド: concept | term | person | organization | product | topic
- ウィキリンク形式: [[ファイル名]]（拡張子なし）
- ファイル名: 英小文字 + ハイフン

## このWikiのドメイン

[ユーザーが追記: このWikiが扱うテーマ・領域]

## カスタムルール

[このWikiに特有の運用ルール・強調したい観点があれば追記]
```
