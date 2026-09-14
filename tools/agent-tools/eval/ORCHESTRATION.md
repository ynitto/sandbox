# 固定3パターンの比較

`orchestration_eval.py` は Single / Cascade / Critique を同じfixtureで比較する。
HydraFusion、adaptive router、既存の2群methods trialには依存しない。
通常のworker評価コマンドと本番設定は変更しない。

## 開始方法

Python 3.12以上とpytestを使用する。POSIXのみ対応。リポジトリの `.venv/bin/python` が使える。

1. `orchestration.example.json` を実験用ファイルへコピーする。
2. baseline・starter・criticのmodelとfamilyを実際のIDに置き換える。baselineは現行運用のモデル、starterは低価格候補に固定する。familyは人が明示し、別名で同じfamilyを登録しない。
3. solver用CLIとClaude CLIに通常どおりログインする。標準設定のcriticは `transport: "cli"` / `cli: "claude"` で、APIキーの設定は不要。baselineにはClaudeとは別familyのモデルを選ぶ。
4. dry-runで解決されたcommit、CLI設定、9実行の順序を確認する。

```sh
rtk proxy .venv/bin/python tools/agent-tools/eval/orchestration_eval.py \
  --manifest /absolute/path/experiment.json > /tmp/orchestration-plan.json

rtk proxy .venv/bin/python tools/agent-tools/eval/orchestration_eval.py \
  --manifest /absolute/path/experiment.json --execute

rtk proxy .venv/bin/python tools/agent-tools/eval/orchestration_report.py \
  /absolute/path/run/ledger.jsonl
```

`--execute` がない場合、モデルは呼ばず、CLI実行ファイルや認証も不要。
CLIはローカルに起動するが、推論は各CLIの接続先で実行される。オフライン推論を意味しない。
例の `REPLACE_*` を残した設定は起動前に拒否する。モデル可用性そのものはCLI/APIの応答で確認される。
starterが本当に安い・弱いかは料金表と別の能力測定で判断し、名前から推定しない。

## 実行契約

| arm | 呼び出し |
|---|---|
| Single | baselineで1 session → private判定 |
| Cascade | starter → 公開gate → FAILの場合だけbaselineで1回修正 → private判定 |
| Critique | baseline → 別familyのtool-less critic → baselineで1回修正 → private判定 |

各session内部では既存CLIの通常tool-loopが動く。外側のretryとfallbackはない。
Revisionは初稿の作業状態を引き継ぐ新しいCLI呼び出し。前sessionの会話履歴や内部推論をresumeしない。
候補は独立したcommitの展開から始め、初期ファイルに対する変更範囲も最終判定する。
CLI定義の権限が適用される。候補checkoutはOSのsandboxではないため、信頼できる評価用環境で動かす。
標準のcriticはClaude CLIを空の一時ディレクトリで起動し、渡す材料はレビュー用テキストだけ。
`--safe-mode --tools "" --strict-mcp-config --mcp-config '{"mcpServers":{}}' --no-session-persistence`
でツール、MCP、カスタマイズ、会話履歴の保存を無効にする。
solver用の書き込み許可・resume・fallback引数はcriticへ引き継がない。
通常のログイン認証を保持するため、APIキー必須の `--bare` は使わない。
criticとして対応するCLIは現在Claudeのみ。必要なフラグがない古いCLIは実行前に拒否する。

T1/T2/T3のseed/checkを既存worker評価から再利用する。T2には公開テストとは別の境界値probeを追加した。
公開gateはT1がpytest、T2が固定公開テスト、T3がJSON構文検証。
最終判定は別プロセスで実施し、採点コードと結果はモデルの入力へ渡さない。
candidateから `tools/agent-tools/eval/` を除外する。Git履歴もfixture用に作り直す。
初版は3課題のみ。12課題pilot・未使用課題による確認実験は、独立したseed/checkを追加してから行う。

既定値は1実行600秒、最終判定に60秒を予約。Cascade初稿180秒、Critique初稿300秒、critic60秒。
コピー・gate・証拠保存も開始後の時間に含む。最終判定までに600秒を超えた結果はPASSにしない。
プロセスはtimeout/キャンセル時にグループごと停止する。通信先で既に始まった推論の課金取消までは保証しない。
Critique初稿の診断用採点は全solver呼び出しの後で行い、その時間は `diagnostic_wall` に分ける。

## 計測と出力

`results/<timestamp>-orchestration-three-arm/` に保存する。

- `manifest.json`: commit、順序、設定、CLI定義hash、評価コードhash。
- `ledger.jsonl`: 開始した各実行の結果と工程別usage。再試行時は新規実験にする。
- `report.json`: PASS率、cost per PASS、wall中央値/p95、昇格率、レビュー前後の変化。
- 各実行の工程ディレクトリ: 入出力・終了理由・呼び出し引数。認証キーは記録しない。
- `candidate.diff` / `changed-files/`: 削除差分と未追跡ファイルを含む候補の証拠。
- `completion.json`: 完了件数と計画件数。途中停止した実験は完全な比較とみなさない。

現時点で費用を自動取得できるsolver経路はClaudeのJSON result (`total_cost_usd`)。
これはproviderの推定値として記録し、session_idとmodelUsageを残す。
それ以外のCLIは起動できるが、費用はnull。Copilotを含めusageが取得できない経路では費用順位を出さない。
Claude CLIのcriticもJSON resultから費用を取得する。API方式のcriticは応答usageと固定料金表で推定する。実課金額やrelative_costとは混ぜない。
CLIが報告しない内部モデル切替・内部retryは検出できない。実験前にprovider側の自動選択を無効にする。

`--stop-after-usd N` は**実行間**で累積費用を確認して停止する。実行中の厳密な課金上限ではない。
設定時に費用不明が出た場合も停止し、欠損をゼロとして続けない。
失敗・timeout・cancelledもPASS率の分母に含む。費用は全工程を含め、欠損が1つでもあれば実行費用はnull。
基盤障害は別のstatusで確認できる。quality gateに落ちた場合と混ぜない。

## critic採用率

solverの自己申告を採用の証拠にはしない。初期レポートの採用率はnull。
候補差分と指摘を人が確認し、次の形式で別ファイルへ記録する。

```json
[
  {
    "run_id": "T1-1-critique",
    "finding_id": "F1",
    "adopted": true,
    "evidence": "初稿と最終差分を確認し、指摘されたKiB丸め処理の修正を確認した"
  }
]
```

```sh
rtk proxy .venv/bin/python tools/agent-tools/eval/orchestration_report.py \
  /absolute/path/run/ledger.jsonl --adoptions /absolute/path/adoptions.json
```

全指摘を確認した場合だけ採用率を算出する。指摘ゼロはN/A（JSONではnull）。
採用率が高いほど優れているとは判断しない。FAIL→PASSとPASS→FAILも併記する。

## 判断の範囲

比較区間は課題を再標本化するpaired bootstrap。反復を独立課題として数えない。
3課題smokeは配線確認であり、性能の結論に使わない。
pilot後も判定は `requires_holdout` に留め、routerやモデル設定を自動変更しない。
課題追加時にはgateとprivate checkerを分け、未知の課題IDを名前だけ登録しない。

## モデルなしのテスト

```sh
rtk proxy .venv/bin/python -m pytest -q tools/agent-tools/eval/test_orchestration.py
```

## 既存のAPI方式を使う場合

`orchestration.api.example.json` を使用する。criticに `transport: "api"` を指定できる。
以前のendpoint付き設定はtransport省略でもAPI方式として読み込む。
HTTPSのChat Completions endpoint、`api_key_env`、`max_tokens` が必要。
料金は `prices_per_million` のinput / cached_input / outputを設定する。未設定は費用不明。
応答のモデルIDにversion suffixがある場合は `response_model_aliases` で明示する。
