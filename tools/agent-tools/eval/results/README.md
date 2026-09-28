# 評価結果

`run_suite.py` が run ごとにサブフォルダを作る場所。生成される通常の結果は Git 管理しない。

- `<timestamp>-<model>[-<label>]/manifest.json`: 比較条件と完了状態
- `<run>/<unit>/command.txt`: 正確な再実行コマンド
- `<run>/<unit>/console.log`: 標準出力・標準エラー
- `<run>/worker|judge/ledger.jsonl`: 1 試行 1 行の結果
- `<run>/retrieval/metrics.json`: arm・問い合わせ形式別の指標
- `<run>/coverage/coverage.json`: 呼び出し面ごとの測定有無（未測定も隠さない）
- `archive/`: 結論の根拠としてリポジトリに保存した過去の台帳

Persistent artifactのqualificationをarchiveする場合は、manifestに`artifact_kind` / `artifact_id` /
immutable checkpoint / eval suite / result refを残す。`RT5` runは`checkpoint_identity`を自動で加える。
異なるmodel/tool条件のrunをartifact checkpoint比較へ混ぜない。

比較時はモデル以外の manifest 条件を揃える。ハーネス調整では逆にモデルを固定し、変更する
条件を 1 つにする。

Calibrationは`readout_eval.py --calibration`で`<run>/manifest.json`・`command.txt`・
`ledger.jsonl`・`report.json`を保存する。fake/replayも同じschemaでsourceを明記する。
根拠として残す場合はrun一式を`archive/<run>/`へ保存する（fakeは実測と混ぜない）。
