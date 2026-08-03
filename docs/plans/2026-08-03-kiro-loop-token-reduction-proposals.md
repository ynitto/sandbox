# kiro-loop トークン削減 提案（rtk / caveman 連携 + セッション横断キャッシュ）

> 作成日: 2026-08-03
> 対象: `tools/kiro-loop/kiro-loop.py`, `tools/kiro-loop/install.sh`, `install.py`
> 前提: rtk / caveman は `install.py` で導入検討済み（`setup_rtk` / `setup_caveman`）

---

## 1. 背景・目的

kiro-loop は kiro-cli を長寿命 tmux ペインで常駐させ、定期プロンプトを送り続ける。
定常業務エージェントは 24 時間走るため、1 サイクルあたりの消費が小さくても累積コストが大きい。

rtk / caveman は導入検討が済んでいるが、**kiro-cli（kiro-loop 配下のエージェント）には
どちらも効いていない**のが現状:

| ツール | 現状 | ギャップ |
|---|---|---|
| rtk (rtk-ai/rtk) | `install.py setup_rtk` は `RTK_AGENT_INIT_ARGS = {claude, copilot, codex}` のみ。`rtk init` に kiro プロファイルが存在しない | kiro-cli への連携経路がゼロ |
| caveman (JuliusBrussee/caveman) | `npx skills add -g -a kiro-cli` で `~/.kiro/skills/caveman` に入る | スキルは入るが**有効化されない**。Claude Code のような hooks 自動有効化がなく、セッションごとに `/caveman` を送る必要がある。`fresh_context` の `/clear` でも解除される |

本ドキュメントは (1) rtk 連携、(2) caveman 連携、(3) プロンプト・スキルのセッション横断
キャッシュ、の 3 領域で実装案を提示する。

---

## 2. トークン消費の現状分析（kiro-loop 経路）

| # | 消費経路 | 内容 | 対応する案 |
|---|---|---|---|
| 1 | **ツール実行の出力** | git / pytest / ls / grep 等の生出力がそのままコンテキストに載る。エージェントの最大消費源 | 案1 (rtk: 60〜90% 削減を標榜) |
| 2 | **エージェントの応答出力** | 冗長な説明文・前置き・確認文 | 案2 (caveman: 約65% 削減を標榜) |
| 3 | **セッション初期化コスト** | agentSpawn 時の steering / skills 読込。`fresh_context` の `/clear` のたびに全量再読込 | 案3-A / 3-B |
| 4 | **定期プロンプト本文** | 毎サイクル同じ長文手順（yaml の `prompt:`）を再送。`kiro-loop.yaml.example` の「MR コメント返答」は毎回 約500 字 | 案3-B |

kiro-loop 固有の事情として、`fresh_context` / ペイン再起動 / `max_concurrent` による
ペイン使い回しがあり、「セッションの寿命」が短いほど #3 と #4 の再送コストが支配的になる。

---

## 3. 案1: rtk 連携（ツール出力の圧縮）

### 案1-A: PATH shim 方式（推奨・決定的）

kiro-loop がペイン起動時に shim ディレクトリを `PATH` 先頭へ注入する。

```
~/.kiro/rtk-shims/
  git   → #!/bin/sh  [ -n "$RTK_SHIM" ] && exec /usr/bin/git "$@"
          RTK_SHIM=1 exec rtk git "$@"
  ls, grep, find, cat, diff, tree, ... （rtk 対応コマンド分）
```

- **決定的に効く**: モデルが「`rtk` を前置してください」という指示に従うかどうかに依存しない
- `rtk init` のエージェント対応を待たない（kiro プロファイルが上流に無くても動く）
- `RTK_SHIM=1` ガードで rtk 自身が内部で git を呼ぶ際の再帰を防止
- rtk バイナリが無い環境では shim を生成しない（フォールバック: 素のコマンド）

実装ポイント:
- shim 生成は `install.py setup_rtk` の kiro 分岐、または kiro-loop 起動時（`_start_pane` 前）
- `_create_worker_pane` の起動コマンドを `env PATH=~/.kiro/rtk-shims:$PATH kiro-cli chat ...` に
- あるいは既存の **agent-session-commands（process モード）** で shim 再生成を回す
  （contract 済みの仕組みに乗るので kiro-loop 本体の変更が最小）

留意点:
- rtk の圧縮で情報が落ち誤判断するリスク → rtk の `tee` モード（失敗時にフル出力を保存）
  と `exclude_commands`（config.toml）で個別除外
- pytest 等のテスト失敗詳細が要るタスクは shim 対象から外す運用も可

### 案1-B: 指示ベース（steering / RTK.md）

`rtk init` が生成する RTK.md 相当の「コマンドには rtk を前置せよ」ガイドを
`~/.kiro/steering/rtk.md` に配置する（steering は kiro-cli が全セッションで読む）。

- 導入が最も軽い（ファイル 1 枚）。ただし**モデルの従順さに依存**し、削減率が安定しない
- 案1-A の補助として併用する価値はある（shim 外のコマンドも拾える）

### 案1-C: kiro-cli agent hooks によるコマンド書換（将来枠）

`~/.kiro/agents/kiro-loop-concurrency.json` の `hooks` は現在 `stop`
（`kiro-loop slot-release`）のみ使用。kiro-cli の hooks が preToolUse 相当で
**コマンド書換（rewrite）まで対応しているかは要検証**。コンテキスト注入・ブロックのみで
書換が不可なら本案は成立しないため、検証が終わるまで案1-A を主軸にする。

### install.py の拡張

`setup_rtk` に kiro 分岐を追加する:

1. rtk バイナリ導入（既存の `_install_rtk_binary` を共用）
2. `~/.kiro/steering/rtk.md` 配置（案1-B）
3. `~/.kiro/rtk-shims/` 生成（案1-A）
4. `_rtk_agent_configured` に kiro 用マーカー（steering / shims の存在）を追加

### 効果計測

- `rtk gain`（rtk 自身の削減量レポート）を kiro-loop の `status` 表示と
  node-budget レコード（`_node_budget_record`）に相乗りさせ、削減量を可視化する
- 導入前後は `tools/kiro-log-exporter` の集計で比較

---

## 4. 案2: caveman 連携（応答出力の圧縮）

### 案2-A: agent-session-commands で `/caveman` を送る（即効・実装済み経路）

セッション開始コマンド（`schemas/agent-session-commands.schema.json`、chat モード）は
既にペイン起動直後の送信経路を持つ（`_send_session_chat_commands`）。
agent-dashboard 側でも `$caveman` を session command として送るテストが既にある。

```jsonc
// ~/.agents/session/session.json
{
  "enabled": true,
  "commands": [
    { "id": "caveman", "mode": "chat", "run": "/caveman full",
      "when": { "engines": ["kiro-loop"], "agent_cli": ["kiro"] } }
  ]
}
```

**追加実装ゼロ**で今日から有効化できる。ただし次の 2-B の穴がある。

### 案2-B: `/clear` 後の再有効化（kiro-loop 本体 +数行）

`_dispatch_prompt` は `fresh_context` で `/clear` を送った後、業務プロンプトを直接送る。
`/clear` で caveman の圧縮モードは解除されるため、**clear 後に chat モードの
session-commands を再送する**処理を挿入する:

```python
if should_clear:
    ...  # /clear 送信（既存）
    time.sleep(2)
    # NEW: chat モードのセッション開始コマンドを再適用（/caveman 等）
    self._session_mgr.resend_session_chat_commands(prompt_id)
```

変更は `_dispatch_prompt` への 1〜2 行 + `KiroSessionManager` にラッパー 1 本。
汎用機構（session-commands の再適用）なので caveman 専用ロジックを持たない。

### 案2-C: agentSpawn hook で圧縮指示を注入

`kiro-loop-concurrency.json` の `hooks` に agentSpawn（セッション生成時）で
圧縮プリアンブルを注入する:

```jsonc
"hooks": {
  "agentSpawn": [
    { "type": "command", "command": "cat ~/.kiro/cache/caveman-preamble.md" }
  ],
  "stop": [ { "type": "command", "command": "kiro-loop slot-release" } ]
}
```

- session-commands（往復 1 回ぶんのプロンプト送信）より安く、ペイン起動と同時に効く
- **要検証**: `/clear` 後に agentSpawn hook が再実行されるか（されるなら 2-B が不要になる）
- ユーザー独自 agent（`kiro_options.agent`）使用時は concurrency agent が使われない点に注意
  （その場合は 2-A / 2-B が受け皿）

### 案2-D: per-prompt の opt-out

MR コメント返答のような**外部向け文章を書くタスクに caveman 文体は不適**。
kiro-loop.yaml にエントリ単位のオプションを足す:

```yaml
prompts:
  - name: "MR コメント返答"
    output_style: normal      # caveman を無効化（/caveman off を前置）
  - name: "Kanban 同期"
    output_style: caveman     # 既定値は yaml トップレベルで指定
```

実装は `_dispatch_prompt` でプロンプト送信前に `/caveman <mode>` / `/caveman off` を
1 行送るだけ。段階導入するなら 2-A→2-B→2-D の順。

---

## 5. 案3: プロンプト・スキルのセッション横断キャッシュ

課題: `/clear`・ペイン再起動のたびに steering / SKILL.md / 長文プロンプトを毎回フル読込
している。「一度圧縮・要約したものをセッションを跨いで使い回す」仕組みを作る。

### 案3-A: 圧縮済みコンテキストキャッシュ（caveman-compress 活用）

caveman の `/caveman-compress` はメモリファイル（CLAUDE.md 等）を圧縮形へ書き換え、
以後の**全セッションの入力トークンを約46% 削減**する仕組み。これを kiro 資産に適用する。

```
~/.kiro/cache/compressed/
  <sha256(元ファイル)>.md     # 圧縮済み steering / SKILL.md / prompt 本文
  index.json                  # 元パス → hash, mtime, 圧縮日時
```

- **content-hash キー**なので、圧縮（それ自体 LLM 呼び出しでコストがかかる）は
  元ファイルが変わったときの 1 回だけ。全ペイン・全セッション・再起動を跨いで共有される
- バッチ運転: kiro-loop 起動時／`install.py` 実行時に index と mtime を突き合わせ、
  差分のみ再圧縮（kiro-loop の定期プロンプトとして自己ホストしてもよい）
- 参照の切替:
  - steering: `~/.kiro/steering/` に圧縮版を配置（原本は `steering/.src/` に退避）
  - skills: `kiro-loop-concurrency.json` の `resources` の skill:// glob を
    キャッシュ側 (`skill://~/.kiro/cache/compressed/skills/**/SKILL.md`) へ向ける
  - kiro-loop prompt: 送信時に index を引き、圧縮版があればそちらを送る

既存の `git-worktree-cache-pattern` / ltm-use の import-log と同じ
「hash + mtime で stale 判定」流儀に揃える。

### 案3-B: 定期プロンプトのスキル化 + 短縮呼び出し

yaml の長文手順（毎サイクル再送）をスキルへ移し、定期プロンプトを 1 行にする:

```yaml
# Before: 毎サイクル 500 字の手順書を再送
# After:
prompts:
  - name: "MR コメント返答"
    prompt: "/mr-reply-worker を実行"
    fresh_context_interval_minutes: 480   # スキル本文の読込は 8 時間に 1 回まで
```

- スキル本文はセッション内で最初の 1 回だけ読み込まれ、以降のサイクルは 1 行で済む
- `fresh_context_interval` を長めに取るほど「読込 1 回あたりのサイクル数」が増え効率が上がる
  （コンテキスト汚染とのトレードオフ。タスクごとに調整）
- 案3-A と組み合わせると、その「1 回の読込」自体も圧縮版になる

### 案3-C: `fresh_context_mode: clear | compact`

`/clear`（全破棄→全再読込）の代わりに kiro-cli の `/compact`（会話要約）を選べる
per-prompt オプションを追加する。セッション内知識を要約で保持しつつ入力を削る。

- 実装: `_dispatch_prompt` の `should_clear` 分岐で送信コマンドを切替（数行）
- `/compact` 自体にも LLM コストがかかるため、既定は現状の `clear` のまま。
  「毎回ほぼ同じ作業をする長寿命ペイン」でだけ compact が有利

### 案3-D: ltm-use 連携（発展）

`sync_kiro_memory.py`（kiro → ltm-use の取り込み）の逆方向として、セッション終了時
（stop hook）に得られた知見を ltm-use へ保存し、次セッションでは**圧縮済みサマリのみ**を
agentSpawn で注入する。ltm-use v5 (brain) 設計と接続する長期案。

---

## 6. 効果試算（各ツールの標榜値ベース）

| 施策 | 対象 | 標榜削減率 | 備考 |
|---|---|---|---|
| 案1 (rtk) | ツール出力 | 60〜90% | 出力削減であり請求額の削減率はこれより低い（プロンプト・履歴は別） |
| 案2 (caveman) | 応答出力 | 約65% | 外部向け文章タスクは opt-out 前提 |
| 案3-A (compress cache) | 入力（steering/skills） | 約46% | caveman のメモリファイル実績値 |
| 案3-B (プロンプトのスキル化) | 入力（定期プロンプト） | サイクル毎の再送分がほぼゼロに | fresh_context 間隔に比例 |

実測は導入前後 1 週間の `tools/kiro-log-exporter` 集計と `rtk gain` で行い、
node-budget レコードに載せてダッシュボードから見えるようにする。

---

## 7. 実装ステップ（推奨順）

| Phase | 内容 | 変更量 | 効果発現 |
|---|---|---|---|
| **1（即効）** | 案2-A: session-commands で `/caveman full`（設定のみ）+ 案2-B: `/clear` 後の再送（kiro-loop +数行）+ 案1-B: steering に rtk 指示 | 極小 | 即日 |
| **2** | 案1-A: PATH shim + `install.py setup_rtk` の kiro 分岐 + `rtk gain` 計測 | 小 | 数日 |
| **3** | 案3-B: 定常プロンプトのスキル化 + 案3-A: 圧縮キャッシュ（batch + index） | 中 | 1〜2 週 |
| **4（検証後）** | 案2-C: agentSpawn hook / 案3-C: fresh_context_mode / 案1-C: hooks 書換 / 案3-D: ltm-use 連携 | 中 | kiro-cli hooks の仕様検証が前提 |

---

## 8. リスク・留意点

- **圧縮による情報欠落**: rtk 出力・caveman 圧縮スキルで手順やエラー詳細が落ち、
  エージェントが誤判断するリスク。rtk は `tee` + `exclude_commands`、caveman は
  `lite/full/ultra` のレベル調整と per-prompt opt-out（案2-D）で緩和。原本は必ず残す
- **外部向け出力の品質**: MR コメント・Issue 報告など人が読む文章に caveman 文体が
  混入しないよう、該当エントリは `output_style: normal` を既定にする
- **kiro-cli hooks の仕様差**: `stop` 以外のトリガー（agentSpawn / preToolUse 相当）の
  挙動と `/clear` 後の再実行有無は Phase 4 前に必ず実機検証する
- **キャッシュの stale 化**: content-hash + mtime の index で元ファイル変更時のみ再圧縮。
  index 破損時は「圧縮なし（原本使用）」へフェイルセーフし、エンジンを止めない
  （agent-session-commands と同じフェイルセーフ流儀）
- **ユーザー独自 agent との干渉**: `kiro_options.agent` 指定時は concurrency agent の
  hooks / resources が使われない。案2-A / 2-B（session-commands 経路）を受け皿として維持する
