# agent-herd judge — Jev 型の判断 AI をローカル LLM（gemma4:e4b）で真似る設計

> 作成 2026-09-19
> 対象: `tools/agent-tools/agentcore/agentcore/judge.py` / `herdcli.py`（`judge` サブコマンド）
> 上位文書: [agent-herd 設計](../designs/agent-herd-design.md)（ADR-4「権限と受入をモデルの外で
> 判定する」の延長）、[agent-herd 仕様書](../specs/agent-herd-spec.md) §5.5
> 関連: [ローカル LLM 改善案 2026-08-27](./2026-08-27-agent-tools-local-llm-effective-improvement-proposals.md)
> §1 事実 2・3（判定はゲート、多基準はモデルに訊かない）、
> [`agent-herd decide`](./2026-08-30-measured-improvements-intake.md) A3

---

## 0. 一枚で

TypeSafe AI が 2026-09-15 に公開した Jev は「System One モデル」を名乗る。文章を 1 トークン
ずつ生成する代わりに、**状態と型付きの問いを受け取り、選択肢の上の確率分布を 1 回の並列
読み出しで返す**。速く（40〜200 倍）、安く、型を外さない。用途は振り分け・可否・採点のような
「答えの集合が先に決まっている、繰り返しの判断」に限る。

agent-herd には既に同族の口が 2 つある。`decide`（事実の抽出 → 機械判定）と、ハーネスの
受入判定 / 遷移条件（モデルに JSON を書かせて読む）。前者は「多基準の採否をモデルに訊かない」
という実測の結論で、後者は「文章を生成させて JSON を拾う」形のまま残っている。

本設計は 3 つ目の口 `agent-herd judge` を足す。**モデルに文章を書かせず、1 トークン目の
分布を読む**。これが Jev の「並列読み出し」に対応するローカル版で、gemma4:e4b の
「短い・局所的・構造化なら合格」という性質（08-27 §1 事実 1）と噛み合う——判断 1 件が
prefill 1 回で終わり、生成の暴走も JSON の壊れも原理的に起きない。

```
  Jev（雲）                              agent-herd judge（LAN の ollama）
  state + {name: question}   ──同じ形──▶  state + {name: question}
        │                                       │  状態を先・問いを後に並べる（接頭辞キャッシュ）
        ▼                                       ▼
  並列読み出し（独自モデル）              /api/chat  logprobs=true  num_predict=4  temperature=0
        │                                       │  A/B/C… のラベルに落ちた質量を正規化
        ▼                                       ▼
  {choice, probabilities, noul}      ──同じ形──▶  {choice|value|score, probabilities,
                                                  confidence, coverage, method}
```

## 1. なぜ「生成して読む」ではなく「分布を読む」か

| 観点 | 生成して JSON を読む（現行の judge_acceptance 等） | 1 トークン目の分布を読む（本設計） |
|---|---|---|
| 走行時間 | prefill + 数十〜数百トークンの decode | prefill + 4 トークン |
| 故障モード | JSON が壊れる・散文が混じる・暴走（`num_predict` 天井で切る） | ラベルが読めない（`coverage` が 0）——それ以外は無い |
| 確度 | 無い（モデルに「確信度」を書かせても意味が無い） | ラベルの質量そのもの。しきい値で「決めない」へ倒せる |
| 多基準 | 崩れる（08-29 実測 0/5） | 問いを 1 基準ずつに分けるのが形として強制される |

Jev が「幻覚できない」と言うのはこの構造による——答えは選択肢の集合の外に出られない。
ローカル版でも同じ性質が成り立つ。違うのは較正（calibrated probability）で、gemma4:e4b の
1 トークン目の分布は Jev のように調整されていない。だから `confidence` を「確率」ではなく
「しきい値で切る目安」と扱い、`method` と `coverage` を必ず添えて、呼び出し側が信頼の程度を
決められるようにする（§4）。

## 2. 契約

### 2.1 問い（Jev の形をそのまま借りる）

```json
{
  "team":     {"type": "choice",  "instructions": "どのチームが扱うべきか",
               "criteria": {"billing": "請求と返金", "support": "それ以外"}, "other": "どちらでもない"},
  "urgent":   {"type": "boolean", "instructions": "至急か"},
  "severity": {"type": "score",   "instructions": "深刻さ", "criteria": ["low", "medium", "high"]}
}
```

- `choice`: `criteria` はキー → 説明（挿入順を保つ）。
- `boolean`: 内部では `yes` / `no` の 2 択として扱う。
- `score`: `criteria` は順序つき。キーが全部数ならその値、そうでなければ 0 からの順位を
  値にし、`score` は確率加重（OpenJev の提案と同じ「確率加重の指数」。最頻だけを採ると
  分布の情報を捨てる）、`bucket` は最頻。
- `other`: Jev の noul（どれでもない）に相当する**明示の**選択肢。黙って足さない——
  足すと「選択肢のどれかを必ず選ぶ」契約のつもりの呼び出し側が、想定外のキーを受ける。

### 2.2 答え

```json
{"answers": {
   "team":     {"type": "choice",  "choice": "billing", "probabilities": {"billing": 0.78, "support": 0.17, "other": 0.05},
                "other": 0.05, "confidence": 0.78, "coverage": 0.93, "method": "logprobs"},
   "urgent":   {"type": "boolean", "value": false, "probability": 0.11, "probabilities": {"yes": 0.11, "no": 0.89},
                "confidence": 0.89, "coverage": 0.99, "method": "logprobs"},
   "severity": {"type": "score",   "score": 1.4, "bucket": "high", "probabilities": {"low": 0.1, "medium": 0.4, "high": 0.5},
                "confidence": 0.5, "coverage": 0.96, "method": "logprobs"}},
 "abstained": []}
```

`coverage` はモデルの 1 トークン目の分布のうちラベルに落ちた割合（`top_logprobs` の上位 20 の
範囲）。低ければ「モデルが選択肢の形で答えていない」——プロンプトか問いの立て方の問題で、
確率を信じる前にそちらを直す。

### 2.3 入口

```
agent-herd judge --questions <JSON|パス> [--state パス] [--model M]
                 [--min-confidence 0-1] [--samples N] [--think on|off|auto] < 状態
```

終了コードは `decide` と同じ作法: 0 = 全問に答えた、1 = `abstained` あり（確度不足）か
ollama の失敗、2 = 引数の誤り。**確度が足りない問いを黙って答えへ倒さない**のが要点で、
決めていないことを終了コードで伝える。

Python からは `agentcore.judge.evaluate(state, questions, model=…)`。`request` を差し替えられる
ので、消費側のテストは ollama 無しで書ける。

## 3. 実装の要点

1. **プロンプトは状態が先、問いが後。** 同じ状態への複数の問いが接頭辞を共有し、ollama の
   プロンプトキャッシュに乗る（08-27 案 D と同じ理屈。Jev の「shared-state multi-question」
   に対応する）。
2. **選択肢には A / B / C … の 1 文字ラベルを振る。** 選択肢のキー（`billing` 等）を
   そのまま答えさせると、トークナイザによっては複数トークンに割れて 1 トークン目の分布が
   語頭の共有で濁る。1 文字ならどのトークナイザでも 1 トークンで、`" A"` / `"A"` / `"A."`
   の揺れは読み出し側で正規化する。
3. **読む位置はラベルの質量が最大の位置。** 1 トークン目が改行や空白になるモデルがあるので
   `num_predict=4` で数位置を取り、ラベルに最も質量が落ちた位置を採る。
4. **`think` は既定 off。** `ollama-json` profile と同じ理由（思考チャネルに答えが吸われて
   本文が空になる）。`--think auto` で送らない選択も残す（think 非対応モデルは `think` を
   受けると 400 になる）。
5. **温度 0、`format` なし。** 文法制約は要らない——読むのは分布であって本文ではない。

## 4. ollama が logprobs を返さないとき（縮退の順序）

`logprobs` / `top_logprobs` は ollama の chat / generate API にある（0.12 系後半で追加）。
古い配布や一部のランタイムでは応答に `logprobs` が無い。**入っていても読めないことがある**
——OpenAI 互換の `{"content": [...]}` のように形が違う場合で、判定は「キーの有無」ではなく
**ラベルの質量を読めたかどうか**で行う。そのときの順序:

| 条件 | 振る舞い | `method` | `confidence` | `coverage` |
|---|---|---|---|---|
| ラベルに質量が落ちた | 分布を読む（本来の形） | `logprobs` | 最頻ラベルの確率 | 落ちた質量 |
| 読めず（`logprobs` が無い／形が違う）、`--samples N`（N ≥ 2） | structured outputs（enum）で N 回引き、票数を確率に | `vote` | 最頻ラベルの得票率 | 読めた票 / N |
| 読めず、N = 1 | 本文の 1 文字を読む | `text` | **0**（確度の材料が無い） | 0 |
| ラベルをどこからも読めない | `JudgeError`（答えを作らない） | — | — | — |

「確率 1.0 を捏造する」経路は無い。`text` は読み取れた事実（`choice` / `value` / `bucket` と
ラベル 1 つに立った `probabilities`）だけを残し、確度は名乗らない。`abstained()` は
`method` が `text` の答えを**しきい値に関わらず棄権に入れる**——`--min-confidence` が 0.0
（実測前の置き値。§6）の呼び出しでは `confidence` の比較だけでは止まらないため。本文の
ラベルで足りる呼び出しは `abstained(answers, min, allow_text=True)` で受け取れる。

> 2026-09-20 の修正前、この表には「`logprobs` があるが読めない」の行が無く、実装もその
> 状態を `text` へ直行させていた（`confidence` 1.0・`--samples` は黙って無視）。表に無い
> 状態は実装でも見落とされる。縮退の条件を足すときは表を先に直す。

## 5. 何を置き換え、何を置き換えないか

| 既存の口 | 本設計での扱い |
|---|---|
| `agent-herd decide`（抽出 → 機械判定） | **置き換えない。** 多基準の採否は機械が決める形が正しい（08-29 実測）。`judge` は単一基準の問いを確率で答える別の道具。多基準を `judge` に 1 問で訊かない（基準ごとに問いを分ける） |
| `harness run --judge`（受入条件の判定） | 現状維持。受入条件は自然文で、選択肢が先に決まっていない。設計書「任せない」表のとおり、自然文の受入は役割ごと撤去の方向 |
| statemachine の遷移条件（`needs_llm_eval`） | **配線済み（2026-09-19）。** 条件は真偽で答えの集合が固定、同じ出力（状態）に複数の条件（問い）という形が本設計の得意な形そのもの。`_sm_judge_conditions` が条件 1 件を boolean 1 問にして `judge.evaluate` を呼び、答えを `--evals` へ載せる。使うのはローカル定義（`relative_cost` 0）のときだけで、judge の失敗・確度不足（`_SM_JUDGE_MIN_CONFIDENCE`、既定 0）は従来の制御応答へ倒し、証跡（`condition_judge_*`）に残す。しきい値の既定は §6 の実測後に決める |
| agent-project の `route`（書込先の自動ルーティング） | **配線済み（2026-09-19）。** `request.route_judge` が候補リポジトリを `choice` の選択肢に、`other` を「どの候補にも属さない」にして 1 問で訊く。`other` は ""（書込先なし）に写す——RO3 の「決められないと言えるか」が明示の選択肢になる。judge が決めなければ従来の `_route_agent_prompt` |
| agent-flow の `filter`（単一基準・`decision` 無し） | **配線済み（2026-09-19）。** `agent.filter_judge` が依存 1 件 = 候補 1 件で boolean を 1 問ずつ訊き、`kept` を作る。依存が 1 件（本文に候補が並ぶ形）は候補を列挙できないので生成経路。多基準（`decision` あり）は従来どおり抽出 → 機械判定 |
| agent-project の `assess` | **配線済み（2026-09-19）。** c / r / a の 3 段の採点は `score` 型そのもの。`prioritize.assess_judge` が軸 1 つを問い 1 つにし、確率加重の `score` を四捨五入（偶数丸めを避けるため自前）して 1〜3 にする。記録する書式 （`c=N r=N a=N`）は変えないので、読む側（リスクダイジェスト・spec ルーティング）は無改修。judge が決めなければ生成経路、それも駄目なら既存のヒューリスティック |

### 5.1 クラウド CLI の実行でも判定だけを judge へ（設定 `judge.model`、2026-09-19 追記）

§5 の配線 4 件はどれも「ローカル定義（`relative_cost` 0）で回しているときだけ」だった。
理由は judge が LAN の ollama を直に叩くことで、ollama の無い環境で勝手に叩きに行かない
ための門。ただしこの門のせいで、**判定にいちばん高いトークンを払っている実行——Claude Code
などクラウド CLI で回している実行——では judge が一度も使われない**。遷移条件 1 件ごとに
出力全文と workflow ファイルを添えて「JSON で true/false を返せ」と生成させ、route / filter /
assess も同じ形でクラウドに訊いていた。

門を設定 1 つで開けられるようにした。各 PC の `~/.agents/agent-herd.yaml`（`agentcore.herdconfig`）
の `judge.model` にモデル名があれば `judge.model_for_spec` / `local_model` は定義を見ずに
そのモデルを返し、4 件の配線はクラウド CLI の実行でも判定だけを judge へ回す。`off` なら逆に
どの実行でも judge を使わない。既定（`auto`）の振る舞いは変えていない。

| | 実行の定義 | 判定の行き先（`auto`） | 判定の行き先（`judge.model: gemma4:e4b`） |
|---|---|---|---|
| 遷移条件 | claude | 制御応答（出力全文 + workflow を添えて JSON 生成） | judge（prefill 1 回 + 4 トークン） |
| route / filter / assess | claude | クラウドに JSON 生成 | judge |
| どれも | aider / ollama | judge（実行のモデル） | judge（指名したモデルに固定） |

**環境変数ではなく設定ファイルにした理由。** 最初は `AGENT_JUDGE_MODEL` で開けたが、judge を
呼ぶのは agent-herd / agent-loop / agent-flow で、agent-app（Windows）から WSL 側のそれらへ
環境変数は届かない（ログインシェルが env を作る）。ファイルなら python が動く側の home に
残り、`agent-herd config set judge.model …` という 1 本の口を app もスキルも人も共有できる。
app の「設定 > 実行制御」はこのコマンドに書き込みを頼む（app の config.json には持たない——
持つと 2 か所に真実ができる）。

指名したモデルを実行のモデルより優先するのは意図で、「実行は 12b、判定は e4b」のように
判定だけ軽いモデルへ寄せられる。judge が使えない・確度が足りないときの縮退（生成経路へ
倒して証跡に残す）は値に関係なく同じで、ollama に届かないときも実行は止まらない。

### 5.2 ステートマシンの分岐を「結果の選択肢」として書く（`outcome`、2026-09-19 追記）

遷移条件の判定は「条件 1 件 = boolean 1 問」だった。これは Jev の形としては半分で、
同じステートから出る候補は本来 **1 つの choice**（状態 → 遷移先の分布）である。候補が 3 つ
なら prefill が 3 回から 1 回になり、「2 つの条件が同時に真」という矛盾が構造として消える
（priority で先勝ちにする必要が無い）。

statemachine-use の transitions に `outcome`（この遷移が成立する結果の短い名前）を足した。
同じ元ステートの LLM 評価が要る候補すべてに `outcome` があれば、ハーネス
（`_sm_condition_questions`）とスキル自身の実行系（`scripts/judge_bridge.py`）は
「結果はどれか」の choice 1 問を組み、`other`（どれでもない）を明示の選択肢にする。
`condition_rule` で測れる候補は選択肢に入れない——測れるものは測り、残りだけを選ばせる。

| 実行の形 | judge の使い方 |
|---|---|
| `agent-herd harness statemachine` | ハーネスが `judge.evaluate` を直に呼ぶ（従来どおり）。問いの形だけ choice が増えた |
| `run_machine.py`（スキル自身の実行系） | `--judge auto`: `agent-herd` が PATH にあり `agent-herd config --check judge` が 0 なら `agent-herd judge` を subprocess で呼ぶ。無ければ従来の YES/NO 生成 |
| 会話内の手動実行 | `next_state.py --auto-eval` が `judge_questions` を返し、モデルは `agent-herd judge` に渡して `--judge-answers` で確定する。無ければ従来の `--eval` |

judge が無い経路では `outcome` を条件文（「最後の出力の結果が『…』である」）にして YES/NO で
評価するので、**定義は 1 つでよい**。スキルの作成モードには「出力の内容で分岐する遷移は
`outcome` で書く」を設計原則に足し、scaffold の骨組みにもその案内を入れた。

### 5.3 ステートの中で使う 3 つの口（2026-09-19 追記）

遷移だけでなく、ステートの中で LLM が呼ばれる場所にも選択肢の読み出しで済むものがある。
どれも「決定的な手段 → judge → 生成」の順にして、**judge が無い設定でも生成（いちばん高い
呼び出し）の回数が増えない**ことを不変条件にした。

| 場面 | 決定的 | judge | 生成（最後の手段） |
|---|---|---|---|
| 判定だけのステート（`judge:`。分類・振り分け） | — | choice 1 問。生成 0 | 宣言から作った短いプロンプト（選択肢を列挙、キーを 1 語）で 1 回 |
| `output_validator` に合わない出力 | 契約の語が第 1 行の途中・後ろの行・大文字小文字違い → 直す | 「どの契約の語か」1 問（確度 0.6 以上） | 再生成（`max_retries`） |
| `check` が落ちた | 環境の失敗の定型句 → 再投入しない | 「やり直しで直るか」1 問（確度 0.85 以上の「直らない」だけ止める） | 従来どおり再投入 |

宣言の正規化はスキル側の 1 実装（`judge_bridge.normalize_judge_state`）に置き、ハーネスは
`next_state.py --state-judge` で読む（`--state-check` と同じ理由——YAML を読み直すと形を足した日に
ずれる）。判定だけのステートの `other` と確度不足は `unsure` の語（既定 UNSURE）にする。
最頻の選択肢へ黙って倒さないのは §2.1 の `other` と同じ理由で、`condition_rule` で人へ回せる。

検査失敗の judge しきい値（0.85）と契約の語の補完（0.6）は仮置きで、§6 の実測で見直す。
環境の失敗の定型句は分類器ではなく、実出力に現れる語の一致だけ（レビュー P2 の但し書きの範囲）。

## 6. 測ってから決めること

設計時は ollama が無く未測定だったが、2026-09-20 に
[実測](2026-09-20-judge-readout-first-measurement.md)を記録した。既定の問い方では、全ノードが
green で要求の段が欠けている E3〜E5 が 0/9（確度 0.949〜0.991）で、しきい値をどこに置いても
除けなかった。**問いを「状態の行を指させる」形へ変え、欠落の判定を機械へ移すと corpus 全体が
36/36 になる**（同文書 §8）。確度の使いどころは、問いの形を決めてからでないと測れない。
小標本のため既定値は確定しない。
追加した`readout_eval.py --calibration`はmethod別のBrier・ECE・threshold sweep・coverage・
棄権・失敗・usageをJSONに残す。fake/replayも同じschemaを使い、旧台帳から分布を推測しない。
手順とPR #862の段0/段1境界は[eval README](../../tools/agent-tools/eval/README.md#judge-calibration-gatereadout_eval)
を参照。次に対象workloadの標本を増やす際は、
`tools/agent-tools/eval` の既存セル（F1 / J2 / RO1〜RO3 / CL1 / E1〜E3 の単一基準の判定）を
`judge` 経路で引き直し、次を見る。

1. `coverage` の分布。ラベルに落ちる質量が低い（< 0.8）なら、プロンプトの形（ラベルの
   置き方・「Answer (one letter):」の末尾）を直す。
2. `confidence` と正答の関係（信頼度図）。しきい値 0.6 / 0.7 / 0.8 でどれだけ棄権し、
   棄権しなかった分の正答率がどれだけ上がるか。これが `--min-confidence` の既定の根拠になる。
3. 生成経路（`ollama-json`）との壁時計の差。prefill が律速なら差は小さいはずで、そのときは
   「速い」ではなく「形式故障が無い」「確度で分岐できる」が採用理由になる。

## 7. 採らなかった案

- **`decide` を拡張して確率を返す**: `decide` の契約は「モデルは事実の転記、採否は機械」で、
  確率という概念を持たない。混ぜると「機械が決めたのか、モデルの確度で決めたのか」が
  結果から読めなくなる。
- **選択肢のキーをそのまま答えさせる**: §3 の 2 のとおり、トークン分割で分布が濁る。
- **`format`（enum）で 1 回生成して確率 1.0 にする**: 形式は守れるが確度が無く、Jev の
  本質（確度で分岐できる）を落とす。縮退の最後（`text`）としてだけ使う。
- **OpenAI 互換 API（`/v1/chat/completions`）の logprobs を使う**: ollama のネイティブ API に
  同じ口があり、agent-herd の他の経路（`ollama_loop`）と接続情報・環境補完を共有できる
  ネイティブを選ぶ。
