# 止まった run を「続きから」復旧する

kiro-project を止めた／落ちたあと、viewer に**実行中と応答なしの run が並んで、どれもやり直せない**状態になったときの手順。

> **まず知っておくこと（2026-07 の MVP 硬化後）**: 強制終了からの復旧は基本 **自動** になった。
> ① kiro-project を再起動すると、実行者が失踪した doing タスクは ready へ戻り（stale claim 回収）、
> 次の act が `last_run` から**同じ run を再開**する（失敗ノードだけやり直し・done は温存）。
> ② 状態 git の詰まり（中断 rebase・除外パスの追跡混入・履歴の食い違い）は同期のたびに自己修復
> され、分岐は plumbing マージで必ず合流する。③ viewer 側の詰まりは **🩺（同期を修復）ボタン**
> 1 つで直る（ヘッダの ⇣ の隣。何をしたかは平易な文で通知される）。
> 以下の手動手順は、それでも直らない場合の調査用。

## 何が起きているか

orchestrator が消えると、run は `status=running` のまま固まる。誰も進めないが、終端もしていない。この状態を「停滞（stalled）」と呼ぶ。放っておくと、成功済みのノードごと作り直すしかなくなる。

停滞かどうかは status では判別できない。**生存リース（`meta.orch_lease_until`）**で見る。

| 見え方 | 実態 | 判定 |
|---|---|---|
| 実行中 | リースが未来を指す | 触らない（走っている） |
| 応答なし | リースが切れている／リース自体が無く更新も古い | **停滞。続きから再開できる** |
| 完了・失敗 | 終端 | 再実行は最初から |

## 手順

### 1. まずプロセスを止める

残ったプロセスが状態ファイルを書き続けていると、調査した端から状態が変わる。

```bash
pkill -f kiro-projects-viewer     # viewer
python3 tools/kiro-project/kiro-project.py stop   # 常駐ループ
ps aux | grep -iE "kiro" | grep -v grep           # 残っていないこと
```

### 2. run の実態を見る

`bus/runs/<run-id>/` を直接読む。**進捗があるか（`results/` の数）が最も大事**で、これが「救う価値」を決める。

```bash
cd .kiro-project/bus/runs
for d in */; do
  python3 -c "
import json
m = json.load(open('$d/meta.json'))
print('$d', m.get('status'), 'lease=', m.get('orch_lease_until', 'なし'))"
  echo "  進捗: $(ls $d/results 2>/dev/null | wc -l) / $(ls $d/tasks 2>/dev/null | wc -l)"
done
```

進捗ゼロの run は救う価値がない。原因（下記）を潰して作り直す。

### 3. 成果がどこにあるか確かめる

**run が消えても、worker の成果は git に残っている。** kiro-flow は各ノードの成果を作業ブランチ `kp/<task-id>` にコミットする。bus から run を消しても、ブランチは残る。

```bash
git branch -a | grep "kp/"
git diff --stat origin/main...origin/kp/<task-id>   # 中身があるか
```

ここに実装が残っていれば、それが最も確実な成果物。テストを通してから main へマージする（run を再実行するより速く、確実）。

### 4. viewer から「続きから」やり直す

停滞 run には **`↻ 失敗した工程だけやり直す（残り N 件）`** ボタンが出る。押すと `resume-run` 指示
（`commands/` ドロップ）が本体へ届き、本体が **last_run の固定 → ready への積み直し** を原子的に行う。
kiro-project はこの `last_run` を見て**同じ run を再開**し、kiro-flow が**失敗ノードだけを pending へ戻して done は温存**する。

CLI でやるなら 1 コマンド:

```bash
python3 tools/kiro-project/kiro-project.py resume-run <task-id> --run <run-id> --reason "続きから"
```

`resume-run` を使わず `approve` だけすると、**成功済みノードを捨てて新しい run を作り直す**（26 ノード中 1 つの失敗で 25 ノード分を焼き直す）。ここが要。

なお `revise` と、feedback を伴う差し戻しは計画そのものを変えるので、意図的に新しい run になる。

## よくある原因

### worker の agent CLI が死んでいる

全ノードが同じ理由で失敗していたら、まず疑う。`final.json` の `summary` に出る。

```
- t1 [failed]: 実行エラー: codex 失敗 (rc=1): ...
```

- **codex**: 利用上限に達すると `rc=1` で即失敗する
- **kiro-cli**: AWS 認証が切れると `rc=0` のまま**空応答**を返す（成功に見えて何も起きない）

`.kiro-project/kiro-flow.yaml` の `agents:` で生きている CLI に切り替える。

### 分散構成（kiro-project=WSL / viewer=Windows）

別 PC で動かす場合、両者はファイルシステムを共有しない。**共有チャネルは `kiro-state` ブランチだけ**。

```
PC-A (WSL) kiro-project                origin                 PC-B (Win) viewer
  worktree/.kiro-project  ──push──▶  kiro-state  ──pull──▶  clone (kiro-state)
   (状態 + bus)           ◀──pull──              ◀──push──   指示 (commands/)

  backup_state ───────────push──────▶  main   ← バックアップ専用。viewer は触らない
```

- **viewer は `kiro-state` を見る**（`main` ではない）。main には significant だけを載せ、**bus を流していない**ので、main を見ても run が一切見えない
- **viewer 側の用意**: Windows で clone し、`kiro-state` を checkout して、その `.kiro-project` を viewer の `roots` に登録する。以降は viewer の git 同期（`gitPullSec` / `gitAutoPush`）が `kiro-state` を往復する

  viewer が要るのは状態だけなので、ソースを丸ごと落とさない（sparse clone）:

  ```bash
  git clone --filter=blob:none --sparse <url> sandbox-kiro-state
  cd sandbox-kiro-state
  git sparse-checkout set .kiro-project
  git checkout kiro-state
  ```
- **main に書くのは kiro-project の `backup_state` だけ**。viewer は main を触らない

同期には `origin` が要る。`git remote -v` で origin があること、`kiro-state` が push されていること（`git ls-remote --heads origin kiro-state`）を確認する。

### 状態はどこにあるか

kiro-project は状態を `<repo>-kiro-state` worktree（`kiro-state` ブランチ）へ逃がす。本体を dirty にせず、人の git 操作（stash / rebase / pull --autostash）が書き込み中の状態ファイルを壊さないための設計。git 管理外や worktree を作れない環境では自動で本体にフォールバックする（設定は要らない）。

この worktree は **`.kiro-project` だけの sparse checkout**（`tools/` や `docs/` は展開しない）。ディスク以上に、**人が worktree 側の `tools/` を本物と思って編集する事故**を防ぐのが目的。そこでの変更は `kiro-state` ブランチに乗るだけで、main には決して届かない。sparse は作業ツリーの見え方を変えるだけで、ブランチの中身は完全なので、状態のコミット・push・バックアップは影響を受けない。

**読み書きの実体は worktree 側**。`_migrate_state_into_worktree` は「worktree に中身があれば触らない」ので、本体側だけ直しても無視される。**復旧するときは worktree 側を直すこと。**

正本ブランチ（既定 `main`）には**バックアップ**が載る（`state_backup_branch`）。人の判断・計画が動いたとき（backlog / needs / decisions / charter）だけ、その時点の状態を 1 コミットで同期する。実行の副産物（journal / status.json / bus）は 5 秒ごとに変わるので worktree 側の履歴に留める。

バックアップは本体の作業ツリー・index に触らない（plumbing で ref だけ進める）ので、人が別ブランチで作業していても壊れない。失敗しても実行は止まらない。

**両方に `.kiro-project` が見えるのは正常**（worktree = 実体、main = バックアップ）。ただし viewer は `autoDiscover` で両方を拾うことがあるので、同じ run が二重に並んで見えたら `excludeDirs` で本体側を除外する。
