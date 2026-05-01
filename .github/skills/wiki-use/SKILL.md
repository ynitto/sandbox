---
name: wiki-use
description: Karpathy LLM Wiki パターンに基づく知識ベース管理スキル。「wikiに取り込んで」「wikiに追加して」「URLをwikiに保存して」でingest、「wikiを検索して」でquery、「wikiを初期化して」でinit、「wikiをチェックして」でlintが発動する。ソース・URLから概念ページを自動生成・更新する。
metadata:
  version: 1.1.0
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
├── sources/          ← 取り込み元の原文（変更しない）
│   └── <YYYY-MM-DD>-<slug>.<ext>
├── wiki/             ← LLM が管理する知識ページ
│   ├── concepts/     ← 概念・用語ページ
│   ├── entities/     ← 人物・プロダクト・組織ページ
│   ├── topics/       ← テーマ別まとめページ
│   └── meta/         ← hot.md（最近のコンテキスト）
├── SCHEMA.md         ← このWikiの構造・規約定義
├── index.md          ← 全ページの目録
└── log.md            ← 操作ログ（追記専用）
```

`wiki_root` は `<agent_home>/skill-registry.json` の `skill_configs.wiki-use` セクションで指定する（後述）。

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

| 操作 | トリガー例 | スクリプト |
|------|-----------|-----------|
| **init** | 「wikiを初期化して」「wiki-useをセットアップして」 | `wiki_init.py` |
| **ingest** | 「wikiに取り込んで」「ソースを取り込んで」「〈ファイル〉をwikiに追加して」 | `wiki_ingest.py` + エージェントによる編集 |
| **query** | 「wikiを検索して」「〜についてwikiで調べて」「〜の知識は？」 | `wiki_query.py` |
| **lint** | 「wikiをチェックして」「リントして」「wiki の整合性を確認して」 | `wiki_lint.py` |

---

## init（Wiki を初期化する）

```bash
python scripts/wiki_init.py
```

実行内容:
1. `<agent_home>/skill-registry.json` の `skill_configs.wiki-use` が未設定の場合、ユーザーに `wiki_root` を確認する
2. `wiki_root` 配下に標準ディレクトリ構造を作成する
3. `SCHEMA.md`・`index.md`・`log.md`・`wiki/meta/hot.md` を初期テンプレートで生成する
4. 完了後に構造を表示する

設定例（`<agent_home>/skill-registry.json` の `skill_configs.wiki-use`）:
```json
{
  "skill_configs": {
    "wiki-use": {
      "wiki_root": "~/Documents/wiki"
    }
  }
}
```

---

## ingest（ソースを取り込む）

ingest はこのスキルの中核操作。**エージェントが** ソースを読み込み、Wiki ページを生成・更新する。

---

### ケース A: 単一ファイル / URL の場合

```bash
python scripts/wiki_ingest.py copy --source <ファイルパス|URL> [--published YYYY-MM-DD]
```

コピー後、以下の「全ステップ」を 1 回実行する。

---

### ケース B: フォルダ指定（複数ファイル）の場合

**必ずバッチ分割して処理する。** 全ファイルを一度に読まない。

#### B-1. バッチ一覧を取得する

```bash
python scripts/wiki_ingest.py list-batches --source <フォルダパス> [--batch-size 5]
```

出力例:
```
TOTAL: 12 files, 3 batches (batch-size: 5)

=== BATCH 1/3 ===
/path/to/file01.md
/path/to/file02.md
...

=== BATCH 2/3 ===
...
```

#### B-2. 各バッチに対して「全ステップ」を繰り返す

バッチの数だけ、以下の「全ステップ」を繰り返す。  
**1 バッチが完了してから次のバッチに進む。**

---

### 全ステップ（1 バッチ = N ファイルに対して実行）

#### ステップ 1: ソースをコピー

バッチ内の各ファイルを 1 件ずつ `copy` する。  
同一内容が既にある場合は `[SKIP]` と表示されスキップされる。

```bash
# バッチ内のファイルを 1 件ずつ実行
python scripts/wiki_ingest.py copy --source <ファイルパス> [--published YYYY-MM-DD]
```

#### ステップ 2: 現在の Wiki 状態を確認

```bash
python scripts/wiki_query.py list-pages
```

既存ページの一覧と概要を確認し、重複・関連ページを把握する。  
（バッチの最初の 1 回のみ実行すればよい）

#### ステップ 3: ソースを読み込み、Wiki ページを生成・更新（エージェントが実行）

**3-1. ソースを精読する**  
バッチ内の各ファイルを read_file で読み込む。  
フロントマターに `published` フィールドがある場合はその値を控える。

**3-2. 概念を抽出する**  
ソースから以下を列挙する:
- 新しい概念・用語（`wiki/concepts/` に作成）
- 人物・プロダクト・組織（`wiki/entities/` に作成）
- テーマ・まとめ（`wiki/topics/` に作成）

**3-3. 既存ページとの照合**  
各概念について既存ページの有無を確認する（`wiki_query.py search` を使う）。
- 既存ページがある → そのページに情報を**追記・更新**する
- 新しい概念 → 新規ページを**作成**する

**3-4. ページを作成・更新する**  
各ページは「ページフォーマット」（`references/page-conventions.md`）に従う。  
1 バッチから 5〜15 ページを作成・更新するのが目安。  
発行日が分かっている場合は、そのソースから書き込んだ情報ブロックの末尾にインライン注記する:

```
*発行: YYYY-MM-DD / [[ソーススラッグ]]*
```

発行日不明の場合は注記不要（ユーザーに確認しない）。

**3-5. クロスリファレンスを追加する**
- 新規ページを参照するページがあれば `[[ページ名]]` リンクを追記する
- 関連概念ページ同士を `## 関連` セクションでつなぐ

#### ステップ 4: index.md・log.md・hot.md を更新する

```bash
# index.md に新規ページを登録
python scripts/wiki_ingest.py update-index --pages <作成したページのパスリスト>

# log.md に操作を記録（バッチ内の各ソースごとに実行）
python scripts/wiki_ingest.py log \
  --source <ソースパス> \
  --pages-created <作成数> \
  --pages-updated <更新数> \
  [--published YYYY-MM-DD]

# hot.md（最近のコンテキスト）を更新（直近 20 件を維持）
python scripts/wiki_ingest.py update-hot --pages <作成・更新したページのパスリスト>
```

> ステップ 4 が完了したら次のバッチへ進む（ケース B の場合）。

---

### ページ作成・更新の判断基準

| 状況 | 判断 |
|------|------|
| ソースに固有の新しい概念がある | 新規ページを作成する |
| 既存ページの概念にソースが新情報を提供 | 既存ページに追記・更新する |
| 既存ページとほぼ同じ内容 | 既存ページに出典として追記するだけ |
| 非常に小さな補足情報 | 関連ページの「関連」セクションに1行追記 |
| 固有名詞・人名・組織名 | `entities/` に作成（重複は統合する） |
| 複数概念を横断するテーマ | `topics/` に作成 |

---

## query（Wiki を検索する）

```bash
# キーワードで検索
python scripts/wiki_query.py search "<キーワード>"

# ページ一覧表示
python scripts/wiki_query.py list-pages

# 特定ページを表示
python scripts/wiki_query.py show wiki/concepts/<ページ名>.md

# hot.md（最近のコンテキスト）を表示
python scripts/wiki_query.py hot
```

検索結果から関連ページを特定し、Read ツールで内容を取得して回答する。
検索にヒットしないが wiki に存在しそうな場合は `list-pages` で全体を確認する。

---

## lint（Wiki の整合性をチェックする）

```bash
python scripts/wiki_lint.py
```

チェック内容:
- **孤立ページ**: `index.md` に未登録のページ
- **リンク切れ**: `[[ページ名]]` 形式のリンクが存在しないページを参照している
- **未取り込みソース**: `sources/` にコピー済みだが `log.md` に未記録のファイル
- **空ページ**: 本文が極端に短いページ（100文字未満）

出力例:
```
[WARN] 孤立ページ: wiki/concepts/foo.md (index.mdに未登録)
[WARN] リンク切れ: wiki/topics/bar.md → [[baz]] (baz.mdが存在しない)
[INFO] 孤立ソース: sources/2026-01-01-some-paper.pdf (log.mdに未記録)
[OK] 空ページなし
```

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
→ wiki_ingest.py copy --source <path>
→ ソース精読（フロントマターから published 日付を取得）
→ 概念抽出（10ページ作成・3ページ更新）、発行日が分かる情報ブロックに `*発行: YYYY-MM-DD / [[ソース]]*` を注記
→ wiki_ingest.py update-index / log --published YYYY-MM-DD / update-hot
→ 「10ページ作成・3ページ更新しました」と報告

ユーザー: 「このフォルダをwikiに取り込んで」（フォルダパス添付）
→ wiki_ingest.py list-batches --source <folder> --batch-size 5
→ [BATCH 1/3] の 5 ファイルを copy → 精読 → 概念抽出 → ページ作成 → index/log/hot 更新
→ [BATCH 2/3] の 5 ファイルを同様に処理
→ [BATCH 3/3] の残りファイルを同様に処理
→ 「全バッチ完了: 計 X ページ作成・Y ページ更新」と報告

ユーザー: 「トランスフォーマーについてwikiで調べて」
→ wiki_query.py search "トランスフォーマー"
→ 該当ページを Read → 要約して回答

ユーザー: 「wikiの整合性チェックして」
→ wiki_lint.py
→ 問題点を報告し、修正方法を提案する

ユーザー: 「wiki-useをセットアップして」
→ wiki_init.py
→ 設定ファイルのパスを確認し、ディレクトリ構造を作成
```
