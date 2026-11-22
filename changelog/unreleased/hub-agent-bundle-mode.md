### change-watch: gitlab-issue-hub-agent に通知 bundle モードを追加

- `bundle-add` で同じリポジトリ・種別の通知を既定 10 分の窓で保留し、`bundle-flush` で期限切れの通知を 1 つのコメントにまとめる。
- 毎分の掃き出しとハブ issue への投稿を行う Dagu ワークフロー例を追加する。
