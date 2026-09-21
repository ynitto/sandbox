# Selector Long-Horizon Qualification

現在の selector が選んだ **agent CLI harness + model** で、実タスクがどこまで完了したかを比較する eval。production の router、config、success 判定は変更しない。Android Bench は依存にしない。

## 調査した main と不足点

2026-09-21 に fetch した `origin/main` と作業開始時の HEAD はともに
`c6a96540c5771e557f5b252c196bc2ab53a4bea1`。既存の agent-app の未コミット変更は本作業の対象外。
`.codegraph/` は無いため既存コードを直接調査した。

| 対象 | 正典 / 既存機能 | 本 eval での扱い |
|---|---|---|
| `agentcore.modelselect.select` | Jev → judge → audit、confidence門、context/quota/budget prefilter | 同じ関数へ固定stage応答を入力して評価 |
| `agent-herd select` | purpose、ratings JSON、workload、min-confidence、stage指定 | CLI独自のrankは無い。Python正典を使う |
| `executionresolver.resolve_execution` | workload/purpose policy、availability、retry、park、適格候補内のselector | fixtureの任意 `resolver` snapshotを正典へ渡す |
| `modelselect.audit_order` | PASS率→平均tokens→policy rank→relative_cost→宣言順 | audit、高格付け、最安、oracleのtie-breakで再利用 |
| `agent-audit ratings --json` | 用途別PASS率、平均tokens、outcome件数、rank | `ratings`を入力snapshotとして保存。実行結果で上書きしない |
| `verifycontract` | plan digest、receipt採否、全体verdict、固定command実行 | `plan_errors` / `receipt_errors` / `receipt_overall`を正典にする |
| `eval/engine.py` | 本番への共通呼び出し層、headless CLI組立、process-group timeout | `selection_runtime()`のみ追加。mode付きrunnerではないため独立した小さなevalを置く |
| `eval/eval_io.py` | 一意なrun directory、JSON保存 | `new_run_dir` / `write_json`を再利用 |
| `candidate_eval.py` | ファイル・パターン等の候補**生成**を評価 | agent/model選択のoutcome比較ではない |
| `readout_eval.py` | judgeの回答・確度・較正を評価 | 選択先での実タスク完了を扱わない |
| `worker_eval.py` / `project_verify_eval.py` | worker課題、verifyの評価 | CLI起動・usage marker parserを再利用。selector横断比較は無かった |

`eval/` のコード、data/results、既存selector tests、設計・specを調査した範囲で、selector-selectedと同一候補集合の反実仮想outcomeを比較する既存end-to-end evalは無かった。

### selector が既に出している観測値

- `selected`（agent_cli/model）、`stage`、`confidence`、`probabilities`、`reason`
- `candidates`、`dropped`（id、quota/context/budget理由）、`state`（prompt profile、候補特性、rating、quota、budget）
- `attempts`（stage、outcome、choice、confidence、method、model。errorの場合detail）
- `usage.tokens_in/tokens_out`（全体合計。ただし一部の欠測は0へ正規化される）
- resolver decisionとreceiptにはstage/confidence/reason/droppedが残るが、下位stageの未実行応答やstage別usage/wallは残らない。

このためevalだけに `selector_observations` を持つ。runtimeへのfield追加も不要だった。

## Offline

リポジトリrootから（`python3` はpytest/PyYAML等、既存eval依存を持つ環境を使う）:

```sh
.venv/bin/python tools/agent-tools/eval/model_selection_eval.py --selfcheck
.venv/bin/python tools/agent-tools/eval/model_selection_eval.py
# 任意の実測outcome tableを再集計
.venv/bin/python tools/agent-tools/eval/model_selection_eval.py --fixtures /path/to/measured-fixtures.json
```

結果は既存IO規約で `results/model-selection/<UTC>-selector-offline/report.json` に保存する。
既定catalogの全outcome・stage応答は **synthetic**。これは評価計算の動作確認であり、実モデルの性能や0.6の妥当性を示さない。syntheticとmeasuredの同一reportへの混載は拒否する。

### Fixture format

`data/model-selection/fixtures.json` が実例。各fixtureは以下を固定する。

- `id`、`prompt`、`purpose`、`workload`、`horizon`、`provenance`（synthetic / measured）
- `candidates`: 明示したagent_cli/model、relative_cost、context_tokens、rank、rating等のsnapshot。offlineではローカル定義やユーザーconfigを読まない。
- `ratings`: agent-auditのrowsまたは `{rows: [...]}`。候補固有ratingがあれば既存規則どおりそちらが優先。
- `quotas`、`budget`: 観測時のsnapshot。空のquotaは空として扱い、台帳を読まない。
- 任意 `resolver`: `resolve_execution` のkwargs（compiled_control、budget_state、unavailable、attempt_counts等）。workload/purposeはfixtureから渡す。nowはtimezone付きISO文字列で固定できる。policyの候補メタデータとfixture candidatesは同じsnapshotを用意する。実行時clockを推測しない。
- `verification_plan`: digest付き既存plan。`outcomes[candidate_id]` は独立した `result_rev` と既存 `receipt` を持つ。
- `checkpoints`: `{id, command_index, weight, label}`。indexはplan.commandsを指す。重複index、不正weight、範囲外indexは拒否。
- outcomeの `tokens` / `cost` / `currency` / `wall_seconds` は観測できた場合だけ数値。unknownはnull。
- `selector_observations[jev|judge]`: `status: answer` と正規化済み `answer`（choice/confidence/method等）、独立した実測 `tokens` / `wall_seconds`。API失敗は`error`、未設定は`not-configured`、judge不在は`not-available`、未観測は省略または`unobserved`。

既存receiptを取り込む際はplanと期待result_revも取得する。receiptの自称verdict、agentの「完了しました」、CLIのexit=0だけをPASSへ変換しない。plan digest / revision / command員数が不一致なら `invalid-receipt` として分ける。

### 比較と分母

比較する5腕は selector-selected / audit-fallback / cheapest-eligible / highest-rated-eligible / fixture-oracle。
高格付けbaselineとauditは同じ正典順位なので同じ候補になる。最安はrating/rankを除いた候補を同じ `audit_order` に渡し、relative_costと宣言順で決める。relative_costを金額へ換算しない。

`objective` は `verified-pass_then_audit-usage-policy_else_completion` を明示する。

1. verified PASSを優先。
2. PASS同士は、既存auditのusage/rank/relative_cost順を使う。ratingのPASS率を同じ値にし、average_tokensには実測tokensを入れる。unknownは正典どおり既知値の後ろ。
3. PASS無しならcheckpoint completionの高い順。tieは同じaudit順。

全適格候補の比較可能なoutcomeが無いとoracleはunknown。regretは単一utilityへ圧縮せず、`missed_pass`、`completion_gap`、双方PASS時だけの`extra_tokens_when_both_pass`、`oracle_match`を残す。oracleはfixtureとこの明示objectiveの範囲だけでの比較であり、普遍的な最適モデルを意味しない。

completionは成功した固定checkpointのweight合計 / 全weight合計。flakyは未達。環境要因でcheckpointを観測できなければnull。全体PASSはcompletionと独立に、既存receipt正典で決まる。production successには一切使わない。

reportは腕別、stage別（jev/judge/audit）、confidence別（0.6–0.7 / 0.7–0.8 / 0.8–0.9 / 0.9+）、horizon別とthreshold sweepを含む。confidence無しのauditはunknown bucket、0.5台はbelow-0.6。PASS率の分母はvalid receiptでpass/failが確定した件数 `verified_n`。n、status件数、known/unknown件数も必ず併記する。CLI/API failure、inconclusive、abstain、no eligible、未観測を誤答へ加算しない。API failureのあとfallbackした場合は、fallbackで選んだcandidateの成果を採点し、stage失敗はselector_events/attemptsに残す。

各groupのtokens・wall・completionはknown値だけのmean/known_sumとunknown_nを出す。runtimeのmodule hashもreportに保存する。tokens/cost/wallはcandidateの実行（wallは最終検証を含む）、selector_tokens/selector_wall_secondsは選択のoverhead、total_tokens/total_wall_secondsは両方既知の場合だけ。costはcurrency別に集計し、異なる通貨を合算しない。selector_costは未計測ならnull。real-runはtransport応答のtoken fieldを正規化前に観測し、明示された0と欠測を区別する。既存selectorの0に正規化済みusageしか無い場合は実測0と断定せず、raw answerに保持して集計値はunknownにする。

### Threshold sweep

0.5 / 0.6 / 0.7 / 0.8 / 0.9 を **eval内の引数だけ** で渡す。同じ固定stage応答を現行selectへ再入力し、Jev/judge採用率、audit fallback率、PASS、completion、usageを比較する。候補1件のaudit選択はfallback率には含めず、audit stage率には含む。

下位stageが未観測なら、その応答が必要になったthresholdのrowは`unobserved-stage`。本番receiptだけから未実行judgeの結果を捏造しない。real-runは0.9で1回観測するので、同じ応答による0.5〜0.9の再生に必要なstageを取得できる。selector APIをthresholdごとに再実行しない。

全候補をprefilterが落とした際、本番selectには全候補へ戻す挙動がある。raw selectionを残したうえでevalは`no-eligible-candidate`へ分離する。本番動作は変更しない。

## Opt-in real run

**このコマンドだけがremote selector / Agent CLIを実行し、実際の利用枠を消費する。** 通常offline実行とunit testsはネットワーク不要。

1. 使用するAgent CLI、認証、model、Jev/judgeの接続設定を既存の方法で用意する。
2. Pythonは既存eval/test依存（pytest、PyYAML等）を用意する。Node課題M3/L3はNode/npmも必要。adapterは隔離workspaceでlockfileに対して `npm ci --ignore-scripts` を実行する。失敗はenvironment-errorとなる。
3. 実在するCLI/modelを2件以上書いたJSONファイルを作る。普段の候補定義のmetadataや、事前のratings snapshotも含められる。料金はadapterでは推定しない。
4. 小さな課題から明示して実行する。

```sh
.venv/bin/python tools/agent-tools/eval/model_selection_eval.py \
  --real-run --case S1 --candidates /path/to/candidates.json --timeout 1800
# L2だけを最大1時間/呼び出しで実行する例
.venv/bin/python tools/agent-tools/eval/model_selection_eval.py \
  --real-run --case L2 --candidates /path/to/candidates.json --timeout 3600
```

candidate JSONの形（CLI/model名は自分の既存定義に合わせる）:

```json
[
  {"agent_cli": "YOUR_CLI_A", "model": "YOUR_MODEL_A"},
  {"agent_cli": "YOUR_CLI_B", "model": "YOUR_MODEL_B"}
]
```

adapterはfixtureの固定base revisionをcandidateごとに `git archive` から新しいrepoへ展開する。解答commitの履歴や現在の未コミット変更は渡さない。固定テストだけをverification revisionから重ね、開始時に未達であることを確認する。開始時PASSならinvalid-seed、環境チェック不成立ならenvironment-errorとして実行を抑止する。

起動は `engine.headless_cmd`、timeoutは `engine.run_process` のprocess-group制御。promptも開始状態も共通にし、agent自身のtool-loopで実装・確認・修正を行わせる。新しいorchestratorや独自repair loopは作らない。生のargv/stdout/stderr、harness、wall、usage markersを保存する。

終了後に固定検証ファイルを復元し、candidate結果を隔離repoへcommitしてresult_revを確定する。その成果に対して `verifycontract.run_plan_command` / `build_receipt` を使う。上書きされたテストで合格させないための復元であり、実行コードそのものの改竄耐性を保証するセキュリティ境界ではない。成果diffはreview可能なまま残す。

real adapterはcommand-only v1 plan、direct selector fixtureを対象とする。workset/integration付きreceiptとresolver snapshotはoffline tableとして取り込める。real-runで候補を差し替えたまま古いcompiled policyを使うことは拒否する。quota/context/budgetで除外された候補は起動しない。productionで全candidateを実行する経路は追加しない。

`measured-fixtures.json`、各candidateの`receipt.json` / `outcome.json`、`report.json`が成果物。毎candidate後にoutcome tableを保存する。CLI失敗やtimeoutでも最終receiptを保存するが、それらのrunは比較可能な成功/失敗率の分母から分離する。timeoutは各agent/準備/検証commandの上限で、課題全体の合計時間上限ではない。

## 課題catalogと限界

| ID | 区分 | sandboxの課題 |
|---|---|---|
| S1 | short | judgeの1ファイルbug fix |
| S2 | short | evalのファイル数判定bug + test |
| S3 | short | judge縮退表のdocs修正 |
| M1 | medium | assessのjudge/生成/heuristic共通化 + eval |
| M2 | medium | judge state/action併記 + schema/tests |
| M3 | medium | IPC backendを5ファイルへ分離 |
| L1 | long | CLI/core/resolver/flowを横断するselector追加 |
| L2 | long | coreとskillの2系統statemachineをjudgeへ移行 |
| L3 | long | audit/app/automationをまたぐweekly usage、複数検証系の反復 |

horizonは変更範囲・必要な検証工程による分類であり、数日〜1週間かかったという実測ではない。過去commitの再現課題なので学習データ混入や解答暗記の可能性もあり、この9件だけで一般的なlong-horizon能力を主張しない。実測時はmodel version、CLI version、実行日、machine条件、利用枠、繰り返し数を別途揃え、失敗内容と長時間runの分布も確認する。

9件すべてで固定開始revisionがFAIL、修正済みrevisionがPASSになることをローカルで確認した。これは**fixtureの検証**であり、candidateの実測ではない。Nodeのreferenceチェックには既存workspaceのdependency cacheをNODE_PATH経由で使った。通常real-runは各fixtureのlockfileから準備する。

現時点でreal candidate比較は実行していない。`select.min_confidence=0.6`もstage順も改善を裏付けるデータはまだ無く、変更・自動適用はしない。
