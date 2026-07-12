# 成果報告（BSD/GNU コマンド差異への依存除去）

## (a) 成果・サマリー

**コード変更なし**。対象範囲（`tools/kiro-flow/kiro-flow.py` の `GitBus`/`StateGit`、
`tools/kiro-project/kiro-project.py` の `StateGit`/`DirectStateGit` ——いずれも自己修復
ロジック本体）を精査したが、`sed`/`readlink`/`stat` 等の外部コマンドを shell 経由で
呼び出している箇所は**存在しなかった**。

- git 操作はすべて `subprocess.run(["git", "-C", workdir, ...])` の list 形式（`shell=True` 不使用）
  で `git` バイナリを直接呼んでおり、BSD/GNU 版 `git` 間の挙動差は元々発生しない。
- ファイル属性・パス解決は一貫して Python 標準ライブラリで完結している
  （`os.path.realpath`／`os.path.getmtime`／`os.chmod`／`Path.stat().st_mtime`／
  `Path.resolve()`／`shutil.rmtree`／`os.walk` 等）。`readlink`/`stat` コマンドへの
  委譲は無い。
- リポジトリ全体（`tools/` 配下）で `sed`/`readlink`/`stat -f|-c`/`awk` を実際に外部
  コマンドとして呼んでいるのは `tools/kiro-flow/install.sh`・`tools/kiro-loop/install.sh`・
  `tools/makaroshki-bridge/install.sh`・`tools/gitlab-obsidian-sync/watch.sh` のみで、
  いずれも**インストーラのシェバン書き換え／ラベル監視スクリプト**であり、自己修復ロジック
  とは無関係。install.sh 側は既に `sed -i ''`（BSD）/`sed -i`（GNU）の try/fallback で
  差異を吸収済みで、コメントにも「BSD sed と GNU sed の差異を吸収」と明記されている。
- macOS で失敗していた4テスト（依存タスク t5〜t10 で特定・修正済み）の真因は、自己修復
  ロジックの外部コマンド依存ではなく、テストヘルパー `_zero_loose_objects()`
  （`tools/kiro-flow/tests/test_kiro_flow.py:59`）が非root環境で git の作る `0444` の
  loose object に対し chmod なしで書き込もうとして `PermissionError` になっていた点で、
  コミット `0cf9c599671d89bdb1f967d766dc5c5002bb0bd9` で解消済み（t10 で確認済み）。

## (b) 検証内容と結果

- worktree（`kp/macOS-kiro-flow-git-4-gr-171537`、detached HEAD）で `git status --short`
  差分なしを確認（本タスクでの編集は無し）。
- 完了条件コマンドを実行:
  `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`
  → **900 passed in 122.15s, exit code 0**。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: タスク文中の「自己修復ロジック内の外部コマンド呼び出し（sed/readlink/stat等）」
  は、実装を精査した結果 実在しないと判断した。該当箇所が無い以上「置き換え」は不要であり、
  存在しないコードを作り出す／無関係な install.sh 等に手を入れることは範囲外・過剰修正に
  当たるため見送った（範囲を守る原則）。
- **未解決事項**: なし。完了条件は既に満たされている（依存タスク t10 の時点で達成、本タスク
  でも独立再確認済み）。
- **範囲外で見つけた問題**: なし。install.sh 系の sed 差異吸収は既存のまま妥当で、改善の
  必要は認めなかった。
