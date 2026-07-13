# t1: tools/kiro-project/kiro_project/ 構造棚卸しマップ

対象リポジトリ: https://github.com/ynitto/sandbox （worktree: kp/kiro-project-codd-gate-171537）
調査基準: 本 worktree の HEAD（`git status --short` 差分ゼロ・コード変更なし＝調査のみ）

## (a) 成果 — 構造マップ

### 0. 前提の訂正（重要）

タスク名は `tools/kiro-project/kiro_project/`（アンダースコア区切りのサブパッケージ）を指すが、
実際にはこのパスは**存在しない**。実体は `tools/kiro-project/`（ハイフン区切り）直下に

- `kiro-project.py`（11,219 行のモノリシックな単一ファイル。Python の識別子制約上
  `import` はできず、CLI エントリとして直接実行される想定）
- `codd_gate_*.py`（5 本の小さな衛星モジュール。前 run `run-20260712-213419-5922` の
  a1/a2/a4/b2 相当タスクが新規作成したもの）

がフラットに置かれているだけで、`kiro_project` という名の Python パッケージ（`__init__.py` 付き
ディレクトリ）への分割は**まだ行われていない**。以降「パッケージの分割後構造」は
「`tools/kiro-project/` 配下のモジュール群の現状構造」と読み替えて報告する。

### 1. モジュール一覧・責務

| ファイル | 行数 | 責務 | kiro-project.py への結線 |
|---|---|---|---|
| `kiro-project.py` | 11,219 | 本体。Task/Config/Charter/Policy の型、backlog 永続化、優先順位付け、act/verify、CLI（argparse）、watch ループ、project（charter 駆動）ループを1ファイルに同居させたモノリス | — |
| `codd_gate_base.py` | 54 | 差分ゲート `--base` に渡す rev の解決（`KIRO_BASE_REV` env → task の base branch → `HEAD~1` の優先順位、純粋関数） | **未結線**（`grep -rn "codd_gate_" kiro-project.py` ヒット0） |
| `codd_gate_detect.py` | 142 | codd-gate CLI 実体の解決（`resolve_codd_gate`：explicit→PATH→同梱パス）、`--version` 取得、`repos.json` の schema 適合チェック、`--help` プローブによる verify/tasks/--debt 対応能力検出 | 未結線 |
| `codd_gate_status.py` | 138 | `codd_gate_detect` の生判定を受け、no-op 縮退済みの値オブジェクト `CoddGateStatus`（`usable`/`command()`/`reason`）を組み立てる。`codd_gate_detect` に依存する唯一のモジュール間 import | 未結線 |
| `codd_gate_routing.py` | 82 | `--repos`/`--repo-dir` 引数の組み立て（`build_routing_args`。regression/acceptance/enqueue の3フック共通で使う想定の純粋関数） | 未結線 |
| `codd_gate_debt.py` | 100 | `codd-gate tasks --debt` の stdout（JSON）を `schemas/task.schema.json` 契約でパースし `DriftItem` へ正規化（`parse_debt_output`） | 未結線・**どこからも呼ばれていない**（t5 で確認済みの重複確認: grep ヒット0） |
| `tests/test_kiro_project.py` | 8,547 | 本体の既存機能テスト（579 件相当。ファイル名・テスト名に `codd` を含まない） | — |
| `tests/test_codd_gate_detect.py` | 246 | `codd_gate_detect`/`codd_gate_status` のテスト（29 件） | — |
| `tests/test_codd_gate_routing.py` | 94 | `codd_gate_routing` のテスト | — |

5本の衛星モジュールは docstring で明示的に「kiro-project.py 本体（`cfg.codd_gate` フィールド新設・
regression/acceptance/enqueue の3フックへの結線）は同一 run の別タスクの責務」と述べており、
**設計・実装は完了しているが配線（import・呼び出し）は未着手**という一貫した状態にある。

### 2. エントリポイント

#### CLI（`main(argv=None) -> int`, `kiro-project.py:10955`）
- `argparse.ArgumentParser`（`kiro-project.py:10960`）。サブコマンド未指定時は `run --watch` 相当で常駐監視。
- 主要サブコマンド: `run`（正準ループ）/ `enqueue`（`kiro-project.py:11057`）/ `triage` / `needs` /
  `promote` / `rot` / `stats` / `audit` / `runlog` 等。
- `_add_common(sp)`（`kiro-project.py:10485`）が全サブコマンドに共通 CLI 引数を注入。
  `--regression-cmd`/`--regression-revert`（`10598-10601`）、`--intake-cmd`/`--intake-interval`
  （`10602-10607`）がここで定義される。
- ディスパッチ: `main()` 内の辞書（`"run": lambda: cmd_run(cfg)` 等、`kiro-project.py:11189` 付近）。

#### ループ本体（2系統）
1. **backlog watch**（charter 無し）: `cmd_run`（`8081`）→ `run_watch`（`6765`）→ 毎パス
   `run_loop`（`6535`）を呼び、idle poll 中に `run_intake(cfg)`（`6795`）で外部ゲートを汲み上げる。
2. **project watch**（charter 駆動）: `cmd_run`（`8081`、`charter_names(cfg)` があればこちら）→
   `project_watch`（`10061`）→ charter ごとに `cmd_project`（`9805`）を1パスずつ回す。
   `cmd_project` は plan（分解）→ execute（`run_loop` 呼び出し）→ evaluate（`_project_evaluate`,
   `9753`。中で `evaluate_acceptance` を呼ぶ）の3段。
- タスクレベルの1件処理（act→検証ゲート→done/review/retry/escalate 確定）は
  `_settle_task`（`5490`）に集約されており、`run_loop` の per-task 本体を切り出したもの。

### 3. 既存の設定読み込み経路

優先順位は **CLI 引数 > 設定ファイル > 組み込み既定**（`kiro-project.py:10146` のコメントに明記）。

1. `CONFIG_DEFAULTS`（`kiro-project.py:10172-10305`）— snake_case キーと既定値の辞書。
   `regression_cmd`/`regression_revert`/`intake_cmd`/`intake_interval` はここで
   `None`/`False`/`None`/`600.0` として定義（`10255-10258`）。
2. `_find_config(explicit)`（`10308`）— `--config` 明示 → `./` → `./.kiro/` → `~/.kiro/` の順に
   `kiro-project.yaml`/`.yml`/`.json` を探索。
3. `_load_config_file(path)`（`10152`/`10158`。PyYAML の有無で分岐）— YAML/JSON をそのまま dict 化。
4. `resolve_config(args)`（`10328`）— CLI 側が `None`（未指定）のキーだけを設定ファイル値→
   `CONFIG_DEFAULTS` の順で埋める（`argparse.Namespace` を直接書き換える副作用関数）。
5. `build_config(args) -> Config`（`10339`）— `root`（`--root`。唯一のアンカー）を基準に
   backlog/policy/decisions/journal/needs 等の全パスを解決し、`Config` dataclass
   （`4510` 定義開始）を構築する。`regression_cmd`/`intake_cmd`/`intake_interval` は
   `10435-10437` でそのまま `getattr(args, ...)` から Config へ渡される。

`Config.regression_cmd`/`regression_revert`/`intake_cmd`/`intake_interval` フィールド自体の定義位置は
`kiro-project.py:4607-4610`。

### 4. regression / acceptance / enqueue の実装箇所（file:line + シグネチャ）

#### regression（差分ゲート・回帰検査）
| 関数/フィールド | file:line | シグネチャ |
|---|---|---|
| `Config.regression_cmd` / `regression_revert` | `kiro-project.py:4607-4608` | フィールド定義（`str \| None`, `bool`） |
| `run_verify` | `kiro-project.py:3018` | `def run_verify(cmd: str, workdir: Path, timeout: float, env: "dict \| None" = None) -> "tuple[bool, str]"` |
| `run_verify_stable` | `kiro-project.py:3031` | `def run_verify_stable(cmd: str, workdir: Path, timeout: float, confirm: int = 1, env: "dict \| None" = None) -> "tuple[bool, bool, str]"` |
| `_task_verify_cwd` | `kiro-project.py:3098` | `def _task_verify_cwd(cfg: "Config", task: "Task") -> "tuple[Path, str \| None]"` |
| `_settle_task`（回帰ゲート呼び出し箇所） | `kiro-project.py:5490`（呼び出し本体は `5524-5534`） | `def _settle_task(cfg: "Config", task: "Task", location: str, act_msg: str, cycle: int, dtok: int, dusd: float, git_base, verify_env, policy: "Policy", autonomy_cache: dict, reasons: dict) -> dict` |
| `git_change_baseline`（差分基準・`KIRO_BASE_REV` 注入元） | `kiro-project.py:831` | — |

`_settle_task` 内 `5524`: `if ok and not flaky and cfg.regression_cmd:` → `run_verify(cfg.regression_cmd, vcwd, cfg.verify_timeout, venv)` で回帰ゲートを実行し、NG なら `_block`（人の判断へ）。
`venv`（`KIRO_BASE_REV`）は一時 clone 時のみ注入（`5517-5519`）で、`codd_gate_base.py` の
docstring が指摘する通り非 git ワークスペース等では未注入＝空文字に倒れる穴が残る。

#### acceptance（charter 受入判定）
| 関数 | file:line | シグネチャ |
|---|---|---|
| `Charter.acceptance` | `kiro-project.py:8184` | フィールド定義（`list[str]`。受入 verify コマンド行） |
| `_acceptance_cwd` | `kiro-project.py:9524` | `def _acceptance_cwd(cfg: "Config", charter: "Charter") -> "tuple[Path, str \| None]"` |
| `evaluate_acceptance` | `kiro-project.py:9546` | `def evaluate_acceptance(cfg: "Config", charter: "Charter") -> "tuple[int, int, list]"` |
| `_acceptance_kind` | `kiro-project.py:9575` | `def _acceptance_kind(line: str) -> "tuple[str, str]"` |
| `resolve_charter_acceptance` | `kiro-project.py:9589` | `def resolve_charter_acceptance(cfg: "Config", charter: "Charter", state: "dict \| None" = None, kiro_run=None) -> "tuple[list[str], list[str]]"` |
| `_acceptance_specs` | `kiro-project.py:9618` | `def _acceptance_specs(cmds: "list[str]") -> "list[dict]"` |
| `_failing_acceptance_specs` | `kiro-project.py:9625` | `def _failing_acceptance_specs(results: "list") -> "list[dict]"` |
| `_project_evaluate`（呼び出し元） | `kiro-project.py:9753` | `def _project_evaluate(cfg: "Config", charter: "Charter", pid: str, state: dict, cycle: int, cost_used: float, review_fn, charter_tag: str = "") -> "tuple[str \| None, str]"` |

`_project_evaluate` の `9759` で `evaluate_acceptance` を呼び、未達 acceptance を
`_failing_acceptance_specs`→`_enqueue_specs`（`9777`）で改善タスクへ変換する。
`evaluate_acceptance` 内 `9556-9561` で `KIRO_BASE_REV` を評価先 clone の HEAD から注入（regression 側と同型だが独立実装）。

#### enqueue（取り込み・タスク生成）
| 関数 | file:line | シグネチャ |
|---|---|---|
| `enqueue_task` | `kiro-project.py:290` | `def enqueue_task(cfg: "Config", spec: dict) -> Task` |
| `task_from_spec` | `kiro-project.py:250` | `def task_from_spec(cfg: "Config", spec: dict) -> Task` |
| `run_intake`（外部ゲート汲み上げフック） | `kiro-project.py:502` | `def run_intake(cfg: "Config") -> "list[Task]"` |
| `cmd_enqueue`（CLI 手動投入口） | `kiro-project.py:7979` | `def cmd_enqueue(cfg: Config, args) -> int` |
| `_enqueue_specs`（spec 群の冪等投入。acceptance 改善タスクにも使用） | `kiro-project.py:9258` | `def _enqueue_specs(cfg: "Config", specs: "list[dict]", existing: "list[str]", threshold: float, charter: "str \| None" = None, active_only: bool = False) -> "list[Task]"` |
| `persist_task` / `serialize_task` | `kiro-project.py:196` / `175` | ディスク書き込み・シリアライズ |
| `Config.intake_cmd` / `intake_interval` | `kiro-project.py:4609-4610` | フィールド定義 |

`run_intake` は `cfg.intake_cmd`（シェルコマンド）を `subprocess.run(..., shell=True, cwd=cfg.workdir)`
で実行し、stdout の JSON（`enqueue --json` と同形式）を `enqueue_task` へ回す。冪等判定は
`cfg.backlog` 直下 `*.md` のファイル名 stem 集合との突合（`538-544`）。`codd_gate_debt.py` の
`parse_debt_output`/`DriftItem` はこの経路のどこからも呼ばれておらず、`run_intake` 自前のインライン
JSON パースと機能が重複している（t5 が指摘済み・未確定事項）。

### 5. 全体像（結線状況）

```
CLI (main/argparse, :10955)
  └─ cmd_run (:8081)
       ├─ run_watch (:6765, charter無し) ─┐
       └─ project_watch (:10061, charter駆動)  │
              └─ cmd_project (:9805)           │
                    ├─ execute: run_loop ───────┼─→ run_loop (:6535)
                    └─ evaluate: _project_evaluate (:9753)
                          └─ evaluate_acceptance (:9546)  ★acceptance

run_loop (:6535)
  ├─ run_intake (:502)                          ★enqueue（自動）
  ├─ _settle_task (:5490)
  │     └─ cfg.regression_cmd 実行 (:5524-5534) ★regression
  └─ ...

cmd_enqueue (:7979) ── CLI 手動投入            ★enqueue（手動）

codd_gate_{base,detect,status,routing,debt}.py ── 全5本、上記のどこからも import されていない
```

## (b) 検証内容と結果

- `find tools/kiro-project -name "*.py"` で全 Python ファイルを列挙し、`tools/kiro-project/kiro_project/`
  というサブパッケージが存在しないことを確認（フラット構造）。
- `wc -l` で行数を確認（`kiro-project.py` 11,219 行、衛星モジュール 54〜142 行、テスト
  `test_kiro_project.py` 8,547 行 / `test_codd_gate_detect.py` 246 行 / `test_codd_gate_routing.py` 94 行）。
- 5本の衛星モジュール（`codd_gate_base/debt/detect/routing/status.py`）を全文読み、各 docstring の
  責務境界宣言（「同一 run の別タスクの責務」列挙）を確認した。
- `grep -n "codd" kiro-project.py` → ヒットはコメント/docstring/CLI ヘルプ文言の4箇所のみで、
  `import codd_gate_*` 等の実コードからの参照は0件（未結線を実地確認）。
- `enqueue_task`/`task_from_spec`/`run_intake`/`cmd_enqueue`/`_enqueue_specs`、
  `run_verify`/`run_verify_stable`/`_settle_task`/`_task_verify_cwd`、
  `evaluate_acceptance`/`_acceptance_cwd`/`resolve_charter_acceptance`/`_project_evaluate`、
  `CONFIG_DEFAULTS`/`_find_config`/`resolve_config`/`build_config`/`Config`/`Charter` の各定義を
  `Read` で直接開き、シグネチャと行番号を実測した（本報告の表はすべて実測値）。
- 完了条件コマンドを本 worktree でそのまま実行し、両方とも exit=0 を確認（コード変更なしで
  既に満たされている状態）:
  ```
  $ python3 -m pytest tools/kiro-project/tests -q -k codd
  29 passed, 579 deselected in 0.05s
  $ codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict
  OK: 一貫性ゲート通過
  ```
- `git status --short` は本タスク開始前・終了後とも差分ゼロ（調査のみ、コード変更なし）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- タスク名の `kiro_project/`（アンダースコア）パッケージは実在しないため、`tools/kiro-project/`
  （ハイフン）配下のフラットなモジュール群を「分割後構造」の実体として棚卸しした（§0 参照）。
- 完了条件コマンドは本タスク（調査のみ）の実行前から既に exit=0 で満たされており、これは前 run
  （`run-20260712-213419-5922`）と本 run の先行タスク（a1/a2/a4/b2 等）が既に
  `codd_gate_*.py` とそのテストを作成済みのため。本タスクはこの状態を変更していない。

**未解決事項**（実装タスク（今回 run の b1-b3/c1-c2/e1-e2 相当）が判断すべき点。t5 の指摘と重複する分は割愛し新規分のみ）:
- `codd_gate_status.CoddGateStatus` の計算結果をプロセス内でキャッシュする層（a3 相当）が
  `codd_gate_*.py` に見当たらない。`detect_status`/`build_status` は毎回サブプロセスを呼ぶ生実装で、
  `run_loop` の高頻度呼び出し（`_settle_task` は1タスク確定ごと）にそのまま結線すると
  `--version`/`--help` プローブが呼び出し回数分walkする。結線タスクはキャッシュ層の要否を判断する必要がある。
- regression（`_settle_task`）と acceptance（`evaluate_acceptance`）は共に `KIRO_BASE_REV` を
  「一時 clone の HEAD」から独立に注入する同型ロジックを個別実装しており（`5517-5519` と
  `9556-9561`）、`codd_gate_base.resolve_base_rev` を導入する場合はこの2箇所両方が結線対象になる。

**範囲外で見つけた問題**（直さず報告のみ）:
- 無し（本タスクの調査範囲内で新規の問題は見つからなかった。t5 が既に報告した
  `run_intake` と `cmd_enqueue` の非対称性（`apply_intake_recall`/`ensure_needs` を
  `run_intake` 経路だけ呼ばない）は再掲しない）。
