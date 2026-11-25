### tools/goalie

- 目標管理の agent-dots と受け箱の copilot-app-jobs を統合し、`goalie.py job` で runner を呼び出せるようにした。旧スクリプトのパスも互換 shim から利用できる。
- 既存の目標の `.dot/` を初回の書き込み時に `.goalie/` へ移行する。DAG 例と新規フックの名前を goalie に更新した。
