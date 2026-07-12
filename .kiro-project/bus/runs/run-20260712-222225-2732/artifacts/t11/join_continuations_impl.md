# `_join_continuations` 実装（t11 候補）

**差別化の切り口**: 「継続に入った後の行は落とさない」非対称フィルタ — 空行・純コメント行の除外は
*継続の起点*にのみ適用し、いったんバックスラッシュ継続に入った行はコメント然/空行然としていても
連結対象として保持する。バックスラッシュ直後の行を無条件で捨てる素朴な実装は結合済みコマンドを
途中で欠落させるため、これを他候補との差分として明示する。

## (a) 成果物

`tools/kiro-project/kiro-project.py` L2943 付近（`_has_command_like_leading_token` と
`_first_executable_line` の間）に新規ヘルパーを追加した。既存関数への配線変更（`_first_command_line`
等の書き換え）は行っていない — それは依存グラフ上 t12 の範囲。

```python
_TRAILING_BACKSLASH_RE = re.compile(r"\\\s*$")


def _join_continuations(lines: list[str]) -> list[str]:
    """行末バックスラッシュ `\\` による継続行を1つの論理コマンド文字列へ結合する。

    継続中でない行のうち、空行・`#` 始まりの純コメント行は結合対象にせず落とす
    （継続の起点にしない）。いったん継続に入った行（直前行が `\\` 終端）は、
    たとえ空行やコメント然とした内容でも連結対象として保持する — バックスラッシュ
    直後の行を無条件で落とすと結合済みコマンドが途中で壊れるため。戻り値は論理行
    ごとに1件のリストで、各行の末尾 `\\` は除去し、継続元と継続先はシェルの行
    継続と同じく半角スペース1つで連結する。
    """
    joined: list[str] = []
    parts: list[str] = []
    continuing = False
    for raw in lines:
        stripped = raw.strip()
        if not continuing and (not stripped or stripped.startswith("#")):
            continue
        m = _TRAILING_BACKSLASH_RE.search(stripped)
        if m:
            parts.append(stripped[: m.start()].rstrip())
            continuing = True
            continue
        parts.append(stripped)
        joined.append(" ".join(p for p in parts if p))
        parts = []
        continuing = False
    if parts:
        joined.append(" ".join(p for p in parts if p))
    return joined
```

### 挙動

| 入力 | 出力 |
|---|---|
| `["pytest -q \\", "  -k first_command_line"]` | `["pytest -q -k first_command_line"]` |
| `["cmd1 \\", "cmd2 \\", "cmd3"]`（多段継続の連鎖） | `["cmd1 cmd2 cmd3"]` |
| `["", "echo hi", "# comment", "echo bye"]` | `["echo hi", "echo bye"]`（空行・純コメント行は落ちる） |
| `["cmd1 \\"]`（末尾が `\` のまま入力終端） | `["cmd1"]`（収集済み分をそのまま1件として返す。例外は投げない） |
| `[]` / `["", "# only comments"]` | `[]` |

`tools/kiro-project/tests/test_kiro_project.py` の `TestVerifyAssist` に上記5ケースをテストとして追加
（`test_join_continuations_*`、`_first_command_line` 系テストの直後に配置）。

## (b) 検証

```
python3 -m pytest tools/kiro-project/tests -q -k first_command_line
# → 14 passed, 517 deselected（終了コード0）— 完了条件を満たす
python3 -m pytest tools/kiro-project/tests -q -k join_continuations
# → 5 passed, 526 deselected（終了コード0）— 新規テスト単体
```

フルスイート（`-k` なし）も実行: 1 failed, 530 passed。失敗は
`TestVerifyAssist.test_synth_verify_strips_ansi_from_kiro_output` の1件のみで、`git stash` して
本タスクの変更を除いた state（base commit `2e99f23`）でも同一の失敗を再現済み（変更前後で無変化）。
ANSI ストリップと `_first_command_line`/`_first_executable_line` 側のタイミング不整合が原因と見られ、
`_join_continuations` の追加とは無関係（t9 の報告が既に同一の既知不具合として指摘済み）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- t11 のタスク原文（`_join_continuations(lines) -> list[str]`）を一次契約とし、依存の t7 仕様
  （`_first_command_line` 自体の新パース契約）はスコープ外として参照のみに留めた。t7 は本関数の
  存在に言及していないため、入出力契約はタスク原文と graph.json の t12（ヘルパー合成先）の記述
  「フェンス優先 → プロンプト記号除去 → 継続結合 → コマンドらしさ判定」から、本関数が
  「フェンス内/フェンス外の行リスト」を受け取り「継続結合済みの論理行リスト」を返す中間ステップで
  あると解釈した。
- 継続結合はシェルの `\` + 改行の意味論（バックスラッシュと改行を消して1個の空白として連結）に
  倣った。改行そのものを残す・区切り無しで直結する等の別解は採らなかった。
- 継続中の行に対する空行/コメント除外は行わない（非対称フィルタ）とした。これは他候補と差別化した
  設計判断であり、後続の t12 統合時に別候補の挙動と比較検討されたい。

**未解決事項**:
- 継続中に現れる `#` 始まりの行（例: `cmd1 \` の直後に `# note` が続く）を連結に含めるか除外するかは
  仕様に明記がなく、本実装は「連結対象として保持」を選んだ。除外が妥当な場合は要調整。
- 本関数を `_first_command_line`/`_first_executable_line` へ実際に配線する作業は t12 の範囲であり、
  本タスクでは未実施（意図的なスコープ遵守）。

**範囲外で見つけた問題**:
- `test_synth_verify_strips_ansi_from_kiro_output` は本タスクの変更前から失敗している既知の
  pre-existing failure（t9 の報告と一致、`-k first_command_line` の完了条件には含まれないため未着手）。
