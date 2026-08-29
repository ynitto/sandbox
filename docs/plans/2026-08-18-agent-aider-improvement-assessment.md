# agent-aider 改良余地の評価

- 日付: 2026-08-18
- 状態: Implementation reviewed — Phase 1/2 partially complete; Gate 0〜2 pending
- 対象: `agent-aider` / `ollama_chat/gemma4:e4b`
- 前提: CPU only / RAM 32 GB

## 1. 結論

`agent-aider` には改良余地がある。ただし、Aider を汎用の自律エージェントへ拡張するのではなく、
**既知の局所修正を、固定 policy・明示ファイル・決定的検査・限定再試行の下で確実に実行する worker**
として完成度を上げるべきである。

優先順位は次のとおり。

1. 承認済みだが未実装の Gemma 4 reliability policy を、A/B gate を含めて実装する。
2. 決定的な検査結果を受けて同じ局所 task を再投入する運用を標準化する。
3. `aider / gemma4:e4b` の適格範囲を局所修正へ厳密に限定する。
4. adapter と評価台帳の観測性・契約テストを強化する。
5. sampling は一律の既定変更ではなく、独立した評価条件として扱う。
6. repo map、bash tool-loop、自由なファイル探索を `agent-aider` に重複実装しない。

## 2. 現在地

現在の `aider_adapter.py` は薄い wrapper であり、主な責務は次の二つである。

- 非ログインシェルで欠落する `OLLAMA_HOST` / `OLLAMA_API_BASE` / `NO_PROXY` の補完。
- Aider analytics JSONL の token count を `@agent-usage` 契約へ変換すること。

Aider の起動自体は analytics 用一時ファイルを追加して argv を透過し、終了後に usage を集計するだけである。
現在の adapter には model 固有 policy、managed model settings、policy marker、停止分類、checker、再投入はない。

本番定義 `agents/aider.json` は `gemma4:e4b` を既定とし、次の性格を持つ。

- `headless_autonomy: single-shot`
- Aider の Git / auto commit を無効化
- `--map-tokens 0`
- `--file` / `--read` による明示的なファイル受け渡し
- 外側の実行系が timeout とエラー分類を所有

これは汎用エージェントではなく、対象ファイルが決まった編集器として妥当な境界である。

## 3. 最優先候補: Gemma 4 reliability policy

### 3.1 既存設計

`2026-08-13-agent-aider-gemma4-system-policy-design.md` では、
`gemma4-e4b-reliability-v1` を Aider の `system_prompt_prefix` へ注入する設計が承認済みである。

背景試験では、短い constraint-following system instruction により、成功対照を維持したまま
J1 が `0/3` から `3/3`、総合が `6/12` から `9/12` へ改善した。ただし、全候補の走査を要求する
F2 は `0/3` のままだった。このため policy は有望だが、決定的 checker の代替にはならない。

policy は次を固定規則として要求する。

- 明示された要件・禁止・受入条件・出力制約をすべて必須として扱う。
- リスト、候補、ファイル、基準を途中で打ち切らず全件確認する。
- 観測していないファイル、API、依存、編集、テスト結果を捏造しない。
- task を満たす最小変更に留める。
- 完了前に全成果物と受入条件を確認する。
- Aider 本来の編集 protocol を置換しない。

### 3.2 未実装部分

設計と実装計画は存在するが、現行コードには次がない。

- `--agent-policy gemma4-e4b-reliability-v1`
- `--agent-num-ctx` / `--agent-num-predict`
- 一時 managed model-settings の生成
- `system_prompt_prefix` の注入
- `@agent-policy id=... sha256=...`
- 未知 policy、対象外 model、外部 settings 競合の fail closed
- adapter 専用の契約テスト
- worker eval の policy off / on arm

### 3.3 実装条件

既定化の前に、既存設計の Gate 0〜2 を維持する。

1. **Gate 0: 注入契約**
   - wrapper option が Aider argv へ漏れない。
   - policy が system prompt の先頭へ一度だけ入る。
   - Aider の edit prompt と reminder が後続に残る。
   - usage marker と policy marker が共存する。
   - 正常終了・異常終了の双方で一時ファイルを削除する。
2. **Gate 1: 意味 A/B**
   - deterministic judge で baseline / policy を比較する。
   - J1 / F2 の改善と J2 / R1 の無退行を確認する。
3. **Gate 2: Aider worker A/B**
   - まず T2 / T1min、通過後だけ T1 / T3 を比較する。
   - pass/fail だけでなく wall time、token、偽完了、test tampering を記録する。

policy は task 別の自由文にせず、adapter が所有する版管理済み ID に限定する。二つ目の実在 profile が
反復評価で必要になるまでは、可変 profile interface を先行実装しない。

## 4. 最も実績がある改善: 決定的検査と再投入

T1 の過去評価は次の結果だった。

| arm | 受入 | 壁時計中央値 | 読み |
|---|---:|---:|---|
| 一発で実装 + テスト | 1/3 | 446s | 不安定 |
| 実装 / テストへ分解するだけ | 0/3 | 237s | 分解自体には効果なし |
| 分解 + 決定的 gate + 再投入 | 3/3 | 952s | 合格するが高コスト |

この実測から、効いているのは次の二点である。

1. ハーネスが成果物の失敗を決定的に検知する。
2. 失敗した局所 step を再投入する。

診断の詳細を渡さず「仕様を満たしていない」とだけ返した arm も 3/3 だったが、具体的な不一致を渡すと
再試行が約 28% 速かった。したがって、検査の真偽は前提、診断 stdout の受け渡しは速度最適化である。

checker と retry loop を `aider_adapter.py` 自身へ抱え込ませるべきではない。責務は次のように分ける。

| 層 | 責務 |
|---|---|
| `agent-aider` | Aider 起動、model 固有変換、usage / policy /終了様式の観測 |
| engine / statemachine | checker 実行、遷移、限定 retry、候補昇格 |
| deterministic checker | 成果物の振る舞いと受入条件の判定 |

## 5. 適格範囲

既存評価から確定している範囲は次のとおり。

| task | `aider / gemma4:e4b` の結果 | 判断 |
|---|---:|---|
| 1 関数 + 決定的 probe | 3 方式合計 9/9 | 適格 |
| 既存 failing test の修正 | 3 方式合計 9/9 | 適格 |
| 実装とテスト追加を一括 | 通算 1/12 | 自動選択では不適格 |
| 分解 + 決定的 gate | 3/3、中央値 952s | 定型化済みの場合だけ候補 |
| 複数成果物 + 契約テスト | 0/3 | 不適格 |
| `aider / gemma4:12b` code worker | wall 600 / 1800 とも収束せず | 不適格 |

自動選択する局所修正は、少なくとも次を満たすこと。

- 既存コード 1 ファイル、1 symbol 程度。
- 成果物が一つ。
- 既存テストまたは決定的 probe がある。
- テスト、schema、文書の新規作成を要求しない。
- acceptance と verification command が機械可読である。

一つでも欠く場合は `agent-aider` を適格扱いせず、別候補を選ぶか保留する。checker のない
single-shot 実行を自己申告だけで合格させない。

## 6. Sampling の扱い

2026-08-15 の実測で、Aider 経路の実効 temperature は 0 であり、同じ入力に同じ失敗が続いた一因が
greedy decoding であることが確認された。推奨寄り sampling（temperature 1.0 / top-p 0.95 /
top-k 64）の arm では T1 が `0/3` から `1/3` へ動き、T2 は `3/3` を維持した。

ただし、これは一律の既定変更を支持するほど強くない。

- T1 は依然 1/3 である。
- JSON、編集形式、停止性への退行を別途確認する必要がある。
- n=3 中心の結果であり、率の精密な推定ではない。
- 決定的 gate の必要性は変わらない。

policy、model、sampling を同時に変更せず、それぞれを独立した arm として比較する。sampling 条件は
ledger / manifest に必ず記録し、「未指定」と「既定値を明示」を区別する。

## 7. 観測性とテスト

### 7.1 Adapter 契約テスト

`test_aider_adapter.py` を追加し、少なくとも次を公開挙動として検査する。

- policy 未指定時の argv 透過と既存 usage 変換。
- `--agent-policy` が Aider argv へ漏れないこと。
- 対象 model の完全一致。
- 未知 policy と model 不一致の fail closed。
- managed settings と外部 `--model-settings-file` の競合拒否。
- `num_ctx` / `num_predict` の正整数検証と同一 entry への合成。
- policy marker と usage marker の共存。
- 正常終了、Aider error、`FileNotFoundError` 時の一時ファイル cleanup。

### 7.2 評価台帳

worker evaluation の各 record には次を残す。

- agent CLI と model
- Aider version
- policy ID / SHA-256
- effective model settings
- sampling 条件
- map token と auto-test の有無
- wall limit と実時間
- token usage
- checker の pass/fail と診断
- timeout、CLI error、empty、returned などの終了様式
- retry 回数

`worker_eval.py` には現在の `agents/aider.json` と一致しない古い説明が残っているため、実装時に起動条件の
表示とコメントを正典に合わせる。

## 8. 維持すべき境界

### 8.1 明示的なファイル受け渡し

Aider は chat に入っているファイルしか編集しない。`file_flag: --file` と `read_flag: --read` を維持し、
engine が編集対象と参照対象を明示する。自由探索を前提にしない。

### 8.2 repo map は既定 0

このリポジトリでは Aider の実効 context が狭く、常設材料を削る運用を既に採っている。repo map は
既定 0 のままとし、探索が必要な評価 task だけ明示的に予算を与える。通常運用では read-only agent が
候補ファイルを絞り、その結果を `--read` / `--file` で Aider へ渡す。

### 8.3 tool-loop を重複実装しない

bash 付き反復は既存の `agent-ollama` が所有している。Aider の reflection / auto-test、外側 engine の
retry、独自 bash loop を三重化すると CPU only 環境で停止性が悪化する。

| 処理 | 経路 |
|---|---|
| 対象ファイルが決まった局所編集 | `agent-aider` |
| 探索・調査・command 反復 | `agent-ollama` 等の tool-loop |
| 決定的検査 | engine / checker |
| 失敗時の再投入 | statemachine / execution policy |
| 厳密 JSON | `ollama-json` |
| 網羅的レビュー | 検証専用候補 + timeout / retry |

## 9. 実施ロードマップ

### 9.0 2026-08-20 実装レビュー

レビュー対象は main へ入った実装コミット `9dc9ef8`（policy 本体）と `784e0ea`（eval 条件の保存）。設計正典
`2026-08-13-agent-aider-gemma4-system-policy-design.md` と実装計画
`2026-08-13-agent-aider-gemma4-system-policy-implementation-plan.md` に照らし、状態を次のように判定した。

凡例: `[x]` 完了、`[~]` 一部完了、`[ ]` 未完了。

#### 完了した実装

- [x] adapter 専用 option `--agent-policy` / `--agent-num-ctx` / `--agent-num-predict` を
  Aider argv から除去する。
- [x] 固定 ID `gemma4-e4b-reliability-v1` と固定 policy 本文を adapter が所有し、対象 model
  `ollama_chat/gemma4:e4b` を完全一致で検証する。
- [x] policy と `num_ctx` / `num_predict` を単一の一時 model-settings entry に合成し、
  `system_prompt_prefix` と `extra_params` を JSON で渡す。
- [x] 未知 policy、policy の model 不一致、managed settings と外部 `--model-settings-file` の競合、
  非正整数を起動前に fail closed とする。
- [x] `@agent-policy id=... sha256=...` marker を出力し、既存の `@agent-usage` 変換を維持する。
- [x] analytics log と managed settings を通常終了、`FileNotFoundError`、および Aider 非ゼロ終了の後に
  削除する契約テストを持つ。非ゼロ終了のケースでは `@agent-policy` と `@agent-usage` が同一 run に
  一度ずつ共存することも同じテストで固定した。
- [x] worker eval で policy off / v1 を token 単位で切り替え、`num_ctx` / `num_predict` を adapter
  option で渡す。`--agent-policy` 未指定は「本番定義をそのまま継承する」第三の腕として区別する。
- [x] worker eval の古い「`agents/aider.json` は未作成」という表示を正典参照へ修正する。
- [x] ledger に Aider version、policy ID / hash、sampling、`num_ctx` / `num_predict`、wall limit と wall time、
  token usage、map token、auto-test の有無、checker の pass/fail と診断、終了様式、呼出回数、retry 回数と
  retry trace を記録する。policy ID / hash と token usage は adapter marker から call 単位で読み、
  eval 側で条件を複製しない。

#### レビューで確認した未完了・不足

- [ ] **本番既定化の前提 gate が未通過。** `agents/aider.json` では policy が既定化済みだが、
  Gate 0 の実 Aider prompt smoke、Gate 1 の deterministic judge A/B、Gate 2 の worker A/B は
  実施記録がない。設計上は gate 通過前の本番既定化を完了扱いにしない。
- [~] Gate 0 の unit contract は argv除去、settings内容、fail closed、cleanup、および非ゼロ終了時の
  marker 共存を確認する。残るのは unit test では届かない範囲——実 prompt 内での一度だけの先頭注入、
  edit prompt / reminder の維持、一時 settings の作成・書込失敗時の cleanup が未確認である。
- [ ] adapter の変更分岐 C1 100%を測定していない。現環境には `pytest-cov` / `coverage` がなく、
  coverage gate は未完了である。
- [x] ledger の観測性は token usage、map token、auto-test 有無、checker 診断、wall limit、retry 回数まで
  拡張された。**残っていた 2 項目も 2026-08-29 に入れた**——trace の各呼び出しが実行 argv 全体
  （`argv`）と実効 model settings（`model_settings`）を持つ。settings は adapter が管理する腕では
  `@agent-settings` marker が正、それが無い腕（`--agent-policy off` 等）は argv が名指しした
  ファイルの中身が正。policy 本文は載せない（同一性は `policy_sha256` が担保する）。
- [ ] Gate 1 の採用条件（J1 / F2改善、J2 / R1無退行、総合改善、parse / repair率無悪化）を未評価。
- [ ] Gate 2 の T2 / T1min baseline-policy比較と、通過後の T1 / T3 比較を未評価。
- [ ] execution resolver の局所修正適格条件、checker必須化、限定retry、retry exhaustion後の候補昇格は
  未実装。`worker_eval.py` の retry は評価ハーネス内の既存挙動であり、本番運用側の完了証拠ではない。
- [ ] sampling、policy、modelを分離した ledger / manifest による比較試験と、別 coding model 比較を未実施。

#### レビュー判断

adapter と eval seam の実装は **A/B評価を開始できる段階**まで進んだ。一方で、採用 gate と運用側は
未完了であるため、計画全体の状態は `Complete` ではない。特に本番定義の policy flag は「採用済み」の
証拠として扱わず、Gate 0〜2 を通過できない場合は設計の中止条件に従って外す。

### Phase 1: 低リスク整備

1. [x] adapter の現行挙動を契約テストで固定する。
2. [x] `worker_eval.py` の古い条件表示を修正する。
3. [x] Aider version と実効条件を台帳へ記録する（2026-08-29 に完了。実行 argv 全体と実効
   model settings が trace の各呼び出しに入る）。

### Phase 2: Reliability policy

1. [x] wrapper option と managed settings を実装する。
2. [x] policy marker、競合検出、cleanup を実装する。
3. [~] unit test は通過（非ゼロ終了時の marker 共存と cleanup を含む）。`--show-prompts` smoke と
   一時 settings の作成・書込失敗 contract は未完了。

### Phase 3: A/B gate

1. [ ] deterministic judge baseline / policy。
2. [ ] T2 / T1min baseline / policy。
3. [ ] 短い gate を通過した場合のみ T1 / T3。
4. [ ] 成功対照に退行がなければ本番既定化する（設定変更は先行しているが、採用判定は未完了）。

### Phase 4: 運用側

実装と突き合わせ直した（2026-08-23。[2026-08-22 計画 §4.2 A2](2026-08-22-local-llm-further-utilization-and-runtime-tuning-assessment.md)）。

1. [~] 局所修正の適格条件は `agentcore.nodecontract.local_patch_blockers()` が機械判定する。
   ただし呼び出しは `agent_flow/work.py` の claim 時 1 か所で、**理由を claim / result のメタへ残すだけ**
   ——`executionresolver` は blockers を読まない（観測まで。拒否には配線していない）。
2. [ ] checker が無い場合の自動選択拒否は未配線。**当面は配線しない**と決めた——Resolver が拒否するには
   候補側に「局所修正専用」という能力属性が要り、selection_policy の schema・Compiler（agent-audit）・
   dashboard に波及する。代わりに (1) の観測値で不適格割り当ての頻度を数え、ハーネスの escalate 率は
   **運用値ではなく上限**として読む。再評価条件: escalate した aider ノードのうち blockers 付きが
   無視できない割合（目安 1/3）を占めたとき。
3. [x] checker fail 時の限定再投入は agent-loop の statemachine（gate の `max_retries`・診断つき再投入）
   と、worker_eval の `run_steps` に入っている。
4. [x] 上限到達での昇格は statemachine の `check_on_exhausted`（既定 `escalate`）。
   Resolver 側も `retry_limit` 到達で次候補へ回す（`fallback_candidates`）。

### Phase 5: モデル / ハーネス比較

- [ ] 上記条件を固定した後で、`gemma4:e4b` と別の coding model を比較する。Pi 等との比較は同一モデルを
使ったハーネス比較として分離し、model 差と agent 差を同時に変更しない。

## 10. 採用しない方向

- adapter 内部への汎用 bash tool-loop。
- engine やユーザーからの自由な system prompt 注入。
- 実在する第二 profile がない段階での可変 policy framework。
- repo map の常時有効化。
- checker のない自己申告完了。
- 複数成果物、schema、契約変更、曖昧な設計への自動投入。
- `gemma4:12b` の code worker 昇格。

## 11. 参照

- `agents/aider.json`
- `agents/ollama.json`
- `agents/ollama-verify.json`
- `tools/agent-tools/agentcore/agentcore/aider_adapter.py`
- `tools/agent-tools/agentcore/agentcore/agentcli.py`
- `tools/agent-tools/eval/worker_eval.py`
- `tools/agent-tools/eval/test_worker_eval.py`
- `tools/agent-tools/eval/results/archive/2026-08-13-t1-decomposition-report.md`
- `tools/agent-tools/eval/results/archive/2026-08-14-text-eval-report.md`
- `docs/plans/2026-08-13-agent-aider-gemma4-system-policy-design.md`
- `docs/plans/2026-08-13-agent-aider-gemma4-system-policy-implementation-plan.md`
- `docs/plans/2026-08-14-agent-tools-local-first-operation-plan.md`
- `docs/plans/2026-08-15-agent-tools-candidate-execution-policy-dashboard-design.md`
