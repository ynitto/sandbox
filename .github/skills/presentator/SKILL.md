---
name: presentator
description: JSONスペックからPowerPointプレゼンテーションを生成するスキル。「スライドを作って」「プレゼン資料を作って」「PPTXを生成して」「既存のPPTXを編集して」「プレゼンのスタイルを作りたい」などで発動する。ブリーフィング→アウトライン→アートディレクション→スライド構成→レビューの段階的ワークフローで高品質なプレゼンを作成する。
metadata:
  version: 1.0.0
  tier: stable
  category: presentation
  tags:
    - pptx
    - powerpoint
    - slides
    - presentation
    - json
---

# presentator

JSONスペックからPowerPointプレゼンテーションを生成するスキル。
パスはこの SKILL.md からの相対パス。コマンド実行前にこのディレクトリに `cd` すること。

## セットアップ（初回のみ）

```bash
cd .github/skills/presentator
uv sync
```

アイコンが必要な場合:

```bash
uv run python3 scripts/download_aws_icons.py
uv run python3 scripts/download_material_icons.py
```

## CLI

```bash
uv run python3 scripts/pptx_builder.py {コマンド} [引数]
```

**重要:** ワークフローを読み込む前に、スライドの構成・内容・デザイン・レイアウトについて一切決定しないこと。ワークフローファイルにブリーフィング・アウトライン・アートディレクションを含む完全なプロセスが定義されている。ワークフローを読み込んでからステップに従って進めること。

**開始時:** 以下のオプションを提示し、どれを行うか確認する。

A. 新規作成 — ゼロからスライドを作る
B. 既存PPTX編集 — 提供されたファイルを修正する
C. 手動編集同期 — ユーザーがPowerPointで直接編集した後の続き
D. スタイル作成 — 再利用可能なスタイルガイドを作る

## ワークフロー A: 新規プレゼンテーション

既存PPTXがない場合。
→ `uv run python3 scripts/pptx_builder.py workflows create-new-1-briefing` を実行して開始。各ファイルの「次のステップ」に従って進む。

## ワークフロー B: 既存PPTXの編集

既存PPTXが提供された場合。
→ `uv run python3 scripts/pptx_builder.py workflows edit-existing` を実行して開始。

## ワークフロー C: 手動編集の同期

ユーザーが生成済みPPTXをPowerPointで直接編集した後、さらに変更を加えたい場合。
→ `uv run python3 scripts/pptx_builder.py workflows create-new-4-hand-edit-sync` を実行して開始。

## ワークフロー D: スタイル作成

再利用可能なスタイルガイドを新規作成したい場合。
→ `uv run python3 scripts/pptx_builder.py workflows create-style` を実行して開始。
