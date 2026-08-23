# agent-dashboard からの各 agent-tools ローカル LLM 設定提案 — 実測済み割り当てを管理面の宣言へ落とす

> 作成 2026-08-23
> 前提: CPU only / RAM 32 GB / 推論サーバは ollama（`OLLAMA_NUM_PARALLEL=1`）。
> 位置づけ: [2026-08-14 ローカル主体運転計画](2026-08-14-agent-tools-local-first-operation-plan.md)の
> 役割×モデル割り当て（§2）と、[2026-08-15 候補ベース実行方針設計](2026-08-15-agent-tools-candidate-execution-policy-dashboard-design.md)の
> 制御面（dashboard）/ 根拠面（agent-audit）/ 実行面（各エンジン）の分担、
> [2026-08-22 追加活用の評価](2026-08-22-local-llm-further-utilization-and-runtime-tuning-assessment.md)
> §4.3 までの消化（B1 の planner / project:verify / doctor 実測、B3 の 12b 縮退配線）を、
> **いま dashboard から宣言できる設定**として 1 枚に落とす。新しい機構は提案しない。

## 0. 結論の先出し

- **dashboard から設定するのは 4 点だけである。** (1) 実行レベルの構成（tiers の候補表 =
  `profiles.json`）、(2) 実行方針プリセット（おまかせ）、(3) 候補適格性
  （`qualifications.json` — agent-audit が書き、これが**無いと selection_policy が
  コンパイルされず候補ベースにならない**）、(4) 実行制御（`workloads.flow.concurrency=1`
  とクラウド枠の温存）。役割×モデルの細かい対応表を dashboard で作る必要はないし、
  作る口も無い（設計どおり——通常 UI に処理種別×候補のマトリクスは持ち込まない）。
- **役割ごとのモデルの実体は変種（variants）の既定が持つ。** verify → `ollama-verify`
  （gemma4:12b・stall-timeout・縮退基準つき）、aider 経路の split → `ollama-list-thinking`
  （gemma4:e4b・temperature 0）。tier 候補には base 定義だけを置けばよく、12b を tier
  候補に**入れない**（コード worker への流出を構成で塞ぐ）。
- **ただし配線上の食い違いが 1 つある。** JSON 系変種（`ollama-json` / `ollama-list` /
  `ollama-read`）と base `ollama` の `default_model` が **`qwen3` のまま**である。
  `agent_flow.agent._agent_for` は「自動選択層のモデルを変種既定で上書きする」ため、
  tier 候補に `ollama / gemma4:e4b` を置いても、**判定系役割は qwen3 で走る**。
  実測（e4b 6/6 等）と本番のモデルが食い違うので、これだけは agents/ 定義の更新
  （または各ツール設定での役割別モデル明示）が要る（§4.1）。本提案で唯一のコード変更である。
- **B1 の実測で dashboard 自身（doctor 4 モード）が初の「ローカル全面委譲」面になった**
  （e4b 12/12）。逆に project の局所 verify 変種は不成立が確定しており、ローカルへ降ろさない。

## 1. 前提 — 実測で確定している役割×モデル（再掲 + B1 で増えた分）

| 役割 | 割り当て | 根拠 |
|---|---|---|
| コード worker（適格な局所修正） | `aider / gemma4:e4b` + 決定的ゲート + 再投入 → 上限到達で昇格 | T2/T4 9/9・T1gate 3/3（gate-generality） |
| 抽出・分析・構造化要約・分類・単基準 filter | `ollama / gemma4:e4b` 単発 JSON | text-eval 6/6・F1 3/3 |
| split | e4b（aider 経路は `ollama-list-thinking`） | 4/6 + `gate_tasks` 決定検査 |
| reduce・evaluator | e4b 単発 JSON | 6/6・5/6 |
| テキストのレビュー・検証 | `ollama / gemma4:12b`（`ollama-verify` 変種） | text-eval 6/6。暴走 2/27 → 再投入 1 回 + e4b 縮退配線済み（B3） |
| 多基準 filter / judge | 決定化パイプ（P4・実装済み）。残る裁定だけクラウド | E6: F2P/J1P 3/3 |
| planner（flow） | **クラウド維持**。e4b は鎖 2/3・fan-out 3/3・列挙 1/3・単一 0/3 で部分的 | B1 planner_eval |
| project の verify（自然文 criteria） | **ローカル不成立**（12b は散文 JSON・e4b は捏造 pass）。決定的コマンドだけローカル圏 | B1 project_verify_eval |
| dashboard doctor（4 モード） | `gemma4:e4b` で足りる（12/12） | B1 doctor_eval |
| コード worker としての 12b | 禁止（tier 候補に入れない） | wall 600/1800 とも 0 完走 |
| 記憶検索 | bge-m3（ltm-use v5.5.0 実装済み・生成モデルと独立） | B4 |

## 2. dashboard で宣言する 4 点

### 2.1 実行レベルの構成（全体設定 → エージェント / 実行レベル）

`profiles.json` の tiers に置く候補は次の 4 段。**候補は base 定義だけ**
（変種先は tier 候補に指定できない——dashboard が保存時に拒否する仕様）。

| tier（UI 表示） | 候補（宣言順 = 同順位時の優先順） | 何がここで走るか |
|---|---|---|
| basic（単純作業） | `ollama / gemma4:e4b` | classify / filter / map / extract（JSON 一動作。`KIND_MIN_TIER` の basic 圏） |
| small（軽量） | `aider / gemma4:e4b`、`ollama / gemma4:e4b` | work / generate（aider）、reduce / retrieve / verify（ollama。verify は変種で 12b に化ける） |
| medium（標準） | 利用可能なクラウド CLI（例: `claude / sonnet` 級） | planner / judge / split / synthesize と昇格受け |
| large（高性能） | クラウド上位（例: `claude / opus` 級） | 品質優先時・明示固定時のみ |

- **12b をどの tier にも書かない。** verify 役は `ollama` → `ollama-verify` 変種の
  `default_model`（gemma4:12b）で自動的に 12b になり、コード worker へは流れない。
  縮退（2 回連続 transient → e4b）も B3 で配線済みなので、候補順をいじる必要はない。
- flow は kind ごとの下限 tier カタログ（`flow-tiers.js`）を持つので、この 4 段を
  埋めるだけで「basic 圏の kind は e4b 単発、形を決める kind はクラウド」の割り当てが
  plan 生成時に機械的に決まる。役割別の手当てはしない。

### 2.2 実行方針（全体設定 → 実行制御）

**「おまかせ（推奨）」のままにする**（balanced・通常=標準・残 20% で軽量へ）。

「節約」にしない理由: 節約は常時 small 上限なので、**昇格受け（medium のクラウド候補）が
tier 検査で常に落ち、(b) 族の課題が park し続ける**。ローカル主体の実体は方針プリセット
ではなく §2.1 の tiers 構成が作る——basic / small が全部ローカルなら、**適格な定型は平常
クラウド 0 で回り（E7 で確認済み）、クラウドは planner と昇格だけに残る**。これは
local-first 計画 §2 の「クラウドに残るのは 3 つ」をプリセット変更なしで表現した形である。
クラウド消費をさらに固く縛りたい場合は、プリセットを変えるのではなく §2.4 の
機能別上限（node-budget）で縛る。

### 2.3 候補適格性（qualifications.json）— 無いと候補ベースにならない

`profiles.apply()` は **`qualifications.json` が存在するときだけ** `selection_policy` を
control へコンパイルする。無い端末では legacy の単一候補 fallback（workload 直下の
agent_cli / model）だけで動き、処理契約による適格判定・fallback 候補・park が全部消える。
設定手順に必ず入れる:

1. eval archive を初期適格性へ seed する（候補ベース実行計画の U1。実測の出典つき）。
2. 以後は `agent-audit qualify --apply` を収集サイクルへ入れ、本番 receipt で昇格・降格・
   期限切れを回す（agent-audit が唯一の writer。dashboard は読み取り専用）。
3. **クラウド候補にも qualification が要る**——`unknown` は自動選択されないので、
   seed か receipt 実績が無いクラウド候補は昇格受けにならず park する。medium に置く
   候補が `qualified` になっていることを「候補の使い分け」画面で確認する。

### 2.4 実行制御 — 直列運転とクラウド枠の温存

- **`workloads.flow.concurrency = 1`**（全体設定 → 実行制御）。ローカル LLM は
  `resource_group=local-llm` 同時 1 が前提で、e4b と 12b を同時常駐させない。
  verify のたびにモデルが入れ替わるコストは許容する（同役割の直列バッチ化=案 3 は
  未着手・未測定。設定でどうにかする段ではない）。
- node-budget の機能別上限で、metered なクラウド CLI（claude / codex / copilot / kiro）の
  消費を「planner + 昇格」想定の小さい枠に絞る（P6 の規律を予算で表現する。
  worker 系へクラウドが流れた run は agent-audit の役割別集計で事後検知できる）。

## 3. workload ごとの期待動作と個別設定

| workload | 設定 | 期待動作・注意 |
|---|---|---|
| flow | §2 のみ（追加設定なし） | 中心面。basic/small 圏 kind はローカル、planner / judge / split / synthesize と昇格はクラウド。局所修正の適格判定（`local_patch_blockers`）は**観測のみ**（A2 の決定）——escalate した aider ノードの 1/3 以上が blockers 付きになったら拒否配線を再評価 |
| routine（agent-loop・定常業務） | routine entry / state に `check` を宣言してから e4b を許す | 候補選択は state 単位。決定的 check（P1）のある state だけローカル適格。check の無い single-shot を自動でローカルに落とさない（設計 §11.2）。escalate（exit 3）は昇格シグナル |
| project | 追加設定なし。plan / review / delivery をローカルへ降ろさない | 自然文 criteria の verify はローカル不成立が実測で確定（B1）。verify は決定的コマンド（verification.commands）だけローカル圏、判断はクラウドか人へ |
| amigos | 有界 role だけ候補宣言（extract / retrieve = e4b、有界文章 review = 12b） | 5 面とも能力未測定（coverage 全面 missing）。team-builder / conductor / 広い implementer をローカルへ振らない |
| audit | extract / distill = e4b、review = 12b | text-eval の適用面そのまま。audit 自身が qualifications の writer なので、この面の receipt 粒度が根拠面の質を決める |
| dashboard | **workload `dashboard` をローカル e4b にしてよい**（初のローカル全面委譲面） | doctor 4 モード 12/12（B1）。「読んで指す」役割は e4b で足りる。method-draft / charter 系は未測定なのでクラウドのまま |

## 4. 設定が効くための配線上の注意（今回のコード確認で判明した分）

### 4.1 JSON 系変種の `default_model` が qwen3 のまま — 唯一のコード変更提案

`agent_flow.agent._agent_for` は、変種対象の役割（JSON 契約・split・retrieve・verify）で
**人が明示していないモデルを変種自身の `default_model` で上書きする**（用途専用
チューニングを tier / control の自動選択で無効化しないための正しい仕様）。ところが:

| 定義 | default_model | 実測済みの正 |
|---|---|---|
| `agents/ollama.json` | qwen3 | gemma4:e4b |
| `agents/ollama-json.json` | qwen3 | gemma4:e4b |
| `agents/ollama-list.json` | qwen3 | gemma4:e4b |
| `agents/ollama-read.json` | qwen3 | gemma4:e4b |
| `agents/ollama-verify.json` | gemma4:12b | （正しい） |
| `agents/ollama-list-thinking.json` | gemma4:e4b | （正しい） |

qwen3 系は gemma4 導入前（2026-08-10 以前）の既定の残りで、**tier 候補に
`ollama / gemma4:e4b` を置いても、判定系役割は qwen3 で走る**（qwen3 未 pull の端末は
env エラーで気づけるが、pull 済みの端末は黙って別モデルで劣化する）。受けは 2 択:

- **（推奨）** 上 4 定義の `default_model` を `gemma4:e4b` へ更新する。実測済み割り当て
  （§1）の正典化であり、「model / harness / sampling を同時に変えない」規律にも反しない
  （harness は不変・モデルは実測済みの側へ寄せるだけ）。
- 当面の回避: 各ツール設定（`agents:` の役割別 model）で e4b を明示する。ただし端末ごとの
  設定散在に戻るので恒久策にしない。

### 4.2 その他

1. **配布の鮮度**: `~/.agents/agents/aider.json` が 2026-08-15 配布のままの端末がある
   （`--agent-policy gemma4-e4b-reliability-v1` を持たない——2026-08-22 文書 §4.3 の発見）。
   設定前に `install.sh` を再実行する。
2. **モデルの取得**: `ollama pull gemma4:e4b gemma4:12b`。bge-m3（記憶検索）は ltm-use 側。
3. **ハーネスの escalate 率は上限として読む**（A2）。本番は不適格タスクを aider へ
   割り当てうるので、dashboard の実績表示で escalate が実測より高くても設定の失敗ではない。
4. **12b の縮退基準を設定でいじらない**: retry_limit=1・候補順 12b → e4b は B3 で決めた
   縮退基準の表現そのものであり、qualifications / 候補順の手編集で壊さない。

## 5. 設定しないこと（既決の再掲）

- `num_ctx` の拡大（「割るのが正解」——事実 7）。
- 12b のコード worker 起用（tier 候補に書かないことで構成的に塞ぐ）。
- `--resample` の既定化（測って不採用——2026-08-22 §4.3）。
- 決定的コンテキスト・スライシングの本番配線（採用根拠は経済に一本化済みだが、
  `read_files=` の区別導入とセットの設計が先。CLI 明示利用のみ）。
- fine-tuning・ランタイム乗り換え（koboldcpp 等は独立 arm の測定待ち）。
- 適格性の手編集を通常 UI に出すこと（読み取り専用のまま）。

## 6. 再評価条件

- **B2（MoE の 32 GB 実測）が通ったら**: 検証役（現 12b）と昇格先の候補差し替えは、
  この構成なら `ollama-verify` の `default_model` 1 行と qualifications の再測で済む。
  tier 表・方針・エンジンは触らない——それがこの設定構成を選ぶ利点である。
- **coverage の missing（28 面）が埋まったら**: amigos / project の該当面を §3 の表へ
  昇格させる。基準線なしにローカルへ移さない規律は不変。
- 昇格率が恒常的に高い・worker 系へクラウドが流れる run が検出された・doctor の
  ローカル委譲で人の検収に残した軸（読みやすさ・網羅性）に苦情が出た場合は、
  該当面だけ medium へ戻す（tiers 表の 1 行で戻せる）。

## 7. 参照

- [2026-08-14 ローカル主体運転計画](2026-08-14-agent-tools-local-first-operation-plan.md)（§2 割り当てマップ）
- [2026-08-15 候補ベース実行方針と dashboard 設計](2026-08-15-agent-tools-candidate-execution-policy-dashboard-design.md)（§6 契約・§9 UI・§12 資源制御）
- [2026-08-22 追加活用とランタイムチューニングの評価](2026-08-22-local-llm-further-utilization-and-runtime-tuning-assessment.md)（§4.2 棚卸し・§4.3 消化記録）
- `agents/aider.json` / `agents/ollama*.json`（variants と役割別既定）
- `tools/agent-dashboard/src/features/orchestration/main/`（`flow-tiers.js` / `profiles.js` / `execution-policy.js` / `execution-policy-compiler.js` / `qualifications.js`）
- `tools/agent-flow/agent_flow/agent.py`（`_agent_for` の変種既定によるモデル上書き）
- `tools/agent-tools/eval/coverage.json`（未測定面）
