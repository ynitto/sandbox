# codd_gate_base.py / codd_gate_detect.py 公開インターフェース一覧

対象:
- `tools/kiro-project/codd_gate_base.py`
- `tools/kiro-project/codd_gate_detect.py`

両モジュールとも `from __future__ import annotations` のみで、依存は標準ライブラリに限定（`os` /
`json` / `re` / `shutil` / `subprocess` / `sys` / `pathlib.Path`）。kiro-project.py 本体の型
（Task/Charter）には依存しない純粋関数群として設計されている。

---

## 1. `codd_gate_base.py`

差分ゲート（regression_cmd）向けの base rev 解決を担う。公開関数は1つのみ。

### 定数

| 名前 | 値 | 用途 |
|---|---|---|
| `FALLBACK_BASE_REV` | `"HEAD~1"` | base rev が他のどの手段でも得られない場合の最終フォールバック |

### 公開関数

#### `resolve_base_rev(task_base_branch=None, env=None) -> str`

- **引数**
  - `task_base_branch: str | None` — charter の repo エントリが持つ `base=`（例 `"main"`）。呼び出し側が
    `charter_repo_spec_map(ch).get(task.get("workspace"), {}).get("base")` 等で事前に取り出した文字列を渡す。
  - `env: dict[str, str] | None` — 省略時は `os.environ`。テスト時はダミー dict を注入できる。
- **戻り値**: `str`（常に非空文字列）
- **解決優先順位**（前段が空なら次段へ）
  1. `env["KIRO_BASE_REV"]`（strip 後に非空なら採用）
  2. `task_base_branch`（strip 後に非空なら採用）
  3. `FALLBACK_BASE_REV`（`"HEAD~1"`）
- **例外**: 投げない。I/O を行わずローカル判断のみ（`env` は plain dict 前提）。
- **データ構造**: 独自のデータクラス等は無し。引数・戻り値ともにプリミティブ（`str`/`dict`）。

---

## 2. `codd_gate_detect.py`

codd-gate CLI バイナリの実在解決・バージョン/能力の**生の判定値**取得を担う。「使ってよいか」の
判断（no-op 縮退・finding 化）はこのモジュールの責務外（`codd_gate_status.py` 側）。

### 定数

| 名前 | 値 | 用途 |
|---|---|---|
| `BINARY_NAME` | `"codd-gate"` | `shutil.which` に渡すバイナリ名 |
| `PROBE_TIMEOUT` | `5`（秒） | 各 `subprocess.run` の既定 timeout |
| `_VERSION_RE` | `re.compile(r"codd-gate (\d+)\.(\d+)\.(\d+)")` | `--version` 出力のパース用（非公開） |
| `_SUBCOMMANDS_RE` | `re.compile(r"\{([\w,]+)\}")` | `--help` 出力からサブコマンド集合を抜く用（非公開） |

### 公開関数

#### `resolve_codd_gate(explicit=None, which=shutil.which) -> list[str] | None`

codd-gate の起動 argv prefix（`subprocess` に渡す先頭要素群）を解決する。`resolve_kiro_flow` と対称の
解決連鎖。

- **引数**
  - `explicit: str | None` — 明示指定された実行パス（最優先）。
  - `which` — DI 用フック。既定は `shutil.which`（テストで差し替え可能）。
- **戻り値**: `list[str] | None`
  - 見つかれば起動 argv prefix（例 `["/usr/local/bin/codd-gate"]` or `[sys.executable, ".../codd-gate.py"]`）。
  - 一切見つからなければ `None`（"不明な起動コマンドを組み立てない"という設計判断）。
- **解決順**
  1. `explicit` が truthy → `.py` で終われば `[sys.executable, explicit]`、そうでなければ `[explicit]`
  2. `which(BINARY_NAME)` が見つかれば `[found]`
  3. 同梱パス `<このファイルの祖父ディレクトリ>/codd-gate/codd-gate.py` が存在すれば `[sys.executable, str(local)]`
  4. いずれも無ければ `None`
- **例外**: 明示的な try/except は無し（`which` に例外的挙動を仕込んだ場合は伝播しうる。呼び出し側の
  `detect_status`（`codd_gate_status.py`）が想定外例外を吸収する設計）。

#### `resolve_codd_gate_bin(config_bin=None, env=None, which=shutil.which) -> str | None`

`resolve_codd_gate` の `explicit` に何を渡すかを決める前段の解決連鎖（環境変数 → 設定ファイル値 → PATH）。

- **引数**
  - `config_bin: str | None` — 呼び出し側が設定ファイル（kiro-project.yaml 等）から読み取った値。
  - `env: dict[str, str] | None` — 省略時は `os.environ`。
  - `which` — DI 用フック。既定は `shutil.which`。
- **戻り値**: `str | None`
- **解決順**（各段は空文字列/None を「未設定」として次段へ）
  1. `env["CODD_GATE_BIN"]`（strip 後に非空なら採用）
  2. `config_bin`（strip 後に非空なら採用）
  3. `which(BINARY_NAME)`（見つからなければ `None`）
- **例外**: 全体を `try/except Exception` で包み、想定外の例外はすべて `None` に縮退させる
  （「見つからない」と「検出処理が壊れている」を呼び出し側で区別させない設計）。
- **合成例**（モジュール docstring より）:
  `resolve_codd_gate(resolve_codd_gate_bin(cfg.codd_gate_bin), which=shutil.which)`

#### `get_version(binary, run=subprocess.run, timeout=PROBE_TIMEOUT) -> tuple[int, int, int] | None`

`<binary> --version` を実行してバージョンタプルを得る（codd-gate 側は argparse の
`action="version"` で exit 0 直終了する経路）。

- **引数**
  - `binary: list[str]` — `resolve_codd_gate` が返す起動 argv prefix。
  - `run` — DI 用フック。既定は `subprocess.run`（テストで差し替え可能）。
  - `timeout: int` — 秒。既定 `PROBE_TIMEOUT`（5）。
- **CLI 呼び出し形**: `subprocess.run([*binary, "--version"], capture_output=True, text=True, timeout=timeout)`
- **戻り値**: `tuple[int, int, int] | None`
  - `_VERSION_RE` が `proc.stdout` にマッチすれば `(major, minor, patch)` の int タプル。
  - マッチしなければ `None`。
- **終了コードの扱い**: `proc.returncode != 0` → 無条件で `None`。
- **例外の扱い**: `OSError` / `subprocess.SubprocessError`（timeout 含む）を捕捉して `None`。
- **設計方針**: timeout・非 0 終了・パース不能はすべて「不明」（`None`）に一様化する
  （「わからない」を「大丈夫」に丸めない）。

#### `check_repos_schema_compat(repos_path: str | Path) -> tuple[bool, str]`

codd-gate CLI を**呼ばない**唯一の公開関数。`repos.json`（`export_repo_registry` の出力）が
`repos.schema.json` の最小要件を満たすかをローカルに検証する。

- **引数**: `repos_path: str | Path`
- **戻り値**: `tuple[bool, str]` — `(適合するか, 不適合理由 or 空文字列)`
- **判定内容**
  1. JSON として読み込み・パースできること（失敗 → `(False, "repos.json を読み込めない: <例外>")`）
  2. トップレベルが `dict` であること（失敗 → `(False, "repos.json のトップレベルが object ではない")`）
  3. `_` で始まらないキーの値がすべて `dict` であること
     （失敗 → `(False, "repos.json のエントリ '<key>' が object ではない")`）
  4. 全て満たせば `(True, "")`
- **例外**: `OSError` / `ValueError`（JSON デコードエラー）のみ捕捉。それ以外は伝播しうる。

#### `detect_capabilities(binary, run=subprocess.run, timeout=PROBE_TIMEOUT) -> dict[str, bool]`

`--help` / `<サブコマンド> --help` を実プローブし、`verify`・`tasks` サブコマンドと `--debt`
フラグの利用可能性を能力フラグとして返す。`get_version` とは独立に、実バイナリの argparse 出力へ
直接問い合わせる（「実際にこのバイナリで受理されるか」の裏取り）。

- **引数**: `get_version` と同型（`binary: list[str]`, `run`, `timeout`）。
- **戻り値**: `dict[str, bool]` — 常に `{"verify": bool, "tasks": bool, "debt": bool}` の3キー固定。
- **CLI 呼び出し形**（内部で非公開ヘルパー2つを介して複数回呼び出す）:
  1. `[*binary, "--help"]` → 出力の `_SUBCOMMANDS_RE`（`{a,b,c}` 形式）にマッチしたサブコマンド集合を取得
     （`_list_subcommands`）。`"verify"` / `"tasks"` がこの集合に含まれるかで各能力を判定。
  2. `capabilities["verify"]` / `capabilities["tasks"]` が True の各サブコマンドについて
     `[*binary, <subcommand>, "--help"]` を実行し、出力に `"--debt"` 文字列が含まれるかを判定
     （`_subcommand_supports_flag`）。
  3. `capabilities["debt"]` は「対象サブコマンドが1つ以上あり、かつ全てが `--debt` をサポート」の
     場合のみ True（`bool(debt_checks) and all(debt_checks)`）。
- **終了コード・例外の扱い**: いずれの呼び出しも `proc.returncode != 0` または
  `OSError`/`subprocess.SubprocessError`（timeout 含む）なら、その段の判定は False（または空集合）に
  縮退。プローブ失敗を例外として外へ漏らすことはない。

### 非公開ヘルパー（参考・直接は呼ばれない想定）

- `_list_subcommands(binary, run=subprocess.run, timeout=PROBE_TIMEOUT) -> set[str]`
  `--help` 出力から `{verify,tasks,scan,...}` 形式の集合を抽出。プローブ失敗時は空集合。
- `_subcommand_supports_flag(binary, subcommand, flag, run=subprocess.run, timeout=PROBE_TIMEOUT) -> bool`
  `<subcommand> --help` の出力に `flag` 文字列が含まれるかを判定。プローブ失敗時は `False`。

---

## 3. codd-gate CLI 呼び出し形のまとめ（このモジュール群が組み立てる実引数）

このモジュール群自体は `verify`/`tasks` 本実行（差分ゲート判定・タスク化）は行わず、**存在確認・能力
プローブ**のみを行う。実プローブに使われる argv パターンは以下の3種類のみ:

| 呼び出し元 | argv | 期待する成功シグナル | 失敗時の縮退先 |
|---|---|---|---|
| `get_version` | `<binary> --version` | `returncode == 0` かつ stdout が `codd-gate X.Y.Z` にマッチ | `None`（バージョン不明） |
| `detect_capabilities` (`_list_subcommands`) | `<binary> --help` | `returncode == 0` かつ stdout に `{sub1,sub2,...}` 形式のサブコマンド一覧 | 空集合 → 全能力 False |
| `detect_capabilities` (`_subcommand_supports_flag`) | `<binary> verify --help` / `<binary> tasks --help` | `returncode == 0` かつ stdout に `--debt` を含む | `False`（当該サブコマンドの `--debt` 非対応扱い） |

いずれも `capture_output=True, text=True, timeout=PROBE_TIMEOUT(=5)` で実行し、
`OSError`/`subprocess.SubprocessError`（timeout 含む）は個別に捕捉して「不明・不可」側へ縮退させる
（例外を呼び出し元へ伝播させない）。

参考: codd-gate 本体（`tools/codd-gate/codd-gate.py`）側の実際のサブコマンド定義と終了コード規約
（このモジュール群が前提とする仕様。本体側の変更検知はこのモジュール群の範囲外）:

- `verify --repos FILE [--config] [--repo-dir NAME=DIR ...] [--sync] [--json] --base REV [--repo NAME] [--strict] [--strict-cross] [--debt [--max-broken N] [--max-undocumented N] [--max-untested N]]`
  → 一貫性ゲート。ドリフト無し `exit 0` / ドリフトあり `exit 1`（`main()` 内 `1 if ng else 0` 等）。
- `tasks [--base REV] [--repo NAME] [--debt] [--priority N] [--max N] [--cohort] [--inbox DIR]`
  → 修復タスク生成。`findings` の有無で `1 if findings else 0`。
- `scan` / `impact` / `check` — 本モジュール群のプローブ対象外（`detect_capabilities` は
  `verify`/`tasks`/`--debt` のみ判定）。
- 引数不正・レジストリ未検出等は `_die(msg, code=2)` で `exit 2`（argparse の使用エラーと同じ 2）。

---

## 検証内容と結果

- `python3 -m pytest tools/kiro-project/tests -q -k codd`
  → **成功**（63 passed, 579 deselected, 3 subtests passed）。
- `grep -rl "codd_gate" tools/kiro-project/kiro_project/`
  → **成功**（`tools/kiro-project/kiro_project/coddgate.py` がヒット）。
- `codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict`
  → **失敗（exit 1）**。`tools/kiro-project/kiro_project/coddgate.py` が
  `[GRAY] ドキュメント・テストのどちらにも接続が無い` として検出され NG。
  このファイルは docstring に明記の通り「単体 import しない・現時点で未結線」の断片であり、
  接続（doc/test 紐付けや `__init__.py` の `_FRAGMENTS` 経由の結線）は本タスクの担当範囲外
  （b1-b3/c1-c2/e1-e2 系の別タスクの責務）。この一覧化タスク自体はコードを変更していないため、
  上記 GRAY 判定は本タスク着手前から存在する状態。

## 前提・未解決事項・範囲外で見つけた問題

- **前提**: 「codd-gate CLI の呼び出し形」は、`codd_gate_base.py`/`codd_gate_detect.py` が
  実際に codd-gate バイナリへ発行する `subprocess` 呼び出し（`--version`/`--help`/
  `<sub> --help` のプローブ群）を指すと解釈した。`verify`/`tasks` の本実行 argv 組み立ては
  この2ファイルの責務外（b2/b3/d2 側）のため、上表では codd-gate 本体の CLI 仕様として参考記載に留めた。
- **未解決事項**: 全体ゲート `codd-gate verify --strict` は現状 exit 1（`kiro_project/coddgate.py`
  の GRAY 未接続）。本タスクの担当範囲外のため未修正のまま報告する。
- **範囲外で見つけた問題**: 上記と同一。`kiro_project/coddgate.py` の doc/test 接続、または
  `__init__.py` `_FRAGMENTS` への結線が別タスクで必要（評価役の判断に委ねる）。
- ファイルは調査のみで一切変更していない（commit/push 対象なし）。
