### change-watch: GitLab Issue の原因調査エージェントを追加

- タイトルが一致する新規 Issue を検出し、goalie 経由で Copilot アプリに調査を依頼する。
- 調査手順とコメント原案の作成を statemachine-use で定義し、調査結果をエージェントから Issue に投稿する。
