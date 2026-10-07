# 注文 API（Python）と仕様書

画面の無いバックエンドで、関数の改名と項目の追加を流す。2026-10-05 に通し、見つけた不具合を直した（PR #911）。

## サンプル

作業用のフォルダに 2 つのリポジトリを並べて作る。どちらも `main` で始め、作ったものをコミットしてから codd を入れる。
実装に `.gitignore` は置かない（`init.py` が書く `.codd/` だけのもののまま。キャッシュの扱いを見るため）。

**api（実装）**

- `orders/models.py` — データクラス `Item`（`sku`・`price`・`qty`）と `Order`（`id`・`items`・`status = "new"`）
- `orders/service.py` — `TAX_RATE = 10`、`calc_total(order)`（小計に税を足す）、`cancel(order)`（発送済みは取り消せない）
- `orders/routes.py` — `GET /orders/{id}/total` が `calc_total` を、`POST /orders/{id}/cancel` が `cancel` を呼ぶ
- `users/service.py` — `get`・`update`（`status` を変える）
- `db/migrations/001_orders.sql` — orders テーブル（`id`・`status`）
- `tests/__init__.py`（空）・`tests/test_service.py`（`calc_total` が 220、`cancel`）・`tests/test_users.py`

**spec（設計書）**

- `docs/api/orders.md` — 見出しは概要・エンドポイント・状態。「`GET /orders/{id}/total` — 合計（税込み）を返す。`calc_total` で計算する」
- `docs/api/users.md` — 同じ見出しで、`get`・`update`
- `docs/db/schema.md` — orders の列の表

**codd を入れる**: api は `--side impl --ref ../spec`、`test` は `python3 -m unittest discover -s tests -t .`。spec は `--side design --ref ../api`。

シナリオはそれぞれ作り直したサンプルで流す。

## 1. 割引を足して改名する

**頼むこと**（api で）: 注文に割引（`discount`、円）を足し、合計は割引を引いてから税を掛けて。`calc_total` は `compute_total` に改めて。
マイグレーション `002_discount.sql` も書く。

**期待**

- 計画の検査が、書式の見本 `spec:docs/db/schema.md` を「未判断」に足し、新しく足す `discount` のテストが無いと止める。直せば通る
- 変えたあと、codd がテストを動かして出来た `__pycache__` の `.pyc` を「響くテスト」に数えない。何度 verify-apply しても通る
- 計画の `discount: int = 0` のようなコードの断片は、先頭の名前（`discount`）として扱う。テストの変更案に `discount` と書けば足りる
- 自分の変更案があれば、参照先の変更案があっても影響範囲を「なし」と書いてよい（測って「未判断」として書き足す）
- エージェントが手でテストを動かしたときのキャッシュは「計画に無いファイル」で止まる（`.gitignore` が無いため。直していない）

## 2. 状態を変える関数を足す

**頼むこと**（api で）: 注文の状態を変える `Order.set_status` を足し、`cancel` から使って。

**期待**: 計画の検査が「未判断」を書き足し、判断を書けば通る。参照先を変えずに最後まで通る。

## 3. 割引を途中まで変える

**頼むこと**: 1 と同じ。ただし変える段で、計画に挙げたファイルの一部（コードとテスト）を変えずに verify-apply する。

**期待**: 同じファイルを 2 つの指摘に重ねて挙げない（テストの変更案のテストは「テストをまだ変えていません」だけに出る）。
advise は AUTO APPLY。残りを変えれば通る。

## 4. 仕様書を直さずに改名する

**頼むこと**（api で）: `calc_total` を `compute_total` に改めて（中身は変えない）。

**エージェントの判断**（わざとこうする）: 参照先の変更案は「なし」にして、仕様書を直さない。

**利用者の答え**: 仕様書の `calc_total` は同じ関数なので、直すことを計画に足す（PLAN）。

**期待**

- 変えたあとの検査が「消した名前を、計画で変えないファイルがまだ書いています: `calc_total` — docs/api/orders.md:9」で止める。
  変える段だけでは直せない（直すと計画に無い変更になる）ので、advise は訊かずにやり直さず利用者に訊く。勧めは計画を直す（PLAN）
- 仕様書のその行を計画の前提の根拠に挙げているので、「関係なし」と書いても済まない
- 計画に仕様書の変更を足して確認で OK すれば、仕様書を直して通る

## 直していないもの

- 中身が空の `tests/__init__.py` もテストのファイルに数える（害は出ていない）
- エージェントが一部だけ import して出来た `.pyc` は、codd がテストを動かしても作り直されないので「計画に無いファイル」で止まる（消せば済む）
