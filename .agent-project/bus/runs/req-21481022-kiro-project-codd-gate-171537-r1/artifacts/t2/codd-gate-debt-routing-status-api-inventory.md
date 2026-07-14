# codd_gate_debt / codd_gate_routing / codd_gate_status — 公開API・依存関係一覧

対象コミット時点の worktree: `kp/kiro-project-codd-gate-171537`
（`tools/kiro-project/codd_gate_debt.py` / `codd_gate_routing.py` / `codd_gate_status.py`。調査のみ、コード変更なし）

## 1. codd_gate_debt.py — `tasks --debt` 出力パーサ

**責務**: `codd-gate tasks --debt`（差分モードの `tasks` も同形式）の stdout テキストを
`schemas/task.schema.json` 契約（`title` 必須・additionalProperties true）に従って
`DriftItem` のリストへレコード単位で正規化する。CoddGateStatus のセッション粒度キャッシュとは
別粒度（呼ぶ都度・要素ごと）の防御的パースとして意図的に切り出されている。

公開API:
- `DriftItem`（frozen dataclass） — `title: str` / `id: str|None=None` / `fields: dict={}`。
  - `to_spec() -> dict` — `enqueue_task(cfg, spec)` / `run_intake` がそのまま受け取れる dict に戻す。
- `DebtParseResult`（frozen dataclass） — `items: list[DriftItem]` / `errors: list[str]`（棄却レコード理由、1件1文字列）。
- `parse_debt_output(text: str) -> DebtParseResult` — 唯一のエントリポイント。空文字列/空白のみは0件扱い、JSON不正時は空items+1件errorに縮退、レコード単位の不備（非object・title欠落）は1件だけerrorsに落として残りを継続処理。

非公開: `_normalize_record(raw, index)`。

依存: 標準ライブラリのみ（`json`, `dataclasses`）。他 codd_gate_* モジュール・kiro-project.py の型への依存なし。

**注意（命名と責務の乖離）**: 本タスクの指示文は本モジュールを「負債ラチェット」と呼んでいるが、
実装は基準値との比較（ratchet）を一切行わない純粋な**リスト正規化パーサ**であり、
"ratchet"（current vs baseline の悪化判定）に相当する実装は本ファイルには無い。
実際の負債ラチェット相当のロジックは `tools/kiro-project/kiro_project/coddgate.py` の
`CoddGateDebtStatus` / `codd_gate_debt_status(current, baseline)` に存在する（§4 参照）。
評価役は「負債ラチェット」がどちらを指すか要確認。

## 2. codd_gate_routing.py — repos.json / --repo-dir 引数ビルダ

**責務**: regression/acceptance/enqueue の3フック（設計上の b3/c1/e1）が共通で使う、
`--repos` と `--repo-dir` の実引数を組み立てる純粋関数群。kiro-project.py の型（Config/Charter/Task）に依存しない設計（呼び出し側がプリミティブ値を渡す）。

公開API:
- `DEFAULT_REPO_DIR = "."`（モジュール定数）
- `resolve_repos_arg(repos_path, vcwd=None) -> str` — `vcwd` 配下に `repos_path` があれば `vcwd` からの相対パス、無ければ絶対パスへフォールバック。`vcwd` 省略時は `repos_path` をそのまま文字列化。存在確認はしない（純粋関数）。
- `resolve_repo_dir_arg(name, dir=DEFAULT_REPO_DIR) -> str` — `"NAME=DIR"` の1エントリ。
- `build_routing_args(repos_path, name, vcwd=None, dir=DEFAULT_REPO_DIR) -> list[str]` — 唯一の主要エントリポイント。`["--repos", <値>, "--repo-dir", "<name>=<dir>"]` を返し、`CoddGateStatus.command()` へそのまま展開できる形。

依存: 標準ライブラリのみ（`pathlib.Path`）。他 codd_gate_* モジュールへの依存なし。

## 3. codd_gate_status.py — 検出結果の値オブジェクトとno-op縮退

**責務**: codd-gate が未検出・非互換のいずれであっても例外を漏らさず `usable=False` の
`CoddGateStatus` を返す（no-op縮退）。呼び出し側は `if status.command(...):` の1行で済む。

公開API:
- `MIN_SUPPORTED_VERSION = (1, 0, 0)`（モジュール定数）
- `CoddGateStatus`（frozen dataclass） — `binary: list[str]|None` / `version: tuple|None=None` / `findings: list[dict]=[]`。
  - `usable`（property） — `binary is not None and not findings`。
  - `command(*args) -> list[str]|None` — usable なら `[*binary, *args]`、でなければ `None`。
  - `reason`（property） — 先頭findingの`title`、usableなら空文字列。
- `build_status(binary, version=None, version_known=True, schema_ok=True, schema_detail="") -> CoddGateStatus` — 実在→バージョン→schemaの短絡順でfinding化する純粋関数。例外を投げない。
- `detect_status(explicit=None, which=shutil.which, run=subprocess.run) -> CoddGateStatus` — `resolve_codd_gate` + `get_version` を1回で完結させる「合流点」（唯一の入口ではない。schema適合まで確定させたい呼び出し側は `build_status` を直接呼んでよい）。

非公開: `_finding_not_found` / `_finding_version_unknown` / `_finding_version_too_old` / `_finding_schema_incompatible`。

依存: 標準ライブラリ（`shutil`, `subprocess`, `dataclasses`）＋ 同梱の **`codd_gate_detect`**（`get_version`, `resolve_codd_gate`）。
3ファイル中、唯一 tools/kiro-project 内の他モジュールに直接依存する（`from codd_gate_detect import get_version, resolve_codd_gate`）。

## 4. 依存グラフ（tools/kiro-project 配下の codd_gate_* 全体）

```
codd_gate_detect.py  (stdlib only)
        ↑
codd_gate_status.py  (stdlib + codd_gate_detect)
codd_gate_base.py    (stdlib only, 独立)
codd_gate_routing.py (stdlib only, 独立)
codd_gate_debt.py    (stdlib only, 独立)
codd_gate_invoke.py  (stdlib only, CoddGateStatus とはダックタイピングで結合・import なし)
        ↑ すべてを import
codd_gate_hooks.py   (base + debt + invoke + routing + status を合成する「合流点」)
        = run_diff_gate() / collect_debt_specs() を提供
        ↑
kiro-project.py 本体（regression/acceptance/enqueue フック）… 未結線（後述）
```

`codd_gate_hooks.py` が3フック向けの唯一の合成窓口:
- `run_diff_gate(repos_path, name, vcwd, task_base_branch, ...)` — `detect_status` → `resolve_base_rev` → `build_routing_args` → `invoke_codd_gate(status, "verify", ...)` → pass/fail タプル。
- `collect_debt_specs(repos_path, name, vcwd, ...)` — `detect_status` → `build_routing_args` → `invoke_codd_gate(status, "tasks", "--debt", ...)` → `parse_debt_output` → `DriftItem.to_spec()` のリスト。

## 5. kiro-project.py 本体への結線状況（範囲外の確認事項）

`kiro-project.py` 本体（regression_cmd/acceptance/intake_cmd の実処理）は現時点で
`codd_gate_*` モジュールを1つも import していない（grep で import 文ゼロ、ヒットしたのは
コメント・設定キーの説明文のみ: L505, L3296, L8522, L10257, L10604）。
つまり `codd_gate_hooks.run_diff_gate` / `collect_debt_specs` は実装済みだが、
`_settle_task` の regression 判定や `run_intake` からはまだ呼ばれていない（各モジュールの
docstring が「意図的に含めない」と明記している b1-b3/c1-c2/e1-e2 の結線タスクは別タスク）。

## 6. 範囲外で見つけた問題（評価役への報告）

1. **`kiro_project/coddgate.py` は孤立フラグメント**: `tools/kiro-project/kiro_project/` には
   `coddgate.py` 1ファイルのみ存在し、`__init__.py` も、ファイル自身が前提とする `_head.py` も
   リポジトリ全体に存在しない（`_FRAGMENTS` という語もこのファイル内のコメント以外どこにも出現しない）。
   ファイル自身の冒頭コメントが「単体 import しない・現時点で未結線」と明記する通り、
   どこからも import されていないコードである（grep で `import kiro_project` 系の参照ゼロ）。
   このファイルは `codd_gate_enabled()` / `CoddGateNoopResult` / `CoddGateDebtStatus` /
   `codd_gate_debt_status(current, baseline)` / `codd_gate_summary_text()` を持ち、
   **`codd_gate_status.py` のno-op縮退や `codd_gate_debt.py` のドリフト正規化とは別物の、
   もう一系統の codd-gate 連携実装**（特に `codd_gate_debt_status` が current/baseline を
   比較する本来の「負債ラチェット」に相当）になっている。
   本タスクの完了条件 `grep -rq "codd_gate" tools/kiro-project/kiro_project/` は
   このファイルの存在だけで機械的に満たされてしまうため、後続タスク（結線・テスト追加）が
   どちらの実装系統を正典とするか、あるいは両者を統合するのかを明確にする必要がある。
2. `codd_gate_debt.py` / `codd_gate_status.py` 単体の専用テストファイルが無い
   （`test_codd_gate_debt.py` は存在しない。`codd_gate_status.py` は `test_codd_gate_detect.py` 内の
   `TestCoddGateStatusNoOpDegradation` 等で間接的にカバーされているのみ）。
   `parse_debt_output` の正規化ロジック（非object・title欠落・JSON不正時の縮退）は
   `test_codd_gate_hooks.py` 経由の統合テストでしか触れられていない可能性が高く、
   単体テスト（元要求の「t4」）はこのファイル一覧調査タスクの対象外。

## 7. 検証

- 本タスクは調査のみで worktree のファイルは変更していない（`git status --short` で対象3ファイル差分なしを確認）。
- 完了条件のシェルコマンド（pytest -k codd / grep codd_gate / codd-gate verify）は本タスクの
  担当範囲外（ファイル一覧化作業に対応する verify ではなく、run 全体の最終ゲート）のため、
  本タスク単体では実行・報告しない。上記§5・§6の事実確認は `grep`/`find`/`Read` で直接検証済み。

## 8. 採用した前提

- 「負債ラチェット」は依頼文の表現をそのまま踏襲しつつ、実装が指すものとの乖離を§1・§6-1で明記した。
- 「公開API」は各モジュールのモジュールレベルの class/function/定数のうち `_` 始まりでないものとした。
- 依存関係は `import` 文ベースの静的解析（grep）とし、実行時のダックタイピング結合（`codd_gate_invoke.py` が `CoddGateStatus` を import せず `status.command()` を呼ぶ等）は文章で補足した。
