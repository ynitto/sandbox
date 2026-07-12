# macOS git 自己修復テスト4件の暗黙前提

## 1テスト1行

- `GitDistributedTests::test_empty_objects_clone_is_rebuilt_on_reuse`: `setUp` が `tempfile.mkdtemp()` 配下に `main` のローカル bare remote と clone 置場を作り、初回 push 済みの管理 clone（`<root>/clones/empty-obj/.git`）に loose object が1個以上あること、その read-only になり得る object を書込可能化して0バイト化した後も remote を正として同じ絶対 clone パスを破棄・再 clone できることを前提とする。
- `GitDistributedTests::test_sync_push_self_heals_on_object_corruption`: 同じ隔離 bare remote／絶対 clone パス構成で、`req0` push 後の管理 cloneの loose object を0バイト化し、壊れた cloneへの未 push `req1` は消失を許容する一方、自己修復後の `req2` と既 push `req0` が remote から別の一時絶対パスへ cloneして確認できることを前提とする。
- `StateGitSyncTests::test_empty_objects_state_clone_is_rebuilt_on_reuse`: `setUp` が `tempfile.mkdtemp()` 配下に HEAD=`main` の bare remote、ローカル bus、管理 clone `<tmp>/bus/.state-git` を置き、`state_git_subdir="kf"` の初回同期で commit/loose object が作られ、`_STATE_GITS.clear()` をプロセス再起動相当として同じ絶対パスを remote から再生成できることを前提とする。
- `StateGitSyncTests::test_state_sync_self_heals_on_object_corruption_midflight`: 初期同期済み（`_ready=True`）の `<tmp>/bus/.state-git` と、別の一時 cloneから1 commit進めた `main` remoteを用意し、管理 cloneの loose objectを0バイト化すると次の pullが破損HEADを必ず読み、同期は例外を漏らさず `.git` を削除して `_ready=False` にし、次回同じ絶対パスへ再 cloneできることを前提とする。

## fixture・環境の共通契約

- pytest の `tmp_path` / `monkeypatch` fixture は使わず、`unittest.TestCase.setUp`、`tempfile.mkdtemp`、`mock.patch` を使用する。各テストの git remote、clone、bus はテスト専用一時ディレクトリ内の絶対パスで、相対パスや cwd に依存しない。
- モジュール import 時に cwd を別の一時ディレクトリへ `chdir` し、開発者の `./kiro-flow.yaml` を遮断する。`KIRO_FLOW_STUB_SLEEP_MAX=0`、`GIT_CONFIG_COUNT/KEY_0/VALUE_0` による `commit.gpgsign=false`、存在しない `KIRO_SKILL_REGISTRY` を設定し、実環境・署名・自動更新の影響を遮断する。
- 4件だけの `mock.patch` は `kf.time.sleep` の無効化であり、破損検出・clone/pull/push自体は実 git を使用する。
- macOS では loose object が `0444` になり得るため、破損注入 helper は `os.chmod(path, 0o644)` 後に0バイト化する必要がある。

## 採用した前提・範囲

- 「失敗している4テスト」は `_zero_loose_objects()` を呼ぶ上記4件と解釈した。呼出箇所がちょうど4件で、macOS固有の read-only loose object と直接対応するためである。
- 調査・報告のみを担当範囲とし、指定 worktree のファイルは変更していない。既存ブランチには破損注入 helper の macOS対応などが既に含まれていた。

## 検証

- 対象4件: `4 passed in 2.55s`。
- 全体完了条件: `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q` → `900 passed in 119.61s (0:01:59)`、終了コード0。
