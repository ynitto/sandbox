# Orchestration feature

ノード横断（マシン単位）の管理面。プロジェクトやミッションではなく、**この PC のエージェント資源**を
扱う。amigos に間借りしていたノード予算の実装はここへ移管して v2 対応した
（`amigos:budgetSave` は互換のため残してある）。

設計: [`docs/designs/agent-dashboard-design.md`](../../../../../docs/designs/agent-dashboard-design.md) §5。
画面: [`docs/plans/2026-07-19-agent-dashboard-orchestration-token-budget-design.md`](../../../../../docs/plans/2026-07-19-agent-dashboard-orchestration-token-budget-design.md)。

## 扱う契約（すべてツール横断のデータ契約。コードは共有しない）

| 契約 | 置き場 | この画面の役割 |
|---|---|---|
| [node-budget](../../../../../schemas/node-budget.schema.json) | `$AGENT_BUDGET_DIR`（既定 `~/.agents/budget/`） | 実行時間・トークンの上限設定と消費内訳の表示、ワークロード別の配分計算・レート較正 |
| [agent-control](../../../../../schemas/agent-control.schema.json) | `$AGENT_CONTROL_DIR`（既定 `~/.agents/control/`） | 望ましい状態（lifecycle・エージェント/モデル上書き・縮退指定）の read/write と、各エンジンが書く `status/` の読み取り |
| [agent-cli](../../../../../schemas/agent-cli.schema.json) | `agents/<name>.json` | ドロップイン定義の棚卸し・検証・編集 |
| [agent-instructions](../../../../../schemas/agent-instructions.schema.json) | プロジェクト配下 | 共通指示の編集（委譲先ノードへ伝播する） |
| [agent-session-commands](../../../../../schemas/agent-session-commands.schema.json) | その端末の設定ファイル | セッション開始時の前準備コマンド（**伝播しない**） |

## 不変条件

書くのは上記の契約ファイルだけで、エンジンのプロセスにも状態リポジトリにも触れない。
lifecycle を `pause` / `stop` にしてもプロセスは殺さない — 各エンジンが自分のターン先頭で
読んで自律的に止まる（pull 型。push 型の IPC を持たない）。
