# codd-agent のシナリオ

codd-agent を、サンプルのリポジトリで最初から最後まで通したときの記録。1 つのファイルが 1 つのサンプルで、
サンプルの要点・頼むこと・利用者の答え・期待を自然文で書く。手順やファイルの中身は固めない。

## 一覧

| ファイル | サンプル | 見るところ |
|---|---|---|
| [aws-orders.md](aws-orders.md) | AWS の注文サービス（画面・API・非同期ワーカー）と設計書 | 人の承認が要るファイル、改名、設計書の側からの変更、点検 |
| [backend-orders.md](backend-orders.md) | 注文 API（Python）と仕様書 | 改名で消した名前の残り、テストのキャッシュ、途中までの変更 |
| [pc-main-screen.md](pc-main-screen.md) | PC 版のメイン画面（TSX）と設計書 | よくある名前のローカル変数、途中のコミット |
| [web-hello.md](web-hello.md) | 小さな Web 画面と e2e のケース | 新しい名前のテスト、注記の無い e2e のケース |
| [monorepo-library.md](monorepo-library.md) | ソース・テスト・文書が 1 つのリポジトリにあるライブラリ（TS） | 同じリポジトリの文書を参照先にする、ファイルの削除、やめて戻す |
| [three-repos-guides.md](three-repos-guides.md) | 画面・サーバー（Go）・設計書の 3 つのリポジトリと手引き | 3 つにまたがる変更、手引きの検査と確かめごと、設計書の側からの変更 |
| [semantic-graph.md](semantic-graph.md) | 税の計算（Python）と設計書・運用メモ・e2e のケース | 文書を LLM で読んだグラフ、確か・要判断・参考の分け方、判断済みを出さない |

## 再生する

エージェントに頼む。

> tools/codd-agent/tests/scenarios/aws-orders.md のシナリオを再生して

再生するエージェントは次のようにする。

1. 作業用の一時フォルダに、「サンプル」の要点を満たすリポジトリを作り、このリポジトリの `tools/codd-agent/init.py` で codd を入れる
2. シナリオを順に、codd エージェントの役で回す（`.statemachine/codd/codd.py` を `show` → `rule --all` → `explore` → `draft` →
   計画を書く → `verify-plan` → `summary` → `decide` → 変える → `verify-apply` → `report` → `record`）。利用者に訊く場面では、
   シナリオに書いた「利用者の答え」を返す。終わったら両方のリポジトリでコミットして次へ
3. 「期待」と違ったところ（止まらないはずで止まった、止まるはずで通った、余計に回った、訊くはずで訊かなかった）を挙げる。
   ファイル名・行・文言の違いは気にしない

不具合を見つけて直したら、そのとき通した道をシナリオに書き足す。新しいサンプルを足すときも同じ形で書く。
