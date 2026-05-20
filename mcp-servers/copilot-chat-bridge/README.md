# Copilot Chat Bridge MCP Server

VS Code 上の Copilot Chat を **外部から操作する** ための MCP サーバー。
`vscode-extensions/copilot-chat-bridge` 拡張機能とペアで動作し、Claude
Code / Codex CLI / 別ターミナルの自分自身など、別の MCP クライアントから
VS Code 内の Copilot Chat（およびそれが使う言語モデル）に問い合わせできる。

## 構成

```
┌────────────────────────┐  stdio JSON-RPC  ┌─────────────────────────┐  HTTP/127.0.0.1:52719  ┌──────────────────────────┐
│ MCP クライアント       │ ───────────────▶│ server.py (Python)      │ ─────────────────────▶│ VS Code 拡張機能         │
│ (Claude Code / Codex / │                  │ FastMCP → HTTP ブリッジ │                        │ copilot-chat-bridge      │
│  別ターミナル等)       │                  │                         │                        │ ├─ vscode.lm.sendRequest │
│                        │                  │                         │                        │ └─ workbench.action.chat │
└────────────────────────┘                  └─────────────────────────┘                        └──────────────────────────┘
```

## 2 つのアクセス経路

ハイブリッド構成: 同じ拡張機能 + MCP サーバーで両方の経路を公開している。

| 経路                            | 用途                                                 | 応答取得 | UI 履歴 |
| ------------------------------- | ---------------------------------------------------- | -------- | ------- |
| **A. `vscode.lm.sendRequest`**  | Copilot が使う LLM をプログラム的に呼ぶ              | ✅       | ❌      |
| **B. `workbench.action.chat.*`** | Chat パネルに prompt を流し込む / 新セッション開始   | ❌       | ✅      |

## 公開ツール

| ツール                       | 経路 | 概要                                                   |
| ---------------------------- | ---- | ------------------------------------------------------ |
| `list_chat_models`           | A    | 利用可能な chat モデル一覧 (id / vendor / family)      |
| `ask_copilot`                | A    | プロンプトを投げて応答テキストを取得                   |
| `ask_copilot_with_context`   | A    | エディタ選択範囲 / 全文 / 任意ファイルを context として添付 |
| `open_chat`                  | B    | Chat パネルを開いて prefill (履歴に残したいとき)       |
| `new_chat_session`           | B    | Chat を /clear して新セッション開始                    |
| `bridge_health`              | -    | ブリッジ到達性チェック                                 |

## インストール

```bash
# このディレクトリで実行
python install.py --agent claude       # Claude Code (~/.claude/.mcp.json) に登録
python install.py --agent copilot      # GitHub Copilot (VS Code ユーザー設定) に登録
python install.py --agent codex        # Codex
python install.py --agent kiro         # Kiro
python install.py --all                # 全部
python install.py                      # 自動検出
python install.py --skip-extension     # 拡張機能のビルド・インストールを省略
```

インストーラの動作:

1. `vscode-extensions/copilot-chat-bridge` を `npm install && npm run compile && vsce package` でビルドし、`code --install-extension` で導入する（`--skip-extension` で省略可）。
2. `server.py` を `~/.mcp-servers/copilot-chat-bridge/server.py` にコピー。
3. 指定エージェントのユーザーレベル設定 (`mcp.json` / `.mcp.json`) にエントリをマージ。

## 環境変数

| 変数                              | 既定         | 説明                                          |
| --------------------------------- | ------------ | --------------------------------------------- |
| `COPILOT_CHAT_BRIDGE_HOST`        | `127.0.0.1`  | ブリッジホスト                                |
| `COPILOT_CHAT_BRIDGE_PORT`        | `52719`      | ブリッジポート                                |
| `COPILOT_CHAT_BRIDGE_TIMEOUT`     | `600`        | HTTP タイムアウト（秒）                       |

## 動作確認

VS Code を再起動した後、

```bash
curl http://127.0.0.1:52719/api/health
# {"status":"ok","defaultVendor":"copilot",...}
```

その後、MCP クライアントから `bridge_health` → `list_chat_models` →
`ask_copilot` の順で叩くと初回だけ VS Code 内で同意ダイアログが出る。
「Allow」を選ぶと以降は無確認で通る。

## 主な使い分け

### 別エージェントに Copilot を呼ばせて回答を取り戻す

Claude Code 等から `ask_copilot` で投げる。応答テキストが MCP 経由で返るので、
そのままパイプライン処理可能。Chat UI 履歴には残らないため「裏で副次的に
LLM を使う」用途に向く。

```text
ask_copilot(prompt="この diff のリスクを箇条書きで挙げて", 
            family="gpt-4o")
```

### 人間に Copilot Chat の入力欄を渡す

`open_chat(query="...", is_partial_query=true)` で Chat パネルにドラフトを
流し込む。人間が確認・編集してから送信する想定。応答は読み戻せない（VS Code
が拡張機能に Chat 出力を公開していないため）。

### コンテキストクリアして新規セッション開始

`new_chat_session()` で `workbench.action.chat.newChat` を実行。SDD ワーク
フローで「要件固め → /clear → 実装開始」のような切り替えに使う。

## 既知の罠

- **初回 consent ダイアログ**: `vscode.lm.sendRequest` は VS Code 上で
  ワークスペースごとに一度同意が必要。同意が無いと `NoPermissions` で 502
  を返す。`justification` 引数で「なぜ呼んでいるか」を人間に説明できる。
- **Copilot 未契約環境**: vendor=`copilot` のモデルは GitHub Copilot 契約が
  必要。契約が無いと `list_chat_models` の結果が空になる。`vendor=null` を
  渡して他プロバイダ (もしあれば) にフォールバックすると良い。
- **`open_chat` の応答は取れない**: VS Code が拡張機能に Chat 出力を公開
  していない仕様上、B 経路で応答テキストは取得不能。読み戻したいなら A 経路
  (`ask_copilot`) を使う。
- **`mode` パラメータの解釈はビルド依存**: `workbench.action.chat.open` の
  `mode` (`ask`/`edit`/`agent`) は比較的新しいビルドでのみ尊重される。古い
  VS Code では無視される（エラーにはならない）。
- **VS Code Insiders / 別プロファイル**: install.py は安定版の `Code/User/`
  にのみ書き込む。Insiders や別プロファイルを使っている場合は、出力された
  mcp.json を該当プロファイルのディレクトリへコピー or リンクする。

## トラブルシューティング

- **`Cannot reach Copilot Chat Bridge`**: VS Code が起動していないか、拡張機能が
  まだロードされていない。VS Code の出力パネル "Copilot Chat Bridge" を確認。
- **`no matching chat model is available`**: vendor / family の組み合わせに
  該当するモデルが無い。`list_chat_models` で実際に登録されているモデルを
  確認する。
- **502 + `code: "NoPermissions"`**: 同意ダイアログが拒否された／まだ出ていない。
  VS Code 上でコマンドパレットから一度 Copilot Chat を使うか、再度 `ask_copilot`
  を呼んでダイアログを再表示する。
