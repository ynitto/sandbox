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
| [agent-control](../../../../../schemas/agent-control.schema.json) | `$AGENT_CONTROL_DIR`（既定 `~/.agents/control/`） | 自動選択されたエージェント/モデルと各エンジンの`status/`を読み、lifecycle・同時実行数を保存する。用途別agent/model上書きは互換読取のみで通常UIから編集しない |
| [agent-cli](../../../../../schemas/agent-cli.schema.json) | `agents/<name>.json` | ドロップイン定義の棚卸し・検証・編集 |
| [agent-instructions](../../../../../schemas/agent-instructions.schema.json) | プロジェクト配下 | 共通指示の編集（委譲先ノードへ伝播する） |
| [agent-session-commands](../../../../../schemas/agent-session-commands.schema.json) | その端末の設定ファイル | セッション開始時の前準備コマンド（**伝播しない**） |
| [agent-profiles](../../../../../schemas/agent-profiles.schema.json) | `$AGENT_CONTROL_DIR`（既定 `~/.agents/control/`） | 実行レベル（単純作業/軽量/標準/高性能）の宣言と、ワークロードの予算残率・agent CLI ごとの枠からの決定的なtier/候補の選択。**エンジンはこの契約を読まない**——選択結果は agent-control（上の行）へ投函するだけ |
| [agent-tuning](../../../../../schemas/agent-tuning.schema.json) | `$AGENT_TUNING_DIR`（既定 `~/.agents/tuning/`）と `$AGENT_METHODS_DIR`（既定 `~/.agents/methods/`） | 作業ルールの説明・適用条件を表示し、自動適用または独自ルールを保存。有効化時はsnapshot + source hashで固定し、更新がある場合だけ明示的な更新操作を出す |

## 全体設定から宣言できるもの（この面が control.json へ書く）

| 画面 | 契約のキー | 読む側 |
|---|---|---|
| 実行制御 → 実行方針 | agent-profilesから`workloads.<wl>.tier` / `agent_cli` / `model`へ投函 | 各エンジン（`routine` は dashboard 自身＝定常業務の tmux 起動） |
| 実行制御 → 実行の許可・停止 | `workloads.<wl>.lifecycle` | 各エンジン |
| 実行制御 → 同時に動かす数（自動実行） | `workloads.flow.concurrency`（`max_runs` / `workers`） | agent-flow / agent-project 常駐体（`max_runs` を自分のワーカープール枠として読む） |

`concurrency` は「この PC で同時にどれだけ走らせてよいか」の宣言。**その端末の資源の話**なのに、
設定の置き場（各プロジェクトの `agent-flow.yaml`）はプロジェクトごとに散っていて、1 台の負荷を
下げたい人が全プロジェクトの yaml を直して回ることになっていた。優先順位は他のキーと同じ
control > CLI 引数 > 設定ファイル > 既定で、画面で空欄にすればキーが消えて元の解決へ戻る。
壊れた値（負数・`workers: 0`）は保存前に弾く——GUI の入力ミスで run が誰にも進められなくなる方が、
上書きが効かないより高くつく。

## 不変条件

書くのは上記の契約ファイルだけで、エンジンのプロセスにも状態リポジトリにも触れない。
lifecycle を `pause` / `stop` にしてもプロセスは殺さない — 各エンジンが自分のターン先頭で
読んで自律的に止まる（pull 型。push 型の IPC を持たない）。

## 実行プロファイル自動選択（`profiles.js`）

利用量削減の Phase 1 案 D。実行レベルごとの候補（`agent_cli` + `model`）を宣言すると、
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
