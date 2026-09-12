# agent-herd — 外の agent runtime から context engineering だけを取り込む（記録）

> 作成 2026-09-12
> 対象: `tools/agent-tools/agentcore`（`ollama_loop` / `ollama_adapter` / `ollama_events` / `ollama_tui`）
> 上位文書: [agent-herd 実行系設計](../designs/agent-herd-design.md) ADR-6、
> [agent-herd 利用ガイド兼 CLI 仕様](../specs/agent-herd-spec.md) §11.3 / §11.4 / §11.7
> 関連: [ツールの開示と権限の昇格の設計](./2026-08-07-agent-ollama-tool-disclosure-design.md) §2.2・§3.2・§4.4、
> [agent-app 単体と herd で増えるもの](./2026-09-09-agent-app-standalone-and-herd-benefits-design.md) §3.2 T6

---

## 0. 一文で

TrueFoundry が 2026-09-11 に改めて発表した OSS agent harness **TrueForge**（MIT。model / MCP /
Skills / sandbox / approval / session state / subagents / context management を束ねた runtime）は
**丸ごとは入れない**。持ち帰るのは context engineering の 2 点——deferred tool/schema loading と
large-result offloading——で、前者は現行の形で既に先まで行っていることを設計書に記録し、
後者を Ollama 内蔵ループへ実装した。

## 1. なぜ runtime を入れないか

| TrueForge の機能 | このリポジトリでの所有者 | 入れると何が二重になるか |
|---|---|---|
| model の選択・切り替え | `agents/*.json` + `agentcli`（tier / variant / profile） | 定義ファイルと runtime 設定の 2 か所で同じ判断 |
| MCP / Skills | クラウド CLI 各自の MCP、`ollama_skills`（明示遅延読み） | スキルの置き場と読み方 |
| sandbox / approval | `read` セットの実行ゲート、agent-app の承認、harness のパス正規化 | 権限の境界がどちらの実装かで変わる |
| session state | 各 CLI のセッション継続、`ollama_events` の JSONL | 会話の正典 |
| subagents | agent-flow の bus・claim・納品、two-wave fan-out | 分担と回収の契約 |
| audit | agent-audit（両経路のセッションを同じ読み方で読む） | 台帳の語彙 |
| context management | `ContextTracker`・`context_slice`・スキルの遅延読み | 文脈の予算 |

runtime を入れると、これらの**所有者が二重になる**。片方だけ直って片方が古い、という不整合
クラスが増えるだけで、機能は 1 つも増えない（統合入口設計 2026-08-25 §1.1 で畳んだ「3 重複製」
と同じ形を、外から持ち込むことになる）。

## 2. 何を持ち帰ったか

### 2.1 Deferred tool / schema loading — 実装は増やさず、設計に記録した

TrueForge の言い方は「MCP tool schema を最初から全部 context へ入れず、まず名前と短い説明だけを
渡し、選ばれた tool だけ full schema を展開する」。agent-herd を同じ物差しで見ると:

| 段 | TrueForge | agent-herd（現行） |
|---|---|---|
| 0 | 全 tool の schema を常時投入 | — |
| 1 | 名前 + 短い説明だけ投入、選ばれたら schema 展開 | — |
| 2 | — | **schema を持たない**。道具はテキスト規約（bash 1 つ / read の語彙数行）で、`/api/chat` の `tools` を使わない |
| スキル | 一覧を見せて選ばせる | 一覧は見せない。`--skill` かプロンプト先頭のスラッシュ行で**明示されたものだけ**、frontmatter を落として前置き |
| 用途 | — | スラッシュ行と `--purpose` は子プロセスを起こす**前**に決着（モデルに選ばせない） |

つまり agent-herd は段 1 を飛ばして段 2 に居る。MCP を接続していないので schema の段階展開
そのものは持ちようがなく、持つ必要も無い。**コードは変えず、ADR-6 として設計書に「この形が
deferred loading の到達点である」と記録した。** 将来 MCP を繋ぐことがあれば、そのときの規則は
「名前と 1 行だけ、選ばれたら schema」で、常時投入へは戻さない。

### 2.2 Large-result offloading — Ollama 内蔵ループへ実装した

TrueForge の言い方は「巨大な tool result をそのまま LLM context へ戻さず、file/artifact へ置いて
summary + handle だけを返す」。agent-herd の現行は `_clip`（頭・尻を残して中ほどを「中略 N 文字」
で落とす）で、prefill は守れるが**中ほどは永久に失われ、モデルの側も読み直せない**。

実装したもの（`ollama_loop.offload_output`）:

| TrueForge の語 | agent-herd での実体 |
|---|---|
| file / artifact | `<ログ>.results/round-NNN.txt`（ログの隣・同名。`--no-log` は OS の一時ディレクトリ） |
| summary | 頭・尻（上限から案内の分を引いた予算）+ 全文の文字数・行数 |
| handle | ファイルの絶対パス。読み直しの手段は `grep -n` / `head -n` / `tail -n +K`——**すべて `read` セットの語彙にある**ので、外出しファイルを読むために権限を広げない |

守った不変条件:

- **固定プレフィックスは変えない**。案内は末尾追記の観測（observation）に載る（ツール開示設計 §4.4）。
- **上限内の出力は 1 バイトも変わらない**。ファイルも作らない。
- **書けなければ従来どおり**（外出しは節約であって契約ではない。実行を止めない）。
- **空回りの判定は全文で行う**。所在の案内はラウンドごとに違うので、会話へ返した本文で比べると
  同じ結果が別物に見える——`run_command` が全文のダイジェストを返し、`_round_signature` はそれを見る。
- **台帳に残す**。`tool_result` に `spill` と `output_chars_full`。`follow` / TUI の 1 行にも出す。

## 3. 持ち帰らなかったもの・境界

- **外付けハーネス（`harness.toolloop`）の `run` 結果**は末尾切り詰めのまま。モデルが読めるのは
  プロジェクト内のパスだけで（パス正規化の設計）、ログの隣に置いても読み直せない。作業ツリーの
  中へ置く案は成果物と混ざるので採らない。必要になったら「切り詰めた事実と長さを結果に書く」
  から始める（statemachine の `check` 出力は既にそうしている）。
- **compaction**（会話の要約圧縮）は入れない。`ContextTracker` が枯渇を明示的な停止に変える
  現行方針（ADR-5）のままで、要約を挟むと CPU では要約 1 回が数分になる。agent-app 側の
  「渡り歩きの履歴をローカルで要約してから送る」（2026-09-09 §3.2 T2）は別件。
- **モデルに結果を要約させてから渡す**形も採らない（同じ理由）。

## 4. 検証

- `agentcore/tests/test_ollama_loop.py`: 外出し（全文保存・所在・頭尻・上限）、上限内は無変更、
  置き場なし・書けないときの退避、極小上限でも所在は残る、`run_command` の項目、空回り判定が
  全文で行われること、`run_loop` のイベントと次ラウンドの入力。
- `test_ollama_events.py`（置き場の規則）、`test_ollama_adapter.py`（ログの隣 / `--no-log` は一時
  ディレクトリ・外出しが起きるまで作らない）、`test_ollama_tui.py`（進捗の 1 行）。
- 既存: agentcore の 2 テストルート（331 + 791 件）が無改変で通る。
