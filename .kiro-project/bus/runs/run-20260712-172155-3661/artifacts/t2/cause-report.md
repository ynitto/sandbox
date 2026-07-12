# macOS 固有失敗原因分類レポート

作成日時: 2026-07-12T17:26 JST  
対象ファイル: `tools/kiro-flow/tests/test_kiro_flow.py`  
参照アーティファクト: t1/failures.md

---

## 前提注記

t1 が確認したとおり失敗は 6 件（タスク指示の「4件」と差異あり）。  
グループ A（PermissionError）4 件が「macOS 固有の git 自己修復テスト失敗」の本体。  
グループ B（DeferDecision）2 件も **macOS 固有ではないが** テスト環境の問題であり、本 run で合わせて修正すべき対象。それぞれ独立した原因で分類する。

---

## グループ A — macOS git オブジェクト書き込み権限（4件）

### 原因カテゴリ

**macOS-git-object-permission**: macOS の git（Apple 配布版・Homebrew 版ともに）は loose object を作成する際に `0444`（所有者も書き込み不可）でファイルを生成する。Linux の git も同様だが、macOS の tmpfs（`/var/folders/...`）では ACL と POSIX パーミッションが厳格に適用されるため、テストヘルパーが `open(..., "wb")` で上書き切り詰めしようとすると `PermissionError: [Errno 13]` が発生する。

### 該当コード

```
tools/kiro-flow/tests/test_kiro_flow.py  行 61–68
```

```python
def _zero_loose_objects(clone) -> int:
    objdir = os.path.join(str(clone), ".git", "objects")
    zeroed = 0
    for sub in os.listdir(objdir):
        d = os.path.join(objdir, sub)
        if len(sub) == 2 and os.path.isdir(d):
            for name in os.listdir(d):
                open(os.path.join(d, name), "wb").close()   # ← PermissionError (macOS)
                zeroed += 1
    return zeroed
```

### macOS 固有性の根拠

- エラーパスが `/var/folders/...`（macOS の `$TMPDIR` 実体）である。
- git の loose object は仕様上 immutable（書き込んだら変えない）のため 0444 で作られる。Linux でも同じだが macOS ではとくに厳格で、プロセス権限での `open("wb")` が拒否される。
- `os.sep` / `pathlib` / パス区切り文字の問題ではない（パスはすべて `os.path.join` で構築されており POSIX 準拠）。
- ハードコードされた `/` 区切りも存在しない。

### 修正方針

`open()` の前に `os.chmod(path, 0o644)` を呼び書き込み権限を付与する。1行追加で全4件が解消する。

```python
p = os.path.join(d, name)
os.chmod(p, 0o644)          # macOS: 0444 → 0644 に昇格してから切り詰め
open(p, "wb").close()
zeroed += 1
```

### 影響テスト（4件）

| # | テストメソッド | 行 |
|---|---------------|----|
| A-1 | `GitDistributedTests::test_empty_objects_clone_is_rebuilt_on_reuse` | 3596 |
| A-2 | `GitDistributedTests::test_sync_push_self_heals_on_object_corruption` | 3613 |
| A-3 | `StateGitSyncTests::test_empty_objects_state_clone_is_rebuilt_on_reuse` | 4648 |
| A-4 | `StateGitSyncTests::test_state_sync_self_heals_on_object_corruption_midflight` | 4672 |

---

## グループ B — 環境変数 KIRO_FLOW_DEFER_WAITS=1 の汚染（2件）

### 原因カテゴリ

**test-env-leak-DEFER_WAITS**: macOS 固有ではなく、テスト実行シェルの環境変数に `KIRO_FLOW_DEFER_WAITS=1` が常設されている（実証済み: `echo $KIRO_FLOW_DEFER_WAITS` → `1`）。  
`GitlabExecutorPluginTests.setUp` は `KIRO_FLOW_EXECUTOR_CONFIG` しか退避・クリアしておらず、`KIRO_FLOW_DEFER_WAITS` を触らない。このため `gitlab.py:953` の `if os.environ.get("KIRO_FLOW_DEFER_WAITS") == "1":` が常に真となり、ブロック待機を期待するテストで `DeferDecision` が飛ぶ。

### 該当コード

```
tools/kiro-flow/tests/test_kiro_flow.py  行 1432–1444（GitlabExecutorPluginTests.setUp/tearDown）
```

```python
def setUp(self):
    self._cfg = {..., "poll_interval": 0.0, "timeout": 0.0, ...}
    self._prev_env = os.environ.get("KIRO_FLOW_EXECUTOR_CONFIG")
    os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = json.dumps(self._cfg)
    # KIRO_FLOW_DEFER_WAITS を退避・クリアしていない ← 問題箇所

def tearDown(self):
    if self._prev_env is None:
        os.environ.pop("KIRO_FLOW_EXECUTOR_CONFIG", None)
    else:
        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = self._prev_env
    # KIRO_FLOW_DEFER_WAITS を復元していない ← 問題箇所
```

なお `DeferDecisionTests`（行 5055–5067）では `_prev_defer` を保存・復元する正しい実装があり、`GitlabExecutorPluginTests` がそのパターンを踏襲していないのが直接的な欠落。

### 修正方針

`GitlabExecutorPluginTests.setUp` で `KIRO_FLOW_DEFER_WAITS` を `None` へクリアし、`tearDown` で元の値に戻す。

```python
def setUp(self):
    ...
    self._prev_defer = os.environ.get("KIRO_FLOW_DEFER_WAITS")
    os.environ.pop("KIRO_FLOW_DEFER_WAITS", None)   # テスト中は無効化

def tearDown(self):
    ...
    if self._prev_defer is None:
        os.environ.pop("KIRO_FLOW_DEFER_WAITS", None)
    else:
        os.environ["KIRO_FLOW_DEFER_WAITS"] = self._prev_defer
```

### 影響テスト（2件）

| # | テストメソッド | 行 |
|---|---------------|----|
| B-1 | `GitlabExecutorPluginTests::test_open_mr_keeps_waiting_until_merged` | 1578 |
| B-2 | `GitlabExecutorPluginTests::test_timeout_raises_before_any_mr` | 1592 |

---

## 総括

| グループ | 件数 | macOS 固有? | 根本原因 | 修正箇所 | 修正規模 |
|---------|------|------------|---------|----------|----------|
| A: PermissionError | 4 | **Yes** | git loose object が 0444 → `open("wb")` 失敗 | `_zero_loose_objects()` 行 68 | 1行挿入（os.chmod） |
| B: DeferDecision | 2 | No（環境変数汚染） | `KIRO_FLOW_DEFER_WAITS=1` が環境に常設、setUp でクリアせず | `GitlabExecutorPluginTests.setUp/tearDown` | 各3行追加 |

パス区切り文字（`os.sep`）・`pathlib` 利用の有無・Posixパスのハードコードは、今回の6件の失敗原因ではない（確認済み）。
