# generate: `_first_command_line` 実装（差別化の切り口: t4 の抽出仕様を実装コードで直接検算する）

t1〜t4 が仕様面から一致点・矛盾点を検証したのに対し、本タスクは worktree の実コード
（`tools/agent-project/agent_project/verify.py:280-460`）を1行ずつ t4 の仕様書と突き合わせ、
完了条件コマンドと関連回帰を実行して**コード側から**同じ結論を裏づける。

## 成果

`_first_command_line`（`verify.py:431`）は t4 が記述した仕様（Stage0 ANSI 除去 →
Stage1 フェンス優先スキャン（sh -n 免除）→ Stage2 フェンス外フォールバック（sh -n 必須）、
行正規化4段階、採否判定4条件）を**既にそのまま実装済み**であることをコードリーディングで
確認した。ラベル `検証コマンド:`（全角コロン可）の非貪欲・反復除去（`_strip_leading_command_label`,
L347-362）、`$ ` プロンプト除去（`_strip_leading_shell_prompt`, L338-341）、コードフェンス
優先スキャン（`_code_fence_lines` + `_first_executable_line(require_shell_syntax=False)`,
L448-450）、フェンス外フォールバック（`_has_command_like_leading_token` 事前フィルタ +
`require_shell_syntax=True`, L452-460）のいずれも仕様書の記述と1対1で対応しており、
実装追加・変更は不要と判断した。**worktree に差分なし**（調査のみ）。

## 検証内容と結果

1. 完了条件コマンドを worktree 上で実行 → **exit 0**
   ```
   PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'
   ```
2. 関連回帰（`tools/agent-project/tests/test_agent_project.py`）:
   `pytest -k "first_command_line or synth_verify or join_continuations or is_windows_shell_command"`
   → **33 passed**（t1/t2/t3/t4 の33件と同数、退行なし）
3. `git status --short` / `git diff --stat` → 出力なし（無編集を再確認）

## 採用した前提・未解決事項・範囲外で見つけた問題

- 前提: タスク文言「実装し」は「完了条件を満たすコードを worktree に用意すること」を指し、
  既存実装が完了条件・関連回帰をすべて満たす場合は新規コード追加を要求しないと解釈した
  （t1〜t4 も同じ前提で一致）。この解釈のもと、実装済みコードの正当性をコードリーディング
  と実行の両面から再検算するに留めた。
- 未解決事項: なし（完了条件はコマンド実行で確認済み）。
- 範囲外の付随所見（t4 と同一、再掲のみ・修正はしない）:
  - 既知の穴: `検証コマンド: $ codd-gate ...`（ラベルと `$` プロンプトが同一行）は `None` になる。
    原因は `_strip_leading_shell_prompt` が `_strip_leading_command_label` より先に適用される
    合成順序（L420, L457）。本タスクの完了条件（ラベルとコマンドが別行）には含まれない。
  - `_join_continuations`（L381-409）は `_first_command_line` のパイプラインから呼ばれておらず
    無関係（呼び出し元ゼロ）。
