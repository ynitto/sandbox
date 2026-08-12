# 評価結果

`run_suite.py` が run ごとにサブフォルダを作る場所。生成される通常の結果は Git 管理しない。

- `<timestamp>-<model>[-<label>]/manifest.json`: 比較条件と完了状態
- `<run>/<unit>/command.txt`: 正確な再実行コマンド
- `<run>/<unit>/console.log`: 標準出力・標準エラー
- `<run>/worker|judge/ledger.jsonl`: 1 試行 1 行の結果
- `<run>/retrieval/metrics.json`: arm・問い合わせ形式別の指標
- `<run>/coverage/coverage.json`: 呼び出し面ごとの測定有無（未測定も隠さない）
- `archive/`: 結論の根拠としてリポジトリに保存した過去の台帳

比較時はモデル以外の manifest 条件を揃える。ハーネス調整では逆にモデルを固定し、変更する
条件を 1 つにする。
