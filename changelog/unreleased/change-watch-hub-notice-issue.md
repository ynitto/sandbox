### change-watch: 知らせを新しい issue にしてメールで届ける例を足す

- gitlab-issue-hub-agent に、知らせを 1 件ずつ新しい issue にして、すぐ閉じる Dagu の例 `examples/change-watch-issue-hub-notice.yaml.example` を足す。ハブの issue にコメントを足す方法では通知メールに前のコメントまで入り、Teams に投稿できないことがあった。
- 新しい issue の JSON を作るコマンド `issue` と、プロジェクトの URL から API の URL を作るフィルタ `gitlab_project_api` を足す。
