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

## 実装上の特徴

[Zenn 記事「Windows勢だけどcmuxのターミナル操作機能が羨ましいので、VSCode拡張＋MCPで再現してみた」](https://zenn.dev/kozoka_ai/articles/d92e42e33368ad)
で語られている設計判断のうち、本実装でも踏襲しているもの・改善したものを記す。

- **2層構成 (VS Code 拡張 + MCP サーバー)**: VS Code 拡張機能は VS Code プロセス内
  でしか動作せず、MCP サーバーは MCP クライアント (Claude Code / Copilot 等)
  に子プロセスとして起動される。両者を繋ぐためにループバック HTTP (port 52718)
  を IPC として使う。
- **ランタイム外部依存ゼロ**: VS Code 拡張は Node.js 組み込みの `http` のみで
  HTTP サーバーを立てる。`package.json` の `dependencies` は空、`devDependencies`
  に TypeScript と型定義のみ。
- **MCP サーバーは Python + FastMCP**: 上流 Zenn 記事の Node 実装が苦労した「MCP
  プロトコルのフレーミング (Claude Code は仕様外の NDJSON で送ってくる)」「プロト
  コルバージョンのエコーバック」「レスポンス id のエコーバック」を SDK が吸収する。
  本リポジトリの他の MCP サーバー (`mcp-servers/gitlab`) と同じ言語に揃えるねらい
  もある。
- **リングバッファ (既定 200 行/ターミナル)**: 長時間動くサーバーログがメモリを
  食い潰さないよう、ターミナル名ごとに最新行のみ保持する。`OutputBuffer` クラスは
  チャンク境界が行末に揃わない場合も継ぎ目を正しく扱う (上流のフラット実装より堅牢)。
- **ツール description にワークフロー知識を埋め込む**: 「サーバー起動は
  `send_to_terminal` + `wait_for_output` を組み合わせる」「別エージェントへ
  送るときは改行を含めない」など、cmux の "skill" 相当の指針を `server.py`
  のツール定義そのものに書き込み、CLAUDE.md / `.copilot-instructions.md`
  に頼らず適切に使い分けてもらう。
- **段階的デバッグを支える3点セット**:
  1. `bridge_health` ツール (MCP 経由) / `curl http://127.0.0.1:52718/api/health`
     で疎通確認。
  2. VS Code のコマンドパレットから `Terminal Bridge: List terminals` /
     `Terminal Bridge: Show status` を叩いて、ターミナル一覧と拡張機能のログを
     出力パネルで確認。
  3. それでもダメなら出力パネル「Terminal Bridge」の `[capture]` / `[http]` ログ。
- **ポート・バッファ行数を VS Code 設定で変更可**: `terminalBridge.port` /
  `terminalBridge.captureBufferLines`。複数 VS Code ウィンドウを並べる場合や、
  既に 52718 を使う何かがいる環境向けの逃げ道。

## 既知の罠

- **改行を含む `send_to_terminal` で別エージェントの入力欄が壊れる**: 別ターミナル
  で動いている Claude Code / Codex などの対話 CLI に送るとき、`text` に `\n` を
  含めると入力欄が中途半端な状態になる。本文と「送信 (`\n`)」を別々の
  `send_to_terminal` に分けること。`server.py` の `send_to_terminal`
  description に明記済み。
- **`npx tsc` は別人**: npm に `tsc` v2.0.4 という無関係のパッケージがあり、
  `npx tsc` だとそちらが解決されることがある。本リポジトリは `npm run compile`
  経由でローカル `./node_modules/.bin/tsc` を呼ぶので問題ないが、手動ビルド時は
  注意。
- **`.mcp.json` のパスが環境依存になりがち**: DevContainer の中で書いた絶対パス
  が WSL ホスト側で存在しない、といった事故が起きやすい。`install.py` は
  `~/.mcp-servers/terminal-bridge/server.py` を `$HOME` 起点で生成し、
  **ユーザーレベル設定** (Claude → `~/.claude/.mcp.json` / VS Code User
  `mcp.json` / Kiro → `~/.kiro/settings/mcp.json`) に書き込むことでこの罠を回避。
- **shell integration の遅延起動**: `create_terminal` 直後は
  `hasShellIntegration` が `false` のことがある。ブリッジは 500ms 待ってから
  応答するが、それでも反映されない場合は数百 ms 後に `list_terminals` を再取得
  すれば良い。

## トラブルシューティング

- **`Cannot reach Terminal Bridge`**: VS Code が起動していないか、拡張機能が
  まだロードされていない。VS Code の出力パネル "Terminal Bridge" を確認する。
- **`execute_in_terminal` が `shell integration is not available`**: 対象端末で
  shell integration が有効化されていない。`code --version` が 1.93 以降であることと、
  対応シェル (bash / zsh / pwsh / fish) で起動していることを確認する。
- **VSCode 拡張機能が起動しない**: `View → Output → Terminal Bridge` でログを確認。
  ポート 52718 が既に使われている場合は `terminalBridge.port` を変更する。

## 出典

設計の出発点となった記事:
[Windows勢だけどcmuxのターミナル操作機能が羨ましいので、VSCode拡張＋MCPで再現してみた (Zenn, kozoka_ai)](https://zenn.dev/kozoka_ai/articles/d92e42e33368ad)

本実装は上記記事の **仕様** (HTTP API 形・ツール一覧・port 番号) のみを参考に、
コードはゼロから独自実装している (上流 TypeScript MCP サーバーをそのまま取り
込むのではなく、Python FastMCP で書き直したのは前述のフレーミング問題回避と
本リポジトリ既存パターンに合わせるため)。
