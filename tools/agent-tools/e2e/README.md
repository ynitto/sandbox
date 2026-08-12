# agent-tools シナリオ E2E

`agent-project` / `agent-flow` / `agent-amigos` / `agent-loop` / `agent-audit` と、同梱の
`agent-ollama` / `agent-aider` 実行アダプターをシナリオ単位で
検査するランナーです。既定は各エンジンの決定的な `stub` エージェントを使うため、ネットワークも
クラウド利用料も発生しません。成功だけでなく検証失敗、予算停止、再計画、map/reduce、JSON 出力、
複数ノード、質問応答、差し戻し、headless tool loop、イベントACK、監査の収集・抽出・蒸留、
Ollamaのツール拒否・無進捗・文脈枯渇・安全なreplay、aiderのusage集計を含みます。

```bash
# 全シナリオ（通常はこちら。課金なし）
python3 tools/agent-tools/e2e/run.py

# エンジン、またはシナリオを絞る
python3 tools/agent-tools/e2e/run.py --engine agent-flow
python3 tools/agent-tools/e2e/run.py --engine agent-loop
python3 tools/agent-tools/e2e/run.py --scenario flow-recovery --scenario flow-result-json

# CI が収集しやすい詳細 JSON（失敗時の stdout/stderr も含む）
python3 tools/agent-tools/e2e/run.py --json > e2e-report.json
python3 tools/agent-tools/e2e/run.py --list
```

## 実クラウド CLI（明示 opt-in）

`--agent-cli` を付けた場合に限り、mock 一式の代わりに `agent-flow` の小さな smoke scenario を
指定 CLI で 1 回実行します。このモードは認証済み CLI とネットワークが必要で、**利用料が発生する
可能性があります**。通常の CI では指定しないでください。

```bash
python3 tools/agent-tools/e2e/run.py --agent-cli codex
python3 tools/agent-tools/e2e/run.py --agent-cli claude --request 'agent-e2e-ok とだけ答える'
```

`agent-ollama` と `agent-aider` は上位3エンジンと同じオーケストレーターではなく、共通の
agent CLI契約へ接続する実行アダプターです。しかし入出力、usage、tool loop、安全ゲートは障害点に
なるため対象外にはせず、サーバーやaiderプロセスをmockした無課金シナリオとして収録しています。
実モデルを起動する評価は速度・再現性・費用が異なるため、この通常E2Eには混ぜません。

シナリオ一覧とカバレッジ軸は `scenarios.json` に宣言しています。追加時は、入力・出力・終了コード・
状態遷移のうち何を増やすかを `covers` に明記し、テスト名を安定した公開契約として参照してください。
