# Judge Calibration Gate — 実測と暫定適用判断

2026-09-20。ユーザーから実測・threshold推奨・本番適用の指示を受けた。
[raw ledger](../../tools/agent-tools/eval/results/archive/20260920-gemma4-e4b-calibration/ledger.jsonl)、
[report](../../tools/agent-tools/eval/results/archive/20260920-gemma4-e4b-calibration/report.json)、
[manifest](../../tools/agent-tools/eval/results/archive/20260920-gemma4-e4b-calibration/manifest.json)、
[暫定policy](../../tools/agent-tools/eval/results/archive/20260920-gemma4-e4b-calibration/policy.json)。

## 実測

現在のfixtureにはE4〜E6もあり、12セル×3回、計36セル実行／51問。
全問logprobs。transport / response failureは0、低coverage（<0.8）は0。
入力・問いのSHA-256を全行で実行後のfixtureと照合し、一致を確認した。
温度0の反復は独立標本ではなく、statusはinsufficient_dataのまま。

| 指標 | 結果 |
|---|---:|
| 問い単位 accuracy（threshold 0） | 42/51 = 82.35% |
| セル全体 accuracy（threshold 0） | 27/36 = 75% |
| Brier（全クラス平方誤差の和、0〜2） | 0.351062 |
| ECE（5 bucket） | 0.162747 |
| coverage p10 / p50 / p90 | 0.9969 / 0.9999 / 1.0 |
| セル呼び出しlatency p50 / p90 | 0.493秒 / 2.574秒 |
| APIで観測した入力 / 出力token | 14,502 / 102（欠損0） |

| confidence下限 | 採用問数 | answer rate | 問い単位accuracy |
|---|---:|---:|---:|
| 0.0 / 0.5 / 0.6 | 51 | 100% | 82.35% |
| 0.7 / 0.8 | 48 | 94.12% | 81.25% |
| 0.9 | 39 | 76.47% | 76.92% |

E3（出力段不足）は0.9508、E4（集計段不足）は0.9487、E5（収集段不足）は0.9905で
3回とも誤答した。E6（全段あり）は0.9913で正解。全体thresholdを上げても不正解の排除より
正解の棄権が先に起こる。E5を除くためだけに0.991等を選ぶのはこのfixtureへの過適合になる。
**自動完了・quality評価をconfidenceだけで有効化できる根拠は得られていない。**

## 暫定の運用判断

| 用途 | 判断 | 根拠・制限 |
|---|---|---|
| filter | min confidence 0.6 | F1の最小0.6272。3/3セル、18/18問正解。0.7では正解セルを全て棄権。1 fixtureのみで、実際の複数deps入力のend-to-end性能は未確定 |
| route | min confidence 0.8 | RO1〜3が9/9正解。otherの0.8186を残す。otherは「書込先を決めない」有効回答で、自動実行を意味しない |
| assess / statemachine transition | judge採用保留 | 当該用途のラベル付き実測なし。既存の生成経路へfallbackするため、ワークフロー全体の停止や人手承認を追加するものではない |
| PR #862 自動quality評価 | sample + evidence-shadowで比較・記録 | 後続の根拠別評価を利用。完了・routeには使わない。E1〜6をquality score／issue choiceの校正結果と読み替えない |

filter/routeも精度保証ではなく、ユーザーの適用指示に基づく暫定設定。
method=logprobs、model=gemma4:e4bだけに適用し、異なるmethod/modelを採用しない。
coverage 0.8は既存設計の診断下限を維持したもので、今回低coverage標本がないため最適化値ではない。
J2/CL1は各3/3だが、新しい自動化consumerは追加しない。
段0のused attributionは継続できる。report生成処理は今後も設定を自動変更しない。

## 設定の契約

`agent-herd config set judge.calibration <JSON>`で人が承認したpolicyだけを保存する。
model・method・min_coverage・thresholdsを必須とし、用途のnullまたは省略は採用保留。
設定不正・未測定model/method・低coverageも保留する。consumerが既に指定したthresholdを
緩めない。設定がない場合は従来どおり。`judge.model`のauto/pinned/offは独立して維持する。

```bash
agent-herd config set judge.calibration '{"model":"gemma4:e4b","method":"logprobs","min_coverage":0.8,"thresholds":{"filter":0.6,"route":0.8,"assess":null,"transition":null}}'
agent-herd config --json
# policy適用を取り消す（従来のconsumerへ戻る）
agent-herd config unset judge.calibration
```

これはfilter/route/assess/transitionの採用gateであり、standalone `agent-herd judge`の
計測APIや明示的な`--min-confidence`、他のstatemachine補助判断（契約の語補完・check triage）を
置き換えない。生成fallbackの正当性も本実測では保証していない。

## 適用状態

2026-09-20 09:29 JST、ユーザーの追加明示指示を受け、自動承認レビューを通して本番適用済み。
agent-herd / agent-flow / agent-project / agent-loopを更新し、agent-aider / agent-ollamaの
aliasも新しいagent-herdへ接続した。既存agent-auditの根拠別評価対応は維持した。
インストール済みとの差分を許可リストと照合し、stagingのコードがリポジトリと一致することを確認した。

judge.modelはautoのまま、policyはgemma4:e4b/logprobsに限定する。
filter=0.6、route=0.8、min_coverage=0.8、assess/transition=nullを設定し読み戻した。
consumer自身の既存thresholdがこれより厳しい場合は既存値を維持する。
CLI更新は次回CLI起動から有効。既に読み込まれた実行中プロセスは停止していない。

App設定はsample + evidence-shadowへ復元し、以前のoff化案は適用しなかった。
前回の設定保存後にstrategyが消えていたことを今回観測した。原因は未確定。
新しいApp処理は次回起動から有効なので、起動後に設定画面の比較・記録方式も確認する。
この適用は小標本での暫定運用であり、自動承認精度の保証や品質評価の自動採否への昇格ではない。

[deployment receipt](../../tools/agent-tools/eval/results/archive/20260920-gemma4-e4b-calibration/deployment.json)
に日時・実行ファイルhash・バックアップ先・設定を保存した。
バックアップは`/Users/nitto/.agents/calibration-backups/20260920T002955Z`。
既存judgeテスト30件と、4つのインストール済みCLIのhelp起動・実設定を使うgate検証が成功した。
gate検証にはthreshold境界、低coverage、text、別model、assess/transitionの保留を含む。
