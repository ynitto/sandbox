# query — Wiki を検索する

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
- 新しい接続関係や洞察の発見
- 体系的なまとめ・概要

**保存の手順**:
1. 回答内容を `wiki/topics/<slug>.md` として保存する（既存topicsに統合できる場合は統合）
2. `python scripts/wiki_ingest.py update-index --pages wiki/topics/<slug>.md` で index.md に登録する
3. `python scripts/wiki_ingest.py update-hot --pages wiki/topics/<slug>.md` で hot.md を更新する

> ユーザーが「保存しなくていい」と言った場合はスキップする。
