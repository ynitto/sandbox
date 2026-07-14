# agent-project / agent-flow への Issue→Spec→実装→学習フロー統合案

> 作成日: 2026-07-12 ／ ステータス: **実装済み（P1–P5 全フェーズ・2026-07-12）**
> 対象: `tools/agent-project/` `tools/agent-flow/` `tools/agent-dashboard/`
> 前提: [`agent-project-design.md`](../designs/agent-project-design.md)（不変条件 §1・拡張点 §4.1）
> [`agent-flow-design.md`](../designs/agent-flow-design.md)
>
> 実装の正典は設計書側: リスクダイジェスト=§5.4.6・アセスメント/spec 連鎖=§5.10・
> plan の after 出力=§6.1・repo-map=§6.5。本書は経緯と根拠のアーカイブ。

次の概念フローを、既存の設計・思想を崩さずに取り込む案。viewer はフロントエンドのまま、
ユーザー操作が複雑にならない範囲で可視化を足す。

```
Issue → Repository Understanding → Backlog Planner → Dependency Graph
  → Spec Orchestrator（Complexity/Risk/Ambiguity 評価。Spec 不要なら素通り）
  → Kiro Spec Driven（spec.md / design.md / tasks.md）
  → Task Scheduler → Parallel Implementation → Continuous Review
  → Validation Pipeline → Risk Assessment → Human Approval
  → Knowledge Learning → Next Backlog
```

---

## 1. 結論の先出し

フローの 14 段のうち **9 段は既存機構がそのまま対応する**。本当に無いのは次の 5 つで、
どれも agent-project 設計書 §1 の不変条件を保ったまま「外周を足す」形で入る。
**ループの背骨（S0–S7）に新しいステージは増やさない**。

| # | ギャップ | 入れ方（一言で） | 触る場所 |
|---|---------|----------------|---------|
| G1 | Spec Orchestrator（3軸評価） | S0 triage にアセスメント（採点→ルート決定）を足す | agent-project S0 |
| G2 | Kiro Spec Driven（spec/design/tasks） | spec 前段タスクを既存プリミティブ（after DAG＋needs 承認＋決定的取り込み）で連鎖させる | agent-project S0/S6 |
| G3 | Dependency Graph（計画段の依存） | plan 分解の出力スキーマに `after` を足す | plan プロンプト＋enqueue |
| G4 | Repository Understanding | リポジトリ理解を成果物（context/*.md）にして plan / act / verify 合成へ注入する | 文脈注入 §6.4 の拡張 |
| G5 | Risk Assessment（承認前の要約） | review の needs カードに決定的なリスクダイジェストを添付する | agent-project S4 |

agent-flow 側の改修は**不要**（任意の追補のみ・§7）。理由: このフローの肝は「Spec 推奨なら
人の承認を挟んでから実装に降りる」ことだが、agent-flow の run は終端まで自動で走る実行層で、
人の承認待ちを表現する場所は agent-project の needs/ 契約にしかない。よって Spec Driven は
バックログ層（agent-project）で表現するのが正しい。

---

## 2. 対応表 — フロー段 × 既存機構

| フロー段 | 既存の対応物 | 判定 |
|---------|------------|------|
| Issue | inbox/（E4 push）・`enqueue`・`intake_cmd`（E3 pull）・charter | ✅ そのまま |
| Repository Understanding | `detect_repo_context`（verify 合成向けの軽い検出）・charter `## repos`・repos.json | ⚠️ 断片的（計画前の「理解の成果物」が無い）→ G4 |
| Backlog Planner | charter 三相ループ① plan（`plan_via_agent`）・flow-planner スキル | ✅ そのまま |
| Dependency Graph | タスク `after:` DAG・`impact`・viewer のタスクグラフ描画 | ⚠️ 実行・可視化はあるが plan が after を出さない → G3 |
| Spec Orchestrator | 無し（近縁: plan_review・intake-recall・rot） | ❌ → G1 |
| Kiro Spec Driven | 無し（kiro-cli 自体の spec 機能は act の 1 回の run 内に閉じ、人の承認を挟めない） | ❌ → G2 |
| Task Scheduler | S1（planner / policy / claim / concurrency / after 除外） | ✅ そのまま |
| Parallel Implementation | agent-flow worker 並列・git バス分散・`--concurrency` | ✅ そのまま |
| Continuous Review | adversarial-verification・統合前 gate（`--review`）・gitlab レビュー・in-flight 反映（§18.4） | ✅ そのまま |
| Validation Pipeline | S3 検証ゲート（verify・回帰・red-green・flake・protect・進捗） | ✅ そのまま |
| Risk Assessment | protect / gate / audit はあるが「承認直前のリスク要約」が無い | ⚠️ → G5 |
| Human Approval | plan_review・delivery_review・milestone gate・needs/ 三値決着 | ✅ そのまま |
| Knowledge Learning | decisions/ learn/avoid・蒸留・ltm 昇格・verifylib（§11 統一学習バス） | ✅ そのまま |
| Next Backlog | followup・evaluate→改善タスク・replan・charter 更新で次フェーズ | ✅ そのまま |

「✅」の 9 段は**改修ゼロで概念が既に立っている**。設計書にフローとの対応を書き足すだけでよい。

---

## 3. G1 — Spec Orchestrator: S0 の投入時アセスメント

### 3.1 何をするか

新規タスクの投入経路（plan / enqueue / inbox / followup / intake / tasks.md 展開）で、
タスクを 3 軸で採点し、`spec` ルートに乗せるか素通りさせるかを決める。

- 採点: `complexity` / `risk` / `ambiguity` を各 1–3 で。実装は `synth_verify` と同じ流儀:
  **単発 LLM 委譲（`_run_kiro_cli`・有界・1 タスク 1 回）＋決定的フォールバック**。
  agent 不在・失敗時はヒューリスティックのみで採点する（protect パス一致 → risk+、
  avoid recall ヒット → risk+、verify 未定義 or accept のみ → ambiguity+、
  cohort_items 持ち・title の対象範囲の広さ → complexity+）。
- 記録: タスクフィールド `- assess: c=2 r=3 a=1` と `- route: spec|direct`。
  既知フィールドとして順序保持で書き戻す（§8 データモデルの流儀）。
- ルート決定は**決定的**: `max(c,r,a) >= spec_threshold`（既定 3）なら spec ルート。
  しきい値は設定。採点だけが LLM で、分岐は本体が決める（不変条件 5）。

### 3.2 人が必ず勝つ（不変条件 3）

- policy.md に `spec: <pattern>`（強制 spec 化）を追加。タスク明示 `- route: direct` は
  採点に勝つ。plan_review の needs カード（kind=plan-review）に採点と推奨ルートが載るので、
  人は承認時に見て `- route:` を書き換えるだけでよい。**新しい承認ステップは増えない**
  （既定 on の plan_review に相乗りする）。
- アセスメントは「タスクを足す（spec 前段を前置する）」方向にしか働かない。done の条件・
  予算・優先順位には触れない。

### 3.3 段階導入

まず**採点の表示のみ**（ルーティング無効・`spec_track: false` 既定）で入れ、needs カードで
採点の妥当性を人が見てから、`spec_track: true` でルーティングを有効化する。
`--review-project` と同じ opt-in の入れ方。

---

## 4. G2 — Kiro Spec Driven: spec 前段タスクの連鎖

### 4.1 形 — 新しいループ段ではなく、タスクの並びで表現する

spec ルートのタスク T は、S0 で次の連鎖に**決定的に**変換される。

```
T-spec（spec 作成タスク）                     T の実装タスク群（tasks.md から展開）
  act  : specs/<T>/spec.md・design.md・        after: T-spec
         tasks.md を書く                        act 文脈に spec.md/design.md を注入
  verify: 決定的テンプレ（file-exists＋   ──▶  （§6.4 の charter 注入と同列・有界）
         必須見出しの file-contains）           最後に T 本来の verify を持つ
  review: human（必ず検収＝人が spec を承認）    総合検証タスク（after: 実装全件）
```

- **T-spec の承認は既存の needs 契約そのまま**。三値決着も同じ: 承認 → tasks.md を展開／
  差し戻し（フィードバック記入）→ spec を書き直して再提案／却下 → T ごと廃止＋avoid 記録。
- **tasks.md の書式は enqueue --json 互換**（`schemas/task.schema.json` の spec 配列を
  Markdown 内の JSON ブロックで持つ）。展開は既存 `_enqueue_specs` の再利用で、
  E3/E4 と同じデータ契約に乗る。新しいパーサを作らない。
- 元 T の verify は捨てない: 展開時に「T の総合検証」タスク（after: 実装タスク全件、
  verify = T の verify）として残す。**done は verify のみが根拠**（不変条件 1）を保ったまま、
  spec → 実装 → 統合検証が既存の after DAG だけで立つ。
- 上限: 展開数は `--max-spawn` の傘の下（followup と同じ計上）。無限に増えない（不変条件 2）。

### 4.2 置き場所 — 状態リポジトリの specs/

`<root>/specs/<task-id>/{spec.md,design.md,tasks.md}` に置く。needs/decisions と同じ
「人の判断が通るファイル」であり、git 同期（direct state-git）で viewer から読み書きできる。
成果物リポジトリ側ではない（spec は成果物でなく、実装タスクを生む中間判断材料。
実装が done になれば具現化されたコードが正になる）。

kiro-cli（Kiro 本体）の steering / spec 機能とは独立に持つ。act の中で kiro-cli が自前の
spec を切るのは従来どおり自由だが、**人の承認を挟む spec はループの外（ファイル契約）に
出ていなければならない**、が本案の要点。

---

## 5. G3 / G4 / G5 — 小さい 3 件

### G3: plan 分解が after を出す

`_plan_decompose_prompt` の出力スキーマに `"after": ["先行タスクの title"]` を追加し、
`_enqueue_specs` 側で title → 採番 id に決定的に解決する。循環は既存
`_after_introduces_cycle` で拒否（不正 after は落として journal に残す）。
tasks.md 展開（G2）も同じ経路。viewer のタスクグラフは既にあるので、
**計画した瞬間から依存グラフが見える**ようになる。フローの Dependency Graph 段はこれで立つ。

### G4: Repository Understanding の成果物化（opt-in）

- charter モードの plan 前に、各書込先 repo の理解を `context/<repo-name>.md` として
  エージェントに生成させる（構造・主要モジュール・ビルド/テストコマンド・規約。有界 2000 字目安）。
- **repo HEAD の sha を署名にキャッシュ**し、変わらなければ再生成しない（charter 変更署名と
  同じ流儀）。生成失敗は空＝従来動作。
- 注入先は 3 つ: ① `_plan_decompose_prompt`（分解の精度）② `build_request`（act の文脈）
  ③ `synth_verify`（`detect_repo_context` の上に重ねる）。どれも既存の有界注入と同列。
- 生成コストがあるので `repo_map: false` 既定の opt-in。

### G5: Risk Assessment — 承認前のリスクダイジェスト

S4 で review（検収待ち）の needs/<id>.md を作るとき、`## リスク` 節を添付する。
**決定的な材料だけで成立**させる: protect パス接触の有無・diff 統計（workdir の git）・
回帰ゲート結果・red-green 証跡・retry 回数・avoid 類似ヒット・G1 の採点・cost。
LLM の一段要約は任意（不在でも欠けない）。gitlab-gatekeeper の「人間が 1 枚で決める
判断パケット」の考え方を needs カードに薄く移植したもの。既存の承認フローは一切変えない。
情報が増えるだけ。

---

## 6. viewer 拡張（操作を増やさず、見え方だけ足す）

viewer は今も needs/commands/inbox/charter/policy のファイル契約と git 同期だけで
結合している。この結合点は変えない。

1. **パイプラインリボン**（✅ 実装）: タスクタブとかんたんモードの概要カードに、概念フロー上の
   現在地を件数付きで 1 行表示する。既存 status＋タグからの**純粋な写像**で、新しい状態は増えない:
   `inbox/draft/proposed`=計画 → `spec_for / route:spec 未展開`=Spec → `ready/doing/offloaded`=実装 →
   `review/blocked`=承認 → `archive`=完了。Spec 段は spec ルーティング運用時のみ現れる
   （該当タスクも specs/ 成果物も無ければ非表示＝従来と同じ見た目）。
2. **spec カード**（✅ 実装）: needs カード（spec 作成タスクの検収・展開後の総合検証の両方）に
   specs/<id>/ の 3 ファイルを開くボタンが載る。承認・差し戻し・却下は既存の needs 操作そのまま。
3. **リスクバッジ**（✅ 実装）: needs カードに low/med/high バッジ。ダイジェスト全文は
   「判断材料を見る」の折りたたみに含まれる（`## リスク` 節）。
4. **タスクグラフ**: 既存機能。G3 で計画段階からエッジが入るため改修不要。

「かんたんモード」ではリボンとバッジだけ見せ、spec 本文・ダイジェスト詳細は
メンテナンスモード側に置くと、難解さを持ち込まない。

---

## 7. agent-flow 側（任意の追補のみ）

- 必須の改修は無い。spec 前段も実装タスクも、agent-flow から見ればただの act。
- 任意: flow-planner の patterns-catalog に「spec-driven 複合」
  （`generate(spec) → verify(spec 妥当性) → split(tasks) → map(実装) → reduce`）を
  variants として追記できる。ただしこれは**人の承認を挟まない run 内 spec** であり、
  フロー図の Spec Driven（人が spec を見る）の代替にはならない。用途が違うことを
  カタログに明記する。

---

## 8. 不変条件チェック（agent-project 設計書 §1）

| 不変条件 | 本案での扱い |
|---------|-------------|
| 1. done は verify のみ | spec 承認は plan_review/needs の既存ゲート。T の verify は総合検証タスクとして残る。採点・spec 化は done を作れない |
| 2. 必ず有限回で止まる | 採点は 1 タスク 1 回・repo-map は sha キャッシュ・tasks.md 展開は max_spawn の傘の下 |
| 3. 人の policy ＞ エージェント | policy `spec:`・タスク `- route:` が採点に勝つ。spec は人の承認なしに実装へ降りない |
| 4. 標準ライブラリのみ | 全て既存のファイル操作＋`_run_kiro_cli` サブプロセス |
| 5. 決定的操作＋知能は委譲 | 採点・spec 生成・repo-map は LLM 委譲、ルート分岐・展開・ダイジェストは本体が決定的に行う |

拡張点の規律（§4.1「ここに列挙の無い場所へは差し込まない」）も守る: 新しい外部 CLI
フックは作らない。G1–G5 は全て本体機能の拡張で、外部からは従来どおり E1–E6 と
policy/commands で触る。

---

## 9. 段階導入

| フェーズ | 内容 | 価値の出方 | 状況 |
|---------|------|-----------|------|
| P1 | G5 リスクダイジェスト（決定的のみ）＋ viewer バッジ | 最小の変更で承認の質が上がる | ✅ 実装（`risk_digest`・needs frontmatter `risk:`・viewer バッジ） |
| P2 | G1 採点（表示のみ・ルーティング無効） | 人が採点の妥当性を needs カードで確認できる | ✅ 実装（`assess_task`・`--assess` 既定 on） |
| P3 | G1 ルーティング有効化 ＋ G2 spec 連鎖 ＋ viewer spec カード | フローの核（Spec Orchestrator / Spec Driven）が立つ | ✅ 実装（`route_spec_tasks`/`expand_spec_tasks`・`--spec-track` opt-in・viewer spec ボタン） |
| P4 | G3 plan の after 出力 | 計画段階から依存グラフが見える | ✅ 実装（plan スキーマ `after`・`_resolve_after_titles`） |
| P5 | G4 repo-map（opt-in） | 分解・実装・verify 合成の精度向上 | ✅ 実装（`ensure_repo_maps`/`repo_map_context`・`--repo-map` opt-in） |

各フェーズは独立に戻せる（設定既定はすべて従来挙動）。P1/P2 は本体の出力が増えるだけで
挙動が変わらないため、稼働中プロジェクトに入れても安全。

---

## 10. 未決事項（実装時の決着）

- 採点しきい値既定 → `spec_threshold: 3` で実装（設定で 1-3 に調整可）
- tasks.md 内 JSON の必須キー → title 必須・verify 推奨（無ければ inbox 行き＝既存規則）・
  after/accept/verify_template/note/priority は任意、で実装
- spec 差し戻し → 既存の needs フィードバック（同一タスクの再試行）をそのまま使う（専用上限なし。
  verify NG は max_retries が締める）
- repo-map の共有単位 → repo 単位（`context/<repo名>.md`・charter 非依存）で実装
- spec タスクの委譲問題 → 解消（spec タスクは location=local 固定＋委譲 executor を agent へ
  差し替え。実装タスクは従来どおり委譲される）
- viewer パイプラインリボン → 実装（§6）
