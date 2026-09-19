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
古い配布や一部のランタイムでは応答に `logprobs` が無い。そのときの順序:

| 条件 | 振る舞い | `method` | `coverage` |
|---|---|---|---|
| `logprobs` があり、ラベルに質量が落ちた | 分布を読む（本来の形） | `logprobs` | 落ちた質量 |
| `logprobs` が無く `--samples N`（N ≥ 2） | structured outputs（enum）で N 回引き、票数を確率に | `vote` | 読めた票 / N |
| `logprobs` が無く N = 1 | 本文の 1 文字を読む | `text` | 0 |
| ラベルをどこからも読めない | `JudgeError`（答えを作らない） | — | — |

「確率 1.0 を捏造する」経路は無い。`text` は確率の形をしているが `coverage: 0` で、
呼び出し側は `method` を見れば区別できる。

## 5. 何を置き換え、何を置き換えないか

| 既存の口 | 本設計での扱い |
|---|---|
| `agent-herd decide`（抽出 → 機械判定） | **置き換えない。** 多基準の採否は機械が決める形が正しい（08-29 実測）。`judge` は単一基準の問いを確率で答える別の道具。多基準を `judge` に 1 問で訊かない（基準ごとに問いを分ける） |
| `harness run --judge`（受入条件の判定） | 現状維持。受入条件は自然文で、選択肢が先に決まっていない。設計書「任せない」表のとおり、自然文の受入は役割ごと撤去の方向 |
| statemachine の遷移条件（`needs_llm_eval`） | **配線済み（2026-09-19）。** 条件は真偽で答えの集合が固定、同じ出力（状態）に複数の条件（問い）という形が本設計の得意な形そのもの。`_sm_judge_conditions` が条件 1 件を boolean 1 問にして `judge.evaluate` を呼び、答えを `--evals` へ載せる。使うのはローカル定義（`relative_cost` 0）のときだけで、judge の失敗・確度不足（`_SM_JUDGE_MIN_CONFIDENCE`、既定 0）は従来の制御応答へ倒し、証跡（`condition_judge_*`）に残す。しきい値の既定は §6 の実測後に決める |
| agent-project の `route`（書込先の自動ルーティング） | **配線済み（2026-09-19）。** `request.route_judge` が候補リポジトリを `choice` の選択肢に、`other` を「どの候補にも属さない」にして 1 問で訊く。`other` は ""（書込先なし）に写す——RO3 の「決められないと言えるか」が明示の選択肢になる。judge が決めなければ従来の `_route_agent_prompt` |
| agent-flow の `filter`（単一基準・`decision` 無し） | **配線済み（2026-09-19）。** `agent.filter_judge` が依存 1 件 = 候補 1 件で boolean を 1 問ずつ訊き、`kept` を作る。依存が 1 件（本文に候補が並ぶ形）は候補を列挙できないので生成経路。多基準（`decision` あり）は従来どおり抽出 → 機械判定 |
| agent-project の `assess` | 次の候補。c / r / a の 3 段の採点は `score` 型そのもの |

## 6. 測ってから決めること

本設計は ollama の無い環境で書いたので、**gemma4:e4b での実測は未着手**。入れる前に
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
