# 調査結果: `_first_command_line` の日本語ラベル対応

## (a) 結論（サマリー）

**現在の worktree（`ap/verify-codd-gate-042729`, `main` から分岐）では、完了条件は既に成立している（exit=0）。**
「日本語ラベル行が先頭にあるとコマンド行を抽出できない」というバグは、このブランチの元になった
`main` の時点で既に修正済みだった（コミット `598ea1d8`）。今回の調査で新規のコード変更は行っていない
（範囲外の変更をしない方針のため、既に満たされている完了条件に対して余計な修正は加えていない）。

## 実装の詳細（調査内容）

- 対象: `tools/agent-project/agent_project/verify.py`
  - `_first_command_line(out)`（459–488行目）: ANSI除去 → コードフェンス優先スキャン →
    フェンスが無ければフェンス外行にフォールバックし、`_first_executable_line` へ委譲する。
  - `_strip_leading_command_label(line)`（372–390行目）: 正規表現
    `^.*?検証コマンド\s*[:：]\s*` を「変化がなくなるまで」繰り返し適用し、行頭（または行内の
    前置き散文の後）にある『検証コマンド:』『検証コマンド：』ラベルを剥がす。
    `_first_executable_line`（448, 485行目）内で `_strip_leading_shell_prompt` と並べて、
    コマンド判定・`sh -n` 構文チェックの**前**に適用される。
  - `_KNOWN_COMMAND_WORDS`（355–360行目）に `codd-gate` が含まれており、ラベル剥離後の
    `codd-gate verify --base "$KIRO_BASE_REV"` は先頭トークン判定を通過する。
- 呼び出し元（verify合成）: `synth_verify`（491行目）が `_first_command_line(out)` の戻り値を
  候補コマンドとして受け取り（505行目）、Windows シェル判定・自然言語判定・恒真式判定を経て
  `task.verify` に採用する。

### 完了条件コマンドの実測（今回の実行）

```
$ PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; \
  assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == \
  "codd-gate verify --base \"$KIRO_BASE_REV\""'
$ echo $?
0
```
→ **exit=0（成功）**。AssertionError は発生していない。

併せて関連ユニットテスト（`tools/agent-project/tests/test_agent_project.py` の
`first_command_line` 系20件、`test_first_command_line_strips_japanese_label_on_command_line` 等
ラベル関連ケースを含む）も実行し、全件 pass を確認した:

```
$ PYTHONPATH=tools/agent-project python3 -m pytest tools/agent-project/tests/test_agent_project.py \
  -k "first_command_line" -q
....................
20 passed, 690 deselected in 0.26s
```

### なぜ「一見バグに見えかねない」箇所があったか（念のため潰した疑義）

`_first_executable_line`（440–456行目）は行前処理に `_strip_code(...)` を呼ぶが、`_strip_code` は
`verify.py` 内では未定義（`model.py` の56行目で定義）。これは import 事故ではなく、本パッケージが
「複数断片を1つの共有名前空間へ `exec` 合成する」設計（`agent_project/__init__.py` の `_FRAGMENTS`）
のためで、`model` 断片が `verify` 断片より前に exec される（`_FRAGMENTS` 順序: `model` → … →
`verify`）。関数呼び出し時の名前解決は呼び出し時点の共有 globals に対して行われるため、
`NameError` は発生しない。実測でも `_strip_code` に起因する例外は出ていない。

## (b) 検証内容と結果

- 完了条件のワンライナーを実行 → **exit=0（成功）**。上記の実行ログを参照。
- 関連する既存ユニットテスト20件を実行 → 全件 pass。
- ソースコード読解により、ラベル剥離ロジック（`_strip_leading_command_label`）が
  「ラベル単独行」「ラベル+コマンド同一行」「ラベル前に散文」「ラベル二重付与」の
  いずれの形式にも対応済みであることをコードレベルで確認。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: タスク文中の「原因を特定する」「失敗内容を記録する」は、実際に失敗している場合の
  手順として書かれているが、本ブランチ時点のコードでは既に修正済みで完了条件は成立していた。
  そのため「原因」は"既知の過去バグ・現在は解消済み"として報告し、失敗内容の代わりに
  **成功（exit=0）という実測結果**を記録した。これは事実の報告であり、推測ではない。
- **変更なし**: 完了条件が既に満たされているため、`verify.py` 等への追加変更は行っていない
  （範囲外の「ついで修正」を避ける方針に従った）。
- **未解決事項**: なし。追加調査や別タスク化が必要な問題は見つかっていない。
- **範囲外で見つけた問題**: なし。
