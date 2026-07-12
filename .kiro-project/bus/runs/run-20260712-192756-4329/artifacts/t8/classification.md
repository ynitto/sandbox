# macOS git 自己修復テスト4件の分類

class=テストの環境依存前提

| テスト | 原因分類 | 修正先 | 根拠 |
|---|---|---|---|
| `GitDistributedTests::test_empty_objects_clone_is_rebuilt_on_reuse` | テストの環境依存前提 | fixture修正 | 障害注入用 `_zero_loose_objects()` が loose object を直接切り詰める。macOS の Apple Git が object を `0444` で作成する実測があり、修正前の `open(..., "wb")` は `PermissionError` を再現する一方、自己修復本体の異常は確認されていない。 |
| `GitDistributedTests::test_sync_push_self_heals_on_object_corruption` | テストの環境依存前提 | fixture修正 | 同じ障害注入 helper を使用するため、失敗点は本体の push／自己修復処理ではなく read-only object の破損生成処理にある。 |
| `StateGitSyncTests::test_empty_objects_state_clone_is_rebuilt_on_reuse` | テストの環境依存前提 | fixture修正 | 同じ障害注入 helper が macOS の `0444` object を書込可能と仮定していた。StateGit の再構築処理に実装バグを示す証拠はない。 |
| `StateGitSyncTests::test_state_sync_self_heals_on_object_corruption_midflight` | テストの環境依存前提 | fixture修正 | 同じ障害注入 helper の切り詰め前提だけが OS 差を踏む。通常順・逆順・個別 process の全条件で現 HEAD は成功し、順序依存やグローバル状態漏れは否定された。 |

## 振り分け結論

- 実装修正: 0件。`GitBus` / `StateGit` の自己修復ロジックの不具合を示す再現・tracebackはない。
- テスト修正: 0件。期待値や assertion の誤りではなく、共通の障害注入 fixture/helper が原因である。
- fixture修正: 4件。`_zero_loose_objects()` で切り詰め前に `os.chmod(path, 0o644)` を行う修正が適切。現 HEAD には既にこの修正が含まれる。
- 環境設定不足: 0件。pytest の導入/PATH差や Git user設定の有無は一部調査環境で観測されたが、対象4件の macOS失敗原因とは結び付かない。

## 証拠の整合性

依存成果物はいずれも対象4件の失敗を現 HEAD で再現しておらず、全体結果には `900 passed` と `899 passed, 1 skipped` の環境差がある。ただし全報告で完了条件の終了コードは0であり、原因分類は修正前挙動の直接再現（`0444` objectへの書込みで `PermissionError`）と既存修正差分に基づく。Linux/CIで同一4件が green という点には直接ログがなく、分類根拠には用いていない。

## 完了条件

`python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q` → `900 passed in 122.29s`、終了コード0。
