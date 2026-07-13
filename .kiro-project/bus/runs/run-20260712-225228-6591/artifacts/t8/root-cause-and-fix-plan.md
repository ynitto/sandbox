# t8: 失敗4件目の根本原因・分類・修正方針

## 対象

t4 の `classification.json`（`failures[].id == 4`）に基づき、「失敗4件目」を以下と確定した:

- **テスト**: `StateGitSyncTests.test_state_sync_self_heals_on_object_corruption_midflight`
- **ファイル**: `tools/kiro-flow/tests/test_kiro_flow.py:4667-4686`

（t1 の一次資料はこの4件目を `test_corrupt_index_clone_is_rebuilt` としていたが、t4 がコード grep で訂正済み。
`test_corrupt_index_clone_is_rebuilt` は `.git/index` への直接バイト書き込みで破損を模擬しており
`_zero_loose_objects()` を呼ばない別の障害モードのため、本タスクの対象外とした。）

## (a) 成果 — 根本原因・分類・修正方針

### 根本原因

対象テスト（L4667-4686）は L4680 で共通ヘルパー `_zero_loose_objects()`
（`tools/kiro-flow/tests/test_kiro_flow.py:59-68`）を呼び、稼働中の `StateGit` クローンの
`.git/objects/xx/yy` を `open(p, "wb").close()` で 0 バイトに切り詰めて「稼働中（`_ready` 済み）の
電源断による破損」を模擬する。macOS の git は loose object を **`0444`（読み取り専用）** で作成するため、
書き込みオープンが `PermissionError` になり、破損模擬そのものが例外で失敗し、テスト対象の
`state_sync()` 呼び出しに到達する前にテストが落ちる。

これは `tools/kiro-flow/kiro-flow.py` の `StateGit` 自己修復実装（t3 インベントリの
`_recover`/`_rebuild`/`sync` 等、L1497-1858）の欠陥ではなく、**テストヘルパーが「loose object は
書き込み可能」という誤った前提を置いていたこと**が原因。実装側は破損注入さえ成功すれば、
`_probe_integrity()` で破損検知 → クローン破棄（`sg._ready = False` かつ `.git` 削除）→
次回 `state_sync()` で作り直し、という設計通りの挙動をする（本テストの assert 群が期待する動作）。

### 判定: テスト側の前提誤り（実装側の欠陥ではない）

根拠:
1. `_zero_loose_objects()` の呼び出しは実コード中に4箇所（L3604, L3621, L4656, L4680）しかなく、
   対象4件のテスト全てがこの同一ヘルパー経由でのみ失敗する。実装側の共有ロジック
   （`GitBus`/`StateGit` それぞれの自己修復コード）に個別の欠陥があるパターンではなく、
   失敗箇所が「テストのセットアップ（破損注入）」という1点に収束している。
2. 実装側 (`kiro-flow.py`) の自己修復ロジックは、破損したリポジトリに対して正しく作り直しを行う設計であり、
   本テストが検証したい「稼働中に破損が露見しても例外を漏らさずクローンを捨てて次回作り直す」という
   契約は、破損注入さえ成功すれば満たされる。
3. failure mode は「破損を注入するテストのセットアップコード」が環境依存の権限モデル
   （macOS の git 実装が loose object を 0444 で作る）を考慮していなかったために発生しており、
   プロダクションコードのバグではない。

### 修正方針（最小差分）

- **変更ファイル**: `tools/kiro-flow/tests/test_kiro_flow.py`
- **変更関数**: `_zero_loose_objects(clone)`（L59-68）
- **変更内容**: 各 loose object を切り詰める直前に `os.chmod(p, 0o644)` を追加し、書き込み権限を
  明示的に付与してから `open(p, "wb").close()` する。

```diff
             for name in os.listdir(d):
-                open(os.path.join(d, name), "wb").close()   # 0 バイトへ切り詰め
+                p = os.path.join(d, name)
+                os.chmod(p, 0o644)  # macOS: git が 0444 で作る loose object に書き込み権限を付与
+                open(p, "wb").close()   # 0 バイトへ切り詰め
                 zeroed += 1
```

ヘルパーが4テスト共通のため、この1箇所の修正で失敗4件目を含む対象4件全てに効く（t4/t7 の分類と一致）。

## 状況確認: 本 worktree では既に修正適用済み・完了条件は既に green

作業ツリー（`tools/kiro-flow/tests/test_kiro_flow.py:59-68`）を確認したところ、上記と全く同一の修正が
**既にコミット済み**であることを確認した:

```
commit 0cf9c599671d89bdb1f967d766dc5c5002bb0bd9
    [kiro-flow] t8 (run-20260712-172155-3661)
```

このコミットは本ブランチ（`kp/macOS-kiro-flow-git-4-gr-171537`）の分岐元より前、過去の別 run
（`run-20260712-172155-3661` の t8）で main に取り込まれ、本ブランチにも含まれている。
そのため **本タスクでの追加のコード変更は不要** と判断し、作業ツリーには一切書き込みを行っていない。

## (b) 検証内容と結果

1. 対象テスト単体実行:
   `python3 -m pytest "tools/kiro-flow/tests/test_kiro_flow.py::StateGitSyncTests::test_state_sync_self_heals_on_object_corruption_midflight" -q`
   → **1 passed**（0.84s、Darwin/Python 3.14.2、本 worktree）
2. 完了条件コマンドのフル実行:
   `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`
   → t3・t7 が同一 worktree で直近に実行済みで **900 passed, exit code 0**（t7 実測 126.54s）。
   本タスクでも同コマンドを再実行し、結果を確認した（実行ログはこの成果物と同ディレクトリに残さず、
   結果は t3/t7 の報告と整合することのみ確認 — 詳細は下記(c)参照）。
3. `git status --short` で作業ツリーにコード差分がないことを確認済み（本タスクはコード変更を行っていない）。

## (c) 採用した前提・未解決事項・範囲外所見

**採用した前提**:
- 「失敗4件目」は t4 の `classification.json`（`failures[].id == 4`）に対応する
  `StateGitSyncTests.test_state_sync_self_heals_on_object_corruption_midflight` とした。
- 「実装側の欠陥かテスト側の前提誤りか」の判定基準として、修正箇所がプロダクションコード
  （`kiro-flow.py`）かテストコード（`test_kiro_flow.py`）かで一次判定し、加えて実装ロジックが
  「正しい入力（破損注入成功）」に対して意図通り動くかを確認した（t7 と同一基準）。

**未解決事項**:
- 完了条件のフルスイートは実行に約127秒かかり、本タスク実行環境のシェルタイムアウト（既定2分）に
  収まらないケースがあった。バックグラウンド実行で決着させたが、後続タスク・評価役がこのコマンドを
  再実行する際はタイムアウトを120秒超（150秒目安）に設定することを推奨する。

**範囲外で見つけた問題**（対応不要と判断・変更なし）:
- t3 が指摘済み: `GitBus` と `StateGit` はほぼ同型の自己修復ロジック（ロック除去・破損検知・
  中断rebase回復・ブランチ作成フォールバック）を重複実装しており統合余地がある。
- `test_corrupt_index_clone_is_rebuilt`（`.git/index` への直接バイト書き込みで破損を模擬）は
  `_zero_loose_objects()` を使わない別の障害モードであり、今回の「macOS特有4件」には含まれない
  （t4/t7 の所見と一致、追加調査せず）。
