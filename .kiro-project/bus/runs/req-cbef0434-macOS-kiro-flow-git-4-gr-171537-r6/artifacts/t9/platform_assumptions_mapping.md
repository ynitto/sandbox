# macOS 固有要因調査④: git 自己修復ロジック本体のプラットフォーム依存前提 4 種と「4件の失敗」への対応付け

## (a) 成果 / サマリー

**結論: 4種の前提のうち3種はコード上既に防御済み（該当なし）、1種（環境変数非隔離）のみ実装上の
根拠が見つかったが、そもそも対応付けるべき「4件の失敗」自体が実在しない（900 passed / 0 failed,
exit 0）ため、いずれの前提も失敗と対応付けることはできない。**

依頼は「t3 の抽出結果に含まれる…前提を列挙し、4件の失敗のどれを説明できるかを対応付ける」だったが、
`artifacts/t3/` は空ディレクトリ（ファイル成果物なし）であり、t3 の抽出結果そのものは本 run の
成果物として存在しなかった。そのため、t2（テスト棚卸し）が特定した対象クラス
（`GitBus` / `StateGit` の自己修復ロジック、`tools/kiro-flow/kiro-flow.py` L1062-1670 付近）を
自分で読み取り専用確認し、4種の前提を実装コードで裏付けた。

### 前提 4 種の列挙と検証結果

| # | 前提 | 該当箇所（読み取りのみ） | 判定 |
|---|---|---|---|
| 1 | 生パス文字列比較 | `GitBus._origin_matches()` L1241-1244、`StateGit._is_managed()` L1611-1613：`origin == self.remote or (... os.path.realpath(origin) == os.path.realpath(self.remote))` | 生の `==` は短絡評価の高速パスに過ぎず、`or` で realpath 正規化比較に必ずフォールバックする。symlink 差（`/var` vs `/private/var`）単体では偽陰性を起こさない構造。t6 が同一結論に独立到達済み（該当4テストは全て pass）。 |
| 2 | `os.path.realpath` 未適用 | `_is_own_repo_root()` L1235-1239、`_origin_matches()` L1241-1244、`StateGit._is_managed()` L1608-1613、`daemon_lock_key()` L4770-4773 | **成立しない**。自己修復ロジックの経路比較は全て両辺 `os.path.realpath()` で正規化してから比較している。t6 が symlink 最小再現＋実測（`GitDistributedTests` 22件 pass）で既に確認済み。 |
| 3 | ブランチ名ハードコード | `GitBus.__init__`/`StateGit.__init__` の `branch: str = "main"` はコンストラクタ既定値のみ。実際の `_setup_worktree` は常に `self.branch`（呼び出し元指定）を使い、`init.defaultBranch` を参照しない | **成立しない**。t8 が確認済み：macOS の system gitconfig（Xcode CLT 由来）は `init.defaultBranch=main` を強制するが、ブランチ名を後段で使うテストは `git symbolic-ref HEAD refs/heads/main` で明示固定しており依存しない。 |
| 4 | 環境変数非隔離 | `GitBus._git_env()` L1121-1132 / `StateGit._env()` L1518-1526：`env = dict(os.environ)` で **アンビエント環境をコピーするだけで消去しない**。追加設定するのは `GIT_CEILING_DIRECTORIES` / `GIT_DISCOVERY_ACROSS_FILESYSTEM` / `LC_ALL`（/`GIT_EDITOR`）のみで、`GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE` / `GIT_SSH_COMMAND` 等は素通し。加えて `GitBus._clone_once()` L1268-1276 と `StateGit._ensure_clone()` の初回 clone L1654 は **`subprocess.run` に `env=` を渡していない**（自己修復コア内で唯一 `_git_env()`/`_env()` の防御を経由しない git 呼び出し）。 | **実装上は唯一裏付けが取れた前提**。ただし t8 の指摘通り、テスト側は `os.environ` に `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0` を設定して gpgsign を無効化する目的で、この非隔離設計を意図的に利用している（バグではなく設計）。 |

## (b) 検証内容と結果

- `artifacts/t3/` を `find` で確認 → 空ディレクトリ（ファイル0件）。t3 の抽出結果は本 run に成果物として残っていないため、依頼が前提とする「t3 の抽出結果」を直接参照することはできなかった。
- 割当ワークスペース（`kiro-flow-ws-25146-najl633l/sandbox`, commit `c91b626`、t1/t4と同一コミット、`git status --short` clean）で `tools/kiro-flow/kiro-flow.py` を Read で直接確認（L1092-1431: `GitBus`、L1497-1670: `StateGit`）。ファイル変更は行っていない。
- 依存タスク t5・および同run内の t1/t4/t6/t8 の成果物を全文確認し、事実関係の整合性を取った。
  - t1: `pytest tools/kiro-project/tests tools/kiro-flow/tests -q` → 終了コード 0、900 passed / 0 failed（129.77s）。
  - t4: t1 の green を受けて個別失敗の再実行は「該当なしのため未実施」。
  - t5: `needs`/`decisions` の履歴を精査し、「4件失敗」という当初記述は実体不明で、この run 系列で反復した実際の異常は verify タイムアウト（120.0s、DR-0010で600へ引き上げ済み）の誤判定だった可能性が高いと結論。
  - t6: 前提1・2（生パス比較・realpath未適用）に相当する仮説を独立に最小再現＋実測で検証し「既に防御済み」と結論（`GitDistributedTests` 22件 pass、フルスイート900 passed）。
  - t8: 前提3（ブランチ名ハードコード）に相当する `init.defaultBranch=main`（macOS system gitconfig, Xcode CLT由来）の影響を調査し「テストコードはこの差分に耐性がある設計」と結論（フルスイート900 passed, 123.19s）。
- 本タスクで新たに確認したのは前提4（環境変数非隔離）の実装箇所（`_git_env`/`_env` の非消去設計、および `_clone_once`/初回clone の `env=` 省略）であり、t6・t8はこの観点を直接検証していない。

## (c) 4件の失敗への対応付け（依頼された形式）

| 前提 | 説明できる失敗 |
|---|---|
| 1. 生パス文字列比較 | **該当なし** — 4件の失敗自体が実在しない（t1/t4: 900 passed / 0 failed, exit 0）。加えて該当コードはrealpathへの安全なフォールバックを持つ（t6で実測確認済み）。 |
| 2. `os.path.realpath` 未適用 | **該当なし** — 前提そのものがこのコードベースでは成立しない（t6で実測確認済み）。 |
| 3. ブランチ名ハードコード | **該当なし** — 前提そのものがこのコードベースでは成立しない（t8で実測確認済み）。 |
| 4. 環境変数非隔離 | **該当なし** — 実装上の裏付け（非消去 `env = dict(os.environ)`、`_clone_once`/初回cloneでの`env=`省略）は見つかったが、これが実際の失敗を引き起こしたという証拠（失敗ログ・トレースバック）は本run系列のどこにも存在しない。 |

**「4件の失敗」自体が実在しないため、対応付けるべき対象がない。** これは t1/t4/t5/t6/t8 全てが独立に到達した結論と整合する。

## (d) 採用した前提・未解決事項・範囲外の問題

- **採用した前提**: t3成果物が存在しないため、依頼文中の「t3 の抽出結果」は「t2が特定した対象クラス（GitBus/StateGitの自己修復ロジック）に対応する実装コード本体」と読み替えて調査対象を確定した。ファイル編集は行っていない（指示通り）。
- **未解決事項**: 「macOSで失敗するgit自己修復テスト4件」の具体的なテスト名・トレースバックは、本run系列のどの成果物（t1/t2/t4/t5/t6/t8、アーカイブ、decisions/needs）にも記録がなく、本タスクでも新たに発見できなかった。t5が指摘した「verifyタイムアウト120sの誤判定」説が最も整合的な説明のまま。
- **範囲外で見つけた問題**: `GitBus._clone_once()`（L1268-1276）と `StateGit._ensure_clone()` の初回clone（L1654）が `subprocess.run` に `env=self._git_env()`/`env=self._env()` を渡しておらず、自己修復コア内で唯一 `GIT_CEILING_DIRECTORIES`/`GIT_DISCOVERY_ACROSS_FILESYSTEM` の防御を経由しない箇所になっている。実害の証拠は無い（該当箇所は「.gitがまだ存在しない新規clone」時のみ通るため親リポジトリ誤認のリスクは実質的に低い）が、他の全git呼び出しとの一貫性という観点では設計上の抜けと言える。修正はスコープ外のため実施していない。
