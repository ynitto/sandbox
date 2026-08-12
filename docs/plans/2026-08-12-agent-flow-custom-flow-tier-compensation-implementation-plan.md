# カスタムフローへの tier 補償（split 粒度・review 解禁）実装計画

設計: [`2026-08-12-agent-flow-basic-tier-emergency-operation-design.md`](2026-08-12-agent-flow-basic-tier-emergency-operation-design.md)

## 背景

カスタムフロー（`plan_strategy_user`）は「ノードグラフの**型**」を人が定義し、
`split`→`map`/`reduce` の fan-out、`classify`+route、`verify`+retry で
**実行時にノードが増える**設計になっている。増える部分はエンジンが生成するので、
そこへ tier 補償を掛けても「人が書いたグラフを作り替える」ことにはならない。

`e46ad89`（tier:basic のお膳立て）はこの動的部分へ届いていない。原因は 2 つの実装ギャップで、
どちらも機構は既にあり、繋がっていないだけである。

| ギャップ | 現状 | 本来 |
|---|---|---|
| G1: 分解粒度が tier 非対応 | fan-out 数は split ノードの出力要素数。`split` の呼び出しに tier 指示が無い（planner/evaluator にはある） | basic では 1 手順で終わる大きさまで細かく割らせる |
| G2: review が固定 False | `plan_strategy_user` が `"review": False` を直書き。`tier_review_decision` が呼ばれず、`_emit_reduce_tree` の verify gate 機構が発火しない | 三値（auto）として tier 判定へ通し、basic では gate を挟む |

採用案は設計時の **(a)**: split ノードの呼び出しを tier 対応にする。
fan-out 自体は機械展開（`_expand_splits` は LLM を通らない）のまま保つ——
持久運転で減らしたい判断役の呼び出しを、補償のために増やしては本末転倒になる。

## 完了条件

- `workloads.flow.tier=basic` のとき、カスタムフローの `split` が
  1 手順で完了できる粒度まで細かく分解し、出力契約（JSON 配列のみ）は変わらない。
- 細分化で fan-out クランプに達した場合、**黙って要素を捨てない**（記録して人が気付ける）。
- カスタムフローの review が三値として tier 判定を通り、basic では map→reduce 間に
  verify gate が入る。**平常時（basic 以外）の挙動は変わらない。**
- 人が書いた静的ノードの段は変わらず、動的生成ノードだけが workload の段に従う。
- 既存のユーザー定義フロー契約（厳格検証・形を丸めない）は変わらない。

## 0. 変更前の固定

### 対象

- 既存テストだけを実行し、現在の失敗を記録する。
- PR #698 のブランチへ `origin/main` を先に取り込む（`orchestrate.py` の `_node_entry` が
  interaction 保持と tier 保持で衝突。どちらも追加同士なので両方を残す）。

### 確認コマンド

```bash
cd tools/agent-flow
python3 -m pytest tests/ -q

cd ../agent-dashboard
node test/adhoc-flow.test.js
node test/flow-tiers.test.js
```

## 1. split の分解粒度を tier 対応にする（G1）

### 変更ファイル

- 更新 `tools/agent-flow/agent_flow/patterns.py`
- 更新 `tools/agent-flow/agent_flow/agent.py`
- 更新 `tools/agent-flow/tests/test_methods.py`
- 新規 `tools/agent-flow/tests/test_tier_split.py`

### 実装

`patterns.py` の `TIER_PLANNER_DIRECTIVES` / `TIER_EVALUATOR_DIRECTIVES` の隣へ
`TIER_SPLIT_DIRECTIVES` と `tier_split_directive(tier)` を足す（既存 2 つと同じ形）。

指示文の要件:

- 「各要素は 1 つの短い手順で完了できる大きさまで割る」——判断・複数手順を 1 要素に含めない。
- **出力契約を必ず再確認させる**。split は `LIST_CONTRACT_ROLES`（トップレベルが JSON 配列で
  ないと `_expand_splits` が展開されず run が空振りする）。プロンプト末尾へ後置する追記なので、
  「説明文を付けず配列だけを返す」を指示文の中でもう一度言う。
- 散文を誘発しない。`test_methods.py` には「JSON 契約の役割へ散文を書かせる手法が入っていないこと」
  を固定するテストがあり、同じ規律をこの指示にも適用する。

`agent.py` の `execute_agent()`（`role` テーブルを持つ関数）で、
`_flow_worker_prompt()` が返した prompt へ**後置**する。前置ではなく後置なのは
`e46ad89` が `continue_agent` で確立した規約に合わせるため（スキル側は tier を知らないので
二重注入にならない）。

tier の解決順は `_method_context` と同じにする:

```
str((agent or {}).get("tier") or "") or flow_tier()
```

ノードに固定 tier があればそれを優先し、無ければ agent-control の workload 宣言を使う。
`kind == "split"` のときだけ適用する。

### テスト

- basic のとき split プロンプトへ指示が入り、他の tier では入らない。
- ノード固定 tier がある場合、workload 宣言より優先される。
- 指示が入っても split の出力契約（配列のみ）の記述が残っている。
- `test_methods.py` の JSON 契約役割の散文禁止テストへ split の tier 指示も含める。

## 2. fan-out クランプを可視化する（1 の副作用対策）

### 変更ファイル

- 更新 `tools/agent-flow/agent_flow/continuation.py`
- 更新 `tools/agent-flow/tests/test_planner.py`

### 実装

`_expand_splits` は `items = items[:max(1, max_fanout)]` で**黙って切り捨てている**
（既定 `max_fanout=50`）。1 の細分化で要素数は増えるので、basic 運転では
このクランプに当たる確率が上がり、**成果が静かに欠落する**。

「暴走防止」の目的は保ったまま、切り捨てを観測可能にする。

- 切り捨てが起きたときログを出し、fan-out の replan 理由へ件数を残す
  （`f"data-driven fan-out: +{len(fanout_tasks)}"` に切り捨て件数を添える）。
- 併せて reduce ノードの goal へ「元 N 件のうち M 件のみを処理した」を明記し、
  集約結果が全件のように読まれないようにする。

`max_fanout` を tier で自動的に上げることはしない——上限は「この PC が同時に抱えてよい量」の
話で、分解の細かさとは別の軸だから。上げるかどうかは人の判断に残す。

### テスト

- 要素数が `max_fanout` を超えるとき、切り捨て件数がログと replan 理由に出る。
- 切り捨てが無いときは従来と完全に同じ文言・同じノード id（既存テストの回帰確認）。

## 3. user-plan の review を tier 判定へ通す（G2）

### 変更ファイル

- 更新 `tools/agent-flow/agent_flow/patterns.py`
- 更新 `tools/agent-flow/agent_flow/orchestrate.py`
- 更新 `tools/agent-flow/tests/test_user_plan.py`

### 実装

`plan_strategy_user(plan, request)` に `tier: str = ""` を足し、
`"review": False` の直書きを次へ置き換える。

```
review = plan.get("review", "auto")        # 三値。plan が明示すれば尊重
strategy["review"] = tier_review_decision(review, ["user-defined"], tier)
```

`orchestrate.py` の user_plan 分岐（`plan_strategy_user(user_plan, args.request)`）へ
`flow_tier()` を渡す。

**後方互換が成り立つ理由**: `AGGREGATING_PATTERNS = {"map-reduce", "fan-out-and-synthesize"}`
に `"user-defined"` は含まれないので、`_review_decision("auto", ["user-defined"])` は
`False` を返す。つまり **basic 以外では今日と同じ False**。basic のときだけ
`tier_review_decision` が `True` へ倒し、`_emit_reduce_tree` が map→reduce 間へ
verify gate を挿す。gate が入るのは動的 fan-out 領域だけで、人が描いた静的な形は変わらない。

`plan.review` を明示できるようにするのは、ビルダー側に宣言口を作るとき（§5）の受け口。

### テスト

- 既定（`review` 未指定）× 非 basic → `strategy["review"] is False`（今日と同じ）。
- 既定 × basic → `True`。
- `plan.review: true` × 非 basic → `True`（明示は tier に依らず尊重）。
- `plan.review: false` × basic → `False`（明示 False を tier で覆さない。
  `tier_review_decision` は bool を尊重する既存仕様の確認）。
- review が True のとき、split を含むカスタムフローの fan-out に `<split>-gate` が入る。

## 4. 動的ノードと静的ノードの段の分離を固定する（テストのみ）

### 変更ファイル

- 更新 `tools/agent-flow/tests/test_planner.py`

### 実装

コード変更は不要——現状の仕組みで既に正しく分かれている。**回帰させないために固定する。**

| ノード | tier | 持久運転での挙動 |
|---|---|---|
| 人が描いた静的ノード | ビルダーが固定（PR #698） | 変わらない |
| `map` / `reduce` / `gate` | 無し（継承） | workload の段に従う＝basic |
| retry の作り直しノード | 置き換え元の固定 tier を継承（PR #698） | 元が固定なら変わらない |

「補償が届く部分だけが段を下げる」という設計上のルールが、実装上も自動的に成り立つ。

### テスト

- fan-out で生成された map/reduce/gate に `tier` キーが無い（＝継承する）。
- 固定 tier ノードの retry は固定 tier を保つ（PR #698 の既存テストを参照で足す）。

## 5. 見送る範囲（この計画に含めない）

- **ビルダーへの review 宣言 UI**: §3 で受け口（`plan.review`）だけ作る。
  画面の追加は持久運転の UI と一緒に出す方が説明しやすい。
- **`classify`+route が追加する `-act` ノードの補償**: `continue_stub` が
  `f"{label} 専門処理: {request[:30]}"` という粗い goal で機械生成しており、
  basic 向けの具体化が効かない。既知の残件として設計文書へ記す。
- **split を含むカスタムフローを `map-reduce` パターンとして扱う**:
  そうすると auto が非 basic でも True になり、**平常時の挙動が変わる**。
  効果はあるが後方互換を壊すので、別途判断する。
- **持久運転そのもの**（段の宣言・agentcore への決定関数移設）: 設計文書の段階 1〜2。
  本計画はその前提となる補償の穴を先に塞ぐもので、独立して価値がある。

## 実装順序

1. `origin/main` を PR #698 のブランチへ取り込む（衝突解消）
2. §1 split の tier 指示
3. §2 クランプの可視化（§1 が確率を上げるので同じ PR に入れる）
4. §3 review の解禁
5. §4 段の分離を固定するテスト
6. 設計文書の「制約と未解決点」を実装後の状態へ更新、CHANGELOG

§1〜§4 は `workloads.flow.tier=basic` のときだけ挙動が変わる。
basic を宣言していない端末（＝今日のすべての端末）では**完全に無変更**である。

## リスク

| リスク | 対応 |
|---|---|
| split の tier 指示で出力契約が壊れ、`_expand_splits` が空振りする | 指示文で配列のみを再確認。`LIST_CONTRACT_ROLES` の既存 validator（`data` が配列でなければ失敗）がフェイルクローズとして働く |
| 細分化で fan-out が増え、クランプに当たって成果が欠落する | §2 で可視化。`max_fanout` の自動引き上げはしない |
| review 解禁が平常時の挙動を変える | `"user-defined"` が `AGGREGATING_PATTERNS` に無いため auto は False のまま。テストで固定（§3） |
| verify gate が basic で走り、弱い検証になる | 設計 D1 のとおり verify は持久運転でも basic へ落とさない。段階 3（持久運転本体）で担保する。**それまでは gate も workload の段に従う**ため、持久運転より先にこの計画だけを入れる場合は basic を宣言しないこと |
