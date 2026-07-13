# t3: regression（差分ゲート）実装箇所・入出力データ構造・codd-gate 差し込み点の確定

対象: `tools/kiro-project/kiro-project.py`（worktree HEAD `6e21135`、ブランチ
`kp/kiro-project-codd-gate-171537`）+ `tools/codd-gate/codd-gate.py`。読了範囲:
`regression`/`回帰`/`verify` 全 grep ヒットの該当箇所全文 + 同一 run 内の先行タスク成果物
（`t2/api_contract.md`, `t4/acceptance_extension_point.md`, `t6/pytest_k_codd_investigation.md`）
+ 前 run（`run-20260712-213419-5922`）の `artifacts/s1/regression-gate-hook-analysis.md`,
`artifacts/d2/codd-gate-status-interface-design.md`。行番号はすべて本 worktree で `grep -n`/`Read`
により実測した現在値（s1/d2 の行番号は前 run 時点のもので、以後のコミット（a1/a4/b1/b2 等）により
ずれている。本書はその差分を現在値へ再確定したもの）。

## (a) 成果 / サマリー

### 1. regression（差分ゲート）の実装箇所

専用の抽象化は無く、汎用シェルコマンドフック `Config.regression_cmd` として実装されている。

| 役割 | 関数/フィールド | file:line |
|---|---|---|
| 設定フィールド | `Config.regression_cmd: str \| None = None` / `Config.regression_revert: bool = False` | `kiro-project.py:4607-4608` |
| CLI引数 | `--regression-cmd` / `--regression-revert`（`BooleanOptionalAction`） | `kiro-project.py:10598, 10600` |
| CONFIG_DEFAULTS 既定値登録 | 同キー | `kiro-project.py:10255-10256` |
| Config構築時の解決 | `regression_cmd=getattr(args, "regression_cmd", None)` 等 | `kiro-project.py:10435-10436` |
| コア実行関数（regression_cmd はこれを直接1回呼ぶ。専用ラッパー無し） | `run_verify(cmd, workdir, timeout, env) -> tuple[bool, str]` | `kiro-project.py:3018-3028` |
| （対比）task本体verifyが使う安定化版 | `run_verify_stable(...) -> tuple[bool, bool, str]`（confirm回まで再実行しflaky検知） | `kiro-project.py:3031-3044` |
| 実行 cwd 解決 | `_task_verify_cwd(cfg, task) -> tuple[Path, str \| None]` | `kiro-project.py:3098-3132` |
| **回帰ゲート呼び出し本体** | `_settle_task` 内 `if ok and not flaky and cfg.regression_cmd:` ブロック | `kiro-project.py:5524-5534`（関数定義は `5490`） |
| KIRO_BASE_REV 注入（一時clone時） | `_settle_task` 内 `venv = {"KIRO_BASE_REV": head} if head else None` | `kiro-project.py:5517-5519` |
| 失敗時のブロック処理 | `_block(cfg, task, reason, reasons, evidence)` | `kiro-project.py:4916-4924` |
| revert（`regression_revert=True` のみ） | `_revert_workdir(cfg)` | `kiro-project.py:4925-` |
| doctor 表示 | `if cfg.regression_cmd: lines.append(f"- 回帰ゲート: PASS（...）")` | `kiro-project.py:5356-5357` |
| テスト | `test_regression_gate_blocks_on_failure` / `test_regression_gate_passes` / `test_demote_then_pin_on_rework` | `tests/test_kiro_project.py:3460, 3469, 717` |

呼び出し元は `_settle_task()` の2箇所のみ（通常サイクルとoffload再入）。`_settle_task()` の外から
`run_verify(cfg.regression_cmd, ...)` を直接呼ぶコードは無い（s1 の調査結果を本 worktree で
再確認、コールグラフに変化なし）。

前提条件（ゲートが評価される3条件、`kiro-project.py:5524`）: `ok and not flaky and cfg.regression_cmd`
がすべて真の場合のみ評価される。task 自身の verify が失敗/flaky の場合は回帰ゲート自体が
実行されない。リトライ経路は無く、失敗は一発で `needs`（人の判断）に落ちる。

### 2. ゲート判定の入出力データ構造

**kiro-project 側（regression_cmd フックの視点）:**

- **入力 = base revision**: `run_verify(cmd, workdir, timeout, env)` の `env` 引数
  （`dict[str, str] | None`）経由で `KIRO_BASE_REV` を渡す。現状の解決経路は1つだけ実装済み:
  一時clone実行時（workspace指定task）に `head = _git_out(vcwd, "rev-parse", "HEAD").strip()` →
  `venv = {"KIRO_BASE_REV": head}`（`kiro-project.py:5517-5519`）。task base branch や
  `HEAD~1` フォールバックを含む3段解決 `codd_gate_base.resolve_base_rev(task_base_branch, env) -> str`
  （`tools/kiro-project/codd_gate_base.py:32-54`）は実装済みだが**どこからも呼ばれていない**
  （`grep -rn "resolve_base_rev" tools/kiro-project` はモジュール自身のみヒット）。
- **入力 = 変更ファイル一覧**: kiro-project は regression_cmd に対して**明示的なファイルリストを
  渡さない**。regression_cmd は `cwd=vcwd, env=venv` だけを与えられた任意シェルコマンド文字列
  であり（`run_verify` は `subprocess.run(cmd, shell=True, cwd=..., env=...)`）、「どのファイルが
  変わったか」の計算はコマンド自身（codd-gate 側）の責務。kiro-project が自前で持つ「変更」データ
  構造は regression_cmd とは**独立**の別物: `meaningful_changes(cfg, git_base) -> set[str]`
  （`kiro-project.py:871-876`、内部で `changed_paths_since`＝`git diff --name-only` を使う
  `kiro-project.py:837-849`）。これは回帰ゲート**通過後**に done 判定側（保護パス検知・
  no-progress検知）が使うものであり、regression_cmd の入力ではない点に注意（`5546` の
  `if ok and not flaky and not regressed:` で回帰ゲートの後段に位置する）。
- **出力 = 合否・理由**: `run_verify()` の戻り値 `tuple[bool, str]`（`rok, rmsg`）。
  `rok`: `proc.returncode == 0`。`rmsg`: `f"exit={rc} {stdout末尾400+stderr末尾400}"[:500]`。
  `rok=False` なら `regressed=True` → `_block(cfg, task, f"回帰検知: グローバル検査 \`{cfg.regression_cmd}\`
  失敗 — {rmsg}", reasons, evidence=ev)`（`kiro-project.py:5530-5531`）で `rmsg` がそのまま
  needs ファイルの reason に転記される。

**codd-gate 側（`regression_cmd` に `codd-gate verify --strict` を設定した場合、実際に判定を
行う内部データ構造）:**

- 入力: `base: str`（`--base`／`$KIRO_BASE_REV`）+ `target: Repo`（`--repo-dir` で解決）。
  `changed_files(repo: Repo, base: str) -> dict[str, str]`（`codd-gate.py:613-641`）が
  `git diff --name-status -z base -- path` と `git ls-files --others` を合成し
  `{relpath: status文字}`（status は `A`/`M`/`D` 等、rename は `D`+`A` に分解）を返す——これが
  タスク文の「変更ファイル一覧」の実体構造。
- 判定: `classify_impact(mapdata, repos, target, base) -> dict`（`codd-gate.py:660-722`）が
  `changed_files` を呼び、接続マップと突き合わせて `green`/`amber`/`gray`/`followup`
  （各 `list[dict]`、要素は `{type, node, counterpart?, detail}`）に分類する。戻り値全体:
  ```python
  {"base": base, "repo": target.name,
   "changed": {f: s for f, s in sorted(changed.items())},
   "green": [...], "amber": [...], "gray": [...], "followup": [...]}
  ```
  （`codd-gate.py:720-722`）。
- 出力 = 合否・理由: `ng = bool(imp["amber"]) or (args.strict and bool(imp["gray"])) or
  (args.strict_cross and bool(imp["followup"]))`（`codd-gate.py:1097-1098`）→
  `return 1 if ng else 0`（`codd-gate.py:1101`）。理由は非JSON時 `"OK: 一貫性ゲート通過"` /
  `"NG: ドリフトあり — \`codd-gate tasks\` で修復タスクを生成できる"`（定型文、`codd-gate.py:1100`）
  で、詳細は直前に印字される `[AMBER]`/`[GRAY]`/`[FOLLOWUP]` 各 `detail` 行（`codd-gate.py:1090-1094`）。
  この exit code が `run_verify()` の `proc.returncode` としてそのまま kiro-project 側の `rok` になる
  ——2つの層のデータ構造は「exit code 0/1」1点だけで結合されており、`imp` dict 自体は
  kiro-project からは見えない（stdout の人間可読テキストとしてのみ `rmsg` に部分的に残る）。

### 3. `codd-gate verify --strict` を差し込む具体的な関数と行位置

現状 `cfg.regression_cmd` を自動組み立てするコードは存在しない（値は常に人が CLI/設定ファイルで
明示しない限り `None`＝回帰ゲート自体が無効。`t2/api_contract.md` §1 と同一確認）。前 run の
d2 設計（`run-20260712-213419-5922/artifacts/d2/codd-gate-status-interface-design.md` §4.1）が
差し込み先を確定済みで、本 worktree の現在行番号でもその設計はそのまま成立する:

- **差し込み関数**: `load_charter(cfg: "Config") -> "Charter | None"`（`kiro-project.py:8586-8591`）
- **差し込み行位置**: `8591` 行 `return _apply_repo_registry(cfg, ch)` の直後
  （`_apply_repo_registry` 適用後・`Charter` 返却前）。差し込み後の形:
  ```python
  def load_charter(cfg: "Config") -> "Charter | None":
      p = cfg.charter
      if not p or not p.exists():
          return None
      ch = parse_charter(p.read_text(encoding="utf-8"))
      ch = _apply_repo_registry(cfg, ch)
      if cfg.regression_cmd is None:                      # 明示設定は自動配線より優先（上書きしない）
          status = resolve_codd_gate_status(cfg)           # 未実装（d2 §6 の統合エントリポイント）
          if status.usable:
              args_list = build_routing_args(                          # codd_gate_routing.py:67
                  repo_registry_path(cfg), ch.name or "sandbox")        # repo_registry_path: kiro-project.py:8475
              argv = status.command("verify", *args_list, "--base", "$KIRO_BASE_REV", "--strict")
              cfg.regression_cmd = shlex.join(argv)
      return ch
  ```
- **根拠**: `_apply_repo_registry`（`8557-8583`）が「パース後・返却前にインメモリで `Charter` を
  加工する」既存パターンをすでに持っており、`t4/acceptance_extension_point.md` が acceptance 側の
  拡張点として同一パターン（`load_charter`/`_apply_repo_registry` 相当のパース後フック）を挙げて
  いることとも整合する。`cfg.regression_cmd is None` の条件は d2 §6-5「明示設定は自動配線より
  常に優先」を実装レベルに落としたもの。
- **実行時に実際に `codd-gate verify ... --strict` が発火する場所（消費側・変更不要）**:
  `kiro-project.py:5525` `run_verify(cfg.regression_cmd, vcwd, cfg.verify_timeout, venv)`。
  `cfg.regression_cmd` に上記文字列が入っていれば、この行がそのまま
  `codd-gate verify --repos <repos.json> --repo-dir <name>=<dir> --base $KIRO_BASE_REV --strict`
  を shell 実行する。`--base` のシェル変数展開は `venv` の `KIRO_BASE_REV`（`5517-5519`。
  一時clone以外の経路では現状未注入＝空文字展開→codd-gate側 `_die`、`codd-gate.py:1080`）に
  依存するため、`resolve_base_rev` をこの経路に合流させるかどうかは差し込み実装（b3 相当）が
  別途判断する必要がある（本タスクは箇所の特定までがスコープ）。

### 4. 未実装・未結線であることの確認

- `resolve_codd_gate_status(cfg)` 自体が未実装（`codd_gate_status.py` の `detect_status` は
  `version_known=True, schema_ok=True` 固定の暫定実装で、`cfg` を引数に取らない別シグネチャ。
  d2 が提案する `resolve_codd_gate_status(cfg, which=..., run=...) -> CoddGateStatus` という
  統合エントリポイントはまだコード化されていない）。
- `grep -rn "import codd_gate" tools/kiro-project` は各モジュール自身のテストファイル以外
  ヒット0件、`load_charter` 内に `codd_gate_*` への参照は無い（`t2/api_contract.md` §1と同一）。
- 上記より、本タスクが確定した「関数・行位置」は**設計上の差し込み点**であり、コードは
  まだそこに存在しない。

## (b) 検証内容と結果

- コード変更なし（調査のみ）。`git status --short`（worktree）差分ゼロを確認。
- 完了条件のシェルコマンドをそのまま worktree で実行し、変更前から exit 0 であることを確認
  （このタスクの調査がゲートを壊していないことの確認）:
  ```
  $ python3 -m pytest tools/kiro-project/tests -q -k codd
  29 passed, 579 deselected in 0.05s
  $ codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict
  差分: sandbox HEAD~1..作業ツリー（2 ファイル）
    [GREEN] tools/kiro-project/codd_gate_routing.py（接続 1 本・整合）
    [GREEN] tools/kiro-project/tests/test_codd_gate_routing.py（参照は全て解決）
  OK: 一貫性ゲート通過
  $ echo $?
  0
  ```
- file:line はすべて worktree 内 `grep -n`/`Read` で実測（HEAD `6e21135`）。前run（s1/d2）の
  行番号との差分（例: `_settle_task` の回帰ブロックは s1 時点 `4940-4950` → 本書時点 `5524-5534`）
  はコミット `a1`/`a4`/`b1`/`b2` が `codd_gate_*.py` 系の docstring・コードを本体ファイルより
  前方（`kiro-project.py` 冒頭〜中盤の他関数群）に追加したことによる素朴な行数増分であり、
  関数の実装内容・呼び出し構造そのものに変化は無い（s1 の結論をそのまま再確認できた）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**前提**:
- タスク文の「regression（差分ゲート）実装箇所」は `Config.regression_cmd` フック
  （`_settle_task` 内の呼び出し）を指すと解釈した。s1/d2/t4 の先行調査と同一の解釈。
- 「ゲート判定の入力（base リビジョン・変更ファイル一覧）」は、kiro-project 側（regression_cmd
  への env/cwd という薄い入力）と codd-gate 側（実際に base・変更ファイルを消費して判定する
  `classify_impact`/`changed_files`）の**両方**を指すと解釈し、両階層のデータ構造を併記した。
  kiro-project は「変更ファイル一覧」を regression_cmd へ明示的に渡さない設計になっているため、
  この前提を明記しないと「入力に変更ファイル一覧がある」という記述が誤解を招くと判断した。
- 「codd-gate verify --strict を差し込む具体的な関数と行位置」は、d2（前run）がすでに設計として
  確定していた内容を、本 worktree の**現在の行番号**へ再確認・再確定する作業と位置づけた
  （新規設計判断はしていない。d2 の内容と齟齬がある箇所は見つからなかった）。

**未解決事項（後続タスクへの申し送り）**:
- `resolve_codd_gate_status(cfg)` という統合エントリポイント自体が未実装（`detect_status` との
  シグネチャ差異・キャッシュ方式の2案が d2 §6-4 で未決のまま）。これを実装しない限り
  `load_charter` への差し込みは書けない。
- `codd_gate_base.resolve_base_rev` は実装済みだがどこからも呼ばれておらず、`5517-5519` の
  既存 `KIRO_BASE_REV` 注入（一時clone限定）との統合方法が未決（一時clone以外の経路では
  `--base` が現状シェル変数の未定義展開＝空文字になり `codd-gate` 側 `_die` する、s1 が
  指摘した既知のギャップがそのまま残っている）。
- `codd_gate_base.py`/`codd_gate_debt.py` にユニットテストが無い点は `t6` が確認した通り
  範囲外（本タスクでも変更していない）。

**範囲外で見つけた問題**:
- s1 が指摘した `regression_revert` の workdir/vcwd 不一致（一時clone実行時、revert対象が
  実際に回帰の起きたclone ではなく無関係な `cfg.workdir` になる）は本タスクの範囲外として
  未修正のまま残っている。差し込み実装時にこの既知の非対称性を踏まえる必要がある。
