# query — Wiki を検索する

パス規約:
- ローカルパスは `wiki_root` 起点の相対パスで記述する（例: `wiki/atoms/<ページ名>.md`）
- 絶対パスは使わない

## ステップ 0（任意）: コンテキストを確認する

検索の前に補足コンテキストとして参照する。**メインの調査はステップ 1 の wiki 検索で行う。**

```bash
# 最近取り込んだページを確認する
python scripts/wiki_query.py hot

# 過去に保存した価値あるクエリとその回答ページを確認する
python scripts/wiki_query.py queries
```

---

## ステップ 1: 検索

```bash
# キーワードで検索
python scripts/wiki_query.py search "<キーワード>"

# ページ一覧表示
python scripts/wiki_query.py list-pages

# 特定ページを表示
python scripts/wiki_query.py show wiki/atoms/<ページ名>.md

# hot.md（最近のコンテキスト）を表示
python scripts/wiki_query.py hot
```

検索結果から関連ページを特定し、Read ツールで内容を取得して回答する。
検索にヒットしないが wiki に存在しそうな場合は `list-pages` で全体を確認する。

---

## ステップ 2: 回答をwikiにファイルバックする

以下に該当する回答は、チャット履歴に消えてしまう前に wiki に保存する価値がある。
LLMは回答後に該当するか判断し、該当する場合はユーザーに保存を提案する。

**保存を提案すべきケース**:
- 複数のatoms/topicsページを横断して比較・分析した回答
- 新しい接続関係や洞察の発見（"a connection you discovered"）
- 体系的なまとめ・概要

**保存の手順**:
1. 回答内容を `wiki/topics/<slug>.md` として保存する（既存topicsに統合できる場合は統合）
2. `python scripts/wiki_ingest.py update-index --pages wiki/topics/<slug>.md` で index.md に登録する
3. `python scripts/wiki_ingest.py update-hot --pages wiki/topics/<slug>.md` で hot.md を更新する

> ユーザーが「保存しなくていい」と言った場合はスキップする。

---

## ステップ 3: クエリを保存する

ステップ 2 でトピックページを保存した場合は、そのクエリも記録する。
`save-query` は **queries.md と log.md の両方**に記録する（Karpathy パターンでは log.md が ingests・queries・lint passes を追記管理する）。

```bash
python scripts/wiki_query.py save-query \
  --query "<ユーザーのクエリ文>" \
  --answer wiki/topics/<slug>.md \
  [--keywords キーワード1 キーワード2]
```

ページを保存しなかった場合でも、**探索に値する良い質問**だったと判断したときは
`--answer` なしでクエリだけを記録する:

```bash
python scripts/wiki_query.py save-query \
  --query "<ユーザーのクエリ文>" \
  [--keywords キーワード1 キーワード2]
```

> ユーザーが「保存しなくていい」と言った場合はスキップする。
