# kiro-flow / kiro-projects — ユーザーの決定・指摘を全体へ還元する仕組み ＋ verify 品質改善 設計案

> 作成日: 2026-07-07
> 対象ブランチ: `claude/kiro-flow-feedback-design-pgq77d`
> 対象ツール: `tools/kiro-flow/`（`kiro-flow.py` / `executors/gitlab.py`） ／ `tools/kiro-projects/`（`kiro-projects.py`）
> ステータス: Draft（設計案。実装は未着手。まず方針合意を取る段階）

## 0. 目的

現状の kiro-projects（制御層）＋ kiro-flow（実行層）＋ gitlab executor（委譲）の運用で顕在化している 2 つの問題を改善する。

- **問題A — フィードバックの局所性**: gitlab executor で作業しているとき、個々のイシューに投稿したユーザーコメントは
  「そのイシュー内の作業（＝同一タスクの次の試行）」には活きるが、**同様のタスク（イシュー）には活きない**。
  さらに、指摘が **タスク分解（plan）や verify の再考にまで及ばない**。
- **問題B — verify CLI の品質**: 「verify を CLI コマンドで行い不確実性をなくす」という設計思想は正しいが、
  ユーザーが CLI を書くのは難しく、**自動生成された verify の品質がイマイチ**である。

本書は**設計案**である。実装前に方針の合意を取ることを目的とし、最小挿入点と拡張の段階を示す。

---

## 1. 現状の仕組みと問題の所在（診断）

### 1.1 問題A — フィードバックには「非対称な 2 チャネル」がある

feedback が入る経路は 2 つあり、**到達範囲がまったく違う**。

| チャネル | 入口 | 捕捉のされ方 | 到達範囲 |
|----------|------|--------------|----------|
| **(A) 人ゲート / revise / needs** | `needs/<id>.md` 記入・`approve`・`revise` | `append_decision(..., learn=(title, guide))` で `decisions/*.md` に **learn ルール**化 | **横断**（下記の recall が拾う） |
| **(B) gitlab 却下 guidance** | イシューへの人コメント（却下時） | `task.feedback` に注入するだけ | **同一タスクの次の試行のみ** |

**(A) がなぜ横断するか（既存の良くできた機構）**:

- 捕捉: `append_decision(learn=)`（`kiro-projects.py:849-872`）が `decisions/<id>.md` に `- learn: <title> :: <guide>` を残す。
  書き込み地点は `feedback-resume`(1626) / `approve-done`(4425) / `approve-and-fix`(4439) / `cmd_revise`(4629)。
- 適用（横断到達）:
  - `find_learned_resolution()`（`905-922`）＋ `_best_learn_match()`（`886-902`）: 反復 NG のとき、**別タスク**の
    タイトルを Jaccard 類似で照合し、似ていれば learn を feedback に注入して自動解決（`_settle_failure:3360-3372`）。
  - `linked_learnings_context()`（`2184-2200`）: charter `## links` 先**別プロジェクト**の learn を act 文脈へ。
  - `promote_learnings()` / `write_ltm_memory()`（`1435-1488`）: 実績のある learn を ltm-use home に昇格＝**プロジェクト/run 横断で永続**。
  - `--intake-recall`: 投入・triage 時に過去 `avoid` と類似なら先回りで人へ。

**(B) がなぜ局所で死ぬか（ギャップの正体）**:

`gitlab.py` は却下時に `_human_comments()`（`443-459`）で人コメントを集め、`_rejected_payload()`（`861-886`）で
`data.guidance` と `[gitlab-reject] … やり直し指示: {guidance}` に載せる。これを kiro-projects の
`read_reject_guidance()`（`2778-2804`）が拾い、`_settle_failure()` の却下枝（`3378-3391`）で **`task.feedback` に注入するだけ**。

```python
# kiro-projects.py _settle_failure（3378-3391）— 却下枝（現状）
else:
    task.status = "ready"
    if executor_delegates(cfg):
        guidance = read_reject_guidance(cfg, location == "remote")
        if guidance:
            task.drop("feedback")
            task.extra.append(("feedback", guidance.replace("\n", " ⏎ ")))
    persist_task(cfg, task)
    # ← ここに append_decision(learn=) が「無い」。人ゲート枝(1626/4425/4439/4629)には有る。
```

この 1 か所に `learn=` 捕捉が無いことが、gitlab フィードバックが横断しない根本原因。さらに却下枝は
`status="ready"` にして **act を再実行するだけ**で、`plan`（分解）や `verify`/`accept` の再考には一切戻らない。

**加えて 3 つの構造ギャップ**:

1. **捕捉が却下時のみ**: open のまま approve されたイシューへのコメントや、作業中のコメントは拾われない
   （＝褒め・方向づけの正例が捨てられる）。
2. **適用先が act だけ**: 既存 recall（`find_learned_resolution` / `linked_learnings_context`）は `build_request` 経由で
   **次の act** にしか効かない。plan と verify 合成には届かない。
3. **cohort が一方向・人ゲート限定**: pilot→batch の還流（`materialize_cohort_rest:371-403`）は
   **人の pilot 承認**からのみ発火。gitlab でメンバ/pilot が却下されても `cohorts/<id>.json` や兄弟メンバへは返らない。

### 1.2 問題B — verify の「合成」が単発 LLM ＋ 構文チェックのみ

「CLI で不確実性をなくす」思想を実装しているのは **kiro-projects 側の verify**（終了コード 0 = PASS。
`run_verify:1878-1888`）であり、kiro-flow の `verify` ノードは **LLM 判定**（`execute_kiro(kind="verify"):2669-2725` が
`{"ok":…}` を返させるだけ／CLI 検証は存在しない）。したがって問題B の対象は kiro-projects の verify **合成**。

verify の用意経路（`ensure_verify:2038-2058`）:

1. 人が `--verify '<cmd>'` を直接書く（**最良・最も確実**）。
2. `- verify_template: <名前> :: <引数>` … 決定的展開（`expand_verify_template:1965-1984`、**LLM 不要**）。
   `file-contains` / `file-exists` / `defines` / `diff-contains` / `cmd-succeeds` の 5 種のみ。
3. `- accept: <自然言語>` … **LLM が 1 行シェルを合成**（`synth_verify:2020-2035`、プロンプト `_synth_verify_prompt:1987-1996`）。

**品質ギャップ（synth 経路）**:

- **文脈が無い**: `synth_verify` は `title + accept` だけを見る。リポジトリのテスト基盤（pytest/npm scripts/Makefile）・
  ファイル構成・触ったパスを知らない ⇒ **`grep` 存在チェックに退化**しやすい。
- **品質ゲートが構文だけ**: 唯一の検査 `_looks_like_shell_command()`（`2003-2017`）は「全角記号を含まない」＋
  `sh -n`（構文チェックのみ・非実行）。**「正しい検査か」「意味があるか」は一切見ない**。
  `true`・恒真式・既存の古い状態にマッチする grep・履歴一致 verify が**そのまま done の唯一根拠**になり得る。
- **検証者の検証が無い**: 合成された verify が「**変更前は fail・変更後は pass**」するかを実行して確かめていない。
  `require_progress`（`3455-3473`）は「何か変わったか」までしか見ず、「この検査がこの変更を追えているか」は見ない。
- **学習・再利用が無い**: 人が書いた良質な verify や、過去に通った検査を、似たタスクへ再利用する経路が無い（毎回ゼロから当て推量）。

---

## 2. 設計の背骨（両問題に共通）: 統一フィードバックバス

2 つの問題は独立に見えて、同じ背骨で解ける。**「人のあらゆる判断・指摘を 1 本の学習ストア（`decisions/` ＋ ltm）へ集約し、
複数の消費者（act / plan / verify 合成 / triage）が読む」**という多対多の構造にする。

```
   ┌─────────────── 捕捉（統一・蒸留）─────────────────┐
   │ 人ゲート/revise/needs ─┐                          │
   │ gitlab イシューのコメント┤→ 蒸留(episodic→semantic/procedural) → decisions/*.md（learn/avoid/verify）
   │ 却下 guidance          ┘                          │            ＋ ltm-use home（横断・永続）
   └──────────────────────────────┬────────────────────┘
                                   │  recall（Jaccard / ltm）
        ┌──────────────┬───────────┼───────────────┬──────────────────┐
        ▼              ▼           ▼               ▼                  ▼
   次の act        plan(分解)   verify 合成      triage/intake      cohort 兄弟
 （既存）        （新規§3.3） （新規§3.3/§4.4） （既存 recall）    （新規§3.5）
```

既存の learn 機構（Jaccard recall・ltm 昇格・links 横展開）を**そのまま土台に流用**し、
「入口を 1 本化」「消費者を増やす」「蒸留と昇格を足す」の 3 手で問題A を解く。問題B は同じストアに
**verify（procedural memory）**を載せることで問題A の解に相乗りする。

---

## 3. 問題A の設計 — フィードバックを全体へ還元する

### 3.1 捕捉の統一 — gitlab コメントを learn 化する（最小・最大レバレッジ）

**最小変更**: `_settle_failure` 却下枝（`3378-3391`）に、`task.feedback` 注入に加えて learn 捕捉を足す。

```python
if guidance:
    task.drop("feedback")
    task.extra.append(("feedback", guidance.replace("\n", " ⏎ ")))
    if cfg.learn_capture:                                   # ← 追加
        append_decision(cfg, task.id, "gitlab",
                        action="gitlab-reject", reason=guidance,
                        learn=(task.title, distill_learn(cfg, task, guidance)))  # §3.2
```

これだけで `find_learned_resolution`（横断）・`linked_learnings_context`（横プロジェクト）・ltm 昇格が**既存のまま**効く。
gitlab の人コメントが、初めて「似たタスク」へ届く。

**捕捉を却下時以外にも広げる**: `gitlab.py` を拡張し、**approve 時の人コメントも** `data.notes` として surface する
（`_approved_payload` 系）。却下＝`avoid` 寄り、承認コメント＝`learn` 寄りに振り分ける（人ゲートの
`approve`/`hold` と同じ learn/avoid の二分をそのまま踏襲）。

### 3.2 蒸留 — 生コメントを「一般化ルール」へ（episodic → semantic/procedural）

生の人コメントをそのまま learn にすると一回限りの指示になり、Jaccard 照合に乗りにくい。ltm-use v5 の
**consolidate（エピソード→意味記憶）**に倣い、`distill_learn()` を新設する:

- 入力: task.title / accept / guidance（生コメント）。
- 出力: `<一般化した条件（title パターン）> :: <再利用可能な指針>`。「このタスク固有の固有名詞を、種別・パターンへ引き上げる」。
- 例: 「#123 のログイン画面、実サーバでなく localhost で e2e してるのでダメ」→
  `e2e / 統合テスト系タスク :: e2e は実サーバ配備で実施。localhost 実行を verify で禁止する`。
- 実装は kiro-cli 委譲（1 呼び出し・有界）。失敗時は**生コメントを verbatim で learn**（劣化しても現状より前進）。決定的フォールバックを保証。

蒸留の副産物として、**verify に効く指針**（「done の判定はここを見よ」）を分離して `verify` 種別の learn として残す
（§4.4 で verify 合成が読む）。

### 3.3 適用先の拡張 — act だけでなく plan / verify にも効かせる

既存 recall は `build_request`（次の act）にしか注入していない。読み手を 2 つ増やす。

- **plan（分解の再考）**: kiro-projects charter モードの `plan` フェーズ、および再計画時の分解プロンプトに、
  マッチした learn/avoid を「過去に同種イシューが却下された理由・回避すべき分解」として注入する。
  kiro-flow の flow-planner へも、要求本文とは別の `--learnings`（構造化）channel で伝搬させ、
  **分解のグラフ自体が変わる**（例: 「この種は 1 段細かく割れ」「verify ステップを必ず挟め」）。
  → 「タスク分解を再考する」到達を実現。
- **verify 合成（verify の再考）**: `synth_verify` / `ensure_verify` に、`verify` 種別の learn を注入
  （§4.2/§4.4）。→ 「verify を再考する」到達を実現。

### 3.4 昇格ラダー — 単一 reject を「系の再考」へ格上げ

同種の却下が**反復**したら、act 再実行を続けず**系（分解・verify・方針）**へ差し戻す。

```
①タスク feedback（同一タスク・現状）
  → ②横断 learn（似たタスクへ・§3.1）
    → ③横プロジェクト link / ltm 昇格（§2 既存）
      → ④反復検知で人へ「系の再考」提案（新規）
```

④: 同一 Jaccard クラスタの却下が `--reject-recur`（既定 2）回を超えたら、`needs/<id>.md` を起こして
「この種の分解 / verify / policy を見直すか？」を人に問う。人の決定（`revise --verify` / policy `route`/`gate` 追加 /
charter 更新）は通常どおり decisions/ に learn として残り、以後の分解・verify・triage に効く。
これが**人を介した「系の再考」**の正式な口＝cohort 的な固め直しをフィードバック起点で起こす。

### 3.5 cohort への還流 — pilot/メンバの却下を兄弟へ返す

gitlab で cohort のメンバ（または pilot）が却下されたら、`_settle_failure` から `cohorts/<id>.json` を更新し、
`materialize_cohort_rest` と同じ経路で**未実行メンバの feedback を上書き**する（現状の一方向・人ゲート限定を双方向化）。
pilot 段階の gitlab 指摘が、残り一括展開前に反映される。

---

## 4. 問題B の設計 — verify を「検証された・文脈付き・学習される」パイプラインに

単発の盲目 LLM 1 行から、**検証済み・文脈付き・学習される**合成パイプラインへ。

### 4.1 Red-Green 検証（検証者の検証）← 核・不確実性キラー

合成/合成候補を done の根拠にする前に、「その検査が変更を弁別できるか」を**実行して**証明する。

```
候補 verify cmd
  ├─ baseline（$KIRO_BASE_REV / act 前ツリー）で実行 ⇒ FAIL であるべき（red）
  └─ post-act（現ツリー）で実行            ⇒ PASS であるべき（green）
判定: red かつ green のみ採用。
  - baseline で PASS  → 恒真式 / 既存状態マッチ / 履歴一致 ⇒ 棄却
  - post でも FAIL    → 検査が強すぎ or 誤り ⇒ 棄却
```

- `true`・存在 grep（既存内容）・`git log|grep`（履歴）といった agent 診断の**偽 done 全パターンを実行レベルで排除**する。
  `require_progress` の「何か変わったか」を、「**この検査がこの変更を追えているか**」へ強化した上位互換。
- 実装: 既に持っている baseline rev ＋ worktree-cache（`KIRO_GIT_CACHE_DIR`）を使い、baseline worktree を生やして red を取る。
- **安全弁**: 破壊的/高コストな verify は `- verify_validate: none` で opt-out。red が取れない（元から PASS しか作れない条件）
  ものは自動 done させず人へ（§4.5）。

### 4.2 リポジトリ文脈つき合成 ＋ テンプレ拡充

- `synth_verify` に**リポジトリ文脈**を渡す: 検出したテスト/ビルド基盤（`package.json` scripts・`pytest`・`Makefile`・CI 設定）、
  タスクの `- paths:`/差分、backlog/過去タスクの既存 verify 例。→ grep 退化を防ぎ「実際に回るコマンド」を出させる。
- `verify_template` を**拡充**（テンプレは決定的＝最高品質）: `test-passes :: <cmd>` / `endpoint-returns :: <url> :: <status>` /
  `builds :: <cmd>` / `exit-zero :: <cmd>` など。合成より**テンプレ一致を優先**。

### 4.3 多候補 ＋ 敵対的妥当性（kiro-flow パターンの適用）

1 行ではなく **N 候補**を出し、§4.1 の red-green ゲート＋敵対的批評（「この検査が PASS でも acceptance を満たさない
false-done を 1 つ挙げよ」）で選別する。これは kiro-flow の `generate-and-filter` / `adversarial-verification` を
**verify コマンドの著作そのもの**に適用する形＝verify 著作自体を小さな kiro-flow グラフにできる（`--planner flow-planner` 流用）。

### 4.4 verify の学習・再利用（問題A との接続）

- red-green を通った verify を、種別キーで `decisions`/ltm に **procedural memory**（ltm-use v5 の `memory_type: procedural`）
  として保存（`verify_source: synth+validated`）。人が書いた verify（金の標準）は**シード**として最優先で取り込む。
- 新規 `accept:` では、**まず似た過去タスクの検証済み verify を recall**してから合成にフォールバック（毎回ゼロ生成をやめる）。
- 人が `revise --verify` で悪い verify を直したら、それ自体が learn（procedural）になり、以後の similar タスクへ伝播
  （＝§3 のループを verify に適用）。

### 4.5 劣化時のフォールバック — 「空欄」でなく「検証済み草案」を人へ

red-green が取れない・良候補が無いときは、現状どおり自動 done せず人へ回す。ただし**空欄でなく、
候補コマンドと red-green の実行証跡を添えて**回す（`needs/<id>.md`）。人は白紙から CLI を書くのでなく、
**草案をレビュー/微修正して approve**するだけでよい。「ユーザーが CLI を書くのは難しい」負担を、
「生成→検証→草案提示→承認」に置き換える。

---

## 5. 2 つの問題の接続点

- 問題A の**統一ストア**に、問題B の**検証済み verify（procedural）**が相乗りする（§4.4）。
- 問題A の**昇格ラダー④**（反復却下→系の再考）が、問題B の**verify 再考**を起こす入口になる（§3.4→§3.3→§4.4）。
- 問題B の**red-green**は、問題A で「done の判定はここを見よ」と蒸留された指針（§3.2）を verify 化する受け皿になる。

つまり両者は「1 本の学習ループ ＋ 実行検証ゲート」の別断面であり、**共通の背骨（§2）に載せると相互に強化される**。

---

## 6. 段階導入（フェーズ）

| フェーズ | 内容 | 変更規模 | 効果 |
|---------|------|----------|------|
| **P1** | §3.1 gitlab 却下 guidance を learn 捕捉（`_settle_failure` 1 か所＋`gitlab.py` の notes surface） | 小 | gitlab 指摘が初めて横断 |
| **P2** | §4.1 Red-Green 検証を `ensure_verify`/`run_verify` に追加（opt-out 付き） | 中 | 偽 done を実行レベルで排除。verify 品質の底上げ |
| **P3** | §3.2 蒸留 ＋ §4.2 文脈つき合成 ＋ テンプレ拡充 | 中 | learn/verify の一般化と実用度 |
| **P4** | §3.3 plan/verify への recall 注入 ＋ §4.4 verify 学習再利用 | 中 | 分解・verify の再考到達 |
| **P5** | §3.4 昇格ラダー（反復検知→系の再考）＋ §3.5 cohort 還流 ＋ §4.3 多候補 | 大 | 系レベルの自己改善 |

P1・P2 は独立して価値があり、後方互換（learn_capture off・verify_validate none で従来挙動）。まず P1/P2 を薄く入れて検証するのを推奨。

---

## 7. 未決事項 / 論点（合意したいポイント）

1. **蒸留に LLM を使うか**: 生 verbatim（決定的・現状踏襲）と LLM 蒸留（一般化・品質）のどちらを既定にするか。
   推奨: 既定 LLM 蒸留＋失敗時 verbatim フォールバック。
2. **Red-Green のコスト/破壊性**: baseline worktree で verify を回すコスト、副作用のある verify（DB 書き込み等）の扱い。
   推奨: opt-out（`verify_validate: none`）＋ red-green は「読み取り/テスト系」に既定適用、外部副作用系は skip。
3. **plan への learn 注入の強さ**: 分解を変えるのは強力だが planner を振り回しかねない。有界（文字数上限・件数上限）にする。
4. **昇格ラダーの閾値**: `--reject-recur`（何回で人へ）と Jaccard しきい値の既定。既存 `--learn-threshold`(0.5) と揃えるか。
5. **gitlab の approve コメント捕捉**: approve 時に人コメントを surface する API 追加コスト（`get-comments` 追加呼び出し）と頻度。
6. **kiro-flow verify ノード（LLM 判定）を CLI 化するか**: 本案は kiro-projects 側の verify を対象にした。kiro-flow 内側の
   verify ノードも将来 CLI 化するかは別スコープ（要判断）。

---

## 8. 影響ファイル（実装時の主な挿入点）

| ファイル | 箇所 | 変更 |
|----------|------|------|
| `tools/kiro-projects/kiro-projects.py` | `_settle_failure` 3378-3391 | §3.1 learn 捕捉・§3.4 反復検知 |
| 〃 | `synth_verify` 2020-2035 / `_synth_verify_prompt` 1987-1996 | §4.2 文脈注入・§4.3 多候補 |
| 〃 | `ensure_verify` 2038-2058 / `run_verify` 1878-1888 | §4.1 red-green・§4.4 再利用 |
| 〃 | `expand_verify_template` 1965-1984 | §4.2 テンプレ拡充 |
| 〃 | `build_request` 2203-2225 / `find_learned_resolution` 905-922 | §3.3 plan/verify への recall |
| 〃 | cohort 371-403 | §3.5 gitlab 却下の還流 |
| `tools/kiro-flow/executors/gitlab.py` | `_rejected_payload` 861-886 / approve 系 | §3.1 approve コメント surface |
| `tools/kiro-flow/`（flow-planner） | `.github/skills/flow-planner/` | §3.3 `--learnings` 伝搬 |

---

## 付録: 用語

- **learn / avoid**: `decisions/*.md` に残る横断学習ルール。learn＝どう解けばよいか、avoid＝この種は自動実行させない。
- **ltm-use home**: 実績のある learn が昇格する永続ストア（プロジェクト/run 横断）。
- **red-green 検証**: verify コマンドが「変更前 fail・変更後 pass」を実行で満たすかの検証（検証者の検証）。
- **procedural memory**: ltm-use v5 の記憶タイプ。手順・パターン（＝検証済み verify コマンド）の記録。
