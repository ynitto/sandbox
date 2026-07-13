# macOS 固有要因調査③: 実行環境の git 設定と失敗テスト前提との差分

## (a) 成果物 / サマリー

対象: 割当ワークスペース `kiro-flow-ws-25146-najl633l/sandbox`（`https://github.com/ynitto/sandbox/`, HEAD `c91b626`）。
`git config --list --show-origin` を scope 別（--local / --global / --system）に採取し、`init.defaultBranch` /
`user.name` / `user.email` / `commit.gpgsign` / `core.hooksPath` を個別確認した。

### 採取結果（scope 別）

| scope | 由来ファイル | 値 |
|---|---|---|
| local（worktree の `.git`。実体は `kiro-git-cache/*.git`） | `.../kiro-git-cache/17fc970f...git/config` | `user.name=kiro-flow` / `user.email=kiro-flow@local`（kiro-flow が設定）。`init.defaultBranch` 等は無し |
| global | `/Users/nitto/.gitconfig` | `user.name=ynitto7` / `user.email=ynitto7@gmail.com` / `push.autoSetupRemote=true` |
| **system**（macOS 固有） | `/Library/Developer/CommandLineTools/usr/share/git-core/gitconfig` | `credential.helper=osxkeychain` / **`init.defaultBranch=main`** |
| system（POSIX 標準パス） | `/etc/gitconfig` | 存在しない（`fatal: unable to read config file`） |

個別キーの実効値（local > global > system の優先順位を反映）:

```
init.defaultBranch = main   （出所: CommandLineTools 同梱の system gitconfig）
user.name           = kiro-flow      （kiro-flow がこの worktree の local config に設定）
user.email          = kiro-flow@local
commit.gpgsign      = (未設定)
core.hooksPath       = (未設定)
```

git バージョン: `git version 2.50.1 (Apple Git-155)`（Xcode Command Line Tools 付属）。

### macOS 固有ポイント

Linux/CI 環境には通常 `/etc/gitconfig` が存在しないか、あっても distro パッケージ由来の別内容であり、
`init.defaultBranch=main` を system scope で強制する慣行は無い（git 2.28 未満は `-b` 省略時に一律 `master`
を作る）。macOS では Xcode Command Line Tools が `init.defaultBranch=main` と `credential.helper=osxkeychain`
を system gitconfig として同梱しており、`git init`（`-b` 省略）が **常に `main` ブランチを作る**という
macOS 固有の暗黙前提が生まれる。

## (b) 検証内容と結果

1. **`git config --list --show-origin`（local/global/system 個別）** — 上表のとおり採取。差分の起点は
   system scope の `init.defaultBranch=main` のみ。`commit.gpgsign` と `core.hooksPath` はどの scope にも
   存在しない（未設定）。
2. **失敗テストの前提コード調査**（`tools/kiro-flow/tests/test_kiro_flow.py`,
   `tools/kiro-project/tests/test_kiro_project.py` を grep）:
   - 両テストファイルとも冒頭で `GIT_CONFIG_COUNT=1` / `GIT_CONFIG_KEY_0=commit.gpgsign` /
     `GIT_CONFIG_VALUE_0=false` を `os.environ` に設定し、**commit.gpgsign をテスト全体で明示的に無効化**
     している（コメントに「署名が有効な環境では偶発的に落ちる」と明記）。今回の採取では gpgsign 自体は
     未設定なので現状は発火しないが、有効化された環境への防御はコード側に既に存在する。
   - `git init` を `-b <branch>` 明示なしで呼ぶ箇所（`test_kiro_project.py:743,1103,4214`,
     `test_kiro_flow.py:3449,3583` 等）を全て確認したが、いずれもブランチ名を後段でアサートしておらず
     `init.defaultBranch` の値に依存しない。
   - ブランチ名を後段で使うケース（`test_kiro_flow.py` の `GitBusMultiNodeTests.setUp`
     （`kf-git-` bare リポジトリ）、`test_kiro_project.py` の `TestStateSyncBatching._init_repo` /
     `test_direct_does_not_amend_pushed_commit`）は、**`git init` 直後に
     `git symbolic-ref HEAD refs/heads/main` を明示実行して `main` に固定**しており、`init.defaultBranch`
     の実効値（このマシンでは `main`、他環境では `master` の可能性）に左右されない。
     `test_kiro_flow.py:3328` のコメントに「既定ブランチ名に依存しない: git バージョンや
     init.defaultBranch 設定に関わらず」と明記されている。
   - コミット時の identity（user.name/user.email）は、全箇所で `-c user.email=... -c user.name=...`
     または `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`/`GIT_COMMITTER_NAME`/`GIT_COMMITTER_EMAIL` を明示注入
     しており、global config（このマシンでは `ynitto7`/`ynitto7@gmail.com`）に依存する箇所は無い。
   - `core.hooksPath` はこのマシンでは未設定（system/global 双方）であり、テスト側にも参照箇所が無い
     ため、差分自体が発生しない。
3. **完了条件コマンドの実行**（`python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`）:
   `900 passed in 123.19s`、終了コード 0 で成功。実行時間 123.19s は、依存タスク t5 が指摘した
   「旧 verify タイムアウト 120.0s」を超過する水準であり、DR-0010（`decisions/macOS-kiro-flow-git-4-gr-171537.md`）
   の「テスト自体は green、verify_timeout=600 への引き上げが必要」という結論と整合する。

## (c) 採用した前提・未解決事項・範囲外の問題

- **前提**: 「失敗テストが暗黙に前提としている値」の照合対象は、割当ワークスペース内の
  `tools/kiro-flow/tests/test_kiro_flow.py` と `tools/kiro-project/tests/test_kiro_project.py`
  （完了条件コマンドが直接参照する2ファイル）とした。
- **調査結論**: 採取した macOS 固有の git 設定差分は system scope の `init.defaultBranch=main`
  （および無関係な `credential.helper=osxkeychain`）のみ。しかし対象テストコードは、ブランチ名に
  依存する箇所を全て `-b` 明示または `symbolic-ref` 強制で自己完結させており、`commit.gpgsign` も
  明示的にテスト全体で無効化している。`user.name`/`user.email`/`core.hooksPath` についても、
  テスト側がグローバル値に依存する箇所は発見できなかった。**したがって、今回採取した macOS の
  git 設定差分がテスト失敗の直接原因になっている証拠は見つからなかった。**
  これは依存タスク t5 の結論（「4件失敗」の実体は未確認、900 passed/0 failed が実測、原因は
  verify タイムアウト 120s の疑いが濃厚）と整合する。
- **未解決事項**: 「macOS で失敗する git 自己修復テスト4件」の具体的なテスト名・トレースバックは、
  本 run 系列のどの成果物（t1・t4・t5・アーカイブ・decisions）にも記録が無く、本タスクでも新たに
  発見できなかった。仮に将来 macOS 環境で再現した場合、まず疑うべき差分は本レポートの
  `init.defaultBranch=main`（system scope, CommandLineTools 由来）だが、現状のテストコードは
  この差分に対して耐性がある設計になっている。
- **範囲外で見つけた問題**: 無し（ファイル編集はしていない。テスト実行は完了条件確認のための
  非破壊コマンドのみ）。
