# codd-gate 結線インターフェース仕様（regression / acceptance / enqueue 共通）

対象読者: t9（ヘルパ実装）・t10（regression 結線）・t11（acceptance 結線）・t12（enqueue 結線）。
本書は t1・t2・t3・t5・t6・t7 の成果と、既存実装（`codd_gate_status.py`/`codd_gate_base.py`/
`codd_gate_routing.py`/`codd_gate_invoke.py`/`codd_gate_debt.py`、いずれも `tools/kiro-project/`
直下）・`kiro-project.py` 本体（`_settle_task`/`cmd_approve`/`run_intake`）・codd-gate 本体
（`tools/codd-gate/codd-gate.py`）を突き合わせて確定した、後続タスクが機械的に従うべき唯一の契約。
本タスクは仕様統合のみで作業ツリーへの変更は行っていない。

---

## 1. ヘルパの配置ファイル

**新設: `tools/kiro-project/codd_gate_hooks.py`**（t9 が実装）。既存の `codd_gate_base.py`/
`codd_gate_routing.py` と同じ設計方針を踏襲する:

- `kiro-project.py` の型（`Config`/`Task`/`Charter`）に依存しない。呼び出し側（t10-t12）が
  `cfg`/`task`/`ch` から取り出した**プリミティブ値**（str/Path/dict）を渡す。
- 依存は標準ライブラリと同梱の `codd_gate_status`/`codd_gate_base`/`codd_gate_routing`/
  `codd_gate_invoke`/`codd_gate_debt` のみ（他モジュールと同じフラット import 規約:
  `from codd_gate_status import ...` — 同一ディレクトリ内解決）。
- ファイル I/O は一切行わない（後述 4節）。

このモジュールは4つの既存モジュールを合成する「合流点」であり、regression/acceptance/enqueue
の3フックは**この1ファイルの2公開関数だけ**を呼べばよい。3フック個別に
`detect_status → resolve_base_rev → build_routing_args → invoke_codd_gate` の合成を書かない
（t7 原則1「検出ロジックの重複実装・迂回を行わない」を合成ロジックそのものにも適用する）。

---

## 2. 公開関数のシグネチャと戻り値型

### 2.1 `run_diff_gate` — regression（t10）・acceptance（t11）共通

両フックとも `codd-gate verify --strict` を1回実行して pass/fail を得るだけの同一操作であり
（t4・t5 いずれも `(ok: bool, msg: str)` 形式を前提にしている）、**1関数を2箇所から呼ぶ**。

```python
def run_diff_gate(
    repos_path: "str | Path",
    name: str,
    vcwd: "str | Path",
    task_base_branch: "str | None" = None,
    *,
    status: "CoddGateStatus | None" = None,
    codd_gate_bin: "str | None" = None,
    dir: str = DEFAULT_REPO_DIR,          # codd_gate_routing.DEFAULT_REPO_DIR 再エクスポート
    env: "dict[str, str] | None" = None,
    which=shutil.which,
    run=subprocess.run,
    timeout: float = DEFAULT_TIMEOUT,     # codd_gate_invoke.DEFAULT_TIMEOUT 再エクスポート
) -> "tuple[bool, str]":
    ...
```

引数の出所（呼び出し側 t10/t11 が用意する値）:
- `repos_path`: `repo_registry_path(cfg)` の結果（`Path`）。
- `name`: 対象ワークスペースの登録名（`task.get("workspace")` が指す repo spec の `name`。
  完了条件コマンドの `--repo-dir sandbox=.` の `sandbox` に相当）。
- `vcwd`: `_task_verify_cwd(cfg, task)`（`kiro-project.py:3098`）の第1戻り値。
- `task_base_branch`: `charter_repo_spec_map(ch).get(task.get("workspace"), {}).get("base")`。
- `status`: 呼び出し側が `CoddGateStatus` をプロセス内キャッシュ済みなら渡す。省略時は本関数が
  内部で `detect_status(explicit=codd_gate_bin, which=which, run=run)` を1回実行する
  （3節「キャッシュとの関係」参照）。

実装（合成の中身。t9 はこの通りに実装する）:

```python
def run_diff_gate(repos_path, name, vcwd, task_base_branch=None, *, status=None,
                   codd_gate_bin=None, dir=DEFAULT_REPO_DIR, env=None,
                   which=shutil.which, run=subprocess.run, timeout=DEFAULT_TIMEOUT):
    if status is None:
        status = detect_status(explicit=codd_gate_bin, which=which, run=run)
    if not status.usable:
        return True, ""                                          # no-op（未検出・非互換）
    base_rev = resolve_base_rev(task_base_branch, env=env)
    routing = build_routing_args(repos_path, name, vcwd, dir)
    result = invoke_codd_gate(status, "verify", *routing, "--base", base_rev, "--strict",
                               run=run, timeout=timeout)
    if result.status == "skipped":
        return True, f"codd-gate: {result.reason}"                # usable だが実行時に縮退
    if result.status == "failed":
        return False, f"codd-gate: {result.reason}"               # 本物のゲート失敗
    return True, ""                                                # "ok"
```

### 2.2 `collect_debt_specs` — enqueue（t12）

`codd-gate tasks --debt` は差分ゲートと異なり `--base` を取らない（t3 1-B節: 全体負債の棚卸しで
差分基準が不要）。戻り値も pass/fail ではなく `run_intake`/`enqueue_task` へそのまま渡せる
spec dict のリストであり、`run_diff_gate` とはドメインが異なるため別関数にする
（「戻り値の型」が bool/list で異なる以上、無理に1関数へ統合しない）。

```python
def collect_debt_specs(
    repos_path: "str | Path",
    name: str,
    vcwd: "str | Path",
    *,
    status: "CoddGateStatus | None" = None,
    codd_gate_bin: "str | None" = None,
    dir: str = DEFAULT_REPO_DIR,
    which=shutil.which,
    run=subprocess.run,
    timeout: float = DEFAULT_TIMEOUT,
) -> "tuple[list[dict], str]":
    ...
```

実装:

```python
def collect_debt_specs(repos_path, name, vcwd, *, status=None, codd_gate_bin=None,
                        dir=DEFAULT_REPO_DIR, which=shutil.which, run=subprocess.run,
                        timeout=DEFAULT_TIMEOUT):
    if status is None:
        status = detect_status(explicit=codd_gate_bin, which=which, run=run)
    if not status.usable:
        return [], ""                                              # no-op（未検出・非互換）
    routing = build_routing_args(repos_path, name, vcwd, dir)
    result = invoke_codd_gate(status, "tasks", "--debt", *routing, run=run, timeout=timeout)
    if result.status != "ok":
        return [], f"codd-gate: {result.reason}"                   # skipped/failed とも空リストへ縮退
    parsed = parse_debt_output(result.stdout)
    specs = [item.to_spec() for item in parsed.items]
    return specs, "; ".join(parsed.errors)                         # errors はレコード単位の棄却理由
```

`DriftItem.to_spec()`（`codd_gate_debt.py:45-51`）は既に `schemas/task.schema.json` 準拠の
`{"title": ..., "id": ..., ...}` dict を返す設計であり、変換は不要——`collect_debt_specs` は
プロセス起動とパースを合成するだけで、spec の形自体には手を加えない。

各 spec の `id` は codd-gate 側の `_task_id()`（`tools/codd-gate/codd-gate.py:737-744`、
sha1 先頭6桁で決定的に生成）が全レコードに必ず付与する。**t6 が範囲外所見として挙げた
「`run_intake` の重複判定は `id` が空だと機能しない」という懸念は、codd-gate が発行する id が
常に非空・決定的であるため実害がないことを本タスクで確認した**（`tasks_from_debt`/
`tasks_from_impact` の全 `out.append(...)` が例外なく `"id": _task_id(...)` を含む。t6 の
時点では調査範囲外だったため未解決事項とされていたが、本統合で解消する）。

---

## 3. no-op 時の戻り値（確定表）

t7 の三原則（skipped は既存挙動を変えない）をそのまま数値化する。**usable=False（未検出・非互換）
は無音の no-op、usable=True だが実行時に縮退した場合のみ理由文字列を添えて可視化する**——
codd-gate 未導入環境（大半のユーザー）で regression/acceptance のログに毎回ノイズが出るのを避けつつ、
導入済み環境でのタイムアウト等の実行時異常は journal で追えるようにするための意図的な非対称。

| 状況 | `run_diff_gate` | `collect_debt_specs` |
|---|---|---|
| `status.usable == False`（未検出・非互換） | `(True, "")` | `([], "")` |
| usable だが `invoke_codd_gate` が `"skipped"`（起動失敗・timeout） | `(True, "codd-gate: <reason>")` | `([], "codd-gate: <reason>")` |
| `"failed"`（verify のドリフト検知 / tasks 側のツールエラー） | `(False, "codd-gate: <reason>")` | `([], "codd-gate: <reason>")` |
| `"ok"` | `(True, "")` | `(specs, "; ".join(parse_debt_output の errors))` |

呼び出し側（t10-t12）の合流方法:
- regression: `rok = rok and codd_ok`／`rmsg = "; ".join(filter(None, [rmsg, codd_msg]))`
  （t4 の指定通り、`rok`/`regressed` へ論理積で合流）。
- acceptance: `mr_ok = mr_ok and codd_ok`／`mr_msg = "; ".join(filter(None, [mr_msg, codd_msg]))`
  （t5 の指定通り、`finalize_task_mr` の戻り値へ合流。`"codd-gate: "` 接頭辞により、差し戻し理由
  文面上で GitLab MR 由来と codd-gate 由来を文字列レベルで区別できる——t5 が t14 へ持ち越した
  「区別すべきか未確定」という論点に対する最小限の答えを本統合で用意した）。
- enqueue: `collect_debt_specs` が返す `specs` を、`run_intake` が `intake_cmd` の stdout から
  得る `data` と同じ配列として扱い、**既存の id 完全一致重複判定（`kiro-project.py:538-551`）に
  そのまま通す**。`collect_debt_specs` 自身は重複判定を持たない（enqueue 経路の冪等性は
  `run_intake` 側の責務のまま——`codd_gate_debt.py` の docstring が明記する境界を踏襲）。

**キャッシュとの関係**: `codd_gate_status.py` の docstring が「プロセス内キャッシュは a3 の責務」
として意図的に対象外とした通り、`run_diff_gate`/`collect_debt_specs` も自前でキャッシュしない。
`status` 引数を省略すれば毎呼び出しで `detect_status` を再実行する（正しいが `--version`/`--help`
プローブ分のオーバーヘッドが3フック分＝最大 task 1件あたり数回発生する）。t10-t12 が
`cfg` に `CoddGateStatus` を1回だけ計算してキャッシュする仕組みを追加するならこの `status`
引数へ注入すればよく、本モジュール側の変更は不要——これが `status` 引数を用意した理由。
現時点でこのキャッシュ機構自体は未実装（今回のスコープ外、必要なら別タスク）。

---

## 4. repos.json は読み取り専用で書き換えない

`codd_gate_hooks.py` は**ファイルへの書き込みを一切行わない**。根拠:

- `run_diff_gate`/`collect_debt_specs` はいずれも `repos_path` を**文字列/Pathとして受け取り、
  `build_routing_args()`（`codd_gate_routing.py`、既存・純粋関数）へ渡すだけ**。同関数は
  `Path.resolve()`/`relative_to()` で読み取り専用の path 演算のみ行い、`open`/`write_text` を
  一切呼ばない（`codd_gate_routing.py` 全文で `write` の出現なしを確認済み）。
- repos.json の**書き込み**は既存の `export_repo_registry(cfg, specs, path=None)`
  （`kiro-project.py:8519-8541`）が唯一の書き手であり、charter の `## repos` から
  `_meta.generated_from` 付きで再生成する専用経路。`codd_gate_hooks.py`・t9-t12 のいずれも
  この関数を呼ばない／代替実装を作らない。
- 呼び出し側（t10-t12）が渡す `repos_path` は `repo_registry_path(cfg)`
  （`kiro-project.py:8475-8481`、`<project>/repos.{yaml,yml,json}` を探すだけの読み取り関数）
  の戻り値をそのまま使う。t9 は `repo_registry_path` の呼び出しも `codd_gate_hooks.py` に
  持ち込まない（`cfg` 依存を避ける設計方針と表裏一体——`cfg` を受け取らない以上、
  `export_repo_registry` を誤って呼びようがない）。
- codd-gate 本体プロセス（別プロセス）が `--repos` で受け取った repos.json を内部でどう読むかは
  `codd_gate_hooks.py` の関知するところではないが、codd-gate 側も verify/tasks サブコマンドで
  repos.json への書き込みは行わない（読み取り専用ツールとして設計されている。
  `tools/codd-gate/codd-gate.py` の repos.json 相当パスへの `write_text` 呼び出しは
  `scan`/`sync` 系サブコマンド専用のマップキャッシュ（`.codd-gate/map.json`）向けのみで、
  `--repos` 引数で渡されるファイル自体は読み込み専用）。

---

## 5. t1〜t7 の記述の矛盾・重複・欠落と本統合での解消

- **矛盾なし**: t1（detect 契約）・t2（invoke 契約）・t3（verify スキーマ）・t7（no-op 方針）は
  相互に整合しており、既存実装（`codd_gate_status.py` 等）とも一致することを各タスクが個別に
  裏取り済み。本統合でも再確認し、齟齬は見つからなかった。
- **重複**: t5・t6 はいずれも「タスク文面が指すファイル（`mr.py`/`model.py`）が実体不在で
  `kiro-project.py` 単一ファイルに読み替えた」という同型の前提訂正を独立に行っている。
  本書ではこの前提を追認し、`codd_gate_hooks.py` も同じ単一ファイル実装（`kiro-project.py`）を
  結線先として設計した。
- **欠落として指摘されていたが本統合で解消した点**:
  - t6「`run_intake` の id 空欄時の重複判定すり抜け」→ 2節で確認した通り、codd-gate は
    `_task_id()` で全 debt/impact タスクに非空 id を必ず付与するため実害なし。
  - t5「codd-gate 由来と GitLab MR 由来の差し戻し理由をどう区別するか（t14 へ持ち越し）」→
    3節の `"codd-gate: "` 接頭辞規約で、少なくとも文字列レベルの区別手段を用意した
    （構造化が必要なら t14 でさらに詰める余地は残す）。
- **本統合の範囲外として残す欠落**（t9-t12 では解決しない・後続へ申し送り）:
  1. **完了条件コマンドの `grep -rq "codd_gate" tools/kiro-project/kiro_project/` は
     ディレクトリ自体が存在しないため、モジュール配置を変えない限り恒久的に失敗する**
     （t3・t4・t5・t6 が独立に発見・報告済み、本統合でも再確認）。`codd_gate_*.py` 群は
     `tools/kiro-project/` 直下にフラット配置する設計で一貫しており（本書もこれを追認）、
     `kiro_project/` パッケージ化はどのタスク（a1-a4/t1-t7/本書）にも指示されていない。
     この不一致は run 全体の完了条件設定側の問題であり、コード側の対応では解消できない
     ——t9-t12 が全て実装を終えても `grep tools/kiro-project/kiro_project/` は失敗し続ける。
     kiro-flow オーケストレータ側で完了条件のパスを `tools/kiro-project/` に修正するか、
     互換シムとして空の `tools/kiro-project/kiro_project/__init__.py` を置くかの判断が必要
     （後者は実体のない空パッケージを作るだけの欺瞞的対応になりかねず非推奨。前者を推奨する）。
     **本タスクの担当範囲外のため、ここでは是正せず報告のみに留める。**
  2. t5「`ingest_feedback()` 経由の代替決着経路には codd-gate が及ばない」非対称は t13/t14 の
     設計判断に委ねる（本書では `cmd_approve()` 経路のみを結線対象と確定し、この非対称の解消は
     スコープ外とする）。

---

## 6. 検証内容と結果

- 依存成果物 t1・t2・t3・t5・t6・t7（本文中に全文引用済み）と、既存実装 `codd_gate_status.py`・
  `codd_gate_base.py`・`codd_gate_routing.py`・`codd_gate_invoke.py`・`codd_gate_debt.py`
  （全5ファイル、tools/kiro-project 直下）を全文読了。
- `tools/kiro-project/kiro-project.py` の `repo_registry_path`/`export_repo_registry`/
  `charter_repo_spec_map`/`_task_verify_cwd`/`Config`（`regression_cmd`/`intake_cmd` 定義）を
  読み、`cfg.codd_gate_bin` 等の codd-gate 専用 Config フィールドが**現時点で1つも存在しない**
  こと（`grep -n "codd_gate" kiro-project.py` が0件）を確認した——t9-t12 が Config フィールド
  新設からの結線を担うことになる。本書はそのフィールド名を規定しないが、`codd_gate_hooks.py`
  の関数群が `cfg` に依存しないプリミティブ引数設計であるため、Config フィールド名の決定は
  呼び出し側の自由度として残しており、本仕様と衝突しない。
- `tools/codd-gate/codd-gate.py` の `_run`/`_emit_tasks`/`tasks_from_debt`/`tasks_from_impact`/
  `_task_id` を読み、`tasks --debt` が `--base` 不要・常に exit 0（`_emit_tasks` が無条件
  `return 0`）であること、全タスク spec に決定的 `id` が付与されることを実装レベルで確認した。
- 本タスクはコード変更を行っていない（`git status --short` で作業ツリー無変更を確認）。
  `python3 -m pytest tools/kiro-project/tests -q -k codd` は t9 未着手のため実行対象外
  （既存50件は従来通り通る想定だが、`codd_gate_hooks.py` 自体のテストは無いため件数は変わらない）。

## 前提・未解決事項

- **前提**: t8 のタスク文面「3フック共通の結線インターフェース仕様を確定する」を、
  ヘルパモジュールの配置・関数シグネチャ・戻り値型・no-op 値・repos.json 不可侵の5点を
  機械的に一意に決める設計文書の確定と解釈した。t9-t12 のコード実装そのものは行っていない
  （「担当は上記タスクのみ」の指示に従い、全体のやり直しはしていない）。
- **未解決事項**（後続タスクへ申し送り。上記5節に集約済み）:
  1. 完了条件コマンドの `grep` パス不一致（run 設定側の是正が必要、コード対応では解消不可）。
  2. `ingest_feedback()` 経由の代替決着経路への codd-gate 未結線（t13/t14 の設計判断）。
  3. `CoddGateStatus` のプロセス内キャッシュ（a3 相当）は未実装。`status` 引数を用意したので
     将来追加しても本仕様との後方互換は保たれるが、現時点の t9 実装では省略してよい
     （呼び出しごとに `detect_status` を再実行する単純な実装で完了条件は満たせる）。
