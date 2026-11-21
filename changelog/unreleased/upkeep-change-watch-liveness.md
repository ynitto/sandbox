### upkeep-health-check: change-watch の死活監視を追加

- change-watch の状態 DB（`state.db` / `state.db-wal`）の更新時刻で処理の鮮度を判定する `change_watch` チェックを追加し、`ok` / `stale` / `unknown` を既存の JSON 出力に返す。PC や Dagu が止まって change-watch の書き込みが途絶えた場合に検出できる。
