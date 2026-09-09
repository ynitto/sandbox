# agent-app: agent-tools 無しで動く本体と、agent-herd ありで増えるもの — 設計

日付: 2026-09-09 / 対象: `tools/agent-app`、`.github/skills/statemachine-use`、`tools/agent-tools`（agent-herd / agentcore）、`tools/agent-loop`

## 0. 一文で

agent-app は **CLI と tmux だけで会話・タスクの作成・タスクの実行が閉じる**デスクトップにし、
agent-tools（agent-herd / agent-loop / agent-flow）は「入れると増える」任意の層として位置づける。
増えるものは、費用 0 のローカル実行（`herd`）、定期実行と履歴、複数 AI のワークフロー、そして
クラウドのトークンを使わずに済ませる前処理である。

## 1. 現状（2026-09-09 まで）の依存と、何が問題だったか

agent-app が agent-tools の一族を呼んでいた場面を洗い出した。

| 場面 | 呼んでいたもの | 無いとどうなっていたか |
|---|---|---|
| 会話（tmux / ヘッドレス） | CLI を直接（`agents/*.json`） | **動く**（`herd` の行が出ないだけ） |
| タスクを AI と作る | 会話と同じ CLI + statemachine-use スキル | **動く** |
| 構成を確認（`--dry-run`） | この端末の python + 同梱スキル | **動く** |
| タスク・ワークフローの「使う AI」 | `agent-herd defs --json` | **AI を 1 つも選べない**。既定の `aider` も常に使えない |
| AI 支援（工程の見直し・ワークフロー教示） | `agent-herd --purpose plan --readonly -p` | 起動できない |
| 手動実行 | `agent-loop statemachine` / `run`（agentcore の harness） | 「実行基盤に接続できませんでした」で実行ボタンが押せない |
| 定期実行・履歴・daemon | agent-loop | 使えない |
| ワークフロー | agent-flow | 使えない |

つまり **会話は独立していたのに、タスクは一覧の時点で agent-herd に握られていた**。導入の入口が
agent-tools 一式（Python の zipapp 群と install.sh）になり、「CLI を並べて会話し、タスクを教えて回す」という
agent-app 単体の価値が伝わらなかった。

## 2. 方針: メイン機能は agent-app で閉じる（ADR-11）

メイン機能を **会話 / タスクの作成 / タスクの実行 / AI 支援** と定め、これらが agent-tools 無しで動くように
した。任意機能（定期実行・履歴・ワークフロー・`herd`）は無ければ画面が 1 行でそう言う。

### 2.1 使える AI の一覧を自前にする

`src/main/agents.js` を新設し、会話の `agents:list` とタスクの `automation:agents:list` が **同じ 1 つの一覧**を
見る。定義は `agents/*.json`（探索順は agent-cli 仕様）、「使える」印はホスト（Windows では WSL）の PATH。
一族（`command[0] === 'agent-herd'`）が使えれば仮想の `herd` も並ぶ（ADR-8 のまま）。タスク側は
「使えるものの名前」だけを受け取る。

既定の AI は会話の「おすすめ」tier と同じ CLI（`automationAgent` が空のとき）。会話で使えている CLI が
そのままタスクでも使え、`aider` の決め打ちで「既定が常に使えない」を無くす。一覧に無い名前を選んで
あっても黙って別の AI へ倒さない（会話と同じ規則）。実行しようとすれば
`使う AI「<名前>」はこの環境で使えません` と断る。

### 2.2 AI 支援は定義の単発 argv でその CLI を直接起こす

`agentCli.oneShotCmd(spec, { model, readonly })` を足した。セッション継続も履歴の再送も持たず、
`command + (write_args | readonly_args) + model_flag model + command_suffix (+ prompt_flag)` と
プロンプトの渡し方（stdin / argv の最後）だけを返す。`{output_file}` は残し、起こす側が一時ファイルへ
置き換える（codex）。

`automation/ipc.js` の `assistRunSpec` が `hooks.assistRunSpec` として handlers に渡り、
`launchAi` はそれで起こす。`herd` を選んだときだけ従来どおり `agent-herd --purpose plan`（`--agent` は
渡さない）。stdout（か `{output_file}`）は会話と同じ `response.parseTranscript` で本文を取り出してから
JSON として読む。`runner.stream` に `input`（stdin へ流して閉じる）を足した。

### 2.3 手動実行は agent-loop が無ければ同梱スキルで回す

`agent-loop inspect` が答えない（`available: false`）ときだけ、`automation/direct-run.js` が同梱の
statemachine-use スキルに次を渡す:

```
python3 <skill>/scripts/run_machine.py .statemachine/<名前>/workflow.yaml \
  --agent exec --agent-command '<定義から組んだ argv の JSON>' --prompt-via stdin|argv \
  --instruction '<共通指示・開始アクション・スキル選択の合成>' --context k=v --input '…' --result-line
```

スキル側（`run_machine.py`）に足したのは 3 つだけ:

- `--agent exec` + `--agent-command` + `--prompt-via`: 呼び出し側が組んだ argv を工程ごとに 1 回起こす。
  `{output_file}` は一時ファイルに置き換えて終了後に読む。
- `--instruction`: `StateMachineEngine(instruction=…)`。工程のアクションにだけ
  `<指示>\n\n## 今回の工程\n<本文>` の形で前置する（agentcore の harness と同じ形。遷移条件の評価には付けない）。
- `--result-line`: 最後の行に `RESULT {"ok","finalState","stdout","error","escalate","steps"}`。
  agent-loop / harness の `RESULT` 行と同じ読み方（`agentLoop.parseResult`）ができる。LLM 呼び出しの失敗も
  `ok: false` の `RESULT` で申告する。

`agents/*.json` の読み方をスキルへ持ち込まない（agentcli の写しを増やさない）。argv は agent-app が
組めるので、スキルには「argv を受けて起こす」口だけを足した。Windows では CLI が WSL に居るので
python も WSL 側（`python3`）で起こし、スキルの置き場は WSL の表記へ直す。

`herd` を選んでいて agent-loop が無いときは、agent-herd の既定に任せる口が無いので、会話と同じ規則で
一族の共通 TUI と同じ定義（既定バックエンド `ollama`。無ければ一族の他の定義）を名指しする
（`resolveAgent` の `purpose: 'direct'`）。

### 2.4 画面

新しい画面は作らない。既存の実行詳細（概要）と実行環境のダイアログの文言と有効・無効だけを変える。

```
┌ 概要 ───────────────────────────────────────────────────────────┐
│ 手動実行                                              待機中     │
│  ● おすすめ · claude · スキル 自動 ⌄        [実行] [構成を確認] [停止] │
│  ┌──────────────────────────────────────────────────────┐        │
│  │ 実行すると、ここに進行状況が表示されます。            │        │
│  └──────────────────────────────────────────────────────┘        │
├──────────────────────────────────────────────────────────────────┤
│ 定期実行                                   [自動実行を開始]  [予定を追加] │
│ 予定なし · 定期実行と履歴には agent-loop が要ります     (開始は無効)     │
└──────────────────────────────────────────────────────────────────┘
```

- 「実行」は agent-loop が無くてもステートマシンのタスクなら押せる（プロンプトのタスクは agent-loop の
  設定にしか無いので、そもそも一覧に出ない）。
- 足りないものは定期実行カードの 1 行だけ（「実行基盤に接続できませんでした」の警告帯は出さない）。

```
┌ 実行環境 ────────────────────────────────────────────────────────┐
│ 使う AI [claude ⌄]          モデル（任意）[        ]              │
│ 構成確認用スキルの場所（任意）[ 通常は自動で検出します ]          │
│ [保存] [接続を確認]                                                │
│ (使えます) Python（構成確認）        利用可能（python3: 3.13）      │
│ (使えます) statemachine-use スキル   見つかりました（…）           │
│ (使えます) 使える AI（CLI の定義）   2 件（claude / codex）         │
│ (任意)     ローカル実行系（agent-herd） 無くても動きます。あると費用 0 の… │
│ (任意)     定期実行と履歴（agent-loop）  無くても手動実行はできます。…    │
│ (任意)     複数AIワークフロー（agent-flow） 無くてもタスクと会話は動きます。… │
│ (使えます) ブラウザ操作（playwright-cli） …                        │
└──────────────────────────────────────────────────────────────────┘
```

借りている形: 実行詳細の `.execution-card`（「概要」の手動実行・定期実行）と、実行環境の `.tool-list`
（既存の `使えます` / `未準備` の印に `任意` を足しただけ。色は `--bg` / `--muted` のトークン）。

### 2.4b 最適化の切り替えと、使えない機能の薄さ（2026-09-09 追記）

設定 > 実行制御に **「エージェントを最適化する」**（既定 ON）を置く。効くのは agent-herd が使えるときだけで、
OFF にするか agent-herd が無ければ「agent-herd が無いのと同じ」動きになる:

- 自動選択（設定の既定の起動方針）は **おすすめ** だけ。節約・品質重視は薄くなる。
- 手動選択（ターンごと・タスクの実行設定）は **おすすめ** か **直接指定** だけ。
- tier の行は medium だけ使え、small / large は薄くなる。
- 保存してある節約・品質重視は、その間は おすすめ として解決する（`settings.effectivePolicy`。会話のターンも
  同じ規則で `settings.resolve(…, { optimized })`）。
- 理由（「agent-herd が要ります」など）は画面に出さない。薄くするだけ。

```
設定 > 実行制御
  [x] エージェントを最適化する   節約・品質重視の起動方針と small / large tier を使います
  既定の起動方針   (●) おすすめ   ( ) 節約（薄い）   ( ) 品質重視（薄い）
  small  … 行ごと薄い / medium … 使える / large … 行ごと薄い

起動方針 [おすすめ ▾]  … おすすめ / 節約（薄い）/ 品質重視（薄い）/ 直接指定
```

使えない機能も同じく薄くする: agent-flow が無ければサイドバーの「ワークフロー」、agent-loop が無ければ
タスクの「履歴」タブと「定期実行」のカード。道具の有無は `automation:capabilities`
（`{ herd, agentLoop, agentFlow }`、60 秒キャッシュ）の 1 つの答えを会話画面とタスク画面が共有する。

### 2.5 検証

- `tools/agent-app/test/standalone.test.js`（新設）: 一覧が 1 つであること、単発 argv、AI 支援の直接起動
  （偽の CLI で stdin 経由の往復）、`direct-run` の argv（Linux / Windows）、handlers の分岐、実行環境の
  任意の道具、既定の AI。
- `.github/skills/statemachine-use/tests/test_run_machine_exec.py`（新設）: stdin / argv / `{output_file}`、
  共通指示がアクションにだけ前置されること、`RESULT` が最後の行であること、CLI の失敗と不正な argv の申告。
- 既存: `npm test`（268 件）、スキルの pytest（76 件）、Electron 実機スモーク（xvfb）でタスク画面を撮って確認。

## 3. agent-herd（agent-tools）ありで増えるもの

「無くても動く」の上に、入れると何が増えるかを **機能** と **トークン効率** の 2 軸で整理する。
実装済みのものと、これから足す提案を分けて書く。提案は app 側と family 側の改修点を併記する。

### 3.1 いま既に増えるもの（実装済み）

| 増えるもの | 効く場面 | 仕組み |
|---|---|---|
| `herd`（費用 0 のローカル LLM）を会話・タスク・AI 支援で選べる | 節約したいターン、夜間の定期実行、クラウドの利用枠が尽きたとき | 一族（aider / ollama）が PATH にあれば一覧に `herd` が並ぶ。会話は共通 TUI を 1 本開き、Ask は `/find`、作業フォルダのファイル添付は `/edit`（ADR-8） |
| Ask が **読み取り専用を保証**される | 調べるだけの依頼を安心して任せる | `herd` は `readonly: enforced`。クラウド CLI の多くは best-effort |
| 手動実行の正典が harness に移る | 受入条件（`check`）の昇格、実行ログ（JSONL）、台帳（`@agent-usage`）、single-shot CLI（aider）への反復 | `agent-loop statemachine` → agentcore の harness |
| 定期実行・実行履歴・daemon | 「毎朝 9 時に回す」「先週の失敗を見る」 | agent-loop の `inspect` / `schedule` / `log` |
| 複数 AI のワークフロー（計画・分担・検証・納品） | 大きめの依頼を分解して並列に回し、ブランチで納品する | agent-flow の bus |
| AI 支援の JSON 契約が **文法で強制**される | 工程の見直し・ワークフロー教示で「JSON になっていない」修正の往復が消える | `herd` → `agent-herd --purpose plan` → 定義の `variants` が `ollama` の `json` profile（`--format json`）へ振り替える |

### 3.2 トークン効率の観点で増えるもの（提案・優先順）

前提: クラウド CLI は利用枠が有限で、ローカル LLM は遅いが費用 0（コンセプト正典 §2 の与件 6）。
agent-app がクラウドへ送っている本文のうち、**判断が要らない前処理**をローカルへ寄せる。

| # | 提案 | 減るもの | app 側の改修 | family 側の改修 | 判定 |
|---|---|---|---|---|---|
| T1 | **「節約」tier の既定を `herd`** にする（一族が使えるときの初期値。設定で上書き可） | 節約を選んだターンのクラウドトークンが 0 に | `settings.normalize` の初期値を `agents.js` の一覧で決める（初回だけ。既存設定は触らない） | なし | 初回起動時に一族が居る PC で、節約ターンのクラウド使用量が 0 になること |
| T2 | **エージェントを渡り歩くときの履歴の再送を、ローカルで要約してから送る** | `replayPrompt` が未読のやり取りを全文再送している分（60 件の会話で数千〜数万トークン） | `runTurn` で `unseen` が閾値を超えたら `agent-herd -p --purpose summarize` を先に呼び、要約を「あなたのセッションの外で進んだやり取り」として渡す。要約に失敗したら全文（既定に倒す） | `agents/ollama.json` に `summarize` の purpose（`variants`）を足す。読み取り専用・`--format` なし・`num_predict` を絞る | 渡り歩き 1 回あたりのクラウド入力トークンが実測で下がり、要約後の会話が破綻しない（再生で確かめる） |
| T3 | **タスクの「構成を確認」の次に、AI 支援の見直し（review）を `herd` で回す**のを既定にする | 工程の見直しは JSON 契約の役割で、ローカルの `json` profile が向く。クラウドを使わない | 見直しの既定 AI を `herd`（使えるとき）に | なし（既存の `plan` 用途で足りる。`--format json` で修正往復も消える） | 見直し 1 回のクラウドトークンが 0 |
| T4 | **実行情報にトークン使用量と文脈使用率を出す** | 使い過ぎに気づける（減らす根拠が見える） | ヘッドレス・tmux の stderr / 画面から `@agent-usage` `@agent-context` を拾って `parts.information` に載せる | agent-herd は出力済み。クラウド CLI 向けには定義の `usage_pattern`（agent-cli 仕様に昇格）を検討 | 会話の実行情報にターンごとの使用量が並ぶ |
| T5 | **タスクの会話（作成・変更）の既定を `herd` にできる** | 定義を書く作業は long-running で、クラウドだと枠を食う | 既に会話の起動方針で選べる。「タスクを AI と作る」の初期値を設定に足す | `/sm` の作成モードがローカルで成立するかは実測が要る（gemma4:e4b で定義を書き切れるか） | 作成モードの完走率を再生で測ってから既定にする |
| T6 | **スキルの本文をプロンプトに混ぜない** | 自動選択したスキルを `inline` で本文に貼っている CLI では、毎ターン数千トークン | `skillSelection.deliver` で `herd` は `--skill <名前>` の遅延読みへ | agent-herd は実装済み（カタログを LLM に見せない） | `herd` のターンでスキル本文が本文に載らない |

T1・T3・T6 は app 側の既定値と分岐だけで済む。T2 は family 側に用途を 1 つ足す。T4 は agent-cli 仕様の
拡張を伴う。T5 は実測待ち。

### 3.3 機能の観点で増えるもの（提案）

| # | 提案 | app 側 | family 側 |
|---|---|---|---|
| F1 | 実行環境の「使える AI」に **profile（`ollama-json` / `ollama-read` …）も並べる** | 一覧の行に `profiles` を持たせる | `agent-herd defs --json` に `profiles` と `available` を返させる（app は agent-herd があるときだけ読む） |
| F2 | `check` の昇格（`escalate`）を **画面で受ける**——「この段では解けない」を上位 tier で再実行する導線 | `RESULT.escalate` で「品質重視で再実行」のボタン | harness は `exit 3` で申告済み。agent-loop 側の再投入 API があれば app から呼ぶ |
| F3 | 手動実行の **ログを agent-loop 無しでも残す**（`~/.agents/logs/agent-app/`） | `direct-run` の stdout を JSONL に落とし、履歴タブの「直近 1 件」を出す | なし。ただし正典は agent-loop の履歴なので、あるときはそちらだけを出す |
| F4 | agent-herd の `replay` を **タスクの品質測定**に使う（同じ工程を設定違いで再生し、`RESULT` の一致率を見る） | 履歴から「この工程を再生」 | `agent-herd replay` は会話ログの再生が対象。harness の JSONL も入力源にする拡張 |

F3 は「無いときの穴埋め」で、agent-loop の価値（履歴の正典）を薄めるので優先度は低い。

## 4. 変えなかったもの・非目標

- `agents/herd.json` は作らない（ADR-8）。一族の判定は `command[0]` のまま。
- ワークフロー（agent-flow）を agent-app 単体で回す代替は作らない。bus・claim・納品はエンジンの本体。
- agent-loop の zipapp を exe に同梱しない（Python のスタックを Electron の配布物に抱えない。入れれば
  同じ定義が正典の経路で回る）。
- 2 経路（harness / `run_machine.py --agent exec`）の意味は、どちらもスキルの `--dry-run` と
  `next_state.py` の契約を読むことで揃える。harness にしか無いもの（受入・台帳・single-shot の反復）は
  §3.1 のとおり「増えるもの」として扱い、無い環境で真似しない。

## 5. 関連

- 設計書: [`agent-app-design.md`](../designs/agent-app-design.md) ADR-11、§9、§12
- 仕様: [`agent-app-spec.md`](../specs/agent-app-spec.md) 前提、§6.3、§12
- agent-herd: [`agent-herd-design.md`](../designs/agent-herd-design.md)、[`tools/agent-tools/README.md`](../../tools/agent-tools/README.md)
- `herd` の規則: [`2026-09-07-agent-app-startup-and-herd-design.md`](./2026-09-07-agent-app-startup-and-herd-design.md)
