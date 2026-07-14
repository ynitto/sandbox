# agent-stack（agent-project / agent-flow 制御面）

agent-dashboard のうち、**agent-project** と **agent-flow** を可視化・操作する部分。

## 置き場

| パス | 役割 |
|------|------|
| `config.js` | 既定設定（`projects` / `agent`） |
| `main/` | プロジェクト読取・flow バス・操作・オーサリング・IPC |
| `preload.js` | `window.api` に載せるメソッド工場 |
| `index.js` | feature 記述子（`features/index.js` から読まれる） |

UI（タブ・概要／バックログ／要対応／実行／履歴）は当面 `src/renderer/` に同居する。
タブ要素には `data-feature="agent-stack"` を付け、他制御面と区別できるようにしてある。

## 境界

- **入ってよい**: agent-project / agent-flow のファイル契約・CLI・バス
- **base に残す**: Electron 起動、git 同期、汎用 GitLab API、シェルオープン
- **触らない**: `src/features/kiro-loop/`（別制御面）

上流の agent-dashboard を取り込むときは、主にこのディレクトリと `src/base/`・`src/renderer/` をマージする。
