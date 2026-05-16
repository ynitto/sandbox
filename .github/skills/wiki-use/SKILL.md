---
name: wiki-use
description: Karpathy LLM Wiki パターンに基づく知識ベース管理スキル。「wikiに取り込んで」「wikiに追加して」「URLをwikiに保存して」でingest、「wikiを検索して」でquery、「wikiを初期化して」でinit、「wikiをチェックして」でlintが発動する。ソース・URLから概念ページを自動生成・更新する。
metadata:
  version: 1.3.0
  tier: experimental
  category: knowledge
  config_script: scripts/wiki_init.py
  tags:
    - wiki
    - knowledge-base
    - obsidian
    - llm-wiki
    - karpathy
---

# wiki-use（LLM Wiki Use）

Karpathy LLM Wiki パターンを Claude Code で実装する知識ベース管理スキル。
ソースファイルや URL から自動的に構造化された Wiki ページを生成・更新し、知識を蓄積する。

設定リファレンス: [`references/configuration.md`](references/configuration.md)
ページ規約: [`references/page-conventions.md`](references/page-conventions.md)

---

## Wiki の構造

```
<wiki_root>/
├── wiki/             ← LLM が管理する知識ページ
│   ├── atoms/        ← 個別トピックのページ（概念・用語・人物・製品・組織）
│   ├── topics/       ← 複数atomsを横断するまとめ・比較・分析ページ
│   └── meta/
│       ├── hot.md      ← 最近のコンテキスト（直近20件）
│       └── queries.md  ← 価値あるクエリの記録（直近50件）
├── SCHEMA.md         ← このWikiの構造・規約定義（LLMと共に育てる）
├── index.md          ← 全ページの目録（1ページ1行の箇条書き）
└── log.md            ← 操作ログ（追記専用）
```

ソースデータは wiki_root 外で管理する（取り込み前に保全済みであることを前提とする）。

### atoms vs topics の使い分け

- **atoms**: 「〜とは何か」「〜は誰か」と1文で答えられる個別トピック。  
  typeフロントマターで `concept | term | person | organization | product` を指定する。
- **topics**: 複数のatomsをつなぐ横断的なまとめ・比較・分析。  
  単一のatomに収まらない場合はtopicsに置く。

`wiki_root` は `<agent_home>/skill-registry.json` の `skill_configs.wiki-use` セクションで指定する（後述）。

## ローカルパス規約

- wiki-use が生成・記録として扱うローカルパスは、**常に `wiki_root` 起点の相対パス**で記述する
- 絶対パス（`/Users/...` や `/home/...`）は使わない
- 例: `wiki/atoms/attention-mechanism.md`、`wiki/meta/hot.md`、`index.md`、`log.md`

---

## 設定の読み込み

操作開始時に必ず以下を実行してパスを確認する:

```bash
python scripts/wiki_utils.py config
```

出力例:
```
wiki_root: /home/user/Documents/wiki
```

設定ファイルが存在しない場合は `init` でガイドする。

---

## 操作一覧

> **各操作を実行する前に、対応する references ファイルを必ず読み込むこと。**

| 操作 | トリガー例 | 詳細手順 |
|------|-----------|---------|
| **init** | 「wikiを初期化して」「wiki-useをセットアップして」 | [`references/op-init.md`](references/op-init.md) |
| **ingest** | 「wikiに取り込んで」「ソースを取り込んで」「〈ファイル〉をwikiに追加して」 | [`references/op-ingest.md`](references/op-ingest.md) |
| **query** | 「wikiを検索して」「〜についてwikiで調べて」「〜の知識は？」 | [`references/op-query.md`](references/op-query.md) |
| **lint** | 「wikiをチェックして」「リントして」「wiki の整合性を確認して」 | [`references/op-lint.md`](references/op-lint.md) |

query 操作は **3ステップ**で構成される:
1. **過去クエリ確認** — `queries.md` を見て類似質問の有無を確認
2. **検索・回答** — wiki ページを検索して回答する
3. **保存** — 価値ある回答は topics ページに、クエリは `queries.md` に記録する

---

## プロアクティブな操作

以下の状況では自律的に行動すること:

- ユーザーが URL やファイルパスを貼り付けて「これ読んで」「まとめて」と言ったとき → `ingest` を提案・実行する（ソースは必ずユーザーが指定したパス/URL を使う）
- セッション開始時に wiki を使う作業が想定されるとき → `python scripts/wiki_utils.py config` で設定を確認する
- query の結果が 0 件で「ページがない」と分かったとき → ingest を勧める

---

## 使用例

```
ユーザー: 「この論文をwikiに取り込んで」（ファイルパス添付）
→ references/op-ingest.md を読み込んでケース A を実行する

ユーザー: 「このフォルダをwikiに取り込んで」（フォルダパス添付）
→ references/op-ingest.md を読み込んでケース B を実行する

ユーザー: 「トランスフォーマーについてwikiで調べて」
→ references/op-query.md を読み込んで query を実行する

ユーザー: 「wikiの整合性チェックして」
→ references/op-lint.md を読み込んで lint を実行する

ユーザー: 「wiki-useをセットアップして」
→ references/op-init.md を読み込んで init を実行する
```
