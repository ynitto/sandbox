# t7: codd-gate 自動検出 結線仕様（regression / acceptance / enqueue 統合版）

対象: `tools/kiro-project/kiro-project.py` 本体への codd-gate 自動検出・自動配線。
既存の `codd_gate_{base,detect,status,routing,invoke,debt}.py`（全6モジュール・標準ライブラリのみ・
「例外を外に投げない／不明・不足は no-op 側に倒す」で統一済み、t1〜t6 で実測・確認済み）を**結合部品**
として使い、regression（差分ゲート）・acceptance（受入判定の負債ラチェット）・enqueue（負債の修復
タスク取り込み）の3フックへの結線点・設定キー・codd-gate 未インストール時のフォールバックを1本化する。

本書は b3（regression）・c1-c2（acceptance）・e1-e2（enqueue）実装タスクが**そのまま従う契約**。
コード変更はしていない（本タスクは統合・確定のみ。完了条件コマンドは無変更のまま exit 0 を実測済み）。

---

## 0. 設計原則（3フック共通・確定事項）

1. **既存の手動フック（`cfg.regression_cmd` / charter `## acceptance` の生コマンド行 / `cfg.intake_cmd`）
   は一切変更しない。auto 配線は既存手動フックに"上乗せ"される独立した追加チェックであり、置き換えない。**
   両方設定されていれば両方とも評価される（`codd-gate-design.md` §4 の E1/E2/E3 は本来「ユーザーが手で
   codd-gate コマンドを書く」ための汎用フックとして既に機能する——今回の自動検出は「書かなくても
   自動で入る」を追加するものであり、既存の汎用フック契約自体は無改造で残す）。
2. **codd-gate 未インストール／非互換／`codd_gate_auto=False` のときは、3フックのどれも一切の
   挙動変化を起こさない**（regression: 追加ブロックなし／acceptance: `results`/`total` に何も足さない
   ／enqueue: タスクを生成しない）。`CoddGateStatus.usable=False` → 即座に何もしない、が唯一の分岐。
3. **検出は3フック共通で1回だけ**（`codd_gate_status.detect_status(explicit=cfg.codd_gate_bin)` は
   プロセス内でメモ化し、タスク／サイクルごとに再検出しない）。実装は kiro-project.py に
   `_codd_gate_status(cfg) -> CoddGateStatus` を新設し、`cfg.codd_gate_bin` をキーにキャッシュする
   （`_INTAKE_LAST` と同じ「モジュールレベル dict キャッシュ」の流儀に合わせる）。
4. **`CoddGateResult`（`codd_gate_invoke.py`）はプロセス内一過性の値オブジェクトのまま、各フックの
   呼び出し境界で必ず既存の表現へ変換し、`_block()`/`results`タプル/`enqueue_task` spec の先へは
   一切そのまま渡さない。**
   - t3 が指摘した「`(bool,str)` tuple 方式（regression_cmd 既存経路）と `CoddGateResult`（新設）の
     2つの不合格表現の並存」は、型を統一するのではなく**境界で吸収する**ことで解消する
     — `_block()`/`evaluate_acceptance` の `results` タプル/`enqueue_task` の spec dict、いずれも
     無改造のまま。`CoddGateResult` はどの永続化構造にも登場しない（自身の docstring
     「ディスクには乗らない」を全フックで守る）。
5. **既定値はすべて brownfield 安全側**（既存負債・未接続ファイルで初回から止めない）。
   `codd_gate_auto` の既定は **True**（自動検出自体は既定 on）だが、`--strict`・`--max-*` 系の
   しきい値はすべて既定「未指定＝無条件 PASS」（t2 実測どおり）。**自動配線を有効にしただけで
   既存プロジェクトが突然詰まることはない**——ゲートが実際に NG を返すのは、ユーザーが明示的に
   しきい値/strict を設定した場合のみ。

---

## 1. 設定キー（新設・確定）

`kiro-project.py` の `Config` dataclass・`CONFIG_DEFAULTS` dict・argparse（`cfg.regression_cmd`/
`cfg.intake_cmd` と同じ並びに追加）に以下を追加する。命名は既存の `regression_*`/`intake_*` の
flat snake_case 規約に合わせた。

| 設定キー | 型 | 既定値 | CLI フラグ | 用途（どのフックで使うか） |
|---|---|---|---|---|
| `codd_gate_auto` | bool | `True` | `--codd-gate-auto` / `--no-codd-gate-auto`（`BooleanOptionalAction`、`regression_revert` と同流儀） | **マスタースイッチ**。3フック共通。False で自動配線を完全無効化（手動フックのみ残る） |
| `codd_gate_bin` | `str \| None` | `None` | `--codd-gate PATH` | `detect_status(explicit=...)` へ渡す明示バイナリ/argv。3フック共通（`codd_gate_status._finding_not_found` の fix 文言が既に `--codd-gate で実体を指定する` と明記済みのため、フラグ名はこれに合わせて確定——`--codd-gate-bin` 等の別名は採らない） |
| `codd_gate_strict` | bool | `False` | `--codd-gate-strict` / `--no-codd-gate-strict` | **hook1（regression）専用**。diff モード `verify` の `--strict`。acceptance（`--debt` モード）には効かない（t2 実測：`--strict`/`--strict-cross` は diff 分類にのみ作用） |
| `codd_gate_max_broken` | `int \| None` | `None` | `--codd-gate-max-broken N` | **hook2（acceptance）専用**。`verify --debt --max-broken` |
| `codd_gate_max_undocumented` | `int \| None` | `None` | `--codd-gate-max-undocumented N` | **hook2 専用**。`verify --debt --max-undocumented` |
| `codd_gate_max_untested` | `int \| None` | `None` | `--codd-gate-max-untested N` | **hook2 専用**。`verify --debt --max-untested` |
| `codd_gate_debt_max` | int | `20`（codd-gate 自身の既定と一致） | `--codd-gate-debt-max N` | **hook3（enqueue）専用**。`tasks --debt --max` |
| `codd_gate_debt_cohort` | bool | `False` | `--codd-gate-debt-cohort` / `--no-codd-gate-debt-cohort` | **hook3 専用**。`tasks --debt --cohort` |

`codd_gate_intake_interval` のような専用キーは**新設しない**——hook3 の周期律速は既存
`cfg.intake_interval`（既定 600 秒）をそのまま流用する（intake の「どれくらいの頻度で外部を
汲み上げるか」は供給元によらず1つの概念という判断。§3 参照）。

---

## 2. hook1 — regression（差分ゲート・E2）

**結線点**: `_settle_task`（`kiro-project.py:5490`）、既存の `cfg.regression_cmd` チェック
（5524-5533行目）の**直後**（同じ `try` ブロック内、`ok and not flaky` の条件も引き継ぐ）。

```python
if ok and not flaky and cfg.regression_cmd:          # 既存・無改造
    rok, rmsg = run_verify(cfg.regression_cmd, vcwd, cfg.verify_timeout, venv)
    if not rok:
        regressed = True
        if cfg.regression_revert:
            _revert_workdir(cfg)
        _block(cfg, task, f"回帰検知: グローバル検査 `{cfg.regression_cmd}` 失敗 — {rmsg}", reasons, evidence=ev)
        autonomy_record(cfg, task, clean=False, cache=autonomy_cache)
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（回帰検知）"
                       + ("・revert 済" if cfg.regression_revert else ""))

if ok and not flaky and not regressed and cfg.codd_gate_auto:     # ← 新設・追加
    status = _codd_gate_status(cfg)
    if status.usable:
        base_rev = (venv or {}).get("KIRO_BASE_REV") or codd_gate_base.resolve_base_rev(
            _workspace_spec_for(cfg, task, ) and _workspace_spec_for(cfg, task).get("base"),
            env=os.environ,
        )
        repos_path = cfg.backlog.parent / "repos.json"
        ws_name = _strip_code(str(task.get("workspace") or "").strip())
        args = (codd_gate_routing.build_routing_args(repos_path, ws_name, vcwd) if ws_name
                else ["--repos", codd_gate_routing.resolve_repos_arg(repos_path, vcwd)])
        args += ["--base", base_rev]
        if cfg.codd_gate_strict:
            args.append("--strict")
        result = codd_gate_invoke.invoke_codd_gate(status, "verify", *args, timeout=cfg.verify_timeout)
        if result.status == "failed":
            regressed = True
            if cfg.regression_revert:
                _revert_workdir(cfg)
            _block(cfg, task, f"回帰検知: codd-gate 一貫性ゲート失敗 — {result.reason}", reasons, evidence=ev)
            autonomy_record(cfg, task, clean=False, cache=autonomy_cache)
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（codd-gate 回帰検知）"
                           + ("・revert 済" if cfg.regression_revert else ""))
        # result.status in ("ok", "skipped") → 何もしない
```

**base rev 解決の優先順位**（`codd_gate_base.resolve_base_rev` の設計どおり）:
`venv["KIRO_BASE_REV"]`（既に `_settle_task` が一時 clone の HEAD から算出済みならそれを再利用——
`cfg.regression_cmd` と全く同じ基準を使うことで、手動/自動2つのゲートが食い違う base で判定しない）
→ タスクの workspace repo の `base=`（charter repo エントリ）→ `HEAD~1`。

**repo-dir 解決**: `task.workspace` が設定されていれば `build_routing_args(repos_path, ws_name, vcwd)`
（`--repos`+`--repo-dir NAME=.`）。未設定（git-bus root で検証する通常タスク）なら `--repo-dir` は
付けず `--repos` のみ（`vcwd` が repos.json の `dir` と既に一致しているため上書き不要。
codd-gate 自身の解決順位——`--repo-dir` 指定が無ければレジストリの `dir` を使う——に委ねる）。

**フォールバック**: `status.usable=False` → 何もしない（regression チェックは `cfg.regression_cmd`
の結果のみで確定。既存挙動と完全に同一）。`result.status=="skipped"`（起動失敗・timeout 等）も同様に
無視（`regressed` を変えない）。

---

## 3. hook2 — acceptance（受入判定の負債ラチェット・codd-gate-design.md §4 の ②）

**結線点**: `evaluate_acceptance`（`kiro-project.py:9546-9572`）、既存の `## acceptance` 行ループの
**直後・return の直前**（t4 の提案どおり。`results` は `(cmd, ok, msg)` タプルのリストという
既存の型に codd-gate 項目を1件足すだけで、`_failing_acceptance_specs`/milestone/journal/収束判定は
無改造のまま正しく波及する——t4 §3.2 参照）。

```python
def evaluate_acceptance(cfg, charter):
    wd, tmp = _acceptance_cwd(cfg, charter)
    try:
        results = []
        for cmd in charter.acceptance:               # 既存ループ・無改造
            ...
            results.append((cmd, ok, msg))

        if cfg.codd_gate_auto:                        # ← 新設・追加
            status = _codd_gate_status(cfg)
            if status.usable:
                repos_path = cfg.backlog.parent / "repos.json"
                single = charter.repo_specs[0] if len(charter.repo_specs) == 1 else None
                args = (codd_gate_routing.build_routing_args(repos_path, single["name"], wd) if single
                        else ["--repos", codd_gate_routing.resolve_repos_arg(repos_path, wd)])
                args.append("--debt")
                for key, flag in (("codd_gate_max_broken", "--max-broken"),
                                   ("codd_gate_max_undocumented", "--max-undocumented"),
                                   ("codd_gate_max_untested", "--max-untested")):
                    v = getattr(cfg, key)
                    if v is not None:
                        args += [flag, str(v)]
                result = codd_gate_invoke.invoke_codd_gate(status, "verify", *args, timeout=cfg.verify_timeout)
                if result.status != "skipped":        # skipped は total に計上しない＝無害（t17 要求）
                    cmd_repr = "codd-gate verify " + " ".join(args)
                    results.append((cmd_repr, result.ok, result.reason or (result.stdout or "")[:500]))

        passed = sum(1 for _, ok, _ in results if ok)
        return passed, len(results), results
    finally:
        ...
```

**`--base` は不要**: `--debt` モードは `--base`/`--repo` を渡しても無視される（t2 実測§2.3/2.5）ため、
`codd_gate_base.resolve_base_rev` はこのフックでは呼ばない（hook1 と違い base rev 解決コードは
書かない——書いても到達しないコードになるため、実装時に混入させないことを明記する）。

**`state["acceptance_total"]` のズレを修正（t4 §3.2 の要注意点・本フック導入と不可分の修正）**:
`cmd_project`（9917行目）の `state["acceptance_total"] = len(charter.acceptance)` は
`evaluate_acceptance` 呼び出し**前**の値で固定されており、codd-gate 項目を動的に足すと
`_project_evaluate` が使う実際の `total`（`evaluate_acceptance` の返り値）と食い違う。
**この事前セットは削除し、`_project_evaluate` が `evaluate_acceptance` を呼んだ後に
`state["acceptance_total"] = total`（返り値）で上書きする1系統に統一する。** これを
hook2 の結線と同時に行わない場合、milestone/viewer 表示の `acceptance N/M` が実態とズレる
（t4 が発見した不整合をここで確定的に解消する）。

**フォールバック**: `status.usable=False` → `results`/`total` は既存の `## acceptance` 行のみで
確定（コード変更前と完全同一の受入判定）。`result.status=="skipped"` も同様に `results` へ追加しない
（total を歪めない）。

---

## 4. hook3 — enqueue（負債の修復タスク自動取り込み・E3 の auto 経路）

**結線点**: 新設関数 `run_codd_gate_intake(cfg) -> list[Task]` を `run_intake`（`kiro-project.py:502`）
の**直後に追加**し、両方の既存呼び出し箇所に並置する（`cfg.intake_cmd` の有無に関わらず独立に動く）:

- `kiro-project.py:5608`: `inboxed = run_intake(cfg) + run_codd_gate_intake(cfg) + ingest_inbox(cfg)`
- `kiro-project.py:6795`: `run_intake(cfg)` の直後に `run_codd_gate_intake(cfg)` を追加

**なぜ `cfg.intake_cmd` に codd-gate コマンド文字列を自動代入する方式にしないか**: `codd_gate_debt.py`
の docstring が「`id` を **e2 の重複投入防止キーとして直接使う想定**」「`kiro-project.py` への結線・
`cfg.intake_cmd`/`run_intake` との統合、id ベースの冪等排除は e2 の責務」と明記しており、
`codd_gate_invoke`/`codd_gate_debt` は**構造化オブジェクトとして直接消費される**設計で作られている
（`run_intake` の「シェル文字列→subprocess→JSON.loads」経路を codd-gate 側にも流用する薄い代替案は、
既存モジュール群の設計意図と食い違うため採らない——本書はこの矛盾を、モジュールの docstring を正として
解消した）。一方で `run_intake` の**冪等排除ロジック（id ベース dedup・interval 律速・journal 記録）は
正しい先例**なので、そのアルゴリズムを別関数として複製する（t5 が指摘した「CLI enqueue/inbox 経路には
dedup が効かない」問題を、codd-gate 自動経路では**再現しない**——`run_intake` 由来の id dedup 方式を踏襲する）。

```python
_CODD_GATE_INTAKE_LAST: "dict[str, float]" = {}

def run_codd_gate_intake(cfg: "Config") -> "list[Task]":
    if not cfg.codd_gate_auto:
        return []
    interval = float(cfg.intake_interval or 0)          # cfg.intake_interval を流用（専用キーは新設しない）
    key = str(cfg.backlog)
    now = time.time()
    if interval > 0 and now - _CODD_GATE_INTAKE_LAST.get(key, 0.0) < interval:
        return []
    _CODD_GATE_INTAKE_LAST[key] = now
    status = _codd_gate_status(cfg)
    if not status.usable:
        return []
    repos_path = cfg.backlog.parent / "repos.json"
    args = ["--repos", codd_gate_routing.resolve_repos_arg(repos_path, cfg.workdir),
            "--debt", "--max", str(cfg.codd_gate_debt_max)]
    if cfg.codd_gate_debt_cohort:
        args.append("--cohort")
    result = codd_gate_invoke.invoke_codd_gate(status, "tasks", *args, timeout=cfg.verify_timeout)
    if result.status == "failed":
        append_journal(cfg.journal, f"codd-gate intake NG: {result.reason}")
        return []
    if result.status == "skipped":
        return []
    parsed = codd_gate_debt.parse_debt_output(result.stdout)
    for err in parsed.errors:
        append_journal(cfg.journal, f"codd-gate intake レコード無視: {err}")
    created: "list[Task]" = []
    existing = {f.stem for f in cfg.backlog.glob("*.md")} if cfg.backlog.exists() else set()
    for item in parsed.items:
        spec = item.to_spec()
        sid = _slug_id(str(spec.get("id", "") or ""))
        if sid and sid in existing:                     # run_intake と同じ id ベース冪等排除
            continue
        try:
            created.append(enqueue_task(cfg, spec))
        except ValueError as e:
            append_journal(cfg.journal, f"codd-gate intake spec 無効: {e}")
            continue
        if sid:
            existing.add(sid)
    if created:
        append_journal(cfg.journal, f"codd-gate intake 取り込み {[t.id for t in created]}")
    return created
```

**`--repo-dir` は付けない**: enqueue/intake はプロジェクト全体（repos.json の全エントリ）の負債を
横断棚卸しする用途であり、特定タスクの1 repo に絞る hook1/hook2 とは性質が違う——`--repos` のみを渡し、
codd-gate 自身のマルチリポジトリ走査に委ねる。

**`--base` も不要**（hook2 と同じ理由。`--debt` モードは無視する）。

**フォールバック**: `status.usable=False` → `run_codd_gate_intake` は空リストを返すだけ（`run_intake`
の結果・`ingest_inbox` の結果には一切影響しない）。`result.status` が `"failed"`/`"skipped"` の場合も
journal に一言残すだけでタスクは1件も積まない（ループを止めない・`run_intake` の「有限・無害」原則を
そのまま踏襲）。

---

## 5. 検出のキャッシュ（3フック共通の新設ヘルパー）

```python
_CODD_GATE_STATUS_CACHE: "dict[str | None, object]" = {}   # object = codd_gate_status.CoddGateStatus

def _codd_gate_status(cfg: "Config"):
    key = cfg.codd_gate_bin
    if key not in _CODD_GATE_STATUS_CACHE:
        _CODD_GATE_STATUS_CACHE[key] = codd_gate_status.detect_status(explicit=cfg.codd_gate_bin)
    return _CODD_GATE_STATUS_CACHE[key]
```

`cfg.codd_gate_bin` をキーにする（`--project all` で複数プロジェクトが同一プロセス内に多重化されても、
明示バイナリ指定が異なれば別キャッシュになる。通常は `None` 1本のみ）。`detect_status` は現状バージョン
/schema 互換判定を合流させていない（t1 が指摘した既知の gap）ため、実体としては `shutil.which` 相当の
軽い呼び出し1回だが、タスク/サイクル単位で繰り返さないという方針自体は3フック共通で固定する。

---

## 6. 未インストール時のフォールバック挙動（1文で確定）

**`codd-gate` が未検出・非互換、または `cfg.codd_gate_auto=False` のとき、regression/acceptance/enqueue
の3フックはいずれも「codd-gate 導入前と完全に同一の挙動」に縮退し、`_block()`・`results`・
`enqueue_task` のいずれにも一切の副作用を残さない。** 唯一の分岐点は `CoddGateStatus.usable`
（3フック共通）と `CoddGateResult.status != "skipped"`（hook2 の total 計上／hook1 の regressed 判定
／hook3 のタスク生成、それぞれの直前ガード）であり、これ以外の場所に codd-gate 由来の分岐を増やさない。

---

## 7. t1〜t6 の成果に対して行った矛盾・欠落の解消（明記）

- **t3 の「2つの不合格表現の並存」**: 型を統一するのではなく、`CoddGateResult` を各フックの呼び出し
  境界でのみ消費し既存表現へ変換する、という**境界での吸収**として解消した（§0-4, §2）。
- **t4 の `state["acceptance_total"]` ズレ**: hook2 の結線と不可分の修正として、事前セットの削除＋
  `_project_evaluate` 側での上書きに一本化することを明記した（§3）。t4 は「t16 実装時の課題」として
  申し送りしていたが、本書では hook2 の契約に含めて確定させた（先送りにすると総数不整合が初回から
  発生するため、切り離せないと判断）。
- **t4 の `KIRO_BASE_REV` 注入欠落**: hook1 では `codd_gate_base.resolve_base_rev` で確実に埋める
  設計にした。hook2/hook3 は `--debt` モードのため base rev 自体が不要と判明（t2 の実測）——
  t4 が「acceptance 側の codd-gate 項目も resolve_base_rev を経由すべき」と提案していた箇所は、
  実際には到達しないコードになると判断し、あえて実装しない（作らないことで矛盾を解消）。
- **t5 の dedup 不整合（CLI/inbox 経路は id dedup が効かない）**: codd-gate 自動 intake 経路
  （hook3）はこの不整合を継承せず、`run_intake` の id dedup を踏襲する設計にした（§4）。
  CLI `enqueue --id`/inbox の改名挙動そのものは本書のスコープ外（別タスクの判断のまま）。
- **t1 の「`codd_gate_invoke.py` は 38f99cac 対象外」という前提**: t1 は一覧化対象を意図的に
  5ファイルに絞ったが、本書では `codd_gate_invoke.py`（t9 相当・commit `6224bd1`）を含む
  **6モジュール全体**を結線対象として扱う（t1 自身も「範囲外だが結線タスクの対象になる」と明記済み
  で矛盾はない。読み手が誤って5ファイルだけを結線対象と誤解しないよう本書で明記）。
- **設計書との突き合わせ**: `docs/designs/codd-gate-design.md` §4（E1/E2/E3 の対応表）・
  `docs/designs/kiro-project-design.md` §4.1（フック契約カタログ）を正典として直接確認し、
  t1〜t6 のいずれの記述とも矛盾がないことを検証した（t1〜t6 はいずれもコード実測に基づき正確だった
  ——本書が新たに追加した決定は「自動検出をどの粒度で3フックへ落とすか」という、t1〜t6 が
  明示的に範囲外としていた設計判断のみ）。

## 8. 検証

- `python3 -m pytest tools/kiro-project/tests -q -k codd` → `47 passed, 579 deselected, 3 subtests passed`（exit 0）
- `codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict` → `OK: 一貫性ゲート通過`（exit 0）
- `git status --short` はクリーン（本タスクはコード変更を行っていない。統合仕様の確定のみ）。

## 9. 前提・範囲外（本書が確定しなかったこと）

- **前提**: 本 run の依存タスク（t1〜t6）はいずれも調査専任で worktree 変更なし、完了条件は
  既に別タスクの成果でグリーンだった。本タスクも同様に調査・統合のみとし、コード変更は行っていない
  （実装は後続の b3/c1-c2/e1-e2 相当タスクの責務）。
- **範囲外**: `codd_gate_status.detect_status` のバージョン/schema 互換判定の合流（t1 が指摘した
  既知の gap。a2/a4 相当）、`codd_gate_status.py`/`codd_gate_base.py`/`codd_gate_debt.py` の単体
  テスト追加（t3/t6 が指摘。`test_codd_gate_status.py`/`test_codd_gate_base.py`/
  `test_codd_gate_debt.py` は未着手）、CLI `enqueue --id`/inbox 経路の dedup 不整合（t5 指摘、
  ドキュメンテーションバグの可能性）——いずれも本書の結線仕様とは独立に解消可能なため、後続タスクの
  判断に委ねる。
