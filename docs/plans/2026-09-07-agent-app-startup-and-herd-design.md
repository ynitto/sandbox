# agent-app: 起動時の重さ（Windows）と `herd` の扱い — 検討記録

日付: 2026-09-07 / 対象: `tools/agent-app`（一部 `tools/statemachine-maker`）

## 1. 起動時の重さ

### 1.1 見つかった原因

Windows でリポジトリを登録すると起動が重い。コードを追うと、フォルダの探索そのものより
**待ち順と同期 I/O** が原因だった。

| # | 場所 | 何が起きていたか | 影響 |
|---|---|---|---|
| 1 | `renderer.js` `init()` | `host:info`（WSL の起動 + `bash -l`）を **await してから** 画面を組んでいた | ログインシェル（nvm / pipx …）の分だけ白い画面 |
| 2 | `selectRepo()` | `agents:list`（WSL で `command -v`）→ `session:list` → `wt:list` を**直列に await** してから一覧・ツリー・タスクを描いていた | 常駐シェルは 1 本の FIFO なので、上の probe の後ろに並ぶ |
| 3 | `worktree.list` | `git worktree list` に続けて **worktree ごとの `git status --porcelain`** を同じ呼び出しで待っていた | `/mnt/c` では status が数秒〜数十秒。その間 worktree の選択肢が空 |
| 4 | `files.js` `listDir` | 1 件ごとに **`fs.statSync`** | `\\wsl$\` 越しでは stat 1 回が往復 1 回。数百件のフォルダで main が固まる（IPC も端末ミラーも止まる） |
| 5 | `files.js` `find` | **同期の深さ優先** で毎回ツリー全体を歩く。潜らないのは `.git` と `node_modules` だけ。1 文字ごとに撃ち、返事の順序も保証しない | 大きなリポジトリで 1 打鍵ごとに main が数秒止まる。古い返事が新しい返事を上書きしうる |
| 6 | `store.listSessions` | 会話ファイル（端末スナップショット最大 12 × 120,000 字）を送信・応答のたびに**全部 parse** | 会話が増えると一覧の更新がじわじわ遅くなる |

「フォルダサーチ」と呼べるのは 4・5 で、どちらも効率の問題（同期・全走査・キャッシュ無し）と
UI ブロックの問題（main の同期 I/O が IPC を止める）を同時に持っていた。

### 1.2 対処

- **待たずに描く。** `init()` はホストの確認を Promise（`state.hostReady`）で持つだけにし、設定と会話一覧
  （手元のファイル）で画面を組む。`selectRepo()` は `agents:list` と `wt:list` を並行に投げ、届き次第
  そこだけ描き直す。別のリポジトリへ移っていたら捨てる（`repoToken`）。**送信だけは** CLI の有無と
  tmux の有無が要るので `agentsReady` / `hostReady` を待つ（経路の決定に使うため）。
- **worktree は 2 段。** `wt:list` に `withStatus` を足し、まず `git worktree list` だけで一覧を出し、
  あとから変更数・先行コミット数を数え直す。
- **ファイルは全部 `fs.promises`。** `listDir` / `readFile` / `find` を非同期にし、stat / readdir は 16 並列。
  `resolveInside` は同期版（添付の検査など）と非同期版を分けた。
- **名前検索は索引。** root ごとにフォルダ全体を**幅優先**で歩いた索引を 60 秒覚え、問い合わせは索引を引く
  だけにした。生成物のフォルダ（`node_modules` `dist` `build` `.venv` …）には潜らず、100,000 件か 10 秒で
  打ち切る（幅優先なので浅い階層は必ず載る。`truncated` を画面に出す）。順序は前方一致 → 部分一致、
  それぞれ浅い順。`/` を含めばパスで探せる。画面は打った順の最後の返事だけを出し（`filterSeq`）、
  ツリーの「更新」で索引を捨てる（`refresh`）。
- **会話一覧は mtime キャッシュ。** ファイルの mtime と大きさが同じなら前回の要約を使う。
- **`\\wsl$\` のリポジトリの索引は WSL の中で作る。** 検索が読むパスは登録したままの Windows 表記で、
  WSL 表記への変換は git と tmux に渡すときだけ（ここは以前から）。遅いのはリポジトリの実体が WSL に
  あるとき Windows 側の fs から 9P 越しに歩くことなので、その場合だけ常駐シェルで
  `git ls-files --cached --others --exclude-standard -z` を 1 回撃ち、返った相対パスから索引を作る
  （`.gitignore` も効く。git リポジトリでなければ fs で歩く）。`C:\` と Linux / macOS は fs のまま
  （ローカルディスクなら fs が最速）。

### 1.3 変えなかったもの

- `host:info` 自体（WSL 起動 + ログインシェル）。PATH を利用者の環境で引くために `-l` は要る。
- 共有ワークベンチ（statemachine-maker）の起動時の `agent-herd defs --json` / `agent-loop inspect`。
  子プロセスなので main は止めない。Windows ではこれらを WSL ではなく Windows 側で起こす仕様のままで、
  本件の範囲外。
- worktree ごとの `git status --untracked-files=all`。件数の意味を変えないため。

## 2. `herd` を会話・タスク・ワークフローで使う

### 2.1 dashboard と app の違い

agent-dashboard の `herd` は **実行レベル（tiers）の候補に書ける管理面のラベル** で、
`agents/herd.json` は存在しない。Execution Policy Compiler が用途（purpose）ごとの実測
（`qualifications.json`）を引いて `(aider|ollama, model)` へ展開し、エンジンは展開後だけを見る
（`herd-family.js`、`docs/plans/2026-08-26-agent-tools-recommended-setup-simplification-design.md` §3.5）。
一族の判定は `command[0] === 'agent-herd'` で機械的に導く。

agent-app の一覧は `agents/*.json` の実ファイルから作るので `herd` が出ない。さらに、

- 会話には用途の軸が無い（依頼は「実行」か「Ask」）。
- 実測の台帳を読まない（読ませると管理面と実行面の分離が壊れる、という agentcore の不変条件と同じ）。
- タスク（`agent-herd harness statemachine --agent-cli <名前>`）とワークフロー（`agent-flow --agent-cli <名前>`）は
  **実在する定義名**しか受け取らない（agent-herd の `known` に `herd` は無い）。

### 2.2 ollama と aider を使い分ける必要はあるか

ある。ただし 1 点だけ。agent-herd の `defs` が役割行で言うとおり、

- **aider** … 渡したファイルを直す編集役（自分では探索しない。single-shot、`--file` で対象を受け取る）
- **ollama** … 自分で調べて実行するツールループ（bash。Ask では `--think on` の読み取り専用）

で、計画・評価・抽出・検証などの 15 用途は両定義の `variants` が同じ profile（`ollama-json` 等）へ
振り替える。つまり入口の違いが効くのは「編集・実装を誰がやるか」だけである。

### 2.3 会話画面ではどうなるか（初案の弱点）

初案は「ターンごとに aider か ollama を選ぶ」だった。会話画面で見ると弱い。

- tmux 経路では、添付の有無で aider と ollama が入れ替わると **tmux セッションを起動し直し**、文脈は
  履歴の再送で追いつかせることになる（会話が続いている感覚が切れる）。
- aider の共通 TUI は添付ファイルを `/add` しないので、「添えたファイルを直す」根拠が対話では効きにくい。
- 会話は ollama、タスクは aider と写す先が違い、利用者から見て「herd を選んだのに何が動くのか」が
  分かりにくい。

一方、agent-herd 自身が既に共通の入口を持っている。

- トップレベルの `agent-herd` はクラウド CLI と同型（引数なし＝共通 TUI、`-p`＝単発、`--agent`
  `--purpose` `--readonly`。既定バックエンドは `ollama`）。
- 共通 TUI にはスラッシュの実行形（agentcore の `slashroute` 種別 B）があり、`/ask`（道具なし）、
  `/find`（読み取り専用の道具）、`/edit`（編集ハーネス。どのエージェントで直すかは宣言側が決める）、
  `/sm`（ステートマシン）。ヘッドレスの本文先頭でも同じ表で読む。
- dashboard の cowork も一族には `/sm` の 1 行を送るだけで、CLI を選び替えていない。

### 2.4 決定: 入口は agent-herd の 1 つ、用途はスラッシュ行

`herd` を **仮想エージェント**として扱うが、**agent-app は aider と ollama を選ばない**（`src/main/herd.js`）。

| 場面 | 起動 | 伝え方 |
|---|---|---|
| 会話・Ask | 共通 TUI（agent-herd の既定バックエンド `ollama` の定義）を 1 本 | 本文の先頭に `/find` |
| 会話・実行、作業フォルダのファイルを添付 | 同じセッション | 本文の先頭に `/edit` |
| 会話・実行、添付なし | 同じセッション | そのまま（ツールループ） |
| タスクの実行 | `agent-herd harness statemachine` | `--agent-cli` を渡さない（agent-herd の既定と宣言に任せる） |
| AI 支援（計画。読み取り専用） | `agent-herd --purpose plan` | `--agent` を渡さない |
| ワークフローの実行 | agent-flow | `--agent-cli` は省くとホスト設定（kiro 等）へ落ちるので、harness の既定と同じ `aider` |

- `agents/herd.json` は作らない。一覧の末尾に `virtual: true` の 1 行を足す（一族が 1 つでもあるとき）。
  画面の直接指定と設定の tier はどちらもこの一覧から選ぶので、両方に出る。
- 会話は CLI を入れ替えないので tmux セッションと文脈が続く。スラッシュ行は共通指示・履歴の再送より前、
  本文の一番上に置く（`slashroute` は先頭から連続する `/name` 行だけを読む）。
- 一族の定義がホストの PATH に無ければ断り、**一族の外へは倒さない**（ADR-3 と同じ姿勢）。既定バックエンド
  の定義が無ければ一族の他の定義（同じ共通 TUI）を開く。
- モデルは tier / 直接指定の値をそのまま渡し、空なら定義の `default_model`（dashboard の「モデル欄は
  空でよい」と同じ）。
- 会話の「次のターンの既定」は `herd` のまま残す。メッセージには実際の `cli` と `family: 'herd'` を残し、
  実行情報に `herd → ollama /edit` と理由を出す。
- タスク・ワークフローは statemachine-maker の `registerIpcHandlers` に `agentDefinitions`（一覧に `herd` を
  足す）と `hooks.resolveAgent`（`purpose: task | plan | flow` → 渡す名前。`''` は渡さない）を足して対応した。
  AI 支援の argv は agent が空なら `--agent` を省く。maker 単体では従来どおり。

### 2.5 やらなかったこと・次の一歩

- 用途別の実測（qualifications）に基づくモデル選択。要るなら dashboard の Compiler が焼いた展開結果
  （`(agent_cli, model)` の順位）を読む口を agent-app に足す。規則をここに増やすのではなく、正典を
  1 つ（Compiler）に寄せる。
- ヘッドレス経路（tmux なし）で本文先頭の `/edit` が編集ハーネスへ回るかは agent-herd 側の実装に依る
  （共通 TUI では回る）。回らなければ ollama のツールループがそのまま直す。
- ワークフローの工程ごとの `agent_cli`（agent-flow の control）は agent-app では触らない。`herd` を
  写すのは実行開始時の `--agent-cli` だけ。agent-flow に「省略時はローカル既定」ができれば、ここも
  渡さない形へ揃える。
