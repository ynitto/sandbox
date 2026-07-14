# t1 調査メモ — codd-gate 自動検出と regression/intake 結線

## 0. 要旨

- **検出方式（a1/a2 相当）は実装済み・動作確認済み**。`tools/agent-project/codd_gate_detect.py` /
  `codd_gate_status.py` が既に main ブランチにコミット済みで、`detect_status()` は実環境で
  `usable=True` を返し `command("verify","--base","HEAD")` も正しい argv を返す（下記 §4 で実行確認）。
- **ルーティング補助モジュール（b2/b3 相当）も実装済み**: `codd_gate_routing.py`（`--repos`/`--repo-dir`
  組み立て）、`codd_gate_base.py`（`KIRO_BASE_REV` 解決の欠落穴埋め）、`codd_gate_debt.py`（`tasks --debt`
  出力の per-record パース）。
- **ユニットテスト（t4 の目標物）も既に存在し pass する**: `tests/test_codd_gate_detect.py`
  （29 件中に含まれる）/ `tests/test_codd_gate_routing.py`。§4 で実行確認済み。
- **未結線（今回の run で実際に埋めるべき唯一の欠落）は2点だけ**:
  1. `.agent/agent-project.yaml` に `regression_cmd:` / `intake_cmd:` の実値が入っていない
     （現状ファイルには regression/intake 関連キーが1行も無い）。
  2. `agent_project/mr.py`（regression 実行）と `agent_project/model.py`（intake 実行）が、
     上記の検出・ルーティングモジュールを **一切 import していない**。`cfg.regression_cmd` /
     `cfg.intake_cmd` は素の文字列として `shell=True` でそのまま実行されるだけで、codd-gate 未導入時に
     壊れたコマンドとして「回帰失敗」誤検知するフォールバックが無い。
- 完了条件の4コマンドのうち **後半2つ（python detect_status アサーション／pytest 2 本）は無変更で
  既に exit 0 になることを確認済み**。残る2つの grep は yaml 未設定のため確実に失敗する。
  → t2 の「codd_gate_status.py を実装する」は実質「既存実装の追認・（必要なら）強化」に、
  t3・t4 が本丸（yaml 結線＋実行経路の配線、既存テストの再確認）になる。

## 1. 環境・パスの前提（最初に踏まえるべき注意）

このタスクに渡された worktree（`/Users/nitto/Workspace/sandbox-agent-state/.agent-project`、
`agent-state` ブランチの sparse checkout）には **`tools/agent-project` も `.agent/agent-project.yaml` も
存在しない**（sparse-checkout 対象が `.agent-project` パスのみのため）。実体は別 worktree
`/Users/nitto/Workspace/sandbox`（`main` ブランチ、remote `origin` = `https://github.com/ynitto/sandbox.git`）
にある。本メモの調査はすべてこちらを **読み取り専用**で参照して行った（書き込みはしていない）。

**t2/t3/t4 への申し送り**: `/Users/nitto/Workspace/sandbox` は現在、今回の request と無関係な
大規模差分（`tools/kiro-flow` 削除、`docs/designs/*` の kiro→agent リネーム、`schemas/*` 変更等、
`git status` で 80 ファイル超）を抱えた **共有チェックアウト**。ここへ直接書き込むと無関係な差分に
巻き込まれる／衝突するリスクが高い。git 利用規約に従い、

```
python3 /Users/nitto/.kiro/skills/flow-worker/scripts/git_worktree.py provision \
  https://github.com/ynitto/sandbox.git --ref main
```

で専用 worktree を取得してから `tools/agent-project/` と `.agent/agent-project.yaml` を編集することを
強く推奨する。

## 2. tools/agent-project の既存モジュール構成

`tools/agent-project/`（`/Users/nitto/Workspace/sandbox` 内）:

```
agent-project.py            … 薄いエントリポイント（16行）。sys.path に自ディレクトリを足して
                               agent_project パッケージの main() を呼ぶだけ（後方互換の起動口）。
agent_project/               … 実体（旧モノリシック agent-project.py を機能単位に機械分割したパッケージ）
  ├ config.py                 Config dataclass（regression_cmd/intake_cmd/intake_interval フィールド）
  ├ configfile.py              CLI引数/デフォルト値定義（regression_cmd 等のデフォルト None・ヘルプ文言）
  ├ charter.py                 charter_repo_spec_map / repo_registry_path / export_repo_registry
  ├ policy.py                  git_change_baseline（KIRO_BASE_REV の元ネタ）
  ├ verify.py                  run_verify / _task_verify_cwd（regression_cmd の実行経路そのもの）
  ├ mr.py                      _settle_task（regression_cmd 呼び出し箇所, mr.py:437-438）
  ├ model.py                   run_intake（intake_cmd 呼び出し箇所, model.py:463-）
  ├ loop.py                    run_intake の定期呼び出し元（loop.py:576）
  ├ request.py                 resolve_agent_flow（codd-gate 検出と同型の解決連鎖の前例, :4-19）
  ├ doctor.py                  doctor_env_findings（agent-flow 等の実在チェック。codd-gate 未対応）
  └ …（他 batch/decisions/flow/gitcache/instances/cli/_head 等）
codd_gate_detect.py          … 【新規・独立】codd-gate の実在・能力検出（agent_project パッケージ外）
codd_gate_status.py          … 【新規・独立】CoddGateStatus 値オブジェクト・no-op縮退
codd_gate_routing.py         … 【新規・独立】--repos/--repo-dir 引数ビルダ
codd_gate_base.py            … 【新規・独立】KIRO_BASE_REV 解決フォールバック
codd_gate_debt.py            … 【新規・独立】tasks --debt 出力パーサ
tests/
  ├ test_codd_gate_detect.py  … 既存（29 assertion 分の一部、pass 確認済み）
  ├ test_codd_gate_routing.py … 既存（pass 確認済み）
  └ test_agent_project.py     … 本体の巨大テスト（556KB、今回は未変更のはず）
agent-project.yaml.example    … 設定サンプル。regression_cmd/intake_cmd のコメント例が既にある
                                 （L171: `# regression_cmd: make -s smoke`、L179: `# intake_cmd: codd-gate tasks --debt`）
install.sh
```

**重要な設計上の注意点**: `codd_gate_*.py` 5 ファイルは `agent_project/` パッケージの**外**、
`agent-project.py` と同じ階層（`tools/agent-project/` 直下）に置かれている。`codd_gate_status.py`
内部で `from codd_gate_detect import resolve_codd_gate`（絶対 import・パッケージ prefix 無し）を
使っており、これは完了条件のコマンド `PYTHONPATH=tools/agent-project python3 -c 'from codd_gate_status
import detect_status; ...'` の呼び出し規約とちょうど一致する。**`agent_project/` パッケージ内から
これらを import する場合も同じ形（`sys.path` に `tools/agent-project/` 自身を通した上でトップレベル
import）にする必要がある**——`from agent_project.codd_gate_status import ...` のような相対パッケージ
importは効かない（ファイルがパッケージの外にあるため）。

**さらに要注意**: `codd_gate_detect.py`/`codd_gate_status.py` 等のモジュール docstring は
`agent-project.py:3477` や `mr.py` 相当行を「agent-project.py:4906」のように**旧・分割前のモノリシック
ファイルの行番号**で参照している。現在は `agent_project/` パッケージへ分割済みのため、これらの行番号は
**すべて陳腐化している**（実際の対応: `resolve_agent_flow`→`request.py:4`、`_settle_task`→`mr.py:403`、
`_task_verify_cwd`→`verify.py:103`、`git_change_baseline`→`policy.py:219`）。t2/t3 が結線コードを書く際は
docstring の行番号ではなく本メモの実位置を参照すること（範囲外の修正だが、ついでに docstring を
直すかは t2/t3 判断——本メモは指摘のみに留める）。

## 3. .agent/agent-project.yaml のスキーマ（regression/intake 関連キー）

現在の実ファイル（`/Users/nitto/Workspace/sandbox/.agent/agent-project.yaml`、34行、コメント過多で
実質値はほぼ無し）には **regression_cmd・intake_cmd に関する記述が1行も無い**（コメントアウトされた
`flow_planner`/`spec_track` 等の注記のみ）。

記法は `agent-project.yaml.example`（同ディレクトリ、27982 バイト）が正典:

```yaml
# regression_cmd: make -s smoke   # done 確定前のグローバル回帰検査（失敗で done にせず人へ）
                                   # (L171 付近)
...
# intake_cmd: codd-gate tasks --debt
                                   # (L179 付近)
```

- インデント: トップレベルキー（先頭空白なし）。完了条件の grep パターン
  `^[[:space:]]*regression_cmd:.*codd-gate verify --base` / `^[[:space:]]*intake_cmd:.*codd-gate tasks`
  は行頭の空白0個以上を許容するので、トップレベルでもインデント付きでもマッチする。
- `#` によるコメントアウトが既定（=未設定）。有効化するには先頭の `#` を外し実値を書く。
- 対応する Python 側スキーマ（`configfile.py:117-120`）:
  - `regression_cmd: "str | None" = None` — done 確定前のグローバル回帰検査コマンド
  - `intake_cmd: "str | None" = None` — 外部ゲート/検出器から修復タスクを汲み上げるコマンド
  - `intake_interval: float = 600.0` — intake_cmd の実行間隔（秒）。関連キーとして yaml に
    同時に置くのが自然（今回の完了条件には含まれないため必須ではない）。
- CLI 引数でも同名で上書き可能（`configfile.py:297,299`: `regression_cmd=getattr(args,
  "regression_cmd", None)` 等）——yaml が正で CLI は上書き用。

**t3 が書くべき最小差分**（既存コメント行の直下に有効な設定として追加。`$KIRO_BASE_REV` はシェル
変数参照のままにする設計——d2 設計どおり Python 側で argv に埋め込まない）:

```yaml
regression_cmd: codd-gate verify --base "$KIRO_BASE_REV"
intake_cmd: codd-gate tasks --debt
```

これだけで grep 2条件はそのまま満たす。ただし §5 のとおり、codd-gate 未導入環境でこの文字列を
無条件に `shell=True` 実行すると「コマンド未検出→exit 127→回帰失敗」という**誤検知**になるため、
検出結果に応じた no-op 縮退の配線（§5）とセットで入れる必要がある。

## 4. codd-gate CLI の実体（動作確認済み）

```
$ which codd-gate
/Users/nitto/.local/bin/codd-gate      # PATH 上の実行ファイル。symlink ではなく実体（57414 bytes）
$ file /Users/nitto/.local/bin/codd-gate
Python script text executable, Unicode text, UTF-8 text
$ codd-gate --help
usage: codd-gate [-h] [--version] {scan,impact,verify,tasks,check} ...
```

- **実体は「PATH 上の実行ファイル」**（`tools/codd-gate/codd-gate.py` のリポジトリ内スクリプトを
  install.sh 等で `~/.local/bin/codd-gate` へ配置したもの。`git-skill-manager` 経由のインストールと
  推測される。`codd_gate_detect.resolve_codd_gate()` の解決連鎖は
  「explicit 指定 → `shutil.which("codd-gate")` → `tools/codd-gate/codd-gate.py` 同梱パス」の順で、
  今回の環境は2段目（PATH）で解決する。
- サブコマンド: `scan` / `impact` / `verify` / `tasks` / `check`。`verify --base <rev>` は差分ゲート、
  `tasks --debt` は既存負債の修復タスク化——完了条件の2コマンドと完全に一致する。
- リポジトリ内にも `tools/codd-gate/`（ソース本体・README あり、現在ワーキングツリーで README のみ
  差分あり）が存在し、`codd_gate_detect.py` の同梱パスフォールバック
  （`tools/agent-project/../codd-gate/codd-gate.py`）と整合する。

**実行確認**（読み取り専用、`/Users/nitto/Workspace/sandbox` を変更せず実施）:

```
$ cd /Users/nitto/Workspace/sandbox
$ PYTHONPATH=tools/agent-project python3 -c '
from codd_gate_status import detect_status
s = detect_status()
assert s.usable and s.command("verify", "--base", "HEAD")
'
# → usable=True, command=['/Users/nitto/.local/bin/codd-gate', 'verify', '--base', 'HEAD']
# 例外なし・exit 0（完了条件の該当コマンドは今の環境で無変更のまま通る）

$ python3 -m pytest tools/agent-project/tests/test_codd_gate_detect.py \
                     tools/agent-project/tests/test_codd_gate_routing.py -q
# → 29 passed in 0.04s
```

## 5. 検出方式（実装済み・確認済み）

`codd_gate_detect.resolve_codd_gate(explicit=None, which=shutil.which)`:
explicit 指定 → `shutil.which("codd-gate")` → `tools/codd-gate/codd-gate.py` 同梱パスの順で解決、
どれも無ければ `None`（`resolve_agent_flow` と対称形。ただし codd-gate は任意機能なので「起動コマンドを
でっち上げない」ため `None` を返す点が agent-flow と異なる——agent-flow は必須なので最終フォールバックの
`legacy` パスを無条件に返す）。

`codd_gate_status.detect_status(explicit=None, which=shutil.which) -> CoddGateStatus`:
実在チェックのみで `CoddGateStatus(binary, version=None, findings=[])` を組み立てる合流点。
`CoddGateStatus.usable`（`binary is not None and not findings`）と
`CoddGateStatus.command(*args)`（usable でなければ `None`、usable なら `[*binary, *args]`）が
呼び出し側の唯一の接点——**呼び出し側は `if status.command(...):` の1行だけで済む no-op 縮退**。

バージョン下限判定（`MIN_SUPPORTED_VERSION = (1,0,0)`）・schemas 互換判定
（`check_repos_schema_compat`）は `codd_gate_detect.get_version` / `.check_repos_schema_compat` として
関数は存在するが、**`detect_status()` からは呼ばれていない**（`version_known=True, schema_ok=True` の
既定のまま `build_status` に渡している）。これは検出の欠陥ではなく設計上の未合流（"a2 がまだ合流して
いない" と docstring に明記）——完了条件のアサーションは実在チェックのみで満たされるため今回のスコープ
では影響しないが、将来バージョン非互換を検出したい場合は
`build_status(binary, version=get_version(binary), version_known=..., schema_ok=check_repos_schema_compat(repos_path)[0])`
の形で呼び直す拡張余地がある（今回の t2/t3/t4 の必須要件ではない）。

## 6. 結線先・ルーティング挿入点（未実装。t2/t3 が埋めるべき具体位置）

### 6.1 regression（b3 相当）— 挿入点: `agent_project/mr.py:437-438`

現状:
```python
if ok and not flaky and cfg.regression_cmd:    # done 確定前のグローバル回帰ゲート（巻き込み事故）
    rok, rmsg = run_verify(cfg.regression_cmd, vcwd, cfg.verify_timeout, venv)
```
`cfg.regression_cmd` は素の文字列。`venv` は `_task_verify_cwd` が一時クローンを使った場合のみ
`{"KIRO_BASE_REV": <clone HEAD>}` を含む（`mr.py:430-432`）。通常経路（workspace 未指定タスク）では
`KIRO_BASE_REV` が注入されないケースがあり、`codd_gate_base.py` の docstring が指摘する
「`--base ""` で codd-gate が `_die` する」穴がここに実在する。

**推奨する結線（実装は t2/t3 の責務。本メモは挿入点の特定のみ）**:
1. `mr.py` の先頭付近（他の `agent_project` サブモジュール import と同じ箇所）で
   `sys.path` に `tools/agent-project/`（`codd_gate_status.py` 等が置かれた自ディレクトリの親）を
   通した上で `codd_gate_status` / `codd_gate_base` を import する。
   （`agent_project/__init__.py` が各ファイルを `exec` 合成する構成——`request.py:1-3` の
   コメント「単体 import しない」を踏まえ、import 追加は `__init__.py` の合成順・共有名前空間の
   衝突が無いか要確認。範囲外のためここでは指摘のみ）。
2. `cfg.regression_cmd` に codd-gate 由来の文字列が入っている場合、実行前に
   `codd_gate_status.detect_status().usable` を確認し、`False` なら回帰ゲートを **スキップ**
   （＝未導入環境で誤って「回帰検知」させない no-op 縮退）。
3. `venv` への `KIRO_BASE_REV` 注入を `codd_gate_base.resolve_base_rev(task_base_branch=...,
   env=venv or {})` 経由に統一し、未注入ケースを埋める。
4. `--repos`/`--repo-dir` を付けたい場合は `codd_gate_routing.build_routing_args(repos_path, name, vcwd)`
   を `run_verify` に渡す `cmd` 文字列の組み立てに反映する（現状 `agent-project.yaml.example` の
   コメント例は `--base` のみのシンプル形なので、今回の完了条件（grep パターン）を満たすだけなら
   この4番目の対応は必須ではない——将来の拡張余地として記す）。

### 6.2 intake（e1/e2 相当）— 挿入点: `agent_project/model.py:463-` (`run_intake`)、呼び出し元
`mr.py:529` と `loop.py:576`

現状:
```python
p = subprocess.run(cfg.intake_cmd, shell=True, cwd=str(cfg.workdir),
                   capture_output=True, text=True, timeout=cfg.verify_timeout)
```
regression と同型の問題（未導入時に exit≠0 として journal に「intake NG」が残るだけで実害は無いが、
検出結果に応じた明示的スキップの方が意図が明確）。`codd_gate_debt.parse_debt_output()` は
「`tasks --debt` の stdout をパースして `DriftItem` に正規化する」役割だが、`run_intake` は現状
`json.loads(out)` を直接呼んでおり（`model.py:494` 付近）**この専用パーサを経由していない**。

**推奨する結線**:
1. `run_intake` 冒頭で `cfg.intake_cmd` が codd-gate 由来なら `detect_status().usable` を確認し、
   未導入なら早期 `return []`（既存の `if not cfg.intake_cmd: return []` と同じパターンに揃える）。
2. `subprocess.run` の呼び出しを `status.command("tasks", "--debt", *routing_args)` 由来の argv に
   置き換えるか、少なくとも stdout パースを `codd_gate_debt.parse_debt_output(p.stdout)` 経由にして
   1レコード不備で全体を落とさない防御的パースの恩恵を受ける（現状は `json.loads` が失敗したら
   intake 全体を握りつぶす設計——`model.py:493-` の例外ハンドラを要確認。範囲外のため詳細読み込みは
   していない）。

### 6.3 検出状態の可視化（任意・完了条件には無関係）— 挿入点: `agent_project/doctor.py:186`
`doctor_env_findings` は `agent-flow`/`git`/PATH 系の実在チェックを findings 化しているが、
codd-gate は対象外。`codd_gate_status.detect_status().findings` をここへ合流させると `agent-project
doctor` から codd-gate 未導入・バージョン不整合が可視化できる（今回の完了条件のスコープ外・t2/t3/t4
の goal 文にも含まれないため、実装は not-in-scope として報告するのみ）。

## 7. 完了条件4コマンドの現状（本メモ執筆時点、変更なしで確認）

| # | コマンド | 現状 | 理由 |
|---|---|---|---|
| 1 | `grep regression_cmd:.*codd-gate verify --base` | **NG** | yaml に未設定（§3） |
| 2 | `grep intake_cmd:.*codd-gate tasks` | **NG** | yaml に未設定（§3） |
| 3 | `PYTHONPATH=... python3 -c 'detect_status()...'` | **OK（無変更で通過）** | §4 で実行確認済み |
| 4 | `pytest test_codd_gate_detect.py test_codd_gate_routing.py` | **OK（無変更で通過、29 passed）** | §4 で実行確認済み |

→ t2 の実装対象（`codd_gate_status.py`）は既に存在し検証済みのため、t2 は「新規実装」ではなく
「既存実装のレビュー・（§5 で触れたバージョン/schema 合流などの）強化要否の判断」が実質的な作業になる
可能性が高い。t3・t4 が今回の run で価値を生む本体（yaml 結線＋ §6 のルーティング配線／既存2テストの
再確認と、必要なら追加ケースの補強）。

## 8. 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- 本タスク（t1）は調査のみで、コード・yaml の変更は行っていない（実装は t2/t3/t4 の責務。graph.json
  で確認済み——本 run のタスクグラフは t1(調査)→{t2,t3,t4}(並列実装)→gate(検証)→synth→loop(反復)→docs
  という構成で、ワーカー冒頭に渡された「完了条件」シェルコマンドは *run 全体（meta.json の
  request）* の DoD であり t1 単体の完了条件ではないと判断した）。
- 調査対象の実ファイルは `/Users/nitto/Workspace/sandbox`（main ブランチ）にあると判断した
  （自 worktree はスパースで対象ファイルを持たないため）。読み取りのみ行い書き込みはしていない。

**未解決事項（t2/t3/t4・gate へ申し送り）**:
- `agent_project/__init__.py` が各サブモジュールをどう合成しているか（`exec` 順・共有名前空間）を
  未確認。`codd_gate_status`/`codd_gate_base` の import 追加が合成順や名前衝突を起こさないか、
  t2/t3 は着手前に `agent_project/__init__.py` を確認すること。
- `model.py` の `run_intake` が `json.loads` 失敗時にどう振る舞うか（`model.py:494` 以降）は未読了。
  `codd_gate_debt.parse_debt_output` へ置き換える際の互換性は t3/t4 側で要確認。

**範囲外で見つけた問題（直していない。報告のみ）**:
- `codd_gate_detect.py`/`codd_gate_status.py`/`codd_gate_base.py`/`codd_gate_debt.py` の docstring が
  参照する `agent-project.py:XXXX` 行番号は、パッケージ分割後の現状と一致せず全て陳腐化している
  （§2 参照）。
- `/Users/nitto/Workspace/sandbox` は本 request と無関係な大規模差分（kiro→agent リネーム等）を
  多数抱えた共有チェックアウトであり、t2/t3/t4 が直接書き込むと巻き込まれるリスクがある（§1 参照）。
