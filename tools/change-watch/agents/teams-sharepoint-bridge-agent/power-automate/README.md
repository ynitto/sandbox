# Teams と SharePoint をつなぐ Power Automate レシピ

> Power Automate の実機では未確認。動的コンテンツの名前、出力の構造、式で使うアクション名は自分の環境で確かめる。

| フロー | 動作 | ファイル |
|---|---|---|
| 往路 | 許可された Teams の新規投稿から SharePoint に依頼 JSON を作る | [組み方](request/flow.md)・[式](request/expressions.md) |
| 復路 | GitLab の通知メールから依頼を引き直し、Teams の元の投稿に返信する | [組み方](reply/flow.md)・[式](reply/expressions.md) |

1. SharePoint の依頼フォルダを用意し、Dagu が動く PC に同期する。[エージェントの README](../README.md) に従って同期先と宛先を設定する。
2. 往路を作る。許可したテスト投稿から `<request_id>.json` が 1 件でき、同期先でエージェントが読めることを確かめる。
3. 同じ依頼フォルダを読む復路を作る。GitLab の issue 作成通知とコミット通知で、相関マーカーと結果リンクが届き、元の投稿に返信できることを確かめる。少なくとも返信が済むまで依頼ファイルを残す。
