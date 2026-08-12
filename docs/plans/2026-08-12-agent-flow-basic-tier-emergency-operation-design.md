# tier:basic を予算逼迫時の緊急運転へ使う 検討

## 背景

main の `e46ad89`（計画承認ゲートと tier:basic のお膳立て）で、agent-flow に
`workloads.flow.tier=basic` を前提とした補償機構が入った。

| 補償 | 実装 | 効果 |
|---|---|---|
| 分解の細かさ | `tier_planning_granularity`: auto→finest | ノードを「1つの短い手順」まで割る |
| 計画指示 | `tier_planner_directive` | goal に対象パス・期待成果・確認方法まで書かせる |
| 評価指示 | `tier_evaluator_directive` | 能力不足の失敗を大きな作り直しでなく分割で対処 |
| 検証 | `tier_review_decision`: auto→常時ON | basic の成果を無検証で集約・終端しない |
| 記録 | `strategy.tier` | 採った tier を run に残す |

commit メッセージの意図は明確で「予算逼迫の緊急時に tier:basic（最小能力）ワーカーを
**普段は任せない役割・作業へ**投入する下地」である。つまりエンジン側は緊急運転を待っている。

一方、管理面（agent-dashboard）は basic を一度も宣言しない。

- `execution-policy.js`: `POLICY_TIERS = ['small', 'medium', 'large']`
  （コメント: 「workload 全体へ適用する方針は、複雑さが分かっている工程専用の basic を選ばない」）
- おまかせ / 節約 / 品質優先の 3 プリセットはすべて最下段が `small`
- カスタムの通常時 tier も basic を拒否する

**結論の骨子: エンジンの補償機構は、管理面から到達する経路が無い。**
今日 basic に到達する手段は control.json の手編集だけで、そのとき補償機構は効くが、
下記 H2〜H6 の穴が開いたままになる。

## 検討結果（先に結論）

| 用途 | 可否 | 理由 |
|---|---|---|
| **予算逼迫時の緊急運転** | **採用を推奨** | 今日 small の下は pause / stop しかない。basic は「止める」の手前の実質的な最下段になる。エンジン側の補償が既にある |
| **節約プリセットの通常 tier** | **見送りを推奨** | 節約は平常時の恒常設定であり緊急時ではない。tier は workload 単位なので flow 以外（project/amigos/audit）へも波及するが、補償があるのは agent-flow だけ |
| 節約プリセットの**上限到達時**の挙動 | **選択肢として提供** | 現在の節約は `on_exhausted: pause`。ここを緊急運転へ切り替える選択は筋が通る |

節約の通常 tier を basic にすると、能力不足を平常運転で恒常的に受け入れることになる。
`e46ad89` 自身が「緊急時」と限定しているとおり、例外運転として設計するのが整合する。

## 今日の最下段と、basic が埋める隙間

```
残予算  100% ────────── 20% ──── 5% ──── 0%（超過）
おまかせ  medium         │  small        │ on_exhausted
品質優先  large  │ medium │  small       │  ├ degrade: small のまま継続
節約      small（常に）                   │  ├ pause: 新規実行を控える
                                          │  └ stop: graceful 停止
                                          ▼
                              【提案】emergency: basic で継続
```

`degrade` は「small のまま継続」でしかない（`degraded` は手動宣言の agent 上書きで、
tier を basic にはしない＝エンジンの補償機構は発火しない）。
`pause` / `stop` は仕事が止まる。**この 3 択の隙間が緊急運転の居場所**である。

## 見つかった穴（実装前に閉じる必要がある）

### H1. 管理面から到達できない

`POLICY_TIERS` と 3 プリセットが basic を出さない。`profiles.save` は方針が参照する tier に
候補が無ければ入力エラーにするため、basic 候補の宣言も前提になる。

### H2. planner / evaluator まで basic に落ちる（最重要）

`profiles.apply` は `workloads.<wl>` へ **1 組の agent_cli/model** を投函し、エンジンの
`_agent_for(purpose)` は purpose 別上書きが無ければ全 purpose がそれを使う。
つまり緊急運転では **planner 自身が basic になる**。

補償機構の中身は「planner に finest まで割らせ、goal を具体的に書かせる」ことなので、
その planner が最小能力になると補償が成立しない。**自己矛盾**であり、緊急運転を入れるなら
planner / evaluator は basic より上へ固定する必要がある。

### H3. verify も basic に落ちる

`tier_review_decision` は basic のとき review を常時 ON にする——「無検証で集約・終端しない」
という正しい意図だが、増えた verify ノードもまた basic で走る。
**弱い検証を増やしても安全にはならない**（誤 pass はループを終わらせ、誤 fail は再作業を焼く）。
verify は basic より上へ固定するのが筋。

### H4. ユーザー定義フローには補償が無い

補償は `_plan_strategy` / `_plan_initial`（planner・pattern 経路）にしか掛からない。
dashboard のカスタムワークフロー（`plan_strategy_user`）は人がグラフを固定しているので、
finest 分解も planner 指示も適用されない。**カスタムフローの basic は無補償**である。
（`when.tiers: ["basic"]` の作業ルール——`restate-task` 等——だけは効くが、分解は変わらない。）

→ PR #698 の適格性カタログは、まさにここで正しい防波堤になる。**カスタムフローに対しては
緊急運転でも下限を緩めない**のが正しい。

### H5. コストが逆転しうる

finest は並列ノード数を ×3 にスケールし、review 常時 ON でノードがさらに増え、
能力不足由来の作り直しも増える。**呼び出し回数は確実に増える。**

これが成立するのは basic 候補が**ローカル実行（`relative_cost: 0`）のときだけ**である。
現在の CLI 定義では ollama 系 / aider / opencode が 0、claude / codex / copilot / cursor /
kiro が 1。basic 候補にクラウド CLI を宣言すると、緊急運転が small より高くつく。

→ 緊急運転の有効化時に「basic 候補がすべて `relative_cost: 0`」を保存時検証すべき。

### H6. PR #698 の適格性カタログと衝突する

`flow-tiers.js` は work / generate / verify / reduce / retrieve の下限を `small` としている。
緊急運転で `policy.steps` に basic が入ると、`decideAutoTier` は「不適格な段を選びうる」と
判定して **auto ノードを small へ切り上げて固定**する。
つまり**緊急運転が黙って無効化される**（PR #698 が入った後に basic を到達可能にすると顕在化する）。

カタログの下限は「平常時の下限」であると再定義し、緊急運転用の下限を別に持つ必要がある。

### H7. tier は workload 単位で、補償があるのは flow だけ

`workloads.<wl>.tier` は workload ごと。緊急運転を全 workload へ一律適用すると、
補償機構を持たない project / amigos / audit / routine / dashboard も basic で走る。
既存の `workloads.<wl>.on_exhausted`（機能別上書き）があるので、**flow だけ緊急運転**という
宣言は既存契約のまま表現できる。

## 提案する緊急運転プロファイル

平常時の下限（PR #698 のカタログ）を、緊急運転では次の 1 段だけ下げる。
**下げるのは「1つの短い手順」に割れる機能に限る**——finest 分解が効く形だから。

| 機能・役割 | 平常時の下限 | 緊急運転 | 理由 |
|---|---|---|---|
| classify / filter / extract / map | 単純作業 | 単純作業 | 変更なし |
| work / generate / reduce / retrieve | 軽量 | **単純作業** | finest 分解で 1 手順へ割れる（補償が効く） |
| verify | 軽量 | **軽量（据置）** | H3。検証を増やす方針なので検証役は落とさない |
| synthesize / judge / split | 標準 | **標準（据置）** | 横断比較・フロー形状の決定は分解で代替できない |
| planner / evaluator | 標準 | **軽量以上へ固定** | H2。補償機構そのものを支える役割 |
| カスタムフローのノード | カタログどおり | **緩めない** | H4。無補償のため |

結果として緊急運転は「大量にある作業ノードを basic へ落とし、少数の判断点だけ最低限の
能力を残す」形になる。PR #698 が既に持つ clamp + pin 機構がそのまま使える。

## 契約への影響（既存契約のままで足りるか）

`on_exhausted` に新しい値を足す必要は**無い**。UI の 1 つの選択肢を、既存 2 契約への
書き分けへ落とすだけで表現できる（execution-policy.js は既に両方へ冪等保存している）。

| UI の選択 | node-budget | agent-profiles |
|---|---|---|
| 品質を下げて継続 | `on_exhausted: degrade` | 最下段 `small` |
| **最小構成で継続（緊急運転）** | `on_exhausted: degrade` | 最下段 **`basic`**（`small` は 1 つ上の段へ） |
| 一時停止 / 停止 | `pause` / `stop` | 変更なし |

例（おまかせ + 緊急運転）:
`steps = [{0.2, medium}, {0.05, small}, {0, basic}]`

一方、H2 / H3 の固定は `workloads.flow.agents.planner` / `.verify` への投函が必要で、
これは「Dashboard の通常 UI からは `agents.<purpose>` を新規作成・編集しない」という
統一実行方針設計の決定と衝突する。**ここが唯一の要判断点**（下記）。

## 比較したアプローチ

| アプローチ | 実装コスト | リスク | 補償の成立 | 推奨度 |
|---|---|---|---|---|
| 最下段に basic を足し、purpose 固定で planner/verify を守る（提案） | 中 | 中 | ○ | ★★★ |
| 最下段に basic を足すだけ（purpose 固定なし） | 小 | 高 | ✕（H2/H3） | ★☆☆ |
| `degraded` に basic 候補を宣言する | 小 | 中 | ✕（tier が basic にならず補償が発火しない） | ★☆☆ |
| エンジンが basic のとき planner/verify を自前で上げる | 中 | 高 | ○ | ★☆☆（選択の知能をエンジンへ戻す＝分業破壊） |
| 節約プリセットの通常 tier を basic にする | 小 | 高 | ✕（H4/H7・平常時の恒常劣化） | ✕ |

## 要判断点

1. **planner / evaluator / verify を basic から守るために、Dashboard が
   `workloads.flow.agents.<purpose>` を書いてよいか。**
   統一実行方針設計は「通常 UI からは編集しない」と決めている。緊急運転は通常 UI の編集ではなく
   システム管理の固定だが、外部設定を上書きする点は同じ。
   代案: 緊急運転を「flow だけ・planner 経路だけ」に絞り、purpose 固定なしで受け入れる
   （補償の質は落ちる）。
2. **緊急運転を有効化できる条件として「basic 候補がすべてローカル（relative_cost 0）」を
   必須にするか、警告に留めるか。**（H5）
3. **適用範囲**: flow のみか、将来の補償実装を待って他 workload へ広げるか。

## 契約テスト観点（実装する場合）

- CT1: 緊急運転を選ぶと最下段が basic になり、node-budget は `degrade` を保存する。
- CT2: basic 候補が無い／クラウド候補を含む場合、保存前にエラー（または警告）になる。
- CT3: 緊急運転中、auto の work / generate ノードが basic へ落ち、small へ切り上げられない（H6）。
- CT4: 緊急運転中も verify / judge / synthesize / split は据置の下限を保つ。
- CT5: 緊急運転中も planner / evaluator が basic で走らない（H2）。
- CT6: カスタムフローのノードは緊急運転でも適格性が緩まない（H4）。
- CT7: 節約プリセットの通常 tier は basic にならない。
- CT8: 緊急運転を解除すると最下段が small へ戻る（冪等・往復可能）。

## Decision Record

| 項目 | 内容 |
|---|---|
| 検討日 | 2026-08-12 |
| 状態 | **提案（要判断点 1〜3 の決定待ち）** |
| 推奨案 | tier:basic を「上限到達時の第4の選択肢＝最小構成で継続（緊急運転）」として、agent-profiles の最下段に足す。平常時の適格性下限は据え置き、緊急運転でのみ work/generate/reduce/retrieve を単純作業へ落とす。planner/evaluator/verify は basic から守る |
| 却下案 | 節約プリセットの通常 tier を basic にする（平常時の恒常劣化・flow 以外へ波及）、`degraded` での代替（tier が basic にならず補償が発火しない）、エンジン側での自衛（分業破壊） |
| 主な理由 | 今日 small の下が pause/stop しかなく、basic は「止める」の手前の実質的な最下段になる。エンジン側の補償は `e46ad89` で既にあり、管理面の到達経路だけが欠けている |
| トレードオフ | 補償（finest ×3 + review 常時 ON）で呼び出し回数は増えるため、basic 候補がローカルでなければコストが逆転する。緊急運転中も判断点ノードは basic に落ちないので、予算消費はゼロにならない |
| 前提 | PR #698（機能・役割別の適格性カタログ）が先に入ること。#698 の `decideAutoTier` に緊急運転の下限を足す形で実装する |
