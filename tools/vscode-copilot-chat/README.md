# VS Code Copilot Language Model Bridge

自作 CLI から、VS Code にログイン済みの Copilot モデルを公式の Language Model API
（`vscode.lm`）経由で呼ぶ最小構成です。`code chat` の UI 起動ではなく、回答を stdout に
返します。**片道実行と対話（REPL）の両方**に対応します。

**macOS・Linux・WSL** で動きます。WSL のときだけ Windows 側の VS Code を起こし、
それ以外は同じ OS 上の VS Code を起こします。

```text
vscode-copilot-chat (Python CLI)   ← 会話履歴はここが持つ
  └─ HTTP + Bearer token / 127.0.0.1
      └─ VS Code Extension        ← 状態を持たない変換器
          └─ vscode.lm.selectChatModels({ vendor: "copilot" })
              └─ model.sendRequest(...) → stdout
```

## インストール

前提は VS Code、GitHub Copilot Chat、Python 3 です。

```bash
bash tools/vscode-copilot-chat/install.sh
vscode-copilot-chat --start "このリポジトリを要約して"
```

`--start` はカレントディレクトリを開く VS Code を起こします。**どちらの経路を使うかは
OS 名ではなく道具の有無で決めます**——`powershell.exe` と `wslpath` が両方あれば WSL、
無ければ同じ OS 上の VS Code です（WSL は Linux を名乗るので platform 名では分かれません）。

| | 起こし方 | `--user-data-dir` |
|---|---|---|
| macOS / Linux | `code --new-window <cwd>` を env 付きで直接実行 | `~/.vscode-copilot-bridge/user-data` |
| WSL | `wslpath -w` で Windows path へ変換し、PowerShell から実行 | `%LOCALAPPDATA%\vscode-copilot-bridge` |

どちらも**専用の `--user-data-dir`** を使います。既に起動中の VS Code へ接続してしまうと
port/token の環境変数が拡張へ届かないためです。CLI 自身が既定 port `32190` と生成した
token を保持するので、接続情報をどこかから探す必要はありません。port は `--port` で
固定できます。

専用プロファイルなので、ふだん使っている VS Code の拡張（MCP など）はこのウィンドウには
載りません。モデルを呼ぶだけならそれで足ります。

### macOS で `code` が見つからないとき

VS Code の「Shell Command: Install 'code' command in PATH」を実行していないと `code` は
PATH にありません。CLI は `/Applications/Visual Studio Code.app` と
`~/Applications/Visual Studio Code.app` の中も見にいくので、通常はそのままで動きます。
別の場所・Insiders などを使う場合は `--code-bin` で指定してください。

```bash
vscode-copilot-chat --code-bin '/path/to/code' --start "…"
```

`install.sh` が拡張を置くのは `~/.vscode/extensions` です。Insiders を使う場合は
`~/.vscode-insiders/extensions` へ手で置く必要があります。

インストーラが CLI を置く `~/.local/bin` が PATH に無ければその旨を表示します。

起動だけを行う場合と、同じbridgeへ続けて問い合わせる場合:

```bash
vscode-copilot-chat --start-only --start --port 32191
vscode-copilot-chat "次の質問"
```

初回リクエスト時に VS Code がモデル利用の同意を求める場合は許可してください。モデルが
見つからない場合は、Copilot Chat がインストール済み・サインイン済み・組織ポリシーで
許可済みか確認します。

## 対話モード

端末から引数なしで起動すると対話モードに入ります（`--interactive` / `-i` で明示指定も可）。
応答は届いた端から流れます。

```console
$ vscode-copilot-chat
vscode-copilot-chat 対話モード。/help でコマンド一覧、Ctrl-D で終了。
copilot> このリポジトリを要約して
（応答が逐次流れる）
copilot> さっきの要約を3行にして
```

| コマンド | 動作 |
|---|---|
| `/help` | コマンド一覧 |
| `/clear` | 会話履歴を捨てて新しい会話を始める |
| `/model [family]` | モデル family を表示・変更（引数なしで既定へ戻す） |
| `/exit` `/quit` | 終了（Ctrl-D も同じ） |

応答の途中で Ctrl-C を押すと、その手番を中断します（接続が切れると拡張側が
`CancellationToken` を落とすので、モデルも止まります）。プロンプトで押した場合は入力を
捨てるだけで、対話は続きます。**失敗・中断した手番は履歴に残しません**——壊れた文脈を
次の質問が引きずらないためです。

**パイプ入力は従来どおり片道実行のまま**です（標準入力が端末でなければ対話に入りません）。

```bash
vscode-copilot-chat < error.log
printf 'このエラーを説明して' | vscode-copilot-chat
vscode-copilot-chat --family gpt-4o --json "短く挨拶して"
```

## tmux から自動運転する

`agents/vscode-copilot.json` がこの CLI を[エージェント CLI プラグイン契約](../../agents/README.md)
へ載せているので、`agent_cli: vscode-copilot` と書けば agent-loop などが tmux 経由で
この対話モードを駆動できます。待機判定はプロンプト `copilot> ` を見ます。

```yaml
agent_cli: vscode-copilot
```

CLI 側の定数 `PROMPT` と定義の `interactive.ready_pattern` は対で、片方だけ変えると自動運転が
黙って「常に処理中」になります。`tools/agent-loop/test/test_cli_profile.py` の
`VscodeCopilotProfileTest` が両者の一致を固定しています。

## IPC 契約と安全性

拡張は `127.0.0.1` の OS 割り当てポートだけで待ち受け、起動ごとに 256-bit token を生成
します。接続情報は `~/.vscode-copilot-bridge.json` に mode `0600` で atomic に書き、CLI は
Bearer token を提示します。リクエスト上限は 4 MiB です（会話履歴を丸ごと送るため）。
外部ホストには公開しません。

エンドポイントファイルは両プロセスで `VSCODE_COPILOT_BRIDGE_FILE` を設定すれば変更でき
ます。トークンを含むため、共有・コミットしないでください。

API は `POST /v1/chat`・`GET /v1/tools`・`POST /v1/tool` の 3 つです。いずれも Bearer token が要ります。

**会話状態は CLI 側が持ち、拡張は毎回すべての手番を受け取る状態を持たない変換器**でいます。
bridge を再起動しても会話が消えず、複数の CLI セッションが 1 つの拡張を同時に使えます。
`POST /v1/chat` の本文は次のとおりです。

```json
{"messages":[{"role":"user","content":"質問"},
             {"role":"assistant","content":"前の回答"},
             {"role":"user","content":"続き"}],
 "family":"任意のモデル family",
 "stream":false}
```

`role` は `user` か `assistant` だけです。単発の `{"prompt":"質問"}` も受け付けます
（旧 CLI との互換）。

`stream` を省略すると `{"text":"回答", "model":{"id":"...","family":"...","name":"..."}}` を
返します。`stream: true` のときは `application/x-ndjson` で 1 行 1 イベントを流します。

```text
{"delta":"回"}
{"delta":"答"}
{"done":true,"model":{"id":"...","family":"...","name":"..."}}
```

最初の 1 片を書くまではヘッダを送らないので、モデル不在などの失敗は非ストリームと同じく
HTTP status 付きの JSON エラーで返ります。書き始めた後の失敗だけが `{"error":"..."}` 行に
なります。

これは **モデル呼び出し**であり、VS Code Agent mode の built-in tools、ファイル編集、
ターミナル実行を自動的に利用するものではありません。ただしツール自体は借りられます
——次節を参照。

## VS Code のツールを見る

`GET /v1/tools` は `vscode.lm.tools`（VS Code に今そのとき登録されているツール）を
そのまま返します。CLI からは `--tools` です。

```bash
vscode-copilot-chat --tools          # 名前・タグ・説明の 1 行目
vscode-copilot-chat --tools --json   # inputSchema を含む全文
```

並ぶのは 3 種類です。

| 出どころ | 例 |
|---|---|
| VS Code 本体 | `runSubagent` `run_in_terminal` `get_terminal_output` `runTests` `manage_todo_list` |
| Copilot Chat 拡張 | `copilot_readFile` `copilot_applyPatch` `copilot_replaceString` `copilot_searchCodebase` |
| VS Code に設定した MCP サーバ | 設定しだい |

**中身は VS Code のバージョン・設定・入れている MCP サーバで変わります。**
どのツールが使えるかを手元の一覧で決め打ちせず、この口で実測してください。

借りられないのは **agent skills** です（`chatSkills` は package.json の contribution point で、
列挙・実行の API は無い）。skill・instructions は素の Markdown なので、必要なら自分で
読んでプロンプトへ入れます。

## ツールを呼ぶ

`POST /v1/tool` は `vscode.lm.invokeTool` をそのまま通します。CLI からは `--call` です。
**何を渡すかはこちらでは決めません**——入力スキーマは VS Code が持っていて、検証も
VS Code が行います。ツールごとの知識をこの repo に置くと、環境差で必ず古くなります。

```bash
vscode-copilot-chat --call runSubagent                        # inputSchema を見る
vscode-copilot-chat --call runSubagent --input '{"prompt":"テストを直して"}'
echo '{"prompt":"…"}' | vscode-copilot-chat --call runSubagent --input -
```

`--input` を省くとそのツールの説明と `inputSchema` を表示します。まずこれを見てから
渡す JSON を決めてください。

呼び出しは chat request の外なので `toolInvocationToken` は `undefined` です。進捗 UI は
出ませんが**承認ダイアログは出ます**——ターミナル実行などはそこで人が止められます。

### 一覧に並んでいても呼べないツールがある

**`toolInvocationToken` を必須にしているツールは、この bridge からは呼べません。**
このトークンは「chat participant が chat request を処理している文脈」でしか手に入らず、
チャットの外から呼ぶ経路には存在しないためです。実機で確認できているのは
`runSubagent` がこれに当たることです。

```console
$ vscode-copilot-chat --call runSubagent --input '{"prompt":"…","description":"…"}'
vscode-copilot-chat: runSubagent は chat request の中からしか呼べません（…）
```

`--tools` に並ぶことと呼べることは別です。**どのツールがこの制約を持つかは `--probe`
で調べられます。**

```console
$ vscode-copilot-chat --probe
  呼べそう   copilot_readFile
      入力検証で止まった: bridge error (500): missing required property: filePath
  呼べない   runSubagent
      toolInvocationToken が要る
  未確認    runTests
      必須項目が無く、空入力で動きうるので試さない

呼べない 1 / 呼べそう 2 / 実行された 0 / 未確認 1
トークン検査は入力検証より先に出ることを確認済み。「呼べそう」はゲートされていないと読んでよい。
```

**なぜ副作用が出ないか。** `--probe` が送るのは `{}` だけで、しかも `required` が
空でないツールにしか送りません。必須項目がある以上 `{}` は入力検証で必ず落ちるので、
ツール本体は動きません。`runTests` のように**必須項目が無い**（引数なしで走りうる）
ツールは、試さずに「未確認」として残します。

**「呼べそう」をどこまで信じてよいか。** 判定の前提は「VS Code が入力検証より先に
トークンを見る」ことです。`--probe` はこれを実地で確かめます——不正な入力を送って
なおトークンのエラーが返るツールが 1 つでもあれば、順序が示せたことになります。
1 つも無い場合はその旨を出力に書き、「呼べそう」は『トークンで止まらなかった』以上の
意味を持たないものとして扱います。

確実なのは実際に有効な入力で 1 回呼んでみることです。`--probe` は当たりを付けるための
道具で、証明ではありません。

応答は text part を連結した `text` と、種別を残した `content` です。

```json
{"content":[{"type":"text","value":"…"},{"type":"other"}],"text":"…"}
```

`type: "other"` は prompt-tsx など文字列で受け取れない part です。黙って捨てると
空応答に見えるので種別だけ残します。

## エージェントへ丸投げする（現状は使えません）

> **この入口は今のところ動きません。** `runSubagent` は `toolInvocationToken` を必須に
> しており、チャットの外から呼ぶこの bridge には渡すものがありません。フラグと検査は
> 残していますが、実行すると上記のエラーで止まります。
>
> 動かすには拡張側に chat participant を登録し、チャット経由で受け取った本物の
> `toolInvocationToken` を使う必要があります（チャットパネルが経路に入ります）。

```bash
vscode-copilot-chat --agent "テストが落ちているので直して"
vscode-copilot-chat --agent "この repo の構造を調べて" --agent-name Explore
vscode-copilot-chat --agent - < task.md
```

| フラグ | 対応する項目 |
|---|---|
| `--agent TASK` | `prompt`（必須）。`-` で標準入力から読む |
| `--description TEXT` | `description`（必須）。省略時は依頼文の先頭 40 文字から作る |
| `--agent-name NAME` | `agentName`。省略時は VS Code の既定エージェント |

`--agent-name` には VS Code のカスタムエージェント名を渡す想定です（`.github/agents/`
に置いたもの）。ただし上記のとおり、この経路自体が今は通りません。

`model` を指定したいときは `--call runSubagent` で直接渡してください。

### `--agent` だけはツールの名前を知っている

このツールに限り、CLI が `runSubagent` という名前と 2 つの必須項目を知っています。
`--call` の「何も知らない」方針の例外なので、**送る前に実物のスキーマと突き合わせます**
（必須項目が増えた・名前が変わった、を検出したら送らずに `--call` を案内する）。
決め打ちが静かに壊れるのを防ぐためです。

エージェントの実行は長くかかります。既定の応答待ちは 300 秒なので、足りなければ
`--timeout` を伸ばしてください（切れると接続が落ち、VS Code 側もそこで止まります）。

## テスト

```bash
python3 -m pytest tools/vscode-copilot-chat/tests            # CLI
python3 -m unittest discover -s test                          # tmux 待機判定（tools/agent-loop で実行）
```
