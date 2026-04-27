# markitdown（Officeファイル変換）

markitdown CLIでOfficeファイル（Word・Excel・PowerPoint・PDF等）をMarkdownに変換し、Obsidian Flavored Markdownに整形する。

未インストールの場合: `pip install markitdown`

## 対応フォーマット

| 拡張子 | 形式 |
|--------|------|
| `.docx` | Microsoft Word |
| `.xlsx` | Microsoft Excel |
| `.pptx` | Microsoft PowerPoint |
| `.pdf` | PDF |
| `.html` / `.htm` | HTML |
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

## ワークフロー

### 1. Markdownに変換

```bash
markitdown document.docx -o output.md
```

### 2. Obsidian Flavored Markdownに整形

変換後のMarkdownをObsidian向けに整形する:

**フロントマターを追加**:

```yaml
---
title: ドキュメントタイトル
date: 2024-01-15
tags:
  - imported
  - office
source: document.docx
---
```

**内部リンクをウィキリンクに変換**: ボルト内の既存ノートへの参照が含まれている場合、標準Markdownリンク `[ノート名](ノート名.md)` をウィキリンク `[[ノート名]]` に変換する。

**コールアウトに変換**: 重要な注記・警告・ヒントは Obsidian コールアウト形式に整形する:

```markdown
> [!note]
> 元のドキュメントの注記内容

> [!warning]
> 元のドキュメントの警告内容
```

**画像埋め込みに変換**: 画像パスはObsidian埋め込み形式 `![[image.png]]` に変換する。

**ハイライトに変換**: 重要箇所は `==ハイライト==` 形式を使用する。

### 3. 整形例

変換前（markitdown出力）:

```markdown
# プロジェクト計画書

## 概要

このドキュメントはプロジェクトの計画を説明する。

**注意**: 期限は厳守すること。

![図1](images/diagram.png)
```

変換後（Obsidian Flavored Markdown）:

```markdown
---
title: プロジェクト計画書
date: 2024-01-15
tags:
  - imported
  - project
source: project-plan.docx
---

# プロジェクト計画書

## 概要

このドキュメントはプロジェクトの計画を説明する。

> [!warning]
> 期限は厳守すること。

![[diagram.png]]
```
