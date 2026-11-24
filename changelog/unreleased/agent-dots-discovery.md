### tools/agent-dots

- 蓄積から次の仕事を発見する Discovery の Phase 1 を追加。`dot.py harvest` が許可されたソース（既定では agent-dots の記録・`docs/plans`・スキル feedback）を決定論的に走査して `.dot/discovery/` に索引を作り、空き時間の tick が索引の要約だけで Copilot アプリに評価させ、`act` が出典を検証してローカルの提案キューへ入れる。人は `dot.py proposals` で採否を決め、承認された提案は既存の goal / routine として登録され、採否と結果が履歴に残る。Obsidian Vault コレクタ（公式 CLI 優先・直接読みフォールバック）を含む全コレクタを実装し、日次の harvest 用 Dagu 設定例も追加した。
