# Gemma4 ハーネス改善案（2026-08-28 メモ）の agent-tools 適用性レビュー

> 作成 2026-08-27
> 対象: [docs/plans/2026-08-28-gemma4-harness-improvement.md](../plans/2026-08-28-gemma4-harness-improvement.md)（外部チャット由来の意見メモ。以下「計画メモ」）
> 位置づけ: 計画メモは一意見であるため、(1) 引用されている研究・OSS の実在と数値を裏付け、
> (2) agent-tools の現行実装と突合し、(3) 批判的にレビューした上で、採用・保留・不採用を切り分ける。
> 前提は [2026-08-27 効果的改善案](../plans/2026-08-27-agent-tools-local-llm-effective-improvement-proposals.md) と同じ
> （CPU only / RAM 〜32 GB / ollama 中心 / worker は `aider` + `gemma4:e4b`）。

---

## 0. 結論の先出し

- **計画メモの「7 つの補助輪」のうち、agent-tools に未実装なのは実質 3 つ半。**
  2（one-step 実行）と 4（外部 verifier）は実装済み。1・3・5・6・7 は部分実装で、
  計画メモが暗黙に置く「ゼロから作る」前提は現行コードと合わない。
- **裏付けが最も強い施策は、最も地味な「ターン上限の引き締め」である。**
  原典（state-harness）の実測は「naive なターン上限だけで Gemma4:E4B が +35pp」。
  しかも同じ原典は「凝ったハーネス（state-harness 本体）は naive 上限と有意差なし」と報告している。
  つまり計画メモの複雑な FSM 図を作り込む根拠は、計画メモ自身の引用元からは出ない。
- **引用の裏付け結果はまだら。** 「Better Harnesses, Smaller Models」「state-harness」
  「MagenticLite」「SmallCTL」は実在確認。**Argus の「E4B 83% vs Qwen2.5-3B 58%」は確認できず**、
  採用判断の根拠にしない。
- **採用に値するのは 3 点**: (P1) write 系ステートのラウンド上限を E4B 向けに 2〜3 へ絞る評価 arm、
  (P2) check 失敗時の「失敗テストのみ選別注入」、(P3) ハーネス側検索（ただし
  [2026-08-27 案 B](../plans/2026-08-27-agent-tools-local-llm-effective-improvement-proposals.md)
  のスライス本番配線が先）。残りは実装済み・裏付け不足・既存方針（非目標）との衝突のいずれかで保留か不採用。

---

## 1. 引用の裏付け結果

| 計画メモの引用 | 実在 | 裏付けの中身と割引 |
|---|---|---|
| Better Harnesses, Smaller Models（2026-07） | ✅ [arXiv:2607.08938](https://arxiv.org/abs/2607.08938)（CMU） | 16/21 タスク×SLM 組で改善、最良 89.7% を 4% コストで回収 — 数値は原典どおり。ただし**タスクの反復性と効果の相関 ρ=−0.96**: 反復業務でのみ効き、新規性の高い問題には効かないと明記。open-ended coding への一般化は論文の主張外 |
| state-harness の実験（E4B 35%→70%） | ✅ [GitHub: vishal-dehurdle/state-harness](https://github.com/vishal-dehurdle/state-harness) | naive turn cap 単体で E4B +35pp、4 モデル平均 +17.5pp — 計画メモの記述とほぼ整合。**計画メモが落としている重要点**: (a) state-harness 本体は naive cap と有意差なし（価値は診断のみ）、(b) 自作 20 タスク + SWE-bench 37 件の小規模で、著者自身が再現要と明記、(c) 単発試行の 8% 未満の差は非決定性フロア内。推奨「open-ended coding は 2〜3 ターン」はここから |
| Argus（E4B tool-chain 83% vs Qwen2.5-3B 58%） | ❌ 確認できず | 該当するリポジトリ・数値とも検索で発見できず（同名の無関係プロジェクトのみ）。**この数値を根拠に使わない** |
| SmallCTL | △ 実在（[紹介動画 2026-03](https://www.youtube.com/watch?v=tY96h6bokdk)） | SLM 向け端末ハーネスとして実在するが、定量評価は見つからず。設計の参考止まり |
| MagenticLite / MagenticBrain | ✅ [Microsoft Research（2026-05）](https://www.microsoft.com/en-us/research/blog/magenticlite-magenticbrain-fara1-5-an-agentic-experience-optimized-for-small-models/) | 実在。ただし**専用学習済みモデル（MagenticBrain / Fara1.5）前提**で、既製 Gemma への転用効果は示されていない。fine-tune を閉じている本リポジトリの前提とは交わらない |
| 「SLM の tool use には schema 制約が特に有効という survey」 | △ 両論あり | 有効説の一方で、[Tam et al. "Let Me Speak Freely?"](https://arxiv.org/abs/2408.02442) は **format 制約が生成系タスクの推論を 10〜30% 劣化させる**（分類系は劣化しない）と報告。推奨される緩和は「思考は自由形式 → 出力のみ制約」の 2 段。agent-tools が `format` 指定時に `think: false` を強制している実測（`ollama_loop.py:259-272`）はこの問題と同族であり、無条件の schema 固定を支持しない |

---

## 2. 「7 つの補助輪」× agent-tools 現行実装の突合

対象コードは `tools/agent-tools/agentcore/`。

| # | 計画メモの提案 | 現状 | 主な根拠 |
|---|---|---|---|
| 1 | FSM（PLAN→ACT→VERIFY→RECOVER） | **部分実装** | YAML 駆動 FSM は完備。遷移判定はハーネスが測った値のみで、モデル文は 1 バイトも混ぜない（`harness/statemachine.py:595-607`、`:671-703`）。欠けるのは正準 4 相テンプレと RECOVER 状態（既定は escalate = exit 3）。ステート内は 4 動詞 × 最大 8 周の小ループが残る |
| 2 | one-step 実行 | **実装済み** | 「exactly one state-machine action / Do not work on later states」（`statemachine.py:207-209`）、1 ターン 1 コマンド（`ollama_loop.py:669-671`）、skill 側の著述規約（`statemachine-use/SKILL.md:41-49`） |
| 3 | schema 制約出力 | **部分実装** | 推論層の文法制約は導入済み（ollama `format`、プロンプト費ゼロ設計。`ollama_loop.py:241-283`）。tool 契約自体は文章教示 + 事後の寛容パース（`harness/toolloop.py:291-368`）。`expected_result` は不在。GBNF は [2026-08-27 案 G](../plans/2026-08-27-agent-tools-local-llm-effective-improvement-proposals.md) の評価 arm |
| 4 | 外部 verifier | **実装済み** | check の exit code を oracle にし、シェルなし argv 実行（`statemachine.py:573-633`）。SHA-256 による変更証跡ゲート（`toolloop.py:740-753`、`:894-915`）。receipt は自己申告 verdict を無視して再導出（`verifycontract.py:308-366`）。judge は prose 基準限定・fail-closed の第 2 層のみ |
| 5 | evidence-based context | **部分実装** | AST 決定的スライス `context_slice.py` は agent-flow へ配線済み（`agent_flow/context.py:78-124`、fallback 領収書つき）。読み材料の事前割付もある。**ハーネス側 ripgrep→チャンク供給は不在** — 検索は今もモデルが `read` toolset で行う |
| 6 | 失敗分類 + 回復レシピ | **部分実装** | env/transient/quota の分類が再試行経路を駆動（`agentcli.py:764-783`）。check 失敗時は実出力 2000 字を再注入（`statemachine.py:622-633`、盲目再試行より 28% 速い実測コメントあり）。**失敗テストのみの選別注入・作業成果物側の失敗分類表は不在** |
| 7 | hard stopping → 強制 REPLAN | **部分実装** | 同一動作・無変更・verifier 枯渇・context 枯渇の停止は全て存在（`statemachine.py:471-482`/`:825-842`、`ollama_loop.py:876-884`、`ollama_context.py:230-239`）。ただし終端は escalate/停止/著者定義 continue であり、**REPLAN へ戻す経路はない** |

計画メモ末尾の「Gemma をエージェントにしない」アーキ図（state machine + tools + verifier +
局所判断関数としての Gemma）は、agent-tools の現行設計とほぼ同型である。新奇なのは構図ではなく
「どこを締めるか」の差分だけで、以下 3 節はその差分に絞る。

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
   (b) tool schema の固定 prefill 費を避けるという ollama 層の設計判断（`ollama_loop.py:16-22`）、
   の両方と衝突する。制御席（planner/judge 等）は既に JSON 制約 variant へ振替済みであり、
   残るのは worker の tool 契約だけ。ここは GBNF arm の実測（案 G）で決めるべきで、計画メモの
   記述だけで採用しない。
4. **`expected_result` は検証の重複。** 検証は既に外部 oracle（check / SHA-256 / receipt 再導出）で
   行っており、モデル自己申告の期待値を足しても oracle にはならない。トークン費だけ増える。
5. **反復性の限定を引き継ぐ必要がある。** Better Harnesses の ρ=−0.96 は、agent-tools の
   得意領域（extract/classify/split 等の定型役、6/6 実測）には合うが、open-ended な実装ステートへ
   同じ期待を持ち込む根拠にはならない。
6. **計画メモは本リポジトリの実測資産を参照していない。** 例えば「evidence-based context」は
   context_slice として実装・一部配線済み（tokens −72〜87% の実測つき）。適用の焦点は
   「新設」ではなく「配線完了（案 B）と検索の決定化」に置くべき。

---

## 4. 適用提案（効く順）

### P1 ★★★ write 系ステートのラウンド上限を E4B 向けに引き締める（評価 arm）

**何をするか。** 現行の `_TL_MAX_TOOL_ROUNDS = 8`（`toolloop.py:50`）/ `DEFAULT_MAX_ROUNDS = 12`
（`ollama_loop.py:60`）に対し、`write:` を持つステート限定でラウンド上限 2〜3 の設定を
ステート定義（または profile）から渡せるようにし、eval archive で受入率・壁時計を現行と比較する。

**なぜ。** 唯一「+35pp」級の実測裏付けがあり、実装は設定値の配管だけで機構を増やさない。
既に `write:` 宣言ステートは制御ラウンドを省略しており（`statemachine.py:330-347`）、
同じ「訊く回数を減らす」系譜の延長。**注意**: 原典は小規模のため、本番既定値の変更は
自前 eval が再現してから。上限超過の既定は現行どおり escalate（REPLAN 新設はしない — §3-1）。

### P2 ★★ check 失敗時の「失敗テストのみ」選別注入

**何をするか。** `_sm_check_note()`（`statemachine.py:622-633`）は現在、check 出力の末尾 2000 字を
無選別に再注入している。check が pytest 系のとき、失敗テストのブロックだけを決定的に抽出して
注入する（抽出不能時は現行の末尾切り詰めへ fallback）。

**なぜ。** 計画メモ 6 の中で唯一、既存実装（再注入で 28% 高速化の実測）の素直な延長であり、
E4B の長文弱さ（MRCR）にも合う。失敗分類表の first-class 化はやらない — 分類が要るのは
env/transient/quota（実装済み）と「直るか直らないか」（tier 判断、実装済み）で足りている。

### P3 ★★ ハーネス側検索（ripgrep → 抜粋供給）— ただし案 B の後

**何をするか。** 現在モデルが `read` toolset で自走している探索（`rg` はモデルが打つ許可コマンド、
`ollama_loop.py:96`）を、ハーネス側で「rg → ヒット周辺チャンク → 読み材料へ事前割付」に置換する
opt-in 経路を足す。context_slice と同じ規律（決定的・失敗時は fallback・省略を明示）で。

**なぜ。** 計画メモ 5 の未実装半分であり、「モデルの裁量を狭める」方向は非目標
（自由 tool-loop の無条件解放をしない）と整合する。**順序**: 先に
[案 B](../plans/2026-08-27-agent-tools-local-llm-effective-improvement-proposals.md)（read 専用
チャネルの導入とスライス本番配線）を終えないと、供給した抜粋が編集可能ファイルとして aider に
渡る配線バグの上に積むことになる。着手判断の前に、read toolset の探索が実際にラウンドを
何周消費しているかを eval ログで測る。

### 保留・不採用

| 項目 | 判定 | 理由 |
|---|---|---|
| PLAN→ACT→VERIFY→RECOVER 正準テンプレ | 保留 | 既存 YAML FSM で表現可能（`gated_implement.yaml` に PLAN/repair 状態を足すだけ）。構造追加が効く実測はなく、原典は逆を示す（§3-1）。欲しくなったら例 YAML の追加で足り、コード変更ではない |
| 強制 REPLAN 経路（7 の後半） | 保留 | 停止条件は完備。escalate で上位 tier に渡す現行設計は `schema.md:105-107` の実測に裏付けられており、E4B に再計画させる方が良い証拠はない |
| tool 契約の JSON Schema / GBNF 固定（3） | 保留 | 案 G の評価 arm のまま。推論劣化（§1）と prefill 費（`ollama_loop.py:16-22`）の両論を実測で裁く |
| `expected_result` フィールド | 不採用 | 外部 oracle と重複し oracle 性がない（§3-4） |
| 失敗分類表の first-class 化（6 の残り） | 不採用 | 既存分類（env/transient/quota + check 再注入 + tier 判断）で経路は足りている。P2 だけ切り出す |
| MagenticLite 型の専用小型モデル | 不採用 | fine-tune を閉じた前提（[2026-08-27 効果的改善案](../plans/2026-08-27-agent-tools-local-llm-effective-improvement-proposals.md) §非目標）と矛盾。既製 Gemma への転用効果の証拠もない |
| SmallCTL / Argus の構成模倣 | 不採用 | Argus は数値の裏付け不能。SmallCTL は定量なし。読む価値はあるが採用根拠にはならない |

---

## 5. まとめ

計画メモの構図（決定的ハーネス + 局所判断関数としての Gemma）は agent-tools が既に採っている
構図であり、「適用できる案」として残るのは P1〜P3 の 3 点に絞られる。いずれも共通するのは
「機構を足す」のではなく「モデルに委ねる範囲をさらに削る」ことであり、これは計画メモの
中心主張とも、本リポジトリの実測に基づく既存方針とも一致する。逆に、計画メモで最も
目を引く FSM 図と schema 全面固定は、計画メモ自身の引用元の実測が支持していない。

### 参照

- [Better Harnesses, Smaller Models (arXiv:2607.08938)](https://arxiv.org/abs/2607.08938)
- [state-harness (GitHub)](https://github.com/vishal-dehurdle/state-harness)
- [MagenticLite / MagenticBrain (Microsoft Research)](https://www.microsoft.com/en-us/research/blog/magenticlite-magenticbrain-fara1-5-an-agentic-experience-optimized-for-small-models/)
- [Let Me Speak Freely? (arXiv:2408.02442)](https://arxiv.org/abs/2408.02442)
- [SmallCTL 紹介動画](https://www.youtube.com/watch?v=tY96h6bokdk)
