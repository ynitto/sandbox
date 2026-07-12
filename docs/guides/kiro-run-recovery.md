# 止まった run を「続きから」復旧する

kiro-project を止めた／落ちたあと、viewer に**実行中と応答なしの run が並んで、どれもやり直せない**状態になったときの手順。

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

停滞 run には **`↻ 失敗した工程だけやり直す（残り N 件）`** ボタンが出る。押すと:

1. run からタスクを引く（`req-<hash>-<task-id>-r<n>` から、または作業ブランチ `kp/<task-id>` から逆引き）
2. タスクの `last_run` にその run-id を固定する
3. タスクを `ready` に戻す

kiro-project はこの `last_run` を見て**同じ run を再開**し、kiro-flow が**失敗ノードだけを pending へ戻して done は温存**する。

CLI でやるなら:

```bash
# 1. 再開先を固定（この run の続きから、という指示）
node -e "require('./tools/kiro-projects-viewer/src/main/flow')
  .pinResumeRun('.kiro-project', '<task-id>', '<run-id>')"

# 2. ready へ戻す
python3 tools/kiro-project/kiro-project.py approve <task-id> --reason "..."
```

`last_run` を書かずに `approve` だけすると、**成功済みノードを捨てて新しい run を作り直す**（26 ノード中 1 つの失敗で 25 ノード分を焼き直す）。ここが要。

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

### 状態が二重に見える

kiro-project は状態を `<repo>-kiro-state` worktree（`kiro-state` ブランチ）へ逃がす（`state_worktree: true` が既定）。本体を dirty にせず、git 操作で状態ファイルを壊さないための設計。

このため **`<repo>/.kiro-project` と `<repo>-kiro-state/.kiro-project` の 2 つが存在し得る**。viewer は `autoDiscover` で両方を拾うので、同じ run が二重に並んで見える。

**実行時に使われるのは worktree 側**。`_migrate_state_into_worktree` は「worktree に中身があれば触らない」ので、本体側だけ直しても無視される。復旧するときは worktree 側を直すこと。
