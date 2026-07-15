# t3 の同一行ラベル対応を除去し、元の要求スコープに戻す

## (a) 成果

対象: `/Users/nitto/Workspace/sandbox` の
`tools/agent-project/agent_project/verify.py` と
`tools/agent-project/tests/test_agent_project.py`。

作業開始時点で、対象ワークツリーには t3 が追加した「ラベルとコマンドが同一行
（`検証コマンド: <command>`）」対応がすでに取り除かれた状態のコードが存在していた
（`git diff` で確認。以下は現状把握できた差分の内容）:

- `verify.py`: `_VERIFY_COMMAND_LABEL_RE` と `_strip_leading_command_label`
  （行頭の『検証コマンド:』／全角コロン可を剥がすヘルパー）を削除。
- `verify.py`: `_first_executable_line` と `_first_command_line` の両方から
  `_strip_leading_command_label(...)` の呼び出しを外し、docstring も
  「日本語ラベルの同一行対応」の記述を削除して元の内容（シェルプロンプト記号
  `$ ` の除去のみ）に戻した。
- `tests/test_agent_project.py`: t3 が追加した3件のテスト
  （`test_first_command_line_strips_japanese_label_on_command_line`、
  `test_first_command_line_strips_japanese_label_with_fullwidth_colon`、
  `test_first_command_line_japanese_label_does_not_split_quoted_colon`）を削除。

このワークツリーは他タスクとの共有チェックアウトであり（t3 の報告にも同様の注記あり）、
`test_agent_project.py` には無関係な並行タスクの追加（`TestCoddGateAutoWiring` 等）も
含まれているが、本タスクではそれらに一切触れていない。追加でコードは変更していない
（対象の除去自体が既に完了していたため、差分の作成ではなく現状の妥当性検証を行った）。

## (b) 検証内容と結果

- 完了条件コマンドをそのまま実行:
  `PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base "$KIRO_BASE_REV"") == "codd-gate verify --base "$KIRO_BASE_REV""'`
  → **exit 0**。改行形式（ラベル単独行→次行コマンド）の抽出は既存挙動どおり成功。
- 同一行形式が意図どおり `None` に戻っていることを実測で確認:
  - `検証コマンド: codd-gate verify --base "$KIRO_BASE_REV"`（半角コロン同一行）→ `None`
  - `検証コマンド：codd-gate verify --base "$KIRO_BASE_REV"`（全角コロン同一行）→ `None`
- 無関係な退行がないことを確認: `git commit -m "note: fix bug"` のようなコマンド内コロンを
  含む入力は従来どおり `git commit -m "note: fix bug"` を返す。
- `_strip_leading_command_label` / `_VERIFY_COMMAND_LABEL_RE` への参照が
  `verify.py` / `test_agent_project.py` の双方から完全に消えていることを `grep` で確認。
- `pytest tools/agent-project/tests/test_agent_project.py -k "first_command_line or first_executable_line"`
  → 15 件全てパス。
- `pytest tools/agent-project/tests/test_agent_project.py`（モジュール全体）を実行し、
  t3 が報告していた既知の環境依存失敗（`TestDaemonRouting::test_kf_base_passes_flow_config`、
  macOS の `/var`→`/private/var` シンボリックリンク解決由来、本タスクと無関係）以外に
  新規の失敗がないことを確認した。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 依頼文の「元の要求である改行形式の抽出だけを既存挙動として維持する」を、
  t3 が追加した同一行対応（ヘルパー関数・呼び出し箇所・専用テスト）を丸ごと除去し、
  改行形式のみが動く元の実装に戻すことと解釈した。t4 が敵対的入力
  （二重ラベル・散文プレフィックス付き同一行）で `None` を返す既知の欠陥を報告しており、
  本タスクはその機能自体を後退させることで解決している。
- **前提**: 作業開始時点ですでに除去済みの状態だったため、これは本タスクの前回反復
  （loop-until-done）による作業と判断し、内容が要求と一致することを再検証したうえで
  そのまま採用し、追加の編集は行わなかった。
- **範囲外で見つけた問題**: なし（t2/t4 が指摘した `curl` 未対応・英語ラベル同一行の
  スコープ外指定は t3 報告のとおりで、本タスクの対象外）。
- **未解決事項**: 特になし。完了条件・関連テストともにパス。
