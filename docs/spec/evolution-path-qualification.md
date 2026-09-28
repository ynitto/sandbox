# Evolution Path Qualification（MVP）

## Scope

これはEvoPathBenchの移植でも新しいrunnerでもない。既存の`readout_eval.py`等が作るimmutableな
case ledgerを正典とし、persistent artifactのcheckpoint identity、process metric、pure A/B比較だけを
`tools/agent-tools/eval/evolution.py`が加える。最初の対象は`routing-question/team`である。

## Checkpoint contract

qualification resultは次を必須とする。

* `artifact_kind`, `artifact_id`
* 40桁のgit commit SHA（またはSkill等、git外artifactのcontent fingerprint）
* versioned `eval_suite` と archive内の `result_ref`
* fixed base model / tool configurationを表す`environment`

ブランチ名や`HEAD`はcheckpointにできない。比較時にartifact、suite、model/tool条件が異なれば
比較不能として拒否し、artifact差とenvironment差を混ぜない。Skillのcontent identityには既存
`artifactShare.js`と同様にmtimeでなく内容をhashする`content_fingerprint()`を利用できる。

## Team suite

prompt設計用`team-tuning-v1.json`と、評価専用`team-held-out-v1.json`は物理的に分離した。
正解は人が固定した`verify / compare / split / other`であり、LLMにoracleを付けさせない。
held-outは既存readout runnerの`RT5`として実行する。

```bash
python3 tools/agent-tools/eval/readout_eval.py --calibration --cases RT5 \
  --model <fixed-model> --repeat 1 --min-confidence 0.6 --output-dir <archive-run>
```

`other`は「1 AIで足りる」という正規のclassでありabstainではない。logprob/voteのconfidenceが
`route.min_confidence`未満、confidenceなし、またはtext fallbackだけをabstainとする。readonlyでは
runtime contractどおりteam question自体を作らず、fixtureの`readonly_contract`をoffline testする。
threshold sweepを観察してもconfigへ自動書き戻しはしない。

## Separate metrics

qualificationは一つのscoreへ畳まず次を保存する。

1. **current capability**: answered accuracy、abstention、confidence range/mean、class confusion、wrong/abstained IDs。
2. **retention**: previous accepted checkpointでPASSし、currentでもPASSした割合とregression IDs。
3. **generalization**: tuningから隔離されたheld-outのpass/totalとfailed IDs。
4. **adaptation**: explicit rule-change fixtureがあるときだけ測る。ない場合は`NOT_APPLICABLE`。

held-outは最低12件かつclassごとに最低3件を要求する。未達は`INSUFFICIENT_DATA`で、PASSには
読み替えない。pure comparatorは`IMPROVED / RETAINED / REGRESSION / MIXED /
INSUFFICIENT_DATA`をdeterministically返し、改善と回帰が同時なら`MIXED`にする。

## Skill connection and future boundary

Skill evaluatorは同じqualification JSON二つを共通comparatorへ渡し、previous accepted Skillで通った
held-out behaviorがrefine後も通るかをadvisory表示する。既存のstatic quality、trigger eval、runtime
Pass率、retry、metrics、promote/refineはそのままで、promotionを自動blockしない。

Memory/workflowへ広げる際も新runnerを作らず、既存evaluatorのcase resultを同じ4 outcomeへadapterで
写す。artifact固有なのはidentityとfixtureだけに留め、explicit rule-change fixtureなしにadaptationを
推測しない。
