# kiro-projects / kiro-flow — ユーザーの決定・指摘を全体へ還元する仕組み ＋ verify 品質改善 設計案

> 対象ツール: `tools/kiro-projects/`（`kiro-projects.py`）／ `tools/kiro-flow/`（`kiro-flow.py` / `executors/gitlab.py`）／
> `.github/skills/gitlab-idd/`（イシュー実行スキル）／ `.github/skills/flow-planner/`
> ステータス: Draft（設計案。実装は未着手。まず方針合意を取る段階）

本書は 1 本の統合設計案。責務境界は「**gitlab executor はコメントを運ぶだけ／蒸留・learn・recall・verify は kiro-projects**」で切る。

## 0. 目的（2 つの問題）

kiro-projects（制御層）＋ kiro-flow（実行層）＋ gitlab executor（＋ gitlab-idd スキルでのイシュー実行）の運用で
顕在化している 2 つの問題を改善する。

- **問題A — フィードバックの局所性**: gitlab executor で作業しているとき、個々のイシューに投稿した
  **ユーザーコメント**は「そのイシュー内（同一タスクの次の試行）」にしか活きず、**同様のタスクに還元されない**。
  さらに指摘が **タスク分解（plan）や verify の再考にまで及ばない**。
  加えて、イシューは **gitlab-idd スキルのエージェント（worker / reviewer）** が実行するため、
  イシュー上にはエージェントの自動コメントが大量に並ぶ。**還元すべきは人のコメントだけ**で、
  エージェントのコメントを無暗に拾ってはならない。
- **問題B — verify CLI の品質**: 「verify を CLI コマンドで行い不確実性をなくす」思想は正しいが、
  ユーザーが CLI を書くのは難しく、**自動生成された verify の品質がイマイチ**。

## 1. 診断（現状の所在）

### 1.1 問題A — feedback には非対称な 2 チャネルがある

| チャネル | 捕捉 | 到達範囲 |
|----------|------|----------|
| **(A) 人ゲート/revise/needs** | `append_decision(learn=)`（`kiro-projects.py:1626/4425/4439/4629`）で `decisions/*.md` に learn ルール化 | **横断**（recall が拾う） |
| **(B) gitlab 却下 guidance** | `task.feedback` に注入するだけ（`_settle_failure` 却下枝 `3378-3391`） | **同一タスクの次の試行のみ** |

(A) は既存の良くできた横断機構が支える: `find_learned_resolution()`（`905-922`）＋`_best_learn_match()`（`886-902`）が
別タスクを Jaccard 照合、`linked_learnings_context()`（`2184-2200`）が別プロジェクト、`promote_learnings()`（`1435-1488`）が
ltm-use home へ昇格。

(B) は `_settle_failure` 却下枝に **`append_decision(learn=)` が無い**ため横断しない。かつ却下枝は `status="ready"` で
**act を再実行するだけ**で plan/verify に戻らない。これが問題A の根本原因。

### 1.2 問題A' — 人コメントの捕捉が却下時のみ ＋ gitlab-idd のエージェントコメント混入リスク

現状 `executors/gitlab.py` が人コメントを読むのは決着時（実質**却下**）だけ（`_rejected_payload:861-886` →
`_human_comments:443-459`）。承認時・作業中の通常コメントは拾われない。

そして**より重要な欠落**: `_human_comments` の「人コメント」判定は緩く、gitlab-idd のエージェントコメントを取りこぼし切れない。

```python
# gitlab.py _human_comments（443-459）現状の除外条件
if n.get("system"): continue                              # ①GitLab system note
if "gitlab-idd:creator-node-id" in body: continue         # ②creator マーカーのみ
if body.startswith("kiro-flow:"): continue                # ③kiro-flow 自身の自動コメント
# → これ以外は「人」とみなして拾う
```

gitlab-idd の worker/reviewer は多数の自由文コメントを投稿する（`worker-role.md`・`non-requester-review.md`）:

| エージェントコメント | マーカー |
|----------------------|----------|
| 🚀 着手コメント | `<!-- gitlab-idd:worker-node-id:… -->` |
| scout（影響調査）マップ | `<!-- gitlab-idd:scout-map -->` |
| clarification / approach 提案 | `<!-- gitlab-idd:clarification-requested… -->` / `approach-proposed…` |
| 非リクエスターレビュー | `<!-- gitlab-idd:non-requester-reviewed:… -->` |
| **📝 設計記録などの自由文** | **マーカー無しのことがある** |

現状の除外は **creator マーカーしか見ない**ため、worker/reviewer のマーカー付きコメントも、
**マーカー無しの自由文（設計記録等）も「人コメント」として拾ってしまう**。これを learn 化すると、
エージェントの独り言が横断学習を汚染する。ユーザー要望「人のコメントのみを反映／エージェントを無暗に拾わない」は
ここを指す。

### 1.3 問題B — verify 合成が単発 LLM ＋ 構文チェックのみ

「不確実性をなくす verify」の実装は kiro-projects 側の verify（終了コード 0=PASS。`run_verify:1878-1888`）。
用意経路（`ensure_verify:2038-2058`）は ①人が直書き（最良）②`verify_template` 決定的展開（`1965-1984`・LLM 不要・5 種のみ）
③`accept:` → **LLM が 1 行合成**（`synth_verify:2020-2035`）。

合成経路の品質ギャップ:

- **文脈が無い**: `synth_verify` は `title+accept` だけ。テスト基盤・ファイル構成・触ったパスを知らず **grep 退化**。
- **品質ゲートが構文だけ**: `_looks_like_shell_command()`（`2003-2017`）は全角記号除外＋`sh -n`（非実行）のみ。
  `true`・恒真式・既存状態マッチ・履歴一致がそのまま done 根拠になり得る。
- **検証者の検証が無い**: 「変更前 fail・変更後 pass」を実行確認していない。
- **学習・再利用が無い**: 良質な verify を似たタスクへ再利用する経路が無い。

## 2. 設計の背骨 — 統一学習バス（両問題共通）

**人のあらゆる判断・指摘を 1 本の学習ストア（`decisions/`＋ltm）へ集約し、複数の消費者が読む**多対多構造にする。
既存の learn 機構（Jaccard recall・ltm 昇格・links 横展開）をそのまま土台に流用する。

```
   ┌────────── 捕捉（人コメントのみ・統一・蒸留）──────────┐
   │ 人ゲート/revise/needs ─┐                              │
   │ gitlab の人コメント     ┤→ §3.1 人/エージェント判別 → 蒸留 → decisions/*.md（learn/avoid/verify種別）
   │  (却下/承認/作業中)     ┘  (episodic→semantic/procedural)   ＋ ltm-use home（横断・永続）
   └───────────────┬──────────────────────────────────────┘
                   │ recall（Jaccard / ltm）
     ┌──────┬──────┼───────┬───────────┬──────────┐
     ▼      ▼      ▼       ▼           ▼          ▼
  次の act plan(分解) verify合成 triage/intake cohort兄弟  （§3/§4）
  (既存)  (§3.3新) (§3.3/§4新) (既存recall)  (§3.5新)
```

### 2.1 蒸留 — 生コメントを一般化ルールへ（episodic → semantic/procedural）

生コメントをそのまま learn にすると一回限りの指示になり Jaccard に乗りにくい。ltm-use v5 の
**consolidate（エピソード→意味記憶）**に倣い `distill_learn()` を新設:

- 入力: task.title / accept / **人コメント**（§3.1 で人と確定したもののみ）。
- 出力: `<一般化した条件（title パターン）> :: <再利用可能な指針>`（固有名詞を種別・パターンへ引き上げる）。
- 例: 「#123 のログイン画面、実サーバでなく localhost で e2e してるのでダメ」→
  `e2e/統合テスト系 :: e2e は実サーバ配備で実施。localhost 実行を verify で禁止`。
- **durable 判定**: 作業中コメント（§3.2）は一過性の相談を含むため、蒸留段で「再利用可能な恒久指示か」を判定し落とす。
- 実装は kiro-cli 委譲（1 呼び出し・有界）。**失敗時は生 verbatim で learn**（劣化しても現状より前進）。
- 副産物として **verify に効く指針**（「done はここを見よ」）を `verify` 種別 learn として分離（§4.4 が読む）。

## 3. 問題A の設計 — 人の指摘のみを全体へ還元する

### 3.1 人コメントのみを確実に拾う（gitlab-idd 実行を前提にした判別）← 最優先要件

gitlab-idd で worker/reviewer エージェントがイシューを実行する以上、「人／エージェント」判別は
**マーカー頼みでは不十分**（マーカー無しの自由文がある）。**著者アカウントベースの正判定**を主軸に、多層で守る。

**判別の 3 層（AND で人と確定した時だけ learn 対象）**:

1. **著者アカウントで正判定（主軸）**: コメントは、著者が**人間アカウント**のときだけ人コメントとみなす。
   - GitLab note の `author.bot == true`（プロジェクトアクセストークン等のボットユーザー）は**除外**。
   - 設定 `gitlab.agent_authors`（gitlab-idd の worker/reviewer/requester が動くアカウントの username/id 一覧）に一致すれば**除外**。
   - 設定 `gitlab.human_reviewers`（人間レビュアーの allowlist）があれば**それ以外を除外**（最も厳密）。
2. **プロトコルマーカーで除外（常時）**: 著者に依らず、次を機械コメントとして**除外**。
   - GitLab `system` note。
   - 本文が `kiro-flow:` で始まる（executor 自身の自動コメント）。
   - 本文が **いずれかの `<!-- gitlab-idd:* -->` マーカーを含む**（`creator-node-id` / `worker-node-id` /
     `scout-map` / `clarification-requested` / `approach-proposed` / `non-requester-reviewed` など。現状 creator のみ→**全マーカーへ拡張**）。
3. **エージェント著者の自動学習（per-issue）**: executor は自分が起票したイシュー上で
   `gitlab-idd:worker-node-id` / `non-requester-reviewed` マーカーコメントの**著者アカウントを抽出**し、
   そのアカウントの**マーカー無しコメント（設計記録等）も同イシューではエージェント扱い**にする。
   → 手設定が無くても、マーカー無し自由文を著者経由で漏れなく除外できる。

**保守的既定（precision 優先）**: 「無暗にエージェントを拾わない」を最優先し、**人と正判定できないコメントは
learn しない**。allowlist も agent_authors も無く、bot 判定もマーカーも付かない曖昧なコメントは、
既定では**取り込まない**（`gitlab.trust_unmarked_comments: true` で従来寄りの緩い取り込みへ opt-in。
recall と precision のトレードオフを設定で選ばせる）。

> 責務: 判別に必要な生データ（各コメントの `author.{id,username,bot}` / `system` / 本文 / `note.id`）は
> **executor が emit するだけ**。人/エージェントの最終判定と学習は kiro-projects 側で行う（executor に知能を入れない）。

### 3.2 捕捉の統一 — 却下・承認・作業中の人コメントを構造化 emit

§3.1 で人と確定できるよう、executor は決着以外も含めて**人コメント候補を著者情報つきで運ぶ**。

- **決着時の対称化**: `_rejected_payload` に加え承認 payload でも人コメント候補を `data.notes` に載せる
  （却下＝`avoid` 寄り、承認＝`learn` 寄りに下流が振り分け）。従来 done の result は人コメントを運ばず正例を捨てていた。
- **作業中の逐次 emit**: park & poll の監視（`watch_interval` 既定 90 秒）に相乗りし、前回以降に増えた人コメントを
  `data.notes` 増分として運ぶ（GitLab API は `get-comments` 1 本・既存バッチに畳む＝負荷増やさない）。
- **emit する構造**: `data.notes = [{note_id, author:{id,username,bot}, system, body, ts}]`。生のまま運び、
  人/エージェント判別（§3.1）と蒸留（§2.1）は下流。`note_id` で決着時・作業中の**重複排除**。

### 3.3 適用先の拡張 — act だけでなく plan / verify にも効かせる

既存 recall は `build_request`（次の act）にしか注入していない。読み手を増やす:

- **plan（分解の再考）**: charter モードの plan フェーズ・再計画の分解プロンプトにマッチ learn/avoid を注入し、
  kiro-flow の flow-planner へ **`--learnings`**（構造化・有界）channel で伝搬。要求本文に畳むと分解後の各ノードに
  薄まるため、planner の**戦略選定**段（`.github/skills/flow-planner/`）に独立注入し、分解グラフ自体を変える
  （例: 「この種は 1 段細かく割れ」「集約前に verify gate を挟め」「この分割は避けよ」）。件数・文字数は有界化。
- **verify 合成（verify の再考）**: `synth_verify`/`ensure_verify` に `verify` 種別 learn を注入（§4.2/§4.4）。

### 3.4 昇格ラダー — 単一 reject を「系の再考」へ格上げ

```
①タスク feedback（同一タスク・現状）
 →②横断 learn（似たタスクへ・§3.1/§3.2）
  →③横プロジェクト link / ltm 昇格（既存）
   →④反復検知で人へ「系の再考」提案（新規）
```

④: 同一 Jaccard クラスタの却下が `--reject-recur`（既定 2）回超過で `needs/<id>.md` を起こし、
「この種の分解 / verify / policy を見直すか？」を人へ。人の決定（`revise --verify` / policy `route`/`gate` /
charter 更新）は learn として残り、以後の分解・verify・triage に効く。＝人を介した系の再考。

### 3.5 cohort への還流 — pilot/メンバの却下を兄弟へ返す

gitlab で cohort メンバ/pilot が却下されたら `_settle_failure` から `cohorts/<id>.json` を更新し、
`materialize_cohort_rest`（`371-403`）と同じ経路で**未実行メンバの feedback を上書き**（現状の一方向・人ゲート限定を双方向化）。

## 4. 問題B の設計 — verify を「検証された・文脈付き・学習される」パイプラインに

### 4.1 Red-Green 検証（検証者の検証）← 核・不確実性キラー

合成候補を done 根拠にする前に「その検査が変更を弁別できるか」を**実行して**証明する。

```
候補 verify cmd
 ├─ baseline（$KIRO_BASE_REV / act 前ツリー）で実行 ⇒ FAIL であるべき（red）
 └─ post-act（現ツリー）で実行            ⇒ PASS であるべき（green）
判定: red かつ green のみ採用。 baseline で PASS→恒真/既存/履歴一致で棄却。 post で FAIL→誤りで棄却。
```

- `true`・存在 grep・`git log|grep` の偽 done を**実行レベルで排除**。`require_progress`（`3455-3473`）の上位互換。
- 実装: 既存の baseline rev ＋ worktree-cache（`KIRO_GIT_CACHE_DIR`）で baseline worktree を生やして red を取る。
- **安全弁**: 破壊的/高コスト verify は `- verify_validate: none` で opt-out。red が取れない条件は自動 done させず人へ（§4.5）。

### 4.2 リポジトリ文脈つき合成 ＋ テンプレ拡充

- `synth_verify` に**文脈**を渡す: 検出したテスト/ビルド基盤（`package.json` scripts・`pytest`・`Makefile`・CI 設定）・
  タスクの `- paths:`/差分・過去タスクの verify 例。→ grep 退化を防ぐ。
- `verify_template` を**拡充**（決定的＝最高品質・合成より優先）: `test-passes :: <cmd>` /
  `endpoint-returns :: <url> :: <status>` / `builds :: <cmd>` / `exit-zero :: <cmd>` など。

### 4.3 多候補 ＋ 敵対的妥当性（kiro-flow パターンの適用）

1 行でなく **N 候補**を出し、§4.1 の red-green ＋敵対的批評（「PASS でも acceptance を満たさない false-done を 1 つ挙げよ」）で
選別。kiro-flow の `generate-and-filter`/`adversarial-verification` を **verify コマンドの著作そのもの**に適用
（verify 著作を小さな kiro-flow グラフにできる）。

### 4.4 verify の学習・再利用（問題A との接続）

- red-green を通った verify を種別キーで `decisions`/ltm に **procedural memory**（ltm-use v5 `memory_type: procedural`）として保存
  （`verify_source: synth+validated`）。人が書いた verify を**シード**に最優先取り込み。
- 新規 `accept:` では**まず似た過去タスクの検証済み verify を recall**してから合成にフォールバック。
- 人が `revise --verify` で直した verify も learn（procedural）になり similar タスクへ伝播（§3 のループを verify に適用）。

### 4.5 劣化時のフォールバック — 「空欄」でなく「検証済み草案」を人へ

red-green が取れない/良候補が無いときは自動 done せず人へ。ただし**候補コマンドと red-green 実行証跡を添えて** `needs/<id>.md` へ。
人は白紙から書かず**草案をレビュー/微修正して approve**。「CLI を書く難しさ」を「生成→検証→草案提示→承認」に置換する。

## 5. 2 つの問題の接続点

- 統一ストア（§2）に検証済み verify（procedural・§4.4）が相乗り。
- 昇格ラダー④（反復却下→系の再考・§3.4）が verify 再考（§4.4）の入口になる。
- red-green（§4.1）は「done はここを見よ」と蒸留された指針（§2.1）を verify 化する受け皿。
- ＝両者は「1 本の学習ループ ＋ 実行検証ゲート」の別断面で、共通背骨に載せると相互強化される。

## 6. 段階導入（フェーズ）

| フェーズ | 内容 | 規模 | 効果 |
|---------|------|------|------|
| **P0** | §3.1 人/エージェント判別の厳格化（`_human_comments` を全マーカー＋著者 bot/agent_authors＋per-issue 自動学習へ） | 小 | エージェントコメントの誤取込を止める（他フェーズの前提） |
| **P1** | §3.2 人コメントの統一 emit ＋ §3.1 経由の learn 捕捉（`_settle_failure`／notes 取り込み） | 小 | 人の指摘が初めて横断 |
| **P2** | §4.1 Red-Green 検証を `ensure_verify`/`run_verify` に追加（opt-out 付き） | 中 | 偽 done を実行排除・verify 底上げ |
| **P3** | §2.1 蒸留 ＋ §4.2 文脈つき合成 ＋ テンプレ拡充 | 中 | learn/verify の一般化と実用度 |
| **P4** | §3.3 plan/verify への recall 注入 ＋ §4.4 verify 学習再利用 | 中 | 分解・verify の再考到達 |
| **P5** | §3.4 昇格ラダー ＋ §3.5 cohort 還流 ＋ §4.3 多候補 | 大 | 系レベルの自己改善 |

**P0 が全ての前提**（人コメントのみを拾う土台）。P0・P1・P2 は独立に価値があり後方互換
（`learn_capture` off・`verify_validate: none`・`trust_unmarked_comments` で従来挙動に寄せられる）。まず P0→P1→P2 を薄く入れて検証を推奨。

## 7. 未決事項（合意したい論点）

1. **人/エージェント判別の既定強度**: precision 優先（人と正判定できないコメントは既定で learn しない）で始めるか、
   `trust_unmarked_comments` の既定を on/off どちらにするか。推奨: **既定 off（precision 優先）**。
2. **agent_authors の与え方**: 手設定 allowlist/denylist と per-issue 自動学習（§3.1-3）の優先順。推奨: 自動学習＋手設定で上書き。
3. **蒸留に LLM を使うか**: 生 verbatim（決定的）と LLM 蒸留（一般化）どちらを既定に。推奨: LLM 蒸留＋失敗時 verbatim。
4. **作業中コメントの取り込み**: durable 判定のしきい値・既定 on/off。
5. **Red-Green のコスト/破壊性**: baseline worktree で回すコスト・副作用 verify の扱い。推奨: opt-out＋読み取り/テスト系に既定適用。
6. **plan への learn 注入の強さ**: 分解を変えるのは強力。有界（件数・文字数上限）にする。
7. **昇格ラダーの閾値**: `--reject-recur` と Jaccard しきい値。既存 `--learn-threshold`(0.5) と揃えるか。
8. **kiro-flow verify ノード（LLM 判定）の CLI 化**: 本案対象外（本案の verify は kiro-projects 側＝終了コードゲート）。将来判断。

## 8. 影響ファイル（両ツール）

| ファイル | 箇所 | 変更 |
|----------|------|------|
| `tools/kiro-flow/executors/gitlab.py` | `_human_comments` 443-459 | §3.1 全 `gitlab-idd:*` マーカー除外・著者 bot/`agent_authors` 判定・per-issue エージェント著者学習 |
| 〃 | `_rejected_payload` 861-886 / 承認 payload / park&poll 監視 / `_get_comments` 407-411 | §3.2 承認・作業中コメントの著者付き emit・`note_id` 重複排除 |
| `tools/kiro-projects/kiro-projects.py` | `_settle_failure` 3378-3391 | §3.1 判別後の learn 捕捉・notes 取り込み・§3.4 反復検知 |
| 〃 | `read_reject_guidance` 2778-2804 | notes（承認/作業中・著者付き）の読み取り・人判定 |
| 〃 | `synth_verify` 2020-2035 / `_synth_verify_prompt` 1987-1996 | §4.2 文脈注入・§4.3 多候補 |
| 〃 | `ensure_verify` 2038-2058 / `run_verify` 1878-1904 | §4.1 red-green・§4.4 再利用 |
| 〃 | `expand_verify_template` 1965-1984 | §4.2 テンプレ拡充 |
| 〃 | `build_request` 2203-2225 / `find_learned_resolution` 905-922 | §3.3 plan/verify への recall |
| 〃 | cohort 371-403 / `append_decision` 849-872（新 `distill_learn`） | §3.5 cohort 還流・§2.1 蒸留 |
| `.github/skills/flow-planner/` | 戦略選定段 / `patterns-catalog.yaml` | §3.3 `--learnings` 受け口・戦略調整 variants |
| `tools/kiro-flow/kiro-flow.yaml.example` | `gitlab:` ブロック | §3.1 `agent_authors` / `human_reviewers` / `trust_unmarked_comments` 設定 |

## 付録: 用語

- **learn / avoid**: `decisions/*.md` の横断学習ルール。learn＝どう解くか、avoid＝この種は自動実行させない。
- **ltm-use home**: 実績のある learn の永続ストア（プロジェクト/run 横断）。
- **red-green 検証**: verify が「変更前 fail・変更後 pass」を実行で満たすかの検証（検証者の検証）。
- **procedural memory**: ltm-use v5 の記憶タイプ。手順・パターン（＝検証済み verify）の記録。
- **gitlab-idd マーカー**: `<!-- gitlab-idd:{creator|worker}-node-id / scout-map / clarification-requested / approach-proposed / non-requester-reviewed -->`。エージェントコメントの機械的目印。
