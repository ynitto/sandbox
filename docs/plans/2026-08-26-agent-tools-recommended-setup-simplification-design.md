# 実測（eval）由来の「おすすめ構成」を 1 操作でエンジンへ配る — agent-herd 設定の単純化

> 作成 2026-08-26
> 前提: [2026-08-23 ローカル LLM 設定提案](2026-08-23-agent-dashboard-local-llm-configuration-proposal.md)（宣言する 4 点）、
> [2026-08-15 候補ベース実行方針設計](2026-08-15-agent-tools-candidate-execution-policy-dashboard-design.md)（制御面 / 根拠面 / 実行面の分担）、
> [2026-08-25 agent-herd 統一入口設計](2026-08-25-agent-herd-unified-entry-design.md)（用途別起動形の profile 化）。
> 対象: `tools/agent-dashboard/src/features/orchestration/`・`tools/agent-tools/eval/`・
> `tools/agent-audit/agent_audit/qualifications.py`・`agents/*.json`。
> 位置づけ: **提案**。§2 の 3 件は本文書の調査で再現した実バグなので、提案の採否と独立に直す。

---

## 0. 結論の先出し

- **複雑さの正体は「設定項目が多い」ことではなく、設定 → エンジンの経路が 3 本あって
  交わらないことである**（§1.2）。3 本のうち dashboard から触れるのは 1 本半で、
  実測（eval）が持っている次元（`operation_class` = 役割）を運べる経路は**エンジン側の
  `variants` だけ**である。それは dashboard に口が無い。
- **いま人が踏む手順は 8 つ・3 面にまたがり、うち 3 つは GUI に口が無い**（§1.1）。
  そのうち 6 つは端末に依存しない定数で、**実測 archive から機械的に決まる**。
  人が本当に決めるのは「どのクラウド CLI を持っているか」1 点だけである。
- **提案は機構を足さない。** 「おすすめ構成」を `qualification_seed.py` と同じ決定的変換で
  **eval archive から生成される 1 個の読み取り専用データ資産**（`recommendation.json`）にし、
  dashboard に **プルダウン 1 個 ＋ 適用ボタン 1 個**の画面を足す（§3）。
  生成は eval / agent-audit 側の 1 実装のまま、dashboard は**起動口と差分プレビューだけ**を持つ
  ——「dashboard を根拠面の writer にしない」不変条件（設計 §4.2）に触らない。
- **調査中に配線の壊れが 3 件見つかった**（§2）。いずれも 2026-08-25 の profile 統一の
  取りこぼしか、その前からの残りである。とくに **B1（変種ガードが死んでいる）は
  「12b をコード worker へ流さない」という構成上の封じを無効化している**。
- 自由入力（`agent_cli` / `model` をテキストで打つ）をやめて**実在する定義からの選択**に
  変えると、B1 は禁止リストではなく**許可リストとして構成的に閉じる**（§3.4）。

---

## 1. いま何が複雑なのか

### 1.1 人が踏む手順は 8 つ・3 面にまたがる

| # | 手順 | 面 | 端末依存か | GUI |
|---|---|---|:-:|:-:|
| 1 | `ollama pull gemma4:e4b gemma4:12b` | CLI | いいえ | × |
| 2 | `bash tools/agent-tools/install.sh`（定義の鮮度） | CLI | いいえ | × |
| 3 | 実行レベルの構成（4 段 × 候補を**自由テキストで**入力） | dashboard | 一部 | ○ |
| 4 | 実行方針を「おまかせ」にする | dashboard | いいえ | ○ |
| 5 | `qualification_seed.py` で `qualifications.json` を 1 回置く | CLI | いいえ | **×** |
| 6 | `agent-audit collect && agent-audit qualify --apply` を定期化 | CLI | いいえ | **×**（collect のみ ○） |
| 7 | 同時実行数 `workloads.flow.concurrency` を 1 にする | dashboard | はい | ○ |
| 8 | CLI 単位の枠（`allocation.agents.<cli>.max_tokens`） | ファイル直接編集 | はい | **×** |

**8 手順のうち 6 つ（1・2・3 の大半・4・5・6）は端末に依存しない定数**であり、
`tools/agent-tools/eval/results/archive/` から機械的に決まる。にもかかわらず、そのうち
2 つ（5・6）は GUI に口が無く、1 つ（3）は**正解を暗記して手で打ち込む**形になっている。

手順 3 の実体は `orchTierCandidateRowHtml()` の 2 個のテキスト入力である
（`tools/agent-dashboard/src/renderer/sections/orchestration.js:399-409`。placeholder は
`claude` / `opus`）。推奨構成は候補 5 件なので、**10 個の自由テキスト欄に正解を打ち込む**
ことになる。補完も候補一覧も無く、打ち間違いは保存時に通る。

さらに**順序の罠**がある。`execution-policy.js:136-147` は「方針が使う段に候補が 1 件も
無ければ保存を拒否する」ので、**手順 4 は手順 3 のあとでなければ実行できない**。
新品の端末で最初に開いた人は「実行方針」から触るが、そこでは何も保存できない。

### 1.2 設定 → エンジンの経路が 3 本あり、交わらない

これが「わかりにくい」の中心である。

| | 経路 A（段と候補） | 経路 B（役割と起動形） | 経路 C（機能の下限） |
|---|---|---|---|
| 設定する場所 | dashboard 実行レベル / 実行方針 | `agents/*.json` の `variants` と profile の `default_model` | `flow-tiers.js` の `KIND_MIN_TIER`（ソース定数） |
| 保存先 | `profiles.json` → `control.json` | 配布物（`~/.agents/agents/`） | ソース（配布物ですらない） |
| エンジン側の読み手 | `agent_flow.agent._agent_for` / `agentcore.executionresolver` | `agentcli.resolve_variant`（`_agent_for` の**最後**） | dashboard の plan 生成（`node.tier`） |
| 粒度 | **workload 単位**（flow / project / …） | **purpose 単位**（verify / split / extract / …） | **kind 単位** |
| dashboard の口 | ○ | ×（JSON 生編集のみ） | ×（無い） |
| eval の実測が入るか | ○（`qualifications.json` 経由） | ×（人が定義へ焼く） | × |

**経路 A は役割の次元を運べない。** `executionresolver.resolve_execution()` は
`purpose_or_role` を受け取るが、`selection_policy` がある経路では**一度も使わない**
（使うのは legacy 経路の `wl.agents[purpose]` だけ — `executionresolver.py:269`）。
自動選択は `remaining[0]`、つまり **workload ごとに rank 1 位が全ノードを取る**。

**経路 B が経路 A の決定を上書きする。** `_agent_for` は Resolver の決定を受けたあと、
最後に `resolve_variant(cli, purpose)` を通す（`agent.py:272-277`）。人が明示していない
モデル（＝Resolver が選んだモデルを含む）は**変種の `default_model` で置き換わる**。

その結果、次が起きる:

```
dashboard の表示 : medium = ollama / gemma4:e4b（rank 1 位）
control.json     : {"agent_cli":"ollama","model":"gemma4:e4b", selection_policy:[…]}
purpose=verify の実行 : agent-herd ollama --format json --stall-timeout 180 gemma4:12b
```

**画面が見せている候補と、実際に走るモデルが違う。** これは設計どおりの挙動
（用途専用チューニングを自動選択で潰さない）だが、**dashboard 上にその事実がどこにも
出ない**ので、利用者から見ると「設定したのと違うものが動く」になる。

### 1.3 実測が持つ次元と、経路が運べる次元がずれている

`agent-candidate-qualifications` は `candidate=(agent_cli, model) → {operation_class → 格付け}`
であり、agent-audit の集計キーも `(agent_cli, model, operation_class)` である。
つまり**実測は最初から役割の次元を持っている**。

ところが `execution-policy-compiler.js` は、候補が持つ**どれか 1 つでも** `qualified` /
`trial` の格付けを持っていれば rank に載せ、`qualification_refs` に全部を並べるだけで、
**operation_class ごとの区別を捨てる**（`compileSelectionPolicy` に operation の入力が無い）。

現在の archive で言うと、`ollama / gemma4:e4b` は `bounded-review` が **blocked**（6 中 2）
だが、`extract` / `bounded-analysis` が qualified なので rank 1 位になり、**レビュー役にも
1 位として選ばれうる**。実際にはそのあと経路 B が `verify` を `ollama-verify`(12b) へ
振り替えるので救われている——**壊れた設計を別の壊れた設計が隠している**状態である。

---

## 2. 調査で見つかった配線の壊れ（3 件・提案の採否と独立に直す）

### B1. 変種ガードが死んでいる（12b の封じが外れている）

`profiles.js` は「変種先を汎用 tier 候補に指定できない」ガードを持つ
（`withoutVariantTargets` / `save` の例外）。その対象名は
`agents.js::variantTargetNames()` が返す。この関数は **`variants` の値に一致する
`<name>.json` が実在するドロップインだけ**を対象名にする（`list()` の `isVariantTarget`）。

2026-08-25 の profile 統一で `ollama-json.json` 等 5 ファイルを消したため、
**一致するドロップインが 1 件も無くなり、対象名は空集合になった**。再現:

```
$ KIRO_AGENTS_DIR=agents node -e '…agents.variantTargetNames({})…'
variantTargetNames: []
```

影響は 2 つある。

1. `ollama-verify` / `gemma4:12b` を **medium 段の候補として保存できてしまう**
   （保存時の例外が飛ばない）。2026-08-23 提案 §2.1 の「12b をどの tier にも書かないことで
   コード worker への流出を構成的に塞ぐ」が、**構成では塞がっていない**。
2. 旧版が保存した変種候補の移行（`save` の else 枝）も no-op になるので、
   統一前に保存した端末の `profiles.json` に残った変種候補が**掃除されずに生き続ける**。

**直し方は名前の集合を作り直すことではない。** `variants` の値は今や「base 名 + profile 名」の
綴りであり、ファイルの実在で判定するのは統一後の世界では意味を失っている。
**正しい判定は「候補の `agent_cli` は `canonical_name()` を通した正典名でなければならない」**
——つまり禁止リストではなく**許可リスト**である（§3.4 でこれを UI ごと構成的に閉じる）。

### B2. `qualification_seed.py` が非正典の `agent_cli` を書く

現在の出力（archive 実行結果）:

```
aider / gemma4:e4b        single-symbol-edit=qualified  existing-test-repair=qualified
aider / gemma4:12b        code-worker=blocked
ollama / gemma4:e4b       extract=qualified  bounded-analysis=qualified  bounded-review=blocked
ollama-verify / gemma4:12b  bounded-review=qualified  bounded-analysis=qualified   ← 非正典
```

4 件目の `agent_cli` は `ollama-verify` である（`qualification_seed.py:206`）。
2026-08-25 以降これは**エージェント名ではなく profile 名**で、台帳と格付けへ書く
`agent_cli` は `canonical_name()` を通した `ollama` でなければならない
（agent-herd 仕様 §4.0）。実害:

- `compileSelectionPolicy` は tier 候補と `(agent_cli, model)` の完全一致で照合する。
  tier 候補には正典名しか置けない（置くべきでない）ので、**この候補は永久に照合せず、
  12b のレビュー実測 6/6 は selection_policy に一度も載らない**。
- 一方で本番 receipt は正典名 `ollama` + `gemma4:12b` で記録される（`work.py:387`）。
  `agent-audit qualify` はそちらで別候補を作るので、**同じ実行系の実測が
  `ollama-verify/12b`（seed 由来）と `ollama/12b`（receipt 由来）へ割れる**
  ——profile 統一が潰したはずの偽候補が、seed 側に残っている。

直し: `_text_candidate(archive, "gemma4:12b", "ollama-verify", …)` を
`"ollama"` へ変え、用途の次元は `qualifications` のキー（`operation_class`）に任せる。

### B3. 文書が約束している「12b → e4b の縮退順」が実体として存在しない

`agents/ollama.json` の verify profile の hint と 2026-08-23 提案 §4.2.4 は
「selection_policy では `retry_limit=1` と候補順 12b → e4b がこの縮退基準の表現」と書く。
しかし同提案 §2.1 は「12b をどの tier にも書かない」と決めている。
**tier 候補に無い候補は `candidatesWithinCeiling` に現れず rank に載らない**ので、
**その候補順は構成上つくれない**。現在この縮退が効いているとすれば、それは
selection_policy ではなく `agents/ollama.json` の `errors[].class: transient` と
再投入であって、文書が指している場所ではない。

正典を片方に寄せる必要がある。§3.5 に選択肢を置く。

---

## 3. 提案 — 「おすすめ構成」を実測から生成される 1 個のデータ資産にする

### 3.1 守る不変条件（新機構を足さないための枠）

| 不変条件 | 出典 | 本提案での扱い |
|---|---|---|
| エンジンは `profiles.json` / `qualifications.json` を読まない | schemas/README | 変えない（推奨も control へコンパイルしてから届く） |
| 根拠面の writer は agent-audit だけ | 候補実行方針設計 §4.2 | 変えない（dashboard は**起動口だけ**持つ） |
| インストーラは制御面を書かない | 2026-08-23 §2.5 | 変えない（配るのは**読み取り専用の推奨**まで） |
| 用途の次元は `operation_class` が持ち、`agent_cli` へ畳まない | agent-herd 仕様 §4.0 | 強化する（B2 を直す） |
| 「model / harness / sampling を同時に変えない」 | local-first 計画 | 推奨は**実測済みの組み合わせだけ**を運ぶ |

### 3.2 新しい契約 `agent-recommendation`（読み取り専用・配布物）

`schemas/agent-recommendation.schema.json` を足し、実体は
`agents/` と同じ経路で配る（`~/.agents/recommendation.json`）。制御面ではないので
インストーラが書いてよい。中身は 5 ブロック:

```jsonc
{
  "version": 1,
  "generated_at": "2026-08-26T00:00:00Z",
  "source": { "kind": "eval-archive", "revision": 1,
              "ledgers": ["worker/ledger-2026-08-14-text-eval-gemma4-e4b.jsonl", "…"] },

  // (1) 実行レベルの候補表。埋まらない段は「枠」で出す（人が選ぶのはここだけ）
  "tiers": {
    "basic":  { "order": 0, "candidates": [{ "agent_cli": "ollama", "model": "gemma4:e4b" }] },
    "small":  { "order": 1, "candidates": [{ "agent_cli": "aider",  "model": "gemma4:e4b" },
                                           { "agent_cli": "ollama", "model": "gemma4:e4b" }] },
    "medium": { "order": 2, "slots": [{ "requires": "cloud-standard" }] },
    "large":  { "order": 3, "slots": [{ "requires": "cloud-premium"  }] }
  },

  // (2) 実行方針プリセット
  "execution_policy": { "mode": "auto" },

  // (3) 端末資源の宣言（ローカル候補があるときだけ）
  "control": { "workloads": { "flow": { "concurrency": { "max_runs": 1, "workers": 1 } } } },

  // (4) 適格性 seed（qualification_seed.py の出力そのもの。第 2 実装を作らない）
  "qualifications": { "version": 1, "revision": 1, "candidates": [ … ] },

  // (5) 前提条件（適用前の点検に使う）
  "requires": {
    "models": ["gemma4:e4b", "gemma4:12b"],
    "agent_defs": { "ollama": "<sha256>", "aider": "<sha256>" },
    "entrypoint": "agent-herd"
  },

  // (6) 1 行ずつの根拠（画面にそのまま出す）
  "evidence": [
    { "path": "tiers.basic.candidates[0]", "why": "extract 6/6・bounded-analysis 6/6",
      "ledger": "worker/ledger-2026-08-14-text-eval-gemma4-e4b.jsonl" },
    { "path": "tiers.small.candidates[0]", "why": "single-symbol-edit 9/9・existing-test-repair 9/9",
      "ledger": "worker/ledger-2026-08-14-followup-arms-gemma4-e4b.jsonl" },
    { "path": "!tiers.*.candidates[aider/gemma4:12b]", "why": "code-worker 6/18・wall 600 で 0 完走",
      "ledger": "worker/ledger-2026-08-14-code-arms-gemma4-12b-wall600.jsonl" }
  ]
}
```

**`slots` が要点である。** クラウド候補だけは実測ではなく「その端末が何を契約しているか」で
決まるので、推奨は**枠として宣言し、値を持たない**。人が決めるのはここだけになる。

### 3.3 生成側 — `tools/agent-tools/eval/recommend.py`（第 2 実装を作らない）

`qualification_seed.py` と同じ archive・同じ決定性（`--generated-at` 固定で出力も同じ）で
`recommendation.json` を出す。**(4) のブロックは `qualification_seed.build_seed()` を
そのまま呼ぶ**——格付けの生成は 1 実装のままにする。tier 表と evidence は、seed が
すでに持っている `status` から機械的に導く:

| 導出規則 | 根拠 |
|---|---|
| `code-worker` 系が `qualified` の候補 → `small` | 成果物を作る kind は `KIND_MIN_TIER` で small 以上 |
| text 系が `qualified` かつ p50 が最小の候補 → `basic` | basic は「短い一手順で完結する」kind 圏 |
| どこかで `blocked` かつ `critical_failure_risk=1` の候補 → **どの段にも入れず** evidence に理由を残す | 12b コード worker の封じ |
| クラウド候補 → `slots`（実測が無いので値を持たない） | seed に載らない＝`unknown` は自動選択されない |

CI で `recommend.py` の出力と `agents/*.json` の突き合わせをテストする
（例: 推奨が挙げた `agent_cli` が全部 `canonical_name()` を通ること = **B2 の再発防止**）。

### 3.4 適用側 — dashboard に「おすすめ構成」1 画面

全体設定の先頭に置く。**入力は 1 個、ボタンは 1 個。**

```
おすすめ構成（実測 2026-08-26 版）

  点検
    ✓ agent-herd 1.x が入っています
    ✓ agents/ollama.json が配布版と一致（2026-08-25）
    ✗ agents/aider.json が古い（--agent-policy を持ちません）  → [install.sh を実行]
    ✓ gemma4:e4b   ✗ gemma4:12b が未取得  → [ollama pull]

  クラウド枠（この端末で使うものを選んでください）
    標準 (medium)  [ claude / sonnet     ▼ ]     ← 検出された CLI から選ぶ
    高性能 (large) [ claude / opus       ▼ ]

  適用されるもの（現在 → 推奨）
    実行レベル 単純作業   （未設定） → ollama / gemma4:e4b     根拠: extract 6/6
    実行レベル 軽量       （未設定） → aider / gemma4:e4b …    根拠: 9/9
    実行方針             （未設定） → おまかせ
    同時実行数            2 → 1                                 理由: local-llm 同時 1
    適格性               （未設定） → 4 候補を seed
                                        [ 適用 ]
```

適用が行うこと（順序も固定する。§1.1 の順序の罠を消す）:

1. `profiles.save()` — tiers（slots は選択で埋めてから）
2. `executionPolicy.save()` — `mode: auto`（1 のあとなので候補未設定で弾かれない）
3. `control.saveControl()` — `workloads.flow.concurrency`
4. **`agent-audit seed --from-recommendation <path>` を起動する**（新サブコマンド）
5. `profiles.apply()` — `selection_policy` のコンパイル

4 が肝である。**dashboard は `qualifications.json` を書かない。** agent-audit に
「推奨の (4) ブロックを検証して置く」だけの入口を足し、writer を 1 つに保つ
（既存の `collect` ボタンと同じ形。2026-08-23 §2.5 が「入れるなら起動口だけ」と
残していた案 (b) そのもの）。

**自由テキストをやめる。** 既存の実行レベル画面の 2 個のテキスト入力は、

- エージェント: `agents.list()` の**正典名だけ**を並べた `<select>`
- モデル: 選んだエージェントの `default_model` ＋ 推奨と現行 qualifications に現れる model の
  `<datalist>`（自由入力は残すが候補が出る）

に変える。これで **B1 は禁止リストを直さなくても構成的に閉じる**——選べないものは
保存されない。`variantTargetNames()` は残すが、判定を「実在ファイル」から
「`canonical_name()` と一致しない綴り」へ変える（旧 `profiles.json` の掃除にはまだ要る）。

### 3.5 役割の次元をどう戻すか（§1.2・§1.3・B3 への回答）

3 案ある。**採るのは案 2 で、案 3 は前提が揃うまで着手しない。**

| | 案 1: 何もしない | **案 2: 経路 B を推奨が所有し、画面に出す** | 案 3: `selection_policy` を operation 別にする |
|---|---|---|---|
| 変えるもの | — | 推奨に `agents/*.json` の版を含め、画面へ「役割別の実効」を読み取り専用で出す | `agent-control` v2 → v3、compiler、Resolver、全 Adapter |
| 役割の割当を決めるのは | `variants`（見えない） | `variants`（**見える**） | `selection_policy`（実測が直接効く） |
| 画面と実行の食い違い | 残る | **消える**（画面が変種先とモデルを表示する） | 消える |
| B3 の縮退順 | 表現できない | `errors[].class` と再投入が正典だと**文書を実装に合わせる** | `selection_policy` で本当に表現できる |
| コスト | 0 | 小（表示と配布の版だけ） | 大 |

案 2 の具体形: `orchestration:overview` に `agents.list()` から導いた
**役割 → 実効 (agent_cli, profile, model)** の表を足し、実行レベル画面の下に出す。

```
役割別の実効起動形（agents/*.json の宣言。実行レベルの選択より後に効きます）
  verify    → ollama (verify)        gemma4:12b   ← 段の選択に関わらずこれで走ります
  split     → ollama (list)          gemma4:e4b
  extract   → ollama (json)          gemma4:e4b
  retrieve  → ollama (read)          gemma4:e4b
  work      → 段の候補そのまま
```

これだけで「設定したのと違うものが動く」は消える。**書き換えの口は出さない**
（変種は実測でチューニングされた既定で、GUI から触らせると 2026-08-23 §5 の
「12b の縮退基準を設定でいじらない」が破れる）。

B3 は**文書側を直す**: `agents/ollama.json` verify profile の hint から
「selection_policy では retry_limit=1 と候補順 12b → e4b がこの基準の表現です」を削り、
実際の表現である「`class: transient` による再投入 1 回 ＋ 2 回連続で e4b へ」に書き換える。

なお `compileSelectionPolicy` には**独立に必要な誠実さの修正**がある: 現状は
「どれか 1 つでも qualified なら rank に載る」ので、`ollama/gemma4:e4b` が
`bounded-review` blocked のまま rank 1 位になる。最小の直しは
**候補行に qualified な operation_class の一覧を残し、画面に出す**こと（選択の挙動は
変えない。変えるのは案 3 の仕事）。

---

## 4. 段階

| 段 | 内容 | 依存 | 大きさ |
|---|---|---|---|
| **P0** | B1・B2 の修正と回帰テスト（`variantTargetNames` の判定を正典名基準へ / seed の `agent_cli` を `ollama` へ / CI で「推奨の agent_cli が全部正典名」を縛る） | なし | 小 |
| **P1** | `recommend.py` と `agent-recommendation` スキーマ。CLI だけで完結（`--print-diff` で現状との差分を出す） | P0 | 中 |
| **P2** | `agent-audit seed --from-recommendation`（writer を 1 つに保ったまま GUI から呼べるようにする） | P1 | 小 |
| **P3** | dashboard「おすすめ構成」画面（点検・slots・差分・適用）＋ 実行レベル入力の select 化 ＋ 役割別の実効表（案 2） | P1・P2 | 中 |
| **P4** | B3 の文書修正と、`selection_policy` 候補行への operation_class 一覧 | P0 | 小 |

P0〜P2 だけでも手順は 8 → 3 に減る（`ollama pull` / `install.sh` / `recommend → seed`）。
P3 で 8 → **1 画面 ＋ 2 個のプルダウン**になる。

## 5. before / after

| | いま | P3 後 |
|---|---|---|
| 触る面 | CLI・dashboard・テキストエディタの 3 面 | dashboard 1 面（点検が CLI 実行を案内する） |
| 自由入力 | `agent_cli` / `model` を 10 欄 | クラウド枠 2 個の選択 |
| 正解の出どころ | 設計文書を読んで暗記 | `recommendation.json`（実測 archive から生成） |
| 順序 | 実行レベル → 実行方針（逆にすると保存できない） | 1 ボタン（順序は実装が持つ） |
| 12b がコード worker へ流れない保証 | **無い**（B1 で封じが外れている） | 候補が選択式なので構成的に不可能 ＋ 適格性が `blocked` |
| 画面と実行の一致 | verify は画面と別モデルで走る | 役割別の実効表に出る |

## 6. しないこと

- **推奨をインストーラが制御面へ書くこと。** 配るのは読み取り専用の `recommendation.json` まで
  （端末ごとの実測と枠に依存する値を配布物に焼かない — 2026-08-23 §5）。
- **dashboard を `qualifications.json` の writer にすること。** 起動口だけを持つ。
- **`variants` を GUI から編集させること。** 実測チューニングを画面から壊せるようにしない。
- **推奨に「未測定の面」を入れること。** `coverage.json` が `missing` の面
  （amigos 全面・project 9 面・dashboard の残り 8 面）は推奨に登場させない。
- **`selection_policy` を operation 別にすること（案 3）を今やること。** 前提は
  「purpose → operation_class の対応が 1 実装で決まっていること」で、いまそれは
  `nodecontract` の機械判定に部分的にしかない。

## 7. 再評価条件

- **B2（MoE の 32 GB 実測）が通ったら**: `recommend.py` の入力 archive が増えるだけで、
  tier 表も画面も変わらない（この構成を選ぶ利点）。
- **`coverage.json` の missing が埋まったら**: 該当面を推奨の対象へ足す。
  基準線なしにローカルへ移さない規律は不変。
- **推奨の適用後に park が続く端末が出たら**: 原因は seed の `unknown`（クラウド候補は
  receipt が貯まるまで park する — 仕様どおり）か slots の選択ミス。点検画面に
  「park している workload と理由」を出す方が、推奨の値をいじるより先である。

## 8. 参照

- [2026-08-23 agent-dashboard からのローカル LLM 設定提案](2026-08-23-agent-dashboard-local-llm-configuration-proposal.md)（宣言する 4 点・§2.5 の経路一覧）
- [2026-08-15 候補ベース実行方針と dashboard 設計](2026-08-15-agent-tools-candidate-execution-policy-dashboard-design.md)（§5.2 Resolver・§6 契約・§4.2 writer の不変条件）
- [2026-08-25 agent-herd 統一入口設計](2026-08-25-agent-herd-unified-entry-design.md) / [agent-herd 仕様書](../specs/agent-herd-spec.md) §4.0（profile と正典名）
- `tools/agent-dashboard/src/features/orchestration/main/{agents,profiles,execution-policy,execution-policy-compiler,flow-tiers}.js`
- `tools/agent-tools/agentcore/agentcore/{agentcli,executionresolver}.py`
- `tools/agent-flow/agent_flow/agent.py`（`_agent_for` の変種上書き）
- `tools/agent-tools/eval/qualification_seed.py`・`tools/agent-tools/eval/coverage.json`
