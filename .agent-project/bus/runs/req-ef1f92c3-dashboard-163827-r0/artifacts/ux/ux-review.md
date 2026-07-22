# UX レビュー: 一貫性ゲートの状態把握と有効化（dashboard）

対象: `tools/agent-dashboard` 作業ツリー（HEAD=t8 + 未コミットのソース改稿）。
観点は 3 つ — 結線済み／未結線の判別しやすさ、有効化導線の実行可能性、診断要約の可読性低下の有無。

## 1. 結線済み／未結線の判別しやすさ

現物で確認できた良い点:

- 見出しバッジが 3 値（`renderer.js` `consistencyGateHtml`）。`有効` / `一部のみ` / `未結線` を分ける。2 値だと「一度も有効化していない既定状態」が『一部のみ』＝部分的に動作中と読める。
- 行ごとに `結線済み` / `未結線` バッジ ＋ 設定値そのものを常に表示。未結線でも値があるときは「別のコマンドが設定されています。一貫性ゲートの検査ではありません」と添える。`regression_cmd` は codd-gate 専用キーではない（`make -s smoke` などもここに入る）ので、値を隠すと「未結線＝空」と誤読される。
- 判定は main 側 1 箇所（`project.js` の `GATE_REGRESSION_RE` / `GATE_INTAKE_RE`、`codd_gate_wiring.py` と同語順）。全結線の派生述語 `wired` も main が 1 度だけ出し、renderer と needs が各自 `&&` を組み直さない。

指摘（本タスクで修正済み）:

- **F1**: `test/overview-ui.test.js` のゲート fixture が payload の `wired` を欠き、renderer が `gate.wired` を見る改稿に追随していなかった。全結線でも見出しが `一部のみ` になり、`全結線のゲートバッジ（有効）が出る` が失敗。判別しやすさを守る唯一のバッジテストが赤のまま放置されていた。→ fixture に `wired` を追加。

## 2. 有効化導線の実行可能性

「画面の指示をそのまま実行できるか」で見て、穴は見つからなかった:

- 書くべき yaml 行は **未結線のキーの行だけ**を `<pre class="mono">` で出す。`<root>/repos.json` は `p.dir` で実パスに展開し、`p.dir` が無いときだけ README と同じプレースホルダへ戻す。
- CLI（`codd_gate_regression.py`）の提示は `configFile あり && regression_cmd 未結線` に限定。`--config` は既存ファイルしか指せず、無ければエラーで止まるため。実行ディレクトリ（`tools/agent-project/`）と `--dry-run` も併記。
- `intake_cmd` に対応する注入 CLI は無い旨を明示し、yaml 直接編集へ誘導。
- 設定ファイル未検出時は `.agents/agent-project.yaml` の作成手順のみ。開くボタンも CLI も出さない。
- 「設定ファイルを開く」ボタンは概要が `data-gate-open` ＋ `bindConsistencyGate`（`overview.js:366`）、needs 側は既存 `data-open` 経路。どちらも配線済みで到達可能。UI から設定を書き換える経路は無い（done 不変条件を守る）。

## 3. 診断要約の可読性低下の有無

- ゲート節は `renderNeedFacts` の facts 末尾に**追加するだけ**。`need-diag`（検証失敗チップ＋要約 `<strong>`）、`確認・対処`、`need-failure-context` の dl、`<details>判断材料を見る` はいずれも無改変で残る。
- 由来を `regression` / `verify` に分けたのは妥当。`回帰検知:` プレフィックスは codd-gate 以外の `regression_cmd` にも付くため、`regressionWired` と併用しないと「回帰検査が止めた」と断定した瞬間に概要の結線表示と矛盾する。

指摘（本タスクで修正済み）:

- **F2**: `test/needs-gate-integration.test.js` が旧 API 名 `needGateFailure` を grep しており、**ファイル読み込み時点で `AssertionError: renderer に function needGateFailure が見つかりません` でクラッシュ**。可読性を守る 8 ケースが 1 件も実行されていなかった。ソースは `needGateSource(failure, n, gate)`（返り値 `'regression' | 'verify' | null`）へ改稿済み。→ 名前・シグネチャ・返り値をテスト側で現行仕様に合わせ、`regression_cmd` 未結線なら文面だけで断定しないケースを追加。
- **F3**: 同テストの「`consistencyGate` 未提供でもゲート節は出る」は現行の意図と逆。ペイロード無し（旧 main）では概要のゲート節も空になるため、needs から存在しないセクションへ誘導してはいけない。→ 「ゲート節を出さない／失敗要約は残る」へ書き換え。

## 依存タスク報告との矛盾

t7・t8 は「detail-tabs-ui のみ既存 FAIL、他は全通過」と報告したが、現在の作業ツリーでは逆で、`detail-tabs-ui` は通り `needs-gate-integration` と `overview-ui` が落ちていた。報告後にソース側が改稿（`needGateFailure`→`needGateSource`、`gate.wired` 追加、見出しバッジ 3 値化）され、テストが追随していなかったため。報告の「全通過」は執筆時点のもので、現物とは一致しない。

## 検証

- `npm test` 全通過（exit 0）。個別に `needs-gate-integration` / `overview-ui` / `consistency-gate` / `consistency-gate-ui` / `needs-diagnosis` / `needs-command-failure` / `needs-layout-ui` / `detail-tabs-ui` を再実行して確認。
- 変更はテスト 2 ファイルのみ（`test/needs-gate-integration.test.js`、`test/overview-ui.test.js`）。ソース（`renderer.js` / `needs.js` / `project.js`）には触れていない。

## @followup（範囲外）

- @followup `tools/agent-project` の GUIDE.md と `codd_gate_regression.py --config` 既定が旧名 `.agent/agent-project.yaml` のまま。README（`.agents/`）が正。dashboard 側は `.agents/` に統一済みなので、画面と CLI ヘルプで食い違う。
- @followup `toolconfig.js:39` の行内コメント除去がクォート内も切る。値に ` #` を含む `regression_cmd` を書かれると dashboard だけ値を欠いて誤表示する。
