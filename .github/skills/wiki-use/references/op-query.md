# query — Wiki を検索する

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
