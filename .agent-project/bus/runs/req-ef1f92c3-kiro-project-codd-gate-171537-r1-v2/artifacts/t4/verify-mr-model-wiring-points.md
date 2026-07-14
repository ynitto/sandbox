# verify.py の回帰ゲート・mr.py の検収判定・model.py の enqueue — 結線ポイント特定

## (a) 成果サマリー

### 前提（採用した解釈）
[t3](../t3/fragments-mechanism-coddgate-position.md) の確認どおり、作業ブランチ（`kp/kiro-project-codd-gate-171537`）には
`kiro_project/verify.py` / `mr.py` / `model.py` 自体がまだ存在しない（`kiro_project/` 配下にあるのは `coddgate.py` のみ。
パッケージ分割リファクタはコミット `1cee8484` で main にのみ入っている）。本タスクも調査のみでファイルを書き換えない
指示のため、`git show main:tools/kiro-project/kiro_project/{verify,mr,model}.py` を正典として関数を特定した。

### 結論: 3ファイルの結線ポイント一覧

| ファイル | 関数 | シグネチャ | 合否の表現 |
|---|---|---|---|
| `verify.py` | `run_verify` | `run_verify(cmd: str, workdir: Path, timeout: float, env: dict\|None = None) -> tuple[bool, str]` | 戻り値 `(ok, msg)`。`ok = (proc.returncode == 0)`。タスク自身の `verify` 実行にも、**`cfg.regression_cmd`（回帰ゲート）の実行にも同じ関数が使われる**（呼び出し元は mr.py 側） |
| `mr.py` | `_settle_task` | `_settle_task(cfg, task, location, act_msg, cycle, dtok, dusd, git_base, verify_env, policy, autonomy_cache, reasons) -> dict` | **戻り値は bool ではない**（`{"archived": int, "followups": list}`）。合否は `task.status` の副作用的な遷移（`done` / `review` / `ready`（積み直し）/ `blocked`（エスカレーション））と、内部ローカル変数 `regressed: bool`（回帰ゲート結果）で表現される |
| `mr.py` | `finalize_task_mr`（`_settle_task` とは別系統・MR 決着専用） | `finalize_task_mr(cfg: Config, task: Task) -> tuple[bool, str]` | 戻り値 `(settled_ok, reason)`。`True`=マージ/クローズ/対象外で決着済み、`False`=未クリーン（コンフリクト・未解決コメント）で done にしない |
| `model.py` | `enqueue_task` | `enqueue_task(cfg: Config, spec: dict) -> Task` | **戻り値は bool ではない**。成功は `Task` オブジェクトを返すこと自体で表現、失敗は例外（`task_from_spec` 内の `ValueError`、例: `"title は必須です"`）で表現。呼び出し側（`run_intake`/`ingest_inbox`）が `try/except ValueError` で握る |
| `model.py` | `run_intake`（`enqueue_task` の呼び出し元。負債取り込みの実フック） | `run_intake(cfg: Config) -> list[Task]` | 戻り値は生成された `Task` のリスト（0件もあり得る＝正常）。`cfg.intake_cmd` の exit code ≠0・非JSON出力は無視して `[]` を返す（エラーを潰して継続） |

### 前提とのズレ（重要な発見）
タスク文言は「verify.py の**回帰ゲート関数**」を単体の関数として想定しているが、実際のソースには
verify.py 側に「回帰ゲート」という名の関数・処理は無い（`grep 回帰` は verify.py で0件）。
実体は次の分業になっている:

- **verify.py** はプリミティブ `run_verify()` のみを持つ「任意のシェルコマンドを実行して合否を返す」汎用関数。
  回帰ゲート専用ロジックはここには無い。
- **mr.py の `_settle_task`**（389-483行目）が、tasク検証ゲートの一連（verify→回帰→保護→進捗→flake）を
  すべて1関数の中でインラインに実行し、399-409行目で

  ```python
  if ok and not flaky and cfg.regression_cmd:    # done 確定前のグローバル回帰ゲート（巻き込み事故）
      rok, rmsg = run_verify(cfg.regression_cmd, vcwd, cfg.verify_timeout, venv)
      if not rok:
          regressed = True
          ...
          _block(cfg, task, f"回帰検知: グローバル検査 `{cfg.regression_cmd}` 失敗 — {rmsg}", reasons, evidence=ev)
  ```
  という形で「回帰ゲート」を実装している。つまり **verify.py の `run_verify` を mr.py 側から呼ぶことで
  回帰ゲートが成立する**構造であり、verify.py 単体に regression 専用関数を新設するのは既存設計と非対称になる。
  `cfg.regression_cmd`（`config.py:122`）は `str | None` の設定値で、素通しでシェル実行される。

- `risk_digest`（mr.py 191-234行目）にも `if cfg.regression_cmd: lines.append(f"- 回帰ゲート: PASS...")`
  という表示専用の参照があるが、これは承認前のリスクダイジェスト文言生成であり判定そのものではない。

### codd-gate 結線の具体的な差し込み方（[t1](../t1/codd_gate_base_detect_catalog.md)・[t2](../t2/codd-gate-debt-routing-status-api-inventory.md) の裏付けと合わせて）

1. **差分ゲート（regression）**: codd-gate CLI の `verify --repos FILE --base REV [--strict] [--debt ...]` は
   ドリフト無し=`exit 0` / あり=`exit 1`（t1 記載）。`run_verify` はシェルコマンド文字列をそのまま実行する
   汎用関数なので、**`cfg.regression_cmd` に codd-gate 起動コマンドを組み立てて代入する**のが素直な結線経路
   （`_settle_task` 側のロジック変更は不要）。base rev の解決には `codd_gate_base.resolve_base_rev()`
   （t1）が使える。自動検出（未導入環境で regression_cmd を汚さない）には `codd_gate_detect.resolve_codd_gate_bin`
   / `detect_capabilities`（t1）を config 読み込み時 or `_settle_task` 呼び出し前に噛ませる必要がある。
2. **検収判定**: `_settle_task` 自体は codd-gate 固有処理を持たない（run_verify 経由で間接的に効く）。
   MR ベースの検収を厳密化したいなら `finalize_task_mr` 側に codd-gate 結果を条件追加する余地があるが、
   現状 `_settle_task` の `regressed` 分岐で「回帰NG→done にしない」が既に成立しているため、二重実装に注意。
3. **負債取り込み**: `run_intake`（model.py 463-515行目）は docstring で
   「外部の決定的ゲート/検出器（例: `codd-gate tasks --debt`）を watch の周期で汲み上げる汎用フック」と
   明記されており、**既にこの用途のために設計されている**。`codd_gate_debt.parse_debt_output(text)` が返す
   `DriftItem.to_spec()`（t2）は `enqueue_task(cfg, spec)` がそのまま受け取れる dict 形式。つまり
   `cfg.intake_cmd = "codd-gate tasks --debt ..."` を設定するだけで `run_intake` → `enqueue_task` の
   既存経路にそのまま乗る設計になっている（新規コードパス追加ではなく設定値の結線で足りる可能性が高い）。

## (b) 検証内容と結果
- `git show main:tools/kiro-project/kiro_project/{verify,mr,model}.py` を全文取得し読了（458/541/577行）。t3 の
  「作業ブランチにこれらのファイルが存在しない」という前提を `git ls-tree -r --name-only main -- tools/kiro-project/kiro_project/`
  と `git ls-tree -r --name-only HEAD -- tools/kiro-project/kiro_project/` の比較で再確認（main は27ファイル、
  作業ブランチは `coddgate.py` の1ファイルのみ）。
- `grep -n "回帰\|regression"` を3ファイルに対して実行し、「回帰ゲート」という文言が mr.py にのみ存在し
  verify.py には存在しないことを機械的に確認（上記「前提とのズレ」の根拠）。
- `config.py`（main）の `regression_cmd` / `regression_revert` フィールド定義を確認し、`_settle_task` の
  参照箇所と型が一致することを確認。
- t1・t2 の成果物（本タスクの直接依存には含まれないが同一 run の並行タスク）を参照し、codd-gate 側の
  公開 API（`resolve_base_rev` / `resolve_codd_gate*` / `parse_debt_output`/`DriftItem.to_spec`）が
  今回特定した結線ポイントの引数・戻り値と型レベルで整合することを確認した。
- 本タスクは調査のみのためファイルは一切変更していない（`git status --short` で作業ツリーがクリーンであることを確認）。
  完了条件のシェルコマンド（pytest / grep / `codd-gate verify`）は、`__init__.py` 一式が作業ブランチに
  まだ無いため本タスクでは実行対象外（t1 が同一状態で `codd-gate verify --strict` を実行し exit 1 を確認済み、
  理由は `coddgate.py` が GRAY 未接続のため — 本タスクの担当範囲外）。

## (c) 前提・未解決事項・範囲外で見つけた問題
- **前提**: 上記の通り main 版を正典として読み解いた。次の実装タスクは、まず main の `__init__.py`・
  `verify.py`・`mr.py`・`model.py` 一式を作業ブランチへ持ち込んだ上で、`_FRAGMENTS` に `"coddgate"` を
  `"prioritize"` と `"verify"` の間へ挿入する（t3 で確定済み）作業と併走することになる。
- **未解決事項**: 「回帰ゲート」を verify.py 側の独立関数として新設するか、既存どおり mr.py の
  `_settle_task` 内から `run_verify(cfg.regression_cmd, ...)` を呼ぶ形（＝設定値の結線のみ）で済ませるかは
  設計判断が必要（後続タスクの範囲）。後者の方が既存構造との非対称が生じず変更量も小さい。
- **範囲外で見つけた問題**: t1 で指摘済みの `kiro_project/coddgate.py` の GRAY（doc/test 未接続）は本タスクでも
  再確認したが対処は範囲外。また `codd_gate_base.py` / `codd_gate_detect.py` / `codd_gate_debt.py` などが
  `kiro_project/` パッケージ外に残っている点（t3 既報）も未解消のまま。
