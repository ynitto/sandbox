# Git 設定依存の実測棚卸し

## サマリー

macOS の指定 worktree 上で、`mktemp -d` 配下に空の一時リポジトリを `git init` して実測した。リポジトリのソースコードは変更していない。

| 観点 | 実測結果 | 設定元・含意 |
|---|---|---|
| Git version | `git version 2.50.1 (Apple Git-155)` | Apple Command Line Tools 付属 Git |
| `init.defaultBranch` | `main` | `/Library/Developer/CommandLineTools/usr/share/git-core/gitconfig`。実際の初期 HEAD も `main` |
| `user.name` | 設定あり（値は機密性を考慮して非記載） | `~/.gitconfig` |
| `user.email` | 設定あり（値は機密性を考慮して非記載） | `~/.gitconfig` |
| `core.ignorecase` | `true` | 一時リポジトリの `.git/config` に `git init` が設定 |
| `safe.directory` | 設定なし | `git config --show-origin --get-all safe.directory` の出力なし |

`git init` の stderr は空で、旧既定ブランチ名に関する hint も出なかった。したがってテストは `master` 固定、ユーザー identity の未設定、大小文字を区別するファイルシステム、または `safe.directory` の存在を暗黙に仮定すべきではない。

## 検証内容と結果

- 完了条件: `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`
  - システム Python での初回実行: exit 1（`No module named pytest`）。これはコードの失敗ではなくテストランナー不足。
  - `mktemp -d` の隔離 venv に `pytest` を導入し、その `bin` を `PATH` 先頭にした同一コマンド: **exit 0**、`899 passed, 1 skipped in 119.16s`。
- `codd-gate impact --repo-dir default=. --base main --json`: exit 0。テスト内のダミー参照に対する既存 amber 所見は出たが、本観点タスクによる変更はない。
- 一時リポジトリと隔離 venv は検証後に削除した。

## 採用した前提・未解決事項・範囲外

- 完了とは、指定された Git 設定項目を一時リポジトリで実測し、後続タスクが macOS 固有の失敗原因を判断できる形で記録し、指定テストコマンドを利用可能な隔離環境で exit 0 にすること、と解釈した。
- `user.name` / `user.email` は「設定有無」が要求事項であり、実値の共有は不要かつ個人情報になり得るため伏せた。
- `safe.directory` は本ユーザー環境では未設定。所有者不一致の別ユーザー環境での挙動までは本タスクの実測範囲外。
- 指定 worktree は開始時点で detached HEAD だった。利用規約に従って checkout・branch・commit・push は実行していない。
- codd-gate の amber はテスト fixture 内の文字列を参照と解釈した既存所見であり、範囲外のため修正していない。
