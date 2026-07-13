# codd_gate_*.py 公開 API 一覧（38f99cac マージ分）

対象: `tools/kiro-project/codd_gate_{base,detect,status,routing,debt}.py`（コミット
`38f99ca` "Merge kp/kiro-project-codd-gate-171537: codd-gate 検出モジュールとテスト" で
main に取り込まれた5ファイル）。

同ディレクトリに `codd_gate_invoke.py` も存在するが、`git log --follow` で確認したところ
このファイルは別コミット（`6224bd1` `[kiro-flow] t9`）由来であり 38f99cac の対象外のため、
本一覧のスコープからは除外した（採用前提）。

依存関係: `codd_gate_status.py` が `codd_gate_detect.resolve_codd_gate` を import する以外、
5モジュールはすべて標準ライブラリのみに依存し相互に独立（各ファイルの docstring に明記）。

---

## codd_gate_base.py

差分ゲート（regression_cmd）向け base rev 解決。

| 関数 | シグネチャ | 戻り値型 | 例外 |
|---|---|---|---|
| `resolve_base_rev` | `(task_base_branch: str \| None = None, env: dict[str, str] \| None = None)` | `str` | 投げない（docstring で明記。env は plain dict 前提、I/O なしのローカル判断のみ） |

解決順位: `KIRO_BASE_REV` 環境変数 → `task_base_branch` → 定数 `FALLBACK_BASE_REV`（`"HEAD~1"`）。

モジュール定数: `FALLBACK_BASE_REV = "HEAD~1"`

---

## codd_gate_detect.py

codd-gate CLI の実在・能力検出（生の判定のみ、no-op 縮退の判断はしない）。

| 関数 | シグネチャ | 戻り値型 | 例外 |
|---|---|---|---|
| `resolve_codd_gate` | `(explicit: str \| None = None, which=shutil.which)` | `list[str] \| None` | 投げない設計（docstring に明記。ただし内部で呼ぶ `which`/`Path.exists` 自体が例外を出す可能性は呼び出し側 `codd_gate_status.detect_status` が try/except で吸収） |
| `resolve_codd_gate_bin` | `(config_bin: str \| None = None, env: dict[str, str] \| None = None, which=shutil.which)` | `str \| None` | 投げない（関数内 `try/except Exception` で全て `None` に縮退） |
| `get_version` | `(binary: list[str], run=subprocess.run, timeout: int = PROBE_TIMEOUT)` | `tuple[int, int, int] \| None` | 投げない（`OSError`/`subprocess.SubprocessError` を捕捉して `None`。timeout・非0終了・パース不能も `None`） |
| `check_repos_schema_compat` | `(repos_path: str \| Path)` | `tuple[bool, str]` | 投げない（`OSError`/`ValueError` を捕捉して `(False, 理由)`） |
| `detect_capabilities` | `(binary: list[str], run=subprocess.run, timeout: int = PROBE_TIMEOUT)` | `dict[str, bool]`（キー: `verify`, `tasks`, `debt`） | 投げない（内部の `_list_subcommands`/`_subcommand_supports_flag` がプローブ失敗を全て `False`/空集合に縮退） |
| `_list_subcommands`（非公開） | `(binary: list[str], run=subprocess.run, timeout: int = PROBE_TIMEOUT)` | `set[str]` | 投げない |
| `_subcommand_supports_flag`（非公開） | `(binary: list[str], subcommand: str, flag: str, run=subprocess.run, timeout: int = PROBE_TIMEOUT)` | `bool` | 投げない |

モジュール定数: `BINARY_NAME = "codd-gate"`, `PROBE_TIMEOUT = 5`, `_VERSION_RE`, `_SUBCOMMANDS_RE`（正規表現、非公開）

---

## codd_gate_status.py

codd-gate 検出結果の値オブジェクトと no-op 縮退。`codd_gate_detect.resolve_codd_gate` に依存。

### データクラス

`CoddGateStatus`（`@dataclass(frozen=True)`）
- フィールド: `binary: list[str] | None`, `version: tuple[int, int, int] | None = None`, `findings: list[dict] = field(default_factory=list)`
- プロパティ `usable -> bool`: `binary is not None and not findings`
- メソッド `command(self, *args: str) -> list[str] | None`: usable でなければ `None`
- プロパティ `reason -> str`: `findings[0]["title"]` またはなければ空文字列

### 関数

| 関数 | シグネチャ | 戻り値型 | 例外 |
|---|---|---|---|
| `build_status` | `(binary: list[str] \| None, version: tuple[int, int, int] \| None = None, version_known: bool = True, schema_ok: bool = True, schema_detail: str = "")` | `CoddGateStatus` | 投げない（純粋関数。docstring に明記） |
| `detect_status` | `(explicit: str \| None = None, which=shutil.which)` | `CoddGateStatus` | 投げない（`resolve_codd_gate` 呼び出しを `try/except Exception` で包み `binary = None` に縮退させてから `build_status` へ） |
| `_finding_not_found`（非公開） | `()` | `dict` | 投げない |
| `_finding_version_unknown`（非公開） | `(binary: list[str])` | `dict` | 投げない |
| `_finding_version_too_old`（非公開） | `(binary: list[str], version: tuple[int, int, int])` | `dict` | 投げない |
| `_finding_schema_incompatible`（非公開） | `(detail: str = "")` | `dict` | 投げない |

モジュール定数: `MIN_SUPPORTED_VERSION = (1, 0, 0)`

`build_status` の finding 短絡順（前段が失敗すれば後段は評価しない）:
未検出 → バージョン不明 → バージョン下限未満 → schema 不適合 → いずれも無ければ `findings=[]`（usable）。

---

## codd_gate_routing.py

repos.json パスと `--repo-dir` マッピングの引数ビルダ。標準ライブラリのみに依存。

| 関数 | シグネチャ | 戻り値型 | 例外 |
|---|---|---|---|
| `resolve_repos_arg` | `(repos_path: str \| Path, vcwd: str \| Path \| None = None)` | `str` | 投げない（`ValueError`/`OSError` を捕捉して絶対パスへフォールバック。純粋関数、存在確認なし） |
| `resolve_repo_dir_arg` | `(name: str, dir: str = DEFAULT_REPO_DIR)` | `str`（`"NAME=DIR"` 形式） | 投げない |
| `build_routing_args` | `(repos_path: str \| Path, name: str, vcwd: str \| Path \| None = None, dir: str = DEFAULT_REPO_DIR)` | `list[str]`（`["--repos", ..., "--repo-dir", ...]`） | 投げない（上記2関数の合成のみ） |

モジュール定数: `DEFAULT_REPO_DIR = "."`

---

## codd_gate_debt.py

`codd-gate tasks`/`--debt` 出力のパースとドリフト項目の正規化。標準ライブラリのみに依存。

### データクラス

`DriftItem`（`@dataclass(frozen=True)`）
- フィールド: `title: str`, `id: str | None = None`, `fields: dict = field(default_factory=dict)`
- メソッド `to_spec(self) -> dict`: `{"title": ...}` に `id`（あれば）と `fields` を merge した dict を返す

`DebtParseResult`（`@dataclass(frozen=True)`）
- フィールド: `items: list[DriftItem]`, `errors: list[str]`

### 関数

| 関数 | シグネチャ | 戻り値型 | 例外 |
|---|---|---|---|
| `parse_debt_output` | `(text: str)` | `DebtParseResult` | 投げない（`json.loads` の `ValueError` を捕捉して `errors` に格納。空/空白文字列は「0件」として正常系扱い） |
| `_normalize_record`（非公開） | `(raw: object, index: int)` | `tuple[DriftItem \| None, str \| None]` | 投げない（非 object・title 欠落を1レコード単位でエラー化し、全体を止めない） |

---

## 横断メモ（設計上の共通点）

- 全モジュールが **例外を外に投げない**（各 docstring で明示）方針で統一されている。
  「不明・不足はすべて連携しない側（no-op / False / None）に倒す」という共通方針が
  `codd_gate_detect.py` の docstring に明記され、他モジュールもこれに追随している。
- 依存注入パターン: `run=subprocess.run`, `which=shutil.which` のようにテスト容易性のため
  I/O 関数をデフォルト引数として差し込めるようにしている（`codd_gate_detect.py`,
  `codd_gate_status.py`）。
- `codd_gate_base.py` / `codd_gate_routing.py` / `codd_gate_debt.py` は kiro-project.py の
  型（Task/Charter/Config）に一切依存しない「純粋関数」設計（呼び出し側が必要な値だけを
  取り出して渡す）。
- 各モジュールの docstring に「意図的に含めないもの（同一 run の別タスクの責務）」が
  明記されており、kiro-project.py 本体への結線（regression/acceptance/enqueue の3フック、
  b1-b3/c1-c2/e1-e2 に相当）は本5ファイルのいずれにも含まれていない（未結線）。

## 検証

- `python3 -m pytest tools/kiro-project/tests -q -k codd` → `47 passed, 579 deselected, 3 subtests passed`（exit 0）
- `codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict` → `OK: 一貫性ゲート通過`（exit 0）
- 上記2コマンドを `&&` で連結した完了条件コマンドは exit 0 で成功することを確認済み。

## 前提・未解決事項・範囲外で見つけた問題

- **前提**: タスク文言の「38f99cac でマージ済みの検出モジュール」を、実際に当該コミットの
  diff に含まれる5ファイル（base/detect/status/routing/debt）に限定して解釈した。
  同ディレクトリの `codd_gate_invoke.py` は別コミット由来（`git log --follow` で確認）のため
  対象外とした。
- **範囲外の観察（修正はしていない）**: 各モジュールの docstring が明記する通り、
  kiro-project.py 本体への結線（regression_cmd/intake_cmd への自動配線、3フックへの接続）は
  この5ファイルの時点では未実装。ただし本タスク実行時点の作業ツリーには `codd_gate_invoke.py`
  および対応テストが既に存在し、完了条件のシェルコマンドは両方とも exit 0 で通過することを
  確認済み（結線タスクは本タスクの対象外のため、内容の詳細一覧化はしていない）。
- 未解決事項なし。コード変更は行っていない（調査・一覧化のみ）。
