# t20 成果報告: 後方互換回帰テスト（素のコマンド1行 / バックスラッシュ継続）

## (a) 成果

`tools/kiro-project/tests/test_kiro_project.py` にテスト1件を追加した
（`test_first_command_line_returns_none_for_prose_only` の直後、`_join_continuations` 系テストの直前）。

```python
def test_first_command_line_bare_line_and_backslash_continuation_stay_backward_compatible(self):
    self.assertEqual(
        km._first_command_line("python3 -m pytest tools/kiro-project/tests -q -k first_command_line"),
        "python3 -m pytest tools/kiro-project/tests -q -k first_command_line",
    )
    # _join_continuations は _first_command_line に結線されていない（別関数として単体テスト済み）ため、
    # 継続入力でも最初の物理行（末尾 `\` 付き）が従来どおり返る。
    self.assertEqual(
        km._first_command_line("pytest -q \\\n  -k first_command_line"),
        "pytest -q \\",
    )
```

対象実装（`kiro-project.py` の `_first_command_line`）はテスト追加のみで無変更。

## (b) 検証内容と結果

- 完了条件コマンド: `python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
  → **16 passed, 517 deselected（終了コード0）**。新規追加分を含め全通過。
- 参考としてフルスイートも実行: `python3 -m pytest tools/kiro-project/tests -q`
  → 532 passed, **1 failed**（`test_synth_verify_strips_ansi_from_kiro_output`）。
  この失敗は本タスクの変更（テスト追加のみ、`_first_command_line` 本体は無変更）とは無関係の
  既存不具合であることを、変更前後でロジック差分が無いことから確認済み（詳細は下記「範囲外で見つけた問題」）。
- `git status`: `tools/kiro-project/tests/test_kiro_project.py` のみ変更（worktree、commit/push は未実施）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- t7 仕様書の記載どおり、HEAD (`f5102be`) 時点で `_first_command_line` はフェンス優先＋プレフィックス除去
  （`$ ` シェルプロンプト・箇条書き記号）の新契約を既に実装済みと確認した（t11/t14/t15/t17 で反映済みと推測）。
- `_join_continuations` は定義のみで `_first_command_line` に一切結線されていない（grep で呼び出し箇所ゼロを確認）。
  これは t7 が「`_first_command_line` の契約外」と明示した既知の不整合であり、本タスクはこれを
  「バグ」として修正対象にはせず、**現状（かつ修正前コミット `9fcf0e9` から不変）の挙動を固定する回帰テスト**
  として扱った。実際に `9fcf0e9` 時点のロジック（非フェンス経路: `_has_command_like_leading_token` +
  `_looks_like_shell_command`）と現行ロジックを比較し、この2ケースについて振る舞いが完全に同一であることを
  確認済み。
- 「素のコマンド1行」は、既存テスト（`test_first_command_line_returns_direct_command` 等）が
  コメント行や前置き行を伴う入力のみを検証しており、フェンスも前置きも一切無い単一行のみの入力を
  直接検証するテストが無かったため、そのギャップを埋める形で追加した。

**未解決事項**: なし（本タスクのスコープは満たした）。

**範囲外で見つけた問題**:
1. `test_synth_verify_strips_ansi_from_kiro_output` がフルスイート実行で失敗する（ANSI除去後の
   `grep -q '## 概要' README.md` が「実行可能なコマンド行が無い」と判定され `synth_verify` が空文字を返す）。
   本タスクの差分（テスト追加のみ）とは無関係の既存不具合と判断した。原因の深掘り・修正は本タスクの
   スコープ外のため、別タスク化の要否は評価役の判断に委ねる。
2. t7 が申し送った「カテゴリ5・6の回帰テスト未追加」「docstring/`ensure_verify` 不整合」は、
   本タスクの対象（素のコマンド1行・バックスラッシュ継続の後方互換）とは別軸の指摘のため未着手のまま。
