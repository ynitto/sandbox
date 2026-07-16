# t2: `_first_command_line` の全呼び出し元と既存テスト回帰入力の洗い出し

## (a) 成果

### 呼び出し元マップ（本番コード、`tools/agent-project/agent_project/*.py` 全体を grep）

`_first_command_line` の呼び出し元は **1 箇所のみ**:

```
verify.py:477  synth_verify() 内:  cand = _first_command_line(out)
```

`synth_verify` 自体の呼び出し元（＝変更の波及範囲）:

```
verify.py:528   ensure_verify()          — task.verify が未設定なら合成して埋める
project.py:86   cmd_project 系の finalize/acceptance 経路 — charter/プロジェクト accept 文から合成
```

`ensure_verify` の呼び出し元:

```
mr.py:552   task が CONSUMABLE かつ verify 未設定のときに ensure_verify(cfg, t) を呼ぶ
```

つまり影響経路は `_first_command_line → synth_verify → {ensure_verify → mr.py, project.py}` の
一本道。`_first_command_line` を直接呼ぶのは `synth_verify` だけなので、変更の妥当性は
`synth_verify` の合成フロー（Windows 判定→自然言語判定→恒真式判定と組み合わせた最終結果）
まで見て確認する必要がある（`_first_command_line` 単体の戻り値だけでは不十分）。

パッケージ構成の注記: `agent_project/__init__.py` は各断片（`verify.py` 含む）を単一の共有
名前空間へ `exec` して合成する方式（docstring 参照）。そのため `_first_command_line` は
`verify.py` 内 def であっても `agent_project` パッケージの globals にそのまま現れ、
`from agent_project import _first_command_line`（本タスクの完了条件コマンド）は追加の
re-export なしに解決する。他の断片ファイルにも `_first_command_line` 等への直接参照は無い
（grep 確認済み、上記1箇所のみ）。

### 既存テストの回帰入力一覧（`tools/agent-project/tests/test_agent_project.py`, `TestVerifyAssist`）

`_first_command_line` を直接叩くテストは 5816〜5943 行に 20 件。入力→期待出力（すべて現行実装で green、
今回の変更後もこの表の対応関係を1件も崩してはならない）:

| 入力（要約） | 期待出力 |
|---|---|
| `"\n# comment\npytest -q\n"` | `"pytest -q"` |
| 散文（句読点なし前置き）+ `\n` + コマンド | コマンド行 |
| 英語散文（無句読点）+ `\n` + `pytest -q` | `"pytest -q"` |
| 散文 + `\n` + `./scripts/check.sh --quick` | 同コマンド |
| 散文 + `\n` + `custom-check --all`（ハイフン CLI） | 同コマンド |
| フェンス2ブロック混在文書 → `_code_fence_lines` | `["first", "second"]`（出現順） |
| ` ``` `（無タグ）フェンス内 `pytest -q` | `"pytest -q"` |
| ` ```sh ` フェンス | フェンス内コマンド |
| ` ```console ` フェンス、`$ pytest -q` | `"pytest -q"`（フェンス内でも `$ ` は剥がれる） |
| 未閉フェンス（末尾までがブロック） | フェンス内最終行のコマンド |
| 散文 + ` ```bash ` フェンス | フェンス内コマンド |
| コロン終端の前置き文 + フェンス | フェンス内コマンド |
| フェンス内に空行・コメント・字下げコメントが混在 | 最初の非空・非コメント実行行のみ（後続の `echo ...` は無視） |
| ` ``` `\n`bash`\n`# ...`\n`python3 ...` （言語タグ残骸が本文扱い） | 最初の実行行 |
| `"$ python3 -m pytest ... -q"` | 先頭 `$ ` が除去された同コマンド |
| `'検証コマンド: {CMD}'`（半角コロン同一行） | `{CMD}` |
| `'検証コマンド：{CMD}'`（全角コロン同一行） | `{CMD}` |
| `'git commit -m "note: fix bug"'`（ラベル語なしのコロン） | 無変化でそのまま返る（誤剥離しない） |
| `'検証コマンド: 検証コマンド: {CMD}'`（二重ラベル） | `{CMD}`（while ループで収束） |
| `'以下を実行してください。検証コマンド: {CMD}'`（同一行に前置き散文＋ラベル） | `{CMD}` |
| `"\n# comment only\n"` | `None` |
| 散文のみ（コマンド候補なし） | `None` |

本タスクの完了条件そのものと同型（**ラベルが独立行、コマンドが次行**）のケース `"検証コマンド:\n{CMD}"` は
上記個別テストには存在しないが、t1 の調査（`artifacts/t1/report.md` バリアント表 #1）で動作確認済みであり、
本タスク側でも下記の通り実行確認済み（(b) 参照）。

`synth_verify` 経由（`_first_command_line` の唯一の呼び出し元）の回帰テスト、同ファイル 5744〜5990 行:

- `test_synth_verify_strips_ansi_from_kiro_output` — ANSI 混入出力から素のコマンドを合成
- `test_is_windows_shell_command_flags_powershell_and_cmd` — Windows シェル判定（`_first_command_line` の後段フィルタ）
- `test_synth_verify_rejects_unfenced_powershell_and_retries_to_posix` — フェンス無し PowerShell は候補から落ちて再合成
- `test_synth_verify_prompt_states_posix_and_forbids_powershell` — 合成プロンプトの文言契約
- `test_synth_verify_rejects_fenced_powershell_with_specific_note` — フェンス付き PowerShell は候補には乗るが後段で却下
- `test_first_command_line_prose_only_never_becomes_synth_verify_command` — 散文だけの応答が繰り返されても verify は空文字のまま（人へ委譲）

関連ヘルパーの直接テスト（`_first_command_line` が内部で使う関数群、こちらも回帰対象）:
- `_code_fence_lines`: 上記フェンス系テストに同居
- `_join_continuations`: 5945〜5968 行に単体テスト4件あり。**ただし本番コードからの呼び出しは
  現状ゼロ**（`verify.py` 全体 grep でも定義行以外に出現しない）。`_first_command_line` の
  抽出パイプラインには組み込まれていない未結線のヘルパーであることを確認した。

## (b) 検証内容と結果

- `grep -rn "_first_command_line\|_first_executable_line\|_strip_leading_command_label\|_has_command_like_leading_token\|_code_fence_lines\|_strip_leading_shell_prompt\|_join_continuations\|synth_verify(\|ensure_verify(" tools/agent-project/agent_project/*.py` で全断片ファイルを横断確認。呼び出し元は上記マップの通り。
- 完了条件コマンドを実行し、現行実装のまま成功を確認（コード変更なし）:
  ```
  PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'
  ```
  → 終了コード 0。
- `PYTHONPATH=tools/agent-project python3 -m pytest tools/agent-project/tests/test_agent_project.py -q -k "first_command_line or synth_verify or join_continuations or is_windows_shell_command"` → **33 passed**（本タスクに関係する既存回帰は全て green、現状ベースライン）。
- フルスイート `PYTHONPATH=tools/agent-project python3 -m pytest tools/agent-project/tests/test_agent_project.py -q` → **677 passed, 1 failed**（143秒）。
  失敗は `TestDaemonRouting.test_kf_base_passes_flow_command_config`（正しくは
  `TestDaemonRouting::test_kf_base_passes_flow_config`）で、`tempfile` が返す `/var/...` パスと
  `Path.resolve()` が返す macOS 実体パス `/private/var/...` の不一致によるもの。`_first_command_line`
  / `synth_verify` / verify 合成とは無関係な、環境依存（macOS のシンボリックリンク）の既存失敗であり、
  本タスクの変更前から独立して存在する。
- 現在の worktree は `main` からの差分ゼロ（`git diff main -- tools/agent-project/agent_project/verify.py` が空）。
  すなわち「検証コマンド:」ラベル抽出の完了条件は**現行 main の実装で既に満たされている**。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 本タスクは呼び出し元・回帰入力の洗い出しのみが範囲であり、コード変更は行っていない
  （worktree 変更なし）。
- **重要な前提共有**: `_first_command_line` の変更が許される安全域は、上記「回帰入力一覧」の
  全行の入出力対応を1件も崩さないこと、かつ唯一の呼び出し元 `synth_verify` の後段フィルタ
  （Windows 判定・自然言語判定・恒真式判定）と組み合わせた最終挙動（6テスト）を崩さないこと。
  `_first_command_line` は他に呼び出し元が無いため、単体の入出力契約さえ守れば波及は
  `synth_verify` の6テストのみに限定される。
- **範囲外で見つけた問題1**: `TestDaemonRouting.test_kf_base_passes_flow_config` がこの環境で
  `/var` vs `/private/var` の差で失敗する（本タスクと無関係、既存のプラットフォーム依存の不具合）。
  修正はこのタスクの範囲外と判断し、報告のみに留めた。
- **範囲外で見つけた問題2**: `_join_continuations` は定義・単体テストのみが存在し、本番コードの
  どこからも呼ばれていない（`_first_command_line` の抽出パイプラインにも未結線）。バックスラッシュ
  継続行を1コマンドに結合する意図の関数だが、現状は死んでいる（呼び出し側が無い）。今回の
  「検証コマンド:」ラベル対応そのものには影響しないが、将来この関数を結線する変更が入る場合は
  影響範囲の見直しが必要になる。
- **範囲外で見つけた問題3（t1 からの申し送り、二重確認）**: `artifacts/t1/report.md` のバリアント
  #12「ラベルと `$` プロンプトが同一行に同居」（`検証コマンド: $ {CMD}`）が `None` を返す既知の穴。
  `_strip_leading_command_label(_strip_leading_shell_prompt(line))` という合成順のため、ラベルを
  剥がした後に露出する `$ ` が再度プロンプト除去にかからない。本タスクの完了条件（ラベルと `$`
  プロンプトが同時に同一行へ同居する形）には該当しないため回帰対象外だが、今後この形式への
  対応要否は評価役の判断に委ねる。
- **未解決事項**: 特になし。完了条件は現行実装で満たされており、本タスクとしての追加調査は完了。
