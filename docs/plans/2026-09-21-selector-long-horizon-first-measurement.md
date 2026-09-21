# Selector Long-Horizon Qualification — 初回実測

状態: 実行中。完了済みの課題だけを集計し、未完了をFAILへ変換しない。

## 条件

- 2026-09-21 JST。9課題 × 2候補 × 各1回。short各候補600秒、medium1200秒、long1800秒の上限。
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
`campaign.complete` がfalseのreportは途中結果。

## 実測中に修正した検証条件

S3の元の第3checkpointは `git diff --check` だったが、adapterが成果をcommitしてから検証するため空のworking diffを見ていた。開始commitから成果HEADまでのdiffを検査するコマンドへ修正した。

同じモデル成果を両候補とも再検証し、Gemma FAIL / Kiro PASSは変わらなかった。追加のモデル実行はしていない。元plan/receiptは保持し、修正後を `measured-fixtures-amended.json` / `receipt-amended.json` として保存。新しい検証条件でも開始revision FAIL / reference revision PASSを確認した。途中stdout/stderrをtimeout時にも残す記録処理も補強した。

## 解釈上の範囲

### 隔離違反と再開条件

M1のKiroは既定profileのfilesystem MCPを通じて、隔離workspaceではなく元repoの `tools/agent-project/agent_project/prioritize.py` を2か所変更した。stdoutに記録された `oldText/newText` と現ファイルの完全一致を確認し、この2変更だけを取り消した。他の同時作業の変更には触れていない。M1 Kiroは `containment-error` としてPASS/FAIL・completion・oracle比較から除外する。CLIはquota/approval errorも報告しており、元のerrorとreceiptは保持する。

検知時に実行中だったM2 Ollamaを停止し、`interrupted-for-containment` として保持した。追加試行はしていない。以後の未開始候補では、KiroのMCPを無効にした専用profileと、元repoを読み書きできないmacOS process guardを使用する。親子プロセスでのsource read/write拒否とcandidate workspaceへのwrite許可をcanaryで確認した。この変更前後はharness条件が異なる。

並行作業でmainのselectorも変更されていたため、再開分は作業開始時main `c6a96540c5771e557f5b252c196bc2ab53a4bea1` の固定checkoutを使う。short実測reportの3つのruntime hashはこのrevisionと一致した。M1/M2のselector観測は初期processで取得済みの値を保持し、再取得しない。最終replayも固定runtimeで行う。

入力directoryの `containment-incident.json`、`pinned-runtime.json` と各候補の `invocation.json` / `candidate.sb` に証跡を保存する。既存試行を失敗も含めて繰り返さないresume処理と保護条件のfake testを追加した。関連selector/resolver/CLI/evalテストは321件＋4 subtestsが通過した。

### 結果の適用範囲

1回ずつの小さなhistorical replayであり、一般的な能力順位や数日〜1週間の実運用成功率とは同一視しない。longは変更範囲・検証工程による区分。固定テストのcoverageにも限界があり、例えばS3は指定語とコード回帰とdiffの機械検査で、ドキュメント全体の意味を採点してはいない。

Jevは今回未測定。unknown usage/costを含むため、全候補の実金額や総tokensの費用対効果は比較できない。candidateやthresholdの途中変更、集まった成績の本番ratingsへの投入はしていない。
