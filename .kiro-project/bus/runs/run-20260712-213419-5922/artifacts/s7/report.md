# s7: tools/kiro-project/tests 既存テスト構成調査

## (a) 成果サマリー

対象: `tools/kiro-project/tests/test_kiro_project.py`（単一ファイル、7778行、515テスト）。

### テスト基盤
- フレームワークは **標準ライブラリ `unittest.TestCase`**。pytest はテストランナーとして
  unittest クラスをそのまま収集しているだけで、`pytest.fixture` / `@pytest.mark.*` は
  **1件も使われていない**。`pytest.ini` / `pyproject.toml` / `setup.cfg` / `conftest.py` も
  リポジトリのどこにも存在しない（`tools/kiro-project/` 配下・リポジトリ直下とも確認済み）。
  実行方法はファイル冒頭のdocstringに明記: `python -m unittest discover -s tools/kiro-project/tests`。
- 被試験モジュール `kiro-project.py` はファイル名にハイフンを含み `import` できないため、
  `importlib.util.spec_from_file_location` で動的ロードし `km` という別名で `sys.modules` に
  登録している（test_kiro_project.py:45-49）。新規テストもこの `km` 経由で対象を呼ぶ。
- モジュールロード前に神経質な環境分離を3つ実施（test_kiro_project.py:24-43）:
  1. `GIT_CONFIG_*` 環境変数で `commit.gpgsign=false` を子プロセスに強制（署名設定によるgit操作の間欠失敗を防止）
  2. `KIRO_SKILL_REGISTRY` を存在しないパスに固定（自動アップデートの実ネットワークアクセス防止）
  3. `os.chdir(tempfile.mkdtemp(...))` でテスト全体を中立cwdに退避
     （**重要な既知事故**: リポジトリ直下で実行すると `root=.` を拾い、実リポジトリへ
     テストがコミット/pushする事故が2026-07-11に実際に発生済み、とコメントあり）

### fixture 方針（pytest fixture ではなく手製ヘルパー）
- `mkb(d, tid, status=..., verify=..., source=..., title=None, retries=0)`
  （test_kiro_project.py:52-58）: `backlog/<id>.md` を直接書き出すタスクファイル生成ヘルパー。
- `cfg_for(d, **kw)`（test_kiro_project.py:61-69）: `km.Config` を dry_run=True・
  plan_review/delivery_review=False などテスト向け既定値で組み立て、`**kw` で個別上書き。
  新規codd-gate関連テストも `cfg_for` の上書きキーワード（例: `intake_cmd`, `regression_cmd` 相当）
  で設定を注入するのが既存流儀に合う。
- 各テストメソッド内で `with tempfile.TemporaryDirectory() as d:` を都度使うのが標準パターン
  （クラス共通の `setUp`/`tearDown` でtmpdirを使う例はなく、テストごとに独立ディレクトリ）。
- `setUp` はグローバル状態のクリア用途でのみ使用。例: `TestIntake.setUp` が
  `km._INTAKE_LAST.clear()`（test_kiro_project.py:244-245、intake実行間隔スロットリング用の
  モジュールグローバルをテスト間でリセット）。

### モック方針
- `unittest.mock as mock` を使用（`pytest-mock`/`monkeypatch` フィクスチャは不使用）。
  `mock.patch.object(...)` が82箇所。
- 外部プロセス呼び出しは **`mock.patch.object(km.subprocess, "run", ...)`** で差し替えるのが
  定番パターン（test_kiro_project.py:1432等、agent CLI呼び出しテストで多用）。
  `codd-gate verify` をサブプロセス起動するコード（b3想定）のテストもこの形が踏襲されるはず。
- 内部関数の差し替えは **`mock.patch.object(km, "<関数名>", ...)`**（例:
  `state_git_for`, `check_update`, `maybe_self_update`, `ensure_cache`, `read_reject_guidance`）。
- 外部CLIの実在検出は **依存性注入された `which` 引数**で検証している点が重要な既存パターン:
  `km.doctor_env_findings(cfg, which=shutil.which)` がデフォルトで、テストは
  `which=lambda _n: None`（未検出）や `which=lambda n: None if n == "claude" else "/usr/bin/"+n`
  （特定バイナリだけ未検出）を渡して分岐を検証している（test_kiro_project.py:812-849, `TestDoctor`）。
  **s4/a1の codd-gate CLI 実在検出（`shutil.which("codd-gate")`）のテストも、この `which=` DI
  パターンを踏襲するのが最も自然**（`km.doctor_env_findings` は既に同種の実装 test_kiro_project.py:6907）。
- 恒久的モジュールグローバルの一時差し替えは `orig = km.X; km.X = fake;
  self.addCleanup(lambda: setattr(km, "X", orig))` の形（`TestFlakeTolerantVerify._patch_verify`,
  test_kiro_project.py:389-401）。`mock.patch.object` で足りない「関数を呼ぶたびに違う戻り値を
  返す stub」を書く際の慣用手順。

### pytest マーカー
- **一切使われていない。** `-m` によるテスト絞り込みの仕組みはこのプロジェクトに存在しない。
  現状のテスト選別は `-k <キーワード>`（クラス名/メソッド名の部分一致）のみが機能する経路。

### 既存 codd 関連テストの有無
- `test_kiro_project.py` 内に **`codd` を含むクラス名・メソッド名は0件**。
  `grep -i codd` でヒットするのはコメント/docstringの2箇所のみ:
  - test_kiro_project.py:241 `TestIntake` のdocstring
    （「外部の決定的ゲート/検出器（codd-gate等）から修復タスクを…」）
  - test_kiro_project.py:345 `test_charter_exports_generated_registry` のdocstring
    （「外部ツール（codd-gate --repos）へ渡す」）
- 上記2クラスは **codd-gate を名指しで検証しているわけではなく、codd-gate が将来使う
  汎用フック機構（`intake_cmd`／`repos.json` 生成）を検証している**:
  - `TestIntake`（test_kiro_project.py:240-301, 5テスト）: `run_intake_enqueues_and_dedups_by_id`,
    `run_intake_interval_throttles`, `run_intake_tolerates_failures`,
    `run_loop_intakes_and_consumes`, `watch_idle_intake_wakes_pass`。
    CHANGELOG.md:889 の「kiro-projects intake 5件」はこれを指す。
  - `TestRepoRegistry`（test_kiro_project.py:304-384, 4テスト）: `repos.json`（`--repos` 渡し用の
    レジストリファイル）の読み込み優先順位・charterからの自動生成・破損時フォールバックを検証。
- 別ディレクトリだが `tools/codd-gate/tests/test_codd_gate.py`（553行）に **codd-gate 単体の
  テストスイートが既に存在**（`ClassifyTests`, `MapTests`, `ImpactTests`, `TasksTests`,
  `DebtVerifyTests`, `CheckTests`, `ScanCliTests`, `SyncTests` の8クラス、実測30テスト全PASS）。
  ただしこれは `tools/kiro-project/tests` の管轄外であり、本タスクのスコープ外。

## (b) 検証内容と結果

1. `python3 -m pytest tools/kiro-project/tests -q -k codd`
   → **515 deselected, 0 selected, exit code 5**（"no tests ran"）。
   codd を名指しするテストが存在しないため、キーワード一致テストがゼロで不成功終了。
2. `python3 -m pytest tools/kiro-project/tests -q`（フルスイート）
   → **515 passed, exit code 0**（88秒）。既存テスト自体は健全。
3. `python3 -m pytest tools/codd-gate/tests -q`
   → **30 passed, exit code 0**。codd-gate 単体のテストスイートは独立して健全。
4. `codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base HEAD~1 --strict`
   → 未実行（下記「未解決事項」参照。`codd-gate` CLI自体は `/Users/nitto/.local/bin/codd-gate`
   にインストール済みだが、`./.kiro-project/repos.json` がこのworktreeに存在しない）。
5. `pytest.ini` / `pyproject.toml` / `setup.cfg` / `tox.ini` / `conftest.py` の探索
   → `tools/kiro-project/` 配下・リポジトリ直下とも**該当ファイルなし**（マーカー登録の設定点も無い）。

## (c) 前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- 本タスク（s7）は調査専任であり、コード変更は行っていない（作業ツリー差分なし）。
- ラッパーに付与された完了条件シェルコマンド
  （`pytest -k codd && codd-gate verify --repos ... --strict`）は、
  この run 全体（s1〜s7 → d1/d2 → a1〜e2 → t1〜t4 → doc → gate）の**最終受け入れ条件**であり、
  s7単体では意味を成さないと判断した。根拠: 同run の `graph.json` を確認したところ、
  s7 は `t1`〜`t4`（`-k codd` にヒットする実テストを追加するタスク）の依存元でしかなく、
  a1〜e2（検出モジュール本体・3経路結線の実装）は別タスクとして定義されている。
  s7の範囲で `-k codd` を通過させる実テストを書く、または `codd-gate` の呼び出しを
  kiro-project側に実装することは「範囲を守る」の逸脱になるため行っていない。
- したがって本タスクでは完了条件シェルコマンドを**達成しない**（達成には a1〜t4 の実装が必要）。
  これは仕様の誤り・手抜きではなく、fan-out-and-synthesize 戦略上の意図的な役割分担と判断した。

**未解決事項（後続タスクへの申し送り）**:
- `t1`〜`t4` が新規テストクラスを追加する際、以下の**既存流儀への追従**を推奨する:
  - `km.doctor_env_findings(cfg, which=shutil.which)` と同じ **DI可能な `which` 引数**パターンを
    codd-gate CLI検出関数にも採用すると、`TestDoctor` と同型のテストがそのまま書ける。
  - サブプロセス呼び出し（`codd-gate verify` 実行）は `mock.patch.object(km.subprocess, "run", ...)`
    でモックする（実CLIを叩かない）。
  - 新規クラスは `TestIntake`/`TestRepoRegistry` 同様、クラスdocstringに検証対象の意図を
    日本語で明記する既存慣習に合わせる。
  - クラス名またはメソッド名に **`codd` という文字列を含める必要がある**（`-k codd` で
    選別されるため）。例: `TestCoddGateDetect`, `test_codd_gate_absent_is_noop` 等。
- `codd-gate verify --repos ./.kiro-project/repos.json ...` を通すには、実行時に
  `./.kiro-project/repos.json` が存在している必要がある（b2の repo-dir マッピング組み立てと
  連動）。現worktreeには存在しない。

**範囲外で見つけた問題（直していない・報告のみ）**:
- なし（テスト構成自体に既存の欠陥・不整合は見当たらなかった）。

## 変更ファイル
なし（調査のみ、作業ツリーへの書き込みは行っていない）。
