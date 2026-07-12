# macOS 固有失敗原因 分類レポート

作成日時: 2026-07-12T17:26 JST  
参照元: t1/failures.md  
検証環境: macOS Darwin / git 2.50.1 (Apple Git-155)

---

## サマリー

失敗 6 件を 2 種の根本原因に分類した。

| 分類ID | 種別 | 件数 | 失敗テスト |
|--------|------|------|-----------|
| CAT-A  | macOS git loose object パーミッション（0444） | 4 | 下記 A-1〜A-4 |
| CAT-B  | テスト環境変数 `KIRO_FLOW_DEFER_WAITS=1` 汚染 | 2 | 下記 B-1〜B-2 |

**タスク指示の「4件」とは CAT-A を指す**。CAT-B の2件は同一テストファイルで発生するが、
macOS 固有ではなく環境変数の事前状態依存という別原因。全6件を報告する。

---

## CAT-A: macOS git loose object パーミッション（0444）— 4件

### 原因の詳細

git は loose object（`.git/objects/xx/yy`）を **0444（読み取り専用）** で書き込む。
これは意図的な設計で、オブジェクト不変性の保護が目的。

```
-r--r--r--  .git/objects/4c/f9f177c4c015836fca6a31f9c3917e89ae29ec
-r--r--r--  .git/objects/92/e9f8ce16311d7cd4eb8a32010bce3d6deef2a9
```

この挙動は Linux でも同様だが、**macOS ではルートユーザーでも open("wb") で上書きできず
PermissionError: [Errno 13] が必ず発生する**（Linux では root が 0444 を無視できるが、
macOS の HFS+/APFS はそれを許可しない）。

### 失敗箇所

`_zero_loose_objects()` 関数（`test_kiro_flow.py` 行 61–68）:

```python
open(os.path.join(d, name), "wb").close()   # 0 バイトへ切り詰め
# ↑ 0444 ファイルに "wb" で開こうとして PermissionError
```

### 影響テスト 4件

| テストID | クラス | 失敗行 | 失敗箇所の呼び出し形式 |
|---------|--------|--------|----------------------|
| A-1 | `GitDistributedTests::test_empty_objects_clone_is_rebuilt_on_reuse` | 3596 | `self.assertGreater(_zero_loose_objects(clone), 0)` |
| A-2 | `GitDistributedTests::test_sync_push_self_heals_on_object_corruption` | 3613 | `_zero_loose_objects(clone)` |
| A-3 | `StateGitSyncTests::test_empty_objects_state_clone_is_rebuilt_on_reuse` | 4648 | `self.assertGreater(_zero_loose_objects(clone), 0)` |
| A-4 | `StateGitSyncTests::test_state_sync_self_heals_on_object_corruption_midflight` | 4672 | `_zero_loose_objects(sg.clone)` |

### 修正方針

`_zero_loose_objects()` 内で `open()` の前に `os.chmod(path, 0o644)` を挿入する。

```python
# 変更前（行 68）
open(os.path.join(d, name), "wb").close()

# 変更後
p = os.path.join(d, name)
os.chmod(p, 0o644)          # macOS: 0444 では open("wb") が PermissionError になるため
open(p, "wb").close()
zeroed += 1
```

変更対象: `tools/kiro-flow/tests/test_kiro_flow.py` 行 67–68 の `_zero_loose_objects()` 内のみ（1 箇所）。
プロダクションコードへの変更は不要。

---

## CAT-B: テスト環境変数 `KIRO_FLOW_DEFER_WAITS=1` 汚染 — 2件

### 原因の詳細

`gitlab.py` の `execute()` は `KIRO_FLOW_DEFER_WAITS=1` が設定されていると、
ブロック待機せずに `DeferDecision` を送出するパスに分岐する（行 953）。

```python
if os.environ.get("KIRO_FLOW_DEFER_WAITS") == "1":
    ...
    raise DeferDecision(...)   # ← タイムアウト設定に関わらず必ず通る
```

`GitlabExecutorPluginTests.setUp()`（行 1435）は `KIRO_FLOW_EXECUTOR_CONFIG` のみを制御し、
`KIRO_FLOW_DEFER_WAITS` を **unset も上書きもしない**。テスト実行前に環境変数が
`KIRO_FLOW_DEFER_WAITS=1` の状態だと、`_run_with()` が `DeferDecision` を受け取り、
テストが期待する tuple 戻り値（`text, data`）や `RuntimeError` が得られず失敗する。

### 失敗テスト 2件

| テストID | クラス | 失敗行 | 期待していた挙動 |
|---------|--------|--------|----------------|
| B-1 | `GitlabExecutorPluginTests::test_open_mr_keeps_waiting_until_merged` | 1578 | `execute()` がブロック待機して `(text, data)` を返す |
| B-2 | `GitlabExecutorPluginTests::test_timeout_raises_before_any_mr` | 1592 | タイムアウト超過で `RuntimeError` を送出する |

### CAT-B が macOS 固有でない理由

`KIRO_FLOW_DEFER_WAITS=1` は OS 非依存の環境変数汚染で、CI 設定・shell rc・
前テストの tearDown 漏れなど複数の原因でセットされ得る。
ただし macOS 環境でのテスト実行時に実際に観測されているため、修正対象として含める。

### 修正方針

`GitlabExecutorPluginTests.setUp()` で `KIRO_FLOW_DEFER_WAITS` を明示的に unset し、
`tearDown()` で復元する。

```python
def setUp(self):
    ...
    self._prev_env = os.environ.get("KIRO_FLOW_EXECUTOR_CONFIG")
    os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = json.dumps(self._cfg)
    # 追加: KIRO_FLOW_DEFER_WAITS が汚染されていても park ロジックが動かないようにする
    self._prev_defer = os.environ.pop("KIRO_FLOW_DEFER_WAITS", None)

def tearDown(self):
    if self._prev_env is None:
        os.environ.pop("KIRO_FLOW_EXECUTOR_CONFIG", None)
    else:
        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = self._prev_env
    # 追加: tearDown で復元
    if self._prev_defer is not None:
        os.environ["KIRO_FLOW_DEFER_WAITS"] = self._prev_defer
```

変更対象: `tools/kiro-flow/tests/test_kiro_flow.py` の `GitlabExecutorPluginTests.setUp` / `tearDown`（2 箇所）。

---

## 修正対象ファイルまとめ

| ファイル | 変更箇所 | 対応分類 |
|---------|---------|---------|
| `tools/kiro-flow/tests/test_kiro_flow.py` | `_zero_loose_objects()` 行 67–68：`os.chmod` 追加 | CAT-A（4件） |
| `tools/kiro-flow/tests/test_kiro_flow.py` | `GitlabExecutorPluginTests.setUp/tearDown`：`KIRO_FLOW_DEFER_WAITS` の保存・クリア・復元 | CAT-B（2件） |

修正はすべてテストコードのみ。プロダクションコード（`kiro-flow.py`・`executors/gitlab.py`）の変更は不要。
