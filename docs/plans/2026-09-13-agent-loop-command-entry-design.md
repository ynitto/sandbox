# agent-loop: 固定コマンドを実行するエントリ（`command:`）の設計

> **状態（2026-09-13）**: 検討段階。実装前。

## 背景と課題

agent-loop の定期実行には、LLM を起こさない仕事が既に 4 件ある（`agent-loop.yaml.example`
の「資源制御」「使用量較正」「記憶メンテナンス」「Moltbook 巡回」）。いずれも
**イベントフック（`hooks:`）を流用**して実装している——`check()` の中で `subprocess.run` を
回し、最後に `None` を返して「今回は送らない」と申告する。

イベントフックの契約（`docs/specs/agent-loop-spec.md` §3.1）は「**送るかどうかと本文を決める**」
ためのもので、「送らずに実行する」ためのものではない。流用で次の食い違いが起きている。

| 食い違い | 何が起きるか |
|---|---|
| `check()` はデーモンの**プロセス内**で、30 秒（`_HOOK_TIMEOUT_SEC`）を超えると隔離される | フック側は `timeout: 300` を既定にしており、30 秒を超えた時点でデーモンは `hook_timeout` を記録して隔離する。処理そのものはスレッド上で走り続けるので、**「失敗した」と記録されつつ実際には完走する**。逆に本当に固まると、デーモンのスレッドを占有したまま隔離解除を待つ |
| `None` は「無風（idle）」 | 実行して仕事をしたのに idle と数えられ、`adaptive` を付けると間隔が後退する。例外は `error` 扱いで最小間隔付近を保つ——**成功と失敗で間隔の動きが逆**になる |
| 実行の記録が無い | `_upsert_execution` も `record_repository_run` も `RESULT` 行も通らない。agent-app の「履歴」に出ず、ログペインも開かない。失敗はデーモンのログに WARNING が 1 行残るだけ |
| 同時実行の枠の外 | `max_concurrent` のスロットを取らずに走る。重い整理バッチが対話実行と重なる |
| 手で回せない | `statemachine:` には `agent-loop statemachine --entry NAME` があるが、フックには相当する口が無い。agent-app のカタログでは種別「フック」で「定期実行で起動します」と表示するだけで「今すぐ実行」が無い |
| 書く側の負担 | 1 コマンドを回すのに `check(hook_config)` を書き、`hook_config` の入れ子から引数を取り出し、`subprocess.run` と例外処理を自前で持つ。`hooks/` の 4 件は同じ雛形の写しになっている |

一方で、`statemachine:` エントリの実装（2026-08-11 headless 設計、2026-09-05 repository-execution
設計）で、**対話ペインを使わない実行経路**（`_dispatch_headless` → `_run_headless`）と、
その周辺——スロット取得、実行レコード、ログの見せ場所、`RESULT` 行、リポジトリ履歴、
`--entry` で手で回す口、agent-app のカタログ——は既に揃っている。足りないのは
「ステートマシンの代わりに argv を 1 回実行する」分岐だけである。

柱と原則: 柱2 / C3 — 人の介入なしで回る仕事を増やす。C5 — 実行の成否を機械の観測
（終了コード）に置く。同じことを 2 つの仕組みで書かない。

## 要件

1. `agent-loop.yaml` のエントリで、**LLM を起こさず固定の argv を 1 回実行する**ことを
   宣言できる。宣言は `statemachine:` と同じ位置づけ（エントリの種別を決めるキー）。
2. 実行は既存の headless 経路に乗る。スロット、実行レコード、ログペイン、`RESULT` 行、
   リポジトリ履歴の扱いを `statemachine:` と揃える。新しい記録面を作らない。
3. 成否は**終了コード**。タイムアウトは失敗。成功と失敗で間隔の扱いが逆転しない。
4. `agent-loop command --entry NAME` で、デーモンも tmux も無しに同じ宣言を手で回せる。
5. agent-app のカタログで種別として見え、「今すぐ実行」できる（画面の変更は
   `CLAUDE.md` の手順どおり、ASCII で承認を得てから別途）。
6. 既存の `hooks:` は変えない。4 件の流用は本設計の口へ**順に**移し、移した後もフック契約は
   「送るかどうかを決める」用途として残る。
7. シェルは通さない。`check:`（statemachine-use）と同じく argv を直接実行する。

## 設計

### 宣言

キー名は `command`。`statemachine-use` の `check:` と同じ 3 形を受ける（利用者は既に
`check:` でこの綴りを知っている）。

```yaml
prompts:
  # 文字列（shlex で分割）
  - name: "資源制御"
    command: "node tools/agent-dashboard/scripts/resource-control.js --control-dir ~/.agents/control"
    interval_minutes: 5

  # 配列（分割済み）
  - name: "使用量較正"
    command: ["agent-audit", "calibrate", "--audit-dir", "~/.agents/audit"]
    interval_minutes: 60

  # オブジェクト（タイムアウト・環境変数を足すとき）
  - name: "記憶メンテナンス"
    command:
      argv: ["python3", "scripts/memory-maintenance.py", "--scope", "home"]
      timeout_sec: 600          # 既定 300
      env: { LTM_HOME: ~/.claude/skills }
    cwd: ~/notes
    cron: "0 3 * * *"
```

| 項目 | 型 | 既定 | 意味 |
|---|---|---|---|
| `command` | str \| list[str] \| {argv, timeout_sec?, env?} | — | 実行する argv。シェル記号（`| & ; < > ( ) $ \` ` など）を含む字句は起動エラー。パイプや条件分岐が要るならスクリプトファイルにして、それを argv に書く |
| `command.timeout_sec` | int | 300 | 超えたらプロセスグループごと止めて失敗にする |
| `command.env` | dict[str, str] | なし | 追加の環境変数。値は `~` を展開する |

argv の各字句は先頭の `~` だけ展開する（`hook_config` で `expanduser` していた分を
宣言側で吸収する）。実行ファイルは `PATH` 解決に任せ、相対パスは `cwd` 基準で読む。

**1 エントリ 1 コマンド**。連続して回すものはスクリプトにまとめるか、エントリを分ける
（statemachine-use の「1 ステート 1 成果物」と同じ考え方——順序と失敗の切れ目を宣言に
残す）。

### 併用できる宣言・できない宣言

読み込み時に fail-closed で断る（`statemachine:` と同じ方針。黙って無視して「設定したのに
効かない」を作らない）。

| 併用 | 扱い |
|---|---|
| `cron` / `interval_minutes` / `run_immediately_on_startup` / `cwd` / `exclude_from_concurrency` / `id` / `enabled` | そのまま効く |
| `hooks` / `event_hook_fallback` / `hook_config` | 効く。フックが「回すかどうか」と材料を決め、コマンドが仕事をする（§hooks との併用） |
| `prompt` / `slash` / `statemachine` / `webhook` | 起動エラー（種別が 2 つになる。`webhook` は将来課題として §未実装 に残す） |
| `agent_cli` / `model` / `session` / `acceptance` / `acceptance_judge` / `tuning_profile` / `fresh_context` | 起動エラー（LLM も対話面も無いので意味を持たない） |
| `mode: ralph` / `oneshot` / `clean_session` / `target` | 起動エラー |
| `adaptive` | `hooks` が無ければ起動エラー。無風の概念が無い（回せば必ず「実行した」）。`hooks` があれば `check()` の `None` が無風なので従来どおり効く |
| `preflight` | そのまま効く（送る前の判定はコマンドにも意味がある） |

### hooks との併用

フック契約（§3.1）はそのまま使う。`check()` が `None` を返せば回さない。返した値の
使い道だけをコマンド向けに定める。

| `check()` の返り値 | コマンドでの扱い |
|---|---|
| `None` | 回さない（無風。`adaptive` の後退対象） |
| `str` | 本文として**標準入力**へ渡す |
| `dict.prompt` | 同上 |
| `dict.cwd` | 実在するディレクトリなら作業ディレクトリ（従来どおり） |
| `dict.vars` | argv の各**字句**へ `{key}` で差し込む（`prompt.format_map` と同じ規則。`_SafeDict` で未定義キーは残す） |

`vars` の置換は字句単位なので、値に空白や記号があっても引数の数は変わらない。
シェル記号の検査は**宣言時の字句**にだけ掛け、置換後の値には掛けない（値は材料であって
コマンドラインではない）。

```yaml
  - name: "Issue 同期"
    hooks: gitlab-issue-hook
    command: ["python3", "scripts/sync-issue.py", "--iid", "{issue_iid}"]
    interval_minutes: 5
```

`check()` の 30 秒制限は据え置く。フックは「回すかどうか」を決めるだけで、仕事は
コマンド側へ移るので、制限は狙いどおりに効く。`check()` の中で `subprocess.run` を
回す書き方（いまの流用）は移行で無くす。

**`ack()` は終了コード 0 のときに呼ぶ。** いま `ack()` を呼ぶのはペインへの送信が
成功した場所だけで（`_dispatch_prompt` の末尾）、`_run_headless` は呼んでいない。
つまり `hooks` + `session: per-run` や `hooks` + `statemachine` の headless 実行は
**既にイベントを既読にできず、次回同じイベントを拾う**。`command` は必ず headless
なのでこの穴を踏む。併用を許す前提で、headless 経路の完了時（command は終了コード 0、
statemachine は `ok`、prompt は `verified`）に `_call_hook_ack` を呼ぶよう直す。
失敗した実行は ack しない——送信失敗と同じ扱いで、次回もう一度拾われる。

**同じエントリは直列。** フックが N 件返すと dispatch request が N 件でき、それぞれが
headless スロットを取って並走する。同期スクリプトの多重起動は事故になりやすいので、
`command` エントリは**エントリ単位で 1 実行ずつ**にする（2 件目以降は pending のまま
待つ。`max_concurrent` の枠とは別）。並走してよいコマンドは、いまのところ無い。

### 実行経路

`_dispatch`（`scheduler.py`）の経路解決で、`entry["command"]` があれば `_entry_route` を
**通さずに** `_dispatch_headless` へ回す。CLI プロファイルの解決は要らない——`_run_headless`
の `_tl_resolve_agent` を command エントリでは飛ばす。

`_run_headless` の分岐は `workflow` / `prompt` の 2 つから 3 つになる。

| 分岐 | 実体 | 結果契約のキー |
|---|---|---|
| `statemachine` | `agentcore.harness.statemachine.run_statemachine` | `ok, escalate, finalState, stopReason, logFile, files` |
| `command`（新設） | `agentcore.commandrun.run_command`（新設） | `ok, status, stopReason, logFile, durationSec` |
| それ以外 | `agentcore.harness.toolloop.run_prompt` | `ok, verified, verifiedBy, stopReason, files, evidenceErrors` |

`run_command` は agentcore に置く（`loopentry` と同じく agent-herd・agent-dashboard と
1 実装を共有する）。中身は `statemachine-use` の `run_check` と同じ形——argv を `Popen` で
プロセスグループとして起こし、stdout / stderr を実行ログ（`_headless_log_file`）へ流し、
終了コードで `ok` を決める。`stopReason` は `stopreason` に 1 値足す（`command_exit`）。
タイムアウトは `stopReason: command_timeout`、`ok: false`。

失敗理由（`_fail_execution` の `reason`）は `statemachine_*` に揃えて 2 つ。

| reason | いつ |
|---|---|
| `command_invalid` | 宣言が読めない（reload 後に壊れた等。起動時は読み込みで断っている） |
| `command_failed` | 終了コード非 0、タイムアウト、実行ファイル不在 |

進行表示は `_tl_progress` と同じ口で、開始 1 行・終了 1 行（`RESULT {json}`）だけを
ログペインへ出す。stdout の全文は jsonl 側に残す（ペインに流すとバッチの出力で埋まる）。

### 記録

- 実行レコード（`_upsert_execution` / `execution_terminal`）は statemachine と同じ。
- リポジトリ履歴（`record_repository_run`）に `kind: command` で 1 行残す。現状 `workflow`
  が必須なので、`kind` を足して `workflow` を任意にする（履歴画面の照合は `runId` で
  行っているので、`workflow` 無しでも引ける）。
- 間隔の更新は `outcome="dispatched"`。失敗しても `error` へは倒さない（実行はした。
  再試行の間隔を縮めるかは利用者が `interval_minutes` で決める）。

### 手で回す口

```
agent-loop command --entry "記憶メンテナンス" [-d DIR] [--config PATH]
agent-herd  harness command --entry "記憶メンテナンス"
```

`statemachine --entry` と同じく `agentcore.loopentry` で宣言を引き、同じ `run_command` を
呼び、`RESULT {json}` を 1 行出して終了コードを返す（0 = 成功、1 = 失敗）。

### agent-app（カタログ・履歴）

`inspect` の `kind` に `command` を足す（`repository_ui.py` の分類は現状 `prompt` /
`hook` / `statemachine` / `broken`）。画面側は種別ラベル「コマンド」と「今すぐ実行」
（`cmd_repository_command` を `cmd_repository_statemachine` と同型で足す）。画面の
変更は本設計の範囲外とし、`CLAUDE.md` の手順（ASCII → 承認 → 実装 → 実機確認）で別に出す。

### 既存フックの移行

| いま | 移行後 |
|---|---|
| `resource-control-hook`（audit collect → node 制御スクリプト） | 2 エントリに割る。collect は best-effort なので `command` の失敗が制御を止めない（別エントリなら自然にそうなる） |
| `audit-calibrate-hook` | `agent-audit calibrate …` の 1 エントリ |
| `memory-maintenance-hook`（複数スクリプトの順次実行） | フック本体の「何をどの順で呼ぶか」を `scripts/memory-maintenance.py` に移し、1 エントリで呼ぶ。「削除は走らせない」の禁止事項はスクリプト側の責務のまま |
| `moltbook-duty-hook` | 同上。`hook_config` の `skill_home` / `label_conn` は argv の引数へ |

移行は 1 件ずつ。`hooks/` の 4 ファイルは移行が済むまで残し、済んだら消す。フック契約
そのもの（GitLab 系・file-watch・webhook）は触らない。

## 実装計画

1. `agentcore.loopentry.command_spec(entry)` — 3 形の正規化と字句検査。`statemachine_spec`
   と同じ返り値の作法（正規化した entry を保存して再度通せる）。テストは
   `test_statemachine_entry.py` と同型で `test_command_entry.py`。
2. `agentcore.commandrun.run_command(spec, cwd, log_file, env)` — Popen・プロセスグループ・
   タイムアウト・jsonl 記録。`stopreason` に `COMMAND_EXIT` / `COMMAND_TIMEOUT`。
3. `scheduler.validate_entries` — `command` の採用と併用検査。`_dispatch` の経路分岐、
   `_run_headless` の第 3 分岐、`record_repository_run` の `kind`。
   headless 経路の完了時に `_call_hook_ack` を呼ぶ（既存の穴。statemachine / prompt の
   headless 実行にも効く）。`command` エントリのエントリ単位直列化。
4. `agent-loop command --entry`、`agent-herd harness command --entry`。
5. `repository_ui.inspect` の `kind: command` と `cmd_repository_command`。
6. `agent-loop.yaml.example` の 4 件を `command:` へ書き換え、`hooks/` の該当 4 ファイルを
   削除。README / `docs/specs/agent-loop-spec.md` §2.3 に `command` 行と §2.3.2 を足す
   （利用者向けに内部名を出さない——`tools/ci/check_user_docs.py` を通す）。
7. agent-app の種別ラベルと「今すぐ実行」（ASCII 承認の後）。

## 未実装・将来課題

- `webhook` + `command`（push で回す）。パススルーの受信 JSON を標準入力へ渡せば
  `hooks` と同じ形になるはずだが、要るものが出てから決める。
- stdout を次のプロンプトの材料にする（command の出力を `input` に渡す）。それは
  ステートマシンの `check:` か `run` の仕事で、本設計は「送らずに実行する」に限る。
