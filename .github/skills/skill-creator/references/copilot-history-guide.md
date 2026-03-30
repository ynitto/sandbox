# VSCode Copilot 履歴ガイド

VSCode Copilot のチャット履歴の構造と取得方法の詳細。

---

## ストレージの場所

| OS | workspaceStorage パス |
|---|---|
| macOS | `~/Library/Application Support/Code/User/workspaceStorage/` |
| Linux | `~/.config/Code/User/workspaceStorage/` |
| Windows | `%APPDATA%\Code\User\workspaceStorage\` |

各サブディレクトリはワークスペースをハッシュ名で表す。`workspace.json` でプロジェクトパスを確認できる:

```bash
cat ~/.config/Code/User/workspaceStorage/<hash>/workspace.json
# → {"folder": "file:///home/user/myproject"}
```

---

## データ形式（新形式: chatSessions/）

VSCode 1.90 以降では `chatSessions/` ディレクトリに JSON ファイルが作成される:

```
workspaceStorage/<hash>/
└── chatSessions/
    ├── <session-id>.json
    └── <session-id>.json
```

各 JSON ファイルの構造例:

```json
{
  "requests": [
    {
      "message": {
        "text": "テストを追加してください",
        "parts": [{"text": "テストを追加してください"}]
      },
      "timestamp": 1700000000000,
      "response": [...]
    }
  ],
  "creationDate": 1700000000000,
  "lastMessageDate": 1700000000100
}
```

ユーザーメッセージは `requests[].message.text` から取得する。

---

## データ形式（旧形式: state.vscdb フォールバック）

`chatSessions/` が存在しない場合、`state.vscdb`（SQLite）を参照:

```bash
# 内容確認
sqlite3 ~/.config/Code/User/workspaceStorage/<hash>/state.vscdb \
  "SELECT value FROM ItemTable WHERE key = 'interactive.sessions';" | python3 -m json.tool | head -100
```

`extract-copilot-history.py` は自動的にフォールバックを試みる。

---

## スクリプトのオプション早見表

```bash
# 全ワークスペース、過去90日
python extract-copilot-history.py --days 90 --noise-filter

# 特定プロジェクト、過去30日
python extract-copilot-history.py --workspace "my-project" --days 30

# カスタムストレージパス（VS Code Insiders 等）
python extract-copilot-history.py \
  --storage "~/Library/Application Support/Code - Insiders/User/workspaceStorage"

# セッション数を制限
python extract-copilot-history.py --max-sessions 20
```

---

## セキュリティ注意事項

- APIキー・トークン等の秘密情報が含まれる場合はマスクして出力する
  - 例: `export API_KEY=sk-xxx...` → `export API_KEY=<masked>`
- ユーザー固有パスは `~/` に置換する
- 生のセッション内容をそのままスキルにコピーしない
