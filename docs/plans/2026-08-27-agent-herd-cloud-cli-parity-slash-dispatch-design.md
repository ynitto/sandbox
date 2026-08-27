# agent-herd — クラウド CLI を正とした入口の再構成（スラッシュ 1 語で実行形が決まる）

> 作成 2026-08-27
> 対象: `tools/agent-tools/agentcore`（`herdcli` / `agentcli` / `ollama_{loop,tui,skills}` / `harness`）・
> `tools/agent-loop`（`scheduler` / `cliprofile` / `toolloop`）・`tools/agent-flow`（`agent.py`）・
> `tools/agent-project`（`prioritize.py`）・`tools/agent-audit`（`llm.py`）・`agents/*.json` ・
> `schemas/agent-cli.schema.json` ・`tools/agent-dashboard/src/features/orchestration/main/`
> 前提: [2026-08-25 agent-herd 統一入口設計](./2026-08-25-agent-herd-unified-entry-design.md)（配布と
> ハーネスの 1 実装化）、[2026-08-26 おすすめ構成の単純化](./2026-08-26-agent-tools-recommended-setup-simplification-design.md)
> （設定 → エンジンの経路が 3 本あって交わらない、の指摘）、
> [2026-08-23 ローカル LLM 設定提案](./2026-08-23-agent-dashboard-local-llm-configuration-proposal.md)（役割×モデルの実測）
> 綴りの正典: [agent-herd 仕様書](../specs/agent-herd-spec.md)（本書が採択されたら同時に改訂する）
> 位置づけ: **提案**。§5 の抜け 4 件は本書の調査で実装から確認した現状で、提案の採否と独立に残る。

---

## 0. 結論の先出し

- **正は「クラウド CLI がどう見えるか」である。** 人が打つか engine が組むかで面を分けない
  ——claude / codex では、スラッシュコマンドは**人も engine も同じ 1 行で**投げる CLI 自身の
  コマンド面である。agent-herd もそう振る舞えばよい。
- **したがって用途（purpose）もスラッシュでよい。** `/verify` `/split` `/extract` は
  「人の軸」ではなく**コマンド面の語彙**であり、engine が prompt 先頭へ 1 行足すのは
  agent-loop の `slash:` が既にやっていること（`run_prompt`：層2 はネイティブ前置、層3 は
  スキル解決）。前稿で「purpose は人が打つ軸ではない」と分離したのは**取り違え**だった。
- **鍵は「スラッシュ行は起動前に読む」こと。** 起動形（どのハーネス・どの toolset・どの
  profile・どの候補）は argv を組む前に決まらなければならない。スラッシュ行をランチャが
  先に読み、決めたうえで**その行を CLI へ渡すか消費するかは定義が宣言する**——ネイティブの
  スラッシュを持つ CLI には残して渡し、持たない CLI ではルータの解釈が実装そのものになる。
- **語彙が 1 つになる。** `variants` のキー・`by_purpose` のキー・スキル名・スラッシュ名を
  **同じ名前空間**に置く。いま同じことを決める経路が 4 本あり（§1）、engine 側の許可リストは
  5 か所に散っている（§5 G2）。ルータ 1 実装に畳めば、engine は「用途の 1 語」を渡すだけで
  よくなり、許可リストも変種調停も engine から消える。
- **弱いモデル向けの自由度削減はこの構造の副産物である。** 先頭が `/` ならルール、そうで
  なければ推論。ツールセットの選択がモデルの判断から 1 語へ移り、未知の `/x` は明示エラーで
  止まる（黙って本文として推論へ流さない）。
- **opencode は同梱から外す**（§6）。**aider の対話は共通 TUI のバックエンドとして実装する**
  （§7）。どちらも新機構ではなく、既にあるものの置き場を変える話である。

---

## 1. 何を解くか — 同じことを決める経路が 4 本ある

「この呼び出しをどのエージェント・どの起動形で走らせるか」を決める経路が、いま 4 本ある。

| | 経路 A: 段と候補 | 経路 B: 用途別の起動形 | 経路 C: 用途別の候補 | 経路 D: コマンド面 |
|---|---|---|---|---|
| 宣言する場所 | dashboard の実行レベル → `profiles.json` → `control.json` | `agents/*.json` の `variants` | `control.json` の `selection_policy.by_purpose` | prompt 先頭の `/name` |
| 読む側 | `executionresolver.resolve_execution` | `agentcli.resolve_variant` | 同 A（`by_purpose` を引く） | `agent_loop.run_prompt` / `ollama_skills.split_leading_slashes` / `ollama_tui._LOCAL_COMMANDS` |
| 粒度 | workload | purpose | purpose | コマンド名 |
| 誰が起動できるか | engine のみ | engine のみ | engine のみ | 人・engine の両方 |
| 実測が入るか | ○ | ×（人が定義へ焼く） | ○ | × |

A と C は同じ Resolver の中で繋がっている。**繋がっていないのは B と D である。**

- **B は engine ごとに許可リストを持つ**（§5 G2）。`ollama.json` は `variants` に 15 キーを宣言しているが、
  flow は 9・project は 6・audit は 2 しか引かず、harness は許可リスト無しで直引きする。
  「宣言したのに効かない」が静かに起きる。
- **D は層ごとに別実装**である。`run_prompt` は層2 へネイティブ前置・層3 へスキル解決、
  TUI は自前のローカルコマンド表、skills は先頭スラッシュの切り出し——**3 か所**にある。
- **B と D は同じことを言おうとしている。** 「この呼び出しは verify である」を、B は
  engine の内部変数で、D は prompt の 1 行で表現している。

クラウド CLI にはこの分裂が無い。`/review` と打てば（人が打っても、上位のスクリプトが
prompt へ足しても）CLI 自身がそれを解釈し、必要ならモード・権限・読む材料を切り替える。
**面が 1 つだから分裂しようがない。**

---

## 2. 原則 — クラウド CLI が正

以後この 3 行を判断基準にする。

1. **外から見た面はクラウド CLI と同型にする。** 対話/非対話・モデル・権限・作業ディレクトリは
   CLI オプション。実行形の切り替えはコマンド面（スラッシュ）。
2. **人と engine を区別しない。** 同じ綴りが両方から使える。engine 専用の裏口を作らない。
3. **ローカル固有の事情は定義（`agents/*.json`）とルータへ閉じる。** 呼ぶ側の語彙は
   クラウド CLI と同じままにする。

---

## 3. 採用設計

### 3.1 入口の綴り

```
agent-herd                          # 対話（TUI）
agent-herd -p "…"                   # 非対話 1 回（stdin も可）
agent-herd --model gemma4:12b -p …
agent-herd --agent aider -p …       # バックエンド = agents/*.json の定義名
agent-herd --readonly -p …
agent-herd --dir PATH
agent-herd --continue / --resume ID # セッション継続（§4）
```

- `-p` / `--model` / 権限フラグ / `--continue` はクラウド CLI と同型。
- `--agent` が取るのは**定義名**（`ollama-json` のような profile 綴りも解決できる）。
  「adapter 名」という概念を外から消す。
- 既存の `aider` / `ollama` サブコマンドと `chat` / `exec` / `defs` / `harness` は
  **別名として温存**する（仕様書 §3 の綴りを壊さない。help の下段へ降ろすだけ）。
- **エンジン面は変わらない。** agent-loop / flow / project は `agents/*.json` から argv を
  組む経路（`agentcli.headless_cmd` / `interactive_cmd`）で、人が打つ綴りとは独立している。

### 3.2 スラッシュ行 — 起動前に読む 1 行

**規約**: 本文の**先頭から連続する** `/name [args]` の行はコマンド行である
（`ollama_skills.split_leading_slashes` と同じ切り出し。名前は `^[a-z0-9][a-z0-9._-]*$`
——`agent_loop.scheduler._SLASH_NAME_RE` の規約をそのまま正典にする）。

**ランチャは argv を組む前にこの行を読む。** 判定は文字列マッチだけで、LLM は 1 回も
呼ばれない。決定の順序は次のとおり。

```
先頭のコマンド行を切り出す
  ├─ ルート表に載っている名前 → 実行形を決める（ハーネス / toolset / profile / 候補）
  ├─ 載っていない & スキルが実在 → スキルとして解決（材料へ載せる）
  └─ どちらでもない        → 明示エラー（本文として推論へ流さない）
決めた実行形で argv を組む
  ├─ 定義が `slash_native: true`  → 行を残して渡す（`skill_command_prefix` で記号を差し替え。codex は `$`）
  └─ `slash_native: false`        → 行を消費する（ルータの解釈が実装そのもの）
```

`slash_native` は `agents/*.json` の新フィールド 1 つ（既定 false。`skill_command_prefix` が
既に「その CLI のスキル起動記号」を宣言しているので、その隣に置く）。

**ルート表**（`agentcore/slashroute.py` 1 実装。行き先はすべて実装済み）:

| 綴り | 決まるもの | 当て先 |
|---|---|---|
| `/sm <名前> [--param k=v]` | ステートマシン実行 | `harness/statemachine.cmd_statemachine` |
| `/edit <指示>` | 編集ハーネス（read/write/run/final + 受入ゲート） | `harness/toolloop.run_goal` |
| `/ask` `/find` | toolset（無し / read セット） | `ollama_loop.TOOLSETS` |
| `/verify` `/judge` `/review` `/split` `/extract` `/retrieve` `/plan` … | 用途 → profile と候補 | `agentcli.resolve_variant` + `executionresolver`（§3.3） |
| `/model` `/tools` `/think` `/ctx` `/status` | セッション操作 | `ollama_tui._LOCAL_COMMANDS` |
| その他 | スキル | `ollama_skills.find_skill` / `harness/toolloop._tl_resolve_skill` |

**新設は表と `slash_native` の宣言だけ**である。3 か所に分かれている解釈
（`run_prompt` の層別分岐・TUI のローカルコマンド・skills の切り出し）はこの表を引く形へ
畳み、層別分岐は `slash_native` の宣言 1 つに置き換わる。

### 3.3 語彙の一本化 — purpose はコマンド名である

`variants` のキー・`by_purpose` のキー・スキル名・スラッシュ名を**同じ名前空間**に置く。

```
                          ┌─────────────────────────────┐
  engine（flow/project/   │  slashroute.resolve(         │
  audit/loop/harness）    │    command="verify",         │   →  (agent_cli, model,
    「用途は verify だ」   │    cli=…, model=…, control=…) │        harness, toolset,
                          │  )                           │        prompt 前置の有無)
  人「/verify …」          └─────────────────────────────┘
```

- **engine は許可リストを持たない。** 用途の 1 語を渡すだけ。ルータが
  `variants`（起動形）と `by_purpose`（候補）を**1 か所で**調停する。
- 調停規則は agent-flow が既に持っているもの（`agent.py:254`）を正典に昇格させる:
  **用途別順位表（`by_purpose`）由来の決定は変種の `default_model` で上書きしない。**
  呼び出し元が明示したモデルも上書きしない。
- 未知のコマンド名は**振り替えない**（現行の「カタログに無い用途は共通 candidates へ
  フォールバック」と同じ既定）。ただし `--strict-commands` で明示エラーにできる。

これで §1 の B と D が 1 本になり、engine 側の 5 つの許可リストが消える。

### 3.4 弱いモデル向けの自由度削減

構造の副産物として次が効く。新しい仕掛けは足さない。

| いままで | これから |
|---|---|
| ツールを使うかはモデルが決める（bash ループが自分でコマンドを選ぶ） | `/ask`（道具なし）・`/find`（read セット）・`/edit`（編集ハーネス）で**人か engine が 1 語で固定** |
| ステートマシンは agent-loop の設定でしか起動できない | `/sm <名前>` の 1 語で、対話でもヘッドレスでも同じに起動する |
| 未知のスラッシュは本文として推論へ流れる | 明示エラーで止まる（層3 のスキル未解決を fail fast にしている現行方針と同じ） |
| 用途の宣言が engine の内部変数 | 実行ログに `/verify` の 1 行として残る（後から同じ条件で引き直せる） |

---

## 4. 同じ観点で見直した他の面

「クラウド CLI ならどう見えるか」で残りも棚卸しした。

| 面 | クラウド CLI | いまの agent-herd | 採る形 |
|---|---|---|---|
| 対話/非対話 | 引数なし / `-p` | `chat` / `exec` サブコマンド | §3.1 のフラグ。サブコマンドは別名で温存 |
| モデル | `--model` | 定義の `{model}` / 位置引数 | `--model`。位置引数は温存 |
| 権限 | `--permission-mode plan` / `--sandbox read-only` | `--readonly`（定義の `readonly: enforced` / `best-effort`） | 現行のまま。綴りだけ `--readonly` に統一 |
| セッション継続 | `--continue` / `--resume` | agent-loop 側の `session: keep` / `per-run`（設定ファイル） | `--continue` / `--resume` を入口へ。agent-loop の設定はその糖衣に位置づけ直す |
| 出力形式 | `-p --output-format json` | `RESULT {json}` 1 行 + `@agent-usage`（stderr） | `--output-format json` を足し、`RESULT` はその 1 形式として温存 |
| スキル | `~/.claude/skills` / `.claude/commands` | `~/.agents/skills` ほか（`install.py` が配る） | 置き場は現行。**名前空間をスラッシュと共有**（§3.3） |
| サブエージェント | `--agent` | 変種（`variants`） | `--agent` は定義名。用途の振り替えはコマンド名（§3.3） |
| MCP | `--mcp-config` | 無い | **非目標**（§8） |

**セッション継続だけは意味を確かめてから移す。** ローカルの単発実行は毎回新プロセスで、
「継続」の実体は材料の再構築である（会話を積むと文脈が太る——F4）。`--continue` を受ける
なら、実体が何かを仕様書に書く。

---

## 5. この再構成で消える負債と、残る負債

実装を洗って見つかった 4 件。**G2 と G4 は §3.3 で消え、G3 は §3.2 で消える。G1 だけは
宣言を足す作業として残る。**

### 現状の対応表（実装から確認）

| purpose | ollama | aider | flow | project | audit | harness | by_purpose |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| planner | ○ | ○ | ○ | - | - | ○ | ○ |
| plan | ○ | ○ | - | ○ | - | - | ○ |
| evaluator / filter / reduce | ○ | ○ | ○ | - | - | - | ○ |
| extract | ○ | ○ | ○ | - | ○ | - | ○ |
| judge / split | ○ | ○ | ○ | - | - | - | ○ |
| review | ○ | ○ | **-** | ○ | ○ | - | ○ |
| prioritize / route / adjudicate / assess | ○ | ○ | **-** | ○ | - | - | ○ |
| **retrieve** | ○ | **-** | ○ | - | - | - | ○ |
| **verify** | ○ | **-** | ○ | - | - | ○ | ○ |
| work / generate / classify / map / synthesize | - | - | - | - | - | - | ○ |
| statemachine | - | - | - | - | - | (引く) | **-** |

出典: `agents/{ollama,aider}.json` の `variants`、`agent_flow/agent.py:102`
（`VARIANT_ELIGIBLE_ROLES`）、`agent_project/prioritize.py:89`（`JSON_CONTRACT_PURPOSES`）、
`agent_audit/llm.py:19`（`VARIANT_PURPOSES`）、`harness/toolloop.py:833,973`（許可リスト無しの
直引き）、`agent-dashboard/.../purpose-operations.js`（`PURPOSE_OPERATIONS`）。

### G1 — aider に `verify` / `retrieve` が無い【残る】

`_tl_judge_agent`（`harness/toolloop.py:966`）は `resolve_variant(cli, "verify")` を引くが、
`aider.json` にその宣言が無い。したがって **`agent_cli: aider` の entry で
`acceptance_judge: true` にすると、作業した aider 自身（e4b・`--dry-run`）が採点する**——
仕様 §3.4 が「最も弱い構成」と呼ぶ形になる。`retrieve` も同様で、ollama 側は
「`ollama-json` へ寄せると read tool を失う」から変種を宣言しているのに、aider には
受け皿が無い。

**直し方**: `aider.json` に `"verify": "ollama-verify"` と `"retrieve": "ollama-read"` を足す。
定義をまたぐ振り替えは `"split": "ollama-list-thinking"` という前例が同じファイルにある。

### G2 — 許可リストが 5 か所に散っている【§3.3 で消える】

engine が用途の 1 語を渡すだけになり、`VARIANT_ELIGIBLE_ROLES` / `JSON_CONTRACT_PURPOSES` /
`VARIANT_PURPOSES` と harness の直引きは削除される。

### G3 — agent-loop だけ purpose を Resolver へ渡していない【§3.2 で消える】

`agent_loop/control.py:101,116,239` はすべて `_control_policy_decision()`（引数なし）で、
`by_purpose` が構造的に効かない。一方 `tuning.routine_purpose(pane_id)` は entry ごとの
purpose を既に持つ（node-budget の記帳に使用中）。コマンド行が正典になれば、entry の
`slash:` がそのまま用途の宣言になり、渡し忘れが起こらない。

harness の statemachine は `_control_policy_decision("statemachine")` を渡すが
（`harness/statemachine.py:932`）、`PURPOSE_OPERATIONS` に `statemachine` が無いので必ず
共通 candidates へフォールバックする。**`/sm` をルート表に載せる時点で、この用途を
カタログへ載せるか渡すのをやめるかを決める。**

### G4 — 変種の既定モデルが `by_purpose` の決定を上書きするガードが flow にしかない【§3.3 で消える】

| 呼び出し側 | 実装 | 挙動 |
|---|---|---|
| flow `agent.py:287` | `explicit_model` + `decision.get("purpose")` を見る | 用途別順位表の決定を守る（正しい） |
| project `prioritize.py:176` | `if variant["default_model"]: model = …` | **人が明示した設定モデルまで**上書き |
| audit `llm.py:38` | `model or variant.get("default_model")` | 明示は守るが `by_purpose` は見ない |
| harness `_tl_judge_agent:978` | `variant.get("model")` | `resolve_variant` の返却キーは `default_model` なので**常に None**（結果は変種 spec の既定に落ちるので実害は無いが、キー名が食い違ったまま） |

2026-08-26 §1.2 が「経路 B が経路 A を上書きする」と指摘した病理は、**flow だけ直っていて
他 3 か所に残っている。** ルータ 1 実装にすれば調停も 1 つになる。

---

## 6. opencode を同梱から外す

**根拠**: このハードで opencode × ローカル ollama が成立しないことは調査済みである
（[2026-08-06](./2026-08-06-opencode-ollama-cpu-inference-proposals.md) §0: prefill の大半は
opencode がエージェントハーネスとして毎リクエスト注入する固有分、目安 1〜2 万トークン）。
`agent-ollama` はその代替として生まれた。加えて opencode は「コード側に CLI 分岐を持たない」
という定義契約から最も外れている（adapter 323 行 + `tools/opencode/` 458 行 + hook plugin 15 行 +
`turnhooks.py:196,239` / `agent_audit/readers.py` の `opencode-sqlite` / `install.py` の分岐）。
`eval/recommend.py` の `_LEDGERS` にも無く、実測を持たない候補である。

**外す範囲**: 上記の定義・アダプタ・分岐と、`herdcli` の `ADAPTERS` / `ALIAS_BY_ARGV0` / HELP。
使いたい人は `agents/opencode.json` を自分で置ける（探索順 1・2 がユーザー定義を先勝ちに
する契約は既にある）。失うのは usage 実測と preflight で、README に 1 行残す。

**失うものの明示**: ①ネイティブのターン完了検知（`session.idle`）を持つ唯一のローカル候補
②会話 transcript の収集経路（`opencode-sqlite`）③別 PC の GPU 機構成の入口
（`tools/opencode/install.sh` は `--ollama-host http://gpu-pc:11434` 前提）。
①は §7 で共通 TUI に置き換わる。②は `@agent-usage` でトークンは残るが transcript は失う。
③は GPU 機を足すときに別途決める。

---

## 7. aider の対話 — 共通 TUI のバックエンドとして実装する

`aider.json` の `interactive` は `command` しか宣言しておらず、`ready_pattern` /
`busy_pattern` / `turn_completion` / `clear_command` が無い。このまま tmux から自動運転すると
「入力欄を出したまま処理する TUI では ready の消失が起きない」（`agents/README.md` の判定
優先順位）に正面から当たるうえ、**入力を受けるのが aider なのでコマンド行を先に読めない**
——§3.2 の規約が対話では効かなくなる。

したがって実装するのは aider の TUI ではなく、**共通 TUI（`ollama_tui`）の aider バックエンド**
である。この TUI は「全画面にしない＝`capture-pane` で読める」制約を最初から満たしている。

```
agent-herd --agent ollama     # 既存
agent-herd --agent aider      # 前面は同じ TUI、1 入力 = aider 1 回（--message）
```

- ready/busy 判定が**バックエンドによらず 1 つ**になる（CLI ごとの実測・宣言が要らない）
- コマンド行の規約が対話でもヘッドレスでも同じに効く
- **システムプロンプトの競合は起きない。** aider は 1 ターンごとの編集役として呼ばれる
  だけで、SEARCH/REPLACE 規約も reliability policy（`aider_adapter.py:15-27`）もそのまま
- 会話の継続は材料の機械再構築で持つ（会話を積まないので文脈が太らない — F4）

実装は 3 つ: ①`ollama_tui` からバックエンド呼び出しを 1 つの口へ剥がす
②aider バックエンド（`aider_adapter` を `--message` で 1 回呼ぶ薄い層）
③`agents/aider.json` の `interactive.command` を共通 TUI 起動へ差し替える。

---

## 8. 非目標

- **aider に bash ツールループを足すこと。** aider は自分の編集プロトコルをシステム
  プロンプトとして必ず注入し、外から差し込める枠は `--model-settings-file` の
  `system_prompt_prefix` 1 つだけで、そこは reliability policy が占有している
  （`aider_adapter.py:136` はモデルが `ollama_chat/gemma4:e4b` でなければ起動を拒否する）。
  三重ループ（aider の reflection + ハーネスの再投入 + bash ループ）は
  [2026-08-24 radical ideas](./2026-08-24-aider-gemma4-generalization-radical-ideas.md) の
  教義 D1 が避けた形であり、`write_files` の内容指紋照合（`toolloop.py:1251-1279`）という
  受入ゲートの土台も失う。
- **新しい設定ファイル面を作ること。** ルート表はコード内の定数 1 つ。人に書かせない。
- **MCP 対応。** ローカル側は持たない。要るならクラウド CLI を使う。
- **`edit` toolset の新設**（`ollama_loop.py:90` の `PLANNED_TOOLSETS`）。§7 で足りるうちは
  着手しない。
- **実測の候補同一性を変えること。** 段階 0〜2 では `(agent_cli, model)` の意味を変えない
  （§9 の受入条件）。

---

## 9. 実装計画

各段は単独で出荷・巻き戻しできる。**仕様書（`docs/specs/agent-herd-spec.md`）は正典なので、
綴りが変わる段では同じコミットで改訂する。**

| 段 | 内容 | 受入条件 |
|---|---|---|
| 0 | `aider.json` に `verify` / `retrieve` を足す（G1） | `resolve_variant("aider","verify")` が `ollama-verify` を返す。既存テスト green |
| 1 | `agentcore/slashroute.py` を新設し、3 か所の解釈（`run_prompt` の層別分岐・`ollama_tui._LOCAL_COMMANDS`・`ollama_skills` の切り出し）をそこへ畳む。**振る舞い不変** | agent-loop の `slash` 既存テストが無改変で green |
| 2 | 用途の解決をルータへ集約（G2・G4）。engine の 3 つの許可リストと harness の直引きを削除し、調停 1 実装へ | flow / project / audit の既存テスト green。`by_purpose` 由来の決定が変種既定で上書きされないことを 4 経路すべてで検証 |
| 3 | `slash_native` を定義へ足し、コマンド行の渡す/消費するを宣言で決める。`/sm` `/edit` `/ask` `/find` をルート表へ | 未知コマンドが明示エラーで止まる。層3 でスキル未解決が起動時 fail fast のまま |
| 4 | `agent-herd` のトップレベルフラグ（§3.1）。既存サブコマンドは別名で温存 | `agent-aider X` と `agent-herd aider X` の同一性テスト（`test_herdcli.Argv0DispatchTests`）が green |
| 5 | opencode の同梱解除（§6） | 削除後に `agent-herd defs` が 8 件（現行 9 件から opencode を除いた数）を返す。dashboard の golden テスト更新 |
| 6 | 共通 TUI のバックエンド分離 + aider バックエンド（§7） | tmux `capture-pane` から見た画面が ollama バックエンドと同じ規約（`ready_pattern` 共有） |
| 7 | 仕様書・README・`eval/recommend.py` の更新 | 仕様と実装の突き合わせ |

**段 6 だけは実測の扱いが変わる。** 呼び出しの形が変わるので、`ledger` に `harness` 軸を
足して T2 対照を 1 本取る（[radical ideas 案 I](./2026-08-24-aider-gemma4-generalization-radical-ideas.md)
の採用条件と同じ規律。同時に model / policy / sampling を変えない）。段 0〜5 は
`(agent_cli, model)` の意味を変えないので、既存の格付けはそのまま有効である。

---

## 10. 互換性

- **既存の設定ファイルは無改変で動く。** agent-loop の `slash:` は綴りも意味も変わらない
  （むしろ層別分岐が消えて挙動が揃う）。
- **`agent-aider` / `agent-ollama` の argv・stdout / stderr 契約は変わらない。**
  変わるのは help と、`agent-herd` 自身が受けるフラグである。
- **`agents/*.json` は追加のみ**（`slash_native` と aider の 2 キー）。既存フィールドの
  意味は変えない。
- **消えるのは engine 内部の定数と opencode 関連**で、外向きの綴りではない。

---

## 11. 未決

1. `--continue` / `--resume` の実体（材料の再構築か、CLI 側のセッション機能か）を
   バックエンドごとにどう宣言させるか。
2. `statemachine` を用途カタログ（`PURPOSE_OPERATIONS`）へ載せるか、purpose を渡すのを
   やめるか（G3 の後半）。
3. opencode の transcript 収集（`opencode-sqlite`）を失うことが agent-audit の格付けに
   与える影響。現時点では aider も transcript を持たないので、失う面は既に片肺である。
4. **仕様書の drift が 1 件ある**（本書の調査で判明・提案とは独立）。
   `docs/specs/agent-herd-spec.md` §4.0 は「同梱定義は **8 件**」と書いているが、実際は
   `vscode-copilot.json` が加わって **9 件**である。仕様書は「実装と食い違ったら、
   どちらかが間違っているので直すまで作業を止める」と宣言しているので、段 5 を待たずに
   直す。
