# エージェント CLI プラグインと失敗トリアージ — 設計書

> **対象系統**: `agent-project` / `agent-flow` / `agent-amigos` / `agent-loop` /
> `agent-dashboard` / `agent-audit`（CLI チャット・Doctor・cowork の tmux 実行を含む。
> `kiro-*` 旧系統は残置。改称方針は
> [`agent-tools-rename-design.md`](agent-tools-rename-design.md)）。

> 最終更新: 2026-08-09 ／ 関連: `schemas/agent-cli.schema.json`（契約の正典）,
> `agents/`（同梱定義）, `tools/agent-tools/agentcore/agentcore/agentcli.py`（Python ローダの 1 実装）。
> 対話モード拡張の判断記録は §2.1 に転記した。

エージェント CLI の呼び出しを**リポジトリ内で共通化したデータ契約**で差し替え可能にし、
あわせて失敗を**決定的にトリアージ**（誰が直すか分類）する仕組み。契約が覆う軸は
「CLI × モード（ヘッドレス / 対話）× 権限（書き込み可 / 読み取り専用）」の 3 つで、
**受入条件は「CLI の挙動・作法が変わったとき、修正が `agents/<name>.json` 1 ファイルで
完結すること」**。以下の設計判断はすべてこの 1 点から導いている。

## 1. 動機

- agent_cli は kiro / claude / copilot / codex のハードコード 4 択で、cursor / ollama / hermes
  などの CLI を足すには各ツールのコードを触る必要があった。未知の値は**黙って kiro-cli に
  落ちる**罠もあった（設定ミスに気づけない）。
- ハードコードは 1 か所ではなかった。「この CLI をどう起動するか」の知識が **8 か所・4 実装**
  （agent-project / agent-flow / agent-amigos の Python 3 実装 + dashboard の JS）に分かれ、
  しかも**同じ CLI でも用途によって argv が違う**（書き込みありの act と、助言のみの Doctor で
  権限フラグが別）。旧契約はヘッドレス片道・書き込み可の 1 マスしか表現できず、対話起動・
  読み取り専用フラグ・入力受付検出は各所のコード分岐に散っていた。CLI の作法が変わるたびに
  複数箇所の修正が要る。
- 失敗の扱いが「なんでもリトライ・なんでも人へ」だった。認証切れ・利用上限のような
  **環境要因**は、どのタスクをリトライしても同じ理由で落ちる（実際 codex の利用上限で
  26 ノードがリトライを焼き尽くし「理由不明の全滅」になった）。逆に一時的エラーまで
  人へ回すと判断待ちが濫造される。

## 2. プラグイン契約（データ契約・ローダは言語ごとに 1 実装）

- **正典**: [`schemas/agent-cli.schema.json`](../../schemas/agent-cli.schema.json)。
  1 CLI = 1 ファイル `agents/<name>.json`。`agent_cli: <name>`（グローバル / `agents:` の
  役割毎上書き）で使う。
- **組み込み CLI も定義ファイルにした**: kiro / claude / copilot / codex も `agents/` 同梱の
  JSON になり、コード側のフォールバックテーブルは**持たない**。持てば「JSON を直したのに
  古い挙動のまま」という二重管理が別の形で戻る。定義が見つからなければインストールの破損
  として明示エラーにする（`install.sh` 再実行の誘導つき）。
- **探索順**: `$KIRO_AGENTS_DIR` → `<プロジェクトルート>/agents/`（= 実行時 cwd）→
  `~/.agents/agents/` → `~/.kiro/agents/` → 同梱定義（リポジトリ直下 / パッケージ
  resources の `agents/`。最後の候補）。同名は先勝ちで、**組み込み名の予約は解除した**——
  上位に `claude.json` を置けば同梱定義を上書きできる。これが無いと受入条件
  （JSON 1 ファイルで完結）が成り立たない。ユーザー共通の定義は `~/.agents/agents/`
  だけを読み、旧 `~/.agent` ホームへはフォールバックしない。
- **ローダは言語ごとに 1 実装**: Python 側は `agentcore.agentcli`（load / headless_cmd /
  interactive_cmd / classify_error）に集約し、agent-project / agent-flow / agent-amigos が
  これを使う。当初の「各ツールが自前の小さなローダを持つ」方針は Python 側について取り下げた
  ——URL 正規化を 3 者が自前実装して吸収規則が食い違った前例と同型で、`{model}` 省略規則や
  空応答の扱いが実装ごとにずれると同じ定義ファイルがツールによって別の argv になる。
  dashboard（JS）だけは UI の応答性のため自前ローダのままとし、**同一定義に対して Python と
  同じ argv を返すことをゴールデンテストで固定**する。
- 定義できること:
  - **ヘッドレス片道**（従来）: argv（`{model}` / `{output_file}` プレースホルダ・末尾固定の
    `command_suffix`）・プロンプトの渡し方（stdin / argv）・モデルフラグと既定モデル・
    応答の取り出し（stdout / ファイル）・追加環境変数・タイムアウト・空応答の扱い・
    **エラー分類規則（errors）**。対応アダプターは本文を stdout、実測値を stderr の完全一致行
    `@agent-usage tokens_in=<整数> tokens_out=<整数>` へ分ける。消費側は最後の一致行だけを読み、
    無い・壊れている場合は従来どおり実行時間だけを記録する。
  - **headless の自律性（`headless_autonomy`）**: `command`（ヘッドレス）で 1 回起動したとき、
    その CLI が**自分でツールを回して仕事を完遂できるか**の申告。`tool-loop` は内部に探索・
    編集・コマンド実行のループを持つ（`claude -p` / `codex exec` / `kiro-cli chat
    --no-interactive` / `agent-ollama --tools bash`）ので、プロンプトを渡して 1 回呼べば終わる。
    `single-shot` は持たない——aider は渡されたファイルを編集するだけ、素の推論変種
    （`ollama-json` / `ollama-list`）はツールを持たないので、呼び出し側がツールループを
    供給しないと**着手すらしない**。未宣言は安全側の `single-shot`。
    分ける理由は、`interactive` の有無と自律性が**別の軸**だから。対話 CLI を tmux で
    自動運転する側が薄くて済んでいたのは、ツールループを CLI が内蔵していたからで、
    「対話できない = 使えない」ではない。この 1 ビットがあると、消費側は
    「1 回呼ぶ」か「ツールループを供給する」かを CLI 名で分岐せずに決められる。
  - **用途別の変種の申告（`variants`）**: 用途（role/kind/purpose 名。例
    `planner` `judge` `split` `verify` `retrieve`）ごとに、この定義の代わりに使う定義名の
    マップ。variant は「1 つのエージェント（例 `ollama`）を用途で使い分ける」実体を表す
    ——base 定義を選ぶだけで、用途ごとにどの起動形・どのモデルを使うかはこの申告に従って
    エンジンが自動で振り替える。知識を 2 つに割るのが要点で、**「この用途にはこの変種を
    使うか」は定義が申告し、「どの用途が振り替え対象か」はエンジンが宣言する**——どちらも
    自分が知っていることしか言わないので、エンジンが CLI 名で分岐する（`ollama` のときだけ
    挙動を変える）必要がなくなる。ツールループ前提の定義を JSON 契約の役割にそのまま使うと、
    本文の代わりに制御語だけが返って空応答で落ちるため、その組み合わせを人の設定作業に
    頼らず塞ぐのが目的（[agent-ollama-design.md](./agent-ollama-design.md) §2・
    コンセプト 柱3）。指す先が存在しない・自分自身を指す申告は無視して元の定義で走る
    （設定ミスで実行を殺さない）。振り替えは 1 段だけで連鎖させない。振り替え後のモデルは、
    呼び出し元が明示指定していなければ変種自身の `default_model` を使う（用途専用の
    チューニングを base のモデル指定で上書きさせないため。例: `verify` → `ollama-verify`・
    `gemma4:12b`）。
    エンジン側は自分が扱う用途の集合（例: agent-flow の `VARIANT_ELIGIBLE_ROLES`・
    agent-project の `JSON_CONTRACT_PURPOSES`）だけをこのマップへ問い合わせる——キーが
    存在するからといって、どの呼び出し元でも無条件に振り替わるわけではない（agent-project
    独自の `verify` purpose は寛容パーサ＋証跡の本文を伴うため対象外のまま）。
    出力がトップレベルの JSON **配列**でなければ下流が動かない役割（agent-flow の split）は、
    JSON モードがトップレベルをオブジェクトに固定するエンジン（ollama の `format: "json"`）
    では満たしようがないため、スキーマを渡せる別の変種を `split` キーで申告する。
  - **相対コスト（`relative_cost`）**: 同じ仕事 1 回の無次元値（ローカル=0、通常クラウド=1）。
    通貨やモデル別価格は持たず、宣言済み `fallbacks` から現在より高コストの最初の 1 件だけを
    内容失敗時の再試行候補にする。実行回数の上限は各エンジンが持ち、候補の選択だけを共通化する。
  - **権限の 2 モード**: 既定（書き込み可）にだけ付ける `write_args` と、助言のみにする
    `readonly_args` の対。`readonly: enforced | best-effort` で強制力を宣言する——このレイヤは
    argv を組み立てるだけで、フラグを無視する CLI への防御は持たない。保証できない CLI で
    読み取り専用を要求した呼び出しには警告が返り、画面が「助言のみを保証できません」と人に
    見せる。使い捨て用途にはセッション永続化を切る `no_session_args`。
  - **spill**: 長大プロンプトを一時ファイルへ退避し、短い指示（`{file}` 置換）と専用の権限
    フラグで読ませる方式。kiro-cli の「positional プロンプトを渡すと stdin を読まない」癖の
    データ化で、指示文が依存するツール名（`fs_read`）ごと JSON に移した。
    **「退避」には別物が 2 つある**（混ぜると壊れる）。定義の `spill`（この項）は退避時に
    権限フラグを `spill.args` で**置き換える**——「本文を読ませるためにファイル読み取りだけ
    許す」読み取り専用の用途に閉じた振る舞いで、dashboard の診断がこれを使う。もう 1 つは
    **argv 長制限（OS の `ARG_MAX`）の退避**で、`agentcore.agentcli.spill_prompt` が担い
    **権限フラグには触らない**。ヘッドレス実行（検証エージェント・分解・裁定）に前者を
    掛けると、退避したときだけコマンドを 1 つも実行できなくなり、検証は全基準
    「検証不能」に倒れる。見ているものが CLI の癖か OS の上限かで、層が違う。
    **2 つを寄せる案は採らない**（P2-5 の決着）——定義の `spill.instruction` は
    権限置換とセットの機構で、`spill_prompt` から使うと同じ穴を踏む。代わりに
    `spill_prompt` 側の指示文は `agentcore.agentcli.spill_instruction` が枠だけを持ち、
    呼び出し側は「何の全文か」だけを渡す（3 者が全文を自前で持つと、言い回しの改善が
    1 か所にしか入らず、入っていない方は誰も気付かない）。`spill_prompt` は `prompt_via` も
    見て、stdin 渡しの CLI では何もしない——「stdin は `ARG_MAX` に当たらない」は OS の
    事実で、この判定を呼び出し側 3 者に書かせるとまた 3 者でずれる。退避の閾値は設定
    `argv_limit`（既定 100000）で、agent-project / agent-flow / agent-amigos が同名のキーを
    持つ（OS とシェルの事情はノードごとに違ってよいので、ノード側で上書きできる層に置く）。
    退避しても OS の上限が閾値より低い環境では argv 長超過（`E2BIG`）が残るので、
    失敗トリアージはこれを env（環境要因）に分類する——素の `OSError` の文言は汎用パターンに
    掛からず「内容の問題」と読まれ、タスクのリトライ予算を焼いていた。
  - **`skill_command_prefix`**: 対話セッションへ送るテキストのうち、スキル起動コマンドの
    行頭記号（既定 `/`、`/skill-name` 形式）。codex はスキルを `$skill-name` で起動するため
    `$` を宣言する（`agents/codex.json`）。セッション開始コマンド（agent-session-commands）
    の chat モードなど、人が `/skill-name` と書いたテキストを CLI へ送る経路が
    `agentcore.agentcli.rewrite_skill_commands` 経由でこの宣言を見て行頭の `/` を差し替える
    ——既定 `/` の CLI では何も変えない。
  - **対話モード（`interactive`）**: 対話起動 argv・対話専用の `write_args` / `readonly_args`・
    入力受付を検出する正規表現（`ready_pattern` / `ready_timeout_sec`）・初回プロンプトの
    注入方法（`prompt_inject: send-keys | file`）。dashboard の CLI チャット・cowork の
    tmux 実行・対話診断が対象。**agent-loop も 2026-08-06 からこの契約に載った**——設定の
    `agent_cli` 指定時は `agent_loop/cliprofile.py` が agentcore.agentcli で定義を解決して
    ペインを起動する（未指定は従来の kiro-cli 固定経路のまま。設計:
    [`agent-loop-design.md`](./agent-loop-design.md) 機能 5）。旧 `agent-loop.py:_start_pane`
    だけが対象外として残る（kiro-cli 固定の残置系統）。
  - **待機判定（`busy_pattern` / `failure_pattern` / `idle_quiet_sec` / `clear_command`）**:
    tmux で CLI を自動
    運転する側（agent-loop の送信可否・スロット解放）は「待機中か処理中か」をペイン画面から
    判定するが、**その方法は CLI ごとに違う**。入力欄を出したまま処理する TUI（claude の
    `(esc to interrupt)` 等）では ready の消失が起きないため `busy_pattern`（処理中の正の
    検出）が判定の正になり、どちらのパターンも持てない CLI は `idle_quiet_sec`（画面が N 秒
    変化しなければ待機）で判定する。`failure_pattern` は `agent-loop send --wait` の明示的な
    失敗だけを宣言し、未指定なら pane / process 終了以外を推測しない。`clear_command` は
    コンテキスト破棄コマンドの差
    （kiro/claude=`/clear`、codex=`/new`、無い CLI は空文字宣言）を吸収する。
  - **セッションログ（`session_log`）**: agent-audit が CLI 自身の transcript を収集するための
    format / paths / usage / clean 規則。argv ローダの入力ではなく、ログの所在と読み方を定義側に
    置く宣言である。
- 未知の agent_cli で定義も無ければ**明示エラー**（黙るフォールバックは廃止）。
  例外は cowork の定常業務 tmux 実行（`cowork.js:coworkChatLaunch`）——定義解決に失敗しても
  `kiro-cli chat --trust-all-tools` へ落として定常業務を止めない。ただし黙ってはいない。
  フォールバック発動時は `console.warn` で理由（元エラーのメッセージ）を残す。
- 同梱定義: `agents/{kiro,claude,copilot,codex,cursor,aider,ollama,ollama-json,ollama-list,ollama-read,opencode}.json`。
  追加手順は
  [`agents/README.md`](../../agents/README.md)。

用途とフラグの対応は 1 枚に集約してある（ここがかつて実装ごとに食い違っていた）:

| 呼び出し元 | モード | readonly | no_session |
|---|---|---|---|
| act / plan / verify / worker / amigo の手番 | headless | ✗ | ✗ |
| dashboard の charter 補完・Doctor・構造化 Assist・受入条件補完 | headless | ✓ | ✓ |
| dashboard の CLI チャット・cowork の tmux 実行 | interactive | ✗ | ✗ |
| agent-loop の定期プロンプト（`session: keep`・既定） | interactive | ✗ | ✗ |
| agent-loop の定期プロンプト（`session: per-run` / `interactive` 無しの定義） | headless | ✗ | ✓ |
| 対話診断（失敗診断の既定） | interactive | ✓ | ✓ |

### 2.1 対話モード拡張の判断記録

契約の形に痕跡だけが残っている判断を書き残す。

- **`ready_pattern` は正規表現の文字列**。既存実装が `grep -qiE` でそのまま使えるので、新しい
  検出機構を作らない。未指定の定義は組み込みの既定パターンで動く（知らない CLI でも従来どおり）。
- **`prompt_inject` という名前**。当初案は `interactive.prompt_via` だったが、トップレベルに
  既にある `prompt_via: stdin|argv` と同名で enum だけ違う入れ子になり、誤設定が静かに
  効かないだけで気づけないので改名した。`file` 方式は当時どこにも実装が無かったが、対話診断が
  スナップショットを渡す経路として必須になるのが分かっていたので、後追いにせず先に契約へ入れた。
- **既定側をわざわざ `write_args` として分離した**。「`readonly_args` を足すだけ」で済まなかった
  のは、kiro の `--trust-all-tools`（書き込み可）と `--trust-tools=`（読み取り専用）が追加では
  なく排他で、両方並べると「後勝ち」に賭けることになるからだ。
- **`readonly_args` / `no_session_args` はトップレベルに置き、対話側は任意の上書き**。読み取り
  専用はヘッドレスの Doctor や構造化 Assist でも要るので、対話の中だけに置くと同じ知識を
  二度書くことになる。逆に `interactive.write_args` は継承しない。ヘッドレス用の強い権限フラグ
  （claude の `--dangerously-skip-permissions` 等）を対話へ黙って持ち込まないため、対話で要る
  ものは明示させる。
- **`command_suffix`**。codex の `-`（stdin から読む位置引数）は必ず末尾でなければならず、
  `command` に書くとモード別フラグとモデル指定が後ろへ回ってしまう。
- **spill の指示文は本文の置き換えではなく付け足し**。Doctor は役割と出力書式を argv 側に
  載せて本文だけファイルへ逃がすので、置き換えると役割ごと消える。
- **対話セッションの副作用は許容する**。対話は人が画面を見ながら操作するもので、副作用は
  人の責任範囲。制限すると CLI チャットや定常業務の現行用途が壊れる。例外は診断だけで、
  `readonly_args` + `no_session_args` で開き、tmux セッション名も `agent-doctor-` 接頭辞の
  別系統に分ける。読み取り専用のつもりの窓が作業セッションへ合流すると、そこから書き込みが
  できてしまうからだ。
- **`busy_pattern` を `ready_pattern` の拡張でなく別フィールドにした**（2026-08-06、agent-loop
  対応時）。「ready にマッチしない = 処理中」という従来の推論は、入力欄を常時表示する TUI では
  成立しない——ready は処理中もマッチし続ける。否定を 1 本の正規表現に押し込むより、
  「処理中の正の検出」を独立に宣言させて優先順位（busy ＞ ready ＞ 静穏 ＞ 既定 busy）を
  コード側で固定するほうが、定義の読み書きも判定の説明もまっすぐになる。
- **`headless_autonomy` は定義に申告させ、他フィールドから推測しない**（2026-08-11）。
  `interactive` の有無・`file_flag` の有無・`write_args` にツール系フラグがあるか、どれも
  相関はあるが根拠にはならない。推測で決めると、定義を足した人が意図と違う経路へ振り分けられ、
  しかもそれが実行時の「着手しない」という分かりにくい症状で出る。**未宣言は安全側の
  `single-shot`**——ツールループを余計に供給しても仕事は進むが、必要な供給を落とすと
  何も起きないまま静かに空振りする。
- **`interactive` の有無を「使えるかどうか」の判定に使わない**（2026-08-11）。かつて
  agent-loop は `interactive` 節の無い定義を起動時 fail fast にしていたが、対話セッションは
  **会話を人に見せるための可視化**であって実行の必須要件ではない。判定に使うべきなのは
  「対話 argv を組めるか」（= 経路の選択）であって、「この CLI が使えるか」ではない。
- **agent-loop は第二のローダを書かず agentcore を同梱する**。「ローダは言語ごとに 1 実装」
  （§4）を守るため、agent-loop の `install.sh` が zipapp へ agentcore を同梱し、リポジトリ
  直接実行は相対探索で import する。agentcore を解決できない環境では `agent_cli` 指定だけが
  使えず（明示エラー）、従来の kiro-cli 固定経路は影響を受けない。

## 3. 失敗トリアージ（決定的・LLM 不使用）

エラー本文（プラグインの `errors` → 汎用パターンの順）から「誰が直すか」を分類し、
メッセージ先頭の機械可読タグ **`[agent-error:<class>]`** で全層に運ぶ。

| class | 意味 | 誰が直すか | 各層の動き |
|---|---|---|---|
| `control` | 管理設定による実行停止（`[agent-control]`） | 人（dashboard で実行を許可） | 下記「環境要因」の扱い |
| `quota` | 利用上限 | 時間（またはプラン見直し） | 下記「環境要因」の扱い |
| `auth` | 認証切れ | 人（再ログイン） | 同上 |
| `env` | 実行環境（CLI 不在・モデル不正・argv 長超過） | 人（環境修復） | 同上 |
| `transient` | 一時的（タイムアウト・接続断） | 誰も（自動で解ける） | 通常リトライ |
| （タグ無し） | 内容の問題 | タスク単位の判断 | 従来どおり retry → 裁定 → 人 |

`quota` は、当面戻らない `exhausted` と復帰時刻を持つ `rate_limit` に細分する。後者は本文から
`reset_at` を決定的に抽出し、node-budget の観測行へ載せて復帰後の候補復帰に使う。

**実行制御・環境要因（control/quota/auth/env）の扱い** — 3 層が同じタグを読む
（表示上は「実行制御による停止」と「環境要因の失敗」を分けるが、打ち切り・needs 化の
仕組みは共通）:

1. **agent-flow**（`_continue` → `_env_failure_reason`）: 環境要因の失敗ノードが 1 つでもあれば
   **再計画せず run を即 failed で終端**（`meta.failure_reason` にタグ付き理由）。全ノードで
   リトライを焼き尽くす無駄を止める。done ノードは温存＝再開で続きから。
2. **agent-project**（`_settle_failure`）: vmsg と `last_run` の meta/final からタグを読み、
   **リトライを消費せず・裁定（これも LLM＝同じ理由で失敗する）も呼ばず**、原因と直し方を
   明記して needs へ。環境を直して approve すれば同じ run の続きから再開する。
3. **viewer**（`runAdvice`）: `failureReason` のタグを読み、タスク状態より先に
   「🔑 認証切れ — 再ログイン後、要対応タブで承認すると続きから再開」等を言い切る。

## 4. 不変条件

- 分類は**決定的**（正規表現のみ・LLM 不使用）。判定に迷うものはタグ無し＝「内容の問題」
  に倒し、従来のタスク単位フロー（retry → 裁定 → 人）に委ねる。ヒントは実際に一致した
  規則から採る（クラス一致で引くと、ある CLI に足した規則の案内文が別 CLI の失敗に付く——
  実際に踏んで直した潜在バグ）。
- トリアージは「止める・人へ知らせる」方向にのみ働く。done を作らない・予算を破らない
  （agent-project 設計書 §1 の不変条件に従属）。
- プラグインは stdlib（json/re）だけで読める。PyYAML 等の依存を増やさない
  （dashboard も同じ理由で JSON 定義のみを読む）。
- CLI の挙動・作法の変更は `agents/<name>.json` 1 ファイルの修正で完結する（受入条件）。
  読み取り専用の**防御**はこの層に持たない——責務が「argv の組み立て」から「実行の隔離」へ
  膨らむと、いま畳んだ散らばりが別の場所に再発する。保証できないことは宣言して人に見せる。

## 5. viewer の executor 連動（付随）

run の `meta.executor` を orchestrator が記録（`note_executor`）し、viewer は
`run.gitlabish`（executor==='gitlab'、旧 run は証跡から推定）で GitLab 連携 UI
（⟳ 最新化・関連イシュー・自動突き合わせ）を表示切替する。gitlab executor を
使っていない run に無意味なボタンを並べない。
