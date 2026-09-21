# Selector Long-Horizon Qualification — 初回実測

状態: 収集終了。9課題・18候補枠を記録し、17試行を実施（うち1件は中断）。残る1枠は月間quota不足で未実行。現在のselectorをlong-horizon workloadでqualifiedと判定できる結果ではない。

## 実測結果

開始時main `c6a9654` のselectorは9課題すべてでjudge経由でGemmaを選んだ。confidenceは0.9995〜0.9998だったが、評価可能な8件のverified PASSは0件、平均completionは41.7%。中断した1件は分母から除外した。

| 課題 | 区分 | Gemma + Ollama | Sonnet 4.5 + Kiro |
|---|---|---|---|
| S1 | short | FAIL / 0% | PASS / 100% |
| S2 | short | FAIL / 67% | PASS / 100% |
| S3 | short | FAIL / 67% | PASS / 100% |
| M1 | medium | FAIL / 67% | 除外: 隔離違反 |
| M2 | medium | 除外: 安全確認のため中断 | 除外: 隔離違反 |
| M3 | medium | FAIL / 33% | 除外: CLI/API障害 |
| L1 | long | FAIL / 33% | 除外: CLI/API障害 |
| L2 | long | FAIL / 33% | 除外: CLI/API障害 |
| L3 | long | FAIL / 33% | 未実行: 月間quota不足 |

百分率は固定checkpointの達成率であり、LLMの主観採点ではない。CLIエラー・隔離違反のraw receiptも保持しているが、通常のタスクFAILとして集計していない。Kiroの3 PASSはshortだけであり、9課題全体に対する100%成功を意味しない。

| 比較腕 | verified PASS / 評価可能件数 | completion平均 | 評価不能件数 |
|---|---:|---:|---:|
| audit-fallback | 0/8 | 41.7% | 1 |
| cheapest-eligible | 0/8 | 41.7% | 1 |
| fixture-oracle | 3/3 | 100.0% | 6 |
| highest-rated-eligible | 0/8 | 41.7% | 1 |
| selector-selected | 0/8 | 41.7% | 1 |

oracle比較が成立したのはshortの3件だけ。その3件では、selector・audit・最安・最高ratingの各腕はいずれもGemmaを選び、oracleのKiroがPASSした。selectorのmissed PASSは3/3、completion gap平均は0.556、oracle一致は0/3。残り6件のoracle/regretはunknown。

### stage・confidence・threshold

実選択stageはjudgeが9件、Jevとauditは0件。confidence bucketも9件すべて0.9+で、他bucketの性能は未測定。judge / 0.9+ groupのverified PASSは0/8、completion平均41.7%、candidate tokens平均28,305.75（8件既知・1件unknown）、candidate wall平均278.3秒（8件既知・1件unknown）。wallはcandidate実行と最終検証を含み、準備時間を含まない。

| threshold | Jev/judge採用率 | audit fallback率 | verified PASS | completion | candidate tokens既知合計 |
|---|---:|---:|---:|---:|---:|
| 0.5 | 100% | 0% | 0/8 | 41.7% | 226,446（unknown 1件） |
| 0.6 | 100% | 0% | 0/8 | 41.7% | 226,446（unknown 1件） |
| 0.7 | 100% | 0% | 0/8 | 41.7% | 226,446（unknown 1件） |
| 0.8 | 100% | 0% | 0/8 | 41.7% | 226,446（unknown 1件） |
| 0.9 | 100% | 0% | 0/8 | 41.7% | 226,446（unknown 1件） |

選択処理自体のoverheadは9件合計6,293 tokens・48.8秒。Kiroのtokensと全候補の実金額はunknown。missing usageを0に置き換えず、relative_costから金額へ換算していない。

### この実測から検討できること

- 0.5〜0.9のthreshold変更では候補が変わらなかった。0.6を書き換える根拠は得られていない。
- audit等のbaselineも同じ候補を選んだため、今回の表ではaudit優先へ変更しても改善しない。Jevの実測が無いため、stage順の優劣は判定できない。
- 次の比較では隔離条件を揃え、quotaが利用可能な状態でmedium/longのoutcome tableを埋める必要がある。用途・workload別のPASS実測ratingsとconfidenceの較正を検証する候補はあるが、自動適用していない。

固定runtimeでのoffline再集計と現mainの互換replayはいずれもAPI無しで完走し、5腕・stage/confidence・thresholdの集計値が一致した。これは凍結した応答の互換検証であり、変更後のmainがliveで同じ回答をすることの実測ではない。関連テストは322件＋4 subtestsが通過し、eval専用30件も通過した。

## 条件

- 2026-09-21 JST。9課題 × 2候補 × 各1回の枠で開始。short各候補600秒、medium1200秒、long1800秒の上限。L2 Kiroで月間上限が明示されたため、L3 Kiroは未実行・quota不足として記録する（17試行、1件未実行）。
- 候補: `ollama/gemma4:e4b`（ローカル、model digest `c6eb396dbd59`）と `kiro/claude-sonnet-4.5`（Kiro CLI 2.21.4）。model単体ではなく、このCLI harnessとの組を評価。
- ユーザーが外部Kiroへの公開repo/課題文の送信、Credit消費、隔離workspaceでのコード変更を明示承認してから実行。
- Jev未設定、judgeはauto。OllamaのGemmaがjudgeを担う。production min_confidenceは0.6のまま。
- 各課題の開始revisionと固定テストを両候補で揃え、順番はOllama→Kiro。並列実行しない。archive seedには元repoの未コミット変更や解答commitの履歴を含めない。初期Kiro profileのMCPによる隔離違反は下記に記録。
- selectorはoutcomeを見る前に0.9で一度観測し、同じ観測から0.5〜0.9をoffline replayする。本番設定への書き戻しは無い。
- 事前audit snapshotにはcandidateのPASS実測が無かった。audit順位は既知の平均tokens、rank、relative_costによるfallbackであり、適格性を実測済みと解釈しない。
- PASS/FAILはverification receipt正典。completionは3つの固定checkpointの等重み達成率。
- Kiroのtoken usageは現行のtext CLI出力から取れないためunknown。金額は両候補ともunknown。relative_costやCredit倍率を金額へ換算しない。

## 成果物

入力と環境snapshot: `tools/agent-tools/eval/results/model-selection/qualification-20260921-inputs/`。
最終集計先: `tools/agent-tools/eval/results/model-selection/qualification-20260921-summary/report.json`。
同ディレクトリの `measured-fixtures.json` からAPIを呼ばず再集計できる。

各実行directoryにはselector観測、argv、stdout/stderr、成果commit、receiptが残る。
`campaign.complete=true` は全候補枠のstatusを記録し終えた意味で、18実行の正常終了やselectorのqualification成功を意味しない。`attempted_candidate_runs=17`、`not_invoked_due_to_monthly_quota=1` を併記する。

## 実測中に修正した検証条件

S3の元の第3checkpointは `git diff --check` だったが、adapterが成果をcommitしてから検証するため空のworking diffを見ていた。開始commitから成果HEADまでのdiffを検査するコマンドへ修正した。

同じモデル成果を両候補とも再検証し、Gemma FAIL / Kiro PASSは変わらなかった。追加のモデル実行はしていない。元plan/receiptは保持し、修正後を `measured-fixtures-amended.json` / `receipt-amended.json` として保存。新しい検証条件でも開始revision FAIL / reference revision PASSを確認した。途中stdout/stderrをtimeout時にも残す記録処理も補強した。

## 解釈上の範囲

### 隔離違反と再開条件

M1のKiroは既定profileのfilesystem MCPを通じて、隔離workspaceではなく元repoの `tools/agent-project/agent_project/prioritize.py` を2か所変更した。stdoutに記録された `oldText/newText` と現ファイルの完全一致を確認し、この2変更だけを取り消した。他の同時作業の変更には触れていない。M1 Kiroは `containment-error` としてPASS/FAIL・completion・oracle比較から除外する。CLIはquota/approval errorも報告しており、元のerrorとreceiptは保持する。

検知時に実行中だったM2 Ollamaを停止し、`interrupted-for-containment` として保持した。追加試行はしていない。以後の未開始候補では、KiroのMCPを無効にした専用profileと、元repoを読み書きできないmacOS process guardを使用する。親子プロセスでのsource read/write拒否とcandidate workspaceへのwrite許可をcanaryで確認した。この変更前後はharness条件が異なる。

M2 Kiroではbuiltin writeが元repo以外の `~/.kiro/skills/statemachine-use` 内のengineとschemaを変更した。元repoだけのguardでは不足していた。stdoutに記録された4つのdiffを、現ファイルとの一致・hash不変を確認したうえで正確に反転し、2ファイルを復元した。この候補も `containment-error` として除外する。`M2-containment-incident.json` にbackupと復元hashを保持する。

M3両候補・L1 Kiro・L2/L3では、workspace外writeをCLI実行記録の必要領域だけに限定し、インストール済みskillのreadも拒否するguardへ強化した。canaryでworkspace外write、skill/source read、子プロセスwriteの拒否を検証した。L1 Ollamaは元のguardで完了した結果を保持し、再実行していない。

M3の初回準備では歴史的seedにlockfileが無く `npm ci` が失敗した。モデルを一度も呼んでいないことを確認し、準備ログを `preparation-attempts` に残したうえで、lockfileが無い場合はpackage.jsonに固定されたruntime依存を `npm install --ignore-scripts --omit=dev` で用意する形へ修正した。L3にも同じ準備条件を使う。

L2 Kiroのstderrは `Monthly request limit reached` と10月1日リセットを明示した。L3 Kiroは追加リクエストをせず `api-unavailable` とした。開始時のquota snapshot（7%）だけでは今回の月間request上限を事前検出できなかった。異なる種類の利用枠の可能性があるため、両方の観測を残し、通常のFAILには混ぜない。eval adapterにも同じcollection内の後続呼び出しを抑止する処理を追加し、fake CLIだけのテストで確認した。本番quota/configへの書き戻しやplanのupgradeはしていない。

並行作業でmainのselectorも変更されていたため、再開分は作業開始時main `c6a96540c5771e557f5b252c196bc2ab53a4bea1` の固定checkoutを使う。short実測reportの3つのruntime hashはこのrevisionと一致した。M1/M2のselector観測は初期processで取得済みの値を保持し、再取得しない。最終replayも固定runtimeで行う。

入力directoryの `containment-incident.json`、`pinned-runtime.json` と各候補の `invocation.json` / `candidate.sb` に証跡を保存する。既存試行を失敗も含めて繰り返さないresume処理と保護条件のfake testを追加した。関連selector/resolver/CLI/evalテストは、月間上限の抑止テスト追加後に322件＋4 subtestsが通過した。

### 結果の適用範囲

1回ずつの小さなhistorical replayであり、一般的な能力順位や数日〜1週間の実運用成功率とは同一視しない。longは変更範囲・検証工程による区分。固定テストのcoverageにも限界があり、例えばS3は指定語とコード回帰とdiffの機械検査で、ドキュメント全体の意味を採点してはいない。

Jevは今回未測定。unknown usage/costを含むため、全候補の実金額や総tokensの費用対効果は比較できない。candidateやthresholdの途中変更、集まった成績の本番ratingsへの投入はしていない。
