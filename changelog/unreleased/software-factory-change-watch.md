### software-factory: GitLab への問い合わせを change-watch にまとめられるようにする

- `factory.json` に `change_watch` を書くと、Issue と MR の一覧は change-watch の取得元から読み、CI を待つ MR だけを `ci_max_age_sec`（既定 240 秒）ごとに取り直す。ラベルの付け替えと MR の作成は応答を一覧に反映し、ラベルは付け外しだけを送る。
- 取得元の例 `change-watch.example.yaml` と、本物の change-watch を偽の GitLab につなぐ結合テストを足す。`gitlab_url` で直接つなぐ構成もそのまま使える。
