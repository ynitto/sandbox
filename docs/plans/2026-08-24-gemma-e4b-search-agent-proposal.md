# Gemma E4B 級 PC でエージェント品質を高める探索実行基盤

> **ステータス**: Proposal  
> **作成日**: 2026-08-24  
> **対象**: `gemma4:e4b` を主なローカル worker とするコーディングエージェント  
> **要点**: モデルを無制限に呼ぶのではなく、独立性のある候補を予算内で生成し、隔離環境の機械検証で選び、実測に基づいて探索量を変える。

## 目的

量子化やランタイム最適化の後に残る品質差を、モデルサイズではなく**探索・検証・再利用**で縮める。E4B を「一度で正解を返す回答器」ではなく、限定された状態から次の操作候補を提案する **policy** として使う。

ただし「候補数を増やせば賢くなる」は条件付きである。同一モデル・同一プロンプトの再標本化は誤りが相関しやすく、テストも仕様の完全な代理ではない。したがって目標は inference 回数ではなく、次で測る。

> **単位時間・電力量あたりに、未知の受入基準を満たす候補へ到達する確率をどれだけ上げられるか。**

### 用語の精密化

- 本書の `gemma4:e4b` はこのリポジトリで使うランタイム上のモデル識別子であり、一般名としての「4B パラメータモデル」と同一視しない。E4B の E は effective parameters を表すため、保存容量、常駐メモリ、KV cache、実効速度は量子化・context 長・runtime・offload 構成を別に計測する。
- **World state** はリポジトリ全体そのものではなく、commit、作業差分、対象 symbol、制約、検査結果を束ねた観測可能なスナップショットを指す。
- **Verifier** は「正しさの証明器」ではない。明文化された制約への反例を見つけ、候補を棄却または順位付けする仕組みである。
- **World model** は学習済み環境モデルではなく、観測した遷移と索引のリポジトリ固有キャッシュである。過大な名称を避けるなら `Transition Store` と呼ぶ。

## 変更対象

### 1. Repository Compiler：全文要約ではなく、根拠つき task slice を作る

コードを一律に要約すると、細部の契約を落として誤りを増やす。先に AST、symbol、import/dependency、call site、テスト対応、所有規則、変更履歴を索引化し、タスクごとに小さな **task slice** を作る。

```text
issue + base commit
        │
        ▼
query terms / failing test / touched symbol
        │
        ▼
AST + symbol graph + dependency edges + test map + CODEOWNERS/history
        │
        ├── structured facts（位置と生成時刻つき）
        └── exact excerpts（行番号・blob hash つき）
```

LLM へ渡す例:

```yaml
target: PaymentService.process
base_commit: 2f6c...
callers:
  - CheckoutController.submit
callees:
  - PaymentGateway.charge
  - TransactionRepository.save
invariants:
  - source: tests/payment_service_test.py:81
    statement: save is allowed only after a successful charge
related_tests:
  - tests/payment_service_test.py::test_duplicate_transaction
exact_excerpts:
  - path: src/payment_service.py
    lines: 41-96
    blob: a18d...
```

グラフは候補検索に使い、最終判断に必要な実コードは抜粋で残す。動的 dispatch、reflection、設定、生成コードなど静的解析で見えない辺には `confidence` と `unknown_edges` を付ける。索引の base commit と作業木がずれた場合は利用せず再生成する。

**強制レイヤー**: compiler が provenance と commit 整合性を検査し、根拠のない invariant や stale slice を scheduler が受理しない。

### 2. Search Controller：独立した枝を、隔離して探索する

直列ループを、状態と予算を持つ best-first search に置き換える。

```text
Task → task slice → frontier
                       ├─ diagnosis A → plan → patch ─┐
                       ├─ diagnosis B → plan → patch ─┼→ verifier → accept
                       └─ diagnosis C → probe → state ┘      │
                                                 ↑           └→ expand / stop / escalate
```

探索単位は文章の「仮説」だけでなく `(state_id, action, resulting_patch, evidence)` とする。各枝は同じ worktree を共有せず、base commit から作った使い捨て worktree/container で動かす。候補の多様性は temperature だけに頼らず、次を変える。

- fault localization の入口（失敗テスト、callers、recent diff）
- 操作戦略（最小修正、契約先行、テスト先行、revert/compare）
- context slice と tool observation
- 必要なら model / agent CLI

無制限な tree-of-thought は採用しない。既定は `beam_width=3`、`max_depth=2` 程度から実測し、同一 diff、同一 failure signature、意味的に同じ plan は展開前に重複排除する。生成トークン数ではなく wall time、peak RAM、energy proxy、検証費用を全予算へ含める。

**強制レイヤー**: scheduler が同時実行数、wall-clock、token、検証回数、ディスク使用量を hard limit として停止し、executor が枝ごとの filesystem/process/network 分離を担う。

### 3. Mechanical Verifier：pass/fail と順位付けを分離する

Verifier は次の順で安い検査から実行する。

1. patch 適用、許可パス、生成物・秘密情報・依存追加の検査
2. parse / format / lint / typecheck / build
3. 変更箇所に近い unit test と既知 regression test
4. property / fuzz / mutation / integration test（利用可能な場合のみ）
5. sandbox 内の full suite、性能・セキュリティ検査

必須 gate は加点方式にしない。たとえば test failure を「変更行が少ない」点で相殺できてはならない。

```text
eligible =
  patch_applies
  AND required_tests_pass
  AND typecheck_pass
  AND policy_violations == 0

rank(eligible candidates) = (
  confidence_adjusted_coverage,
  mutation_kills,
  -scope_penalty,
  -runtime_cost
)
```

`changed_lines * 0.1` のような根拠のない連続スコアは初期案に置かない。最小 diff は tie-breaker に限定し、生成ファイルや rename で行数が膨らむケースを正規化する。flaky test は複数回結果から別状態 `INCONCLUSIVE` とし、失敗を候補の責任だと即断しない。テストを変更した候補は、可能なら**元テスト + 新実装**と**新テスト + 基準実装**を交差実行し、テスト削除による偽 pass を防ぐ。

hidden test は開発時に存在するとは限らず、存在しても探索へ繰り返し露出させると過適合する。探索用 test と最終 holdout gate を分離し、holdout の詳細は最終候補に一度だけ返す。自然言語要件、UX、保守性など機械化できない基準は、LLM の自己採点ではなく明示的な人手レビューまたは上位モデルへの escalation として残す。

**強制レイヤー**: gate runner が必須検査、timeout、sandbox、holdout 非開示を実行時に強制する。採用判断は receipt に記録された gate 結果から決定し、worker の「完了」宣言では決めない。

### 4. Transition Store：失敗全文ではなく、再利用可能な証拠を保存する

完全な `state hash` は小変更で別物になり、逆に粗い hash は異なる状態を衝突させる。完全一致キーと類似検索キーを分ける。

```text
exact_key  = hash(repo_id, base_commit, dirty_diff, toolchain_lock)
similarity = language + symbols + failure_signature + task_kind + dependency_neighborhood

(state_ref, action_ref) → outcome
  outcome = gate results + failure signature + patch hash + cost + provenance
```

再利用時は過去の成功 patch をそのまま適用せず、「どの診断・検査・symbol が有効だったか」を prior として frontier の順序に反映する。過去の失敗も永続的な禁止にはせず、base commit、toolchain、テストが変われば減衰させる。秘密、ユーザーデータ、巨大ログは保存前に redact / content-address 化し、保持期限を設ける。

改善効果は記憶あり/なしの shadow evaluation で測る。成功率だけでなく、初回有効候補までの時間、無駄な枝数、stale memory による回帰を比較する。

**強制レイヤー**: memory writer が schema、provenance、redaction、TTL を強制し、retriever が commit 距離と鮮度の閾値を満たさない記憶を実行指示として返さない。

### 5. Adaptive Compute：難易度予測より逐次的な不確実性で増額する

開始前の「low / medium / high」分類だけでは、分類器自身の誤りが固定される。まず安価な probe を行い、観測に応じて予算を追加する anytime 方針にする。

| 段階 | 初期予算 | 増額条件 | 停止条件 |
|---|---:|---|---|
| Triage | 1–2 calls | 対象 symbol が一意でない、再現不能 | task slice と再現手順が得られないなら escalate |
| Local repair | 2–3 patches | 候補が異なる failure を示す | 必須 gate pass + margin を満たす |
| Search | 最大 6–12 branches | frontier に新規 failure signature が残る | 改善なし、予算上限、同型候補のみ |
| Review | 上位 1–2 件 | 非機械要件または high-risk path | holdout pass + review 完了 |

「100 inference credits」は説明用の例に留める。直列 100 回は家庭用 PC では latency と電力を増やすだけになりうる。予算の単位はモデル呼び出し回数ではなく、予測残時間と期待改善量 `expected_success_gain / marginal_cost` にする。OOM、thermal throttling、interactive load も scheduler の入力へ含める。

**強制レイヤー**: scheduler が逐次予算と stop/escalate rule を実装し、モデルは自分で予算を延長できない。

### 6. Offline Replay：本番 holdout を汚染しない範囲で再探索する

「夢見る」より **offline replay** と呼ぶ方が実態に近い。アイドル時に、固定済みの過去 issue、既知 patch、テストを用いて別経路を探索し、次を更新する。

- failure signature の正規化規則
- task slice の取り方
- strategy ごとの成功率・費用
- transition prior
- regression / mutation test 候補（人または独立 gate の承認後に採用）

同じ過去テストへの反復最適化は test-suite overfitting を起こす。時系列で train/replay と holdout issue を分離し、holdout 成績が改善しない更新は本番へ昇格しない。生成 patch を自動 merge せず、ネットワークは既定 off、CPU/GPU 温度、電力、ディスク、利用時間帯に上限を置く。

**強制レイヤー**: replay runner がデータ分割、resource quota、network policy、成果物の quarantine を強制し、promotion gate が holdout 比較を通った索引・policy だけを本番へ反映する。

### 7. 全体アーキテクチャ

```text
Repository ─→ Compiler ─→ versioned task slice
                              │
Issue ─→ Triage ──────────────┤
                              ▼
                    Budgeted Search Controller
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
                 isolated  isolated  isolated
                  worker     worker     worker
                    └─────────┼─────────┘
                              ▼
                       Candidate Pool
                              │
                              ▼
                    Mechanical Gate Runner
                       │ pass │ fail / ?
                       ▼      └────→ expand / stop / escalate
                   holdout/review
                       │
                       ▼
                 accepted patch + receipt
                       │
                       ▼
                  Transition Store
```

AlphaGo との類似は「policy が候補を出し、探索と外部評価が補う」点までである。コード変更には完全なゲーム規則、安価な simulator、常に正しい勝敗信号がないため、MCTS や self-play の効果をそのまま仮定しない。

## 精度を上げるために元案から修正した点

| 元の表現 | 修正後 |
|---|---|
| 候補を大量生成すれば選べる | 相関した誤答を重複排除し、戦略・context・観測を変えて多様性を作る |
| Deterministic Verifier が正解を選ぶ | verifier は観測可能な契約違反を落とす。未機械化要件は人手/上位モデルへ送る |
| 単一の加重 score | 必須 gate と eligible 候補内の ranking を分離する |
| state hash で過去木を再利用 | exact identity と類似 failure/symbol 検索を分離し、鮮度で減衰する |
| コードを LLM に読ませない | 構造索引で絞り、根拠となる exact excerpt は必ず読ませる |
| 100 inference を割り当てる | 限界効用を観測しながら逐次増額し、改善停止で打ち切る |
| Offline Dreaming で賢くなる | 時系列 holdout で汎化を確認できた policy / index 更新だけ昇格する |
| 小さな AlphaGo | 不完全 verifier と高価な環境を持つ best-first repair system と位置付ける |

## 現状の検討結果との突き合わせ（2026-08-24）

本提案は新規構想としては筋が通るが、このリポジトリでは既に主要な仮説の一部を実測している。したがって、図の上から順に全機能を作る判断はしない。[ローカル LLM 追加活用評価](2026-08-22-local-llm-further-utilization-and-runtime-tuning-assessment.md)と `tools/agent-tools/eval/README.md` の台帳を正として、提案要素を次のように読み替える。

### 確定した観測

| 観測 | 実測 | この提案への含意 |
|---|---|---|
| 局所修正は既存の gate + 診断つき再投入で成立 | T1gate / T2gate は基準線で合計 6/6、escalate 0 | 成功済みの領域へ探索木を足す余地は小さい。まず現行経路を使う |
| best-of-3 は欠落族を救わない | T3gate は基準線、`--resample 3` とも 0/3。27 attempt が同じ契約テスト欠落 | 「候補数を増やす」を既定化しない。必要なのは候補の再抽選でなく成果物単位への分解 |
| best-of-3 は壁時計を悪化させる | T1 中央値 564s → 1040s、T3 1257s → 3452s。escalate 率は改善なし | Search Controller は現時点で **No-Go**。新しい独立戦略が単発で効くまで保留 |
| symbol slice は受入を保って入力と時間を削る | 570 行で tokens −72% / 壁時計 −33%、2,020 行で tokens −87% / 壁時計 −83%。全文・slice とも 3/3 | Repository Compiler はフルグラフより、実証済みの read-only slice の本番配線を先に行う |
| slice に品質向上の証拠はない | 2,020 行の全文でも 3/3。単一 symbol 検索では長文弱点が発現しなかった | 「見落としを減らす」を採用理由にせず、prefill 経済だけを便益として計上する |
| verifier の効果は既に大きいが万能ではない | gate + 再投入で 0/9 → 3/3。一方、自然文 criteria の project verify は e4b / 12b とも成立せず | 新しい汎用スコアより、検査コマンドを置ける単位への分解と非機械要件の人手移送を優先する |
| E4B の候補生成も種類を選ぶ | path 3/3、test name 3/3、regex 0/3 | 存在検査できる「選択候補」だけへ限定投入し、自由な変換候補には広げない |
| 遷移記憶より先に通常検索が成果を出した | bge-m3 の段構えで paraphrase hit@5 35% → 60%、本番 25% → 85% | Transition Store を急がず、既存 retrieval の運用と receipt 蓄積を先にする |
| 評価 coverage はまだ疎い | 42 面中 missing 34 → 28。planner はケース別 0/3〜3/3、project verify は不成立、dashboard doctor は 12/12 | 未測定面の観測と危険な既知面の routing が、汎用探索基盤より先 |

主要値は各 3 回の小標本が多い。ここでの `3/3` は成功率 100% の推定ではなく「少なくとも成立例がある」、`0/3` は「この条件では成立例を観測できなかった」と読む。もっとも、best-of-N は受入が同じまま費用が大幅に増えており、既定採用を見送るには十分な反証になっている。

### 優先度判断

優先度は、期待品質だけでなく **既存証拠の強さ × 本番への距離 × 失敗時の影響 ÷ 実装・運用費用** で決める。

| 優先度 | 項目 | 判断 | 次の判定ゲート |
|---:|---|---|---|
| **P0** | read-only context slice の本番配線 | **実施**。既に中核と A/B があり、最大の実測便益がある。ただし flow の `--file`（編集可）と `--read`（読取のみ）の区別を先に通す | opt-in で、受入無退行、stale/fallback 可視化、p50 wall time 改善。小ファイルは slice しない |
| **P0** | 機械 gate を置ける単位への task 分解 | **実施**。T3 の同型欠落に対する現在唯一の根拠ある方向 | 複数成果物を一成果物ずつに割った新 arm が T3 系で成立し、各 node に checker がある |
| **P0** | 危険な routing の封じ込め | **実施**。project の自然文 verify は不成立、planner はケース依存という既知リスクを先に扱う | 自然文 criteria を「道具あり候補または人」へ送り、局所 verify は決定的 command のみにする。planner は gate を通らない計画を採用しない |
| **P1** | coverage の高リスク面を追加測定 | **継続**。missing 28 面を全て均等に埋めず、production frequency × blast radius で選ぶ | 実運用頻度上位と accept/verify/plan 系を先に direct eval 化し、routing 表へ反映 |
| **P1** | receipt / failure taxonomy / cost 計測 | **実施**。将来の memory と adaptive compute の共通前提 | task family、gate、retry、wall time、tokens、fallback、blockers が本番 receipt から集計できる |
| **P1（実機枠）** | 32 GB 機での MoE RAM probe / iGPU prefill arm | **独立に測定**。モデル探索とは混ぜない | 同一モデル・同一 harness で resident RAM と prefill/decode を分離し、余白 3 GiB とwall-time便益を満たす |
| **P2** | Adaptive Compute の最小版 | **条件付き実施**。学習済み難易度分類器は作らず、既存の retry 上限、同型 failure、資源上限で止める | receipt から task family 別の追加試行の限界効用を算出できること |
| **P3** | Strategy-diverse best-of-N / Search Controller | **保留（No-Go）**。単純 resample は不採用 | T3 系に対し「分解・契約先行」など単発で異なる failure signature または成功を出す戦略が2つ以上見つかった場合だけ best-of-2 を再評価 |
| **P4** | Transition Store | **保留**。現在は再利用すべき有効な探索枝より receipt schema の安定が先 | 50件以上の同一 task family、複数 repository revision、memory-off 対照を用意できること |
| **P5** | Offline Replay | **保留**。Transition Store と temporal holdout が前提 | P4 が holdout の time-to-valid-patch を改善してから開始 |
| **対象外** | fine-tuning、汎用 architecture score、全面ランタイム移行 | **現時点では実施しない** | GPU/データ量/独立評価または明確な律速変化が生じたとき再検討 |

この順位では、元案の中心だった「Parallel Rollout / Search」は3番目ではなく **P3** まで下がる。反対に、元案では前処理に見えた task slice の本番配線、既存 gate を活かす分解、危険な routing の封じ込めが P0 である。これは構想を否定する判断ではなく、既に得られた反証に従って「探索へ compute を払う前に、1 回の呼び出しを安く・狭く・検証可能にする」判断である。

## 実装順序

最初から探索木や長期記憶を作らない。前節の優先度を、依存関係を含む実行順へ落とす。

1. **Now 1 — Routing safety**: project verify と planner の既知不適格条件を routing / deterministic gate で封じる。
2. **Now 2 — Read/edit 分離 + slice opt-in**: `read_files` 契約、fallback receipt、小ファイルの bypass を実装し、T5/T6 相当を本番経路で再測する。
3. **Now 3 — T3 の成果物分解 arm**: 実装、契約テスト、文書などを各1成果物の node にし、それぞれの checker で再投入する。ここで初めて欠落族が動くか確認する。
4. **Next — Coverage と receipt**: production frequency と blast radius の高い未測定面を埋め、task family 別の retry / fallback / cost を集計する。
5. **Conditional — Minimal adaptive budget**: 同型 failure の早期停止と family 別上限を receipt の実測から設定する。
6. **Deferred — Diverse search**: 単発で効果を示した独立 strategy が複数できた場合にだけ best-of-2 を再評価する。
7. **Deferred — Transition / replay**: schema が安定し、十分な件数と temporal holdout ができてから shadow mode で始める。

この順序では `Repository Compiler → Verifier → Search` をそのまま実装しない。Compiler は実証済みの slice に限定し、Verifier は新規フレームワークでなく既存 gate の適用範囲を広げ、Search は明示的な再開条件を満たすまで作らない。記憶は探索データの schema が安定してからでなければ、再利用不能なログを蓄積するだけになる。

### 実装進捗（2026-08-24）

- **Now 1 — 完了**: project acceptance は、charter に明記された deterministic command だけを機械評価する。`accept:` を含む自然文は human checklist へ送り、LLM の判定を project done の根拠にしない。planner は既存の plan review gate を維持し、承認前の計画を実行しない。
- **Now 2 — 配線完了・実測待ち**: `read_allocation` は編集可能な `--file` ではなく参照専用の `--read` へ渡す。大きい Python 参照に `slice: true` と `symbols` が明示された場合だけ一時 slice を使い、小ファイルは bypass、非対応・失敗時は原本へ fallback する。判断は `data.context_slices` receipt に残す。
- **Now 3 — arm 実装完了・実測待ち**: `T3splitgate` は schema と契約テストを一成果物/nodeへ分け、各 node の直後に C1/C3 checker を置く。次は `T3gate` との同条件比較で failure signature を測る。Now 2 の T5/T6 相当の本番再測も同じ評価期間に行う。

## 受入基準

同じ issue corpus、base commit、runtime 設定、wall-clock 上限で single-shot と比較し、次を満たしたときだけ次 phase へ進む。

- hidden/temporal holdout の task success rate が基準線を上回り、95% bootstrap confidence interval と raw counts を併記している。
- `pass@k` だけでなく、**time-to-first-valid-patch、検証込み wall time、peak RAM、失敗あたり費用**を報告している。
- 必須 gate を 1 件でも落とした候補は、ranking score にかかわらず採用されない。
- 候補は base commit ごとの隔離 worktree で実行され、枝間で未記録の状態を共有しない。
- task slice の各 fact は source location / blob hash を持ち、stale なら fail closed する。
- verifier は `PASS / FAIL / INCONCLUSIVE` を区別し、flaky・timeout・環境障害を実装失敗と混同しない。
- memory on/off、search on/off の ablation を行い、各機構の純増効果を示す。
- offline replay が holdout test、秘密、ネットワーク、production branch を直接利用・変更しない。
- 最大予算到達、同型候補の反復、改善停止時に終了し、非機械要件または high-risk 変更を明示的に escalate する。

### 最小実験

当初想定した `single-shot` 対 `best-of-3` は既に実施し、受入不変・壁時計悪化のため閉じた。次の最小実験は rollout 数ではなく、T3 欠落族に対する**分解単位**を比較する。

| Arm | 構成 | 確認したいこと |
|---|---|---|
| A | 現行 T3gate + retry 既定 | 欠落族の基準線（既測 0/3） |
| B | 一成果物/node + 各 node の deterministic checker | 欠落が task granularity で解消するか |
| C | B + read-only slice（大きい参照材料があるケースのみ） | 分解後も受入を維持し、prefill 費用を削れるか |

B が成立しなければ rollout を増やさない。まず failure signature が「成果物丸ごと欠落」から変化したかを見る。B が成立して初めて、その分解 strategy と別の単発 strategy の両方が存在するかを調べる。多様性がないなら N を増やしても改善しない。

## 検証方法

1. 過去 issue を commit 時点で再現し、時系列で development set と holdout set に分ける。
2. 各 arm へ同じ wall-clock と hardware envelope を与え、seed と実行順をランダム化する。
3. candidate、prompt/task-slice hash、tool trace、patch hash、gate receipt、費用を JSONL へ保存する。
4. task-level paired comparison で成功率、時間、資源量を比較し、平均だけでなく中央値、p90、raw numerator/denominator を出す。
5. verifier の偽陽性を、人手で確定した小さな gold set と mutation injection で測る。
6. stale index、flaky test、timeout、OOM、同一 patch 反復、悪意ある test 変更、secret 混入を fault injection する。
7. memory/replay は shadow mode から始め、holdout 回帰がないことを確認して昇格する。

## 非目標と残る限界

- 小型モデルを大型モデルと同等にする保証はしない。探索は候補集合に正解が一度も入らない問題を解けない。
- test pass は仕様充足、セキュリティ、UX、長期保守性の証明ではない。
- call graph や architecture constraint の完全自動抽出は目標にしない。未知を明示する。
- fine-tuning を恒久的に否定しない。十分に品質管理された transition が蓄積すれば、将来の比較対象になりうる。
- 並列数を増やすこと自体を成果にしない。メモリ帯域を共有する単一 PC では、直列 best-of-N の方が速い場合がある。

## 結論

方向性は妥当である。ただし中心命題は「E4B を 100 回使えば強くなる」ではなく、次のように置く方が正確である。

> **E4B を限定された候補生成 policy として使い、根拠つき repository slice、相関を抑えた有界探索、隔離された機械検証、鮮度管理された遷移記憶を組み合わせる。追加 compute は、holdout 上の限界改善が費用を上回る間だけ投入する。**

これは「モデルを育てずにエージェントを育てる」という元案を維持しつつ、正解判定の不完全性、探索の相関、test overfitting、家庭用 PC の資源制約を設計の第一級要件にした形である。
