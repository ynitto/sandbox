# agent-herd — LAN の ollama を動かす統合入口とハーネス統合の設計

> 作成 2026-08-25
> 対象: `tools/agent-tools/agentcore`（aider_adapter / ollama_adapter / ollama_loop / ollama_tui /
> ollama_replay）/ `tools/opencode/agent-opencode.py` / `tools/agent-loop`（toolloop / statemachine）/
> `agents/*.json` / `tools/agent-tools/install.sh`
> 効く柱・原則: **柱3 / C7 — 実行入口とハーネスの 1 実装化**（同じ契約の写しが 3 か所に
> 分散している状態を正典 1 つへ畳む）。C9（仕事の格付けに応じた振り分け）の入口も 1 本になる。
> 上位文書: [agent-tools コンセプト正典](../designs/agent-tools-concept.md) §7・§8
> **綴りの正典**: [agent-herd 仕様書](../specs/agent-herd-spec.md)
> （本書は「なぜそうするか」、仕様書は「打つと何が起きるか」。実装状況は §8 を見よ）
> 関連: [agent-aider 改良余地の評価](./2026-08-18-agent-aider-improvement-assessment.md) §8.3、
> [agent-ollama 設計](../designs/agent-ollama-design.md)、
> [agent-loop DESIGN](../../tools/agent-loop/DESIGN.md)

---

## 0. 全体像（先に 1 枚）

やることは 1 行で言える。**バラバラに配っている 3 つの実行ファイルを 1 つの zipapp に畳み、
agent-loop の中に閉じているハーネスを外へ出す。** 使う側から見た名前と打ち方は変わらない。

```
  いま — 配布物 4 つ                        これから — 配布物 2 つ、写しゼロ

  ┌──────────────────────────────┐         今までどおりの名前（argv[0] の別名）
  │ agent-ollama                  │         ┌───────────┬────────────┬──────────────┐
  │   zipapp（agentcore 同梱）     │         │agent-aider│agent-ollama│agent-opencode│
  ├──────────────────────────────┤         └─────┬─────┴──────┬─────┴──────┬───────┘
  │ agent-aider                   │               ▼            ▼            ▼
  │   単独ファイル                 │  畳む   ┌──────────────────────────────────────┐
  │   ⚠ agentcore を import 不可   │  ───▶  │ agent-herd    zipapp 1 ファイル        │
  ├──────────────────────────────┤         │   （上の 3 名はこれへの hardlink）      │
  │ agent-opencode                │         │  ┌────────┬──────────┬─────────────┐  │
  │   単独ファイル ⚠ 同上          │         │  │adapters│harness ★ │  agentcli   │  │
  ├──────────────────────────────┤         │  ├────────┴──────────┴─────────────┤  │
  │ agent-loop                    │         │  │ hostenv — 環境補完はここ 1 実装だけ│  │
  │   zipapp                      │         │  └──────────────────────────────────┘  │
  │   ▸ harness が中に閉じている    │         │  サブコマンド: aider · ollama ·        │
  │     （外から呼べない）          │         │    opencode · chat · harness ·        │
  └──────────────────────────────┘         │    defs · exec · 観測                 │
                                            └──────────────────────────────────────┘
  ⚠ 環境補完のコードが 3 か所に複製            ┌──────────────────────────────────────┐
  ⚠ ハーネスは agent-loop 経由でしか呼べない   │ agent-loop  zipapp                    │
  ⚠ 対話の入口が CLI ごとにばらばら            │   ★ harness を import して従来コマンド │
  ⚠ 対話で試したことがヘッドレスで再現しない     │     を維持                            │
                                            └──────────────────────────────────────┘
                                            ✓ 写しゼロ・ハーネスは tmux 無しで単独実行
                                            ✓ 対話も 1 本
```

**変わるのは配り方と置き場で、使い方ではない。** `agent-aider …` は今までと同じ argv・
同じ stdout / stderr で動き続ける（別名は互換シムではなく本体そのものなので、
「シムだけ古い」が構造的に起きない）。★ 印の harness が agent-loop から出てくるのが、
この統合のもう半分である。

| | |
|---|---|
| **畳む理由** | 環境補完（`OLLAMA_API_BASE` / `NO_PROXY`）が 3 ファイルに複製され、コード中に「直すときは 3 箇所を揃えること」と書いてある状態（§1.1） |
| **出す理由** | ゴール非依存に書かれた harness が agent_loop の exec 合成断片で、デーモンと tmux 抜きには呼べない（§1.2） |
| **壊さないもの** | 既存 3 名の argv・stdout / stderr 契約・ログ置き場、および `agent-loop` の全コマンドと既存テスト |

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

- **G1**: 統合入口 `agent-herd`（新 CLI・zipapp 1 本。命名の根拠は §9.1 — LAN に飼った
  ollama の群れを 1 つの入口から束ねて動かす）。`agent-aider` / `agent-ollama` /
  `agent-opencode` は同一 zipapp への別名（argv[0] ディスパッチ）となり、単独でも従来どおり
  **argv・stdout/stderr 契約完全互換**で使える。
- **G2**: 対話型の統一コマンド `agent-herd chat [<cli>] [--model M]`。定義の `interactive`
  ブロックを解決して起動する（ollama は内蔵 TUI、aider は policy つき対話起動）。
- **G3**: `toolloop` / `statemachine` を `agentcore.harness` へ移設し、
  `agent-herd harness …` から単独実行できる。agent-loop は薄い委譲で従来コマンドを維持。
- **G4**: 環境補完・adapter 共通処理を `agentcore.hostenv`（仮称）1 実装へ集約。
- **G5**: `agents/*.json` の `command` を統合入口経由の綴りへ正典化し、ファミリーの全経路
  （エンジン・dashboard・loop）が同じ 1 バイナリを踏む。

### 非ゴール

- **N1**: `ollama_loop`（ollama ネイティブの bash ループ）と `harness.toolloop`（single-shot
  CLI への外付け限定契約）の**実装統合はしない**。契約が違う 2 つの道具であり、2026-08-18
  評価 §8.3 の境界（tool-loop を三重化しない——bash 付き反復の所有者は agent-ollama）を
  維持する。統合するのは**置き場と入口**だけ。
- **N2**: クラウド CLI（claude / codex / kiro / copilot / cursor）の**呼び出し経路は
  agent-herd に通さない**（adapter 化もしない）。それらは定義ファイルだけで足りており、
  `agents/*.json` の `command` は素の CLI を指したままにする。判断の理由と、既に統一されて
  いる面・将来 adapter 化する基準は §9.3。
- **N3**: 新しい汎用 REPL は作らない。`chat` は既存の対話面（ollama_tui / aider 対話 /
  interactive ブロック）への**ルーティング**であり、対話 UI の新実装ではない。
- **N4**: `eval/` / `e2e/` の測定基盤の再編はしない（replay の入口統合のみ行う）。
- **N5**: エンジン内部の agentcli（argv 組み立て・usage 解析）の subprocess 化はしない。
  エンジンは従来どおり in-process で argv を組む。変わるのは組まれた argv が指す先だけ。

---

## 3. 設計の骨子 — busybox 型 1 zipapp + argv[0] ディスパッチ

```
                 ~/.local/bin/
                   agent-herd     ┐
                   agent-aider    ├─ 同一 zipapp（hardlink / コピー）
                   agent-ollama   │
                   agent-opencode ┘
                        │
                        ▼  __main__.py: basename(argv[0]) でディスパッチ
        ┌───────────────┼──────────────────────────────┐
        ▼               ▼                              ▼
  agentcore.herdcli  adapters                      harness
  （サブコマンド面）   aider_adapter / ollama_adapter   toolloop / statemachine
        │            / opencode_adapter               （agent_loop から移設）
        └────────── agentcore 共通層 ──────────────────┘
             agentcli（定義解決・argv 組み立て）
             hostenv（環境補完・profile 取り込み）★新設・3 重複製の畳み先
             ollama_loop / ollama_tui / ollama_replay / ollama_context …
```

入口から実行先までを 1 枚で見ると、**分岐は `argv[0]` の 1 回だけ**で、あとは定義が決める:

```
  agent-herd    agent-aider   agent-ollama   agent-opencode
      └─────────────┴──────┬──────┴───────────────┘
                           ▼  basename(argv[0]) — 実体は 1 ファイル
      ┌────────────────────────────────────────────────┐
      │ サブコマンド面                                    │
      │  aider │ ollama │ opencode │ chat │ harness │ defs │ status/follow
      └────────────────────┬───────────────────────────┘
                           ▼  CLI 名と用途を渡す
      ┌────────────────────────────────────────────────┐
      │ agentcore.agentcli — 定義の唯一のローダ            │
      │  load_cli() │ resolve_variant(cli, purpose) │ headless_cmd() → argv
      └──────────┬─────────────────────────┬───────────┘
                 ▼ argv を組む              ▼
      ┌──────────────────────────┐  ┌──────────────────┐
      │ cost 0 — LAN の ollama    │  │ cost 1 — 雲の CLI │
      │  aider / ollama /         │  │  claude / codex /│
      │  ollama-json / -list /    │  │  kiro / copilot /│
      │  -list-thinking / -read / │  │  cursor          │
      │  -verify / opencode       │  │                  │
      └──────────┬───────────────┘  └────────┬─────────┘
                 ▼                            ▼
      LAN 上の ollama サーバ（別 PC 可）      各社の API
      OLLAMA_API_BASE / NO_PROXY を補完       個人の資格情報のまま
```

決定は 3 つ:

1. **入口は新名 `agent-herd`**（名前の検討は §9）。busybox と同じく、1 つの zipapp を
   複数の名前で置き、`Path(sys.argv[0]).name` でサブコマンドへディスパッチする。
   `agent-aider …` と `agent-herd aider …` は完全に同じコードパスに落ちる。
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
  agent-herd           →   argv[1] をサブコマンドとして解釈
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

サブコマンド名の空間を何にするか（adapter 名だけか、定義名も載せるか）は §4.7 で決める。
実際の綴りは §4.8、全サブコマンド共通の契約と境界事例の扱いは §4.9。

### 4.3 `defs` — 定義の観測

エンジンと dashboard が読むのと同じ `agentcore.agentcli` で解決した結果を人に見せる:

```bash
agent-herd defs                 # 解決可能な定義の一覧（探索順・variants 込み）
agent-herd defs aider           # aider.json の実効内容と、write/readonly の実効 argv
agent-herd defs --json ollama   # 機械可読
```

「同じ定義ファイルがツールによって別の argv になる」事故（agentcli の存在理由）を、
人が同じローダで確認できる口。実装は agentcli の呼び出しと整形だけで、判断は持たない。

### 4.4 `chat` — 対話型の統合入口

```bash
agent-herd chat                       # 既定: ollama 定義の interactive（内蔵 TUI）
agent-herd chat ollama --model qwen3  # 同上・モデル指定
agent-herd chat aider [--model M]     # aider を対話起動（下記）
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
install も `install.sh --only agent-herd` で実行系だけ入れられるようにする（§6）。

### 4.6 `exec` — 定義経由のヘッドレス実行（P3・任意）

```bash
echo '本文' | agent-herd exec aider --model gemma4:e4b --readonly --file src/a.py
```

エンジンが in-process でやっている「定義 → argv 組み立て → 実行 → usage/エラー分類」を
人が 1 コマンドで再現する口。**エンジンはこれを使わない**（N5）——用途はデバッグの
再現性（「エンジンから呼ぶと落ちるが手で叩くと動く」を潰す）に限る。判断ロジックは
agentcli の既存実装のみで、新しい分岐を持たないこと。

### 4.7 呼び出しの 2 系統 — adapter 直と定義経由

インターフェースの背骨は 1 本の線である: **サブコマンドは adapter の名前であって定義の名前ではない。**

| | adapter サブコマンド | `exec`（定義経由） |
|---|---|---|
| 綴り | `agent-herd ollama --format json qwen3` | `agent-herd exec ollama-json --model qwen3` |
| 引数 | adapter の生フラグをそのまま渡す | agentcli が定義から argv を組む |
| 定義を読むか | 読まない（単体で完結する） | 読む（`variants` / `readonly` も効く） |
| 使う場面 | 定義ファイルの `command`・既存手順書・人の直叩き | 人がエンジンの実行を手で再現する（デバッグ） |
| 名前の空間 | `aider` / `ollama` / `opencode` の 3 つだけ | `agents/*.json` の全定義（13 件） |

`ollama-json` / `ollama-list` / `ollama-verify` は**定義**であって adapter ではない
（実体はどれも ollama adapter にフラグを足したもの）。だからサブコマンドには載せず、
`exec` から引く。誤って `agent-herd ollama-json` と打った場合は、黙って別解釈せず
`exec` を案内する明示エラーで止める（§4.9）。

### 4.8 起動例（場面別）

```bash
# ── ヘッドレス（定義ファイルの command が組む形。エンジンが叩く）
agent-herd ollama --think off --format json gemma4:e4b < prompt.txt
agent-herd aider --agent-policy gemma4-e4b-reliability-v1 \
                 --model ollama_chat/gemma4:e4b \
                 --file src/a.py --read docs/spec.md --message "…"

# ── 旧綴りは完全互換（別名なので同じコードパスに落ちる）
agent-ollama --think off --format json gemma4:e4b < prompt.txt

# ── 対話（人が向き合う）
agent-herd chat                            # 既定は ollama の内蔵 TUI
agent-herd chat ollama --model qwen3
agent-herd chat aider  --model gemma4:e4b  # policy と接続補完はヘッドレスと同一経路

# ── ハーネス（tmux もデーモンも要らない）
agent-herd harness statemachine --cli aider --model gemma4:e4b \
                                --workflow methods/review.machine.yaml \
                                --var target=src/a.py
agent-herd harness toolloop --cli aider --goal-file goal.md --max-rounds 8

# ── 観測とデバッグ
agent-herd status                          # 進捗を 1 行 JSON で（外部監視向け）
agent-herd follow                          # 同じものを人が読む形で追尾
agent-herd defs                            # 解決できる定義の一覧
agent-herd defs aider --json               # 実効 argv（エンジンが組むのと同じもの）
agent-herd exec ollama-json --model gemma4:e4b < prompt.txt
agent-herd replay --arm model=gemma4:e4b,format=json --replay-limit 20
```

### 4.9 共通契約と、実装前に潰す穴

**サブコマンドをまたいで同じもの**（既存 adapter の契約をそのまま入口の契約に昇格させる）:

- **stdout は本文だけ。** 診断は 1 バイトも混ぜない
- **stderr は診断と計測。** `@agent-usage tokens_in=… tokens_out=…`（その実行の累計）と
  `@agent-context used=… limit=…`（いま文脈がどれだけ埋まっているか）は意味が違うので行を分ける
- **完走しなかったら本文末尾に機械可読の封筒** `{"ok": false, "issues": [...]}`
  （`--format json` のときは足さない）。判定に使うのは封筒であって人向けの注記ではない
- **exit code は実体のものをそのまま返す。** 入口で丸めない
- **ログは `~/.agents/logs/<adapter>/` へ JSONL 追記。** agent-audit がセッションとして
  読める形を変えない

**実装前に潰す穴**:

| 穴 | 決め |
|---|---|
| 入口自身のフラグ | `agent-herd` は自分のフラグを持たない（`--help` / `--version` のみ）。サブコマンド以降は実体へ素通し。曖昧なときは `--` で区切る |
| `--tui` と `chat` | 両方残す。`ollama --tui` は**機械契約**（`interactive.command` が tmux から ready/busy 判定つきで叩く）、`chat` は**人の口**。実体は同じ `ollama_tui` |
| `--help` の階層 | `agent-herd --help` はサブコマンド一覧だけ。詳細は `agent-herd ollama --help`（= 既存 `agent-ollama --help` と同一本文） |
| 未知のサブコマンド | 定義名（`ollama-json` 等）なら `exec` を案内して終了。それ以外は一覧を出して終了。**黙って別解釈しない** |
| `chat` の対象が対話を持たない | 定義に `interactive` が無ければ明示エラー。黙ってヘッドレスへ倒さない（追加は定義に書く＝エンジン改修不要を保つ） |

---

## 5. ハーネス統合 — `agentcore.harness`

### 5.1 移植（移行ではない）

**まず移植する。元は消さない。** 段取りを 2 つに割る:

```
段1 ポーティング（実施済）
  agent_loop/toolloop.py      ──コピー──▶ agentcore/harness/toolloop.py
  agent_loop/statemachine.py  ──コピー──▶ agentcore/harness/statemachine.py
  agent_loop は自分の断片を持ち続ける（コマンドも証跡も 1 バイトも変わらない）
        │
        │  写しが黙ってずれないよう AST パリティテストで縛る
        ▼
段2 移行（実施済）
  本文を _toolloop_body.py / _statemachine_body.py の 1 か所へ畳み、
  agentcore.harness と agent_loop の両方がそれを exec する
```

段 1 で agent-loop を触らないので、**回帰の可能性がそもそも無い**（agent-loop から見ると
何も変わっていない）。段 1 だけで「tmux もデーモンも無しにハーネスを回せる」という目的は
達成される。

### 5.1.1 段2 で分かったこと — import 委譲にはできない

段2 の素直な形は「`agent_loop` の断片を消して `from agentcore.harness import …` へ
置き換える」だが、**それはできない**。agent-loop のテストは
`mock.patch.object(agent_loop, "_tl_run_agent")` のように**共有名前空間を差し替える**
（実測 57 箇所・6 ファイル）。import で委譲すると関数の `__globals__` が agentcore 側に
なるため、その差し替えが効かなくなる——しかも「パッチが当たらない」は静かな失敗で、
テストが本物の CLI を起動しにいく形で出る。

そこで**本文をデータとして共有する**形にした:

```
  agentcore/harness/_toolloop_body.py   ← 本文はここ 1 か所（直接 import しない）
        │                    │
        │ exec               │ exec
        ▼                    ▼
  agentcore.harness       agent_loop の共有名前空間
  .toolloop（自分の         （従来どおりの合成。__globals__ は agent_loop のまま
   名前空間へ）              なので 57 箇所の差し替えがそのまま効く）
```

exec による合成は agent_loop 自身の既存の作法（`__init__.py` が断片を順に exec する）なので、
新しい仕組みを持ち込んではいない。2 つの実行系は本文を共有するが名前空間は別で、互いの
モンキーパッチは干渉しない。継ぎ目（記帳と control 解決）は `harness.set_hooks` で
agent-loop 側の実装を差し込む——差し込まないと台帳が静かに空になるので、これもテストで縛る。

**結果: agent-loop の 554 テストが 1 行も変えずに通る。** 段1 で置いた AST パリティテストは
役目を終え、「本文が 1 か所であること」「共有名前空間の意味論が保たれていること」を縛る
`test_harness_shared_body.py` へ作り替えた。

- exec 合成断片を**通常のモジュール**にする。ただし本文は**逐語コピー**で、変えたのは
  断片が共有名前空間から借りていた名前の供給だけ（借用は stdlib を除くと 4 つしか無かった:
  `agent_home_subdir` / `_import_agentcli` / `_node_budget_record` /
  `_control_policy_decision`）。逐語コピーにしておくと、元との一致を AST で機械的に
  突き合わせられる——書き直すと写しがずれても誰も気づけない。
- 借用のうち記帳（`_node_budget_record`）と control 解決（`_control_policy_decision`）は
  agent-loop 固有の状態を触るので、**既定を「何もしない / None」にした差し込み口**
  （`agentcore.harness.set_hooks`）にした。移植先が黙って agent-loop の台帳へ書くより、
  書かないほうを既定にする。
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
`agent-herd harness …` は **tmux なしの素の実行**（stdout/stderr + 証跡ファイル）であり、
同じハーネスの 2 つの見せ方であって 2 実装ではない。

### 5.3 単独実行の口

```bash
agent-herd harness statemachine --workflow path/to/machine.yaml \
    --cli aider --model gemma4:e4b [--var k=v …]
agent-herd harness toolloop --cli aider --model gemma4:e4b \
    --goal-file goal.md [--max-rounds 8]
```

exit code / 打ち切り封筒 / 証跡の形は agent-loop 経由と同一。これにより
「statemachine を回すためだけに agent-loop のデーモン・tmux・設定ファイルが要る」が消える。

移設しても**判定は 1 か所のまま**である。`run_prompt()`（現 `toolloop.py:1188`）が見るのは
定義の `headless_autonomy` ただ 1 つで、そこから 2 経路に分かれる:

```
        agent-herd harness …（agent-loop 経由でも同じ関数を通る）
                          │
                          ▼
            run_prompt() — headless_autonomy を見る【唯一の分岐点】
                 ┌────────┴────────┐
       single-shot│                 │tool-loop
                 ▼                 ▼
   層3 run_goal()              層2 run_cli_loop()
   引き具を付ける                引き具を付けない（1 回呼ぶだけ）
   ┌─────────────────────┐     ┌──────────────────────────┐
   │ read_files │ write_files │  │ CLI 内部のツールループへ素通し │
   │ run        │ final       │  └──────────────────────────┘
   └─────────────────────┘     ollama / ollama-read / opencode  (cost 0)
   aider / ollama-json /         claude / codex / kiro /
   ollama-list / -list-thinking  copilot / cursor            (cost 1)
   / ollama-verify
   ── 5 件すべて cost 0（LAN の ollama）  ── 雲が通るのはこの経路だけ
```

**限定 4 ツール契約を受け取る `single-shot` 定義は、現時点で 5 件とも cost 0 のローカル推論**
である（雲の CLI は 5 件とも `tool-loop` なので層 2 を素通りし、引き具に触れない）。
この事実が §9 の命名判断の根拠になる。

### 5.5 エンジンに散っていたローカル LLM 応答の手当て（実施済）

ハーネス以外にも、**ローカル LLM の応答を engine ごとに手当てしている実装**が散っていた。
調査結果:

| 実装 | 状態 | 対処 |
|---|---|---|
| `agent_flow/util.py` の `extract_json` / `unwrap_list` / `extract_list` | 実装本体 | `extract_json` / `unwrap_list` を `agentcore.llmjson` へ移し、委譲に |
| `agent_amigos/util.py` の `extract_json` | **agent-flow の写し**（違いは例外文だけ） | 同上 |
| `agent-tools/eval/engine.py` の 3 関数 | 既に agent-flow へ委譲する薄い層（写しではない） | **触らない** |
| `agent-audit/readers.py` の ollama 言及 | ログの読み手であってハーネスではない | 触らない |

`unwrap_list` が要るのは ollama の JSON モードが**トップレベルをオブジェクトに固定する**
ためで、engine 側の仕様であってモデルの能力ではない。この手当てが engine ごとにずれると、
**同じモデル応答が経路によって通ったり落ちたりする**——しかも落ちた側は「モデルが悪い」に
見えるので原因が分からない。`hostenv` と同型なので同じように 1 実装へ畳んだ。

`extract_list`（Thinking モデルが返す外側の配列なしの形を畳む）は agent-flow の split 契約に
固有なので移していない。移すなら「配列契約を持つ engine が 2 つ目に現れたとき」。

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

1. **zipapp を 1 本追加**: `agent-herd`（agentcore 全体 + `__main__.py` のディスパッチャ。
   `--with-rich` は従来どおりこの zipapp への同梱に変わる）。
2. **既存 3 名は同一 zipapp の hardlink**（hardlink 不可の FS ではコピー）で置く:
   `agent-aider` / `agent-ollama` / `agent-opencode`。
   - `aider_adapter.py` の単独ファイルコピー配布は**廃止**。これで agentcore を import
     できるようになり、環境補完 3 重複製を `agentcore.hostenv` 1 実装へ畳める。
   - `tools/opencode/agent-opencode.py` は `agentcore/opencode_adapter.py` へ移設し、
     `tools/opencode/` には委譲シム（既存 install.sh の呼び出しパス維持）とテストを残す。
3. `install.sh --only agent-herd` を追加（実行系だけ入れる。エンジン不要のノード向け）。
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
   aider.json:        ["agent-aider", …]      → ["agent-herd", "aider", …]
   ollama*.json:      ["agent-ollama", …]     → ["agent-herd", "ollama", …]
   opencode.json:     ["agent-opencode"]      → ["agent-herd", "opencode"]
   ollama.json interactive: ["agent-ollama", "--tui", …] → ["agent-herd", "chat" 相当の綴りにはしない。
                            実行契約は従来どおり adapter 直（"agent-herd", "ollama", "--tui", …）]
   ```

   `interactive.command` は tmux から叩かれる契約（ready/busy 判定つき）なので、
   ルーティング層の `chat` ではなく adapter 直の綴りにする——`chat` は人間用の入口であり、
   機械が待機判定つきで扱うのは従来の TUI 契約のまま。

2. これにより **agent-project / agent-flow / agent-amigos / agent-audit / agent-loop /
   agent-dashboard の全経路が同じ 1 バイナリを踏む**。process listing・版の突き合わせ・
   更新漏れの検出が「agent-herd 1 本の版を見る」に単純化される。
3. argv0 ディスパッチにより**旧綴りの定義も動き続ける**ので、定義の書き替えは互換リスク
   ゼロの正典化であり、ロールバックは定義を戻すだけ。
4. agent-dashboard の JS ローダのゴールデンテスト（同じ定義から同じ argv）は、定義変更と
   同じ PR で期待値を更新する。

### 7.0 実施した書き替え（P3・実装済）

ローカル実行系の 8 定義が統合入口を指すようになった。クラウド 5 件は N2 のとおり素のまま:

| 定義 | 旧 | 新 |
|---|---|---|
| `aider`（`command` と `interactive.command`） | `agent-aider …` | `agent-herd aider …` |
| `ollama`（同上）/ `ollama-json` / `ollama-list` / `ollama-list-thinking` / `ollama-read` / `ollama-verify` | `agent-ollama …` | `agent-herd ollama …` |
| `opencode` | `agent-opencode` | `agent-herd opencode` |
| `claude` / `codex` / `kiro` / `copilot` / `cursor` | 素の CLI | **変更なし**（§9.3） |

これで agent-project / agent-flow / agent-amigos / agent-audit / agent-loop /
agent-dashboard の全経路が同じ 1 バイナリを踏む。ゴールデンテストは Python
（`test_agentcli` / `test_ollama_adapter`）と dashboard（`agent-cli-golden` /
`agent-assist` / `routine-agent-cli`）の両方を同じ変更で更新した。

テストが自作する**合成定義**（`execution-policy` / `orchestration` の fixture）は
旧綴りのまま残してある——ローダが入口の綴りに依存しないことの証拠になるからで、
書き替える必要は無い。

### 7.1 variant は入口を増やさない

variant を「入口の分岐」と読むと設計を誤る。`resolve_variant()` が返すのは**別の定義名**で
あって別の実行系ではない。返された定義は同じローダを通り、同じ zipapp を指す argv になる:

```
  agent-herd ──▶ resolve_variant(ollama, split) ──▶ ollama-list.json ──▶ --format array
      ▲                variants["split"] を引くだけ      別の定義名が返る          │
      └──────────────────────────────────────────────────────────────────────┘
         組み上がった argv は同じ zipapp を指す — 入口は増えず、定義だけが増える
```

だから役割をいくら増やしても入口は 1 本のままで、増えるのは `agents/*.json` だけ
（エンジンの改修は要らない）。同じことが aider 側でも既に起きている:

```
  aider.json（single-shot・variants 13 役割）
      ├─ planner / judge / review / plan / …  ──▶ ollama-json
      └─ split                                ──▶ ollama-list-thinking
```

**「aider を入口にして variant を統合する」は定義層で既に済んでいる**——これを adapter 層へ
複製すると振り分けの判断が定義と実装の 2 か所に割れる（§9 案 A の不採用理由）。統合すべきは
入口と配布であって、振り分けの判断ではない。

---

## 8. 移行フェーズ

| フェーズ | 内容 | 受入条件 |
|---|---|---|
| **P0 配布統合** ✅ **実装済** | `hostenv` 抽出・opencode/aider adapter の agentcore 移設・busybox zipapp・install.sh 変更 | 既存 3 名の argv/出力契約のゴールデンが不変で通る。env parity テストが「1 実装参照」を縛る形へ置換される |
| **P1 入口面** ✅ **実装済** | `agent-herd` サブコマンド面（aider/ollama/opencode/defs/exec/chat/観測）・aider.json の interactive ブロック | `agent-herd aider …` と `agent-aider …` が同一結果。`chat aider` の環境仕込みがヘッドレスと同一実装を通ることをテストで縛る |
| **P2 ハーネス移植** ✅ **実装済**（段1） | toolloop/statemachine を `agentcore.harness` へ**コピー**・`agent-herd harness …`・AST パリティテスト。**agent_loop は無改変** | agent-loop の既存テストが無改変で通る（触っていないので当然通る）。移植先が単体 import でき、zipapp から回る |
| **P2 段2 移行** ⬜ 未着手 | agent_loop の断片を消して `agentcore.harness` への委譲へ | `test_statemachine.py` 等 agent-loop の 554 テストが無改変で通る |
| **P1.5 弁別子の是正** ✅ **実装済** | dashboard の `!spec.interactive` を `needsHeadlessHarness`（`headless_autonomy` で判定）へ・`aider.json` に `interactive` を追加 | 既存全定義で判定が従来と同じ。`chat aider` が policy つきで起動し、aider の定型業務はハーネスのまま |
| **P3 正典化** ✅ **実装済** | `agents/*.json` 8 件の `command` / `interactive.command` を `["agent-herd", "<sub>", …]` へ・Python と dashboard のゴールデン更新 | 全エンジンの結合テスト・dashboard ゴールデンが green。旧綴りの別名も argv[0] 分岐で従来どおり動く |

各フェーズは独立に取り込め、どのフェーズで止めても不整合が残らない（P0 だけでも
3 重複製の解消と配布統合という C7 の主目的は達成される）。

### 8.1 P0 / P1 の実装（2026-08-25）

入っているもの:

- `agentcore/hostenv.py` — 環境補完の唯一の実装。`ollama_adapter` / `aider_adapter` /
  `opencode_adapter` は再輸出するだけになり、3 重複製が消えた
- `agentcore/opencode_adapter.py` — `tools/opencode/agent-opencode.py` から移設。
  元の場所は開発木用の委譲シム（既存テストはこのシム経由で無改変のまま通る）
- `agentcore/herdcli.py` — argv[0] ディスパッチと `aider` / `ollama` / `opencode` /
  `chat` / `defs` / `exec` / `status` / `follow` / `replay` / `harness`（§9 参照）
- `install.sh` — `agent-herd` zipapp と 3 名のハードリンク、`--only agent-herd`
- `tools/opencode/install.sh` — 単体ファイルのコピーから自己完結 zipapp へ

`harness` は**サブコマンドとして存在するが実行しない**。呼ぶと終了コード 2 で
`agent-loop statemachine` を案内する——設計書を読んで打った人に「未知のサブコマンド」と
返すのは不親切なので、所在だけは答える。中身は P2 で入る。

### 8.2 `interactive` の二重の意味を解いた（P1.5・実装済）

`agents/aider.json` に `interactive` を足すと、**定型業務の実行経路が黙って切り替わる**
という制約に最初の実装で当たった。agent-dashboard が `spec.interactive` の**有無**を
「対話ペインで駆動できる CLI か」の代理として読み、無い CLI（aider・素の ollama）を
agent-loop の statemachine ハーネスへ回していたためである。CI の `dashboard (npm test)` が
検出し、一度は aider の `interactive` を戻した。

**P1.5 で正しい弁別子へ直した。** `cowork.js` に `needsHeadlessHarness(spec)` を置き、
3 箇所の `!selected.spec.interactive` をそれへ置き換えた:

```js
function needsHeadlessHarness(spec) {
  if (!spec || !spec.interactive) return true;            // 対話面が無ければ駆動しようが無い
  return String(spec.headlessAutonomy || 'single-shot') === 'single-shot';
}
```

2 つの宣言は別物である——`interactive` は「対話面を提供するか」、`headless_autonomy` は
「自分で探索・実行まで回せるか」。aider は**両方が真になりうる唯一の CLI**（対話もできるが
ヘッドレスでは single-shot）で、そこで代理が壊れた。

この述語は既存の全定義に対して**従来と同じ判定を返す**（`interactive` を持ちかつ
`single-shot` なのは aider だけで、それは足す前は前段の条件で拾われていた）。つまり挙動が
変わるのは aider に対話面が付いたときだけで、そのとき「定型業務はハーネスのまま」という
正しい側へ倒れる。固定は dashboard の `state-machine-window.test.js` と、Python 側の
`test_herdcli.ChatTests`。

**P2 を分けた理由****P2 を分けた理由**: `toolloop.py`（1,275 行）と `statemachine.py`（866 行）は
agent_loop の **exec 合成断片**で、暗黙の共有グローバル（`_tl_*` / `_sm_*`）に依存している。
通常モジュールへ書き直すのは 2,100 行規模の振る舞い保存リファクタで、`test_statemachine.py`
を含む agent-loop の 554 テストを合格ゲートにした独立の変更として扱うべきである。配布統合
（P0）と入口面（P1）に混ぜると、回帰が出たときにどちらの変更が原因か切り分けられない。

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

### 案 D（採用）: 新名 `agent-herd` の busybox 型統合

busybox 型そのものは案 A〜C の弱点を構造的に消す。残るのは名前で、これは §9.1 で決める。

### 9.1 入口の名前 — 何を言うべきか

この入口が抱える 3 つの adapter は**すべて LAN 上の ollama を叩く**——`agent-aider` は
`ollama_chat/gemma4:e4b` へ、`agent-opencode` は「別 PC の ollama」へ（同ファイルの docstring）、
`agent-ollama` は言うまでもない。名前はここを言うべきである。

しかも「localhost」ではなく「LAN」であることが実装に効いている。3 adapter がそろって
`OLLAMA_API_BASE` / `NO_PROXY` の補完を持つのは、推論サーバが別マシンに居て、補完を怠ると
プロキシへ流れて 504 になるからだ（この補完の 3 重複製こそ §1.1 で畳もうとしているもの）。
名前が**ネットワークの向こう側**を含意できると、この性質と噛み合う。

| 候補 | 由来 | 長所 | 弱点 |
|---|---|---|---|
| **`agent-herd`（採用）** | 群れ／群れを御す | 名詞と動詞を兼ね「動かす」が名前に入る。**引き具（ハーネス）と喩えが繋がる**。短く、リポジトリ内で未使用 | 「LAN」を字面では言わない |
| `agent-farm` | 農場 ＋ server farm | 「LAN 上の計算機群」が技術語としてそのまま通る二重の意味 | 規模が大きく響く（実体は 1〜2 台） |
| `agent-pasture` | 放牧地 | 「手元に放してある群れ」の情景が最も明確 | 7 文字。毎日打つ名前としては長い |
| `agent-lan` | そのまま LAN | 最も直接的で誤読の余地がない | ollama も「動かす」も言わない。汎用インフラ語と紛れる |
| `agent-rig` | 推論リグ | 手元のハードウェアで回している感触。短い | llama の含意なし。mining rig を連想させる |
| `agent-barn` | 納屋 | 短く記憶に残る | 「仕舞う場所」であって「動かす」ではない |
| `agent-onprem` | オンプレミス | 意味は正確 | 企業 SaaS の語。個人 PC で回す実態と語感が合わない |
| `agent-run`（前案） | 実行系の入口 | 中立で、どの経路にも嘘にならない | 「LAN の ollama を動かす」という一番言うべきことを言わない |

`agent-herd` を採る決め手は、**喩えが実装の分岐と一致していること**である。力の弱い群れには
引き具を付けて荷を引かせ、自力で走れる個体には付けない——これは比喩ではなく、`run_prompt()`
が `headless_autonomy` で実際に行っている分岐そのもの（§5.3 の図）。名前とコードが同じ
モデルを指しているので、読む側の頭の中の像が実装からずれない。

なお `agent`（裸）は衝突・検索性が悪く、`agent-local` は「localhost」と読まれるうえ
LAN の含意が消えるので採らない。リポジトリの命名は `agent-amigos` /
`multi-agent-shogun-kiro` / `codd-gate` と比喩を許す家風があり、`agent-herd` はその流儀に収まる。

### 9.2 名前が背負えない唯一の経路

層 2（`run_cli_loop`）は雲の CLI へ素通しするので、ollama 由来の名前は**この 1 経路だけ**を
言い落とす。取れる道は 2 つで、**(a) を採る**。

- **(a) 重心で名づけ、素通しを明記する。** 引き具を必要とするのはローカル群だけで、層 2 は
  「引き具を付けない」経路だと本設計に書く（§5.3）。喩えとしても正しく、実装の分岐とも
  一致する。層 3 を通る 5 定義が全て cost 0 であることがこの命名を支える。
- (b) ハーネスだけ別の入口に置く。正確だが、いま畳もうとしている分裂を作り直すことになる。

### 9.3 クラウド CLI の呼び出し口は統一しない（N2 の決定記録）

`agent-herd` という名前を選ぶと「では claude / codex / kiro / copilot / cursor も
この入口から呼ぶべきか」という問いが立つ。**呼び出し経路（`agents/*.json` の `command`）は
統一しない。** 理由は 4 つ。

1. **adapter は「素の argv で表せないもの」があるときだけ置く**、がこのリポジトリの既存判断
   である（`agent-opencode.py` の冒頭注記——実測 usage・落ちない失敗・本文抽出の 3 つとも
   素の argv では表せないから adapter が居る）。クラウド CLI は定義ファイルだけで契約に
   適合できている。そこへ agent-herd を挟むと**判断ゼロの素通しラッパ**が 1 hop 増えるだけで、
   統合が消す種類の重複（写し）を何も消さない。
2. **耐障害の向きが逆転する。** ローカル実行系は「クラウドが使えないときに作業を止めない」
   バックアップ（R1）である。クラウドの呼び出しを agent-herd 経由にすると、バックアップ側の
   バイナリがクラウド実行の必須依存になり、agent-herd の導入ミス 1 つで**全経路**が止まる。
   いまクラウド経路は素の CLI だけで動き、この独立性が保険として機能している。
3. **名前が嘘になる。** §9.1 で agent-herd を選んだ根拠は「LAN に飼った群れを束ねて動かす」
   が実体と一致することだった。claude を herd 経由で呼ぶと、その一致を自分で壊す。
4. **版の突き合わせ面が増える。** クラウド CLI は各自の周期で自動更新される。間にラッパを
   挟むと「CLI の版 × ラッパの版」の組み合わせが観測対象に加わる。

一方で、**統一が効く面は既に統一されている**ことを見落とさない。境界は
**「argv を決める」と「その argv が何を指すか」の間**にある:

```
  呼び出す側: agent-project / agent-flow / agent-amigos / agent-audit /
              agent-loop / agent-dashboard / 人（defs・exec）
                                   │
                                   ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │ 「どう呼ぶか」を決めるところ — 全 CLI が同じ 1 実装を通る（例外なし）  │
  │   load_cli() │ resolve_variant(cli, purpose) │ headless_cmd() → argv │
  │   この解決を使う口（雲の CLI も対象）: defs · exec · harness          │
  │   harness も同じ headless_cmd() で argv を組む——層 2 で雲を回すときも │
  │   例外を作らない                                                    │
  └──────────────────────────────────────┬───────────────────────────┘
                                         │
  ═══ 統一の境界 ═══════════════════════╪═══════════════════════════════
  「argv を決める」までが共通 —「その argv が何を指すか」からが分かれる
                     ┌───────────────────┴───────────────────┐
                     ▼                                       ▼
  ┌────────────────────────────────┐   ┌────────────────────────────────┐
  │ adapter が要る → agent-herd 経由 │   │ adapter が要らない → 素の CLI    │
  │ argv[0] = agent-herd            │   │ argv[0] = claude / codex / …   │
  │ 実測 usage・接続補完・本文抽出・  │   │ 定義ファイルだけで契約に適合    │
  │ TUI は素の argv で表せない        │   │ できている                      │
  │                                │   │                                │
  │ aider · ollama · ollama-json ·  │   │ claude · codex · kiro ·        │
  │ ollama-list · -list-thinking ·  │   │ copilot · cursor               │
  │ -read · -verify · opencode（8）  │   │                          （5） │
  │            ▼                    │   │            ▼                   │
  │ LAN 上の ollama サーバ           │   │ 各社の API（個人の資格情報）     │
  └────────────────────────────────┘   └────────────────────────────────┘
            ▲                                        ╎
            └────────────────────────────────────────┘
      昇格 — adapter が要るようになった時点で左へ移る（基準は下記）
```

表で言い直すと:

| 面 | 統一点 | クラウド CLI も対象か |
|---|---|---|
| 呼び出し方の解決（定義 → argv） | `agentcore.agentcli`（唯一のローダ） | **対象**。エンジンは全 CLI をここ経由で組む |
| 定義の観測 | `agent-herd defs` | **対象**。実効 argv の確認は全定義で同じ口 |
| 人のデバッグ実行 | `agent-herd exec`（P3） | **対象**。エンジンと同じ経路を手で再現できる |
| ハーネス供給 | `agent-herd harness`（層 2 素通し） | **対象**。定義経由で任意 CLI を回せる |
| **subprocess の実行パス** | — | **対象外**。素の CLI を直接 exec する（本節） |

つまり「入口の統一」の実体は agentcli（定義層）で既に達成されており、agent-herd が
束ねるのは **adapter を必要とするものだけ**である。

**昇格基準**: クラウド CLI が opencode と同型の理由——実測 usage を `@agent-usage` へ移す、
落ちない失敗を落とす、生イベントから本文を抽出する——で adapter を必要とした時点で、
agentcore に adapter を書き、同じ zipapp のサブコマンドへ載せる。基準は「adapter が要るか」
であって「揃えたいか」ではない。

### 9.4 variant を別の `agent_cli` にしているのは設計の誤り（訂正）

**当初この節では「1 ファイルに畳んでも識別子は名前として残す必要がある」と書いたが、それは
誤りだった。** 根拠として挙げたのは「`resolve_variant` が名前を返し、台帳にもその名前が
残る」だが、それは現状の実装がそうなっているというだけで、**そうであるべき理由になって
いない**。契約を読み直すと、むしろ現状のほうが契約と食い違っている。

#### 用途の次元は、契約側に既にある

候補の格付け契約（`schemas/agent-candidate-qualifications.schema.json`）はこう定義されている:

```
candidate = (agent_cli, model)
     └─ qualifications: { operation_class → 格付け }
```

`agent-audit` の集計キーも `(agent_cli, model, operation_class)` である
（`agent_audit/qualifications.py`）。usage の `GROUP_KEYS` にも `agent_cli` と `purpose` が
**別の列として**並んでいる。

つまり**「どの用途か」は候補の中の次元として既にモデル化されている**。それを
`agent_cli` の値へ畳み込んでいるのが `ollama-json` / `ollama-list` / `ollama-read` /
`ollama-verify` / `ollama-list-thinking` で、同じ次元を 2 か所で表している。

#### 実害 1 — 証跡が割れる

eval の記録を数えると、`agent_cli` として `ollama` と `ollama-json` が**別々に**現れる。
1 つの実行系（`agent-herd ollama` × `gemma4:e4b`）の実測が、6 つの偽の候補へ分散している。
格付けは `(agent_cli, model, operation_class)` で集計されるので、**本来 1 候補の
operation_class 別の格付けとして積み上がるはずの証拠が、候補ごとに割れて薄くなる**。
サンプル数が要る格付けにとってこれは直接の損失である。

#### 実害 2 — 台帳と一覧で「別のエージェント」に見える

（利用者からの指摘。こちらのほうが重い。）`ollama-*` が `claude` / `codex` / `kiro` と
同じ列に並ぶと、運用者には「13 のエージェントがある」と見える。実際には
**8 のエージェントがあり、そのうち 1 つに 5 つのモードがある**。クラウド CLI との差異が
実体以上に大きく見え、候補の選択・予算配分・格付けの読みがすべてその歪んだ像の上で行われる。

#### あるべき形 — 定義は「エージェント 1 つ」に対して 1 つ

用途別の起動差は、定義の中の**プロファイル**にする。名前を残す必要はない
（残すとしても JSON schema の中の話で、`agent_cli` の値空間を汚す理由にはならない）。

```json
{
  "name": "ollama",
  "command": ["agent-herd", "ollama", "{model}"],
  "errors": [ … ],                        ← 35 規則 → 1 か所（§9.4 の測定）
  "profiles": {
    "json":  { "command": ["agent-herd","ollama","--think","off","--format","json","{model}"],
               "headless_autonomy": "single-shot", "write_args": [] },
    "list":  { … }, "read": { … }, "verify": { "default_model": "gemma4:12b", … }
  },
  "variants": { "planner": "json", "judge": "json", "split": "list", "verify": "verify" }
}
```

- `resolve_variant("ollama", "split")` は `(agent_cli="ollama", profile="list")` を返す
- 台帳は `agent_cli: ollama` に統一され、用途は既存の `operation_class` / `purpose` 列が持つ
- クラウド CLI は `profiles` を持たない——**定義ファイル数が実際のエージェント数と一致する**

#### 移行の重さ（これが唯一の反対理由）

読み手が 5 つある: `agentcli`（Python）・dashboard の JS ローダ・`agent-audit` の格付けと
usage・control の `selection_policy`・既存の eval アーカイブ（`ollama-json` の行が残っている）。
データ契約の変更なので、旧綴りを読み替える移行期間と、両ローダのゴールデン更新が要る。

**それでも畳むべきである。** 割れた証跡は時間とともに積み上がり、後から統合するほど
読み替えの範囲が広がる。`errors` の 35 規則の重複はその副産物として一緒に消える。

---

## 10. テスト戦略

1. **argv0 ディスパッチ**: 各 basename → サブコマンドの対応表テスト（`agent-herd` 明示形と
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

1. **stub の取り込み**: `tools/agent-loop/stub/kiro-cli-stub.py` を `agent-herd stub` として
   同梱するか。プロトコル試験には便利だが、配布物に試験具を混ぜる是非があるので P3 で判断。
2. **Windows（WSL 外）**: hardlink 不可環境はコピーで代替する方針だが、wsl-launcher 経由の
   導線で問題が出ないか P0 実装時に確認。
3. **`ollama_loop` と `harness.toolloop` の将来統合**: §5.4 のとおり台帳の実測が並ぶまで
   保留。判断材料（同役割・両経路の PASS 率と壁時計）を agent-audit で取れる形にだけ
   しておく。
4. **`chat` の dashboard 連携**: dashboard の対話診断が `chat` を使うか従来の
   `interactive.command` 直接かは dashboard 側設計に委ねる（本設計は契約を変えない）。
