# codd_gate_*.py API契約表（tools/kiro-project 配下）

対象: `tools/kiro-project/codd_gate_{base,detect,status,routing,debt}.py`（5モジュール）
読了範囲: 全5ファイル全文 + `tests/test_codd_gate_detect.py`（21件）+
`tests/test_codd_gate_routing.py`（8件）= 既存29テスト全件 + `tools/codd-gate/codd-gate.py --help` /
`verify --help` / `tasks --help`（実CLIとの整合確認）。

## 0. 前提・スコープ注記

- タスク文中の起点コミット `38f99cac` はこの worktree の `git log --all` / `git cat-file -t` で
  解決できなかった（存在しないハッシュ）。実装は `run-20260712-213419-5922` の一連のタスクコミット
  （a1/a2/a4/b1/b2、`git log --oneline --follow` で追跡可能）で段階的に導入されたものと判断し、
  **現在の作業ブランチ HEAD の内容**を「マージ済みの codd_gate_*.py」として扱った。
- `python3 -m pytest tools/kiro-project/tests -q -k codd` は現時点で **29 passed**（0 failed）。
  内訳は本表の「テスト網羅」列に対応するファイル・クラス単位のカウントと一致する。
- `codd-gate` バイナリは `/Users/nitto/.local/bin/codd-gate`（version 1.0.0、
  `MIN_SUPPORTED_VERSION=(1,0,0)` を満たす）としてこの環境の PATH 上に実在する。
  `verify`/`tasks` サブコマンドと両方の `--debt` フラグを実際に持つことを `--help` で確認済み
  （`detect_capabilities` が判定する3能力すべて `True` になる環境）。

## 1. モジュール一覧と責務境界（既存 vs 差分の第一分類）

| モジュール | 責務 | 29テストの対象か | kiro-project.py への結線 |
|---|---|---|---|
| `codd_gate_detect.py` | codd-gate 実在解決・生の能力/バージョン判定 | ○ (21件) | 未結線 |
| `codd_gate_status.py` | 判定結果の no-op 縮退（値オブジェクト） | ○ (5件 + 別クラスから間接2件) | 未結線 |
| `codd_gate_routing.py` | `--repos`/`--repo-dir` 引数ビルダ | ○ (8件) | 未結線 |
| `codd_gate_base.py` | `--base` の解決（`KIRO_BASE_REV` → task base → `HEAD~1`） | **× (0件・テストファイル無し)** | 未結線 |
| `codd_gate_debt.py` | `tasks --debt` stdout のパース・正規化 | **× (0件・テストファイル無し)** | 未結線 |

`grep -rn "import codd_gate" tools/kiro-project` の結果は各モジュール自身のテストファイルのみで、
`kiro-project.py` 本体からの import は0件。`cfg.regression_cmd`/`cfg.intake_cmd`
（`kiro-project.py:4607,4609`）は既存の汎用フックだが、codd-gate 用の値を自動組み立てて
注入するコードはまだ無い（現状は人がコマンド文字列を手で cfg に渡す前提）。

---

## 2. 公開API契約表

### 2.1 `codd_gate_base.py`

| 項目 | 内容 |
|---|---|
| `resolve_base_rev(task_base_branch: str \| None = None, env: dict[str, str] \| None = None) -> str` | 差分ゲート基準revを解決する純粋関数 |
| 引数 | `task_base_branch`: charter repoエントリの `base=`（例 `"main"`）。省略時 `None`。<br>`env`: 環境変数辞書。省略時 `os.environ`（plain dict前提、`os.environ` 互換オブジェクトなら可） |
| 戻り値 | 常に非空 `str`。優先順位: `env["KIRO_BASE_REV"]`（strip後非空なら採用）→ `task_base_branch`（strip後非空なら採用）→ `FALLBACK_BASE_REV`（`"HEAD~1"`定数） |
| 例外 | 投げない（I/O なし。`env.get` の呼び出しのみ） |
| 呼び出し規約 | 全引数キーワード可・両方省略可。呼び出し側が charter から `base=` 値を事前に取り出して渡す設計（charter/Task型に非依存） |
| テスト網羅 | **0件（テストファイル自体が存在しない）** |

### 2.2 `codd_gate_detect.py`

| 関数 | シグネチャ | 引数 | 戻り値 | 例外 |
|---|---|---|---|---|
| `resolve_codd_gate` | `(explicit: str \| None = None, which=shutil.which) -> list[str] \| None` | `explicit`: 明示パス（`.py`終端なら`[sys.executable, explicit]`に展開）。`which`: DI用（既定`shutil.which`） | argv prefix の `list[str]`、解決不能なら `None` | 投げない設計（内部I/Oは`which`呼び出しと`Path.exists`のみ、docstring上は無捕捉） |
| `get_version` | `(binary: list[str], run=subprocess.run, timeout: int = PROBE_TIMEOUT) -> tuple[int,int,int] \| None` | `binary`: argv prefix。`run`: DI用（既定`subprocess.run`）。`timeout`: 既定5秒 | 3要素タプル or `None`（timeout・非0終了・パース不能はすべて`None`） | `OSError`/`subprocess.SubprocessError`を内部でcatchし`None`化。呼び出し側に例外は伝播しない |
| `check_repos_schema_compat` | `(repos_path: str \| Path) -> tuple[bool, str]` | `repos_path`: repos.jsonのパス | `(True, "")` または `(False, "<理由>")` | `OSError`/`ValueError`（json.loads失敗）を内部でcatchし戻り値化。伝播しない |
| `detect_capabilities` | `(binary: list[str], run=subprocess.run, timeout: int = PROBE_TIMEOUT) -> dict[str, bool]` | 上記と同型 | `{"verify": bool, "tasks": bool, "debt": bool}`（キー3つ固定） | プローブ失敗は内部で全部`False`に縮退。伝播しない |
| `_list_subcommands` / `_subcommand_supports_flag` | 非公開（先頭`_`） | — | — | 外部から直接呼ばれない前提（テストも public API 経由のみ） |

定数: `BINARY_NAME = "codd-gate"`, `PROBE_TIMEOUT = 5`, `_VERSION_RE`, `_SUBCOMMANDS_RE`（正規表現、非公開）。

呼び出し規約（テストが固定化している契約）:
- `which=`/`run=` は依存性注入の**キーワード引数**として29テスト中12件がフェイクを差し込む
  （`_fake_run`/`_raising_run`ヘルパー、`kiro-project.py`の`doctor_env_findings(cfg, which=shutil.which)`
  と同じDIパターン）。位置引数化・リネームは既存テストを直接壊す。
- `get_version`/`detect_capabilities`の`run`フェイクは`subprocess.CompletedProcess`互換オブジェクト
  （`.returncode`/`.stdout`）を返す前提。
- `detect_capabilities`の`--help`プローブ判定は`argv[-1] == "--help" and len(argv) == 2`
  （トップレベル）と`argv[-2:] == [sub, "--help"]`（サブコマンド）の**位置に基づく分岐**を
  テストのフェイクrunが直接踏んでいる — argvの構築順序（`[*binary, "--help"]` /
  `[*binary, subcommand, "--help"]`）を変えるとテストのフェイク判定ロジックごと壊れる。
- `resolve_codd_gate(explicit, which=which)`はexplicit指定時に`which`を一切呼ばない契約
  （`test_resolve_codd_gate_explicit_overrides_which`が`which`をAssertionErrorで即死させて検証）。

テスト網羅: `TestCoddGateDetectResolution`(4) + `TestCoddGateDetectVersion`(6) +
`TestCoddGateDetectCapabilitiesAndSchema`(6) = **16件**（同ファイル内の
`TestCoddGateStatusNoOpDegradation`5件は`codd_gate_status`経由でこのモジュールも間接的に運動させる）。

### 2.3 `codd_gate_status.py`

| 項目 | 内容 |
|---|---|
| `CoddGateStatus`（`@dataclass(frozen=True)`） | フィールド: `binary: list[str] \| None`, `version: tuple[int,int,int] \| None = None`, `findings: list[dict] = field(default_factory=list)` |
| `.usable`（property） | `bool`。`binary is not None and not findings` |
| `.command(*args: str) -> list[str] \| None` | `usable`なら`[*binary, *args]`、そうでなければ`None` |
| `.reason`（property） | `str`。`findings[0]["title"]`、findings空なら`""` |
| `build_status(binary, version=None, version_known=True, schema_ok=True, schema_detail="") -> CoddGateStatus` | 4引数目まで省略可能なキーワード引数。短絡順（実在→バージョン既知→バージョン下限→schema）で最初の失敗だけを`findings`に1件積む。例外を投げない純粋関数 |
| `detect_status(explicit=None, which=shutil.which) -> CoddGateStatus` | `resolve_codd_gate`を内部で呼び出す統合エントリポイント。`resolve_codd_gate`が例外を出しても`try/except Exception`で捕捉し`binary=None`へ縮退。**バージョン/schema判定はまだ合流していない**（`version_known=True, schema_ok=True`固定で`build_status`へ渡す＝実在確認だけで`usable=True`になる暫定実装、docstringが明記） |
| 定数 | `MIN_SUPPORTED_VERSION = (1, 0, 0)` |
| 非公開finding生成 | `_finding_not_found`/`_finding_version_unknown`/`_finding_version_too_old`/`_finding_schema_incompatible`（各`dict`を返す。キー: `category`/`severity`/`title`/`evidence`/`fix`） |

呼び出し規約: `findings`の各`dict`は`severity`が`"info"|"warn"|"critical"`のいずれか（テストが
`findings[0]["severity"]`で検証）。`reason`は日本語文字列の部分一致でテストされる
（`assertIn("見つからない", result.reason)`等）— finding文言を変えると既存テストが壊れる。

テスト網羅: `TestCoddGateStatusNoOpDegradation`(5件) + `TestCoddGateDetectCapabilitiesAndSchema`内の
間接呼び出し。`test_codd_gate_routing.py::test_composes_with_codd_gate_status_command`も
`build_status`→`.command()`を経由（**合計で実質6-7件が直接運動**）。

### 2.4 `codd_gate_routing.py`

| 関数 | シグネチャ | 契約 | 例外 |
|---|---|---|---|
| `resolve_repos_arg` | `(repos_path: str \| Path, vcwd: str \| Path \| None = None) -> str` | `vcwd=None`なら`str(repos_path)`をそのまま返す。`vcwd`配下なら`"./<relative>"`（POSIXスラッシュ、`Path.as_posix()`）。配下外なら絶対パス文字列へフォールバック | `ValueError`/`OSError`（`relative_to`失敗）を内部catchし絶対パスへ縮退。伝播しない。存在確認はしない（純粋関数） |
| `resolve_repo_dir_arg` | `(name: str, dir: str = DEFAULT_REPO_DIR) -> str` | `f"{name}={dir}"`。`DEFAULT_REPO_DIR = "."` | 投げない |
| `build_routing_args` | `(repos_path: str \| Path, name: str, vcwd: str \| Path \| None = None, dir: str = DEFAULT_REPO_DIR) -> list[str]` | `["--repos", resolve_repos_arg(...), "--repo-dir", resolve_repo_dir_arg(...)]`（順序固定・4要素固定） | 上記2関数と同じ縮退 |

呼び出し規約: `build_routing_args`の戻り値は`status.command("verify", *build_routing_args(...), "--base", ..., "--strict")`
の形で`CoddGateStatus.command()`へ**そのまま展開**される合成契約
（`test_composes_with_codd_gate_status_command`が実引数列
`["codd-gate", "verify", "--repos", ..., "--repo-dir", ..., "--base", "HEAD~1", "--strict"]`
まで固定検証）。引数順序（`--repos`→`--repo-dir`）を変えると完了条件の期待コマンド形とも
食い違う。

テスト網羅: `TestResolveReposArg`(4) + `TestResolveRepoDirArg`(2) + `TestBuildRoutingArgs`(2) = **8件**。

### 2.5 `codd_gate_debt.py`

| 項目 | 内容 |
|---|---|
| `DriftItem`（`@dataclass(frozen=True)`） | フィールド: `title: str`, `id: str \| None = None`, `fields: dict = field(default_factory=dict)` |
| `.to_spec() -> dict` | `{"title": ...}`に`id`（あれば）と`fields`の中身をマージした dict。`enqueue_task(cfg, spec)`/`run_intake`がそのまま受け取れる形 |
| `DebtParseResult`（`@dataclass(frozen=True)`） | フィールド: `items: list[DriftItem]`, `errors: list[str]` |
| `parse_debt_output(text: str) -> DebtParseResult` | `codd-gate tasks --debt`のstdoutテキストを受け取り正規化。空/空白のみ→`DebtParseResult([], [])`。トップレベルobject/arrayどちらも吸収（`data if isinstance(data, list) else [data]`）。1レコード不備でも全体を捨てず該当レコードだけ`errors`へ | `json.loads`失敗（`ValueError`）を内部catchし`errors=[f"JSON として解釈できない: {exc}"]`へ縮退。伝播しない |
| `_normalize_record`（非公開） | `(raw: object, index: int) -> tuple[DriftItem \| None, str \| None]` | `title`必須（空/欠落は`(None, "<理由>")`）。`id`は`None`/`""`なら`None`化。`fields`は`title`/`id`以外の全キー | — |

呼び出し規約: `errors`の各要素は`f"[{index}] ..."`形式（0始まりインデックス埋め込み文字列）。
呼び出し側がjournal等へそのまま流せる前提の**文字列フォーマット契約**（構造化オブジェクトではない）。

テスト網羅: **0件（テストファイル自体が存在しない）**。

---

## 3. 既存29テストの合計内訳（検算）

| ファイル | クラス | 件数 |
|---|---|---|
| `test_codd_gate_detect.py` | `TestCoddGateDetectResolution` | 4 |
| | `TestCoddGateDetectVersion` | 6 |
| | `TestCoddGateStatusNoOpDegradation` | 5 |
| | `TestCoddGateDetectCapabilitiesAndSchema` | 6 |
| `test_codd_gate_routing.py` | `TestResolveReposArg` | 4 |
| | `TestResolveRepoDirArg` | 2 |
| | `TestBuildRoutingArgs` | 2 |
| **合計** | | **29** |

モジュール解決の呼び出し規約: 両テストファイルとも`sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`
でスクリプト冒頭に`tools/kiro-project/`を追加してから`import codd_gate_detect`のようにフラットimportする
（`tools/kiro-project/`に`__init__.py`は無く、パッケージではない）。新規テスト（`codd_gate_base`/`codd_gate_debt`用）を
追加する際もこの規約に合わせる必要がある。

---

## 4. 既存の検出ロジック（実装済み・変更不要）

- codd-gate実在解決の3段連鎖（explicit → PATH → 同梱パス）: `resolve_codd_gate`
- バージョン取得と`--help`ベースの能力プローブ（`verify`/`tasks`サブコマンド + 両方の`--debt`フラグ）:
  `get_version`, `detect_capabilities` — 実CLI（`codd-gate 1.0.0`）の`--help`出力と整合確認済み
- repos.json出力契約の構造チェック: `check_repos_schema_compat`
- 判定結果のno-op縮退（値オブジェクト + finding化 + `usable`/`command()`/`reason`）: `CoddGateStatus`, `build_status`
- `--base`解決の優先順位ロジック: `resolve_base_rev`（テスト無しだが実装は完結）
- `--repos`/`--repo-dir`引数ビルダ（self-hosted相対パス / 非self-hosted絶対パスの分岐含む）:
  `resolve_repos_arg`, `resolve_repo_dir_arg`, `build_routing_args`
- `tasks --debt`出力のレコード単位パース・正規化: `parse_debt_output`（テスト無しだが実装は完結）

## 5. これから追加すべき差分（未実装・未結線）

1. **kiro-project.py本体への結線が丸ごと未着手**
   `grep -rn "import codd_gate" tools/kiro-project`は各モジュール自身のテストファイル以外ヒット0件。
   `Config`（`kiro-project.py:4607`付近）に`codd_gate`相当のフィールドは無く、
   `cfg.regression_cmd`/`cfg.intake_cmd`へcodd-gateコマンドを自動組み立てて注入する処理も無い
   （各モジュールのdocstringが「b1-b3/c1-c2/e1-e2」として名指しで積み残している範囲と一致）。
   - regression フック配線（差分ゲート `verify --base ... --strict`をregression_cmdへ）
   - acceptance判定への結線（受入判定側の未着手範囲、c1-c2）
   - `intake_cmd`/`run_intake`から`parse_debt_output`を呼んで負債をタスク化する結線（e1-e2）
2. **`CoddGateStatus`のプロセス内キャッシュ**（各docstringが"a3"として言及、`detect_status`は
   呼ぶたびに`resolve_codd_gate`を再実行する。セッション粒度で1回計算してキャッシュする層が無い）
3. **`detect_status`はバージョン/schema実測が未合流**（`version_known=True, schema_ok=True`固定。
   `get_version`/`check_repos_schema_compat`の実測結果を`build_status`へ渡す統合コードが無い —
   現状`detect_status`は「実在するかどうか」しか見ていない暫定実装）
4. **`codd_gate_base.py`・`codd_gate_debt.py`のユニットテストが0件**（実装は完結しているが、
   `test_codd_gate_base.py`/`test_codd_gate_debt.py`が存在しない。完了条件の
   `pytest ... -k codd`件数を29から増やす場合はここが対象）
5. **`tasks`/`--debt`出力のper-record検証は`codd_gate_debt.py`止まり**で、`run_intake`
   （`kiro-project.py:503`）側から`DriftItem.to_spec()`を経由して`enqueue_task`へ渡す配線が無い
6. 完了条件のシェルコマンド自体（`codd-gate verify --repos ... --repo-dir sandbox=. --base ... --strict`）
   はこの環境で**手動実行すれば成功する状態**（CLI実在・v1.0.0・repos.json形式OK）だが、
   これを**kiro-project実行時に自動発火させる経路**（上記1）がまだ無い、というのが差分の本質。

## 6. 未解決事項・範囲外で見つけた問題

- 起点コミット`38f99cac`が本worktreeで解決できない点は上記0節の通り前提を明記して読み替えた。
  正しいハッシュが別リポジトリ（例: kiro-flow状態リポジトリ側）を指している可能性があるが、
  本タスクの範囲外のため深追いしていない。
- `codd_gate_base.py`/`codd_gate_debt.py`にテストが無いことは実装バグではなく、各モジュールの
  docstringが自ら「別タスクの責務」と明記している既知の未着手範囲（本表5節2項/4項と同一）。
  修正はこのタスクの範囲外（後続タスクの担当と判断）。
