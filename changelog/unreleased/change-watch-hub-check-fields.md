### change-watch: 古い issue・コメントを片付ける例が長い本文で止まらないようにする

- gitlab-issue-hub-agent の `check-old-issue`・`check-old-note` に、受け取った変化の項目を 1 つずつ渡す `--kind`・`--state`・`--system`・`--writer`・`--created-at` を足す。
- 古い issue を閉じる例と古いコメントを消す例の `check` を、項目ごとに渡す形にする。説明・本文を丸ごと渡していたため、長い知らせで「command line is too long」になって止まっていた。
