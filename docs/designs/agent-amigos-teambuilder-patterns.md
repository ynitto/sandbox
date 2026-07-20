# team-builder オーケストレーションパターン — 取り込みと拡張提案

team-builder スキルに、論文由来のマルチエージェント・オーケストレーションパターン
（出典: [h5i-python/examples/papers](https://github.com/h5i-dev/h5i-python/tree/main/examples/papers)、全 40 種）を
**agent-amigos のロール構成へ写した設計テンプレ**として取り込んだ記録と、現実装では写せない
パターンのための**拡張提案**をまとめる。

- パターンカタログ: [`.github/skills/team-builder/patterns/`](../../.github/skills/team-builder/patterns/)（`<id>.json`）
- カタログ契約: [`.github/skills/team-builder/references/pattern.schema.json`](../../.github/skills/team-builder/references/pattern.schema.json)
- スキル本体: [`.github/skills/team-builder/SKILL.md`](../../.github/skills/team-builder/SKILL.md)

---

## 取り込み方針（3 バケット）

| バケット | 扱い | 件数 |
|---------|------|:---:|
| **高価値（tier=high）** | JSON 化し、`build-team` 実行時にカタログをプロンプトへ注入して**自動選択**の対象にする | 8 |
| **中価値（tier=medium）** | JSON 化するが自動選択には**入れない**。`--pattern <id>` / commands の `"pattern"` で明示指定したときだけ使う | 25 |
| **現実装で不可** | JSON テンプレは作らず、本書に**拡張提案**として記録する（現状は近似も難しい） | 7 |

40 = 8（high）+ 25（medium）+ 7（拡張待ち）。

### 高価値 8（自動選択）

`self-refine`（磨き上げ）/ `metagpt-sop`（開発 SOP）/ `agentcoder`（生成→テスト→デバッグ）/
`multiagent-debate`（討論→裁定）/ `mixture-of-agents`（多提案→統合）/ `chateval`（多観点審査）/
`self-consistency`（多解→合意）/ `least-to-most`（順次分解）。

直交する 8 つのミッション形（磨く／作る／コード＋テスト／議論で詰める／多様性で底上げ／
多面評価／頑健化／分解積み上げ）を最小構成で覆うことを狙った。

### 中価値 25（JSON のみ・明示指定）

refine 系（reflexion, self-debugging, critic, constitutional-ai）、SOP 系（chatdev, mapcoder,
alphacodium, agentless, parsel）、writing/decomp 系（chain-of-agents, storm, skeleton-of-thought）、
debate 系（mad-divergent, reconcile, persuasive-debate, negotiation, camel）、
ensemble/verify/vote 系（llm-blender, mav-bon, selfcheckgpt, universal-self-consistency,
agent-forest, codet, prd-peer-rank, chain-of-verification）。

高価値の派生・特化で、重複するため自動選択には出さない。用途が明確なときに `--pattern` で使う。

---

## 現実装（agent-amigos）で写せる範囲

agent-amigos が**そのまま**表現できるのは:

- **逐次ロールパイプライン**（要件→設計→実装→検証）＝ `collaborates_with` ＋会話
- **リファインループ**＝ reviewer/approver ＋ 差し戻し（reject）ラウンド、`review_rounds`
- **検証・審査ゲート**＝ `approver` ロール ＋ `done_when: reviewer-approved`（複数 approver 可）
- **集約（gather）**＝ integrator（省略時は自動補充される組み込みロール）
- **静穏収束**＝ `quiescence_turns`（討論の往復を収束させる）

これらに乗るパターン（refine / SOP / verify / 逐次分解）は `feasibility: native` として素直に写せる。

---

## 現実装で写せない機能（横断ギャップ）と拡張提案

多くの sampling/voting・debate・search 系は、次の**プリミティブの欠如**により本来の形では
写せない。カタログでは複数の別ロール＋integrator の判断で**近似**（`feasibility: partial`）しているが、
以下を入れれば「近似」を「忠実」にできる。

### G1. 並列同一シート（`seats > 1`）— ✅ 実装済み

- **実装**: `seats: N`（N≥2）を `normalize_mission` が `<role>#0..#N-1` の具体席ロールへ**展開**する
  （`_expand_seats`）。各席は独立した通常ロールなので、claim / roster / runner / 収束 / 統合 /
  納品の既存機構をそのまま再利用する（コアの協働プロトコルに手を入れない）。`collaborates_with` が
  席化グループを指す場合は席 id 群へ書き換える。1 ノード運用でも self-staff が全席を充足する。
- **使い方**: ロールに `seats: 5` を付けるだけ。sampling/voting/ensembling 系（self-consistency,
  agent-forest, mixture-of-agents, universal-self-consistency 等）が忠実に写せるようになった。
- **残**: 展開は静的（公示時に席数固定）。実行中の席の増減は G5。

### G2. 集約・投票プリミティブ — ✅ 実装済み（基本モード）

- **実装**: 席グループに `aggregate` を宣言でき、integrator が**決定的に集約**する。
  各席は回答を `aggregate_answer`（既定 `ANSWER.md`）へ書き、integrator が
  `deliverable/<group>/AGGREGATE.{md,json}` と manifest の `aggregates` に結果を残す。
  - `majority` — 最頻値（決定的タイブレーク: 得票降順 → 回答昇順）
  - `consensus` — 全席一致の判定（`agreed`）つき最頻値
  - `gather` — 全席の回答を席見出し付きで集める（選抜せず、後段の approver/aggregator が統合）
- **集約モード**: `majority` / `consensus` / **`weighted-vote`**（席の `SCORE` を回答ごとに合計）/
  **`approval-count`**（`SCORE` 最大の候補席を選抜）/ `gather`。weighted-vote と approval-count も
  **実装済み**。写せるパターン: self-consistency / agent-forest（majority）、reconcile（weighted-vote）、
  mav-bon（approval-count）、mixture-of-agents / universal-self-consistency（gather ＋ aggregator/selector）。
- **`done_when: consensus`（早期収束）**: ✅ 実装済み。席グループの最頻回答が `consensus_ratio`
  （既定 0.6・`consensus_min` 席以上）を占めたら、全席の完了を待たず収束する。
- **残（未実装）**: `pairwise-rank`（双方向ペア対戦: llm-blender, prd-peer-rank）は比較が意味判断
  のため決定的集約にできない。ranker ロール（approver）に委ねる設計とする（＝拡張ではなく設計方針）。

### G3. 同期ラウンド（ラウンドバリア）— ✅ 実装済み

- **実装**: 席グループに `rounds: N` を付けると、各席が `round-<k>.md` を 1 ラウンドずつ書き、
  runner が**全席の round-(k-1) が揃うまで round-k へ進めない**バリアを課す（`_rounds_turn`）。
  最終ラウンドの主張が ANSWER.md になり declare_done する。バリアはファイル存在で判定するので
  非同期のターンループ上でも決定的に同期する（差し戻しラウンドとは別軸の「議論ラウンド」）。
- **早期終了**: `done_when: consensus` と併用すると、前ラウンドで席が合意（`consensus_ratio` 到達）
  した時点で残りラウンドを打ち切って確定する。
- **写せるようになったパターン**: multiagent-debate / persuasive-debate（seats+rounds+judge）、
  reconcile（seats+rounds+weighted-vote+consensus）。裁定は judge（approver）か aggregate で締める。
- **残**: 通信トポロジ制御（exchange-of-thought の bus/star/ring/tree）は未対応。全席が全席の前
  ラウンドを読む全結合のみ。複数ロールにまたがる討論（affirmative/negative を別ロールにする形）は
  1 席グループ内の席差（ミッション文で役割を割り当てる）で表現する。

### G4. 探索木・分岐評価（branch → score → prune）

- **現状**: 分岐・バックトラック・ビーム/ MCTS のような探索構造が無い。ラウンドは線形。
- **要るパターン**: tree-of-thoughts（BFS＋ビーム）, graph-of-thoughts（分岐＋マージ）,
  lats（MCTS）。
- **近似**: 現状は困難（探索の状態管理そのものが無い）。→ **拡張待ち**（カタログにも入れない）。
- **拡張提案**: 2 案。(a) 探索は agent-flow（タスクグラフ／Dynamic Workflow）の領分として
  **team-builder が agent-flow プランを出力する**経路を用意する（住み分け）。
  (b) agent-amigos 内に軽量な「候補ノード＋スコアラー＋選抜」の**有界探索ロール群**を
  組み込みパターンとして持つ（分岐数・深さを上限付きで）。まずは (a) を推奨。

### G5. 実行中の動的チーム編成（recruit / prune）— ✅ 実装済み（基盤）

- **実装**: owner 操作 **`restaff`**（CLI `agent-amigos restaff <mid> --add <roles> --prune <ids>`、
  commands `{"command":"restaff", …}`）。
  - **add**: 追加ロールを `normalize_added_roles` で検証・席展開して `roles/<id>.json` を書く。
    新ロールは通常どおり募集・充足される（1 ノードなら self-staff が拾う）。追加で必須ロールが
    増えれば収束が再び開き、統合し直す。
  - **prune**: `pruned/<id>.json` を書く。剪定ロールは `active_roles` で収束・募集・ターン実行から
    外れ、担当 amigo は次ターンで exit する。
- **写せるようになったこと**: exchange-of-thought は topology（下記）で **native**。AgentVerse
  （再編成）・meta-prompting（専門家の追加）は **restaff の add を owner／制御ループが呼ぶ
  ワークフロー**として表現する（team-builder を途中で再度呼んで差分編成 → restaff）。DyLAN 的
  剪定は席の評価（`SCORE`/aggregate）＋ restaff の prune で表現する。
- **残**: 完全自律の自己組織化（毎ラウンド自動で剪定・招集する制御ループ）は restaff を叩く
  上位ワークフロー側の責務とする（コアは編成変更の**プリミティブ**までを提供）。

### 通信トポロジ制御（同期討論の拡張）— ✅ 実装済み

- **実装**: 討論席（rounds>=1）に `topology`（`complete`（既定）/ `ring` / `star` / `tree`）を
  指定でき、各席が毎ラウンド**読む相手を制限**する（`topology_neighbors`）。ラウンドバリアは
  全席同期のまま（読む範囲だけを絞る）。exchange-of-thought の bus/star/ring/tree に対応。

---

## 拡張待ちパターン（現状はカタログ非搭載）

| pattern | 主因ギャップ | 備考 |
|---------|-------------|------|
| tree-of-thoughts | G4（探索木） | まず agent-flow 委譲を推奨 |
| graph-of-thoughts | G4（分岐＋マージ） | 同上 |
| lats | G4（MCTS） | 同上 |
| dylan | 自律制御（G3+G5+G2 は実装済み） | 席評価（SCORE）＋ restaff prune を叩く上位ループで表現 |
| agentverse | 自律制御（G5 は実装済み） | 再編成は restaff add、判定は approver。自律ループは上位 |
| meta-prompting | 自律制御（G5 は実装済み） | 専門家追加は restaff add。司会ループは上位 |

exchange-of-thought は topology（G3 拡張）で **native 化してカタログに追加済み**。
dylan / agentverse / meta-prompting は「restaff を叩く上位ワークフロー」で表現でき、残るのは
その**自律制御ループ**（コア外）。純粋に**現実装で写せない**のは G4（探索木）系のみ。

---

## 実装の優先順位（推奨）

1. ~~**G1 seats>1** ＋ **G2 集約モード**~~ — ✅ **実装済み**（seats 展開 ＋
   majority/consensus/weighted-vote/approval-count/gather ＋ `done_when: consensus`）。
   sampling/voting/ensembling 系の大半が「近似」から「忠実」になった。コアの原則（状態のファイル
   導出・決定的 claim）は据え置き、seats はロール展開・集約は integrator の拡張で実現した。
   - `pairwise-rank` のみ、比較が意味判断のため決定的集約にせず ranker ロールに委ねる設計とした。
2. ~~**G3 同期ラウンド**~~ — ✅ **実装済み**（`rounds: N` ＋ ラウンドバリア ＋ consensus 早期終了）。
   debate 系（multiagent-debate / persuasive-debate / reconcile）が忠実になった。残は通信トポロジ制御。
3. ~~**G5 restaff**~~ ＋ ~~通信トポロジ~~ — ✅ **実装済み**。restaff（add/prune）で動的編成の
   プリミティブを、topology（complete/ring/star/tree）で討論の伝播制御を提供。AgentVerse/DyLAN/
   meta-prompting は restaff を叩く上位ワークフローで表現する（自律制御ループはコア外）。
4. **G4 探索木** — 唯一の現実装未対応。まず **agent-flow への委譲**（team-builder が agent-flow
   プランを出力）で住み分ける。agent-amigos 本体への探索構造の内蔵は最後。

いずれも「入力の前段（チーム設計）を賢くする」今回の方針の延長で、協働プロトコルのコアは
据え置いたまま段階的に価値を上げられる。
