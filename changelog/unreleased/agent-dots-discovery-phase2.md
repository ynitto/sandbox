### tools/agent-dots

- Discovery の Phase 2 で `auto_execute` の `readonly` と `small/local-write` を有効化し、権限を限定できる低リスクの提案を自動承認できるようにした。
- 意味検索の第1段階として、ローカル索引の TF-IDF コサイン類似度で評価資料に関連候補を追加し、類似候補を出典ごとに検証できる形でグループ化した。
