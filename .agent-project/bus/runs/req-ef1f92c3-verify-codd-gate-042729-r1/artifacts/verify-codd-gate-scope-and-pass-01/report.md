# t3 の同一行ラベル対応を再除去し、元要求スコープへ戻す

## (a) 成果

対象: `/Users/nitto/Workspace/sandbox`（main worktree、他タスクとの共有チェックアウト）の
`tools/agent-project/agent_project/verify.py`。

作業開始時点で確認したところ、`verify.py` には t3 由来の「ラベルとコマンドが同一行
（`検証コマンド: <command>`）」対応が **再度存在していた**（`_VERIFY_COMMAND_LABEL_RE` /
`_strip_leading_command_label`、さらに前置き散文・二重ラベル・全角コロンにも対応する
拡張版）。同種の除去は先行タスク `verify-codd-gate-required-scope-02` が一度完了済みと
報告していたが、本タスク開始時には共有チェックアウト上で元に戻っていた（同一ファイルを
複数タスクが並行編集しているための巻き戻り。t4→t5 系列が敵対的入力への対応として
再拡張したものと推測）。そのため本タスクで改めて次を除去した:

- `_VERIFY_COMMAND_LABEL_RE`（正規表現定義）と `_strip_leading_command_label`（剥がしヘルパー、
  二重ラベル対応の while ループ・散文プレフィックス許容を含む拡張版）を削除。
- `_first_executable_line` 内の `_strip_leading_command_label(...)` 呼び出しを除去し、
  `_strip_leading_shell_prompt(_strip_code(raw_line.strip()))` のみに戻した。
- `_first_command_line` 内のフィルタ述語からも同ヘルパーの呼び出しを除去。
- 両関数の docstring から「日本語ラベル同一行対応」の記述を削除し、シェルプロンプト
  記号 `$ ` の除去のみを説明する元の文面に戻した。
- `tests/test_agent_project.py` は確認のみ行い、`検証コマンド`/`_strip_leading_command_label`/
  `_VERIFY_COMMAND_LABEL_RE` への参照がゼロであることを grep で確認済み（先行タスクによる
  テスト削除が維持されており、本タスクでの追加編集は不要だった）。同ファイルには無関係な
  並行タスクの追加（`TestCoddGateAutoWiring` 等、別バックログ項目 `agent-project-codd-gate--042729`
  由来）が含まれるが、本タスクでは一切触れていない。

## (b) 検証内容と結果

- 完了条件コマンドをそのまま実行:
  `PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base "$KIRO_BASE_REV"") == "codd-gate verify --base "$KIRO_BASE_REV""'`
  → **exit 0**。
- 同一行形式が意図どおり `None` に戻っていることを実測:
  - `検証コマンド: codd-gate verify --base "$KIRO_BASE_REV"`（半角コロン） → `None`
  - `検証コマンド：codd-gate verify --base "$KIRO_BASE_REV"`（全角コロン） → `None`
- 既存の回帰ケースが壊れていないことを実測:
  - 素のコマンド行 `python3 -m pytest -q` → 変化なくそのまま抽出。
  - コマンド内コロン `git commit -m "note: fix bug"` → 変化なくそのまま抽出（誤分割なし）。
  - シェルプロンプト `$ python3 -m pytest -q` → `python3 -m pytest -q`（従来どおり）。
- `pytest tools/agent-project/tests/test_agent_project.py -k "first_command_line or first_executable_line"`
  → **15 passed**。
- `pytest tools/agent-project/tests/test_agent_project.py`（モジュール全体、667件）
  → **666 passed, 1 failed**。唯一の失敗は `TestDaemonRouting::test_kf_base_passes_flow_config`
  で、macOS の `/var` ⇔ `/private/var` シンボリックリンク解決に起因する既知の環境依存失敗
  （t3・required-scope-02 双方が同一失敗を無関係と報告済み。本タスクの変更前から存在し、
  本タスクの変更と無関係）。
- `git diff -- tools/agent-project/agent_project/verify.py` を確認し、対象差分が同一行ラベル
  対応の除去のみ（`_VERIFY_COMMAND_LABEL_RE` / `_strip_leading_command_label` の削除と
  その2箇所の呼び出し除去、docstring 復元）で、元要求（改行形式の抽出）に無関係な変更が
  混在していないことを確認した。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 元要求（`backlog/verify-codd-gate-042729.md` の完了条件）はラベル単独行→次行
  コマンドの改行形式のみを要求しており、ラベルとコマンドが同一行の形式・前置き散文・
  二重ラベルへの対応は要求外と判断した。除去後も完了条件が既存コードパス（`_has_command_like_leading_token`
  がラベル行を「コマンドらしい先頭トークンでない」として自然に除外する挙動）だけで
  満たされることを確認しており、除去は元要求の達成を損なわない。
- **確認事項（他タスクとの競合）**: 本タスクと同一ゴールの並行タスク
  （`verify-codd-gate-required-scope-02`、`verify-codd-gate-final-check-04`）が共有
  チェックアウト上で同時に走っており、除去と再追加が競合し得る状態だった。本タスク完了
  時点のスナップショットでは除去済みだが、後続の並行タスク（特に t5「t4 の指摘を取り込んで
  再修正」系列）が再度同一行対応を書き戻す可能性がある。最終的な収束は統合ゲート（t6/t7、
  もしくは final-check-04）側での再確認に委ねる。
- **範囲外で見つけた問題**: なし。`test_agent_project.py` 内の `TestCoddGateAutoWiring` 等は
  別バックログ項目由来で本タスクと無関係のため報告のみで変更していない。
