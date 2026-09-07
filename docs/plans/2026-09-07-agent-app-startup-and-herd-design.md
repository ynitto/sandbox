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

### 2.3 決定

`herd` を **仮想エージェント**として扱い、起動直前に依頼の形で写す（`src/main/herd.js`）。

| 場面 | 用途 | 選ぶ順 | 理由 |
|---|---|---|---|
| 会話・Ask | ask | ollama → aider | 読み取り専用が enforced。自分で調べて答える |
| 会話・実行、作業フォルダのファイルを添付 | edit | aider → ollama | 添えたファイル＝直す対象（aider はチャットに入れたファイルしか編集しない） |
| 会話・実行、添付なし | work | ollama → aider | 自分で探して直す |
| タスク・ワークフローの実行 | task | aider → ollama | dashboard の `work` 用途の既定と同じ。他の用途は `variants` が振り替える |
| AI 支援（計画。読み取り専用） | plan | ollama → aider | `--purpose plan` は variants で ollama-json へ行く |

- `agents/herd.json` は作らない。一覧の末尾に `virtual: true` の 1 行を足す（一族が 1 つでもあるとき）。
  画面の直接指定と設定の tier はどちらもこの一覧から選ぶので、両方に出る。
- 使えない一員は飛ばし、**一族の外へは倒さない**（ADR-3 と同じ姿勢）。
- モデルは tier / 直接指定の値をそのまま渡し、空なら各定義の `default_model`（dashboard の「モデル欄は
  空でよい」と同じ）。
- 会話の「次のターンの既定」は `herd` のまま残し、ターンごとに選び直す（添付の有無で変わる）。
  メッセージには実際の `cli` と `family: 'herd'` を残し、実行情報に `herd → aider` と理由を出す。
- タスク・ワークフローは statemachine-maker の `registerIpcHandlers` に `agentDefinitions`（一覧に `herd` を
  足す）と `hooks.resolveAgent`（起動直前に写す）を足して対応した。maker 単体では従来どおり。

### 2.4 やらなかったこと・次の一歩

- 用途別の実測（qualifications）に基づくモデル選択。要るなら dashboard の Compiler が焼いた展開結果
  （`(agent_cli, model)` の順位）を読む口を agent-app に足す。規則をここに増やすのではなく、正典を
  1 つ（Compiler）に寄せる。
- tmux 経路の aider は添付ファイルを `/add` しない（本文で所在を伝えるだけ）。共通 TUI 側に `/add` 相当が
  入ったら `herd → aider` のときだけ送る。
- ワークフローの工程ごとの `agent_cli`（agent-flow の control）は agent-app では触らない。`herd` を
  写すのは実行開始時の `--agent-cli` だけ。
