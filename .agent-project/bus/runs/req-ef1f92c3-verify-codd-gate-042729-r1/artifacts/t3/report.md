# `_first_command_line` 日本語ラベル同一行対応

**切り口**: t1/t2 は「完了条件は現状コードで既に満たされる」ことの確認に留めたが、本タスクは
そこから一歩進め、t1/t2 が共通して指摘した既知ギャップ（ラベルとコマンドが同一行の形式が
`None` になる）を、既存の全回帰ケースを壊さない最小差分で解消した。

## (a) 成果

対象: `/Users/nitto/Workspace/sandbox` の `tools/agent-project/agent_project/verify.py`
（HEAD `b1868483`。他タスクが同一チェックアウトで並行編集中のため、触れたのは
`verify.py` と `tests/test_agent_project.py` の2ファイルのみ）。

- `_VERIFY_COMMAND_LABEL_RE` / `_strip_leading_command_label` を追加（verify.py:320-332）。
  行頭一致 `^検証コマンド\s*[:：]\s*`（半角・全角コロン両対応）のみを剥がす。
  「行内の最初のコロンで切る」一般化はせず固定ラベル語のみを対象にした — t2 が
  指摘した `git commit -m "note: fix bug"` / `grep -n "time: 12:30" file.txt` /
  `docker run alpine:3.18 echo hi` のようなコマンド内コロンを誤分割しないため。
- `_first_executable_line`（行の最終整形）と `_first_command_line`（フィルタ述語）の
  両方に `_strip_leading_command_label` を追加適用し、「ラベル+コマンドが同一行」でも
  フィルタで弾かれず、返り値からラベルが除去された状態で通るようにした。
- ラベル単独行（例 `検証コマンド:\n<command>`）はラベル除去後 `""` になり、既存の
  `_has_command_like_leading_token` の空文字ガードでこれまでどおり候補から外れる
  ため、完了条件の2行形式の挙動は変えていない。
- テスト3件を追加（test_agent_project.py、`test_first_command_line_strips_leading_shell_prompt_symbol`
  の直後）:
  `test_first_command_line_strips_japanese_label_on_command_line`（半角コロン同一行）、
  `test_first_command_line_strips_japanese_label_with_fullwidth_colon`（全角コロン同一行）、
  `test_first_command_line_japanese_label_does_not_split_quoted_colon`
  （`git commit -m "note: fix bug"` の非破壊確認）。

## (b) 検証内容と結果

- 完了条件のシェルコマンドをそのまま実行 → **exit 0**（従来どおり成功、戻り値も期待値と一致）。
- t2 が挙げた回帰入力サンプル（4-1〜4-6、22ケース）を実際に通し、**全て一致**
  （4-6 の日本語ラベル同一行2ケースのみ `None` → 期待どおりコマンド抽出に変化。
  英語ラベル同一行 `'Verify command: pytest -q'` は今回のスコープ外のため意図的に
  `None` のまま — 今回の依頼は日本語ラベルのみ）。
- `tools/agent-project/tests/test_agent_project.py` の `first_command_line` /
  `synth_verify` / `resolve_acceptance` 系テスト26件（既存23件＋新規3件）が全てパス。
- モジュール全体（`pytest tools/agent-project/tests/test_agent_project.py`）を実行し
  653件中652件パス。唯一の失敗 `TestDaemonRouting::test_kf_base_passes_flow_config` は
  macOS の `/var` → `/private/var` シンボリックリンク解決に起因する既存の環境依存の
  失敗であることを、変更前コード（`git stash` で本タスクの差分を退避して再実行）でも
  同一の失敗が再現することで確認済み（本タスクの変更とは無関係）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: charter の「agent-project をエンジンとして扱う」制約と t1/t2 の調査結果から、
  修正対象は `sandbox` ワークスペースの `tools/agent-project` 一式と判断した
  （`.agent-project` 側には `tools/` が存在しないため）。
- **前提**: 依頼文の「ラベルと同一行にコマンドが続く形式も許容し」は日本語ラベル
  『検証コマンド:』についてのみ明示されていたため、英語ラベル `Verify command:` の
  同一行対応はスコープ外とし、`None` のまま残した（t2 が既知ギャップとして記録済み）。
- **未解決事項（範囲外）**: `_KNOWN_COMMAND_WORDS` に `curl` が含まれておらず、
  フェンスなし `curl ...` は抽出されない（t2 が指摘済み、別タスク化を推奨）。
- **共有チェックアウトについての注記**: `/Users/nitto/Workspace/sandbox` は他タスクが
  同時に `tools/agent-dashboard/*` 等を編集中の共有チェックアウトだった。commit / push
  等は行わず、`verify.py` と `test_agent_project.py` の編集のみに留めた。全体テスト実行の
  過程で一時的に `git stash` / `stash pop` を使ったが、直後にポップして自分の2ファイルの
  差分のみが残ることを確認済み（他タスクの並行編集内容は保持されたまま）。
