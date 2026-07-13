# t3: codd_gate_detect.py / codd_gate_routing.py / codd_gate_status.py / codd_gate_debt.py 棚卸しと coddgate.py 統合時の最終API定義

## 前提（t1/t2 の重大な前提崩れを追認、本タスクへの影響なし）

作業ブランチ `kp/kiro-project-codd-gate-171537`（HEAD `99d71b2e`）に `tools/kiro-project/kiro_project/`
パッケージは存在せず、`_head`〜`cli` の `_FRAGMENTS` 構造は `main` ブランチにのみ存在する（t1・t2 既報告、
本タスク実行時点でも未解消）。この不整合の解消は t3 の範囲外。本タスクは「4ファイルの読解と最終API定義」
という `work` 種別の調査タスクであり、対象ファイルは現ワークツリーに実在するため作業自体は成立する。
ワークツリーへの書き換えは行っていない（`git status` 差分なし）。

## 対象4ファイルの棚卸し

### codd_gate_detect.py（182行。stdlib のみ: json, os, re, shutil, subprocess, sys, pathlib）

| 現行シンボル | 種別 | シグネチャ | 責務 |
|---|---|---|---|
| `BINARY_NAME` | 定数 | `str = "codd-gate"` | 探索対象バイナリ名 |
| `PROBE_TIMEOUT` | 定数 | `int = 5` | プローブ系 subprocess の既定 timeout(秒) |
| `resolve_codd_gate` | 関数 | `(explicit: str\|None=None, which=shutil.which) -> list[str]\|None` | 起動 argv prefix を explicit→PATH→同梱パスの順で解決 |
| `resolve_codd_gate_bin` | 関数 | `(config_bin: str\|None=None, env: dict\|None=None, which=shutil.which) -> str\|None` | `resolve_codd_gate` の `explicit` に渡す値を env(`CODD_GATE_BIN`)→config→PATH の順で解決。例外は握り潰し None に縮退 |
| `get_version` | 関数 | `(binary: list[str], run=subprocess.run, timeout=PROBE_TIMEOUT) -> tuple[int,int,int]\|None` | `--version` を実行しバージョンタプルを取得。timeout・非0終了・パース不能は None |
| `check_repos_schema_compat` | 関数 | `(repos_path: str\|Path) -> tuple[bool, str]` | repos.json が repos.schema.json の最小要件（トップレベル object 等）を満たすか |
| `detect_capabilities` | 関数 | `(binary: list[str], run=subprocess.run, timeout=PROBE_TIMEOUT) -> dict[str, bool]` | `--help` プローブで verify/tasks サブコマンドと `--debt` フラグの利用可否を判定 |
| `_list_subcommands` | private関数 | `(binary, run, timeout) -> set[str]` | `--help` 出力からサブコマンド集合を抽出 |
| `_subcommand_supports_flag` | private関数 | `(binary, subcommand, flag, run, timeout) -> bool` | `<sub> --help` 出力にフラグ文字列が含まれるか |

### codd_gate_routing.py（83行。stdlib のみ: pathlib）

| 現行シンボル | 種別 | シグネチャ | 責務 |
|---|---|---|---|
| `DEFAULT_REPO_DIR` | 定数 | `str = "."` | `--repo-dir` の既定 dir 値 |
| `resolve_repos_arg` | 関数 | `(repos_path: str\|Path, vcwd: str\|Path\|None=None) -> str` | `--repos` の値を解決（vcwd 配下なら相対パス、それ以外は絶対パス） |
| `resolve_repo_dir_arg` | 関数 | `(name: str, dir: str=DEFAULT_REPO_DIR) -> str` | `--repo-dir` の1エントリ `NAME=DIR` を組み立て |
| `build_routing_args` | 関数 | `(repos_path, name, vcwd=None, dir=DEFAULT_REPO_DIR) -> list[str]` | 上記2つを合成し `["--repos", ..., "--repo-dir", ...]` を返す |

全関数が純粋関数（I/O・例外なし）。

### codd_gate_status.py（151行。stdlib: shutil, subprocess, dataclasses。同スコープ内で `codd_gate_detect` に依存）

| 現行シンボル | 種別 | シグネチャ | 責務 |
|---|---|---|---|
| `MIN_SUPPORTED_VERSION` | 定数 | `tuple = (1, 0, 0)` | 対応バージョン下限 |
| `CoddGateStatus` | dataclass(frozen) | フィールド `binary, version, findings`。プロパティ `usable, reason`。メソッド `command(*args)` | 検出結果の値オブジェクト。no-op 縮退の中核（findings が1件でもあれば usable=False） |
| `build_status` | 関数 | `(binary, version=None, version_known=True, schema_ok=True, schema_detail="") -> CoddGateStatus` | 生判定を「実在→バージョン→schema」の短絡順で finding 化 |
| `detect_status` | 関数 | `(explicit=None, which=shutil.which, run=subprocess.run) -> CoddGateStatus` | `resolve_codd_gate`+`get_version` をまとめて実行し `build_status` へ橋渡しする合流点 |
| `_finding_not_found` / `_finding_version_unknown` / `_finding_version_too_old` / `_finding_schema_incompatible` | private関数 | 引数なし〜(binary, version) | finding dict（category/severity/title/evidence/fix）を組み立てる |

`detect_status` は `codd_gate_detect.py` の `resolve_codd_gate` と `get_version` を直接呼ぶ
（`from codd_gate_detect import get_version, resolve_codd_gate`）。4ファイル内で唯一の実 import 依存。

### codd_gate_debt.py（101行。stdlib のみ: json, dataclasses）

| 現行シンボル | 種別 | シグネチャ | 責務 |
|---|---|---|---|
| `DriftItem` | dataclass(frozen) | フィールド `title, id, fields`。メソッド `to_spec()` | schemas/task.schema.json に正規化した1件。`to_spec()` は `enqueue_task`/`run_intake` 互換 dict を返す |
| `DebtParseResult` | dataclass(frozen) | フィールド `items: list[DriftItem], errors: list[str]` | パース結果一式 |
| `parse_debt_output` | 関数 | `(text: str) -> DebtParseResult` | `codd-gate tasks --debt` の stdout（object/array 両対応、空文字は0件）をレコード単位で防御的にパース |
| `_normalize_record` | private関数 | `(raw: object, index: int) -> tuple[DriftItem\|None, str\|None]` | 1レコードの検証（object か、title 必須）と正規化 |

## 関数名衝突の棚卸し

**4ファイル間での完全一致な名前衝突は無い**（全15公開シンボル+6 private helper を突き合わせて確認）。

ただし以下は要注意点として記録する:

1. **`resolve_codd_gate` と `resolve_codd_gate_bin` の名称近接**（衝突そのものではないが、
   単純に `codd_gate_` を先頭へ付け替えるだけだと `codd_gate_resolve` 系の名前が2つ並び役割が
   紛らわしくなる）。前者は起動 argv（`list[str]`）を返し、後者は `explicit` へ渡す1文字列
   （`str`）を env→config→PATH で解決する前段関数——戻り値の型も担う段も異なる。最終APIでは
   役割が一目で分かるよう別語幹にする（下記「最終API定義」参照）。
2. **ファイル間の実依存**: `codd_gate_status.py` が `codd_gate_detect.py` から
   `get_version`/`resolve_codd_gate` を import している。1ファイルに統合すると
   この import 文自体が不要になり削除対象になる（同一名前空間内の関数呼び出しに変わるだけ）。
   結果として **`codd_gate_status.py` 由来の関数は、定義順として `codd_gate_detect.py` 由来の
   関数より後に置く必要がある**（Python の関数本体は呼び出し時に名前解決されるため技術的必須
   ではないが、`_FRAGMENTS` の断片規約が「前方参照は避け記述順を意味あるものにする」慣習を
   敷いているため、可読性目的で踏襲する）。
3. **private helper の命名が汎用的すぎる**: `_finding_not_found` / `_list_subcommands` /
   `_subcommand_supports_flag` / `_normalize_record` は英単語1〜2語のみで、`kiro_project/`
   が最終的に26以上の断片を **1つの共有 globals** へ exec 合成する設計（t1 報告）である以上、
   他断片（`state.py`/`model.py`等）に同名 private helper が既にある場合サイレントに上書きされる
   リスクがある。この衝突可能性は本タスクの対象4ファイル同士では検証済み（衝突なし）だが、
   **4ファイル外（`codd_gate_base.py`＝t2、`codd_gate_hooks.py`/`codd_gate_invoke.py`＝担当タスク
   無し、`verify.py`/`mr.py`/`model.py`等の既存23断片＝t4）との衝突は本タスクのスコープ外で
   未検証**。t5（coddgate.py 新規作成）着手前に synth/gate 側で全断片の識別子一覧との突合せを
   推奨する。
4. **定数も同じ理由で汎用的**（`BINARY_NAME`, `PROBE_TIMEOUT`, `MIN_SUPPORTED_VERSION`,
   `DEFAULT_REPO_DIR`）。タスク要件は「関数名」の接頭辞統一だが、共有 globals 環境という
   特性を踏まえ定数も `CODD_GATE_` 接頭辞化を付随的に推奨する（必須ではない）。

## 最終API定義（coddgate.py 断片・この4ファイル分。`codd_gate_` 接頭辞で統一）

新旧対応表（シグネチャ・戻り値・挙動は現行から変更しない。名前のみ変更）:

| 現行名 | 最終名 | 種別 |
|---|---|---|
| `BINARY_NAME` | `CODD_GATE_BINARY_NAME` | 定数（推奨） |
| `PROBE_TIMEOUT` | `CODD_GATE_PROBE_TIMEOUT` | 定数（推奨） |
| `resolve_codd_gate` | `codd_gate_resolve_argv` | 関数 |
| `resolve_codd_gate_bin` | `codd_gate_resolve_bin_override` | 関数 |
| `get_version` | `codd_gate_get_version` | 関数 |
| `check_repos_schema_compat` | `codd_gate_check_repos_schema_compat` | 関数 |
| `detect_capabilities` | `codd_gate_detect_capabilities` | 関数 |
| `_list_subcommands` | `_codd_gate_list_subcommands` | private関数（推奨） |
| `_subcommand_supports_flag` | `_codd_gate_subcommand_supports_flag` | private関数（推奨） |
| `MIN_SUPPORTED_VERSION` | `CODD_GATE_MIN_SUPPORTED_VERSION` | 定数（推奨） |
| `CoddGateStatus` | `CoddGateStatus`（変更なし） | クラス |
| `build_status` | `codd_gate_build_status` | 関数 |
| `detect_status` | `codd_gate_detect_status` | 関数 |
| `_finding_not_found` 等4件 | `_codd_gate_finding_not_found` 等（同語幹に接頭辞付与） | private関数（推奨） |
| `DEFAULT_REPO_DIR` | `CODD_GATE_DEFAULT_REPO_DIR` | 定数（推奨） |
| `resolve_repos_arg` | `codd_gate_resolve_repos_arg` | 関数 |
| `resolve_repo_dir_arg` | `codd_gate_resolve_repo_dir_arg` | 関数 |
| `build_routing_args` | `codd_gate_build_routing_args` | 関数 |
| `DriftItem` | `CoddGateDriftItem`（`CoddGateStatus` と同じ PascalCase 規約に揃える） | クラス |
| `DebtParseResult` | `CoddGateDebtParseResult` | クラス |
| `parse_debt_output` | `codd_gate_parse_debt_output` | 関数 |
| `_normalize_record` | `_codd_gate_normalize_debt_record` | private関数（推奨） |

命名規約: モジュール直下の公開関数は `codd_gate_<動詞>_<対象>`、クラスは `CoddGate<名詞>` の
PascalCase、private helper は `_codd_gate_<内容>`。「推奨」を付けた項目（定数・private helper）は
タスク要件（関数名の接頭辞統一）そのものではないが、上記の共有 globals 衝突リスクを踏まえた
付随提案であり、必須変更ではない。

renaming に伴う参照更新（この4ファイル内で完結、外部ファイルへの影響は次節）:
- `codd_gate_detect_status` 本体内: `resolve_codd_gate(explicit, which=which)` →
  `codd_gate_resolve_argv(explicit, which=which)`、`get_version(binary, run=run)` →
  `codd_gate_get_version(binary, run=run)`。
- ドキュメントに残る合成例 `resolve_codd_gate(resolve_codd_gate_bin(cfg.codd_gate_bin),
  which=shutil.which)` は `codd_gate_resolve_argv(codd_gate_resolve_bin_override(cfg.codd_gate_bin),
  which=shutil.which)` に読み替える。

断片内の推奨定義順（依存順・可読性優先。技術的な強制ではない）:
1. 定数一式
2. detect 由来（`codd_gate_resolve_bin_override` → `codd_gate_resolve_argv` →
   `codd_gate_get_version` → `codd_gate_check_repos_schema_compat` →
   `codd_gate_detect_capabilities` とその private helper）
3. status 由来（detect 由来のシンボルを直接呼ぶため直後に配置。`CoddGateStatus` →
   finding helper 群 → `codd_gate_build_status` → `codd_gate_detect_status`）
4. routing 由来（他に依存しない独立ブロック）
5. debt 由来（他に依存しない独立ブロック）

## 未結線の関数（現状どこからも実運用で呼ばれていない）

`codd_gate_resolve_bin_override`（旧 `resolve_codd_gate_bin`）・
`codd_gate_check_repos_schema_compat`（旧 `check_repos_schema_compat`）・
`codd_gate_detect_capabilities`（旧 `detect_capabilities`）の3つは、`tests/test_codd_gate_detect.py`
以外から一切参照されていない（`codd_gate_hooks.py`/`codd_gate_invoke.py` にも呼び出しなし）。
renaming 自体に支障はないが、統合後にどの結線点（t7 の `codd_gate_verify` 等）が実際にこれらを
使うのか、あるいは使わないまま維持するデッドコードなのかは本タスクの範囲外の判断事項。

## テストカバレッジの現状

- `codd_gate_detect.py`・`codd_gate_routing.py`: 専用単体テスト（`test_codd_gate_detect.py`
  15KB, `test_codd_gate_routing.py`）で全公開関数がカバーされている。
- `codd_gate_status.py`・`codd_gate_debt.py`: **専用単体テストファイルが無い**。
  `CoddGateStatus`/`build_status`/`detect_status`/`parse_debt_output` は
  `test_codd_gate_hooks.py`・`test_codd_gate_invoke.py` 経由で `run_diff_gate`/`invoke_codd_gate`
  の合成結果として間接的にのみ検証されている（`build_status` の4分岐・`parse_debt_output` の
  レコード単位防御的パースの単体境界値テストは無い）。
- `python3 -m pytest tools/kiro-project/tests -q -k codd` を実行し **63 passed（3 subtests 含む）**
  を確認（t2 報告と同値。本タスクでは変更を加えていないため差分なし）。

## 検証

- 4ファイルおよび呼び出し元 `codd_gate_hooks.py`/`codd_gate_invoke.py`（読解のみ、対象外）を
  全量読了。
- `python3 -m pytest tools/kiro-project/tests -q -k codd` 実行、63 passed を確認（ベースライン
  確認のみ。本タスクはコード変更を行っていない）。
- `git status --short`: 差分なし。
- 本タスク自体に個別の完了条件は割り当てられていない（`work` 種別、成果物は本レポート）。
  run 全体の完了条件（`kiro_project/` 配下への実配線・`codd-gate verify --strict` 成功）は
  t5 以降 + gate/loop タスクの担当。

## 採用した前提・未解決事項・範囲外で見つけた問題

- 前提: 「公開関数」はモジュール直下でアンダースコア始まりでない関数・dataclass を指すと
  解釈した。private helper・定数の改名は「推奨」に留め、タスク要件（関数名の接頭辞統一）と
  明確に区別した。
- 未解決（t1/t2 既報告、本タスクでも再確認）: 作業ブランチに `kiro_project/` パッケージが
  存在しない。t5 着手前に main のパッケージ化 refactor を merge するか計画を作り直すかの
  意思決定が必要。
- 範囲外で見つけた問題1: `codd_gate_hooks.py`（`run_diff_gate`, `collect_debt_specs`）と
  `codd_gate_invoke.py`（`invoke_codd_gate`, `CoddGateResult`）の棚卸しは t1〜t4 のどのタスクにも
  割り当てられていない。この2ファイルは実際には本タスク対象4ファイル＋`codd_gate_base.py`を
  合成する「結線層」そのものであり、t5-t9 が新設予定の `codd_gate_verify`/`codd_gate_detect_drift`/
  `codd_gate_debt_status` は `run_diff_gate`/`collect_debt_specs`/`invoke_codd_gate` と機能が
  重複する可能性が高い。この2ファイルの命名・責務の突き合わせを行うタスクがないまま t5 が
  着手すると、同じロジックが2系統できる／`CoddGateResult`（3値 ok/failed/skipped の値オブジェクト）
  のような既存の設計資産が再利用されず作り直される恐れがある。
- 範囲外で見つけた問題2: `codd_gate_status.py`・`codd_gate_debt.py` に専用単体テストが無い。
  charter の目標「ユニットテストを拡充すること」に照らすと、統合時にこのギャップ（`build_status`
  の4分岐、`parse_debt_output` の不正レコード処理）を埋めるテスト追加が必要（t2 が
  `codd_gate_base.py` 側で報告した同種のギャップと合わせ、後続タスクでの手当てを推奨）。
- 範囲外で見つけた問題3: `codd_gate_resolve_bin_override`/`codd_gate_check_repos_schema_compat`/
  `codd_gate_detect_capabilities` が現状どこからも実運用で呼ばれていない（テストのみ）。
  デッドコードとして扱うか、将来の結線タスクが使う前提で残すかの判断が必要。
