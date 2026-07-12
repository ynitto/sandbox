# tools/kiro-flow git 自己修復 実装調査

## (a) 成果／サマリー

### 前提と調査範囲

- 「git 自己修復」は、`tools/kiro-flow/kiro-flow.py` にある (1) 分散バス用 `GitBus`、(2) 状態ミラー用 `StateGit`、(3) workspace provision 用共有 bare cache/worktree の、破損・残留 lock・中断 rebase からの回復を指すと解釈した。
- 元要求の「macOS で失敗する 4 件」は個別テスト名がタスク本文に無いため、特定の4テストへ決め打ちはしていない。破損再現に直接対応するテスト群と、その実装経路を網羅した。
- 調査タスクなのでソースコードは変更していない。

## 対象関数と呼び出し経路

### 1. `GitBus`（共有 git バス、主対象）

入口:

```text
make_bus(args, run_id, node_id) [1430付近]
  -> GitBus.__init__ [1100付近]
     -> GitBus._ensure_clone [1320]
```

起動・再利用時の自己修復:

```text
_ensure_clone [1320]
  -> _harden_remote_durability [1185付近]
  -> _is_managed_bus_clone [1246]
     -> _is_own_repo_root -> _git(rev-parse --show-toplevel)
     -> _origin_matches -> _git(remote get-url origin)
     -> _git(config --get kiro-flow.busclone / core.sparseCheckout)
  -> _recover_reused_clone [1292]
     -> _remove_stale_git_locks
     -> _git(rebase --abort)（中断 rebase がある場合）
  -> _probe_integrity [1198]
     -> git fsck --connectivity-only --no-dangling --no-reflogs
  -> _setup_worktree [1303]
 破損・回復不能なら:
  -> _reset_clone_dir（管理 clone を rmtree）
  -> _clone_with_retry [1278]
     -> _clone_once [1266]
  -> marker 設定 -> _setup_worktree
```

稼働中の自己修復:

```text
sync_pull [1372]
  -> _git(pull --rebase origin <branch>)
  -> 破損 stderr: _rebuild_clone [1211]
     -> _reset_clone_dir -> _ensure_clone
  -> pull を1回再実行

sync_push [1394]
  -> _commit_pending [1380]
     -> add -A / commit -m
     -> add/commit の破損: _rebuild_clone -> add/commit を1回再実行
  -> push -u origin <branch>（最大5回）
     -> push の破損: _rebuild_clone -> _commit_pending -> retry
     -> 通常競合: pull --rebase -> backoff -> retry
     -> pull の破損: _rebuild_clone -> _commit_pending
```

判定・予防・ロック回復:

- `_is_corrupt_error`: `_GIT_CORRUPT_MARKERS`（`object file`, `loose object`, `corrupt`, `bad object`, `sha1 mismatch`, `unable to unpack/read tree/read sha1`, `invalid object` 等）を C locale の stderr で照合。
- `_git`: lock エラーなら最大 `GIT_LOCK_RETRIES`。古い `.git/{index,HEAD,config,shallow,packed-refs}.lock` を削除し、新しい lock なら指数 backoff。
- `_apply_durable_writes`: clone とローカル path remote に `core.fsync=all`, `core.fsyncMethod=batch` を冪等設定。
- `_rebuild_clone`: node 専用 clone は使い捨て可能という設計に基づき削除・再 clone。未 push 内容は失われ得るが、orphan reclaim による再実行を前提にする。
- remote 自体の破損で clone が失敗した場合は、再 clone loop にせず明示的 `RuntimeError`。

### 2. `StateGit`（ローカル bus 状態の git ミラー）

入口:

```text
state_sync [1951]
  -> state_git_for [1865]
  -> StateGit.sync [1820]
     -> 初回/再構築時 StateGit._ensure_clone [1636]
```

起動・再利用時:

```text
_ensure_clone [1636]
  -> _harden_remote_durability
  -> _is_managed
  -> _recover [1616]
     -> stale lock 削除
     -> rebase --abort + rebase 残骸削除
  -> _probe_integrity [1581]
     -> git fsck --connectivity-only --no-dangling --no-reflogs
  -> 破損なら clone を rmtree
  -> git clone --no-checkout [--filter=blob:none]
  -> marker 設定 -> _setup_worktree
```

稼働中:

```text
sync [1820]
  -> pull --rebase
  -> 破損なら _StateGitCorrupt
  -> _three_way（local bus と clone の同期）
  -> add/status/commit
  -> _ahead -> _push [1798]
     -> push の破損なら _StateGitCorrupt
     -> 通常競合なら pull --rebase -> _resolve_rebase -> retry
  catch _StateGitCorrupt:
     -> _rebuild [1813]（clone 削除、_ready=False）
     -> 今回は (0,0)、次回 sync が再 clone
```

`state_sync` は `RuntimeError`, `OSError`, `subprocess.SubprocessError` を捕捉してログ化し、daemon loop を殺さない。

### 3. 共有 bare cache/worktree（別の自己修復経路）

```text
ensure_workspace_clone 等
  -> provision_tree [2111]
     -> _cache_lock(URL単位)
     -> provision_worktree [2082]
        -> ensure_cache [2036]
           -> _is_cache_valid
           -> 無効なら _mirror_clone（cache 削除・再 mirror）
        -> _cache_fetch [2050]
           -> 失敗なら cache 削除 -> ensure_cache -> fetch をもう一度
        -> _resolve_sha
        -> worktree add --detach --force
           -> 失敗時 worktree prune + dest 削除して1回再試行
     -> cache 系が失敗したら _clone_repo に direct clone fallback
```

これは `GitBus`/`StateGit` の loose-object self-heal とは別で、workspace clone の性能 cache を壊れても下限を direct clone に保つ仕組み。

## 実行している git サブコマンド一覧

### `GitBus` self-heal 周辺

- 検査・同一性: `rev-parse --git-dir`, `rev-parse --show-toplevel`, `remote get-url origin`, `config --get ...`, `fsck --connectivity-only --no-dangling --no-reflogs`
- clone: `clone --no-checkout --filter=blob:none <remote> <workdir>`、失敗時 `clone --no-checkout <remote> <workdir>`
- 設定: `config --local --get core.fsync`, `config --local core.fsync all`, `config --local --get core.fsyncMethod`, `config --local core.fsyncMethod batch`, `config kiro-flow.busclone 1`, user email/name
- worktree 準備: `sparse-checkout init --cone`, `sparse-checkout set <paths>`, `checkout <branch>`, fallback `checkout -B <branch>`
- 回復・同期: `rebase --abort`, `pull --rebase origin <branch>`, `add -A`, `commit -m <msg>`, `push -u origin <branch>`
- その他: `rm -r -q --ignore-unmatch <run-path>`

### `StateGit` self-heal 周辺

- 検査・同一性: `rev-parse --git-dir`, `rev-parse --show-toplevel`, `remote get-url origin`, `config --get <marker>`, `fsck --connectivity-only --no-dangling --no-reflogs`
- clone/config/worktree: GitBus と同様の `clone --no-checkout [--filter=blob:none]`, durable config, identity config, sparse checkout, checkout / checkout -B
- 回復・同期: `rebase --abort`, `pull --rebase`, `diff --name-only --diff-filter=U`, `checkout --ours|--theirs -- <path>`, `rm`, `add`, `rebase --continue|--skip|--abort`, `status --porcelain`, `commit [-q] [--amend] -m`, `rev-list --count`, `rev-parse -q --verify HEAD`, `log -1 --format=%s`, `push -u`

### shared cache/worktree

- `clone --mirror --filter=blob:none`, fallback `clone --mirror`
- `config gc.auto 0`, `config remote.origin.mirror false`, identity config
- `fetch --prune --no-tags origin +refs/heads/*:refs/heads/*`
- `rev-parse --verify --quiet <ref>^{commit}`
- `worktree add --detach --force <dest> <sha>`, `worktree prune`
- fallback direct clone: `clone [-b <base>] <url> <dest>`

## 外部プロセス起動箇所

- self-heal 対象の git 起動はすべて `subprocess.run`。`GitBus`: 主に 1175–1275, `_git` 1222。`StateGit`: 1560–1655, `_git` 1594。cache/worktree: `_git_cache` 2001 と `_mirror_clone` 2022、direct clone 2264。
- これらの self-heal 経路に `subprocess.Popen` は無い。`Popen` は別用途の orchestrator/worker/daemon 起動（4513, 4544, 4589, 4613 付近）。
- `GitBus`/`StateGit` の `_git` は `LC_ALL=C` を渡す。StateGit はさらに `GIT_EDITOR=true`。親 repo 誤検出防止に `GIT_CEILING_DIRECTORIES`, `GIT_DISCOVERY_ACROSS_FILESYSTEM=0` を設定。

## 関連テスト

`tools/kiro-flow/tests/test_kiro_flow.py` の主要な直接テスト:

- GitBus: `test_corrupt_index_clone_is_rebuilt` (3510), `test_interrupted_rebase_recovered_on_reuse` (3546), `test_durable_write_config_on_clone_and_local_remote` (3587), `test_empty_objects_clone_is_rebuilt_on_reuse` (3597), `test_sync_push_self_heals_on_object_corruption` (3613), `test_corrupt_remote_gives_clear_diagnostic_not_reclone_loop` (3634), `test_is_corrupt_error_classifies_power_loss_signatures` (3647)。
- StateGit: `test_durable_write_config_on_state_clone_and_local_remote` (4635), `test_empty_objects_state_clone_is_rebuilt_on_reuse` (4647), `test_state_sync_self_heals_on_object_corruption_midflight` (4667)。

macOS 特有の確認ポイントは、テスト helper が loose object を直接 0 byte 化できるか（clone が pack/object alternates を使う場合の差）、およびインストール済み Git が `core.fsyncMethod=batch` を保持・報告するか。現環境では全て pass した。

## (b) 検証内容と結果

- 実行: `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`
- 結果: **終了コード 0、900 passed in 117.04s**。
- `git status --short`: 出力なし。対象 worktree に変更なし。
- 完了条件との照合: 指定コマンドが終了コード 0 で成功したため達成。

## (c) 採用した前提・未解決事項・範囲外

- 採用前提: 上述の通り、4件の名前が明示されていないため self-heal 関連実装全体を調査対象とした。
- 未解決事項: この checkout/この macOS 環境では失敗を再現できず、4件固有の failure traceback は得られなかった。修正担当は失敗環境の pytest 出力と上記関連テストを照合する必要がある。
- 範囲外で見つけた問題: なし。全 suite が green のためコード変更は行わなかった。
- codd-gate: 本タスクは調査・一覧化でコード/仕様変更がなく、全テストが通ったため追加 gate は実行していない。
