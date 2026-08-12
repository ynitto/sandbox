# agent-aider Gemma 4 system policy 実装計画

- 日付: 2026-08-13
- 状態: Ready for implementation
- 設計正典: `docs/plans/2026-08-13-agent-aider-gemma4-system-policy-design.md`
- 設計コミット: `f9a66ad`
- 実装方式: Python `unittest` + pytest-cov、Red-Green-Refactor の垂直スライス

## 1. 完了条件

実装完了はコードが書けた時点ではなく、次をすべて満たした時点とする。

1. `agent-aider --agent-policy gemma4-e4b-reliability-v1 --model ollama_chat/gemma4:e4b ...`
   が fixed policy を Aider の `system_prompt_prefix` へ一度だけ注入する。
2. wrapper 専用 option は Aider argv へ漏れない。
3. policy 未指定時の既存 usage 変換と argv 透過は変わらない。
4. 未知 policy、model 不一致、外部 model-settings 競合を黙って無効化しない。
5. analytics log と policy settings の一時ファイルが全終了経路で削除される。
6. `agents/aider.json` が Gemma 4 policy を本番既定として宣言する。
7. `worker_eval` が off / v1 を同じ条件で比較し、policy ID/hash を ledger に残す。
8. 変更した adapter の C1 分岐カバレッジが 100%である。
9. deterministic judge A/B と Aider worker A/B の採用ゲートを満たす。

## 2. 現在地と前提

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
