# Terminal Bridge MCP Server

VS Code 統合ターミナルを MCP ツールとして公開する。`vscode-extensions/terminal-bridge`
拡張機能とペアで動作する。

## 構成

```
┌────────────────────────┐  stdio JSON-RPC  ┌─────────────────────────┐  HTTP/127.0.0.1:52718  ┌──────────────────────────┐
│ MCP クライアント       │ ───────────────▶ │ server.py (Python)      │ ─────────────────────▶ │ VS Code 拡張機能         │
│ (Copilot Chat / Claude │                  │ FastMCP → HTTP ブリッジ │                        │ vscode-terminal-bridge   │
│  Code / Codex / Kiro)  │                  │                         │                        │ → vscode.window.terminals│
└────────────────────────┘                  └─────────────────────────┘                        └──────────────────────────┘
```

## 公開ツール

| ツール                  | 概要                                                            |
| ----------------------- | --------------------------------------------------------------- |
| `list_terminals`        | 開いているターミナル一覧 (index/name/processId 等)              |
| `execute_in_terminal`   | shell integration 経由でコマンド実行・出力取得                  |
| `send_to_terminal`      | テキスト送信（プロンプト応答や非インテグレーション端末向け）    |
| `read_terminal_output`  | キャプチャ済みリングバッファの取得                              |
| `create_terminal`       | 新しいターミナルを作成（初期コマンドと cwd を指定可）           |
| `close_terminal`        | ターミナルを閉じる                                              |
| `wait_for_output`       | 出力に対する正規表現マッチを最大 2 分まで待機                   |
| `bridge_health`         | ブリッジ到達性チェック                                          |

## インストール

```bash
# 1. このディレクトリで実行
python install.py --agent copilot      # GitHub Copilot (VS Code ユーザー設定) に登録
python install.py --agent claude       # Claude Code (~/.claude/.mcp.json) に登録
python install.py --agent codex        # Codex
python install.py --agent kiro         # Kiro
python install.py --all                # 全部
python install.py                      # 自動検出
python install.py --skip-extension     # 拡張機能のビルド・インストールを省略
```

インストーラの動作:

1. `vscode-extensions/terminal-bridge` を `npm install && npm run compile && vsce package` で
   ビルドし、`code --install-extension` で導入する（`--skip-extension` で省略可）。
2. `server.py` を `~/.mcp-servers/terminal-bridge/server.py` にコピー。
3. 指定エージェントのユーザーレベル設定 (`mcp.json` / `.mcp.json`) にエントリをマージ。

## 環境変数

| 変数                       | 既定         | 説明                                          |
| -------------------------- | ------------ | --------------------------------------------- |
| `TERMINAL_BRIDGE_HOST`     | `127.0.0.1`  | ブリッジホスト                                |
| `TERMINAL_BRIDGE_PORT`     | `52718`      | ブリッジポート（拡張機能の設定と揃えること）  |
| `TERMINAL_BRIDGE_TIMEOUT`  | `180`        | HTTP タイムアウト（秒）                       |

## 動作確認

VS Code を再起動した後、

```bash
curl http://127.0.0.1:52718/api/health
# {"status":"ok","terminals":N,"capturedTerminals":[...]}
```

が返れば拡張機能側 OK。MCP クライアントを再起動するとツール一覧に
`terminal-bridge.*` が出現する。

## トラブルシューティング

- **`Cannot reach Terminal Bridge`**: VS Code が起動していないか、拡張機能が
  まだロードされていない。VS Code の出力パネル "Terminal Bridge" を確認する。
- **`execute_in_terminal` が `shell integration is not available`**: 対象端末で
  shell integration が有効化されていない。`code --version` が 1.93 以降であることと、
  対応シェル (bash / zsh / pwsh / fish) で起動していることを確認する。
- **VSCode 拡張機能が起動しない**: `View → Output → Terminal Bridge` でログを確認。
  ポート 52718 が既に使われている場合は `terminalBridge.port` を変更する。
