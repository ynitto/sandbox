# モデル選択の判断材料・表現の実験（2026-09-21）

まず [実装結果](implemented-independent.md) を読む。現行の動作は [agent-herd の select 仕様](../../specs/agent-herd-spec.md#57-select) を参照する。

元依頼「agent-appの追加機能を提案してほしい」は本番経路の検証で Claude が選ばれた。候補順を逆転しても6件の結果は一致したが、期待する local/cloud との一致は各順4/6。不具合調査・分散設計には Ollama が選ばれ、難しい依頼全般への対応は未解決。

全てローカル judge の検証で、本家 Jev の評価でも選択先の作業品質評価でもない。スコアは校正された成功率ではない。

## 記録の読み順

| 段階・採否 | 報告 | 入力・スクリプト・生データ |
|---|---|---|
| 変更前の傾向調査 | [初期調査](report.md) | [probe.py](probe.py)、[results.jsonl](results.jsonl)、[contrast.py](contrast.py)、[contrast.jsonl](contrast.jsonl) |
| 表現を変えた多肢選択。逆順で悪化し不採用 | [調整結果](tuning-report.md)、[独立検証](assessed-validation.md) | [tune.py](tune.py)、[tune.jsonl](tune.jsonl)、[validate_assessed.py](validate_assessed.py)、[通常順](assessed-validation-holdout-normal.jsonl)、[逆順](assessed-validation-holdout-reverse.jsonl)、[開発例逆順](assessed-validation-development-reverse.jsonl)、[採用段階を含む集計](assessed-validation-summary.json) |
| 証拠の扱いを見直し | [実装前レビュー](evidence-review.md) | 対応状況は実装結果を参照 |
| 候補ごとの独立適合評価を試作 | [固定 holdout 評価](independent-holdout-report.md) | [independent.py](independent.py)、[開発結果](independent.jsonl)、[holdout スクリプト](independent-holdout.py)、[入力](independent-holdout-input.json)、[結果](independent-holdout.jsonl) |
| 本番へ実装・検証 | [実装結果](implemented-independent.md)、[本番経路の検証](integration-validation.md) | [integration-validation.py](integration-validation.py)、[結果](integration-validation.jsonl) |
| 回転平均で多肢選択を再試験。再び不採用 | [再試験](choice-retry-report.md) | [choice-retry.py](choice-retry.py)、[choice-state.py](choice-state.py)、[結果](choice-retry.jsonl) |

`independent.py` の開発実験は同等幅0.05、固定 holdout と本番は0.01。異なる条件の結果を混ぜない。holdout 入力は assessed 検証と同じ6件であり、独立適合方式だけに対する完全な未見評価とは扱わない。

## 再実行

リポジトリ直下から実行する。Ollama と `gemma4:e4b` が利用可能な環境が必要。実行すると判定モデルを呼ぶが、選択された CLI の作業は起動しない。

```sh
rtk proxy .venv/bin/python docs/experiments/2026-09-21-selection/integration-validation.py
rtk proxy .venv/bin/python docs/experiments/2026-09-21-selection/independent-holdout.py
rtk proxy .venv/bin/python docs/experiments/2026-09-21-selection/validate_assessed.py
```

全スクリプトの通常実行は [experiment_output.py](experiment_output.py) を使い、毎回新しい `reruns/run-*` に出力する。保存済みの実測ファイルには追記・上書きしない。再実行結果と Python キャッシュは Git 管理対象外。採用する記録だけ条件・報告とともに別途保存する。

スクリプトは現在の agentcore と候補定義を読み込む。過去と同一の結果は保証されない。過去の入力 state・質問が保存されている JSONL を比較の根拠とする。再測定を記録するときはコードの版、モデル、候補定義、閾値、キャッシュの条件も記す。

## 今回の整理

不採用案の一時パッチ、判定器に到達しなかった環境失敗の詳細ログ、本文と重複する本番検証の小集計、Python キャッシュを削除した。採否を左右した実測結果は残した。assessed の `/tmp` 入力依存は、保存済みの実測6件と一致する holdout 入力に置き換えた。
