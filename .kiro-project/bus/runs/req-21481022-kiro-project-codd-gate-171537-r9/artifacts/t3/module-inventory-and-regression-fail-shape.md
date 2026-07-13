# t3: kiro_project/ モジュール一覧 と regression（差分ゲート）実行関数・不合格表現の調査

対象リポジトリ: `tools/kiro-project/`（フラットなモジュール群。`__init__.py` は無く正式な
Python パッケージではない。各モジュールは `sys.path.insert(0, <このディレクトリ>)` の上で
`import codd_gate_xxx` する規約——タスク文中の「kiro_project/ パッケージ」はこの分割後の
フラットモジュール群を指すものと解釈した）。

## (a) モジュール一覧（パッケージ分割後）

| モジュール | 行数 | 役割 | 対応テスト |
|---|---|---|---|
| `kiro-project.py` | 11219 | 本体（モノリス）。Config/Task/Charter、triage、run_verify 系、`_settle_task`（verify→回帰→保護→進捗判定）、CLI 一式 | `tests/test_kiro_project.py`（8547 行） |
| `codd_gate_detect.py` | 181 | codd-gate CLI の実在・能力の生判定（`resolve_codd_gate`/`resolve_codd_gate_bin`/`get_version`/`check_repos_schema_compat`/`detect_capabilities`） | `tests/test_codd_gate_detect.py` |
| `codd_gate_status.py` | 138 | 検出結果の値オブジェクト `CoddGateStatus`（usable判定・no-op縮退）と `build_status`/`detect_status` | **テストなし**（gap） |
| `codd_gate_invoke.py` | 85 | codd-gate 呼び出し1回分の値オブジェクト `CoddGateResult` と `invoke_codd_gate` | `tests/test_codd_gate_invoke.py` |
| `codd_gate_routing.py` | 82 | `--repos`/`--repo-dir` 引数ビルダ（`resolve_repos_arg`/`resolve_repo_dir_arg`/`build_routing_args`） | `tests/test_codd_gate_routing.py` |
| `codd_gate_base.py` | 54 | 差分ゲートの基準 rev 解決（`resolve_base_rev`） | **テストなし**（gap） |
| `codd_gate_debt.py` | 100 | `codd-gate tasks --debt` 出力のパース（`DriftItem`/`DebtParseResult`/`parse_debt_output`） | **テストなし**（gap） |

いずれも「依存は標準ライブラリのみ・kiro-project.py 側の型（Config/Task/Charter）に依存しない
純粋関数／値オブジェクト」という設計方針で統一されている（各モジュール冒頭の docstring に明記）。
**`kiro-project.py` 本体はまだこれらを import していない**（`grep codd_gate` で本体からの
import 0 件。`codd-gate` という文字列自体は denylist コマンド一覧・コメント・ヘルプ文言としてのみ
出現）——b1-b3/c1-c2/e1-e2 に相当する結線タスクは未着手の段階。

## (b) regression（差分ゲート）の実行関数

呼び出し箇所: `_settle_task`（`kiro-project.py:5490`）内、`kiro-project.py:5524-5525`。

```python
if ok and not flaky and cfg.regression_cmd:    # done 確定前のグローバル回帰ゲート（巻き込み事故）
    rok, rmsg = run_verify(cfg.regression_cmd, vcwd, cfg.verify_timeout, venv)
    if not rok:
        regressed = True
        ...
```

実行の中身は `run_verify`（`kiro-project.py:3018`）:

```python
def run_verify(cmd: str, workdir: Path, timeout: float, env=None) -> "tuple[bool, str]":
    if not cmd.strip():
        return (False, "verify 未定義（自己申告では done にできない → 人の判断へ）")
    try:
        proc = subprocess.run(cmd, shell=True, cwd=str(workdir), timeout=timeout,
                              capture_output=True, text=True,
                              env={**os.environ, **env} if env else None)
    except subprocess.TimeoutExpired:
        return (False, f"verify タイムアウト（{timeout}s）")
    tail = (proc.stdout or "")[-400:] + (proc.stderr or "")[-400:]
    return (proc.returncode == 0, f"exit={proc.returncode} {tail.strip()}"[:500])
```

`cfg.regression_cmd` は `codd-gate verify --base "$KIRO_BASE_REV" --repos <root>/repos.json …`
（設計書 `docs/designs/codd-gate-design.md` §4 の E2 差し込み点）を任意の shell コマンド文字列
として渡す想定で、`run_verify` はそれをそのまま `subprocess.run(..., shell=True)` で1回実行する
だけの薄い関数。`run_verify_stable`（3031行目）は confirm 回数分 `run_verify` を繰り返して
flaky 判定を足すラッパーだが、regression_cmd 呼び出し自体（5525行目）は `run_verify` を直接
1回呼ぶだけで `run_verify_stable` は経由しない（task 本体の verify とは異なる扱い）。

## (c) 「不合格」を表現するデータ構造・返し方

**kiro-project.py 本体（regression_cmd の受け口）は専用の例外型・データクラスを持たない。**
不合格は素朴な `tuple[bool, str]`（`ok`, `msg`）で表現され、失敗は「例外」ではなく
「戻り値の bool が False」で伝播する（`run_verify` はタイムアウトも `subprocess` 例外も
自身で捕捉し、失敗を通常の戻り値に畳み込む——呼び出し側に try/except を強制しない）。

その後段の変換過程:

1. `rok=False` → ローカル変数 `regressed = True`（`_settle_task` 内、5527行目）
2. `cfg.regression_revert` が真なら `_revert_workdir(cfg)`（作業ツリーを HEAD に戻す best-effort）
3. `_block(cfg, task, f"回帰検知: グローバル検査 `{cfg.regression_cmd}` 失敗 — {rmsg}", reasons, evidence=ev)`
   （`kiro-project.py:4916`）を呼び、ここで初めて「不合格」が永続化された状態へ変わる:
   - `task.status = "blocked"`（Task オブジェクトのフィールド更新）
   - `reasons[task.id] = reason`（呼び出し元 `run_loop` が持つ dict へ理由文字列を格納）
   - `_remember_needs_reason(task, reason)` / `persist_task(cfg, task)` / `write_needs_file(...)`
     で people-facing な needs ファイルへ理由文字列（`rmsg` を埋め込んだ日本語メッセージ）を書き出す
   - `release_claim(cfg, task)` で実行権を解放
4. `append_journal(cfg.journal, ...)` で journal にも一行追記

要するに、regression_cmd 経路の「不合格」は **例外でも専用データクラスでもなく、
(bool, str) タプル → bool フラグ（`regressed`）→ `task.status="blocked"` + `reasons` dict +
needs ファイル + journal というシーケンシャルな副作用の連鎖**で表現される。

対照的に、**新設された `codd_gate_invoke.py` の `CoddGateResult`** は同じ「codd-gate の合否」を
扱うが、明示的な frozen dataclass（`status: "ok"|"failed"|"skipped"`, `exit_code`, `stdout`,
`reason`）として値オブジェクト化されている。これは `_settle_task` の `run_verify` 経路とは
別物で、**まだ regression_cmd の呼び出し経路には接続されていない**（`_settle_task` は依然
`cfg.regression_cmd` という shell コマンド文字列 + `run_verify` の tuple 経路のまま）。
つまり現状は「二つの不合格表現」が並存している:

- 旧来（現行 regression_cmd 経路）: `(bool, str)` tuple → `regressed` フラグ → `_block`
- 新設（codd_gate_invoke 経由・未結線）: `CoddGateResult(status="failed", exit_code, stdout, reason)`

`CoddGateResult.ok` プロパティ（`status == "ok"`）を判定に使えば `_settle_task` 側の
`if not rok:` と同じ意味になるが、`"skipped"` を「regression 無効時と同じ既存挙動」として
扱うのか、`"failed"` だけを `regressed=True` に対応させるのかは、この2経路をどう合成するかの
設計判断（b3 の責務）であり本タスクの範囲外。

## 検証内容と結果

- `python3 -m pytest tools/kiro-project/tests -q -k codd` → 47 passed（既存の codd_gate_* 系
  ユニットテストのみ収集され、全て green）
- `codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base HEAD~1 --strict`
  → `OK: 一貫性ゲート通過`（exit 0）
- 上記2コマンドを連結した完了条件コマンドをそのまま実行し、exit code 0 を確認（コード変更は
  一切行っていない・調査のみで完了条件を満たした）。

## 採用した前提・未解決事項・範囲外で見つけた問題

- 前提: 「kiro_project/ パッケージ」は正式な Python パッケージ（`__init__.py` 付き）ではなく、
  `tools/kiro-project/` 配下のフラットなモジュール群（`kiro-project.py` 本体 + 新設
  `codd_gate_*.py` 群）を指すと解釈した。
- 範囲外で見つけた問題（本タスクでは修正しない）:
  - `codd_gate_status.py` / `codd_gate_base.py` / `codd_gate_debt.py` に対応する単体テストが
    まだ無い（`test_codd_gate_status.py`/`test_codd_gate_base.py`/`test_codd_gate_debt.py` 不在）。
  - `kiro-project.py` 本体はまだどの `codd_gate_*` モジュールも import しておらず、
    `cfg.regression_cmd`/`_settle_task` への自動配線（b3 相当）は未着手。
  - `CoddGateResult`（新・値オブジェクト方式）と `_settle_task` の `(bool, str)` tuple 方式
    という「不合格」の二つの表現が並存しており、結線時にどちらのセマンティクスに正規化するかは
    別タスクの意思決定が必要。
- 未解決事項: なし（本タスクの完了条件は調査のみで満たされている）。
