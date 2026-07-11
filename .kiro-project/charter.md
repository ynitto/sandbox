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
# 自然言語 accept: はエージェントに決定的 verify へ合成させる仕様だが、本プロジェクトは
# エージェント不使用（executor: stub）で運用するため、最初から決定的シェルコマンドで書く。
- cd /Users/nitto/Workspace/sandbox && kiro-project --help >/dev/null 2>&1 && kiro-flow --help >/dev/null 2>&1
- test -f charter.md || test -d backlog || test -f journal.md || test -d needs || test -d archive

## repos
# 対象リポジトリ（任意）。owns を書くと書込先（ワークスペース）、書かなければ参照のみ。

## links
# 参考リンク・横展開先プロジェクト（任意）。
