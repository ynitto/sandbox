# macOS 固有要因調査①: /var/folders vs /private/var/folders のパス比較影響

## (a) 成果 / サマリー

**結論: 症状の仮説（tmp_path/tempfile の `/var/folders/...` と realpath 解決後の
`/private/var/folders/...` の差がパス比較を壊している）は、このコードベースでは成立しない。**
差分自体は macOS 上で実在するが、該当しうる比較箇所（git self-heal 系）はすべて
両辺を `os.path.realpath()` で正規化してから比較しており、既に防御済みだった。

- symlink 解決差そのものは実在する（最小再現で確認済み、下記(b)）。
- 「git 自己修復」に該当する4テスト候補（`GitDistributedTests` 内の
  `test_managed_bus_clone_is_reused` / `test_stale_index_lock_recovered_on_reuse` /
  `test_corrupt_index_clone_is_rebuilt` / `test_interrupted_rebase_recovered_on_reuse`）は
  いずれも `kf.GitBus.__init__` → `_is_own_repo_root()` 等の内部比較を経由するが、
  この関数は `os.path.realpath(top) == os.path.realpath(self.workdir)` という形で
  両辺を realpath 正規化してから比較しており、symlink 差の影響を受けない設計になっている。
- 実測でもこの4テストを含む `GitDistributedTests` 22件は全て pass、完了条件の
  フルスイートも 900 passed / 0 failed（exit 0, 122.20s）。

## (b) 検証内容と結果

### 1. symlink 解決差そのものの実在確認（最小再現）

```python
import tempfile, os, pathlib
d = tempfile.mkdtemp(prefix='repro-')
print('mkdtemp path      :', d)
print('realpath(path)    :', os.path.realpath(d))
print('equal mkdtemp==realpath ?', d == os.path.realpath(d))
```

実行結果（このmacOS環境）:
```
mkdtemp path      : /var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/repro-kdz01x9l
realpath(path)    : /private/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/repro-kdz01x9l
equal mkdtemp==realpath ? False
```
→ **差分は実在する。** `/var` 自体が macOS では `/private/var` への symlink であるため。

### 2. 該当コードパターンでの再現（tempfile.mkdtemp → git rev-parse --show-toplevel 比較）

`GitBus`/`StateGitBus` の自己修復判定と同型の処理（`os.path.abspath` で作った作業ディレクトリを
`git -C <dir> rev-parse --show-toplevel` の出力と比較）を最小再現:

```python
bus_root = os.path.abspath(tempfile.mkdtemp(prefix='kf-bus-'))
clone = os.path.join(bus_root, '.state-git')
os.makedirs(clone); subprocess.run(['git', '-C', clone, 'init', '-q'])
top = subprocess.run(['git','-C',clone,'rev-parse','--show-toplevel'],
                      capture_output=True, text=True).stdout.strip()
```

実行結果:
```
bus_root(abspath) : /var/folders/.../kf-bus-bpg4dupv
clone (abspath join): /var/folders/.../kf-bus-bpg4dupv/.state-git
git show-toplevel  : /private/var/folders/.../kf-bus-bpg4dupv/.state-git

直接比較 top == clone            : False   ← 生の文字列比較なら壊れる
realpath(top) == realpath(clone) : True    ← realpath正規化後は一致
```

→ **git は内部で cwd を OS 解決済み（`/private/...`）の絶対パスとして返す**一方、
`tempfile.mkdtemp()`/`os.path.join`/`os.path.abspath` は symlink を解決しないため、
生の文字列比較（`==`）を使うコードがあれば実際に壊れる。これが仮説の core mechanism。

### 3. 実コードの該当箇所を確認（既に防御済み）

- `tools/kiro-flow/kiro-flow.py:1235-1239` `_is_own_repo_root()`:
  `top = self._git(["rev-parse","--show-toplevel"])...; return os.path.realpath(top) == os.path.realpath(self.workdir)`
- `tools/kiro-flow/kiro-flow.py:1241-1244` `_origin_matches()`: 同様に `os.path.realpath()` で両辺正規化。
- `tools/kiro-flow/kiro-flow.py:1609,1613`（`StateGitBus._is_managed()`）、
  `tools/kiro-project/kiro-project.py:5112,5116`（同名メソッド）も同型。
- `daemon_lock_key()` (`kiro-flow.py:4772-4773`, `kiro-project.py:3465-3469`) も
  `"local::" + os.path.realpath(args.bus)` で正規化してからハッシュ化しており、
  テスト `test_lock_path_canonical_and_config_dir`（symlink 経由でも同一ロックパスになることを
  明示的に検証）が既にこの防御をカバーしている。

すなわち、コードベースは symlink 差を前提にした防御的実装（`os.path.realpath` での正規化）を
既に持っており、これは意図的な設計（コメント「symlink 経由でも同一ロックパス」等）であって
偶然ではない。

### 4. 実測（このワークスペースで実行、ファイル変更なし）

```
$ python3 -m pytest tools/kiro-flow/tests/test_kiro_flow.py -q -k "GitDistributedTests"
......................                                                   [100%]
22 passed, 366 deselected in 17.40s

$ python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q
900 passed in 122.20s (0:02:02)
```
完了条件コマンドは exit code 0（900 passed / 0 failed）で成功。

## (c) 採用した前提・未解決事項・範囲外の問題

- **前提**: 依存タスク t5 の報告（t1/t4 とも 0 failed、`needs`/`decisions` の履歴上の実際の原因は
  「verify タイムアウト 120s」であり個別テスト失敗の記録は存在しない）を踏まえ、本調査は
  「4件の失敗が実在する場合の原因候補」として仮説を機械的に検証する形で実施した。
  実測フルスイート 122.20s は旧 verify タイムアウト 120s をわずかに超えており、
  t5 が指摘した「タイムアウトを失敗と誤認した」という見立てと整合する。
- **範囲**: 「該当コード相当の最小再現」は git self-heal 系（`GitBus`/`StateGitBus` の
  workdir/toplevel 比較、daemon lock key 正規化）に絞った。それ以外（例えばログファイルパスの
  文字列比較や、成果物のパス表示など）で `/var/folders` を直接文字列比較している箇所は
  今回のgrep（`realpath|samefile|/private|abspath|resolve()`）の範囲では見つからなかったが、
  全ファイル・全関数を悉皆調査したわけではない。
- **未解決事項**: 「macOSで失敗する git 自己修復テスト4件」という当初の症状記述に対応する
  実際の失敗ログ・トレースバックは、このタスクの調査対象範囲（依存関係を含む）のどこにも
  見つかっていない（t5 も同じ結論）。本タスクは「その仮説が原理的に成立し得るか」を
  検証する立場で実施し、結果は「成立しない（既に防御済み）」。
- **範囲外で見つけた問題**: なし。ファイル編集は行っていない（指示通り）。
