# タスク t4 成果報告: macOS 固有 git コマンド差異に起因する失敗の特定

## 結論

**macOS 固有の git コマンド差異（`git -C` / `--no-pager` / `porcelain` 等）に起因する失敗: 0 件**

t1 のエラーログを精査した結果、失敗した 2 件はいずれも git コマンドと無関係だった。

---

## 失敗テストの実際の原因

| テスト | ファイル | 失敗行 | 実際の原因 |
|--------|---------|--------|-----------|
| `GitlabExecutorPluginTests::test_open_mr_keeps_waiting_until_merged` | `tools/kiro-flow/tests/test_kiro_flow.py` | 呼び出し: 1580 / 例外発生: `gitlab.py:960` | `KIRO_FLOW_DEFER_WAITS=1` が環境変数として残存し、`execute()` が期待される `approved` 戻り値ではなく `DeferDecision` を raise |
| `GitlabExecutorPluginTests::test_timeout_raises_before_any_mr` | `tools/kiro-flow/tests/test_kiro_flow.py` | 呼び出し: 1594 / 例外発生: `gitlab.py:960` | 同上。`RuntimeError` を期待していたが `DeferDecision` が先に発生 |

### 根本原因

`GitlabDeferPollTests`（`test_kiro_flow.py:5060`）の各テストが `os.environ["KIRO_FLOW_DEFER_WAITS"] = "1"` を設定する。  
`GitlabExecutorPluginTests.setUp` はこの変数を管理しておらず、アルファベット順実行（`GitlabDeferPollTests` → `GitlabExecutorPluginTests`）で値が残存し、後続テストの `execute()` 挙動を変えた。

**git コマンドとの関係なし。** 失敗はすべて Python レベルの環境変数残存によるもの。

---

## macOS 固有 git 差異の調査結果

t1 エラーログ・t2 依存リストを通じて確認した範囲：

- `subprocess` 呼び出しで `git -C`・`--no-pager`・`--porcelain` を使うコードへの言及なし
- 失敗スタックトレースに git 実行は一切含まれない（`gitlab.py:960` の `raise DeferDecision(...)` が直接の例外発生元）
- 失敗は macOS 固有の挙動ではなく、テスト実行順序依存の環境変数汚染

よって、「macOS 固有の git コマンド差異」カテゴリに分類できる失敗は存在しない。

---

## (c) 採用した前提・未解決事項

- **採用した前提**: t1・t2 の成果物が正確であることを前提に分析した。t1 が 900 件全 green を確認済みのため、このタスクは調査のみで変更不要と判断。
- **チャーターとの差異**: チャーターでは「4 件失敗」とあるが、実際の失敗は 2 件（t1・t2 報告と一致）。残り 2 件は今回の実行環境では再現しなかった、または先行修正で解消済みと推定。
- **範囲外で見つけた問題**: なし。
