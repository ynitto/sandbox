## ソースを単位に分ける

前回の確認での利用者の指摘（あれば、それを踏まえて分け直す）: {{answer}}

分母（`extraction/inventory.tsv` のテキストのファイル）を、**1 つ残らず**どれかの単位に入れる。
数え上げを目で追って漏れを探さず、書いたら検査に任せる（漏れたファイルは検査が挙げる）。

`extraction/units.txt` に次の形で書く。単位の行は行頭から、パターンは字下げする。

```text
# 並び順がそのまま書く順になる。土台（データ・共通部品）から先に並べる
unit customer-table data 顧客テーブル
  db/schema/customer.sql
unit orders module 受注の登録と変更
  src/orders/**
  src/common/order_util.py
unit order-api api 受注の REST API
  src/api/orders_controller.py
unit vendor out-of-scope 同梱した外部ライブラリ（仕様の対象にしない）
  vendor/**
```

- id は小文字・数字・ハイフン。種類は {{categories}}
- パターンはソースのフォルダからの相対。`*` は 1 階層、`**` は何階層でも、末尾 `/` はその下すべて
- 1 単位は {{max_lines}} 行・{{max_files}} ファイルまで（1 つの会話で読み切れる大きさ）。
  1 ファイルだけで超えるなら、そのファイルを 1 単位にする
- 1 つのファイルを複数の単位に入れてよい（画面とその API を別の単位にする、など）。対象外と対象の両方には入れない
- 単位を決めるために読むのは、フォルダ名・ファイル名と、必要なら各ファイルの先頭だけ。中身の調べは次の段で行う

終えたら `OK`（検査が分母の使い切りと大きさを確かめる）。
