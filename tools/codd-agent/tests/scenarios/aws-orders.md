# AWS の注文サービス

画面・API・非同期ワーカーを持つ注文サービスで、実装と設計書を別のリポジトリに置く。
2026-10-07 に初めて通し、見つけた不具合を直した（PR #920）。

## サンプル

作業用のフォルダに 2 つのリポジトリを並べて作る。どちらも `main` で始め、作ったものをコミットしてから codd を入れる。
中身は下の要点を満たせばよい（名前・行・文言は合わせなくてよい）。`__pycache__/` は `.gitignore` に入れる。

**shop-app（実装）** Python と JavaScript。

- `CLAUDE.md` — Lambda のハンドラは `handler(event, context)` にして業務の処理は外の関数に置く。状態の名前は
  `backend/shared/status.py` の定数だけを使う。単体と画面のテストを通す
- `backend/shared/status.py` — 状態の定数 `PENDING`・`PAID`・`NOTIFIED`・`FAILED` と、その並び `ALL`
- `backend/shared/store.py` — 注文の置き場所（本番は DynamoDB、テストはメモリの `MemoryStore`。`put`・`get`・`update_status`）
- `backend/api/orders.py` — 注文 API（API Gateway → Lambda）。`validate_order`（商品・数量 1〜10・メールアドレス）、
  `create_order`（保存して SQS の order-queue に積む）、`get_order`（`order_id`・`item_id`・`quantity`・`status` を返す）、`handler`
- `backend/worker/process_order.py` — 非同期ワーカー（SQS → Lambda）。`charge` で支払い、`notify` で
  `f"to={order['email']} order={order['order_id']} status={order['status']}"` のようなメールの文を返す。
  `PENDING` → `PAID` → `NOTIFIED`、支払いに失敗したら `FAILED`。2 回届いても 1 回だけ処理する
- `backend/legacy/v1_orders.py` — 旧 API の互換。`V1_STATUS = {status.PENDING: "pending", status.PAID: "paid", status.NOTIFIED: "done", …}` と `to_v1`
- `frontend/src/` — `api.js`（`createOrder` が `res.status` を読む）、`orderStatus.js`（`STATUS_LABELS` で状態を画面の言葉に）、
  `orderForm.js`（`MAX_QUANTITY` と、入力の誤りを返す `formErrors`）。`frontend/package.json` は `"type": "module"`
- `frontend/test/` — `api.test.js`・`orderForm.test.js`（node:test。`statusLabel("NOTIFIED")` などを確かめる）
- `infra/template.yaml` — SAM。API・ワーカーの関数、order-queue（`maxReceiveCount: 3`）、`order-dlq`、orders テーブル
- `tests/unit/` — `test_orders.py`・`test_process_order.py`（`status=PAID` を経て `NOTIFIED` になる）・`test_legacy.py`（`"NOTIFIED"` が `"done"` になる）
- `tests/scenarios/order_flow.feature` — 注文から知らせるまでのシナリオ（Gherkin）

**shop-docs（設計書）** Markdown だけ。

- `CLAUDE.md` — API は `docs/api/`、ワーカーは `docs/worker/`、画面は `docs/frontend/` に書く。要件は人の承認を得て変える
- `docs/requirements/orders.md` — 機能（商品と数量とメールアドレスで注文できる、数量は 1〜10、支払いのあとメールで知らせる）と
  非機能（失敗した注文は 3 回までやり直し、それでも失敗したら運用者が確かめられるところに残す）
- `docs/guidelines/api-guidelines.md` — 項目名は snake_case。誤りは `{"errors": [...]}` で、400・404
- `docs/architecture/aws.md` — 部品の表（API Gateway・Lambda・SQS の order-queue・`order-dlq`（3 回失敗で移す）・DynamoDB）
- `docs/api/orders.md` — POST /orders の項目の表（`item_id`・`quantity`・`email`）と GET /orders/{order_id} が返す項目
- `docs/worker/process-order.md` — 状態の移り変わりの表（`PENDING` → `PAID` → `NOTIFIED`）と、やり直し（`maxReceiveCount` 3 回）
- `docs/frontend/screens.md` — 注文フォームの入力と、状態ごとの表示（`PAID` は支払い済み、`NOTIFIED` は完了）

**codd を入れる**

```bash
python3 <sandbox>/tools/codd-agent/init.py shop-app --side impl --ref docs=../shop-docs --test "python3 -m unittest discover -s tests/unit -t ."
python3 <sandbox>/tools/codd-agent/init.py shop-docs --side design --ref app=../shop-app
```

- shop-app の `codd.json`: `test` を `{"単体": [python3 -m unittest …], "画面": [node --test frontend/test/api.test.js frontend/test/orderForm.test.js]}`、
  `protect` を `["backend/legacy/"]`
- shop-docs の `codd.json`: `protect` を `["docs/requirements/", "docs/guidelines/"]`

シナリオは順に流す（前のシナリオで変えてコミットした状態から続ける）。どれも、利用者に訊く場面ではシナリオに書いた答えを返す。

## 1. 備考を足す

**頼むこと**（shop-app で）: 注文に備考（`note`、任意、200 文字まで）を付けられるようにして。注文フォームで入れ、API で受け取り、
お知らせのメールに載せる。

**利用者の答え**: 要件の変更は承認する。注文の一覧に備考を出すのは今回やらない。

**期待**

- 計画の検査が、書式の見本にした設計書と、（計画が `createOrder` を変えるなら）名前で当たる `api.test.js` を「未判断」として書き足す。判断に書き換えれば通る（`api.test.js` は変更不要）
- 確認の要約に「人の承認が要るファイル」として `docs:docs/requirements/orders.md` が出る。OK の記録に承認したファイルが残る
- 変えるファイルは 11 前後でも 1 段で変える（10 と 1 に分けない）
- 変えたあとの検査が、備考を書いていないテスト（`res.status` を読むだけの `api.test.js`、旧 API の `test_legacy.py`）を
  「直していない響くテスト」にしない。`notify` の f 文字列を変数に移すように直すこと（引用符の組を取り違えやすい形）
- 計画の記録に、端末の絶対パスが残らない

## 2. 完了の状態名を変える

**頼むこと**（shop-app で）: 完了の状態名を `NOTIFIED` から `COMPLETED` に変えて。

**利用者の答え**: 旧 API（`backend/legacy/`）のキーを変えるのは承認する。v1 の綴り `done` は変えない。データの移行は今回やらない。

**期待**

- 計画に `status.COMPLETED` と書いても、まだ無いファイル `status.COMPLETE` と読まない（変えたあとに「変え残し」で止まらない）
- テストの変更案に `COMPLETED` と書けば、新しく足す `status.COMPLETED` を扱ったとみなす
- 旧 API が「人の承認が要るファイル」として確認に出て、承認すれば最後まで通る

## 3. やり直しの回数

**頼むこと**（shop-app で）: ワーカーのやり直しを 3 回から 5 回にして（`order-dlq` に落ちる注文が多い）。

**利用者の答え**: 要件（`docs/requirements/`）は変えない。変えずに今回やらないことへ回す。あとで要件を変えて止まったら、戻す（APPLY）。

**期待**

- 確認で要件の変更を NG にすると、計画から外して「今回やらないこと」へ移せば、要約から「人の承認が要るファイル」が消えて通る
- そのあと、変える段でエージェントが要件まで変え、`.codd/apply.md` の「計画との違い」に「追加」と書いても、変えたあとの検査が
  「人の承認が要るファイルを、承認を得ずに変えています」で止める。advise は訊かずにやり直す（AUTO）ではなく、利用者に訊く
- 要件を戻せば通る

## 4. 設計書から変える

**頼むこと**（shop-docs で）: GET /orders/{order_id} が備考も返すと仕様に書き、実装を合わせて。

**期待**

- 参照先の実装（`get_order`）とテストも、設計書の側の計画から変えられる
- 備考を使う側（ワーカー・画面）とそのテストは名前で当たって「未判断」になる。「関係なし」「変更不要」と書けば通る
- `show` の「人の承認が要るファイル」は `app:backend/legacy/` のように参照先の名前が付く
- 報告の「テスト」では、実装のテストに `app:` が付く

## 5. 点検

**頼むこと**: 両方のリポジトリで点検（lint）を回して。

**期待**: ここまで codd を通して入れた変更だけなので、壊れたパスも「codd を通らなかった変更」も出ない
（`__pycache__` がコミットされていると、それを拾う。サンプルの誤り）。

## 6. 支払いの状態名を変える

**頼むこと**（shop-app で）: 支払いを確定した状態名を `PAID` から `CHARGED` に変えて。

**エージェントの判断**（わざとこうする）: 計画の検査が旧 API を「未判断」として書き足しても、「変更不要: 旧 API の綴りは変えない」とする。

**利用者の答え**: 止まって訊かれたら、旧 API も直してよい（綴り `paid` は変えない）と答える（PLAN）。

**期待**

- 旧 API の「未判断」の行に「人の承認が要るファイル: 変えないと動かなくなるなら、直すと書いて確認で承認を得る」と添えてある
- 変えたあと、単体テストが旧 API（`status.PAID` が無い）で落ちる。検査は「変更の影響を受ける、人の承認が要るファイルがあります」を
  足し、advise は訊かずにやり直さず利用者に訊く。勧めは「変えた分は残して、計画を直す（PLAN）」（変え直しても旧 API は直せない）
- PLAN で旧 API を計画に挙げ直しても、前に「変更不要」とした判断（`CLAUDE.md` など）は、「従う手順」に名前が出てくるだけでも戻り、
  また「未判断」にならない
- 報告の「テスト」で、自分の変更案に挙げた `order_flow.feature` を「計画に無い」としない
- PLAN で旧 API を計画に挙げ直し、確認で承認すれば通る。計画の記録に PLAN と承認したファイルが残る

## 直していないもの

- 要件の文書 `requirements/orders.md` と単体テスト `test_orders.py` を、ファイル名が対だとして「響くテスト」に挙げる。
  仕様書とテストを対にしたい場面もあるのでそのまま（「変更不要」と書けば済む）
- 備考を足す前に保存した注文を GET すると `note` が無く落ちる（サンプルのアプリの誤りで、codd のものではない）
