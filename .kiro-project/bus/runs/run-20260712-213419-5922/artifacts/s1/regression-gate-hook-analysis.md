# kiro-project の回帰ゲート（regression_cmd）実装調査

対象: `tools/kiro-project/kiro-project.py`（他ファイルに regression/回帰関連の実装なし。
`tools/codd-gate/` は別ツールで、kiro-project からは未結線＝下記「codd-gate との関係」参照）。

## 1. 結論サマリー

kiro-project の「差分ゲート相当」は、専用の抽象化を持たない **汎用シェルコマンドフック**
`Config.regression_cmd` である。task 自身の `verify` が PASS した直後、done 確定前に
`_settle_task()` 内で 1 回だけ同期的に `run_verify()` を呼び、失敗したら即座に `_block()` で
人の判断（needs）へ回す。リトライ・flaky 判定は一切なく、コマンドが 0 以外を返した瞬間に確定で
ブロックする「壊れたら即止める」設計。codd-gate 固有の知識は無く、`regression_cmd` に
`codd-gate verify ...` を設定するだけで結線できる想定のプラグインポイント。

## 2. フック関数のシグネチャ

### 2.1 コア実行関数（regression_cmd はこれをそのまま呼ぶ。専用ラッパーは無い）

```python
def run_verify(cmd: str, workdir: Path, timeout: float,
               env: "dict | None" = None) -> "tuple[bool, str]"
```
kiro-project.py:2702

- `cmd` が空文字なら即 `(False, "verify 未定義...")`。
- `subprocess.run(cmd, shell=True, cwd=workdir, timeout=timeout, capture_output=True, text=True, env=...)`。
- `TimeoutExpired` を捕捉し `(False, "verify タイムアウト（{timeout}s）")` を返す（例外は外に漏らさない）。
- 戻り値は `(returncode == 0, "exit={rc} {stdout末尾400+stderr末尾400}"[:500])`。

比較対象として task 本体の `verify` が使う `run_verify_stable()`（kiro-project.py:2715）は
`confirm` 回まで再実行して PASS/FAIL の揺れ（flaky）を検知するが、**regression_cmd は
`run_verify()` を直接1回呼ぶだけで `run_verify_stable()` を経由しない**（後述 4.3）。

### 2.2 呼び出し箇所（regression_cmd 専用の関数は存在せず、呼び出しはインライン）

kiro-project.py:4940-4950（`_settle_task` 内）:
```python
if ok and not flaky and cfg.regression_cmd:    # done 確定前のグローバル回帰ゲート（巻き込み事故）
    rok, rmsg = run_verify(cfg.regression_cmd, vcwd, cfg.verify_timeout, venv)
    if not rok:
        regressed = True
        if cfg.regression_revert:
            _revert_workdir(cfg)
        _block(cfg, task, f"回帰検知: グローバル検査 `{cfg.regression_cmd}` 失敗 — {rmsg}", reasons,
               evidence=ev)
        autonomy_record(cfg, task, clean=False, cache=autonomy_cache)
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（回帰検知）"
                       + ("・revert 済" if cfg.regression_revert else ""))
```

### 2.3 設定の入出力口

- `Config` dataclass フィールド（kiro-project.py:4061-4062）:
  `regression_cmd: str | None = None`, `regression_revert: bool = False`
- `CONFIG_DEFAULTS`（kiro-project.py:9635-9636）に同キーで既定値を登録
  （設定ファイル `project.json`/`kiro-flow.yaml` からの上書きに使われる汎用マージループ、9713 行）。
- CLI 引数（kiro-project.py:9961-9964）:
  `--regression-cmd <str>`、`--regression-revert`/`--no-regression-revert`
  （`argparse.BooleanOptionalAction`、既定 `None`＝未指定→ config/既定値に委譲）。
- `Config` 構築時の解決（kiro-project.py:9798-9799）:
  `regression_cmd=getattr(args, "regression_cmd", None)`,
  `regression_revert=bool(getattr(args, "regression_revert", False))`
  （CLI 明示 > 設定ファイル > 既定 None/False の優先順位は 9713 行の汎用 CONFIG_DEFAULTS マージに従う）。

現状、`regression_cmd`/`regression_revert` を自動設定する「codd-gate 自動検出」ロジックは
**コード中に存在しない**（`grep -i "auto.*detect\|codd" kiro-project.py` で該当なし）。値は常に
人が CLI/設定ファイルで明示しないと空のまま＝回帰ゲート自体が無効。

## 3. 呼び出し元（コールグラフ）

```
run_loop()                         kiro-project.py:5950-
  └─ 通常サイクル (act 直後)        kiro-project.py:6045
       res = _settle_task(cfg, task, location, act_msg, cycle, dtok, dusd,
                          git_base, verify_env, policy, autonomy_cache, reasons)

_flow_settle_offloaded 相当の再入（offload 完了ポーリング）  kiro-project.py:5938
       res = _settle_task(cfg, task, loc, msg, cycle0+settled+1, dtok, dusd,
                          gb, venv, policy, autonomy_cache, reasons)

_settle_task()                     kiro-project.py:4906-
  └─ vcwd, vtmp = _task_verify_cwd(cfg, task)      # 2782 行
  └─ ok, flaky, vmsg = run_verify_stable(task.verify, vcwd, ...)   # task 本体の verify
  └─ if ok and not flaky and cfg.regression_cmd:
         rok, rmsg = run_verify(cfg.regression_cmd, vcwd, cfg.verify_timeout, venv)  # ここが回帰ゲート
         ├─ _block(cfg, task, ..., reasons, evidence=ev)     # 4370 行
         │    ├─ task.status = "blocked"; persist_task()
         │    ├─ write_needs_file(cfg, task, reason, evidence=evidence)   # 1713 行
         │    └─ release_claim(cfg, task)                                # 4507 行
         ├─ _revert_workdir(cfg)（regression_revert=True の時のみ）       # 4378 行
         └─ autonomy_record(cfg, task, clean=False, cache=autonomy_cache) # 708 行
```

呼び出し元は 2 箇所のみで、いずれも `_settle_task()` を経由する。`_settle_task()` の外から
直接 `run_verify(cfg.regression_cmd, ...)` を呼ぶコードは他に無い。

## 4. 戻り値の扱い

### 4.1 `run_verify()` の戻り値 `(rok: bool, rmsg: str)`
- `rok`（`bool`）: 呼び出し側で `if not rok:` の分岐にのみ使われる。`rok=True` の場合は
  `regressed` フラグを立てないだけで、戻り値自体は捨てられる（成功時のログ・journal 記録は無し）。
- `rmsg`（`str`, stdout/stderr 末尾を含む最大500文字）: 失敗理由として `_block()` の `reason`
  文字列に埋め込まれ、そのまま `needs` ファイル（人が読む判断材料）に載る。

### 4.2 後段カスケードとの関係（`regressed` フラグの二重使用）
回帰チェック自体はこの try ブロック内で完結して `_block()` まで呼んでしまうため、後段の
flaky/no-progress/undiscriminating 判定カスケード（kiro-project.py:4980-)では
```python
elif regressed:
    pass   # 既に blocked 化済み。done/review にしない
```
と、二重ブロックを避けるためのガードとしてのみ再利用される。`changed`（差分検出）・
`no_progress`・`undiscriminating` の算出条件にも `not regressed` が入っており、回帰検知が
発生すると以降の偽 done 検査（no-progress・red-green）は評価自体がスキップされる。

### 4.3 task 本体の verify との非対称性
task 本体の `verify` は `run_verify_stable()`（`cfg.verify_confirm` 回まで再実行し
PASS/FAIL の揺れを flaky として隔離）を通るが、`regression_cmd` は素の `run_verify()` を
**1回のみ**呼ぶ。つまり回帰ゲートには flaky 再確認の仕組みが無く、1回の失敗で即ブロックする
（回帰ゲートを不安定なコマンドにすると誤ブロックのリスクが高い、という設計上の非対称）。

## 5. 失敗時の挙動

1. `task.status` を `"blocked"` に変更し `persist_task()` で永続化。
2. `write_needs_file()` で人向けの needs ファイルを生成（reason・evidence を含む）。
3. `release_claim()` で実行権（claim）を解放＝他 worker が触れる状態に戻す。
4. `cfg.regression_revert=True` の場合のみ `_revert_workdir(cfg)` を実行：
   `git -C cfg.workdir checkout -- .` と `git -C cfg.workdir clean -fd`（**未コミットの作業ツリー
   変更のみが対象。コミット/push 済みの変更は対象外**、docstring に明記）。既定は `False`（何もしない）。
5. `autonomy_record(cfg, task, clean=False, ...)` で `- track:` 別の自律レベルを手戻りとして
   記録し、閾値超で降格・2回でピン留め（`auto_level` 有効時のみ）。
6. journal に `"cycle {n}: {task.id} → 人の判断（回帰検知）"`（revert 済みなら注記追加）を追記。
7. **リトライ経路は無い** — 通常の verify NG（`max_retries` まで積み直し）とは異なり、回帰検知は
   一発で needs（人の判断）に落ちる。人が hold/approve/軌道修正するまで自動では進まない。
8. done/review には決してならない（4-6 が実行された時点で `regressed=True` が確定するため）。

### 5.1 前提条件（ゲートが評価される条件）
`ok and not flaky and cfg.regression_cmd` の3条件が全て真の場合のみ評価される。つまり
task 自身の `verify` が失敗している場合や flaky と判定された場合は、回帰ゲートは**そもそも
実行されない**（通常の NG/flaky 経路が優先し、そちらで別途ブロック/リトライが処理される）。

## 6. テストによる裏付け

`tools/kiro-project/tests/test_kiro_project.py`:
- `test_regression_gate_blocks_on_failure`（3383行）: `regression_cmd="false"` →
  `done=0, blocked=1` を確認。
- `test_regression_gate_passes`（3392行）: `regression_cmd="true"` → `done=1` を確認。
- `test_demote_then_pin_on_rework`（714行）: `regression_cmd="false"` を使い、回帰失敗が
  `autonomy_record(clean=False)` 経由で track の降格→2回目でピン留めに繋がることを確認。

## 7. codd-gate との関係（全体文脈への接続）

- `regression_cmd` は codd-gate 専用ではない汎用フック。「差分ゲート」として結線する場合は
  運用側が `--regression-cmd 'codd-gate verify --repos ... --base ... --strict'` を設定するだけで
  良い設計になっている（コード変更不要）。
- 対になる負債取り込みフックは別物 `intake_cmd`（同ファイル内、regression とは独立した
  `_settle_task` 外の watch/idle ループで使われる。例: `codd-gate tasks --debt` の
  `enqueue --json` 出力を冪等取り込み）。今回の調査対象（regression）とはコードパス・戻り値の
  形も別（`intake_cmd` は stdout を JSON として解釈する）なので、混同しないよう注意。
- 「codd-gate 自動検出」ロジック（`regression_cmd`/`intake_cmd`/`repos.json` を自動生成・
  自動設定するコード）は **現時点でリポジトリ中に存在しない**。
  `.kiro-project/repos.json` も本 worktree には無く、`codd-gate verify --repos
  ./.kiro-project/repos.json ...` を実行すると
  `[codd-gate] エラー: repos レジストリが見つかりません: .kiro-project/repos.json` で失敗する
  （実行して確認済み・読み取りのみで副作用なし）。この自動検出・自動結線は本 run の別タスクが
  実装する範囲と判断し、本タスクでは着手していない。

## 8. 採用した前提・未解決事項・範囲外で見つけた問題

**前提**:
- タスク文の「regression（差分ゲート相当）実装」は `Config.regression_cmd` フックを指すと解釈した
  （kiro-project 内で「回帰」を名乗る唯一の機構であり、コミット履歴・design doc・完了条件の
  `codd-gate verify --strict` からも codd-gate を差し込む先として妥当）。
- 本タスクは調査のみと明記されているため、コードは一切変更していない。

**未解決事項**:
- 完了条件のシェルコマンド（`pytest -k codd` と `codd-gate verify --strict`）は、本 worktree の
  現状では両方失敗する（`pytest -k codd` は kiro-project 側に codd 関連テストが無く exit 5、
  `codd-gate verify` は `repos.json` 未生成で失敗）。これは調査タスクの範囲外＝auto-detection
  実装タスク側の完了条件であり、本タスクの成果物（この分析）はその実装の入力として使うことを
  意図している。

**範囲外で見つけた問題（直さず記録のみ）**:
- `regression_cmd` は `vcwd`（workspace 指定タスクでは一時 clone のルート）で実行されるが、
  `_revert_workdir(cfg)` は常に `cfg.workdir`（git-bus ルート）を対象にする。workspace 指定タスクで
  回帰が検知され `regression_revert=True` の場合、実際に回帰が起きたリポジトリ（一時 clone→push 済み
  ブランチ）は revert されず、無関係な git-bus ルートに対して no-op の revert が走るだけになる
  （一時 clone 自体は `finally` で単純 `rmtree` されるのみで git revert は行われない）。
  codd-gate 差分ゲートを workspace 型タスクに結線する場合はこの挙動を踏まえる必要がある。
