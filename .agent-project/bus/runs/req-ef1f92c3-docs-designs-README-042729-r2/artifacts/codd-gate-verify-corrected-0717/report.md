# codd-gate verify 是正確認レポート（docs-designs-README-042729 r2）

## 結論
verify=fail（人の指摘コマンドは exit 0 に到達しない）。ただし原因は `docs/designs/README.md` の内容ではなく、
`--base` に渡された rev がどちらの worktree でも祖先として解決できない**孤立コミット**であること。
`docs/designs/README.md` 自体は内容検証・完了条件とも pass。**書き換えは行っていない**（issues が README 起因でないため）。

## (a) 実行結果

### 元の完了条件コマンド（対象 worktree: `.../agent-flow-ws-22527-t463t0dk/sandbox`）
```
test -f docs/designs/README.md && grep -q 'agent-project-design.md' docs/designs/README.md \
  && grep -q 'agent-flow-design.md' docs/designs/README.md && grep -q 'codd-gate-design.md' docs/designs/README.md \
  && grep -q 'agent-tools-rename-design.md' docs/designs/README.md
```
→ **exit 0**（PASS）。作業ツリー差分なし（`git status --short` 空）。

### 人の指摘コマンド `codd-gate verify --base 45a480f10edd965081cc9a4b3afcfbb7a916c2e9 --repos repos.json`
同一 worktree でそのまま実行 → **exit 2**「`[codd-gate] エラー: repos レジストリが見つかりません: repos.json`」
（`repos.json` は仕様上 `src` リポジトリ側には存在せず、agent-project が charter から `<root>/repos.json`
＝ `.agent-project/repos.json` に自動生成する契約のため。docs/designs/codd-gate-design.md 18-107行, 248行 参照）。

`--repos repos.json` が bare 相対パスで解決できる唯一の cwd は `.agent-project`（agent-project.yaml の
`root: .agent-project` に対応する control-plane worktree、コメントにも「実行時 cwd は root 自身になる」と明記）。
そこで実行すると：
- `--repo-dir` 未指定 → **exit 2**「スキャン可能な repo がありません」（`repos.json` に `dir`/`local` が無いため）。
- 実運用の `regression_cmd`（`agent-project.yaml` 実物）: `codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.` を同じ cwd で実行 → repos レジストリは解決（＝人の指摘の「`.agent-project/repos.json`→`repos.json`」修正は有効と確認）。しかし **exit 1（NG）**。AMBER は全件 `.agent-project/bus/runs/**` 配下の run 中間成果物（他タスク t1-t4 の readme-entry ドラフト、別タスク verify-codd-gate-042729 の report.md 等）の相互参照切れで、`docs/designs/README.md` の内容とは無関係。かつ `.agent-project` は本タスクの書込対象 worktree ではない（共有チェックアウト）。
- 対象 worktree 側で self-hosted `repos.json`（`dir:"."`、検証後に削除）を用いて同条件を再現しても同じく **exit 1**。診断の結果、`--base 45a480f10edd965081cc9a4b3afcfbb7a916c2e9` は
  - 対象 worktree（sandbox, branch 由来: main 系）の HEAD の祖先ではない
  - `.agent-project`（agent-state ブランチ）の現在の HEAD の祖先でもない
  - どちらの worktree にも当該 SHA を含む ref が存在しない（`git for-each-ref --contains` が空）
  孤立コミット（`agent-project: state sync 2026-07-16T06:36:55`）と判明。祖先関係の無い commit を base にした diff は無関係な全ファイルを差分として拾うため、意味のある検証にならない（`$KIRO_BASE_REV` が agent-state 側の自己修復コミット等で以降 orphan 化したものと推測）。

### 内容単体の裏取り（参考）
```
codd-gate check --repo-dir src=. --refs docs/designs/README.md
```
→ `OK: docs/designs/README.md の参照は全て解決` / **exit 0**。README 自体の参照切れは無い。

## (b) 検証内容と結果まとめ
| コマンド | cwd | 結果 |
|---|---|---|
| 完了条件コマンド | 対象 worktree | exit 0 PASS |
| `codd-gate verify --base <sha> --repos repos.json`（そのまま） | 対象 worktree | exit 2（repos.json 不在。仕様上想定内） |
| 同上 | `.agent-project` | exit 2（`--repo-dir` 省略により「スキャン可能な repo なし」） |
| 実運用 regression_cmd 相当（`--repo-dir src=.` 付加） | `.agent-project` | exit 1 NG（AMBER は他タスクのbus成果物起因、README無関係） |
| 同上を対象 worktree で self-hosted `repos.json` により再現 | 対象 worktree（検証後 repos.json 削除・差分なし） | exit 1 NG（`--base` が孤立コミットのため） |
| `codd-gate check --refs docs/designs/README.md` | 対象 worktree | exit 0 OK（README単体は無傷） |

## (c) 採用した前提・未解決事項・範囲外で見つけた問題
- **前提**: 人の指摘テキストは `--repo-dir src=.` を省略した簡略表記と判断し、`agent-project.yaml` 実物の `regression_cmd` を実効コマンドとして採用した（元の失敗ログ・修正指示のいずれも同様に省略していたため）。
- **未解決事項**: `--base 45a480f10edd965081cc9a4b3afcfbb7a916c2e9` がどちらの worktree でも祖先解決できない孤立コミットである点は worker からは是正不能（$KIRO_BASE_REV の再採取か、agent-state 側の履歴問題の調査が必要）。
- **範囲外で見つけた問題（未修正）**:
  1. `.agent-project/bus/runs/**` 配下（他タスク t1-t4, verify-codd-gate-042729 等）の中間成果物に相互参照切れが多数あり、`.agent-project` を `--repo-dir src=.` で走査すると常に NG になる。docs-designs-README-042729 の担当範囲外、かつ `.agent-project` は共有チェックアウトのため本タスクからは修正していない。
  2. `.agent-project/docs/designs/README.md`（本来 `src` リポジトリ直下にあるべきファイルの孤立コピー、恐らく過去 run の副作用）が `.agent-project` 側に存在するが、対象 4 設計ファイルは同ディレクトリに無くリンク切れの原因の一つになりうる。これも `.agent-project` 内のため本タスクからは触っていない。
- `docs/designs/README.md` は完了条件・内容検証とも pass のため**無修正**。対象 worktree の `git status` は空（一時作成した検証用 `repos.json` は削除済み）。
