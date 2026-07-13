# synth_verify / _first_command_line 実装調査（現状の逐語書き出し）

対象ファイル: `tools/kiro-project/kiro-project.py`
（worktree: `kp/synth_verify-_first_comm-172544`、HEAD = `9fcf0e9`）

## 位置づけ

`_first_command_line` は `synth_verify`（L2968-2993）が LLM 応答文字列 `out` から
「先頭のコマンド行」を抜き出すために呼ぶ唯一のパーサ（L2982）。戻り値が偽なら
`retry_note` を立てて再合成へ回る（コードフェンス対応の有無が verify 合成の成否を
直接左右する）。

## 逐語コード

### `_first_command_line`（L2953-2965）

```python
def _first_command_line(out: str) -> Optional[str]:
    """合成出力の先頭のコマンド行を返す。どの規則にも合わなければ None。

    コードフェンスを最優先でスキャンする: フェンスが見つかれば、フェンス内の最初の
    非空・非コメント行を無条件でコマンドとして採用する。フェンスが一つも無ければ、
    フェンス外の行を対象にした従来ロジック（既知コマンド語などの先頭トークン判定 +
    sh -n 構文チェック）へフォールバックする。
    """
    fenced = _first_executable_line(_code_fence_lines(out), require_shell_syntax=False)
    if fenced:
        return fenced
    lines = (out or "").splitlines()
    return _first_executable_line([line for line in lines if _has_command_like_leading_token(line.strip())])
```

- **戻り値の型**: `Optional[str]`。
- **None になる条件**: フェンス内候補が無く（`_code_fence_lines` が空、または全行が
  空行／`#` コメント／フェンス言語タグ／該当なし）、かつフェンス外にも
  `_has_command_like_leading_token` を満たす行が無い場合。両経路とも
  `_first_executable_line` が `None` を返した場合に限り最終的に `None` になる。
- **行分割**: フェンス外経路は `(out or "").splitlines()`。フェンス内経路は
  `_code_fence_lines(out)` が内部で `splitlines()` している（下記）。
- **正規表現**: このメソッド自体は正規表現を直接使わない。呼び出し先
  （`_code_fence_lines`, `_has_command_like_leading_token`, `_looks_like_shell_command`）
  に正規表現が現れる。

### `_first_executable_line`（L2934-2950、`_first_command_line` の共通実装）

```python
def _first_executable_line(lines: list[str], *, require_shell_syntax: bool = True) -> Optional[str]:
    """候補行から最初のコマンドを返す。見つからなければ None。

    require_shell_syntax=False の場合は `_looks_like_shell_command` の sh -n 構文チェックを
    課さない。コードフェンスで明示的に区切られた行は LLM の意図（これがコマンドである）が
    明確なため、素通しで信頼する（フェンス外の地の文はこの限りでなく従来どおり厳格に見る）。
    """
    for raw_line in lines:
        line = _strip_code(raw_line.strip())
        if (
            line
            and not line.startswith("#")
            and line.casefold() not in _SHELL_FENCE_LANGUAGE_TAGS
            and (not require_shell_syntax or _looks_like_shell_command(line))
        ):
            return line
    return None
```

- **戻り値の型**: `Optional[str]`。ループを最後まで回って一致が無ければ関数末尾の
  `return None` に到達する。
- **フェンス内呼び出し**（`require_shell_syntax=False`）: `sh -n` 構文チェックを課さず、
  空行・`#` コメント行・`_SHELL_FENCE_LANGUAGE_TAGS`（言語タグの残骸）以外の最初の行を
  無条件で採用する。
- **フェンス外呼び出し**（既定 `require_shell_syntax=True`）: 上記に加えて
  `_looks_like_shell_command(line)`（`sh -n` 構文チェック＋全角句読点弾き）を通過した
  行のみ採用する。

### `_code_fence_lines`（L2883-2906）

```python
_FENCE_OPEN_RE = re.compile(r"```(\w*)\s*$")


def _code_fence_lines(out: str) -> list[str]:
    """Markdown コードフェンス内の行を、ブロックの出現順に返す。

    開始フェンスは言語タグの有無を問わない。「実行してください: ```bash」のように
    同一行にフェンスの前置き文が同居していても、行末が ``` (+言語タグ) であれば開始と
    認識する（行頭一致 startswith だけだと前置き同居ケースを取りこぼすため）。
    閉じフェンスがなければ、入力末尾までをそのブロックの内容として扱う。
    """
    fenced_lines: list[str] = []
    in_fence = False
    for line in (out or "").splitlines():
        marker = line.strip()
        if in_fence and marker == "```":
            in_fence = False
            continue
        if not in_fence and _FENCE_OPEN_RE.search(marker):
            in_fence = True
            continue
        if in_fence:
            fenced_lines.append(line)
    return fenced_lines
```

- **正規表現**: `_FENCE_OPEN_RE = re.compile(r"```(\w*)\s*$")`。`.search(marker)` で
  「行末が ``` （+ 任意の言語タグ語 \w*）で終わる」行を開始フェンスとみなす。
  `startswith` ではなく行末一致なので、`「実行してください: \`\`\`bash」` のように
  前置き文とフェンスが同居していても開始として拾える。閉じフェンスは
  `marker == "```"`（前置き・言語タグを許さない厳密一致）のみ。
- **行分割**: `(out or "").splitlines()`。
- **戻り値の型**: `list[str]`（フェンス内側の生の行を出現順に連結。複数フェンスブロック
  があれば全ブロック分を結合する）。
- **未閉じフェンスの扱い**: 閉じ ``` が来ないまま入力が終われば `in_fence` は
  `True` のままループが終わり、残り全行がそのブロックの内容として `fenced_lines` に
  積まれ続ける（＝入力末尾までを 1 ブロックとして扱う）。
- **None 相当の扱い**: フェンスが一つも無ければ空リスト `[]` を返す（`None` ではない）。
  呼び出し元 `_first_command_line` はこの空リストを `_first_executable_line` に渡し、
  結果として `fenced` 変数が `None`（または空文字相当の偽値）になり、フェンス外の
  フォールバック分岐へ進む。

### `_has_command_like_leading_token`（L2911-2931、フェンス外フォールバックの絞り込み）

```python
# フェンス外では `sh -n` が英語の散文も単純コマンドとして受理するため、頻出する
# 実行語から始まる行だけを候補にする。ハイフンを含む CLI 名とパス指定も許可する。
_KNOWN_COMMAND_WORDS = frozenset({
    "awk", "bash", "cargo", "cd", "codd-gate", "diff", "docker", "find", "git", "go",
    "grep", "java", "make", "mvn", "node", "npm", "npx", "perl", "php", "pip", "pip3",
    "pnpm", "poetry", "pytest", "python", "python3", "rg", "ruby", "sed", "sh", "test", "tox",
    "uv", "yarn", "zsh",
})


def _has_command_like_leading_token(line: str) -> bool:
    """フェンス外の行が既知コマンド語または実行可能らしいトークンで始まるか判定する。"""
    if not line:
        return False
    token = line.split(maxsplit=1)[0]
    bare = token.rsplit("/", 1)[-1]
    return (
        bare in _KNOWN_COMMAND_WORDS
        or token.startswith(("./", "../", "/"))
        or bool(re.fullmatch(r"[A-Za-z0-9_.]+-[A-Za-z0-9_.-]+", bare))
    )
```

- **正規表現**: `re.fullmatch(r"[A-Za-z0-9_.]+-[A-Za-z0-9_.-]+", bare)`
  （ハイフンを含む CLI 名、例 `custom-check` を許可する）。
- **戻り値の型**: `bool`。空行は `False`。
- `_first_command_line` のフェンス外経路は、この判定を通った行だけを
  `_first_executable_line(..., require_shell_syntax=True)`（既定値）へ渡す。

### `_looks_like_shell_command`（L2866-2880、`sh -n` 構文チェック）

```python
# 全角の文/句読点。シェルコマンドにはまず現れず、自然言語（散文・拒否文）の強い指標。
_PROSE_PUNCT = "。、！？；：「」『』（）"


def _looks_like_shell_command(line: str) -> bool:
    """合成された 1 行が「決定的なシェルコマンド」か、エージェントの自然言語かを判定する。
    全角の文/句読点を含むものは散文とみなして弾き、残りは `sh -n`（構文解析のみ・非実行）で
    妥当性を確認する。疑わしきは False（→ verify 未定義のまま人の判断へ）。"""
    s = line.strip()
    if not s:
        return False
    if any(ch in s for ch in _PROSE_PUNCT):       # 全角の文/句読点 → 自然言語
        return False
    try:
        # sh -n は構文チェックのみで実行しない。不完全な if/未閉じクォート等の散文を弾く。
        chk = subprocess.run(["sh", "-n", "-c", s], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True          # 構文チェック不能な環境では句読点判定のみで通す（best-effort）
    return chk.returncode == 0
```

- **正規表現**: 使わない。全角句読点の文字集合 `_PROSE_PUNCT` によるメンバーシップ判定と
  `sh -n -c <line>` のサブプロセス構文チェック（非実行）。
- **戻り値の型**: `bool`。

### `_strip_code`（L129-133、フェンス由来行の後処理）

```python
def _strip_code(val: str) -> str:
    v = strip_ansi(val).strip()
    if len(v) >= 2 and v.startswith("`") and v.endswith("`"):
        return v[1:-1]
    return v
```

- ANSI エスケープ除去 → 前後空白 trim → 前後を単一バッククォートで囲まれていれば
  中身だけ返す（インラインコード表記の剥離）。`_first_executable_line` が各候補行に
  適用する。

### `_SHELL_FENCE_LANGUAGE_TAGS`（L2909）

```python
_SHELL_FENCE_LANGUAGE_TAGS = frozenset({"bash", "console", "sh", "shell", "zsh"})
```

フェンス内の最初の非空行が言語タグの残骸（例: フェンス開始行と本文行の解釈がずれて
`bash` という語だけの行が紛れ込むケース）である場合に候補から除外する。

### `synth_verify`（L2968-2993、`_first_command_line` の唯一の呼び出し元）

```python
def synth_verify(cfg: "Config", title: str, accept: str, kiro_run=None,
                 hint: str = "", repo_ctx: str = "", attempts: int = 2) -> str:
    """自然言語の完了条件 accept からエージェント（kiro-cli）が決定的 verify を合成する。
    失敗・不能・kiro-cli 不在は空文字（→ verify 未定義のまま人へ）。テストは kiro_run を注入する。
    hint（過去の類似 learn）・repo_ctx（検出したテスト/ビルド基盤）で grep 退化を抑える。
    **自己修復（多候補）**: 散文/シェル非妥当/恒真式に退化した候補は不採用とし、理由を添えて最大
    attempts 回まで再合成させる（1 回で諦めず、より良い候補を引き出す）。"""
    run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="verify"))
    retry_note = ""
    for _ in range(max(1, attempts)):
        try:
            out = run(_synth_verify_prompt(title, accept, hint, repo_ctx, retry_note), cfg.model)
        except Exception:  # noqa: BLE001  kiro-cli 不在・タイムアウト等は合成せず人へ
            return ""
        cand = _first_command_line(out)
        if not cand:
            retry_note = "応答に実行可能なコマンド行がなかった"; continue
        # 自然言語（説明・拒否文）を shell=True に流すと ; | && ` > rm 等が誤実行されうるため弾く。
        if not _looks_like_shell_command(cand):
            retry_note = "シェルコマンドでなかった"; continue
        # 恒真式（true / echo … 等）は done の根拠にならない＝不採用。実挙動を確かめる候補を求める。
        if _verify_is_degenerate(cand):
            retry_note = "恒真式に退化していた。テスト/ビルド/差分/最終状態で実挙動を確かめよ"; continue
        return cand
    print(f"[kiro-project] verify 合成失敗: {retry_note}（task: {title}）", file=sys.stderr)
    return ""
```

- **戻り値の型**: `str`。成功時は採用したコマンド文字列、失敗時（attempts 回とも
  不採用、または kiro-cli 呼び出し自体が例外）は空文字 `""`。
  `_first_command_line` が `None` を返した場合はこの関数内では即 return せず
  `retry_note` を更新して次の attempt へ進む（`if not cand:` で `None` も空文字も
  ここに分岐する）。全 attempts を使い切っても解決しなければループを抜けて
  `""` を返す。

## 呼び出し関係

```
ensure_verify(L2996) --accept 経路--> synth_verify(L2968, cmd = synth_verify(...))
  synth_verify --> _first_command_line(out)           [L2982]
    _first_command_line --> _code_fence_lines(out)     [L2961]
    _first_command_line --> _first_executable_line(fenced_lines, require_shell_syntax=False)  [L2961]
    _first_command_line --> _first_executable_line(filtered_lines, require_shell_syntax=True)  [L2965, フォールバック]
      _first_executable_line --> _strip_code(raw_line.strip())
      _first_executable_line --> _looks_like_shell_command(line)  [require_shell_syntax=True のときのみ]
  synth_verify --> _looks_like_shell_command(cand)      [L2986, 二重チェック]
  synth_verify --> _verify_is_degenerate(cand)          [L2989]
```

他の呼び出し元は `main`（L8993）で charter 用に `synth_verify(cfg, charter.name or "project", text, kiro_run)`
を呼ぶ 1 箇所のみ。`_first_command_line` の呼び出し元は `synth_verify` の L2982 のみ。

## 補足（現状の実装状態についての注記）

このブランチ（`kp/synth_verify-_first_comm-172544`、HEAD `9fcf0e9`）を調査した時点で、
`_first_command_line` は既に「コードフェンスを最優先でスキャンし、フェンス内なら
`sh -n` 構文チェックを課さず無条件採用する」ロジック（`_code_fence_lines` +
`_first_executable_line(..., require_shell_syntax=False)`）を実装済みだった。
`tools/kiro-project/tests/test_kiro_project.py` にもフェンス関連のユニットテスト
（`test_first_command_line_extracts_all_fence_lines_in_order` など、L4926-4990 付近）が
既に存在し、完了条件コマンド `python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
は本タスク着手前から成功していた（12 passed, 512 deselected）。
