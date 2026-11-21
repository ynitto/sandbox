### change-watch: 知らせの issue を決めた日数で閉じる・消す例を足す

- gitlab-issue-hub-agent に、知らせを新しい issue にしてメールで届け、決めた日数（既定 7 日）がたった issue を閉じるか消す Dagu の例 `examples/change-watch-issue-hub-notice-expire.yaml.example` と、その設定の例 `examples/change-watch-notice.yaml.example` を足す。
- issue の期限が来たかを決めるコマンド `check-old-issue` を足す。
