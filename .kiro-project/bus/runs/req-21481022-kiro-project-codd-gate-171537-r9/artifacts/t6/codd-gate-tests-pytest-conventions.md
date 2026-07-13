# t6: tools/kiro-project/tests の pytest 構成・`-k codd` 命名規約・fixture/モック方針の調査

対象: `tools/kiro-project/tests/`（`test_codd_gate_detect.py` / `test_codd_gate_invoke.py` /
`test_codd_gate_routing.py` / `test_kiro_project.py` の4ファイル、計626テスト）。
コード変更は行っていない（調査のみ）。

## (a) pytest 構成

- `pytest.ini` / `pyproject.toml` / `setup.cfg` / `tox.ini` / `conftest.py` は
  `tools/kiro-project/` 配下に一切存在しない（リポジトリ全体を深さ4まで探索して確認。
  `.github/skills/*/tests/conftest.py` が2件あるが無関係なスキルのテストで本件と独立）。
  → pytest は**完全にデフォルト設定**で動作している（デフォルトの収集パターン
  `test_*.py` / `Test*` クラス / `test_*` メソッド、`addopts` なし、独自マーカーなし）。
- リポジトリルートに `.pytest_cache/` があり、過去に `python3 -m pytest` がリポジトリルートを
  rootdir として実行された形跡がある。
- 各テストファイルの docstring は実行方法として
  `python -m unittest discover -s tools/kiro-project/tests` を明記しており、pytest は
  「unittest 互換ランナーとして流用されている」だけで、pytest 固有機能（fixture・
  parametrize・マーカー）は使われていない（下記 (c) で実測）。
- `tests/` 配下に `conftest.py` が無いため、共有 fixture・sys.path のブートストラップ用フックは
  存在せず、各テストファイルが個別に
  `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` を実行して
  `tools/kiro-project/` をインポートパスに追加している
  （`tools/kiro-project/` はフラットなモジュール群で `__init__.py` を持たない正式パッケージでは
  ないため）。

## (b) `-k codd` で収集されるテスト命名規約

`--collect-only` で実測: `47/626 tests collected (579 deselected)`。

**核心の発見: `-k codd` はモジュール（ファイル名）レベルでもマッチする。**
pytest の `-k` はテストの完全ノードID全体（モジュールパス含む）に対する部分文字列マッチであり、
クラス名・メソッド名自体に "codd" が含まれている必要はない。これはファイル名だけで
`-k codd` にヒットする実例で裏付けられる:

- `test_codd_gate_routing.py` のテストクラス `TestResolveReposArg` / `TestResolveRepoDirArg` /
  `TestBuildRoutingArgs`（計8メソッド）は、クラス名・メソッド名のどこにも "codd" という
  文字列を含まない。それでも全8件が `-k codd` で収集される。理由はファイル名
  `test_codd_gate_routing.py` 自体に "codd" が含まれるため。
- 一方 `test_codd_gate_detect.py` / `test_codd_gate_invoke.py` はクラス名にも
  "Codd" を含めている（例: `TestCoddGateStatusNoOpDegradation`,
  `TestInvokeCoddGateSkipsWhenUnusable`）。`-k codd` の収集自体にはファイル名で十分なので、
  この命名は可読性・自己文書化のための冗長な補強であり、フィルタ成立の必須条件ではない。

**今後 codd-gate 関連テストを追加する際の規約**: ファイルを
`test_codd_gate_<トピック>.py`（例: 未着手の `test_codd_gate_status.py` /
`test_codd_gate_base.py` / `test_codd_gate_debt.py`、t3 が指摘した gap）と命名すれば、
クラス・メソッド名の付け方に関わらず自動的に `-k codd` に合流する。

**留意点**: 逆に、ファイル名が `test_codd_gate_*` でなくても、クラス名やメソッド名に
"codd" という文字列が偶然含まれれば同様に `-k codd` にヒットする（`-k` はノードIDの
どのセグメントに対しても部分一致するOR条件であり、「ファイル名がcoddで始まる」という
命名規約を強制する仕組みではない）。現行スイートではこのケースは発生していない
（`test_kiro_project.py` 内に "codd" を含む class/def 定義は0件、grep で確認済み）。

## (c) 既存 fixture・モック方針

- **pytest 固有 API は不使用**: `@pytest.fixture` の出現数は4ファイル合計で0、
  `import pytest` も0。スイート全体が標準ライブラリ `unittest.TestCase` ベースで書かれており、
  pytest はそれを実行できる（より便利な）ランナーとして使われているに過ぎない。
- **2つの流儀が並存**している:
  - `test_kiro_project.py`（既存の本体テスト、8547行）: `setUp`（12箇所）で
    テスト間共有の前提状態（一時 Config・作業ディレクトリ等）を組み立て、
    `mock.patch`（83箇所）で内部関数をパッチする、という従来型の unittest スタイル。
  - `test_codd_gate_detect.py` / `test_codd_gate_invoke.py` / `test_codd_gate_routing.py`
    （今回の codd-gate 機能に付随する新規3ファイル）: **`setUp` を一切使わない**
    （3ファイルとも0箇所）。各テストが必要な入力をその場で組み立てる。
    モック方針は `mock.patch` よりも**依存性注入（DI）を優先**する:
    - モジュールレベルの素朴なフェイク関数 `_fake_run(returncode=0, stdout="", stderr="")`
      （呼び出し引数を `.calls` に記録して返す）と `_raising_run(exc)` を
      `run=` キーワード引数として渡す。本体側 `kiro-project.py` の
      `doctor_env_findings(cfg, which=shutil.which)` と同じ DI パターンに合わせている、と
      docstring で明記されている。
    - 同様に `which=` にも呼び出し可能オブジェクトを注入して PATH 探索を制御する。
    - `mock.Mock(side_effect=AssertionError("..."))` を「呼ばれてはいけない」ことを検証する
      スパイとして使う（短絡分岐で別経路に絶対到達しないことを、到達したら例外で落ちる形で
      アサートする）。
    - `mock.patch.object(detect.Path, "exists", return_value=False)` — 3ファイル中で唯一の
      実際の `unittest.mock.patch` 使用（`test_codd_gate_detect.py` に3箇所）。DI 引数が
      届かない一箇所（同梱パスの実在チェック）を強制的に失敗させるためだけに使われている。
    - `tempfile.TemporaryDirectory()`（detect/routing 合計9箇所）— Path/os をモックする代わりに、
      検証対象自体がファイルパス操作であるテスト（repos.json のスキーマ検証、vcwd 相対パス
      解決）では実ファイルを使う。
    - `self.subTest`（`test_codd_gate_invoke.py` に1箇所）で3種類の例外型をパラメタ化。
      pytest の `@pytest.mark.parametrize` は使っていない。
- **共有 fixture は存在しない**: `conftest.py` が無いため、`_fake_run`/`_raising_run` は
  `test_codd_gate_detect.py` と `test_codd_gate_invoke.py` にそれぞれ**そのまま重複定義**
  されている（抽出・共通化はされていない）。新規テストファイルを追加する場合も、
  現状の慣習に従うなら同様に自前でこれらのヘルパーを用意することになる。

## 検証内容と結果

- `find . -maxdepth 4 \( -iname pytest.ini -o -iname pyproject.toml -o -iname setup.cfg -o -iname tox.ini \)`
  → `tools/kiro-project/` 配下・リポジトリ全体ともに該当なし（pytest 独自設定は不在）を確認。
- `find . -iname conftest.py` → `tools/kiro-project/` 配下は0件（無関係な2スキルの
  `tests/conftest.py` のみ他所に存在）。
- `python3 -m pytest tools/kiro-project/tests -q -k codd --collect-only` →
  `47/626 tests collected (579 deselected)`。47件全ての完全ノードIDを列挙し、
  どのファイル・クラス・メソッドが該当するかを実測で確認（(b) の根拠）。
- grep による定量確認: `@pytest.fixture`=0件、`import pytest`=0件、
  `unittest.TestCase`（detect5/invoke3/routing3/kiro_project79）、
  `setUp`（kiro_project12、他3ファイルは0）、
  `mock.patch`（detect3/invoke0/routing0/kiro_project83）、
  `tempfile`（detect4/routing5/invoke0）、`self.subTest`（invoke1、他0）。
- 完了条件のシェルコマンドをそのまま実行し、exit code 0 を確認（コード変更なし）:
  ```
  python3 -m pytest tools/kiro-project/tests -q -k codd
  → 47 passed, 579 deselected, 3 subtests passed in 0.05s

  codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base HEAD~1 --strict
  → 差分: sandbox HEAD~1..作業ツリー（2 ファイル）
    [GREEN] tools/kiro-project/codd_gate_invoke.py（接続 1 本・整合）
    [GREEN] tools/kiro-project/tests/test_codd_gate_invoke.py（参照は全て解決）
    OK: 一貫性ゲート通過
  ```
  両段とも成功し、連結コマンド全体の exit code は 0。

## 採用した前提・未解決事項・範囲外で見つけた問題

- 前提: タスク文中の「pytest 構成」を「pytest 固有の設定ファイル・収集動作の実態」、
  「命名規約」を「`-k codd` というフィルタ条件に対してテストがどう命名されていれば
  拾われるか」の実測的性質、と解釈した。
- 範囲外で見つけた問題（本タスクでは修正しない。t3 の指摘と重複するが本調査でも独立に確認）:
  - `codd_gate_status.py` / `codd_gate_base.py` / `codd_gate_debt.py` に対応する単体テストが
    まだ無い。追加する際は (b) のファイル命名規約（`test_codd_gate_*.py`）と (c) の DI 方針
    （`run=`/`which=` のような注入引数を持つ純粋関数として書き、`mock.patch` は
    DI が届かない箇所に限定する）を踏襲すれば既存スイートと一貫し、`-k codd` にも
    自然に合流する。
  - `_fake_run`/`_raising_run` ヘルパーが2ファイルに重複定義されており、`conftest.py` による
    共通化がされていない。テストファイルが今後増える場合、抽出の要否は別タスクの判断。
- 未解決事項: なし。本タスクの完了条件（pytest -k codd の成功 + codd-gate verify --strict の
  成功）は、調査時点で既に両方とも satisfied（先行タスクの実装成果による）。本タスク自体は
  コード変更を伴わない調査のみで完了した。
