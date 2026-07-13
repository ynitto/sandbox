# t6: 失敗2件目の根本原因判定と修正方針

対象: `GitDistributedTests.test_sync_push_self_heals_on_object_corruption`
（`tools/kiro-flow/tests/test_kiro_flow.py`）

## (a) 成果 — 根本原因・判定・修正方針

### 根本原因

このテストは git 自己修復ロジック（`kiro-flow.py` 側の `_is_corrupt_error`/`_rebuild_clone` 等）を
検証するため、テストヘルパー `_zero_loose_objects()`（`test_kiro_flow.py:59-72`、当該呼び出しは L3621）
で loose object ファイルを `open(path, "wb").close()` により 0 バイトへ切り詰め、破損を人工的に模擬する。

macOS（BSD 系 git 実装）では、git が loose object ファイルを作成する際のパーミッションが `0444`
（読み取り専用）になる。切り詰め処理はこのファイルを書き込みモードで `open()` するため、
chmod なしでは `PermissionError` が送出され、**破損を模擬する前段階でテスト自体が例外送出により失敗する**。
Linux（git のデフォルト umask 挙動が異なる環境）では顕在化しないため、macOS 固有の失敗として現れていた。

### 判定: テスト側の前提誤り

- 破損を注入される対象は git 自身が作成した loose object ファイルであり、`kiro-flow.py` の自己修復実装
  （`GitBus`/`StateGit` の `_rebuild_clone`・`_recover`・`_is_corrupt_error` 等、t3 の inventory 参照）
  はこのテストでは一切実行されず、失敗経路にも到達していない。
- 失敗の発生源はテストファイル内のヘルパー関数 `_zero_loose_objects()`（プロダクションコードではなく
  `tools/kiro-flow/tests/test_kiro_flow.py` 内の破損模擬ユーティリティ）であり、「loose object は常に
  書き込み可能である」という暗黙の前提が macOS では成立しなかったことが原因。
- したがって **実装側の欠陥ではなく、テスト側（破損模擬ヘルパー）の前提誤り** と判定する。

### 最小差分の修正方針

- 変更ファイル: `tools/kiro-flow/tests/test_kiro_flow.py`
- 変更関数: `_zero_loose_objects(clone)`（L59–72、モジュール直下のテストヘルパー、`GitDistributedTests`
  および `StateGitSyncTests` の複数テストから共用）
- 変更内容: ループ内で対象パス `p` を `open(p, "wb").close()` する直前に `os.chmod(p, 0o644)` を挿入し、
  書き込み権限を明示的に付与してから切り詰める。呼び出し元テスト（4件）・プロダクションコードは無変更。

```python
for name in os.listdir(d):
    p = os.path.join(d, name)
    os.chmod(p, 0o644)  # macOS: git が 0444 で作る loose object に書き込み権限を付与
    open(p, "wb").close()   # 0 バイトへ切り詰め
    zeroed += 1
```

## (b) 検証内容と結果

- **修正の適用状況**: 上記と完全に同一の差分がコミット `0cf9c599671d89bdb1f967d766dc5c5002bb0bd9`
  （`[kiro-flow] t8 (run-20260712-172155-3661)`）として**既にこのタスクの作業ブランチ
  `kp/macOS-kiro-flow-git-4-gr-171537` の履歴に取り込み済み**であることを確認した
  （`git log --oneline` で `5681a20` の直前に存在、`git show 0cf9c59` の diff で内容一致を確認）。
  作業ツリー（worktree）に未コミット差分はなく（`git status --short` 出力なし）、本タスクでの
  追加コード変更は不要と判断した。
- **対象テスト単体**: `python3 -m pytest tools/kiro-flow/tests/test_kiro_flow.py -k test_sync_push_self_heals_on_object_corruption -v`
  → `1 passed`（Darwin / Python 3.14.2）。
- **完了条件コマンド全体**: `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`
  → `900 passed in 122.99s`、**exit code 0**。t3 が報告した先行run（900 passed）と同数で回帰なし。

## (c) 採用した前提・未解決事項・範囲外所見

- **前提**: t4 の分類（失敗2件目 = `test_sync_push_self_heals_on_object_corruption`、主因 class=d、
  修正コミット `0cf9c59`）を一次情報として採用し、本タスクではその主張を実際のコード差分・コミット
  履歴・テスト実行で独立に再検証した。再検証の結果、t4 の記述と完全に一致した。
- **未解決事項**: なし。作業ツリーへの追加変更は行っていない（fix 済みのブランチであることを確認した
  のみ）。
- **範囲外所見**: t3 が指摘した `GitBus` と `StateGit` の自己修復ロジックの重複、および t4 が言及した
  `test_corrupt_index_clone_is_rebuilt`（`.git/index` への直接バイト書き込みによる別障害モード、
  本タスクの対象4件には非該当）はいずれも本タスクの範囲外であり、変更していない。
