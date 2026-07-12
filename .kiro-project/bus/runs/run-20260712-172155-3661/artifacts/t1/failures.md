# テスト失敗詳細レポート

収集日時: 2026-07-12T17:22 JST  
実行コマンド: `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`  
作業ディレクトリ: `/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/kiro-flow-ws-4767-v0tz4kjw/sandbox`

**注意**: タスク指示では「4件」とあるが、実際の失敗は6件だった。全件を報告する。

総結果: 6 failed, 894 passed

---

## グループ A: PermissionError（git objects 書き込み権限）— 4件

macOS で git オブジェクトファイルが読み取り専用（パーミッション 0444）のため、
`_zero_loose_objects()` ヘルパーが `open(..., "wb")` でゼロ化しようとして PermissionError が発生。

### A-1: GitDistributedTests::test_empty_objects_clone_is_rebuilt_on_reuse

- **ファイル**: `tools/kiro-flow/tests/test_kiro_flow.py`
- **行**: 3596（assertGreater）→ 68（_zero_loose_objects 内）

```
PermissionError: [Errno 13] Permission denied:
'/var/folders/.../kf-git-kfo5tn53/clones/empty-obj/.git/objects/02/cddfa4ceda907870c8f319a07bef7c1dcbca4e'
```

スタックトレース:
```
test_kiro_flow.py:3596: in test_empty_objects_clone_is_rebuilt_on_reuse
    self.assertGreater(_zero_loose_objects(clone), 0)
test_kiro_flow.py:68: in _zero_loose_objects
    open(os.path.join(d, name), "wb").close()
E   PermissionError: [Errno 13] Permission denied: '...empty-obj/.git/objects/02/cddfa4...'
```

---

### A-2: GitDistributedTests::test_sync_push_self_heals_on_object_corruption

- **ファイル**: `tools/kiro-flow/tests/test_kiro_flow.py`
- **行**: 3613（_zero_loose_objects 呼び出し）→ 68（_zero_loose_objects 内）

```
PermissionError: [Errno 13] Permission denied:
'/var/folders/.../kf-git-ae3x50xs/clones/push-heal/.git/objects/ad/ee8e1625ad8c87a0e90b35301499b4ef3f02b1'
```

スタックトレース:
```
test_kiro_flow.py:3613: in test_sync_push_self_heals_on_object_corruption
    _zero_loose_objects(clone)
test_kiro_flow.py:68: in _zero_loose_objects
    open(os.path.join(d, name), "wb").close()
E   PermissionError: [Errno 13] Permission denied: '...push-heal/.git/objects/ad/ee8e...'
```

---

### A-3: StateGitSyncTests::test_empty_objects_state_clone_is_rebuilt_on_reuse

- **ファイル**: `tools/kiro-flow/tests/test_kiro_flow.py`
- **行**: 4648（assertGreater）→ 68（_zero_loose_objects 内）

```
PermissionError: [Errno 13] Permission denied:
'/var/folders/.../kf-sg-25zwnh1g/bus/.state-git/.git/objects/95/bc539d4858f0746e5af09e6568a68dee99e0d6'
```

スタックトレース:
```
test_kiro_flow.py:4648: in test_empty_objects_state_clone_is_rebuilt_on_reuse
    self.assertGreater(_zero_loose_objects(clone), 0)
test_kiro_flow.py:68: in _zero_loose_objects
    open(os.path.join(d, name), "wb").close()
E   PermissionError: [Errno 13] Permission denied: '...state-git/.git/objects/95/bc539d...'
```

---

### A-4: StateGitSyncTests::test_state_sync_self_heals_on_object_corruption_midflight

- **ファイル**: `tools/kiro-flow/tests/test_kiro_flow.py`
- **行**: 4672（_zero_loose_objects 呼び出し）→ 68（_zero_loose_objects 内）

```
PermissionError: [Errno 13] Permission denied:
'/var/folders/.../kf-sg-n5mch8nw/bus/.state-git/.git/objects/0c/08fc1892d667dbb7e03edbc8420b8cc13228f2'
```

スタックトレース:
```
test_kiro_flow.py:4672: in test_state_sync_self_heals_on_object_corruption_midflight
    _zero_loose_objects(sg.clone)
test_kiro_flow.py:68: in _zero_loose_objects
    open(os.path.join(d, name), "wb").close()
E   PermissionError: [Errno 13] Permission denied: '...state-git/.git/objects/0c/08fc...'
```

---

## グループ B: DeferDecision 例外（KIRO_FLOW_DEFER_WAITS=1 環境変数）— 2件

テスト実行環境に `KIRO_FLOW_DEFER_WAITS=1` が設定されているため、
`gitlab.py` の execute() がブロック待機ではなく `DeferDecision` を送出してしまう。
テストは `RuntimeError` または tuple 戻り値を期待しているが、実際は `DeferDecision` が飛んで失敗。

### B-1: GitlabExecutorPluginTests::test_open_mr_keeps_waiting_until_merged

- **ファイル**: `tools/kiro-flow/tests/test_kiro_flow.py`
- **行**: 1578（`_run_with` 呼び出し）→ 1462（`gl_plugin.execute`）→ `gitlab.py:960`

```
kf_exec_gitlab.DeferDecision: gitlab: イシュー #8 は承認待ち（park）
```

スタックトレース:
```
test_kiro_flow.py:1578: in test_open_mr_keeps_waiting_until_merged
    text, data, _ = self._run_with(api, mrs_seq=seq)
test_kiro_flow.py:1462: in _run_with
    text, data = gl_plugin.execute("work", "ログイン画面を追加", {})
tools/kiro-flow/executors/gitlab.py:960: in execute
    raise DeferDecision(f"gitlab: イシュー #{iid} は承認待ち（park）", {...})
E   kf_exec_gitlab.DeferDecision: gitlab: イシュー #8 は承認待ち（park）
```

---

### B-2: GitlabExecutorPluginTests::test_timeout_raises_before_any_mr

- **ファイル**: `tools/kiro-flow/tests/test_kiro_flow.py`
- **行**: 1592（`_run_with` 呼び出し）→ 1462（`gl_plugin.execute`）→ `gitlab.py:960`

```
kf_exec_gitlab.DeferDecision: gitlab: イシュー #1 は承認待ち（park）
```

スタックトレース:
```
test_kiro_flow.py:1592: in test_timeout_raises_before_any_mr
    self._run_with(api, mrs_seq=[[]])
test_kiro_flow.py:1462: in _run_with
    text, data = gl_plugin.execute("work", "ログイン画面を追加", {})
tools/kiro-flow/executors/gitlab.py:960: in execute
    raise DeferDecision(f"gitlab: イシュー #{iid} は承認待ち（park）", {...})
E   kf_exec_gitlab.DeferDecision: gitlab: イシュー #1 は承認待ち（park）
```

---

## 根本原因サマリー

| グループ | 件数 | 根本原因 | 修正対象箇所 |
|---------|------|---------|-------------|
| A: PermissionError | 4 | macOS の git はオブジェクトを 0444 で書き込む。`_zero_loose_objects()` が `open(..., "wb")` で上書きしようとして失敗 | `test_kiro_flow.py` の `_zero_loose_objects()` 関数（行 61–68）。書き込み前に `os.chmod(path, 0o644)` で権限を付与する必要がある |
| B: DeferDecision | 2 | テスト実行環境に `KIRO_FLOW_DEFER_WAITS=1` が設定されており、ブロック待機ではなく park ロジックが動く | テストの `setUp`/`tearDown` で `KIRO_FLOW_DEFER_WAITS` を unset するか、`_run_with()` ヘルパーが環境変数をクリアして実行する必要がある |

---

## _zero_loose_objects 修正案（グループ A）

```python
# 現行（行 68）
open(os.path.join(d, name), "wb").close()   # 0 バイトへ切り詰め

# 修正案: chmod で書き込み権限を付与してから切り詰め
p = os.path.join(d, name)
os.chmod(p, 0o644)
open(p, "wb").close()   # 0 バイトへ切り詰め
zeroed += 1
```

## KIRO_FLOW_DEFER_WAITS 修正案（グループ B）

`_run_with()` メソッド（行 1462 付近）の内部、または `setUp`/`tearDown` で：

```python
# setUp に追加
os.environ.pop("KIRO_FLOW_DEFER_WAITS", None)

# または _run_with 内で
env_backup = os.environ.pop("KIRO_FLOW_DEFER_WAITS", None)
try:
    text, data = gl_plugin.execute(...)
finally:
    if env_backup is not None:
        os.environ["KIRO_FLOW_DEFER_WAITS"] = env_backup
```
