# kiro-projects — フィードバックの全体還元（統一学習バス）と verify 品質改善 設計案

> 対象ツール: `tools/kiro-projects/`（`kiro-projects.py`）
> 対になる文書: [`kiro-flow-feedback-reduction-design.md`](./kiro-flow-feedback-reduction-design.md)
> （gitlab イシューの人コメントを捕捉・emit する発生源側／flow-planner への `--learnings` 受け口）
> ステータス: Draft（設計案。実装は未着手。まず方針合意を取る段階）

## 0. 目的（2 つの問題）

- **問題A — フィードバックの局所性**: gitlab executor で作業しているとき、個々のイシューに投稿した
  ユーザーコメントは「そのイシュー内（同一タスクの次の試行）」にしか活きず、**同様のタスクに還元されない**。
  さらに指摘が **タスク分解（plan）や verify の再考にまで及ばない**。
- **問題B — verify CLI の品質**: 「verify を CLI コマンドで行い不確実性をなくす」思想は正しいが、
  ユーザーが CLI を書くのは難しく、**自動生成された verify の品質がイマイチ**。

本書は kiro-projects 側＝**学習ストア（統一フィードバックバス）の本体**と **verify 合成の品質**を扱う。
発生源（gitlab コメント捕捉）と分解（flow-planner）は kiro-flow 側の同名文書に置く。

## 1. 診断（現状の所在）

### 1.1 問題A — feedback には非対称な 2 チャネルがある

| チャネル | 捕捉 | 到達範囲 |
|----------|------|----------|
| **(A) 人ゲート/revise/needs** | `append_decision(learn=)`（`1626/4425/4439/4629`）で `decisions/*.md` に learn ルール化 | **横断**（recall が拾う） |
| **(B) gitlab 却下 guidance** | `task.feedback` に注入するだけ（`_settle_failure` 却下枝 `3378-3391`） | **同一タスクの次の試行のみ** |

(A) は既存の良くできた横断機構が支える:
`find_learned_resolution()`（`905-922`）＋`_best_learn_match()`（`886-902`）が別タスクを Jaccard 照合、
`linked_learnings_context()`（`2184-2200`）が別プロジェクト、`promote_learnings()`（`1435-1488`）が ltm-use home へ昇格。

(B) は `_settle_failure` 却下枝に **`append_decision(learn=)` が無い**ため横断しない（人ゲート枝には有る）。
かつ却下枝は `status="ready"` で **act を再実行するだけ**で plan/verify に戻らない。これが問題A の根本原因。

### 1.2 問題B — verify 合成が単発 LLM ＋ 構文チェックのみ

「不確実性をなくす verify」の実装は kiro-projects 側の verify（終了コード 0=PASS。`run_verify:1878-1888`）。
用意経路（`ensure_verify:2038-2058`）は ①人が直書き（最良）②`verify_template` 決定的展開（`1965-1984`・LLM 不要・5 種のみ）
③`accept:` → **LLM が 1 行合成**（`synth_verify:2020-2035`）。

合成経路の品質ギャップ:

- **文脈が無い**: `synth_verify` は `title+accept` だけ。テスト基盤・ファイル構成・触ったパスを知らず **grep 退化**。
- **品質ゲートが構文だけ**: `_looks_like_shell_command()`（`2003-2017`）は全角記号除外＋`sh -n`（非実行）のみ。
  `true`・恒真式・既存状態マッチ・履歴一致がそのまま done 根拠になり得る。
- **検証者の検証が無い**: 「変更前 fail・変更後 pass」を実行確認していない。`require_progress`（`3455-3473`）は
  「何か変わったか」までで「この検査がこの変更を追えているか」は見ない。
- **学習・再利用が無い**: 良質な verify を似たタスクへ再利用する経路が無い。

## 2. 設計の背骨 — 統一学習バス（両問題共通）

**人のあらゆる判断・指摘を 1 本の学習ストア（`decisions/`＋ltm）へ集約し、複数の消費者が読む**多対多構造にする。
既存の learn 機構（Jaccard recall・ltm 昇格・links 横展開）をそのまま土台に流用する。

```
   ┌─────────── 捕捉（統一・蒸留）────────────┐
   │ 人ゲート/revise/needs ─┐                 │
   │ gitlab コメント(却下/承認/作業中)┤→ 蒸留 → decisions/*.md（learn/avoid/verify種別）
   │ 却下 guidance          ┘  (episodic→        ＋ ltm-use home（横断・永続）
   └────────────────┬──────── semantic/procedural)┘
                    │ recall（Jaccard / ltm）
     ┌──────┬───────┼────────┬────────────┬──────────┐
     ▼      ▼       ▼        ▼            ▼          ▼
  次の act plan(分解) verify合成 triage/intake  cohort兄弟  （§3/§4）
  (既存)  (§3.3新)  (§3.3/§4新)  (既存recall)   (§3.5新)
```

### 2.1 蒸留 — 生コメントを一般化ルールへ（episodic → semantic/procedural）

生コメントをそのまま learn にすると一回限りの指示になり Jaccard に乗りにくい。ltm-use v5 の
**consolidate（エピソード→意味記憶）**に倣い `distill_learn()` を新設:

- 入力: task.title / accept / 生コメント（gitlab の `guidance`/`notes`、または needs 記入）。
- 出力: `<一般化した条件（title パターン）> :: <再利用可能な指針>`（固有名詞を種別・パターンへ引き上げる）。
- 例: 「#123 のログイン画面、実サーバでなく localhost で e2e してるのでダメ」→
  `e2e/統合テスト系 :: e2e は実サーバ配備で実施。localhost 実行を verify で禁止`。
- **durable 判定**: 作業中コメント（kiro-flow §2.2 の逐次 notes）は一過性の相談を含むため、蒸留段で
  「再利用可能な恒久指示か」を判定し、一過性は落とす。
- 実装は kiro-cli 委譲（1 呼び出し・有界）。**失敗時は生 verbatim で learn**（劣化しても現状より前進）。
- 副産物として **verify に効く指針**（「done はここを見よ」）を `verify` 種別 learn として分離（§4.4 が読む）。

## 3. 問題A の設計 — フィードバックを全体へ還元する

### 3.1 捕捉の統一 — gitlab コメントを learn 化する（最小・最大レバレッジ）

**最小変更**（横断の起点）: `_settle_failure` 却下枝（`3378-3391`）に learn 捕捉を足す。

```python
if guidance:
    task.drop("feedback")
    task.extra.append(("feedback", guidance.replace("\n", " ⏎ ")))
    if cfg.learn_capture:                                       # ← 追加
        append_decision(cfg, task.id, "gitlab", action="gitlab-reject",
                        reason=guidance,
                        learn=(task.title, distill_learn(cfg, task, guidance)))  # §2.1
```

これだけで既存 recall（横断）・links（横プロジェクト）・ltm 昇格が**そのまま**効く。

**却下以外の通常コメントも learn 化する**（ユーザー要望の要点）:
kiro-flow §2 が emit する `data.notes`（承認決着・作業中増分）を、承認結果の消化時と result ポーリング時に取り込み、
**却下と同じ蒸留→learn/avoid ストア**へ流す。振り分けは既存の二分に合わせる:

| 由来 | 種別 | 意味 |
|------|------|------|
| 却下 guidance | `avoid` 寄り＋`learn` | この種は要注意・こう直せ |
| 承認コメント notes | `learn` | この解き方でよい（正例） |
| 作業中コメント notes | `learn`（durable のみ） | 途中で示された方向づけ |

`note.id` で重複排除し、決着時と作業中で二度 learn しない。

### 3.2 適用先の拡張 — act だけでなく plan / verify にも効かせる

既存 recall は `build_request`（次の act）にしか注入していない。読み手を増やす:

- **plan（分解の再考）**: charter モードの plan フェーズ・再計画の分解プロンプトにマッチ learn/avoid を注入し、
  kiro-flow の flow-planner へ **`--learnings`**（kiro-flow §3）で伝搬。分解グラフ自体を変える。
- **verify 合成（verify の再考）**: `synth_verify`/`ensure_verify` に `verify` 種別 learn を注入（§4.2/§4.4）。

### 3.3 昇格ラダー — 単一 reject を「系の再考」へ格上げ

```
①タスク feedback（同一タスク・現状）
 →②横断 learn（似たタスクへ・§3.1）
  →③横プロジェクト link / ltm 昇格（既存）
   →④反復検知で人へ「系の再考」提案（新規）
```

④: 同一 Jaccard クラスタの却下が `--reject-recur`（既定 2）回超過で `needs/<id>.md` を起こし、
「この種の分解 / verify / policy を見直すか？」を人へ。人の決定（`revise --verify` / policy `route`/`gate` /
charter 更新）は通常どおり learn として残り、以後の分解・verify・triage に効く。＝人を介した系の再考。

### 3.4 cohort への還流 — pilot/メンバの却下を兄弟へ返す

gitlab で cohort メンバ/pilot が却下されたら `_settle_failure` から `cohorts/<id>.json` を更新し、
`materialize_cohort_rest`（`371-403`）と同じ経路で**未実行メンバの feedback を上書き**（現状の一方向・人ゲート限定を双方向化）。

## 4. 問題B の設計 — verify を「検証された・文脈付き・学習される」パイプラインに

### 4.1 Red-Green 検証（検証者の検証）← 核・不確実性キラー

合成候補を done 根拠にする前に、「その検査が変更を弁別できるか」を**実行して**証明する。

```
候補 verify cmd
 ├─ baseline（$KIRO_BASE_REV / act 前ツリー）で実行 ⇒ FAIL であるべき（red）
 └─ post-act（現ツリー）で実行            ⇒ PASS であるべき（green）
判定: red かつ green のみ採用。
  baseline で PASS → 恒真式/既存状態/履歴一致 ⇒ 棄却。 post で FAIL → 検査が誤り ⇒ 棄却。
```

- `true`・存在 grep・`git log|grep` の偽 done を**実行レベルで排除**。`require_progress` の上位互換。
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
- 昇格ラダー④（反復却下→系の再考・§3.3）が verify 再考（§4.4）の入口になる。
- red-green（§4.1）は「done はここを見よ」と蒸留された指針（§2.1）を verify 化する受け皿。
- ＝両者は「1 本の学習ループ ＋ 実行検証ゲート」の別断面で、共通背骨に載せると相互強化される。

## 6. 段階導入（フェーズ）

| フェーズ | 内容 | 規模 | 効果 |
|---------|------|------|------|
| **P1** | §3.1 gitlab 却下＋通常コメントの learn 捕捉（`_settle_failure` ＋ notes 取り込み。kiro-flow §2 と対） | 小 | gitlab 指摘が初めて横断 |
| **P2** | §4.1 Red-Green 検証を `ensure_verify`/`run_verify` に追加（opt-out 付き） | 中 | 偽 done を実行排除・verify 底上げ |
| **P3** | §2.1 蒸留 ＋ §4.2 文脈つき合成 ＋ テンプレ拡充 | 中 | learn/verify の一般化と実用度 |
| **P4** | §3.2 plan/verify への recall 注入 ＋ §4.4 verify 学習再利用 | 中 | 分解・verify の再考到達 |
| **P5** | §3.3 昇格ラダー ＋ §3.4 cohort 還流 ＋ §4.3 多候補 | 大 | 系レベルの自己改善 |

P1・P2 は独立に価値があり後方互換（`learn_capture` off・`verify_validate: none` で従来挙動）。まず薄く入れて検証を推奨。

## 7. 未決事項（合意したい論点）

1. **蒸留に LLM を使うか**: 生 verbatim（決定的）と LLM 蒸留（一般化）どちらを既定に。推奨: LLM 蒸留＋失敗時 verbatim。
2. **作業中コメントの取り込み強度**: 一過性コメントの誤 learn を防ぐ durable 判定のしきい値・既定 on/off。
3. **Red-Green のコスト/破壊性**: baseline worktree で回すコスト・副作用 verify の扱い。推奨: opt-out＋読み取り/テスト系に既定適用。
4. **plan への learn 注入の強さ**: 分解を変えるのは強力。有界（件数・文字数上限）にする。
5. **昇格ラダーの閾値**: `--reject-recur` と Jaccard しきい値。既存 `--learn-threshold`(0.5) と揃えるか。
6. **kiro-flow verify ノードの CLI 化**: 本案対象外（kiro-flow §4）。将来判断。

## 8. 影響ファイル（kiro-projects 側）

| ファイル | 箇所 | 変更 |
|----------|------|------|
| `tools/kiro-projects/kiro-projects.py` | `_settle_failure` 3378-3391 | §3.1 learn 捕捉・notes 取り込み・§3.3 反復検知 |
| 〃 | `read_reject_guidance` 2778-2804 | notes（承認/作業中）の読み取り追加 |
| 〃 | `synth_verify` 2020-2035 / `_synth_verify_prompt` 1987-1996 | §4.2 文脈注入・§4.3 多候補 |
| 〃 | `ensure_verify` 2038-2058 / `run_verify` 1878-1904 | §4.1 red-green・§4.4 再利用 |
| 〃 | `expand_verify_template` 1965-1984 | §4.2 テンプレ拡充 |
| 〃 | `build_request` 2203-2225 / `find_learned_resolution` 905-922 | §3.2 plan/verify への recall |
| 〃 | cohort 371-403 | §3.4 gitlab 却下の還流 |
| 〃 | `append_decision` 849-872（新 `distill_learn`） | §2.1 蒸留 |

## 付録: 用語

- **learn / avoid**: `decisions/*.md` の横断学習ルール。learn＝どう解くか、avoid＝この種は自動実行させない。
- **ltm-use home**: 実績のある learn の永続ストア（プロジェクト/run 横断）。
- **red-green 検証**: verify が「変更前 fail・変更後 pass」を実行で満たすかの検証（検証者の検証）。
- **procedural memory**: ltm-use v5 の記憶タイプ。手順・パターン（＝検証済み verify）の記録。
