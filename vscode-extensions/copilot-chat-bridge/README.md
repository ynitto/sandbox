# Copilot Chat Bridge (VS Code extension)

VS Code 上の Copilot Chat を **外部から操作する** ためのローカル HTTP API
を提供する拡張機能。`mcp-servers/copilot-chat-bridge` の MCP サーバーと
ペアで動作する。

## 動作概要

- 起動時に `127.0.0.1:52719` で HTTP サーバーを立てる（ポートは設定で変更可）。
- 2 つの経路を 1 つの HTTP API として束ねる:
  - **A. `vscode.lm.sendRequest`** で Copilot Chat が使う言語モデルを直接呼び、応答テキストを返す。
  - **B. `workbench.action.chat.*`** コマンドで VS Code の Chat パネルを開く / 新規セッション開始。
- すべてのリクエストはローカルホストにバインドされる。

## エンドポイント

| メソッド | パス                       | 経路 | 用途                                                     |
| -------- | -------------------------- | ---- | -------------------------------------------------------- |
| GET      | `/api/health`              | -    | ヘルスチェック + 既定 vendor / family / モデル数          |
| GET      | `/api/models`              | A    | `vscode.lm.selectChatModels` の結果                       |
| POST     | `/api/ask`                 | A    | prompt + (optional) system を投げて応答取得               |
| POST     | `/api/ask-with-context`    | A    | 上記 + エディタ選択 / 全文 / ファイル添付                 |
| POST     | `/api/open`                | B    | `workbench.action.chat.open` で Chat パネルを prefill     |
| POST     | `/api/new-session`         | B    | `workbench.action.chat.newChat` で /clear 相当            |

## 設定

| 設定キー                            | 既定値        | 説明                                                 |
| ----------------------------------- | ------------- | ---------------------------------------------------- |
| `copilotChatBridge.port`            | 52719         | リッスンポート                                       |
| `copilotChatBridge.defaultVendor`   | `copilot`     | `/api/ask` で vendor 未指定時の既定                  |
| `copilotChatBridge.defaultFamily`   | `""` (any)    | `/api/ask` で family 未指定時の既定                  |
| `copilotChatBridge.requestTimeoutMs`| 120000        | LM リクエストの既定タイムアウト（最大 600000）       |

## ビルド・インストール

```bash
cd vscode-extensions/copilot-chat-bridge
npm install
npm run compile     # `npx tsc` ではなく `npm run` を使うこと
npx --yes @vscode/vsce package --out copilot-chat-bridge.vsix
code --install-extension copilot-chat-bridge.vsix --force
```

VS Code を再起動するとバックグラウンドで起動する。
動作確認は `curl http://127.0.0.1:52719/api/health` で行う。

## 同意 / 認可

`vscode.lm.sendRequest` の初回呼び出し時、VS Code はワークスペースごとに
ユーザーへ同意ダイアログを表示する。同意が無い状態で `/api/ask` を呼ぶと
`502 + code:"NoPermissions"` を返す。これを避けるには `justification`
フィールドで「なぜ呼んでいるか」を人間に伝えると良い。

## MCP サーバーから利用する

`mcp-servers/copilot-chat-bridge/install.py` を実行すると、指定エージェントの
ユーザーレベル MCP 設定にこの拡張機能を呼び出す MCP サーバーが登録される。
詳細はそちらの README を参照。
