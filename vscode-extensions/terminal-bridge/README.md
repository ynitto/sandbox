# Terminal Bridge (VS Code extension)

VS Code 統合ターミナルをローカル HTTP API として公開する拡張機能。
ペアになる MCP サーバー (`mcp-servers/terminal-bridge`) を介し、Claude
Code / GitHub Copilot / Codex / Kiro などの MCP クライアントから VS Code
内のターミナルを操作できる。

## 動作概要

- 起動時に `127.0.0.1:52718` で HTTP サーバーを立てる（ポートは設定で変更可）。
- `vscode.window.onDidStartTerminalShellExecution` を購読し、ターミナル名ごとに
  リングバッファ（既定 200 行）へ出力をキャプチャする。
- すべてのリクエストはローカルホストにバインドされる。外部からは到達できない。

## エンドポイント

| メソッド | パス                     | 用途                                       |
| -------- | ------------------------ | ------------------------------------------ |
| GET      | `/api/health`            | ヘルスチェック・キャプチャ済みターミナル一覧 |
| GET      | `/api/terminals`         | 開いているターミナル一覧                    |
| GET      | `/api/output?terminal=X` | キャプチャ済み出力                          |
| POST     | `/api/execute`           | コマンド実行（shellIntegration 必須）       |
| POST     | `/api/send`              | テキスト送信                                |
| POST     | `/api/create`            | ターミナル作成                              |
| POST     | `/api/close`             | ターミナル閉鎖                              |
| POST     | `/api/wait-for-output`   | 出力に対する正規表現マッチ待機              |

## 設定

| 設定キー                            | 既定値 | 説明                          |
| ----------------------------------- | ------ | ----------------------------- |
| `terminalBridge.port`               | 52718  | リッスンポート                |
| `terminalBridge.captureBufferLines` | 200    | ターミナルごとの保持行数      |

## ビルド・インストール

```bash
cd vscode-extensions/terminal-bridge
npm install
npm run compile
npx --yes @vscode/vsce package --out terminal-bridge.vsix
code --install-extension terminal-bridge.vsix --force
```

VS Code を再起動するとバックグラウンドで起動する。
動作確認は `curl http://127.0.0.1:52718/api/health` で行う。

## MCP サーバーから利用する

`mcp-servers/terminal-bridge/install.py` を実行すると、ユーザー設定の
MCP 設定ファイルにこのブリッジを呼び出す MCP サーバーが登録される。
詳細はそちらの README を参照。
