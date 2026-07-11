# Charter: sandbox

## goal
- バックログベースの開発が出来ること
- agentによる自律開発が出来ること

## constraints
- kiro-project/kiro-flowをエンジンとして使用する
- kiro-projects-viewerをフロントエンドとして使用する

## assumptions
- 人による操作や確認を最小限にする
- 繰り返しややり直しによって試行錯誤できる

## deliverables
- kiro-project/kiro-flow ソースコード
- kiro-projects-viewer ソースコード
- 設計書・ドキュメント

## acceptance
# 各行＝終了コード0をPASSとみなすシェルコマンド。書けない条件は `- accept: <自然文>` でも可。
# acceptance を書けないプロジェクトは done 判定不能 → 必ず人へ回る。
- accept: このワークスペースで kiro-project/kiro-flow が動作する
- accept: このワークスペースのプロジェクトを kiro-projects-viewer で監視できる

## repos
# 対象リポジトリ（任意）。owns を書くと書込先（ワークスペース）、書かなければ参照のみ。

## links
# 参考リンク・横展開先プロジェクト（任意）。
