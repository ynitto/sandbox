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

## 4. 受け入れ基準（案）

- `agent-audit extract`（ローカル推論）の p50 が **90 秒以下**、p95 が呼び出し側
  タイムアウト（300s）以下。
- ollama サーバログに `truncating input prompt` が出ない。
- `agent-audit usage` の実測トークンで、ローカル役割 1 呼び出しの tokens_in が
  **5,000 未満**に収まっている（opencode 固有分が載っていない証拠）。
