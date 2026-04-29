# markitdown（Office/PDFファイル変換）

markitdown CLIでOffice/PDFファイルをMarkdownに変換する。変換後は `ofm_formatter.py` で機械的後処理を行い、さらにLLMが意味的変換を加えてObsidian Flavored Markdownに整形する。

> **HTMLファイルは対象外** — `.html` / `.htm` ファイルは defuddle を使うこと → [defuddle.md](defuddle.md)

未インストールの場合: `pip install markitdown`

## 対応フォーマット

| 拡張子 | 形式 |
|--------|------|
| `.docx` | Microsoft Word |
| `.xlsx` | Microsoft Excel |
| `.pptx` | Microsoft PowerPoint |
| `.pdf` | PDF |
| `.csv` | CSV |
| `.json` | JSON |
| `.xml` | XML |
| `.zip` | ZIPアーカイブ内のファイル |
| `.jpg` / `.png` / `.gif` | 画像（ALTテキスト抽出） |
| `.mp3` / `.wav` | 音声（文字起こし） |

## 使い方

標準出力に変換結果を表示:

```bash
markitdown <ファイルパス>
```

ファイルに保存:

```bash
markitdown <ファイルパス> -o output.md
```

## ワークフロー（3段階）

### ステップ 1: markitdownでMarkdownに変換

```bash
markitdown document.docx -o raw.md
```

### ステップ 2: ofm_formatter.py で機械的後処理

```bash
python references/ofm_formatter.py raw.md output.md
# またはパイプ
markitdown document.docx | python references/ofm_formatter.py - output.md
```

`ofm_formatter.py` が自動で行う変換（ルールベース・決定論的）:
- **NaN除去**: テーブルセル内の `NaN` / `nan`（Excel空セル・結合セル由来）を空文字に置換
- **空白正規化**: 行末空白の除去、3行以上連続する空行を2行に圧縮
- **フロントマター付加**: `title`・`date`・`tags: [imported]`・`source` を自動生成（既存フロントマターがある場合はスキップ）
- **画像リンク変換**: `![alt](path/to/image.png)` → `![[image.png]]`（外部URLは除外）
- **内部リンク変換**: `[text](note.md)` → `[[note|text]]`（`.md` ファイルへのリンクのみ・保守的）
- **見出し正規化**: H1が複数ある場合、2番目以降をH2に降格

> スクリプトの詳細: [ofm_formatter.py](ofm_formatter.py)

### ステップ 3: LLMによる意味的変換

`ofm_formatter.py` の出力を読み、以下の意味的変換を行う:

**コールアウトに変換**: ドキュメント内の注記・警告・ヒント・重要事項を Obsidian コールアウト形式に整形する:

```markdown
> [!note]
> 元のドキュメントの注記内容

> [!warning]
> 元のドキュメントの警告内容
```

**タグの推定**: ドキュメントの内容からタグを追加してフロントマターを補完する。

**ウィキリンクの最適化**: ボルト内の実在ノートへの参照を `[[ノート名]]` で追加する（Vault構造が分かる場合）。

**ハイライト**: 特に重要な箇所は `==ハイライト==` 形式を使用する。

## 変換例

**変換前（markitdown出力の raw.md）**:

```markdown
# プロジェクト計画書

## 概要

このドキュメントはプロジェクトの計画を説明する。

**注意**: 期限は厳守すること。

![図1](images/diagram.png)
```

**ステップ2後（ofm_formatter.py適用後）**:

```markdown
---
title: document
date: 2024-01-15
tags:
  - imported
source: document.docx
---

# プロジェクト計画書

## 概要

このドキュメントはプロジェクトの計画を説明する。

**注意**: 期限は厳守すること。

![[diagram.png]]
```

**ステップ3後（LLM意味的変換後）**:

```markdown
---
title: プロジェクト計画書
date: 2024-01-15
tags:
  - imported
  - project
source: document.docx
---

# プロジェクト計画書

## 概要

このドキュメントはプロジェクトの計画を説明する。

> [!warning]
> 期限は厳守すること。

![[diagram.png]]
```
