# kiro-autonomous — プロジェクト憲章駆動の長期改善ループ（設計メモ）

- 日付: 2026-06-21
- 位置づけ: [運用・外部操作レイヤ設計書](2026-06-19-kiro-autonomous-ops-design.md) と
  [MVP 設計書](2026-06-16-kiro-autonomous-mvp-design.md) の**上に一段積む**外周拡張。
  既存の正準ループ（backlog → act → verify → done）は内側にそのまま温存し、その外側に
  「目標 → backlog 生成 → 消化 → 評価 → 改善」のループを足す。
- 状態: **実装済み（MVP）**。`tools/kiro-autonomous/kiro-autonomous.py` に新サブコマンド `project` として
  内蔵。テストは `tools/kiro-autonomous/tests/test_kiro_autonomous.py`（`TestProjectLayer`・10 件）。
- 取り込み先: **kiro-autonomous に内蔵**（別ツールにしない）。新サブコマンド `project` と
  charter ファイル（`charter.md.example` が正典）、設定キーの追加で実現する。

## 0. 背景と狙い

### 現状の到達点と隙間
kiro-autonomous は **backlog（タスクの山）を最上位の入力**にしている。これにより：

- **②（分解・実行・反復改善・敵対的レビュー）** は、`kiro-flow`（内側＝7 パターン。`adversarial-verification` /
  `loop-until-done` / 集約前 `review` gate）と kiro-autonomous（外側＝verify ゲート・回帰・followup 自走）で
  **1 run / 1 backlog の範囲では完成**している。
- **③（人の判断・成果物確認・方向修正）** は、`needs/` ↔ `decisions/`・`approve`/`hold`/`reprioritize`・
  `policy.md`・検収ゲートで**ほぼ完成**している。

残る隙間は **①（人が目標・制約・前提・成果物を定義する場所）と、それを駆動に使う仕組み**である。具体的には:

1. **目標を書く場所が無い**。プロジェクト全体の目的・完成条件(DoD)・制約・前提・成果物は、現状
   タスク単位の `verify` と `policy.md` に断片化していて、一枚の「憲章」が存在しない。
2. **backlog の初期投入を人がやる**。AI は「目標から逆算して backlog を起こす」ことをしない。`followup`
   による自走は「タスクからの派生」であって「**目標からの逆算**」ではない。
3. **`drained` で止まる**＝「タスクが尽きた」と「目標が達成された」を区別できない。「短絡的な達成にとどまらない
   長期改善ループ」「成果物群が目標を満たすかの敵対的レビュー」を回す**主体がいない**。

### 狙い
charter（プロジェクト憲章）を**人が書く唯一の最上位入力**にし、その上に **evaluator-optimizer のもう一段**を
載せる。kiro-flow が「1 run 内で静止するたびに評価して replan」するのと**相似な構造**を、プロジェクト粒度で回す。

> 不変条件（本書でも維持）: 「done は verify=PASS のみが根拠」「必ず有限停止」「人の policy ＞ エージェント提案」
> 「stdlib のみ・決定的ファイル操作で完結（知能は kiro-flow/スキルへ委譲）」「`protect`/`gate`/`regression` は
> 自動緩和しない」。本書が足すのは **backlog の生成源と収束判定**だけで、内側の正準ループは一切変えない。

---

## A. charter.md（人が書く唯一の入力）

`<root>/charter.md`（既定 `.kiro-autonomous/charter.md`）。**人だけが書く**（`policy.md` と同格の人間専管ファイル）。

```markdown
# Charter: <プロジェクト名>

## goal            # 目標（北極星。1〜数文）
ユーザーが CSV を投入すると要約レポートを生成する CLI を完成させる。

## constraints     # 制約（守るべき境界）
- Python 3.9 / 標準ライブラリのみ（pip 依存を増やさない）
- 既存 API の後方互換を壊さない

## assumptions     # 前提（与件・置かれている状況）
- 入力 CSV は UTF-8・ヘッダ行あり

## deliverables    # 成果物（何を納品するか）
- report.py（生成器）/ tests/ / README の使用例

## acceptance      # 完成条件(DoD)＝受入 verify。**プロジェクト done の唯一の根拠**
- `pytest -q tests/`
- `python report.py sample.csv | grep -q "Summary"`
- `grep -q "## Usage" README.md`
```

- **`acceptance` がプロジェクト版 verify**。各行は**終了コード 0 を PASS とみなすシェルコマンド**で、
  タスクの `verify` と完全に同じ鉄則（履歴でなく最終状態/差分を見る／`$KIRO_BASE_REV` 利用可）に従う。
  **プロジェクトの done は「acceptance 全 PASS」のみが根拠**。レビューや AI の自己申告では done にしない。
- `goal`/`constraints`/`assumptions`/`deliverables` は**自然言語**で、plan/evaluate フェーズで kiro-flow へ
  渡す文脈になる（分解と批判の材料）。acceptance を持たない charter は **plan はできるが done 判定不能**＝
  必ず人へ（鉄則の保全）。
- charter は既存スキル `requirements-definer` で人との対話から起こせる（任意・本体非依存）。

---

## B. プロジェクトループの三相（新サブコマンド `project`）

`kiro-autonomous project [--watch]` が、既存の `run` を**内側の一相**として呼ぶ外側ループを回す。

```
        charter.md（goal/constraints/assumptions/deliverables/acceptance）
                 │
   ┌─────────────▼──────────────────────────────────────────────────────────┐
   │ ① plan     charter → kiro-flow に分解を委譲 → enqueue で backlog を生成    │
   │ ② execute  kiro-autonomous run（既存の正準ループ）を drained まで回す       │
   │ ③ evaluate acceptance を実行 → 未達/レビュー指摘 → 改善 backlog を生成      │
   └─────────────┬───────────────────────────────────┬──────────────────────┘
   未達/指摘あり →┘ （改善サイクルを次へ・有限回）        │ acceptance 全PASS かつ指摘ゼロ
                                                       ▼
                                          milestone gate → needs/ で人へ
                                          （完了報告／次フェーズ提案／継続承認）
```

### ① plan — 目標から backlog を起こす（知能は kiro-flow へ委譲）
- charter の goal/constraints/deliverables を **`kiro-flow run`（`--planner flow-planner`）** へ要求として投げ、
  返ったタスク群を **`enqueue --json` 経由で backlog 化**する。**分解の知能は kiro-flow に委譲**し、
  kiro-autonomous 側は決定的な取り込みだけ（act を kiro-flow に委譲するのと同じ流儀）。
- 生成タスクには**必ず実行可能な `verify` を要求**する。verify を持たないものは `inbox`＝人の triage 行き
  （鉄則①の保全）。acceptance の各条件は、それを満たすタスクの verify の**素材**としても使える。
- plan は **冪等**: 既存 backlog/archive と**タイトル/verify が十分類似**するタスクは再生成しない
  （二重投入防止。照合は既存 `learn` の Jaccard を流用）。

### ② execute — 既存の正準ループをそのまま回す
- `run`（または `run --watch`）を**無改造で**呼ぶ。タスク verify・検収ゲート・回帰・`protect`・予算・
  原子的クレーム・followup・自律裁定・DR 学習は**全て既存のまま効く**。
- execute が `drained`（消化可能タスク枯渇）に達したら③へ。`budget`/`cost` 停止なら③に行かず**プロジェクト層も停止**
  （有限性を二重に保証）。

### ③ evaluate — 「達成」と「枯渇」を分離する（本書の肝）
backlog が尽きた時に発火し、**プロジェクトが本当に goal を満たしたか**を判定する。三段：

1. **acceptance ゲート（決定的・必須）**: charter の `acceptance` 各行を実行。1 つでも FAIL なら **未達**。
2. **敵対的レビュー（opt-in・知能委譲）**: acceptance 全 PASS でも「**短絡的達成（弱い verify を通しただけ）**」を
   疑い、`kiro-flow` の `adversarial-verification` または既存スキル `agent-reviewer` / `council-system` に
   **成果物群 vs goal/deliverables** を批判させる（`--review-project`）。
3. **収束判定**:
   - **未達 or レビュー指摘あり** → 指摘を**改善タスクとして enqueue**（verify 付き。①と同じ冪等照合）。
     これが「長期改善ループ」の駆動源。次の execute へ。
   - **acceptance 全 PASS かつ改善タスク生成ゼロ** → **収束候補**。milestone gate（C）で人へ上げる。

評価結果・生成した改善タスク・収束理由は **`decisions/` に `project-evaluate` として監査記録**する。

---

## C. milestone gate（成果物と方向の人ゲート）

収束候補に達したら、即 done にせず **`needs/<project>.md`（マイルストーン）** を生成して人へ上げる
（検収ゲートのプロジェクト版）。人は `needs` のフィードバック欄＋ `charter.md` 編集で方向を決める:

| 人の選択 | 操作 | 効果 |
|----------|------|------|
| **完了として受領** | `kiro-autonomous approve <project> --reason …` | プロジェクト done（後述の収束）。`DELIVERY.md` に最終納品書 |
| **次フェーズへ継続** | `charter.md` の goal/acceptance を更新して `[x]` | 新 acceptance で plan/execute/evaluate を再開（長期稼働） |
| **方向修正** | `needs` に方針記入＋ `policy.md`／`charter.md` 編集 | 改善タスクを再生成して継続 |
| **保留** | `hold` | プロジェクト層を停止 |

`--watch` 時はマイルストーン提示後も常駐し、charter 更新やフィードバックを poll で拾って再開する
（idle 中はエージェント非起動＝既存性質を継承）。

---

## D. 収束（プロジェクト層も必ず止まる）

内側 `run` の有限性に加え、**プロジェクト層に独立した有限停止**を持たせる（無限改善チャーンの防止）。

| 停止理由 | 意味 | 条件 |
|----------|------|------|
| `accepted` | 人が milestone を `approve`（プロジェクト done） | acceptance 全 PASS かつ人の受領 |
| `converged` | acceptance 全 PASS・改善ゼロが続き人へ提示 | milestone gate へ（人待ち） |
| `project-budget` | 改善サイクル数の上限 | `--max-project-cycles`（既定 5） |
| `project-cost` | プロジェクト累計コスト上限 | `--max-project-cost`（run のコスト集計を横断加算） |
| `no-progress` | 改善しても acceptance PASS 数が増えない | 連続 `--project-stall`（既定 2）回 stall で**人へ**（自動チャーンを止める） |

- **単調性の要請**: evaluate 毎に **acceptance PASS 数を記録**し、改善サイクルを回しても増えない（stall）状態が
  続けば「ループでは解けない」と判断して `needs` へ（鉄則：曖昧・人判断は人へ）。
- acceptance を持たない charter は `converged` 判定不能＝**最初から人へ**（plan/execute はするが done にしない）。

---

## E. 既存資産の再利用と不変条件との整合

| 関心事 | 委譲先（既存） | 本書が足すもの |
|--------|----------------|----------------|
| タスク分解（plan） | `kiro-flow run --planner flow-planner` | charter→要求の橋渡しと `enqueue` 取り込み |
| 反復・敵対的レビュー（run 内） | `kiro-flow`（7 パターン・`review` gate） | （変更なし。そのまま使う） |
| 成果物 vs goal の批判（evaluate） | `agent-reviewer` / `council-system` / kiro-flow `adversarial` | 起動と指摘→改善タスク化 |
| タスク消化・検証・安全 | `kiro-autonomous run`（正準ループ） | （無改造で内側に呼ぶ） |
| 人の判断・検収 | `needs`/`decisions`/`approve`/`policy.md` | milestone gate（プロジェクト版検収） |
| 要件起こし（任意） | `requirements-definer` スキル | charter テンプレ |

**維持する不変条件**:
1. **done は verify=PASS のみ**。プロジェクト done も `acceptance`（=verify）全 PASS が唯一の根拠。
   敵対的レビューは**タスクを足す（締める）方向のみ**で、自己申告 done を作れない。
2. **必ず有限停止**。内側 `run`（drained/budget/cost）＋プロジェクト層（D の 5 条件）の二重。
3. **人の policy ＞ エージェント**。charter/`policy.md` は人専管、milestone は人が握る。
4. **stdlib のみ・決定的ファイル操作**。plan/evaluate の知能は全て kiro-flow/スキルへ委譲し、本体は
   要求の組み立て・`enqueue`・acceptance 実行・収束計算・decision 記録のみ（LLM を本体に持ち込まない）。
5. **既定（`project` を呼ばない限り）で従来挙動は完全不変**。`run`/`watch` 等の既存経路は一切変えない。

---

## F. 追加する設定キー / サブコマンド / ファイル

| 種別 | 追加 | 既定 | 意味 |
|------|------|------|------|
| サブコマンド | `project [--watch]` | — | charter 駆動の plan→execute→evaluate ループ |
| ファイル | `<root>/charter.md` | — | 人が書く目標/制約/前提/成果物/acceptance |
| 状態 | `<root>/project.json` | — | サイクル番号・acceptance PASS 履歴・stall カウント（run-log を一次ソースに増分更新） |
| 通知/検収 | `needs/<project>.md` | — | milestone gate（収束候補の提示） |
| CLI/config | `charter` | `charter.md` | charter のパス上書き |
| CLI/config | `review_project` / `--review-project` | `false` | evaluate の敵対的レビュー（opt-in） |
| CLI/config | `max_project_cycles` | `5` | 改善サイクル上限（有限停止） |
| CLI/config | `max_project_cost` | `0`（無制限） | プロジェクト累計コスト上限 |
| CLI/config | `project_stall` | `2` | acceptance PASS 数が増えない連続回数の上限→人へ |

- 真偽は既存どおり三値（`--review-project`/`--no-review-project`）で `CLI > config > 既定`。
- `project` の終了コードは既存流儀に揃える: `0`＝`accepted`／`1`＝人の対応待ち（`converged`/`no-progress`/blocked）／
  `2`＝`project-budget`/`project-cost`。

---

## G. テスト面（kiro-flow/kiro-cli 抜きで検証）

- charter パース（goal/constraints/assumptions/deliverables/acceptance の抽出・acceptance 無しの扱い）。
- plan の冪等性（既存 backlog/archive と類似タスクを再生成しない）。
- evaluate: acceptance 全 PASS→収束候補／一部 FAIL→未達で改善タスク生成／生成は verify 付き・冪等照合。
- 収束: `accepted`（approve）／`converged`（PASS かつ改善ゼロ→milestone）／`max-project-cycles` で停止／
  `no-progress`（PASS 数が stall）で人へ／acceptance 無しは done 判定不能で人へ。
- milestone gate: charter 更新で次フェーズ再開／`approve` でプロジェクト done＋最終納品書。
- 有限停止の二重化（内側 budget で止まればプロジェクト層も止まる）。
- 敵対的レビュー（`--review-project`）の指摘→改善タスク化（スタブ評価役で検証）。
- **既定（`project` 未使用）で `run`/`watch` の従来テストが完全不変**。
- 監査記録: 各サイクルの `decisions/` への `project-evaluate`、収束理由の記録。

実行: `KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-autonomous/tests`。

---

## H. 実装範囲の見取り（実装済み・MVP）

1. ✅ `parse_charter(text)` / `load_charter(cfg)`: charter.md を `Charter`（goal/constraints/assumptions/
   deliverables/acceptance）へ構造化。`# Charter: <name>` から project id を生成。
2. ✅ `cmd_project(cfg, planner=None, reviewer=None, runner=run_loop)`: 三相ループ。①plan（`plan_via_agent`
   で分解→`_enqueue_specs` で冪等投入）→②`run_loop` を内側呼び出し→③evaluate（`evaluate_acceptance`・
   未達 acceptance をそれ自体 verify とする改善タスク化・opt-in `review_via_agent`・収束/stall/予算判定）。
   planner/reviewer/runner は**注入可能**でテストは kiro-flow/kiro-cli 抜きで完走。
3. ✅ 状態は `<root>/project.json`（history・best・stall・cost・status）。各 evaluate を `decisions/` に
   `project-evaluate` で監査記録。
4. ✅ milestone gate: `write_milestone` が `needs/<project>.md` を生成、`cmd_approve` が収束済みプロジェクトの
   `approve <project>` を `finalize_project`（最終納品書＋state=accepted）へ分岐。`project_watch` が charter
   更新/フィードバックを poll で取り込む（`--watch`）。
5. ✅ Config/CLI/CONFIG_DEFAULTS に `charter`/`review_project`/`max_project_cycles`/`max_project_cost`/
   `project_stall` を追加（F）。
6. ✅ テスト（`TestProjectLayer` 10 件）・README / `charter.md.example` を追記。

> **MVP の委譲先**: plan/review の分解・批判は現状 `kiro-cli`（`_run_kiro_cli`）に JSON 配列で出させる
> （ファイル内の `rank_agent`/`adjudicate_escalation` と同じ流儀）。設計の狙いどおり**知能は委譲**し本体は
> 決定的（enqueue・acceptance 実行・収束）のまま。`kiro-flow run --planner flow-planner` を分解バックエンドに
> 差し替えるのは planner 注入点を替えるだけで可能（将来拡張）。
> 既定値（5 / 2 等）は初期値。運用後に `runlog`/`project.json` を見て調整余地あり。

---

## I. 非目標（本書の範囲外・将来拡張）

- **charter の自動生成**（人の対話なしに goal を起こす）— 入力は人専管を維持。`requirements-definer` は任意の補助に留める。
- **plan の本体内蔵化**（kiro-flow を介さない分解）— 知能は委譲し、本体に LLM を持ち込まない不変条件を守る。
- **複数プロジェクトのポートフォリオ管理**（charter を跨ぐ優先度調整）— まずは 1 root = 1 charter から。
- **敵対的レビューの常時 ON 化**— コスト増を避け opt-in（`--review-project`）に留める。

これらは本体の不変条件を保ったまま、同じ「外周を足す」方針で段階追加できる。
