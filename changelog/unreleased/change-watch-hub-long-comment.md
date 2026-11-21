### change-watch: ハブの issue に長いコメント・説明を書けるようにする

- gitlab-issue-hub-agent の例のワークフローが、コメント・説明の JSON をコマンド行に載せず、ファイルに書いて送るようにする。長い文で「command line is too long」になって止まっていた。
- `note`・`description`・`fields` に、JSON をファイルに書く `--out` を足す。コメントを足す例に、本文をファイルで渡す `COMMENT_FILE` を足す。
