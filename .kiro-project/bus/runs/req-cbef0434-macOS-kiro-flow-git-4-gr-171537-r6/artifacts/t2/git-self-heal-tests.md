# git 自己修復関連テストの棚卸し（tools/kiro-flow/tests 配下、読み取り専用調査）

対象ファイル: `tools/kiro-flow/tests/test_kiro_flow.py`（このディレクトリの唯一のテストファイル）
調査対象リポジトリ: `https://github.com/ynitto/sandbox/`（worktree: kp/macOS-kiro-flow-git-4-gr-171537）

## 前提（採用した解釈）

- 「git 自己修復」は、リポジトリの破損・残骸・一過性障害を検知して**例外を出さず自動的に回復させる**振る舞いを指すと解釈した。
  単なる分散排他制御（claim の勝者決定）や通常のワークスペース clone 生成テストは対象外とした。
- クラス単位で該当する主対象は以下 2 クラス。
  - `GitDistributedTests`（L3314-）: バス用 git クローン（`GitBus`）の自己修復
  - `StateGitSyncTests`（L4454-）: 状態共有用 git クローン（`StateGit` / `state_sync`）の自己修復
- 「リポジトリ再初期化」「ブランチ復旧」「detached HEAD 修復」はタスク文中の例示であり、実際のテスト名・docstring に完全一致するものはない。
  最も近い実体は「クローンの作り直し（rebuild/reinit 相当）」「ロック・rebase 残骸の除去」「破損検知による自己回復」である（下記に対応関係を記載）。

## 一覧（1テスト1行要約）

### class GitDistributedTests（バス用クローンの自己修復）

- `test_clone_retries_on_transient_network_failure` (L3406) — 起動時クローンが一過性ネットワーク障害に遭遇してもリトライし、最終的に成功することを検証。
- `test_clone_gives_up_after_retries` (L3429) — クローンが恒久的に失敗し続ける場合は `CLONE_RETRIES` 回で諦め、明示的な `RuntimeError` を送出することを検証。
- `test_stale_index_lock_recovered_on_reuse` (L3499) — SIGKILL/電源断で残った古い `index.lock` をクローン再利用時に除去し、以後の add/commit/push が失敗し続けないことを検証（≒残骸除去による自己修復）。
- `test_corrupt_index_clone_is_rebuilt` (L3514) — ロック除去でも直せないほど破損した index を持つクローンを、再利用時に**作り直して**自己回復することを検証（≒リポジトリ再初期化に相当）。
- `test_lock_going_stale_during_retry_is_removed` (L3531) — 実行中に遭遇したロックでも、リトライ中に十分古く（残骸と判明）なれば除去して成功する一方、新しいロックはまだ消さないことを検証。
- `test_interrupted_rebase_recovered_on_reuse` (L3550) — 中断された `pull --rebase` の残骸（`rebase-merge/`）をクローン再利用時に破棄し、以後の pull が成功することを検証（≒ブランチ復旧に近い）。
- `test_git_retries_while_live_lock_is_held` (L3560) — 稼働中の他 git プロセスが保持する新しいロックは消さず、短いバックオフで解放を待って成功することを検証。
- `test_empty_objects_clone_is_rebuilt_on_reuse` (L3601) — 電源断でオブジェクトがサイズ0破損したクローンを fsck 相当の健全性プローブ（`_probe_integrity`）で検知し、リモートから**作り直して**自己回復することを検証。
- `test_sync_push_self_heals_on_object_corruption` (L3617) — push 実行中にローカルオブジェクト破損が露見しても恒久的な push 失敗に陥らず、クローンを作り直して回復し、以後正常に push できることを検証。
- `test_corrupt_remote_gives_clear_diagnostic_not_reclone_loop` (L3638) — 共有リポジトリ本体（リモート）自体が破損している場合は作り直しても直らないため、無限再クローンに陥らず「共有リポジトリ破損」と明示した `RuntimeError` で中断することを検証（自己修復の限界の境界テスト）。
- `test_is_corrupt_error_classifies_power_loss_signatures` (L3651) — 電源断由来の破損メッセージ（空オブジェクト・sha1不一致・bad object 等）を破損と判定し、一過性のネットワーク/権限エラーとは区別する分類ロジックを検証（自己修復の発火条件の単体テスト）。

### class StateGitSyncTests（状態共有用クローンの自己修復）

- `test_empty_objects_state_clone_is_rebuilt_on_reuse` (L4651) — `state_git` クローンのオブジェクトが電源断でサイズ0破損した場合、次プロセスの `_ensure_clone` が検知してクローンを**作り直し**、manifest 喪失後も3-way同期が再収束することを検証。
- `test_state_sync_self_heals_on_object_corruption_midflight` (L4671) — 稼働中（`_ready` 済み）に破損が露見しても `state_sync` は例外を漏らさずクローンを破棄し、呼び出し側（デーモンループ）を落とさず次回同期で作り直すことを検証。

## 範囲外として除外したテスト（判断根拠）

- `GitDistributedTests` 内の `test_claim_across_separate_clones_single_winner` / `test_request_claim_elects_single_daemon` / `test_run_over_git_bus_completes` / `test_sparse_checkout_limits_worktree` / `test_cleanup_clone_removes_worktree` / `test_clone_inside_parent_repo_does_not_touch_parent` / `test_reuse_full_checkout_of_same_remote_is_refused` / `test_managed_bus_clone_is_reused` / `test_clone_into_foreign_nonempty_dir_is_refused` / `test_durable_write_config_on_clone_and_local_remote` / `test_make_bus_cleanup_removes_active_clones` — 破損・残骸からの回復ではなく、通常経路の分散排他・sparse-checkout・安全ガード（誤上書き防止）・予防的設定の検証のため除外。
- `GitlabExecutorPluginTests` 内の `test_ensure_workspace_clone_creates_run_branch`（L2749）は detached HEAD 状態を作る点で似ているが、これは共有 cache 経由の**正常系**worktree 動作（二重 checkout 制約回避のための意図的な detached 化）であり、破損からの修復ではないため除外。
- `SelfUpdateTests`（L4293-）はクラス名に "Self" を含むが、kiro-flow スキル自体のバージョン自己更新（スキルリポジトリの pull）を検証するもので、git リポジトリの破損自己修復とは無関係のため除外。
- `OrphanRecoveryTests`（L364-）は分散実行における孤児 run（プロセス/デーモンのリース失効）の再開・失敗判定を検証するもので、git リポジトリ自体の修復ではないため除外。

## 検証内容と結果

- 対象ディレクトリに存在するテストファイルは `test_kiro_flow.py` の1本のみであることを `find` で確認した。
- 上記全テスト関数の行番号・docstring・実装内容は `Read` ツールで実ファイル（L2700-2820, L3314-3693, L4454-4693）を直接参照して転記した（要約の意訳のみで、機械的な引用ではない）。
- 本タスクは読み取り専用のため、コード変更・コミットは行っていない（`git status` 相当の変更なし）。
- pytest 実行は本タスクの完了条件ではなく後続タスクの責務と判断し、実行していない（担当タスクは列挙・要約のみ）。

## 未解決事項・範囲外で見つけた問題

- 「detached HEAD 修復」に文字通り一致するテストは見つからなかった。`test_ensure_workspace_clone_creates_run_branch` が detached HEAD に言及するが、これは正常系の設計（意図的な detached 化）であり、破損からの修復ではない。全体文脈にある「macOS で失敗する git 自己修復テスト4件」がどのテストを指すかは本タスクの範囲外（後続タスクで pytest 実行結果と突き合わせて特定する必要がある）。
- charter の「codd-gate があればそれを活用」「ユニットテストの拡充」は本タスク（読み取り専用の列挙）には直接関係しないため、逸脱なしと判断した。
