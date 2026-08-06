# opencode × ollama（CPU 推論）が実用にならない問題 — 原因の構造と対策案

> 作成 2026-08-06
> 対象: `tools/opencode` / `agents/opencode.json` / `agents/ollama.json` / `agentcore/ollama_adapter.py`
> 症状: ollama（qwen3.5:9b・CPU 実行、CPU 内蔵 GPU は未使用）+ opencode で、
> 簡単なプロンプトに対して応答が 10 分以上返らない。不安定。
> 既に試したこと: スキル・instructions の削減、think モードの無効化 → **改善せず**。
> 制約: ハードウェアは変えられない。ゴールは agent-tools シリーズの `agent_cli` として使えること。

---

## 0. 結論の先出し

**ボトルネックは生成（decode）ではなく、入力の読み込み（prefill）と冷起動である。**
そして prefill の大半を占めるのは、利用者が削れる部分（スキル・instructions・ユーザープロンプト）
ではなく、**opencode がエージェントハーネスとして毎リクエスト注入する固有分**
（組み込みシステムプロンプト + 全ツールの JSON スキーマ + 環境情報。目安 1〜2 万トークン）である。

だから「スキルを消す」「think を切る」が効かなかった。前者は総量の 1〜2 割しか削れず、
後者は出力トークンを減らすだけで prefill には 1 トークンも効かない。

CPU での 9B dense モデルの prefill はおおむね毎秒数十トークン。1.5 万トークンの初期
コンテキストなら**本文を 1 文字も生成する前に数分〜10 分近く消える**。さらにエージェント
ループはツール呼び出しのたびに会話全体を再送するので、キャッシュに乗らなければ倍々で焼ける。
症状と定量的に一致する。

**したがって根本対策は「opencode を速くする」ことではなく「このハードで opencode に
やらせる仕事を変える」ことになる（§2 案 A）。** opencode の固有価値はツール実行つきの
エージェントループであり、それは構造的に巨大プロンプト × 複数ラウンドを意味する。
CPU 単体の 9B dense とは成立しない組み合わせで、README が最初から「推論は**別 PC の
GPU 機**に投げる」設計にしていたのはこのためである。

### 0.1 位置づけ — クラウド CLI のバックアップ実行系（本対応のコンセプト）

本対応で作るものは「速いローカル推論」ではない。**クラウドサービスとしての
エージェント CLI がガバナンスや予算の事情で使えなくなったときに、agent-tools
シリーズの作業を止めないためのバックアップ実行系**である。この位置づけから
要件が 2 つに定まる:

- **R1 — 止めない。** 品質・応答速度などの非機能要件は犠牲にしてよい。
  agent-tools 契約（headless / readonly / write / interactive / usage 実測 /
  エラー分類）に完全適合し、`agent_cli` の差し替えだけで全エンジンから使えること。
  品質的な問題は**後からの試行でリカバリーする**——agent-flow の verify /
  evaluator / 再計画と agent-audit の台帳（実測 usage・transcript）がその材料に
  なる。ローカル産の成果には実測ログが残るので、クラウド復帰後に再実行すべき
  対象を台帳から特定できる。
- **R2 — 逐次監視できる。** バックアップ運転では 1 呼び出しが数十分かかることが
  正常になる。だから**「長時間の停止（に見える状態）を異常と扱わない証拠」を
  常時出せる**ことが要件である。失敗検知は壁時計タイムアウトではなく
  **無進捗（stall）ベース**へ寄せる——「遅い」は正常、「進んでいない」だけが異常。
  証拠の実体は F-2 の JSONL 進捗ログ（タイムスタンプ付き・追記のみ）。

将来の接続先も既にある: 実行プロファイルの決定的自動選択（2026-08-05 Phase1
設計・案 D）は「枠が枯れていない CLI の最初の候補を採る」設計なので、候補列の
最後にローカル CLI を置けば**「クラウドの枠が枯れたら自動でバックアップへ」が
宣言だけで書ける**。本書の範囲では切替は手動（設定変更）とする。

**決定事項（2026-08-06）**: work 経路の主案は F-2（agent-ollama への統合、
`--tools` で write モードにのみループ）。TUI は既定 ANSI 素書き +
`install.sh --with-rich` のオプトイン同梱（補遺 1 の推奨どおり）。スキルは
明示・遅延読み込み（補遺 2）+ agent-loop 連携プロパティ（同補遺）。

---

## 1. まず計測して確定させる（すべての案の前提）

どの案が効くかは prefill / 冷起動 / decode の内訳で決まる。ollama は 1 リクエストで
全部返してくれるので、curl 一発で確定する:

```bash
curl -s http://localhost:11434/api/generate -d '{
  "model": "qwen3.5:9b",
  "prompt": "1+1は?",
  "stream": false,
  "options": {"num_predict": 32}
}' | python3 -c 'import json,sys; d=json.load(sys.stdin); \
print({k: d.get(k) for k in ("load_duration","prompt_eval_count","prompt_eval_duration","eval_count","eval_duration")})'
```

読み方（duration はナノ秒）:

| 大きい値 | 意味 | 効く対策 |
|---|---|---|
| `load_duration` | モデルの冷起動 | keep_alive を延ばす（案 C） |
| `prompt_eval_duration` ÷ `prompt_eval_count` が遅い | prefill が律速 | プロンプト総量を減らす（案 A/D）か prefill を速くする（案 B） |
| `eval_duration` ÷ `eval_count` が遅い | decode が律速 | モデルを軽くする（案 C） |

同じことを **opencode 経由**でも 1 回やり、`prompt_eval_count` を比べる。素の curl で数十、
opencode 経由で 1 万超なら、この文書の診断がそのまま裏づけられる
（`agent-opencode` は stderr に `@agent-usage tokens_in=…` を出すので、そこでも読める）。

あわせて ollama のサーバログで `truncating input prompt` を探す。**サーバ側の文脈長
（`OLLAMA_CONTEXT_LENGTH` / `num_ctx`）が opencode の初期コンテキストより小さいと、
システムプロンプトが黙って切り捨てられ、指示を失った応答が返る**——「不安定」の正体は
高確率でこれか、呼び出し側タイムアウト（agent-project 既定 300s）による途中 kill の混在である。

---

## 2. 対策案

### 案 A — 役割で CLI を分ける（推奨・構造的な解・設定変更のみ）

agent-tools がローカル推論に落としたい役割（agent-audit の extract / 要約 / 表題 / advise 系）は
**単発の text → text** であり、ツール実行を必要としない。これには既にある
`agent_cli: ollama`（`agent-ollama` → `/api/generate` 直叩き）を使う。プロンプトは
agent-tools が組んだ分だけ（数百〜数千トークン）になり、opencode 固有の 1〜2 万トークンが
**丸ごと消える**。CPU でも 1〜2 分で返る計算になり、実用域に入る。

```yaml
# agent-audit.yaml — ローカルに落とすのは単発系だけ。既定 CLI はクラウドのまま
agents:
  extract: {agent_cli: ollama, model: qwen3.5:9b}
# agent-flow.yaml / agent-project.yaml の書き込み系役割には opencode を割り当てない
```

opencode（＝ツール実行つきループ）は、README の設計どおり**別 PC の GPU 機が使える構成に
限定**する。GPU 機が無い間、書き込み系の役割はクラウド CLI に残す。

- 利点: 即日適用できる。エンジン・スキーマの改修ゼロ。opencode の導入物も無駄にならない
  （GPU 機が来たらそのまま使う）。
- 欠点: ローカルだけで「ファイルを書くエージェント」は持てない。ただしそれは CPU 9B では
  どの道成立していないので、失うものは実質ない。

### 案 B — iGPU を prefill に使う（ハード追加なしの高速化）

「ハードは変えられない」が、**iGPU は既にある**。重要なのは効き方の非対称性:

- decode はメモリ帯域律速。iGPU は CPU と同じ DDR を共有するので**ほぼ速くならない**。
- prefill は計算律速。iGPU に載せると**数倍〜10 倍級で速くなる**ことが多い。
  今回のボトルネックは prefill なので、ピンポイントに効く。

経路は iGPU のベンダーで変わる:

| iGPU | 経路 |
|---|---|
| AMD APU（Radeon 680M/780M 等） | ollama の ROCm + `HSA_OVERRIDE_GFX_VERSION`、または ollama の実験的 Vulkan バックエンド |
| Intel（Iris Xe / Arc iGPU） | ipex-llm 配布の ollama、または llama.cpp（SYCL / Vulkan）の `llama-server` |

llama.cpp の `llama-server` は OpenAI 互換なので、opencode 側は provider の baseURL を
差し替えるだけで済む（`tools/opencode/install.sh --ollama-host` がそのまま使える）。
BIOS/UMA の共有メモリ割り当てがモデルサイズに足りているかは要確認。

- 利点: 案 A と直交。併用すれば opencode 本体の起動待ちも縮む。
- 欠点: 効果と安定性がチップ・ドライバ依存。§1 の計測で prefill 律速を確定させてから着手する。

### 案 C — モデルとサーバ設定を CPU 向きにする（案 A の土台）

1. **keep_alive を延ばす。** 既定 5 分。agent-tools の呼び出し間隔が 5 分を超えると毎回
   冷起動（= `load_duration` 数十秒〜数分）からやり直す。`OLLAMA_KEEP_ALIVE=1h` など。
2. **プレフィックスキャッシュに乗せる。** `OLLAMA_NUM_PARALLEL=1` でスロットを 1 本にすると
   直前リクエストと共有する接頭辞の再計算が消える。agentcore の promptcompose（安定
   プレフィックス化・2026-08-05 Phase1 設計 §3）はクラウドのキャッシュだけでなく
   **ollama のこのキャッシュにもそのまま効く**——注入順が毎回同じなら接頭辞が一致するから。
3. **KV キャッシュを軽くする。** `OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0` で
   KV メモリ半減。32k 文脈の KV が RAM を圧迫してスワップに入ると桁で遅くなる（「たまに
   異常に遅い」のもう一つの正体候補）。
4. **モデルを CPU 向きに選ぶ。** dense 9B は CPU には重い。RAM が許すなら
   MoE（例: qwen3 系 30B-A3B、アクティブ約 3B）が dense 9B より数倍速く、ツール呼び出しも
   強い。RAM が厳しければ dense 4B 級へ。**要約・抽出用途なら 4B 級で足りることが多く**、
   `install.sh --small-model` の口も既にある。
5. **文脈長は用途に対して必要最小限に。** ただし opencode で使う限り初期コンテキストが
   1〜2 万トークンあるので 16k 未満には下げられない（下げると §1 の切り捨てで不安定化する）。
   「短くもできず、長くすれば遅くなる」——これ自体が opencode × CPU の不成立の傍証である。

### 案 D — それでも opencode を通すなら、痩せた専用エージェントを定義する

opencode の設定でヘッドレス専用のエージェントを定義し、ツールを全部止める:

```json
{
  "agent": {
    "headless": {
      "tools": {"bash": false, "edit": false, "write": false, "webfetch": false,
                 "glob": false, "grep": false, "read": false},
      "permission": {"edit": "deny", "bash": "deny", "webfetch": "deny"}
    }
  },
  "mcp": {}
}
```

ツールスキーマ分の prefill が消え、ループも 1 ラウンドで終わるので体感は大きく変わる。
`agents/opencode.json` の `readonly_args` / `write_args` をこの軽量エージェント経由に
差し替えれば agent-tools からもそのまま乗れる。

ただしこれは **opencode を agent-ollama に近づける行為**である。opencode に残る固有価値
（ツール実行）を自分で捨てているので、素直に案 A で agent-ollama を使う方が確実で、
可動部品も少ない。位置づけは「opencode の導入・ログ収集経路（opencode-sqlite）を
どうしても使い続けたい場合の妥協案」。

### 案 E — agent-ollama の小改修（案 A の補強・任意）

案 A を主経路にするなら、`ollama_adapter.py` に足りないものが 2 つある:

1. `options` を渡す口が無い（`num_ctx` / `keep_alive` / `temperature`）。環境変数
   （例 `OLLAMA_OPTIONS` の JSON）で `/api/generate` の body に合流させる。
   サーバ全体の環境変数をいじらずにリクエスト単位で keep_alive・文脈長を制御できる。
2. think 系モデルの思考抑制。qwen3 系は生成が `<think>` ブロックを含むことがあり、
   単発 text→text 用途では出力を焼くだけなので、options（またはプロンプト規約）で止める。
   ※ decode 側の節約なので効果は補助的。主犯は prefill である（§0）。

どちらも標準ライブラリのみ・数十行の追加で、既存の CLI 契約（stdin → stdout +
`@agent-usage`）は変えない。

### 案 F — opencode を使わない軽量ツール実行（work 経路の代替）

opencode の 1〜2 万トークンは「汎用エージェントハーネスの値段」であって、ツール実行
そのものの値段ではない。**ツールを 1〜3 個に絞れば、prefill は 1/10 以下になる。**
agents/<名前>.json のプラグイン契約（`prompt_via` / `write_args` / `readonly_args` /
`spill` / `errors`）は CLI を選ばないので、軽い実行系をそのまま差せる。3 系統ある。

#### F-1 — ワンショット・パッチ方式（最軽量・推奨）

エージェントループを**やめる**。1 リクエストで「対象ファイルの内容を渡す → SEARCH/REPLACE
ブロック（または unified diff）を返させる → アダプターが決定的に適用する」。

```
prefill の内訳（目安）:
  役割・出力契約         ~500 tokens
  タスク（flow-worker）  ~500-1,000
  対象ファイル本文       ~2,000-3,000（scope 契約 ≤30 行変更の前提なら数ファイル）
  合計                   ~3,000-4,500  →  CPU prefill でも 1-2 分、decode 込みで 2-4 分/ノード
```

**iGPU なしの純 CPU でも work ノードが実用域に入る**のがこの案だけの特長。agent-flow の
`granularity` スコープ契約（変更 ≤30 行・対象が計画時に決まる）と噛み合っており、
「探索が要らない程度に小さく割る」前提を planner が既に作ってくれている。適用失敗
（SEARCH 不一致）は `format_retries`（レイヤ 2）と同型の 1 回修復で拾う。

実装は `agent-ollama-patch`（仮）: stdin のタスクから対象ファイル指定を読み、本文を
inline して `/api/chat` を 1 回叩き、返ったブロックを適用して変更サマリを stdout へ。
標準ライブラリのみ・200 行前後。`agents/ollama-patch.json` を足すだけで agent-flow の
`agents: work:` から使える。欠点: モデルが探索できないので、対象が計画時に特定できない
タスク（「原因を探して直せ」型）には向かない——それは opencode（クラウド/GPU 機）に残す。

#### F-2 — 最小エージェントループ（bash 1 ツール）

mini-swe-agent が実証した形: ツールは **bash 1 個だけ**、システムプロンプト ~1,000
トークン、ツールスキーマすら使わずテキスト規約（コードブロック = 実行コマンド）で回す。
探索が必要なタスクも扱える一方、ラウンドごとに再 prefill が乗る（`OLLAMA_NUM_PARALLEL=1`
の接頭辞キャッシュで差分だけにできる）。既製の mini-swe-agent を薄いアダプターで
契約に載せるか、`/api/chat` の tools で read_file / apply_edit / run_command の 3 つだけ
持つ同型を自作する（スキーマ ~800 トークン）。位置づけは F-1 と opencode の中間。

#### F-2 の実装形態 — ヘッドレス + デバッグ TUI の 2 面構成

F-2 を自作する場合、「ラウンド毎の動きを見たい・ログに残したい・固まっていないことを
確認したい」は、**ループ本体とビューを分離**すれば安く手に入る。

```
┌ ループ核（純関数的な 1 実装）
│   ラウンド進行・/api/chat（stream=true）・コマンド実行
│   → 構造化イベントを 1 行 JSON で発行するだけ。描画を知らない
├ イベント: round_start / llm_progress / tool_exec / tool_result / round_end / stall
├ シンク 1: JSONL ログ（常時。--log <file>、既定 ~/.agents/logs/…）
├ シンク 2: ヘッドレス面 = stdout に最終本文・stderr に @agent-usage（既存 CLI 契約のまま）
└ シンク 3: TUI 面 = イベントを画面に描くだけの薄いビュー
```

設計の要点は 4 つ。

1. **「固まっていない」の判定はストリーミングで作る。** `stream=false` だと prefill 中と
   ハングが外から区別できない——今回の問題がまさにそれ。`stream=true` で受け、
   「最終トークンからの経過秒 / tok/s」を `llm_progress` イベント（1〜2 秒間隔に間引き）
   として出す。これが TUI の生存表示にも、**watchdog**（`stall_timeout` 秒無進捗なら
   transient 分類で自己中断——無人運転の agent-flow で人の代わりに「固まった」を検知する）
   にもなる。
2. **TUI は「ログの tail」として作る。** ループ核がどの面でも JSONL を書くので、
   TUI は (a) 自分でループを抱えて描く対話実行と、(b) `--follow <logfile>` で
   **agent-flow がヘッドレスで回している最中のノードへ後から覗きに行く**アタッチの
   2 モードが同じ描画コードで済む。デバッグ目的なら (b) が本命になる。
3. **描画はリッチにしない。** 標準ライブラリだけで足りる——ヘッダ 2 行（ラウンド番号・
   経過・最終トークンからの秒数・tok/s）を ANSI カーソル移動で更新し、その下へ
   イベント行を素直にスクロールさせる程度（curses も stdlib にあるが、ここまで要らない）。
   tmux 内で崩れないことだけ確認する（次項の理由）。
4. **対話面は CLI 定義の `interactive` に載せる。** `agents/<名前>.json` の
   `interactive.command` を TUI 起動にしておけば、agent-dashboard の対話診断
   （tmux send-keys 注入）からそのまま開ける。ヘッドレス契約とは別枠なので
   agent-flow 側の挙動には影響しない。

言語は Python（標準ライブラリのみ）を推す。agent-ollama / agent-opencode と同じ様式で
zipapp 同梱にも乗り、JS だと Node ランタイムと TUI ライブラリの依存が増えるだけで
得るものがない。

```
┌ t3 「README の誤字修正」 qwen3.5:9b   round 3/10   経過 4m12s
│ 生成中… 最終トークン 0.8s 前 (7.2 tok/s)
├────────────────────────────────────────
│ 12:01:03 R1 llm 42s  in=1,832tk out=210tk
│ 12:01:45 R1 $ grep -n "typo" README.md   → exit 0 (0.3s)
│ 12:02:30 R2 llm 61s  in=+412tk out=188tk（接頭辞キャッシュ命中）
│ 12:03:35 R2 $ sed -i …                   → exit 0 (0.1s)
```

#### F-2 の置き場所と契約 — agent-ollama へ統合する

**結論: CLI の表面（コマンド名 `agent-ollama` と定義 `agents/ollama.json`）へ統合するのが
筋。** 別コマンドを立てると「ollama の住所・タイムアウト・think の知識」が 2 か所に増え、
利用者は用途で CLI 名を切り替えることになる。契約スキーマは最初から「同じ CLI でも用途で
権限が違う」を `write_args` / `readonly_args` で 1 定義に収める設計なので、そこに載せる:

```jsonc
// agents/ollama.json（差分イメージ）
{
  "command": ["agent-ollama", "--think", "off", "{model}"],
  "write_args": ["--tools"],          // 書き込みモードのときだけループ+ツールが生える
  "readonly": "enforced",             // 既定/読み取り専用は従来どおり text→text（ツール無し）
  "interactive": {
    "command": ["agent-ollama", "--tui", "{model}"],
    "prompt_inject": "send-keys"
  },
  "errors": [ /* 既存 + */ 
    { "match": "応答が停止しました|stall detected", "class": "transient",
      "hint": "stall_timeout 内にトークンもツール実行も進まず自己中断しました（リトライで解けることが多い）" }
  ]
}
```

- **readonly の真実性が保たれる**のがこの形の要点。ループとツールは `--tools`（write
  モード）でだけ生え、既定・readonly は今の純 text→text のまま——`"enforced"` 宣言に
  嘘が入らない。agent-flow の work ノードだけが write でループを得る。
- **実装の置き場は agentcore 内の別モジュール**（`ollama_loop.py` / `ollama_tui.py`）。
  `ollama_adapter.py`（純 text 経路）は育てず、そのまま残す。install.sh の zipapp は
  agentcore ツリーを丸ごと同梱する作りなので、**ビルド経路は無改修**で新モジュールが乗る。
- **TUI の共用は 2 経路が自動で付いてくる**: agent-dashboard の対話診断は
  `interactive.command` を読むだけなので上記差し替えで TUI が開き、agent-loop は
  tmux send-keys / capture-pane で任意の対話 CLI を回す作りなので同じセッションを
  そのまま定期駆動できる。ここから**設計制約**が 1 つ出る——capture-pane が読める
  ことが前提なので、**全画面（alternate screen）にしない**。行指向でイベントを
  スクロールさせ、ステータス 1〜2 行だけをカーソル移動で更新する。プロンプト入力も
  素朴な行読み（send-keys の「文字列 + Enter」がそのまま効く形）にする。
- **外部ライブラリは「あれば使う」に留める**。rich（純 Python）を optional import で
  使えば描画コードはほぼ消えるが、ハード依存にするとエンジン zipapp 側の
  「標準ライブラリのみ」の不変条件を壊す。`import rich` 失敗時は ANSI 素書きへ
  フォールバック（ステータス行の更新だけなので 30 行程度）。
- **`--think on|off` は CLI オプションとして持つ**（API の `think` フィールドへ直結。
  環境変数 `AGENT_OLLAMA_THINK` で既定を上書き）。プロンプトへ `/no_think` を混ぜる
  方式はモデル依存で成果物にも漏れうるので採らない。定義ファイルの `command` 配列に
  `"--think", "off"` を焼き込めば、エンジン側は何も知らずに済む——案 E の think 抑制は
  この形で吸収する。qwen3 系の JSON 出力契約（§4.2 制約 2）の安定化を兼ねる。
- **「固まっていない」ことの保証は 3 層**: (1) `stream=true` の `llm_progress`
  イベント（最終トークンからの経過秒 / tok/s）を JSONL ログへ常時記録、(2) TUI /
  `--follow` がそれを描画、(3) `--stall-timeout`（既定例 120s 無進捗）で自己中断し、
  上記 errors の transient 分類に乗せてエンジンのリトライ層へ返す。遅いのは許容し、
  無進捗だけを落とす——「遅い」と「死んだ」を区別する信号がストリーミングで初めて手に入る。
- 任意: JSONL イベントログを `session_log`（format: 新設 `ollama-jsonl`）として宣言すれば
  agent-audit collect にそのまま乗り、ローカル推論の transcript も台帳へ入る。

#### F-2 補遺 1 — rich の zipapp 同梱

**技術的には可能。** rich とその依存（markdown-it-py / pygments）は純 Python で、
データも .py モジュールとして持つため zipimport で普通に動く。やり方も単純で、
install.sh のビルド一時ディレクトリへ `pip install --target "$BUILD" rich` してから
`python -m zipapp` に畳むだけ——agent-ollama の zipapp はエンジンの zipapp とは別物なので、
エンジン側の「標準ライブラリのみ」不変条件は壊さない。

ただし install.sh は現在**意図的に pip 依存なし・オフライン完結**で書かれている
（冒頭に明記あり）。同梱はこの不変条件と衝突し、代償は 3 つ:

| 方式 | 代償 |
|---|---|
| ビルド時 `pip install --target` | インストールにネットワークと pip が要るようになる |
| リポジトリへ wheel を vendoring | リポジトリが ~10MB 太る + ライセンス同梱の管理 |
| 実行時 optional import（前述） | `pip install rich` した環境でだけリッチになる |

TUI の要件（ステータス 1〜2 行の更新 + 行スクロール）は素の ANSI で 30〜50 行なので、
**既定は同梱なしの素書きで出し、install.sh に `--with-rich`（ビルド時 pip・失敗したら
素書きのまま続行）をオプトインとして足す**のが、オフライン不変条件を既定で守りつつ
「入れたい人は zip に閉じ込められる」の両取りになる。

#### F-2 補遺 2 — スキルの明示・遅延読み込み（自動選択なし）

要件は「スラッシュコマンド呼び出し・スキル名の明示指定に反応して、所定のユーザー
フォルダ以下の SKILL.md を遅延で読む。prefill による自動選択・自動使用はしない」。
これは**アダプター側の決定的なプロンプト前処理**として実装でき、LLM には一切
カタログを見せない——未使用時の prefill コストは正確にゼロになる。

- **発動は 2 形態だけ**（どちらも決定的に検出できるもの。自然文からの推測はしない）:
  1. `--skill <name>`（複数可）— エンジン設定・agent-loop の定期プロンプトなど
     プログラム経路の明示指定
  2. プロンプト**先頭ブロックのスラッシュ行** `^/([a-z0-9][a-z0-9-]*)( .*)?$` —
     TUI・send-keys 注入など人手経路。走査は先頭の連続するスラッシュ行のみで、
     本文中は見ない（貼り付けたコードやパスの `/usr/...` を誤爆させない）
- **解決順**: `$AGENT_OLLAMA_SKILLS_DIR` → `~/.agents/skills/<name>/SKILL.md` →
  `~/.claude/skills/<name>/SKILL.md`（install.py の配布先と同じ場所を読むので、
  配布経路は既存のまま）。
- **展開**: frontmatter（`---` 囲みの name/description/metadata）を剥いだ本文を
  プロンプトの前置きに注入し、スラッシュ行は引数だけ残して置換。`{skill_dir}` を
  実パスへ置換しておくと、ループモード（--tools）ではスキル同梱の scripts/ を
  bash ツールから実行できる。
- **見つからないとき**: `--skill` は env 分類で即失敗（hint: `python install.py --agent …`）。
  スラッシュ行は stderr に警告してそのまま素通し（スキル名でない普通の行かもしれない
  ——偽陽性でプロンプトを壊さない）。
- TUI にはローカルコマンド `/skills`（一覧表示。LLM へは送らない）だけ足す。

この形なら「スキル機構」はアダプター内の 50〜80 行の純関数（プロンプト → プロンプト）で
済み、flow-worker / flow-planner（agent-flow 自身が描画するスキル）とも競合しない。

**agent-loop / kiro-loop 連携 — 定期プロンプトの専用プロパティ `slash`**:
定期プロンプトの設定エントリに、送信文の前にスラッシュコマンドとして何を送るかを
宣言する `slash: string | string[]` を足す（本文へ手で `/name` を書き込む運用を
避ける）。agent-ollama 専用にはせず、スラッシュコマンドを解するどの対話 CLI にも
効く共通口とする。**fork 先の kiro-loop 系プロジェクトへ単体で展開するため、
仕様・送信順・移植ガイドは独立文書に切り出した**:
[`docs/designs/agent-loop-slash-property-design.md`](../designs/agent-loop-slash-property-design.md)。

#### F-2 補遺 3 — バックアップ運転の監視:「遅い」を異常にしない証拠

§0.1 R2 の実装。バックアップ運転では 1 呼び出し数十分が正常なので、
「停止に見えるが進んでいる」ことを**人にもプログラムにも示せる**ようにする。

1. **一次証拠 = JSONL 進捗ログ。** `llm_progress`（最終トークンからの経過秒 /
   累計トークン / tok/s）と `tool_exec` をタイムスタンプ付きで追記し続ける。
   追記のみのファイルなので、「その時刻に進捗があった」ことの事後証明にもなる。
2. **機械可読の現在地 `agent-ollama --status [<log>]`**。最新イベントから
   `{phase, round, last_progress_at, since_last_progress_sec, tokens_per_sec}` を
   1 行 JSON で返す。agent-dashboard の稼働シグナル（doctor の recent/stuck 判定）や
   外部監視がこれをポーリングすれば、**「stuck に見えるが last_progress が 3 秒前」を
   異常と扱わずに済む**。人間は TUI `--follow` で同じものを見る。
3. **失敗検知の主役交代。** バックアップ構成ではエンジン側の壁時計タイムアウト
   （agent-flow `agent_timeout` / agent-project 既定 300s 等）を大きく取り、
   実質の検知器を `--stall-timeout`（無進捗ベース）に移す。壁時計は「無限ハング時の
   最後の砦」としてだけ残す（例: agent_timeout 3600 / stall_timeout 180）。
   これが「長時間の停止を異常と扱わない」を運用に落とした形——遅くても進んでいれば
   落とさず、進まなくなったときだけ transient で返してリトライ層に渡す。

#### F-2 実装状況（2026-08-06・実装済み）

上記のうち **F-2 の全体（置き場所・TUI・think・監視・スキル）と補遺 1〜3 を実装した**。
案 A / C は設定・環境変数だけなので実装物は無い。案 B は実機検証待ち。F-1 / F-3 は未着手
（F-2 で足りない場面が出たら再検討する）。

| 置き場 | 役割 |
|---|---|
| `agentcore/ollama_loop.py` | ストリーミング呼び出し・watchdog・bash 1 ツールのループ |
| `agentcore/ollama_events.py` | 進捗イベント（JSONL）・`read_status()`・`follow_events()` |
| `agentcore/ollama_skills.py` | 明示・遅延のスキル解決（`--skill` / 先頭スラッシュ行） |
| `agentcore/ollama_tui.py` | 行指向ビュー（`--tui` / `--follow`）。rich は任意 |
| `agentcore/ollama_adapter.py` | 引数解釈とモード分岐。`generate()` は後方互換で残す |
| `agents/ollama.json` | `write_args: ["--tools"]` / `--think off` / `interactive` → `--tui` |

宣言と実装の対応は `test_ollama_adapter.py` の `TestContractDefinition` が固定する
（`readonly` にツールが混ざらないこと・定義の argv がそのまま解釈できること）。

実装で確定した細部（設計時に決めていなかったもの）:

- 打ち切りの上限は 3 局面に分けた: `connect`（既定 30s）/ `prefill`（既定 **0 = 無制限**）/
  `decode`（`--stall-timeout` 既定 180s）。**prefill を無制限にしたのが要点**——ここに
  上限を置くと「CPU で 10 分」の正常な実行が死ぬ。検知の粒度は heartbeat 間隔（5s）。
- 待ちの上限判断と打ち切りは呼び出し側スレッドが持ち、接続と行読みは別スレッドへ出した。
  ソケットにタイムオーバーを掛けない代わりに、打ち切りたいときはソケットを直接 shutdown する。
- **実装中に踏んだ事故を 1 つ記録する**: 打ち切りで `res.close()` を呼ぶと、`http.client` の
  応答は `BufferedReader` のロックを取りに行き、そのロックは受信でブロックしている
  リーダースレッドが握っているため**打ち切った側が固まる**。「無進捗を検知したのに
  プロセスが終われない」という最悪の形になるので、`close()` ではなくソケットの
  `shutdown(SHUT_RDWR)` で解く。実 HTTP サーバ相手の回帰テストで固定した
  （`TestStallReturnsPromptly`）。
- ループが規約から外れた応答を受けたときは 2 回まで言い直しを促し、それでも駄目なら
  最後の本文を成果として返す（§0.1 R1「止めない」——曖昧な成果でも止まるより良い）。

#### F-3 — 既製の軽量コーディング CLI（aider）

aider はヘッドレス実行（`aider --message … --yes-always`）と ollama 接続を持ち、
SEARCH/REPLACE 編集なので decode も軽い。システムプロンプトは数千トークン域で、
リポジトリマップを `--map-tokens 0` で切れば opencode より 1 桁軽い。導入が最速な
代わりに、usage の実測化・エラー分類・readonly 宣言はアダプター（agent-opencode と
同型の薄いラッパー）を 1 枚挟む必要がある。

#### 比較

| 経路 | prefill 目安 | ラウンド | 探索 | 純 CPU での work |
|---|---|---|---|---|
| opencode | 10,000-20,000 | 多 | ○ | 不成立 |
| F-3 aider（map off） | ~2,000-4,000 | 少 | △（渡したファイル中心） | 限界域 |
| F-2 bash ループ | ~1,000-2,000 + ラウンド差分 | 中 | ○ | 遅いが成立しうる |
| F-1 パッチ 1 発 | ~3,000-4,500 | **1** | ×（計画時に対象確定が前提） | **成立** |

推奨は **F-1 を主経路、探索型タスクだけ opencode（クラウド/GPU 機）へ**という分業。
F-1 が刺さらないタスクの比率が高いと分かってから F-2 を検討すればよい。

---

## 3. 推奨の進め方

1. **§1 の計測**で prefill / 冷起動 / decode の内訳と、切り捨て（truncating）の有無を確定する
   （30 分。以降の全判断の根拠になる）。
2. **案 A を即日適用**する。設定変更のみ。単発系役割が実用域に入る。
3. **案 C（1〜3）をサーバ側に適用**する。環境変数 3 つ。冷起動と再 prefill が消える。
4. モデルを **案 C-4** で見直す（RAM と相談して MoE か 4B 級）。
5. 余力で **案 B（iGPU）** を検証する。効けば prefill が桁で縮み、opencode 経由の復権も
   視野に入る。
6. opencode はそれまで agent-tools の書き込み系役割から外し、GPU 機（別 PC）が
   用意できた時点で README の本来の構成に戻す。

## 4. agent-flow の worker として使えるか

結論: **kind による。テキスト系 kind は案 A の延長で行ける。ファイルを触る `work` は
opencode 必須なので §0 の prefill 壁がそのまま掛かり、CPU 単体では成立しない。**

### 4.1 「スキルを使う」ことはローカル推論の障害にならない

agent-flow の worker が使う flow-worker「スキル」は、CLI 側のスキル機構ではない。
agent-flow 自身が `worker_skill`（既定 `flow-worker`）の `scripts/prompt.py` を
**ローカルで実行してプロンプトを組み立て、stdin で CLI に渡す**（`agent.py` の
`run_agent`）。つまり CLI がスキルを読める必要はなく、`agent_cli: ollama` でも
flow-worker の実行規律（三つの約束・出力契約）はそのまま届く。

実測（`prompt.py` に work ペイロードを流した結果）: 骨格は **約 1,400 文字 ≒ 500
トークン** + タスク文脈・依存成果。opencode 固有の 1〜2 万トークンとは 1〜2 桁違う。
CPU の prefill でも数十秒の領域で、**worker プロンプトの重さは問題にならない**。

逆に、opencode への**スキル配布**（`install.py --agent opencode --all-skills`）は
ローカル運用では毒になる——スキル一覧がシステムプロンプトに載って prefill を増やし、
SKILL.md の読み込みがツールラウンドを 1 つ足す。opencode を使う場合も配布は必要最小限に
絞る。flow-worker / flow-planner は agent-flow 側が読むので配布不要。

### 4.2 kind 別の可否

`agent-flow.yaml` の `agents:` は planner / evaluator / worker（全 kind 既定）/ 個別 kind
の粒度で `{agent_cli, model}` を上書きできる。これで割ると:

| kind | 内容 | ローカル（`agent_cli: ollama`） |
|---|---|---|
| classify / filter / judge / reduce / split / map / synthesize / generate | text → text（+ JSON 出力契約） | **行ける**（下記 2 制約に注意） |
| verify | 依存成果の独立検算 | 依存成果が本文に inline されていれば行ける。ワークスペースのファイルを読む検証は不可 |
| planner / evaluator | JSON 契約の計画・継続判断 | 行ける（JSON 遵守は要観察） |
| **work** | ワークスペースでのファイル編集 | **不可**。ツール実行が要るので opencode 必須 → §0 の壁 |

制約が 2 つある:

1. **artifacts のパス参照が読めない。** 大きな中間成果物は `artifacts/<id>/` への
   パス参照で後続へ渡る設計だが、これは「後続の CLI がファイルを読める」前提。
   agent-ollama にはツールが無いので、**ローカルに落とす kind へは依存成果が
   `output`/`data` に inline で乗る経路だけ**が有効。グラフ設計時にここを跨がせない。
2. **JSON 出力契約の遵守が 9B では揺れる。** filter/judge/reduce/split と
   planner/evaluator は末尾 JSON が契約。`format_retries`（レイヤ 2）が 1 回は拾うが、
   qwen3 系の `<think>` ブロックが混入すると崩れやすい——案 E（think 抑制）は
   agent-flow 経路では品質要件でもある。

### 4.3 設定例と運用条件

```yaml
# agent-flow.yaml — テキスト系 kind だけローカルへ。work はクラウド（or 別 PC GPU の opencode）
agent_cli: <クラウド CLI>          # 既定は従来どおり
agents:
  classify:   {agent_cli: ollama, model: qwen3.5:9b}
  filter:     {agent_cli: ollama, model: qwen3.5:9b}
  judge:      {agent_cli: ollama, model: qwen3.5:9b}
  reduce:     {agent_cli: ollama, model: qwen3.5:9b}
  split:      {agent_cli: ollama, model: qwen3.5:9b}
  map:        {agent_cli: ollama, model: qwen3.5:9b}
  synthesize: {agent_cli: ollama, model: qwen3.5:9b}
  # work / verify / planner / evaluator は当面クラウドに残す（様子を見て順次移す）
workers: 1            # ローカル推論は直列に。並列 2 はプレフィックスキャッシュを潰し合う
agent_timeout: 1200   # 既定 600s。ローカルの冷起動 + 生成で超えると transient 扱いで無駄リトライ
```

サーバ側は §2 案 C（`OLLAMA_KEEP_ALIVE` / `OLLAMA_NUM_PARALLEL=1` / KV 量子化）を
そのまま適用する。map-reduce の fan-out はローカルでは**直列消化**になるので、
列挙駆動の大きな run をローカル kind に向けるときは件数×1 ノード数分の壁時計を見込む。

### 4.4 work もローカルでやりたい場合の成立条件

**先に案 F（§2）を検討すること。** 特に F-1（ワンショット・パッチ方式）は opencode を
使わずに work ノードを純 CPU の実用域へ入れる唯一の経路で、agent-flow のスコープ契約と
噛み合う。以下は「それでも opencode で」の場合の条件で、全部そろって、ようやく
「1 ノード数分〜十数分」の域:

1. 案 B（iGPU prefill）が実機で効くこと——これが前提条件。効かなければ不成立
2. 案 D の痩せた opencode エージェント（read/edit/bash のみ・MCP 無効・スキル配布最小）
3. モデルは MoE（アクティブ 3B 級）か dense 4B 級
4. `workers: 1` + `OLLAMA_NUM_PARALLEL=1` + `agent_timeout: 1800` 程度
5. `granularity` のスコープ契約（変更 ≤30 行想定）でノードあたりのラウンド数を絞る

再計画（`max_iterations`）とリトライ（`max_retries`）が掛け算で乗ることを忘れない——
1 ノード 10 分は「run 1 本が半日」を意味しうる。**まず 4.3 のテキスト系だけで運用を始め、
案 B の計測結果が出てから work の移行を判断する**のが安全な順序である。

## 5. 受け入れ基準（案）

- `agent-audit extract`（ローカル推論）の p50 が **90 秒以下**、p95 が呼び出し側
  タイムアウト（300s）以下。
- ollama サーバログに `truncating input prompt` が出ない。
- `agent-audit usage` の実測トークンで、ローカル役割 1 呼び出しの tokens_in が
  **5,000 未満**に収まっている（opencode 固有分が載っていない証拠）。
- バックアップ運転（§0.1）: 実行中の任意の時点で `--status`（または `--follow`）が
  **直近の進捗時刻を返し**、進捗がある限り壁時計経過だけでは失敗扱いされない
  （壁時計 timeout で kill された実行が JSONL 上「進捗あり」だった件数 = 0）。
- 無進捗は `stall_timeout` 内に transient 分類で自己中断し、エンジンのリトライ層に
  拾われる（無限ブロックした worker = 0）。
