# VS Code Copilot Language Model Bridge

自作 CLI から、VS Code にログイン済みの Copilot モデルを公式の Language Model API
（`vscode.lm`）経由で呼ぶ最小構成です。`code chat` の UI 起動ではなく、回答を stdout に
返します。

```text
vscode-copilot-chat (Python CLI)
  └─ HTTP + Bearer token / 127.0.0.1
      └─ VS Code Extension
          └─ vscode.lm.selectChatModels({ vendor: "copilot" })
              └─ model.sendRequest(...) → stdout
```

## インストール

前提は VS Code、GitHub Copilot Chat、Python 3 です。

```bash
bash tools/vscode-copilot-chat/install.sh
# VS Code を完全に再起動
vscode-copilot-chat "このリポジトリを要約して"
```

初回リクエスト時に VS Code がモデル利用の同意を求める場合は許可してください。モデルが
見つからない場合は、Copilot Chat がインストール済み・サインイン済み・組織ポリシーで
許可済みか確認します。

標準入力と JSON 出力にも対応します。

```bash
vscode-copilot-chat < error.log
printf 'このエラーを説明して' | vscode-copilot-chat
vscode-copilot-chat --family gpt-4o --json "短く挨拶して"
```

## IPC 契約と安全性

拡張は `127.0.0.1` の OS 割り当てポートだけで待ち受け、起動ごとに 256-bit token を生成
します。接続情報は `~/.vscode-copilot-bridge.json` に mode `0600` で atomic に書き、CLI は
Bearer token を提示します。リクエスト上限は 1 MiB です。外部ホストには公開しません。

エンドポイントファイルは両プロセスで `VSCODE_COPILOT_BRIDGE_FILE` を設定すれば変更でき
ます。トークンを含むため、共有・コミットしないでください。

API は `POST /v1/chat` です。

```json
{"prompt":"質問", "family":"任意のモデル family"}
```

成功時は `{"text":"回答", "model":{"id":"...","family":"...","name":"..."}}` を
返します。これは **単発のモデル呼び出し**であり、VS Code Agent mode の built-in tools、
ファイル編集、ターミナル実行を自動的に利用するものではありません。それらが必要なら、
bridge 側で明示的にツールと承認フローを設計してください。
