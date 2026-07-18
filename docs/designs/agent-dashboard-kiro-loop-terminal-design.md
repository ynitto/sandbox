# agent-dashboard: kiro-loop 連携（監視・復旧・端末ビュー）

> 日付: 2026-07-16（2026-07-18 意義・監視/復旧レイヤを統合）  
> 対象: `tools/agent-dashboard/src/features/kiro-loop/`  
> 関連: Cowork（一覧・実行）・[`agent-dashboard-feature-split-design.md`](./agent-dashboard-feature-split-design.md)

## 背景と意義

kiro-loop は Claude Code の loop 機能を任意のエージェント CLI で使えるようにしたものだが、
監視・介入面が tmux のまま残っている。状態確認には WSL コンソールを開いて `tmux attach`、
復旧には CLI で `kiro-loop send` — 外部接点（`~/.kiro/loop-state/<pid>.json`・`ls` / `send`
サブコマンド・`~/.kiro/kiro-loop.log`）はあるが、すべて pull 型かつ CLI 前提で、
人の負荷と導入障壁になっていた。

dashboard 連携の意義は、この監視・介入面を dashboard に移し、
**人が tmux を知らなくても kiro-loop を運用できる状態**を作ること。具体的には:

1. **単一窓口の完成** — dashboard は既に agent-project / agent-flow / Cowork / amigos を
   束ねている。kiro-loop が加わることで定常ループも含めた全ワークロードを 1 画面で見渡せ、
   tmux は純粋な実行基盤に退く
2. **監視が「見に行く」から「見たい時に見える」へ** — 最終実行時刻・会話履歴が dashboard に
   出れば、異常検知のために attach する必要がなくなる
3. **復旧が実行制御と整合したまま GUI 化できる** — `kiro-loop send` はプロンプト名解決・
   busy 判定・スロット取得を内蔵している。dashboard からの復旧を生の `send-keys` でなく
   `send` 経由にすれば、GUI 操作が同時実行制御を壊さない

## 前提

| 側 | 実行環境 |
|----|----------|
| agent-dashboard（Electron） | **Windows** |
| kiro-loop / tmux / kiro-cli | **WSL** |

dashboard から tmux・kiro-loop CLI を触る経路は常に `wsl.exe -e …` を経由する。

## 2 レイヤ構成

| レイヤ | 見せるもの | 実現手段 |
|--------|-----------|---------|
| **構造化状態** | 最終実行時刻・状態（alive/busy）・会話履歴・復旧送信 | `loop-state/<pid>.json` の読み取り＋`kiro-loop send` への依頼 |
| **生画面** | 動いている tmux ペインそのもの | `capture-pane` ポーリング → attach（段階 A/B/C） |

普段は構造化状態ビューで足り、深掘りしたい時だけ生画面へ降りる —
dashboard の「概要から詳細へ」の思想に従う。

## 責務分担

| 面 | 役割 |
|----|------|
| **Cowork** | ジョブ一覧・設定同期・実行ボタン・ログ推定の状態 |
| **kiro-loop** | 稼働中ループの構造化状態・復旧送信・tmux の視聴（将来は attach 操作） |

UI 入口は Cowork 行の「実行状況」。実体実装は `features/kiro-loop` に閉じる。

**文言の方針**: 画面に tmux / セッション / プロンプト / capture-pane といった内部語を出さない。
定期プロンプト名は設定ファイル由来だと画面上で分かるよう「予定の名前」と呼び、
その旨を表の下に添える（名前だけを見て何のことか分からない状態を避ける）。

## 不変条件

**dashboard は kiro-loop の状態の書き手にならない。**
読むのはファイル（`loop-state/`・ログ・capture-pane）、操作は CLI（`send`）への依頼に限定する。
kiro-state 同期の単一書き手の不変条件（書き手を増やして状態共有が復旧不能になった実障害）と同型の防止線で、
agent-project 連携で dashboard が守っている構図（読み取り専用＋commands ドロップ）と揃える。

## 段階

### A. 生画面の視聴のみ（本 PoC）

- Main が `wsl.exe` 経由で `tmux capture-pane -p -t <target>` をポーリング
- Renderer はモノスペースの読み取り専用パネルに表示（**xterm / node-pty なし**）
- セッション解決: `tmux list-sessions` のうち `kiro-loop-*`、必要なら `#{pane_current_path}` またはパス digest で repo に紐付け

### B. インタラクティブ attach（次）

- Main に `node-pty`、Renderer に `xterm.js` + fit addon
- `wsl.exe -e tmux attach -t <session>`（または read-only の `new-session -t` グループ）
- IPC: `kiroLoop:ptyStart|ptyData|ptyInput|ptyResize|ptyKill`
- A の capture ポーリングは attach 中は止める

### C. 構造化状態ビューと復旧（操作統合）

- `loop-state/<pid>.json` を読み、ワークスペースごとの状態（alive / busy）と最終実行時刻を一覧表示
- 会話履歴: `capture-pane -S` の scrollback 取得から始め、必要なら送信・完了イベントの永続ログ化へ
- 復旧送信: ユーザーが dashboard から入力したプロンプト、または kiro-loop として動くべきだった
  定期プロンプト（プロンプト名指定）を `kiro-loop send` 経由で送る。busy 時は CLI が exit 1 で
  即時拒否するため、UI 側で「処理中につき送信待機」に変換する
- 入力・send-keys / Cowork「実行」との役割整理、多重 attach 禁止・フォーカス競合のルール

### kiro-loop 側のギャップ（C の前提となる小拡張）

| ギャップ | 現状 | 拡張 |
|---------|------|------|
| 最終実行時刻 | `write_state()` はデーモン単位の `updated_at` のみ | プロンプト単位の `last_sent_at`・結果を状態ファイルへ追記 |
| 会話履歴 | capture-pane は現在画面のみ | scrollback（`-S`）取得、または送信・完了イベントのログ化 |
| busy 時の送信 | `send` は即時 exit 1（人が待って再実行する設計） | dashboard 側で待機・再試行 UX に変換（kiro-loop は変えない） |

## IPC

| チャネル | 段階 | 用途 |
|----------|------|------|
| `kiroLoop:listSessions` | A | `{ repo? }` → セッション／ペイン一覧 |
| `kiroLoop:capture` | A | `{ target }` → 最新ペイン文本 |
| `kiroLoop:state` | C | `loop-state/*.json` の読み取り → ワークスペース状態・最終実行時刻 |
| `kiroLoop:send` | C | `{ session, prompt }` → `wsl.exe -e kiro-loop send` の実行と結果 |

## 非目標（A）

- キー入力・リサイズ・色（SGR）の完全再現
- node-pty / electron-rebuild
- kiro-loop Python 本体の変更

## 受け入れ

### A

- Windows dashboard + WSL で、稼働中 `kiro-loop-*` セッションの出力がパネルに更新される
- セッションが無いとき端末 UI を押しつけてこない（Cowork から開いたときだけ空状態を出す）
- `features/kiro-loop` にロジックが閉じ、`npm test` の関連テストが通る

### C

- tmux を開かずに、ワークスペースごとの最終実行時刻と稼働状態が dashboard で分かる
- 定期プロンプト・任意プロンプトを dashboard から `send` 経由で送れ、busy 時は待機として扱われる
- dashboard が kiro-loop の状態ファイル・tmux ペインへ書き込む経路が存在しない
