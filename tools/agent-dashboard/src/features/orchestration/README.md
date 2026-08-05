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
| [agent-profiles](../../../../../schemas/agent-profiles.schema.json) | `$AGENT_CONTROL_DIR`（既定 `~/.agents/control/`） | 実行プロファイル（大/中/小）の宣言と、ワークロードの予算残率・agent CLI ごとの枠からの決定的な段/候補の選択。**エンジンはこの契約を読まない**——選択結果は agent-control（上の行）へ投函するだけ |

## 不変条件

書くのは上記の契約ファイルだけで、エンジンのプロセスにも状態リポジトリにも触れない。
lifecycle を `pause` / `stop` にしてもプロセスは殺さない — 各エンジンが自分のターン先頭で
読んで自律的に止まる（pull 型。push 型の IPC を持たない）。

## 実行プロファイル自動選択（`profiles.js`）

利用量削減の Phase 1 案 D。「大・中・小」等の段（`agent_cli` + `model` の候補列）を宣言すると、
ワークロードの予算残率（node-budget）と agent CLI（＝アカウント）ごとの枠（node-budget の
`allocation.agents`）から**純関数 `decide()`** が段と候補を決定的に選び、選択結果だけを
agent-control（control.json）へ投函する。段は budget の残率だけで決め（ヒステリシス＋最小保持で
フラッピングを防ぐ）、CLI 枠の枯渇は**候補の選択**にのみ影響する（一段下へフォールバック）。

- **不変条件: エンジンは profiles.json を読まない。** 選択ロジックは dashboard の 1 実装に閉じる
  （柱1 / C2・C7）。エンジンは今日どおり agent-control を読むだけ。
- `apply()` は現状の control.json と一致する決定を書かない（`saveControl` は必ず
  `revision` を+1 するため、無変化の書き込みは各エンジンの `revision_applied` 突き合わせを
  無意味にする）。
- 画面（全体設定 → 実行制御）は「評価する（dry-run。書かない）」→「適用」の 2 段。
- 自動評価（`policy.interval_sec`）は現時点で**宣言のみ**（保存はされるが、定期実行の
  スケジューラは未実装）。今日の手段は手動の評価/適用ボタン。

設計: [`docs/plans/2026-08-05-phase1-token-efficiency-detailed-design.md`](../../../../../docs/plans/2026-08-05-phase1-token-efficiency-detailed-design.md) §1。
テスト: [`test/orchestration-profiles.test.js`](../../../../test/orchestration-profiles.test.js)。
