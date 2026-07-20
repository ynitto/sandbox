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

### G1. 並列同一シート（`seats > 1`）

- **現状**: `normalize_mission` が `seats>1` を明示的に弾く（P0 未対応）。1 ロール＝1 名。
- **要るパターン**: self-consistency, agent-forest, universal-self-consistency, mixture-of-agents,
  multiagent-debate, mav-bon, selfcheckgpt（いずれも「同じ役割の N 人」を並べる）。
- **近似の割り切り**: `solver-a/b/c` のように別 id で複製している（人数が固定・冗長）。
- **拡張提案**: `seats: N` を実装し、roster/claim/status を `<role>#<k>` の名前空間へ拡張。
  1 ロール定義から N 席を募集・充足し、integrator は席をまたいで集約する。決定的 claim・
  lease・away の各規律は席単位に一般化する（既存の名前空間付き claim の自然な拡張）。

### G2. 集約・投票プリミティブ（gather 種別）

- **現状**: integrator は自由記述の統合のみ。多数決・一貫性選抜・ペア順位・確信度重み付けが無い。
- **要るパターン**: self-consistency（多数決）, agent-forest（多数決）, mav-bon（承認数集計）,
  reconcile（確信度重み投票）, prd-peer-rank（ピア信頼度重み）, llm-blender（双方向ペア対戦）,
  codet（テスト通過数順位）。
- **近似**: integrator に「最も一貫する解を選べ」と指示するだけ（集計の再現性が無い）。
- **拡張提案**: integrator に**集約モード**を宣言できるようにする
  （`aggregate: majority | self-consistency | pairwise-rank | approval-count | weighted-vote`）。
  席／候補ロールの成果物を機械的に集計し、決定的に勝者・融合結果を出す。収束条件
  `done_when: consensus`（合意率しきい値）も併せて用意する。

### G3. 同期ラウンド（ラウンドバリア）

- **現状**: 会話は自由記述＋ `quiescence_turns` の静穏収束のみ。「全員が k ラウンド目を出し切って
  から次へ」という同期が無い。
- **要るパターン**: multiagent-debate, reconcile, exchange-of-thought, chateval（逐次ボット読み）,
  persuasive-debate, dylan。
- **近似**: メッセージ往復＋ integrator/judge の裁定で流す（ラウンド境界は曖昧）。
- **拡張提案**: ミッションに `rounds: {count, barrier: true}` を持たせ、各ラウンドで全席の発話が
  揃うまで次ラウンドへ進めない**バリア**を runner に実装する（`round`/差し戻しラウンドとは別軸の
  「議論ラウンド」）。early-stop（合意時）も許す。

### G4. 探索木・分岐評価（branch → score → prune）

- **現状**: 分岐・バックトラック・ビーム/ MCTS のような探索構造が無い。ラウンドは線形。
- **要るパターン**: tree-of-thoughts（BFS＋ビーム）, graph-of-thoughts（分岐＋マージ）,
  lats（MCTS）。
- **近似**: 現状は困難（探索の状態管理そのものが無い）。→ **拡張待ち**（カタログにも入れない）。
- **拡張提案**: 2 案。(a) 探索は agent-flow（タスクグラフ／Dynamic Workflow）の領分として
  **team-builder が agent-flow プランを出力する**経路を用意する（住み分け）。
  (b) agent-amigos 内に軽量な「候補ノード＋スコアラー＋選抜」の**有界探索ロール群**を
  組み込みパターンとして持つ（分岐数・深さを上限付きで）。まずは (a) を推奨。

### G5. 実行中の動的チーム編成（recruit / prune）

- **現状**: ロースターは公示時に確定。実行中のロール追加・削除・専門家の動的招集は無い
  （self-staff は「未充足ロールの補充」だけで、新ロールは生やさない）。
- **要るパターン**: agentverse（実行中に再編成）, dylan（弱いエージェントを毎ラウンド剪定）,
  meta-prompting（司会が専門家をその場で発明）, exchange-of-thought（トポロジ切替）。
- **近似**: ロースターを最初に固定して代替（動的性は失われる）。→ **拡張待ち**。
- **拡張提案**: オーナー操作に `restaff`（ロールの追加・停止）を追加し、`acceptance: agent` と
  同様にオーナーノードの CLI が「途中でチーム構成を見直す」ターンを回せるようにする。
  team-builder を**ミッション途中でも**呼び出し、現状の会話・成果を踏まえて差分編成を提案する
  （「team-builder の常駐化」）。DyLAN 的剪定は席の評価＋停止（G1/G2 と併用）で表現する。

---

## 拡張待ちパターン（現状はカタログ非搭載）

| pattern | 主因ギャップ | 備考 |
|---------|-------------|------|
| tree-of-thoughts | G4（探索木） | まず agent-flow 委譲を推奨 |
| graph-of-thoughts | G4（分岐＋マージ） | 同上 |
| lats | G4（MCTS） | 同上 |
| dylan | G3＋G5＋G2（同期＋剪定＋投票） | 席評価＋停止＋集約が揃えば近づく |
| agentverse | G5（動的再編成） | restaff ＋ team-builder 常駐で表現 |
| meta-prompting | G5（動的専門家招集） | 同上 |
| exchange-of-thought | G3＋G2（トポロジ同期＋投票） | 通信トポロジは別途 |

---

## 実装の優先順位（推奨）

1. **G1 seats>1** ＋ **G2 集約モード** — この 2 つで sampling/voting/ensembling 系の大半
   （self-consistency, agent-forest, mixture-of-agents, mav-bon, llm-blender, codet 等）が
   「近似」から「忠実」になる。カバレッジ最大・実装は既存の名前空間付き claim / integrator の
   自然な拡張で、コアの原則（状態のファイル導出・決定的 claim）を壊さない。
2. **G3 同期ラウンド** — debate 系の忠実度が上がる。runner のターンループにバリアを足す中規模。
3. **G5 restaff ＋ team-builder 常駐** — 動的編成。オーナー操作とスキル呼び出しの組み合わせで、
   コア変更は小さい。
4. **G4 探索木** — まず **agent-flow への委譲**（team-builder が agent-flow プランを出力）で
   住み分ける。agent-amigos 本体への探索構造の内蔵は最後。

いずれも「入力の前段（チーム設計）を賢くする」今回の方針の延長で、協働プロトコルのコアは
据え置いたまま段階的に価値を上げられる。
