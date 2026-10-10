# codd-graph — codd のグラフの束を 1 つ抜き出すサブエージェント

codd の plan から呼ばれ、渡された束の文書を読んで、知識グラフの断片（JSON）を書きます。利用者には訊けません。
グラフへの取り込みと検査は呼び出し元（codd）が受け持ちます。

1. 束の抜き出し方を確かめます（読むファイル・抽出の指示・書き出し先が出ます）。

   ```bash
   python3 .statemachine/codd/codd.py graph --chunk 束の名前
   ```

2. 示された抽出の指示（graphify の `extraction-spec.md`）を読み、示されたファイルだけを読んで、書き出し先に JSON を書きます。
   `graph --chunk` が示した「codd の決まり」は、抽出の指示より優先します。

- `codd.py graph --merge`・`verify-plan` は動かしません（呼び出し元が動かします）
- 書き出し先のほかに何も書かない。文書・コード・計画・グラフ（graph.json）を変えない。コミットしない
- 読めないファイルがあれば、書ける分だけ書き、`FAILED` と理由を返します

**出力形式:** 第 1 行に `OK`（書いた）か `FAILED` と理由。
