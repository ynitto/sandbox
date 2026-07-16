# agent-dashboard: kiro-loop tmux 端末ビュー

> 日付: 2026-07-16  
> 対象: `tools/agent-dashboard/src/features/kiro-loop/`  
> 関連: Cowork（一覧・実行）・[`agent-dashboard-feature-split-design.md`](./agent-dashboard-feature-split-design.md)

## 前提

| 側 | 実行環境 |
|----|----------|
| agent-dashboard（Electron） | **Windows** |
| kiro-loop / tmux / kiro-cli | **WSL** |

dashboard から tmux を触る経路は常に `wsl.exe -e …` を経由する。

## 目的

Cowork が持つ「一覧・設定・ワンショット実行」に加え、**動いている kiro-loop の tmux を dashboard 内で見る**入口を `features/kiro-loop` に置く。

## 責務分担

| 面 | 役割 |
|----|------|
| **Cowork** | ジョブ一覧・設定同期・実行ボタン・ログ推定の状態 |
| **kiro-loop** | 生きている tmux の一覧・視聴（将来は attach 操作） |

UI 入口は Cowork 行の「端末」。実体実装は `features/kiro-loop` に閉じる。

## 段階

### A. 視聴のみ（本 PoC）

- Main が `wsl.exe` 経由で `tmux capture-pane -p -t <target>` をポーリング
- Renderer はモノスペースの読み取り専用パネルに表示（**xterm / node-pty なし**）
- セッション解決: `tmux list-sessions` のうち `kiro-loop-*`、必要なら `#{pane_current_path}` またはパス digest で repo に紐付け

### B. インタラクティブ attach（次）

- Main に `node-pty`、Renderer に `xterm.js` + fit addon
- `wsl.exe -e tmux attach -t <session>`（または read-only の `new-session -t` グループ）
- IPC: `kiroLoop:ptyStart|ptyData|ptyInput|ptyResize|ptyKill`
- A の capture ポーリングは attach 中は止める

### C. 操作統合（将来）

- 入力・send-keys / Cowork「実行」との役割整理
- 多重 attach 禁止・フォーカス競合のルール

## IPC（A）

| チャネル | 用途 |
|----------|------|
| `kiroLoop:listSessions` | `{ repo? }` → セッション／ペイン一覧 |
| `kiroLoop:capture` | `{ target }` → 最新ペイン文本 |

## 非目標（A）

- キー入力・リサイズ・色（SGR）の完全再現
- node-pty / electron-rebuild
- kiro-loop Python 本体の変更

## 受け入れ（A）

- Windows dashboard + WSL で、稼働中 `kiro-loop-*` セッションの出力がパネルに更新される
- セッションが無いとき端末 UI を押しつけてこない（Cowork から開いたときだけ空状態を出す）
- `features/kiro-loop` にロジックが閉じ、`npm test` の関連テストが通る
