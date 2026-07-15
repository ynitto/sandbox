# t1: `_first_command_line` 現行実装調査

対象: `sandbox`（workspace）`tools/agent-project/agent_project/verify.py`（HEAD `f4660b04`、当該ファイルに未コミット差分なし）。
コード確認は `Read`、挙動確認は実際に対話シェルで `PYTHONPATH=tools/agent-project python3` を実行して検証した（推測ではない）。

## (a) 成果 — 現行実装の抽出規則

### 本体: `_first_command_line(out)` — verify.py:386-406

```python
386: def _first_command_line(out: str) -> Optional[str]:
399:     out = strip_ansi(out)
400:     fenced = _first_executable_line(_code_fence_lines(out), require_shell_syntax=False)
401:     if fenced:
402:         return fenced
403:     lines = (out or "").splitlines()
404:     return _first_executable_line(
405:         [line for line in lines if _has_command_like_leading_token(_strip_leading_shell_prompt(line.strip()))]
406:     )
```

処理順序:
1. **ANSI除去**（399行目、`model.py:49-53` の `strip_ansi`）— `text or ""` で `None` も安全に空文字へ正規化する。
2. **コードフェンス優先スキャン**（400-402行目）— `_code_fence_lines`（273-296行目）が ```` ``` ```` で囲まれた行を抽出し、フェンス内が見つかれば `_first_executable_line(..., require_shell_syntax=False)`（367-383行目）でフェンス内最初の非空・非コメント・非言語タグ行を**無条件**にコマンドとして採用する。フェンスが無ければ `fenced=None`。
3. **フェンス外フォールバック**（403-406行目）— 全行を `_has_command_like_leading_token`（320-330行目）でフィルタしてから `_first_executable_line`（`require_shell_syntax` 省略＝既定 `True`）に渡す。

### 行フィルタ: `_has_command_like_leading_token` — verify.py:320-330

```python
320: def _has_command_like_leading_token(line: str) -> bool:
324:     token = line.split(maxsplit=1)[0]
325:     bare = token.rsplit("/", 1)[-1]
326:     return (
327:         bare in _KNOWN_COMMAND_WORDS
328:         or token.startswith(("./", "../", "/"))
329:         or bool(re.fullmatch(r"[A-Za-z0-9_.]+-[A-Za-z0-9_.-]+", bare))
330:     )
```

先頭トークンが 303-308行目の `_KNOWN_COMMAND_WORDS`（`awk`, `bash`, `cargo`, `cd`, **`codd-gate`**, `diff`, `docker`, … `python3`, `rg` … 等の固定集合）に一致するか、`./` `../` `/` で始まるか、`語-語` 形状（ハイフン入り CLI 名）に一致すれば通過。**`codd-gate` はこの初期クローン時点（コミット `47c65ff7`、`git log -p` 確認済み）から既に集合に含まれている** — 後から追加された形跡はない。

### 採否判定: `_first_executable_line` — verify.py:367-383 と `_looks_like_shell_command` — verify.py:256-270

```python
374:     for raw_line in lines:
375:         line = _strip_leading_shell_prompt(_strip_code(raw_line.strip()))
376:         if (
377:             line
378:             and not line.startswith("#")
379:             and line.casefold() not in _SHELL_FENCE_LANGUAGE_TAGS
380:             and (not require_shell_syntax or _looks_like_shell_command(line))
381:         ):
382:             return line
383:     return None
```

フェンス外経路は `require_shell_syntax=True` のため、全角句読点を含まない（263-264行目）かつ `sh -n`（構文チェックのみ、非実行）が通る（265-270行目）行だけを採用する。

### 戻り値・空文字/None 時の扱い

- 戻り値の型は `Optional[str]`（386行目）。**採用した行文字列をそのまま返す**（加工・trim はしない。ただし比較対象の行自体は 375行目で `.strip()` 済み）。
- どの規則にも合致しなければ `None`（フェンス側 383行目 / フォールバック側も同じ `_first_executable_line` 経由で 383行目）。
- 入力が空文字 `""` または `None`: 399行目の `strip_ansi` が `None` を `""` に正規化するため両者は同じ経路を通る。`_code_fence_lines("")` は空行リストを返し `fenced=None`、403行目 `(out or "").splitlines()` も `[]`。結果、`_first_command_line("")` と `_first_command_line(None)` はいずれも **`None`** を返す（フィルタ対象行が0件なので `_first_executable_line([])` が空ループで `None`）。

## (b) 検証内容と結果

### 対象入力での挙動（該当行を根拠にトレース）

入力: `"検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\""`

1. 399行目 `strip_ansi` — ANSI無し、無変換。
2. 400行目 `_code_fence_lines` — ```` ``` ```` が1つも無いので `[]` → `fenced = None`（383行目経由）。
3. 403行目 `lines = ["検証コマンド:", "codd-gate verify --base \"$KIRO_BASE_REV\""]`。
4. 405行目のフィルタ:
   - `"検証コマンド:"` → 先頭トークン全体が `"検証コマンド:"`（空白を含まないため1トークン）。`_KNOWN_COMMAND_WORDS` に無く、`./` 等でも始まらず、ハイフン形状の正規表現にも一致しない（全角文字＋コロン） → **除外**。
   - `"codd-gate verify --base \"$KIRO_BASE_REV\""` → 先頭トークン `"codd-gate"` が `_KNOWN_COMMAND_WORDS` に一致 → **採用**。
5. `_first_executable_line(["codd-gate verify --base \"$KIRO_BASE_REV\""])` — `#` 始まりでもフェンス言語タグでもなく、`_looks_like_shell_command` が全角句読点無し・`sh -n` 構文OKで `True` → **この行がそのまま返る**。

### 完了条件コマンドの再現結果 — 現状は失敗しない

```bash
cd /Users/nitto/Workspace/sandbox
PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'
echo "EXIT CODE: $?"
# → EXIT CODE: 0
```

`_first_command_line(...)` の戻り値は `'codd-gate verify --base "$KIRO_BASE_REV"'` で期待値と完全一致し、assert は例外を送出しない。**現行コード（workspace `sandbox`、HEAD `f4660b04`、`verify.py` に未コミット差分なし）では、この完了条件コマンドは失敗しない（終了コード0）。**

`agent_project` は単一ファイル分割パッケージで、`__init__.py` が `_FRAGMENTS`（`_head`, …, `verify`, …）を共有名前空間へ `exec` 合成する構造（`__init__.py:1-40` 付近）。`agent_project.verify` を独立モジュールとして直接 import すると `_head` 由来の `re` 等が無く `NameError` になるため、確認は完了条件と同じ `from agent_project import _first_command_line` 経由で行った（これが正しい呼び出し経路）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: `workspace: sandbox`（`backlog/verify-codd-gate-042729.md` 記載）を対象リポジトリとした。`.agent-project` 側には `tools/` 自体が存在しないため、これ以外に完了条件コマンドが成立する候補ディレクトリはない。
- **未解決事項（重要）**: 本タスクの依頼文は「なぜ完了条件コマンドが失敗するのか再現手順つきで示す」ことを求めているが、**実際に再現したところ現行コードでは失敗しなかった**（上記 (b) 参照）。`codd-gate` は `_KNOWN_COMMAND_WORDS` に初回クローンコミット `47c65ff7` の時点から既に含まれており（`git log -p --follow -- verify.py` で確認、以後この行に変更なし）、後続タスクによる直近の修正でもない。したがって「失敗の再現」はできず、代わりに「現在は成功する」という事実を報告する。後続の gate/synth/loop タスクは、追加の修正を前提とせず、まずこの完了条件コマンドを実行して現状で満たされているかどうかを確認することを推奨する。
- **範囲外で気づいた点**: `_has_command_like_leading_token` はフェンス外で1行に複数トークンがあっても先頭トークンしか見ないため、ラベル行 `"検証コマンド:"` のように日本語ラベルの直後に空白なくコマンドが続く形（例 `"検証コマンド: codd-gate ..."` のように同一行の場合）は未検証・未対応の可能性がある（今回の依頼入力はラベルとコマンドが別行のため対象外）。修正の要否は範囲外のため判断は評価役に委ねる。
