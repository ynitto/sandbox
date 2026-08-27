# agent-tools ファミリーでローカル LLM を効かせる — 効果的な改善案（2026-08-27）

> 作成 2026-08-27
> 前提: CPU only / RAM 〜32 GB / 推論は ollama 中心 / コード worker は `aider` + `gemma4:e4b`。
> 位置づけ: [ローカル主体運転](2026-08-14-agent-tools-local-first-operation-plan.md)、
> [追加活用評価](2026-08-22-local-llm-further-utilization-and-runtime-tuning-assessment.md)、
> [dashboard 設定提案](2026-08-23-agent-dashboard-local-llm-configuration-proposal.md)、
> [おすすめ構成の単純化](2026-08-26-agent-tools-recommended-setup-simplification-design.md)
> までの実測・設計を踏まえ、**いま着手すると効く改善**だけを優先度つきで再提案する。
> 既に測って不採用・実装済みの打ち手は重複させない。

---

## 0. 結論の先出し

- **ローカル LLM の天井はモデルサイズではなく、仕事の形と配線である。**
  E4B は「短い・局所的・構造化」で合格済み。効かないのは長い・横断的・メタ認知の仕事と、
  その仕事をローカルへ落とす設定経路の交錯である。
- **最も効く次の一手は「新しい推論レバー」ではなく 3 本。**
  (1) **おすすめ構成 1 操作適用**（設定→エンジンの 3 経路を人が暗記しなくてよいようにする）、
  (2) **コンテキスト・スライスの本番配線**（実測で tokens −72〜87%・壁時計 −83%・受入同等）、
  (3) **未測定呼び出し面の埋めと決定化の拡大**（割り当て先の大半がまだ能力未知）。
- **モデルを強くする方向は後回し。** fine-tune・12b コード worker・OpenAI 互換全面載せ替え・
  best-of-N 再抽選は、いずれも実測または設計判断で閉じている／効果が薄い。
- **ランタイム実験（iGPU / GBNF）は独立 arm のまま測る価値はあるが、本番乗り換え前提にしない。**

---

## 1. 判断の土台（動かさない事実）

| # | 事実 | 含意 |
|---|---|---|
| 1 | E4B は短局所構造化で天井、長横断メタ認知で崩れる | 仕事を割る・ゲートする・決定化するのが正解 |
| 2 | 品質の守りはゲート（決定的検査 + 再投入） | プロンプト上積みや多数決 judge に賭けない |
| 3 | 多基準 filter / judge はプロンプト全滅 → 決定化で 3/3 | 同型の失敗は機構側へ寄せる |
| 4 | 12b は検証役のみ。コード worker は暴走・停止性で不成立 | tier 候補に 12b を置かない |
| 5 | 律速は decode ではなく再試行・ラウンド・prefill | 回転数は「1 呼び出し短縮」と「無駄な再 prefill 削減」 |
| 6 | `num_ctx` 拡大は E4B のレバーにならない（MRCR 弱） | 入れるより割る・抜粋する |
| 7 | best-of-N は (a) escalate 0 済み、(b) 効かず壁時計 3 倍 → 不採用 | 再抽選より分解・適格条件 |
| 8 | コンテキスト・スライスは受入同等で経済大勝 | 本番配線が未着手の最大の実測資産 |
| 9 | 設定経路が 3 本交錯し、画面と実走モデルが食い違う | 運用摩擦がローカル活用の実効を削る |
| 10 | `coverage.json` に missing が多数残る | 「ローカル主体」宣言の根拠が面ごとに欠けている |

---

## 2. 優先改善案（効く順）

### 案 A ★★★ おすすめ構成の 1 操作適用（運用摩擦の除去）

**何をするか。** eval archive から決定的に生成する `recommendation.json` を読み取り専用資産とし、
dashboard に「herd / ローカル主体」を選んで適用する口を 1 つだけ足す。
人が打つのはクラウド CLI の選択程度にし、`gemma4:e4b` / `12b` / `aider` / `ollama` の組み合わせは
実測が決める（[2026-08-26 設計](2026-08-26-agent-tools-recommended-setup-simplification-design.md)）。

**なぜ最優先か。** 能力は既に測れているのに、**正しい割り当てが端末へ届かない**と
ローカル主体は机上の計画で終わる。手順 8 のうち 6 は定数なのに人手で打ち込ませている。
機構を増やさず「根拠面の資産 → 制御面の適用」だけなので、設計の不変条件（dashboard を
根拠 writer にしない）も守れる。

**同時に直す配線。**

- 変種ガード / profile 名と canonical `agent_cli` の照合ずれ（12b の worker 流出封じが死ぬ系統）。
- `selection_policy` が `operation_class` を捨てて workload 単位 rank だけにする問題
  （e4b が extract 合格でも review blocked なのにレビュー候補 1 位になりうる）。
- 解決は用途軸 GUI を増やすのではなく、**tier から「どのモデルか」を取り上げ、
  purpose → variant / by_purpose へ寄せる**（同設計 §3.5）。

**完了条件。** 新品端末で「herd 適用」1 操作後、doctor / extract / 適格 work がクラウド 0 で回り、
verify だけ 12b 変種へ、12b がコード worker に流れないことが claim / result で追える。

---

### 案 B ★★★ コンテキスト・スライスの本番配線（壁時計の回収）

**何をするか。** 既に実装・実測済みの `agentcore.context_slice` を、
agent-flow の「読むだけの材料」経路へオプトイン配線する。

**前提の配線修正（セットで必須）。** 現状 `read_allocation` の path が
`headless_cmd` の `files=`（aider では `--file`＝編集可能）に落ち、**読む材料と編集対象の区別が消える**。
先に `read_files=`（または同等）を導入し、スライスは読み取り材料にだけ適用する。

**運用形。**

- 設定キー 1 つ・既定 off。
- 対象シンボルは編集対象の import から決定的導出（LLM なし）。
- 切れなければ原本へ倒し、倒れた事実と kept/total を claim / result に残す。
- 材料が小さい課題は対象外（T1 級で差が出ないのは既知）。

**採用根拠は経済だけ。** tokens_in −72〜87%・壁時計 −83%・受入同等。見落とし縮小は未証明なので根拠にしない。

**完了条件。** オプトイン ON の適格ノードで tokens_in / 壁時計が台帳上有意に下がり、T2 対照に退行がない。

---

### 案 C ★★ 未測定面の埋め + 「候補生成→決定的検算」の拡大

**何をするか。** ローカルへ降ろす前に、面ごとに最小セルで能力を確定する。既に効いた型を再利用する。

| 優先面 | 予想（既存軸） | ローカル方針の当たり |
|---|---|---|
| project の決定的 verify / doctor 以外の dashboard 面 | 材料が全部ある「読んで指す」は e4b 向き | doctor 同様に委譲候補 |
| flow: extract / map / retrieve / classify | 短構造化なら e4b 天井帯 | 単発 JSON で委譲 |
| amigos の定型ロール | メタ認知・討論は弱帯 | クラウド or 決定化。ローカルは下書きのみ |
| project 自然文 verify | 既にローカル不成立 | 決定的コマンドだけローカル圏のまま |

**併せて拡大する機構。** `candidate_eval` でパス・テスト名は e4b 3/3。regex は読み違いで 0/3。
本番へは「選ぶ」候補（read_allocation のパス・verify のテスト名）から入れ、誤りは機械が落とす。
多基準判定の残りは P4 型（候補生成 + 決定的検算）へ寄せ、LLM に同時充足させない。

**完了条件。** `coverage.json` の missing が「意図的にクラウド固定」か「local qualified / blocked」に分かれ、
missing のまま自動選択に載らない。

---

### 案 D ★★ 同役割の直列バッチ化（prefill 再利用）

**何をするか。** `OLLAMA_NUM_PARALLEL=1` 前提で、独立 step / fan-out の消化順を**同役割で束ねる**。
system prefix（reliability policy・役割骨格）の接頭辞キャッシュ命中を上げ、再 prefill を減らす。

**なぜ安いか。** モデル・プロンプト・契約に触らない。品質再測が不要で、TTFT 分布の before/after だけで採否が決まる。

**境界。** 依存のある順序は変えない。現行は入力長が TTFT にほぼ効かない規模もあるため、
**効果が小さい可能性は織り込み済み**——測って駄目なら捨てる。

**完了条件。** 同 workload の TTFT p50 が有意に下がる、または「差なし」で閉じる記録が残る。

---

### 案 E ★ 天井役割への小型サブモデル（e2b 級）

**何をするか。** 抽出・分類・split など **e4b で既に 6/6 の役割**だけ e2b 級 arm を引き、
同点なら小さい方を使う。コード worker・レビュー・planner には使わない。

**なぜ後段か。** 案 A/B/C が「正しい仕事を正しい経路へ」を直したあとの**回転数チューニング**だから。
先に配線が壊れていると、速い誤割り当てが増えるだけになる。

**完了条件。** 対象役割で受入退行なし + 壁時計短縮。1 点でも退行した役割には載せない。

---

### 案 F ★ 台帳 few-shot（定型 worker のみ）

**何をするか。** 合格台帳から task 種別ごと合格実例 1 本を決定的に前置きする（動的検索は後）。

**境界。** filter / judge 判断領域には入れない（プロンプトレバー全滅済み）。
形式・手順の模倣が効く定型 worker に限定。受入が動かなければ即取り下げ（prefill 増のコストがある）。

---

### 案 G ○ 独立評価 arm（本番乗り換えではない）

| arm | 狙い | 採用ゲート |
|---|---|---|
| koboldcpp / llama.cpp Vulkan で iGPU prefill | 案 B（2026-08-06）の最安検証。効けば桁改善の可能性 | ollama 基準線と prefill 内訳を並べる。退行なし。stall / usage 契約を満たしてから移行検討 |
| GBNF 文法制約 | SEARCH/REPLACE・array 契約の形式故障を文法で消す | 現行 `format` + 振替で十分なら閉じる |
| MoE 26B RAM probe（32 GB 機） | 上下どちらに振るかの材料。16 GB は重みだけで不成立済み | `moe_ram_probe.py` を対象機で実行するだけ |

全面乗り換え・fine-tune・投機的デコード優先は、いまの律速（受入・再試行）に対して費用対効果が合わない。

---

## 3. 役割別の「ローカルで持つ / 持たない」再確認

| 役割 | ローカル方針 | 根拠の要約 |
|---|---|---|
| 適格な局所コード修正 | aider + e4b + ゲート | T2 / T1gate |
| 抽出・分析・構造化・単基準 filter・reduce・evaluator・doctor | e4b 単発 JSON | 天井 or 12/12 |
| split | e4b + 決定的 `gate_tasks` | 4/6 + 検査 |
| テキスト review / verify | 12b 変種（stall + 縮退） | text-eval。worker 禁止 |
| 多基準 filter / judge | 決定化。残りはクラウド | E6 |
| flow planner | クラウド維持（部分的にしか通らない） | planner_eval |
| project 自然文 verify | ローカル不成立 | project_verify_eval |
| 長い agent loop / 複数成果物横断 | 割る・ゲート・escalate。edit 道具拡張は据え置き | Tau2 / T3 / expansion 設計 |

---

## 4. 実行順序（短い）

| 段 | 内容 | 依存 |
|---:|---|---|
| 1 | **案 A**（recommendation + 配線バグ封じ + purpose 次元の運搬） | なし。運用の前提 |
| 2 | **案 B**（`read_files` 区別 + context_slice オプトイン） | 案 A と独立可。並行可 |
| 3 | **案 C**（missing 面の最小セル + 候補生成の本番投入） | 案 A の割り当てが正しいほど効く |
| 4 | **案 D**（同役割バッチ）→ 効かなければ閉じる | 品質非依存 |
| 5 | **案 E / F**（e2b・few-shot） | 天井役割が確定してから |
| 併行 | **案 G**（iGPU / GBNF / MoE probe） | 実機があるときだけ。本番経路に入れない |

---

## 5. やらないこと（再掲）

- ローカル fine-tuning（QLoRA / 蒸留）— CPU/32GB と正面衝突。ゲートは anyway 必要。
- `num_ctx` 拡大で長文を解かせる — E4B の検索弱点を拡大するだけ。
- gemma4:12b をコード worker / tier 候補へ戻す。
- OpenAI 互換エンドポイント経由で既存 CLI へ載せる全面移行 — `think` / `format` / `num_ctx` / `keep_alive` を失う。
- best-of-N 再抽選の運用既定化 — 実測で不採用。
- 多基準判定へのプロンプト再投入。
- 自由 tool-loop / edit 道具の無条件解放 — 受入率が測れてから。
- 基準線なしの koboldcpp / llama-server 本番差し替え。

---

## 6. 成功の見え方

ローカル活用が「効いている」とは、次が同時に成り立つ状態である。

1. **定型の高消費役割がクラウド 0 で回り続ける**（extract / doctor / 適格 work）。
2. **画面・control・実走モデルが一致**し、12b が worker に流れない。
3. **大きな read 材料つきの局所修正で壁時計が桁で下がる**（スライス ON）。
4. **自動選択に載る面はすべて qualified / blocked / cloud-fixed のどれか**で、missing のまま流れない。
5. **人の初期設定が「herd を選ぶ」程度**に縮んでいる。

---

## 7. 参照

- [2026-08-14 ローカル主体運転計画](2026-08-14-agent-tools-local-first-operation-plan.md)
- [2026-08-22 追加活用とランタイム評価](2026-08-22-local-llm-further-utilization-and-runtime-tuning-assessment.md)
- [2026-08-23 dashboard ローカル LLM 設定提案](2026-08-23-agent-dashboard-local-llm-configuration-proposal.md)
- [2026-08-26 おすすめ構成の単純化](2026-08-26-agent-tools-recommended-setup-simplification-design.md)
- [2026-08-25 agent-herd 統一入口](2026-08-25-agent-herd-unified-entry-design.md)
- [agent-tools コンセプト正典](../designs/agent-tools-concept.md)
- `tools/agent-tools/eval/coverage.json` / `agentcore/context_slice.py` / `agents/*.json`
