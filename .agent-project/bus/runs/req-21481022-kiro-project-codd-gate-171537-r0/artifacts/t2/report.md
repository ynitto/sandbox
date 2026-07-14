# t2: codd_gate_base.py 棚卸し — coddgate.py への移送/破棄一覧

## 前提（t1 の重大な前提崩れを追認）

作業ブランチ `kp/kiro-project-codd-gate-171537`（HEAD `99d71b2e`）のワークツリーには
`tools/kiro-project/kiro_project/` パッケージが存在しない。`tools/kiro-project/` は
単一ファイル `kiro-project.py`（約64万バイト）＋独立した `codd_gate_base.py` 他6ファイル
という構成のままで、t1 が報告した状態から変化していない。`kiro_project/__init__.py` /
`_FRAGMENTS` は `main` ブランチにのみ存在する。

この不整合の解消（main のパッケージ化 refactor を merge するか、単一ファイル向けに
計画を作り直すか）は t2 の範囲外・t1 が既に評価役への判断依頼として報告済みの事項。
本タスクは指示どおり `codd_gate_base.py` 1ファイルの棚卸しに専念し、ワークツリーへの
書き換えは行っていない（`git status` 差分なし）。

## codd_gate_base.py の棚卸し

ファイル全量: 55行。stdlib 依存は `os` のみ（`from __future__ import annotations` 済み）。

### 公開シンボル

| シンボル | 種別 | シグネチャ | 依存 |
|---|---|---|---|
| `FALLBACK_BASE_REV` | 定数 | `str = "HEAD~1"` | なし |
| `resolve_base_rev` | 純粋関数 | `resolve_base_rev(task_base_branch: str \| None = None, env: dict[str, str] \| None = None) -> str` | `os.environ`（`env` 未指定時のみ参照）。ファイル I/O・subprocess・repos.json 読込は一切なし |

### 挙動

優先順位は3段（前段が空文字なら次段へ）:
1. `env["KIRO_BASE_REV"]`（未指定なら `os.environ`）— strip 後に非空なら採用
2. 引数 `task_base_branch`（呼び出し側が charter の repo エントリから取り出した `base=` 値）— strip 後に非空なら採用
3. `FALLBACK_BASE_REV`（`"HEAD~1"`）

例外は投げない設計（`env` は plain dict 前提、I/O なしなので失敗しうる操作自体が無い）。

### 依存関係グラフ（このファイル単体）

- 内向き依存: なし（`kiro-project.py` 側の型 Task/Charter に非依存。呼び出し側がプリミティブ値を渡す設計）
- 外向き依存（このファイルを import する側）: `codd_gate_hooks.py` の1箇所のみ
  ```python
  from codd_gate_base import resolve_base_rev
  ...
  base_rev = resolve_base_rev(task_base_branch, env=env)  # run_diff_gate 内
  ```
  `codd_gate_hooks.run_diff_gate` が `resolve_base_rev` → `build_routing_args`
  （`codd_gate_routing.py`）→ `invoke_codd_gate`（`codd_gate_invoke.py`）の順に合成して
  `codd-gate verify --strict` の実引数を組み立てる。**サブプロセス実行（`codd_gate_invoke.py`）・
  パス解決（`codd_gate_routing.py`）・repos.json 読込/スキーマ検証（`codd_gate_detect.py`）は
  このファイルには一切含まれない** — それらは t3 の担当4ファイル側にある。タスク文の
  「共通ヘルパ（subprocess実行・パス解決・設定/repos.json読込など）」という記述は
  `codd_gate_*` 一式全体を指しており、`codd_gate_base.py` 単体の実体は base-rev 解決の
  1関数のみ。この非対称を誤認しないよう明記しておく。

### テストカバレッジ

`codd_gate_base.py` 専用の単体テストファイル（`test_codd_gate_base.py`）は存在しない。
`resolve_base_rev` は `tests/test_codd_gate_hooks.py` 経由で `run_diff_gate` の合成結果として
間接的にしか検証されていない（優先順位3段の単体境界値テストは無い）。現行テストは
`python3 -m pytest tools/kiro-project/tests -q -k codd` で63件全通過（実行確認済み、詳細は下記）。

## 移送する要素（coddgate.py へ）

- `FALLBACK_BASE_REV` 定数 — そのまま移送。値・意味とも変更不要（シェル側の既定値
  `${KIRO_BASE_REV:-HEAD~1}` と対応させる必要があるため固定）。
- `resolve_base_rev` 関数本体（優先順位ロジック・シグネチャとも変更不要）。ただし
  t3 の統合方針「関数名は `codd_gate_` 接頭辞で統一」に合わせ、**`codd_gate_resolve_base_rev`
  へ改名**して移送することを推奨（t5-t9 で新設される `codd_gate_enabled` /
  `codd_gate_verify` / `codd_gate_detect_drift` / `codd_gate_debt_status` /
  `codd_gate_summary_text` と命名規約を揃える）。t7 の `codd_gate_verify(repos_path,
  repo_dir, base_rev, strict=True)` は `base_rev` を解決済み値として引数で受け取る設計
  なので、この関数は `codd_gate_verify` 内部に畳み込まず、`verify.py` の回帰ゲート側が
  呼び出し前に使う独立ヘルパとして残すのが t7 のシグネチャと整合する。
- 優先順位テストのギャップを埋める単体テスト（env 明示・branch 明示・両方空の3ケース、
  空文字と None の区別）を新規追加してこの関数に同梱する — charter の「ユニットテストを
  拡充すること」にも合致する。

## 破棄する要素

- モジュール docstring 中の run-artifact 相互参照（`d2（.kiro-project/bus/runs/
  run-20260712-213419-5922/artifacts/d2/...）4.1節` 等）と「このモジュールが意図的に
  含めないもの」節にある他タスクID（b3/b2/a1/a4）への言及。t1 が確認した断片規約
  （1-4行程度の要約コメントのみ、機能要約に絞る）に合わせ、これらの過去 run 固有の
  経緯説明は移送先では不要——`coddgate.py` は単一ファイルに統合されるため「このモジュールが
  含めないもの」という切り分け自体が意味を失う。
- ファイル単体としての `codd_gate_base.py` は t2 の指示どおり削除対象（t3 側4ファイルと
  同様、`codd_gate 一式` の統合後は個別ファイルとして残さない）。実処理を持たない
  「破棄」対象のロジックはこのファイルには存在しない（55行中、コメント/docstring を除く
  実効コードは定数1行+関数本体約8行のみで、無駄・重複コードなし）。

## 検証

- `git status`: ワークツリー差分なし（調査のみのため無変更）。
- `python3 -m pytest tools/kiro-project/tests -q -k codd` を実行し 63 passed（3 subtests
  含む）を確認——現行 `resolve_base_rev` の挙動が壊れていないことの裏取り（この報告の
  記述はこの実行結果と実ソースコード読解のみに基づく）。
- 本タスク自体に個別の完了条件は割り当てられておらず（`work` 種別、成果物は本レポート）、
  run 全体の完了条件（`kiro_project/` パッケージ配下への実配線・`codd-gate verify --strict`
  成功）は t5 以降 + gate タスクの担当。ここでは実行していない。

## 採用した前提・未解決事項・範囲外で見つけた問題

- 前提: タスク文の「共通ヘルパ」という表現は `codd_gate_*` 一式全体を指す総称と解釈し、
  `codd_gate_base.py` 単体が実際に持つ機能（base-rev 解決のみ）との差分を上記「棚卸し」
  節で明示した。
- 未解決事項（t2 の範囲外、評価役/後続タスクの判断が必要）: t1 が報告した
  「作業ブランチに `kiro_project/` パッケージが存在しない」問題は本タスク実行時点でも
  未解消。t5（`coddgate.py` 新規作成、deps: t1/t2/t3）が着手する前に、main のパッケージ化
  refactor を merge するか計画を単一ファイル向けに作り直すかの意思決定が必要。
- 範囲外で見つけた問題: `codd_gate_base.py` に専用単体テストが無く、優先順位ロジックの
  境界値（env のみ空文字 vs None、branch のみ空文字 vs None 等）が未検証。移送時に
  テスト追加を推奨するが、追加作業自体は本タスクでは行っていない。
