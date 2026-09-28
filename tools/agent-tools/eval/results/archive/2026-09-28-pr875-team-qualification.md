# PR #875 `team` routing qualification

## Checkpoint and execution state

* artifact: `routing-question/team`
* checkpoint: `39e241d5d53305e2449cd192fedc85732540945b`
* suite: `routing.team.held-out.v1`（16 cases、各class 4）
* result: **INSUFFICIENT_DATA / NOT_MEASURED**

このcheckoutにはOllama executableがなく、real `readout_eval --calibration --cases RT5`を実行できない。
fake runを実測値としてarchiveすることもしない。したがってPR説明にあるcalibration gapを数字で
覆わず、次の欄を明示的にno-dataとする。

| metric | result |
|---|---|
| accuracy | INSUFFICIENT_DATA |
| confidence buckets | n=0（全bucket） |
| confusion by verify/compare/split/other | n=0 |
| abstention | n=0 / rate unavailable |
| regression / weak cells | comparison baselineなし / unavailable |
| adaptation | NOT_APPLICABLE（rule-change fixtureなし） |

## Review conclusion

Runtimeの`team` question、readonly skip、`other`とlow-confidence abstainの区別はoffline testsで固定した。
しかしmodel実測がないため、既存`route.min_confidence`の妥当性について変更提案は行わない。
thresholdの自動変更・config書き戻しも行わない。固定したmodel/tool条件で上記commandを実行し、
このcheckpointのreal ledgerをarchiveしてから、次checkpointとのretentionを判定する。
