# Amigos feature

agent-amigos（役割駆動マルチエージェント協働）の独立した制御面。4 つの責務を持つ。

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

## 受入プレビューと納品棚（読み取り + commands 投函）

**成果物はミッションの中で見せる**（利用者が考える単位はミッションで、納品はその結果）。
一覧には受け取り済みの印だけを出し、中身は詳細ダイアログで見せる。

- **読み方の正典は `preview.js`**: 受入待ち（バスの `deliverable/`）と受け取り済み
  （納品棚）で同じ読み方を使う。30 ファイル・テキスト 20000 字・画像 2MB までの有界読みで、
  markdown は本文、画像は data URI、他はメタ情報だけを返し、renderer が種別ごとに描き分ける。
  成果物はプログラムに限らないため、「開かなくても中身が分かる」ことを受入判定の前提に置く。
- **受入プレビュー**: phase が reviewing のミッションだけ `missions.readDeliverablePreview`
  が overview に載せる。
- **受け取り済みの中身**: `amigos:deliveryContents` で**詳細を開いたときに 1 件だけ**読む。
  overview は毎回ポーリングされるので、全ミッションの全文・画像を常時運ばない。
- **受入操作**: 「この成果を受け取る」「修正を依頼する」は accept / reject の
  commands 投函（`amigos:accept` / `amigos:reject`）。**搬出も final.json も
  owner デーモンが書く** — dashboard は書き手を増やさない。
- **納品棚**（`deliveries.js`）: accept 済みの `<home>/deliveries/<mid>/delivery.json`
  （正典: `schemas/delivery.schema.json`）を読み、overview でミッション id により
  `mission.delivery` へ結び付ける。「保存先を開く」は既存の `shell:openPath` に流す。
  削除も搬出もしない。
- **gc されたミッションの納品**: バスから消えるとミッションが無くなり成果物の行き場が
  失われるので、`orphanDeliveries` として別に返し、renderer が「過去の成果物」節へ
  ミッションと同じ器（archived フラグ付き）で並べる。
- **投函の未取り込みを見せる**: commands ドロップは常駐デーモンが取り込んで初めて効くので、
  溜まったままなら常駐が停止している。`discoverHomes` の `pendingCommands` を画面に出して
  無言の失敗にしない。
- renderer に `window.prompt` は使わない（Electron の renderer では未対応で例外になる）。
  入力は `<dialog>` で受ける（修正依頼は `dlg-amigos-reject`）。

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
  IPC は読み取りの `amigos:overview`（ミッション + 予算 + 納品）、設定の `amigos:budgetSave`、
  commands 投函の `amigos:request` / `claim` / `accept` / `reject`。
- タブはミッションか予算データが存在するときだけ表示する（cowork と同じ流儀）。
