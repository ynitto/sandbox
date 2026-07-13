# codd_gate_detect.py — 呼び出し側契約（存在検出 API）

対象: `tools/kiro-project/codd_gate_detect.py`（依存は標準ライブラリのみ）。
このモジュールの責務は「codd-gate CLI が実在するか／何を提供するか」の**生の判定**のみ。
finding 化・no-op 縮退・プロセス内キャッシュ・kiro-project.py 本体への結線は `codd_gate_status.py`
以降の責務であり、本モジュールには含まれない。

公開関数は 5 つ（他はアンダースコア始まりの private ヘルパーで契約対象外）。

---

## 1. `resolve_codd_gate(explicit=None, which=shutil.which) -> list[str] | None`

codd-gate の起動 argv prefix を `explicit → PATH → 同梱パス` の順で解決する。

| 項目 | 内容 |
|---|---|
| 引数 `explicit` | `str \| None`。渡されればこれを最優先（`which` は呼ばれない）。`.py` で終われば `[sys.executable, explicit]`、それ以外は `[explicit]`。**存在確認はしない**（explicit 指定時はパスの実在チェックなしでそのまま argv 化される） |
| 引数 `which` | `Callable[[str], str \| None]`。デフォルト `shutil.which`。DI 用 |
| 戻り値型 | `list[str]`（argv prefix）または `None` |
| **未インストール時** | `explicit` 未指定 かつ PATH 上に `codd-gate` が無く かつ同梱パス（`<このファイルの親の親>/codd-gate/codd-gate.py`）も存在しない場合 → **`None` を返す**（例外は投げない） |
| **例外送出の有無** | **本体に try/except は無い**。`which` や `Path.exists()` が想定外の例外（`OSError` 等）を送出した場合、それは**呼び出し側へそのまま伝播する**。呼び出し側（`codd_gate_status.detect_status`）はこれを自前の try/except で「未検出」へ縮退させている——本関数自体はその防御をしない |

## 2. `resolve_codd_gate_bin(config_bin=None, env=None, which=shutil.which) -> str | None`

`resolve_codd_gate` の `explicit` に何を渡すか決める前段の解決連鎖: `環境変数 CODD_GATE_BIN → config_bin → PATH`。

| 項目 | 内容 |
|---|---|
| 引数 `config_bin` | `str \| None`。呼び出し側が設定ファイル読み込み後に取り出した値をそのまま渡す（本関数はファイル I/O をしない） |
| 引数 `env` | `dict[str, str] \| None`。`None` なら実 `os.environ` を使う |
| 引数 `which` | DI 用、デフォルト `shutil.which` |
| 戻り値型 | `str`（バイナリパス／コマンド名の文字列。`resolve_codd_gate` と違い **list ではない**）または `None` |
| 空文字列・空白のみの扱い | 各段は strip 後に空なら「未設定」として次段へフォールバック（`CODD_GATE_BIN="   "` は config へ、config が空文字なら PATH へ） |
| **未インストール時** | 3 段すべて未設定／未検出 → `None` |
| **例外送出の有無** | **関数全体が `try/except Exception: return None` で囲まれている**。`which` が任意の例外を送出しても外に漏れず `None` に縮退する。`resolve_codd_gate` とは対称的に**呼び出し側は例外を気にしなくてよい** |

## 3. `get_version(binary, run=subprocess.run, timeout=PROBE_TIMEOUT) -> tuple[int, int, int] | None`

`<binary> --version` を実行し `codd-gate X.Y.Z` パターンでバージョンを取得する。

| 項目 | 内容 |
|---|---|
| 引数 `binary` | `list[str]`。通常 `resolve_codd_gate` の戻り値をそのまま渡す |
| 引数 `run` | `subprocess.run` 互換 callable。DI 用、デフォルト `subprocess.run` |
| 引数 `timeout` | `int`（秒）。デフォルト `PROBE_TIMEOUT = 5` |
| 戻り値型 | `tuple[int, int, int]` または `None` |
| **未インストール時** | `run` が `FileNotFoundError`（`OSError` の派生）を送出 → 内部で捕捉し `None`。同様に非 0 終了 → `None`、`--version` 出力が正規表現 `codd-gate (\d+)\.(\d+)\.(\d+)` にマッチしない → `None`、`subprocess.TimeoutExpired`（`SubprocessError` の派生）→ `None` |
| **例外送出の有無** | `except (OSError, subprocess.SubprocessError)` のみを捕捉。**この2系統以外の例外（例: DI された `run` が独自に `RuntimeError` 等を送出した場合）は捕捉されず呼び出し側へ伝播する**。テスト側コメントは「get_version 自体は例外を投げない設計」だが、実装上のガードはこの2例外系統に限定されており、`codd_gate_status.detect_status` は呼び出し側でさらに防御的 try/except を重ねている |

## 4. `check_repos_schema_compat(repos_path) -> tuple[bool, str]`

`repos.json`（`export_repo_registry` の出力）が `repos.schema.json` の最小要件を満たすか検証する。**codd-gate バイナリを一切起動しない**（ローカル JSON 検証のみ）ため「未インストール時」の概念自体が適用されない。

| 項目 | 内容 |
|---|---|
| 引数 `repos_path` | `str \| Path` |
| 戻り値型 | `tuple[bool, str]` — `(ok, detail)`。`ok=True` のとき `detail == ""`。`ok=False` のとき `detail` に日本語の理由文字列 |
| 判定ルール | トップレベルが object であること／`_` 始まり以外のキーの値が object であること |
| **例外送出の有無** | `except (OSError, ValueError)` のみを捕捉（読み込み失敗・JSON パース失敗）。それ以外の型不正（例: `repos_path` が非対応型で `Path()` コンストラクタが `TypeError` を出す等）は**捕捉されず伝播する** |

## 5. `detect_capabilities(binary, run=subprocess.run, timeout=PROBE_TIMEOUT) -> dict[str, bool]`

`--help` / `<サブコマンド> --help` を実プローブし、`verify`・`tasks` サブコマンドと `--debt` フラグの利用可能性を返す。

| 項目 | 内容 |
|---|---|
| 引数 | `get_version` と同型（`binary: list[str]`, `run`, `timeout`） |
| 戻り値型 | `dict[str, bool]`。**キーは常に固定 3 つ** `{"verify": bool, "tasks": bool, "debt": bool}`（`None` は返さない） |
| `debt` の判定 | `verify`・`tasks` のうち利用可能な方だけを対象に `--debt` 対応を個別プローブし、**対象が1つ以上かつ全て対応**のときのみ `True`。対象が0件（=`verify`も`tasks`も無い）なら空リスト `bool([])==False` |
| **未インストール時** | `--help` プローブが失敗（`OSError`/`SubprocessError` 系。非0終了含む）→ 内部ヘルパー `_list_subcommands`/`_subcommand_supports_flag` がそれぞれ `set()`/`False` に縮退 → 最終的に **`{"verify": False, "tasks": False, "debt": False}`**（例外は投げない） |
| **例外送出の有無** | 本体に try/except は無いが、内部で呼ぶ2つの private ヘルパーが `(OSError, subprocess.SubprocessError)` を捕捉するため、実質的にこの2系統の失敗では例外を送出しない。それ以外の例外型は伝播しうる |

---

## 呼び出し側が踏まえるべき設計原則（docstring より）

- 「わからない」を「大丈夫」に丸めない: 全関数が失敗時に **False 側（None / False / 空dict値）** へ倒す設計方針で統一されている。
- ただし **例外を確実に飲み込むのは `resolve_codd_gate_bin` のみ**。`resolve_codd_gate` / `get_version` / `check_repos_schema_compat` / `detect_capabilities` は特定の例外系統（主に `OSError`, `subprocess.SubprocessError`, `ValueError`）だけを捕捉する設計であり、DI された `which`/`run` が想定外の例外型を送出すると呼び出し側まで伝播しうる。現に `codd_gate_status.detect_status` は `resolve_codd_gate` と `get_version` の呼び出しをそれぞれ自前の try/except で包んでおり、これは本モジュールが提供しない防御を呼び出し側が追加で担っている実例。
- 推奨合成パターン（モジュール docstring 記載）:
  `resolve_codd_gate(resolve_codd_gate_bin(cfg.codd_gate_bin), which=shutil.which)`
- `resolve_codd_gate` / `get_version` の生の戻り値を受けて finding 化・no-op 縮退の判断を行う合流点は `codd_gate_status.py` の `build_status(binary, version=..., version_known=..., schema_ok=...)`。本モジュール自体は「使ってよいか」を判断しない。

---

## 検証内容と結果

- `tools/kiro-project/tests/test_codd_gate_detect.py` を `python3 -m unittest tests.test_codd_gate_detect -v` で実行し、32 件全て成功（上記の戻り値・例外挙動の記述はこのテストで実証済みの内容と一致させている）。
- ソースは `tools/kiro-project/codd_gate_detect.py` を全文読了（182行）。呼び出し元 `codd_gate_status.py`（`get_version`, `resolve_codd_gate` を import）・`codd_gate_invoke.py`（docstring 参照のみ）・`codd_gate_debt.py`（docstring 参照のみ）を grep で確認し、実際の消費パターンを契約記述に反映した。

## 前提・未解決事項・範囲外で見つけた問題

- 前提: 本タスクは「存在検出 API を呼び出し側契約として列挙する」調査タスクであり、コード変更は行っていない（`codd_gate_detect.py` / テストとも既存のまま）。
- 未解決事項: なし（対象ファイルの公開関数 5 つを全て列挙済み）。
- 範囲外で見つけた事項（本タスクでは修正しない）: `resolve_codd_gate` と `get_version`/`check_repos_schema_compat`/`detect_capabilities` の間で「例外を飲み込む範囲」が不揃い（`resolve_codd_gate_bin` は全例外を飲み込むが、他は特定の例外系統のみ）。現状は呼び出し側（`codd_gate_status.detect_status`）が個別に追加の try/except を重ねて吸収しており、モジュール全体としては動作しているが、モジュール docstring の「わからない・不足は連携しない側に倒す」という一貫方針からすると非対称である。この run の別タスク（b1-b3/c1-c2/e1-e2 等、kiro-project.py 本体への結線）で新たな DI 呼び出し元を追加する際は、この非対称性を踏まえて呼び出し側で防御的 try/except を用意する必要がある点に注意。
