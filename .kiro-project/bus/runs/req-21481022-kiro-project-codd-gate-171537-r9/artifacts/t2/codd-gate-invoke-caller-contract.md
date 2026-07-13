# codd_gate_invoke.py 呼び出し側契約

対象ファイル: `tools/kiro-project/codd_gate_invoke.py`（+ 依存する `codd_gate_status.py`）
テスト: `tools/kiro-project/tests/test_codd_gate_invoke.py`（6テストケース、全て `run=` DI でプロセス起動なし）

## 1. 公開関数シグネチャ

```python
def invoke_codd_gate(
    status,                       # codd_gate_status.CoddGateStatus インスタンス（必須・位置引数）
    *args: str,                   # codd-gate へ渡す追加 argv（例: "verify", "--repos", "repos.json", "--strict"）
    run=subprocess.run,           # プロセス起動関数の DI。テストではフェイクに差し替える
    timeout: float = DEFAULT_TIMEOUT,  # 秒。既定 120.0（kiro-project.py の verify_timeout 既定と揃えてある）
) -> CoddGateResult
```

```python
@dataclass(frozen=True)
class CoddGateResult:
    status: str                  # "ok" | "failed" | "skipped" の3値のみ
    exit_code: "int | None"      # プロセス完走時のみ int。未完走（skipped の一部）は None
    stdout: str                  # ok/failed は proc.stdout（Noneなら空文字列に正規化済み）、skipped は常に ""
    reason: str = ""             # ok は常に空文字列。failed/skipped は理由文字列（最大500文字）

    @property
    def ok(self) -> bool:        # status == "ok" の糖衣
        ...
```

前段の `CoddGateStatus`（`codd_gate_status.py`）側の契約:

```python
status.usable: bool              # binary is not None and not findings
status.command(*args) -> "list[str] | None"   # usable でなければ None、そうでなければ [*binary, *args]
status.reason: str                # findings[0]["title"]。usable なら空文字列
```

## 2. 終了コードの返し方（呼び出し側が見るべき唯一の分岐）

呼び出し側は `result.status` の3値だけを見ればよい。`exit_code` は補助情報（ログ・診断用）で、
分岐条件として使う必要はない。

| 実行結果 | `status` | `exit_code` | 意味 |
|---|---|---|---|
| プロセス完走・returncode == 0 | `"ok"` | `0` | codd-gate 自身が「一貫性ゲート通過」と判定 |
| プロセス完走・returncode != 0 | `"failed"` | 実際の returncode（int） | codd-gate 自身が「NG」と判定した本物のゲート失敗 |
| `status.usable == False`（未検出・非互換） | `"skipped"` | `None` | プロセスを一切起動せず即座に縮退 |
| 起動失敗（`OSError` / `subprocess.SubprocessError`） | `"skipped"` | `None` | バイナリが実行時に消えた等 |
| タイムアウト（`subprocess.TimeoutExpired`） | `"skipped"` | `None` | 既定 120s（呼び出し側で上書き可） |

**呼び出し側の推奨分岐パターン**:
```python
result = invoke_codd_gate(status, "verify", "--strict")
if result.status == "skipped":
    ...  # 既存の「ゲート無効時と同じ」挙動へフォールバック。done/regression 判定を止めない
elif result.status == "failed":
    ...  # 本物のゲート失敗として受け止める（result.reason に exit=N と出力末尾500文字）
else:  # "ok"
    ...
```
`result.ok`（`status == "ok"` の bool）は真偽判定の糖衣として使える。

## 3. タイムアウトの扱い

- 既定値は `DEFAULT_TIMEOUT = 120.0`（秒）。呼び出し側が `timeout=` で上書き可能。
- `run(argv, capture_output=True, text=True, timeout=timeout)` の形で `subprocess.run` 互換の
  `run` 呼び出し規約に従う（DI のため、テストや将来の呼び出し側は任意の callable を渡せる）。
- タイムアウト発生時は `subprocess.TimeoutExpired` を関数内部で捕捉し、`CoddGateResult(status="skipped", exit_code=None, reason="codd-gate の呼び出しがタイムアウトした（{timeout}s）")` を返す。
  **例外は関数境界の外へ一切伝播しない。**

## 4. 例外の扱い（呼び出し側が try/except を書く必要がない設計）

`invoke_codd_gate` は以下の例外を内部で捕捉し、いずれも `status="skipped"` へ縮退させる。
呼び出し側はこの関数呼び出しを try/except で囲む必要がない（内部で完結する no-op 縮退契約）。

| 捕捉する例外 | 発生源 | 縮退後の reason |
|---|---|---|
| `subprocess.TimeoutExpired` | `run()` 呼び出し | `"codd-gate の呼び出しがタイムアウトした（{timeout}s）"` |
| `OSError` | `run()` 呼び出し（バイナリ不在等） | `"codd-gate の起動に失敗した: {exc}"` |
| `subprocess.SubprocessError`（`TimeoutExpired` 以外） | `run()` 呼び出し | `"codd-gate の起動に失敗した: {exc}"` |

`status.usable == False` の場合はそもそも `run()` を呼ばず（`argv = status.command(*args)` が
`None` を返した時点で即 return）、`status.reason`（未検出/非互換の理由）をそのまま `reason` に転記する。

未捕捉のまま外へ漏れる可能性がある例外: **無し**（テスト
`test_never_raises_for_any_injected_run_failure` が `OSError` / `SubprocessError` /
`TimeoutExpired` の3種について非送出を保証）。ただし `status` 引数の型が `CoddGateStatus` と
異なる（`command()` メソッドを持たない）場合の `AttributeError` 等、契約違反の呼び出しまでは
保護しない。

## 5. 呼び出し側（regression/acceptance/enqueue 等）が守るべき前提

1. `invoke_codd_gate` を呼ぶ前に `CoddGateStatus` を1回構築しておく（`detect_status()` または
   `build_status()`）。`status` 引数は必須・型契約あり。
2. 戻り値は `status` の3値分岐のみで処理し、`exit_code`/`stdout`/`reason` は診断・ログ表示にのみ
   使う（`failed` の判定条件として `exit_code` を直接比較する必要はない）。
3. `"skipped"` は「わからない・使えない」を意味し「NG」ではない。既存の
   ゲート無効時と同じフォールバック経路に合流させる（done/regression 判定を止めない）。
4. タイムアウトを明示指定したい場合のみ `timeout=` を渡す。既定 120s は
   `kiro-project.py` の `verify_timeout` 既定と揃っているため通常は省略でよい。
5. 追加 argv（`*args`）は `status.command()` を経由して先頭に `binary`（`["codd-gate"]` 等）が
   自動付与される。呼び出し側は `"verify"`, `"--repos"`, `<path>`, `"--strict"` のような
   サブコマンド以降だけを渡せばよい。

## 検証内容と結果

- `codd_gate_invoke.py` と `codd_gate_status.py` を全文精読し、シグネチャ・戻り値型・
  例外捕捉範囲・タイムアウト既定値をソースから直接抽出した（推測なし）。
- `tools/kiro-project/tests/test_codd_gate_invoke.py` の6ケース（ok/failed/timeout/OSError/
  usable=False 2パターン/全例外非送出の網羅テスト）を突き合わせ、上表の分岐が
  テストでも保証されていることを確認した。
- 本タスクは調査のみのため作業ツリーへの変更なし（コード変更ゼロ）。テスト実行はコード変更が
  ないため実施していない（読み取り専用タスクのため対象外）。

## 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: タスク文の「tasks 実行 API」は本モジュールには存在しない（`codd_gate_invoke.py` が
  提供するのは `verify` 系呼び出しの汎用ラッパー `invoke_codd_gate` のみで、`tasks` サブコマンド
  専用の別関数はない）。呼び出し側は `invoke_codd_gate(status, "tasks", ...)` のように同じ関数へ
  `args` で渡す設計と判断し、その前提でこの文書を記述した。
- **範囲外で見つけた問題**: 本体（`kiro-project.py`）側で `regression`/`acceptance`/`enqueue` の
  3フックへこの契約がどう結線されているかは本タスクの調査範囲外（モジュール冒頭のコメントにも
  「kiro-project.py 本体への結線は同一 run の他タスクの責務」と明記されている）。結線状況の
  確認は後続タスクに委ねる。
- 未解決事項なし。
