# kiro-flow git 自己修復ロジック 実装箇所インベントリ

対象ファイル: `tools/kiro-flow/kiro-flow.py`（単一モジュール。`tools/kiro-flow/executors/gitlab.py` には
自己修復ロジックなし＝grep 済み、ヒットゼロ）。

## 1. 共通ロックユーティリティ

| 内容 | 場所 |
|---|---|
| ホスト内ファイルロック（`_cache_lock` / `StateGit` 双方から利用） | `_file_lock` 関数 — L68 |

## 2. `GitBus` クラス（バス用管理クローンの自己修復）— L1092–1425

バス（inbox/claims/results/runs の JSON 共有）用の git 管理クローンに対する自己修復。

| 機能 | 実装箇所 |
|---|---|
| 定数: ロック残骸判定の経過秒（30s）/ ロック起因失敗の再試行回数 | `GIT_LOCK_STALE_SEC` L1067 / `GIT_LOCK_RETRIES` L1069 |
| stale ロック名一覧（index.lock, HEAD.lock, config.lock, shallow.lock, packed-refs.lock） | `_STALE_GIT_LOCKS` L1137 |
| **index.lock 等の除去**（mtime が古いものだけ削除） | `_remove_stale_git_locks` L1140 |
| ロック起因エラー判定 | `_is_lock_error` L1156 |
| オブジェクト破損（電源断でのサイズ0 loose object 等）判定 | `_is_corrupt_error` L1162、判定語一覧 `_GIT_CORRUPT_MARKERS` L1085 |
| durable write（core.fsync=all 等）の冪等適用（予防策） | `_apply_durable_writes` L1168、定数 `_DURABLE_GIT_CONFIG` L1081 |
| ローカルパスのリモート側にも durable write を効かせる | `_harden_remote_durability` L1184 |
| 再利用クローンの健全性チェック（`git fsck`） | `_probe_integrity` L1198 |
| **破損クローンの作り直し**（rmtree → 再clone） | `_rebuild_clone` L1211 |
| git コマンド実行＋ロック起因失敗時の自己リトライ（stale lock 除去 or backoff） | `_git` L1219 |
| workdir が自分自身のリポジトリ root か検証（親リポジトリ誤爆防止） | `_is_own_repo_root` L1235 |
| origin URL 一致検証 | `_origin_matches` L1241 |
| 管理下バスクローンか判定（sparse-checkout 誤爆防止のガード） | `_is_managed_bus_clone` L1246 |
| 失敗クローン残骸のディレクトリ削除 | `_reset_clone_dir` L1260 |
| 初回クローン（blob:noneフィルタ→非対応時フォールバック） | `_clone_once` L1266 |
| 初回クローンの指数バックオフ再試行 | `_clone_with_retry` L1278 |
| **中断 rebase の自己回復**（`rebase --abort` + `rebase-merge`/`rebase-apply` 削除）＋ロック残骸除去 | `_recover_reused_clone` L1292 |
| **ブランチの checkout / 作成**（`checkout <branch>` 失敗時に `checkout -B <branch>` でフォールバック作成） | `_setup_worktree` L1303（L1316–1318 が該当行） |
| クローン用意の統括（再利用回復 → 破損なら作り直し → 非空ディレクトリなら中断） | `_ensure_clone` L1320 |
| pull 失敗が破損由来なら作り直して再pull | `sync_pull` L1372 |
| add/commit が破損由来で失敗したら作り直して再試行 | `_commit_pending` L1380 |
| push リトライ（破損→作り直し、競合→pull --rebase して再push） | `sync_push` L1394 |

## 3. `StateGit` クラス（ワークスペース状態同期クローンの自己修復）— L1497–1858

`GitBus` と同型の自己修復ロジックを、ローカルバス⇔共有リポジトリの3-way同期用クローンに対して独立実装している（重複コード、統合の余地はあるが本タスクは調査のみのため未変更）。

| 機能 | 実装箇所 |
|---|---|
| 定数: ロック残骸判定秒数 / リトライ回数 / push リトライ回数 | `_STATE_LOCK_STALE_SEC` L1488 / `_STATE_GIT_RETRIES` L1489 / `_STATE_PUSH_RETRIES` L1490 |
| 内部シグナル用例外（破損検知→sync層で作り直しを誘発） | `_StateGitCorrupt` L1493 |
| stale ロック名一覧 | `_STALE_LOCKS` L1528 |
| **index.lock 等の除去** | `_remove_stale_locks` L1530 |
| ロック起因エラー判定 / 破損判定 | `_is_lock_error` L1544 / `_is_corrupt_error` L1549 |
| durable write 適用 / リモート側への適用 | `_apply_durable_writes` L1555 / `_harden_remote_durability` L1569 |
| 健全性チェック（`git fsck`） | `_probe_integrity` L1581 |
| git 実行＋ロック起因リトライ | `_git` L1591 |
| 管理下クローン判定 | `_is_managed` L1605 |
| **中断 rebase の自己回復**（`rebase --abort` + ディレクトリ削除）＋ロック除去 | `_recover` L1616 |
| **ブランチの checkout / 作成**（`checkout <branch>` 失敗時 `checkout -B <branch>`） | `_setup_worktree` L1625（L1633–1634 が該当行） |
| クローン用意の統括（回復→健全性確認→ダメなら rmtree して新規clone） | `_ensure_clone` L1636 |
| 3-way 差分適用中の衝突解消（`checkout --ours/--theirs`→`rm`、`rebase --continue`/`--skip`失敗時は`rebase --abort`） | `_resolve_rebase` L1766（呼び出し元 `_three_way` L1725） |
| push リトライ（破損なら `_StateGitCorrupt` を送出、競合なら pull --rebase + `_resolve_rebase`） | `_push` L1798 |
| **破損クローンの作り直し**（rmtree、次回 sync で再構築） | `_rebuild` L1813 |
| 同期本体。`_StateGitCorrupt` を捕捉して `_rebuild` を呼ぶ | `sync` L1820（catch は L1854–1857） |

## 4. 共有 git キャッシュ + detached worktree 供給（worker 作業ツリーの自己修復）— L1966–2358

ワーカーが作業する一時 worktree を、ホスト共有の bare ミラーキャッシュから **detached HEAD** で払い出す仕組み。ドキュメント: `docs/designs/git-worktree-cache-pattern.md`（不変条件 INV-1〜3 を本モジュールのコメントが参照）。

| 機能 | 実装箇所 |
|---|---|
| 破損シグネチャ語 / 払い出し済みURL集合 | `_CACHE_CORRUPT` L1973 / `_provisioned_urls` L1975 |
| キャッシュ root（`KIRO_GIT_CACHE_DIR` で上書き可） | `cache_root` L1978 |
| URL→キャッシュパスのハッシュ化 | `_cache_path_for` L1985 |
| URL単位のホスト内ロック（cache 変更の直列化） | `_cache_lock` L1990（`_file_lock` L68 を利用） |
| キャッシュの健全性チェック（`rev-parse --git-dir`） | `_is_cache_valid` L2005 |
| **bare ミラーの新規/再作成**（gc.auto=0設定含む） | `_mirror_clone` L2014 |
| キャッシュ用意（無ければ作成・壊れていれば再作成、リトライ付き） | `ensure_cache` L2036 |
| 増分fetch。破損系エラーは呼び出し側へ「作り直し」を促す False | `_cache_fetch` L2050 |
| refs優先順でのSHA解決 | `_resolve_sha` L2068 |
| **detached worktree の払い出し**（`worktree add --detach --force`、失敗時 `worktree prune` して再試行） | `provision_worktree` L2082 |
| **キャッシュ失敗時の direct clone フォールバック**（INV-3） | `provision_tree` L2111 |
| worktree登録の回収（cache側 prune） | `_prune_caches` L2127 |
| 長期未使用ミラーの掃除（生存worktreeは常にprune） | `sweep_cache_dirs` L2139 |

## 5. ワークスペース（run の唯一書込先リポジトリ）の clone / ブランチ運用 — L2166–2358

| 機能 | 実装箇所 |
|---|---|
| run 用作業ブランチ名の決定（`kf/<run_id>`） | `run_branch_name` L2247 |
| **フォールバック直接clone**（base指定→既定の順に試行、指数バックオフ、失敗残骸の削除） | `_clone_repo` L2252 |
| **detached HEAD のまま作業起点を整える**設計（ブランチはpush時に作る方針。commit用identity設定） | `_prepare_run_branch` L2281（設計コメント L2282–2286） |
| ワークスペースclone確保（provision_tree呼び出し。refs優先順=[branch, base]） | `ensure_workspace_clone` L2292 |
| **push 時にdetached HEADから作業ブランチを作成/更新**（`push origin HEAD:refs/heads/<branch>`）。reject時はfetch+rebaseして再push | `finalize_workspace` L2322（該当行 L2337, L2343–2344） |
| 作業ツリー削除＋共有cacheのworktree登録prune | `cleanup_workspace` L2349 |

## 6. gc（janitor）コマンド経由の孤立残骸回収 — L5036–5192

デーモン常駐時に SIGKILL 等で残った残骸を定期回収する経路（上記の即時自己修復とは別レイヤ）。

| 機能 | 実装箇所 |
|---|---|
| 孤立ロックファイルの掃除 | `sweep_lock_files` L5036 |
| 一時ファイルの掃除 | `sweep_tmp_files` L5070 |
| 孤立 work repo（成果物リポジトリの一時clone）の掃除 | `sweep_work_repo_dirs` L5103 |
| 孤立バスクローンディレクトリの掃除 | `sweep_clone_dirs` L5132 |
| `gc` コマンド本体（上記+`sweep_cache_dirs`を集約） | `cmd_gc` L5192（呼び出し集約は L5160–5174） |
| 予防的補足コメント: node_id に pid を含め index.lock 競合を回避 | `cmd_submit` L4686–4689 |

---

## 検証

- 完了条件コマンドを実行済み: `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`
  → **900 passed**（exit code 0、実行時間 126.67s）。本 worktree（macOS/Darwin, Python 3.14.2）では
  現時点で失敗テストは 0 件だった。
- 本タスクは「実装箇所の特定（調査）」が役割のため、作業ツリーへの変更は行っていない
  （`git status --short` で差分なしを確認済み）。

## 前提・注記

- 「git 自己修復ロジック」を、(a) ロック残骸(index.lock等)除去、(b) オブジェクト破損検知→クローン作り直し、
  (c) 中断rebaseの自己回復、(d) worktree/detached HEAD の供給と復旧、(e) ブランチ作成/切替のフォールバック、
  (f) 孤立残骸のgc回収 の6分類で網羅した。範囲は `tools/kiro-flow/` 配下のみ（依頼文言どおり）。
- `GitBus`（バス用）と `StateGit`（ワークスペース状態同期用）はほぼ同型のロック/破損検知/回復ロジックを
  個別実装している（コード重複）。統合すれば保守性は上がるが、本タスクの範囲外（調査のみ）のため未変更。
  範囲外の改善候補として報告するに留める。
- 完了条件のpytestは本worktreeでは既に green だった。macOS特有の失敗4件は、この worktree の分岐元
  （base）時点、または実行環境の差分（他ワーカーによる並行修正・タイミング依存の一過性失敗等）に起因する
  可能性がある。本タスクは特定作業のみを担当するため、失敗4件の再現・原因切り分け・修正は別タスクの担当
  と判断し着手していない。
