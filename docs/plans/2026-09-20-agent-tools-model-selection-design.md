# agent-tools の呼び出し先（エージェント・モデル）を依頼文で選ぶ — jev → judge → agent-audit の順で縮退する設計

> 作成 2026-09-20
> 対象: `tools/agent-tools/agentcore/agentcore/modelselect.py` / `herdcli.py`（`select` サブコマンド）/
> `herdconfig.py`（`select.*`）/ `executionresolver.py`（`selector`）/ `tools/agent-flow/agent_flow/agent.py`
> 上位文書: [コンセプト正典](../designs/agent-tools-concept.md) 柱 3（資源効率・P7 / P8）、
> [候補ベース実行の設計](./2026-08-15-agent-tools-candidate-execution-policy-dashboard-design.md) §5.2 / §7、
> [agent-herd judge 設計](./2026-09-19-agent-herd-system-one-judge-design.md)
> 仕様: [agent-herd 仕様書](../specs/agent-herd-spec.md) §5.7 / §5.6 / §9.3、[agentcore 仕様書](../specs/agentcore-spec.md) §2.2

---

## 0. 一枚で

候補ベース実行（selection_policy）は「この workload・この用途で適格な候補」を Compiler が
順位付けし、Resolver は rank 1 位を取る。順位は agent-audit の実測（用途 × 候補の PASS 率と
消費）から出ているので用途単位では正しいが、**同じ用途でも依頼 1 件ごとに要る能力は違う**
——3 行の誤字修正と認証モジュールの書き換えは同じ `worker` である。1 位が常に取ると、
軽い依頼にも上位モデルが出る（P7）か、重い依頼にも安い候補が出て差し戻しが増える。

本設計は、実行直前に**依頼文そのものを見て、適格候補の中から 1 件を選ぶ**口を足す。
判断の材料は候補の特性・トークン量・利用制限で、判断の主体は 3 段で縮退する:

```
  prompt + 候補                            決定的な絞り込み（LLM を呼ばない）
    │                                        quota 枯渇 / レート制限中 / 文脈不足 / 縮退指定のクラウド
    ▼                                        残り 1 件 → そのまま（判断のトークンを払わない）
  状態（task / budget / candidates / policy）+ choice 1 問（criteria = 候補 id、other = none）
    │
    ├─ 1. jev    本家 Jev（TypeSafe AI /v1/systemone）  … API キーがあるとき
    ├─ 2. judge  agent-herd judge（LAN の ollama）        … judge.model が off でなく、指名かローカル候補があるとき
    └─ 3. audit  agent-audit の格付け → policy の rank → relative_cost（決定的・必ず決める）
```

上の段が「使えない」「答えを読めない」「確度が下限に届かない」「どれでもない」のどれかなら
次の段へ倒す。**どの段が決めたかを隠さない**（`stage` と `attempts`）。

## 1. なぜ 3 段か

| 段 | 得意 | 弱点 | だから |
|---|---|---|---|
| 本家 Jev | 較正済みの確率。速く安い。状態を JSON のまま渡せる | 雲。API キーとネットが要る。組織によっては依頼文を外へ出せない | 使えるときの第 1 候補。使えない環境で止めない |
| agent-herd judge | 費用 0・LAN 内。依頼文が外へ出ない | 較正されていない（[初回実測](./2026-09-20-judge-readout-first-measurement.md)）。ollama が要る | 第 2 候補。確度の下限で「決めない」へ倒せる |
| agent-audit の格付け | 決定的・再現可能・LLM 不要。実測に基づく | 依頼 1 件の性質は見えない（用途単位） | 最後の砦。必ず決める |

3 段とも**同じ状態・同じ問い**を見る。Jev の契約（`state` + `{name: {type: choice, instructions,
criteria}}` → `{choice, probabilities, confidence}`）を judge が既に借りているので、
段を替えても問いの立て方を変えずに済む。違いは `other` の扱いだけ（Jev には明示の other が
無いので選択肢 `none` として並べ、judge は `other` を `none` に写す）。

## 2. 判断の材料（状態）

| 区分 | 何を | どこから |
|---|---|---|
| 候補の特性 | `relative_cost`・ローカル / クラウド・自律度（single-shot / tool-loop） | 定義（`agents/<name>.json`、`agentcli.resolve_relative_cost`） |
| 候補の格付け | 用途ごとの PASS 率・平均消費・件数 | `agent-audit ratings --json`（`--ratings`）か、候補自身の `rating` |
| トークン量 | 依頼文の推定トークン数（4 文字 = 1 トークン）、候補の文脈上限（`context_tokens`）、node-budget の消費と上限 | prompt、候補の宣言、`nodebudget.compute_state`（`--workload`） |
| 利用制限 | quota の枯渇・レート制限・復帰時刻・使用率 | node-budget 台帳の観測行（`event: quota | quota_snapshot`。agent-loop / agent-flow / agent-audit が書く） |

依頼文は先頭 1200 文字だけを状態に載せる（全文を送ると判断のトークンが実行のトークンに並ぶ）。
状態には方針の 1 文（「能力・文脈・残量が足りる最も安い候補を選び、判断の要る仕事に上位
モデルを温存する」）を添える——柱 3 の適所適材を問いの中で言う。

## 3. 決定的な絞り込み（LLM の前）

| 落とす条件 | 理由 |
|---|---|
| quota が `exhausted` / `rate_limit`（復帰前） | 選んでも走らない。台帳の観測は消さず、`reset_at` を過ぎれば自然に解ける（agent-audit usage と同じ読み方） |
| `context_tokens` < 依頼文の推定トークン | 入らない依頼を判断に回さない |
| node-budget 超過で `on_exhausted: degrade`、ローカル候補が残る | 縮退は止めずに安い候補で続ける契約（柱 3） |

全部落ちるときは落とさず全候補を判断へ回す。**止めるかどうかは呼び出し側の契約**（Resolver の
park、agent-flow の環境ガード）で、選択の口は選ぶだけ。

## 4. Resolver への差し込み

`resolve_execution(..., selector=…)` を足した。selector は適格候補（`remaining`）が 2 件以上の
ときだけ呼ばれ、返した候補が列にあればそれを選び、無ければ無視して rank 順。決定には
`selector`（stage / confidence / reason / dropped）が残り、receipt の `execution_decision` に写る。

これは Resolver の「availability 除外以外の再採点をしない」（§7）を破らない——順位は
Compiler のまま、**同じ適格集合の中で依頼に足る最小を選ぶ**入力が 1 つ増えただけで、
policy の外へは出ず、park の規則も変わらない。selector が例外を出しても実行は止めない。

agent-flow は `run_agent` の間だけスレッド別の selector を置き、`_control_policy_decision` が
それを Resolver へ渡す。同じ呼び出しの中で Resolver が何度も解決し直しても、判断の LLM は
候補集合ごとに 1 回（memo）。明示指定・run 固定の呼び出しでは置かない（人の決定を上書きしない）。
agent-loop / agent-amigos への配線は同じ 1 行で足せる（今回は未配線）。

## 5. 設定

`~/.agents/agent-herd.yaml` の `select` 節（`agent-herd config set select.… …`）。環境変数では
なく設定ファイルにした理由は judge と同じ（agent-app から WSL 側へ env は届かない）。
API キーだけは環境変数 `TYPESAFE_API_KEY` でも受ける（既存の Jev 利用者の作法）。
`config` の表示ではキーを伏せる。

| 鍵 | 意味 |
|---|---|
| `select.jev.api_key` | 本家 Jev を第 1 段に使う。`off` で使わない |
| `select.jev.endpoint` / `select.jev.model` | ゲートウェイ経由・版固定のため |
| `select.min_confidence` | jev / judge の答えを採る確度の下限（既定 0.6。§7 の実測後に見直す） |

## 6. 採らなかった案

- **Resolver の rank を書き換える**: Compiler の所有物。実行のたびに順位が動くと receipt から
  「なぜこの候補か」が読めなくなる。
- **agentcore が qualifications.json を直接読む**: 「エンジンは agent-candidate-qualifications を
  読まない」（schemas/README）に反する。格付けは `agent-audit ratings --json` を人が渡すか、
  Compiler が焼いた rank を経由する。
- **依頼文の全文を Jev に送る**: 判断のコストが実行のコストに並ぶ。先頭だけで足りない依頼は
  用途と推定トークン数で読める。
- **確度 1.0 の生成で代用する**: judge 設計 §7 と同じ。確度で「決めない」へ倒せることが
  3 段構造の前提。

## 7. 測ってから決めること

- `select.min_confidence` の既定 0.6 は置き値。judge の較正（`readout_eval.py --calibration`）と
  同じ手順で、jev / judge の選択と verify の結果を突き合わせてから決める。
- 絞り込み後に候補が 1 件になる割合。高ければ判断のトークンはほぼ 0 で、選択の効果は
  絞り込みだけで出ている。
- 選んだ候補の PASS 率と平均消費を rank 1 位固定と比べる（同じ台帳・同じ格付けで測れる）。
