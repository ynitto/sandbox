# t7: coddgate.py に `codd_gate_verify()` を追加

**切り口**: 他候補が「t7-t9 予定分まで前倒しで一括実装」や「既存 `codd_gate_hooks.run_diff_gate`/
`codd_gate_invoke.invoke_codd_gate`（bool/3値 status ベース）へラップして委譲」に流れる可能性が
ある中、本候補はタスク文の指示を字義どおり **`subprocess` を直接呼び、終了コード・stdout・stderr を
生のまま構造化する** 実装を選び、既存の3値 status モデルとは独立させた。理由: 対象ファイル
（`kiro_project/coddgate.py`）はフラットな `codd_gate_invoke.py`/`codd_gate_hooks.py` とは別系統
（未マージのパッケージ化 refactor 側）の断片であり、断片規約上モジュールレベル import
（`from codd_gate_invoke import ...` 等）ができないため、委譲は選択できない。

## 成果

`tools/kiro-project/kiro_project/coddgate.py`（t5/t6 の既存2シンボルに追記、既存行は無変更）に
以下を追加。

```python
CODD_GATE_VERIFY_TIMEOUT = 120.0


@dataclass(frozen=True)
class CoddGateVerifyResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def codd_gate_verify(
    repos_path: str,
    repo_dir: str,
    base_rev: str,
    strict: bool = True,
    *,
    run=subprocess.run,
    timeout: float = CODD_GATE_VERIFY_TIMEOUT,
) -> "CoddGateVerifyResult | CoddGateNoopResult":
    if not codd_gate_enabled():
        return codd_gate_noop_result(f"{CODD_GATE_BINARY_NAME} が見つからない（PATH 未検出）")
    argv = [
        CODD_GATE_BINARY_NAME, "verify",
        "--repos", str(repos_path),
        "--repo-dir", str(repo_dir),
        "--base", str(base_rev),
    ]
    if strict:
        argv.append("--strict")
    try:
        proc = run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return codd_gate_noop_result(f"codd-gate の呼び出しがタイムアウトした（{timeout}s）")
    except (OSError, subprocess.SubprocessError) as exc:
        return codd_gate_noop_result(f"codd-gate の起動に失敗した: {exc}")
    return CoddGateVerifyResult(
        exit_code=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or "",
    )
```

- `git status --short` は `M tools/kiro-project/kiro_project/coddgate.py`（+57行）のみ。他ファイル無変更。

## 採用した前提

1. **`repo_dir` の意味**: タスク文の具体例 `--repo-dir sandbox=.` は、`--repos ...`/`--base ...`
   と同じく「`repos_path`/`base_rev` パラメータがこの run の文脈でどう実引数になるか」を示す
   プレースホルダ列挙と解釈した。つまり `repo_dir` パラメータは `--repo-dir` の**値全体**
   （`NAME=DIR` 文字列。例 `"sandbox=."`）をそのまま受け取り、`str(repo_dir)` として渡す——
   関数側で `"sandbox="` を固定接頭辞として焼き込まない。根拠: 既存の `codd_gate_routing.py`
   （フラット側の既実装）は `resolve_repo_dir_arg(name, dir)` のように name/dir を分離した
   別関数として持っており、もし t7 のシグネチャが同じ分離を意図するなら引数は
   `(repos_path, name, dir, base_rev, strict)` のような4分割になっているはずだが、タスク文は
   明確に3位置引数 `(repos_path, repo_dir, base_rev)` のみを指定している。関数を呼び出し側に
   依存させず「sandbox」という特定 repo 名をパッケージ内に固定しない設計のほうが、
   `kiro_project` という汎用パッケージの責務にも合致する。
2. **戻り値の型**: タスク文「終了コード・stdout・stderr を構造化して返す」を素直に
   `CoddGateVerifyResult(exit_code, stdout, stderr)`（frozen dataclass、`ok` プロパティ付き）
   として新設。既存 `codd_gate_invoke.CoddGateResult`（3値 status: ok/failed/skipped）とは
   意図的に統合しなかった——理由は t6 と同じで、対象ファイルは別系統の断片でありモジュール間
   import ができないため、独立した値オブジェクトにせざるを得ない。
3. **縮退条件の範囲**: 「未インストール時は t6 の縮退結果を返す」を文字どおり実装（
   `codd_gate_enabled() is False` → `codd_gate_noop_result(...)`）。加えて、実行時のタイムアウト・
   起動失敗（`subprocess.TimeoutExpired`/`OSError`/`SubprocessError`）も同じ縮退結果へ倒した。
   これはタスク文に明示はないが、フラット側の既存実装 `codd_gate_invoke.invoke_codd_gate` が
   同じ3つの失敗系統をすべて no-op 縮退に寄せる設計（「任意連携は例外を外へ漏らさない」）を
   採っており、この結線点だけ例外を伝播させると縮退方針に矛盾するため、既存の設計判断を
   踏襲した。
4. **DI（`run=subprocess.run`, `timeout=`）**: `invoke_codd_gate` と同じ理由（テスト容易性・
   タイムアウト値の呼び出し側上書き）で踏襲した。`subprocess` はモジュールレベルで import せず
   （断片規約）、デフォルト引数値としてのみ参照する——`_head.py`（main）が exec 合成時点で
   `subprocess` を共有名前空間へ供給済みという t5/t6 と同じ前提に依存する。

## 検証内容と結果

- `python3 -m py_compile tools/kiro-project/kiro_project/coddgate.py` → 構文OK。
- `grep -rq "codd_gate" tools/kiro-project/kiro_project/` → 終了コード0。
- `python3 -m pytest tools/kiro-project/tests -q -k codd` → **63 passed**（t2/t3/t5/t6 と同値、無回帰）。
- `git status --short` → `tools/kiro-project/kiro_project/coddgate.py` のみ変更（+57行）。
- 断片は単体 import 不可のため、`_head` 相当の名前空間（`shutil`/`subprocess`/`dataclass`/`field`
  を注入し `sys.modules` に登録した擬似モジュール）を構築して実行時検証した:
  - **未インストール相当**（`shutil.which` を `None` 固定に差し替え）: `codd_gate_verify(...)` が
    `CoddGateNoopResult(skipped=True, ok=True, reason=...)` を返すことを確認。
  - **実インストール環境**（本マシンの実 `codd-gate` バイナリ、実リポジトリに対する実行）:
    `codd_gate_verify("./.kiro-project/repos.json", "sandbox=.", "HEAD~1", strict=True)` を実行し、
    `CoddGateVerifyResult(exit_code=1, stdout="...GRAY: tools/kiro-project/kiro_project/coddgate.py は
    ドキュメント・テストのどちらにも接続が無い...", stderr="")` を得た（exit_code=1 は
    codd-gate 自身が「本 coddgate.py がまだドキュメント・テストに未接続」と判定した本物の
    ドリフト検知——関数の不具合ではない。run 全体の完了条件が求める `codd-gate verify --strict`
    の実 exit 0 化には、後続タスクでの接続マップ登録／テスト追加が必要）。
  - `run=` に偽関数を注入し、`subprocess.TimeoutExpired`／`OSError` 送出時にそれぞれ
    `CoddGateNoopResult` へ縮退することを確認。
  - `run=` が返す偽 `proc`（`returncode=0`/`returncode=1`）に対し `CoddGateVerifyResult.ok` が
    それぞれ `True`/`False` になることを確認。

## 未解決事項・範囲外で見つけた問題

1. **run 全体の完了条件の3つ目**（`codd-gate verify --repos ./.kiro-project/repos.json
   --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict` の実 exit 0）は、
   本タスクで実装した Python 関数の正しさとは別に、**リポジトリ自体のドキュメント・テスト接続
   状態**に依存する。現状 `tools/kiro-project/kiro_project/coddgate.py` 自身が GRAY
   （未接続）と判定されており、これは t5〜t7 のどのタスクの範囲にも含まれない
   （接続マップ／テスト整備は別タスクの担当）。関数実装自体はこの完了条件を妨げない
   （むしろ正しく動作していることをこの実行結果が証明している）。
2. **`__init__.py`／`_FRAGMENTS` 登録・main のパッケージ化 refactor 未マージ**: t1・t3・t5・t6が
   既報告のとおり未解決のまま引き継ぐ。本タスクでも `coddgate.py` は依然として単体 import
   不可・`kiro_project` パッケージとして未結線。
3. **範囲外（未実施）**: 新規テストの追加（t5/t6 と同じ理由で見送り、実行時の擬似モジュール
   検証で代替）。`run_diff_gate`（フラット側の既存実装）との機能重複解消・統合方針の決定
   （t3 が指摘済み）は本タスクでは判断せず、後続の統合専任タスクに委ねる。

data: {"delivery": {"url": "https://github.com/ynitto/sandbox/", "branch": "kp/kiro-project-codd-gate-171537", "target": "main", "path": ""}}
