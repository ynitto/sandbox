# agent-aider Gemma 4 system policy 実装計画

- 日付: 2026-08-13
- 状態: **Complete（2026-08-29）** — Gate 0〜2 を実施し、本番既定の flag を維持すると決めた（§1.2）
- 最終レビュー: 2026-08-20（実装コミット `9dc9ef8` / `784e0ea`）
- 設計正典: `docs/plans/2026-08-13-agent-aider-gemma4-system-policy-design.md`
- 設計コミット: `f9a66ad`
- 実装方式: Python `unittest` + pytest-cov、Red-Green-Refactor の垂直スライス

## 1. 完了条件

実装完了はコードが書けた時点ではなく、次をすべて満たした時点とする。2026-08-20レビューの
凡例は `[x]` 完了、`[~]` 一部完了、`[ ]` 未完了。

1. [x] `agent-aider --agent-policy gemma4-e4b-reliability-v1 --model ollama_chat/gemma4:e4b ...`
   が fixed policy を Aider の `system_prompt_prefix` へ一度だけ注入する。
   **2026-08-29 に実 Aider（v0.86.2）の `--show-prompts` で確認した**（Gate 0）:
   policy 全文が SYSTEM に **1 回だけ・先頭**に入り、policy を取り除いた SYSTEM は
   policy 無しの出力と**完全一致**する（＝Aider 本来の編集プロンプトを 1 文字も変えていない）。
   USER / ASSISTANT の few-shot と reminder も同一。
   記録: `tools/agent-tools/eval/results/archive/worker/showprompts-2026-08-29-policy-{v1,off}-gemma4-e4b.txt`。
2. [x] wrapper 専用 option は Aider argv へ漏れない。
3. [x] policy 未指定時の既存 usage 変換と argv 透過は変わらない。
4. [x] 未知 policy、model 不一致、外部 model-settings 競合を黙って無効化しない。
5. [x] analytics log と policy settings の一時ファイルを通常終了、`FileNotFoundError`、Aider非ゼロ終了で
   削除する。**2026-08-29 に残りの経路も閉じた**——一時 settings の作成失敗と**書込失敗**を
   契約テストで縛った（どちらも理由を出して閉じ、aider を起動せず、一時ファイルを残さない）。
   書込失敗はそれまで例外が main を突き抜けており、**中身の無い settings を指したまま
   aider が起動しうる**（policy が黙って外れる）形だった。
6. [x] `agents/aider.json` は Gemma 4 policy を宣言済み。**2026-08-29 に Gate 0〜2 を実施し、
   本番既定として維持すると決めた**（§1.2）。
7. [~] `worker_eval` は off / v1 / 未指定（本番定義を継承）のarmと、policy ID/hash・token usageを含む
   ledger項目を実装済みだが、同条件A/Bの実測は未実施。
   **台帳側は 2026-08-29 に完成**（実行 argv 全体と実効 model settings を追加。08-18 §7.2）。
8. [ ] 変更した adapter の C1 分岐カバレッジ100%は未測定。
9. [ ] deterministic judge A/B と Aider worker A/B の採用ゲートは未実施。

### 1.0 Gate 0 の副産物 — 本番は `whole` 編集形式で走っている（2026-08-29）

`--show-prompts` の banner が `Model: ollama_chat/gemma4:e4b with whole edit format` を出した。
本番定義（`agents/aider.json`）も現行の eval も `--edit-format` を宣言しておらず、
Aider のモデル別既定に従っている（`edit_format: diff` を書くのは `worker_eval` の
`_aider_argv_legacy` だけで、これは比較用に残した旧経路である）。

**policy の判定には影響しない**——Gate 0 の比較は policy あり / なしで、編集形式は
両方とも `whole` で揃っている。影響するのは別の文書の説明で、eval README の
「aider が効くのは……diff 形式で返させるので全文再生成の decode を払わない」は
現行構成の説明になっていない（同 README で訂正済み）。

編集形式そのものを腕として測るかは別の判断で、ここでは決めない。

### 1.2 2026-08-29 の決着 — flag は維持する

Gate 0〜2 を実施した結果、**外す根拠が出なかった**ので本番既定を維持する。

| ゲート | 判定 | 中身 |
|---|---|---|
| Gate 0（prompt smoke） | **通過** | 文面が 1 回だけ先頭に入り、Aider 本来の編集プロンプトは無傷（§6） |
| Gate 1（judge A/B） | F2 のみ不通過 | J1 0/5 → 5/5、J2 / R1 無退行、合計 25/40 → 30〜31/40、形式違反 0（§7） |
| Gate 2 必須（T2 3/3 維持） | **満たす** | 3/3 → 3/3（§8） |
| Gate 2 主指標（T1min） | 測れない | 対照が 3/3 の天井。本書が baseline とした 1/3 は古い値（§8） |
| Gate 2 余地セル（T1） | **動いた** | 0/3 → 2/3。off の 3 本は同一の誤り、v1 は 2 本通した（§8.0） |
| Gate 2 余地セル（T3） | 不動 | 両腕 0/3。(b) 族なので成果物を割る話であり policy の管轄外（§8.0） |

**4 ゲートを通して退行は 1 つも出ていない。** 行動 7（不通過なら外す）は
「flag 追加だけを採用証拠にしない」ための規律であって、改善の証拠を捨てる規律ではない。

**残す条件を 2 つ付けて閉じる。**

1. **F2 の 4/5 は達成できなかった事実として残す**（基準を後から緩めて通過扱いにしない）。
2. **n = 3 は存在の証明であって率ではない。** T1 の 0/3 → 2/3 は n ≥ 10 で引き直す。
   それまで「policy で T1 が通るようになった」と率で語らない。

**コストは受け入れる。** tokens_in +12〜17%（policy 本文ぶん）。難セルでは壁時計が上限へ
張り付く（T1 中央値 252s → 600s）——ただし通った 2 本のうち 1 本は `mode=timeout` のまま
受入だけ通っており、これは同日入れた `AGENT_MAX_TOOL_ROUNDS_WRITE`（呼び出し回数上限）が
効く場所である。

### 1.1 2026-08-20レビュー時点の結論

adapterとevaluation seamのコード実装は完了に近いが、この文書が定義する「実装完了」には達していない。
本番定義のflag追加だけを採用証拠にせず、Gate 0〜2とcoverageを完了するまで状態を`Complete`へ変更しない。

完了済みの縦スライス:

- [x] Cycle 1〜8相当: policy注入用settings、透過、marker、model完全一致、未知ID、競合拒否、数値合成・検証。
- [~] Cycle 9相当: 通常終了、`FileNotFoundError`、Aider非ゼロ終了のcleanupと、非ゼロ終了時の
  policy/usage marker共存は確認済み。残るのは一時settings作成・書込失敗の契約。
- [~] Cycle 10相当: shipped definitionと定義テストは実装済み。採用gateは未通過。
- [~] Cycle 11相当: off/v1/未指定のargv、adapter数値option、token台帳を含むledger metadataは実装済み。
  A/B実測は未完了。
- [~] Cycle 12相当: policyとworker A/Bは文書化済み。Gate 0〜2全体の実行例・結果記録とinstall smokeは未完了。

次に行うアクション（順序を固定する）:

1. 一時settings作成・書込失敗の契約テストを追加する（同一runでのmarker共存とAider非ゼロ終了は済）。
2. `pytest-cov`を利用可能な環境でbranch coverageを測り、adapter C1 100%まで不足分岐を埋める。
3. ~~実Aider `--show-prompts` smokeを行い、policyの一度だけの先頭注入とedit prompt/reminder維持を記録する。~~
   **完了（2026-08-29）**——上記 §1-1。
4. ~~worker ledgerへ完全なagent CLIと実効settingsを追加する（tokens in/out、map token、auto-testは済）。~~
   **完了（2026-08-29）**。
5. Gate 1のdeterministic judge baseline/v1を実行し、J1/F2改善とJ2/R1無退行を判定する。
6. ~~Gate 1通過後だけGate 2のT2/T1minを実行し、通過後だけT1/T3へ進む。~~
   **2026-08-29 に人の判断で 1 回だけ順序を破った**——Gate 1 は F2 のみ不通過で、
   policy が効かせたい相手（コード worker）の実測をまだ取っていないため（§7）。
7. Gate 0〜2不通過なら`agents/aider.json`から既定policy flagを外し、adapter/eval armのみ残す。
   **判断は Gate 2 の結果を見てから行う**（同上）。

## 2. 実装開始時点の前提（履歴）

以下は2026-08-13の実装開始前に記録した値であり、2026-08-20の現状を表すものではない。

- 言語: Python 3、標準ライブラリ中心。
- 既存テスト: `unittest`。pytest 8.4.2 から実行できる。
- coverage: pytest-cov 7.1.0 を利用可能。
- 現在の `aider_adapter.py` coverage:
  - statements 45、branch 8、C1 77%
  - 確認コマンド:

```bash
rtk python3 -m pytest -q \
  tools/agent-tools/agentcore/agentcore/tests/test_aider_adapter.py \
  --cov=agentcore.aider_adapter --cov-branch --cov-report=term-missing
```

- 現 worktree には、本設計より前の Ollama native system prompt / eval option の未コミット変更と、
  unrelated な agent-dashboard 変更がある。実装時も対象ファイルを限定して stage し、unrelated diff を
  変更・整形・コミットしない。
- `worker_eval.py` は現在 `num_predict > 0` の場合に独自 `--model-settings-file` を生成する。
  policy と競合するため、adapter 管理の一時 settings へ移す。

## 3. 小さい公開 interface

adapter の公開入口は増やさず、既存の `main(argv)` を維持する。追加するのは wrapper 専用 CLI option
だけで、agentcore 全体の schema や `headless_cmd` に汎用 system prompt field は追加しない。

```text
agent-aider
  [--agent-policy gemma4-e4b-reliability-v1]
  [--agent-num-ctx N]
  [--agent-num-predict N]
  <existing Aider argv...>
```

- `--agent-policy`: 固定 policy ID。自由文は受けない。
- `--agent-num-ctx`, `--agent-num-predict`: eval 用の内部 option。同じ managed model settings entry の
  `extra_params` へ入れる。正整数のみ、未指定は field 自体を作らない。
- 三つの option は subprocess 起動前に必ず除去する。
- policy map、model 完全一致、settings serialization、cleanup は `main(argv)` の内側へ隠す。

model-settings 一時ファイルは JSON で生成する。JSON は YAML 1.2 の有効な subset なので、Aider の
YAML loader が読め、adapter に YAML parser 依存を追加しない。

```json
[
  {
    "name": "ollama_chat/gemma4:e4b",
    "system_prompt_prefix": "<fixed text>",
    "extra_params": {
      "num_ctx": 32768,
      "num_predict": 2048
    }
  }
]
```

`extra_params` は内部 option が一つ以上指定された場合だけ生成する。数値 option だけがあり policy が
off の場合も、prefix なしの managed settings を生成する。これにより num-predict ありの off / on で
同じ settings entry を使える。edit format と map token は既存の CLI flag を正典とし、managed settings
に重複させない。

## 4. TDDサイクル

各サイクルは「テストを一つ追加して RED を確認 → 最小実装で GREEN → 必要な場合だけ refactor」の順で
進める。複数テストを先にまとめ書きしない。

### Cycle 1 — tracer bullet: 対象 model へ policy を注入

**対象**

- test: `tools/agent-tools/agentcore/agentcore/tests/test_aider_adapter.py`
- implementation: `tools/agent-tools/agentcore/agentcore/aider_adapter.py`

**RED**

`main(argv)` を policy ID と対象 model 付きで呼ぶ。system boundary の `subprocess.run` だけを fake にし、
fake Aider から観測した argv と一時 settings 内容で、次の一つの振る舞いを検証する。

> 対象 model の実行は、固定 policy を持つ managed model-settings を Aider へ渡す。

テストは `--agent-policy` が Aider argv に存在しないこと、`--model-settings-file` が一つだけ存在すること、
settings の `name` と `system_prompt_prefix` が正しいことを、同じ外部振る舞いの観測として確認する。

**GREEN**

- policy ID → immutable text / target model の定数 map を追加する。
- wrapper option を左から一度だけ parse して forwarded argv を作る。
- `tempfile.mkstemp` で JSON settings を作り、Aider argv の `--message` より前へ追加する。
- 既存 analytics log / usage 集計を維持する。

**確認**

```bash
rtk python3 -m pytest -q \
  tools/agent-tools/agentcore/agentcore/tests/test_aider_adapter.py -k applies_policy
```

### Cycle 2 — policy 未指定は完全透過

**RED**

policy を渡さない `main(["--model", ..., "--message", ...])` で、Aider argv に analytics flag 以外の
追加 model-settings がなく、policy marker も出ないことを検証する。

**GREEN**

policy 未指定 branch を no-op にする最小実装を加える。既存
`test_sums_exact_message_usage` を変更せず通す。

### Cycle 3 — policy marker と usage marker の共存

**RED**

fake Aider が analytics event を書く policy-on run で、stderr が次の二契約を両方含むことを検証する。

```text
@agent-policy id=gemma4-e4b-reliability-v1 sha256=<12 hex>
@agent-usage tokens_in=... tokens_out=...
```

**GREEN**

policy text の UTF-8 bytes から SHA-256 を計算し、Aider 起動前に policy marker を一度出す。

### Cycle 4 — 対象 model 完全一致

**RED**

有効 policy と `ollama_chat/qwen3.5:9b` を渡すと Aider を起動せず、非ゼロ return code と
`[agent-error:env]` を返すことを検証する。

**GREEN**

`--model VALUE` と `--model=VALUE` を正規化して取り出し、policy の target と比較する。
model 未指定も同じ config error とする。

### Cycle 5 — 未知 policy は fail closed

**RED**

`--agent-policy typo` で Aider が起動されず、未知 ID を含む actionable error が出ることを検証する。

**GREEN**

policy map lookup の error branch を追加する。候補の自動補正や fallback は行わない。

### Cycle 6 — 外部 model-settings 競合を拒否

**RED**

policy または内部数値 option と Aider の `--model-settings-file` を同時指定すると起動せず、managed
profile との競合を説明する error を返すことを検証する。

**GREEN**

managed settings を必要とする forwarded argv に外部 settings flag があれば fail closed にする。policy と
内部数値 option がどちらもない場合だけ、外部 settings を従来どおり透過する。

### Cycle 7 — eval extra_params を同一 entry へ合成

**RED**

`--agent-num-ctx 32768 --agent-num-predict 2048` を指定した run で、wrapper option が Aider argv から消え、
managed settings の同一 model entry に二値が整数で保存されることを検証する。

**GREEN**

内部数値 option を正整数として parse し、`extra_params` を必要時だけ生成する。

### Cycle 8 — 不正な eval 数値を拒否

**RED**

0、負数、非数の代表一件から開始し、起動前 error を検証する。GREEN 後に各境界を一サイクルずつ追加し、
最大3サイクルで正整数以外を網羅する。

**GREEN**

validation branch を最小実装する。Aider へ値を文字列で透過しない。

### Cycle 9 — 一時ファイル cleanup

**RED**

fake Aider が正常 return した後、analytics log と managed settings の双方が存在しないことを検証する。

**GREEN**

二つの path を一つの `finally` ownership にまとめる。

続くサイクルで `subprocess.run` が `FileNotFoundError` を投げる場合も同じ cleanup を検証する。既存の
return code 127 とエラーメッセージを維持する。

### Cycle 10 — shipped agent definition で本番既定化

**対象**

- test: `tools/agent-tools/agentcore/agentcore/tests/test_agentcli_jsonvariant.py`
- implementation: `agents/aider.json`

**RED**

shipped `aider` definition を public loader で読み、生成 argv が policy ID を一度だけ含むことを検証する。

**GREEN**

`agents/aider.json` の command に wrapper flag と固定 ID を追加する。agent CLI schema は変更しない。

**回帰確認**

```bash
rtk python3 -m unittest discover -s tools/agent-tools/agentcore/agentcore/tests
rtk python3 -m unittest discover -s tools/agent-tools/agentcore/tests
rtk node --test tools/agent-dashboard/test/agent-cli-golden.test.js
```

### Cycle 11 — worker_eval の off / v1 arm

**対象**

- test: `tools/agent-tools/eval/tests/test_worker_eval.py`（新規）
- implementation: `tools/agent-tools/eval/worker_eval.py`

**RED**

まず `aider_argv(task)` の公開観測結果について、`--agent-policy off` が shipped definition の fixed flag を
決定的に除去し、`v1` が一度だけ残すことを一ケースずつ検証する。次のサイクルで num-predict が外部
model-settings ではなく wrapper option になることを検証する。

**GREEN**

- CLI option `--agent-policy off|gemma4-e4b-reliability-v1` を追加する。
- argv list を token 単位で編集し、文字列置換は使わない。
- `aider_settings()` と eval 所有の YAML file を削除する。
- `--num-predict` は `--agent-num-predict`、必要な context は `--agent-num-ctx` へ変換する。

次の垂直スライスで ledger record に `policy_id` / `policy_sha256` を追加する。hash は wrapper stderr の
`@agent-policy` marker から取得し、eval 側で policy 本文を複製しない。

### Cycle 12 — docs と install smoke

**対象**

- `tools/agent-tools/README.md`
- `tools/agent-tools/eval/README.md`
- `tools/agent-tools/install.sh`（コード変更が不要なら触らない）

**RED 相当の確認**

現行 install で `aider_adapter.py` が単体 script としてコピーされること、追加 import が標準ライブラリ
だけであることを確認する。文書に記載予定のコマンドを実行し、未実装 option で失敗する状態を確認する。

**GREEN**

install 方式は維持し、README に policy、off arm、Gate 0〜2 の実行例と marker を記載する。

## 5. Coverage gap analysis

全 adapter cycle が GREEN になった後、次を実行する。

```bash
rtk python3 -m pytest -q \
  tools/agent-tools/agentcore/agentcore/tests/test_aider_adapter.py \
  --cov=agentcore.aider_adapter --cov-branch --cov-report=term-missing \
  --cov-report=json:/tmp/agent-aider-coverage.json
```

1. 未カバー分岐を一件ずつ、利用者から観測できる振る舞いへ言い換える。
2. 到達可能なら一件ずつ RED→GREEN を追加する。
3. 到達不能な defensive branch は削除できないか先に検討する。
4. 最大5回で C1 100%を目指す。100%未達なら残る分岐と理由を明示し、実装完了にしない。

既存 `_read_usage` の malformed JSON、負 token、analytics file 読込失敗も公開 stderr / usage marker の
振る舞いとして追加できる。private function の直接 test は行わない。

## 6. Gate 0: 実 Aider prompt smoke

**状態: [ ] 未実施。** 対象環境で`aider` / `agent-aider`実行ファイルを確認できず、unit testだけでは
promptの連結順序を証明できないため、完了扱いにしない。

unit test 後、install 前の adapter script または一時 install prefix を使って実 Aider 0.86.2 を起動する。
Ollama へ生成要求を送らない `--show-prompts` を使う。

```bash
rtk agent-aider \
  --agent-policy gemma4-e4b-reliability-v1 \
  --model ollama_chat/gemma4:e4b \
  --show-prompts --no-git --no-check-update --no-analytics
```

確認項目:

- 出力先頭に policy が一度だけある。
- 直後に Aider の edit-format-specific prompt が残る。
- policy text が user message へ入っていない。
- stderr に policy marker が一度ある。
- 終了後に `agent-aider-policy-*` 一時ファイルが残らない。

## 7. Gate 1: judge semantic A/B

**状態: [x] 実施（2026-08-29）。判定は「条件どおりには不通過」——5 条件のうち F2 だけ届かない。**

baseline / v1 の順を反転した 2 ブロック（A: baseline → v1 / B: v1 → baseline）で、
8 ケース × 5 回 × 2 腕を回した。policy arm は `AGENT_OLLAMA_SYSTEM_PROMPT` へ設計正典の
固定文（`POLICY_ID=gemma4-e4b-reliability-v1` / sha256 `1470af939e08`）を渡している。
台帳: `tools/agent-tools/eval/results/archive/judge/ledger-2026-08-29-gate1-{baseline,v1}-{A,B}-gemma4-e4b.jsonl`。

| ケース | baseline (A / B) | v1 (A / B) | 採用条件 | 判定 |
|---|---|---|---|---|
| J1 | 0/5・0/5 | **5/5・5/5** | 4/5 以上 | **通過** |
| F2 | 0/5・0/5 | 1/5・1/5 | 4/5 以上 | **不通過** |
| J2 | 5/5・5/5 | 5/5・5/5 | 無退行 | 通過 |
| R1 | 5/5・5/5 | 5/5・5/5 | 無退行 | 通過 |
| 8 ケース合計 | 25/40・25/40 | 31/40・30/40 | baseline 超え | 通過 |
| 空・形式違反 | 0/40 | 0/40 | 悪化なし | 通過 |

**2 ブロックで結果が一致した**ので、順序による warm-cache bias は出ていない。
（`S1` 5/5・`S2` 0/5・`F1` 5/5 は両腕とも同じで、policy は動かしていない。
`R2` は v1-B の 1 本だけ 4/5 で、これは採用条件に入っていないが記録しておく。）

**不通過の中身は「policy が悪くした」ではない。**

- 効いた: **J1 が 0/5 → 5/5**。baseline は 5 本とも `winner=c4` を選び、v1 は 5 本とも
  正解の `c3` を選んだ——同形の誤りが丸ごと消えている。
- 届かない: **F2 は 0/5 → 1/5**。誤り方は両腕とも同じで、`c3` だけを採って
  `c2 / c5 / c6` を落とす（期待は 4 件）。policy は 5 本のうち 1 本だけ全件拾わせた。
- 退行は 1 つも無い。形式違反も 0/40 のまま。

**F2 の 4/5 は「policy が F2 を直す」ことを要求する絶対基準**であり、baseline が 0/5 の
セルに対して置かれている。policy は改善させたが基準には届かなかった。

### 7.1 F2 は policy の文面では直らない（2026-08-29 の追試）

「文面を良くすれば F2 も通るのでは」を実測で潰した。3 条（`Use only criteria stated…`）を
**禁止文から手順へ**変えた 1 テーマだけの候補 v2a を作り、順を反転した 2 ブロックで測った
（文面以外は同一）。候補文面と台帳は `tools/agent-tools/eval/results/archive/judge/` に置いた。

| ケース | v1（4 ブロック） | v2a（2 ブロック） |
|---|---|---|
| F2 | 1/5・1/5・0/5・0/5 | 1/5・1/5 |
| J1 / J2 / R1 / F1 / S1 | 5/5 | 5/5（維持） |

**v1 の揺れ幅の中**で、改善も退行も無い。F2 に対するプロンプト側の攻撃はこれで
**4 回連続で失敗**した——sampling（P10）・`think=prompt`（P10）・policy v1・policy v2a。

**構造を変えると同じ日・同じ条件で通る。**

| 経路 | v1 | v2a |
|---|---|---|
| F2（直接 filter させる） | 0/5 | 0/5 |
| F2P（決定化: モデルは事実抽出だけ・判定は機械） | **5/5** | **5/5** |

`think: off` + `--format json` + 「採用した id だけを配列で出せ」の下では、policy が要求する
「全項目を全基準に照らす」**内部手続きを実行する場所が無い**（出力はいきなり `["c3"]`）。
列挙を出力契約の側へ移すと通る。

**結論: F2 の 4/5 は policy の担当ではない。** 多基準 filter / judge は決定化へ回すという
既定の方針が正しく、この基準はゲートの条件として不適切だった（policy に、policy では
届かない仕事を課していた）。**基準は緩めない**——§1.2 の条件 1 のとおり「達成できなかった
事実」として残し、F2 の担当を決定化へ移したことをここに記録する。

**次の判断は §1 の行動 7（不通過なら本番既定の flag を外す）に掛かる**が、
数字は「外す」を素直に支持していない（総合 +5〜6/40・退行なし・J1 は全滅から全通）。

**人の判断（2026-08-29）: flag は維持したまま Gate 2 まで測ってから決める。**
§1 行動 6 の「Gate 1 通過後だけ Gate 2」は、この 1 回だけ明示的に破る。理由は、
policy が本来効かせたい相手が**コード worker** であって judge ではなく、Gate 1 は
本書自身が「policy 文面そのものを**安価に**検証する」代理と位置づけたものだからである
——本命の実測を取らずに外すと、改善の証拠（J1 0/5 → 5/5）を捨てることになる。
順序を破ったこと自体はここに記録として残す。

既存 `judge_eval.py` の native Ollama system prompt seam を使い、policy 文面そのものを安価に検証する。
実行順による warm-cache bias を減らすため、baseline / v1 の順を反転した二ブロックで実施する。

```bash
rtk python3 tools/agent-tools/eval/judge_eval.py \
  --model gemma4:e4b --repeat 5 \
  --cases S1,S2,F1,F2,J1,J2,R1,R2 \
  --results-dir tools/agent-tools/eval/results/gemma4-policy-v1
```

policy arm は `AGENT_OLLAMA_SYSTEM_PROMPT` に設計正典の固定文を渡す。ledger / report に raw prompt を
複製せず policy ID/hash を併記できるよう、必要なら judge eval に同じ marker metadata option を加える。

採用条件:

- J1 4/5以上
- F2 4/5以上
- J2 / R1 無退行
- 8ケース合計で baseline 超え
- parse / repair 率の悪化なし

## 8. Gate 2: Aider worker A/B

**状態: [~] 短いセル（T2 / T1min）を実施（2026-08-29）。必須条件は満たすが、主改善指標は
天井で測れなかった。** T1 / T3 は実行中。

台帳: `tools/agent-tools/eval/results/archive/worker/ledger-2026-08-29-gate2-policy-{off,v1}-gemma4-e4b.jsonl`。
sampling は未宣言（実効 temperature 0 ＝貪欲デコード）なので、**3 回の反復で tokens は完全に同値**
——tokens の差は測定ノイズではなく信号である。壁時計だけはサーバ負荷で揺れる。

| | off | v1 | 差 |
|---|---|---|---|
| T2 受入 | 3/3 | 3/3 | **同じ** |
| T1min 受入 | 3/3 | 3/3 | **同じ** |
| T2 tokens_in | 2917 | 3403 | +486（+17%） |
| T2 tokens_out | 2624 | 3411 | **+787（+30%）** |
| T2 壁時計 中央値 | 227s | 379s | +67% |
| T1min tokens_in | 2334 | 2603 | +269（+12%） |
| T1min tokens_out | 2508 | 1639 | **−869（−35%）** |
| T1min 壁時計 中央値 | 201s | 112s | −44% |

**採用条件の判定。**

- **「T2 3/3 維持」＝必須条件は満たす。**
- **「T1min が baseline 1/3 より改善」＝測れない。** 本書が baseline として書いた 1/3 は
  過去の記録で、**今日の対照（off 腕）は 3/3** だった。対照が天井に張り付いているので、
  policy に改善させる余地が無い。この指標は設計時の前提（T1min は落ちる）が古い。

**tokens_in は policy ぶん増える**（+269〜486。policy 本文 1375 文字におよそ対応）。予想どおり。
**tokens_out は課題で向きが逆になる**——T2 は +30%、T1min は −35%。壁時計も同じ向きに動く。
policy の 7 条（返す前に検算し、未完了なら残りを述べる）が T2 で説明を増やし、
6 条（最小の変更）が T1min で出力を絞った、という読みと整合する。

### 8.0 余地のあるセル（T1 / T3）— **床が動いた**（2026-08-29）

T2 / T1min が両腕とも天井だったので、本書 §8 が認める次の段（T1 / T3 を各 3 回）を回した。
台帳: `results/archive/worker/ledger-2026-08-29-gate2hard-policy-{off,v1}-gemma4-e4b.jsonl`。

| セル | off | v1 | 読み |
|---|---|---|---|
| T2 | 3/3 | 3/3 | 天井（差が出せない） |
| T1min | 3/3 | 3/3 | 天井 |
| **T1**（実装 + テスト追加） | **0/3** | **2/3** | **動いた** |
| T3（複数成果物） | 0/3 | 0/3 | 動かず（(b) 族） |

**off の 3 本は完全に同一の誤り**である（実効 temperature 0 の貪欲デコード。
`1048576 → '1024.0 KiB'` 期待 `'1.0 MiB'` ——単位の繰り上げが 1 段足りない）。
v1 は 2 本を通し、落ちた 1 本は**別の誤り方**になった（`1024 → '1.0 MiB'` 期待 `'1.0 KiB'`
——今度は 1 段行き過ぎている）。つまり policy は「同形で固まっていた失敗」を動かしている。

**n = 3 なので率としては読まない。** 本リポジトリの読み方（n=3 同士は「全滅 ⇔ 全通」だけを
差にする）に照らすと 0/3 対 2/3 は率の証明ではない。読めるのは**存在**である——
policy 無しでは 3 本とも同じ間違いで止まったセルを、policy 有りは通した。

**コスト。** T1 の壁時計は中央値 252s → 600s（上限張り付き）、tokens_in は 6792 → 25428。
通った 2 本のうち 1 本は `mode=timeout` のまま受入だけ通っている（成果は出たが aider が
回り続けた形）——今日入れた `AGENT_MAX_TOOL_ROUNDS_WRITE` が効く場所である。

**T3 は両腕とも 0/3 で不動。** これは (b) 族（作業丸ごと欠落）で、同日の T3splitgate 実測が
示したとおり**成果物を割らないと動かない**——policy の管轄ではない。

### 8.1 ~~flag が掛かる経路と、Gate 1 で効いた経路は別である~~ — 訂正（2026-08-29）

`agents/aider.json` の `variants` を見ると、判定系の役割は**すべて `ollama-*` へ割り当てられている**
（`judge` / `filter` / `reduce` / `extract` / `evaluator` … は `ollama-json`、`split` は `ollama-list`、
`verify` は `ollama-verify`）。`--agent-policy` が載っているのは `command`（＝ aider をコード worker
として起動する経路）と `interactive` だけである。

したがって:

- **Gate 1 が測ったのは判定経路**（`AGENT_OLLAMA_SYSTEM_PROMPT` で ollama 側へ policy 文面を渡した）。
  そこでは J1 が 0/5 → 5/5 と明確に効いた。**しかしこの flag はその経路に掛かっていない。**
- **Gate 2 が測ったのは flag が実際に支配する経路**（コード worker）。そこでは受入が動かず
  （6/6 対 6/6）、コストだけが増えた。

~~**つまり「policy が悪い」ではなく「flag の置き場所が違う」**という形の結果である。~~

**この読みは間違いだった（§8.0）。** 短いセル（T2 / T1min）が両腕とも天井だったのを
「コード worker では効かない」と読んだが、余地のあるセルで測ると **T1 が 0/3 → 2/3** で
動いた。**flag は正しい経路に掛かっている**——天井のセルでは差が出せなかっただけである。

経路の対応そのもの（判定系は `ollama-*` へ行き、flag は `command` と `interactive` にしか
載らない）は事実として残る。含意が変わる: policy 文面は**両方の経路で効く**——
判定系では Gate 1 が測り（J1 0/5 → 5/5）、コード worker では §8.0 が測った（T1 0/3 → 2/3）。
判定系へ広げるかは別の提案として起こす（いまは掛かっていない）。

A/B の両腕は `--agent-policy` を必ず明示する。未指定は本番定義を継承する第三の腕になり、
どちらの対照にもならない。

時間の短い成功対照から段階的に回す。

```bash
rtk python3 tools/agent-tools/eval/worker_eval.py \
  --cli aider --model gemma4:e4b --agent-policy off \
  --tasks T2,T1min --repeat 3

rtk python3 tools/agent-tools/eval/worker_eval.py \
  --cli aider --model gemma4:e4b \
  --agent-policy gemma4-e4b-reliability-v1 \
  --tasks T2,T1min --repeat 3
```

短い gate 通過後だけ T1 / T3 を各3回実行する。各 record で以下を比較する。

- checker の pass/fail と note
- test tampering / import error / incomplete artifact
- wall time、tokens in/out、output chars
- policy ID/hash、num_ctx、num_predict、edit format

T2 3/3 維持を必須とし、T1min が baseline 1/3 より改善することを主改善指標とする。

## 9. 全回帰テスト

**状態: [~] 一部完了。** adapter 11件、shipped definition 16件、worker eval 9件の関連unit testは
計36件が通過した（内訳は `test_aider_adapter` / `test_agentcli_jsonvariant` / `test_worker_eval`）。
以下の全discover、E2E、関連engine起動形を同一revisionで完走した記録はないため、全回帰完了とはしない。

```bash
rtk python3 -m unittest discover -s tools/agent-tools/agentcore/agentcore/tests
rtk python3 -m unittest discover -s tools/agent-tools/agentcore/tests
rtk python3 -m pytest -q tools/agent-tools/eval/tests
rtk python3 tools/agent-tools/e2e/run.py --engine agent-aider
rtk node --test tools/agent-dashboard/test/agent-cli-golden.test.js
```

agent-flow / agent-loop / agent-amigos は共通 `headless_cmd` と shipped definition を読むため、関連する
起動形 golden も実行する。system 文自体の engine code への追加はないことを diff で確認する。

## 10. コミット分割

既存 unrelated diff を混ぜず、次の単位で stage する。

1. `test(agent-aider): cover fixed Gemma policy contract`
   - adapter test と最小 adapter implementation
2. `config(agent-aider): enable Gemma reliability policy`
   - shipped agent definition と definition contract test
3. `test(eval): add Aider system policy arms`
   - worker eval option、ledger、eval unit test
4. `docs(eval): record Gemma policy gates and results`
   - README と A/B report

各コミット前に `git diff --cached --check` と staged path を確認する。

## 11. 中止条件と戻し方

- Gate 0 で Aider prompt が置換・二重化されたら意味評価へ進まない。
- Gate 1 で J2 / R1 が退行したら worker full run へ進まない。
- Gate 2 で T2 が 3/3 未満なら本番既定にしない。
- 固定文の改訂は一度まで。同じ gate を最初から再実行する。
- 採用不可の場合、`agents/aider.json` の wrapper flag を外す。adapter と eval arm は再検証用に残してよい。
- 不採用を task 別 system prompt の自由文注入で回避しない。必要なら設計 Decision Record を再審議する。
