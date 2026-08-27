# Gemma4 ハーネス改善案（2026-08-28 メモ）の agent-tools 適用性レビュー

> 作成 2026-08-27（同日改訂: リポジトリ採択済み提案 2 本との突合を追加）
> 対象: [docs/plans/2026-08-28-gemma4-harness-improvement.md](../plans/2026-08-28-gemma4-harness-improvement.md)（外部チャット由来の意見メモ。以下「計画メモ」）
> 位置づけ: 計画メモは一意見であるため、(1) 引用されている研究・OSS の実在と数値を裏付け、
> (2) agentcore の現行実装と突合し、(3) リポジトリの採択済み提案 2 本 —
> [2026-08-27 効果的改善案](../plans/2026-08-27-agent-tools-local-llm-effective-improvement-proposals.md)（以下「改善案」）と
> [2026-08-27 スラッシュ再構成設計](../plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md)（以下「スラッシュ設計」）
> — との重複・衝突を確かめた上で、採用・保留・不採用を切り分ける。
> 前提は改善案と同じ（CPU only / RAM 〜32 GB / ollama 中心 / worker は `aider` + `gemma4:e4b`）。

---

## 0. 結論の先出し

- **計画メモの「7 つの補助輪」のうち、agent-tools に未実装なのは実質 3 つ半。**
  2（one-step 実行）と 4（外部 verifier）は実装済み。1・3・5・6・7 は部分実装で、
  計画メモが暗黙に置く「ゼロから作る」前提は現行コードと合わない。
- **計画メモの中心線「モデルに判断させる範囲を削る」は、採択済み提案 2 本が既に別ルートで走っている。**
  スラッシュ設計 §3.4 はツールセット・ハーネスの選択を「モデルの判断」から「1 語」へ移し
  （`/ask` `/find` `/edit` `/sm`、未知は明示エラー）、改善案は割り当てと材料の決定化を進めている。
  計画メモから取り込む価値のある差分は、**両提案のどちらにも無いものだけ**であり、
  それは「ターン上限の引き締め」と「check 失敗の選別注入」の 2 点に絞られる。
- **裏付けが最も強い施策は、最も地味な「ターン上限の引き締め」である。**
  原典（state-harness）の実測は「naive なターン上限だけで Gemma4:E4B が +35pp」。
  しかも同じ原典は「凝ったハーネス（state-harness 本体）は naive 上限と有意差なし」と報告している。
  つまり計画メモの複雑な FSM 図を作り込む根拠は、計画メモ自身の引用元からは出ない。
- **引用の裏付け結果はまだら。** 「Better Harnesses, Smaller Models」「state-harness」
  「MagenticLite」「SmallCTL」は実在確認。**Argus の「E4B 83% vs Qwen2.5-3B 58%」は確認できず**、
  採用判断の根拠にしない。
- **採用に値するのは 3 点で、いずれも新機構を起こさず既存機構へ載せる**:
  (P1) write 系ステートのラウンド上限を E4B 向けに 2〜3 へ絞る評価 arm（宣言キー 1 つ）、
  (P2) check 失敗時の「失敗テストのみ選別注入」（`_sm_check_note` の決定的抽出 → 将来は
  スラッシュ設計 段 13 の `observation_template` へ）、
  (P3) ハーネス側検索（`/find` / `retrieve` の**裏側**を決定的 rg → 抜粋供給へ差し替える。
  ただし改善案 案 B の `read_files=` 配線が先）。

---

## 1. 引用の裏付け結果

| 計画メモの引用 | 実在 | 裏付けの中身と割引 |
|---|---|---|
| Better Harnesses, Smaller Models（2026-07） | ✅ [arXiv:2607.08938](https://arxiv.org/abs/2607.08938)（CMU） | 16/21 タスク×SLM 組で改善、最良 89.7% を 4% コストで回収 — 数値は原典どおり。ただし**タスクの反復性と効果の相関 ρ=−0.96**: 反復業務でのみ効き、新規性の高い問題には効かないと明記。open-ended coding への一般化は論文の主張外 |
| state-harness の実験（E4B 35%→70%） | ✅ [GitHub: vishal-dehurdle/state-harness](https://github.com/vishal-dehurdle/state-harness) | naive turn cap 単体で E4B +35pp、4 モデル平均 +17.5pp — 計画メモの記述とほぼ整合。**計画メモが落としている重要点**: (a) state-harness 本体は naive cap と有意差なし（価値は診断のみ）、(b) 自作 20 タスク + SWE-bench 37 件の小規模で、著者自身が再現要と明記、(c) 単発試行の 8% 未満の差は非決定性フロア内。推奨「open-ended coding は 2〜3 ターン」はここから |
| Argus（E4B tool-chain 83% vs Qwen2.5-3B 58%） | ❌ 確認できず | 該当するリポジトリ・数値とも検索で発見できず（同名の無関係プロジェクトのみ）。**この数値を根拠に使わない** |
| SmallCTL | △ 実在（[紹介動画 2026-03](https://www.youtube.com/watch?v=tY96h6bokdk)） | SLM 向け端末ハーネスとして実在するが、定量評価は見つからず。設計の参考止まり。なおスラッシュ設計 §8.1 は外部エージェント 4 本を実パッケージで評価済みで、「規約だけ借りる」判定基準が既にある — SmallCTL を追うならその表に載せて同じ基準で裁く |
| MagenticLite / MagenticBrain | ✅ [Microsoft Research（2026-05）](https://www.microsoft.com/en-us/research/blog/magenticlite-magenticbrain-fara1-5-an-agentic-experience-optimized-for-small-models/) | 実在。ただし**専用学習済みモデル（MagenticBrain / Fara1.5）前提**で、既製 Gemma への転用効果は示されていない。fine-tune を閉じている本リポジトリの前提（改善案 §5）とは交わらない |
| 「SLM の tool use には schema 制約が特に有効という survey」 | △ 両論あり | 有効説の一方で、[Tam et al. "Let Me Speak Freely?"](https://arxiv.org/abs/2408.02442) は **format 制約が生成系タスクの推論を 10〜30% 劣化させる**（分類系は劣化しない）と報告。推奨される緩和は「思考は自由形式 → 出力のみ制約」の 2 段。agent-tools が `format` 指定時に `think: false` を強制している実測（`ollama_loop.py:259-272`）はこの問題と同族であり、無条件の schema 固定を支持しない |

---

## 2. 「7 つの補助輪」× 現行実装 × 採択済み提案の突合

対象コードは `tools/agent-tools/agentcore/`。右端列は、残ギャップが採択済み提案で
既に手当てされているかを示す。

| # | 計画メモの提案 | 現状 | 主な根拠 | 採択済み提案との関係 |
|---|---|---|---|---|
| 1 | FSM（PLAN→ACT→VERIFY→RECOVER） | **部分実装** | YAML 駆動 FSM は完備。遷移判定はハーネスが測った値のみで、モデル文は 1 バイトも混ぜない（`harness/statemachine.py:595-607`、`:671-703`）。欠けるのは正準 4 相テンプレと RECOVER 状態（既定は escalate = exit 3）。ステート内は 4 動詞 × 最大 8 周の小ループが残る | 起動面はスラッシュ設計 種別 B（`/sm <名前>` の 1 語で対話・ヘッドレス同一起動）が解決。残るのはテンプレ例 YAML の有無だけ |
| 2 | one-step 実行 | **実装済み** | 「exactly one state-machine action / Do not work on later states」（`statemachine.py:207-209`）、1 ターン 1 コマンド（`ollama_loop.py:669-671`）、skill 側の著述規約（`statemachine-use/SKILL.md:41-49`） | — |
| 3 | schema 制約出力 | **部分実装** | 推論層の文法制約は導入済み（ollama `format`、プロンプト費ゼロ設計。`ollama_loop.py:241-283`）。tool 契約自体は文章教示 + 事後の寛容パース（`harness/toolloop.py:291-368`）。`expected_result` は不在 | GBNF は改善案 案 G の評価 arm のまま。arm が勝った場合の着地はスラッシュ設計 種別 C 宣言の `output:` 語彙（コマンド単位の出力契約）で、engine コードには入れない |
| 4 | 外部 verifier | **実装済み** | check の exit code を oracle にし、シェルなし argv 実行（`statemachine.py:573-633`）。SHA-256 による変更証跡ゲート（`toolloop.py:740-753`、`:894-915`）。receipt は自己申告 verdict を無視して再導出（`verifycontract.py:308-366`）。judge は prose 基準限定・fail-closed の第 2 層のみ | 初稿で挙げた「git diff を証跡に使っていない」ギャップは**新提案不要** — スラッシュ設計 §7.3 B / 段 9b（`git status --porcelain` 差分で宣言外ファイルの変更を検知、失敗ラウンド巻き戻しも同じ観測に載せる）が既に計画している |
| 5 | evidence-based context | **部分実装** | AST 決定的スライス `context_slice.py` は agent-flow へ配線済み（`agent_flow/context.py:78-124`、fallback 領収書つき）。読み材料の事前割付もある。**ハーネス側 ripgrep→チャンク供給は不在** — 検索は今もモデルが `read` toolset で自走する | 改善案 案 B が本番配線と `read_files=` 経路（読む材料と編集対象の区別）を計画済み。スラッシュ設計の `/find` は toolset を read セットへ**固定**するが、検索そのものはモデル自走のまま — ここが P3 の対象 |
| 6 | 失敗分類 + 回復レシピ | **部分実装** | env/transient/quota の分類が再試行経路を駆動（`agentcli.py:764-783`）。check 失敗時は実出力 2000 字を再注入（`statemachine.py:622-633`、盲目再試行より 28% 速い実測コメントあり）。**失敗テストのみの選別注入は不在** | ペイン経路に失敗トリアージ・quota 観測が無い穴（実害最大）はスラッシュ設計 §7.4-1 / 段 7 が対処予定。「tool error → 規約再提示」はスラッシュ設計 段 13 の `format_error_template`（言い直しの宣言化）が同じものをより測定可能な形で持つ |
| 7 | hard stopping → 強制 REPLAN | **部分実装** | 同一動作・無変更・verifier 枯渇・context 枯渇の停止は全て存在（`statemachine.py:471-482`/`:825-842`、`ollama_loop.py:876-884`、`ollama_context.py:230-239`）。終端は escalate/停止/著者定義 continue であり、**REPLAN へ戻す経路はない** | 改善案 §3 の役割表が「長い agent loop は割る・ゲート・escalate」と既に定めており、REPLAN 不在は設計判断として整合（§3-1） |

計画メモ末尾の「Gemma をエージェントにしない」アーキ図（state machine + tools + verifier +
局所判断関数としての Gemma）は、agent-tools の現行設計とほぼ同型である。新奇なのは構図ではなく
「どこを締めるか」の差分だけで、§4 はその差分に絞る。

---

## 3. 批判的レビュー（計画メモに対して）

1. **最強の裏付けは「構造の追加」ではなく「回数の削減」を支持している。**
   state-harness の原典は、監視・分類・安定性解析を持つハーネス本体が naive cap に勝てなかったと
   自ら報告している。計画メモの図が示唆する多段機構への投資は、引用元の実測とむしろ逆向き。
   本リポジトリの実測（案 G「GBNF は現行で十分なら閉じる」、`schema.md:105-107` の
   「再試行で直るのは仕様読み違え級のみ、全欠落級は 9/9 同一失敗 → tier 引き上げ」）とも整合するのは
   「機構追加より上限と escalate」の側である。
2. **数値の確度が引用ごとに大きく違うのに、メモ内では等価に並んでいる。**
   Argus の 83% は確認不能、state-harness は小規模・要再現、Better Harnesses は査読前だが
   設定が明確。裏付け強度で重み付けし直すと、優先順位は計画メモの並び（FSM が 1 番）から
   「上限（7）→ 失敗別注入（6 の一部）→ 検索の決定化（5）」へ入れ替わる。
3. **schema 制約の全面適用は劣化リスクと prefill 費の両方に反する。**
   「action/tool/args/expected_result まで固定」は、(a) format 制約の推論劣化（§1）、
   (b) tool schema の固定 prefill 費を避けるという ollama 層の設計判断（`ollama_loop.py:16-22`。
   スラッシュ設計 §8.1 が smolagents をプロンプト 17 KB で却下したのも同じ理由）、
   の両方と衝突する。制御席（planner/judge 等）は既に JSON 制約 variant へ振替済みであり、
   残るのは worker の tool 契約だけ。ここは GBNF arm の実測（案 G）で決めるべきで、計画メモの
   記述だけで採用しない。
4. **`expected_result` は検証の重複。** 検証は既に外部 oracle（check / SHA-256 / receipt 再導出）で
   行っており、モデル自己申告の期待値を足しても oracle にはならない。計画メモがこのフィールドで
   検知したい「宣言していないものを触った / 期待と違う結果になった」は、スラッシュ設計 段 9b の
   git 差分観測が **oracle 側で**（モデルの申告に頼らず）解く。
5. **反復性の限定を引き継ぐ必要がある。** Better Harnesses の ρ=−0.96 は、agent-tools の
   得意領域（extract/classify/split 等の定型役、6/6 実測）には合うが、open-ended な実装ステートへ
   同じ期待を持ち込む根拠にはならない。
6. **計画メモは本リポジトリの実測資産と採択済み提案を参照していない。**
   「evidence-based context」は context_slice として実装・一部配線済み（tokens −72〜87% の実測つき）。
   「モデルに tool 選択をさせない」はスラッシュ設計 §3.4 が構造の副産物として既に採っている。
   方向が独立に収束していること自体は傍証として価値があるが、計画メモ固有の寄与は
   両提案のどちらにも無い 2 点（ターン上限・失敗選別注入）へ縮む。
7. **適用先を間違えると採択済み設計と衝突する。** 計画メモを素直に実装すると、
   ルート表を設定ファイル化しない・edit toolset を新設しない・エージェント実装を持ち込まない
   というスラッシュ設計 §8 の非目標や、「自由 tool-loop の無条件解放をしない」という
   改善案 §5 の非目標に抵触しやすい。P1〜P3 はすべて「宣言・テンプレ・ルータ・既存関数」の
   語彙で書けるものに限定した。

---

## 4. 適用提案（効く順）

### P1 ★★★ write 系ステートのラウンド上限を E4B 向けに引き締める（評価 arm）

**何をするか。** 現行の `_TL_MAX_TOOL_ROUNDS = 8`（`toolloop.py:50`）/ `DEFAULT_MAX_ROUNDS = 12`
（`ollama_loop.py:60`）に対し、`write:` を持つステート限定でラウンド上限 2〜3 を宣言から
渡せるようにし、eval archive で受入率・壁時計を現行と比較する。

**宣言の置き場。** 新フィールドの発明はしない。statemachine YAML の state 単位キー
（`max_steps` / `check_retries` と同じ階層）か、スラッシュ設計 種別 C 宣言の frontmatter
（`options:` — `llm` Template の既存語彙）のどちらかに載せる。後者なら `/edit` と `/verify` で
別の上限を課せるので、種別 C が入った後はそちらへ寄せる。

**測定規律。** スラッシュ設計 段 12〜13 と同じ: **単一軸の対照**（同時に model / policy /
sampling / プロンプトを変えない）、ledger で T2 対照、既定値の変更は自前 eval が
原典（+35pp）の方向を再現してから。原典は小規模のため、再現しなければ arm ごと閉じる
（改善案 案 D「測って駄目なら捨てる」と同じ規律）。上限超過の既定は現行どおり escalate
（REPLAN 新設はしない — §3-1）。

### P2 ★★ check 失敗時の「失敗テストのみ」選別注入

**何をするか。** `_sm_check_note()`（`statemachine.py:622-633`）は現在、check 出力の末尾 2000 字を
無選別に再注入している。check が pytest 系のとき、失敗テストのブロックだけを決定的に抽出して
注入する（抽出不能時は現行の末尾切り詰めへ fallback）。

**型は既存のものを使う。** これは改善案 案 C の P4 型（候補生成 → 決定的検算）と同じ
「抽出は機械・fallback は原文」の規律であり、context_slice の 2 原則
（切れなければ原本へ倒す・省略した事実を明示する）をそのまま踏む。実装位置は当面
`_sm_check_note` 内の決定的パーサ 1 つ。スラッシュ設計 段 13（`observation_template` —
ツール出力の詰め方の宣言化）が入った後は、この整形をコードから宣言側へ移し、
コマンド単位で調整可能にする——段 13 の受入条件「現行出力が変わらないことを確認してから
調整を始める」に P2 の抽出もそのまま乗る。

**なぜ。** 計画メモ 6 の中で唯一、既存実装（再注入で 28% 高速化の実測）の素直な延長であり、
E4B の長文弱さ（MRCR、改善案 事実 6）にも合う。失敗分類表の first-class 化はやらない —
分類が要るのは env/transient/quota（実装済み）と「直るか直らないか」（tier 判断、実装済み）で
足りており、ペイン経路の分類欠落はスラッシュ設計 段 7 が別途埋める。

### P3 ★★ ハーネス側検索（rg → 抜粋供給）— 案 B の後、`/find` の裏側として

**何をするか。** 現在モデルが `read` toolset で自走している探索（`rg` はモデルが打つ許可コマンド、
`ollama_loop.py:96`）を、ハーネス側の「決定的 rg → ヒット周辺チャンク → 読み材料へ事前割付」で
置換する opt-in 経路を足す。**利用者と engine から見える語彙は変えない**——スラッシュ設計の
`/find`（種別 B）と `retrieve` 用途（種別 C）の**裏側の実装**をルータ経由で差し替える形にする。
toolset の固定（スラッシュ設計が解く）と、検索自体の決定化（本提案）は別の層である。

**検索パターンはモデルに書かせない。** 改善案 案 C の実測で、e4b はパス・テスト名の候補生成 3/3 に
対し **regex は読み違いで 0/3**。したがって rg へ渡すパターンは、編集対象の import / シンボルから
決定的に導出する（context_slice と同じ規律・同じ素材）。モデルに任せるのは残る曖昧ケースの
「候補からの選択」までで、誤りは機械が落とす（P4 型）。

**順序。** (1) 先に改善案 案 B（`read_files=` の導入とスライス本番配線）——これ無しでは供給した
抜粋が aider の `--file`（編集可能）に落ちる配線の上に積むことになる。(2) 着手判断の前に、
read toolset の探索が実際にラウンドを何周消費しているかを eval ログで測る——消費が小さければ
P3 は閉じてよい。(3) 実装はスラッシュ設計 段 1〜4（ルータと種別 C）が入っていれば宣言 1 枚で
済み、入っていなくても `prepare_read_allocation_files` の隣に同型の関数を足すだけで独立に出せる。

### 保留・不採用

| 項目 | 判定 | 理由 |
|---|---|---|
| PLAN→ACT→VERIFY→RECOVER 正準テンプレ | 保留 | 既存 YAML FSM で表現可能（`gated_implement.yaml` に PLAN/repair 状態を足すだけ）。起動の摩擦は `/sm` の 1 語化（スラッシュ設計 種別 B）が解消する。構造追加が効く実測はなく、原典は逆を示す（§3-1）。欲しくなったら例 YAML の追加で足り、コード変更ではない |
| 強制 REPLAN 経路（7 の後半） | 保留 | 停止条件は完備。escalate で上位 tier に渡す現行設計は `schema.md:105-107` の実測と改善案 §3 役割表（長い loop は割る・ゲート・escalate）に裏付けられており、E4B に再計画させる方が良い証拠はない |
| tool 契約の JSON Schema / GBNF 固定（3） | 保留 | 改善案 案 G の評価 arm のまま。推論劣化（§1）と prefill 費（`ollama_loop.py:16-22`）の両論を実測で裁く。arm が勝った場合の着地は種別 C 宣言の `output:` 語彙で、engine には入れない |
| `expected_result` フィールド | 不採用 | 外部 oracle と重複し oracle 性がない（§3-4）。宣言外変更の検知はスラッシュ設計 段 9b の git 差分観測が oracle 側で解く |
| 失敗分類表の first-class 化（6 の残り） | 不採用 | 既存分類（env/transient/quota + check 再注入 + tier 判断）で経路は足りている。P2 だけ切り出す。ペイン経路の分類欠落はスラッシュ設計 段 7 の管轄 |
| MagenticLite 型の専用小型モデル | 不採用 | fine-tune を閉じた前提（改善案 §5）と矛盾。既製 Gemma への転用効果の証拠もない |
| SmallCTL / Argus の構成模倣 | 不採用 | Argus は数値の裏付け不能。SmallCTL は定量なし。参考にするならスラッシュ設計 §8.1 の評価表（依存・プロンプト規模・コア規模で裁き、規約だけ借りる）へ載せて同じ基準で判定する |

---

## 5. 実行順序と採択済み提案への位置づけ

| 段 | 内容 | 依存 |
|---:|---|---|
| 即時 | **P1**（ラウンド上限の評価 arm） | なし。宣言キー 1 つ + eval 対照 1 本。単独で出荷・巻き戻し可 |
| 即時 | **P2**（失敗テスト選別注入、`_sm_check_note` 内 + fallback） | なし。スラッシュ設計 段 13 が入ったら宣言側へ移送 |
| 案 B 後 | **P3 の前提測定**（read toolset の探索ラウンド消費を eval ログで確定） | 改善案 案 B |
| 測定後 | **P3 本体**（`/find` / `retrieve` の裏を決定的 rg + 抜粋供給へ） | 上記 + （あれば）スラッシュ設計 段 1〜4 |

P1・P2 は改善案の実行順序（案 A → B → C …）ともスラッシュ設計の段 0〜14 とも独立で、
どちらの進行も待たない。P3 だけが案 B の後段であり、これは改善案自身の順序
（「先に配線が壊れていると、速い誤割り当てが増えるだけ」）に従った結果である。

---

## 6. まとめ

計画メモの構図（決定的ハーネス + 局所判断関数としての Gemma）は agent-tools が既に採っている
構図であり、その中心線「モデルの裁量を削る」も、改善案（割り当てと材料の決定化）と
スラッシュ設計（tool 選択の 1 語化・未知コマンドの fail fast）が既に別ルートで進めている。
三者が独立に同じ方向へ収束していること自体は方向の傍証になるが、計画メモ固有の寄与として
残るのは P1〜P3 の 3 点——なかでも採択済み提案のどちらにも無いのは P1（ターン上限）と
P2（失敗選別注入）の 2 点——に絞られる。逆に、計画メモで最も目を引く FSM 図と
schema 全面固定は、計画メモ自身の引用元の実測が支持していない。

### 参照

- [Better Harnesses, Smaller Models (arXiv:2607.08938)](https://arxiv.org/abs/2607.08938)
- [state-harness (GitHub)](https://github.com/vishal-dehurdle/state-harness)
- [MagenticLite / MagenticBrain (Microsoft Research)](https://www.microsoft.com/en-us/research/blog/magenticlite-magenticbrain-fara1-5-an-agentic-experience-optimized-for-small-models/)
- [Let Me Speak Freely? (arXiv:2408.02442)](https://arxiv.org/abs/2408.02442)
- [SmallCTL 紹介動画](https://www.youtube.com/watch?v=tY96h6bokdk)
- [2026-08-27 効果的改善案](../plans/2026-08-27-agent-tools-local-llm-effective-improvement-proposals.md)
- [2026-08-27 スラッシュ再構成設計](../plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md)
