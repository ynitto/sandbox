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

## ローカル PC の実機 E2E（`--local` / `local.py`）

mock 経路が各エンジンの unittest を呼ぶのに対し、こちらは一時 prefix へ
`tools/agent-tools/install.sh` を実行し、**そこで出来た zipapp を実プロセスとして叩く**。
zipapp の組み立て・CLI 定義の配置・エンジン間の受け渡し（agent-flow バス → agent-audit の
収集）まで、この PC の実物で通す。クラウドは 1 回も呼ばない。

```bash
python3 tools/agent-tools/e2e/run.py --local              # fast のみ（モデル不要・20 秒ほど）
python3 tools/agent-tools/e2e/run.py --local --tier model # ローカル ollama を実際に呼ぶ
python3 tools/agent-tools/e2e/run.py --local --tier wired # エンジンをローカルモデルで回す（数分）
python3 tools/agent-tools/e2e/run.py --local --tier all   # 全部（10 分前後）
python3 tools/agent-tools/e2e/run.py --local --scenario flow-run --keep   # 砂場を残す
python3 tools/agent-tools/e2e/run.py --local --list
python3 tools/agent-tools/e2e/local.py --json             # 直接叩いても同じ
```

段は 4 つ。`fast` は stub エージェントだけで回りモデルを要らない（20 秒）。`model` は
ローカル ollama（既定 `gemma4:e4b`・`--model` / `AGENT_E2E_MODEL` で替える）を実際に呼び、
**モデルが書いたファイルを機械が検証する**ところまで見る。`daemon` は agent-loop を常駐させて
tmux ペインへの定期送信を見届ける（30 秒ほど）。`wired` は**エンジンそのものを stub でなく
ローカルモデルで回す**（`--executor agent --agent-cli ollama`。2〜3 分・壁時計はモデルの
気分で 2 倍ほどぶれる）。前提（ollama・モデル・tmux・node）が
無いときは失敗ではなく `SKIPPED` になる。

既定を `gemma4:e4b` にしてあるのは、`gemma4:e2b` が同じ配線で 15 回中 5 回落ちたため
（空応答／ファイルを作らず「完了」と言う）。ここで測るのは配線であってモデルの実力ではない。
空応答だけは実行系の一過性として 1 回引き直す——その先の失敗はそのまま落とす。

`wired` の 2 本は「ローカル推論が本当に走ったか」を、砂場の `AGENT_OLLAMA_LOG_DIR` に
ログが増えたことで確かめる。stub へ落ちても緑になる、という抜け道を塞ぐため。
`flow-wired` のグラフを `--plan-file` の work 1 ノードに固定してあるのも同じ理由。既定の
fan-out は同じ配線を 6 回通るだけ（実測 5 分半 対 30 秒）で、しかも verify ノードを挟むと
**終端がモデルの判定文に左右される**——実測で `check1` の出力が機械の読める verdict に
ならず run ごと failed になった。判定力を測りたいなら `eval/judge_eval.py` の担当で、
ここは配線を見る場所。agent-amigos のローカルモデル版は、4 ロール × 複数ターンで
壁時計が読めないので入れていない。

| シナリオ | 段 | 見るもの |
|---|---|---|
| `install-parity` | fast | install.sh が出した 9 本が起動でき、CLI 定義が配られている |
| `installed-drift` | fast | この PC の `~/.local/bin` が repo のソースより古くない（「直したのに直らない」の検出） |
| `flow-run` | fast | 計画 → fan-out → gate → 統合 と `result --json` の done |
| `project-drain` | fast | backlog 1 件が実行 → verify → done → archive、agent-flow への委譲、納品書 |
| `amigos-cycle` | fast | 公示 → 自己補充 → 各ロール実行 → 統合 → 受入 → 納品棚 |
| `audit-collect` | fast | 先行シナリオが実際に残したバスからの収集（エンジン間の受け渡し） |
| `loop-schedule` | daemon | デーモン起動 → 定期プロンプトが tmux ペインのエージェントへ届き終端する |
| `dashboard-cli-parity` | fast | 入れたばかりの CLI 定義から dashboard(JS) と agentcore(Python) が同じ argv を出す |
| `dashboard-reads-project` | fast | エンジンが archive へ書いたタスクを dashboard が読める |
| `flow-wired` | wired | agent-flow を executor=agent / agent-cli=ollama で回し、モデルが書いた成果物を確かめる（30〜60 秒） |
| `project-wired` | wired | agent-project → agent-flow → ローカルモデルの委譲が層をまたいで通る |
| `ollama-oneshot` | model | 本文 stdout / usage stderr の分離 |
| `herd-harness` | model | ツールループが成果物を書き、受入条件を機械が検証する |
| `loop-harness` | model | 同じハーネスを agent-loop 側の入口から回す |

状態は環境変数で隔離する（`AGENT_PROJECT_AGENTS_HOME` / `AGENT_CONTROL_DIR` /
`AGENT_BUDGET_DIR` / `AGENT_AMIGOS_TURNS_DIR` / `AGENT_LOOP_RUN_DIR` /
`AGENT_OLLAMA_LOG_DIR`）。`loop-schedule` だけは `loop-state` と `slots` が HOME 直下でしか
移せないので HOME ごと差し替え、tmux も専用ソケット（`agent-e2e-<pid>`）へ隔離する——実機で
動いている agent-loop のセッション・スロットを奪わないため。実 `~/.agents` は読み書きしない——とくに `AGENT_CONTROL_DIR` を
外すと、実 `control.json` の workloads 上書きで `stub` 指定が本物のモデルへ化ける。

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
