# agent-run — ローカル実行系の統合入口とハーネス統合の設計

> 作成 2026-08-25
> 対象: `tools/agent-tools/agentcore`（aider_adapter / ollama_adapter / ollama_loop / ollama_tui /
> ollama_replay）/ `tools/opencode/agent-opencode.py` / `tools/agent-loop`（toolloop / statemachine）/
> `agents/*.json` / `tools/agent-tools/install.sh`
> 効く柱・原則: **柱3 / C7 — 実行入口とハーネスの 1 実装化**（同じ契約の写しが 3 か所に
> 分散している状態を正典 1 つへ畳む）。C9（仕事の格付けに応じた振り分け）の入口も 1 本になる。
> 上位文書: [agent-tools コンセプト正典](../designs/agent-tools-concept.md) §7・§8
> 関連: [agent-aider 改良余地の評価](./2026-08-18-agent-aider-improvement-assessment.md) §8.3、
> [agent-ollama 設計](../designs/agent-ollama-design.md)、
> [agent-loop DESIGN](../../tools/agent-loop/DESIGN.md)

---

## 1. 背景と課題

ローカル実行系（コスト 0 の常備戦力）は現在、**入口・配布形態・ハーネスがそれぞれ分裂**している。

### 1.1 入口と配布形態の分裂

| 入口 | 実体 | 配布形態 | agentcore を import できるか |
|---|---|---|---|
| `agent-ollama` | `agentcore/ollama_adapter.py` | zipapp（agentcore 同梱） | できる |
| `agent-aider` | `agentcore/aider_adapter.py` | **単独ファイルのコピー** | **できない** |
| `agent-opencode` | `tools/opencode/agent-opencode.py` | 単独ファイルのコピー | できない |

この配布差の直接の帰結が、**環境補完ブロック（`_complete_ollama_env` / profile 取り込み）の
3 重複製**である。`ollama_adapter.py` 自身が「直すときは 3 箇所（ollama_adapter / aider_adapter /
agent-opencode）を揃えること」と注記している——C7（写しを作らない）に対する既知の負債であり、
実際に aider だけ NO_PROXY 補完が古いままになる事故の温床になっている。

また `agent-aider` が agentcore を import できないため、`context_slice` や `agentcli` の
定義解決など agentcore に積んだ資産を aider 経路だけが使えない。2026-08-18 評価（§3〜§5）で
挙がった改良の多くが「adapter が agentcore を使えれば 1 実装で済む」形をしている。

### 1.2 ハーネスの分裂

「モデルに足りない実行能力を外から補う」ハーネスが 2 系統 3 実装に分かれている。

| ハーネス | 置き場 | 何を補うか | 呼べる場所 |
|---|---|---|---|
| `ollama_loop`（bash / read ツールループ） | agentcore | ollama 素体にツール実行 | `agent-ollama --tools` |
| `toolloop`（read_files / write_files / run / final の限定契約） | **agent_loop の exec 合成断片** | single-shot CLI（aider 等）にツール実行 | `agent-loop` 内部のみ |
| `statemachine`（状態遷移 + toolloop） | **agent_loop の exec 合成断片** | 多段工程の遷移・出力契約 | `agent-loop statemachine` のみ |

`toolloop` / `statemachine` はゴール非依存の汎用ハーネスとして書かれている（toolloop 冒頭の
設計注記どおり）にもかかわらず、agent_loop パッケージの **exec 合成断片**（単体 import 不可）
なので、agent-loop のデーモン一式を介さないと使えない。「aider を statemachine で 1 回だけ
回したい」「dashboard 以外から限定ツール契約を使いたい」が agent-loop 経由でしか表せない。

### 1.3 対話型の分裂

対話の入口も CLI ごとにばらばらである: `agent-ollama --tui`（内蔵 TUI）、aider は素の
`aider` を手で起動（policy・接続補完なし）、opencode は対話導線なし。`agents/<name>.json` の
`interactive` ブロックという契約はあるのに、**それを人が使うための統一コマンドが無い**。

### 1.4 解くべき課題（要約）

1. `agent-aider` / `agent-ollama` / `agent-opencode` を**それぞれ独立の CLI として今後も
   使える**ままにしつつ、実装と配布を 1 つに畳む（環境補完 3 重複製の解消を含む）。
2. **統合入口を 1 本**用意し、ヘッドレス実行・対話型・ハーネス・観測・再生を同じ入口から
   引けるようにする。
3. `toolloop` / `statemachine` を agent-loop 専用から解放し、agentcore のハーネスとして
   どこからでも使えるようにする（agent-loop は従来どおり動く）。
4. agent-tools ファミリー（agent-project / agent-flow / agent-amigos / agent-audit /
   agent-loop / agent-dashboard）が、この統合入口を経由してローカル実行系を呼ぶ形に揃える。

---

## 2. ゴールと非ゴール

### ゴール

- **G1**: 統合入口 `agent-run`（新 CLI・zipapp 1 本）。`agent-aider` / `agent-ollama` /
  `agent-opencode` は同一 zipapp への別名（argv[0] ディスパッチ）となり、単独でも従来どおり
  **argv・stdout/stderr 契約完全互換**で使える。
- **G2**: 対話型の統一コマンド `agent-run chat [<cli>] [--model M]`。定義の `interactive`
  ブロックを解決して起動する（ollama は内蔵 TUI、aider は policy つき対話起動）。
- **G3**: `toolloop` / `statemachine` を `agentcore.harness` へ移設し、
  `agent-run harness …` から単独実行できる。agent-loop は薄い委譲で従来コマンドを維持。
- **G4**: 環境補完・adapter 共通処理を `agentcore.hostenv`（仮称）1 実装へ集約。
- **G5**: `agents/*.json` の `command` を統合入口経由の綴りへ正典化し、ファミリーの全経路
  （エンジン・dashboard・loop）が同じ 1 バイナリを踏む。

### 非ゴール

- **N1**: `ollama_loop`（ollama ネイティブの bash ループ）と `harness.toolloop`（single-shot
  CLI への外付け限定契約）の**実装統合はしない**。契約が違う 2 つの道具であり、2026-08-18
  評価 §8.3 の境界（tool-loop を三重化しない——bash 付き反復の所有者は agent-ollama）を
  維持する。統合するのは**置き場と入口**だけ。
- **N2**: クラウド CLI（claude / codex / kiro / copilot / cursor）の adapter 化はしない。
  それらは定義ファイルだけで足りており、`agent-run exec`（§4.6）が定義経由で呼べれば十分。
- **N3**: 新しい汎用 REPL は作らない。`chat` は既存の対話面（ollama_tui / aider 対話 /
  interactive ブロック）への**ルーティング**であり、対話 UI の新実装ではない。
- **N4**: `eval/` / `e2e/` の測定基盤の再編はしない（replay の入口統合のみ行う）。
- **N5**: エンジン内部の agentcli（argv 組み立て・usage 解析）の subprocess 化はしない。
  エンジンは従来どおり in-process で argv を組む。変わるのは組まれた argv が指す先だけ。

---

## 3. 設計の骨子 — busybox 型 1 zipapp + argv[0] ディスパッチ

```
                 ~/.local/bin/
                   agent-run      ┐
                   agent-aider    ├─ 同一 zipapp（hardlink / コピー）
                   agent-ollama   │
                   agent-opencode ┘
                        │
                        ▼  __main__.py: basename(argv[0]) でディスパッチ
        ┌───────────────┼──────────────────────────────┐
        ▼               ▼                              ▼
  agentcore.runcli   adapters                      harness
  （サブコマンド面）   aider_adapter / ollama_adapter   toolloop / statemachine
        │            / opencode_adapter               （agent_loop から移設）
        └────────── agentcore 共通層 ──────────────────┘
             agentcli（定義解決・argv 組み立て）
             hostenv（環境補完・profile 取り込み）★新設・3 重複製の畳み先
             ollama_loop / ollama_tui / ollama_replay / ollama_context …
```

決定は 3 つ:

1. **入口は新名 `agent-run`**（名前の検討は §9）。busybox と同じく、1 つの zipapp を
   複数の名前で置き、`Path(sys.argv[0]).name` でサブコマンドへディスパッチする。
   `agent-aider …` と `agent-run aider …` は完全に同じコードパスに落ちる。
2. **既存名は互換シムではなく本体そのもの**。別ファイルのラッパを挟まないので、
   「シムだけ古い」という状態が構造的に起きない。argv 契約・stdout/stderr 契約
   （`@agent-usage` / `@agent-context` / 打ち切り封筒）・ログ置き場（`~/.agents/logs/`）は
   1 バイトも変えない。
3. **agent-loop は独立 zipapp のまま**（デーモンであり契約が別）。ただし toolloop /
   statemachine の実体は agentcore.harness へ移り、agent-loop はそれを import して
   従来コマンド（`agent-loop statemachine`）を委譲で維持する。

これで「独立して使える」（各名前が単独で自己完結）と「入口は 1 本」（実装・版・配布は 1 つ)
が同時に成立する。片方だけ古い、という不整合クラスが消える。

---

## 4. CLI 面

### 4.1 ディスパッチ規則

```
basename(argv[0])          解決されるサブコマンド
  agent-aider          →   aider
  agent-ollama         →   ollama
  agent-opencode       →   opencode
  agent-run            →   argv[1] をサブコマンドとして解釈
  それ以外（開発時等）   →   argv[1] をサブコマンドとして解釈
```

### 4.2 サブコマンド一覧

| サブコマンド | 実体 | 位置づけ |
|---|---|---|
| `aider …` | `aider_adapter.main` | 既存 `agent-aider` と argv 完全互換 |
| `ollama …` | `ollama_adapter.main` | 既存 `agent-ollama` と完全互換（`--tools` / `--tui` / `--follow` / `--status` / `--replay` / `--context` を含む） |
| `opencode …` | `opencode_adapter.main`（移設） | 既存 `agent-opencode` と完全互換 |
| `chat [<cli>] [--model M]` | 新設（§4.4） | 対話型の統合入口 |
| `harness toolloop …` | `agentcore.harness`（§5） | 限定ツール契約ハーネスの単独実行 |
| `harness statemachine …` | 同上 | ステートマシンハーネスの単独実行 |
| `replay …` | `ollama_replay` | `agent-ollama --replay` の昇格別名（実体は同一） |
| `status` / `follow` | 既存観測モード | `agent-ollama --status/--follow` の別名 |
| `defs [--json] [<name>]` | 新設（薄い） | `agents/*.json` の一覧・検証・実効 argv の表示 |
| `exec <cli> …` | 新設・P3（§4.6） | 定義経由のヘッドレス実行（人のデバッグ用） |

`replay` / `status` / `follow` は**別名であって第 2 実装ではない**。`agent-ollama --replay`
の綴りも残す（外部手順書を壊さない）。

### 4.3 `defs` — 定義の観測

エンジンと dashboard が読むのと同じ `agentcore.agentcli` で解決した結果を人に見せる:

```bash
agent-run defs                 # 解決可能な定義の一覧（探索順・variants 込み）
agent-run defs aider           # aider.json の実効内容と、write/readonly の実効 argv
agent-run defs --json ollama   # 機械可読
```

「同じ定義ファイルがツールによって別の argv になる」事故（agentcli の存在理由）を、
人が同じローダで確認できる口。実装は agentcli の呼び出しと整形だけで、判断は持たない。

### 4.4 `chat` — 対話型の統合入口

```bash
agent-run chat                       # 既定: ollama 定義の interactive（内蔵 TUI）
agent-run chat ollama --model qwen3  # 同上・モデル指定
agent-run chat aider [--model M]     # aider を対話起動（下記）
```

動作は `agents/<cli>.json` の `interactive` ブロックの解決に一本化する:

- **ollama**: `interactive.command` が自分自身（`agent-ollama --tui`）を指すので、
  subprocess を挟まず in-process で `ollama_tui` へ入る。
- **aider**: `aider.json` へ `interactive` ブロックを**新設**する。ヘッドレス用の
  `--message` / `--yes-always` / `--no-stream` / `--no-pretty` を落とし、reliability policy
  （`--agent-policy` 相当の model-settings 合成）と接続補完（hostenv）は adapter が
  ヘッドレスと同じ経路で仕込んだ上で、素の対話 aider を exec する。
  「対話で試したことがヘッドレスで再現しない」を防ぐため、**環境の仕込みは両モードで
  同一実装**を通すこと（受入条件）。
- **interactive ブロックの無い定義**（opencode 等）: 明示エラーで止める（黙って
  ヘッドレスへ倒さない）。追加したくなったら定義に書く——エンジン改修不要という
  既存の性質を保つ。

`ready_pattern` / `busy_pattern` 等の tmux 向けフィールドは agent-loop / dashboard 用で、
`chat` は使わない（人が直接向き合う起動なので待機判定が要らない）。

### 4.5 独立利用の保証

`agent-aider` / `agent-ollama` は今後も**単体で完結**する: 定義解決は従来どおり
（aider adapter は定義を読まず argv をそのまま aider へ渡す、ollama adapter は
`AGENT_OLLAMA_*` と引数だけで動く）。エンジン・ボード・板の存在を前提にしない。
install も `install.sh --only agent-run` で実行系だけ入れられるようにする（§6）。

### 4.6 `exec` — 定義経由のヘッドレス実行（P3・任意）

```bash
echo '本文' | agent-run exec aider --model gemma4:e4b --readonly --file src/a.py
```

エンジンが in-process でやっている「定義 → argv 組み立て → 実行 → usage/エラー分類」を
人が 1 コマンドで再現する口。**エンジンはこれを使わない**（N5）——用途はデバッグの
再現性（「エンジンから呼ぶと落ちるが手で叩くと動く」を潰す）に限る。判断ロジックは
agentcli の既存実装のみで、新しい分岐を持たないこと。

---

## 5. ハーネス統合 — `agentcore.harness`

### 5.1 移設

```
tools/agent-loop/agent_loop/toolloop.py      → tools/agent-tools/agentcore/agentcore/harness/toolloop.py
tools/agent-loop/agent_loop/statemachine.py  → tools/agent-tools/agentcore/agentcore/harness/statemachine.py
                                               （ゴール非依存部分のみ。tmux 表示・pane 配線は agent-loop に残す）
```

- exec 合成断片を**通常のモジュール**へ書き直す（`from agentcore.harness import toolloop`）。
  断片間の暗黙共有名（`_tl_*` グローバル）は明示 import / 関数引数へ畳む。
- `statemachine` の検証・遷移の正典は従来どおり **statemachine-use スキルのスクリプト**
  （`run_machine.py` / `next_state.py`）。移設で正典は動かさない。
- CLI・モデルの解決は従来どおり `agentcore.agentcli` へ委譲（同一パッケージ内になるので
  むしろ自然になる）。
- 限定ツール契約（read_files / write_files / run / final・パス検証・コマンド検証・証跡）は
  **変更しない**。移設 PR に機能変更を混ぜない。

### 5.2 agent-loop 側の残し方

`agent_loop/toolloop.py` / `statemachine.py` は削除し、`agent_loop/__init__.py` の合成順で
`agentcore.harness` から import して既存の `_tl_*` / `_sm_*` 別名を張る互換層に置き換える。
`agent-loop statemachine …` の argv・出力・証跡は不変（既存 `test_statemachine.py` を
そのまま通すことが受入条件）。dashboard のルーチン（`agent-loop statemachine` を叩く経路）も
無改修で動く。

tmux の中で「動いている様子が見える」性質は agent-loop の価値なので agent-loop に残る。
`agent-run harness …` は **tmux なしの素の実行**（stdout/stderr + 証跡ファイル）であり、
同じハーネスの 2 つの見せ方であって 2 実装ではない。

### 5.3 単独実行の口

```bash
agent-run harness statemachine --workflow path/to/machine.yaml \
    --cli aider --model gemma4:e4b [--var k=v …]
agent-run harness toolloop --cli aider --model gemma4:e4b \
    --goal-file goal.md [--max-rounds 8]
```

exit code / 打ち切り封筒 / 証跡の形は agent-loop 経由と同一。これにより
「statemachine を回すためだけに agent-loop のデーモン・tmux・設定ファイルが要る」が消える。

### 5.4 2 つのツールループの役割固定（N1 の明文化）

| | `ollama_loop`（`--tools`） | `harness.toolloop` |
|---|---|---|
| 対象 | ollama 素体（tool-loop 型） | single-shot CLI（aider / readonly ollama） |
| 道具 | bash 1 つ / read 語彙ゲート | read_files / write_files / run / final の 4 つ |
| 所有 | bash 付き自由反復はこちらが唯一の所有者 | 遷移・受入の要る多段工程の供給側 |
| 停止性 | stall / no_progress / context_exhausted | ラウンド上限 + ハーネスタイムアウト |

両者の**語彙は揃える**: 打ち切り分類（`tool_denied` / `no_progress` 等）と進捗ログの
イベント名は agentcore の 1 か所（`vocab` もしくは harness 共通モジュール）に置き、
agent-audit が両経路のセッションを同じ読み方で読めるようにする。将来この 2 つを
統合するかは、台帳で「同じ役割を両経路で流した実測」が並んでから判断する（本設計では
判断しない）。

---

## 6. 配布 — install.sh の変更

1. **zipapp を 1 本追加**: `agent-run`（agentcore 全体 + `__main__.py` のディスパッチャ。
   `--with-rich` は従来どおりこの zipapp への同梱に変わる）。
2. **既存 3 名は同一 zipapp の hardlink**（hardlink 不可の FS ではコピー）で置く:
   `agent-aider` / `agent-ollama` / `agent-opencode`。
   - `aider_adapter.py` の単独ファイルコピー配布は**廃止**。これで agentcore を import
     できるようになり、環境補完 3 重複製を `agentcore.hostenv` 1 実装へ畳める。
   - `tools/opencode/agent-opencode.py` は `agentcore/opencode_adapter.py` へ移設し、
     `tools/opencode/` には委譲シム（既存 install.sh の呼び出しパス維持）とテストを残す。
3. `install.sh --only agent-run` を追加（実行系だけ入れる。エンジン不要のノード向け）。
4. agent-loop zipapp は従来どおり別 build だが、同梱する agentcore に harness が含まれる
   （§5）。**同時に入れ直す**運用は不変。
5. 環境補完の正典 `agentcore/hostenv.py`（`_complete_ollama_env` + profile 取り込み）を
   新設し、ollama_adapter / aider_adapter / opencode_adapter の 3 写しを削除する。
   既存の `test_adapter_env_parity.py`（写し同士の一致を縛るテスト）は「1 実装を 3 者が
   使っていること」を縛るテストへ置き換える。

---

## 7. agent-tools ファミリーの呼び替え

結合点は**定義ファイルだけ**なので、エンジンのコード変更は不要である（C2: データ契約のみ）。

1. `agents/*.json` の `command` 先頭を統合入口の綴りへ正典化する:

   ```
   aider.json:        ["agent-aider", …]      → ["agent-run", "aider", …]
   ollama*.json:      ["agent-ollama", …]     → ["agent-run", "ollama", …]
   opencode.json:     ["agent-opencode"]      → ["agent-run", "opencode"]
   ollama.json interactive: ["agent-ollama", "--tui", …] → ["agent-run", "chat" 相当の綴りにはしない。
                            実行契約は従来どおり adapter 直（"agent-run", "ollama", "--tui", …）]
   ```

   `interactive.command` は tmux から叩かれる契約（ready/busy 判定つき）なので、
   ルーティング層の `chat` ではなく adapter 直の綴りにする——`chat` は人間用の入口であり、
   機械が待機判定つきで扱うのは従来の TUI 契約のまま。

2. これにより **agent-project / agent-flow / agent-amigos / agent-audit / agent-loop /
   agent-dashboard の全経路が同じ 1 バイナリを踏む**。process listing・版の突き合わせ・
   更新漏れの検出が「agent-run 1 本の版を見る」に単純化される。
3. argv0 ディスパッチにより**旧綴りの定義も動き続ける**ので、定義の書き替えは互換リスク
   ゼロの正典化であり、ロールバックは定義を戻すだけ。
4. agent-dashboard の JS ローダのゴールデンテスト（同じ定義から同じ argv）は、定義変更と
   同じ PR で期待値を更新する。

---

## 8. 移行フェーズ

| フェーズ | 内容 | 受入条件 |
|---|---|---|
| **P0 配布統合** | `hostenv` 抽出・opencode/aider adapter の agentcore 移設・busybox zipapp・install.sh 変更 | 既存 3 名の argv/出力契約のゴールデンが不変で通る。env parity テストが「1 実装参照」を縛る形へ置換される |
| **P1 入口面** | `agent-run` サブコマンド面（aider/ollama/opencode/defs/chat）・aider.json の interactive ブロック | `agent-run aider …` と `agent-aider …` が同一結果。`chat aider` の環境仕込みがヘッドレスと同一実装を通ることをテストで縛る |
| **P2 ハーネス移設** | toolloop/statemachine → `agentcore.harness`・agent-loop 委譲・`agent-run harness …` | `test_statemachine.py` 等 agent-loop の既存テストが無改変で通る。tmux なし単独実行の証跡形が agent-loop 経由と一致 |
| **P3 正典化・任意** | `agents/*.json` の command 書き替え + dashboard ゴールデン更新・`exec`・（任意）stub の取り込み | 全エンジンの結合テスト・dashboard ゴールデンが green。旧綴り定義でも動作（互換テスト） |

各フェーズは独立に取り込め、どのフェーズで止めても不整合が残らない（P0 だけでも
3 重複製の解消と配布統合という C7 の主目的は達成される）。

---

## 9. 代替案の検討

### 案 A: aider を入口として variants を統合する（発案の 1 つ）

`agent-aider` を拡張し、JSON/配列役割は内部で ollama variant へ振る案。**採らない**。

- `aider.json` の `variants` が既に planner/judge/split 等を `ollama-json` /
  `ollama-list-thinking` 定義へ委譲している。つまり **variant の統合点は定義層に既にあり**、
  adapter 層へ複製すると振り分けの判断が 2 か所（定義と adapter）に割れる（C7 違反）。
- aider は「対象ファイルが決まった局所編集」の担当（2026-08-18 評価 §8.3 の表）で、
  この役割名を入口全体の名前にすると、探索・JSON 契約・対話まで aider の名で呼ぶことになり
  観測（台帳の `agent_cli` 名）が濁る。

### 案 B: agent-ollama を入口へ拡張する

案 A の逆で、同じ理由で採らない。加えて agent-ollama は「ollama 素体の adapter」として
強い自己記述（R1/R2）を持ち、aider/opencode のラップを混ぜるとその記述が嘘になる。

### 案 C: 独立ラッパスクリプト群 + 共有ライブラリ（現状の延長）

配布を per-CLI zipapp × 4 に増やす案。「片方だけ古い」不整合クラスが残り、install.sh の
build 行列が増えるだけなので採らない。busybox 型なら版は構造的に 1 つ。

### 案 D（採用）: 新名 `agent-run` の busybox 型統合

名前は `agent-run` とする。`agent`（裸）は衝突・検索性が悪く、`agent-local` は
statemachine ハーネスが定義経由で任意 CLI（クラウド含む）を回せるため嘘になる。
`agent-run` はリポジトリ内で未使用（grep 確認済み）で、「実行系の入口」を過不足なく言う。

---

## 10. テスト戦略

1. **argv0 ディスパッチ**: 各 basename → サブコマンドの対応表テスト（`agent-run` 明示形と
   同一の main に落ちること）。
2. **契約ゴールデン**: 既存 3 名の代表 argv → 実行 argv / stdout / stderr（`@agent-usage`
   行・打ち切り封筒）を移行前後で固定。既存 `test_agentcli_*` / adapter テストに追加。
3. **hostenv 1 実装**: 3 adapter が `agentcore.hostenv` を参照することを import 検査で縛る
   （旧 env parity テストの置換）。
4. **ハーネス移設**: agent-loop の `test_statemachine.py` を無改変で通す（移設が挙動を
   変えていない証明）。`agentcore/tests/` へ harness の単体テストを移し、**両テストルートで
   discover する**既存規約（README）に従う。
5. **chat の環境同一性**: aider 対話起動が組む env / model-settings がヘッドレス経路と同一
   関数から出ることをテストで縛る。
6. **定義正典化**（P3）: 新旧両綴りの定義で同じ argv に解決されること。dashboard JS
   ゴールデンの同 PR 更新。

---

## 11. 未解決事項

1. **stub の取り込み**: `tools/agent-loop/stub/kiro-cli-stub.py` を `agent-run stub` として
   同梱するか。プロトコル試験には便利だが、配布物に試験具を混ぜる是非があるので P3 で判断。
2. **Windows（WSL 外）**: hardlink 不可環境はコピーで代替する方針だが、wsl-launcher 経由の
   導線で問題が出ないか P0 実装時に確認。
3. **`ollama_loop` と `harness.toolloop` の将来統合**: §5.4 のとおり台帳の実測が並ぶまで
   保留。判断材料（同役割・両経路の PASS 率と壁時計）を agent-audit で取れる形にだけ
   しておく。
4. **`chat` の dashboard 連携**: dashboard の対話診断が `chat` を使うか従来の
   `interactive.command` 直接かは dashboard 側設計に委ねる（本設計は契約を変えない）。
