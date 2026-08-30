# VS Code Copilot Language Model Bridge

自作 CLI から、VS Code にログイン済みの Copilot モデルを公式の Language Model API
（`vscode.lm`）経由で呼ぶ最小構成です。`code chat` の UI 起動ではなく、回答を stdout に
返します。**片道実行と対話（REPL）の両方**に対応します。

**macOS・Linux・WSL** で動きます。WSL のときだけ Windows 側の VS Code を起こし、
それ以外は同じ OS 上の VS Code を起こします。

```text
vscode-copilot (Python CLI)   ← 会話履歴はここが持つ
  └─ HTTP + Bearer token / 127.0.0.1
      └─ VS Code Extension        ← 状態を持たない変換器
          └─ vscode.lm.selectChatModels({ vendor: "copilot" })
              └─ model.sendRequest(...) → stdout
```

## インストール

前提は VS Code、GitHub Copilot Chat、Python 3 です。

```bash
bash tools/vscode-copilot/install.sh
vscode-copilot "このリポジトリを要約して"
```

**起動は要りません。** bridge へ繋がらなければ、カレントディレクトリを開く VS Code を
自動で起こして待ちます。既に動いていれば起こしません——同じ `--user-data-dir` で二重に
起こすと、2 つ目の拡張ホストが同じ port を掴めないためです。自動で起こしてほしくない
ときは `--no-start` を付けます（落ちていればそのまま失敗します）。

**入れ替えたときは bridge を一度閉じてください。** `install.sh` を再実行しても、動いている
拡張ホストは古いコードのままです。CLI は動いている bridge を使い回すので（二重起動を
避けるため）、新しい拡張が載るのはそのウィンドウを閉じて次に起こしたときです。

**どちらの経路で起こすかは OS 名ではなく道具の有無で決めます**——`powershell.exe` と
`wslpath` が両方あれば WSL、無ければ同じ OS 上の VS Code です（WSL は Linux を名乗るので
platform 名では分かれません）。

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
vscode-copilot --code-bin '/path/to/code' "…"
```

`install.sh` が拡張を置くのは `~/.vscode/extensions` です。Insiders を使う場合は
`~/.vscode-insiders/extensions` へ手で置く必要があります。

インストーラが CLI を置く `~/.local/bin` が PATH に無ければその旨を表示します。

先に立ち上げておきたい場合（初回の待ちを問い合わせから外したいとき）は `--start-only`
です。使える状態になるまで待ってから終わります。

```bash
vscode-copilot --start-only --port 32191
vscode-copilot --port 32191 "次の質問"
```

`--start` は互換のために受け付けますが、自動起動が既定になったので要りません。

初回リクエスト時に VS Code がモデル利用の同意を求める場合は許可してください。モデルが
見つからない場合は、Copilot Chat がインストール済み・サインイン済み・組織ポリシーで
許可済みか確認します。

## 対話モード

端末から引数なしで起動すると対話モードに入ります（`--interactive` / `-i` で明示指定も可）。
応答は届いた端から流れます。

```console
$ vscode-copilot
vscode-copilot 対話モード。/help でコマンド一覧、Ctrl-D で終了。
copilot> このリポジトリを要約して
（応答が逐次流れる）
copilot> さっきの要約を3行にして
```

| コマンド | 動作 |
|---|---|
| `/help` | コマンド一覧 |
| `/clear` | 会話履歴を捨てて新しい会話を始める |
| `/model [family]` | モデル family を表示・変更（引数なしで既定へ戻す） |
| `/tools [SETS]` | ツールを表示・変更（`/tools read,write`、`/tools off` で素の会話） |
| `/exit` `/quit` | 終了（Ctrl-D も同じ） |

### 対話でツールを使う

**既定の対話はツールを持ちません**（素のモデル呼び出しなので、リポジトリを読めません）。
道具を持たせるには起動時のフラグか `/tools` を使います。

```console
$ vscode-copilot -i --write
copilot> src/a.py を読んで、この関数名を直して
  → copilot_readFile {"filePath":"…"}
  → copilot_replaceString {"filePath":"…"}
直しました。  （3 往復）
copilot> /tools off
tools: off（素の会話に戻ります）
```

`-i --agent-tools read` のように種類を指定することもできます。`/tools` の名前は**その場で**
VS Code の一覧と突き合わせます——次の手番まで黙っていると、打ち間違いに気づくのが
1 往復ぶん遅れるためです。

**履歴は手元が持ちます。** ツール手番でも会話全体を毎回送るので、道具を途中で入れても
切っても文脈は続きます。往復の中身（ツール呼び出しと結果）は手番の中で閉じ、履歴には
最後の本文だけが残ります。

`agent-herd chat vscode-copilot` から入った対話も同じです。

応答の途中で Ctrl-C を押すと、その手番を中断します（接続が切れると拡張側が
`CancellationToken` を落とすので、モデルも止まります）。プロンプトで押した場合は入力を
捨てるだけで、対話は続きます。**失敗・中断した手番は履歴に残しません**——壊れた文脈を
次の質問が引きずらないためです。

**パイプ入力は従来どおり片道実行のまま**です（標準入力が端末でなければ対話に入りません）。

```bash
vscode-copilot < error.log
printf 'このエラーを説明して' | vscode-copilot
vscode-copilot --family gpt-4o --json "短く挨拶して"
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

API は `POST /v1/chat`・`GET /v1/tools`・`POST /v1/tool`・`POST /v1/agent` の 4 つです。いずれも Bearer token が要ります。

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
vscode-copilot --tools          # 名前・タグ・説明の 1 行目
vscode-copilot --tools --json   # inputSchema を含む全文
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
vscode-copilot --call runSubagent                        # inputSchema を見る
vscode-copilot --call runSubagent --input '{"prompt":"テストを直して"}'
echo '{"prompt":"…"}' | vscode-copilot --call runSubagent --input -
```

`--input` を省くとそのツールの説明と `inputSchema` を表示します。まずこれを見てから
渡す JSON を決めてください。

**`required` が欠けている入力は送りません。** VS Code は入力を検証せずツールへ渡すので、
欠けたまま送るとツール本体が `undefined` を掴んで動きます。判定に使うのは VS Code が
配るスキーマだけで、ツールごとの知識は持ちません。

呼び出しは chat request の外なので `toolInvocationToken` は `undefined` です。進捗 UI は
出ませんが**承認ダイアログは出ます**——ターミナル実行などはそこで人が止められます。

### 一覧に並んでいても呼べないツールがある

**`toolInvocationToken` を必須にしているツールは、この bridge からは呼べません。**
このトークンは「chat participant が chat request を処理している文脈」でしか手に入らず、
チャットの外から呼ぶ経路には存在しないためです。実機で確認できているのは
`runSubagent` がこれに当たることです。

```console
$ vscode-copilot --call runSubagent --input '{"prompt":"…","description":"…"}'
vscode-copilot: runSubagent は chat request の中からしか呼べません（…）
```

`--tools` に並ぶことと呼べることは別です。

### 実測で分かっていること

| ツール | chat の外から | 根拠 |
|---|---|---|
| `runSubagent` | **呼べない** | `toolInvocationToken is required for this tool` |
| `copilot_readFile` | **呼べる** | 有効な入力で呼び、ファイルの中身が返った |
| `copilot_applyPatch` `copilot_createFile` `copilot_createDirectory` `copilot_editNotebook` `copilot_createNewJupyterNotebook` `copilot_fetchWebPage` `copilot_createNewWorkspace` | 呼べる | トークンのエラーが返らず、ツール本体まで到達した |

`copilot_*` 系の編集・読み取りツールはゲートされていません。**`vscode.lm` の tool calling を
自前で回す形は成立します。**

### 自動で総当たりしてはいけない

**VS Code は入力をツールへ渡す前に検証しません。** `inputSchema` に `required` があっても、
`{}` を渡すとそのままツール本体が動きます（多くは引数が `undefined` のまま自分のコードで
落ちますが、**落ちる前に副作用を起こすものがあります**）。実際、`copilot_createNewWorkspace`
に空入力を渡したところワークスペースが開き、拡張ホストごと bridge が落ちました。

かつてここに `--probe`（空入力で総当たりして判定する）を置いていましたが、この前提が
誤っていたため撤去しました。**安全な自動判定はありません。**

調べるときは `--call` で **1 つずつ・有効な入力で・そのツールが実際に動くと理解した上で**
呼んでください。読み取り専用のもの（`copilot_readFile` など）から試すのが安全です。

応答は text part を連結した `text` と、部品ごとの `content` です。

```json
{"content":[{"type":"text","value":"…"},
            {"type":"other","value":{"node":"…"}}],
 "text":"…"}
```

`type: "other"` は prompt-tsx など文字列で受け取れない部品です。**JSON にできる範囲で
中身も返します**（循環参照などで JSON にできないときだけ `value` が落ちます）。

**本文は prompt-tsx の中からも取り出します。** `copilot_readFile` のように結果を
prompt-tsx で返すツールがあり、素朴に文字列部品だけを見ると「成功したのに空」に
なります。木を辿ってテキストノードを出現順に連結し、`text` に載せます。

```console
$ vscode-copilot --call copilot_readFile --input '{"filePath":"…","startLine":1,"endLine":20}'
# Agent Skills

AIエージェント（GitHub Copilot / Claude Code）の能力を拡張するスキル集。
…
```

各テキストノードは自分の改行を含むので、連結だけで元の本文が戻ります
（`lineBreakBefore` を見て改行を足すと二重になります）。降りるのは `children` と
`node` だけです——木を無差別に舐めると `references` の中など本文でない場所の
`text` まで拾います。

それでもテキストが 1 つも取れなければ、標準出力へは何も出さず、標準エラーへ理由と
`--json` を案内します。空行を出すと「空文字が返った」と紛らわしいためです。

## エージェント（ツールを使わせて解かせる）

`POST /v1/agent` は、VS Code のツールをモデルに持たせてループを回します。CLI からは
`--agent` です。

```bash
vscode-copilot --agent "この repo の構造を調べて"
vscode-copilot --agent - < task.md
```

```console
$ vscode-copilot --agent "テストの置き場を調べて"
  → copilot_findFiles {"query": "**/test_*.py"}
  → copilot_readFile {"filePath": "…"}
  （3 往復）
テストは tools/<名前>/tests に置かれています。…
```

途中経過（どのツールを何で呼んだか）は**標準エラー**へ、最終的な答えは**標準出力**へ
出ます。パイプに乗るのは答えだけです。何をしているか見えないまま数分黙るのが一番
困るので、往復は逐次出します。

**ツール本体も承認ダイアログも VS Code のもの**です。ここが持っているのは「どのツールを
呼ぶか決めさせて、結果を返して、また訊く」というループだけで、ファイルを読む・書く・
探すの実装は 1 つも持ちません。

### 既定は読み取り専用

持たせるツールは**明示した名前だけを通す allowlist** です。

```text
copilot_readFile        copilot_listDirectory   copilot_findFiles
copilot_findTextInFiles copilot_searchCodebase  copilot_searchWorkspaceSymbols
copilot_readProjectStructure  copilot_findTestFiles
copilot_getChangedFiles copilot_getErrors
```

**VS Code に新しいツールが増えても勝手には入りません。** 読み取り専用という約束を、
名前を数える側（除外リスト）ではなく載せる側（許可リスト）で守るためです。allowlist に
あって VS Code に無いものは黙って外れます。

### 用途で持たせ替える

`--agent-tools` はカンマ区切りで、**セット名とツール名を混ぜて**書けます。

| セット | 中身 |
|---|---|
| `read`（既定） | 上の 10 個 |
| `write` | `copilot_applyPatch` `copilot_replaceString` `copilot_createFile` `copilot_createDirectory` |
| `run` | `run_in_terminal` `get_terminal_output` `runTests` |
| `web` | `copilot_fetchWebPage` |

```bash
vscode-copilot --agent-tools read,write --agent "この関数名を直して"
vscode-copilot --agent-tools read,run  --agent "落ちているテストを調べて"
vscode-copilot --agent-tools read,copilot_replaceString --agent "…"
```

**`--agent-tools` は既定に足すのではなく置き換えます。** 書き込みだけ渡すと、モデルは
読めないまま直そうとします。`read,write` のように読む側も一緒に書いてください。

セットも allowlist のままです。次の 2 つはどのセットにも入れていません。

- `copilot_createNewWorkspace` … 空入力で実行され、ワークスペースが開いて拡張ホストごと
  落ちました（実測）。「今のリポジトリで作業する」という用途と噛み合いません。
- `runSubagent` … `toolInvocationToken` を要求するのでこの bridge からは呼べません。

どちらも名指しでなら渡せます。止めているのは、カテゴリを頼んだだけで付いてくることです。
MCP サーバの道具も環境ごとに違うので、セットには括らず名指しにしています。

**名指しとセットで扱いが違います。** 名前で書いたものが VS Code に無ければ失敗します
——頼んだ道具を使わないエージェントになるより、無いと言われるほうがましです。セットは
カテゴリの依頼なので、環境に無いものは黙って外します（`run` を頼んだのに `runTests` が
無いだけで止まっては困ります）。

書き込み・実行系は**そのツールが実際に動きます**。承認ダイアログが必ず止めてくれると
当てにはしないでください——`copilot_createNewWorkspace` は誰にも止められず動きました。
git が綺麗な状態で試すのが安全です。

### ファイルを編集させる

`--write` を付けるとツール既定が `read,write` になり、`--file` / `--read` で対象を渡せます。

```bash
printf 'この関数名を直して\n' | vscode-copilot --write --file src/a.py --read docs/spec.md
```

**権限を決めるのは `--write` であって `--file` ではありません。** `--file` は「どれが対象か」を
示すだけで、`--write` が無ければ読むだけです。ハーネスは読み取りの手番でも `--file` を
渡してくるので、ここを取り違えると読むだけの手番で書き込みツールが載ります。

パスは絶対に直してモデルへ渡します。VS Code のツールはワークスペース相対のパスを
受け取らないためです。渡した依頼文の前には次が付きます。

```text
編集してよいファイル（これ以外は書き換えない）:
- /abs/path/src/a.py
参考（読むだけ。書き換えない）:
- /abs/path/docs/spec.md
```

**提示していないツールは実行しません。** モデルが提示外の名前を返しても、拡張が
invoke せずに `tool error` としてモデルへ返します。allowlist を「渡す側」だけで守ると、
読み取り専用の手番で書き込みツールが動きえます（スタブで実際に起きました）。

### agent-herd のハーネス engine として使う

`agents/vscode-copilot.json` が `write_args` / `file_flag` / `read_flag` を宣言しているので、
`agent-herd harness` の engine に指定できます。

```bash
agent-herd harness run --agent-cli vscode-copilot ...
```

`headless_autonomy` は `single-shot` です。ハーネスが `read_files` / `write_files` / `run` /
`final` の契約を供給し、`write_files` の手番だけ `--write` が付きます。

```text
readonly=False → vscode-copilot --write --read spec.md --file a.py
readonly=True  → vscode-copilot         --read spec.md --file a.py
```

**bridge のワークスペースと作業ディレクトリがずれていると噛み合いません。** 拡張は
最初に起こしたときの cwd をワークスペースとして開きます。絶対パスを渡すので読み書きは
できますが、`copilot_searchCodebase` のような探索は別のワークスペースを見ます。別の
リポジトリで使うときは bridge を閉じてから起こし直してください。

### 送った形を見る（`--debug`）

VS Code の変換の向こうで 400 が返るとき、手前で持っていた形が唯一の手がかりです。
`--debug` を付けると、毎往復その形を標準エラーへ出します（本文は出しません——依頼文が
丸ごとログへ残らないよう、長さと部品の種類だけ）。

```console
$ vscode-copilot -i --write --debug
copilot>   [debug] 往復 2  user:string(4) / assistant:string(6) / user:string(95)
           / assistant:LanguageModelToolCallPart(c1:copilot_applyPatch)
           / user:LanguageModelToolResultPart(c1:LanguageModelTextPart)
```

**既知の未解決:** 履歴のある対話でツールを呼ぶと、次の往復が
`messages with role 'tool' must be a response to a preceeding message with 'tool_calls'`
で 400 になることがあります。履歴の無い単発（`--agent`）では同じ形が通るので、
先頭に付く `assistant:string` が効いている疑いがありますが、確かめられていません。
当たったときは `/clear` で履歴を捨てると通ります。

### ループの作法

- **ツールの失敗はモデルへ返します。** 落として黙ると同じ呼び出しを繰り返すだけです。
  失敗も `! ツール名: 理由` として画面に出ます
- **手番は呼び出しを Assistant 側・結果を User 側へ積みます**（`callId` で対応が付く）。
  片方だけ積むと次の往復でモデルが文脈を失います
- 既定 12 往復で打ち切ります。答えに至らなければ失敗として返します
- 実行は長くかかります。既定の応答待ちは 300 秒なので、足りなければ `--timeout` を
  伸ばしてください（切れると接続が落ち、VS Code 側もそこで止まります）

`--json` を付けると、最終的な答え・往復数・使ったツール・イベントの全記録が出ます。

## テスト

```bash
python3 -m pytest tools/vscode-copilot/tests            # CLI
python3 -m unittest discover -s test                          # tmux 待機判定（tools/agent-loop で実行）
```
