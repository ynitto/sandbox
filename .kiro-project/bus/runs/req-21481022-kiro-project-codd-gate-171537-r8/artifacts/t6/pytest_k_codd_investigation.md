# t6: pytest -k codd 収集調査

## (a) 成果 / サマリー

### 現状構成（`tools/kiro-project/tests/`）
- `conftest.py` は存在しない（リポジトリ全体でも `tools/kiro-project` 配下には無し。他スキル配下 `.github/skills/table-spec-extractor/tests/conftest.py` 等は無関係）。
- `pytest.ini` / `pyproject.toml` の `[tool.pytest.ini_options]` / `setup.cfg` / `tox.ini` はリポジトリのどこにも無い。→ pytest 設定はデフォルト値のまま（rootdir はテスト実行時の共通祖先ディレクトリから自動決定、import-mode はデフォルトの `prepend`）。
- `__init__.py` は `tools/kiro-project/` にも `tools/kiro-project/tests/` にも無い。各テストファイルが自前で
  `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`
  を行い、`tools/kiro-project/` 直下の `codd_gate_detect.py` / `codd_gate_status.py` / `codd_gate_routing.py` / `kiro-project.py` を import している。conftest.py 相当の役割をこの1行が各ファイルで肩代わりしている。
- 対象ファイルは3つ:
  - `test_kiro_project.py`（579 tests、ファイル名・クラス名・関数名のいずれにも `codd` を含まない。本文中に「codd-gate」という語がコメント/docstringに2箇所出るのみ＝ノードIDには現れない）
  - `test_codd_gate_detect.py`（29 tests、ファイル名自体に `codd` を含む）
  - `test_codd_gate_routing.py`（同上、ファイル名に `codd` を含む）

### 確定した命名規約
`pytest -k codd` はノードID（モジュールパス::クラス名::関数名）の**いずれかの構成要素の部分文字列一致**で選択される。したがって **codd を含めるべき最小単位はファイル名** であり、`test_codd_gate_*.py` のように **ファイル名に `codd` を含めれば、クラス名・関数名に `codd` が一切無くてもそのファイル内の全テストが `-k codd` で選択される**。
現行の2ファイル（`test_codd_gate_detect.py` / `test_codd_gate_routing.py`）は既にこの規約に沿っており、追加の関数名/クラス名変更は不要。
既存の `test_kiro_project.py` は対象外のままでよい（意図的に codd-gate 以外の既存機能テストであり、`-k codd` から除外されるのが正しい挙動）。

**今後 codd-gate 関連のテストを追加する際の規約**:
1. ファイル名を `test_codd_gate_<topic>.py` とする（最重要・これだけで `-k codd` に収集される）
2. 追加で可読性のためクラス名にも `TestCoddGate...` と `Codd` を含める（大文字小文字は pytest の `-k` では区別されない＝`Codd`/`codd`どちらでも一致する。実測: `TestCoddGateDetectResolution` は `-k codd` で一致した）
3. 関数名は `codd` を含めなくても収集可否には影響しない（ファイル名一致で足りる）

## (b) 検証内容と結果

```
$ python3 -m pytest tools/kiro-project/tests -q -k codd
.............................                                            [100%]
29 passed, 579 deselected in 0.21s
$ echo $?
0
```
→ 完了条件の pytest 部分は**現状のコードで既に満たされている**（コード変更なし、調査のみ）。

参考として、バックログ全体の完了条件コマンド（`codd-gate verify` 込み）もこの worktree でそのまま実行し、exit=0（一貫性ゲート通過）を確認した。`codd-gate` バイナリは `/Users/nitto/.local/bin/codd-gate` に存在し、`.kiro-project/repos.json` も配置済みだった。この部分の検証は本タスクの担当範囲外だが、他タスク（t1/b2 等）による先行修正で既に全体ゲートが通る状態になっていることの参考情報として付記する。
```
$ python3 -m pytest tools/kiro-project/tests -q -k codd && codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict
29 passed, 579 deselected in 0.05s
差分: sandbox HEAD~1..作業ツリー（2 ファイル）
  [GREEN] tools/kiro-project/codd_gate_routing.py（接続 1 本・整合）
  [GREEN] tools/kiro-project/tests/test_codd_gate_routing.py（参照は全て解決）
OK: 一貫性ゲート通過
$ echo $?
0
```

past-failure（exit=5）の再現実験:
```
$ python3 -m pytest tools/kiro-project/tests/test_kiro_project.py -q -k codd
579 deselected in 0.04s
$ echo $?
5
```
→ `test_codd_gate_detect.py` が存在しなかった時点（commit `895328a` の親 = `895328a^`）の状態を `test_kiro_project.py` 単体で再現すると、`-k codd` に一致するノードIDが1件も無く `579 deselected` の上で **pytest 標準の「収集ゼロ」exit=5** が発生することを確認した。

## (c) 採用した前提・原因特定・未解決事項

### 過去の exit=5 の原因（確定）
`backlog/kiro-project-codd-gate-171537.md` の `needs_reason: 繰り返し NG（retries=8）: exit=5 585 deselected in 0.20s` に対応する事象。
- git 履歴上、commit `895328a`（t1, run-20260712-213419-5922）より前は `tools/kiro-project/tests/` に `test_kiro_project.py` のみが存在し、このファイルは **ファイル名・クラス名・関数名のどこにも `codd` という部分文字列を含んでいなかった**（`codd-gate` という語はコメント/docstring内に2箇所あるのみで、pytest の `-k` 照合対象であるノードIDには反映されない）。
- そのため `pytest -q -k codd` は全テストを deselect し、選択数0 → pytest の仕様上 exit code **5**（"no tests ran"／収集された対象ゼロ）で終了していた。
- 件数の差異（当時 `585 deselected`、本調査での再現は `579 deselected`）は、`895328a^` 以降 `test_kiro_project.py` 自体にテストの追加・削除があったための単純な母数差であり、原因の同一性には影響しない（同ファイル単体で `-k codd` を掛ければ常に0件選択＝exit=5になる構造は変わらない）。
- `895328a`（t1）で `test_codd_gate_detect.py` が追加され、ファイル名に `codd` が入ったことで初めて `-k codd` が非ゼロ件数を選択するようになり、exit=5 が解消された。`6e21135`（b2）で追加された `test_codd_gate_routing.py` も同じ命名規約に従っている。

### 前提
- 完了条件のうち `codd-gate verify ...` 部分は本タスクのスコープ外（別タスクの責務）と解釈した。参考情報として `which codd-gate` は `/Users/nitto/.local/bin/codd-gate` を検出したが、本タスクでは検証していない。
- 本タスクは「調査して命名規約を確定・原因を記録する」ことが完了条件であり、pytest 側は既に規約を満たしているためコード変更は行っていない（作業ツリーは無変更）。

### 範囲外で見つけた問題
- 無し。
