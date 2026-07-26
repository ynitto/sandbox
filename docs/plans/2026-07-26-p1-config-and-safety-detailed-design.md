# P1 詳細設計: 効かない設定・安全性の 5 件

ステータス: 実装済み（詳細設計 + 実装で確定した差分を §9 に反映）
入力: [`2026-07-26-open-items-and-concerns.md`](2026-07-26-open-items-and-concerns.md) §7.2 /
§6.1-5・§6.1-6・§6.1-7 / §6.2
参照: [P0 詳細設計](2026-07-26-p0-pre-canary-fixes-detailed-design.md)（構造テストの流儀・除外リストの作法） /
[S1 詳細設計](2026-07-26-s1-config-two-layer-detailed-design.md) §3.3（文言カタログ） /
[S4/S5 詳細設計](2026-07-26-s4-s5-review-and-verification-detailed-design.md) §4（verifier） /
[S6/S7 詳細設計](2026-07-26-s6-s7-backlog-planning-detailed-design.md) §5（墓標） /
[S8/S9-4 詳細設計](2026-07-26-s8-s9-4-board-ui-and-doctor-chat-detailed-design.md) §5.2（指示ドロップ） /
[S9 詳細設計](2026-07-26-s9-agent-cli-layer-detailed-design.md)（agent CLI レイヤ）
実装フェーズ: R1（実機 canary）と**並行可**。5 件とも実機 3 台を要さない。

---

## 1. スコープ

やること（総覧 §7.2 の 5 行）:

| # | 直すもの | 何が壊れているか | 規模 |
|---|---|---|---|
| P1-1 | 組み込み検証プロンプトの入力欠落 | スキル未導入ノードで副作用制約が**黙って落ちる** | S |
| P1-2 | agent-project の argv 退避欠落 | プロンプト肥大で `kiro` が E2BIG → verifier 全滅・plan 空振り | S |
| P1-3 | host.yaml トップレベルの無検査 | 綴り間違い・層違い・型違いが**警告ゼロで無視**される | S |
| P1-4 | ノード宛て指示の debounce / `.err` 掃除 | 手置きが即 `.err`・`.err` が無限に溜まる | S |
| P1-5 | `revive` の charter スコープ無視 | 複数 charter 運用で意図しない墓標解除 | S |

やらないこと（スコープ外）:

- **契約（スキーマ）の破壊的変更**。本設計が触る契約は 2 件とも additive:
  `backlog-verifier` の入力 JSON への `side_effects_text` 追加（省略時は従来動作）と、
  `agent-node-command` の**振る舞い**（debounce・`.err` 掃除）で、ファイル形は変えない。
  `CONTRACT_VERSION` は据え置く。
- P2（契約の一本化・§7.3）。`DIFF_CRITERION` の重複解消は P2-5 の担当で、
  本設計は**同型の重複を新たに増やさない**ところまでを担う（§3.1.2）。
- P3-3（doctor への検査移植）。P1-3 は検査を**純関数として切り出す**ところまでを行い、
  doctor から呼ぶのは P3-3。
- 墓標の自動失効・一括 revive（§3 の P3-b。契機待ち）。

**本設計で新たに見つけたもの**は §7 にまとめ、どの P1 項目へ織り込んだかを対応付ける。
うち §7-A は**総覧 §7.2 P1-2 の指示どおりに実装すると検証が壊れる**という前提の訂正で、
§3.2 で採る方式が総覧の記述と意図的に異なる。

---

## 2. 現実装の事実（実測・2026-07-26）

### 2.1 P1-1: 検証プロンプトの 2 経路

`build_verifier_prompt`（`verify.py:299-314`）は スキル → 組み込み の 2 段:

| | スキル（`.github/skills/backlog-verifier/scripts/prompt.py`） | 組み込み（`verify.py:278-296`） |
|---|---|---|
| `acceptance` + `DIFF_CRITERION` | ○（番号付き） | ○（番号付き） |
| `side_effects`（副作用制約） | ○ `_SIDE_EFFECTS[workspace\|network]`（`prompt.py:22-34`） | **×** |
| `rules`（恒常ルール） | ○ | **×** |
| `repo_context`（repo-map 抜粋） | ○ | **×** |
| `recipes`（過去に効いたコマンド） | ○ | **×** |
| `feedback`（前回の失敗） | ○ | **×** |
| `task.why / desc / scope / out_of_scope` | ○ | **×**（title のみ） |
| `workspace.url / branch / base / path` | ○ | **×** |
| 出力契約（末尾 JSON・証跡必須・件数） | ○ | ○ |

`verifier_input`（`verify.py:257-275`）は上記を**すべて組み立てている**。落ちているのは
組み込み側の読み出しだけで、入力の生成は共通。つまり「スキルが見つからない／実行に
失敗した」ときだけ、安全設定（`verify_side_effects`）と品質材料が黙って消える。

スキルの解決は `find_skill_script`（`verify.py:226-254`）で、プロジェクト →
git root → `~/.agents/skills` → `~/.kiro/skills` → `skill-registry.json`。
実運用（リポジトリ内で動かす）ではまず見つかるので、組み込み経路を通るのは
「配布先ノードにスキルを入れ忘れた」「上位に置いた差し替えスキルが落ちた」ときだけ
——つまり**壊れているのに気づきにくい経路**に安全制約が乗っていない。

**テストは逆**（§7-I・実測で確認）: `_shared.py` が中立な一時 cwd へ `chdir` し
エージェントホームも隔離するため、`find_skill_script` は**リポジトリのスキルを
見つけない**。既存テスト `test_commands.py:1049` が実際に見ていたのは組み込み側で、
「スキルと同じ制約が載るか」を確かめるにはスキルの `prompt.py` を**パス直指定**で
走らせる必要がある。

### 2.2 P1-2: argv 退避の有無

| ツール | 呼び出し口 | argv 長超過時 | 上限の出どころ |
|---|---|---|---|
| agent-flow | `agent.py:592-609` | 一時ファイルへ退避 + 参照渡し | 設定 `argv_limit`（既定 100000）・`--argv-limit`・doctor 検査あり |
| agent-amigos | `agentcli.py:111-123` | 同上（`_spill_prompt`） | 定数 `DEFAULT_ARGV_LIMIT = 100000` |
| **agent-project** | `prioritize.py:532-568` | **無防備** | 設定キー自体が無い |

`_agent_cmd`（`prioritize.py:145-156`）は `agentcli.headless_cmd` を呼ぶだけの薄い皮で、
`_run_agent_cli` はその argv を `subprocess.run` へ渡す。プロンプトが `ARG_MAX` を超えると
`subprocess.run` が `OSError: [Errno 7] Argument list too long` を投げる。

- 既定 CLI の `kiro` は `prompt_via: argv`（`agents/kiro.json`）。`copilot` も argv。
  `claude` / `codex` / `cursor` / `ollama` は stdin なので影響を受けない。
- 影響: `run_verifier`（`verify.py:461-`）は `except Exception` で全基準 `unverifiable`、
  `rank_agent` は `None` を返して決定的フォールバック。**LLM を呼べていないのに動いて見える**。
- さらに `OSError` の文言は失敗トリアージの env パターン（`command not found` /
  `No such file or directory`）に掛からないので「内容の問題」に分類され、
  タスクのリトライ予算を焼く（§7-F）。
- スキーマ `agent-cli.schema.json` の `prompt_via` 説明は
  「argv 長制限を超えるプロンプトは一時ファイル退避（参照渡し）に自動で切り替わる」と
  宣言しており、agent-project だけがこの契約を満たしていない。

プロンプトは S5/S6 で明確に肥大した（verifier 入力 = repo 文脈 + rules + レシピ +
feedback、planner 入力 = charter 全文 + 既存タスク + 墓標）。**P1-1 は組み込み側の
プロンプトをさらに太らせる**ので、P1-2 を先に入れる（§6）。

### 2.3 P1-3: host.yaml の検査範囲

`_validate_layers`（`configfile.py:305-345`）が見るのは 3 つだけ:

1. プロジェクト yaml のキー（`HOST_ONLY_KEYS` / `_REMOVED_WORKTREE_KEYS` /
   `_INERT_PROJECT_KEYS` / 未知キー警告）
2. `host.defaults`（`SHARED_KEYS` 以外はエラー E2）
3. `projects[].overrides`（同上）

**host.yaml のトップレベルは誰も見ていない**。`HostConfig.__init__`
（`resident_cli.py:109-153`）は `data.get(...)` で知っているキーだけを拾い、残りは
黙って捨てる。結果:

| 書いたもの | 現在の挙動 |
|---|---|
| `plan_review: false`（トップレベル） | 無視（`defaults:` の下でないと効かない）。警告ゼロ |
| `nodeid: pc-a`（`node_id` の綴り間違い） | 無視 → ホスト名から自動採番。警告ゼロ |
| `state_worktree_dir: …`（廃止キー） | 無視。プロジェクト yaml 側には fail-fast があるのに非対称 |
| `agent_cli: codex`（スカラ） | **`["c","o","d","e","x"]` として板へ publish**（§7-C） |
| `tags: urgent`（スカラ） | 同上 `["u","r","g","e","n","t"]` |
| `projects[].config: …` | 無視（S1 §3.3 の E6 は未実装・§7-J） |

`PROJECT_ONLY_KEYS`（`configfile.py:295`）は定義とテスト（`test_config.py:541`）に
しか使われておらず、「host.yaml に書いたらエラー」という宣言上の役割を果たしていない。

### 2.4 P1-4: ノード宛て指示の取り込み

`_ingest_node_commands`（`resident_cli.py:340-400`）とプロジェクト側
`ingest_commands`（`commands.py:760-924`）の差:

| | プロジェクト側 | ノード側 |
|---|---|---|
| 読めないファイルの猶予 | `cfg.watch and cfg.debounce > 0` なら mtime から `debounce` 秒待つ（`commands.py:783`） | **無し**（即 `.err`） |
| 成功時の古い `.err` 掃除 | `_clear_rejected_commands`（`commands.py:690-716`・id 一致） | **無し** |
| 受理レシートの掃除 | `_cmddrop.write_receipt` 内で prune | 同左（**溜まらない**——総覧の指摘は `.err` のみが正） |
| 失敗の journal 記録 | `_reject_command` が journal へ | **無し**（JSON 不正のときだけ `status.record_error`・§7-E） |
| `.err` の期限切れ掃除 | 無し（成功時の id 一致掃除のみ） | 無し。gc tick も `~/.agents/commands/` を見ない |

`.err` は dashboard の失敗バナーの根拠（`node-commands.js:118-126` が
`*.json.err` を読む）なので、消し方は「成功で消す」以外に無い＝溜まる一方。

**順序の制約**: 指示ファイル名は時刻順（`node-commands.js:56-60`）で、常駐体は
`_cmddrop.pending` の名前順に処理する。同じ公示への「入札 → 中止」が入れ替わると
中止済みの板へ入札を書くことになる（`node-commands.js:53-55` のコメントが警告）。
**素朴な `continue` 型 debounce はこの入れ替えを起こす**（§7-D）。

### 2.5 P1-5: 墓標の charter スコープ

| 操作 | charter の扱い |
|---|---|
| 追記 `append_tombstone`（`charter.py:868-891`） | `(指紋, charter)` 単位。同じ指紋でも charter が違えば別行 |
| 読み `load_tombstones(cfg, charter)`（`charter.py:836-866`） | 指定 charter + **タグ無し**に絞る |
| 照合 `tombstone_hit` / `similar_tombstones` | 上で絞った集合に対して行う |
| **削除 `remove_tombstone(cfg, title)`（`charter.py:893-913`）** | **指紋一致行を charter 無関係に全削除** |

`cmd_revive`（`commands.py:368-393`）は `--charter` を受け取らない
（`cli.py:262-265` は `title` のみ）。複数 charter 運用で同名タスクを別 charter でも
却下していると、片方を revive したつもりで**両方が復活**する。

---

## 3. 設計

### 3.1 P1-1 — 組み込みプロンプトをスキルと同じ入力で組む

#### 3.1.1 方針: 「スキルの有無で安全制約が変わらない」を不変条件にする

組み込みは**最小限**という現在の位置づけ自体は変えない（育てる場所はスキル側）。
変えるのは「最小限に**入れてよい**もの」の線引きで、**安全制約は最小限の内側**にする
——検証はリトライで何度も走るので、副作用制約が落ちた回数だけ副作用が累積する。
品質材料（rules / repo_context / recipes / feedback）も同じ経路で落ちているが、
そちらは「載せない理由が無い」から載せる（入力は既に `verifier_input` が組んでいる）。

#### 3.1.2 副作用制約の文言をどこに置くか

素直に `verify.py` へコピーすると `DIFF_CRITERION` と同じ 2 箇所重複を新たに作る
（総覧 §6.2 が「片方だけ直すと黙ってずれる」と指摘した形）。**本体を正典にし、
スキルは受け取る**——P2-5 が `DIFF_CRITERION` に対して採る方針の先取りで、
今回は additive に:

```python
# verify.py — 副作用の許容範囲（設定 verify_side_effects）の正典。
# スキルへは spec["side_effects_text"] として**解決済みの文**を渡す。スキル側は
# 受け取った文を優先し、無ければ自前の表へ落ちる（旧版スキルとの後方互換）。
VERIFY_SIDE_EFFECT_RULES = {
    "workspace": "作業ツリーの中だけで完結させてください。…（現行スキルの文言をそのまま移す）",
    "network":   "作業ツリーの中の変更と、読み取りのためのネットワーク到達まで可。…",
}

def verify_side_effect_rule(value: str) -> str:
    return VERIFY_SIDE_EFFECT_RULES.get(str(value or "workspace"),
                                        VERIFY_SIDE_EFFECT_RULES["workspace"])
```

`verifier_input` へ 1 キー追加（`side_effects` はそのまま残す＝スキルの契約を壊さない）:

```python
        "side_effects": str(getattr(cfg, "verify_side_effects", "workspace") or "workspace"),
        "side_effects_text": verify_side_effect_rule(getattr(cfg, "verify_side_effects", "")),
```

スキル `prompt.py` の 1 行:

```python
    side = str(spec.get("side_effects_text") or "").strip() or \
        _SIDE_EFFECTS.get(str(spec.get("side_effects") or "workspace"), _SIDE_EFFECTS["workspace"])
```

`_SIDE_EFFECTS` はスキル側に**残す**（スキルは単体でも動く契約なので、入力に無ければ
自分で決められる必要がある）。ずれても本体の値が勝つので、実害のある重複ではなくなる。
`SKILL.md` の入力表へ `side_effects_text` を 1 行追記する。

#### 3.1.3 組み込みプロンプトの構成

スキルの `build_prompt` と**同じ節・同じ順**にする（人が読み比べたときに差分が
「文章の丁寧さ」だけになる形）。節の中身は `spec` から機械的に組む:

```
[原則]  実行結果が根拠 / 直さない / {side_effects_text}
## タスク            id・title・why・desc・scope・out_of_scope（あるものだけ）
## 検証する場所      url・branch（base）・path
## 受入基準          acceptance + DIFF_CRITERION（番号付き）
## 参考: 過去に有効だった検証コマンド   recipes（最大 10）
## 前回の失敗        feedback
## リポジトリの文脈  repo_context
## プロジェクトの恒常ルール  rules
## 出力              末尾 JSON の形・件数・証跡必須・unverifiable の扱い
```

空の節は落とす（スキルの `_block` と同じ）。**節見出しの文字列はスキルと揃える**
——検証レポート（`verification_report_md`）に出る本文を人が読み比べるとき、
経路によって見出しが違うと「別の検証をした」ように見える。

#### 3.1.4 再発防止（構造テスト）

`_builtin_verifier_prompt` に「入力の項目を 1 つ足したが組み込みに載せ忘れる」が
再発しないよう、P0-4 と同じ流儀の**到達検査**を置く（§5.1）:

`verifier_input` が返す全キーに識別可能な番兵を入れた spec を組み立て、
組み込みプロンプトに**すべての番兵が現れる**ことを検査する。載せない項目は
理由付きの除外リストへ登録する（現時点の除外は `side_effects` の 1 件——
値そのものではなく `side_effects_text` の文が載るため）。

### 3.2 P1-2 — argv 退避を agent-project にも入れる

#### 3.2.1 **`headless_cmd(spill_path=)` は使わない**（総覧 §7.2 の記述からの逸脱・§7-A）

総覧 P1-2 は「`headless_cmd` の spill 経路」を配線せよと書いているが、これは
**別物**で、そのまま使うと検証が壊れる:

- `headless_cmd(spill_path=…)`（`agentcli.py:239-286`）は退避時に権限フラグを
  `spill.args` で**置き換える**。`kiro.json` の `spill.args` は `--trust-tools=fs_read`
  で、ヘッドレス書き込みモードの `--trust-all-tools` を消す。
- 目的が違う: 定義側の `spill` は「kiro-cli が positional プロンプト併用時に stdin を
  読まない癖」と、dashboard の**読み取り専用**診断（大きなスナップショットを
  読ませるだけ）のための機構。**コマンドを実行して確かめるのが仕事の verifier**が
  これを通ると、fs_read しか許されず全基準 `unverifiable` に倒れる。
- agent-flow / agent-amigos が**定義の spill を使わず自前で退避している**のは、
  この違いを踏まえた選択（両者のコメントが「定義側の spill は別物」と明記している）。

したがって採るのは flow / amigos と同じ **ad-hoc 退避**（本文をファイルへ出し、
プロンプトを短い参照指示に差し替えるだけ。**権限フラグには触らない**）。

#### 3.2.2 退避の実装は 1 か所へ（3 つ目のコピーを作らない）

同じ 12 行が flow・amigos に既にある。3 つ目を書かず `agentcore.agentcli` へ寄せる:

```python
def spill_prompt(prompt: str, limit: int, *, prompt_via: str, prefix: str,
                 instruction: str) -> "tuple[str | None, str]":
    """argv 長制限を超えるプロンプトを一時ファイルへ退避し、(退避先, 短い指示) を返す。

    退避が要らなければ (None, prompt)。**権限フラグは触らない**——ここが見ているのは
    OS の ARG_MAX であって CLI の癖ではない（定義の `spill` は後者で、退避時に権限を
    fs_read へ絞る。実行して確かめる用途のヘッドレス呼び出しに使うと検証が成立しない）。
    instruction は `{file}` を含む呼び出し側の文（役割ごとに「何の全文か」が違う）。
    """
```

呼び出し側の instruction は現行の文言をそのまま渡す（flow=「依存タスクの成果物を含む
タスク全文」・amigos=「役割・ミッション・新着メッセージ」・agent-project は
「この処理の入力の全文」）。**定義の `spill.instruction` へ寄せるかは P2-5 の決着に回す**
（§7-B）——今回それをやると flow / amigos の既存テストが固定している文言まで動く。

#### 3.2.3 agent-project 側の配線

`_agent_cmd` の**戻り値の形は変えない**（`(argv, stdin, out_file)` の 3-tuple は
`test_resident.py` / `test_config.py` の 10 か所が参照している）。退避は
`_run_agent_cli` で行う——flow / amigos と同じ位置:

```python
    cli, model_ov = _agent_for(purpose)
    plug = load_agent_plugin(cli)              # ← _agent_cmd も同じキャッシュを引く
    spill, prompt = _agentcli.spill_prompt(
        prompt, _agent_argv_limit(), prompt_via=plug["prompt_via"],
        prefix="agent-project-prompt-",
        instruction="この処理の入力の全文は一時ファイル {file} にあります。"
                    "まずこのファイルを読み込み、その内容を対象にしてください。")
    cmd, stdin_text, out_file = _agent_cmd(cli, model_ov or model, prompt)
    …
    finally:
        if spill: os.remove(spill)             # 既存の out_file 掃除と同じ finally
```

掃除は `subprocess.run` を囲む `finally`（タイムアウト・例外でも消える）。
`out_file` の掃除は成功後の読み出しがあるので現状の位置のまま——**2 つの一時ファイルの
寿命が違う**ことをコメントに残す。

#### 3.2.4 上限値の設定キー

`argv_limit` を agent-project にも持たせる（flow と同名・同既定 100000）:

- `CONFIG_DEFAULTS["argv_limit"] = 100000`
- **層は `SHARED_KEYS`**。`ARG_MAX` は OS とシェルの事情でノードごとに違ってよく、
  「違っても実行の意味が変わらない」（SHARED の条件そのもの）。判断や収束条件ではない。
- `Config` フィールド + `build_config` で `_ARGV_LIMIT`（`prioritize.py` のモジュール
  大域）へ確定する。`_AGENT_CLI` / `_AGENT_TIMEOUT` と同じ流儀——`_run_agent_cli` は
  `cfg` を受け取らない free 関数なので大域が要る。**フィールドも持つ**ので P0-4 の
  存在検査に除外を足さずに済む（`journal_*` のように大域だけにすると除外が増える）。
- CLI フラグは足さない（`agent_timeout` と同じく設定ファイル専用。実行のたびに
  変えたい値ではない）。
- `_agent_argv_limit()` は flow と同じく「0 以下なら組み込み既定へ戻す」。

#### 3.2.5 失敗トリアージへの追記

退避が入っても、退避閾値より OS の上限が小さい環境（Windows 系）では E2BIG が残る。
`_AGENT_ERROR_PATTERNS` の env 分類へ `Argument list too long` / `E2BIG` を足す
（§7-F）。分類が env になると、タスクのリトライ予算を焼かずに人へ倒れる。

### 3.3 P1-3 — host.yaml のトップレベルを検査する

#### 3.3.1 純関数 + 呼び出し側 2 者

```python
# resident_cli.py
HOST_TOP_KEYS = frozenset({
    "schema_version", "node_id", "defaults", "projects", "repos", "tags", "agent_cli",
    "board", "board_workdir", "amigos_bus", "amigos_config", "budget", "update",
    "availability", "residency",
})
HOST_PROJECT_KEYS = frozenset({"name", "state_repo", "branch", "root", "overrides",
                               "board_workdir"})

def host_config_findings(data: dict) -> "list[str]":
    """host.yaml の綻びを人が読む文の列にする（**判定だけ・出力はしない**）。

    純関数にするのは読み手が 2 人いるため: 起動時の警告（load_host_config）と
    doctor（P3-3）。同じ規則を 2 実装にすると、doctor が緑なのに起動時に警告が出る
    （またはその逆）という、いちばん人を混乱させる形になる。"""
```

- 警告のみ（fail-fast にしない）。既存運用の host.yaml に未知キーが残っている PC を
  **一斉に起動不能にしない**——canary 明けに E 系へ昇格するかを判断する（総覧 §7.2）。
- S1 の文言カタログへ **W5（host.yaml の未知キー）/ W6（型が宣言と違う）** を登録する。

#### 3.3.2 検査項目

| # | 契機 | 文言（要旨） |
|---|---|---|
| W5-a | `HOST_TOP_KEYS` に無く、`CONFIG_DEFAULTS` にも無いキー | 「未知のキー（無視します）。綴りを確認してください」＋近い既知キーの提示（`difflib.get_close_matches`） |
| W5-b | `SHARED_KEYS` のキーがトップレベルにある | 「`defaults:` の下（またはプロジェクト×ノードなら `projects[].overrides:`）へ書いてください。トップレベルでは効きません」 |
| W5-c | `PROJECT_ONLY_KEYS` のキーがトップレベルにある | 「プロジェクトの合意なので状態リポジトリ直下の agent-project.yaml へ書いてください」 |
| W5-d | `HOST_SOURCED_KEYS`（`update_*` / `state_repo` 等）がトップレベルに素で書かれている | 「`update:` マッピング配下 / `projects[].state_repo` が置き場です」 |
| W5-e | `_REMOVED_WORKTREE_KEYS` がトップレベルにある | プロジェクト yaml 側と同じ移行案内（ただし警告どまり） |
| W6-a | `tags` / `agent_cli` にスカラ | 「1 要素の配列として読みます（`tags: [urgent]` と書いてください）」＋**スカラは 1 要素へ畳んで救済**（§7-C） |
| W6-b | `projects` / `repos` が配列でない・`defaults` / `budget` / `update` / `availability` が mapping でない | 「無視します」＋期待する形 |
| W7 | `projects[]` の要素に `HOST_PROJECT_KEYS` 外のキー | 「無視します」。`config` は S1 §3.3 E6 の案内文（設定は状態リポジトリ直下へ）を出す（§7-J） |

#### 3.3.3 スカラ救済を「無視」ではなく「畳む」にする理由

`tags: urgent` / `agent_cli: codex` は現在**文字ごとの配列**になり、板へ
`["c","o","d","e","x"]` として publish される。板の入札選別は fail-close
（`agentcore/board.py:104`）なので、症状は**誤動作ではなく無言の不参加**——
「なぜかこの PC だけ仕事を取らない」という、いちばん追いにくい形で出る。

無視（空配列）でも文字分解よりましだが、書き手の意図は明白なので畳んで救済する
（`_normalize_host_repos` が旧 mapping 形式を受けるのと同じ流儀）。**畳んだことは
必ず警告する**——黙って直すと「配列で書かなくても動く」という別の思い込みを作る。

#### 3.3.4 警告の出し方

`load_host_config` は CLI の実行のたび（子プロセスも含む）呼ばれる。**プロセス内で
同じパスについて 1 度だけ**出す（`_warn_legacy_config_locations` と同じ流儀の
モジュール大域セット）。常駐体では起動時の 1 回に収まり、`serve` のバナー直後に出る。

### 3.4 P1-4 — 指示ドロップの土台を揃える

#### 3.4.1 `agentcore.commands` へ 3 つ足す

「スコープに依存しない土台だけを置く」という同モジュールの方針の内側に収める
（語彙＝何を実行するかは足さない）:

```python
def pending(dir_path, *, debounce_sec: float = 0.0, now: "float | None" = None,
            stop_at_deferred: bool = False) -> "list[str]":
    """取り込み待ちの指示。debounce_sec > 0 なら「まだ読めない かつ 更新から
    debounce_sec 以内」のファイルを今回は返さない（書きかけを .err へ飛ばさない）。

    stop_at_deferred=True では、猶予したファイルより後ろを**その回は返さない**。
    ノードスコープの指示は同じ公示への「入札 → 中止」が順序を持ち、飛ばして後続を
    処理すると中止済みの板へ入札を書く（ファイル名の時刻順＝処理順という規約）。
    """

def clear_rejected(dir_path, command_id: str) -> int:
    """同じ id への指示が通ったら、過去の失敗退避（*.err）を消す。消した件数を返す。
    新旧 2 形式（{"command": {...,"id"}} / 旧 {"command": "approve","id":…}）を読む。"""

def prune_rejected(dir_path, *, keep: int = 200, ttl_sec: float = 7 * 24 * 3600) -> int:
    """失敗退避を件数上限と TTL で掃除する（gc 用）。消した件数を返す。"""
```

- `pending` の debounce 判定は「**読めない** かつ 猶予内」に限る（読める指示まで
  先送りしない）。プロジェクト側 `ingest_commands:770-773` が明文化した規則と同じ
  ——読める承認を debounce で先送りすると、起こしたパスで承認が取り込まれず
  charter が再評価されて要対応が復活する。
- `clear_rejected` はプロジェクト側 `_clear_rejected_commands`（新旧 2 形式の読み分けを
  含む）の**移設**。プロジェクト側はこれを呼ぶ薄い皮になる（journal 記録だけ残す）。

#### 3.4.2 ノード側（`_ingest_node_commands`）

```python
_NODE_COMMAND_DEBOUNCE_SEC = 3.0   # プロジェクト側 debounce の既定と同値
```

- `_cmddrop.pending(cdir, debounce_sec=_NODE_COMMAND_DEBOUNCE_SEC, stop_at_deferred=True)`。
  常駐体は 30 秒周期なので、猶予に掛かった指示は次の tick（最悪 30 秒後）に処理される。
  手置き（人が `~/.agents/commands/` へ直接置く。スキーマが認めている書き手）が
  即 `.err` にならない。
- 成功時に `_cmddrop.clear_rejected(cdir, did)`（受理レシートを書いた直後）。
  dashboard の失敗バナーは id 単位で出るので、同じ委譲 id で通ったら消える。
- **すべての reject を `status.record_error` に載せる**（現在は JSON 不正のみ・§7-E）。
  `engine/status.json` の `recent_errors` は dashboard の唯一の横断ビューで、
  ここに出ないと「押したのに効かない」の原因追跡が `.err` の直接閲覧に依存する。

#### 3.4.3 gc tick

`tick_gc` のスイーパー列へ 1 つ足す（`resident_cli.py:744-748`）:

```python
    run_gc(_project_gc_sweepers(host)
           + [("board", lambda: _sweep_terminal_delegations(host)),
              ("commands", _sweep_node_commands)], …)

def _sweep_node_commands() -> dict:
    """ノード宛て指示の残骸掃除。`.err` は成功時に id 単位で消えるが、二度と同じ id の
    指示が来なければ残り続ける（板の公示は終端して消える＝その id はもう来ない）。"""
    d = node_commands_dir()
    return {"commands.err": _cmddrop.prune_rejected(d),
            "commands.receipts": _cmddrop.prune_receipts(d)}
```

`.err` の TTL は 7 日（受理レシートの 24 時間より長い）——失敗の履歴は
「なぜ効かなかったか」を人が後から読む一次資料で、成功の痕跡より寿命が要る。

#### 3.4.4 プロジェクト側は挙動を変えない

`ingest_commands` は `continue` 型 debounce（読めないものを飛ばして後続を処理）の
まま残す。プロジェクトの指示はタスク単位で独立しており、順序の制約は
`(入札 → 中止)` のような形で存在しない。**共有するのは土台（読める判定・猶予の計算・
`.err` の読み分け）だけで、順序の規約はスコープの性質で選ぶ**——ここを揃えると
承認が壊れたファイル 1 つの後ろで詰まる。この非対称は `stop_at_deferred` 引数として
契約に現れる（黙って違う挙動になる形にはしない）。

### 3.5 P1-5 — `revive` に charter スコープを通す

#### 3.5.1 `remove_tombstone`

```python
def remove_tombstone(cfg: "Config", title: str, charter: "str | None" = None) -> int:
    """指紋が一致する墓標行を削除する（`revive`）。削除件数を返す。

    charter を渡すと「その charter 向け + タグ無し」だけを消す——読み
    （`load_tombstones`）と同じ絞り方にする。None は全 charter（明示の `--all`）。
    追記が `(指紋, charter)` 単位なのに削除が指紋だけだと、複数 charter 運用で
    片方を revive したつもりで両方が復活する。"""
```

#### 3.5.2 `cmd_revive` の既定

`--charter` の指定が無いときに「全部消す」（現行）へ戻ると同じ穴なので、
**曖昧なら消さずに聞く**:

| 状況 | 挙動 |
|---|---|
| `--charter X` | X + タグ無しを削除 |
| `--all` | 指紋一致を全削除（従来の挙動。明示のときだけ） |
| 未指定 / charter が 0〜1 個 | その charter（無ければ None）で削除。単一 charter 運用では従来と同結果 |
| 未指定 / charter が 2 個以上 かつ 一致行が**複数の異なるタグ**を持つ | **exit 2**。該当行を charter 付きで列挙し、`--charter` か `--all` を促す |
| 未指定 / charter が 2 個以上 かつ 一致行のタグが 1 種類以下 | 削除する（曖昧でない） |

「消す」は取り返しがつく操作（また却下すればよい）だが、**気づかずに復活する**のは
取り返しがつかない（次の plan が黙って作り直す）。止める側に倒す。

CLI: `revive` へ `--charter` と `--all` を足す（`cli.py:262-265`）。
決定記録（`append_decision`）と journal に**削除したスコープ**を残す
——「どの charter の墓標を消したか」は後から backlog の差分を読むときの手掛かりになる。

---

## 4. 変更ファイル一覧

| # | ファイル | 変更 |
|---|---|---|
| P1-1 | `tools/agent-project/agent_project/verify.py` | `VERIFY_SIDE_EFFECT_RULES` / `verify_side_effect_rule` 新設・`verifier_input` へ `side_effects_text`・`_builtin_verifier_prompt` の全面書き直し |
| P1-1 | `.github/skills/backlog-verifier/scripts/prompt.py` | `side_effects_text` を優先（無ければ従来どおり） |
| P1-1 | `.github/skills/backlog-verifier/SKILL.md` | 入力表へ 1 行 |
| P1-2 | `tools/agent-tools/agentcore/agentcore/agentcli.py` | `spill_prompt()` 新設・`headless_cmd(spill_path=)` との違いを docstring へ明記 |
| P1-2 | `tools/agent-project/agent_project/prioritize.py` | `_ARGV_LIMIT` / `_agent_argv_limit()` / `_run_agent_cli` の退避と掃除・env トリアージへ E2BIG |
| P1-2 | `tools/agent-project/agent_project/configfile.py` | `CONFIG_DEFAULTS["argv_limit"]`・`SHARED_KEYS` へ追加・`build_config` で大域確定 |
| P1-2 | `tools/agent-project/agent_project/config.py` | `argv_limit` フィールド |
| P1-2 | `tools/agent-flow/agent_flow/agent.py` / `tools/agent-amigos/agent_amigos/agentcli.py` | 自前退避 → `agentcli.spill_prompt`（文言は据え置き） |
| P1-2 | `tools/agent-project/agent-project.yaml.example` | `argv_limit` の 1 行 |
| P1-3 | `tools/agent-project/agent_project/resident_cli.py` | `HOST_TOP_KEYS` / `HOST_PROJECT_KEYS` / `host_config_findings()` 新設・`HostConfig` のスカラ救済・`load_host_config` の 1 度きり警告 |
| P1-3 | `docs/plans/2026-07-26-s1-config-two-layer-detailed-design.md` | 文言カタログへ W5 / W6 / W7 を追記（既存の「実装で確定した差分」節の流儀） |
| P1-2 | `tools/agent-project/README.md` | 両方に書けるキーの一覧へ `argv_limit` |
| P1-1/3/4/5 | `docs/designs/agent-project-design.md` | 設計正典への反映: 組み込みとスキルが同じ入力で組まれること・host.yaml トップレベルの警告と型の救済・指示ドロップの猶予と `.err` の寿命・墓標解除の charter スコープ |
| P1-2 | `docs/designs/agent-cli-plugin-design.md` | 「退避」が 2 つある（定義の `spill` = 権限フラグ置き換えを伴う読み取り専用向け / `spill_prompt` = OS の `ARG_MAX` 向けで権限に触らない）ことを spill の項へ明記 |
| P1-4 | `schemas/agent-node-command.schema.json` | 取り込みの猶予・順序の規約・`.err` が消える 2 つの場合を記述へ（**契約の形は変えない**。スキーマが書き手として認めている「人の手置き」に効く振る舞いなので、書き手が読む場所に置く） |
| P1-4 | `tools/agent-tools/agentcore/agentcore/commands.py` | `pending` の debounce / `stop_at_deferred`・`clear_rejected` / `prune_rejected` 新設 |
| P1-4 | `tools/agent-project/agent_project/resident_cli.py` | `_ingest_node_commands` の debounce・`.err` 掃除・全 reject の status 記録・`_sweep_node_commands` と gc 登録 |
| P1-4 | `tools/agent-project/agent_project/commands.py` | `_clear_rejected_commands` を agentcore へ委譲（薄い皮に） |
| P1-5 | `tools/agent-project/agent_project/charter.py` | `remove_tombstone` へ charter 引数 |
| P1-5 | `tools/agent-project/agent_project/commands.py` | `cmd_revive` のスコープ解決と曖昧時の停止 |
| P1-5 | `tools/agent-project/agent_project/cli.py` | `revive --charter` / `--all` |
| — | `docs/plans/2026-07-26-open-items-and-concerns.md` | §7.2 に本設計へのリンク |

---

## 5. テスト計画

### 5.1 P1-1

| テスト | 形 | 何を固定するか |
|---|---|---|
| `test_builtin_prompt_carries_side_effect_rule`（新規） | `verifier_skill` を存在しない名前にして組み込み経路を強制し、`workspace` / `network` それぞれで制約文が載ること | **スキル未導入ノードで安全設定が落ちない** |
| `test_skill_and_builtin_share_the_side_effect_rule`（新規） | 同じ cfg で両経路のプロンプトを作り、`VERIFY_SIDE_EFFECT_RULES[...]` の文が**両方に**含まれること | 文言の 2 重管理が起きたら落ちる |
| `test_builtin_prompt_reaches_every_spec_key`（新規・構造） | `verifier_input` の全キーに番兵を入れた spec → 組み込みプロンプトに全番兵が現れる。除外は理由付きリスト（現在 `side_effects` の 1 件） | 入力を足して組み込みに載せ忘れると落ちる（P0-4 と同じ型） |
| `test_verifier_prompt_injects_repo_context_rules_and_recipes`（既存） | 変更しない | スキル経路の回帰 |

**どちらの経路も明示的な seam なしには検査できない**（§7-I）。組み込みは
`cfg.verifier_skill = "no-such-skill"` で強制し、スキルは `.github/skills/…/prompt.py` を
**パス直指定**で走らせる——テストは中立な一時 cwd で走るので `find_skill_script` は
リポジトリのスキルを見つけない（実測）。

### 5.2 P1-2

| テスト | 形 |
|---|---|
| `test_large_prompt_spills_for_argv_cli`（新規） | `_run_agent_cli` の `subprocess.run` を差し替え、`kiro`（argv）で上限超のプロンプト → argv に退避先パスが載り、本文はファイル側にある。ファイルは呼び出し後に消えている |
| `test_large_prompt_not_spilled_for_stdin_cli`（新規） | `claude`（stdin）では退避しない（ARG_MAX に当たらない） |
| `test_spill_keeps_write_permission_flags`（新規・回帰） | 退避しても argv に `--trust-all-tools` が残る（`--trust-tools=fs_read` へ**置き換わらない**）。§7-A を型として固定 |
| `test_argv_limit_reaches_config`（新規） | プロジェクト yaml の `argv_limit: 4096` が `cfg.argv_limit` と `_agent_argv_limit()` に届く。P0-4 の到達検査にも自動で乗る（int 番兵） |
| `test_e2big_is_classified_as_env`（新規） | `Argument list too long` が env 分類（リトライを焼かない） |
| flow / amigos の既存 spill テスト | **変更せずに緑**（`spill_prompt` への移設が挙動を変えていないことの裏取り） |

### 5.3 P1-3

| テスト | 内容 |
|---|---|
| 未知キー | `nodeid: pc-a` → 所見 1 件（近い既知キー `node_id` の提示を含む） |
| 層違い | トップレベル `plan_review: false` → 「プロジェクト yaml へ」、`model: x` → 「`defaults:` の下へ」 |
| スカラ救済 | `agent_cli: codex` → `HostConfig.agent_cli == ["codex"]` かつ所見 1 件。`tags: urgent` も同様 |
| 板への波及（回帰） | 上の host.yaml で `_node_capability` が `["codex"]` を publish し、`requires.agent_cli: ["codex"]` の公示に `eligible` が True（現在は False になる） |
| `projects[]` | 要素の未知キー・`config:` で所見。既知キーだけなら所見ゼロ |
| 正常系 | `host.yaml.example` の全キーで**所見ゼロ**（カタログの取りこぼし検出。例示ファイルを正解データに使う） |
| 警告は 1 度 | 同じパスで `load_host_config` を 2 回呼んでも出力は 1 度 |

### 5.4 P1-4

| テスト | 内容 |
|---|---|
| debounce | 壊れた JSON を今書く → 1 巡目は `.err` にならない（pending にも出ない）。mtime を猶予より古くする → 2 巡目で `.err` |
| 順序保存 | 「入札(t1・壊れている) / 中止(t2・正常)」を置く → 1 巡目は**どちらも処理しない**（中止が先に走らない）。猶予後に入札が `.err`・中止が実行 |
| `.err` 掃除 | 同じ id で 1 度失敗（`.err` あり）→ 同 id の指示が成功 → `.err` が消え、dashboard の `nodeCommandStatus` が `done` になる（JS 側の読み取りと突き合わせる） |
| status への記録 | 板不一致・未知指示・公示不在の各 reject で `status.recent_errors` に 1 件 |
| gc | `.err` を TTL 超で置く → `_sweep_node_commands` が消し、件数を返す |
| プロジェクト側 | 既存の debounce テストが**変更なしで緑**（土台の共有が挙動を変えていない） |

### 5.5 P1-5

| テスト | 内容 |
|---|---|
| charter 限定 | charter A / B に同じタイトルの墓標 → `revive --charter A` で A とタグ無しだけ消える |
| 既定（単一 charter） | charter が 1 つなら従来どおり消える（後方互換） |
| 曖昧で停止 | charter 2 つ・タグの異なる一致行が 2 件 → exit 2・両方残る・列挙が出る |
| `--all` | 明示すれば全削除 |
| 記録 | 決定記録と journal に削除スコープが載る |

### 5.6 まとめて回す

CI はまだ無い（総覧 §1.2 / P3-1）ので P0 と同じ 6 コマンドを手元で回す
（agentcore は**テストルートが 2 つ**・§6.3）:

```
python3 -m unittest discover -s tools/agent-project/tests
python3 -m unittest discover -s tools/agent-flow/tests
python3 -m unittest discover -s tools/agent-amigos/tests
python3 -m unittest discover -s tools/agent-tools/agentcore/agentcore/tests
python3 -m unittest discover -s tools/agent-tools/agentcore/tests
cd tools/agent-dashboard && npm test
```

---

## 6. 実施順序

1. **P1-2 → P1-1** の順（依存）。P1-1 は組み込みプロンプトに repo 文脈・rules・
   レシピ・feedback を足す＝**プロンプトを太らせる**変更なので、退避が先に無いと
   スキル未導入ノードで E2BIG の窓を広げることになる。
2. **P1-4 → P1-3**（どちらも独立だが、P1-4 は canary で踏む可能性がある側）。
   canary 中に手動入札を試すなら P1-4 が入っている方が観測が濁らない。
3. **P1-5 は独立**。いつでもよい。
4. 契約に触らないので**静止点は不要**——P0-3 のような名義変更を伴わず、
   古いノードと新しいノードが混在しても板の語彙は変わらない。
   （`side_effects_text` は additive で、旧版スキルは自前の表へ落ちる。）
5. 完了条件: §5 のテストが緑 + 5 件それぞれの「効かない設定が効く」ことを
   設定ファイル経由で通したテストがあること（`getattr` 既定や属性の手生やしで
   通る形を残さない——P0-4 の教訓）。

---

## 7. 本設計の過程で新たに見つけたもの

総覧 §6 に無いものだけを挙げる。A・C・D・E・F・I は P1 の中で直す前提で §3 に織り込んである。

| # | 内容 | 重要度 | 扱い |
|---|---|---|---|
| A | **総覧 P1-2 の「`headless_cmd` の spill 経路を配線」は、そのままやると検証が壊れる**。定義側の spill は退避時に権限フラグを `spill.args` で**置き換える**設計で、`kiro.json` では `--trust-all-tools` → `--trust-tools=fs_read`。コマンドを実行して確かめるのが仕事の verifier がこれを通ると全基準 `unverifiable` に倒れる。flow / amigos が定義の spill を使わず自前で退避しているのはこの違いによる | 高（前提の訂正） | P1-2 §3.2.1。ad-hoc 退避を採る。回帰テストで固定（§5.2） |
| B | **`headless_cmd(spill_path=)` は Python 側の消費者がゼロ**。使っているのは dashboard（JS）だけで、Python 3 者は自前の日本語指示文を持つ（flow / amigos で文言も別）。「定義ファイルが正典」という S9 の建前と実装がずれている | 低 | P1-2 では文言を据え置き（既存テストが固定している）。定義へ寄せるかは P2-5 の `DIFF_CRITERION` と同じ決着に回す |
| C | **host.yaml の `agent_cli:` / `tags:` にスカラを書くと 1 文字ずつの配列になる**（`[str(a) for a in "codex"]`）。板の `nodes/<id>.json` へ `["c","o","d","e","x"]` が publish され、`requires.agent_cli` / `requires.tags` を持つ公示に**永久に入札しない**。入札選別は fail-close なので誤動作ではなく**無言の不参加**として出る。`defaults.agent_cli`（スカラ）と紛らわしいキーなので誤記は起きやすい | 中〜高 | P1-3 §3.3.3（畳んで救済 + 警告）。板への波及を回帰テストに（§5.3） |
| D | **debounce を素朴に入れるとノード宛て指示の順序が壊れる**。指示はファイル名の時刻順＝処理順が規約で、同じ公示への「入札 → 中止」が入れ替わると中止済みの板へ入札を書く（dashboard 側のコメントが警告している事故）。読めないファイルを飛ばして後続を処理する実装はまさにそれを起こす | 中 | P1-4 §3.4.1 の `stop_at_deferred`。プロジェクト側は従来どおり（性質が違うので契約として引数に出す） |
| E | **ノード側の reject が `engine/status.json` に載らない**。`status.record_error` は JSON 不正のときだけで、板不一致・未知指示・公示不在で `.err` に落ちた指示は横断ビューに現れない。プロジェクト側は journal に残るので、ここだけ痕跡が薄い | 中 | P1-4 §3.4.2 |
| F | **argv 長超過（E2BIG）が「内容の問題」に分類される**。`OSError: [Errno 7] Argument list too long` は env パターン（`command not found` / `No such file or directory`）に掛からず、タスクのリトライ予算を焼く。退避が入っても Windows 系の低い上限では残る | 中 | P1-2 §3.2.5 |
| G | **agent-project には `argv_limit` 設定自体が無い**（flow は設定 + CLI + doctor 検査、amigos は定数）。同じ「OS の事情」を 3 ツールで別々に持っている | 低 | P1-2 §3.2.4 で設定キーを追加。層は SHARED（ノード差を許す） |
| H | **`revive` の既定を狭めると挙動が変わる**。単一 charter 運用では同じだが、複数 charter でタグの違う一致行があると exit 2 で止まる（従来は黙って両方削除） | 低（移行の注記） | P1-5 §3.5.2。`--all` を逃げ道として用意 |
| I | **検証プロンプトの 2 経路のうち、テストが見ているのは組み込みだけ**（当初は逆に書いていた——実装時の実測で**訂正**）。`_shared.py` が中立な一時 cwd へ `chdir` しエージェントホームも隔離するため、`find_skill_script` はリポジトリのスキルを見つけない。つまり既存の `build_verifier_prompt` テストは**スキル経路を一度も通っていない**（組み込みが acceptance と DIFF_CRITERION を持っていたので緑だった）。どちらの経路も明示的な seam なしには検査できない | 中 | P1-1 §5.1。組み込みは `verifier_skill` を存在しない名前にして強制し、スキルは `prompt.py` をパス直指定で走らせる |
| J | **S1 §3.3 の E6（`projects[].config` はエラー）が未実装**。host.yaml の `projects[]` にはキー検査自体が無いので、`config:` を書いても無言で無視される。設計書の文言カタログには載っている | 低 | P1-3 §3.3.2 の W7 として**警告**で拾う（E への昇格は canary 明けの判断に含める） |
| K | **`schema_version` は例示ファイルにあるが誰も読んでいない**。値の検査も、将来のバージョン差の分岐も無い | 低 | P1-3 では既知キーとして受けるだけ（読まないことを明示）。バージョニングの要否は契約の話なので P2 以降 |

---

## 8. 積み残し（本設計では扱わない）

実装後の確定版。**すべて契機待ち**で、P1 として着手すべきものは残っていない
（総覧 §3 の末尾 3 行・§5 の最終項・§7.3 P2-5・§7.4 P3-3 へ転記済み）。

| # | 内容 | 拾う契機 |
|---|---|---|
| 1 | **W5/W6/W7 を E（fail-fast）へ昇格するか**。host.yaml の綻びで起動を止めるか。S1 §3.3 の E6（`projects[].config`）を宣言どおり fail-fast へ戻すかも同じ判断 | canary（総覧 §1.1）で「警告が実際に出た件数と内容」を見てから |
| 2 | **doctor から `host_config_findings()` を呼ぶ**。純関数に切り出してあるので配線するだけ。あわせて設定値の検査（`argv_limit ≤ 0` 等・agent-flow の doctor にはある）も | P3-3（doctor へ検査をまとめて足すとき） |
| 3 | **プロジェクト側 `commands/*.err` の期限掃除**。土台（`prune_rejected`）は用意済みで配線するだけ。状態リポジトリ配下なので古い失敗が全 PC へ配られる | `.err` の残骸が実際に邪魔になったとき。`.err` は失敗バナーの根拠なので、消える条件を増やす前に dashboard の表示規約と突き合わせが要る |
| 4 | **`spill_prompt` の指示文の正典**（§7-B）。定義の `spill.instruction` は Python から使われておらず、3 者が自前の文を持つ | P2-5 で `DIFF_CRITERION` とまとめて決める（**手は P1-1 で実証済み**——解決済みの文を入力で渡し、受け側の表は受け皿に降格する形） |
| 5 | **`Config` を `slots=True` にする**（P0 §8 から継続）。動的属性を生やしている箇所（`cfg._controller_active` 等）の棚卸しが要る | P0 §8 と同じ扱い（構造テストで代替できている間は急がない） |
| 6 | **`side_effects` の実効性**。プロンプトで頼むだけで、CLI の権限フラグとしては強制していない | `readonly` を強制しないという S9 §6 の割り切りと同じ線上。変えるなら割り切りの再決定が先（総覧 §4） |
| 7 | **ノード宛て指示の猶予（3 秒）が定数**。プロジェクト側は `debounce` 設定で変えられるが、ノードスコープは `cfg` を持たない | 手置きの運用が増えて 3 秒では足りないと分かったとき（板 tick は 30 秒周期なので、猶予を延ばす実利は小さい） |

---

## 9. 実装で確定した差分

設計と実装がずれたところ。**本文は書き換えず、ここに理由付きで残す**（P0 詳細設計と同じ流儀）。

| # | 設計 | 実装 | 理由 |
|---|---|---|---|
| P1-1 | 組み込みプロンプトへ入力を載せる | それに加えて `verifier_input` の**全キーに番兵を入れて突き合わせる**構造テストを置いた（除外は `side_effects` の 1 件・理由付き） | 個別に直すだけでは、次に入力を足したときにまた黙って落ちる。P0-4 の「CONFIG_DEFAULTS ⊆ Config」と同じ型の護り |
| P1-1 | スキル経路と組み込み経路の同値をテストで固定 | スキル側は `prompt.py` を**パス直指定**で走らせる | テストは中立な一時 cwd で走るので `find_skill_script` がリポジトリのスキルを見つけない（§7-I の訂正）。`build_verifier_prompt` 経由では組み込みしか通らない |
| P1-2 | `_run_agent_cli` で退避 → `finally` で掃除 | あわせて `_agent_cmd` の呼び出しも `try:` の**内側**へ移し、`out_file` を `None` で先に束縛した | 組み立てが例外で落ちたときに `finally` が `NameError` を投げて本当の原因を隠す（P0-1 で踏んだ形）。ついでに退避ファイルの取りこぼしも消える |
| P1-2 | `spill_prompt(prompt, limit, …)` を agentcore へ | `prompt_via` も引数に取り、**stdin 渡しなら何もしない**判定まで helper に入れた | 「stdin は ARG_MAX に当たらない」は OS の事実で、呼び出し側 3 者が同じ `if` を書くと、また 3 者でずれる |
| P1-3 | 未知キー・層違い・型違いを警告 | `tags` / `agent_cli` のスカラは**畳んで救済**し、その旨も所見に出す | 無視（空配列）でも文字分解よりましだが、書き手の意図は明白。ただし黙って直すと「配列で書かなくても動く」という別の思い込みを作る |
| P1-4 | `pending(debounce_sec=…)` を agentcore へ | プロジェクト側 `ingest_commands` は**呼び出しを変えていない**（従来どおり自前で mtime を見る） | プロジェクト側の猶予は `cfg.watch` と `cfg.debounce` に依存する（watch 中だけ・設定値可変）。土台に寄せると引数が 3 つ増えるだけで、規約は共有できていない。共有したのは `.err` の読み分け（`clear_rejected`）と掃除で、そちらは規約そのもの |
| P1-4 | `.err` の TTL 掃除を gc へ | 受理レシートの prune も同じスイーパーに入れた | ノードスコープのレシート prune は `write_receipt` の内側でしか走らない＝**指示が来なくなったら止まる**。gc から呼べば止まらない |
| P1-5 | 既定は「指定 charter + タグ無し」 | charter が 2 個以上あって一致行のタグが**割れているときだけ** exit 2 で止める | タグが 1 種類以下なら曖昧でない（止める理由が無い）。単一 charter 運用では従来と同結果になることをテストで固定した |

### 9.1 実測（実装後）

| 対象 | 結果 |
|---|---|
| agent-project | 1,112 件 緑（新規 30 件・修正前は 1,082 件） |
| agent-flow / agent-amigos | 571 / 176 件 緑（自前退避を共有ヘルパへ移設・テスト無改変） |
| agentcore（テストルート 2 つ） | 80 / 66 件 緑（新規 19 件） |
| agent-dashboard `npm test` | 緑（失敗 0） |

既存テストで**書き換えたのは 1 件だけ**:
`test_broken_command_file_is_quarantined_not_retried_forever` は「壊れたファイルは即 `.err`」を
固定していたので、新しい契約（猶予の内側では `.err` にしない → 猶予を過ぎたら `.err`）の
両方を確かめる形へ広げた。
