# verify-codd-gate-scope-fix-03 独立検証結果

## (a) 結論

**PASS** — verify-codd-gate-scope-fix-03 の成果は完了条件を満たし、範囲外の変更も混入していない。

## (b) 検証内容と結果

### 1. 完了条件コマンド

対象コード（`agent_project` パッケージ）は `/Users/nitto/Workspace/sandbox`（main worktree）に実体があるため、そちらで実行した（`.agent-project` は制御面のみの sparse worktree）。

```
cd /Users/nitto/Workspace/sandbox
PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'
```

結果: **終了コード 0**（アサーション成功、例外なし）。

### 2. 差分レビュー（範囲判定）

`git diff -- tools/agent-project/agent_project/verify.py` を確認した。変更は以下のみ:

- `_VERIFY_COMMAND_LABEL_RE` 正規表現と `_strip_leading_command_label()` 関数を削除。
- `_first_executable_line` / `_first_command_line` 内の2箇所の呼び出し（`_strip_leading_command_label(...)`）を除去し、`_strip_leading_shell_prompt(...)` のみに戻した。
- 上記削除に伴い docstring からラベル剥がしの説明を除去。

`git diff -- tools/agent-project/tests/test_agent_project.py` でも対応する3件のテストが削除されていることを確認した:

- `test_first_command_line_strips_japanese_label_on_command_line`（同一行ラベル `検証コマンド: <command>`）
- `test_first_command_line_strips_japanese_label_with_fullwidth_colon`（全角コロン同一行ラベル）
- `test_first_command_line_japanese_label_does_not_split_quoted_colon`（コロンを含む引用符内の非分割確認）

これら3件はいずれも「ラベルとコマンドが同一行」のケース専用のテストであり、依存タスク定義（`bus/runs/.../tasks/verify-codd-gate-scope-fix-03.json`）に記載の目的「日本語ラベルとコマンドが同一行の『検証コマンド: <command>』対応だけを特定して取り除く」と一致する。

一方、元の要求（改行形式 `検証コマンド:\ncodd-gate verify ...`）を扱う一般ロジックはコードから削除されておらず、専用の正規表現なしに次の既存メカニズムだけで成立することを確認した:

- 1行目 `検証コマンド:` は `_has_command_like_leading_token` の判定（既知コマンド語 / `./` `../` `/` 始まり / ハイフン付き CLI 名パターン）のいずれにも一致しないため、フェンス外候補から自然に除外される。
- 2行目 `codd-gate verify --base "$KIRO_BASE_REV"` は先頭トークン `codd-gate` が `_KNOWN_COMMAND_WORDS` に含まれるため候補として採用される。

`_strip_leading_command_label` / `_VERIFY_COMMAND_LABEL_RE` への参照が repo 内に残っていないことを `grep` で確認済み（ダングリング参照なし）。

**判定**: 元の要求外だった「同一行ラベル対応」は綺麗に除去されており、元の要求（改行ラベル対応）に無関係な成果の混在はない。

### 3. 追加のテスト実行

```
cd /Users/nitto/Workspace/sandbox
python3 -m pytest tools/agent-project/tests/test_agent_project.py -q -k "first_command_line"
```

結果: `15 passed, 652 deselected`（既存の関連テスト全件グリーン）。

## (c) 前提・未解決事項・範囲外で見つけた問題

- **前提**: 「PYTHONPATH=tools/agent-project」は main worktree（`/Users/nitto/Workspace/sandbox`）を cwd として解決される前提で実行した（`.agent-project` sparse worktree にはパッケージ実体が無いため）。既存メモリ（sandbox の worktree 分割規約）に基づく判断であり、依存タスクの成果物にも同様の前提が見て取れる。
- **範囲外で見つけた問題（未修正・報告のみ）**:
  - 依存タスク `verify-codd-gate-scope-fix-03` の結果 JSON（`bus/runs/req-ef1f92c3-verify-codd-gate-042729-r1/results/verify-codd-gate-scope-fix-03.json`）の `output` フィールドが「バックグラウンドの pytest 全体スイート完了を待っています」という中間状態のメッセージのまま保存されており、実際に完了した成果（コード差分）の内容を説明していない。成果物自体（コード）は正しいが、報告テキストの体裁が不完全である可能性がある。本タスクの範囲外のため修正はしていない。
  - `git status` 上には本 run 以外にも多数の未コミット変更（`docs/designs/*`, `codd_gate_*.py` 等）が存在するが、これらは他タスクの管掌範囲であり本検証では対象外とした。
- 全リポジトリの pytest フルスイートは実行していない（対象範囲を `first_command_line` 関連テストに限定）。フルスイートの完了状態は本タスクの完了条件に含まれていないため未確認のまま記載する。
