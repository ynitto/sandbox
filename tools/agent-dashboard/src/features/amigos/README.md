# Amigos feature

agent-amigos（役割駆動マルチエージェント協働）の独立した制御面。2 つの責務を持つ。

## ミッション一覧（読み取り専用）

- バス上のファイル（真実）だけを読む。**dashboard からバスへは一切書かない**
  （書き込み所有権はオーナー / amigo のもの — agent-amigos 設計書 §4.2）。
- バスの形は 2 種類を受ける: ローカルバス（`<busDir>/missions/<mid>/`）と
  GitBus のクローン作業領域（`<busDir>/mission__<mid>/`）。`amigos.busDirs` 未設定時は
  `~/.agent/amigos/bus/*`（GitBus 既定 workdir）を自動発見する。
  **hub サーバのデータディレクトリもローカルバスと同型**（`missions/<mid>/`）なので、
  hub ホスト上ではそのパスを busDirs に足すだけで全ミッションが読める。
- 表示: phase（近似導出）・ラウンド・名簿（ロール × 担当ノード × 完了/一時停止）・
  ミッション予算の消費（バス events の `cli_seconds` 総和）・未回答質問数・
  deliverable（partial / reason）。正確な状態は `agent-amigos status` が正で、
  ここは「何がどこまで進んでいるか」の一覧が目的。

## ノード予算（node-budget 契約の管理面）

- 正典: `schemas/node-budget.schema.json`。実体は `$AGENT_BUDGET_DIR`
  （既定 `~/.agent/budget/`、`amigos.budgetDir` で上書き）の `config.json` ＋
  `ledger/<YYYYMMDD>.jsonl`。
- dashboard は **config を書き、台帳を読むだけ**。記帳・抑制は各ツールが行う
  （agent-amigos は実装済み: 超過中はそのノードの amigo が paused。
  kiro-loop / agent-project / agent-flow の記帳は後続）。
- UI: 期間（day / month / total）内の消費をワークロード別
  （routine / project / flow / amigos）に表示し、合計上限・内訳上限を編集できる。
  **0 = 無制限**。依頼側・請負側どちらのノードでも同じ契約 = 同じ画面。

## 配線

- feature 契約は他と同じ `{id, configDefaults, registerIpc, preloadApi}`。
  IPC は `amigos:overview`（ミッション + 予算）と `amigos:budgetSave` の 2 チャネルのみ。
- タブはミッションか予算データが存在するときだけ表示する（cowork と同じ流儀）。
