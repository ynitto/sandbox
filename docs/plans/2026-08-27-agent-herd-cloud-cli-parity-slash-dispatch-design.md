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
> 改訂 2026-08-28: [Gemma4 ハーネス改善案の適用性レビュー](../reviews/2026-08-27-gemma4-harness-plan-applicability-review.md)
> （以下「適用性レビュー」）の突合結果（同レビュー §4.4）を反映。本書は
> [2026-08-27 小型モデル制限付き実行案](./2026-08-27-agent-tools-small-model-harness-proposals.md)（以下「制限付き実行案」）
> の依存先を 3 か所——段 7〜8（停止理由・失敗トリアージの観測の器）、段 9b（許可外ファイル
> 変更の検知）、種別 C 宣言の frontmatter `options:`（回数上限の置き場）——で引き受ける。

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
- **最終的にスラッシュコマンドになるのは 4 種**（§3.2）。A セッション操作・B 実行形・
  C 用途（**宣言 1 枚**）・D スキル。利用者から見た綴りは 1 つで、実体だけが違う。
  新設はルータ 1 本と宣言ディレクトリ 1 つで、行き先はすべて実装済みである。
- **外部エージェントは実装を持ち込まず、規約だけ借りる**（§8.1）。Python 統一のため
  pi は却下、smolagents はプロンプト 17 KB で却下。借りるのは `llm` の Template
  （コマンド名 → system / model / tools / 出力契約の束縛。§3.3）と、mini-swe-agent の
  プロンプト外出し（`system_template` は 1 行。§3.5）の 2 つだけ。どちらも依存を増やさない。
- **対話ペインにもヘッドレスと同じ契約を付ける**（§7.2〜7.5）。いまペインに無い 4 契約の
  うち、**完了検知・受入条件・usage 実測の 3 つは「原理的に無理」ではなく配線の不在**で、
  既存の関数（`acceptance_stamps` / `acceptance_evidence_errors` / `session_log` リーダ）を
  ターン境界で回すだけで埋まる。層3 の限定ツール契約だけが設計変更で、それは §7.1 の
  共通 TUI が解く。加えてペイン経路には**失敗トリアージと quota 観測が無く**（§7.4-1）、
  quota が枯れても管理面の段判定に届いていない——これが実害としては最大である。
- **トランスポートは一本化しない。揃えるのは契約とコマンド面である**（§7.5）。
  2 面（対話ペイン / ヘッドレス）は残し、同じ宣言・同じ受入ゲート・同じ台帳がどちらでも
  効くようにする。`/sm` `/edit` を対話で打った場合はルータがヘッドレスへ回す。
- **aider はエージェントではなく編集適用エンジンで、利用者は `aider` を打たない**（§3.6）。
  実行レベルに書くのは `herd` の 1 語で、`(aider, gemma4:e4b)` への展開は Compiler が
  実測から埋める。再構成では **aider の名前を種別 C の宣言 1 行（`/edit` の `agent:`）に
  閉じる**。外すかどうかは実測待ち（§11-5）——移植対象は 657 行だが、9/9 を支えている
  曖昧一致の寛容さを実測前に捨てない。
- **git は aider に渡さず engine が使う**（§7.3 B 末尾）。`--no-git` は維持し、
  `git status --porcelain` の差分で「この実行が触ったファイルの全体」を観測する。
  ハーネスはいま git を 1 か所も使っておらず、**受入条件に書いていないファイルを勝手に
  触ったこと**を検知できない——これは対話ペインだけでなく層2 のヘッドレスにも効く制約で、
  git 差分は両方を同時に直す。
- **opencode は同梱から外す**（§6）。**aider の対話は共通 TUI のバックエンドとして実装する**
  （§7）。どちらも新機構ではなく、既にあるものの置き場を変える話である。
- **本書の 3 か所が他提案の依存先になる**（適用性レビュー §4.4）。制限付き実行案の
  「許可外ファイル変更の検知」（同案 §3.4-2）は段 9b（git 差分観測）でしか実現できず、
  停止理由・失敗トリアージの表出（同案 §3.3・§3.7）は段 7〜8 の器（classify_error
  全経路化 + session_log 正典化）に載せ、回数上限の宣言（同案 Phase 1）は種別 C 宣言の
  frontmatter `options:` か statemachine YAML の state 単位キーに置く——新フィールドは
  発明しない。観測の器と宣言の置き場を分裂させないことが、提案 3 本合流の前提である。

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

**ルート表**（`agentcore/slashroute.py` 1 実装）。**最終的にスラッシュコマンドになるものは
次の 4 種で、綴りの見え方は 1 つ・実体だけが違う。**

| 種別 | 例 | 実体 | 当て先（すべて実装済み） | 誰が用意するか |
|---|---|---|---|---|
| **A. セッション操作** | `/model` `/tools` `/think` `/ctx` `/status` `/skills` | コード内の関数 | `ollama_tui._LOCAL_COMMANDS:319` | agentcore |
| **B. 実行形** | `/sm <名前> [--param k=v]`・`/edit`・`/ask`・`/find` | ハーネスと toolset の切替 | `harness/statemachine.cmd_statemachine`・`harness/toolloop.run_goal`・`ollama_loop.TOOLSETS` | agentcore |
| **C. 用途（purpose）** | `/verify` `/judge` `/review` `/split` `/extract` `/retrieve` `/plan` … | **宣言 1 枚**（§3.3） | `slashroute` + `executionresolver` | 人・配布物 |
| **D. スキル** | `/wiki-use` `/ltm-use` … | `SKILL.md` を材料へ載せる | `ollama_skills.find_skill` / `toolloop._tl_resolve_skill` | 既存のスキル配布 |

**新設はルータ本体と `slash_native` の宣言、そして C の宣言ディレクトリだけ**である。
3 か所に分かれている解釈（`run_prompt` の層別分岐・TUI のローカルコマンド・skills の
切り出し）はこの表を引く形へ畳み、層別分岐は `slash_native` の宣言 1 つに置き換わる。

**`/find`（種別 B）と `retrieve`（種別 C）の裏側は将来差し替える**（適用性レビュー P3）。
現在は toolset を read セットへ固定するだけで、検索そのものはモデルが `read` toolset で
自走する。改善案 案 B（`read_files=` 配線）の後、read toolset の探索ラウンド消費を
eval ログで確定してから、ハーネス側の「決定的 rg → ヒット周辺チャンク → 読み材料へ
事前割付」へ opt-in で置換する。**rg パターンはモデルに書かせない**（案 C 実測:
e4b はパス・テスト名 3/3、regex 0/3）——編集対象の import / シンボルから決定的に導出する
（context_slice と同じ素材・同じ規律）。語彙（`/find` `retrieve` の綴り）は変えず、
裏側実装だけが替わる。消費が小さければ差し替えは閉じる。

### 3.3 用途コマンドの宣言 1 枚（種別 C）

**規約は `llm`（simonw/llm 0.33）の Template から借りる。コードは持ち込まない。**
`llm/templates.py` の `Template` は `name` / `prompt` / `system` / `model` / `options` /
`tools` / `fragments` / `schema_object` / `extract` を 1 枚に束ねており、これは
「**コマンド名 → システムプロンプト・モデル・ツール集合・出力契約の束縛**」そのものである。
いま `agents/*.json` の `variants` と engine 側 5 か所の許可リスト（§5 G2）へ散っているものが、
この 1 枚に畳める。

```markdown
<!-- ~/.agents/commands/verify.md -->
---
description: 受入条件を読み取り専用で判定する
agent: ollama          # 起動形（旧 variants.verify の宛先）
model: gemma4:12b      # 用途専用の既定（実測があればそちらが勝つ。下記）
tools: []              # 道具なし = 判定だけ
output: json           # 出力契約
argument-hint: "[基準ファイル]"
---
あなたは判定役です。作業した本人ではありません。
観測できたものだけで判定し、確かめられないものは fail としてください。
```

置き場は `~/.agents/commands/*.md` と `<project>/.agents/commands/*.md`。**スキル
（`~/.agents/skills/`）と同じ探索規約に揃える**——名前空間が 1 つなので、同名は
先勝ちで、`/verify` が宣言にも スキルにもある状態を作らない。

**frontmatter の `options:` は回数上限の宣言の置き場を兼ねる**（適用性レビュー P1）。
制限付き実行案 Phase 1 の「設定可能な上限」は新規実装ではなく既存機構
（`_TL_MAX_TOOL_ROUNDS` / `DEFAULT_MAX_ROUNDS` / `_MAX_REPEATS` などの分散定数）の
統一・設定化であり、宣言の置き場は statemachine YAML の state 単位キー
（`max_steps` / `check_retries` と同階層）か、この `options:` のどちらかに置く
——新フィールドは発明しない。E4B 固有の arm（`write:` を持つステート限定で上限 2〜3、
state-harness の実測 +35pp の範囲）もここで宣言する。read 系・判定系は現行上限のまま。

**`output:` は schema 制約 arm の着地点である。** GBNF / JSON Schema による tool 契約の
固定（改善案 案 G）は評価 arm のままで、実測で勝った場合にのみこの語彙へ着地する。
無条件の schema 固定はしない——format 制約が生成系タスクの推論を 10〜30% 劣化させる
報告（Tam et al.）があり、`format` 指定時に `think: false` を強制する現行実測とも同族の
問題であるため（適用性レビュー §1・§3-3）。

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

### 3.5 プロンプトをコードから外へ出す

規約は **mini-swe-agent 2.4.6** から借りる（こちらもコードは持ち込まない。効果を実測して
から vendoring を検討する）。同梱の `config/mini.yaml` は **`system_template` が 1 行**
（"You are a helpful assistant that can interact with a computer."）で、手順・規約・観測の
詰め方・書式エラー時の言い直しはすべて設定側にある。テキストベースのアクション解析
（`models/utils/actions_text.py`、70 行）は「応答にフェンスブロックを 1 つだけ出す」を
正規表現で拾う方式で、`ollama_loop._FENCE_RE` と構造が同じである。

いまの `ollama_loop.system_prompt()` はコード内の文字列なので、弱いモデル向けの調整を
実測で回せない。次の 4 つを宣言へ出す:

| 出すもの | 相当 |
|---|---|
| `system_template` | 役割 1 行 |
| `instance_template` | 手順・規約（最初の user メッセージ） |
| `observation_template` | ツール出力の詰め方（現行の `_clip` 相当） |
| `format_error_template` | 規約から外れたときの言い直し（現行の nudge） |

置き場は種別 C の宣言と同じ frontmatter に載せる——コマンドごとにプロンプトを変えられる
ことが、`/verify` と `/edit` で別の規律を課す唯一の手段になる。

**`observation_template` は制限付き実行案 Phase 2（失敗テストのみの選別注入）の着地点でも
ある**（適用性レビュー P2）。段 13 の導入までは `_sm_check_note()`（現在は check 失敗時に
実出力の末尾 2000 字を無選別注入）への**決定的パーサ 1 つ**として実装し、抽出不能時は
現行の末尾切り詰めへ fallback して省略の事実を明示する。段 13 導入後は整形を宣言側へ移す。
失敗別ルート表（同案 §7）は仕様として使い、実装は既存機構への写像（形式壊れ→nudge、
CLI・認証→classify_error env、反復→既存検知、原因不明→escalate）に留める——新分類器は
書かない。

### 3.6 aider の位置付け — 宣言 1 行の裏に隠す

**aider はエージェントではなく編集適用エンジンである。** 正典は
[2026-08-18 評価](./2026-08-18-agent-aider-improvement-assessment.md) §8.3 の分担表で、
aider が持つのは「対象ファイルが決まった局所編集」だけ。探索は agent-ollama、決定的検査は
engine、再投入は statemachine、厳密 JSON は `ollama-json` が持つ。実測の 9/9（T2/T4）は
この狭い役割での数字である。

**利用者は `aider` を打たない。** 実行レベルの構成に書くのは `herd` の 1 語で、
`(aider, gemma4:e4b)` / `(ollama, gemma4:e4b)` への展開は Compiler が実測から埋める
（`herd-family.js` 冒頭。「1 つ書かせるとどれかの用途で必ず外れる」）。台帳の鍵は
`(agent_cli, model)` のままなので、記録上の区別は残る。**agent-loop の entry へ
`agent_cli: aider` と書くのは逃げ道であって既定ではない**——この綴りを既定として紹介すると、
用途ごとの正解を人が暗記する形へ逆戻りする。

したがって本書の再構成では、**aider の名前が出る場所を種別 C の宣言 1 行に閉じる**。

```markdown
<!-- ~/.agents/commands/edit.md -->
---
agent: aider           # ここだけが aider を名指しする
---
```

将来ここを差し替えれば、編集適用の実装を変える変更は 1 行で済む。

**いま aider を外さない理由。** 移植対象の実体は `coders/editblock_coder.py` の 657 行
だけで（`search_replace.py` 757 行は `udiff_coder` 専用）、規模の問題ではない。外すと失う
ものが具体的である。

| 得る | 失う |
|---|---|
| 依存が減る（litellm 経由が消える） | **曖昧一致の階段**（`perfect_replace` → `replace_most_similar_chunk` → difflib）。弱いモデルほど効き、**9/9 はこの寛容さ込みの数字** |
| プロンプトを自分で持てる | `--dry-run`（`readonly: enforced` の根拠） |
| | analytics-log からの usage 実測 |
| | `--model-settings-file` による policy 注入（sha 固定・モデル pin） |

**プロンプトの実測**（aider 0.86.2 を計測）: `main_system` 1,056 文字 + `system_reminder`
2,165 + few-shot `example_messages` 1,658 + 我々の `POLICY_TEXT` 1,375 ＝ **約 6.3 KB
≒ 1.6k トークン**。編集ループの毎ターン再送される（F4）。smolagents の 17 KB ほどでは
ないが、mini-swe-agent の「system 1 行 + instance 2.5 KB」より重いのは事実である。

去就の判断材料は §11 未決 5 に置く。

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

## 7. 対話（インタラクティブ）の扱い

### 7.1 aider の対話 — 共通 TUI のバックエンドとして実装する

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

### 7.2 いまペインに無い契約 — 3 つは配線の不在、1 つは設計変更

ヘッドレスにあってペインに無い契約は 4 つある（すべて実装から確認）。**うち 3 つは
「原理的に無理」ではなく、既にある機構が繋がっていないだけである。**

| 契約 | ヘッドレス | 対話ペイン | 種別 |
|---|---|---|---|
| 完了検知 | 終了コード | 画面監視 or `turn_completion` hook | 配線（A） |
| 受入条件・証跡ゲート | `run_prompt` が実行 | 走らない（`acceptance` は headless 枝でしか消費されない） | 配線（B） |
| usage 実測 | `@agent-usage` を stderr から回収 | 取れない（スロット保持秒からの推定） | 配線（C） |
| 層3 の限定ツール契約 | ハーネスが read/write/run/final を供給 | 供給先が無い | **設計（D）** |

根拠: `scheduler._run_headless` だけが `run_prompt(acceptance=…)` を呼ぶ。
`toolloop._tl_record_usage:619-624` は「headless 経路は自分で subprocess を回すので、
tmux 経路と違って実測が取れる」と注記している。

`statemachine` は state ごとに argv と検査コマンドを決めるので 1 枚のペインでは表現
できない。また tmux 必須にすると `headless_pane: false` のサーバ・CI 運用が消える。
**したがってトランスポートの一本化はしない**——が、**契約は両経路で揃える**（§7.3）。

### 7.3 契約を両経路で揃える

#### A. 完了検知 — 自前 CLI にも turn hook を出させる

turn hook の封筒は既に `dispatch_id` / `generation` / `status` を HMAC 検証つきで運んで
いる（`turnhooks.record_turn_hook_event:336-375`、`version: 1`）。`ollama` の TUI は自前
なので、ターン終了時に `agent-loop hook-event --adapter ollama --status complete` を
呼べばよい（`cli.py:165` の `choices` に `ollama` を足す）。`busy_pattern` への依存が消え、
画面監視は fallback に降りる。

#### B. 受入条件・証跡ゲート — ターン境界で既存関数を回す

**ペインが劣るわけではない。** 層2 のヘッドレス（`run_cli_loop`）も「触ったファイルを
外から観測できない」ので、受入条件が名指ししたパスの指紋変化だけで判定している
（`toolloop.py:1350,1374-1377`）。**層2 は元から同じ精度**である。

```
dispatch 時   acceptance_stamps(criteria, cwd)        ← 既存関数
   ↓ send-keys
turn 完了時   acceptance_evidence_errors(...)         ← 既存関数
```

挿す場所は dispatch とターン完了の 2 点だけで、新しい判定ロジックは要らない。

残るのは判定層（judge）で、これはエージェントの**報告本文**を要する。`capture-pane` は
装飾込みで壊れやすいので、正典は `session_log` に置く——定義に既に
`session_log`（`jsonl-dir` / `kiro-sqlite` / `opencode-sqlite`）があり、agent-audit が
リーダを持っている。最後の assistant メッセージを取れば judge へ渡せる。
定義の `{output_file}`（`output: "file"` 用）を対話でも使う手もある。

##### git 差分で「触ったファイル」を観測する

**ハーネスは git を 1 か所も使っていない**（`harness/*.py` に git の呼び出しなし）。そのため
証跡は「受入条件が名指ししたパスの指紋」だけで、**受入条件に書いていないファイルを勝手に
触ったこと**は検知できない。これは対話ペインの制約ではなく、層2 のヘッドレスにも同じく
効いている制約である。

git 管理下なら解ける。

```
dispatch 前   git status --porcelain のスナップショット
   ↓ 実行（ペイン / ヘッドレスどちらでも）
完了時        差分 = この実行が触ったファイルの全体
```

- 受入条件のパス指紋は**そのまま残す**（git 管理外・未追跡ファイルのために要る）
- git 管理下では差分を上乗せし、`touched` を正確にする
- 非 git の作業ディレクトリでは現行どおり指紋のみへフォールバックする（後方互換）
- 失敗ラウンドの巻き戻し（`git checkout -- <paths>`）も同じ観測の上に載る。現在は戻していない
- **制限付き実行案 §3.4-2（許可外ファイル変更の検知）はこの観測に依存する**（適用性
  レビュー §3-9）。独立には実装できないため、同案側では段 9b の先行を待つか、停止理由から
  「範囲外変更」を一旦外す（§9 の段 7〜9b の注記を参照）

**aider に git を渡すのではない。** `agents/aider.json` は `--no-git --no-auto-commits` の
ままにする——コミットの主体が aider になると、agent-loop の worktree サンドボックス
（`sandbox.py`・`send --sandbox`）や agent-project のブランチ運用と二重になる。
**git を使うのは engine 側**で、隔離はサンドボックス、観測はこの差分が担う。

#### C. usage 実測 — `session_log` を正典にする

`agent_audit/readers.py` の jsonl-dir リーダは**既に usage を抽出している**
（`usage_by_message` / `tokens_in` / `tokens_out` / `usage_measured`）。`ollama.json` は
`session_log.usage: false` と**宣言しているだけ**なので、JSONL の `llm_end` の形を確かめて
`true` にすれば実測が入り、ペイン経路の推定（保持秒 × rates）を外せる。

**B と C は 1 実装に寄せる。** 「ターン境界で `session_log` の差分を読み、この実行の
**報告本文と usage** を返す」関数を agentcore に置き、ペインとヘッドレスの両方が同じ
ものを使う。turn hook の封筒へ `tokens_in/out` を足す案（`version: 2`）もあるが、
クラウド CLI のネイティブ hook は payload が違うので `session_log` のほうが汎用である。

#### D. 層3 の限定ツール契約 — ペインの中身を我々のものにする

2 案あるが、**D-2 が既に §7.1 にあるので D-1 は要らない。**

- **D-1**: aider 自身のコマンド語彙で縮小版を張る（`/add` `/read-only` で割付、`/run` で
  検査）。round 単位の制御は諦める
- **D-2**: 共通 TUI（§7.1）。前面が我々のものなら round 制御を我々が持てるので、契約を
  そのまま供給できる

つまり D は「ペインで供給する」のではなく「**ペインの中身を我々のものにする**」で解ける。

### 7.4 ペイン経路のその他の穴

§7.2 の 4 つ以外に、実装を洗って見つかったもの。

| # | 穴 | 実害 | 根拠 |
|---|---|---|---|
| 1 | **失敗トリアージと quota 観測が無い** | **大**。ペインで quota が枯れても node-budget 台帳に観測行が入らず、管理面の段判定が知らない＝ degrade が効かない | `classify_error` は harness だけ（`toolloop._tl_failure_hint:640-657`）。`failure_pattern` は `send --wait` だけ（`sendcmd.py:386,424`） |
| 2 | 出力契約が無い | dashboard が読む `RESULT {json}` も `empty_output_is_error` もペインには無い | spec §3.5 |
| 3 | freeze 検知が既定 off | ヘッドレスは無進捗上限が既定で効く（定義 `timeout` / 600 秒、天井 4 時間）のに、ペインは `slot_timeout_seconds: 7200` だけ。**止まったペインが 2 時間居座る** | `health.freeze_timeout_seconds` の既定 0 |
| 4 | purpose が Resolver に渡らない | §5 G3 と同根。ペイン経路も同じ | `control.py:101,116,239` |
| 5 | `no_session_args` が効かない | 使い捨て起動の宣言が対話では無視される | `agentcli._mode_args` |

**穴ではなかったもの**: `spill`（長大プロンプトの退避）。ペインは `set-buffer` +
`paste-buffer` で送るので argv 長制限を受けない（`tmux_util.py:23-34`）。

逆向きの非対称（`mode: ralph` / `external target` / `fresh_context` がペイン専用）は既知で、
ヘッドレスでは明示エラーになっている。

### 7.5 統一するのはトランスポートではなく契約とコマンド面

2 面（対話ペイン / ヘッドレス）は**残す**。揃えるのは送り方ではなく、**同じ契約・同じ
コマンド語彙・同じ宣言がどちらでも効くこと**である。

| | 対話ペイン | ヘッドレス | 揃え方 |
|---|---|---|---|
| A セッション操作 | ○ | —（意味を成さない） | — |
| B 実行形 | `/ask` `/find` は効く。`/sm` `/edit` はヘッドレスへ回す | ○ | ルータが経路を選ぶ |
| C 用途 | ○ | ○ | 宣言 1 枚を共有 |
| D スキル | ○ | ○ | 探索規約を共有 |
| 完了検知 | turn hook（→ 全 CLI へ） | 終了コード | §7.3 A |
| 受入条件・証跡ゲート | → 付ける | ○ | §7.3 B |
| usage 実測 | → 付ける | ○ | §7.3 C |
| 失敗分類・quota 観測 | → 付ける | ○ | §7.4-1 |
| 限定ツール契約 | 共通 TUI で供給 | ○ | §7.3 D |

`/sm` と `/edit` を対話で打った場合は、ルータが**ヘッドレス実行へ回して結果をペインに
出す**（`headless_pane` の既存の見せ方をそのまま使う）。人から見れば 1 つのコマンド面で、
裏で経路が分かれているだけになる。

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
- **ルート表（A / B）を設定ファイルにすること。** コード内の定数 1 つに保つ。人が書くのは
  種別 C の宣言だけ。
- **MCP 対応。** ローカル側は持たない。要るならクラウド CLI を使う。
- **`edit` toolset の新設**（`ollama_loop.py:90` の `PLANNED_TOOLSETS`）。§7 で足りるうちは
  着手しない。
- **tmux + send-keys への一本化**（§7.2）。契約 4 つが供給できないため成立しない。
- **実測の候補同一性を変えること。** 段階 0〜2 では `(agent_cli, model)` の意味を変えない
  （§9 の受入条件）。
- **エージェント実装そのものを外から持ち込むこと（§8.1）。** 借りるのは規約だけにする。

### 8.1 外部エージェントの評価と却下理由

Python 統一の制約下で 4 本を実パッケージから確認した（2026-08-27 時点）。

| | 依存 | システムプロンプト | コア | 判定 |
|---|---|---|---|---|
| **mini-swe-agent 2.4.6** | コアのみなら jinja2 + pydantic | `system_template` **1 行**、残りは YAML | `agents/default.py` 190 行 + `environments/local.py` 92 行 + `actions_text.py` 70 行 | **規約を借りる**（§3.5） |
| **llm 0.33** | click・pluggy・pydantic 他 | **組み込みなし** | — | **規約を借りる**（§3.3） |
| smolagents 1.26.0 | 多数 | `code_agent.yaml` **17 KB** / `toolcalling_agent.yaml` 10 KB | — | **却下**。opencode を退けたのと同じ prefill 問題（§6） |
| pocketflow 0.0.3 | **なし** | 無し（フレームワーク） | `__init__.py` 99 行 | 却下。agentcore が既に持つ層と重複 |
| pi（`@mariozechner/pi-coding-agent`） | Node/TypeScript | 短い | — | **却下**。Python 統一の方針に反する |
| SmallCTL | 未確認 | 未確認 | — | **却下（参考止まり）**。SLM 向け端末ハーネスとして実在するが定量評価が見つからず（紹介動画のみ）。制限付き実行案 §8（外部 OSS ハーネス不導入）とも整合（適用性レビュー §1） |

mini-swe-agent は `pip install` すると litellm・textual・datasets・typer まで引き、
`__init__.py` が import 時にバナーを print して設定ディレクトリを作る。採るとしても
**パッケージ依存ではなく約 350 行の vendoring**で、追加依存は jinja2 + pydantic に収まる。
`Model` / `Environment` は Protocol の duck typing（`Model` は 5 メソッド）なので、
`agent-ollama` をそのまま Model として差せる。

**mini-swe-agent の完了判定は `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` の自己申告**で、
受入条件の証跡ゲートを持たない。C5（done の根拠は機械検証）は agentcore 側が持ち続ける
——ここは借りられない。

---

## 9. 実装計画

各段は単独で出荷・巻き戻しできる。**仕様書（`docs/specs/agent-herd-spec.md`）は正典なので、
綴りが変わる段では同じコミットで改訂する。**

| 段 | 内容 | 受入条件 |
|---|---|---|
| 0 | `aider.json` に `verify` / `retrieve` を足す（G1） | `resolve_variant("aider","verify")` が `ollama-verify` を返す。既存テスト green |
| 1 | `agentcore/slashroute.py` を新設し、3 か所の解釈（`run_prompt` の層別分岐・`ollama_tui._LOCAL_COMMANDS`・`ollama_skills` の切り出し）をそこへ畳む。**振る舞い不変** | agent-loop の `slash` 既存テストが無改変で green |
| 2 | 用途の解決をルータへ集約（G2・G4）。engine の 3 つの許可リストと harness の直引きを削除し、調停 1 実装へ | flow / project / audit の既存テスト green。`by_purpose` 由来の決定が変種既定で上書きされないことを 4 経路すべてで検証 |
| 3 | `slash_native` を定義へ足し、コマンド行の渡す/消費するを宣言で決める。種別 B（`/sm` `/edit` `/ask` `/find`）をルート表へ | 未知コマンドが明示エラーで止まる。層3 でスキル未解決が起動時 fail fast のまま |
| 4 | 種別 C の宣言 1 枚（§3.3）を導入。`~/.agents/commands/*.md` を配り、`variants` は移行期のみ併読。**`/edit` の宣言に aider を閉じ込める**（§3.6） | `/verify` が宣言どおりの起動形になる。`by_purpose` があるときは実測が勝つ。`aider` を名指しする箇所が宣言 1 行だけになる |
| 5 | `agent-herd` のトップレベルフラグ（§3.1）。既存サブコマンドは別名で温存 | `agent-aider X` と `agent-herd aider X` の同一性テスト（`test_herdcli.Argv0DispatchTests`）が green |
| 6 | opencode の同梱解除（§6） | 削除後に `agent-herd defs` が 8 件（現行 9 件から opencode を除いた数）を返す。dashboard の golden テスト更新 |
| 7 | **ペインに失敗トリアージと quota 観測を付ける**（§7.4-1） | ペインで quota エラーが出たとき、node-budget 台帳に `quota` 観測行が入る。`errors[]` の `class` が `send --wait` 以外でも効く |
| 8 | **`session_log` を「報告本文と usage」の 1 実装にする**（§7.3 B・C） | ペイン実行の usage が推定ではなく実測で台帳へ入る。`ollama.json` の `session_log.usage` を実測確認のうえ `true` にする |
| 9 | **ペインに受入条件・証跡ゲートを付ける**（§7.3 B） | dispatch とターン完了で `acceptance_stamps` / `acceptance_evidence_errors` が回り、`verifiedBy` がヘッドレスと同じ語彙で記録される |
| 9b | **証跡ゲートへ git 差分を足す**（§7.3 B の末尾）。ペイン・ヘッドレスの両方に効く | git 管理下では `touched` が受入条件のパスに限られず、宣言外のファイル変更を検知できる。非 git では現行の指紋のみへフォールバックする |
| 10 | **自前 CLI にも turn hook を出させる**（§7.3 A） | `ollama` のターン完了がネイティブイベントで届き、`busy_pattern` は fallback に降りる |
| 11 | freeze 検知の既定と出力契約（§7.4-2・3） | 止まったペインが `slot_timeout_seconds` を待たずに検知される |
| 12 | 共通 TUI のバックエンド分離 + aider バックエンド（§7.1・§7.3 D） | tmux `capture-pane` から見た画面が ollama バックエンドと同じ規約（`ready_pattern` 共有）。層3 の限定ツール契約が対話でも供給される |
| 13 | プロンプトの外出し（§3.5） | 現行のプロンプトを宣言へ移して**出力が変わらない**ことを確認してから、調整を始める |
| 14 | 仕様書・README・`eval/recommend.py` の更新 | 仕様と実装の突き合わせ |

**段 7〜11 は対話ペインの契約を揃える塊**で、スラッシュ再構成（段 1〜6）とは独立に進む。
実害の大きい順に 7 → 8 → 9 → 10 → 11 で、7 だけは他のどれとも依存が無いので単独で出せる。

**段 7〜9b は制限付き実行案の依存先を兼ねる**（適用性レビュー §4.4）。同案 §3.3・§3.7 が
求める「停止理由・失敗トリアージを routine の結果と画面へ」は、段 7〜8 の器
（classify_error 全経路化 + session_log 正典化）に載せて運び、**別配線は引かない**——
引くと観測面がまた分裂する。同案 §3.4-2 の「許可されていないファイルを変更していないか」は、
証跡ゲートが受入条件の名指しした path しか見ないため段 9b の git 差分観測でしか検知できない。
したがって**段 9b を先行させるか、同案 Phase 1 の停止理由から「範囲外変更」を一旦外す**
のどちらかを、同案の着手前に決める。この依存により段 9b の優先度は段 9 と同時期まで上がりうる。

> **決定 2026-08-28: 段 9b を先行させる。** 理由は 3 つで、決め手は 3 番目である。
> ① 「範囲外変更」には他の実装経路が無い——証跡ゲートが見るのは「受入条件が名指しした
> パスの指紋が変わったか」で、範囲外変更に要るのは**その補集合**（名指ししていないのに
> 変わったファイル）だから、いまの機構からは原理的に出てこない。「一旦外す」は一時的な
> 簡略化ではなく、代替手段のない検査を落とすことになる。
> ② いま作るのが最も安い——段 9 で証跡ゲートを `toolloop.acceptance_outcome` の 1 実装へ
> 畳んだので、git 差分を足す場所が 1 か所しかなく、ペインとヘッドレスの両方に同時に効く。
> ③ **後から足すと測定がやり直しになる**——同案 §6 の合格条件 4 つのうち 1 つが
> 「未検証の完了や範囲外変更が増えない」である。観測が無いまま 4 arm 比較を取ると、
> 合格条件の 1 つを評価できない状態で測ったことになり、後から観測を足せば測っているものが
> 変わる。単一軸対照の規律に照らせば arm を取り直す羽目になるので、観測は arm を測る
> **前**に存在している必要がある。着手は早まっても正味では遅くなる。

**段 12 と 13 は実測の扱いが変わる。** 呼び出しの形（12）とプロンプト（13）が変わるので、
`ledger` に `harness` 軸を足して T2 対照を 1 本取る
（[radical ideas 案 I](./2026-08-24-aider-gemma4-generalization-radical-ideas.md) の採用条件と
同じ規律。同時に model / policy / sampling を変えない）。段 0〜11 は
`(agent_cli, model)` の意味を変えないので、既存の格付けはそのまま有効である。
**軸名は制限付き実行案 §3.5（実行方針別の評価）と揃える**——「制限付き」は独立の記帳系では
なく `harness` 軸の 1 値として記帳する（適用性レビュー §4.4）。同案 §6 の 4 arm 比較
（現行 / 回数制限のみ / +反復停止 / 完成候補）もこの単一軸対照の規律で測り、
「完成候補が回数制限のみに勝たなければ採用しない」ゲートは外部実測（state-harness:
凝ったハーネスは naive 上限と有意差なし）が裏書きしているため緩めない。

**段 8 は台帳の中身を変えるが、候補の同一性は変えない。** ペイン実行の usage が推定から
実測へ切り替わるので、切替の前後で同じ実行の記帳が食い違う。切替日を `ledger` に記録し、
`rates.per_cli`（時間からのトークン推定）はその CLI について外す——実測が出る CLI に
rate を残すと二重に載る（`toolloop._tl_record_usage:628-630` の注記と同じ理由）。

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
   どちらかが間違っているので直すまで作業を止める」と宣言しているので、段 6 を待たずに
   直す。
5. **aider の去就**（§3.6）。判断材料は「編集適用を自前実装（`editblock_coder` 相当の
   657 行）にしても T2/T4 が退行しないか」の 1 点で、段 12 と同じ `harness` 軸の ledger で
   測れる。退行しなければ依存とプロンプト 1.6k トークンを落とせる。**実測前に外さない**
   ——9/9 を支えているのは曖昧一致の寛容さで、それを捨てる賭けになる。
6. **`herd` の綴りの徹底**。本書の §3.6 は「利用者は `aider` を打たない」を前提にするが、
   README と設定サンプルには entry 単位の `agent_cli: aider` を紹介している箇所が残る。
   段 14 の文書更新で「実行レベルは `herd`、entry の `agent_cli` は逃げ道」と書き分ける。
