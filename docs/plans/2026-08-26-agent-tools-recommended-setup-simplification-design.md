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
  **B2 だけは §3.5 を採っても必須**である（直さないと 12b の実測が永久に照合しない）。
- **設定する人が打つのは `herd` の 1 語である**（§3.5）。`aider` / `ollama` / `opencode` も
  `gemma4:e4b` / `gemma4:12b` も一度も打たない——正しい組み合わせは用途ごとに違い、
  実測がそれを知っている。自由入力 10 欄が **4 行・実質 2 つの選択**（ローカルを使うか /
  クラウドのどれを使うか）になる。
- **いまの tier 軸は二重の仕事をしている**——①予算に応じた段（本来の仕事）と
  ②どのエンジン・どのモデルか（実測が持つべき仕事）。**解決は用途軸を GUI へ足すことでは
  なく、tier 軸から②を取り上げること**である（§3.5.4）。
- **`by_purpose` の追加は純粋に additive で、呼び出し側の変更はゼロ**である
  （3 アダプタとも既に `purpose_or_role` を Resolver へ渡している。Resolver 側は約 5 行）。
  これで **B1 は直す必要そのものが消え、B3 は本当に表現できるようになる**（§3.5.6）。

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
    実行レベル 単純作業   （未設定） → herd                 展開: ollama/e4b
    実行レベル 軽量       （未設定） → herd                 展開: aider/e4b・ollama/e4b・ollama/12b
    実行方針             （未設定） → おまかせ
    同時実行数            2 → 1                              理由: local-llm 同時 1
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

- エージェント: `herd`（ローカル実行系）＋ 検出したクラウド CLI を並べた `<select>`
- モデル: **空欄が既定**（＝実測に任せる）。記入したときだけその 1 つに縛る（§3.5.1）

に変える。これで **B1 は禁止リストを直さなくても構成的に閉じる**——選べないものは
保存されない。`variantTargetNames()` は残すが、判定を「実在ファイル」から
「`canonical_name()` と一致しない綴り」へ変える（旧 `profiles.json` の掃除にはまだ要る）。

### 3.5 GUI は `herd` 1 語にする — 用途はエンジンが申告し、モデルは実測が決める

**この節は 2026-08-26 の設計対話で全面的に書き直した。** 初稿は「案 2（`variants` を画面に
読み取り専用で出す）を採り、案 3（`selection_policy` を operation 別にする）は前提が
揃うまで着手しない」としていたが、**前提は既に揃っていた**（3 アダプタとも
`purpose_or_role` を Resolver へ渡している）。初稿の見送り判断が誤りである。

#### 3.5.1 設定する人が打つもの

```
実行レベルの構成
  単純作業  [ herd   ]  [        ]   ← モデル欄は空
  軽量      [ herd   ]  [        ]   ← モデル欄は空
  標準      [ claude ]  [ sonnet ]
  高性能    [ claude ]  [ opus   ]
```

- **`agent_cli` は `herd` の 1 語。** `aider` / `ollama` / `opencode` を人が選び分けない。
- **モデル欄は空でよい。** 空 = 実測が用途ごとに選ぶ。**記入 = その 1 つに縛る**
  （12b を pull していない端末など、意図的に狭めたいとき）。正しいモデルは用途ごとに
  違う（抽出は e4b・レビューは 12b・コード編集は aider の e4b）ので、1 つ書かせると
  どれかの用途で必ず外れる。
- **クラウドは従来どおり具体名。** herd を通らず（herd 設計 §1）、実測も無く、何を
  契約しているかは人しか知らない。

自由入力 10 欄が **4 行・実質 2 つの選択**（ローカルを使うか / クラウドのどれを使うか）になる。

#### 3.5.2 `herd` 一族は機械的に導ける（新しい宣言を足さない）

| 定義 | `command[0]` | `relative_cost` |
|---|---|---:|
| `aider` / `ollama` / `opencode` | **`agent-herd`** | 0 |
| `claude` / `codex` / `copilot` / `cursor` / `kiro` | 素の CLI | 1 |

`command[0] == "agent-herd"` が一族の定義で、`relative_cost: 0` と完全に一致する。
`herd.json` を作る必要も、定義へ family フィールドを足す必要も無い。

#### 3.5.3 誰がいつ決めるか — 3 層に割れる

「実行直前で判断する」は 2 つの決定に割れ、片方は実行直前にできない。

| 決定 | 判断に要るもの | 決める場所 | 現状 |
|---|---|---|---|
| ① どのアダプタ・どのモデルか | **実測**（qualifications） | **compiler（管理面）** | 用途を捨てている（要修正） |
| ② どの起動形（argv）か | `agents/*.json` だけ | **エンジン・実行直前** | `resolve_variant()` で**既に動いている** |

①を実行直前へ持っていけないのは、`executionresolver` が
「**エンジンは agent-candidate-qualifications を読まない**」不変条件を持つためである
（docstring に明記。読ませると管理面と実行面の分離が壊れる）。したがって:

```
GUI          : 一律 herd（モデル欄は空）
コンパイル時  : herd 一族を用途別に展開 →（agent_cli, model）のランキングを control へ焼く
実行直前     : resolve_variant が profile（argv の形）を決める   ← 既存のまま・変更なし
```

#### 3.5.4 用途の軸は既に管理面にある

`flow-tiers.js` の `KIND_MIN_TIER`（kind → 最低段）が、まさに用途軸のカタログである。
GUI に出していないだけで、概念は既にある。ここへ **用途 → 必要な `operation_class`** を
1 表足せば、利用者からは見えないまま用途軸が使える。

**つまり解決は「用途軸を GUI へ足すこと」ではなく「tier 軸から候補選択の仕事を取り上げる」
ことである。** いまの tier 軸は ①予算に応じた段（本来の仕事）と ②どのエンジン・どの
モデルか（実測が持つべき仕事）を二重に担っている。②を外せば tier は予算の軸に戻る。

#### 3.5.5 実測プロトタイプ（2026-08-26 実行）

GUI は上の 4 行、`qualifications.json` は現 archive の seed（B2 修正後）、用途 →
operation_class は仮カタログ。**12b を軽量段へ入れてよくなった**点だけが従来と違う。

```
purpose    必要な用途                内部で選ばれる順                            上位段へ昇格
─────────────────────────────────────────────────────────────────────────────────────
work       single-symbol-edit,…      aider/gemma4:e4b
generate   single-symbol-edit        aider/gemma4:e4b
verify     bounded-review            ollama/gemma4:12b
judge      bounded-review            ollama/gemma4:12b
extract    extract                   ollama/gemma4:e4b → ollama/gemma4:12b(trial)
evaluator  bounded-analysis          ollama/gemma4:e4b → ollama/gemma4:12b
reduce     constrained-summary       ollama/gemma4:e4b(trial) → …
planner    planner                   —                                        はい（claude/sonnet）
split      bounded-proposal          ollama/gemma4:e4b(trial) → …
```

これは 2026-08-23 提案 §1 の「実測で確定している役割×モデル」と**一致する**。人が暗記して
打ち込んでいた対応表が、`herd` の 1 語から機械的に出る。`planner` がクラウドに残るのも、
ローカル候補に planner の裏付けが無いから**自動でそうなる**だけである。

#### 3.5.6 B1 と B3 が消える

- **12b を tier から締め出す必要がなくなる。** `work` / `generate` は
  `single-symbol-edit` の裏付けが無いので**構成ではなく実測で**選ばれない。死んでいた
  ガード（B1）を直す必要そのものが消える（それでも B2 は必須——直さないと 12b の実測が
  永久に照合しない）。
- **「12b → e4b の縮退順」が本当に書ける**（B3）。`bounded-analysis` の列がそれである。

#### 3.5.7 変更点（GUI 変更は tier 行の入力欄だけ・契約バージョン据え置き）

| # | ファイル | 内容 | 規模 |
|---|---|---|---|
| 1 | `execution-policy-compiler.js` | `herd` 一族の展開 ＋ `by_purpose` を**追加で**出す（`candidates` は残す） | 中 |
| 2 | `executionresolver.py` | 自動選択で `by_purpose[purpose_or_role]` を優先、無ければ従来の `candidates` | **約 5 行** |
| 3 | `executioncontract.py` | `by_purpose` の任意フィールド検証 | 約 10 行 |
| 4 | `flow-tiers.js` の隣 | 用途 → `operation_class` カタログ | 小 |
| 5 | `qualification_seed.py` | B2 修正（必須） | 1 行 |

`selection_policy_errors()` は未知キーを弾かないので**純粋に additive**である。version 2 の
まま、古い読み手は無視し、新しい読み手だけが使う。**呼び出し側の変更はゼロ**
（3 アダプタとも既に `purpose_or_role` を渡している）。

#### 3.5.8 踏む地雷（判明している 4 つ）

1. **`control.json` の legacy 欄へ `herd` と書いてはいけない。** `load_cli("herd")` は
   `AgentCliError: agents/herd.json が見つかりません` で落ちる。`selection_policy` を
   読まない version 1 経路がそこを見るので、**legacy 欄には展開後の rank 1 を書く**。
2. **`candidatesWithinCeiling()` は `agent_cli` と `model` の両方を必須にしている**
   （片方欠けで候補を捨てる）。`herd` 行は family として**先に展開**してから渡す。
3. **未 pull のモデルが選ばれ得る。** `resolve_execution` の `unavailable` 引数
   （実行時 availability の除外）は**あるが誰も埋めていない**。`ollama list` を流し込む
   口が要る（§3.2 の `requires.models` 点検と対になる）。
4. **未登録の用途は従来の挙動のまま**にする（`by_purpose` に無ければ `candidates` へ
   フォールバック）。そうしないと実測の無い用途が一斉に park する。カタログが埋まった
   用途から順に効く opt-in 展開になる。

#### 3.5.9 台帳と格付けの中では区別を残す

**人・GUI・エンジンからは `herd` 1 つ。台帳と格付けの中では `aider` / `ollama` /
`opencode` の区別を残す。** `qualifications` の鍵は `(agent_cli, model)` で、
`(herd, gemma4:e4b)` へ畳むと aider のコード編集 9/9 と ollama のテキスト抽出 6/6 が
同じ候補に混ざる。しかも **aider と ollama の差は用途の次元ではなくハーネスの次元**
（single-shot ＋ 4 ツール契約 / tool-loop ＋ bash 無制限）なので、`operation_class` では
分離できない。2026-08-25 の「用途を `agent_cli` へ畳むな」は正しいが、**この 2 つは
用途違いではない**ので同じ理屈で畳めない。

本当に 1 定義（`herd.json` ＋ profiles: aider / ollama / opencode）にしたければ、
`agent-candidate-qualifications` の鍵へ profile 次元を足す契約変更が要る。**いまは不要**。

#### 3.5.10 後片付け（この変更が開けるもの）

`by_purpose` が入ると、compiler と `variants` はほぼ同じモデルを言うようになる。
そうなれば **`resolve_variant` のモデル上書きは外せる**——変種の役目は
「起動形（argv）の振替」だけで足りる。それが §1.2 で見つけた「画面と実行が食い違う」の
根本的な後片付けである。移行中は**両者が一致することをテストで縛る**。

#### 3.5.11 外から見た agent-herd（コンセプト 3 行）

1. **agent-herd は人・GUI・エンジンから見て 1 つの実行系**である。中の aider / ollama /
   opencode と起動形は意識しない（記録の中だけ区別が残る）。
2. 外部が渡せるのは **対話/非対話・処理種別・ポリシー**の 3 軸で、すべて既定を持つ。
   既定は人が定義に焼いた値ではなく**実測から決まる**。
3. GUI はどれも指定しない。**用途は呼び出し側が申告し、モデルとアダプタは実測から
   管理面が決め、起動形は実行直前に定義が決める。**

3 軸のうち **2 つは既に実装済み**である（実測 2026-08-26）:

| 軸 | 現状 | 既定 |
|---|---|---|
| 対話 / 非対話 | **あり**（`agent-herd chat` / 定義の `interactive`） | 非対話 |
| **処理種別** | **あり**（`--purpose`）→ 実行直前に variant を解決 | base 起動形 |
| ポリシー | **aider adapter 専用**（`--agent-policy`・ID は現在 1 つ） | 定義の `write_args` |

```
$ agent-herd defs ollama                    → profile=(base)  model=gemma4:e4b
$ agent-herd defs ollama --purpose verify   → profile=verify  model=gemma4:12b
$ agent-herd defs ollama --purpose split    → profile=list    model=gemma4:e4b
$ agent-herd defs ollama --purpose extract  → profile=json    model=gemma4:e4b
```

残るギャップは 2 つ: (a) `--purpose` は `defs` / `exec` にしかなく、**エンジンは `exec` を
使わない**（仕様 §4.2）ため、エンジン経路では agent-flow 側の `resolve_variant` が同じ
ことを別の場所でしている——1 実装へ寄せる。(b) ポリシーが herd レベルでなく aider 専用
なので、`--purpose` と同じ層へ上げる。


## 4. 段階

| 段 | 内容 | 依存 | 大きさ |
|---|---|---|---|
| **P0** | **B2 の修正**（seed の `agent_cli` を正典名へ）と回帰テスト（CI で「推奨・seed の `agent_cli` が全部正典名」を縛る）。B1 は §3.5 を採るなら消えるので、採らない場合だけ直す | なし | 小 |
| **P1** | **用途別コンパイル**: `by_purpose` の追加（compiler・Resolver 約 5 行・契約検証）＋ 用途 → `operation_class` カタログ。**GUI 変更なし**で先に効かせられる | P0 | 中 |
| **P2** | **`herd` family の展開**（GUI の tier 行に `herd` の 1 語を書けるようにする。legacy 欄には展開後の rank 1 を書く——§3.5.8-1） | P1 | 中 |
| **P3** | `recommend.py` と `agent-recommendation` スキーマ（CLI だけで完結・`--print-diff`） | P1 | 中 |
| **P4** | `agent-audit seed --from-recommendation`（writer を 1 つに保ったまま GUI から呼べる） | P3 | 小 |
| **P5** | dashboard「おすすめ構成」画面（点検・slots・差分・適用）＋ 役割別の実効表 | P2・P4 | 中 |
| **P6** | 後片付け: `resolve_variant` のモデル上書きを外す（§3.5.10）／B3 の文書修正 | P1 | 小 |

**P1 は単独で価値が出る**（GUI を触らずに、いまの設定のまま用途別の割当が実測どおりになる）。
P2 まで進むと、人が打つのは `herd` の 1 語になる。

## 5. before / after

| | いま | P2 後（GUI） | P5 後（全部） |
|---|---|---|---|
| 打つもの | `agent_cli` / `model` を自由入力 10 欄 | **`herd` 1 語 ＋ クラウド 2 行** | 同左（点検と差分つき） |
| 触る面 | CLI・dashboard・テキストエディタの 3 面 | 同左 | dashboard 1 面 |
| 正解の出どころ | 設計文書を読んで暗記 | 実測（qualifications） | `recommendation.json` |
| 順序 | 実行レベル → 実行方針（逆にすると保存できない） | 同左 | 1 ボタン（順序は実装が持つ） |
| 役割ごとの割当 | workload 単位（rank 1 位が全役割を取る） | **用途ごとに実測で決まる** | 同左 |
| 12b がコード worker へ流れない保証 | **無い**（B1 で封じが外れている） | **実測で不可能**（裏付けが無い） | 同左 |
| 画面と実行の一致 | verify は画面と別モデルで走る | 画面がモデルを名指ししないので食い違い自体が消える | 役割別の実効表で確認できる |


## 6. しないこと

- **推奨をインストーラが制御面へ書くこと。** 配るのは読み取り専用の `recommendation.json` まで
  （端末ごとの実測と枠に依存する値を配布物に焼かない — 2026-08-23 §5）。
- **dashboard を `qualifications.json` の writer にすること。** 起動口だけを持つ。
- **`variants` を GUI から編集させること。** 実測チューニングを画面から壊せるようにしない。
- **推奨に「未測定の面」を入れること。** `coverage.json` が `missing` の面
  （amigos 全面・project 9 面・dashboard の残り 8 面）は推奨に登場させない。
- **`agent-candidate-qualifications` の鍵へ profile 次元を足すこと。** `herd` を台帳の中でも
  1 つに畳みたくなったときだけ必要で、いまは不要（§3.5.9）。
- **`by_purpose` を未登録の用途へ強制すること。** カタログに無い用途は従来の
  `candidates` へフォールバックする（§3.5.8-4）。実測が無い用途を一斉に park させない。
- **`resolve_variant` のモデル上書きを P1 と同時に外すこと。** 一致をテストで縛ってから、
  別段（P6）で外す（§3.5.10）。

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
