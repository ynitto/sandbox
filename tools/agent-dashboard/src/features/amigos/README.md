# Amigos feature

agent-amigos（役割駆動マルチエージェント協働）の独立した制御面。3 つの責務を持つ。

## ホーム（常駐デーモン）との連携 — タスク依頼と手動引き受け

- **ホーム** = `agent-amigos.{yaml,yml,json}` または `.agent/agent-amigos.*` を持つ
  ディレクトリ（設定ファイルが自動発見マーカーを兼ねる — agent-project と同じ流儀）。
  `amigos.homeDirs` の明示指定 + 全体設定 `projects.roots` 配下の走査で見つけ、
  ホームのバス（設定 `bus`、既定はホーム自身）をミッション一覧に含める。
- **タスク依頼（post）**: 「タスクを依頼…」フォーム（投函先ホーム・タイトル・goal・
  design doc 本文・役割ミッション表 JSON）→ ホームの
  `.agent/agent-amigos/commands/*.json` へ post 指示を投函。常駐デーモンが取り込んで公示する。
- **手動引き受け（claim）**: 募集中ロールの「引き受け」ボタン → 同じく claim 指示を投函。
  ホーム側を `manual_claim: true` にすると自動応募が止まり、手動引き受けだけで回せる。
- dashboard がバスへ直接書くことは無い（書くのはホームの commands ドロップだけ —
  agent-project の commands/ と同じ「プロセス間 API を持たない」結合方式）。
  投函先は発見済みホームのみに検証される。

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
