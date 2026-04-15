# wiki-use ページ規約

## ページフォーマット

すべての Wiki ページは以下の YAML フロントマターを持つ:

```yaml
---
title: "<ページタイトル>"
category: concepts | entities | topics
tags: [タグ1, タグ2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources:
  - sources/<YYYY-MM-DD>-<slug>.<ext>
---
```

---

## カテゴリ別フォーマット

### concepts（概念・用語ページ）

```markdown
---
title: "概念名"
category: concepts
tags: [...]
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources:
  - sources/2026-01-01-example.pdf
---

# 概念名

## 定義

[1〜3文で概念を明確に定義する]

## 詳細

[概念の詳しい説明。背景・仕組み・意義など]

## 特徴・性質

- 特徴1
- 特徴2

## 使用例・適用場面

[いつ・どこで・どのように使われるか]

## 関連

- [[関連概念1]]
- [[関連概念2]]

## 出典

- [ソースタイトル](sources/2026-01-01-example.pdf)
```

---

### entities（人物・プロダクト・組織ページ）

```markdown
---
title: "エンティティ名"
category: entities
tags: [...]
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources:
  - sources/2026-01-01-example.pdf
---

# エンティティ名

## 概要

[エンティティの簡潔な説明]

## 詳細

[エンティティに関する詳しい情報]

## 関連する概念

- [[概念1]]
- [[概念2]]

## 出典

- [ソースタイトル](sources/2026-01-01-example.pdf)
```

---

### topics（テーマ別まとめページ）

```markdown
---
title: "テーマ名"
category: topics
tags: [...]
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources:
  - sources/2026-01-01-example.pdf
---

# テーマ名

## 概要

[テーマの全体像を説明する]

## 主要な概念

- [[概念1]]: 簡潔な説明
- [[概念2]]: 簡潔な説明

## 詳細

[テーマの詳しい内容]

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
| Attention Mechanism | `wiki/concepts/attention-mechanism.md` |
| GPT-4 | `wiki/entities/gpt-4.md` |
| Andrej Karpathy | `wiki/entities/andrej-karpathy.md` |
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

```markdown
# Wiki インデックス

最終更新: YYYY-MM-DD

## concepts

| ページ | 概要 | 作成日 |
|--------|------|--------|
| [[attention-mechanism]] | ... | 2026-01-01 |

## entities

| ページ | 概要 | 作成日 |
|--------|------|--------|
| [[gpt-4]] | ... | 2026-01-01 |

## topics

| ページ | 概要 | 作成日 |
|--------|------|--------|
| [[transformer-introduction]] | ... | 2026-01-01 |
```

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

`wiki_init.py` が自動生成するWiki構造の定義書:

```markdown
# Wiki スキーマ

このファイルは Wiki の構造・規約を定義します。LLM はこのファイルを参照して
一貫したページを作成・更新してください。

## ディレクトリ構造

- `wiki/concepts/` — 概念・用語
- `wiki/entities/` — 人物・プロダクト・組織
- `wiki/topics/` — テーマ別まとめ

## ページ規約

- フロントマター必須（title, category, tags, created, updated, sources）
- ウィキリンク形式: [[ファイル名]]
- ファイル名: 英小文字 + ハイフン

## このWikiのドメイン

[ユーザーが追記: このWikiが扱うテーマ・領域]
```
